# -*- coding: utf-8 -*-
"""adapters/mock_chatgpt.py — writer giả để kiểm thử TOÀN BỘ pipeline.

Đặc điểm:
- Sinh ZIP kết quả ĐÚNG chuẩn (4 file) từ chính hồ sơ thật: hook lấy nguyên văn
  từ 01_HOOK_OUTLINE.txt, độ dài đúng ngân sách trong 07_WRITING_TASK.md.
- TUYỆT ĐỐI không gửi gì lên mạng.
- KHÔNG BAO GIỜ upload lên OUTPUT thật (orchestrator chặn qua upload.allow_mock_upload).

Đây KHÔNG phải writer thật. Nó chứng minh pipeline chạy đúng, không chứng minh GPT-6 viết được.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from .base import GPTWriterAdapter
from services.zip_qc import count_chars

FILLER = "その日のことを、まだ覚えている。"


class MockChatGPTAdapter(GPTWriterAdapter):
    name = "mock"
    supports_auto = True

    def __init__(self, cfg: dict, logger=None):
        self.cfg = cfg
        self.log = logger

    # ------------------------------------------------------------ submit
    def submit(self, job: dict, prompt: str) -> dict:
        wd = self.job_workdir(job)
        (wd / "prompt_sent.txt").write_text(prompt, encoding="utf-8")
        (wd / "MOCK_NOTICE.txt").write_text(
            "Adapter MOCK: không có cuộc gọi mạng nào. ZIP kết quả do máy sinh để test pipeline.\n"
            "KHÔNG dùng làm sản phẩm thật.\n", encoding="utf-8")
        return {"accepted": True, "conversation_url": None, "manual_required": False,
                "note": "mock accepted (no network)", "submit_dir": str(wd)}

    def status(self, job: dict) -> dict:
        return {"state": "result_available", "detail": "mock luôn sẵn kết quả", "elapsed_sec": 0}

    # ------------------------------------------------------------ result
    def collect_result(self, job: dict) -> dict:
        wd = self.job_workdir(job)
        handoff = job.get("_handoff") or {}
        out = wd / f"{job['job_id']}_GPT_SCRIPT_DELIVERY.zip"
        self._build_zip(out, job, handoff)
        return {"zip_path": str(out), "source": "mock",
                "note": "ZIP do mock sinh — KHÔNG phải bản dịch của GPT-6"}

    # ------------------------------------------------------------ build
    def _build_zip(self, out: Path, job: dict, handoff: dict) -> Path:
        budget = handoff.get("budget") or {}
        hook_text = (handoff.get("hook_text") or "").strip()
        job_id = job["job_id"]
        ep = job.get("episode_id", "ep?")

        target = int(budget.get("target") or budget.get("max") or 5000)
        bmin = int(budget.get("min") or 0)
        bmax = int(budget.get("max") or 0)

        # Ghép thân bài bằng câu đệm tiếng Nhật cho tới khi đạt [bmin, bmax]
        body_lines = []
        chars = count_chars(hook_text) if hook_text else 0
        n = 0
        while chars < max(bmin, target):
            body_lines.append(FILLER)
            chars += count_chars(FILLER)
            n += 1
            if n > 5000:
                break
        script = (hook_text + "\n\n" + "\n".join(body_lines)).strip() if hook_text else "\n".join(body_lines)
        # nếu vượt max thì cắt bớt dòng đệm
        while bmax and count_chars(script) > bmax and body_lines:
            body_lines.pop()
            script = (hook_text + "\n\n" + "\n".join(body_lines)).strip()
        final_chars = count_chars(script)

        qc = f"""# SCRIPT_QC_REPORT — {ep}

> ⚠️ FILE DO ADAPTER **MOCK** SINH ĐỂ KIỂM PIPELINE. KHÔNG PHẢI KỊCH BẢN THẬT.

## Kết quả kiểm tự động
- Job: `{job_id}`
- Tập: `{ep}`
- Độ dài đo: **{final_chars:,} ký tự** (ngân sách {bmin:,}–{bmax:,}, mục tiêu {target:,})
- Hook: {count_chars(hook_text) if hook_text else 0} ký tự, chép nguyên văn từ `01_HOOK_OUTLINE.txt`

## Checklist
| # | Kiểm tra | Kết quả |
|---|---|---|
| 1 | Độ dài | {'PASS' if (not bmin or bmin <= final_chars <= bmax) else 'FAIL'} |
| 2 | Cấu trúc | KHÔNG ĐÁNH GIÁ (mock) |
| 3 | Độ chính xác | KHÔNG ĐÁNH GIÁ (mock) |
| 4 | Nhất quán | KHÔNG ĐÁNH GIÁ (mock) |
| 5 | Hook nguyên văn | {'PASS' if hook_text else 'N/A'} |
"""
        issues = f"""# SCRIPT_ISSUES — {ep}

- Toàn bộ nội dung do adapter MOCK sinh: câu đệm lặp, không có cốt truyện.
- Đây là công cụ kiểm thử đường ống, không phải bản nháp kịch bản.
"""
        structure = f"""# SCRIPT_STRUCTURE — {ep}

| Phần | Ký tự đo | Đối chiếu |
|---|---:|---|
| Hook khoá | {count_chars(hook_text) if hook_text else 0} | 01_HOOK_OUTLINE.txt |
| Thân (mock filler) | {final_chars - (count_chars(hook_text) if hook_text else 0)} | không áp dụng |
| **Tổng** | **{final_chars}** | |
"""
        meta = {"job_id": job_id, "episode_id": ep, "writer": "MOCK",
                "chars": final_chars, "budget": budget,
                "warning": "MOCK OUTPUT — NOT A REAL SCRIPT"}

        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SCRIPT_FINAL_JP.txt", script)
            zf.writestr("SCRIPT_QC_REPORT.md", qc)
            zf.writestr("SCRIPT_ISSUES.md", issues)
            zf.writestr("SCRIPT_STRUCTURE.md", structure)
            zf.writestr("artifact_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        return out
