# -*- coding: utf-8 -*-
"""test_phase54_script_gate.py — nghiệm thu Phase 5.4 (PRODUCTION READINESS).

Ma trận nghiệm thu theo yêu cầu chủ dự án:

    1. RESEARCH OK                        -> WAITING_OUTLINE, KHÔNG tạo job GPT
    2. SƯỜN OK thiếu ZIP                  -> WAITING_HANDOFF_ZIP
    3. SƯỜN OK đủ ZIP                     -> QUEUED
    4. sai lane/tập                       -> REJECTED (E_GATE_ZIP_WRONG_EPISODE)
    5. đã có OUTPUT                       -> REJECTED (E_GATE_OUTPUT_OCCUPIED)

Cộng thêm các bất biến của Phase 5.4:

    6. ZIP không phải hồ sơ Fabel         -> REJECTED (E_GATE_ZIP_NOT_FABEL)
    7. Hồ sơ bị sửa sau khi đóng gói      -> REJECTED (E_GATE_ZIP_REVISION)
    8. Tập đã có job đang dở              -> REJECTED (E_GATE_DUPLICATE_JOB)
    9. Ô K "cần kịch bản" không có sườn   -> REJECTED (E_GATE_NO_OUTLINE)
   10. Mặc định CHỈ ĐỌC: preflight không ghi gì vào HandoffStore
   11. --apply chỉ ghi QUEUED, KHÔNG gửi gì vào ChatGPT, KHÔNG đánh dấu Sol duyệt
   12. Phase 5.3 vẫn đánh dấu GPT_DELIVERED và KHÔNG đánh dấu script_approved
   13. Không có import writer ngoài (Codex CLI / Sol / API)
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from chatgpt_web_handoff.store import HandoffStore
from services import script_gate as G
from services.state import StateStore

# ---------------------------------------------------------------- dữ liệu mẫu
HOOK = ("三月最後の日曜日、河川敷でキャンピングカーの展示会です。\n"
        "大きな車の列のいちばん奥に、軽自動車のキャンピングカーが一台。\n"
        "その値札の前で、さっきから動かない男がいます。\n"
        "「四百二十万円か。」\n"
        "戸倉誠一さん、六十八歳。\n"
        "農協で三十八年、肥料や資材を配達してきました。\n")
OUTLINE = ("=== KHUNG SƯỜN ===\n"
           "## PHẦN 1 — Tối đó, ông tra cứu (~530 chữ)\n"
           "## PHẦN 2 — Nhận xe (~887 chữ)\n")
TITLE = "【漫画】420万円の軽キャンピングカー"


def _hook_body() -> str:
    return ("# 01_HOOK_OUTLINE — N-ep17\n"
            "# Fabel (claude-fable-5-1) · phiên ['s1'] · 2026-10-24T12:28:22+07:00\n\n"
            f"=== TIÊU ĐỀ DỰ KIẾN ===\n{TITLE}\n\n"
            f"=== HOOK (KHOÁ — chép nguyên văn ở đầu kịch bản; {len(HOOK.replace(chr(10), ''))} chữ) ===\n"
            f"{HOOK}\n"
            f"{OUTLINE}")


def make_fabel_zip(path: Path, *, lane="N", ep=17, model="claude-fable-5-1",
                   tamper_hook=False, with_manifest=True, with_outline=True,
                   sessions=("s1",)) -> Path:
    """ZIP hồ sơ Fabel đúng cấu trúc thật (bọc 1 folder con, có manifest).

    `tamper_hook=True`: manifest vẫn khai checksum của bản GỐC nhưng file trong ZIP
    đã bị sửa -> mô phỏng hồ sơ bị can thiệp sau khi Fabel/Sol chốt.
    """
    body = _hook_body()
    if not with_outline:
        body = body.replace(OUTLINE, "")
    original = body.encode("utf-8")
    written = original
    if tamper_hook:
        # sửa 1 chữ trong khối hook, giữ nguyên mọi thứ khác
        written = original.replace("六十八歳".encode("utf-8"), "七十歳".encode("utf-8"))
        assert written != original, "fixture sai: không sửa được khối hook"

    man = {
        "job_id": f"{lane}-ep{ep}", "lane_id": lane, "episode_id": f"ep{ep}",
        "profile_id": "senior_korekara", "title": TITLE,
        # ID nội bộ, KHÔNG phải sha256 file — đúng như manifest thật N-ep17
        "hook_outline_checksum": "internal-id-not-a-file-hash",
        # checksum của bản GỐC: nếu file bị sửa, bước revision phải phát hiện
        "per_file_checksums": {"01_HOOK_OUTLINE.txt": hashlib.sha256(original).hexdigest()},
        "fabel": {"model": model, "sessions": list(sessions)},
    }
    if sessions:
        hook_text = G.parse_hook(body)["text"]
        man["hook_sol"] = {"state": "PASS_LEAN",
                           "hook_sha256": hashlib.sha256(hook_text.encode()).hexdigest()}
    root = f"Lane{lane}_tap{ep}_GPT_SCRIPT_HANDOFF"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/01_HOOK_OUTLINE.txt", written)
        for extra in ("02_SCRIPT_RULES.md", "03_RESEARCH_FACTS.md", "05_CONTINUITY.md",
                      "07_WRITING_TASK.md", "08_QC_CHECKLIST.md", "10_GPT6_START_HERE.md"):
            zf.writestr(f"{root}/{extra}", f"# {extra}\n")
        if with_manifest:
            zf.writestr(f"{root}/09_HANDOFF_MANIFEST.json",
                        json.dumps(man, ensure_ascii=False, indent=2))
    return path


# ---------------------------------------------------------------- fake Drive
class GateDrive:
    """Drive giả tối thiểu cho gate: folder tập + INPUT/OUTPUT + file."""

    def __init__(self):
        self.folders: dict[str, dict] = {}
        self.files: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self._n = 0

    def _id(self, p):
        self._n += 1
        return f"{p}{self._n:04d}"

    def add_folder(self, name, parent=None):
        fid = self._id("D")
        self.folders[fid] = {"id": fid, "name": name, "mimeType":
                             "application/vnd.google-apps.folder",
                             "parents": [parent] if parent else []}
        return fid

    def add_zip(self, path: Path, parent: str) -> str:
        fid = self._id("F")
        data = Path(path).read_bytes()
        self.files[fid] = {"id": fid, "name": Path(path).name, "parents": [parent],
                           "mimeType": "application/zip", "size": str(len(data)),
                           "modifiedTime": "2026-10-24T00:00:00Z"}
        self.blobs[fid] = data
        return fid

    # --- API bề mặt mà gate dùng
    def get(self, file_id, fields=None):
        if file_id in self.folders:
            return dict(self.folders[file_id])
        if file_id in self.files:
            return dict(self.files[file_id])
        from services.errors import BridgeError
        raise BridgeError("E_DRIVE_NOT_FOUND", file_id)

    def list_children(self, folder_id, *, folders_only=False, name=None):
        out = [dict(f) for f in list(self.folders.values()) + list(self.files.values())
               if folder_id in (f.get("parents") or [])]
        if folders_only:
            out = [f for f in out if f["mimeType"].endswith("folder")]
        if name:
            out = [f for f in out if f["name"] == name]
        return out

    def resolve_pair(self, episode_folder_id):
        kids = self.list_children(episode_folder_id, folders_only=True)
        by = {k["name"]: k["id"] for k in kids}
        from services.errors import BridgeError
        for need, err in (("INPUT", "E_DRIVE_INPUT_MISSING"),
                          ("OUTPUT", "E_DRIVE_OUTPUT_MISSING")):
            if need not in by:
                raise BridgeError(err, f"có {sorted(by)}")
        return {"input_folder_id": by["INPUT"], "output_folder_id": by["OUTPUT"],
                "children": kids}

    def resolve_episode_folder(self, lane, ep_num):
        for f in self.folders.values():
            if f["name"].upper().startswith(f"{lane.upper()}-EP{ep_num}"):
                return {"id": f["id"], "name": f["name"], "version": 1}
        from services.errors import BridgeError
        raise BridgeError("E_DRIVE_NOT_FOUND", f"{lane}-ep{ep_num}")

    def download(self, file_id, dest, expect_sha=None):
        data = self.blobs[file_id]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
        return {"path": Path(dest), "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def web_link(self, file_id, kind="file"):
        if kind == "folder":
            return f"https://drive.google.com/drive/folders/{file_id}"
        return f"https://drive.google.com/file/d/{file_id}/view"


# ---------------------------------------------------------------- fixture
class Env:
    def __init__(self, tmp: Path, cfg: dict, *, approved: bool = True):
        self.tmp = tmp
        # Phê duyệt production là điều kiện #8. Các test kỹ thuật của gate bật sẵn
        # để kiểm nhánh QUEUED; test nào cần kiểm nhánh CHƯA duyệt thì tắt.
        self.cfg = dict(cfg)
        self.cfg["handoff"] = {**(cfg.get("handoff") or {}),
                               "production_upload_enabled": approved}
        self.drive = GateDrive()
        self.handoff = HandoffStore(tmp)
        self.state = StateStore(tmp / "gate.db")
        self.gate = G.ScriptGate(self.cfg, drive=self.drive, state=self.state,
                                 handoff=self.handoff, root=tmp)

    def episode(self, lane="N", ep=17, *, zip_name="fabel", put_zip=True,
                output_zip=False, **zip_kw) -> dict:
        """Dựng folder tập có INPUT/OUTPUT; tuỳ chọn đặt ZIP hồ sơ vào INPUT."""
        f = self.drive.add_folder(f"{lane}-ep{ep}_v01")
        inp = self.drive.add_folder("INPUT", f)
        out = self.drive.add_folder("OUTPUT", f)
        info = {"folder_id": f, "input": inp, "output": out, "lane": lane, "ep": ep}
        if put_zip:
            p = self.tmp / f"{zip_name}_{lane}{ep}.zip"
            make_fabel_zip(p, lane=lane, ep=ep, **zip_kw)
            info["zip_id"] = self.drive.add_zip(p, inp)
        if output_zip:
            p2 = self.tmp / "existing_delivery.zip"
            p2.write_bytes(b"PK\x03\x04old")
            self.drive.add_zip(p2, out)
        return info

    def req(self, cell: str, *, lane="N", ep=17, folder_id=None, row=18, tab="Lane-N"):
        from services.sheet_scanner import ParsedRequest
        return ParsedRequest(
            spreadsheet_id="SHEET", sheet_name=tab, sheet_id=1, row=row,
            ep_num=ep, episode_slug=f"ep{ep}-test",
            local_folder=f"E:\\CLAUDE\\YOUTUBE\\h2dev\\assets\\{lane}-x\\ep{ep}-test",
            cell_raw=cell, status="need_script", status_line="",
            zip_file_id=None, zip_url=None, folder_id=folder_id, folder_url=None,
            extra_notes=[], revision="rev", row_key=f"k{row}")


@pytest.fixture
def env(tmp_path, cfg):
    return Env(tmp_path, cfg)


# ô K mẫu THẬT đo từ Sheet ngày 2026-10-23
CELL_RESEARCH_OK = ("RESEARCH OK 2026-10-23 12:01 — lane_feed đã tải hồ sơ video gốc "
                    "(transcript, thumb, mô tả, phân tích cấu trúc tập + hook) vào thư mục "
                    "cột C. Chưa có tiêu đề/kịch bản: phần h2dev làm.")
CELL_SUON_OK = "Đã xong hook+ sườn- cần kịch bản"
CELL_DONE = ("Kịch bản xong- chờ dựng hook, hồ sơ tại E:\\...\\GPT6_SCRIPT_APPROVED\n"
             "Đã xong hook+ sườn- cần kịch bản\n"
             "RESEARCH OK 2026-10-23 12:00 — lane_feed đã tải hồ sơ")
CELL_NEED_ONLY = "Cần kịch bản cho tập này"


# ================================================================ 1) RESEARCH OK
def test_research_ok_returns_waiting_outline(env):
    """RESEARCH OK -> WAITING_OUTLINE, KHÔNG tạo job GPT, KHÔNG đụng Drive."""
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_RESEARCH_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_WAITING_OUTLINE, r
    assert r["sheet_state"] == G.SHEET_RESEARCH_OK
    assert "Fabel" in r["detail"] and "Hook" in r["detail"]
    # KHÔNG có job nào được tạo
    assert env.handoff.all() == []
    assert env.state.stats() == {}


def test_research_ok_never_touches_drive(env):
    """RESEARCH OK phải dừng TRƯỚC khi gọi Drive (không tốn quota, không tải ZIP)."""
    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"RESEARCH OK không được gọi Drive.{name}")
    env.gate.drive = Boom()
    r = env.gate.preflight(env.req(CELL_RESEARCH_OK), lane="N", ep_num=17)
    assert r["status"] == G.STATUS_WAITING_OUTLINE


# ================================================================ 2) thiếu ZIP
def test_suon_ok_without_zip_returns_waiting_handoff_zip(env):
    """SƯỜN OK nhưng INPUT rỗng -> WAITING_HANDOFF_ZIP, không tạo job."""
    info = env.episode(put_zip=False)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_WAITING_HANDOFF_ZIP, r
    assert r["code"] == "E_GATE_ZIP_MISSING"
    assert env.handoff.all() == []


# ================================================================ 3) đủ ZIP
def test_suon_ok_with_valid_zip_is_queued(env):
    """SƯỜN OK + ZIP hồ sơ Fabel thật trong INPUT -> QUEUED."""
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17)
    assert r["status"] == G.STATUS_QUEUED, r
    assert r["checks"]["zip"]["hook_chars"] == G.parse_hook(_hook_body())["measured"]
    assert r["checks"]["zip"]["outline_present"] is True
    assert r["checks"]["zip"]["fabel"]["model"] == "claude-fable-5-1"
    assert r["checks"]["revision"]["file_matches"] is True
    assert r["checks"]["output"]["ok"] is True


def test_apply_enqueues_only_after_all_checks(env):
    """--apply: chỉ khi đủ điều kiện mới ghi QUEUED; không gửi gì vào ChatGPT."""
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_QUEUED
    job = env.handoff.get("H2DEV-N-ep17-GPT6-v01")
    assert job and job["state"] == "QUEUED", job
    # ranh giới trách nhiệm: chưa duyệt, chưa giao
    assert job.get("script_approved") is False
    assert not job.get("gpt_state")
    assert any(e["kind"] == "gate_queued" for e in job["events"])


def test_dry_preflight_writes_nothing(env):
    """Mặc định CHỈ ĐỌC: preflight không tạo job, không đổi state."""
    info = env.episode()
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=False)
    assert r["status"] == G.STATUS_QUEUED
    assert env.handoff.all() == []
    assert env.state.stats() == {}


# ================================================================ 4) sai lane/tập
def test_wrong_lane_in_zip_is_rejected(env):
    """ZIP khai lane N nhưng dòng Sheet là lane O -> REJECTED, không xếp hàng."""
    f = env.drive.add_folder("O-ep23_v01")
    inp = env.drive.add_folder("INPUT", f)
    env.drive.add_folder("OUTPUT", f)
    p = env.tmp / "wrong_lane.zip"
    make_fabel_zip(p, lane="N", ep=23)          # lane N, không phải O
    env.drive.add_zip(p, inp)
    r = env.gate.preflight(env.req(CELL_SUON_OK, lane="O", ep=23, folder_id=f),
                           lane="O", ep_num=23, apply=True)
    assert r["status"] == G.STATUS_REJECTED, r
    assert r["code"] == "E_GATE_ZIP_WRONG_EPISODE"
    assert env.handoff.all() == []


def test_wrong_episode_in_zip_is_rejected(env):
    """ZIP khai ep17 nhưng job là ep23 -> REJECTED."""
    f = env.drive.add_folder("N-ep23_v01")
    inp = env.drive.add_folder("INPUT", f)
    env.drive.add_folder("OUTPUT", f)
    p = env.tmp / "wrong_ep.zip"
    make_fabel_zip(p, lane="N", ep=17)          # ep17, không phải ep23
    env.drive.add_zip(p, inp)
    r = env.gate.preflight(env.req(CELL_SUON_OK, lane="N", ep=23, folder_id=f),
                           lane="N", ep_num=23, apply=True)
    assert r["status"] == G.STATUS_REJECTED, r
    assert r["code"] == "E_GATE_ZIP_WRONG_EPISODE"
    assert env.handoff.all() == []


# ================================================================ 5) đã có OUTPUT
def test_existing_output_blocks_queue(env):
    """OUTPUT đã có ZIP kịch bản -> REJECTED, KHÔNG ghi đè."""
    info = env.episode(output_zip=True)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_REJECTED, r
    assert r["code"] == "E_GATE_OUTPUT_OCCUPIED"
    assert env.handoff.all() == []


# ================================================================ 6) ZIP không phải Fabel
def test_zip_without_hook_outline_is_not_fabel(env):
    """ZIP chỉ có file linh tinh -> E_GATE_ZIP_NOT_FABEL."""
    f = env.drive.add_folder("N-ep17_v01")
    inp = env.drive.add_folder("INPUT", f)
    env.drive.add_folder("OUTPUT", f)
    p = env.tmp / "junk.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("random.txt", "không phải hồ sơ")
    env.drive.add_zip(p, inp)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=f), lane="N", ep_num=17)
    assert r["status"] == G.STATUS_REJECTED, r
    assert r["code"] == "E_GATE_ZIP_NOT_FABEL"


def test_zip_without_outline_section_is_not_fabel(env):
    """Có HOOK nhưng thiếu KHUNG SƯỜN -> Fabel chưa hoàn thành Outline."""
    f = env.drive.add_folder("N-ep17_v01")
    inp = env.drive.add_folder("INPUT", f)
    env.drive.add_folder("OUTPUT", f)
    p = env.tmp / "hook_only.zip"
    make_fabel_zip(p, lane="N", ep=17, with_outline=False)
    env.drive.add_zip(p, inp)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=f), lane="N", ep_num=17)
    assert r["status"] == G.STATUS_REJECTED
    assert r["code"] == "E_GATE_ZIP_NOT_FABEL"
    zc = r["checks"]["zip"]
    assert any("KHUNG SƯỜN" in x for x in zc["reasons"]), zc


def test_zip_without_manifest_is_not_fabel(env):
    """Thiếu 09_HANDOFF_MANIFEST.json -> không xác minh được nguồn Fabel."""
    f = env.drive.add_folder("N-ep17_v01")
    inp = env.drive.add_folder("INPUT", f)
    env.drive.add_folder("OUTPUT", f)
    p = env.tmp / "no_manifest.zip"
    make_fabel_zip(p, lane="N", ep=17, with_manifest=False)
    env.drive.add_zip(p, inp)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=f), lane="N", ep_num=17)
    assert r["status"] == G.STATUS_REJECTED
    assert r["code"] == "E_GATE_ZIP_NOT_FABEL"


def test_manifest_without_fabel_sessions_is_rejected(env):
    """Manifest không khai phiên Fabel -> không chứng minh Hook do Fabel sinh."""
    f = env.drive.add_folder("N-ep17_v01")
    inp = env.drive.add_folder("INPUT", f)
    env.drive.add_folder("OUTPUT", f)
    p = env.tmp / "no_sessions.zip"
    make_fabel_zip(p, lane="N", ep=17, sessions=())
    env.drive.add_zip(p, inp)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=f), lane="N", ep_num=17)
    assert r["status"] == G.STATUS_REJECTED
    assert r["code"] == "E_GATE_ZIP_NOT_FABEL"
    zc = r["checks"]["zip"]
    assert any("fabel.sessions" in x for x in zc["reasons"]), zc


# ================================================================ 7) revision
def test_tampered_hook_file_is_rejected(env):
    """File hook bị sửa sau khi đóng gói -> E_GATE_ZIP_REVISION."""
    f = env.drive.add_folder("N-ep17_v01")
    inp = env.drive.add_folder("INPUT", f)
    env.drive.add_folder("OUTPUT", f)
    p = env.tmp / "tampered.zip"
    make_fabel_zip(p, lane="N", ep=17, tamper_hook=True)
    env.drive.add_zip(p, inp)
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=f), lane="N", ep_num=17,
                           apply=True)
    assert r["status"] == G.STATUS_REJECTED, r
    assert r["code"] == "E_GATE_ZIP_REVISION"
    assert env.handoff.all() == []


# ================================================================ 8) trùng job
def test_active_state_job_blocks_duplicate(env):
    """StateStore đã có job dở cho cùng tập -> E_GATE_DUPLICATE_JOB."""
    info = env.episode()
    env.state.create({"job_id": "OLD-1", "project": "H2DEV", "lane": "N",
                      "episode_id": "ep17", "row_key": "old", "status": "SUBMITTING"})
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_REJECTED, r
    assert r["code"] == "E_GATE_DUPLICATE_JOB"
    assert env.handoff.all() == []


def test_busy_handoff_job_blocks_duplicate(env):
    """HandoffStore đã có job QUEUED/SENT cho cùng tập -> E_GATE_DUPLICATE_JOB."""
    info = env.episode()
    env.handoff.enqueue({"job_id": "H2DEV-N-ep17-GPT6-v01", "lane": "N",
                         "episode_id": "ep17"})
    r = env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                           lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_REJECTED
    assert r["code"] == "E_GATE_DUPLICATE_JOB"


def test_reapply_same_episode_is_idempotent(env):
    """Chạy lại sau khi đã QUEUED không tạo bản ghi thứ hai."""
    info = env.episode()
    req = env.req(CELL_SUON_OK, folder_id=info["folder_id"])
    r1 = env.gate.preflight(req, lane="N", ep_num=17, apply=True)
    assert r1["status"] == G.STATUS_QUEUED
    n1 = len(env.handoff.all())
    r2 = env.gate.preflight(req, lane="N", ep_num=17, apply=True)
    assert r2["status"] == G.STATUS_REJECTED       # đã có job -> chặn, không nhân bản
    assert len(env.handoff.all()) == n1


# ================================================================ 9) ô K thiếu sườn
def test_need_script_without_outline_marker_is_rejected(env):
    """Ô K chỉ ghi 'cần kịch bản' (chưa xong Hook+Sườn) -> E_GATE_NO_OUTLINE."""
    r = env.gate.preflight(env.req(CELL_NEED_ONLY), lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_REJECTED
    assert r["code"] == "E_GATE_NO_OUTLINE"
    assert env.handoff.all() == []


def test_script_done_row_is_not_a_writing_job(env):
    """Ô K đã 'kịch bản xong' -> ALREADY_SCRIPTED, không phải job viết kịch bản."""
    r = env.gate.preflight(env.req(CELL_DONE), lane="N", ep_num=17, apply=True)
    assert r["status"] == G.STATUS_ALREADY_SCRIPTED
    assert env.handoff.all() == []


def test_empty_cell_is_rejected(env):
    r = env.gate.preflight(env.req(""), lane="N", ep_num=17)
    assert r["status"] == G.STATUS_REJECTED
    assert r["code"] == "E_GATE_NO_MARKER"


# ================================================================ phân loại ô K
@pytest.mark.parametrize("cell,want", [
    (CELL_RESEARCH_OK, G.SHEET_RESEARCH_OK),
    (CELL_SUON_OK, G.SHEET_SUON_OK),
    (CELL_NEED_ONLY, G.SHEET_NEED_SCRIPT),
    ("RESEARCH OK 2026-10-23", G.SHEET_RESEARCH_OK),
    ("research ok", G.SHEET_RESEARCH_OK),
    ("Đã xong hook + sườn", G.SHEET_SUON_OK),
    ("xong hook, sườn OK", G.SHEET_SUON_OK),
    ("", G.SHEET_IGNORE),
])
def test_sheet_decision_classifies(cell, want):
    st, _ = G.sheet_decision(cell, G.markers_from_cfg({}))
    assert st == want


def test_first_line_wins_suon_before_research():
    """Dòng ĐẦU TIÊN có marker thắng: sườn ở dòng 1, RESEARCH OK ở dòng 3."""
    cell = f"{CELL_SUON_OK}\nRESEARCH OK 2026-10-23 12:00 — đã tải hồ sơ"
    st, line = G.sheet_decision(cell, G.markers_from_cfg({}))
    assert st == G.SHEET_SUON_OK
    assert line == CELL_SUON_OK


def test_script_done_outranks_lower_lines():
    """Kịch bản xong ở dòng 1 thắng cả 'cần kịch bản' ở dòng 2 (dữ liệu thật N-ep17)."""
    st, _ = G.sheet_decision(CELL_DONE, G.markers_from_cfg({}))
    assert st == G.SHEET_SCRIPT_DONE


def test_suon_ok_outranks_need_script_in_same_line():
    """'Đã xong hook+ sườn- cần kịch bản' chứa cả 'cần kịch bản' -> phải là SƯỜN OK."""
    st, _ = G.sheet_decision(CELL_SUON_OK, G.markers_from_cfg({}))
    assert st == G.SHEET_SUON_OK


def test_markers_from_config_extend_defaults():
    """config có thể bổ sung marker mà không mất marker mặc định."""
    cfg = {"sheets": {"status_rules": {"suon_ok": ["sườn đã chốt"]}}}
    m = G.markers_from_cfg(cfg)
    assert "suon da chot" in m[G.SHEET_SUON_OK]
    assert "da xong hook" in m[G.SHEET_SUON_OK]


# ================================================================ ZIP an toàn
def test_zip_traversal_is_rejected(env):
    """ZIP có đường dẫn .. -> không phải hồ sơ hợp lệ."""
    p = env.tmp / "evil.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("../../evil.txt", "x")
        zf.writestr("01_HOOK_OUTLINE.txt", _hook_body())
    prof = G.fabel_profile_problems(p)
    assert not prof["ok"]
    assert any("nguy hiểm" in x for x in prof["problems"])


def test_dangerous_file_extension_is_rejected(env):
    p = env.tmp / "evil2.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("01_HOOK_OUTLINE.txt", _hook_body())
        zf.writestr("run.exe", b"MZ")
    prof = G.fabel_profile_problems(p)
    assert not prof["ok"]
    assert any("nguy hiểm" in x for x in prof["problems"])


def test_corrupt_zip_is_reported_not_raised(env):
    p = env.tmp / "corrupt.zip"
    p.write_bytes(b"not a zip at all")
    prof = G.fabel_profile_problems(p)
    assert prof["ok"] is False
    assert any("không mở được" in x for x in prof["problems"])


# ================================================================ ranh giới trách nhiệm
def test_gate_does_not_import_forbidden_writers():
    """Không Codex CLI, không Sol, không API writer: kiểm IMPORT thật, không grep chữ."""
    import ast
    tree = ast.parse(Path(G.__file__).read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.lower() for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.lower())
    forbidden = ("codex", "openai", "anthropic", "sol", "api_writer")
    hits = {m for m in mods if any(f in m for f in forbidden)}
    assert not hits, f"script_gate.py import writer bị cấm: {sorted(hits)}"
    # gate chỉ được dùng module nội bộ của dự án + stdlib (không SDK của nhà cung cấp)
    allowed_prefix = ("chatgpt_web_handoff", "services", "__future__", "hashlib", "json",
                      "re", "time", "unicodedata", "zipfile", "pathlib",
                      # import tương đối trong package services/
                      "drive_client", "errors", "prompt_builder", "state")
    bad = {m for m in mods if not m.startswith(allowed_prefix)}
    assert not bad, f"script_gate.py import ngoài phạm vi: {sorted(bad)}"


def test_gate_never_marks_sol_approved():
    """Module gate KHÔNG có hằng/khái niệm 'Sol đã duyệt'."""
    src = Path(G.__file__).read_text(encoding="utf-8").lower()
    assert "sol_approved" not in src
    assert "script_approved=true" not in src.replace(" ", "")


def test_queued_job_carries_script_approved_false(env):
    info = env.episode()
    env.gate.preflight(env.req(CELL_SUON_OK, folder_id=info["folder_id"]),
                       lane="N", ep_num=17, apply=True)
    job = env.handoff.get("H2DEV-N-ep17-GPT6-v01")
    assert job["script_approved"] is False
