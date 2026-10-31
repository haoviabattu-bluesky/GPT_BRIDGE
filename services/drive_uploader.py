# -*- coding: utf-8 -*-
"""drive_uploader.py — upload ZIP kết quả lên OUTPUT folder của tập, rồi XÁC MINH.

Bắt buộc (spec §15):
1. Nhận file_id từ Drive API.
2. Đọc lại metadata.
3. Xác minh đúng folder cha, tên file, kích thước.
4. Tải lại và so SHA-256 (nếu upload.verify_hash).
5. Lưu output_file_id.
6. Chuyển COMPLETED.
7. Ghi báo cáo bàn giao.

KHÔNG upload vào folder cha của tập. KHÔNG ghi nhầm INPUT. KHÔNG ghi đè ZIP cũ.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .errors import BridgeError


class DriveUploader:
    def __init__(self, cfg: dict, drive, logger=None):
        self.cfg = cfg
        self.drive = drive
        self.log = logger
        self.ucfg = cfg.get("upload") or {}

    def upload_and_verify(self, zip_path: Path, job: dict, *, expect_sha: str | None = None,
                          name: str | None = None) -> dict:
        zip_path = Path(zip_path)
        out_id = job.get("output_folder_id")
        if not out_id:
            raise BridgeError("E_DRIVE_OUTPUT_MISSING", "job không có output_folder_id")
        if not zip_path.exists():
            raise BridgeError("E_DRIVE_UPLOAD", f"không thấy {zip_path}")

        # Chặn cứng: mock không bao giờ lên OUTPUT thật
        src = str(job.get("notes") or "")
        if "mock" in src.lower() and not self.ucfg.get("allow_mock_upload"):
            raise BridgeError("E_MOCK_NOT_UPLOADABLE",
                              "ZIP do adapter MOCK sinh — bị chặn upload (upload.allow_mock_upload=false)")

        fname = name or zip_path.name
        size_local = zip_path.stat().st_size

        created = self.drive.upload(zip_path, out_id, fname,
                                    overwrite=bool(self.ucfg.get("overwrite", False)))
        fid = created["id"]

        # 1) metadata đọc lại
        meta = self.drive.get(fid, "id,name,size,parents,md5Checksum")
        parents = meta.get("parents") or []
        if out_id not in parents:
            raise BridgeError("E_DRIVE_UPLOAD_VERIFY",
                              f"file {fid} nằm ở {parents}, không phải OUTPUT {out_id}")
        if meta.get("name") != fname:
            raise BridgeError("E_DRIVE_UPLOAD_VERIFY",
                              f"tên trên Drive '{meta.get('name')}' != '{fname}'")
        remote_size = int(meta.get("size") or 0)
        if remote_size != size_local:
            raise BridgeError("E_DRIVE_UPLOAD_VERIFY",
                              f"kích thước {remote_size} != {size_local}")

        # 2) hash
        sha_remote = None
        if self.ucfg.get("verify_hash", True):
            with tempfile.TemporaryDirectory() as td:
                dl = self.drive.download(fid, Path(td) / fname)
                sha_remote = dl["sha256"]
            if expect_sha and sha_remote != expect_sha:
                raise BridgeError("E_DRIVE_UPLOAD_VERIFY",
                                  f"SHA-256 lệch: local {expect_sha[:12]}… vs remote {sha_remote[:12]}…")

        return {"file_id": fid, "name": fname, "size": remote_size, "parents": parents,
                "sha256_local": expect_sha, "sha256_remote": sha_remote,
                "url": self.drive.web_link(fid, "file"), "verified": True}

    # ------------------------------------------------------------ receipt
    @staticmethod
    def write_receipt(out_dir: Path, job: dict, qc_report: dict, upload: dict) -> Path:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "DELIVERY_RECEIPT.md"
        checks = qc_report.get("checks") or {}
        rows = []
        for k, v in checks.items():
            if isinstance(v, dict):
                ok = v.get("ok")
                mark = "PASS" if ok is True else ("N/A" if ok is None else "FAIL")
                extra = ", ".join(f"{a}={b}" for a, b in v.items() if a != "ok")
                rows.append(f"| {k} | {mark} | {extra} |")
            else:
                rows.append(f"| {k} | {v} | |")
        table = "\n".join(rows) if rows else "| (không có) | | |"
        p.write_text(f"""# BIÊN NHẬN BÀN GIAO — {job['job_id']}

- Dự án: `{job.get('project')}` · Lane: `{job.get('lane')}` · Tập: `{job.get('episode_id')}`
- Nguồn yêu cầu: sheet `{job.get('source_sheet_name')}` dòng {job.get('source_row')}
- ZIP hồ sơ vào: `{job.get('input_zip_name')}` (id `{job.get('input_zip_file_id')}`)
- ZIP bàn giao: `{upload['name']}` (id `{upload['file_id']}`)
- Drive OUTPUT: {job.get('output_folder_url') or job.get('output_folder_id')}
- Kích thước: {upload['size']:,} bytes
- SHA-256 local : `{upload.get('sha256_local')}`
- SHA-256 remote: `{upload.get('sha256_remote')}`
- Link: {upload['url']}
- Thời điểm: {job.get('updated_at')}

## Kiểm tra tự động
| Hạng mục | Kết quả | Chi tiết |
|---|---|---|
{table}

**Lưu ý trung thực:** các kiểm tra trên chỉ gồm HÌNH THỨC / ĐỘ DÀI / HOOK / ĐỊNH DANH.
Chưa đánh giá nội dung (mạch truyện, tiếng Nhật, độ chính xác dữ kiện).
Kịch bản vẫn phải qua gate_check + QC của dự án trước khi đưa vào sản xuất.

{qc_report.get('honesty_note', '')}
""", encoding="utf-8")
        return p

    @staticmethod
    def write_json(out_dir: Path, name: str, data: dict) -> Path:
        p = Path(out_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p
