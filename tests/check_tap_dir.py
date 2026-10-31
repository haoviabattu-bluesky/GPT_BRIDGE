# -*- coding: utf-8 -*-
"""Kiểm bóc đường dẫn tập từ chỉ thị kiểu GPT-6 thật (26/10/2026)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_loop.executor_v2 import _tap_dir  # noqa: E402

TAP = r"E:\CLAUDE\YOUTUBE\MG_STUDIO\episodes\_thu\v2-agentloop"

cases = {
    "trần": TAP,
    "markdown backtick": f"EPISODE_PATH `{TAP}`",
    "có dấu \\ cuối": TAP + "\\",
    "kèm chữ sau": f"Tập cần xử lý: {TAP} (đã kiểm)",
    "dấu chấm cuối": f"Xem {TAP}.",
    "tương đối": r"episodes\_thu\v2-agentloop",
    "ngoài MG": r"E:\CLAUDE\YOUTUBE\h2dev\pipeline",
    "rác": "không có đường dẫn nào",
}
ok = 0
for ten, s in cases.items():
    got = _tap_dir(s)
    print(f"  {ten:22s} -> {got}")
    if ten in ("ngoài MG", "rác"):
        ok += got is None
    else:
        ok += got is not None and got.name == "v2-agentloop"
print(f"\n{ok}/{len(cases)} đúng")
raise SystemExit(0 if ok == len(cases) else 1)
