# -*- coding: utf-8 -*-
"""Tạm đổi writer.adapter -> codex_cli, chạy doctor, rồi KHÔI PHỤC config.

Chỉ dùng để kiểm tra doctor có báo health của codex_cli không.
Luôn khôi phục config trong finally, kể cả khi lỗi.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config.yaml"


def main() -> int:
    orig = CFG.read_text(encoding="utf-8")
    try:
        mod = orig.replace("  adapter: mock\n", "  adapter: codex_cli\n", 1)
        if mod == orig:
            print("KHÔNG đổi được adapter (không thấy 'adapter: mock')")
            return 1
        CFG.write_text(mod, encoding="utf-8")
        print("== doctor với adapter=codex_cli ==")
        r = subprocess.run([sys.executable, "bridge.py", "doctor"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
        for line in out.splitlines():
            if "adapter" in line or "codex" in line.lower() or "verdict" in line:
                print(line)
        return 0
    finally:
        CFG.write_text(orig, encoding="utf-8")
        back = "adapter: mock" in CFG.read_text(encoding="utf-8")
        print(f"== config đã khôi phục: {'OK' if back else 'LỖI'} ==")


if __name__ == "__main__":
    raise SystemExit(main())
