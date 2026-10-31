# -*- coding: utf-8 -*-
"""test_phase54_real_manifest.py — chốt LẠI schema manifest THẬT (N-ep17).

Vì sao có file này: lần đầu viết `fabel_profile_problems()` tôi giả định
`hook_outline_checksum` là sha256 của file `01_HOOK_OUTLINE.txt`. Đo trên hồ sơ
THẬT `LaneN_tap17_GPT_SCRIPT_HANDOFF.zip` thì SAI:

    hook_outline_checksum       = e2a9a774…  (ID nội bộ, KHÔNG phải hash file)
    per_file_checksums[01_...]  = e7f1f6ef…  = sha256(file thật)   ← cái đo được
    hook_sol.hook_sha256        = 932bbb5d…  = sha256(KHỐI HOOK đã tách)

Nếu ai đó "sửa lại cho gọn" thành so `hook_outline_checksum` với hash file thì
mọi hồ sơ thật sẽ bị chặn. Test này khoá đúng hành vi đo được.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from services import script_gate as G

pytestmark = pytest.mark.filterwarnings("ignore")

# Giá trị THẬT đọc từ hồ sơ N-ep17 ngày 2026-10-24
REAL_HOOK_OUTLINE_CHECKSUM = "e2a9a7743d57b0c6d4494f2e75a40907016b7a72070f278c54bf8f3aa8791a51"
REAL_FILE_SHA256 = "e7f1f6ef95333a1853e390482a7585196dea75b070ff9fb9b0468474cb95e642"
REAL_HOOK_SHA256 = "932bbb5d86fc330335ad41341000d5a582f068d9b438c8a64776d3ff6004cc97"
REAL_HOOK_DECLARED_CHARS = 297

REAL_HOOK_BLOCK = """三月最後の日曜日、河川敷でキャンピングカーの展示会です。
大きな車の列のいちばん奥に、軽自動車のキャンピングカーが一台。
その値札の前で、さっきから動かない男がいます。
「四百二十万円か。」
戸倉誠一さん、六十八歳。
農協で三十八年、肥料や資材を配達してきました。
これは、定年して三年の誠一さんの、ひと月の物語です。
雑誌で眺めてきた車で、妻と日本を回る。
それが誠一さんの夢でした。
妻の美津子さん、六十六歳。
貯金は千九百万円、病院と家の修理に備えるお金です。
「四百二十万なんて、簡単に出せるお金じゃないわよ。」
「あなた、本当に車の中で眠れるの。」
「どこででも寝られるさ。」
「買う前に、ひと月借りてみたら。」"""


def _real_like_zip(path: Path) -> Path:
    """ZIP có ĐÚNG hình dạng manifest thật (kể cả hook_outline_checksum là ID nội bộ)."""
    hook_file = (
        "# 01_HOOK_OUTLINE — N-ep17 · lane senior_korekara\n"
        "# Fabel (claude-fable-5-1) · phiên ['a','b','c','d'] · 2026-10-24T12:28:22+07:00\n"
        "# Nguyên văn đầu ra Fabel — KHÔNG rút gọn.\n\n"
        "=== TIÊU ĐỀ DỰ KIẾN ===\n"
        "【漫画】420万円の軽キャンピングカー\n\n"
        f"=== HOOK (KHOÁ — chép nguyên văn ở đầu kịch bản; {REAL_HOOK_DECLARED_CHARS} chữ) ===\n"
        f"{REAL_HOOK_BLOCK}\n\n"
        "=== KHUNG SƯỜN ===\n"
        "## PHẦN 1 — Tối đó, ông tra cứu (~530 chữ)\n"
    )
    raw = hook_file.encode("utf-8")
    hook_block = G.parse_hook(hook_file)["text"]

    man = {
        "job_id": "N-ep17", "lane_id": "N", "episode_id": "ep17",
        "profile_id": "senior_korekara",
        "title": "【漫画】420万円の軽キャンピングカー",
        "source_research_version": "9f80f6dd" * 8,
        # ⚠️ ID nội bộ — KHÔNG phải sha256 file. Đây là điểm dễ hiểu sai nhất.
        "hook_outline_checksum": REAL_HOOK_OUTLINE_CHECKSUM,
        "per_file_checksums": {"01_HOOK_OUTLINE.txt": hashlib.sha256(raw).hexdigest()},
        "per_file_checksums_note": "sha256 từng file trong gói, trừ chính manifest",
        "fabel": {"model": "claude-fable-5-1", "sessions": ["a", "b", "c", "d"]},
        "hook_sol": {"state": "PASS_LEAN",
                     "hook_sha256": hashlib.sha256(hook_block.encode()).hexdigest()},
        "validation_status": "PASSED", "validation_errors": [],
        "schema_version": "1.0", "handoff_version": 1,
    }
    root = "LaneN_tap17_GPT_SCRIPT_HANDOFF"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/01_HOOK_OUTLINE.txt", raw)
        for n in ("02_SCRIPT_RULES.md", "03_RESEARCH_FACTS.md", "04_CHANNEL_PROFILE.md",
                  "05_CONTINUITY.md", "06_REFERENCE_SCRIPT.txt", "07_WRITING_TASK.md",
                  "08_QC_CHECKLIST.md", "10_GPT6_START_HERE.md", "HANDOFF_ISSUES.md"):
            zf.writestr(f"{root}/{n}", f"# {n}\n")
        zf.writestr(f"{root}/09_HANDOFF_MANIFEST.json",
                    json.dumps(man, ensure_ascii=False, indent=2))
    return path


@pytest.fixture
def real_zip(tmp_path):
    return _real_like_zip(tmp_path / "LaneN_tap17_GPT_SCRIPT_HANDOFF.zip")


def test_real_shape_zip_passes(real_zip):
    """Hình dạng manifest THẬT phải PASS — không được chặn nhầm."""
    prof = G.fabel_profile_problems(real_zip)
    assert prof["ok"] is True, prof["problems"] + prof["revision_problems"]
    assert prof["hook_chars"] == REAL_HOOK_DECLARED_CHARS
    assert prof["outline_present"] is True
    assert prof["fabel"]["model"] == "claude-fable-5-1"
    assert prof["fabel"]["sessions"] == 4


def test_revision_uses_per_file_checksums_not_internal_id(real_zip):
    """Revision phải so per_file_checksums; hook_outline_checksum chỉ để đối chiếu."""
    prof = G.fabel_profile_problems(real_zip)
    rev = prof["revision"]
    assert rev["file_matches"] is True
    assert rev["hook_matches"] is True
    # ID nội bộ vẫn được ghi lại để truy vết, KHÔNG dùng để chặn
    assert rev["hook_outline_checksum"] == REAL_HOOK_OUTLINE_CHECKSUM
    assert rev["file_declared"] == rev["file_measured"]


def test_internal_id_is_not_treated_as_file_hash(real_zip):
    """Nếu ai đó đổi sang so `hook_outline_checksum` với hash file thì test này bắt."""
    prof = G.fabel_profile_problems(real_zip)
    assert prof["revision"]["hook_outline_checksum"] != prof["revision"]["file_measured"], \
        "hook_outline_checksum KHÔNG phải sha256 file — đừng dùng nó làm checksum file"


def test_hook_chars_matches_declared_in_header(real_zip):
    """Số chữ đo được phải khớp số khai trong header '=== HOOK … 297 chữ ==='."""
    prof = G.fabel_profile_problems(real_zip)
    assert prof["hook_chars"] == prof["hook_declared"] == REAL_HOOK_DECLARED_CHARS


def test_identity_from_manifest_not_filename(real_zip):
    """Danh tính đọc từ manifest: 'tap17' trong tên file không được làm hỏng."""
    prof = G.fabel_profile_problems(real_zip)
    ident = G.handoff_identity(real_zip, prof)
    assert ident["lanes"] == ["N"]
    assert ident["episodes"] == [17]
    assert ident["manifest_job_id"] == "N-ep17"
    assert ident["source"]["manifest"] is True


def test_filename_tap_convention_is_understood(real_zip):
    """Tên file kiểu `LaneN_tap17_...` phải hiểu là ep17 (không phải 'không khai tập')."""
    ident = G.handoff_identity(real_zip, {})
    assert 17 in ident["episodes"]


def test_wrong_episode_still_caught_on_real_shape(real_zip):
    """Hồ sơ thật nhưng hỏi sai tập -> vẫn phải chặn."""
    prof = G.fabel_profile_problems(real_zip)
    ident = G.handoff_identity(real_zip, prof)
    probs = G.identity_mismatch({"lane": "N", "episode_id": "ep23"}, ident)
    assert probs and "ep23" in probs[0]


def test_old_watcher_identity_would_have_rejected_this(real_zip):
    """Vì sao KHÔNG dùng `identity_problems()` của watcher cho hồ sơ vào.

    Watcher được thiết kế cho ZIP KẾT QUẢ (có SCRIPT_QC_REPORT.md khai tập).
    Hồ sơ Fabel không có file đó, nên watcher kết luận "không khai số tập" và
    chặn nhầm hồ sơ hợp lệ. Gate phải dùng `identity_mismatch()` thay thế.
    """
    from chatgpt_web_handoff.watcher import identity_problems, zip_identity
    probs = identity_problems({"lane": "N", "episode_id": "ep17"}, zip_identity(real_zip))
    assert probs, "watcher phải chặn (đây là lý do gate không dùng nó)"
    assert any("không khai số tập" in p for p in probs)
