# -*- coding: utf-8 -*-
"""adapters/base.py — giao diện GPTWriterAdapter (spec §10).

Mọi adapter phải trả cùng một cấu trúc để orchestrator/QC/uploader không phải sửa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class GPTWriterAdapter:
    """Hợp đồng tối thiểu. Không adapter nào được tự tuyên bố 'xong' thay cho QC."""

    name = "base"
    supports_auto = False          # True = tự trả file (không cần người thao tác)

    def submit(self, job: dict, prompt: str) -> dict:
        """Giao job cho writer.

        -> {'accepted': bool, 'conversation_url': str|None, 'manual_required': bool,
            'note': str, 'submit_dir': str|None}
        """
        raise NotImplementedError

    def status(self, job: dict) -> dict:
        """-> {'state': 'waiting'|'result_available'|'failed'|'unknown',
               'detail': str, 'elapsed_sec': int}"""
        raise NotImplementedError

    def collect_result(self, job: dict) -> dict:
        """-> {'zip_path': str|None, 'source': 'auto'|'manual', 'note': str}"""
        raise NotImplementedError

    # ------------------------------------------------------------ helpers
    @staticmethod
    def job_workdir(job: dict) -> Path:
        p = Path(job.get("_workdir") or ".")
        p.mkdir(parents=True, exist_ok=True)
        return p


@dataclass
class AdapterResult:
    """Kết quả chuẩn hoá cho mọi adapter (AUTO và MANUAL dùng CHUNG cấu trúc)."""
    zip_path: Path | None = None
    source: str = "manual"
    note: str = ""
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"zip_path": str(self.zip_path) if self.zip_path else None,
                "source": self.source, "note": self.note, "meta": self.meta}
