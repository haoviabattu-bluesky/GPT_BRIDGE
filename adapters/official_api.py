# -*- coding: utf-8 -*-
"""adapters/official_api.py — điểm tích hợp API chính thức (MẶC ĐỊNH TẮT).

Chưa có ngân sách API (spec §10). Module này chỉ là chỗ nối, KHÔNG được bật
nếu chưa được duyệt. Bật lên cũng KHÔNG có nghĩa là đã kiểm chứng.

Khi được duyệt:
- Đặt writer.official_api.enabled = true
- Đặt biến môi trường OPENAI_API_KEY
- Đặt writer.adapter = official_api
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

from .base import GPTWriterAdapter
from services.errors import BridgeError

NEED = ["SCRIPT_FINAL_JP.txt", "SCRIPT_QC_REPORT.md", "SCRIPT_ISSUES.md", "SCRIPT_STRUCTURE.md"]


class OfficialAPIAdapter(GPTWriterAdapter):
    name = "official_api"
    supports_auto = True

    def __init__(self, cfg: dict, logger=None):
        self.cfg = cfg
        self.log = logger
        w = (cfg.get("writer") or {}).get("official_api") or {}
        self.enabled = bool(w.get("enabled", False))
        self.base_url = w.get("base_url", "https://api.openai.com/v1")
        self.key_env = w.get("key_env", "OPENAI_API_KEY")
        self.model = w.get("model", "gpt-6")

    def _check(self):
        if not self.enabled:
            raise BridgeError("E_ADAPTER_NOT_READY",
                              "writer.official_api.enabled=false — chưa được duyệt ngân sách API")
        if not os.environ.get(self.key_env):
            raise BridgeError("E_ADAPTER_NOT_READY", f"thiếu biến môi trường {self.key_env}")

    def submit(self, job: dict, prompt: str) -> dict:
        self._check()
        import requests
        wd = self.job_workdir(job)
        r = requests.post(
            f"{self.base_url}/responses",
            headers={"Authorization": f"Bearer {os.environ[self.key_env]}",
                     "Content-Type": "application/json"},
            json={"model": self.model, "input": prompt}, timeout=300)
        if r.status_code >= 400:
            raise BridgeError("E_ADAPTER_BLOCKED", f"HTTP {r.status_code}: {r.text[:400]}")
        data = r.json()
        (wd / "api_response.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
        return {"accepted": True, "conversation_url": data.get("id"), "manual_required": False,
                "note": f"API {self.model} id={data.get('id')}", "submit_dir": str(wd)}

    def status(self, job: dict) -> dict:
        return {"state": "result_available", "detail": "API trả đồng bộ", "elapsed_sec": 0}

    def collect_result(self, job: dict) -> dict:
        wd = self.job_workdir(job)
        p = wd / "api_response.json"
        if not p.exists():
            return {"zip_path": None, "source": "auto", "note": "chưa có api_response.json"}
        data = json.loads(p.read_text(encoding="utf-8"))
        text = data.get("output_text") or ""
        if not text:
            chunks = []
            for item in data.get("output", []):
                for c in item.get("content", []):
                    if c.get("type") in ("output_text", "text"):
                        chunks.append(c.get("text", ""))
            text = "\n".join(chunks)
        from .chatgpt_web import ChatGPTWebAdapter
        files = ChatGPTWebAdapter._split_files(text)
        missing = [n for n in NEED if n not in files]
        if missing:
            (wd / "api_raw.txt").write_text(text, encoding="utf-8")
            return {"zip_path": None, "source": "auto",
                    "note": f"thiếu khối {missing}; đã lưu api_raw.txt"}
        out = wd / f"{job['job_id']}_GPT_SCRIPT_DELIVERY.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for n in NEED:
                zf.writestr(n, files[n])
            zf.writestr("artifact_meta.json", json.dumps(
                {"job_id": job["job_id"], "source": "official_api", "model": self.model},
                ensure_ascii=False, indent=2))
        return {"zip_path": str(out), "source": "auto", "note": "API output"}

    def health(self) -> dict:
        try:
            self._check()
            return {"ok": True, "model": self.model, "note": "đã cấu hình (chưa gọi thử)"}
        except BridgeError as e:
            return {"ok": False, "code": e.code, "detail": e.detail}
