# -*- coding: utf-8 -*-
"""adapters/codex_cli_writer.py — writer thật dùng Codex CLI chính thức của OpenAI.

KHÁC BIỆT CƠ BẢN so với chatgpt_web: đây là CLI CHÍNH THỨC, đăng nhập bằng
ChatGPT OAuth (hạn mức ChatGPT), KHÔNG điều khiển giao diện web, KHÔNG chiếm
chuột/bàn phím, KHÔNG cần Chrome.

NGUYÊN TẮC AN TOÀN
------------------
1. Sandbox `read-only` (mặc định): model ĐỌC được hồ sơ nhưng KHÔNG ghi/xoá được
   file và KHÔNG chạy được lệnh thay đổi hệ thống. File kết quả do CHÍNH adapter
   này ghi ra từ câu trả lời cuối => ZIP trong hồ sơ không thể thực thi mã.
2. Chỉ giải nén các file TEXT nằm trong danh sách trắng (hồ sơ H2DEV). Không
   giải nén .exe/.bat/.ps1/.py... => không có gì để thực thi.
3. KHÔNG tự đổi model. Model không có trong catalog => BLOCKED, không fallback.
4. Không bypass approval, không dùng `--dangerously-bypass-approvals-and-sandbox`.

HỢP ĐỒNG: kế thừa GPTWriterAdapter — submit/status/collect_result giống hệt các
adapter khác nên orchestrator, output_collector, zip_qc, drive_uploader không đổi.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from .base import GPTWriterAdapter
from services.errors import BridgeError

# Tên file hồ sơ được phép đưa vào vùng làm việc của Codex (chỉ đọc).
HANDOFF_WHITELIST = {
    "10_GPT6_START_HERE.md", "07_WRITING_TASK.md", "01_HOOK_OUTLINE.txt",
    "02_SCRIPT_RULES.md", "05_CONTINUITY.md", "03_RESEARCH_FACTS.md",
    "04_CHANNEL_PROFILE.md", "06_REFERENCE_SCRIPT.txt", "08_QC_CHECKLIST.md",
    "HANDOFF_ISSUES.md", "09_HANDOFF_MANIFEST.json",
}
TEXT_SUFFIX = {".md", ".txt", ".json", ""}

OUTPUT_CONTRACT = """
=== ĐIỀU KIỆN LÀM VIỆC Ở MÁY CỤC BỘ (đọc kỹ, khác bản giao qua chat) ===
- Toàn bộ hồ sơ đã có sẵn NGAY TRONG thư mục làm việc hiện tại của bạn.
- BỎ QUA mọi link Google Drive ở trên. KHÔNG mở link, KHÔNG cần mạng.
- Đọc trực tiếp các file bằng đường dẫn tương đối, ví dụ: {files}
- Nếu một file bắt buộc không đọc được: DỪNG và ghi rõ tên file vào SCRIPT_ISSUES.md.
- KHÔNG sửa, KHÔNG xoá, KHÔNG tạo file trong thư mục hồ sơ.

=== ĐỊNH DẠNG BẮT BUỘC CỦA CÂU TRẢ LỜI CUỐI ===
Trả về DUY NHẤT các khối dưới đây, đúng thứ tự, mỗi file một khối.
Dòng mở khối ghi CHÍNH XÁC tên file, ngay sau đó là khối mã ba dấu huyền.
Không thêm lời dẫn, không thêm kết luận ngoài các khối.

{blocks}
"""


class CodexCLIWriterAdapter(GPTWriterAdapter):
    """Writer thật qua `codex exec` (ChatGPT OAuth)."""

    name = "codex_cli"
    supports_auto = True

    def __init__(self, cfg: dict, logger=None):
        self.cfg = cfg
        self.log = logger
        w = (cfg.get("writer") or {}).get("codex_cli") or {}
        self.cfg_w = w
        self.executable = w.get("executable") or "codex"
        self.model = str(w.get("model") or "").strip()
        self.sandbox = w.get("sandbox") or "read-only"
        self.timeout = int(w.get("timeout_sec", 2700))
        self.allow_model_fallback = bool(w.get("allow_model_fallback", False))
        self.extra_args = list(w.get("extra_args") or [])
        self.json_events = bool(w.get("json_events", False))
        self.required = list((cfg.get("qc") or {}).get("required_files") or [])
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    # ================================================================ helpers
    def _cmd_prefix(self) -> list[str]:
        exe = self.executable
        if isinstance(exe, (list, tuple)):
            return [str(x) for x in exe]
        return shlex.split(str(exe), posix=(os.name != "nt")) or ["codex"]

    def _state_path(self, wd: Path) -> Path:
        return wd / "codex_run.json"

    def _out_dir(self, wd: Path) -> Path:
        p = wd / "codex_out"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _read_state(self, wd: Path) -> dict:
        p = self._state_path(wd)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _write_state(self, wd: Path, **kw) -> dict:
        st = {**self._read_state(wd), **kw}
        self._state_path(wd).write_text(json.dumps(st, ensure_ascii=False, indent=2,
                                                   default=str), encoding="utf-8")
        return st

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if not pid:
            return False
        if os.name == "nt":
            try:
                r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                   capture_output=True, text=True, timeout=15)
                return str(pid) in (r.stdout or "")
            except Exception:  # noqa: BLE001
                return False
        try:
            os.kill(pid, 0)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ================================================================ health
    def health(self) -> dict:
        """Kiểm tra THẬT: CLI tồn tại, đã đăng nhập, model có trong catalog."""
        out = {"adapter": self.name, "model": self.model, "sandbox": self.sandbox}
        try:
            v = subprocess.run(self._cmd_prefix() + ["--version"], capture_output=True,
                               text=True, timeout=60)
            out["version"] = (v.stdout or v.stderr or "").strip()[:120]
            out["cli_ok"] = v.returncode == 0
        except FileNotFoundError:
            return {**out, "ok": False, "code": "E_CODEX_NOT_INSTALLED",
                    "detail": f"không tìm thấy '{self.executable}' trong PATH"}
        except Exception as e:  # noqa: BLE001
            return {**out, "ok": False, "code": "E_CODEX_NOT_INSTALLED",
                    "detail": f"{type(e).__name__}: {e}"}

        try:
            s = subprocess.run(self._cmd_prefix() + ["login", "status"], capture_output=True,
                               text=True, timeout=60)
            txt = ((s.stdout or "") + (s.stderr or "")).strip()
            out["login_status"] = txt[:160]
            out["logged_in"] = (s.returncode == 0 and "not logged in" not in txt.lower())
        except Exception as e:  # noqa: BLE001
            out["logged_in"] = False
            out["login_status"] = f"không đọc được: {type(e).__name__}: {e}"

        # `codex login status` có thể BÁO SAI "Logged in" khi token đã hết hạn
        # (đã gặp thật: status=OK nhưng gọi API trả 401). Vì vậy kiểm thêm hạn token.
        # Chỉ chặn khi CHỨNG MINH ĐƯỢC là hết hạn: thiếu auth.json chưa đủ kết luận
        # (credential có thể nằm ở keyring/CODEX_HOME khác), khi đó tin `login status`.
        tok = self.token_state()
        out["token"] = tok
        if out.get("logged_in") and tok.get("present") and tok.get("expired"):
            return {**out, "ok": False, "code": "E_CODEX_TOKEN_EXPIRED",
                    "detail": f"token ChatGPT đã hết hạn lúc {tok.get('expires_at')} "
                              f"(status của CLI báo sai) — chạy: codex login --device-auth"}

        if not out.get("logged_in"):
            return {**out, "ok": False, "code": "E_CODEX_NOT_LOGGED_IN",
                    "detail": "Codex CLI chưa đăng nhập ChatGPT — chạy: codex login --device-auth"}

        avail = self.available_models()
        out["models_available"] = avail
        if self.model and avail and self.model not in avail:
            # KHÔNG tự đổi model (quyết định của chủ dự án)
            return {**out, "ok": False, "code": "E_CODEX_MODEL_UNAVAILABLE",
                    "detail": f"model '{self.model}' không có trong catalog; "
                              f"đang có: {', '.join(avail)}. Không tự fallback."}
        out["ok"] = True
        return out

    def token_state(self) -> dict:
        """Đọc hạn của access_token trong ~/.codex/auth.json (chỉ đọc, không log token).

        Trả {'present','expired','expires_at'} — KHÔNG bao giờ trả nội dung token.
        """
        home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
        p = Path(home) / "auth.json"
        if not p.exists():
            return {"present": False, "expired": False, "expires_at": None}
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            raw = ((d.get("tokens") or {}).get("access_token")) or ""
            if not raw or raw.count(".") < 2:
                return {"present": True, "expired": False, "expires_at": None}
            payload = raw.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            exp = claims.get("exp")
            if not exp:
                return {"present": True, "expired": False, "expires_at": None}
            when = datetime.datetime.fromtimestamp(int(exp))
            return {"present": True, "expired": when.timestamp() < time.time(),
                    "expires_at": when.strftime("%Y-%m-%d %H:%M:%S")}
        except Exception as e:  # noqa: BLE001
            return {"present": True, "expired": False, "expires_at": None,
                    "error": f"{type(e).__name__}: {e}"}

    def available_models(self) -> list[str]:
        """Đọc catalog THẬT của CLI (models_cache.json), không đoán."""
        home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
        p = Path(home) / "models_cache.json"
        if not p.exists():
            return []
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return [m.get("slug", "") for m in (d.get("models") or []) if m.get("slug")]
        except Exception:  # noqa: BLE001
            return []

    # ============================================================ staging
    def stage_handoff(self, job: dict, wd: Path) -> Path:
        """Giải nén hồ sơ vào vùng CHỈ ĐỌC: chỉ file text trong danh sách trắng.

        Không giải nén thực thi được => nội dung ZIP không thể chạy mã.
        """
        from services.zip_qc import _safe_members

        zip_path = wd / "input" / str(job.get("input_zip_name") or "")
        if not zip_path.exists():
            raise BridgeError("E_HANDOFF_INCOMPLETE",
                              f"không thấy hồ sơ {zip_path} — chạy prepare trước")
        stage = wd / "codex_handoff"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)

        written: list[str] = []
        with zipfile.ZipFile(zip_path) as zf:
            for info in _safe_members(zf, {"qc": {"max_files": 200,
                                                  "max_uncompressed_mb": 200}}):
                name = Path(info.filename.replace("\\", "/")).name
                if not name or name not in HANDOFF_WHITELIST:
                    continue
                if Path(name).suffix.lower() not in TEXT_SUFFIX:
                    continue
                data = zf.read(info)
                (stage / name).write_bytes(data)   # ghi nguyên byte, không giải nén nội dung
                written.append(name)
        if not written:
            raise BridgeError("E_HANDOFF_INCOMPLETE", "hồ sơ không có file text hợp lệ")
        return stage

    # ============================================================ submit
    def submit(self, job: dict, prompt: str) -> dict:
        wd = self.job_workdir(job)
        out_dir = self._out_dir(wd)

        h = self.health()
        if not h.get("ok"):
            return {"accepted": False, "conversation_url": None, "manual_required": True,
                    "note": f"{h.get('code')}: {h.get('detail')}", "submit_dir": str(wd),
                    "health": h}

        stage = self.stage_handoff(job, wd)
        files = sorted(p.name for p in stage.iterdir() if p.is_file())
        blocks = "\n\n".join(f"### {n}\n```\n<nội dung {n}>\n```" for n in self.required)
        full_prompt = (prompt.rstrip() + "\n\n" + OUTPUT_CONTRACT.format(
            files=", ".join(files), blocks=blocks))

        (wd / "prompt_sent.txt").write_text(full_prompt, encoding="utf-8")
        last_msg = out_dir / "last_message.txt"
        if last_msg.exists():
            last_msg.unlink()
        log_path = out_dir / "codex_stdout.log"

        cmd = self._cmd_prefix() + [
            "exec", "--skip-git-repo-check", "-C", str(stage), "-s", self.sandbox,
            "-o", str(last_msg),
        ]
        if self.model:
            cmd += ["-m", self.model]
        if self.json_events:
            cmd += ["--json"]
        cmd += self.extra_args

        env = {**os.environ}
        env.pop("PYTHONPATH", None)          # tránh làm hỏng runtime con
        env.setdefault("NO_COLOR", "1")

        try:
            logf = open(log_path, "wb")
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=logf,
                                    stderr=subprocess.STDOUT, cwd=str(stage), env=env,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                proc.stdin.write(full_prompt.encode("utf-8"))
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        except FileNotFoundError:
            return {"accepted": False, "conversation_url": None, "manual_required": True,
                    "note": "E_CODEX_NOT_INSTALLED: không chạy được 'codex'",
                    "submit_dir": str(wd)}
        except Exception as e:  # noqa: BLE001
            raise BridgeError("E_ADAPTER_BLOCKED", f"không spawn được codex: {e}")

        self._proc = proc
        self._write_state(wd, status="running", pid=proc.pid, model=self.model,
                          sandbox=self.sandbox, started_at=time.time(),
                          started_iso=time.strftime("%Y-%m-%dT%H:%M:%S"),
                          cmd=cmd, handoff_files=files, log=str(log_path),
                          last_message=str(last_msg), timeout_sec=self.timeout)

        def _wait():
            t0 = time.time()
            rc = None
            try:
                rc = proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                self._write_state(wd, status="timeout", exit_code=None,
                                  finished_at=time.time(),
                                  duration_sec=round(time.time() - t0, 1),
                                  detail=f"quá {self.timeout}s — đã kill")
                try:
                    logf.close()
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                logf.close()
            except Exception:  # noqa: BLE001
                pass
            parsed, missing = self.parse_output(last_msg, self.required)
            if rc == 0 and not missing:
                self._write_state(wd, status="done", exit_code=rc, finished_at=time.time(),
                                  duration_sec=round(time.time() - t0, 1),
                                  files_found=list(parsed), files_missing=[])
            else:
                detail = (f"exit={rc}; thiếu: {', '.join(missing)}" if missing
                          else f"exit={rc}")
                self._write_state(wd, status="failed", exit_code=rc, finished_at=time.time(),
                                  duration_sec=round(time.time() - t0, 1),
                                  detail=detail, files_found=list(parsed),
                                  files_missing=missing)

        self._thread = threading.Thread(target=_wait, name=f"codex-{job.get('job_id')}",
                                        daemon=True)
        self._thread.start()

        return {"accepted": True, "conversation_url": None, "manual_required": False,
                "note": f"codex exec đã chạy (pid={proc.pid}, model={self.model}, "
                        f"sandbox={self.sandbox}, {len(files)} file hồ sơ)",
                "submit_dir": str(wd), "pid": proc.pid}

    # ============================================================ status
    def status(self, job: dict) -> dict:
        wd = self.job_workdir(job)
        st = self._read_state(wd)
        if not st:
            return {"state": "unknown", "detail": "chưa submit", "elapsed_sec": 0}

        elapsed = int(time.time() - float(st.get("started_at") or time.time()))
        status = st.get("status")
        if status == "running":
            alive = self._pid_alive(int(st.get("pid") or 0))
            if alive:
                return {"state": "waiting", "detail": f"codex đang viết ({elapsed}s)",
                        "elapsed_sec": elapsed}
            # Tiến trình đã chết: thread _wait có thể chưa kịp ghi state (đua).
            # Đọc lại state MỘT lần; nếu vẫn 'running' thì tự phục hồi từ file kết quả.
            st = self._read_state(wd)
            if st.get("status") == "done":
                return {"state": "result_available",
                        "detail": f"{len(st.get('files_found') or [])} file",
                        "elapsed_sec": elapsed}
            if st.get("status") in ("failed", "timeout"):
                return {"state": "failed", "detail": st.get("detail") or "codex lỗi",
                        "elapsed_sec": elapsed}
            parsed, missing = self.parse_output(Path(st.get("last_message") or ""),
                                               self.required)
            if parsed and not missing:
                self._write_state(wd, status="done", finished_at=time.time(),
                                  duration_sec=elapsed, files_found=list(parsed))
                return {"state": "result_available", "detail": "đã có đủ file",
                        "elapsed_sec": elapsed}
            detail = ("tiến trình codex đã dừng, không đủ file"
                      + (f": thiếu {', '.join(missing)}" if missing else ""))
            self._write_state(wd, status="failed", detail=detail,
                              files_found=list(parsed), files_missing=missing)
            return {"state": "failed", "detail": detail, "elapsed_sec": elapsed}
        if status == "done":
            return {"state": "result_available", "detail": f"{len(st.get('files_found') or [])} file",
                    "elapsed_sec": elapsed}
        return {"state": "failed", "detail": st.get("detail") or "codex lỗi",
                "elapsed_sec": elapsed}

    # ============================================================ collect
    def collect_result(self, job: dict) -> dict:
        wd = self.job_workdir(job)
        st = self._read_state(wd)
        last_msg = Path(st.get("last_message") or (self._out_dir(wd) / "last_message.txt"))
        parsed, missing = self.parse_output(last_msg, self.required)
        if missing:
            return {"zip_path": None, "source": "auto",
                    "note": f"thiếu file kết quả: {', '.join(missing)}"}

        out_dir = self._out_dir(wd)
        files_dir = out_dir / "files"
        if files_dir.exists():
            shutil.rmtree(files_dir)
        files_dir.mkdir(parents=True)
        for n, body in parsed.items():
            (files_dir / n).write_text(body, encoding="utf-8")

        out = wd / f"{job['job_id']}_CODEX_SCRIPT_DELIVERY.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for n in self.required:
                zf.writestr(n, parsed[n])
            zf.writestr("artifact_meta.json", json.dumps(
                {"job_id": job.get("job_id"), "source": self.name, "model": st.get("model"),
                 "sandbox": st.get("sandbox"), "duration_sec": st.get("duration_sec"),
                 "exit_code": st.get("exit_code")}, ensure_ascii=False, indent=2))
        return {"zip_path": str(out), "source": "auto",
                "note": f"codex_cli sinh {len(parsed)} file (model={st.get('model')})"}

    # ============================================================ parse
    @staticmethod
    def parse_output(last_msg: Path, required: list[str]) -> tuple[dict, list[str]]:
        """Tách file từ câu trả lời cuối. Chấp nhận nhiều kiểu tiêu đề khối."""
        found: dict[str, str] = {}
        p = Path(last_msg) if last_msg else None
        if not p or not p.exists():
            return {}, list(required)
        text = p.read_text(encoding="utf-8", errors="replace")

        for name in required:
            n = re.escape(name)
            patterns = [
                # ### NAME  + ```...```
                r"(?:^|\n)[^\n]{0,80}" + n + r"[^\n]{0,40}\n\s*```[a-zA-Z0-9]*\n(.*?)\n?```",
                # ```NAME ... ```
                r"```[a-zA-Z0-9]*\s*" + n + r"\s*\n(.*?)\n?```",
                # === FILE: NAME ===  ... (đến khối kế tiếp)
                r"===\s*(?:FILE:)?\s*" + n + r"\s*===\s*\n(.*?)(?=\n===\s*(?:FILE:)?\s*\S+\s*===|\Z)",
            ]
            for pat in patterns:
                m = re.search(pat, text, re.S)
                if m and m.group(1).strip():
                    found[name] = m.group(1).strip() + "\n"
                    break
        missing = [n for n in required if n not in found]
        return found, missing

    # ============================================================ close
    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
