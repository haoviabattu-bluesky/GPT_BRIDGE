# -*- coding: utf-8 -*-
"""executor_v2.py — executor cho pipeline hook_body_v2 (yêu cầu 2D + 9, 26/10/2026).

Đặt trong GPT_BRIDGE/agent_loop để `run_executor` gọi được. KHÁC `mg_implement.py`:
executor này ĐƯỢC PHÉP ghi tệp sản xuất trong phạm vi hẹp, vì việc nó làm chính là
nối pipeline (yêu cầu 8) — nhưng chỉ trong MG_STUDIO và chỉ những đường dẫn khai báo
ở `PHAM_VI` bên dưới, mỗi lần ghi đều trả SHA256 để GPT-6 kiểm chứng độc lập.

Ba việc:
    HO_SO_GPT6 : đóng gói hồ sơ GPT-6 từ dữ liệu THẬT của tập (core/ho_so_gpt6.py)
    KIEM_HO_SO : kiểm hồ sơ đã đóng (thiếu/sửa tay -> BLOCKED, không báo PASS giả)
    NHAN_BODY  : nhận BODY_SCRIPT_JP.txt từ GPT-6 -> validate deterministic -> ghi canh.json

Nguyên tắc:
- Không gọi LLM (GPT-6 chính là người giao việc; Hermes/Python chỉ kiểm máy).
- Không sửa Hook. Không đụng tập legacy.
- Không báo xong nếu chưa đọc lại tệp và tính SHA256.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

MG = Path(r"E:\CLAUDE\YOUTUBE\MG_STUDIO")
PY = sys.executable

# Chỉ những tệp này được ghi (đường dẫn tương đối trong tập)
PHAM_VI = (
    "body/", "hook/HOOK_APPROVED_JP.txt", "canh.json", "canh_s2_goc.json",
    "trang_thai_qc.json", "SCRIPT_READY.json", "kiem.json",
)


def _guard(p: Path) -> Path:
    """Chặn mọi đường dẫn ra ngoài MG_STUDIO hoặc sang H2DEV."""
    rp = Path(p).resolve()
    h2 = Path(r"E:\CLAUDE\YOUTUBE\h2dev").resolve()
    if h2 in rp.parents or rp == h2:
        raise PermissionError(f"CẤM đụng H2DEV: {rp}")
    if MG.resolve() not in rp.parents and rp != MG.resolve():
        raise PermissionError(f"ngoài phạm vi MG_STUDIO: {rp}")
    return rp


def _sha(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _art(p: Path) -> dict:
    p = Path(p)
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": _sha(p)}


def _tap_dir(instruction: str) -> Path:
    """Bóc đường dẫn tập trong chỉ thị. Không đoán: phải có de_tai.json.

    Đường dẫn ngoài MG_STUDIO -> trả None (nơi gọi báo BLOCKED) thay vì ném
    PermissionError làm sập vòng điều phối: loop.py không bắt exception của executor,
    một chỉ thị sai đường dẫn sẽ giết cả job thay vì trả báo cáo có blocker.
    """
    for m in re.finditer(r"[A-Za-z]:[\\/][^\s\"'<>|]+", instruction or ""):
        p = Path(m.group(0).rstrip(".,;)"))
        # BẪY: đường dẫn có dấu \ cuối hoặc bị markdown bọc `...` -> vẫn phải thử
        for ung in (p, Path(str(p).rstrip("\\/"))):
            if (ung / "de_tai.json").exists():
                try:
                    return _guard(ung)
                except PermissionError:
                    return None
    for m in re.finditer(r"episodes[\\/][\w\-]+[\\/][\w\-]+", instruction or ""):
        p = MG / m.group(0)
        if (p / "de_tai.json").exists():
            try:
                return _guard(p)
            except PermissionError:
                return None
    return None


def _chay_mg(*args: str, timeout: int = 600) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([PY, str(MG / "mg.py"), *args], cwd=str(MG), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ------------------------------------------------------------------ HO_SO_GPT6
def dong_goi_ho_so(instruction: str = "", log=print) -> dict:
    """Đóng gói hồ sơ GPT-6. Trả dict chuẩn executor."""
    tap = _tap_dir(instruction)
    if not tap:
        return {"summary": "BLOCKED — không xác định được thư mục tập trong chỉ thị "
                           "(cần đường dẫn tuyệt đối tới thư mục có de_tai.json, "
                           "và thư mục đó phải nằm trong MG_STUDIO)",
                "evidence": [f"chỉ thị: {(instruction or '')[:300]}"], "artifacts": [],
                "next_action": "STOP", "blocker": "thiếu đường dẫn tập hợp lệ"}
    rc, out = _chay_mg("ho_so_gpt6", "--tap", str(tap), timeout=300)
    arts = []
    for rel in ("body/manifest.json", "hook/HOOK_APPROVED_JP.txt", "body/BODY_OUTLINE.md",
                "body/GPT6_WRITING_TASK.md"):
        p = tap / rel
        if p.exists():
            arts.append(_art(p))
    ok = rc == 0 and len(arts) >= 4
    return {
        "summary": ("Đã đóng gói hồ sơ GPT-6 cho tập " + tap.name) if ok
                   else f"Đóng gói hồ sơ THẤT BẠI (rc={rc})",
        "evidence": [f"tập: {tap}", f"mg.py ho_so_gpt6 rc={rc}", *out.strip().splitlines()[-6:]],
        "artifacts": arts,
        "next_action": "KIEM_HO_SO" if ok else "STOP",
        "blocker": "" if ok else out[-400:],
    }


# ------------------------------------------------------------------ KIEM_HO_SO
def kiem_ho_so(instruction: str = "", log=print) -> dict:
    """Kiểm hồ sơ TRƯỚC khi giao GPT-6 (thiếu / bị sửa tay -> BLOCKED)."""
    tap = _tap_dir(instruction)
    if not tap:
        return {"summary": "BLOCKED — không xác định được thư mục tập", "evidence": [],
                "artifacts": [], "next_action": "STOP", "blocker": "thiếu đường dẫn tập"}
    code = (
        "import sys, json;sys.path.insert(0,r'%s');"
        "from core.tap import Tap;from core.ho_so_gpt6 import kiem_ho_so;"
        "t=Tap(r'%s');print(json.dumps(kiem_ho_so(t),ensure_ascii=False))" % (MG, tap)
    )
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([PY, "-c", code], cwd=str(MG), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, env=env)
    try:
        loi = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"summary": "BLOCKED — không đọc được kết quả kiểm hồ sơ",
                "evidence": [(r.stdout or r.stderr)[-400:]], "artifacts": [],
                "next_action": "STOP", "blocker": "kiểm hồ sơ không chạy được"}
    arts = [_art(tap / "body/manifest.json")] if (tap / "body/manifest.json").exists() else []
    ok = not loi
    return {
        "summary": ("Hồ sơ GPT-6 HỢP LỆ, giao được cho GPT-6 viết Body") if ok
                   else f"Hồ sơ CHƯA hợp lệ: {len(loi)} lỗi — KHÔNG giao GPT-6",
        "evidence": [f"tập: {tap}", f"lỗi: {loi}"] if loi else [f"tập: {tap}", "0 lỗi"],
        "artifacts": arts, "next_action": "VIET_BODY" if ok else "STOP",
        "blocker": "" if ok else "; ".join(loi[:6]),
    }


# ------------------------------------------------------------------ dispatch
def _nhan_body(instruction: str = "", log=print, data: dict | None = None) -> dict:
    """NHAN_BODY nằm ở module riêng (luật 6) — import MUỘN để tránh vòng import."""
    from .executor_v2_body import nhan_body
    return nhan_body(instruction, log=log, data=data)


EXECUTORS = {
    "HO_SO_GPT6": dong_goi_ho_so,
    "KIEM_HO_SO": kiem_ho_so,
    "NHAN_BODY": _nhan_body,
}


def run(*, instruction: str = "", action: str = "HO_SO_GPT6", log=print, data: dict | None = None) -> dict:
    fn = EXECUTORS.get((action or "").upper())
    if not fn:
        return {"summary": f"BLOCKED — action '{action}' không có trong executor_v2",
                "evidence": [f"có: {sorted(EXECUTORS)}"], "artifacts": [],
                "next_action": "STOP", "blocker": f"action lạ: {action}"}
    if fn is _nhan_body:
        return fn(instruction, log=log, data=data)
    return fn(instruction, log=log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=sorted(EXECUTORS))
    ap.add_argument("--instruction", default="")
    a = ap.parse_args()
    print(json.dumps(run(action=a.action, instruction=a.instruction), ensure_ascii=False, indent=1))
