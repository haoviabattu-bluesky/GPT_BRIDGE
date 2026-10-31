# -*- coding: utf-8 -*-
"""server.py — HTTP localhost cho Chrome Extension GPT_BRIDGE.

Vì sao HTTP chứ không phải native messaging:
    - native messaging cần đăng ký registry/HKLM trên Windows -> phải cài đặt máy,
      khó gỡ, và không chạy được nếu chưa đăng ký host manifest.
    - HTTP trên 127.0.0.1 chỉ bind loopback, có token, extension xin quyền
      `host_permissions` đúng một origin. Không cần quyền hệ thống.

BẢO MẬT
    - Bind 127.0.0.1 (không 0.0.0.0) -> máy khác trong LAN không vào được.
    - Mọi request phải có header `X-Bridge-Token` khớp token trong config.
    - Chỉ phục vụ JSON; không đọc/ghi file ngoài `jobs/_handoff` và thư mục job.
    - KHÔNG endpoint nào gửi prompt vào ChatGPT, KHÔNG đọc nội dung chat.
      Extension chỉ báo trạng thái người dùng đã thao tác (opened/sent) và lấy
      prompt để người dùng dán.

CHẠY
    python -m chatgpt_web_handoff.server --port 8765
"""
from __future__ import annotations

import argparse
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .prompt import build_extension_prompt
from .store import HandoffStore

MAX_BODY = 64 * 1024


def _load_cfg(root: Path) -> dict:
    try:
        from services.common import load_config
        return load_config(root / "config.yaml")
    except Exception:                                    # noqa: BLE001
        return {"_root": str(root)}


def _phase5_cfg(cfg: dict) -> dict:
    """Đọc writer.chatgpt_web_handoff; chấp nhận key phase5 cho test nhanh."""
    writer = cfg.get("writer") or {}
    return dict(writer.get("chatgpt_web_handoff") or cfg.get("phase5") or {})


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "GPTBridgeHandoff/5.0"
    store: HandoffStore = None          # gán khi tạo server
    token: str = ""
    conversation_url: str = ""

    # ------------------------------------------------------------ helpers
    def _cors(self) -> None:
        # Extension chạy ở origin chrome-extension://<id>; cho phép đúng origin đó.
        origin = self.headers.get("Origin") or ""
        if origin.startswith("chrome-extension://") or origin.startswith("http://127.0.0.1"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Bridge-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        return secrets.compare_digest(
            str(self.headers.get("X-Bridge-Token") or ""), str(self.token or ""))

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) or {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt: str, *args) -> None:      # noqa: A003
        return                                          # im lặng, không spam console

    # ------------------------------------------------------------ verbs
    def do_OPTIONS(self) -> None:                        # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:                            # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/health", "/"):
            # /health không cần token: để extension kiểm tra server sống.
            self._json(200, {"ok": True, "service": "chatgpt_web_handoff",
                             "version": "5.0.0",
                             "conversation_url": self.conversation_url,
                             "requires_token": True})
            return
        if not self._auth_ok():
            self._json(401, {"ok": False, "error": "sai hoặc thiếu X-Bridge-Token"})
            return

        if path == "/jobs":
            jobs = [HandoffStore.public_view(j) for j in self.store.all()]
            self._json(200, {"ok": True, "jobs": jobs,
                             "conversation_url": self.conversation_url})
            return

        if path.startswith("/job/"):
            job_id = path[len("/job/"):]
            job = self.store.get(job_id)
            if job is None:
                self._json(404, {"ok": False, "error": f"không có job {job_id}"})
                return
            view = HandoffStore.public_view(job)
            built = build_extension_prompt(job)
            # Prompt ĐÃ lưu là bản Hermes dựng lúc xếp job (đầy đủ ngân sách ký tự,
            # số chữ hook...). Chỉ dựng lại khi bản ghi cũ không có prompt, nếu không
            # sẽ âm thầm trả bản rút gọn cho extension.
            if not view.get("prompt"):
                view["prompt"] = built["prompt"]
            view["output_zip_name"] = job.get("output_zip_name") or built["output_zip_name"]
            view["required_files"] = job.get("required_files") or built["required_files"]
            view["conversation_url"] = job.get("conversation_url") or self.conversation_url
            self._json(200, {"ok": True, "job": view})
            return

        self._json(404, {"ok": False, "error": "không có endpoint"})

    def do_POST(self) -> None:                           # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if not self._auth_ok():
            self._json(401, {"ok": False, "error": "sai hoặc thiếu X-Bridge-Token"})
            return
        data = self._body()
        job_id = str(data.get("job_id") or "")
        if not job_id:
            self._json(400, {"ok": False, "error": "thiếu job_id"})
            return
        if self.store.get(job_id) is None:
            self._json(404, {"ok": False, "error": f"không có job {job_id}"})
            return

        # Extension CHỈ báo sự kiện do người dùng thực hiện. Không có endpoint
        # nào gửi prompt hộ hay đọc nội dung hội thoại.
        if path == "/event/opened":
            job = self.store.get(job_id)
            if str(job.get("state")).upper() == "QUEUED":
                self.store.set_state(job_id, "OPENED",
                                     "người dùng đã mở đúng conversation_url")
            else:
                self.store.add_event(job_id, "opened_again", "mở lại cuộc hội thoại")
            self._json(200, {"ok": True, "state": self.store.get(job_id)["state"]})
            return

        if path == "/event/sent":
            self.store.set_state(job_id, "SENT",
                                 "người dùng đã gửi prompt trong ChatGPT")
            self._json(200, {"ok": True, "state": "SENT"})
            return

        if path == "/event/note":
            self.store.add_event(job_id, "note", str(data.get("detail") or "")[:500])
            self._json(200, {"ok": True})
            return

        self._json(404, {"ok": False, "error": "không có endpoint"})


def make_server(root: str | Path, *, port: int = 8765, token: str = "",
                host: str = "127.0.0.1") -> ThreadingHTTPServer:
    root = Path(root)
    cfg = _load_cfg(root)
    phase5 = _phase5_cfg(cfg)
    host = str(phase5.get("server_host") or "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"server_host phải là loopback, không nhận {host!r}")
    if not token:
        token = str(phase5.get("token") or "")
    if not token:
        token = secrets.token_urlsafe(24)
    store = HandoffStore(root)
    handler = type("Handler", (BridgeHandler,), {
        "store": store, "token": token,
        "conversation_url": str(phase5.get("conversation_url") or ""),
    })
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.bridge_token = token                              # type: ignore[attr-defined]
    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GPT_BRIDGE handoff server (localhost)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--token", default="")
    args = ap.parse_args(argv)
    srv = make_server(args.root, port=args.port, token=args.token)
    print(f"[handoff] http://127.0.0.1:{args.port}  token={srv.bridge_token}")  # noqa: T201
    print("[handoff] dán token này vào Options của extension GPT_BRIDGE")       # noqa: T201
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
