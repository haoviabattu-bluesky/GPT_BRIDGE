# -*- coding: utf-8 -*-
"""prompt_builder.py — dựng chỉ thị giao việc cho GPT-6 từ HỒ SƠ THẬT trong ZIP.

Nguyên tắc:
- Prompt template (prompts/h2dev_script_writer.md) chỉ chứa QUY TẮC CHUNG.
- Yêu cầu CỤ THỂ của tập (job_id, độ dài, hook, danh sách file) lấy từ ZIP:
  09_HANDOFF_MANIFEST.json + 07_WRITING_TASK.md.
- KHÔNG để AI sửa Hook/Outline hay đổi độ dài được giao.
- Sinh 2 sản phẩm (khớp tiền lệ H2DEV đã dùng cho L-ep27 / M-ep18):
    * GPT6_TASK_MESSAGE.txt  — tin nhắn dán vào ChatGPT, có link TỪNG file Drive
    * CHATGPT_WEB_HANDOFF_GUIDE.md — hướng dẫn người vận hành (3 cách nạp hồ sơ)

KHÔNG upload file rời: chỉ dùng link file đã có trong INPUT folder (đã đo thật là có).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import BridgeError

# Thứ tự đọc ưu tiên theo spec §12 + thực tế hồ sơ
READ_ORDER = [
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
]

RE_BUDGET = re.compile(
    r"min\s*\**\s*(?P<min>[\d,\.]+)\s*\**\s*·\s*target\s*\**\s*(?P<target>[\d,\.]+)\s*\**\s*·\s*max\s*\**\s*(?P<max>[\d,\.]+)",
    re.I)


def _num(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s) or 0)


def parse_budget(writing_task: str, qc_checklist: str = "") -> dict:
    """Đọc ngân sách ký tự từ 07_WRITING_TASK.md (dòng 'Số ký tự | min ... target ... max ...')."""
    for text in (writing_task, qc_checklist):
        m = RE_BUDGET.search(text or "")
        if m:
            return {"min": _num(m.group("min")), "target": _num(m.group("target")),
                    "max": _num(m.group("max"))}
    # fallback: "4,535 ≤ ký tự ≤ 5,324"
    m = re.search(r"([\d,]{3,})\s*≤[^\d]{0,30}([\d,]{3,})", qc_checklist or "")
    if m:
        return {"min": _num(m.group(1)), "target": 0, "max": _num(m.group(2))}
    return {"min": 0, "target": 0, "max": 0}


def parse_episode_meta(writing_task: str) -> dict:
    """Lấy Job ID / Episode / Lane / Tiêu đề từ bảng markdown trong 07_WRITING_TASK.md.

    Bẫy thật (đo trên hồ sơ N-ep17): bảng có CẢ dòng "Tiêu đề dự kiến" (tiêu đề video)
    và dòng "Tiêu đề chương | KHÔNG có trong bản đọc..." (quy định định dạng).
    Nếu khớp lỏng "tiêu đề" thì tiêu đề video bị ghi đè bằng câu quy định.
    => chỉ nhận title từ key có "dự kiến"/"video"/"title", bỏ mọi key có "chương"/"section".
    """
    out: dict[str, str] = {}
    for line in (writing_task or "").splitlines():
        if not line.strip().startswith("|"):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 2:
            continue
        key = parts[0].lower()
        val = parts[1].strip("`").strip()
        if "job id" in key:
            out["job_id"] = val
        elif key.startswith("lane"):
            out["lane"] = val
        elif key.startswith("episode"):
            out["episode"] = val
        elif ("tiêu đề" in key or "title" in key) and "chương" not in key \
                and "section" not in key:
            if "dự kiến" in key or "video" in key or "title" in key:
                out.setdefault("title", val)
            else:
                out.setdefault("title_fallback", val)
    if "title" not in out and "title_fallback" in out:
        out["title"] = out.pop("title_fallback")
    return out


def parse_hook(hook_outline: str) -> dict:
    """Tách khối HOOK khoá khỏi 01_HOOK_OUTLINE.txt.

    Định dạng thật:
        === HOOK (KHOÁ — chép nguyên văn ở đầu kịch bản; 297 chữ) ===
        <các dòng hook>
        === KHUNG SƯỜN ===
    """
    lines = (hook_outline or "").splitlines()
    start = end = None
    declared = 0
    for i, ln in enumerate(lines):
        if start is None and re.match(r"^=+\s*HOOK\b", ln.strip(), re.I):
            m = re.search(r"(\d{2,5})\s*(?:chữ|ký tự|chars?)", ln, re.I)
            declared = int(m.group(1)) if m else 0
            start = i + 1
            continue
        if start is not None and re.match(r"^=+\s*(KHUNG|PHẦN|OUTLINE)\b", ln.strip(), re.I):
            end = i
            break
    if start is None:
        return {"text": "", "declared": 0, "measured": 0}
    block = "\n".join(lines[start:end if end is not None else len(lines)]).strip("\n")
    measured = len(re.sub(r"[\s\u3000]", "", block))
    return {"text": block, "declared": declared, "measured": measured}


class PromptBuilder:
    def __init__(self, cfg: dict, project_cfg: dict, logger=None):
        self.cfg = cfg
        self.pc = project_cfg
        self.log = logger
        self.root = Path(__file__).resolve().parent.parent

    def template(self) -> str:
        name = self.pc.get("prompt_template") or "h2dev_script_writer.md"
        p = self.root / "prompts" / name
        if not p.exists():
            raise BridgeError("E_UNKNOWN", f"Không thấy prompt template: {p}")
        return p.read_text(encoding="utf-8")

    def build(self, job: dict, handoff: dict, drive_files: dict[str, dict]) -> dict:
        """-> {'task_message','guide','meta'} .

        job        : bản ghi job (job_id, lane, episode_id, ...)
        handoff    : {'manifest': {...}, 'writing_task': str, 'hook_outline': str,
                      'qc_checklist': str, 'files': {name: text}}
        drive_files: {tên file: {'id','url'}} — link TỪNG file trong INPUT (đã upload sẵn)
        """
        wt = handoff.get("writing_task", "")
        qc = handoff.get("qc_checklist", "")
        manifest = handoff.get("manifest") or {}

        budget = parse_budget(wt, qc)
        meta = parse_episode_meta(wt)
        hook = parse_hook(handoff.get("hook_outline", ""))

        job_id = job["job_id"]
        lane = job.get("lane") or self.pc.get("lane_prefix", "?")
        ep = job.get("episode_id", "?")
        title = meta.get("title") or manifest.get("title", "")
        zip_name = job.get("input_zip_name") or ""
        zip_url = job.get("input_zip_url") or ""
        in_url = job.get("input_folder_url") or ""
        out_url = job.get("output_folder_url") or ""

        # ---- danh sách file theo thứ tự đọc
        # Nếu INPUT chỉ có ZIP (không có file rời) thì KHÔNG được hứa link từng file:
        # phải nói thẳng là dùng ZIP, tránh GPT đi tìm link không tồn tại.
        lines = []
        present = [n for n in READ_ORDER if n in drive_files]
        if present:
            for i, name in enumerate(present, 1):
                lines.append(f"  {i}. {name} — {drive_files[name]['url']}")
            file_list = "\n".join(lines)
            file_intro = "HỒ SƠ — ĐỌC THEO ĐÚNG THỨ TỰ:"
            zip_note = f"(Bản gộp: {zip_name} — {zip_url} — chỉ dùng khi không mở được từng file.)"
            attach_hint = (f"2. Chọn TỪNG file rời trong thư mục hồ sơ ({len(present)} file). "
                           f"Đừng chọn .zip — ChatGPT có thể không giải nén được.")
            attach_hint2 = (f"1. Mở thư mục hồ sơ → chọn {len(present)} file rời → Tải xuống.\n"
                            f"2. Trong ChatGPT, đính kèm {len(present)} file (kéo thả), "
                            f"dán GPT6_TASK_MESSAGE.txt, gửi.")
        else:
            file_list = (f"  ZIP hồ sơ — {zip_url}\n"
                         f"  (thư mục hồ sơ: {in_url})")
            file_intro = ("HỒ SƠ — thư mục INPUT chỉ có MỘT file ZIP; tải/giải nén rồi đọc "
                          "TOÀN BỘ file bên trong theo thứ tự sau:")
            for i, name in enumerate(READ_ORDER, 1):
                lines.append(f"  {i}. {name}")
            file_list = file_list + "\n" + "\n".join(lines)
            zip_note = "(Không có link file rời — hồ sơ chỉ tồn tại trong ZIP ở trên.)"
            attach_hint = ("2. Tải ZIP về, giải nén, rồi đính kèm TẤT CẢ file rời bên trong "
                           "(đừng đính kèm thẳng .zip nếu chưa thử).")
            attach_hint2 = ("1. Tải ZIP hồ sơ về máy → giải nén.\n"
                            "2. Trong ChatGPT, đính kèm toàn bộ file vừa giải nén, "
                            "dán GPT6_TASK_MESSAGE.txt, gửi.")

        budget_txt = (f"{budget['min']:,}–{budget['max']:,} ký tự (mục tiêu {budget['target']:,}; "
                      f"đếm theo 07: KHÔNG tính xuống dòng/khoảng trắng, CÓ tính dấu câu và 「」; "
                      f"hook nằm trong tổng)") if budget["min"] else "theo 07_WRITING_TASK.md"

        hook_note = ""
        if hook["measured"]:
            hook_note = (f"\n- Hook khoá: {hook['measured']} ký tự (hồ sơ khai {hook['declared'] or '?'}). "
                         f"Chép NGUYÊN VĂN, không sửa một chữ.\n")

        task = f"""[GIAO VIỆC GPT-6 — {job_id} · lane {lane} · {ep}]

Bạn là GPT-6, biên kịch tiếng Nhật cho kênh này. Hồ sơ tập đã có trên Google Drive.

TIÊU ĐỀ DỰ KIẾN: {title}

{file_intro}
{file_list}

{zip_note}
Thư mục hồ sơ: {in_url}

LUẬT BẮT BUỘC:
- Làm đúng 10_GPT6_START_HERE.md và 07_WRITING_TASK.md. Số ký tự: {budget_txt}.
- Chép NGUYÊN VĂN khối HOOK trong 01_HOOK_OUTLINE.txt làm đoạn mở đầu; KHÔNG sửa hook, KHÔNG viết lại khung sườn.{hook_note}- Chỉ dùng dữ kiện có nhãn VERIFIED trong 03_RESEARCH_FACTS.md. Không bịa sự kiện, số liệu, nguồn, kết luận.
- Giữ đúng tên/cách đọc/tuổi/quan hệ/mốc thời gian ở 05_CONTINUITY.md.
- 06_REFERENCE_SCRIPT.txt chỉ để học giọng văn — CẤM chép nội dung.
- Mâu thuẫn trong hồ sơ → ghi vào SCRIPT_ISSUES.md, không tự đoán.
- Nếu KHÔNG mở được một file nào ở trên: DỪNG LẠI và nói rõ tên file không đọc được (không viết tiếp).

XUẤT ĐÚNG 4 FILE (mỗi file một khối riêng, ghi rõ tên file ở dòng đầu khối):
  - SCRIPT_FINAL_JP.txt — kịch bản tiếng Nhật thuần (UTF-8), pure prose, không tiêu đề/marker.
  - SCRIPT_QC_REPORT.md — số ký tự đo được + bảng tự kiểm theo 08_QC_CHECKLIST.md.
  - SCRIPT_ISSUES.md — chỗ thêm ngoài khung sườn, chỗ không chắc, mâu thuẫn hồ sơ.
  - SCRIPT_STRUCTURE.md — bảng: PHẦN · số ký tự đo được · khớp phần nào của khung sườn.

NỘP KẾT QUẢ: tải 4 file vào thư mục OUTPUT của tập: {out_url}
Không sửa/xoá gì trong thư mục hồ sơ.
"""

        guide = f"""# Hướng dẫn đưa hồ sơ vào ChatGPT Web — {job_id}

Thư mục hồ sơ (chỉ đọc): {in_url}
Thư mục nộp kết quả: {out_url}

## Cách 1 — Kết nối Google Drive trong ChatGPT (nếu tài khoản có mục này)
1. ChatGPT → đính kèm (+) → "Thêm từ Google Drive" → đăng nhập ĐÚNG Gmail được giao việc.
{attach_hint}
3. Dán nội dung GPT6_TASK_MESSAGE.txt vào ô chat rồi gửi.

## Cách 2 — CHẮC CHẮN NHẤT: tải file rời về máy rồi đính kèm
{attach_hint2}

## Cách 3 — Chỉ khi Cách 1/2 không dùng được
Chỉ đính kèm thẳng .zip khi đã thử thấy ChatGPT đọc được nội dung bên trong
(hỏi nó liệt kê tên file) — không giả định là được.

## Kiểm tra ChatGPT đã đọc đúng hồ sơ (BẮT BUỘC trước khi cho viết)
Hỏi: "Liệt kê tên các file bạn đã đọc và chép lại câu đầu tiên của hook trong 01_HOOK_OUTLINE.txt."
Sai/thiếu → đính kèm lại. Chỉ dán link chia sẻ vào chat KHÔNG đảm bảo ChatGPT đọc được file.

## Nộp kết quả
Tải 4 file ChatGPT xuất ra (SCRIPT_FINAL_JP.txt, SCRIPT_QC_REPORT.md, SCRIPT_ISSUES.md, SCRIPT_STRUCTURE.md)
lên thư mục nộp kết quả. Không sửa/xoá gì trong thư mục hồ sơ.
Kết quả CHƯA được dùng sản xuất cho tới khi qua QC của dự án.
"""

        meta_out = {
            "job_id": job_id, "lane": lane, "episode_id": ep, "title": title,
            "budget": budget, "hook": {"declared": hook["declared"], "measured": hook["measured"]},
            "files": present, "files_in_input": len(present), "zip_name": zip_name,
            "zip_url": zip_url,
            "input_folder_url": in_url, "output_folder_url": out_url,
            "task_message_chars": len(task),
        }
        return {"task_message": task, "guide": guide, "meta": meta_out}

    @staticmethod
    def write(out_dir: Path, built: dict) -> dict[str, Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "task_message": out_dir / "GPT6_TASK_MESSAGE.txt",
            "guide": out_dir / "CHATGPT_WEB_HANDOFF_GUIDE.md",
            "meta": out_dir / "prompt_meta.json",
        }
        paths["task_message"].write_text(built["task_message"], encoding="utf-8")
        paths["guide"].write_text(built["guide"], encoding="utf-8")
        paths["meta"].write_text(json.dumps(built["meta"], ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        return paths
