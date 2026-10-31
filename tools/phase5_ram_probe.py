# -*- coding: utf-8 -*-
"""Đo RAM thật của Codex CLI — CHỈ dùng thư viện chuẩn (không thêm dependency).

Cách đo trên Windows: `tasklist /FO CSV` cho Working Set của từng tiến trình,
`wmic process get ParentProcessId` để gom cả cây tiến trình con (codex spawn node).

Chạy:  .venv/Scripts/python.exe tools/phase5_ram_probe.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _sh(cmd: str) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=30, creationflags=NO_WIN).stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def proc_table() -> dict[int, tuple[int, int]]:
    """-> {pid: (ppid, rss_bytes)}"""
    out: dict[int, tuple[int, int]] = {}
    # Working set từ tasklist (KB)
    txt = _sh("tasklist /FO CSV /NH")
    for row in csv.reader(io.StringIO(txt)):
        if len(row) < 5:
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        mem = row[4].replace("\xa0", " ").replace("K", "").replace(",", "").replace(".", "").strip()
        try:
            out[pid] = (0, int(mem) * 1024)
        except ValueError:
            out[pid] = (0, 0)
    # Parent PID từ wmic
    wtxt = _sh('wmic process get ProcessId,ParentProcessId /format:csv')
    for line in wtxt.splitlines():
        parts = line.strip().split(",")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            pid, ppid = int(parts[1]), int(parts[2])
            if pid in out:
                out[pid] = (ppid, out[pid][1])
    return out


def tree_rss(root_pid: int, table: dict[int, tuple[int, int]]) -> tuple[int, int]:
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    seen, stack = set(), [root_pid]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        stack.extend(kids.get(p, []))
    tot = sum(table[p][1] for p in seen if p in table)
    return tot, len(seen)


def run_measured(cmd: list[str], timeout: int = 180, cwd: str | None = None,
                 stdin_data: bytes | None = None) -> dict:
    t0 = time.time()
    try:
        pr = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_data else None,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              cwd=cwd, creationflags=NO_WIN)
    except FileNotFoundError:
        return {"ok": False, "error": "not_found", "cmd": cmd}
    if stdin_data:
        try:
            pr.stdin.write(stdin_data)
            pr.stdin.close()
        except Exception:  # noqa: BLE001
            pass
    peak, peak_n = 0, 0
    while pr.poll() is None:
        tbl = proc_table()
        tot, n = tree_rss(pr.pid, tbl)
        if tot > peak:
            peak, peak_n = tot, n
        if time.time() - t0 > timeout:
            pr.kill()
            break
        time.sleep(0.25)
    out = (pr.stdout.read() or b"").decode("utf-8", "replace")
    return {"ok": pr.returncode == 0, "rc": pr.returncode, "sec": round(time.time() - t0, 2),
            "peak_mb": round(peak / 1048576, 1), "procs": peak_n,
            "tail": out.strip()[-800:], "cmd": cmd}


def main() -> int:
    me = proc_table().get(os.getpid(), (0, 0))[1]
    print(f"python bridge RSS = {me/1048576:.1f} MB\n")
    res: dict = {}

    r = run_measured(["codex", "--version"], timeout=60)
    res["codex_version"] = r
    print(f"[1] codex --version -> rc={r.get('rc')} {r.get('sec')}s "
          f"peak={r.get('peak_mb')} MB ({r.get('procs')} proc) :: {r.get('tail','')[:60]!r}")

    r = run_measured(["codex", "login", "status"], timeout=60)
    res["codex_login_status"] = r
    print(f"[2] codex login status -> rc={r.get('rc')} {r.get('sec')}s "
          f"peak={r.get('peak_mb')} MB :: {r.get('tail','')[:100]!r}")

    td = Path(os.environ.get("TEMP", ".")) / "codex_ram_probe"
    td.mkdir(parents=True, exist_ok=True)
    last = td / "last.txt"
    if last.exists():
        last.unlink()
    r = run_measured(["codex", "exec", "--skip-git-repo-check", "-C", str(td),
                      "-s", "read-only", "-m", "gpt-6.1-sol", "-o", str(last),
                      "reply with exactly PONG"], timeout=180)
    res["codex_exec"] = r
    print(f"[3] codex exec -> rc={r.get('rc')} {r.get('sec')}s peak={r.get('peak_mb')} MB "
          f"({r.get('procs')} proc)")
    print(f"    tail: {r.get('tail','')[-400:]!r}")
    res["codex_exec"]["last_message"] = (last.read_text(encoding="utf-8")[:500]
                                         if last.exists() else None)
    print(f"    last.txt: {res['codex_exec']['last_message']!r}")

    out = ROOT / "temp" / "phase5_ram_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nđã ghi: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
