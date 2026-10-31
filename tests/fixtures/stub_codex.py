# -*- coding: utf-8 -*-
"""Stub `codex` CLI để test adapter mà KHÔNG cần đăng nhập / không gọi mạng.

Mô phỏng đúng bề mặt mà CodexCLIWriterAdapter dùng:
    <stub> --version
    <stub> login status
    <stub> exec --skip-git-repo-check -C DIR -s SANDBOX -o FILE -m MODEL

Hành vi điều khiển bằng biến môi trường:
    STUB_CODEX_MODE = ok | missing_one | fail | hang
    STUB_CODEX_RECORD = file ghi lại argv + prompt đã nhận
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

MODE = os.environ.get("STUB_CODEX_MODE", "ok")

FILLER = "その日のことを、まだ覚えている。"


def _hook_from_cwd() -> str:
    """Đọc hook KHOÁ từ hồ sơ đã stage (01_HOOK_OUTLINE.txt) để chép nguyên văn.

    Trả "" nếu không tìm thấy — khi đó QC sẽ báo E_RESULT_HOOK_CHANGED (đúng ý đồ).
    """
    import glob
    for pat in ("**/01_HOOK_OUTLINE.txt", "01_HOOK_OUTLINE.txt"):
        for f in glob.glob(pat, recursive=True):
            try:
                txt = open(f, encoding="utf-8").read()
            except OSError:
                continue
            m = re.search(r"===\s*HOOK[^\n]*===\s*\n(.*?)\n===", txt, re.S)
            if m:
                return m.group(1).strip()
    return ""


def _script() -> str:
    hook = _hook_from_cwd()
    if not hook:
        return (FILLER * 20).strip()
    # hook + thân bài, giữ hook ở ĐẦU file (nguyên văn)
    return hook + "\n\n" + (FILLER * 12).strip()


def _ids_from_prompt(prompt: str) -> tuple[str, str]:
    """Lấy job_id / episode từ prompt.

    Prompt thật có thể là '[GIAO VIỆC GPT-6 — <job_id> · lane N · ep99]' hoặc
    'JOB: <job_id>' / 'TẬP: <episode>'. Thử lần lượt, không phụ thuộc một khuôn.
    """
    jm = (re.search(r"JOB:\s*([A-Za-z0-9\-]+)", prompt)
          or re.search(r"GIAO VIỆC[^\[]*?([A-Za-z0-9]+-[A-Za-z0-9]+-ep\d+-[A-Za-z0-9]+-v\d+)",
                       prompt)
          or re.search(r"([A-Za-z0-9]+-[A-Za-z0-9]+-ep\d+-[A-Za-z0-9]+-v\d+)", prompt))
    em = (re.search(r"TẬP:\s*([A-Za-z0-9\-]+)", prompt)
          or re.search(r"·\s*ep(\d+)", prompt)
          or re.search(r"\bep(\d+)\b", prompt))
    job = jm.group(1) if jm else ""
    if em:
        ep = em.group(1)
        ep = f"ep{ep}" if ep.isdigit() else ep
    else:
        ep = ""
    return job, ep


def main(argv: list[str]) -> int:
    rec = os.environ.get("STUB_CODEX_RECORD")
    if rec:
        with open(rec, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argv, "mode": MODE}, ensure_ascii=False) + "\n")

    if "--version" in argv:
        print("codex-cli 0.147.0-stub")
        return 0

    if "login" in argv and "status" in argv:
        if MODE == "logged_out":
            print("Not logged in")
            return 1
        print("Logged in using ChatGPT")
        return 0

    if "exec" in argv:
        prompt = ""
        if not sys.stdin.isatty():
            prompt = sys.stdin.read()
        if rec:
            with open(rec, "a", encoding="utf-8") as f:
                f.write(json.dumps({"prompt_chars": len(prompt),
                                    "has_drive_link": "drive.google.com" in prompt,
                                    "has_local_hint": "BỎ QUA mọi link Google Drive" in prompt,
                                    }, ensure_ascii=False) + "\n")

        out = None
        for i, a in enumerate(argv):
            if a == "-o" and i + 1 < len(argv):
                out = argv[i + 1]
        if not out:
            print("stub: thiếu -o", file=sys.stderr)
            return 2

        if MODE == "hang":
            time.sleep(120)
            return 0
        if MODE == "fail":
            print("stub: giả lập lỗi xác thực 401", file=sys.stderr)
            return 1

        blocks = []
        job_id, episode = _ids_from_prompt(prompt)
        files = {
            "SCRIPT_FINAL_JP.txt": _script(),
            "SCRIPT_QC_REPORT.md": (
                f"# SCRIPT_QC_REPORT\n- Job: {job_id or '(không rõ)'}\n"
                f"- Tập: {episode or '(không rõ)'}\n- Độ dài: đo bằng công cụ\n"),
            "SCRIPT_ISSUES.md": "# SCRIPT_ISSUES\n- không có\n",
            "SCRIPT_STRUCTURE.md": (
                f"# SCRIPT_STRUCTURE\n- Job: {job_id or '(không rõ)'}\n"
                f"| PHẦN 1 | 300 | khớp |\n"),
        }
        for name, body in files.items():
            if MODE == "missing_one" and name == "SCRIPT_STRUCTURE.md":
                continue
            blocks.append(f"### {name}\n```\n{body}\n```")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n\n".join(blocks) + "\n")
        print("stub: đã ghi câu trả lời cuối")
        return 0

    print(f"stub: argv không nhận diện: {argv}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
