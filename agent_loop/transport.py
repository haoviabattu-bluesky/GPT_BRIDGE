# -*- coding: utf-8 -*-
"""transport.py — kênh trao đổi THẬT với ChatGPT Web qua CDP.

Nguyên tắc (đã đo trên máy này):
- Chrome RIÊNG (`--user-data-dir=E:\\CLAUDE\\YOUTUBE\\CHATGPT_PROFILE`), cửa sổ đặt
  NGOÀI màn hình (-32000,-32000) => KHÔNG cướp focus (đo: foreground window không đổi).
- KHÔNG pyautogui / pynput / win32api. KHÔNG đọc cookie, KHÔNG vượt CAPTCHA.
- Chỉ thao tác DOM của tab do chính nó mở.

Bài học bắt buộc (từ skill chatgpt-web-multiturn-bridge):
- PITFALL #0: chỉ coi là XONG khi text ĐỨNG YÊN **và** nút Stop ĐÃ BIẾN MẤT.
- PITFALL #1: prompt dài phải gửi bằng FILE .md đính kèm.
- PITFALL #4: hội thoại "cóng" sau ~5-6 lượt => phải phát hiện và mở chat mới.
- Đọc bài: gom TẤT CẢ khối markdown của tin assistant cuối; bóc innerText của <button>.
"""
from __future__ import annotations

import subprocess
import time
import urllib.request
from pathlib import Path

PORT = 9335
PROFILE = Path(r"E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Ngưỡng gửi kèm file thay vì dán vào ô chat (đo thật: 18.857 ký tự làm GPT im lặng)
MAX_INLINE_CHARS = 3000


class TransportError(RuntimeError):
    """Lỗi kênh truyền nhận — KHÔNG được coi là PASS."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------- Chrome
def _alive() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_chrome() -> dict:
    """Bật Chrome riêng nếu chưa có. Cửa sổ NGOÀI màn hình.

    BẪY ĐÃ ĐO (probe_focus_phases, 2026-10-25): `--window-position=-32000,-32000`
    KHÔNG đủ. Windows vẫn đưa cửa sổ Chrome mới lên foreground lúc khởi động
    (P0 hwnd=66960 'Hermes' -> P1 hwnd=18219890 'about:blank - Google Chrome').
    Pha GỬI/NHẬN thì không cướp (P1 -> P2 cùng hwnd, 0 cửa sổ lạ).

    Nên: giữ nguyên vị trí ngoài màn hình, và bật thêm cờ để cửa sổ không tự
    kích hoạt. Sau khi CDP sống, trả lại foreground cũ nếu ta đã lấy mất.
    """
    if _alive():
        return {"launched": False, "reason": "CDP đã sống"}
    PROFILE.mkdir(parents=True, exist_ok=True)
    prev = _foreground_hwnd()
    args = [CHROME,
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE}",
            "--window-position=-32000,-32000",
            "--window-size=1280,900",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-sync",
            "--disable-features=Translate,MediaRouter",
            # Giảm khả năng tự chiếm foreground khi khởi động:
            "--no-startup-window",
            "about:blank"]
    subprocess.Popen(args, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    for _ in range(40):
        time.sleep(0.5)
        if _alive():
            # Trả foreground về cửa sổ trước đó nếu Chrome đã lấy mất.
            _restore_foreground(prev)
            return {"launched": True, "port": PORT, "prev_foreground": prev}
    raise TransportError("E_CHROME_NO_CDP", f"Chrome không mở CDP ở cổng {PORT} sau 20s")


def _restore_foreground(hwnd: int) -> bool:
    """Đưa foreground về `hwnd`. Trả True nếu đã trả được.

    ĐO THẬT 2026-10-25: hàm này **KHÔNG hiệu quả** trên máy này — Windows chặn
    `SetForegroundWindow` từ tiến trình nền không có quyền foreground, nên trả
    False và còn làm `GetForegroundWindow()` về 0 trong thoáng chốt. Vì vậy
    transport KHÔNG gọi hàm này nữa; giữ lại để đo/ghi nhận, không dùng trong
    luồng chính. Xem `scratch_analysis/probe_focus_stages.py`.
    """
    if not hwnd:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        if not u.IsWindow(hwnd):
            return False
        if u.GetForegroundWindow() == hwnd:
            return True
        u.SetForegroundWindow(hwnd)
        return u.GetForegroundWindow() == hwnd
    except Exception:
        return False


# --------------------------------------------------------------------- JS
JS_STATE = r"""() => {
  const btns = [...document.querySelectorAll('button')];
  const stop = btns.find(b => /^(stop|dừng|停止)/i.test((b.getAttribute('aria-label')||'').trim()));
  const composer = document.querySelector('div[aria-label="Ask ChatGPT"][contenteditable="true"]')
                || document.querySelector('#prompt-textarea');
  const turns = [...document.querySelectorAll('[data-turn-key]')];
  const body = document.body.innerText || '';
  return {
    url: location.href,
    hasComposer: !!composer,
    streaming: !!stop,
    turnCount: turns.length,
    roles: turns.map(t => t.getAttribute('data-message-author-role')),
    captcha: /verify you are human|unusual traffic/i.test(body),
    loginWall: /^(log in|sign up)$/im.test(body.slice(0, 400)),
    notFound: /conversation not found|no longer available/i.test(body)
  };
}"""

# Đọc TIN ASSISTANT CUỐI: gom MỌI khối markdown, bóc chữ của <button> (rác UI).
JS_LAST_REPLY = r"""() => {
  const turns = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
  if (!turns.length) return {ok:false, reason:'chưa có tin assistant'};
  const last = turns[turns.length - 1];
  const blocks = [...last.querySelectorAll('.markdown')];
  const parts = (blocks.length ? blocks : [last]).map(b => {
    let s = b.innerText || '';
    [...b.querySelectorAll('button')].forEach(btn => {
      const t = (btn.innerText || '').trim();
      if (t) s = s.split(t).join('');
    });
    return s.trim();
  }).filter(Boolean);
  const text = parts.join('\n\n').trim();
  return {ok:true, text, len:text.length, blocks:parts.length};
}"""


def _foreground_title() -> str:
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def _foreground_hwnd() -> int:
    """HWND của cửa sổ đang foreground — dùng để so CỬA SỔ, không so tiêu đề.

    Bẫy đã đo: Chrome tự đổi tiêu đề khi ChatGPT stream câu trả lời
    ("ChatGPT" -> "Trả lời kiểm tra ..."), nên so tiêu đề sẽ báo "cướp focus"
    giả trong khi cùng một cửa sổ vẫn ở foreground.
    """
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


# --------------------------------------------------------------------- class
class ChatGPTransport:
    """Một kết nối CDP tới Chrome riêng. Không đụng Chrome của người dùng."""

    def __init__(self, conversation_url: str = "", *, log=print):
        self.conversation_url = conversation_url
        self.log = log
        self._pw = None
        self._browser = None
        self.page = None
        self.focus_before = ""

    # ---------------------------------------------------------- lifecycle
    def open(self):
        ensure_chrome()
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.focus_before = _foreground_title()
        self._fg_before = _foreground_hwnd()
        # ĐO THẬT (probe_focus_stages): `connect_over_cdp` + lấy page KÉO cửa sổ
        # Chrome lên foreground (S0b 'Hermes' 66960 -> S1 'about:blank' 2491850),
        # nhưng `new_conversation()` ngay sau đó đã tự trả lại (S2 = 66960 'Hermes').
        # Giai đoạn GỬI/NHẬN (S2->S3) KHÔNG đổi foreground. Vì vậy không gọi
        # _restore_foreground (Windows chặn SetForegroundWindow từ tiến trình nền).
        return self

    def close(self):
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self.page = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    # ---------------------------------------------------------- navigation
    def goto(self, url: str, wait_ms: int = 4000) -> dict:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(wait_ms)
        return self.page.evaluate(JS_STATE)

    def new_conversation(self) -> dict:
        """Mở chat MỚI. Trả state (url còn rỗng cho tới khi gửi tin đầu)."""
        st = self.goto("https://chatgpt.com/?model=auto", 5000)
        if st.get("captcha"):
            raise TransportError("E_CAPTCHA", "ChatGPT yêu cầu xác minh người dùng — DỪNG")
        if not st.get("hasComposer"):
            raise TransportError("E_NO_COMPOSER", "không thấy ô nhập — có thể chưa đăng nhập")
        return st

    def assert_conversation(self, url: str) -> dict:
        """Xác nhận URL hội thoại còn SỐNG (không redirect về trang chủ)."""
        st = self.goto(url, 5000)
        final = st.get("url") or ""
        want = url.split("?")[0].rstrip("/")
        got = final.split("?")[0].rstrip("/")
        if got != want:
            raise TransportError(
                "E_CONVERSATION_DEAD",
                f"mở {want} nhưng bị đưa về {got} (hội thoại không còn tồn tại)")
        return st
