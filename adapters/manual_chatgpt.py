# -*- coding: utf-8 -*-
"""adapters/manual_chatgpt.py — chế độ CÓ NGƯỜI THAO TÁC (mặc định của V1).

Luồng:
1. submit(): ghi gói giao việc ra jobs/<job_id>/ (task message + guide + link từng file).
2. Người vận hành mở ChatGPT Web, đính kèm hồ sơ, dán GPT6_TASK_MESSAGE.txt, gửi.
3. Người vận hành tải 4 file kết quả, đóng ZIP, thả vào jobs/_inbox/.
4. collect_result(): Hermes phát hiện ZIP trong inbox và làm NỐT phần còn lại
   (QC -> upload OUTPUT -> xác minh -> COMPLETED).

Đây là chế độ TRUNG THỰC cho V1: không giả vờ tự động hoá phần ChatGPT.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .base import GPTWriterAdapter


class ManualChatGPTAdapter(GPTWriterAdapter):
    name = "manual"
    supports_auto = False

    def __init__(self, cfg: dict, logger=None):
        self.cfg = cfg
        self.log = logger
        mc = (cfg.get("writer") or {}).get("manual") or {}
        self.inbox = Path(mc.get("inbox_dir") or "jobs/_inbox")
        self.timeout_hours = float(mc.get("timeout_hours") or 48)

    # ------------------------------------------------------------ submit
    def submit(self, job: dict, prompt: str) -> dict:
        wd = self.job_workdir(job)
        (wd / "prompt_sent.txt").write_text(prompt, encoding="utf-8")
        (wd / "COPY_PROMPT.txt").write_text(prompt, encoding="utf-8")
        # adapter tự đủ: nếu prepare() chưa ghi thì ghi luôn bản giao việc
        if not (wd / "GPT6_TASK_MESSAGE.txt").exists():
            (wd / "GPT6_TASK_MESSAGE.txt").write_text(prompt, encoding="utf-8")
        self.inbox.mkdir(parents=True, exist_ok=True)
        note = (wd / "MANUAL_ACTION_REQUIRED.txt")
        note.write_text(
            f"""THAO TÁC THỦ CÔNG BẮT BUỘC — {job['job_id']}

1. Mở {wd / 'CHATGPT_WEB_HANDOFF_GUIDE.md'} và làm theo.
2. Dán nội dung {wd / 'GPT6_TASK_MESSAGE.txt'} vào ChatGPT Web (model GPT-6).
3. Tải 4 file kết quả, đóng thành MỘT file ZIP.
4. Thả ZIP vào: {self.inbox.resolve()}
   (tên file bất kỳ; Hermes sẽ đọc job_id bên trong)

Hermes KHÔNG tự gửi prompt và KHÔNG tự tải kết quả ở chế độ này.
""", encoding="utf-8")
        return {"accepted": True, "conversation_url": None, "manual_required": True,
                "supports_auto": self.supports_auto,
                "note": f"chờ người thao tác; thả ZIP vào {self.inbox.resolve()}",
                "submit_dir": str(wd)}

    # ------------------------------------------------------------ status
    def status(self, job: dict) -> dict:
        hit = self._find_inbox_zip(job)
        started = job.get("updated_at") or job.get("created_at") or ""
        elapsed = 0
        try:
            from datetime import datetime, timezone
            t0 = datetime.fromisoformat(started)
            elapsed = int((datetime.now(timezone.utc) - t0).total_seconds())
        except Exception:  # noqa: BLE001
            pass
        if hit:
            return {"state": "result_available", "detail": f"thấy {hit.name}", "elapsed_sec": elapsed}
        if elapsed > self.timeout_hours * 3600:
            return {"state": "failed", "detail": f"quá {self.timeout_hours}h không có kết quả",
                    "elapsed_sec": elapsed}
        return {"state": "waiting", "detail": f"chờ ZIP trong {self.inbox}", "elapsed_sec": elapsed}

    # ------------------------------------------------------------ collect
    def collect_result(self, job: dict) -> dict:
        hit = self._find_inbox_zip(job)
        if not hit:
            return {"zip_path": None, "source": "manual", "note": "chưa có ZIP trong inbox"}
        wd = self.job_workdir(job)
        dest = wd / f"{job['job_id']}_GPT_SCRIPT_DELIVERY.zip"
        if hit.resolve() != dest.resolve():
            shutil.copy2(hit, dest)
            hit.rename(hit.with_suffix(hit.suffix + ".taken"))
        return {"zip_path": str(dest), "source": "manual", "note": f"nhận từ {hit.name}"}

    # ------------------------------------------------------------ helpers
    def _find_inbox_zip(self, job: dict) -> Path | None:
        """Tìm ZIP trong inbox khớp job: ưu tiên tên chứa job_id hoặc episode_id."""
        if not self.inbox.exists():
            return None
        job_id = str(job.get("job_id") or "").lower()
        ep = str(job.get("episode_id") or "").lower()
        zips = sorted(self.inbox.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for z in zips:
            n = z.name.lower()
            if job_id and job_id in n:
                return z
        for z in zips:
            if ep and ep.replace("-", "") in z.name.lower().replace("-", ""):
                return z
        # cuối cùng: nếu inbox chỉ có đúng 1 ZIP và job đang chờ -> nhận
        pending = [z for z in zips if not z.name.lower().endswith(".taken")]
        if len(pending) == 1:
            return pending[0]
        return None

    def wait_for_result(self, job: dict, poll_sec: int = 15) -> dict:
        """Chờ đồng bộ (dùng ở CLI `run --wait`). Không dùng trong vòng lặp nền."""
        deadline = time.time() + self.timeout_hours * 3600
        while time.time() < deadline:
            st = self.status(job)
            if st["state"] in ("result_available", "failed"):
                return st
            time.sleep(poll_sec)
        return {"state": "failed", "detail": "timeout", "elapsed_sec": 0}
