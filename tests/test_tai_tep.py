# -*- coding: utf-8 -*-
"""test_tai_tep.py — NGHIỆM THU cơ chế tải tệp GPT-6 từ sandbox ChatGPT.

Kiểm phần LOGIC THUẦN (không cần Chrome, không cần mạng):
  - `RE_SANDBOX` bắt đúng link `sandbox:/mnt/data/...`
  - `tim_message_co_tep` duyệt cả `messages` (định dạng mới) và `mapping` (cũ)
  - `tai_body_tu_sandbox` bỏ qua an toàn khi thiếu `conversation_id`/`tap_dir`
  - `tai_tep_sandbox` trả `loi` rõ ràng khi page lỗi (không ném ra ngoài)

Phần cần Chrome thật đã đo riêng 26/10/2026 (xem `GPT6_SANDBOX_FILE_TRANSFER.md`):
tải đúng 99.740 byte, SHA `bdfa9c08…` trùng khít giá trị GPT-6 tự khai.
KHÔNG mock kết quả đó ở đây.

    cd /e/CLAUDE/YOUTUBE/GPT_BRIDGE && PYTHONPATH= PYTHONIOENCODING=utf-8 py -3.13 -m tests.test_tai_tep
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_loop.tai_tep import (  # noqa: E402
    RE_SANDBOX,
    tai_body_tu_sandbox,
    tai_tep_sandbox,
    tim_message_co_tep,
)

ok = 0
fail: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    if cond:
        ok += 1
        print(f"  PASS {name}")
    else:
        fail.append(name)
        print(f"  FAIL {name} {detail}")


def main() -> int:
    print("== RE_SANDBOX ==")
    check("bat link /mnt/data co duoi .txt",
          RE_SANDBOX.findall("xem [Truy cập BODY_SCRIPT_JP.txt](sandbox:/mnt/data/BODY_SCRIPT_JP.txt) y")
          == ["/mnt/data/BODY_SCRIPT_JP.txt"])
    check("bat link trong code block",
          RE_SANDBOX.findall('p="/mnt/data/a.txt" va sandbox:/mnt/data/b.txt') == ["/mnt/data/b.txt"])
    check("bo qua chuoi khong phai sandbox",
          RE_SANDBOX.findall("sandbox ngoai le /mnt/data/x.txt") == [])
    check("khong bat khi thieu /mnt/data",
          RE_SANDBOX.findall("sandbox:/home/u/x.txt") == [])

    print("== tim_message_co_tep ==")
    msgs = [
        {"id": "m1", "content": "khong lien quan"},
        {"id": "m2", "content": "xem sandbox:/mnt/data/BODY_SCRIPT_JP.txt nhe"},
        {"id": "m3", "content": "xem sandbox:/mnt/data/OTHER.txt nhe"},
        {"id": "m4", "content": "lai la sandbox:/mnt/data/BODY_SCRIPT_JP.txt"},
    ]
    got = tim_message_co_tep({"messages": msgs}, "BODY_SCRIPT_JP.txt")
    check("tim dung 2 message theo thu tu", got == ["m2", "m4"], f"-> {got}")
    check("bo qua tep khac ten",
          tim_message_co_tep({"messages": msgs}, "OTHER.txt") == ["m3"])
    check("khong co -> rong", tim_message_co_tep({"messages": msgs}, "KHONG_CO.txt") == [])
    check("hoi thoai rong -> rong", tim_message_co_tep({}, "BODY_SCRIPT_JP.txt") == [])
    # dinh dang CU: mapping (khong co 'messages')
    cu = {"mapping": {
        "a": {"message": {"id": "old1", "content": "sandbox:/mnt/data/BODY_SCRIPT_JP.txt"}},
        "b": {"message": {"id": "old2", "content": "khong"}},
    }}
    check("doc duoc dinh dang CU (mapping)",
          tim_message_co_tep(cu, "BODY_SCRIPT_JP.txt") == ["old1"])
    # message khong phai dict -> khong vo
    check("chiu duoc phan tu rac",
          tim_message_co_tep({"messages": [None, "chuoi", {"id": "m9",
                              "content": "sandbox:/mnt/data/BODY_SCRIPT_JP.txt"}]},
                             "BODY_SCRIPT_JP.txt") == ["m9"])

    print("== tai_body_tu_sandbox: bỏ qua an toàn ==")
    class PageGia:
        """Page gia: ghi lai xem co bi goi khong."""
        def __init__(self): self.goi = 0
        def on(self, *a, **k): self.goi += 1
        def goto(self, *a, **k): self.goi += 1
        def wait_for_timeout(self, *a, **k): self.goi += 1

    pg = PageGia()
    check("thieu conversation_id -> tra nguyen data",
          tai_body_tu_sandbox(pg, {"tap_dir": "C:/tmp"}, log=lambda *_: None)
          == {"tap_dir": "C:/tmp"})
    check("thieu tap_dir -> tra nguyen data",
          tai_body_tu_sandbox(pg, {"conversation_id": "abc"}, log=lambda *_: None)
          == {"conversation_id": "abc"})
    check("thieu ca hai -> khong cham vao page", pg.goi == 0, f"-> {pg.goi}")

    print("== tai_tep_sandbox: lỗi page -> trả loi, KHÔNG ném ==")
    pg2 = PageGia()
    kq = tai_tep_sandbox(pg2, "cid-gia", "BODY_SCRIPT_JP.txt")
    check("ok=False khi khong bat duoc hoi thoai", kq["ok"] is False)
    check("co truong 'loi' mo ta", bool(kq["loi"]) and "thoại" in kq["loi"], f"-> {kq['loi']!r}")
    check("bytes/sha256 rong", kq["bytes"] == 0 and kq["sha256"] == "")
    check("du 6 truong ket qua",
          set(kq) == {"ok", "bytes", "sha256", "noi_dung", "ten_tep_goc", "message_id", "loi"})

    print("== tai_body_tu_sandbox: ghi tệp THẬT khi tải được ==")
    import base64 as _b64
    import hashlib as _hl
    import tempfile
    from pathlib import Path as _P

    NOI_DUNG = '{"canh":[{"loai":"x"}]}'
    SHA = _hl.sha256(NOI_DUNG.encode()).hexdigest()

    class _Resp:
        url = "https://chatgpt.com/backend-api/conversations/abc-123?num_turns=20"
        def json(self):
            return {"messages": [{"id": "mid-1",
                                  "content": "xem sandbox:/mnt/data/BODY_SCRIPT_JP.txt nhe"}]}

    class PageThat(PageGia):
        """Mô phỏng ĐÚNG hợp đồng: `on('response', cb)` rồi `goto()` kích cb."""
        def on(self, ev, cb):
            if ev == "response":
                self.cb = cb
        def goto(self, *a, **k):
            if getattr(self, "cb", None):
                self.cb(_Resp())
        def evaluate(self, js, arg=None):
            return {"sha": SHA, "bytes": len(NOI_DUNG.encode()),
                    "b64": _b64.b64encode(NOI_DUNG.encode()).decode(),
                    "name": "BODY_SCRIPT_JP.txt"}

    with tempfile.TemporaryDirectory() as tmp:
        d = tai_body_tu_sandbox(
            PageThat(), {"conversation_id": "https://chatgpt.com/c/abc-123?x=1", "tap_dir": tmp},
            log=lambda *_: None)
        info = d.get("body_tep_tai_ve") or {}
        f = _P(info["path"]) if info.get("path") else None
        check("có body_tep_tai_ve", bool(info))
        check("ghi đúng <tap>/body/BODY_SCRIPT_JP.txt",
              f is not None and f.parent.name == "body" and f.name == "BODY_SCRIPT_JP.txt",
              f"-> {info.get('path')}")
        check("byte trên đĩa khớp", f is not None and f.stat().st_size == len(NOI_DUNG.encode()))
        check("SHA trong data khớp nội dung", info.get("sha256") == SHA)
        check("nội dung nguyên văn", f is not None and f.read_text(encoding="utf-8") == NOI_DUNG)
        check("bóc đúng uuid khỏi URL (?x=1 bị cắt)", info.get("message_id") == "mid-1")
        check("giữ tên tệp gốc", info.get("ten_goc") == "BODY_SCRIPT_JP.txt")

    print("== tai_body_tu_sandbox: page nem loi -> khong vo ==")
    class PageNem(PageGia):
        def on(self, *a, **k): raise RuntimeError("CDP chet")
    d = {"conversation_id": "https://chatgpt.com/c/uuid-abc?x=1", "tap_dir": "C:/tmp"}
    ra = tai_body_tu_sandbox(PageNem(), d, log=lambda *_: None)
    check("tra ve chinh dict da cho (khong them body_tep_tai_ve)",
          ra is d and "body_tep_tai_ve" not in ra)

    print(f"\n{ok} PASS, {len(fail)} FAIL")
    if fail:
        print("FAIL:", fail)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
