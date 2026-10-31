# -*- coding: utf-8 -*-
"""test_executor_v2.py — NGHIỆM THU executor Agent Loop của pipeline v2 (mục 9, 26/10/2026).

Kiểm THẬT, không mock: gọi `run_executor` như loop.py gọi, trên tập sandbox.
KHÔNG gọi ChatGPT, KHÔNG gọi LLM.

    cd /e/CLAUDE/YOUTUBE/GPT_BRIDGE && PYTHONPATH= PYTHONIOENCODING=utf-8 py -3.13 -m tests.test_executor_v2
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MG = Path(r"E:\CLAUDE\YOUTUBE\MG_STUDIO")

ok = 0
fail: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")


def _tap(tmp: Path) -> Path:
    """Tập giả đủ để đóng gói hồ sơ + nhận Body.

    Đặt trong `episodes/_thu/` của MG_STUDIO (executor CHỈ được ghi trong MG_STUDIO —
    xem `_guard`), tên có tiền tố test để dọn sạch sau khi chạy.
    """
    d = tmp / "ep-v2-exec"
    (d / "audio").mkdir(parents=True)
    (d / "anh").mkdir()
    (d / "de_tai.json").write_text(json.dumps({
        "lane": "x", "de_tai": "test executor", "urls": [], "thoi_luong_phut": 1,
        "pipeline_version": "hook_body_v2"}, ensure_ascii=False), encoding="utf-8")
    (d / "hook.json").write_text(json.dumps({
        "phuong_an": [], "chon": {"so": 1, "canh": [
            {"loai": "tranh_toan_canh", "loi": [{"vai": "nar", "text": "本能寺を囲みました。"}],
             "data": {"anh": "a.jpg"}}]}}, ensure_ascii=False), encoding="utf-8")
    (d / "dan_y.json").write_text(json.dumps({
        "tom_tat_vi": "Tóm tắt", "ngan_sach": {"target": 100, "lo": 80, "hi": 120},
        "chuong": [{"ten": "C1", "so_chu": 100, "D": ["D1"], "y": "ý", "beat": ["b"],
                    "tom_tat_vi": "tt"}]}, ensure_ascii=False), encoding="utf-8")
    (d / "tu_lieu.json").write_text(json.dumps({"D": [
        {"id": "D1", "url": "http://x", "y": "1582", "trich": "本能寺の変", "so": ["1582"]}]},
        ensure_ascii=False), encoding="utf-8")
    return d


def main() -> int:
    # Executor chỉ ghi trong MG_STUDIO -> tập test phải nằm trong episodes/_thu
    tmp = MG / "episodes" / "_thu" / "_test_executor_v2"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        from agent_loop.executor import V2_ACTIONS, run_executor
        from agent_loop.loop import ACTIONS

        print("== ĐĂNG KÝ LỆNH ==")
        for a in V2_ACTIONS:
            check(f"loop.ACTIONS có {a}", a in ACTIONS)
        check("V2_ACTIONS = 3 lệnh", len(V2_ACTIONS) == 3, str(V2_ACTIONS))

        print("== LỆNH LẠ -> TỪ CHỐI, KHÔNG ĐOÁN ==")
        r = run_executor(ROOT, {"next_action": "KHONG_CO", "instruction": ""}, log=lambda *_: None)
        check("lệnh lạ -> STOP", r.get("next_action") == "STOP")
        check("lệnh lạ -> nêu blocker", "KHONG_CO" in str(r.get("blocker", "")))

        print("== HO_SO_GPT6: THIẾU ĐƯỜNG DẪN -> BLOCKED ==")
        r = run_executor(ROOT, {"next_action": "HO_SO_GPT6", "instruction": "làm gì đó"}, log=lambda *_: None)
        check("thiếu đường dẫn -> STOP", r.get("next_action") == "STOP", r.get("summary", ""))
        check("thiếu đường dẫn -> blocker rõ", "đường dẫn" in str(r.get("blocker", "")))

        print("== HO_SO_GPT6: ĐÓNG GÓI THẬT ==")
        d = _tap(tmp)
        r = run_executor(ROOT, {"next_action": "HO_SO_GPT6",
                                "instruction": f"đóng gói hồ sơ cho {d}"}, log=lambda *_: None)
        check("đóng gói -> KIEM_HO_SO", r.get("next_action") == "KIEM_HO_SO", r.get("summary", ""))
        check("có artifact manifest", any("manifest" in a["path"] for a in r.get("artifacts", [])),
              str([a["path"] for a in r.get("artifacts", [])]))
        check("artifact có SHA256", all(len(a.get("sha256", "")) == 64 for a in r.get("artifacts", [])))
        check("hồ sơ thật tồn tại", (d / "body/BODY_OUTLINE.md").exists())

        print("== KIEM_HO_SO: HỢP LỆ ==")
        r = run_executor(ROOT, {"next_action": "KIEM_HO_SO", "instruction": str(d)}, log=lambda *_: None)
        check("hồ sơ sạch -> VIET_BODY", r.get("next_action") == "VIET_BODY", r.get("summary", ""))

        print("== NHAN_BODY: CHƯA CÓ TỆP -> BLOCKED ==")
        r = run_executor(ROOT, {"next_action": "NHAN_BODY", "instruction": str(d)}, log=lambda *_: None)
        check("thiếu BODY_SCRIPT_JP -> STOP", r.get("next_action") == "STOP", r.get("summary", ""))
        check("nêu rõ thiếu nội dung GPT-6", "chưa giao Body" in str(r.get("blocker", "")),
              str(r.get("blocker", "")))

        print("== NHAN_BODY: BODY CHỨA LẠI HOOK -> CHẶN ==")
        (d / "body").mkdir(exist_ok=True)
        body_dup = {"canh": [
            {"loai": "tranh_toan_canh", "loi": [{"vai": "nar", "text": "本能寺を囲みました。"}],
             "data": {"anh": "a.jpg"}, "src": []},
            {"loai": "tranh_toan_canh", "loi": [{"vai": "nar", "text": "朝、光秀は動いた。"}],
             "data": {"anh": "b.jpg"}, "src": []}]}
        (d / "body/BODY_SCRIPT_JP.txt").write_text(json.dumps(body_dup, ensure_ascii=False), encoding="utf-8")
        r = run_executor(ROOT, {"next_action": "NHAN_BODY", "instruction": str(d)}, log=lambda *_: None)
        check("body trùng hook -> FIX (không ghi)", r.get("next_action") == "FIX", r.get("summary", ""))
        check("nêu lỗi trùng hook", any("TRÙNG hook" in e for e in r.get("evidence", [])),
              str(r.get("evidence", [])[:3]))
        check("KHÔNG ghi canh.json khi lỗi", not (d / "canh.json").exists())

        print("== NHAN_BODY: BODY HỢP LỆ -> GHI canh.json ==")
        body_ok = {"canh": [
            {"loai": "tranh_toan_canh", "loi": [{"vai": "nar", "text": "朝、光秀は動いた。"}],
             "data": {"anh": "b.jpg"}, "src": []},
            {"loai": "tranh_toan_canh", "loi": [{"vai": "nar", "text": "その理由は三つあった。"}],
             "data": {"anh": "c.jpg"}, "src": []}]}
        (d / "body/BODY_SCRIPT_JP.txt").write_text(json.dumps(body_ok, ensure_ascii=False), encoding="utf-8")
        r = run_executor(ROOT, {"next_action": "NHAN_BODY", "instruction": str(d)}, log=lambda *_: None)
        check("body sạch -> KIEM", r.get("next_action") == "KIEM", r.get("summary", ""))
        check("đã ghi canh.json", (d / "canh.json").exists())
        if (d / "canh.json").exists():
            kb = json.loads((d / "canh.json").read_text(encoding="utf-8"))
            check("canh.json có 2 cảnh", len(kb["canh"]) == 2)
            check("canh.json ghi nguồn GPT-6", kb.get("nguon_body") == "gpt-6")
            check("canh.json có body_sha256", len(kb.get("body_sha256", "")) == 64)

        print("== NHAN_BODY: LẤY TỪ CÂU TRẢ LỜI CHATGPT (kênh thật) ==")
        body_chat = {"canh": [
            {"loai": "tranh_toan_canh", "loi": [{"vai": "nar", "text": "朝、光秀は動いた。"}],
             "data": {"anh": "b.jpg"}, "src": []}]}
        tra_loi = ("Đây là phần thân:\n\n```json\n" + json.dumps(body_chat, ensure_ascii=False) + "\n```\n")
        (d / "canh.json").unlink(missing_ok=True)
        (d / "body/BODY_SCRIPT_JP.txt").unlink(missing_ok=True)
        r = run_executor(ROOT, {"next_action": "NHAN_BODY", "instruction": str(d),
                                "last_gpt_reply": tra_loi}, log=lambda *_: None)
        check("trích Body từ câu trả lời chat -> KIEM", r.get("next_action") == "KIEM",
              r.get("summary", ""))
        check("lưu nguyên văn câu trả lời GPT-6", (d / "body/GPT6_LAST_REPLY.md").exists())
        check("đã tạo BODY_SCRIPT_JP.txt từ chat", (d / "body/BODY_SCRIPT_JP.txt").exists())
        check("ghi rõ nguồn chatgpt_web_reply",
              any("chatgpt_web_reply" in e for e in r.get("evidence", [])), str(r.get("evidence", [])))

        print("== CHẶN ĐƯỜNG DẪN NGOÀI PHẠM VI ==")
        from agent_loop.executor_v2 import _guard
        try:
            _guard(Path(r"E:\CLAUDE\YOUTUBE\h2dev"))
            check("chặn H2DEV", False, "(đã cho qua)")
        except PermissionError:
            check("chặn H2DEV", True)
        try:
            _guard(Path(r"C:\Windows"))
            check("chặn ngoài MG_STUDIO", False, "(đã cho qua)")
        except PermissionError:
            check("chặn ngoài MG_STUDIO", True)

        print(f"\n== KẾT QUẢ: {ok} đạt / {len(fail)} hỏng ==")
        for f in fail:
            print("  HỎNG:", f)
        return 0 if not fail else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
