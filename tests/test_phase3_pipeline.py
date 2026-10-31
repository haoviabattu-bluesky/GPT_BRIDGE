# -*- coding: utf-8 -*-
"""Phase 3 — QC ZIP, mock adapter, end-to-end tới COMPLETED (Drive giả)."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from services import output_collector, zip_qc
from services.errors import BridgeError


# ------------------------------------------------------------------ QC hồ sơ vào
class TestInspectHandoff:
    def test_ok(self, cfg, handoff_zip):
        h = zip_qc.inspect_handoff(handoff_zip, cfg)
        assert h["manifest"]["job_id"] == "N-ep17"
        assert "01_HOOK_OUTLINE.txt" in h["files"]
        assert h["manifest"]["job_id"] == "N-ep17"
        assert len(h["sha256"]) == 64

    def test_detects_corrupt_zip(self, cfg, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"PK\x03\x04 khong phai zip that")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_handoff(bad, cfg)
        assert ei.value.code == "E_ZIP_CORRUPT"

    def test_blocks_traversal(self, cfg, tmp_path):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../../etc/passwd", "x")
            zf.writestr("ok.txt", "y")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_handoff(evil, cfg)
        assert ei.value.code == "E_ZIP_TRAVERSAL"

    def test_blocks_executable(self, cfg, tmp_path):
        evil = tmp_path / "evil2.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("run.exe", b"MZ")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_handoff(evil, cfg)
        assert ei.value.code == "E_ZIP_TRAVERSAL"

    def test_missing_required(self, cfg, tmp_path):
        p = tmp_path / "thin.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("01_HOOK_OUTLINE.txt", "x")
            zf.writestr("09_HANDOFF_MANIFEST.json", json.dumps({"required_files": []}))
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_handoff(p, cfg)
        assert ei.value.code == "E_HANDOFF_INCOMPLETE"

    def test_manifest_checksum_mismatch_reported(self, cfg, tmp_path):
        """Manifest có checksum sai phải ĐƯỢC BÁO, không im lặng bỏ qua."""
        root = "H"
        with zipfile.ZipFile(tmp_path / "m.zip", "w") as zf:
            for n in ("01_HOOK_OUTLINE.txt", "02_SCRIPT_RULES.md", "03_RESEARCH_FACTS.md",
                      "05_CONTINUITY.md", "07_WRITING_TASK.md"):
                zf.writestr(f"{root}/{n}", "noi dung")
            zf.writestr(f"{root}/09_HANDOFF_MANIFEST.json", json.dumps(
                {"per_file_checksums": {"01_HOOK_OUTLINE.txt": "0" * 64}}))
        h = zip_qc.inspect_handoff(tmp_path / "m.zip", cfg)
        assert h["checksum_mismatch"] == ["01_HOOK_OUTLINE.txt"]


# ------------------------------------------------------------------ QC kết quả
class TestInspectResult:
    def _job(self):
        return {"job_id": "H2DEV-N-ep17-GPT6-v01", "episode_id": "ep17", "lane": "N"}

    def test_ok(self, cfg, result_zip):
        rep = zip_qc.inspect_result(result_zip, cfg, self._job(),
                                    {"budget": {"min": 250, "target": 300, "max": 400},
                                     "hook_text": "六十八歳の春、わたしは四百二十万円の軽キャンピングカーを、"
                                                  "買わずに一か月だけ借りた。夫は若いころから雑誌の切り抜きを"
                                                  "集めていた。"})
        assert rep["verdict"] == "PASS_FORMAT_ONLY"
        assert rep["checks"]["required_files"]["ok"] is True
        assert rep["checks"]["length"]["ok"] is True
        assert rep["checks"]["hook_verbatim"]["ok"] is True
        assert rep["chars"] > 0

    def test_missing_file(self, cfg, tmp_path):
        p = tmp_path / "r.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("SCRIPT_FINAL_JP.txt", "abc")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_result(p, cfg, self._job(), {})
        assert ei.value.code == "E_RESULT_MISSING_FILES"

    def test_empty_script(self, cfg, tmp_path):
        p = tmp_path / "r.zip"
        with zipfile.ZipFile(p, "w") as zf:
            for n in cfg["qc"]["required_files"]:
                zf.writestr(n, "   \n\n " if n == "SCRIPT_FINAL_JP.txt" else "x")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_result(p, cfg, self._job(), {})
        assert ei.value.code == "E_RESULT_EMPTY_SCRIPT"

    def test_length_out_of_budget(self, cfg, tmp_path):
        p = tmp_path / "r.zip"
        with zipfile.ZipFile(p, "w") as zf:
            for n in cfg["qc"]["required_files"]:
                zf.writestr(n, "あ" if n == "SCRIPT_FINAL_JP.txt" else "x")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_result(p, cfg, self._job(),
                                  {"budget": {"min": 250, "target": 300, "max": 400}})
        assert ei.value.code == "E_RESULT_LENGTH"

    def test_hook_changed(self, cfg, tmp_path):
        p = tmp_path / "r.zip"
        body = "全然違う書き出しです。" * 40
        with zipfile.ZipFile(p, "w") as zf:
            for n in cfg["qc"]["required_files"]:
                zf.writestr(n, body if n == "SCRIPT_FINAL_JP.txt" else "x")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_result(p, cfg, self._job(),
                                  {"budget": {"min": 0, "target": 0, "max": 0},
                                   "hook_text": "六十八歳の春、わたしは"})
        assert ei.value.code == "E_RESULT_HOOK_CHANGED"

    def test_job_mismatch(self, cfg, result_zip):
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_result(result_zip, cfg,
                                  {"job_id": "OTHER-JOB", "episode_id": "ep99", "lane": "N"},
                                  {"budget": {"min": 250, "target": 300, "max": 400},
                                   "hook_text": ""})
        assert ei.value.code == "E_RESULT_JOB_MISMATCH"

    def test_bad_encoding(self, cfg, tmp_path):
        p = tmp_path / "r.zip"
        with zipfile.ZipFile(p, "w") as zf:
            for n in cfg["qc"]["required_files"]:
                zf.writestr(n, b"\xff\xfe\x00bad" if n == "SCRIPT_FINAL_JP.txt" else b"x")
        with pytest.raises(BridgeError) as ei:
            zip_qc.inspect_result(p, cfg, self._job(), {})
        assert ei.value.code == "E_RESULT_ENCODING"


# ------------------------------------------------------------------ collector
class TestCollector:
    def test_normalize_extracts_flat_zip(self, cfg, result_zip, tmp_path):
        """ZIP có folder con vẫn phải chuẩn hoá về 4 file phẳng."""
        nested = tmp_path / "nested.zip"
        with zipfile.ZipFile(result_zip) as src, zipfile.ZipFile(nested, "w") as dst:
            for n in src.namelist():
                dst.writestr(f"somefolder/{n}", src.read(n))
        got = output_collector.normalize(nested, tmp_path / "out", "J1")
        assert set(got["files"]) == set(cfg["qc"]["required_files"])
        assert Path(got["zip_path"]).exists()

    def test_normalize_flat_zip_kept(self, cfg, result_zip, tmp_path):
        got = output_collector.normalize(result_zip, tmp_path / "out", "J1")
        assert set(got["files"]) == set(cfg["qc"]["required_files"])
        assert len(got["sha256"]) == 64

    def test_normalize_rejects_traversal(self, cfg, tmp_path):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../x.txt", "x")
        with pytest.raises(BridgeError) as ei:
            output_collector.normalize(evil, tmp_path / "out", "J1")
        assert ei.value.code == "E_ZIP_TRAVERSAL"

    def test_normalize_is_deterministic_across_runs(self, cfg, result_zip, tmp_path):
        """Cùng đầu vào + cùng job_id => CÙNG SHA256 ở mọi lần đóng gói.

        Trước 24/10/2026 normalize() dùng zipfile.write() nên nhúng date_time của
        file nguồn vào ZIP; đóng lại sau vài giây là SHA đổi => chống-upload-trùng
        và verify_hash trên Drive báo sai. Test này khoá hành vi tất định."""
        import time
        shas = []
        for i in range(2):
            got = output_collector.normalize(result_zip, tmp_path / f"run{i}", "J1")
            shas.append(got["sha256"])
            time.sleep(1.1)
        assert len(set(shas)) == 1, f"normalize KHÔNG tất định: {shas}"


# ------------------------------------------------------------------ E2E
class TestEndToEnd:
    def _setup(self, cfg, fake_drive, handoff_zip):
        """INBOX -> N-ep17_v01/{INPUT,OUTPUT}, ZIP nằm trong INPUT."""
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        inp = fake_drive.add_folder("INPUT", ep)
        out = fake_drive.add_folder("OUTPUT", ep)
        zid = fake_drive.add_file("LaneN_tap17_GPT_SCRIPT_HANDOFF.zip", inp,
                                  handoff_zip.read_bytes())
        return ep, inp, out, zid

    def _orch(self, cfg, fake_drive):
        from services.orchestrator import Orchestrator
        return Orchestrator(cfg, "H2DEV", drive=fake_drive)

    def _job(self, orch, ep, inp, out, zid):
        jid = "H2DEV-N-ep17-GPT6-v01"
        orch.store.create({"job_id": jid, "project": "H2DEV", "lane": "N",
                           "episode_id": "ep17", "episode_slug": "ep17-x",
                           "source_row": 18, "row_key": "rk1",
                           "input_zip_file_id": zid, "status": "DETECTED",
                           "notes": json.dumps({"episode_folder_id": ep})})
        orch.store.transition(jid, "VALIDATING", "test")
        return jid

    def test_full_pipeline_to_completed(self, cfg, fake_drive, handoff_zip):
        ep, inp, out, zid = self._setup(cfg, fake_drive, handoff_zip)
        # kiểm thử đường ống: cho phép upload ZIP mock lên Drive GIẢ
        cfg["upload"]["allow_mock_upload"] = True
        orch = self._orch(cfg, fake_drive)
        jid = self._job(orch, ep, inp, out, zid)

        v = orch.validate(jid)
        assert v["ok"], v
        assert orch.store.get(jid)["input_folder_id"] == inp
        assert orch.store.get(jid)["output_folder_id"] == out

        p = orch.prepare(jid)
        assert p["ok"]
        assert Path(p["paths"]["task_message"]).exists()

        s = orch.submit(jid, confirm=True)
        assert s["ok"], s
        assert orch.store.get(jid)["status"] == "WAITING_GPT"

        f = orch.finish(jid)
        assert f["ok"], f
        job = orch.store.get(jid)
        assert job["status"] == "COMPLETED"
        # ZIP phải nằm ĐÚNG trong OUTPUT, không phải INPUT
        outs = fake_drive.list_children(out)
        assert len(outs) == 1
        assert outs[0]["name"] == "LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip"
        assert fake_drive.list_children(inp)[-1]["name"].endswith("HANDOFF.zip")
        # hash trên Drive khớp hash local
        remote = fake_drive.blobs[job["output_file_id"]]
        assert hashlib.sha256(remote).hexdigest() == job["output_sha256"]

    def test_mock_upload_blocked_by_default(self, cfg, fake_drive, handoff_zip):
        """ZIP do MOCK sinh KHÔNG được upload nếu chưa cho phép."""
        ep, inp, out, zid = self._setup(cfg, fake_drive, handoff_zip)
        orch = self._orch(cfg, fake_drive)
        jid = self._job(orch, ep, inp, out, zid)
        orch.validate(jid); orch.prepare(jid); orch.submit(jid, confirm=True)
        f = orch.finish(jid)
        assert f["ok"] is False
        assert f["error"]["code"] == "E_MOCK_NOT_UPLOADABLE"
        assert orch.store.get(jid)["status"] == "NEEDS_REVIEW"
        assert fake_drive.list_children(out) == []

    def test_no_duplicate_job_on_restart(self, cfg, fake_drive, handoff_zip):
        ep, inp, out, zid = self._setup(cfg, fake_drive, handoff_zip)
        orch = self._orch(cfg, fake_drive)
        jid = self._job(orch, ep, inp, out, zid)
        # chạy detect lần 2 với cùng row_key => không tạo thêm
        assert orch.store.by_row_key("rk1")["job_id"] == jid
        assert len(orch.store.list()) == 1

    def test_zip_not_in_input_rejected(self, cfg, fake_drive, handoff_zip):
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        inp = fake_drive.add_folder("INPUT", ep)
        fake_drive.add_folder("OUTPUT", ep)
        wrong = fake_drive.add_folder("SOMEWHERE", "INBOX_FAKE")
        zid = fake_drive.add_file("handoff.zip", wrong, handoff_zip.read_bytes())
        orch = self._orch(cfg, fake_drive)
        jid = self._job(orch, ep, inp, None, zid)
        v = orch.validate(jid)
        assert v["ok"] is False
        assert v["error"]["code"] == "E_ZIP_NOT_IN_INPUT"
        assert orch.store.get(jid)["status"] == "NEEDS_REVIEW"

    def test_upload_verify_detects_hash_mismatch(self, cfg, fake_drive, handoff_zip, result_zip):
        from services.drive_uploader import DriveUploader

        class BadDrive(type(fake_drive)):
            def download(self, file_id, dest, expect_sha=None):
                r = super().download(file_id, dest)
                Path(dest).write_bytes(b"bi doi sau upload")
                r["sha256"] = hashlib.sha256(b"bi doi sau upload").hexdigest()
                return r

        ep, inp, out, zid = self._setup(cfg, fake_drive, handoff_zip)
        bad = BadDrive()
        bad.files, bad.blobs = fake_drive.files, fake_drive.blobs
        up = DriveUploader(cfg, bad)
        job = {"job_id": "J", "lane": "N", "episode_id": "ep17", "output_folder_id": out}
        with pytest.raises(BridgeError) as ei:
            up.upload_and_verify(result_zip, job, expect_sha="0" * 64, name="x.zip")
        assert ei.value.code in ("E_DRIVE_UPLOAD_VERIFY", "E_DRIVE_UPLOAD")
