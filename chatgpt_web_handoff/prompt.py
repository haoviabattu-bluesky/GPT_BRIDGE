# -*- coding: utf-8 -*-
"""prompt.py — dựng prompt bàn giao ĐẦY ĐỦ cho ChatGPT Web.

Yêu cầu Phase 5 mục 5: prompt phải có
    - job ID
    - đường dẫn ZIP hồ sơ trên Google Drive
    - yêu cầu đọc hồ sơ
    - định dạng ZIP kết quả

Prompt này KHÔNG gửi hộ. Nó được hiển thị trong extension để người dùng
dán/gửi trong ĐÚNG cuộc hội thoại đã cấu hình.
"""
from __future__ import annotations

import re

REQUIRED_FILES = (
    "SCRIPT_FINAL_JP.txt",
    "SCRIPT_QC_REPORT.md",
    "SCRIPT_ISSUES.md",
    "SCRIPT_STRUCTURE.md",
)

# Thứ tự đọc hồ sơ — khớp prompts/h2dev_script_writer.md của Phase 1–4.
READ_ORDER = (
    "10_GPT6_START_HERE.md",
    "07_WRITING_TASK.md",
    "01_HOOK_OUTLINE.txt",
    "02_SCRIPT_RULES.md",
    "05_CONTINUITY.md",
    "03_RESEARCH_FACTS.md",
    "04_CHANNEL_PROFILE.md",
    "06_REFERENCE_SCRIPT.txt",
    "08_QC_CHECKLIST.md",
    "HANDOFF_ISSUES.md",
    "09_HANDOFF_MANIFEST.json",
)


def zip_filename(job_id: str) -> str:
    """Tên ZIP kết quả — BẮT BUỘC đúng để watcher khớp đúng job."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(job_id)).strip("_")
    return f"{safe}_RESULT.zip"


def build_prompt(job: dict, *, output_zip_name: str | None = None,
                 read_order: tuple[str, ...] = READ_ORDER) -> str:
    """job cần: job_id, input_zip_url, input_zip_name (tuỳ chọn), output_folder_url.

    Trả về chuỗi prompt tiếng Việt để người dùng gửi trong ChatGPT.
    """
    job_id = str(job["job_id"])
    zip_url = job.get("input_zip_url") or ""
    zip_name = job.get("input_zip_name") or ""
    out_url = job.get("output_folder_url") or ""
    title = job.get("title") or ""
    lane = job.get("lane") or ""
    ep = job.get("episode_id") or ""
    budget = job.get("budget_text") or ""
    hook_chars = job.get("hook_chars") or 0
    out_zip = output_zip_name or zip_filename(job_id)

    order = "\n".join(f"  {i}. {n}" for i, n in enumerate(read_order, 1))

    header = f"[GIAO VIỆC — {job_id}"
    if lane or ep:
        header += f" · lane {lane or '?'} · {ep or '?'}"
    header += "]"

    zip_line = f"ZIP hồ sơ trên Google Drive: {zip_url}" if zip_url else \
        "ZIP hồ sơ trên Google Drive: (chưa có link — DỪNG, báo lại)"
    name_line = f"Tên file: {zip_name}" if zip_name else ""
    title_line = f"TIÊU ĐỀ DỰ KIẾN: {title}" if title else ""
    budget_line = f"- Số ký tự: {budget}." if budget else \
        "- Số ký tự: theo 07_WRITING_TASK.md."
    hook_line = (f"- Chép NGUYÊN VĂN khối HOOK trong 01_HOOK_OUTLINE.txt "
                 f"({hook_chars} ký tự đo được) làm đoạn mở đầu; KHÔNG sửa một chữ, "
                 f"KHÔNG viết lại khung sườn.") if hook_chars else \
        ("- Chép NGUYÊN VĂN khối HOOK trong 01_HOOK_OUTLINE.txt làm đoạn mở đầu; "
         "KHÔNG sửa một chữ, KHÔNG viết lại khung sườn.")
    out_line = f"Thư mục OUTPUT: {out_url}" if out_url else \
        "Thư mục OUTPUT: (không bắt buộc — nộp bằng ZIP đính kèm)"

    return f"""{header}

Bạn là biên kịch tiếng Nhật của kênh này. Hồ sơ tập đã có trên Google Drive —
hãy ĐỌC HỒ SƠ TRƯỚC KHI VIẾT.

{zip_line}
{name_line}
{title_line}

HỒ SƠ — ĐỌC THEO ĐÚNG THỨ TỰ (toàn bộ nằm trong ZIP ở trên):
{order}

LUẬT BẮT BUỘC:
- Làm đúng 10_GPT6_START_HERE.md và 07_WRITING_TASK.md.
{budget_line}
{hook_line}
- Chỉ dùng dữ kiện có nhãn VERIFIED trong 03_RESEARCH_FACTS.md. Không bịa sự kiện,
  số liệu, nguồn hay kết luận.
- Giữ đúng tên/cách đọc/tuổi/quan hệ/mốc thời gian ở 05_CONTINUITY.md.
- 06_REFERENCE_SCRIPT.txt chỉ để học giọng văn — CẤM chép nội dung.
- Mâu thuẫn trong hồ sơ → ghi vào SCRIPT_ISSUES.md, không tự đoán.
- Nếu KHÔNG mở được ZIP hoặc thiếu file nào ở trên: DỪNG LẠI, nói rõ file nào
  không đọc được, KHÔNG viết tiếp.

KẾT QUẢ — XUẤT ĐÚNG 4 FILE, ĐÓNG GÓI THÀNH MỘT ZIP:
  1. SCRIPT_FINAL_JP.txt    — kịch bản tiếng Nhật thuần (UTF-8), pure prose,
                              không tiêu đề, không marker.
  2. SCRIPT_QC_REPORT.md    — số ký tự đo được + bảng tự kiểm theo 08_QC_CHECKLIST.md.
  3. SCRIPT_ISSUES.md       — chỗ thêm ngoài khung sườn, chỗ không chắc, mâu thuẫn hồ sơ.
  4. SCRIPT_STRUCTURE.md    — bảng: PHẦN · số ký tự đo được · khớp phần nào của khung sườn.

ĐỊNH DẠNG ZIP KẾT QUẢ (bắt buộc):
- Tên file ZIP: {out_zip}
- 4 file nằm NGAY ở gốc ZIP, KHÔNG có thư mục con, KHÔNG đổi tên file.
- ZIP chỉ chứa đúng 4 file trên. Không kèm file tạm, không kèm bản nháp.
- Sau khi xuất ZIP, trả lời ngắn: job ID {job_id}, số ký tự đo được, và
  tên ZIP đã tạo. Tôi sẽ tự tải ZIP bằng nút tải xuống của ChatGPT.

{out_line}
Không sửa/xoá gì trong thư mục hồ sơ.
"""


def build_extension_prompt(job: dict) -> dict:
    """Trả về {'prompt','output_zip_name','required_files'} cho extension."""
    out_zip = job.get("output_zip_name") or zip_filename(job["job_id"])
    return {
        "prompt": build_prompt(job, output_zip_name=out_zip),
        "output_zip_name": out_zip,
        "required_files": list(REQUIRED_FILES),
    }
