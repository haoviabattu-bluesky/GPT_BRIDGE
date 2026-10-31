# -*- coding: utf-8 -*-
"""tests/test_phase53_handoff_coordinator.py — 11 mục kiểm thử BẮT BUỘC của Phase 5.3.

Không gọi mạng: Drive là `FakeDrive` trong conftest; HandoffStore/StateStore trỏ vào
thư mục tạm. Mục nào cần Drive thật thì nằm ở `scratch_analysis/phase53_acceptance.py`.

Đối chiếu 11 mục trong yêu cầu Phase 5.3:
  1. test_01_job_downloaded_duoc_tu_phat_hien
  2. test_02_sai_lane_episode_bi_chan
  3. test_03_zip_loi_bi_chan                 (+ test_03b_zip_hong_han_bi_chan)
  4. test_04_khong_upload_hai_lan
  5. test_05_restart_sau_upload_khong_nhan_ban
  6. test_06_khong_completed_khi_verify_that_bai
  7. test_07_job_smoke_khong_cham_output_san_xuat
  8. test_08_production_khong_ghi_de_zip_da_co
  9. test_09_mot_watcher_logic_duy_nhat
 10. test_10_khong_chiem_chuot_ban_phim
 11. test_11_hai_store_dong_bo  (+ toàn bộ test Phase 1–5.2 vẫn phải PASS)
"""
from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path

import pytest

from chatgpt_web_handoff.store import HandoffStore
from services import handoff_coordinator as H
from services.errors import BridgeError
from services.h2dev_delivery import H2DevDelivery, MANIFEST_NAME, SCRIPT_NAME
from services.handoff_coordinator import HandoffCoordinator, classify_job
from services.state import StateStore

HOOK = "六十八歳の春、わたしは四百二十万円の軽キャンピングカーを、買わずに一か月だけ借りた。"
FILLER = "その日のことを、まだ覚えている。"

PROD_ZIP = "LaneN_tap17_GPT6_SCRIPT_OUTPUT.zip"     # ZIP sản xuất đã có trong OUTPUT


# ------------------------------------------------------------------ tiện ích dựng
def make_result_zip(path: Path, *, lane="N", ep="ep17", job_id=None, drop=None,
                    corrupt=False, body=None) -> Path:
    """ZIP kết quả 4 file ở gốc, header tự khai `<lane>-<ep>` như ChatGPT trả về."""
    path = Path(path)
    if corrupt:
        path.write_bytes(b"day khong phai la mot file ZIP")
        return path
    jid = job_id or f"{lane}-{ep}"
    files = {
        "SCRIPT_FINAL_JP.txt": body if body is not None else (HOOK + "\n" + FILLER * 20),
        "SCRIPT_QC_REPORT.md": f"# SCRIPT_QC_REPORT — {lane}-{ep}\n- Job: {jid}\n- Độ dài: ok\n",
        "SCRIPT_ISSUES.md": f"# SCRIPT_ISSUES — {lane}-{ep}\n- không có\n",
        "SCRIPT_STRUCTURE.md": f"# SCRIPT_STRUCTURE — {lane}-{ep}\n\n| PHẦN | ký tự |\n",
    }
    if drop:
        files.pop(drop, None)
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path


def sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Env:
    """Một cảnh thử: Drive giả + HandoffStore + StateStore + coordinator."""

    def __init__(self, cfg, fake_drive, tmp_path, *, episode="ep17", lane="N",
                 job_class=None, job_id=None, result_zip=None, production_approved=False):
        self.cfg = cfg
        self.drive = fake_drive
        self.root = Path(cfg["_root"])
        self.job_id = job_id or f"SMOKE51-{lane}-{episode}-GPT6-v01"
        self.job_class = job_class or "SMOKE"
        self.ep = episode

        # --- folder tập N-ep17_v01 {INPUT, OUTPUT} + ZIP sản xuất trong OUTPUT
        self.ep_folder = fake_drive.add_folder(f"{lane}-{episode}_v01", "INBOX_FAKE")
        self.input_folder = fake_drive.add_folder("INPUT", self.ep_folder)
        self.output_folder = fake_drive.add_folder("OUTPUT", self.ep_folder)
        fake_drive.add_file(PROD_ZIP, self.output_folder, b"ZIP SAN XUAT DA CO")
        # --- folder thử nghiệm (KHÁC hẳn OUTPUT sản xuất)
        self.test_folder = fake_drive.add_folder("PHASE52_TEST_OUTPUT")

        self.zip = Path(result_zip) if result_zip else make_result_zip(
            Path(tmp_path) / f"{self.job_id}_RESULT.zip", lane=lane, ep=episode,
            job_id=self.job_id)

        self.store = HandoffStore(self.root)
        self.store.enqueue({
            "job_id": self.job_id, "project": "H2DEV", "lane": lane,
            "episode_id": episode, "job_class": self.job_class,
            "output_zip_name": f"{self.job_id}_RESULT.zip",
            "workdir": str(self.root / "jobs" / self.job_id),
            "conversation_url": "https://chatgpt.com/c/SMOKE51",
            "output_folder_url": fake_drive.web_link(self.output_folder, kind="folder"),
        })
        self.store.update(self.job_id, result_zip_path=str(self.zip),
                          result_sha256=sha256(self.zip))
        if production_approved:
            self.store.update(self.job_id, production_upload_enabled=True)
        # CHỈ đổi trạng thái — không tạo bản ghi StateStore bằng tay (đúng ràng buộc nghiệm thu)
        self.store.set_state(self.job_id, "DOWNLOADED", "watcher nhận ZIP kết quả")

        cfg.setdefault("upload", {})["test_output_folder_id"] = self.test_folder
        self.state = StateStore(self.root / "jobs" / "bridge.db")
        self.co = HandoffCoordinator(cfg, root=self.root, handoff_store=self.store,
                                     state=self.state, drive=fake_drive)

    # --- tiện ích kiểm
    def children(self, folder_id):
        return [f["name"] for f in self.drive.list_children(folder_id)]

    def state_rec(self):
        return self.state.get(self.job_id)

    def handoff_rec(self):
        return self.store.get(self.job_id)


@pytest.fixture
def env(cfg, fake_drive, tmp_path):
    def _make(**kw):
        return Env(cfg, fake_drive, tmp_path, **kw)
    return _make


# ============================================================ 1. tự phát hiện DOWNLOADED
def test_01_job_downloaded_duoc_tu_phat_hien(env):
    e = env()
    pending = e.co.jobs_to_process()
    assert [j["job_id"] for j in pending] == [e.job_id], "coordinator phải tự thấy job DOWNLOADED"

    res = e.co.tick()
    assert len(res) == 1
    r = res[0]
    assert r["status"] == "COMPLETED", r
    assert r["job_class"] == "SMOKE"
    assert r["uploads"] == 1
    assert r["sha256_in"] == sha256(e.zip)
    assert r["sha256_out"] == r["verified_sha256"], "bước 9 phải đọc lại và khớp SHA-256"
    # ghi vào folder thử nghiệm, KHÔNG vào OUTPUT sản xuất
    assert e.children(e.output_folder) == [PROD_ZIP], "OUTPUT sản xuất phải nguyên vẹn"
    assert "LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip" in e.children(e.test_folder)
    # không dùng thao tác tay: job do DOWNLOADED mà ra, có event do coordinator ghi
    assert e.state_rec()["row_key"] == f"handoff:{e.job_id}"
    assert e.handoff_rec()["state"] == "COMPLETED"
    assert e.handoff_rec()["coordinator"]["uploads"] == 1
    assert e.co.read_journal(e.job_id)["stage"] == "completed"


# ============================================================ 2. sai lane/tập
def test_02_sai_lane_episode_bi_chan(cfg, fake_drive, tmp_path):
    z = Path(tmp_path) / "SMOKE51-N-ep18-GPT6-v01_RESULT.zip"
    make_result_zip(z, lane="N", ep="ep18", job_id="SMOKE51-N-ep18-GPT6-v01")
    e = Env(cfg, fake_drive, tmp_path, result_zip=z)
    r = e.co.tick()[0]
    assert r["status"] == "NEEDS_REVIEW" and r["code"] == "E_HANDOFF_WRONG_EPISODE", r
    assert r["uploads"] == 0
    assert e.children(e.test_folder) == []                     # KHÔNG upload gì
    assert e.children(e.output_folder) == [PROD_ZIP]
    assert e.handoff_rec()["state"] == "NEEDS_REVIEW"


# ============================================================ 3. ZIP lỗi / ZIP hỏng
def test_03_zip_loi_bi_chan(cfg, fake_drive, tmp_path):
    z = Path(tmp_path) / "SMOKE51-N-ep17-GPT6-v01_RESULT.zip"
    make_result_zip(z, drop="SCRIPT_STRUCTURE.md")
    e = Env(cfg, fake_drive, tmp_path, result_zip=z)
    r = e.co.tick()[0]
    assert r["status"] == "NEEDS_REVIEW" and r["code"] == "E_HANDOFF_BAD_ZIP", r
    assert r["uploads"] == 0 and e.children(e.test_folder) == []


def test_03b_zip_hong_han_bi_chan(cfg, fake_drive, tmp_path):
    z = Path(tmp_path) / "SMOKE51-N-ep17-GPT6-v01_RESULT.zip"
    make_result_zip(z, corrupt=True)
    e = Env(cfg, fake_drive, tmp_path, result_zip=z)
    r = e.co.tick()[0]
    assert r["status"] == "NEEDS_REVIEW" and r["code"] == "E_HANDOFF_BAD_ZIP", r
    assert r["uploads"] == 0


def test_03c_sha_doi_tren_dia_bi_chan(env):
    """ZIP bị ghi đè sau khi watcher băm -> không được upload."""
    e = env()
    make_result_zip(e.zip, body=HOOK + "\n" + FILLER * 25)     # nội dung khác, sha khác
    r = e.co.tick()[0]
    assert r["status"] == "NEEDS_REVIEW" and r["code"] == "E_HANDOFF_ZIP_CHANGED", r
    assert r["uploads"] == 0 and e.children(e.test_folder) == []


# ============================================================ 4. không upload hai lần
def test_04_khong_upload_hai_lan(env):
    e = env()
    r1 = e.co.tick()[0]
    assert r1["status"] == "COMPLETED" and r1["uploads"] == 1
    assert len(e.drive.list_children(e.test_folder)) == 1
    # tick lần hai: job đã COMPLETED nên không còn trong hàng DOWNLOADED
    assert e.co.jobs_to_process() == []
    r2 = e.co.process_one(e.handoff_rec(), apply=True)
    assert r2["status"] == "SKIPPED" and r2["uploads"] == 0
    assert len(e.drive.list_children(e.test_folder)) == 1, "KHÔNG được sinh file thứ hai"
    # và nếu ai đó ép chạy lại đường upload, tên trùng + sha khớp -> nhận lại, không upload
    e.store.set_state(e.job_id, "DOWNLOADED")
    e.state.update(e.job_id, status="UPLOADING")
    r3 = e.co.process_one(e.store.get(e.job_id), apply=True)
    assert r3["uploads"] == 0 and r3["status"] == "COMPLETED", r3
    assert len(e.drive.list_children(e.test_folder)) == 1


# ============================================================ 5. restart sau upload
def test_05_restart_sau_upload_khong_nhan_ban(cfg, fake_drive, tmp_path):
    """Crash sau upload, TRƯỚC khi StateStore COMPLETED -> restart không tạo bản trùng."""
    e = env = None  # noqa: F841 (giữ tên cho dễ đọc)
    e = Env(cfg, fake_drive, tmp_path)
    # lần chạy 1: dừng ngay SAU upload bằng cách tiêm lỗi vào _adopt_or_upload
    real = e.co._adopt_or_upload

    def fake(job, dest, out_name, local_zip, sha_local, tmp_dir):
        up, n, note = real(job, dest, out_name, local_zip, sha_local, tmp_dir)
        e.co.write_journal(job["job_id"], stage=H.STAGE_UPLOADED, file_id=up["file_id"],
                           out_name=out_name, dest_folder_id=dest["folder_id"],
                           sha256_out=sha_local)
        raise BridgeError("E_DRIVE_UPLOAD", "giả lập crash ngay sau upload")

    e.co._adopt_or_upload = fake
    r1 = e.co.tick()[0]
    assert r1["status"] == "FAILED" and r1["code"] == "E_DRIVE_UPLOAD"
    assert len(e.drive.list_children(e.test_folder)) == 1, "lần 1 đã upload 1 file"
    assert e.co.read_journal(e.job_id)["stage"] == H.STAGE_UPLOADED
    assert (e.state_rec() or {}).get("status") != "COMPLETED"

    # ---- restart: coordinator mới, cùng đĩa
    e.co._adopt_or_upload = real
    e.store.set_state(e.job_id, "DOWNLOADED")        # watcher/người vận hành cho chạy lại
    co2 = HandoffCoordinator(cfg, root=Path(cfg["_root"]), handoff_store=e.store,
                             state=StateStore(Path(cfg["_root"]) / "jobs" / "bridge.db"),
                             drive=fake_drive)
    r2 = co2.tick()[0]
    assert r2["status"] == "COMPLETED", r2
    assert r2["uploads"] == 0, "phải NHẬN LẠI file đã có, không upload lần hai"
    assert r2["mode"] == "recover"
    assert len(e.drive.list_children(e.test_folder)) == 1


def test_05b_restart_giua_upload_kiem_drive_truoc(cfg, fake_drive, tmp_path):
    """journal 'uploading' + Drive CHƯA có file -> được phép upload lại (0 kết quả)."""
    e = Env(cfg, fake_drive, tmp_path)
    e.co.write_journal(e.job_id, stage=H.STAGE_UPLOADING, out_name="LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip",
                       dest_folder_id=e.test_folder, sha256_out="x" * 64)
    r = e.co.tick()[0]
    assert r["status"] == "COMPLETED" and r["mode"] == "recover"
    assert r["uploads"] == 1, "Drive xác nhận trống thì upload là an toàn"
    assert len(e.drive.list_children(e.test_folder)) == 1


def test_05c_ten_trung_nhung_sha_khac_bi_chan(cfg, fake_drive, tmp_path):
    """Cùng tên trên Drive nhưng nội dung khác -> CONFLICT, tuyệt đối không ghi đè."""
    e = Env(cfg, fake_drive, tmp_path)
    fake_drive.add_file("LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip", e.test_folder,
                        b"ban khac han cua nguoi khac")
    r = e.co.tick()[0]
    assert r["status"] == "BLOCKED" and r["code"] == "E_HANDOFF_CONFLICT", r
    assert r["uploads"] == 0
    assert len(e.drive.list_children(e.test_folder)) == 1


# ============================================================ 6. verify thất bại
def test_06_khong_completed_khi_verify_that_bai(env):
    """Đọc lại Drive không khớp SHA-256 -> FAILED, KHÔNG COMPLETED, không ghi đè."""
    e = env()
    real_dl = e.drive.download
    calls = {"n": 0}

    def lying_download(file_id, dest, expect_sha=None):
        got = real_dl(file_id, dest, expect_sha)
        calls["n"] += 1
        if calls["n"] >= 2:            # lần đọc lại độc lập (bước 9) -> trả sha sai
            got = dict(got, sha256="0" * 64)
        return got

    e.drive.download = lying_download
    r = e.co.tick()[0]
    assert r["status"] == "FAILED" and r["code"] == "E_HANDOFF_NOT_COMPLETED", r
    assert (e.state_rec() or {}).get("status") != "COMPLETED"
    assert e.handoff_rec()["state"] != "COMPLETED"
    assert e.co.read_journal(e.job_id)["stage"] != H.STAGE_COMPLETED


# ============================================================ 7. SMOKE không chạm sản xuất
def test_07_job_smoke_khong_cham_output_san_xuat(env):
    e = env()
    # bản ghi trỏ OUTPUT sản xuất: nếu coordinator nghe theo record thì đã sai
    assert H.folder_id_from_url(e.handoff_rec()["output_folder_url"]) == e.output_folder
    r = e.co.tick()[0]
    assert r["status"] == "COMPLETED"
    assert r["dest_kind"] == "TEST" and r["dest_folder_id"] == e.test_folder
    assert e.children(e.output_folder) == [PROD_ZIP], "OUTPUT sản xuất phải nguyên vẹn"
    assert e.drive.get(e.output_folder)["name"] == "OUTPUT"


def test_07b_smoke_khong_co_folder_thu_nghiem_thi_chan(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path)
    cfg["upload"]["test_output_folder_id"] = None
    cfg["handoff_coordinator"] = {"test_output_folder_id": None}
    r = e.co.tick()[0]
    assert r["status"] == "BLOCKED" and r["code"] == "E_HANDOFF_NO_TEST_FOLDER", r
    assert r["uploads"] == 0 and e.children(e.output_folder) == [PROD_ZIP]
    # job giữ nguyên DOWNLOADED để tick sau tự đánh giá lại
    assert e.handoff_rec()["state"] == "DOWNLOADED"


def test_07c_folder_thu_nghiem_trung_san_xuat_thi_chan(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path)
    cfg["upload"]["test_output_folder_id"] = e.output_folder     # cấu hình SAI
    r = e.co.tick()[0]
    assert r["status"] == "BLOCKED" and r["code"] == "E_HANDOFF_TEST_EQUALS_PRODUCTION", r
    assert r["uploads"] == 0


# ============================================================ 8. production không ghi đè
def test_08_production_khong_ghi_de_zip_da_co(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path, job_class="PRODUCTION", production_approved=True)
    r = e.co.tick()[0]
    assert r["status"] == "BLOCKED" and r["code"] == "E_HANDOFF_OUTPUT_OCCUPIED", r
    assert r["uploads"] == 0
    assert e.children(e.output_folder) == [PROD_ZIP], "ZIP sản xuất phải nguyên vẹn"


def test_08b_production_chua_phe_duyet_thi_chan(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path, job_class="PRODUCTION")   # không bật cờ job
    assert e.cfg.get("handoff_coordinator", {}).get("production_upload_enabled") is not True
    r = e.co.tick()[0]
    assert r["status"] == "BLOCKED" and r["code"] == "E_HANDOFF_NOT_APPROVED", r
    assert r["uploads"] == 0


def test_08c_production_duoc_phe_duyet_thi_upload_dung_output(cfg, fake_drive, tmp_path):
    """Đường sản xuất hợp lệ: OUTPUT TRỐNG + phê duyệt đúng job -> upload vào OUTPUT."""
    e = Env(cfg, fake_drive, tmp_path, job_class="PRODUCTION", production_approved=True)
    fake_drive.delete(fake_drive.find_in_folder(e.output_folder, PROD_ZIP)[0]["id"])
    r = e.co.tick()[0]
    assert r["status"] == "COMPLETED" and r["dest_kind"] == "PRODUCTION", r
    assert r["dest_folder_id"] == e.output_folder
    assert e.children(e.output_folder) == ["LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip"]
    assert e.children(e.test_folder) == [], "production KHÔNG được ghi sang folder thử nghiệm"


# ============================================================ 9. một watcher logic
def test_09_mot_watcher_logic_duy_nhat():
    """Coordinator chỉ ĐỌC HandoffStore; không quét thư mục nhận, không tự chạy watcher."""
    src = Path(H.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    # quét thư mục nhận / tự chạy watcher: TUYỆT ĐỐI không
    forbidden = {"scan_once", "iterdir", "rglob", "walk", "listdir", "run_forever_watcher"}
    assert not (called & forbidden), f"coordinator không được tự quét thư mục: {called & forbidden}"
    assert "ResultWatcher" not in src and "ResultWatcher(" not in src
    assert "import pyautogui" not in src
    # `glob` chỉ được dùng để tìm JOURNAL của chính nó (không phải thư mục Downloads)
    globs = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "glob"]
    assert globs, "phải có glob tìm journal"
    for g in globs:
        arg = g.args[0] if g.args else None
        assert isinstance(arg, ast.JoinedStr), f"glob phải là f-string có mẫu cố định: {ast.dump(g)[:80]}"
        pat = "".join(v.value for v in arg.values if isinstance(v, ast.Constant))
        holders = {ast.unparse(v.value) for v in arg.values if isinstance(v, ast.FormattedValue)}
        assert "jobs" in pat, f"glob lạ: {pat}"
        assert any("journal" in h.lower() for h in holders), (
            f"glob phải trỏ tới journal của chính nó, không phải thư mục nhận: {pat}{holders}")
    # vòng lặp duy nhất: chỉ có MỘT định nghĩa run_forever trong coordinator
    assert src.count("def run_forever") == 1


def test_09b_chi_co_mot_vong_lap_duoc_thiet_lap(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path)
    ticks = {"n": 0}

    def once(*a, **k):
        ticks["n"] += 1
        return []

    e.co.tick = once
    e.co.run_forever(interval=0, max_ticks=3)
    assert ticks["n"] == 3, "run_forever phải dùng đúng tick() của chính nó"


# ============================================================ 10. không chiếm chuột/bàn phím
def test_10_khong_chiem_chuot_ban_phim():
    mods = [Path(H.__file__), Path(H.__file__).with_name("h2dev_delivery.py")]
    banned = {"pyautogui", "pynput", "keyboard", "mouse", "win32api", "uiautomation",
              "computer_use", "playwright", "selenium", "pydirectinput", "ctypes"}
    for m in mods:
        tree = ast.parse(m.read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module.split(".")[0])
        hit = names & banned
        assert not hit, f"{m.name} không được dùng {hit} (sẽ chiếm chuột/bàn phím)"


# ============================================================ 11. hai store đồng bộ
def test_11_hai_store_dong_bo(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path)
    e.co.tick()
    st, hs = e.state_rec(), e.handoff_rec()
    assert st["status"] == "COMPLETED" and hs["state"] == "COMPLETED"
    assert st["output_file_id"] == hs["coordinator"]["output_file_id"]
    assert st["output_sha256"] == hs["coordinator"]["output_sha256"]
    assert st["output_name"] == hs["coordinator"]["output_name"]
    ev = [x["to_state"] for x in e.state.events(e.job_id)]
    assert ev[-1] == "COMPLETED" and "VERIFYING" in ev and "UPLOADING" in ev


def test_11b_blocked_giu_hai_store_nhat_quan(cfg, fake_drive, tmp_path):
    """Job bị chặn hành chính: StateStore BLOCKED, HandoffStore vẫn DOWNLOADED (để thử lại)."""
    e = Env(cfg, fake_drive, tmp_path, job_class="PRODUCTION")
    r = e.co.tick()[0]
    assert r["status"] == "BLOCKED"
    assert e.state_rec()["status"] == "BLOCKED"
    assert e.state_rec()["error_code"] == "E_HANDOFF_NOT_APPROVED"
    assert e.handoff_rec()["state"] == "DOWNLOADED"


# ============================================================ dry-run & doctor
def test_dry_run_khong_ghi_gi(env):
    e = env()
    r = e.co.tick(apply=False)[0]
    assert r["status"] == "DRY_RUN"
    assert r["uploads"] == 0
    assert e.children(e.test_folder) == []
    assert e.state_rec() is None, "dry-run không được tạo bản ghi StateStore"
    assert e.co.read_journal(e.job_id) == {}
    assert e.handoff_rec()["state"] == "DOWNLOADED"


def test_classify_job_tuong_minh_thang_doan_ten(env):
    assert classify_job({"job_id": "SMOKE51-N-ep17-GPT6-v01"}) == "SMOKE"
    assert classify_job({"job_id": "H2DEV-N-ep17-GPT6-v01"}) == "PRODUCTION"
    assert classify_job({"job_id": "X-1", "job_class": "PRODUCTION"}) == "PRODUCTION"
    assert classify_job({"job_id": "SMOKE-1", "job_class": "PRODUCTION"}) == "PRODUCTION"


def test_doctor_bao_dung_trang_thai(env):
    e = env()
    d = e.co.doctor()
    assert [j["job_id"] for j in d["handoff_pending"]] == [e.job_id]
    assert d["test_output_folder_id"] == e.test_folder
    assert d["production_upload_enabled"] is False


def test_job_conflict_voi_job_khac_dang_do(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path)
    e.state.create({"job_id": "H2DEV-N-ep17-GPT6-v01", "project": "H2DEV", "lane": "N",
                    "episode_id": "ep17", "row_key": "sheet:row9", "status": "WAITING_GPT"})
    r = e.co.tick()[0]
    assert r["status"] == "BLOCKED" and r["code"] == "E_HANDOFF_JOB_CONFLICT", r
    assert r["uploads"] == 0


# ============================================================ adapter H2DEV (sandbox)
def test_adapter_dung_goi_ban_giao_trong_sandbox(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path)
    e.co.tick()
    delivered = e.drive.find_in_folder(e.test_folder, "LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip")
    local = Path(cfg["_root"]) / "download.zip"
    e.drive.download(delivered[0]["id"], local)

    cfg["delivery"] = {"sandbox_dir": str(Path(cfg["_root"]) / "jobs" / "_delivery"),
                       "h2dev_root": "E:/CLAUDE/YOUTUBE/h2dev",
                       "production_root": "E:/CLAUDE/YOUTUBE/h2dev/assets",
                       "allow_production_write": False, "script_lock_probe": True}
    d = H2DevDelivery(cfg, root=Path(cfg["_root"]))
    man = d.prepare(local, e.handoff_rec(), slug="ep17-kei-campingcar",
                    drive={"file_id": delivered[0]["id"], "name": "X", "sha256": "y",
                           "dest_kind": "TEST", "folder_id": e.test_folder})
    slug_dir = Path(man["slug_dir"])
    assert (slug_dir / SCRIPT_NAME).exists()
    assert (slug_dir / MANIFEST_NAME).exists()
    assert man["write_scope"] == "sandbox"
    assert man["script"]["sha256_gate_normalized"] == man["script"]["sha256"]
    assert man["zip"]["sha256"] == sha256(local)
    assert man["drive"]["file_id"] == delivered[0]["id"]
    # cổng khoá H2DEV: dò tương thích thật, KHÔNG để lại lock PASS giả
    probe = man.get("gate_probe") or {}
    assert probe.get("probe") is True, probe
    assert probe.get("verify_ok") is True and probe.get("verify_level") == "PASS", probe
    assert probe.get("mismatch_detected") is True, "phải phát hiện được sha lệch (FINAL_QC_STALE)"
    assert not (slug_dir / "script.lock.json").exists(), "không được để lại lock PASS giả"
    assert d.verify(slug_dir)["ok"] is True


def test_adapter_tu_choi_ghi_ngoai_sandbox(cfg, fake_drive, tmp_path):
    e = Env(cfg, fake_drive, tmp_path)
    e.co.tick()
    local = Path(cfg["_root"]) / "d.zip"
    e.drive.download(e.drive.find_in_folder(
        e.test_folder, "LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip")[0]["id"], local)
    cfg["delivery"] = {"sandbox_dir": str(Path(cfg["_root"]) / "jobs" / "_delivery"),
                       "h2dev_root": "E:/CLAUDE/YOUTUBE/h2dev",
                       "production_root": "E:/CLAUDE/YOUTUBE/h2dev/assets",
                       "allow_production_write": False, "script_lock_probe": False}
    d = H2DevDelivery(cfg, root=Path(cfg["_root"]))
    with pytest.raises(BridgeError) as ei:
        d.prepare(local, e.handoff_rec(), slug="ep17",
                  dest_root="E:/CLAUDE/YOUTUBE/h2dev/assets/J-teinengo-fuufu")
    assert ei.value.code == "E_H2DEV_WRITE_DENIED"
    # cũng không được ghi ra Desktop lung tung
    with pytest.raises(BridgeError) as ei2:
        d.prepare(local, e.handoff_rec(), slug="ep17", dest_root=str(Path(cfg["_root"]) / ".." / "ngoai"))
    assert ei2.value.code == "E_H2DEV_WRITE_DENIED"


def test_adapter_khong_sua_h2dev(cfg, fake_drive, tmp_path):
    """Manifest khai rõ chưa qua Sol, và module adapter không ghi vào H2DEV khi chưa duyệt."""
    src = Path(H.__file__).with_name("h2dev_delivery.py").read_text(encoding="utf-8")
    assert "allow_production_write" in src and "E_H2DEV_WRITE_DENIED" in src
    assert "ghi_lock" in src and "kiem_lock" in src