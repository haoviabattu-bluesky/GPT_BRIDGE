# -*- coding: utf-8 -*-
"""agent_loop — trao đổi hai chiều nhiều lượt GPT-6 <-> Hermes qua ChatGPT Web.

Kênh: Chrome RIÊNG (CHATGPT_PROFILE) + CDP cổng 9335. Không pyautogui,
không đọc cookie, không vượt CAPTCHA, không cướp focus.

Dùng nhanh:
    from agent_loop import AgentLoop
    AgentLoop(root, "job-01", conversation_url="").run()
"""
from __future__ import annotations

from .executor import run_executor           # noqa: F401
from .loop import AgentLoop, parse_directive  # noqa: F401
from .protocol import (ProtocolError, parse_report, parse_task,  # noqa: F401
                       render_report, render_task, sha256_file, sha256_text)
from .send_recv import ChatSession            # noqa: F401
from .state import JobState, StateError       # noqa: F401
from .transport import ChatGPTransport, TransportError  # noqa: F401

__all__ = [
    "AgentLoop", "ChatSession", "ChatGPTransport", "JobState",
    "ProtocolError", "StateError", "TransportError",
    "parse_directive", "parse_report", "parse_task",
    "render_report", "render_task", "run_executor",
    "sha256_file", "sha256_text",
]
__version__ = "1.0.0"
