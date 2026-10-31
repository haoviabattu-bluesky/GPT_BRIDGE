# -*- coding: utf-8 -*-
"""tests/conftest.py — fixture dùng chung. KHÔNG gọi mạng trong test mặc định.

Nguyên tắc: mọi test offline phải chạy được khi rút mạng. Test cần Drive/Sheets thật
được đánh dấu `live` và bị bỏ qua nếu thiếu credential hoặc chưa đặt BRIDGE_LIVE=1.
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXT = Path(__file__).resolve().parent / "fixtures"


# ------------------------------------------------------------------ config
@pytest.fixture
def cfg():
    """Cấu hình tối thiểu, trỏ vào temp dir của test (không đụng dữ liệu thật)."""
    import tempfile
    td = Path(tempfile.mkdtemp(prefix="bridge_test_"))
    c = {
        "_root": str(td), "_config_path": str(td / "config.yaml"),
        "app": {"name": "GPT_BRIDGE", "log_level": "WARNING",
                "state_db": str(td / "jobs" / "bridge.db"),
                "stop_file": str(td / "temp" / "STOP"),
                "heartbeat_file": str(td / "logs" / "heartbeat.json")},
        "credentials": {"oauth_token": str(td / "token.json"),
                        "service_account": str(td / "sa.json")},
        "drive": {"input_folder_name": "INPUT", "output_folder_name": "OUTPUT",
                  "inbox_folder_id": "INBOX_FAKE",
                  "episode_folder_pattern": r"^(?P<lane>[A-Za-z]+)-ep(?P<ep>\d+)_v(?P<ver>\d+)$",
                  "max_download_mb": 200,
                  "retry": {"attempts": 2, "base_sleep": 0, "max_sleep": 0}},
        "sheets": {
            "scan_interval_seconds": 60, "scan_range": "A1:M100",
            "columns": {"episode_number": "A", "title": "B", "local_folder": "C", "request": "K"},
            "status_rules": {
                "latest_line_wins": True,
                "need_script": ["can kich ban", "can viet kich ban", "chua co kich ban"],
                "script_done": ["kich ban xong", "da co kich ban", "da giao gpt"]},
            "link_labels": {"zip": ["zip ho so", "zip"], "folder": ["thu muc drive", "thu muc"]},
        },
        "projects": {"H2DEV": {
            "enabled": True, "spreadsheet_id": "SHEET_FAKE", "sheet_name": "Lane-N",
            "sheet_id": 1555179355, "request_column": "K",
            "prompt_template": "h2dev_script_writer.md", "writer": "GPT6",
            "lane_prefix": "N", "profile": "senior_korekara",
            "output_naming": "Lane{lane}_tap{ep_num}_{writer}_SCRIPT_DELIVERY.zip",
            "job_id_pattern": "{project}-{lane}-{episode}-{writer}-v{version:02d}"}},
        "writer": {"adapter": "mock", "mode": "manual",
                   "manual": {"inbox_dir": str(td / "jobs" / "_inbox"), "timeout_hours": 48},
                   "chatgpt_web": {"cdp_port": 9335, "allow_launch": False}},
        "qc": {"required_files": ["SCRIPT_FINAL_JP.txt", "SCRIPT_QC_REPORT.md",
                                  "SCRIPT_ISSUES.md", "SCRIPT_STRUCTURE.md"],
               "script_file": "SCRIPT_FINAL_JP.txt",
               "char_count_exclude": ["\n", "\r", " ", "\u3000", "\t"],
               "max_uncompressed_mb": 200, "max_files": 200,
               "require_hook_verbatim": True, "default_budget": {"min": 0, "target": 0, "max": 0}},
        "upload": {"verify_hash": True, "overwrite": False, "allow_mock_upload": False},
        "limits": {"max_attempts": 3, "unknown_submit_grace_sec": 120,
                   "max_prompt_chars_inline": 6000},
    }
    for d in ("jobs", "jobs/_inbox", "temp", "logs", "docs"):
        (td / d).mkdir(parents=True, exist_ok=True)
    return c


@pytest.fixture
def project(cfg):
    return cfg["projects"]["H2DEV"]


# ------------------------------------------------------------------ dữ liệu mẫu
HOOK_TEXT = (
    "六十八歳の春、わたしは四百二十万円の軽キャンピングカーを、買わずに一か月だけ借りた。"
    "夫は若いころから雑誌の切り抜きを集めていた。"
)
FILLER = "その日のことを、まだ覚えている。"


@pytest.fixture
def handoff_zip(tmp_path):
    """ZIP hồ sơ ĐÚNG cấu trúc thật (bọc trong 1 folder con, có manifest)."""
    import json
    root = "LaneN_tap17_GPT_SCRIPT_HANDOFF"
    files = {
        "01_HOOK_OUTLINE.txt":
            "=== HOOK (KHOÁ — chép nguyên văn ở đầu kịch bản; "
            f"{len(HOOK_TEXT)} chữ) ===\n{HOOK_TEXT}\n"
            "=== KHUNG SƯỜN ===\nPHẦN 1 — Mở\nPHẦN 2 — Thân\nPHẦN 3 — Kết\n",
        "02_SCRIPT_RULES.md": "# 02_SCRIPT_RULES\nMục tiêu 300 · chấp nhận 250 – 400.\n",
        "03_RESEARCH_FACTS.md": "# 03_RESEARCH_FACTS\n| Trường | Giá trị | Nhãn |\n|---|---|---|\n"
                                "| Tiêu đề | abc | VERIFIED |\n",
        "04_CHANNEL_PROFILE.md": "# 04_CHANNEL_PROFILE\n",
        "05_CONTINUITY.md": "# 05_CONTINUITY\n| 戸倉誠一 | 68 |\n",
        "06_REFERENCE_SCRIPT.txt": "参考脚本。\n",
        "07_WRITING_TASK.md": (
            "# 07_WRITING_TASK — N-ep17\n\n"
            "| Mục | Giá trị |\n|---|---|\n"
            "| Job ID | `N-ep17` |\n"
            "| Lane | N · profile `senior_korekara` · tab `Lane-N` |\n"
            "| Episode | ep17 · `ep17-kei-campingcar-kari-1kagetsu` |\n"
            "| Tiêu đề dự kiến | 【漫画】420万円の軽キャンピングカー |\n"
            "| Số ký tự | min **250** · target **300** · max **400** |\n"),
        "08_QC_CHECKLIST.md": "# 08_QC_CHECKLIST\n| 1 | Độ dài | 250 ≤ ký tự ≤ 400 | ☐ |\n",
        "10_GPT6_START_HERE.md": "# 10_GPT6_START_HERE\nSao chép khối dưới đây...\n",
        "HANDOFF_ISSUES.md": "# HANDOFF_ISSUES\n- cảnh báo\n",
    }
    manifest = {
        "job_id": "N-ep17", "episode_id": "ep17",
        "required_files": list(files),
        "per_file_checksums": {},
        "title": "【漫画】420万円の軽キャンピングカー",
    }
    p = tmp_path / "handoff.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(f"{root}/{name}", body)
        zf.writestr(f"{root}/09_HANDOFF_MANIFEST.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2))
    return p


@pytest.fixture
def result_zip(tmp_path):
    """ZIP kết quả ĐÚNG chuẩn 4 file, hook nguyên văn, độ dài trong ngân sách 250–400."""
    body = HOOK_TEXT + "\n" + FILLER * 20
    from services.zip_qc import count_chars
    chars = count_chars(body)
    assert 250 <= chars <= 400, f"fixture sai: {chars}"
    qc = f"# SCRIPT_QC_REPORT — ep17\n- Độ dài: {chars} ký tự\n- Job: N-ep17-GPT6-v01\n"
    p = tmp_path / "result.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SCRIPT_FINAL_JP.txt", body)
        zf.writestr("SCRIPT_QC_REPORT.md", qc)
        zf.writestr("SCRIPT_ISSUES.md", "# SCRIPT_ISSUES\n- không có\n")
        zf.writestr("SCRIPT_STRUCTURE.md", f"# SCRIPT_STRUCTURE\n| Tổng | {chars} |\n")
    return p


# ------------------------------------------------------------------ fake Drive
class FakeDrive:
    """Drive giả trong RAM — mô phỏng đúng hành vi cần thiết, KHÔNG gọi mạng."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self._n = 0

    def _id(self, prefix="F"):
        self._n += 1
        return f"{prefix}{self._n:04d}"

    def add_folder(self, name: str, parent: str | None = None) -> str:
        fid = self._id("D")
        self.files[fid] = {"id": fid, "name": name,
                           "mimeType": "application/vnd.google-apps.folder",
                           "parents": [parent] if parent else []}
        return fid

    def add_file(self, name: str, parent: str, data: bytes, mime="application/zip") -> str:
        fid = self._id("F")
        self.files[fid] = {"id": fid, "name": name, "mimeType": mime,
                           "parents": [parent], "size": str(len(data)),
                           "modifiedTime": "2026-10-24T00:00:00Z"}
        self.blobs[fid] = data
        return fid

    # --- API bề mặt
    def get(self, file_id, fields=None):
        if file_id not in self.files:
            from services.errors import BridgeError
            raise BridgeError("E_DRIVE_NOT_FOUND", file_id)
        return dict(self.files[file_id])

    def list_children(self, folder_id, folders_only=False, name=None):
        out = [dict(f) for f in self.files.values() if folder_id in (f.get("parents") or [])]
        if folders_only:
            out = [f for f in out if f["mimeType"].endswith("folder")]
        if name:
            out = [f for f in out if f["name"] == name]
        return out

    def is_folder(self, file_id):
        return self.files[file_id]["mimeType"].endswith("folder")

    def resolve_pair(self, episode_folder_id):
        from services.errors import BridgeError
        kids = self.list_children(episode_folder_id, folders_only=True)
        by = {}
        for k in kids:
            by.setdefault(k["name"], []).append(k)

        def pick(n, e1, e2):
            hit = by.get(n, [])
            if not hit:
                raise BridgeError(e1, f"có {sorted(by)}")
            if len(hit) > 1:
                raise BridgeError(e2, str([h["id"] for h in hit]))
            return hit[0]["id"]
        return {"input_folder_id": pick("INPUT", "E_DRIVE_INPUT_MISSING", "E_DRIVE_FOLDER_AMBIGUOUS"),
                "output_folder_id": pick("OUTPUT", "E_DRIVE_OUTPUT_MISSING", "E_DRIVE_FOLDER_AMBIGUOUS"),
                "children": kids}

    def resolve_episode_folder(self, lane, ep_num):
        import re
        from services.errors import BridgeError
        pat = re.compile(r"^(?P<lane>[A-Za-z]+)-ep(?P<ep>\d+)_v(?P<ver>\d+)$")
        cands = []
        for f in self.list_children("INBOX_FAKE", folders_only=True):
            m = pat.match(f["name"])
            if m and m.group("lane").upper() == lane.upper() and int(m.group("ep")) == ep_num:
                cands.append({"id": f["id"], "name": f["name"], "version": int(m.group("ver"))})
        if not cands:
            raise BridgeError("E_DRIVE_NOT_FOUND", f"{lane}-ep{ep_num}")
        cands.sort(key=lambda x: x["version"], reverse=True)
        return cands[0]

    def parents_of(self, file_id):
        return self.files[file_id].get("parents") or []

    def assert_in_folder(self, file_id, folder_id):
        from services.errors import BridgeError
        if folder_id not in self.parents_of(file_id):
            raise BridgeError("E_ZIP_NOT_IN_INPUT", f"{file_id} not in {folder_id}")

    def find_in_folder(self, folder_id, name):
        return self.list_children(folder_id, name=name)

    def download(self, file_id, dest, expect_sha=None):
        import hashlib
        data = self.blobs[file_id]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
        return {"path": Path(dest), "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "md5": None}

    def upload(self, path, folder_id, name, overwrite=False, mime="application/zip"):
        from services.errors import BridgeError
        if self.find_in_folder(folder_id, name) and not overwrite:
            raise BridgeError("E_DRIVE_UPLOAD_VERIFY", f"đã tồn tại {name}")
        return {"id": self.add_file(name, folder_id, Path(path).read_bytes())}

    def delete(self, file_id):
        self.files.pop(file_id, None)
        self.blobs.pop(file_id, None)

    def web_link(self, file_id, kind="file"):
        if kind == "folder":
            return f"https://drive.google.com/drive/folders/{file_id}"
        return f"https://drive.google.com/file/d/{file_id}/view"

    def ensure_folder(self, parent_id, name):
        hit = self.list_children(parent_id, folders_only=True, name=name)
        return hit[0]["id"] if hit else self.add_folder(name, parent_id)


@pytest.fixture
def fake_drive():
    return FakeDrive()


@pytest.fixture
def live():
    """Đánh dấu test cần Drive/Sheets thật."""
    import pytest as _p
    if os.environ.get("BRIDGE_LIVE") != "1":
        _p.skip("cần BRIDGE_LIVE=1 và credential thật")
    return True
