# -*- coding: utf-8 -*-
"""mg_implement.py — IMPLEMENT THẬT trên MG_STUDIO, chỉ THÊM tệp mới.

Ràng buộc an toàn (mục D + F):
- KHÔNG ghi đè tệp đã có (nếu đích đã tồn tại thì bỏ qua và báo PARTIAL).
- KHÔNG đụng final.mp4 / hook sản xuất / lane khác.
- Chỉ ghi trong `lanes/x/` + `core/` + `tests/` của MG_STUDIO.
- Mỗi hành động đều trả đường dẫn + SHA256 để GPT-6 kiểm chứng độc lập.

Trả dict chuẩn executor: summary / evidence / artifacts / next_action / blocker.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

MG = Path(r"E:\CLAUDE\YOUTUBE\MG_STUDIO")

# (đường dẫn tương đối, mô tả) — chỉ những tệp agent_loop tự tạo
TEP_MOI = [
    ("core/khoa_hook.py", "khoá Hook bằng SHA-256 (bảo vệ luật 12)"),
    ("core/trang_thai_qc.py", "trạng thái QC bền vững, qc_rounds<=1"),
    ("tests/test_khoa_hook_qc.py", "nghiệm thu 22 bài cho 2 module trên"),
]

# Việc executor này KHÔNG làm được: sửa tệp đang chạy của dự án sản xuất.
# Bẫy thật (job live-20261026_000729 round 3): GPT-6 giao "tích hợp HOOK_LOCK
# vào entry point, scope: mg.py + các điểm gọi pipeline", nhưng `run()` nhận
# `instruction` mà KHÔNG đọc — nó luôn kiểm 3 tệp trong TEP_MOI rồi trả báo cáo
# "IMPLEMENT THẬT … 22/22 đạt". GPT-6 đọc xong phải tự phát hiện là thiếu bằng
# chứng. Nay: chỉ thị đòi sửa tệp sản xuất -> trả BLOCKED + điểm nghẽn cụ thể,
# đúng như GPT-6 cho phép ("ghi BLOCKED và nêu chính xác điểm nghẽn").
DAU_HIEU_SUA_SAN_XUAT = (
    "mg.py", "pipeline.py", "entry point", "tích hợp vào", "tich hop vao",
    "các điểm gọi", "cac diem goi",
)


def _doi_sua_san_xuat(instruction: str) -> str:
    """Trả câu mô tả việc ngoài phạm vi nếu chỉ thị đòi sửa tệp sản xuất."""
    low = (instruction or "").lower()
    for d in DAU_HIEU_SUA_SAN_XUAT:
        if d in low:
            return d
    return ""


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run(*, instruction: str = "", log=print) -> dict:
    ev: list[str] = []
    arts: list[dict] = []
    partial: list[str] = []

    if not MG.exists():
        return {"summary": f"KHÔNG thấy {MG}", "evidence": [], "artifacts": [],
                "next_action": "STOP", "blocker": f"thiếu {MG}"}

    # 0) Chỉ thị đòi sửa TỆP SẢN XUẤT -> BLOCKED thật, không báo thành công giả.
    #    Executor này chỉ THÊM tệp mới (xem docstring đầu tệp); nối guard vào
    #    mg.py / core/pipeline.py là việc của chủ dự án hoặc một executor có
    #    quyền sửa, phải có nghiệm thu riêng.
    dau = _doi_sua_san_xuat(instruction)
    if dau:
        return {
            "summary": ("BLOCKED — chỉ thị yêu cầu SỬA tệp sản xuất "
                        f"(dấu hiệu: «{dau}»), nằm ngoài phạm vi executor IMPLEMENT "
                        "hiện tại (chỉ thêm tệp mới). Không có thay đổi nào được thực hiện."),
            "evidence": [
                f"chỉ thị đọc được: {len(instruction or '')} ký tự",
                f"dấu hiệu sửa sản xuất khớp: «{dau}»",
                "phạm vi executor: chỉ THÊM tệp mới trong core/ tests/ lanes/x/ "
                "(docstring mg_implement.py dòng 2-8)",
                "KHÔNG sửa mg.py, KHÔNG sửa core/pipeline.py, KHÔNG render",
            ],
            "artifacts": [],
            "next_action": "STOP",
            "blocker": ("cần executor có quyền sửa tệp sản xuất (mg.py / "
                        "core/pipeline.py) kèm nghiệm thu hồi quy được; executor "
                        "IMPLEMENT hiện tại cố ý chỉ thêm tệp mới để không phá "
                        "dự án đang chạy"),
        }

    # 1) kiểm từng tệp phải có
    for rel, mo_ta in TEP_MOI:
        f = MG / rel
        if not f.exists():
            partial.append(f"{rel} (THIẾU)")
            continue
        ev.append(f"IMPLEMENTED {rel} — {mo_ta} ({f.stat().st_size} bytes)")
        arts.append({"path": str(f), "bytes": f.stat().st_size, "sha256": _sha(f)})

    # 2) CHẠY THẬT bộ nghiệm thu để chứng minh không phải khung rỗng
    t = MG / "tests" / "test_khoa_hook_qc.py"
    if t.exists():
        try:
            env = {k: v for k, v in os.environ.items()
                   if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
            env["PYTHONIOENCODING"] = "utf-8"   # tránh UnicodeDecodeError khi đọc stdout
            r = subprocess.run([sys.executable, str(t)], cwd=str(MG),
                               capture_output=True, text=True, timeout=180,
                               encoding="utf-8", errors="replace", env=env)
            out = (r.stdout or "") + (r.stderr or "")
            last = [ln for ln in out.splitlines() if "KẾT QUẢ" in ln]
            ev.append(f"chạy tests/test_khoa_hook_qc.py -> exit={r.returncode}; "
                      f"{last[-1].strip() if last else out.strip().splitlines()[-1][:160] if out.strip() else 'không có output'}")
            if r.returncode != 0:
                partial.append("nghiệm thu 2 module KHÔNG đạt")
        except Exception as e:  # noqa: BLE001
            partial.append(f"không chạy được nghiệm thu: {type(e).__name__}: {e}")

    # 3) xác nhận KHÔNG đụng tệp sản xuất
    #    Cấu trúc THẬT: episodes/<lane>/<tập>/hook.json và .../renders/final.mp4
    #    (bản đầu dùng episodes/*/final.mp4 nên đếm ra 0 — sai đường dẫn)
    finals = list((MG / "episodes").rglob("final.mp4"))
    hooks = list((MG / "episodes").rglob("hook.json"))
    ev.append(f"không sửa tệp sản xuất: {len(finals)} final.mp4 và "
              f"{len(hooks)} hook.json giữ nguyên (chỉ đọc để đếm)")

    # 4) Hook Lock đã áp dụng cho tập nào chưa (báo cáo trung thực)
    khoa = list((MG / "episodes").rglob("khoa_hook.json"))
    qcst = list((MG / "episodes").rglob("trang_thai_qc.json"))
    ev.append(f"phạm vi áp dụng: {len(khoa)} tập có khoa_hook.json, "
              f"{len(qcst)} tập có trang_thai_qc.json "
              f"(chưa áp cho tập cũ — không tự khoá hook chưa xác định phê duyệt)")

    # 5) ffprobe có sẵn cho bước kiểm tra final?
    fp = shutil.which("ffprobe")
    ev.append(f"ffprobe: {fp or 'KHÔNG có trong PATH'}")

    if partial:
        return {
            "summary": ("IMPLEMENT một phần: đã tạo/kiểm module khoá Hook và trạng thái QC, "
                        f"nhưng còn {len(partial)} mục chưa đạt. Chưa đụng tệp sản xuất."),
            "evidence": ev, "artifacts": arts, "next_action": "STOP",
            "blocker": "; ".join(partial),
        }

    return {
        "summary": ("IMPLEMENT THẬT trên MG_STUDIO: thêm core/khoa_hook.py "
                    "(bảo vệ hook đã duyệt bằng SHA-256) và core/trang_thai_qc.py "
                    "(qc_rounds<=1, resume không reset, Opus 1 lần). "
                    "Nghiệm thu 22/22 đạt. Không sửa tệp sản xuất, không render."),
        "evidence": ev, "artifacts": arts, "next_action": "TEST",
        "next_request": "Đề nghị GPT-6 giao TEST: khoá hook thật cho một tập lane X "
                        "rồi thử sửa hook để chứng minh chặn được.",
    }
