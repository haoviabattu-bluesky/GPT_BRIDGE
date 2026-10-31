# -*- coding: utf-8 -*-
"""protocol.py — Định dạng thông điệp CỐ ĐỊNH hai chiều.

GPT-6 -> Hermes : TASK_ASSIGNMENT.md
Hermes -> GPT-6 : EXECUTION_REPORT.md

Thiết kế chống gán nhầm (mục F của yêu cầu):
- Mọi thông điệp có khối header `key: value` (không phải YAML, để GPT viết dễ).
- Bắt buộc có đủ project_id / job_id / round / task_status.
- `report_sha256` trong header phải khớp SHA256 của PHẦN THÂN (sau dấu ---).
  => báo cáo bị sửa tay hoặc của job khác sẽ bị từ chối.
- Có `conversation_id` để kiểm phản hồi thuộc đúng hội thoại.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

REQUIRED_TASK = ("project_id", "job_id", "round", "task_status")
REQUIRED_REPORT = ("project_id", "job_id", "conversation_id", "round",
                   "task_status", "report_sha256")

TASK_STATES = ("CREATED", "TASK_READY", "EXECUTING", "REPORT_READY",
               "GPT_REVIEW", "NEXT_TASK", "COMPLETED",
               "BLOCKED", "TRANSPORT_UNAVAILABLE", "NEEDS_OWNER_ACTION")
TERMINAL = ("COMPLETED", "BLOCKED", "TRANSPORT_UNAVAILABLE", "NEEDS_OWNER_ACTION")

_SPLIT = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ parse
def split_message(raw: str) -> tuple[dict, str]:
    """Tách header `key: value` ở đầu và THÂN sau dòng ---.

    Chấp nhận cả khi GPT quên dòng --- (khi đó thân rỗng, header ăn hết).
    """
    text = (raw or "").replace("\r\n", "\n").strip()
    if not text:
        return {}, ""
    # bỏ tiêu đề markdown "# TASK_ASSIGNMENT" / "# EXECUTION_REPORT"
    text = re.sub(r"^#\s*(TASK_ASSIGNMENT|EXECUTION_REPORT)\s*\n", "", text, flags=re.I)
    parts = _SPLIT.split(text, maxsplit=1)
    head_raw = parts[0]
    body = parts[1].strip() if len(parts) > 1 else ""
    head: dict = {}
    for line in head_raw.splitlines():
        line = line.strip().lstrip("-*").strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if m:
            head[m.group(1).lower()] = m.group(2).strip().strip("`").strip()
    return head, body


class ProtocolError(ValueError):
    """Thông điệp sai định dạng / sai job / bị sửa — KHÔNG được coi là hợp lệ."""


def parse_task(raw: str) -> dict:
    head, body = split_message(raw)
    missing = [k for k in REQUIRED_TASK if not head.get(k)]
    if missing:
        raise ProtocolError(f"TASK_ASSIGNMENT thiếu trường: {missing}")
    try:
        head["round"] = int(head["round"])
    except ValueError:
        raise ProtocolError(f"round không phải số: {head.get('round')!r}") from None
    head["task_status"] = head["task_status"].upper()
    if head["task_status"] not in TASK_STATES:
        raise ProtocolError(f"task_status lạ: {head['task_status']}")
    head["body"] = body
    head["_raw"] = raw
    return head


def parse_report(raw: str, *, expect: dict | None = None) -> dict:
    head, body = split_message(raw)
    missing = [k for k in REQUIRED_REPORT if not head.get(k)]
    if missing:
        raise ProtocolError(f"EXECUTION_REPORT thiếu trường: {missing}")
    try:
        head["round"] = int(head["round"])
    except ValueError:
        raise ProtocolError(f"round không phải số: {head.get('round')!r}") from None
    head["task_status"] = head["task_status"].upper()
    if head["task_status"] not in TASK_STATES:
        raise ProtocolError(f"task_status lạ: {head['task_status']}")

    # CHỐNG GÁN NHẦM: so với job đang chờ
    if expect:
        for k in ("project_id", "job_id"):
            if str(head.get(k)) != str(expect.get(k)):
                raise ProtocolError(
                    f"{k} không khớp: nhận {head.get(k)!r}, cần {expect.get(k)!r} "
                    f"-> báo cáo của job khác, TỪ CHỐI")
        if "round" in expect and head["round"] != int(expect["round"]):
            raise ProtocolError(
                f"round không khớp: nhận {head['round']}, cần {expect['round']}")

    # CHỐNG SỬA TAY: SHA256 của thân phải khớp
    want = str(head.get("report_sha256") or "").lower()
    got = sha256_text(body)
    if want and want != got:
        raise ProtocolError(
            f"report_sha256 không khớp thân bài (khai {want[:12]}…, thật {got[:12]}…)")
    head["body"] = body
    head["sha256"] = got
    head["_raw"] = raw
    return head


# ------------------------------------------------------------------ render
def render_task(*, project_id: str, job_id: str, conversation_id: str, round_no: int,
                task_status: str, next_action: str, goal: str, steps: list[str],
                acceptance: list[str], notes: str = "",
                allowed_actions: list[str] | None = None) -> str:
    """Dựng TASK_ASSIGNMENT.md (Hermes dùng khi CẦN tự tạo nhiệm vụ, và để test).

    `allowed_actions`: danh sách lệnh GPT-6 ĐƯỢC phép trả trong `next_action`.
    Vì sao cần (job mg-v2-body-01, 26/10/2026): không nêu danh sách thì GPT-6 tự chọn
    lệnh legacy (SURVEY/IMPLEMENT/TEST) — job chạy 5 round rồi chết ở NEEDS_OWNER_ACTION
    dù hồ sơ v2 đã đóng gói xong. Không có danh sách => không in mục này (giữ nguyên
    hành vi cũ cho mọi test/job đang dùng).
    """
    steps_md = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
    acc_md = "\n".join(f"- [ ] {a}" for a in acceptance)
    act_block = ""
    if allowed_actions:
        act_md = "\n".join(f"- `{a}`" for a in allowed_actions)
        act_block = f"""
## ALLOWED ACTIONS (next_action CHỈ được là một trong các lệnh này)

{act_md}

Lệnh ngoài danh sách này sẽ bị hệ thống CHẶN. Trả đúng cú pháp:

```loop
next_action: <một lệnh ở trên>
round: {round_no}
task_status: TASK_READY
```
"""
    return f"""# TASK_ASSIGNMENT

project_id: {project_id}
job_id: {job_id}
conversation_id: {conversation_id}
round: {round_no}
task_status: {task_status}
next_action: {next_action}
created_at: {now()}

---

## GOAL

{goal}

## STEPS

{steps_md}

## ACCEPTANCE

{acc_md}
{act_block}{chr(10) + "## NOTES" + chr(10) + chr(10) + notes if notes else ""}
"""


def render_report(*, project_id: str, job_id: str, conversation_id: str, round_no: int,
                  task_status: str, next_action: str, summary: str, evidence: list[str],
                  artifacts: list[dict], next_request: str = "",
                  blocker: str = "") -> tuple[str, str]:
    """Dựng EXECUTION_REPORT.md. Trả (văn bản đầy đủ, sha256 của thân).

    `artifacts` = [{"path":..., "sha256":..., "bytes":...}] — manifest, KHÔNG dán file.
    """
    ev_md = "\n".join(f"- {e}" for e in evidence) or "- (không có)"
    if artifacts:
        art_md = "\n".join(
            f"| `{a['path']}` | {a.get('bytes', '?')} | `{a['sha256'][:16]}…` |"
            for a in artifacts)
        art_block = ("| tệp | bytes | sha256 |\n|---|---|---|\n" + art_md)
    else:
        art_block = "(không có tệp kèm theo)"
    body = f"""## SUMMARY

{summary}

## EVIDENCE

{ev_md}

## ARTIFACTS

{art_block}
{chr(10) + "## BLOCKER" + chr(10) + chr(10) + blocker if blocker else ""}
{chr(10) + "## NEXT_REQUEST" + chr(10) + chr(10) + next_request if next_request else ""}
""".strip() + "\n"
    # SHA tính trên thân ĐÃ STRIP (không có \n cuối) — parser cũng strip trước khi băm,
    # nên hai bên luôn khớp dù file có/không có newline cuối.
    digest = sha256_text(body.strip())
    head = f"""# EXECUTION_REPORT

project_id: {project_id}
job_id: {job_id}
conversation_id: {conversation_id}
round: {round_no}
task_status: {task_status}
report_sha256: {digest}
next_action: {next_action}
created_at: {now()}

---

"""
    return head + body, digest
