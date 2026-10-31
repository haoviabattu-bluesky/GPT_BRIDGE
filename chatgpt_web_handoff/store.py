# -*- coding: utf-8 -*-
"""store.py — hàng đợi job bàn giao cho ChatGPT Web.

Mỗi job là MỘT file JSON trong `jobs/_handoff/<job_id>.json`. Ghi nguyên tử
(tmp + os.replace) để server/extension/watcher đọc không bao giờ thấy file dở.

Trạng thái:
    QUEUED      Hermes đã dựng prompt, chờ người dùng mở đúng cuộc chat
    OPENED      người dùng đã mở đúng `conversation_url` (extension báo về)
    SENT        người dùng đã bấm gửi trong ChatGPT (extension báo về)
    DOWNLOADED  watcher đã nhận ZIP kết quả trong thư mục nhận hàng
    COMPLETED   orchestrator Phase 1–4 đã QC + upload OUTPUT xong
    NEEDS_REVIEW ZIP nhận được sai danh tính tập / cần người xem trước khi làm tiếp
    FAILED      lỗi — xem `last_error`

KHÔNG có trạng thái nào do module này tự đặt thành COMPLETED: việc đó thuộc
orchestrator/uploader của Phase 1–4 (đã đạt test).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATES = ("QUEUED", "OPENED", "SENT", "DOWNLOADED", "COMPLETED", "NEEDS_REVIEW", "FAILED")
OPEN_STATES = ("QUEUED", "OPENED", "SENT")
RECEIVED_STATES = ("DOWNLOADED",)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HandoffStore:
    """Kho job dạng file. Không dùng DB để giữ module độc lập, dễ kiểm tra tay."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dir = self.root / "jobs" / "_handoff"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ paths
    def path_of(self, job_id: str) -> Path:
        """Chặn path traversal thật: tên file phải nằm gọn trong jobs/_handoff."""
        raw = str(job_id)
        safe = "".join(c for c in raw if c.isalnum() or c in "-_")
        if not safe or safe != raw.strip():
            raise ValueError(f"job_id không hợp lệ: {job_id!r}")
        p = self.dir / f"{safe}.json"
        try:
            p.resolve().relative_to(self.dir.resolve())
        except ValueError:
            raise ValueError(f"job_id không hợp lệ: {job_id!r}") from None
        return p

    # ------------------------------------------------------------ io
    @staticmethod
    def _atomic_write(p: Path, data: dict) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def get(self, job_id: str) -> dict | None:
        p = self.path_of(job_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, job: dict) -> dict:
        job = dict(job)
        job["updated_at"] = utcnow()
        job.setdefault("created_at", job["updated_at"])
        self._atomic_write(self.path_of(job["job_id"]), job)
        return job

    def all(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            if p.name.startswith("."):
                continue
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        out.sort(key=lambda j: j.get("created_at") or "", reverse=True)
        return out

    def by_state(self, *states: str) -> list[dict]:
        want = {s.upper() for s in states}
        return [j for j in self.all() if str(j.get("state", "")).upper() in want]

    # ------------------------------------------------------------ mutate
    def enqueue(self, payload: dict) -> dict:
        """Tạo/cập nhật job. Gọi lại cùng job_id KHÔNG reset lịch sử sự kiện."""
        job_id = payload["job_id"]
        cur = self.get(job_id) or {
            "job_id": job_id,
            "created_at": utcnow(),
            "events": [],
            "state": "QUEUED",
        }
        for k, v in payload.items():
            if k in ("events", "created_at", "state"):
                continue
            cur[k] = v
        if not cur.get("state") or cur.get("state") == "FAILED":
            cur["state"] = "QUEUED"
        cur.setdefault("events", []).append(
            {"at": utcnow(), "kind": "queued", "detail": "Hermes dựng prompt bàn giao"})
        return self.save(cur)

    def set_state(self, job_id: str, state: str, detail: str = "") -> dict:
        state = state.upper()
        if state not in STATES:
            raise ValueError(f"trạng thái không hợp lệ: {state}")
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job["state"] = state
        if detail:
            job.setdefault("events", []).append(
                {"at": utcnow(), "kind": state.lower(), "detail": detail[:500]})
        if state in ("FAILED", "NEEDS_REVIEW"):
            # cả hai trạng thái đều cần lý do để người vận hành đọc được
            job["last_error"] = detail[:500]
        elif state in ("DOWNLOADED", "COMPLETED"):
            # đã nhận được ZIP đúng: lý do của lần hỏng trước không còn đúng nữa,
            # giữ lại sẽ khiến người vận hành tưởng job vẫn đang lỗi.
            job["last_error"] = ""
        return self.save(job)

    def update(self, job_id: str, **fields) -> dict:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.update(fields)
        return self.save(job)

    def add_event(self, job_id: str, kind: str, detail: str = "") -> dict:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.setdefault("events", []).append(
            {"at": utcnow(), "kind": kind, "detail": str(detail)[:500]})
        return self.save(job)

    # ------------------------------------------------------------ view
    @staticmethod
    def public_view(job: dict, *, with_prompt: bool = True) -> dict:
        """Bản rút gọn cho extension. Không chứa đường dẫn máy, không chứa token."""
        keys = ("job_id", "project", "lane", "episode_id", "conversation_url",
                "state", "created_at", "updated_at", "input_zip_url",
                "input_zip_name", "output_folder_url", "required_files",
                "last_error")
        out = {k: job.get(k) for k in keys if job.get(k) is not None}
        out["state"] = job.get("state")
        if with_prompt:
            out["prompt"] = job.get("prompt") or ""
        out["events"] = (job.get("events") or [])[-8:]
        return out
