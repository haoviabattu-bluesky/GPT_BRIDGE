# -*- coding: utf-8 -*-
"""state.py — SQLite checkpoint cho job. Chống trùng sau restart.

Bảng jobs: một hàng = một job. Khoá duy nhất `row_key` (spreadsheet+sheet+row+file_id+revision)
ngăn tạo trùng khi scanner chạy lại sau restart.

Trạng thái (spec §7):
    DETECTED -> VALIDATING -> READY_TO_SUBMIT -> SUBMITTING -> WAITING_GPT
    -> RESULT_AVAILABLE -> VERIFYING -> UPLOADING -> COMPLETED
    nhánh lỗi: NEEDS_REVIEW | FAILED | BLOCKED

KHÔNG BAO GIỜ đánh dấu COMPLETED chỉ vì đã gửi prompt thành công.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

STATES = ["DETECTED", "VALIDATING", "READY_TO_SUBMIT", "SUBMITTING", "WAITING_GPT",
          "RESULT_AVAILABLE", "VERIFYING", "UPLOADING", "COMPLETED",
          "NEEDS_REVIEW", "FAILED", "BLOCKED"]

TERMINAL = {"COMPLETED", "NEEDS_REVIEW", "FAILED", "BLOCKED"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    project             TEXT NOT NULL,
    lane                TEXT,
    episode_id          TEXT,
    episode_slug        TEXT,
    source_spreadsheet_id TEXT,
    source_sheet_id     INTEGER,
    source_sheet_name   TEXT,
    source_row          INTEGER,
    row_key             TEXT UNIQUE,
    input_zip_file_id   TEXT,
    input_zip_name      TEXT,
    input_zip_url       TEXT,
    input_sha256        TEXT,
    input_folder_id     TEXT,
    output_folder_id    TEXT,
    input_folder_url    TEXT,
    output_folder_url   TEXT,
    conversation_url    TEXT,
    status              TEXT NOT NULL,
    attempt             INTEGER DEFAULT 0,
    output_file_id      TEXT,
    output_sha256       TEXT,
    output_name         TEXT,
    error_code          TEXT,
    error_detail        TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_episode ON jobs(project, lane, episode_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    at         TEXT NOT NULL,
    from_state TEXT,
    to_state   TEXT,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_job ON events(job_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------ job
    def create(self, job: dict) -> dict:
        with self._lock:
            ts = now()
            cols = ["job_id", "project", "lane", "episode_id", "episode_slug",
                    "source_spreadsheet_id", "source_sheet_id", "source_sheet_name",
                    "source_row", "row_key", "input_zip_file_id", "input_zip_name",
                    "input_zip_url", "input_sha256", "input_folder_id", "output_folder_id",
                    "input_folder_url", "output_folder_url", "conversation_url", "status",
                    "attempt", "notes"]
            vals = [job.get(c) for c in cols]
            vals[cols.index("status")] = job.get("status") or "DETECTED"
            vals[cols.index("attempt")] = int(job.get("attempt") or 0)
            try:
                self._conn.execute(
                    f"INSERT INTO jobs ({','.join(cols)}, created_at, updated_at) "
                    f"VALUES ({','.join('?'*len(cols))}, ?, ?)", (*vals, ts, ts))
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                raise ValueError(f"job trùng (row_key/job_id): {e}") from e
        return self.get(job["job_id"])

    def get(self, job_id: str) -> dict | None:
        r = self._conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def by_row_key(self, row_key: str) -> dict | None:
        r = self._conn.execute("SELECT * FROM jobs WHERE row_key=?", (row_key,)).fetchone()
        return dict(r) if r else None

    def by_episode(self, project: str, lane: str, episode_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE project=? AND lane=? AND episode_id=? ORDER BY created_at DESC",
            (project, lane, episode_id)).fetchall()
        return [dict(r) for r in rows]

    def active_for_episode(self, project: str, lane: str, episode_id: str) -> dict | None:
        """Job chưa kết thúc của cùng tập (chặn gửi trùng)."""
        q = ("SELECT * FROM jobs WHERE project=? AND lane=? AND episode_id=? "
             f"AND status NOT IN ({','.join('?'*len(TERMINAL))}) ORDER BY created_at DESC")
        r = self._conn.execute(q, (project, lane, episode_id, *sorted(TERMINAL))).fetchone()
        return dict(r) if r else None

    def list(self, status: str | None = None, limit: int = 200) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update(self, job_id: str, **fields) -> dict:
        if not fields:
            return self.get(job_id)
        allowed = {"status", "attempt", "input_sha256", "input_folder_id", "output_folder_id",
                   "input_folder_url", "output_folder_url", "conversation_url", "output_file_id",
                   "output_sha256", "output_name", "error_code", "error_detail", "notes",
                   "input_zip_file_id", "input_zip_name", "input_zip_url", "episode_slug",
                   "source_row"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cột không cho phép: {bad}")
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {sets}, updated_at=? WHERE job_id=?",
                               (*fields.values(), now(), job_id))
            self._conn.commit()
        return self.get(job_id)

    def transition(self, job_id: str, to_state: str, note: str = "") -> dict:
        if to_state not in STATES:
            raise ValueError(f"trạng thái lạ: {to_state}")
        cur = self.get(job_id)
        if not cur:
            raise KeyError(job_id)
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE job_id=?",
                (to_state, now(), job_id))
            self._conn.execute(
                "INSERT INTO events (job_id, at, from_state, to_state, note) VALUES (?,?,?,?,?)",
                (job_id, now(), cur["status"], to_state, note))
            self._conn.commit()
        return self.get(job_id)

    def events(self, job_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        rows = self._conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def close(self) -> None:
        self._conn.close()


def job_dir(root: Path, job_id: str) -> Path:
    d = Path(root) / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d
