# -*- coding: utf-8 -*-
"""adapter.py — cài GPTWriterAdapter cho ChatGPT Web (Phase 5).

Đây là adapter DUY NHẤT của Phase 5. Không import Codex CLI, không gọi Sol,
không gọi API writer.

supports_auto = False  ->  Hermes KHÔNG tự gửi prompt, KHÔNG tự tải kết quả.
Thao tác người dùng (mở đúng chat, gửi, bấm tải) là chủ ý thiết kế, không phải
khiếm khuyết: đó là cách duy nhất còn hợp điều khoản và không chiếm desktop.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .prompt import build_extension_prompt, zip_filename
from .store import HandoffStore

# adapters/base.py nằm ngoài package này — nạp theo đường dẫn dự án
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from adapters.base import GPTWriterAdapter  # noqa: E402


class ChatGPTWebHandoffAdapter(GPTWriterAdapter):
    name = "chatgpt_web_handoff"
    supports_auto = False

    def __init__(self, cfg: dict, project_cfg: dict | None = None, logger=None):
        # orchestrator.build_adapter gọi adapter(cfg, log) theo hợp đồng Phase 1–4,
        # nên tham số thứ 2 có thể là logger chứ không phải dict project.
        if project_cfg is not None and not isinstance(project_cfg, dict):
            logger, project_cfg = project_cfg, None
        self.cfg = cfg or {}
        self.log = logger
        root = Path(self.cfg.get("_root") or _ROOT)
        if project_cfg is None:
            try:
                from services.common import project_cfg as _pc
                project_cfg = _pc(self.cfg)
            except Exception:                              # noqa: BLE001
                project_cfg = {}
        self.pc = project_cfg or {}
        self.store = HandoffStore(root)
        # Cấu hình nằm ở writer.chatgpt_web_handoff (khớp config.yaml Phase 1–4).
        # Vẫn nhận key "phase5" để test/khởi tạo nhanh không phải dựng cả cây config.
        writer = self.cfg.get("writer") or {}
        sec = dict(writer.get("chatgpt_web_handoff") or self.cfg.get("phase5") or {})
        self.sec = sec
        self.conversation_url = (
            self.pc.get("conversation_url") or sec.get("conversation_url") or "")
        recv = self.pc.get("receive_dir") or sec.get("receive_dir") or ""
        self.receive_dir = Path(recv) if recv else (Path.home() / "Downloads")
        self.server_url = sec.get("server_url") or "http://127.0.0.1:8765"

    # ------------------------------------------------------------ submit
    def submit(self, job: dict, prompt: str) -> dict:
        """Đưa job vào hàng đợi bàn giao; KHÔNG gửi gì vào ChatGPT."""
        job_id = job["job_id"]
        conv = job.get("conversation_url") or self.conversation_url
        if not conv:
            return {"accepted": False, "conversation_url": None,
                    "manual_required": True,
                    "note": "Thiếu conversation_url trong config — không tự mở chat mới.",
                    "submit_dir": None}

        built = build_extension_prompt({**job, "conversation_url": conv})
        workdir = self.job_workdir(job)
        rec = self.store.enqueue({
            "job_id": job_id,
            "project": self.pc.get("key") or self.cfg.get("project_key"),
            "lane": job.get("lane"),
            "episode_id": job.get("episode_id"),
            "title": job.get("title"),
            "conversation_url": conv,
            "input_zip_url": job.get("input_zip_url"),
            "input_zip_name": job.get("input_zip_name"),
            "output_folder_url": job.get("output_folder_url"),
            "output_zip_name": built["output_zip_name"],
            "required_files": built["required_files"],
            "prompt": prompt or built["prompt"],
            "workdir": str(workdir),
            "receive_dir": str(self.receive_dir),
        })
        return {
            "accepted": True,
            "conversation_url": conv,
            "manual_required": True,
            "note": ("Đã xếp job vào hàng đợi. Mở extension GPT_BRIDGE → mở ĐÚNG "
                     "cuộc hội thoại đã cấu hình → gửi prompt → tải ZIP kết quả."),
            "submit_dir": str(workdir),
            "output_zip_name": rec.get("output_zip_name"),
            "server_url": self.server_url,
        }

    # ------------------------------------------------------------ status
    def status(self, job: dict) -> dict:
        rec = self.store.get(job["job_id"])
        if rec is None:
            return {"state": "unknown", "detail": "chưa có bản ghi bàn giao",
                    "elapsed_sec": 0}
        st = str(rec.get("state", "")).upper()
        has_zip = bool(rec.get("result_zip_path")) and Path(rec["result_zip_path"]).exists()
        mapping = {"QUEUED": "waiting", "OPENED": "waiting", "SENT": "waiting",
                   "DOWNLOADED": "result_available", "COMPLETED": "result_available",
                   "NEEDS_REVIEW": "needs_review", "FAILED": "failed"}
        state = mapping.get(st, "unknown")
        # Có ZIP kết quả trên đĩa thì luôn là result_available, kể cả khi state
        # chưa kịp chuyển (watcher vừa ghi file trước khi set_state).
        if has_zip and state in ("waiting", "unknown"):
            state = "result_available"
        return {"state": state,
                "detail": f"{st}: {rec.get('last_error') or ''}".strip(": "),
                "elapsed_sec": 0,
                "handoff_state": st,
                "result_zip_path": rec.get("result_zip_path")}

    # ------------------------------------------------------------ collect
    def collect_result(self, job: dict) -> dict:
        rec = self.store.get(job["job_id"]) or {}
        p = rec.get("result_zip_path")
        if p and Path(p).exists():
            return {"zip_path": p, "source": "manual",
                    "note": "ZIP do người dùng tải từ ChatGPT, watcher đã QC cấu trúc"}
        return {"zip_path": None, "source": "manual",
                "note": (f"chưa thấy ZIP kết quả trong {self.receive_dir} "
                         f"(chờ {rec.get('output_zip_name') or zip_filename(job['job_id'])})")}
