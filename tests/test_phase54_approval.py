# -*- coding: utf-8 -*-
"""test_phase54_approval.py — điều kiện #8 khi VẬN HÀNH.

Chốt với chủ dự án ngày 2026-10-25:

> "Chỉ đưa job vào QUEUED khi cả tám điều kiện đều PASS **và có phê duyệt
>  production**."

Trước bản này, gate vào QUEUED khi 8 điều kiện PASS + `--apply`; phê duyệt
production chỉ được kiểm ở bước upload. Yêu cầu #8 đòi kiểm ngay ở cửa vào.

Hệ quả thiết kế:

* Đủ 8 điều kiện nhưng chưa duyệt → `WAITING_APPROVAL` (KHÔNG phải lỗi, KHÔNG
  phải QUEUED). Đây là trạng thái chờ người, không phải hỏng hóc.
* `apply=True` KHÔNG thay thế phê duyệt. Duyệt là quyết định của chủ dự án.
* Gate chỉ ĐỌC cờ `production_upload_enabled`; không bao giờ tự bật.
"""

from __future__ import annotations

import pytest

from services import script_gate as G
from test_phase54_script_gate import CELL_SUON_OK, Env


@pytest.fixture
def env_unapproved(tmp_path, cfg):
    """Cùng dữ liệu ĐỦ 8 điều kiện, nhưng CHƯA phê duyệt production."""
    return Env(tmp_path, cfg, approved=False)


@pytest.fixture
def env_approved(tmp_path, cfg):
    return Env(tmp_path, cfg, approved=True)


# ------------------------------------------------------- nhánh CHƯA phê duyệt
def test_full_conditions_without_approval_is_waiting_not_queued(env_unapproved):
    """Đủ 8 điều kiện + --apply nhưng chưa duyệt → WAITING_APPROVAL."""
    env = env_unapproved
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_WAITING_APPROVAL
    assert r["status"] != G.STATUS_QUEUED
    assert r["code"] == "E_GATE_NOT_APPROVED"
    assert r["checks"]["approval"] == {"ok": False, "production_upload_enabled": False}


def test_no_job_written_when_unapproved(env_unapproved):
    """Chưa duyệt thì HandoffStore phải TRỐNG — không có job nào."""
    env = env_unapproved
    info = env.episode()
    env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                       lane="N", ep_num=17, apply=True)
    assert env.handoff.all() == []
    assert env.handoff.get("H2DEV-N-ep17-GPT6-v01") is None


def test_waiting_approval_is_not_an_error_state(env_unapproved):
    """WAITING_APPROVAL thuộc nhóm 'đang chờ', không phải REJECTED."""
    assert G.STATUS_WAITING_APPROVAL in G.WAITING_STATES
    env = env_unapproved
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17)
    assert r["status"] != G.STATUS_REJECTED


def test_approval_checked_after_all_technical_checks(env_unapproved):
    """Thiếu ZIP thì vẫn báo thiếu ZIP, KHÔNG nhảy sang WAITING_APPROVAL.

    Thứ tự quan trọng: phê duyệt là cửa CUỐI. Báo "chờ duyệt" cho một hồ sơ
    còn thiếu ZIP sẽ khiến người vận hành tưởng chỉ cần bật cờ là chạy được.
    """
    env = env_unapproved
    info = env.episode(put_zip=False)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_WAITING_HANDOFF_ZIP
    assert r["code"] == "E_GATE_ZIP_MISSING"


def test_research_ok_still_waits_outline_even_if_approved(env_approved):
    """RESEARCH OK chưa có Hook+Outline → WAITING_OUTLINE, duyệt cũng vậy."""
    env = env_approved
    r = env.gate.preflight(env.req("RESEARCH OK 2026-10-23 12:01 — đã tải hồ sơ gốc"),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_WAITING_OUTLINE
    assert env.handoff.all() == []


# --------------------------------------------------------- nhánh ĐÃ phê duyệt
def test_approved_full_conditions_is_queued(env_approved):
    env = env_approved
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_QUEUED
    assert r["checks"]["approval"] == {"ok": True, "production_upload_enabled": True}
    job = env.handoff.get("H2DEV-N-ep17-GPT6-v01")
    assert job is not None and job["state"] == "QUEUED"


def test_approved_but_no_apply_writes_nothing(env_approved):
    """Phê duyệt + đủ điều kiện nhưng KHÔNG --apply → chỉ đọc."""
    env = env_approved
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=False)
    assert r["status"] == G.STATUS_QUEUED
    assert env.handoff.all() == []


# ------------------------------------------------------- nguồn phê duyệt
def test_approval_read_from_upload_section_too(tmp_path, cfg):
    """Cờ có thể nằm ở `upload:` (đường cũ) — gate phải thấy."""
    c = dict(cfg)
    c["handoff"] = {**(cfg.get("handoff") or {}), "production_upload_enabled": False}
    c["upload"] = {**(cfg.get("upload") or {}), "production_upload_enabled": True}
    env = Env(tmp_path, c)
    env.cfg = c
    env.gate = G.ScriptGate(c, drive=env.drive, state=env.state,
                            handoff=env.handoff, root=tmp_path)
    assert env.gate.production_approved() is True


def test_approval_defaults_to_false_when_absent(tmp_path, cfg):
    """Không có cờ ở đâu cả → coi như CHƯA duyệt (fail closed)."""
    c = dict(cfg)
    c.pop("handoff", None)
    c.pop("upload", None)
    env = Env(tmp_path, c)
    env.cfg = c
    env.gate = G.ScriptGate(c, drive=env.drive, state=env.state,
                            handoff=env.handoff, root=tmp_path)
    assert env.gate.production_approved() is False


def test_gate_never_flips_the_approval_flag(env_unapproved):
    """Gate chỉ ĐỌC cờ. Sau khi chạy, cfg vẫn false — không tự bật."""
    env = env_unapproved
    info = env.episode()
    env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                       lane="N", ep_num=17, apply=True)
    assert env.cfg["handoff"]["production_upload_enabled"] is False


# ------------------------------------------------- ca thật: config production
def test_real_config_is_not_approved():
    """Config THẬT của dự án phải đang TẮT phê duyệt production.

    Nếu test này fail nghĩa là ai đó đã bật cờ — phải là quyết định có ý thức
    của chủ dự án, không phải tác dụng phụ của một lần chạy.
    """
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[1]
    p = root / "config.yaml"
    if not p.exists():
        pytest.skip("không có config.yaml")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    h = (raw.get("handoff") or {}).get("production_upload_enabled")
    u = (raw.get("upload") or {}).get("production_upload_enabled")
    assert not h, f"handoff.production_upload_enabled đang bật: {h!r}"
    assert not u, f"upload.production_upload_enabled đang bật: {u!r}"
