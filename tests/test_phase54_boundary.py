# -*- coding: utf-8 -*-
"""test_phase54_boundary.py — Phase 5.4: ranh giới trách nhiệm + không phá Phase 5.3.

Chốt với chủ dự án:

* Sau khi upload OUTPUT, bridge CHỈ đánh dấu `GPT_DELIVERED`.
* KHÔNG đánh dấu kịch bản GPT-6 đã được Sol duyệt (`script_approved` phải là False).
* Việc duyệt kịch bản và chạy tiếp vẫn thuộc H2DEV.
* Không dùng Codex CLI / Sol / API writer; writer vẫn là ChatGPT Web.

Cộng thêm: `scan()` của Phase 1–4 phải GIỮ NGUYÊN hành vi (bỏ hàng 'ignore'),
chỉ `scan_gate()` mới thấy hàng RESEARCH OK.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import handoff_coordinator as H
from services import script_gate as G
from services.sheet_scanner import _status_of


# ================================================================ GPT_DELIVERED
def test_coordinator_marks_gpt_delivered_constant():
    """Hằng số đánh dấu duy nhất là GPT_DELIVERED."""
    assert H.GPT_STATE_DELIVERED == "GPT_DELIVERED"


def test_coordinator_has_no_sol_approved_state():
    """Không được có trạng thái 'Sol đã duyệt' trong module bridge."""
    names = {n for n in dir(H) if n.isupper()}
    assert not any("SOL" in n for n in names), sorted(names)
    assert not any("APPROVED" in n for n in names), sorted(names)


def test_delivered_marker_is_written_at_completed():
    """Đọc mã nguồn: `gpt_state=GPT_STATE_DELIVERED` nằm CÙNG khối với COMPLETED."""
    src = Path(H.__file__).read_text(encoding="utf-8")
    i_complete = src.index("self.state.transition(jid, STATUS_COMPLETED")
    i_marker = src.index("gpt_state=GPT_STATE_DELIVERED")
    assert i_marker > i_complete, "phải đánh dấu SAU khi đã xác nhận COMPLETED"
    # và phải đặt script_approved=False ngay cạnh
    window = src[i_marker - 200:i_marker + 300]
    assert "script_approved=False" in window, window


def test_delivered_marker_only_in_success_path():
    """Không được đánh dấu GPT_DELIVERED ở nhánh lỗi/chặn."""
    src = Path(H.__file__).read_text(encoding="utf-8")
    i_marker = src.index("gpt_state=GPT_STATE_DELIVERED")
    i_except = src.index("except BridgeError as e:", i_marker - 4000)
    assert i_marker < i_except, "đánh dấu phải nằm TRƯỚC khối except"


# ================================================================ scanner không đổi
CELL_RESEARCH_OK = ("RESEARCH OK 2026-10-23 12:01 — lane_feed đã tải hồ sơ video gốc "
                    "vào thư mục cột C. Chưa có tiêu đề/kịch bản: phần h2dev làm.")
CELL_SUON_OK = "Đã xong hook+ sườn- cần kịch bản"


def test_phase1_status_of_still_ignores_research_ok():
    """Phase 1–4 KHÔNG được đổi: `_status_of` vẫn coi RESEARCH OK là 'ignore'."""
    rules = {"need_script": ["can kich ban"], "script_done": ["kich ban xong"]}
    st, _ = _status_of(CELL_RESEARCH_OK, rules)
    assert st == "ignore"


def test_scan_and_scan_gate_are_separate_methods():
    """`scan()` giữ nguyên; `scan_gate()` là đường riêng cho preflight."""
    from services.sheet_scanner import SheetScanner
    assert hasattr(SheetScanner, "scan")
    assert hasattr(SheetScanner, "scan_gate")
    assert SheetScanner.scan is not SheetScanner.scan_gate


def test_gate_classifies_suon_ok_that_phase1_misses():
    """Ô K 'Đã xong hook+ sườn' mà Phase 1 coi là need_script thì gate phải thấy SƯỜN OK."""
    rules = {"need_script": ["can kich ban"], "script_done": ["kich ban xong"]}
    assert _status_of(CELL_SUON_OK, rules)[0] == "need_script"      # Phase 1
    assert G.sheet_decision(CELL_SUON_OK, G.markers_from_cfg({}))[0] == G.SHEET_SUON_OK


# ================================================================ không writer ngoài
def test_no_forbidden_writer_modules_in_project_tree():
    """Module Phase 5.4 không kéo Codex CLI / Sol / API writer vào cây import."""
    import ast
    bad_files = []
    for f in (Path(G.__file__), Path(H.__file__)):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.lower() for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.lower()]
            for m in mods:
                if any(x in m for x in ("codex", "openai", "anthropic", "api_writer")):
                    bad_files.append((f.name, m))
    assert not bad_files, bad_files


def test_gate_never_calls_network_itself():
    """Gate không tự gọi HTTP; mọi truy cập Drive đi qua DriveClient đã có."""
    src = Path(G.__file__).read_text(encoding="utf-8")
    for bad in ("requests.", "urllib", "httpx", "http.client", "socket"):
        assert bad not in src, f"script_gate.py không được tự gọi mạng: {bad}"


# ================================================================ hàng đợi chỉ QUEUED
def test_gate_enqueue_sets_state_queued(tmp_path, cfg):
    """Gate chỉ đặt state=QUEUED — không tự chuyển SENT/DOWNLOADED/COMPLETED."""
    from chatgpt_web_handoff.store import HandoffStore
    from services.state import StateStore
    st = HandoffStore(tmp_path)
    ss = StateStore(tmp_path / "x.db")
    gate = G.ScriptGate(cfg, drive=object(), state=ss, handoff=st, root=tmp_path)

    class Req:
        row, sheet_name, episode_slug = 18, "Lane-N", "ep17-x"

    class Drv:
        def web_link(self, fid, kind="file"):
            return f"https://drive.google.com/file/d/{fid}/view"

    gate.drive = Drv()
    rec = gate.enqueue(Req(), lane="N", ep_num=17, project="H2DEV",
                       zip_info={"name": "h.zip", "file_id": "F1", "sha256": "a" * 64,
                                 "hook_chars": 297, "fabel": {"model": "claude-fable-5-1"}})
    assert rec["state"] == "QUEUED"
    assert rec["script_approved"] is False
    assert not rec.get("gpt_state")


def test_gate_does_not_touch_sheet():
    """Không sửa trạng thái H2DEV: module gate không có hàm ghi Sheet."""
    src = Path(G.__file__).read_text(encoding="utf-8")
    for bad in ("values().update", "values().append", "batchUpdate", "spreadsheets().values"):
        assert bad not in src, f"script_gate.py không được ghi Sheet: {bad}"


def test_gate_does_not_create_zip():
    """Không tạo ZIP giả: gate chỉ ĐỌC ZIP, không ZipFile(..., 'w')."""
    src = Path(G.__file__).read_text(encoding="utf-8")
    assert "ZipFile(p, \"w\")" not in src
    assert "zipfile.ZipFile(" in src          # chỉ mở để đọc
    assert "\"w\"" not in src.split("zipfile.ZipFile")[1][:40]
