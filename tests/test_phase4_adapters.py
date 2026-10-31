# -*- coding: utf-8 -*-
"""Phase 4 — adapter thật, chống gửi trùng, live Drive/Sheets (opt-in)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from adapters.base import GPTWriterAdapter
from services.errors import BridgeError


class TestAdapterContract:
    def test_base_raises(self):
        a = GPTWriterAdapter()
        with pytest.raises(NotImplementedError):
            a.submit({}, "")
        with pytest.raises(NotImplementedError):
            a.status({})
        with pytest.raises(NotImplementedError):
            a.collect_result({})

    def test_all_adapters_share_interface(self, cfg):
        from adapters.manual_chatgpt import ManualChatGPTAdapter
        from adapters.mock_chatgpt import MockChatGPTAdapter
        from adapters.official_api import OfficialAPIAdapter
        for cls in (MockChatGPTAdapter, ManualChatGPTAdapter, OfficialAPIAdapter):
            a = cls(cfg)
            assert isinstance(a, GPTWriterAdapter)
            assert isinstance(a.name, str) and a.name
            assert isinstance(a.supports_auto, bool)


class TestMockAdapter:
    def test_submit_accepts(self, cfg, tmp_path):
        from adapters.mock_chatgpt import MockChatGPTAdapter
        job = {"job_id": "J1", "episode_id": "ep17", "_workdir": str(tmp_path)}
        r = MockChatGPTAdapter(cfg).submit(job, "prompt")
        assert r["accepted"] is True

    def test_collect_produces_4_files(self, cfg, tmp_path, handoff_zip):
        from adapters.mock_chatgpt import MockChatGPTAdapter
        from services import zip_qc
        from services.prompt_builder import parse_budget, parse_hook
        h = zip_qc.inspect_handoff(handoff_zip, cfg)
        job = {"job_id": "J1", "episode_id": "ep17", "_workdir": str(tmp_path),
               "_handoff": {"budget": parse_budget(h["texts"]["07_WRITING_TASK.md"],
                                                  h["texts"]["08_QC_CHECKLIST.md"]),
                            "hook_text": parse_hook(h["texts"]["01_HOOK_OUTLINE.txt"])["text"]}}
        r = MockChatGPTAdapter(cfg).collect_result(job)
        import zipfile
        with zipfile.ZipFile(r["zip_path"]) as zf:
            names = set(zf.namelist())
        assert set(cfg["qc"]["required_files"]).issubset(names)
        assert r["source"] == "mock"


class TestManualAdapter:
    def test_submit_writes_handoff_package(self, cfg, tmp_path):
        from adapters.manual_chatgpt import ManualChatGPTAdapter
        a = ManualChatGPTAdapter(cfg)
        job = {"job_id": "J1", "episode_id": "ep17", "_workdir": str(tmp_path)}
        r = a.submit(job, "noi dung prompt")
        assert r["accepted"] is True
        assert r["manual_required"] is True
        assert r["supports_auto"] is False
        # gói giao việc phải nằm trong workdir để người dùng mở
        assert (tmp_path / "GPT6_TASK_MESSAGE.txt").exists()
        assert (tmp_path / "COPY_PROMPT.txt").exists()
        assert "noi dung prompt" in (tmp_path / "COPY_PROMPT.txt").read_text(encoding="utf-8")

    def test_status_waiting_when_no_result(self, cfg, tmp_path):
        from adapters.manual_chatgpt import ManualChatGPTAdapter
        job = {"job_id": "J1", "episode_id": "ep17", "_workdir": str(tmp_path)}
        s = ManualChatGPTAdapter(cfg).status(job)
        assert s["state"] == "waiting"

    def test_status_result_available_when_zip_dropped(self, cfg, tmp_path, result_zip):
        from adapters.manual_chatgpt import ManualChatGPTAdapter
        inbox = Path(cfg["writer"]["manual"]["inbox_dir"])
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "J1_result.zip").write_bytes(result_zip.read_bytes())
        job = {"job_id": "J1", "episode_id": "ep17", "_workdir": str(tmp_path)}
        s = ManualChatGPTAdapter(cfg).status(job)
        assert s["state"] == "result_available"

    def test_collect_from_inbox(self, cfg, tmp_path, result_zip):
        from adapters.manual_chatgpt import ManualChatGPTAdapter
        inbox = Path(cfg["writer"]["manual"]["inbox_dir"])
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "J1_result.zip").write_bytes(result_zip.read_bytes())
        job = {"job_id": "J1", "episode_id": "ep17", "_workdir": str(tmp_path)}
        r = ManualChatGPTAdapter(cfg).collect_result(job)
        assert r["zip_path"] and Path(r["zip_path"]).exists()
        assert r["source"] == "manual"


class TestOfficialAPIDisabled:
    def test_submit_blocked_when_disabled(self, cfg):
        from adapters.official_api import OfficialAPIAdapter
        a = OfficialAPIAdapter(cfg)
        with pytest.raises(BridgeError) as ei:
            a.submit({"job_id": "J1"}, "p")
        assert ei.value.code == "E_ADAPTER_NOT_READY"

    def test_health_reports_disabled(self, cfg):
        from adapters.official_api import OfficialAPIAdapter
        h = OfficialAPIAdapter(cfg).health()
        assert h["ok"] is False


class TestChatGPTWebGuards:
    def test_no_launch_by_default(self, cfg):
        """Adapter web KHÔNG được tự mở Chrome khi allow_launch=false."""
        from adapters.chatgpt_web import ChatGPTWebAdapter
        a = ChatGPTWebAdapter(cfg)
        assert a.allow_launch is False

    def test_health_fails_without_cdp(self, cfg):
        from adapters.chatgpt_web import ChatGPTWebAdapter
        h = ChatGPTWebAdapter(cfg).health()
        assert h["ok"] is False
        assert "cdp" in str(h).lower() or "chrome" in str(h).lower()

    def test_submit_blocked_without_cdp(self, cfg):
        from adapters.chatgpt_web import ChatGPTWebAdapter
        with pytest.raises(BridgeError) as ei:
            ChatGPTWebAdapter(cfg).submit({"job_id": "J1", "_workdir": cfg["_root"]}, "p")
        assert ei.value.code == "E_ADAPTER_BLOCKED"

    def test_never_bypasses_captcha(self, cfg):
        """Không có code giải CAPTCHA / đăng nhập hộ trong adapter."""
        src = Path("adapters/chatgpt_web.py").read_text(encoding="utf-8").lower()
        for bad in ("solve_captcha", "2captcha", "anticaptcha", "bypass", "stealth"):
            assert bad not in src, f"adapter chứa hành vi bị cấm: {bad}"


class TestNoDuplicateSubmit:
    def test_second_submit_refused(self, cfg, fake_drive, handoff_zip):
        """Đã WAITING_GPT thì không được submit lần 2 (chống gửi trùng)."""
        from services.orchestrator import Orchestrator
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        inp = fake_drive.add_folder("INPUT", ep)
        out = fake_drive.add_folder("OUTPUT", ep)
        zid = fake_drive.add_file("h.zip", inp, handoff_zip.read_bytes())
        orch = Orchestrator(cfg, "H2DEV", drive=fake_drive)
        jid = "J1"
        orch.store.create({"job_id": jid, "project": "H2DEV", "lane": "N",
                           "episode_id": "ep17", "source_row": 18, "row_key": "rk1",
                           "input_zip_file_id": zid, "status": "DETECTED",
                           "notes": json.dumps({"episode_folder_id": ep})})
        orch.store.transition(jid, "VALIDATING", "t")
        orch.validate(jid); orch.prepare(jid)
        assert orch.submit(jid, confirm=True)["ok"]
        second = orch.submit(jid, confirm=True)
        assert second["ok"] is False
        assert "không cho submit" in second["error"]

    def test_attempt_counter_increments(self, cfg, fake_drive, handoff_zip):
        from services.orchestrator import Orchestrator
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        inp = fake_drive.add_folder("INPUT", ep)
        fake_drive.add_folder("OUTPUT", ep)
        zid = fake_drive.add_file("h.zip", inp, handoff_zip.read_bytes())
        orch = Orchestrator(cfg, "H2DEV", drive=fake_drive)
        orch.store.create({"job_id": "J1", "project": "H2DEV", "lane": "N",
                           "episode_id": "ep17", "source_row": 18, "row_key": "rk1",
                           "input_zip_file_id": zid, "status": "DETECTED",
                           "notes": json.dumps({"episode_folder_id": ep})})
        orch.store.transition("J1", "VALIDATING", "t")
        orch.validate("J1"); orch.prepare("J1")
        orch.submit("J1", confirm=True)
        assert orch.store.get("J1")["attempt"] == 1


# ------------------------------------------------------------------ LIVE
@pytest.mark.live
@pytest.mark.skipif(os.environ.get("BRIDGE_LIVE") != "1",
                    reason="cần BRIDGE_LIVE=1 + credential thật")
class TestLiveDrive:
    def test_real_sheet_read(self):
        from services.common import load_config, project_cfg
        from services.sheet_scanner import SheetScanner
        cfg = load_config()
        pc = project_cfg(cfg, "H2DEV")
        sc = SheetScanner(cfg, pc)
        meta = sc.sheet_meta()
        assert meta["title"]
        rows = sc.scan()
        assert isinstance(rows, list)

    def test_real_episode_pair(self):
        from services.common import load_config
        from services.drive_client import DriveClient
        cfg = load_config()
        d = DriveClient(cfg)
        kids = d.list_children(cfg["drive"]["inbox_folder_id"], folders_only=True)
        assert kids, "01_INBOX phải có folder tập"
        pair = None
        for k in kids:
            try:
                pair = d.resolve_pair(k["id"])
                break
            except BridgeError:
                continue
        assert pair is not None
        assert pair["input_folder_id"] and pair["output_folder_id"]

    def test_spec_ids_still_exist(self):
        """Các ID trong đặc tả phải còn tồn tại thật (không đoán)."""
        from services.common import load_config
        from services.drive_client import DriveClient
        cfg = load_config()
        d = DriveClient(cfg)
        for fid, want in (("144FC78DACCFB031B7737DED39A2E501B", "folder"),
                          ("1B2122DDF7C24F5360491708E66E2875B", "folder"),
                          ("1C4D28055609A18040C4FC16F0A3F2EBF", "folder"),
                          ("1AB2E02356FC42D7D6B365B2ADD34BC70", "file")):
            m = d.get(fid, "id,name,mimeType,parents")
            assert m["id"] == fid
            is_folder = m["mimeType"].endswith("folder")
            assert is_folder == (want == "folder"), f"{fid} không phải {want}"
