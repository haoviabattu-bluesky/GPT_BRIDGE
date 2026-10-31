# -*- coding: utf-8 -*-
"""state.py — Máy trạng thái job + khoá độc quyền + checkpoint chống gửi trùng.

Bất biến (mục C yêu cầu):
- KHÔNG hai lượt thực thi đồng thời trên cùng job  -> khoá file + PID.
- Mỗi lượt có checkpoint + lịch sử.
- Chống gửi trùng: mỗi (round, kind) chỉ được gửi 1 lần; muốn gửi lại phải
  `--force-resend` và việc đó được ghi vào lịch sử.
- KHÔNG tự coi phản hồi chưa nhận được là PASS.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .protocol import TERMINAL, now

VALID = ("CREATED", "TASK_READY", "EXECUTING", "REPORT_READY",
         "GPT_REVIEW", "NEXT_TASK", "COMPLETED",
         "BLOCKED", "TRANSPORT_UNAVAILABLE", "NEEDS_OWNER_ACTION")

# Chuyển trạng thái được phép — ép buộc để không nhảy cóc.
# LƯU Ý: TRANSPORT_UNAVAILABLE phải tới được từ MỌI trạng thái chưa kết thúc, vì
# lỗi truyền nhận xảy ra ở bất kỳ lần gửi nào (bẫy đã đo: job smoke-fix chết với
# "không được nhảy NEXT_TASK -> TRANSPORT_UNAVAILABLE" đúng lúc mạng hỏng — sai
# chỗ ghi nhận blocker, không phải sai nghiệp vụ).
ALLOWED = {
    "CREATED":            {"TASK_READY", "BLOCKED", "NEEDS_OWNER_ACTION",
                           "TRANSPORT_UNAVAILABLE"},
    "TASK_READY":         {"EXECUTING", "BLOCKED", "NEEDS_OWNER_ACTION",
                           "TRANSPORT_UNAVAILABLE"},
    "EXECUTING":          {"REPORT_READY", "BLOCKED", "NEEDS_OWNER_ACTION",
                           "TRANSPORT_UNAVAILABLE"},
    "REPORT_READY":       {"GPT_REVIEW", "TRANSPORT_UNAVAILABLE", "BLOCKED",
                           "NEEDS_OWNER_ACTION"},
    "GPT_REVIEW":         {"NEXT_TASK", "COMPLETED", "BLOCKED", "NEEDS_OWNER_ACTION",
                           "TRANSPORT_UNAVAILABLE"},
    "NEXT_TASK":          {"EXECUTING", "COMPLETED", "BLOCKED",
                           "TRANSPORT_UNAVAILABLE", "NEEDS_OWNER_ACTION"},
    "TRANSPORT_UNAVAILABLE": {"REPORT_READY", "NEEDS_OWNER_ACTION", "BLOCKED"},
}


class StateError(RuntimeError):
    pass


class JobState:
    """Một job = một thư mục. Mọi thay đổi ghi nguyên tử."""

    def __init__(self, root: str | Path, job_id: str):
        safe = "".join(c for c in str(job_id) if c.isalnum() or c in "-_")
        if not safe or safe != str(job_id).strip():
            raise ValueError(f"job_id không hợp lệ: {job_id!r}")
        self.job_id = safe
        self.dir = Path(root) / "agent_loop" / "jobs" / safe
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.lock = self.dir / "run.lock"

    # ---------------------------------------------------------- io
    def _write(self, data: dict) -> None:
        data["updated_at"] = now()
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def load(self) -> dict:
        if not self.path.exists():
            return {
                "project_id": "", "job_id": self.job_id, "conversation_id": "",
                "round": 0, "task_status": "CREATED",
                "report_path": "", "report_sha256": "", "next_action": "",
                "created_at": now(), "history": [], "sent": [],
                "checkpoints": [],
            }
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> dict:
        self._write(data)
        return data

    # ---------------------------------------------------------- lock
    def acquire(self, *, stale_sec: int = 6 * 3600) -> None:
        """Khoá độc quyền. Lock cũ quá `stale_sec` hoặc PID đã chết thì thu hồi."""
        if self.lock.exists():
            try:
                info = json.loads(self.lock.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                info = {}
            pid = info.get("pid")
            age = time.time() - float(info.get("at") or 0)
            alive = _pid_alive(pid)
            if alive and age < stale_sec:
                raise StateError(
                    f"job {self.job_id} đang chạy ở PID {pid} "
                    f"({int(age)}s) — KHÔNG chạy song song. Đợi hoặc dừng tiến trình đó.")
            self.lock.unlink(missing_ok=True)
        self.lock.write_text(
            json.dumps({"pid": os.getpid(), "at": time.time(), "host": os.environ.get("COMPUTERNAME", "")}),
            encoding="utf-8")

    def release(self) -> None:
        try:
            info = json.loads(self.lock.read_text(encoding="utf-8"))
            if info.get("pid") == os.getpid():
                self.lock.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            self.lock.unlink(missing_ok=True)

    # ---------------------------------------------------------- transitions
    def transition(self, data: dict, new_state: str, detail: str = "") -> dict:
        new_state = new_state.upper()
        if new_state not in VALID:
            raise StateError(f"trạng thái không hợp lệ: {new_state}")
        cur = str(data.get("task_status") or "CREATED").upper()
        if new_state == cur:
            return data
        if cur in TERMINAL:
            raise StateError(f"job đã ở trạng thái cuối {cur}, không đổi sang {new_state}")
        if new_state not in ALLOWED.get(cur, set()):
            raise StateError(f"không được nhảy {cur} -> {new_state}")
        data["task_status"] = new_state
        data.setdefault("history", []).append(
            {"at": now(), "from": cur, "to": new_state, "detail": str(detail)[:400]})
        return data

    # ---------------------------------------------------------- dedup
    def mark_sent(self, data: dict, *, round_no: int, kind: str, digest: str) -> None:
        data.setdefault("sent", []).append(
            {"at": now(), "round": round_no, "kind": kind, "sha256": digest})

    def already_sent(self, data: dict, *, round_no: int, kind: str) -> dict | None:
        for s in data.get("sent", []):
            if int(s.get("round", -1)) == int(round_no) and s.get("kind") == kind:
                return s
        return None

    def checkpoint(self, data: dict, label: str, **extra) -> dict:
        data.setdefault("checkpoints", []).append(
            {"at": now(), "label": label, "round": data.get("round"),
             "task_status": data.get("task_status"), **extra})
        return self.save(data)


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:
        return False
