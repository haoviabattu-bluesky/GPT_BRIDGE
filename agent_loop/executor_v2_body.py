# -*- coding: utf-8 -*-
"""NHAN_BODY — nhận Body GPT-6, validate máy, ghi canh.json (tách khỏi executor_v2.py, luật 6).

Nguồn: `body/BODY_SCRIPT_JP.txt`, hoặc `data['last_gpt_reply']` (câu trả lời THẬT của
ChatGPT Web — kênh duy nhất hoạt động vì Web không ghi được file vào đường dẫn Windows).
Không sửa chữ GPT-6 (trừ 3 phép đổi HÌNH DẠNG ở `chuan_cau_truc`). Lỗi -> BLOCKED.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .executor_v2 import MG, _art, _sha, _tap_dir

# ------------------------------------------------------------------ NHAN_BODY
def chuan_cau_truc(obj: dict) -> tuple[dict, list[str]]:
    """Sửa các lỗi CẤU TRÚC rõ ràng mà KHÔNG đổi một chữ nào của GPT-6.

    Chỉ 3 phép, đều là "đổi hình dạng, giữ nguyên văn":
      1. `loi` là CHUỖI  -> tách thành mảng 1 câu (không cắt câu).
      2. `vai` nằm ở cấp cảnh -> dời vào từng câu trong `loi`.
      3. `loi` là mảng CHUỖI -> bọc mỗi chuỗi thành {"vai":..., "text":...}.

    KHÔNG sửa: thiếu `data.*`, sai loại cảnh, quá số câu, số câu thoại sai — những lỗi đó
    phải trả về GPT-6 (nội dung sáng tạo, máy không được tự thêm/bớt).
    """
    sua: list[str] = []
    canh = obj.get("canh")
    if not isinstance(canh, list):
        return obj, sua
    for i, c in enumerate(canh, 1):
        if not isinstance(c, dict):
            continue
        vai_canh = c.pop("vai", None) if isinstance(c.get("vai"), str) else None
        if vai_canh:
            sua.append(f"c{i:02d}: dời 'vai' từ cấp cảnh vào từng câu")
        loi = c.get("loi")
        if isinstance(loi, str):
            c["loi"] = [{"vai": vai_canh or "nar", "text": loi}]
            sua.append(f"c{i:02d}: 'loi' là chuỗi -> bọc thành mảng 1 câu (nguyên văn)")
        elif isinstance(loi, list):
            moi = []
            for L in loi:
                if isinstance(L, str):
                    moi.append({"vai": vai_canh or "nar", "text": L})
                elif isinstance(L, dict):
                    if vai_canh and not L.get("vai"):
                        L = {**L, "vai": vai_canh}
                    moi.append(L)
                else:
                    moi.append(L)
            c["loi"] = moi
    return obj, sua


def validate_body(tap: Path, obj: dict) -> list[str]:
    """Kiểm MÁY (0 LLM) cho Body GPT-6 trả về. Trả danh sách lỗi."""
    loi: list[str] = []
    canh = obj.get("canh")
    if not isinstance(canh, list) or not canh:
        return ["thiếu mảng 'canh'"]
    sys.path.insert(0, str(MG))
    from core.schema_canh import kiem_canh          # noqa: E402
    from core.tap import Tap                         # noqa: E402
    t = Tap(tap)
    vai = set(t.lane["vai"])
    loai = t.lane["loai_canh"]
    ids = {d["id"] for d in t.doc("tu_lieu.json")["D"]}
    loi += kiem_canh(obj, vai, loai, ids)
    # Hook KHÔNG được nằm trong body
    hk = (t.doc("hook.json", {}) or {}).get("chon") or {}
    def norm(s: str) -> str:
        return "".join(s.split())
    hook_cau = {norm(L.get("text", "")) for c in hk.get("canh", []) for L in c.get("loi", [])}
    for i, c in enumerate(canh, 1):
        for L in c.get("loi", []):
            k = norm(L.get("text", ""))
            if len(k) >= 8 and k in hook_cau:
                loi.append(f"c{i:02d}: câu Body TRÙNG hook 「{L.get('text', '')[:30]}」 — Body không được chứa lại hook")
    return loi


def gop_khoi_json(raw: str) -> dict | None:
    """Trích MỌI khối JSON trong văn bản và GỘP mảng `canh`. None nếu không có khối nào.

    Vì sao cần (đo thật 26/10/2026, job mg-v2-body-05): GPT-6 viết bài dài rồi chia
    thành NHIỀU object `{"canh": [...]}` nối tiếp nhau trong cùng một câu trả lời.
    `tach_json` chỉ lấy object ĐẦU (29.184 ký tự) rồi báo lỗi "Extra data" -> mất
    nửa sau của Body. Ở đây quét hết, mỗi object lấy `canh`, nối lại theo thứ tự.

    BẪY THỨ HAI (job mg-v2-body-08): GPT-6 có lúc trả HAI BẢN của cùng một bài
    (c01–c82 rồi c83–c164 y nguyên). Gộp thô -> kịch bản dài gấp đôi, lặp từng câu.
    Nên: khối nào TRÙNG HOÀN TOÀN một khối đã nhận thì bỏ; trong cùng một khối,
    cảnh trùng hoàn toàn cảnh ngay trước cũng bỏ.
    """
    import json as _json
    from json.decoder import JSONDecoder
    dec = JSONDecoder()
    canh: list = []
    da_co_khoi: list[str] = []
    i, n, thay = 0, len(raw), 0
    while i < n:
        j = raw.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(raw, j)
        except ValueError:
            i = j + 1
            continue
        if isinstance(obj, dict) and isinstance(obj.get("canh"), list):
            khoa = _json.dumps(obj["canh"], ensure_ascii=False, sort_keys=True)
            if khoa not in da_co_khoi:
                da_co_khoi.append(khoa)
                for c in obj["canh"]:
                    k2 = _json.dumps(c, ensure_ascii=False, sort_keys=True)
                    if not canh or k2 != _json.dumps(canh[-1], ensure_ascii=False, sort_keys=True):
                        canh.append(c)
                thay += 1
        i = end
    return {"canh": canh} if thay else None


def nhan_body(instruction: str = "", log=print, data: dict | None = None) -> dict:
    """Nhận BODY từ GPT-6 -> validate -> ghi canh.json.

    Nguồn nội dung (theo thứ tự, KHÔNG bịa):
      1. `body/BODY_SCRIPT_JP.txt` nếu có (GPT-6 ghi được file — thường là qua bridge file).
      2. `data['last_gpt_reply']` — câu trả lời THẬT của ChatGPT Web. Đây là kênh duy
         nhất hoạt động khi GPT-6 trả bài trong chat: ChatGPT Web KHÔNG ghi được file
         vào đường dẫn Windows, nên "đã tạo file local" không phải bằng chứng.
         Trích khối JSON trong câu trả lời rồi LƯU NGUYÊN VĂN thành BODY_SCRIPT_JP.txt.

    Không bao giờ tự sửa chữ của GPT-6. Lỗi -> BLOCKED kèm danh sách lỗi để GPT-6 sửa.
    """
    tap = _tap_dir(instruction)
    if not tap:
        return {"summary": "BLOCKED — không xác định được thư mục tập", "evidence": [],
                "artifacts": [], "next_action": "STOP", "blocker": "thiếu đường dẫn tập"}
    f = tap / "body/BODY_SCRIPT_JP.txt"
    nguon = "file"
    if not f.exists():
        tra_loi = str((data or {}).get("last_gpt_reply") or "")
        if not tra_loi.strip():
            return {"summary": f"BLOCKED — chưa có {f} và cũng không có câu trả lời GPT-6 trong job",
                    "evidence": [str(f), "data['last_gpt_reply'] rỗng"], "artifacts": [],
                    "next_action": "STOP", "blocker": "GPT-6 chưa giao Body (không có nội dung nào để nhận)"}
        # Lưu nguyên văn câu trả lời để kiểm chứng về sau
        (tap / "body/GPT6_LAST_REPLY.md").write_text(tra_loi, encoding="utf-8")
        sys.path.insert(0, str(MG))
        obj = gop_khoi_json(tra_loi)
        if obj is None:
            return {"summary": "BLOCKED — câu trả lời GPT-6 không có khối JSON cảnh nào",
                    "evidence": [f"độ dài trả lời: {len(tra_loi)}"],
                    "artifacts": [_art(tap / "body/GPT6_LAST_REPLY.md")],
                    "next_action": "FIX", "blocker": "không thấy object {\"canh\": [...]}",
                    "next_request": "GPT-6 giao lại Body dạng JSON cảnh (khối ```json ... ```)"}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        nguon = "chatgpt_web_reply"
        log(f"[NHAN_BODY] lấy Body từ câu trả lời GPT-6 ({len(obj['canh'])} cảnh) -> {f}")
    raw = f.read_text(encoding="utf-8", errors="replace")
    sys.path.insert(0, str(MG))
    obj = gop_khoi_json(raw)
    if obj is None:
        return {"summary": "BLOCKED — BODY_SCRIPT_JP.txt không có khối JSON cảnh", "evidence": [],
                "artifacts": [_art(f)], "next_action": "STOP", "blocker": "không thấy object {\"canh\": [...]}"}
    obj, sua_cau_truc = chuan_cau_truc(obj)
    if sua_cau_truc:
        log(f"[NHAN_BODY] chuẩn hoá cấu trúc {len(sua_cau_truc)} chỗ (không đổi chữ)")
        f.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    loi = validate_body(tap, obj)
    if loi:
        # BẪY (MG 29/10/2026): để nguyên tệp hỏng thì round NHAN_BODY sau đọc LẠI bản cũ
        # (nguồn 'file' ưu tiên trước câu trả lời chat) dù GPT-6 đã gửi bản sửa -> lặp lỗi.
        # Cất đi để round sau lấy bản mới (sandbox hoặc chat).
        import time as _t
        cat = f.with_name(f"BODY_SCRIPT_JP.loi_{_t.strftime('%Y%m%d-%H%M%S')}.txt")
        f.rename(cat)
        f = cat
        return {"summary": f"BLOCKED — Body chưa đạt {len(loi)} lỗi kiểm máy, KHÔNG ghi canh.json",
                "evidence": [f"tập: {tap}"] + [f"- {x}" for x in loi[:12]],
                "artifacts": [_art(f)], "next_action": "FIX",
                "next_request": "GPT-6 sửa đúng các lỗi trên rồi giao lại BODY_SCRIPT_JP.txt",
                "blocker": "; ".join(loi[:6])}
    # ghi canh.json (giữ tieu_de/loi_hua nếu có)
    cu = {}
    p = tap / "canh.json"
    if p.exists():
        try:
            cu = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cu = {}
    for i, c in enumerate(obj["canh"], 1):
        c["id"] = f"c{i:02d}"
    kb = {**cu, "canh": obj["canh"], "nguon_body": "gpt-6", "body_sha256": _sha(f)}
    p.write_text(json.dumps(kb, ensure_ascii=False, indent=1), encoding="utf-8")
    doc = tap / "canh_s2_goc.json"
    doc.write_text(json.dumps(kb, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"summary": f"Nhận Body GPT-6: {len(obj['canh'])} cảnh, kiểm máy 0 lỗi, đã ghi canh.json",
            "evidence": [f"tập: {tap}", f"cảnh: {len(obj['canh'])}", f"nguồn: {nguon}",
                         f"canh.json: {_sha(p)[:16]}"],
            "artifacts": [_art(f), _art(p)],
            "next_action": "KIEM",           # chặng kiem v2 (QC có trần) chạy tiếp
            "next_request": "Chạy mg.py kiem để QC Body theo thứ tự Sonnet -> GPT-6 (1) -> Opus (1)"}
