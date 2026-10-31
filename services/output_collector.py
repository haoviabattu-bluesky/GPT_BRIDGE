# -*- coding: utf-8 -*-
"""output_collector.py — nhận ZIP kết quả, chuẩn hoá về CÙNG một cấu trúc.

Hai chế độ (spec §13):
    AUTO   — adapter trả file qua giao diện lập trình (mock / chatgpt_web / official_api)
    MANUAL — người dùng thả ZIP vào jobs/_inbox/

Cả hai trả về CÙNG một dict => zip_qc và drive_uploader không phải sửa.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .errors import BridgeError


def normalize(zip_path: Path, dest_dir: Path, job_id: str) -> dict:
    """Chuẩn hoá: giải nén an toàn -> gom về 1 cấp -> đóng ZIP chuẩn tên.

    Lý do: ChatGPT/người dùng có thể đóng ZIP có folder con lồng nhau.
    Sau bước này, ZIP luôn có 4 file phẳng ở gốc => QC và Drive ổn định.
    """
    from .zip_qc import _safe_members, _basename_map, sha256_file

    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not zip_path.exists():
        raise BridgeError("E_RESULT_MISSING", f"không thấy {zip_path}")

    tmp = dest_dir / "_extract"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BridgeError("E_ZIP_CORRUPT", str(e))

    with zf:
        infos = _safe_members(zf, {"qc": {"max_files": 200, "max_uncompressed_mb": 200}})
        for i in infos:
            name = Path(i.filename.replace("\\", "/")).name
            if not name:
                continue
            with zf.open(i) as src, open(tmp / name, "wb") as dst:
                shutil.copyfileobj(src, dst)

    produced = sorted(p for p in tmp.iterdir() if p.is_file())
    if not produced:
        raise BridgeError("E_RESULT_MISSING", "ZIP rỗng sau khi giải nén")

    out = dest_dir / f"{job_id}_GPT_SCRIPT_DELIVERY.zip"
    # TẤT ĐỊNH: zipfile.write() nhúng date_time của file nguồn vào ZIP, nên 2 lần
    # đóng gói CÙNG nội dung vẫn ra SHA256 khác nhau => phá chống-upload-trùng và
    # làm verify_hash sai. Đóng bằng ZipInfo có timestamp CỐ ĐỊNH để cùng nội dung
    # luôn ra cùng một artifact.
    _FIXED = (1980, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in produced:
            zi = zipfile.ZipInfo(p.name, date_time=_FIXED)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o644 & 0xFFFF) << 16
            z.writestr(zi, p.read_bytes())

    shutil.rmtree(tmp, ignore_errors=True)
    return {"zip_path": out, "sha256": sha256_file(out), "size": out.stat().st_size,
            "files": [p.name for p in produced]}


def collect(adapter, job: dict, dest_dir: Path) -> dict:
    """Gọi adapter rồi chuẩn hoá. Trả dict thống nhất cho mọi chế độ."""
    raw = adapter.collect_result(job)
    if not raw.get("zip_path"):
        raise BridgeError("E_RESULT_MISSING", raw.get("note") or "adapter không trả ZIP")
    norm = normalize(Path(raw["zip_path"]), Path(dest_dir), job["job_id"])
    norm["source"] = raw.get("source", "unknown")
    norm["adapter_note"] = raw.get("note", "")
    return norm


def extract_text(zip_path: Path, name: str) -> str | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for i in zf.infolist():
                if Path(i.filename.replace("\\", "/")).name == name:
                    return zf.read(i).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    return None
