# -*- coding: utf-8 -*-
"""cli.py — Lệnh cho chủ dự án. Chạy:

    python -m agent_loop.cli new  --job mg-survey-01 --project MG_STUDIO --goal "..."
    python -m agent_loop.cli run  --job mg-survey-01
    python -m agent_loop.cli show --job mg-survey-01
    python -m agent_loop.cli self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_loop.loop import AgentLoop                 # noqa: E402
from agent_loop.protocol import render_task            # noqa: E402
from agent_loop.state import JobState                 # noqa: E402


def cmd_new(a) -> int:
    js = JobState(ROOT, a.job)
    d = js.load()
    d.update({"project_id": a.project, "round": 1, "task_status": "TASK_READY",
              "next_action": a.action, "conversation_id": a.conversation or "",
              "goal": a.goal})
    # 26/10/2026: thư mục tập + tệp hồ sơ cần ĐÍNH KÈM. Thiếu 2 khoá này thì
    # `_send_task` chỉ gửi đường dẫn Windows -> ChatGPT Web không đọc được -> GPT-6
    # báo "thiếu schema" và lặp vô hạn (job mg-v2-body-09).
    if getattr(a, "tap_dir", None):
        d["tap_dir"] = a.tap_dir
    if getattr(a, "attach_files", None):
        d["attach_files"] = list(a.attach_files)
    d["task_text"] = render_task(
        project_id=a.project, job_id=a.job, conversation_id=a.conversation or "",
        round_no=1, task_status="TASK_READY", next_action=a.action,
        goal=a.goal,
        steps=a.steps or ["Thực hiện đúng mục tiêu trên, thu thập bằng chứng thật."],
        acceptance=a.accept or ["Có bằng chứng chạy thật (số đo, đường dẫn, SHA256)."],
        allowed_actions=getattr(a, "allowed_actions", None))
    js.save(d)
    js.checkpoint(d, "created")
    print(f"Đã tạo job {a.job} ở {js.dir}")
    print(f"task_text: {len(d['task_text'])} ký tự")
    return 0


def cmd_run(a) -> int:
    loop = AgentLoop(ROOT, a.job, conversation_url=a.conversation or "",
                     max_rounds=a.max_rounds, stable_sec=a.stable,
                     timeout_sec=a.timeout)
    st = loop.run()
    print("\n=== KẾT QUẢ ===")
    print(json.dumps({k: st.get(k) for k in
                      ("job_id", "project_id", "conversation_id", "round", "task_status",
                       "report_path", "report_sha256", "next_action")},
                     ensure_ascii=False, indent=2))
    print(f"lịch sử: {len(st.get('history', []))} bước; checkpoint: "
          f"{len(st.get('checkpoints', []))}; đã gửi: {len(st.get('sent', []))}")
    return 0 if st.get("task_status") in ("COMPLETED", "GPT_REVIEW", "NEXT_TASK",
                                          "REPORT_READY") else 1


def cmd_show(a) -> int:
    js = JobState(ROOT, a.job)
    d = js.load()
    print(json.dumps({k: v for k, v in d.items() if k not in ("task_text",)},
                     ensure_ascii=False, indent=2)[:6000])
    return 0


def cmd_self_test(a) -> int:
    from agent_loop.executor import core_self_check
    r = core_self_check()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if not r.get("blocker") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent_loop")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="tạo job + TASK_ASSIGNMENT")
    p.add_argument("--job", required=True)
    p.add_argument("--project", default="MG_STUDIO")
    p.add_argument("--goal", required=True)
    p.add_argument("--action", default="SURVEY")
    p.add_argument("--conversation", default="")
    p.add_argument("--steps", nargs="*")
    p.add_argument("--accept", nargs="*")
    # 26/10/2026: nêu rõ danh sách lệnh được phép — thiếu nó GPT-6 tự chọn lệnh legacy
    # (job mg-v2-body-01 chết ở NEEDS_OWNER_ACTION sau 5 round). Mặc định = None để
    # giữ nguyên hành vi cũ; job v2 truyền đủ 3-4 lệnh của pipeline hook_body_v2.
    p.add_argument("--allowed-actions", nargs="*", default=None)
    # 26/10/2026: hồ sơ phải ĐÍNH KÈM tệp thật (ChatGPT Web không đọc được E:\...)
    p.add_argument("--tap-dir", default=None, help="thư mục tập (suy ra body/*.md để đính kèm)")
    p.add_argument("--attach-files", nargs="*", default=None, help="đường dẫn tệp cần đính kèm")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("run", help="chạy vòng điều phối")
    p.add_argument("--job", required=True)
    p.add_argument("--conversation", default="")
    p.add_argument("--max-rounds", type=int, default=6)
    p.add_argument("--stable", type=int, default=4)
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("show", help="xem trạng thái job")
    p.add_argument("--job", required=True)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("self-test", help="tự kiểm protocol + state")
    p.set_defaults(fn=cmd_self_test)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
