# -*- coding: utf-8 -*-
"""Phase 5 — adapter Codex CLI: hợp đồng, an toàn, không fallback model, end-to-end.

Nguyên tắc: KHÔNG gọi Codex CLI thật, KHÔNG gọi mạng, KHÔNG đụng dữ liệu thật.
Mọi test dùng stub `tests/fixtures/stub_codex.py`.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from adapters.base import GPTWriterAdapter
from services.errors import BridgeError

STUB = Path(__file__).resolve().parent / "fixtures" / "stub_codex.py"


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def codex_cfg(cfg, monkeypatch, tmp_path):
    """cfg có writer.adapter=codex_cli trỏ vào stub + catalog model giả."""
    home = tmp_path / "codex_home"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(
        {"models": [{"slug": "gpt-6.1-sol"}, {"slug": "gpt-5.6-terra"}]}),
        encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("STUB_CODEX_MODE", "ok")
    monkeypatch.delenv("STUB_CODEX_RECORD", raising=False)

    cfg["writer"]["adapter"] = "codex_cli"
    cfg["writer"]["codex_cli"] = {
        "executable": [sys.executable, str(STUB)],
        "model": "gpt-6.1-sol",
        "sandbox": "read-only",
        "timeout_sec": 60,
        "allow_model_fallback": False,
    }
    return cfg


@pytest.fixture
def codex_adapter(codex_cfg):
    from adapters.codex_cli_writer import CodexCLIWriterAdapter
    return CodexCLIWriterAdapter(codex_cfg)


def _make_job(tmp_path, handoff_zip, job_id="N-ep17-PHASE5-v01"):
    wd = tmp_path / "job"
    (wd / "input").mkdir(parents=True)
    dest = wd / "input" / "handoff.zip"
    dest.write_bytes(Path(handoff_zip).read_bytes())
    return {"job_id": job_id, "episode_id": "ep17", "input_zip_name": "handoff.zip",
            "_workdir": str(wd)}


def _wait(adapter, job, timeout=60):
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = adapter.status(job)
        if s["state"] != "waiting":
            return s
        time.sleep(0.2)
    raise AssertionError("stub không kết thúc")


# ------------------------------------------------------------------ contract
class TestContract:
    def test_is_writer_adapter(self, codex_adapter):
        assert isinstance(codex_adapter, GPTWriterAdapter)
        assert codex_adapter.name == "codex_cli"
        assert codex_adapter.supports_auto is True

    def test_build_adapter_wires_codex(self, codex_cfg):
        from services.orchestrator import build_adapter
        a = build_adapter(codex_cfg, None)
        assert a.name == "codex_cli"

    def test_unknown_adapter_still_rejected(self, cfg):
        from services.orchestrator import build_adapter
        cfg["writer"]["adapter"] = "khong_ton_tai"
        with pytest.raises(BridgeError) as e:
            build_adapter(cfg, None)
        assert e.value.code == "E_CONFIG"

    def test_existing_adapters_untouched(self, cfg):
        from services.orchestrator import build_adapter
        for kind, name in (("mock", "mock"), ("manual", "manual")):
            cfg["writer"]["adapter"] = kind
            assert build_adapter(cfg, None).name == name


# ------------------------------------------------------------------ health
class TestHealth:
    def test_health_ok(self, codex_adapter):
        h = codex_adapter.health()
        assert h["ok"] is True
        assert h["logged_in"] is True
        assert "gpt-6.1-sol" in h["models_available"]

    def test_not_logged_in(self, codex_adapter, monkeypatch):
        monkeypatch.setenv("STUB_CODEX_MODE", "logged_out")
        h = codex_adapter.health()
        assert h["ok"] is False
        assert h["code"] == "E_CODEX_NOT_LOGGED_IN"

    def test_missing_executable(self, codex_cfg):
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        codex_cfg["writer"]["codex_cli"]["executable"] = ["codex_khong_ton_tai_xyz"]
        h = CodexCLIWriterAdapter(codex_cfg).health()
        assert h["ok"] is False
        assert h["code"] == "E_CODEX_NOT_INSTALLED"

    def test_model_unavailable_does_not_fallback(self, codex_cfg):
        """Model thiếu => BLOCKED, KHÔNG tự chọn model khác."""
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        codex_cfg["writer"]["codex_cli"]["model"] = "gpt-6-astra-khong-co"
        a = CodexCLIWriterAdapter(codex_cfg)
        h = a.health()
        assert h["ok"] is False
        assert h["code"] == "E_CODEX_MODEL_UNAVAILABLE"
        assert h["model"] == "gpt-6-astra-khong-co"      # giữ nguyên, không đổi

    def test_submit_blocked_when_model_unavailable(self, codex_cfg, handoff_zip, tmp_path):
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        codex_cfg["writer"]["codex_cli"]["model"] = "gpt-6-astra-khong-co"
        a = CodexCLIWriterAdapter(codex_cfg)
        r = a.submit(_make_job(tmp_path, handoff_zip), "prompt")
        assert r["accepted"] is False
        assert "E_CODEX_MODEL_UNAVAILABLE" in r["note"]


# ------------------------------------------------- token expiry (lỗi đã gặp thật)
class TestTokenExpiryGuard:
    """`codex login status` từng báo SAI 'Logged in' khi token đã hết hạn (API trả 401)."""

    @staticmethod
    def _write_auth(home: Path, exp_ts: int | None):
        import base64 as b64
        import json as js
        home.mkdir(parents=True, exist_ok=True)
        if exp_ts is None:
            obj = {"tokens": {"access_token": "not-a-jwt"}}
        else:
            hdr = b64.urlsafe_b64encode(js.dumps({"alg": "none"}).encode()).decode().rstrip("=")
            pay = b64.urlsafe_b64encode(js.dumps({"exp": exp_ts}).encode()).decode().rstrip("=")
            obj = {"tokens": {"access_token": f"{hdr}.{pay}.sig"}}
        (home / "auth.json").write_text(js.dumps(obj), encoding="utf-8")

    def _adapter(self, cfg, monkeypatch, home):
        monkeypatch.setenv("CODEX_HOME", str(home))
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        a = CodexCLIWriterAdapter(cfg)
        monkeypatch.setattr(a, "_cmd_prefix", lambda: [sys.executable, str(STUB)])
        return a

    def test_expired_token_blocks_even_if_status_says_logged_in(self, codex_cfg, monkeypatch, tmp_path):
        import time as _t
        home = tmp_path / "codexhome"
        self._write_auth(home, int(_t.time()) - 3600)
        a = self._adapter(codex_cfg, monkeypatch, home)
        monkeypatch.setenv("STUB_CODEX_LOGIN", "ok")
        h = a.health()
        assert h["ok"] is False
        assert h["code"] == "E_CODEX_TOKEN_EXPIRED"

    def test_valid_token_passes(self, codex_cfg, monkeypatch, tmp_path):
        import time as _t
        home = tmp_path / "codexhome"
        self._write_auth(home, int(_t.time()) + 3600)
        a = self._adapter(codex_cfg, monkeypatch, home)
        monkeypatch.setenv("STUB_CODEX_LOGIN", "ok")
        monkeypatch.setenv("STUB_CODEX_MODELS", "gpt-6.1-sol,gpt-5.5")
        h = a.health()
        assert h["token"]["expired"] is False
        assert h["ok"] is True, h

    def test_missing_auth_json_trusts_login_status(self, codex_cfg, monkeypatch, tmp_path):
        """Thiếu auth.json KHÔNG đủ để kết luận đã đăng xuất -> tin `login status`."""
        home = tmp_path / "codexhome"
        home.mkdir(parents=True, exist_ok=True)
        a = self._adapter(codex_cfg, monkeypatch, home)
        monkeypatch.setenv("STUB_CODEX_LOGIN", "ok")
        h = a.health()
        assert h["token"]["present"] is False
        assert h["ok"] is True, h

    def test_token_state_never_leaks_token(self, codex_cfg, monkeypatch, tmp_path):
        import time as _t
        home = tmp_path / "codexhome"
        self._write_auth(home, int(_t.time()) + 60)
        a = self._adapter(codex_cfg, monkeypatch, home)
        blob = json.dumps(a.token_state())
        assert "eyJ" not in blob          # không có chuỗi JWT nào bị lộ
        assert set(a.token_state()) >= {"present", "expired", "expires_at"}


# ------------------------------------------------------------------ safety
class TestSafety:
    def test_sandbox_default_read_only(self, cfg):
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        cfg["writer"]["codex_cli"] = {}
        assert CodexCLIWriterAdapter(cfg).sandbox == "read-only"

    def test_stage_only_whitelisted_text(self, codex_adapter, tmp_path):
        """File lạ (không nguy hiểm) trong ZIP KHÔNG được đưa vào vùng làm việc."""
        wd = tmp_path / "job"
        (wd / "input").mkdir(parents=True)
        zp = wd / "input" / "handoff.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("01_HOOK_OUTLINE.txt", "hook\n")
            zf.writestr("07_WRITING_TASK.md", "task\n")
            zf.writestr("khong_ten_trong_whitelist.txt", "x\n")
            zf.writestr("ghi_chu_la.md", "y\n")
        stage = codex_adapter.stage_handoff(
            {"job_id": "J", "input_zip_name": "handoff.zip", "_workdir": str(wd)}, wd)
        got = sorted(p.name for p in stage.iterdir())
        assert got == ["01_HOOK_OUTLINE.txt", "07_WRITING_TASK.md"]

    def test_dangerous_file_in_zip_fails_closed(self, codex_adapter, tmp_path):
        """ZIP có .bat/.exe/.ps1 => DỪNG NGAY (lớp chặn sẵn có), không giải nén gì."""
        wd = tmp_path / "job"
        (wd / "input").mkdir(parents=True)
        with zipfile.ZipFile(wd / "input" / "handoff.zip", "w") as zf:
            zf.writestr("01_HOOK_OUTLINE.txt", "hook\n")
            zf.writestr("evil.bat", "@echo off\nformat c:\n")
        with pytest.raises(BridgeError) as e:
            codex_adapter.stage_handoff(
                {"job_id": "J", "input_zip_name": "handoff.zip", "_workdir": str(wd)}, wd)
        assert e.value.code == "E_ZIP_TRAVERSAL"
        assert not (wd / "codex_handoff").exists() or not any(
            p.name.endswith(".bat") for p in (wd / "codex_handoff").iterdir())

    def test_zip_traversal_rejected(self, codex_adapter, tmp_path):
        wd = tmp_path / "job"
        (wd / "input").mkdir(parents=True)
        with zipfile.ZipFile(wd / "input" / "handoff.zip", "w") as zf:
            zf.writestr("../../escape.txt", "x")
        with pytest.raises(BridgeError) as e:
            codex_adapter.stage_handoff(
                {"job_id": "J", "input_zip_name": "handoff.zip", "_workdir": str(wd)}, wd)
        assert e.value.code in ("E_ZIP_TRAVERSAL", "E_HANDOFF_INCOMPLETE")

    def test_missing_handoff_raises(self, codex_adapter, tmp_path):
        wd = tmp_path / "job"
        (wd / "input").mkdir(parents=True)
        with pytest.raises(BridgeError) as e:
            codex_adapter.stage_handoff(
                {"job_id": "J", "input_zip_name": "khong_co.zip", "_workdir": str(wd)}, wd)
        assert e.value.code == "E_HANDOFF_INCOMPLETE"

    def test_no_dangerous_flag_in_cmd(self, codex_adapter, handoff_zip, tmp_path,
                                     monkeypatch):
        rec = tmp_path / "rec.jsonl"
        monkeypatch.setenv("STUB_CODEX_RECORD", str(rec))
        job = _make_job(tmp_path, handoff_zip)
        codex_adapter.submit(job, "prompt")
        _wait(codex_adapter, job)
        cmds = [json.loads(l) for l in rec.read_text(encoding="utf-8").splitlines() if l]
        argv = next(c["argv"] for c in cmds if "exec" in c["argv"])
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
        assert "--approve-for-me" not in argv
        assert argv[argv.index("-s") + 1] == "read-only"


# ------------------------------------------------------------------ e2e
class TestEndToEnd:
    def test_submit_status_collect(self, codex_adapter, handoff_zip, tmp_path, monkeypatch):
        rec = tmp_path / "rec.jsonl"
        monkeypatch.setenv("STUB_CODEX_RECORD", str(rec))
        job = _make_job(tmp_path, handoff_zip)

        sub = codex_adapter.submit(job, "Hãy viết kịch bản.")
        assert sub["accepted"] is True
        assert sub["manual_required"] is False

        st = _wait(codex_adapter, job)
        assert st["state"] == "result_available"

        got = codex_adapter.collect_result(job)
        assert got["zip_path"] and Path(got["zip_path"]).exists()
        assert got["source"] == "auto"
        with zipfile.ZipFile(got["zip_path"]) as zf:
            names = set(zf.namelist())
        for n in ("SCRIPT_FINAL_JP.txt", "SCRIPT_QC_REPORT.md",
                  "SCRIPT_ISSUES.md", "SCRIPT_STRUCTURE.md"):
            assert n in names

    def test_prompt_redirects_away_from_drive_links(self, codex_adapter, handoff_zip,
                                                    tmp_path, monkeypatch):
        rec = tmp_path / "rec.jsonl"
        monkeypatch.setenv("STUB_CODEX_RECORD", str(rec))
        job = _make_job(tmp_path, handoff_zip)
        codex_adapter.submit(job, "Hồ sơ: https://drive.google.com/drive/folders/XYZ")
        _wait(codex_adapter, job)
        recs = [json.loads(l) for l in rec.read_text(encoding="utf-8").splitlines() if l]
        p = next(r for r in recs if "prompt_chars" in r)
        assert p["has_local_hint"] is True
        assert p["prompt_chars"] > 100

    def test_missing_output_file_reports_failed(self, codex_adapter, handoff_zip, tmp_path,
                                                monkeypatch):
        monkeypatch.setenv("STUB_CODEX_MODE", "missing_one")
        job = _make_job(tmp_path, handoff_zip)
        codex_adapter.submit(job, "prompt")
        st = _wait(codex_adapter, job)
        assert st["state"] == "failed"
        assert "SCRIPT_STRUCTURE.md" in st["detail"]

    def test_cli_failure_reports_failed(self, codex_adapter, handoff_zip, tmp_path,
                                        monkeypatch):
        monkeypatch.setenv("STUB_CODEX_MODE", "fail")
        job = _make_job(tmp_path, handoff_zip)
        codex_adapter.submit(job, "prompt")
        st = _wait(codex_adapter, job)
        assert st["state"] == "failed"

    def test_collect_before_done_raises_no_zip(self, codex_adapter, tmp_path):
        job = {"job_id": "J", "input_zip_name": "x.zip", "_workdir": str(tmp_path)}
        got = codex_adapter.collect_result(job)
        assert got["zip_path"] is None
        assert "thiếu file kết quả" in got["note"]

    def test_collector_normalizes_codex_zip(self, codex_adapter, handoff_zip, tmp_path):
        """ZIP của codex_cli phải đi qua output_collector như mọi adapter khác."""
        from services import output_collector
        job = _make_job(tmp_path, handoff_zip)
        codex_adapter.submit(job, "prompt")
        _wait(codex_adapter, job)
        norm = output_collector.collect(codex_adapter, job, Path(job["_workdir"]))
        assert norm["source"] == "auto"
        assert norm["sha256"]
        assert "SCRIPT_FINAL_JP.txt" in norm["files"]


# ------------------------------------------------------------------ parsing
class TestParsing:
    def test_parse_three_header_styles(self, tmp_path):
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        req = ["SCRIPT_FINAL_JP.txt", "SCRIPT_ISSUES.md"]
        p = tmp_path / "last.txt"
        p.write_text(
            "### SCRIPT_FINAL_JP.txt\n```\n本文です。\n```\n\n"
            "```markdown SCRIPT_ISSUES.md\n- ok\n```\n", encoding="utf-8")
        found, missing = CodexCLIWriterAdapter.parse_output(p, req)
        assert missing == []
        assert "本文" in found["SCRIPT_FINAL_JP.txt"]
        assert "ok" in found["SCRIPT_ISSUES.md"]

    def test_parse_equals_header_style(self, tmp_path):
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        p = tmp_path / "last.txt"
        p.write_text("=== FILE: SCRIPT_FINAL_JP.txt ===\n本文\n=== FILE: SCRIPT_ISSUES.md ===\n- x\n",
                     encoding="utf-8")
        found, missing = CodexCLIWriterAdapter.parse_output(
            p, ["SCRIPT_FINAL_JP.txt", "SCRIPT_ISSUES.md"])
        assert missing == []
        assert found["SCRIPT_FINAL_JP.txt"].strip() == "本文"

    def test_parse_empty_last_message(self, tmp_path):
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        found, missing = CodexCLIWriterAdapter.parse_output(
            tmp_path / "nope.txt", ["A.txt"])
        assert found == {} and missing == ["A.txt"]
