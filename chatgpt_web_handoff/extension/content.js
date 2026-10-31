// content.js — chạy trong tab chatgpt.com.
// NHIỆM VỤ DUY NHẤT: nhận diện (a) đang ở đúng cuộc hội thoại đã cấu hình,
// (b) người dùng vừa bấm gửi. Rồi BÁO về background.
//
// KHÔNG đọc nội dung tin nhắn, KHÔNG đọc câu trả lời, KHÔNG chèn/gửi prompt,
// KHÔNG lấy cookie/token. Chỉ đọc location.pathname và đếm số lần click chuột
// vào nút gửi (không lấy giá trị ô nhập).

(async function () {
  const { conversationUrl } = await chrome.storage.local.get({ conversationUrl: '' });
  if (!conversationUrl) return;

  const norm = (p) => (p || '').replace(/\/+$/, '');
  let expected;
  try {
    expected = norm(new URL(conversationUrl).pathname);
  } catch {
    return;
  }

  let reportedOpen = false;
  let activeJobId = null;

  async function currentJobId() {
    if (activeJobId) return activeJobId;
    try {
      const r = await chrome.runtime.sendMessage({ type: 'jobs' });
      const jobs = (r && r.jobs) || [];
      const waiting = jobs.find((j) => ['QUEUED', 'OPENED', 'SENT'].includes(j.state));
      activeJobId = waiting ? waiting.job_id : null;
    } catch {
      activeJobId = null;
    }
    return activeJobId;
  }

  function inRightConversation() {
    return norm(location.pathname) === expected;
  }

  // (a) báo "đã mở đúng chat" — một lần cho mỗi lần điều hướng
  async function reportOpened() {
    if (reportedOpen || !inRightConversation()) return;
    const id = await currentJobId();
    if (!id) return;
    try {
      await chrome.runtime.sendMessage({ type: 'event', kind: 'opened', jobId: id });
      reportedOpen = true;
      showBadge('GPT_BRIDGE: đã nhận diện đúng cuộc hội thoại');
    } catch { /* im lặng */ }
  }

  // (b) báo "người dùng đã gửi" — chỉ dựa trên click nút gửi, không đọc ô nhập
  document.addEventListener('click', async (ev) => {
    const btn = ev.target && ev.target.closest && ev.target.closest(
      'button[data-testid="send-button"], button[aria-label*="Send"], button[aria-label*="Gửi"]'
    );
    if (!btn || !inRightConversation()) return;
    const id = await currentJobId();
    if (!id) return;
    try {
      await chrome.runtime.sendMessage({ type: 'event', kind: 'sent', jobId: id });
      showBadge('GPT_BRIDGE: đã ghi nhận bạn gửi yêu cầu');
    } catch { /* im lặng */ }
  }, true);

  function showBadge(text) {
    const el = document.createElement('div');
    el.textContent = text;
    el.style.cssText = [
      'position:fixed', 'bottom:16px', 'left:16px', 'z-index:2147483647',
      'background:#111', 'color:#7CFC9B', 'font:13px/1.4 system-ui,sans-serif',
      'padding:8px 12px', 'border-radius:8px', 'box-shadow:0 4px 16px rgba(0,0,0,.35)',
      'pointer-events:none', 'opacity:.95'
    ].join(';');
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  // ChatGPT là SPA: điều hướng không tải lại trang
  let lastPath = location.pathname;
  setInterval(() => {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      reportedOpen = false;
      activeJobId = null;
      reportOpened();
    }
  }, 1000);

  reportOpened();
})();
