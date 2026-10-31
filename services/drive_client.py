# -*- coding: utf-8 -*-
"""drive_client.py — Google Drive API qua OAuth.

Nguyên tắc bắt buộc (spec Phần II §3):
- KHÔNG BAO GIỜ ghép chuỗi "/input" hay "/output" vào link Drive.
- Mọi folder ID phải lấy bằng API.
- INPUT/OUTPUT phải là folder con THẬT của folder tập, đúng 1 cái mỗi loại.

Tài khoản: OAuth token tại credentials.oauth_token (refresh tự động, ghi lại token).
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

from .errors import BridgeError

FOLDER_MIME = "application/vnd.google-apps.folder"
_FIELDS = "id,name,mimeType,size,parents,md5Checksum,modifiedTime,trashed"


class DriveClient:
    def __init__(self, cfg: dict, logger=None):
        self.cfg = cfg
        self.dcfg = cfg.get("drive") or {}
        self.log = logger
        self._svc = None

    # ------------------------------------------------------------ auth
    def _api(self):
        if self._svc is not None:
            return self._svc
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds_cfg = self.cfg["credentials"]
        token_path = Path(creds_cfg["oauth_token"])
        scopes = self.dcfg.get("scopes") or ["https://www.googleapis.com/auth/drive"]
        if not token_path.exists():
            raise BridgeError("E_DRIVE_AUTH", f"Không thấy token: {token_path}")

        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    token_path.write_text(creds.to_json(), encoding="utf-8")
                except Exception as e:  # noqa: BLE001
                    raise BridgeError("E_DRIVE_AUTH", f"refresh token thất bại: {type(e).__name__}: {e}")
            else:
                raise BridgeError("E_DRIVE_AUTH", "token không hợp lệ và không có refresh_token")
        self._svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._svc

    # ------------------------------------------------------------ retry
    def _retry(self, fn, what: str, code: str):
        r = self.dcfg.get("retry") or {}
        attempts = int(r.get("attempts", 4))
        base = float(r.get("base_sleep", 3))
        cap = float(r.get("max_sleep", 45))
        last = None
        for i in range(attempts):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                last = e
                msg = str(e)
                if "404" in msg or "notFound" in msg:
                    raise BridgeError("E_DRIVE_NOT_FOUND", f"{what}: {msg[:300]}")
                if "403" in msg or "insufficientPermissions" in msg:
                    raise BridgeError("E_DRIVE_NO_PERMISSION", f"{what}: {msg[:300]}")
                if "401" in msg:
                    self._svc = None
                    raise BridgeError("E_DRIVE_AUTH", f"{what}: {msg[:300]}")
                if i < attempts - 1:
                    time.sleep(min(base * (2 ** i), cap))
        raise BridgeError(code, f"{what}: {type(last).__name__}: {str(last)[:300]}", retryable=True)

    # ------------------------------------------------------------ read
    def get(self, file_id: str, fields: str = _FIELDS) -> dict:
        def _do():
            return self._api().files().get(fileId=file_id, fields=fields,
                                           supportsAllDrives=True).execute()
        return self._retry(_do, f"get({file_id})", "E_DRIVE_NOT_FOUND")

    def list_children(self, folder_id: str, *, folders_only: bool = False,
                      name: str | None = None) -> list[dict]:
        q = f"'{folder_id}' in parents and trashed=false"
        if folders_only:
            q += f" and mimeType='{FOLDER_MIME}'"
        if name:
            q += " and name='" + name.replace("'", "\\'") + "'"

        def _do():
            out, tok = [], None
            while True:
                kw = dict(q=q, fields=f"nextPageToken,files({_FIELDS})", pageSize=200,
                          supportsAllDrives=True, includeItemsFromAllDrives=True)
                if tok:
                    kw["pageToken"] = tok
                res = self._api().files().list(**kw).execute()
                out.extend(res.get("files", []))
                tok = res.get("nextPageToken")
                if not tok:
                    return out
        return self._retry(_do, f"list({folder_id})", "E_DRIVE_NOT_FOUND")

    def is_folder(self, file_id: str) -> bool:
        return self.get(file_id, "id,mimeType").get("mimeType") == FOLDER_MIME

    # ------------------------------------------------------------ resolve INPUT/OUTPUT
    def resolve_pair(self, episode_folder_id: str) -> dict:
        """-> {'input_folder_id','output_folder_id','children':[...]}.

        Đúng 1 folder INPUT và 1 folder OUTPUT. Thiếu/nhiều => lỗi rõ ràng, KHÔNG đoán.
        """
        in_name = self.dcfg.get("input_folder_name", "INPUT")
        out_name = self.dcfg.get("output_folder_name", "OUTPUT")
        kids = self.list_children(episode_folder_id, folders_only=True)
        by = {}
        for k in kids:
            by.setdefault(k["name"], []).append(k)

        def pick(name: str, err_missing: str, err_amb: str) -> str:
            hit = by.get(name, [])
            if not hit:
                raise BridgeError(err_missing, f"folder cha {episode_folder_id} chỉ có: "
                                  f"{', '.join(sorted(by)) or '(trống)'}")
            if len(hit) > 1:
                raise BridgeError(err_amb, f"{len(hit)} folder tên '{name}': "
                                  f"{[h['id'] for h in hit]}")
            return hit[0]["id"]

        return {
            "input_folder_id": pick(in_name, "E_DRIVE_INPUT_MISSING", "E_DRIVE_FOLDER_AMBIGUOUS"),
            "output_folder_id": pick(out_name, "E_DRIVE_OUTPUT_MISSING", "E_DRIVE_FOLDER_AMBIGUOUS"),
            "children": kids,
        }

    def resolve_episode_folder(self, lane: str, ep_num: int) -> dict:
        """Tìm folder tập trong 01_INBOX theo mẫu <Lane>-ep<N>_v<NN>.

        KHÔNG ghép URL: liệt kê folder con của inbox rồi khớp regex.
        Nhiều phiên bản => lấy version CAO NHẤT (mới nhất).
        """
        inbox = self.dcfg.get("inbox_folder_id")
        if not inbox:
            raise BridgeError("E_DRIVE_NOT_FOUND", "config drive.inbox_folder_id chưa đặt")
        pat = re.compile(self.dcfg.get("episode_folder_pattern",
                                       r"^(?P<lane>[A-Za-z]+)-ep(?P<ep>\d+)_v(?P<ver>\d+)$"))
        cands = []
        for f in self.list_children(inbox, folders_only=True):
            m = pat.match(f["name"])
            if not m:
                continue
            if m.group("lane").upper() == str(lane).upper() and int(m.group("ep")) == int(ep_num):
                cands.append({"id": f["id"], "name": f["name"], "version": int(m.group("ver"))})
        if not cands:
            raise BridgeError("E_DRIVE_NOT_FOUND",
                              f"Không có folder tập {lane}-ep{ep_num}_v* trong inbox {inbox}")
        cands.sort(key=lambda x: x["version"], reverse=True)
        return cands[0]

    # ------------------------------------------------------------ verify
    def parents_of(self, file_id: str) -> list[str]:
        return self.get(file_id, "id,parents").get("parents", []) or []

    def assert_in_folder(self, file_id: str, folder_id: str) -> None:
        if folder_id not in self.parents_of(file_id):
            raise BridgeError("E_ZIP_NOT_IN_INPUT",
                              f"file {file_id} không nằm trong folder {folder_id}")

    def find_in_folder(self, folder_id: str, name: str) -> list[dict]:
        return self.list_children(folder_id, name=name)

    # ------------------------------------------------------------ transfer
    def download(self, file_id: str, dest: Path, expect_sha: str | None = None) -> dict:
        """Tải file về dest. Trả {size, sha256, md5, path}."""
        import hashlib
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        max_mb = int(self.dcfg.get("max_download_mb", 200))
        meta = self.get(file_id, "id,name,size,mimeType")
        size = int(meta.get("size") or 0)
        if size and size > max_mb * 1024 * 1024:
            raise BridgeError("E_ZIP_TOO_BIG", f"{size} bytes > {max_mb} MB")

        def _do():
            data = self._api().files().get_media(fileId=file_id,
                                                 supportsAllDrives=True).execute()
            dest.write_bytes(data)
            return data
        data = self._retry(_do, f"download({file_id})", "E_DRIVE_DOWNLOAD")
        sha = hashlib.sha256(data).hexdigest()
        if expect_sha and sha != expect_sha:
            raise BridgeError("E_DRIVE_DOWNLOAD", f"SHA-256 không khớp (mong {expect_sha[:12]}…, được {sha[:12]}…)")
        return {"path": dest, "size": len(data), "sha256": sha, "md5": meta.get("md5Checksum")}

    def upload(self, path: Path, folder_id: str, name: str,
               *, overwrite: bool = False, mime: str = "application/zip") -> dict:
        """Upload lên folder_id. KHÔNG ghi đè nếu overwrite=False (báo E_DRIVE_UPLOAD_VERIFY)."""
        from googleapiclient.http import MediaFileUpload
        path = Path(path)
        if not path.exists():
            raise BridgeError("E_DRIVE_UPLOAD", f"Không thấy file: {path}")

        existing = self.find_in_folder(folder_id, name)
        if existing and not overwrite:
            raise BridgeError("E_DRIVE_UPLOAD_VERIFY",
                              f"Đã tồn tại '{name}' trong OUTPUT ({existing[0]['id']}) — không ghi đè")

        media = MediaFileUpload(str(path), mimetype=mime, resumable=True)

        def _do():
            return self._api().files().create(
                body={"name": name, "parents": [folder_id]},
                media_body=media, fields=_FIELDS, supportsAllDrives=True).execute()
        return self._retry(_do, f"upload({name})", "E_DRIVE_UPLOAD")

    def delete(self, file_id: str) -> None:
        def _do():
            self._api().files().delete(fileId=file_id, supportsAllDrives=True).execute()
        self._retry(_do, f"delete({file_id})", "E_DRIVE_UPLOAD")

    # ------------------------------------------------------------ helpers
    def web_link(self, file_id: str, kind: str = "file") -> str:
        if kind == "folder":
            return f"https://drive.google.com/drive/folders/{file_id}"
        return f"https://drive.google.com/file/d/{file_id}/view"

    def ensure_folder(self, parent_id: str, name: str) -> str:
        """Lấy hoặc tạo folder con (chỉ dùng cho self-test, KHÔNG cho INPUT/OUTPUT thật)."""
        hit = self.list_children(parent_id, folders_only=True, name=name)
        if hit:
            return hit[0]["id"]

        def _do():
            return self._api().files().create(
                body={"name": name, "parents": [parent_id], "mimeType": FOLDER_MIME},
                fields=_FIELDS, supportsAllDrives=True).execute()
        return self._retry(_do, f"mkdir({name})", "E_DRIVE_UPLOAD")["id"]
