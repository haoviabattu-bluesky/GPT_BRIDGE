# -*- coding: utf-8 -*-
"""adapters/chatgpt_web.py — điều khiển ChatGPT Web qua CDP trên CHROME RIÊNG.

NGUYÊN TẮC AN TOÀN (spec §17) — đây là lý do adapter này mặc định TẮT:
- Dùng Chrome với --user-data-dir RIÊNG (không đụng Chrome của người dùng).
- Cửa sổ đặt NGOÀI màn hình (-32000,-32000) => không che desktop, không kéo lên trước.
- KHÔNG dùng pyautogui, KHÔNG di chuyển chuột thật, KHÔNG gõ phím vào cửa sổ khác.
- KHÔNG giải CAPTCHA, KHÔNG vượt chống bot, KHÔNG lấy cookie trái phép.
- Nếu gặp CAPTCHA / login / consent => DỪNG, trả BLOCKED, báo người vận hành.

ĐIỀU KHOẢN: đây là điều khiển giao diện web, KHÔNG phải API chính thức.
Việc dùng có thể vi phạm ToS của OpenAI. Adapter này chỉ được bật khi chủ dự án
đã tự xác nhận được phép và đã kiểm chứng hoạt động thực tế.

TRẠNG THÁI: cài đặt nhưng CHƯA được kiểm chứng chạy thật trong môi trường này.
`docs/ADAPTER_FEASIBILITY.md` ghi rõ phần nào đã test, phần nào chưa.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from .base import GPTWriterAdapter
from services.errors import BridgeError

# Dấu hiệu ChatGPT đang trả lời / đã xong (dùng DOM, không dùng ảnh)
JS_STATE = r"""
(() => {
  const q = s => document.querySelector(s);
  const btns = [...document.querySelectorAll('button')];
  const stop = btns.find(b => /stop|dừng|停止/i.test(b.getAttribute('aria-label')||b.textContent||''));
  const composer = q('#prompt-textarea') || q('div[contenteditable="true"]');
  const msgs = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
  const last = msgs.length ? msgs[msgs.length-1].innerText : '';
  const links = [...document.querySelectorAll('a[href]')].map(a=>a.href)
      .filter(h=>/\.zip(\?|$)|drive\.google\.com|oaiusercontent|files\.oai/i.test(h));
  const captcha = /captcha|verify you are human|unusual traffic/i.test(document.body.innerText||'');
  return {hasComposer: !!composer, streaming: !!stop, nMsgs: msgs.length,
          lastText: last.slice(-4000), links: links.slice(-20), captcha};
})()
"""


class ChatGPTWebAdapter(GPTWriterAdapter):
    name = "chatgpt_web"
    supports_auto = True          # có thể tự lấy kết quả — CHỈ khi đã kiểm chứng

    def __init__(self, cfg: dict, logger=None):
        self.cfg = cfg
        self.log = logger
        w = (cfg.get("writer") or {}).get("chatgpt_web") or {}
        self.port = int(w.get("cdp_port", 9335))
        self.profile = Path(w.get("profile_dir") or "E:/CLAUDE/YOUTUBE/CHATGPT_PROFILE")
        self.chrome = w.get("chrome_exe") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        self.pos = w.get("window_position", "-32000,-32000")
        self.size = w.get("window_size", "1280,900")
        self.allow_launch = bool(w.get("allow_launch", False))
        self.timeout = int(w.get("timeout_sec", 1800))
        self.poll = int(w.get("poll_sec", 15))
        self.stable = int(w.get("stable_sec", 3))
        self.model = w.get("model", "")
        self.project_url = w.get("project_url") or ""
        self._pw = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------ browser
    def _endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _alive(self) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(self._endpoint() + "/json/version", timeout=2) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def launch(self) -> dict:
        """Mở Chrome RIÊNG, cửa sổ ngoài màn hình. Không đụng Chrome người dùng."""
        if not self.allow_launch:
            return {"launched": False,
                    "reason": "writer.chatgpt_web.allow_launch=false — chưa được phép tự mở Chrome"}
        if self._alive():
            return {"launched": False, "reason": "đã có CDP sẵn ở cổng " + str(self.port)}
        self.profile.mkdir(parents=True, exist_ok=True)
        args = [self.chrome,
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile}",
                f"--window-position={self.pos}",
                f"--window-size={self.size}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", "--disable-sync",
                "--disable-features=Translate,MediaRouter",
                "about:blank"]
        subprocess.Popen(args, close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        for _ in range(40):
            time.sleep(0.5)
            if self._alive():
                return {"launched": True, "port": self.port, "profile": str(self.profile)}
        return {"launched": False, "reason": "Chrome không mở CDP sau 20s"}

    def _attach(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright
        if not self._alive():
            raise RuntimeError("Không có CDP ở " + self._endpoint() +
                               " — cần mở Chrome riêng trước (allow_launch hoặc thủ công)")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self._endpoint())
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return self._page

    def health(self) -> dict:
        """Kiểm tra THẬT: có CDP sống không. Không tự mở Chrome."""
        ep = self._endpoint()
        try:
            alive = self._alive()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "E_ADAPTER_BLOCKED",
                    "detail": f"không dò được CDP tại {ep}: {type(e).__name__}: {e}"}
        if not alive:
            return {"ok": False, "code": "E_ADAPTER_BLOCKED",
                    "detail": f"không có CDP tại {ep} — cần mở Chrome riêng trước"}
        try:
            page = self._attach()
            url = getattr(page, "url", "")
            return {"ok": True, "cdp": ep, "url": url,
                    "logged_in": "chatgpt.com" in str(url) and "auth" not in str(url)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "E_ADAPTER_BLOCKED",
                    "detail": f"CDP có nhưng không attach được: {type(e).__name__}: {e}"}

    def close(self) -> None:
        try:
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = self._browser = self._page = None

    # ------------------------------------------------------------ submit
    def submit(self, job: dict, prompt: str) -> dict:
        """Mở chat MỚI cho tập này, dán prompt, gửi. Trả conversation_url."""
        wd = self.job_workdir(job)
        (wd / "prompt_sent.txt").write_text(prompt, encoding="utf-8")

        try:
            page = self._attach()
        except RuntimeError as e:
            raise BridgeError("E_ADAPTER_BLOCKED", str(e))
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        st = page.evaluate(JS_STATE)
        if st.get("captcha"):
            return {"accepted": False, "conversation_url": None, "manual_required": True,
                    "note": "Bị chặn xác minh người dùng (CAPTCHA) — adapter DỪNG, không vượt.",
                    "submit_dir": str(wd)}
        if not st.get("hasComposer"):
            return {"accepted": False, "conversation_url": None, "manual_required": True,
                    "note": "Không thấy ô nhập — có thể chưa đăng nhập. Dừng để người xử lý.",
                    "submit_dir": str(wd)}

        if self.model:
            self._pick_model(page)

        box = page.query_selector('#prompt-textarea') or page.query_selector('div[contenteditable="true"]')
        if not box:
            return {"accepted": False, "conversation_url": None, "manual_required": True,
                    "note": "Không tìm thấy ô soạn tin.", "submit_dir": str(wd)}
        box.click()
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(600)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)

        url = page.url
        return {"accepted": True, "conversation_url": url, "manual_required": False,
                "note": "đã gửi prompt vào chat mới", "submit_dir": str(wd)}

    def _pick_model(self, page) -> None:
        try:
            sw = page.query_selector('[data-testid="model-switcher-dropdown-button"]')
            if not sw:
                return
            sw.click()
            page.wait_for_timeout(900)
            want = self.model.lower()
            for item in page.query_selector_all('[role="menuitem"], [role="option"]'):
                t = (item.inner_text() or "").lower()
                if want in t:
                    item.click()
                    page.wait_for_timeout(700)
                    return
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ status
    def status(self, job: dict) -> dict:
        """Xong = KHÔNG còn nút Stop + nội dung đứng yên `stable_sec` giây."""
        started = time.time()
        try:
            page = self._attach()
            if job.get("conversation_url"):
                cur = page.url or ""
                if cur.split("?")[0] != str(job["conversation_url"]).split("?")[0]:
                    page.goto(job["conversation_url"], wait_until="domcontentloaded",
                              timeout=60000)
                    page.wait_for_timeout(1500)
            last, stable_since = None, None
        except Exception as e:  # noqa: BLE001
            return {"state": "unknown", "detail": f"không attach được: {e}", "elapsed_sec": 0}

        while time.time() - started < self.timeout:
            st = page.evaluate(JS_STATE)
            if st.get("captcha"):
                return {"state": "failed", "detail": "CAPTCHA — dừng", "elapsed_sec": int(time.time()-started)}
            if st.get("streaming"):
                last, stable_since = None, None
            else:
                txt = st.get("lastText") or ""
                if txt != last:
                    last, stable_since = txt, time.time()
                elif stable_since and time.time() - stable_since >= self.stable and txt.strip():
                    return {"state": "result_available",
                            "detail": f"{len(txt)} ký tự cuối ổn định", 
                            "elapsed_sec": int(time.time()-started),
                            "links": st.get("links", [])}
            time.sleep(self.poll)
        return {"state": "failed", "detail": "timeout chờ GPT", "elapsed_sec": int(time.time()-started)}

    # ------------------------------------------------------------ collect
    def collect_result(self, job: dict) -> dict:
        """Lấy 4 file trong câu trả lời cuối -> đóng ZIP chuẩn.

        Hỗ trợ: khối code có tiêu đề tên file, hoặc link tải trực tiếp.
        KHÔNG tải link Drive của người khác; chỉ tải link do ChatGPT trả về.
        """
        wd = self.job_workdir(job)
        page = self._attach()
        if job.get("conversation_url") and page.url != job["conversation_url"]:
            page.goto(job["conversation_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

        text = page.evaluate(
            "() => {const m=[...document.querySelectorAll('[data-message-author-role=\"assistant\"]')];"
            "return m.length? m[m.length-1].innerText : '';}")
        files = self._split_files(text)
        need = ["SCRIPT_FINAL_JP.txt", "SCRIPT_QC_REPORT.md", "SCRIPT_ISSUES.md", "SCRIPT_STRUCTURE.md"]
        missing = [n for n in need if n not in files]
        if missing:
            (wd / "gpt_reply_raw.txt").write_text(text, encoding="utf-8")
            return {"zip_path": None, "source": "auto",
                    "note": f"câu trả lời thiếu khối file: {', '.join(missing)} "
                            f"(đã lưu gpt_reply_raw.txt để người xem)"}

        out = wd / f"{job['job_id']}_GPT_SCRIPT_DELIVERY.zip"
        import zipfile
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for n in need:
                zf.writestr(n, files[n])
            zf.writestr("artifact_meta.json", json.dumps(
                {"job_id": job["job_id"], "source": "chatgpt_web",
                 "conversation_url": job.get("conversation_url")},
                ensure_ascii=False, indent=2))
        return {"zip_path": str(out), "source": "auto", "note": "lấy từ câu trả lời ChatGPT"}

    @staticmethod
    def _split_files(text: str) -> dict[str, str]:
        """Tách khối theo tiêu đề tên file. Hỗ trợ cả ```lang và tiêu đề markdown."""
        names = ["SCRIPT_FINAL_JP.txt", "SCRIPT_QC_REPORT.md", "SCRIPT_ISSUES.md",
                 "SCRIPT_STRUCTURE.md"]
        out: dict[str, str] = {}
        for n in names:
            pat = re.compile(r"(?:^|\n)[^\n]{0,40}" + re.escape(n) + r"[^\n]{0,40}\n```[a-zA-Z]*\n(.*?)```",
                             re.S)
            m = pat.search(text)
            if m:
                out[n] = m.group(1)
        if "SCRIPT_FINAL_JP.txt" not in out:
            m = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
            if m and len(m.group(1)) > 500:
                out["SCRIPT_FINAL_JP.txt"] = m.group(1)
        return out
