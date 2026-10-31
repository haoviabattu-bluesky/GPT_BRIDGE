# -*- coding: utf-8 -*-
"""test_run_scripts.py — cho `pytest tests/` chạy được các nghiệm thu kiểu SCRIPT.

Vì sao cần: `test_executor_v2.py` và `test_tai_tep.py` viết theo kiểu SCRIPT (dùng
`main()` + `check()`, in kết quả, thoát bằng mã lỗi) chứ không có hàm `test_*`, nên
`pytest tests/` bỏ qua chúng — dễ tưởng là đã phủ test trong khi thật ra chưa chạy.

File này bọc chúng thành test thật: chạy tiến trình con, đòi mã thoát 0.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ("test_tai_tep.py", "test_executor_v2.py")


def _env() -> dict:
    e = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    e["PYTHONIOENCODING"] = "utf-8"
    return e


@pytest.mark.parametrize("ten", SCRIPT)
def test_script_nghiem_thu(ten: str) -> None:
    """Chạy nghiệm thu kiểu script; đòi exit 0 và có dòng tổng kết PASS."""
    f = ROOT / "tests" / ten
    assert f.exists(), f"thiếu {f}"
    r = subprocess.run([sys.executable, str(f)], cwd=str(ROOT), env=_env(),
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"{ten} thất bại (rc={r.returncode}):\n{out[-3000:]}"
    assert "PASS" in out, f"{ten} không in dòng PASS nào:\n{out[-2000:]}"
