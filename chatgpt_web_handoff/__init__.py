# -*- coding: utf-8 -*-
"""chatgpt_web_handoff — Phase 5: bàn giao job cho ChatGPT Web ĐÚNG cuộc hội thoại.

MỤC TIÊU
    Writer của Phase 5 là ChatGPT Web, trong CHÍNH cuộc hội thoại mà chủ dự án
    chỉ định (để dùng ngữ cảnh đã thiết lập, Memory và Google Drive connector).
    KHÔNG dùng Codex CLI, KHÔNG dùng model `gpt-6.1-sol`, KHÔNG tiêu quota Sol,
    KHÔNG gọi API writer.

NGUYÊN TẮC (đúng yêu cầu Phase 5)
    1. Không tự mở chat mới: mọi thao tác đi qua `conversation_url` cấu hình sẵn.
    2. Không trích xuất dữ liệu/artifact từ ChatGPT Web bằng mã trái điều khoản.
       Kết quả do NGƯỜI DÙNG tải bằng nút tải xuống của ChatGPT.
    3. Không lấy cookie/token, không vượt CAPTCHA, không tự động hoá profile Chrome
       chính khi chưa được cho phép (mặc định: KHÔNG).
    4. Không chiếm chuột/bàn phím: không pyautogui, không CDP điều khiển Chrome
       của người dùng. Kênh duy nhất giữa extension và Hermes là HTTP trên
       127.0.0.1 (có token).

THÀNH PHẦN
    store.py    — hàng đợi job (JSON, ghi nguyên tử)
    prompt.py   — dựng prompt đầy đủ: job ID + link ZIP Drive + định dạng ZIP ra
    server.py   — HTTP localhost cho extension
    watcher.py  — tự phát hiện ZIP trong thư mục nhận hàng, khớp job
    adapter.py  — ChatGPTWebHandoffAdapter (cài GPTWriterAdapter của Phase 1–4)

Module này CHỈ ĐỌC/THÊM; không sửa module Phase 1–4 nào.
"""
from __future__ import annotations

__version__ = "5.0.0"

ADAPTER_NAME = "chatgpt_web_handoff"

__all__ = ["ADAPTER_NAME", "__version__"]
