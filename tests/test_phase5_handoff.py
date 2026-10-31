# -*- coding: utf-8 -*-
"""test_phase5_handoff.py — Phase 5: chatgpt_web_handoff.

Kiểm chứng những gì ĐO ĐƯỢC, không hứa gì thêm:
    - prompt có đủ job ID + link ZIP Drive + yêu cầu đọc hồ sơ + định dạng ZIP ra
    - KHÔNG có đường nào tự mở chat mới (thiếu conversation_url -> từ chối)
    - adapter báo supports_auto=False, manual_required=True
    - store ghi nguyên tử, giữ lịch sử sự kiện, không reset state khi enqueue lại
    - watcher QC cấu trúc ZIP: đủ 4 file ở gốc, UTF-8, không file lạ, không rỗng
    - watcher KHÔNG gán bừa ZIP khi có nhiều job chờ
    - server: bắt buộc token, bind loopback, không endpoint gửi prompt
"""
from __future__ import annotations

import json
import re
import socket
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from chatgpt_web_handoff.prompt import (REQUIRED_FILES, build_extension_prompt,
                                        build_prompt, zip_filename)
from chatgpt_web_handoff.store import HandoffStore
from chatgpt_web_handoff.watcher import ResultWatcher, inspect_zip, sha256_file

# ------------------------------------------------------------------ fixtures
JOB = {
    "job_id": "L-ep28",
    "lane": "L",
    "episode_id": "ep28",
    "title": "Chuyện nhà trọ thời Edo",
    "input_zip_url": "https://drive.google.com/file/d/ABC123/view",
    "input_zip_name": "L-ep28_INPUT.zip",
    "output_folder_url": "https://drive.google.com/drive/folders/OUT456",
    "budget_text": "4,535–5,324 ký tự",
    "hook_chars": 297,
}


def make_zip(path: Path, *, files: dict[str, str] | None = None,
             subdir: bool = False, drop: str | None = None) -> Path:
    files = files or {
        "SCRIPT_FINAL_JP.txt": "あ" * 400,
        "SCRIPT_QC_REPORT.md": "# QC\nok\n",
        "SCRIPT_ISSUES.md": "- không có\n",
        "SCRIPT_STRUCTURE.md": "| PHẦN | ký tự |\n",
    }
    if drop:
        files = {k: v for k, v in files.items() if k != drop}
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(("out/" + name) if subdir else name, text)
    return path


def _lane_files(lane: str, ep: str) -> dict[str, str]:
    """Bộ 4 file hợp lệ, header tự khai danh tính `<lane>-<ep>` như ChatGPT trả về."""
    ep_num = re.sub(r"\D", "", ep) or "1"
    return {
        "SCRIPT_FINAL_JP.txt": f"# {lane}-{ep}\n" + "あ" * 400,
        "SCRIPT_QC_REPORT.md": f"# SCRIPT_QC_REPORT — {lane}-{ep}\n\nok\n",
        "SCRIPT_ISSUES.md": f"# SCRIPT_ISSUES — {lane}-{ep}\n\n- không có\n",
        "SCRIPT_STRUCTURE.md": f"# SCRIPT_STRUCTURE — {lane}-{ep}\n\n| PHẦN | ký tự |\n",
    }


# ------------------------------------------------------------------ prompt
def test_prompt_has_all_required_parts():
    p = build_prompt(JOB)
    assert "L-ep28" in p                                        # job ID
    assert "https://drive.google.com/file/d/ABC123/view" in p    # link ZIP Drive
    assert "ĐỌC HỒ SƠ TRƯỚC KHI VIẾT" in p                       # yêu cầu đọc hồ sơ
    assert "01_HOOK_OUTLINE.txt" in p
    assert "10_GPT6_START_HERE.md" in p
    for f in REQUIRED_FILES:
        assert f in p                                           # định dạng ZIP ra
    assert "L-ep28_RESULT.zip" in p                             # tên ZIP kết quả
    assert "NGAY ở gốc ZIP" in p                                # cấu trúc ZIP
    assert "DỪNG LẠI" in p                                      # không bịa khi thiếu hồ sơ


def test_prompt_has_no_sol_or_codex():
    p = build_prompt(JOB).lower()
    for banned in ("sol", "codex", "gpt-6.1-sol"):
        assert banned not in p


def test_prompt_warns_when_no_zip_url():
    j = dict(JOB, input_zip_url="")
    assert "chưa có link" in build_prompt(j).lower()


def test_zip_filename_is_safe_and_deterministic():
    assert zip_filename("L-ep28") == "L-ep28_RESULT.zip"
    assert zip_filename("M/ep 18") == "M_ep_18_RESULT.zip"
    assert "/" not in zip_filename("a/b")


def test_extension_prompt_shape():
    d = build_extension_prompt(JOB)
    assert set(d) == {"prompt", "output_zip_name", "required_files"}
    assert d["output_zip_name"] == "L-ep28_RESULT.zip"
    assert d["required_files"] == list(REQUIRED_FILES)


# ------------------------------------------------------------------ store
def test_store_atomic_roundtrip(tmp_path):
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "J1", "prompt": "p", "state": "QUEUED"})
    got = st.get("J1")
    assert got["job_id"] == "J1" and got["state"] == "QUEUED"
    assert not list((tmp_path / "jobs" / "_handoff").glob(".tmp_*"))


def test_store_enqueue_keeps_events_and_state(tmp_path):
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "J1", "prompt": "p"})
    st.set_state("J1", "SENT", "người dùng đã gửi")
    st.enqueue({"job_id": "J1", "prompt": "p2"})          # gọi lại cùng job
    j = st.get("J1")
    assert j["state"] == "SENT"                          # KHÔNG bị reset về QUEUED
    assert j["prompt"] == "p2"
    assert len(j["events"]) >= 2
    assert any(e["kind"] == "sent" for e in j["events"])


def test_store_rejects_bad_state(tmp_path):
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "J1"})
    with pytest.raises(ValueError):
        st.set_state("J1", "XONG")


def test_store_path_traversal_blocked(tmp_path):
    st = HandoffStore(tmp_path)
    with pytest.raises(ValueError):
        st.path_of("../../etc/passwd")
    assert st.path_of("J1").parent == tmp_path / "jobs" / "_handoff"


def test_public_view_hides_machine_paths(tmp_path):
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "J1", "workdir": "C:/secret/path", "prompt": "hello"})
    view = HandoffStore.public_view(st.get("J1"))
    assert "workdir" not in view
    assert "secret" not in json.dumps(view)


# ------------------------------------------------------------------ zip QC
def test_inspect_zip_ok(tmp_path):
    z = make_zip(tmp_path / "ok.zip")
    rep = inspect_zip(z)
    assert rep["ok"], rep["problems"]
    assert rep["final_jp_chars"] == 400


def test_inspect_zip_missing_file(tmp_path):
    z = make_zip(tmp_path / "miss.zip", drop="SCRIPT_ISSUES.md")
    rep = inspect_zip(z)
    assert not rep["ok"]
    assert any("SCRIPT_ISSUES.md" in p for p in rep["problems"])


def test_inspect_zip_rejects_subdir(tmp_path):
    z = make_zip(tmp_path / "sub.zip", subdir=True)
    rep = inspect_zip(z)
    assert not rep["ok"]
    assert any("thư mục con" in p for p in rep["problems"])


def test_inspect_zip_rejects_extra_files(tmp_path):
    z = make_zip(tmp_path / "extra.zip", files={
        "SCRIPT_FINAL_JP.txt": "あ" * 400,
        "SCRIPT_QC_REPORT.md": "x", "SCRIPT_ISSUES.md": "x",
        "SCRIPT_STRUCTURE.md": "x", "nhap.txt": "rác"})
    rep = inspect_zip(z)
    assert not rep["ok"]
    assert any("ngoài 4 file" in p for p in rep["problems"])


def test_inspect_zip_rejects_empty_and_short(tmp_path):
    z = make_zip(tmp_path / "short.zip", files={
        "SCRIPT_FINAL_JP.txt": "あ", "SCRIPT_QC_REPORT.md": "x",
        "SCRIPT_ISSUES.md": "x", "SCRIPT_STRUCTURE.md": "x"})
    rep = inspect_zip(z)
    assert not rep["ok"]
    assert any("quá ngắn" in p for p in rep["problems"])


def test_inspect_zip_rejects_non_utf8(tmp_path):
    """SCRIPT_FINAL_JP.txt ghi bằng Shift-JIS phải bị từ chối (hồ sơ yêu cầu UTF-8)."""
    p = tmp_path / "sjis.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("SCRIPT_FINAL_JP.txt", ("あ" * 400).encode("shift_jis"))
        zf.writestr("SCRIPT_QC_REPORT.md", "x")
        zf.writestr("SCRIPT_ISSUES.md", "x")
        zf.writestr("SCRIPT_STRUCTURE.md", "x")
    rep = inspect_zip(p)
    assert not rep["ok"]
    assert any("UTF-8" in x for x in rep["problems"])


def test_inspect_zip_bad_file(tmp_path):
    p = tmp_path / "notzip.zip"
    p.write_bytes(b"day khong phai zip")
    rep = inspect_zip(p)
    assert not rep["ok"]
    assert any("không phải ZIP" in x for x in rep["problems"])


# ------------------------------------------------------------------ watcher
def test_watcher_matches_job_and_moves(tmp_path):
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28", "output_zip_name": "L-ep28_RESULT.zip",
                "workdir": str(tmp_path / "jobs" / "L-ep28")})
    recv = tmp_path / "dl"
    recv.mkdir()
    make_zip(recv / "L-ep28_RESULT.zip")
    w = ResultWatcher(st, recv, settle_sec=0.01)
    out = w.scan_once()
    assert len(out) == 1 and out[0]["job_id"] == "L-ep28"
    j = st.get("L-ep28")
    assert j["state"] == "DOWNLOADED"
    assert Path(j["result_zip_path"]).exists()
    assert not (recv / "L-ep28_RESULT.zip").exists()      # đã chuyển đi


def test_watcher_keep_source_copies_and_keeps_original(tmp_path):
    """keep_source=True: ghi nhận ZIP nhưng GIỮ nguyên bản gốc trong thư mục nhận.

    Yêu cầu vận hành: không được xóa/mất file người dùng đã tải về. Watcher chỉ
    sao sang workdir, bản gốc trong Downloads phải còn nguyên tên và nội dung.
    """
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28", "output_zip_name": "L-ep28_RESULT.zip",
                "workdir": str(tmp_path / "jobs" / "L-ep28")})
    recv = tmp_path / "dl"
    recv.mkdir()
    src = make_zip(recv / "L-ep28_RESULT.zip")
    src_sha = sha256_file(src)

    w = ResultWatcher(st, recv, settle_sec=0.01, keep_source=True)
    out = w.scan_once()

    assert len(out) == 1 and out[0]["job_id"] == "L-ep28"
    j = st.get("L-ep28")
    assert j["state"] == "DOWNLOADED"
    # bản gốc còn nguyên trong thư mục nhận
    assert src.exists() and src.name == "L-ep28_RESULT.zip"
    assert sha256_file(src) == src_sha
    # bản sao trong workdir cũng tồn tại và trùng nội dung
    dest = Path(j["result_zip_path"])
    assert dest.exists() and dest != src
    assert sha256_file(dest) == src_sha


def test_watcher_result_path_is_absolute_even_with_dot_workdir(tmp_path):
    """workdir='.' (job tạo tay) không được sinh result_zip_path tương đối.

    Nếu ghi tương đối, adapter/orchestrator chạy ở cwd khác sẽ không thấy ZIP
    và job kẹt ở DOWNLOADED mà không ai lấy được kết quả.
    """
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28", "output_zip_name": "L-ep28_RESULT.zip",
                "workdir": "."})
    recv = tmp_path / "dl"
    recv.mkdir()
    make_zip(recv / "L-ep28_RESULT.zip")

    out = ResultWatcher(st, recv, settle_sec=0.01).scan_once()
    assert len(out) == 1
    rp = Path(st.get("L-ep28")["result_zip_path"])
    assert rp.is_absolute(), f"phải là đường dẫn tuyệt đối, đang là {rp}"
    assert rp.exists()
    assert str(tmp_path.resolve()) in str(rp)


def test_set_state_downloaded_clears_previous_error(tmp_path):
    """NEEDS_REVIEW -> DOWNLOADED phải xoá last_error cũ, không để lại lý do hỏng."""
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28"})
    st.set_state("L-ep28", "NEEDS_REVIEW", "ZIP nhận sai tập — L-ep27")
    assert "sai tập" in st.get("L-ep28")["last_error"]

    st.set_state("L-ep28", "DOWNLOADED", "nhận ZIP đúng")
    assert st.get("L-ep28")["last_error"] == ""


def test_watcher_accepts_chrome_duplicate_suffix(tmp_path):
    """Chrome thêm ' (1)' khi tải trùng tên — vẫn phải nhận, và mặc định CHUYỂN file đi."""
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28", "output_zip_name": "L-ep28_RESULT.zip",
                "workdir": str(tmp_path / "w")})
    recv = tmp_path / "dl"; recv.mkdir()
    make_zip(recv / "L-ep28_RESULT (1).zip")
    out = ResultWatcher(st, recv, settle_sec=0.01).scan_once()
    assert len(out) == 1 and out[0]["job_id"] == "L-ep28"
    assert not (recv / "L-ep28_RESULT (1).zip").exists()   # keep_source mặc định False


def test_watcher_does_not_guess_between_two_jobs(tmp_path):
    """2 job chờ + ZIP tên không khớp -> KHÔNG gán bừa (thà báo lỗi hơn ghi sai tập)."""
    st = HandoffStore(tmp_path)
    for jid in ("L-ep28", "M-ep18"):
        st.enqueue({"job_id": jid, "output_zip_name": f"{jid}_RESULT.zip",
                    "workdir": str(tmp_path / "w" / jid)})
    recv = tmp_path / "dl"; recv.mkdir()
    make_zip(recv / "ket_qua.zip")
    assert ResultWatcher(st, recv, settle_sec=0.01).scan_once() == []
    assert st.get("L-ep28")["state"] == "QUEUED"
    assert st.get("M-ep18")["state"] == "QUEUED"


def test_watcher_no_fallback_to_only_pending_job(tmp_path):
    """1 job chờ KHÔNG phải bằng chứng danh tính: ZIP tên lạ -> KHÔNG nhận.

    (Bản cũ có nhánh "chỉ có một job đang chờ + cấu trúc đạt -> nhận bừa";
    đó chính là lỗ hổng đã gán ZIP L-ep27 vào job N-ep17.)
    """
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28", "output_zip_name": "L-ep28_RESULT.zip",
                "workdir": str(tmp_path / "w")})
    recv = tmp_path / "dl"; recv.mkdir()
    make_zip(recv / "ChatGPT_download.zip")
    assert ResultWatcher(st, recv, settle_sec=0.01).scan_once() == []
    assert st.get("L-ep28")["state"] == "QUEUED"
    assert (recv / "ChatGPT_download.zip").exists()          # KHÔNG bị đổi tên/di chuyển


def test_watcher_rejects_zip_from_another_episode(tmp_path):
    """REGRESSION Phase 5.1 — ZIP của L-ep27 TUYỆT ĐỐI không được gán vào N-ep17.

    Đây là đúng ca đã xảy ra thật: job SMOKE51-N-ep17-GPT6-v01 là job DUY NHẤT
    đang chờ, người dùng tải nhầm LaneL_tap27_GPT6_SCRIPT_DELIVERY.zip, watcher cũ
    nhận nó, ĐỔI TÊN thành ..._RESULT.zip rồi đặt DOWNLOADED.
    """
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip",
                "workdir": str(tmp_path / "jobs" / jid)})
    st.set_state(jid, "SENT", "người dùng đã gửi")
    recv = tmp_path / "dl"; recv.mkdir()
    lane_l = make_zip(recv / "LaneL_tap27_GPT6_SCRIPT_DELIVERY.zip",
                      files=_lane_files("L", "ep27"))

    out = ResultWatcher(st, recv, settle_sec=0.01).scan_once()

    assert out == []                                        # không nhận gì cả
    assert st.get(jid)["state"] == "SENT"                   # KHÔNG lên DOWNLOADED
    assert st.get(jid).get("result_zip_path") is None
    assert lane_l.exists()                                  # KHÔNG đổi tên, KHÔNG di chuyển
    assert lane_l.name == "LaneL_tap27_GPT6_SCRIPT_DELIVERY.zip"


def test_watcher_accepts_only_when_name_and_identity_match(tmp_path):
    """Đối chứng: đúng tên + đúng danh tính thì nhận, và ghi lại bằng chứng."""
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip",
                "workdir": str(tmp_path / "jobs" / jid)})
    recv = tmp_path / "dl"; recv.mkdir()
    make_zip(recv / f"{jid}_RESULT.zip", files=_lane_files("N", "ep17"))
    out = ResultWatcher(st, recv, settle_sec=0.01).scan_once()
    assert len(out) == 1 and out[0]["job_id"] == jid
    j = st.get(jid)
    assert j["state"] == "DOWNLOADED"
    assert j["result_identity"]["lanes"] == ["N"]
    assert j["result_identity"]["episodes"] == [17]
    assert len(j["result_sha256"]) == 64


def test_watcher_rejects_right_name_wrong_content_identity(tmp_path):
    """Tên đúng job nhưng nội dung tự khai tập khác -> NEEDS_REVIEW, không DOWNLOADED.

    Ca thật: file người dùng tải về đã bị đặt tên ..._RESULT.zip nhưng bên trong
    là kịch bản L-ep27 (5823 ký tự, header '# SCRIPT_QC_REPORT — L-ep27').
    Đây là ca cần NGƯỜI đưa lại đúng file, nên trạng thái là NEEDS_REVIEW,
    không phải FAILED (FAILED hàm ý lỗi kỹ thuật, dễ bị chạy lại tự động).
    """
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip",
                "workdir": str(tmp_path / "jobs" / jid)})
    recv = tmp_path / "dl"; recv.mkdir()
    # tên file của N-ep17, nhưng nội dung khai L-ep27
    make_zip(recv / f"{jid}_RESULT.zip", files=_lane_files("L", "ep27"))
    out = ResultWatcher(st, recv, settle_sec=0.01).scan_once()
    assert out and not out[0]["report"]["ok"]
    j = st.get(jid)
    assert j["state"] == "NEEDS_REVIEW"
    assert "L" in (j.get("last_error") or "")
    assert j.get("result_zip_path") is None
    assert (recv / f"{jid}_RESULT.zip").exists()             # file gốc còn nguyên
    assert any(e["kind"] == "zip_wrong_identity" for e in j["events"])


def test_watcher_needs_review_can_still_be_recovered_by_right_zip(tmp_path):
    """Job NEEDS_REVIEW phải còn ĐƯỜNG PHỤC HỒI: ZIP đúng tới sau -> DOWNLOADED."""
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip",
                "workdir": str(tmp_path / "jobs" / jid)})
    recv = tmp_path / "dl"; recv.mkdir()
    make_zip(recv / f"{jid}_RESULT.zip", files=_lane_files("L", "ep27"))
    ResultWatcher(st, recv, settle_sec=0.01).scan_once()
    assert st.get(jid)["state"] == "NEEDS_REVIEW"

    # người dùng tải lại đúng file (Chrome thêm hậu tố) và thay file sai
    (recv / f"{jid}_RESULT.zip").unlink()
    make_zip(recv / f"{jid}_RESULT (1).zip", files=_lane_files("N", "ep17"))
    out = ResultWatcher(st, recv, settle_sec=0.01).scan_once()
    assert [o["job_id"] for o in out] == [jid]
    j = st.get(jid)
    assert j["state"] == "DOWNLOADED"
    assert j["result_identity"]["episodes"] == [17]


def test_watcher_exact_name_only_no_prefix_match(tmp_path):
    """`startswith` cũ cho phép tên gần giống lọt vào -> giờ phải khớp CHÍNH XÁC."""
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip",
                "workdir": str(tmp_path / "w")})
    recv = tmp_path / "dl"; recv.mkdir()
    # cùng tiền tố stem nhưng là tập/phiên bản khác
    for bad in (f"{jid}_RESULT_OLD.zip", f"{jid}_RESULT_v02.zip",
                f"{jid}_RESULT_ep18.zip"):
        make_zip(recv / bad, files=_lane_files("N", "ep17"))
    assert ResultWatcher(st, recv, settle_sec=0.01).scan_once() == []
    assert st.get(jid)["state"] == "QUEUED"
    assert len(list(recv.glob("*.zip"))) == 3


def test_watcher_name_match_ignores_chrome_suffix_only(tmp_path):
    """Chỉ tha hậu tố ' (1)' của trình duyệt, không tha biến thể khác."""
    from chatgpt_web_handoff.watcher import name_matches
    job = {"job_id": "J1", "output_zip_name": "J1_RESULT.zip"}
    assert name_matches(job, "J1_RESULT.zip")
    assert name_matches(job, "J1_RESULT (1).zip")
    assert name_matches(job, "J1_RESULT (12).zip")
    assert not name_matches(job, "J1_RESULT_2.zip")
    assert not name_matches(job, "J1_RESULT_final.zip")
    assert not name_matches(job, "LaneL_tap27_GPT6_SCRIPT_DELIVERY.zip")


def test_parse_identity_reads_lane_and_episode():
    from chatgpt_web_handoff.watcher import parse_identity
    assert parse_identity("SMOKE51-N-ep17-GPT6-v01") == ("N", 17)
    assert parse_identity("# SCRIPT_QC_REPORT — L-ep27") == ("L", 27)
    assert parse_identity("# SCRIPT_STRUCTURE — N-ep17") == ("N", 17)
    assert parse_identity("M-ep18_GPT6_SCRIPT_DELIVERY") == ("M", 18)
    assert parse_identity("LaneL_tap27_GPT6_SCRIPT_DELIVERY")[0] == "L"
    assert parse_identity("LaneN_tap17_GPT_SCRIPT_HANDOFF")[0] == "N"
    assert parse_identity("youtube-trending-research-master") == (None, None)


def test_recover_downloaded_flags_wrong_episode(tmp_path):
    """Job đã bị đánh dấu DOWNLOADED sai -> recover đưa về NEEDS_REVIEW, không xoá file."""
    from chatgpt_web_handoff.watcher import recover_downloaded
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    wd = tmp_path / "jobs" / jid
    wd.mkdir(parents=True)
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip", "workdir": str(wd)})
    # dựng đúng trạng thái hỏng: file tên N-ep17 nhưng ruột là L-ep27
    poisoned = make_zip(wd / f"{jid}_RESULT.zip", files=_lane_files("L", "ep27"))
    st.update(jid, result_zip_path=str(poisoned), result_final_jp_chars=5823)
    st.set_state(jid, "DOWNLOADED", "nhận ZIP sai")

    dry = recover_downloaded(st, apply=False)
    assert dry and not dry[0]["ok"] and dry[0]["applied"] is False
    assert st.get(jid)["state"] == "DOWNLOADED"              # dry-run không đổi gì

    done = recover_downloaded(st, apply=True)
    assert done and done[0]["applied"] is True
    j = st.get(jid)
    assert j["state"] == "NEEDS_REVIEW"
    assert j.get("result_zip_path") is None
    assert "L" in (j.get("last_error") or "")
    assert poisoned.exists()                                 # KHÔNG xoá ZIP gốc
    assert any(e["kind"] == "recovered_needs_review" for e in j["events"])


def test_recover_downloaded_keeps_correct_job(tmp_path):
    """Job DOWNLOADED đúng tập thì recover không được đụng vào."""
    from chatgpt_web_handoff.watcher import recover_downloaded
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    wd = tmp_path / "jobs" / jid
    wd.mkdir(parents=True)
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip", "workdir": str(wd)})
    good = make_zip(wd / f"{jid}_RESULT.zip", files=_lane_files("N", "ep17"))
    st.update(jid, result_zip_path=str(good))
    st.set_state(jid, "DOWNLOADED", "ok")
    rows = recover_downloaded(st, apply=True)
    assert rows and rows[0]["ok"] and rows[0]["applied"] is False
    assert st.get(jid)["state"] == "DOWNLOADED"


def test_recover_downloaded_flags_wrong_structure_when_identity_ok(tmp_path):
    """Danh tính khai đúng nhưng ruột là ZIP bàn giao -> vẫn phải NEEDS_REVIEW.

    Ca thật: LaneL_tap27_GPT6_SCRIPT_DELIVERY.zip bị đổi tên thành
    ..._RESULT.zip. Header .md bị sửa thành N-ep17 nên chỉ soi danh tính
    sẽ cho qua — phải soi cả cấu trúc.
    """
    from chatgpt_web_handoff.watcher import recover_downloaded
    st = HandoffStore(tmp_path)
    jid = "SMOKE51-N-ep17-GPT6-v01"
    wd = tmp_path / "jobs" / jid
    wd.mkdir(parents=True)
    st.enqueue({"job_id": jid, "lane": "N", "episode_id": "ep17",
                "output_zip_name": f"{jid}_RESULT.zip", "workdir": str(wd)})
    # khai đúng N-ep17 nhưng chỉ có file bàn giao, không có 4 file kết quả
    delivery = {"LaneN_tap17/01_HOOK_OUTLINE.txt": "x" * 400,
                "LaneN_tap17/02_SCRIPT_RULES.md": "# SCRIPT_QC_REPORT — N-ep17\n"}
    bad = make_zip(wd / f"{jid}_RESULT.zip", files=delivery)
    st.update(jid, result_zip_path=str(bad))
    st.set_state(jid, "DOWNLOADED", "nhận ZIP sai")

    rows = recover_downloaded(st, apply=True)
    assert rows and rows[0]["applied"] is True
    j = st.get(jid)
    assert j["state"] == "NEEDS_REVIEW"
    assert "SCRIPT_FINAL_JP.txt" in (j.get("last_error") or "")
    assert bad.exists()                                      # KHÔNG xoá file


def test_watcher_marks_failed_on_bad_structure(tmp_path):
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28", "output_zip_name": "L-ep28_RESULT.zip",
                "workdir": str(tmp_path / "w")})
    recv = tmp_path / "dl"; recv.mkdir()
    make_zip(recv / "L-ep28_RESULT.zip", drop="SCRIPT_STRUCTURE.md")
    out = ResultWatcher(st, recv, settle_sec=0.01).scan_once()
    assert out and not out[0]["report"]["ok"]
    j = st.get("L-ep28")
    assert j["state"] == "FAILED"
    assert "SCRIPT_STRUCTURE.md" in j["last_error"]


def test_watcher_ignores_partial_download(tmp_path):
    st = HandoffStore(tmp_path)
    st.enqueue({"job_id": "L-ep28", "output_zip_name": "L-ep28_RESULT.zip",
                "workdir": str(tmp_path / "w")})
    recv = tmp_path / "dl"; recv.mkdir()
    (recv / "L-ep28_RESULT.zip.crdownload").write_bytes(b"x" * 10)
    assert ResultWatcher(st, recv, settle_sec=0.01).scan_once() == []
    assert st.get("L-ep28")["state"] == "QUEUED"


# ------------------------------------------------------------------ adapter
def test_adapter_refuses_without_conversation_url(tmp_path):
    from chatgpt_web_handoff.adapter import ChatGPTWebHandoffAdapter
    a = ChatGPTWebHandoffAdapter({"_root": str(tmp_path), "phase5": {}},
                                 {"key": "h2dev"})
    r = a.submit(dict(JOB), "prompt")
    assert r["accepted"] is False
    assert r["conversation_url"] is None
    assert "conversation_url" in r["note"]
    assert a.store.get("L-ep28") is None          # KHÔNG tạo job nửa vời


def test_adapter_is_manual_and_queues(tmp_path):
    from chatgpt_web_handoff.adapter import ChatGPTWebHandoffAdapter
    a = ChatGPTWebHandoffAdapter(
        {"_root": str(tmp_path),
         "phase5": {"conversation_url": "https://chatgpt.com/c/abc-123"}},
        {"key": "h2dev"})
    assert a.supports_auto is False
    r = a.submit(dict(JOB), "")
    assert r["accepted"] and r["manual_required"] is True
    assert r["conversation_url"] == "https://chatgpt.com/c/abc-123"
    assert r["output_zip_name"] == "L-ep28_RESULT.zip"
    rec = a.store.get("L-ep28")
    assert rec["state"] == "QUEUED"
    assert "L-ep28" in rec["prompt"] and "ABC123" in rec["prompt"]
    assert a.status(dict(JOB))["state"] == "waiting"


def test_adapter_collect_reports_missing_zip(tmp_path):
    from chatgpt_web_handoff.adapter import ChatGPTWebHandoffAdapter
    a = ChatGPTWebHandoffAdapter(
        {"_root": str(tmp_path), "phase5": {"conversation_url": "https://chatgpt.com/c/x",
                                            "receive_dir": str(tmp_path / "dl")}},
        {"key": "h2dev"})
    a.submit(dict(JOB), "")
    r = a.collect_result(dict(JOB))
    assert r["zip_path"] is None
    assert "L-ep28_RESULT.zip" in r["note"]


def test_adapter_status_after_download(tmp_path):
    from chatgpt_web_handoff.adapter import ChatGPTWebHandoffAdapter
    a = ChatGPTWebHandoffAdapter(
        {"_root": str(tmp_path), "phase5": {"conversation_url": "https://chatgpt.com/c/x"}},
        {"key": "h2dev"})
    a.submit(dict(JOB), "")
    # chưa có file trên đĩa -> vẫn là waiting (không báo xong khống)
    a.store.update("L-ep28", result_zip_path=str(tmp_path / "r.zip"))
    assert a.status(dict(JOB))["state"] == "waiting"
    # có file thật -> result_available
    (tmp_path / "r.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    assert a.status(dict(JOB))["state"] == "result_available"


# ------------------------------------------------------------------ server
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def live_server(tmp_path):
    from chatgpt_web_handoff.server import make_server
    (tmp_path / "config.yaml").write_text(
        "phase5:\n  conversation_url: https://chatgpt.com/c/abc-123\n", encoding="utf-8")
    st = HandoffStore(tmp_path)
    st.enqueue(dict(JOB, prompt="PROMPT-XYZ", output_zip_name="L-ep28_RESULT.zip"))
    port = _free_port()
    srv = make_server(tmp_path, port=port, token="tok-test")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", srv.bridge_token, st
    srv.shutdown()
    srv.server_close()


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Bridge-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _post(url, body, token=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    if token:
        req.add_header("X-Bridge-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_server_health_needs_no_token(live_server):
    base, _, _ = live_server
    code, d = _get(base + "/health")
    assert code == 200 and d["ok"] is True
    assert d["conversation_url"] == "https://chatgpt.com/c/abc-123"


def test_server_requires_token(live_server):
    base, tok, _ = live_server
    code, _ = _get(base + "/jobs")
    assert code == 401
    code, _ = _get(base + "/jobs", "sai-token")
    assert code == 401
    code, d = _get(base + "/jobs", tok)
    assert code == 200 and d["jobs"][0]["job_id"] == "L-ep28"


def test_server_job_returns_prompt_and_zip_name(live_server):
    base, tok, _ = live_server
    code, d = _get(base + "/job/L-ep28", tok)
    assert code == 200
    assert d["job"]["output_zip_name"] == "L-ep28_RESULT.zip"
    assert d["job"]["conversation_url"] == "https://chatgpt.com/c/abc-123"
    # Trả NGUYÊN VĂN prompt đã lưu, không dựng lại từ metadata (dễ rơi mất
    # dòng ngân sách ký tự / số đo hook).
    assert d["job"]["prompt"] == "PROMPT-XYZ"


def test_server_binds_loopback_only(tmp_path):
    """Server chỉ bind 127.0.0.1 — máy khác trong LAN không vào được."""
    from chatgpt_web_handoff.server import make_server
    srv = make_server(tmp_path, port=0, token="t")
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_server_event_opened_then_sent(live_server):
    base, tok, st = live_server
    code, d = _post(base + "/event/opened", {"job_id": "L-ep28"}, tok)
    assert code == 200 and d["state"] == "OPENED"
    code, d = _post(base + "/event/sent", {"job_id": "L-ep28"}, tok)
    assert code == 200 and d["state"] == "SENT"
    assert st.get("L-ep28")["state"] == "SENT"


def test_server_rejects_unknown_job(live_server):
    base, tok, _ = live_server
    code, _ = _get(base + "/job/KHONG-CO", tok)
    assert code == 404
    code, _ = _post(base + "/event/sent", {"job_id": "KHONG-CO"}, tok)
    assert code == 404


def test_server_has_no_send_endpoint(live_server):
    """Không được có endpoint gửi prompt hộ — đó là ranh giới tuân thủ."""
    base, tok, _ = live_server
    for path in ("/send", "/submit", "/prompt/send", "/auto"):
        code, _ = _post(base + path, {"job_id": "L-ep28"}, tok)
        assert code == 404, f"{path} không được tồn tại"
