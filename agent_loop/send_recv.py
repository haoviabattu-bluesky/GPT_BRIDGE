# -*- coding: utf-8 -*-
"""send_recv.py — GỬI một tin và NHẬN câu trả lời hoàn chỉnh từ ChatGPT Web.

SELECTOR THẬT (đo trên UI hiện tại 2026-10-25 — UI cũ `data-message-author-role`
đã CHẾT, trả về 0 phần tử, đừng dùng lại):
    lượt      : [data-turn-key]                  (1 phần tử / lượt)
    tin user  : [data-user-message-bubble]       (bên trong lượt)
    khối trả lời: [data-markdown-copy]           (bên trong lượt, CÓ THỂ NHIỀU khối)
    đang chạy : button[aria-label="Stop"]
    ô soạn    : div[aria-label="Ask ChatGPT"][contenteditable="true"]
    nút gửi   : form button[aria-label="send"]

BA BẪY ĐÃ ĐO (đừng bỏ qua):
1. ĐỌC SỚM: model đứng yên vài giây giữa lúc viết là bình thường. Chỉ nhận "xong"
   khi text ĐỨNG YÊN >= stable_sec **VÀ** nút Stop ĐÃ BIẾN MẤT.
2. Gom TẤT CẢ khối [data-markdown-copy] của lượt — chỉ lấy khối cuối là mất bài.
3. Gửi bằng JS trong 1 tick — nút Send bị React gỡ/tạo lại liên tục.
"""
from __future__ import annotations

import time
from pathlib import Path

from .transport import MAX_INLINE_CHARS, TransportError

# Rác giao diện luôn xuất hiện trong khối markdown — phải bóc ra.
BOILERPLATE = (
    "ChatGPT can make mistakes. Check important info.",
    "ChatGPT can make mistakes.",
)

JS_STATE = r"""() => {
  const btns = [...document.querySelectorAll('button')];
  const stop = btns.some(b => /^stop$/i.test((b.getAttribute('aria-label')||'').trim()));
  const composer = document.querySelector('div[aria-label="Ask ChatGPT"][contenteditable="true"]');
  const turns = document.querySelectorAll('[data-turn-key]');
  const body = document.body.innerText || '';
  const last = turns[turns.length - 1];
  return {
    url: location.href,
    hasComposer: !!composer,
    streaming: stop,
    turnCount: turns.length,
    lastKey: last ? last.getAttribute('data-turn-key') : null,
    composerLen: composer ? (composer.innerText || '').trim().length : -1,
    captcha: /verify you are human|unusual traffic/i.test(body),
    responseComplete: body.includes('Response complete'),
    bodyLen: body.length
  };
}"""

# Đọc tin ASSISTANT của LƯỢT CUỐI: gom MỌI khối markdown, bóc nút UI + disclaimer.
JS_LAST_REPLY = r"""() => {
  const turns = [...document.querySelectorAll('[data-turn-key]')];
  if (!turns.length) return {ok:false, reason:'chưa có lượt nào'};
  const last = turns[turns.length - 1];
  const blocks = [...last.querySelectorAll('[data-markdown-copy]')];
  const BAD = /^ChatGPT can make mistakes/i;
  const parts = [];
  for (const b of blocks) {
    let s = b.innerText || '';
    // bóc chữ của nút (Copy, Good response, Read aloud...) — nằm lẫn trong innerText
    for (const btn of b.querySelectorAll('button')) {
      const t = (btn.innerText || '').trim();
      if (t) s = s.split(t).join('');
    }
    s = s.trim();
    if (!s || BAD.test(s)) continue;
    parts.push(s);
  }
  // lượt này là của assistant? (không có bong bóng user => chắc chắn assistant)
  const isUserTurn = !!last.querySelector('[data-user-message-bubble]');
  return {
    ok: true,
    text: parts.join('\n\n').trim(),
    blocks: parts.length,
    turnKey: last.getAttribute('data-turn-key'),
    isUserTurn,
    userText: isUserTurn ? (last.querySelector('[data-user-message-bubble]').innerText||'').trim() : ''
  };
}"""

# Đọc TẤT CẢ các lượt (mỗi lượt 1 chuỗi). Dùng khi cần tìm nội dung nằm ở lượt CŨ.
JS_ALL_TURNS = r"""() => {
  const turns = [...document.querySelectorAll('[data-turn-key]')];
  return turns.map(t => {
    const blocks = [...t.querySelectorAll('[data-markdown-copy]')];
    const parts = [];
    for (const b of blocks) {
      let s = b.innerText || '';
      for (const btn of b.querySelectorAll('button')) {
        const y = (btn.innerText || '').trim();
        if (y) s = s.split(y).join('');
      }
      s = s.trim();
      if (s) parts.push(s);
    }
    return {text: parts.join('\n\n').trim(), blocks: parts.length,
            isUser: !!t.querySelector('[data-user-message-bubble]')};
  });
}"""

JS_COMPOSER_LEN = r"""() => {
  const c = document.querySelector('div[aria-label="Ask ChatGPT"][contenteditable="true"]');
  return c ? (c.innerText || '').replace(/\n/g, '').length : -1;
}"""

JS_SEND = r"""() => {
  const f = document.querySelector('form');
  if (!f) return {ok:false, reason:'không thấy form'};
  const bs = [...f.querySelectorAll('button')];
  const gui = bs.find(b => (b.getAttribute('aria-label')||'').toLowerCase() === 'send')
           || bs.find(b => b.type === 'submit');
  if (!gui) return {ok:false, reason:'không thấy nút Gửi (ô soạn có thể đang rỗng)',
                    labels: bs.map(b => b.getAttribute('aria-label')||b.type)};
  // BẪY ĐÃ ĐO: bấm nút disabled thì KHÔNG có gì xảy ra nhưng vẫn "thành công" giả.
  if (gui.disabled || gui.getAttribute('aria-disabled') === 'true') {
    return {ok:false, reason:'nút Gửi đang BỊ KHOÁ (tệp chưa xử lý xong?)'};
  }
  gui.click();
  try { f.requestSubmit(); } catch (e) {}
  return {ok:true};
}"""


JS_ATTACH_STATE = r"""(nm) => {
  const chips = [...document.querySelectorAll('button[aria-label*="Remove"]')];
  const chip = chips.some(c => (c.getAttribute('aria-label')||'').includes(nm));
  const f = document.querySelector('form');
  const bs = f ? [...f.querySelectorAll('button')] : [];
  const send = bs.find(b => (b.getAttribute('aria-label')||'').toLowerCase() === 'send')
            || bs.find(b => b.type === 'submit');
  // nút gửi CHỈ dùng được khi tồn tại và không bị disabled/aria-disabled
  const sendUsable = !!send && !send.disabled &&
                     send.getAttribute('aria-disabled') !== 'true' &&
                     !send.hasAttribute('data-disabled');
  return {chip, sendUsable, sendFound: !!send,
          sendDisabled: send ? (send.disabled || send.getAttribute('aria-disabled')==='true') : null};
}"""


class ChatSession:
    """Gửi/nhận nhiều lượt trên MỘT transport (một tab)."""

    def __init__(self, tp, *, stable_sec: int = 3, timeout_sec: int = 900,
                 poll_sec: int = 2, log=print):
        self.tp = tp
        self.stable = stable_sec
        self.timeout = timeout_sec
        self.poll = poll_sec
        self.log = log
        self.sent_count = 0

    # ------------------------------------------------------------ helpers
    def state(self) -> dict:
        return self.tp.page.evaluate(JS_STATE)

    def _wait_idle(self, timeout: int = 180) -> None:
        """Chờ KHÔNG còn nút Stop trước khi gõ tin mới (gõ khi bận thì chữ bị nuốt)."""
        page = self.tp.page
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not page.evaluate(JS_STATE).get("streaming"):
                return
            page.wait_for_timeout(1000)
        raise TransportError("E_STILL_STREAMING", "ChatGPT vẫn đang trả lời sau timeout")

    def _type(self, text: str) -> None:
        """Gõ tin vào ô soạn — PHẢI xoá sạch bản nháp cũ trước.

        BẪY ĐÃ ĐO (2026-10-25): chatgpt.com khôi phục BẢN NHÁP cũ vào ô soạn
        (đo được 3326 ký tự tồn đọng). Khi có bản nháp, `keyboard.insert_text`
        KHÔNG chèn được gì — độ dài không đổi, nút Send vẫn bấm được nhưng gửi
        đi bản nháp cũ, và số lượt KHÔNG tăng => mọi lần gửi đều thất bại âm thầm.
        Xoá bằng Ctrl+A rồi Delete (đo: còn lại len=1 là dấu \n của contenteditable).
        """
        page = self.tp.page
        composer = page.query_selector('div[aria-label="Ask ChatGPT"][contenteditable="true"]')
        if not composer:
            raise TransportError("E_NO_COMPOSER", "không thấy ô soạn tin")

        # 1) xoá sạch bản nháp
        composer.click()
        page.wait_for_timeout(200)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.wait_for_timeout(300)
        leftover = page.evaluate(JS_COMPOSER_LEN)
        if leftover > 2:  # 1 = dấu \n còn lại của contenteditable
            page.evaluate("""() => {
              const c = document.querySelector('div[aria-label="Ask ChatGPT"][contenteditable="true"]');
              c.focus();
              document.execCommand('selectAll', false, null);
              document.execCommand('delete', false, null);
            }""")
            page.wait_for_timeout(300)
            leftover = page.evaluate(JS_COMPOSER_LEN)
            if leftover > 2:
                raise TransportError(
                    "E_COMPOSER_DIRTY",
                    f"ô soạn còn {leftover} ký tự bản nháp cũ, không xoá được")

        # 2) gõ nội dung thật, rồi ĐO lại cho chắc
        composer.click()
        page.keyboard.insert_text(text)
        page.wait_for_timeout(500)
        got = page.evaluate(JS_COMPOSER_LEN)
        want = len(text.replace("\n", ""))
        # sai lệch >5% => coi như gõ hỏng, không gửi rác đi
        if got < want * 0.95:
            raise TransportError(
                "E_TYPE_INCOMPLETE",
                f"ô soạn chỉ có {got}/{want} ký tự sau khi gõ — không gửi")

    def _attach(self, path: Path) -> None:
        """Đính kèm tệp và CHỜ tới khi nút Send BẬT thật.

        Bẫy đã đo (probe_attach.py, 2026-10-25): chip "Remove <tên>" xuất hiện ở
        giây thứ 1, nhưng nút Send chỉ BẬT ở giây thứ 4 (ChatGPT còn xử lý tệp).
        Bản đầu chỉ chờ chip => bấm Send khi nút còn disabled => không gửi được,
        số lượt không tăng => E_SEND_UNCERTAIN. Phải chờ ĐIỀU KIỆN GỬI ĐƯỢC.
        """
        page = self.tp.page
        inp = page.locator('form input[type="file"]').first
        inp.set_input_files(str(path))
        deadline = time.time() + 180
        seen_chip = False
        while time.time() < deadline:
            st = page.evaluate(JS_ATTACH_STATE, path.name)
            seen_chip = seen_chip or st["chip"]
            # đủ điều kiện: có chip VÀ nút send không bị khoá
            if st["chip"] and st["sendUsable"]:
                page.wait_for_timeout(400)   # nhịp ổn định
                return
            page.wait_for_timeout(700)
        if not seen_chip:
            raise TransportError("E_ATTACH_TIMEOUT", f"file {path.name} không đính được sau 180s")
        raise TransportError(
            "E_ATTACH_NOT_READY",
            f"file {path.name} đã đính nhưng nút Send vẫn bị khoá sau 180s "
            f"(ChatGPT chưa xử lý xong tệp)")

    # ------------------------------------------------------------ send
    def send(self, text: str, *, attach: Path | None = None, wait: bool = True,
             spill_dir: Path | None = None) -> dict:
        """Gửi 1 tin. Tin dài tự động chuyển thành FILE ĐÍNH KÈM (protocol §B).

        Bẫy đã đo (run 20261025_214840): báo cáo 3062 ký tự > ngưỡng 3000 làm
        cả vòng lặp chết giữa đường. Theo giao thức, nội dung lớn phải nằm ở
        file + manifest SHA256, chỉ tóm tắt vào tin nhắn. Ở đây làm TỰ ĐỘNG:
        ghi file, đính kèm, và gửi phần đầu ngắn làm con trỏ.
        """
        page = self.tp.page
        self._wait_idle()

        before = self.state()
        if before.get("captcha"):
            raise TransportError("E_CAPTCHA", "yêu cầu xác minh người dùng — DỪNG, không vượt")
        if not before.get("hasComposer"):
            raise TransportError("E_NO_COMPOSER", "không thấy ô soạn tin")

        if len(text) > MAX_INLINE_CHARS and attach is None:
            # tự động chuyển sang file đính kèm thay vì bỏ cả vòng lặp
            d = Path(spill_dir or (Path(__file__).parent / "jobs" / "_spill"))
            d.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            f = d / f"message_{stamp}_{len(text)}.md"
            f.write_text(text, encoding="utf-8")
            head = "\n".join(text.splitlines()[:14])[:900]
            orig_len = len(text)
            text = (f"Nội dung đầy đủ ở tệp đính kèm `{f.name}` "
                    f"({orig_len} ký tự). Phần đầu để tham chiếu:\n\n{head}\n\n"
                    f"[…xem toàn văn trong tệp đính kèm…]")
            attach = f
            self.log(f"[spill] tin {orig_len} ký tự -> đính kèm {f.name}")

        if attach is not None:
            self._attach(attach)

        self._type(text)
        page.wait_for_timeout(500)

        n = page.evaluate(JS_COMPOSER_LEN)
        if n <= 0:
            raise TransportError("E_TYPE_FAILED", f"ô soạn tin rỗng sau khi gõ (len={n})")

        res = page.evaluate(JS_SEND)
        if not res.get("ok"):
            raise TransportError("E_SEND_FAILED", str(res.get("reason")))
        self.sent_count += 1

        # Xác nhận tin ĐÃ ĐI. KHÔNG dựa riêng vào turnCount: đo thật (probe_turncount,
        # 2026-10-25) cho thấy ChatGPT ảo hoá DOM nên số [data-turn-key] có lúc GIẢM
        # (5 -> 4) dù tin đã gửi. Xác nhận khi BẤT KỲ tín hiệu nào sau đây đúng:
        #   (a) turnCount tăng, (b) lượt cuối có data-turn-key MỚI, (c) ô soạn tin rỗng.
        t0 = time.time()
        confirmed_by = ""
        while time.time() - t0 < 90:
            st = self.state()
            if st.get("turnCount", 0) > before.get("turnCount", 0):
                confirmed_by = "turnCount tăng"
                break
            if st.get("lastKey") and st.get("lastKey") != before.get("lastKey"):
                confirmed_by = "data-turn-key mới"
                break
            if st.get("composerLen") == 0:
                confirmed_by = "ô soạn tin đã rỗng"
                break
            page.wait_for_timeout(1000)
        else:
            st = self.state()
            raise TransportError(
                "E_SEND_UNCERTAIN",
                f"gửi rồi mà không xác nhận được trong 90s "
                f"(turns {before.get('turnCount')}->{st.get('turnCount')}, "
                f"lastKey {before.get('lastKey')}->{st.get('lastKey')}, "
                f"composerLen={st.get('composerLen')})")
        self.log(f"[send] xác nhận đã gửi: {confirmed_by}")

        if not wait:
            return {"sent": True, "turns_before": before.get("turnCount"),
                    "confirmed_by": confirmed_by, "reply": None}
        return {"sent": True, "turns_before": before.get("turnCount"),
                "confirmed_by": confirmed_by, "reply": self.wait_reply()}

    # ------------------------------------------------------------ receive
    def all_turns(self) -> list[dict]:
        """Đọc MỌI lượt trong hội thoại — [{text, blocks, isUser}].

        BẪY ĐÃ ĐO (job mg-v2-body-04, 26/10/2026): `wait_reply()` chỉ đọc lượt CUỐI.
        GPT-6 viết Body ở lượt 1 (72.831 ký tự JSON cảnh), rồi các lượt sau chỉ là
        nhận xét/kiểm tra của nó. Đọc lượt cuối -> mất nguyên bài Body, và executor
        NHAN_BODY báo "GPT-6 chưa giao Body" dù Body ĐÃ có trong hội thoại.
        Hàm này để nơi gọi tự tìm nội dung ở bất kỳ lượt nào.
        """
        return self.tp.page.evaluate(JS_ALL_TURNS)

    def find_turn(self, chua: str = '"canh"', min_len: int = 3000) -> dict | None:
        """Lượt DÀI NHẤT có chứa `chua` (mặc định: khối JSON cảnh). None nếu không có.

        Dùng cho NHAN_BODY: nội dung sáng tạo nằm ở lượt nó viết, không phải lượt
        tổng kết cuối cùng.
        """
        ung = [t for t in self.all_turns()
               if chua in (t.get("text") or "") and len(t.get("text") or "") >= min_len]
        return max(ung, key=lambda t: len(t["text"])) if ung else None

    def wait_reply(self) -> dict:
        """Chờ câu trả lời HOÀN CHỈNH: text đứng yên + KHÔNG còn nút Stop."""
        page = self.tp.page
        t0 = time.time()
        last_text, stable_since = None, None
        saw_stop = False

        while time.time() - t0 < self.timeout:
            st = page.evaluate(JS_STATE)
            if st.get("captcha"):
                raise TransportError("E_CAPTCHA", "CAPTCHA giữa lúc chờ — DỪNG")
            if st.get("streaming"):
                saw_stop = True
                last_text, stable_since = None, None
                page.wait_for_timeout(self.poll)
                continue

            rep = page.evaluate(JS_LAST_REPLY)
            txt = (rep.get("text") or "") if rep.get("ok") else ""

            if txt and txt == last_text:
                if stable_since is None:
                    stable_since = time.time()
                if time.time() - stable_since >= self.stable:
                    return {"text": txt, "len": len(txt),
                            "blocks": rep.get("blocks", 0),
                            "turn_key": rep.get("turnKey"),
                            "elapsed_sec": int(time.time() - t0),
                            "saw_stop": saw_stop}
            else:
                last_text, stable_since = txt, None
            page.wait_for_timeout(self.poll)

        raise TransportError(
            "E_REPLY_TIMEOUT",
            f"không nhận được câu trả lời hoàn chỉnh sau {self.timeout}s "
            f"(text cuối {len(last_text or '')} ký tự)")
