// background.js — cầu nối giữa extension và Hermes local (127.0.0.1).
// KHÔNG gửi prompt hộ, KHÔNG đọc nội dung hội thoại. Chỉ:
//   - lấy danh sách job / chi tiết job
//   - báo sự kiện người dùng đã mở đúng chat / đã bấm gửi
//   - mở tab đúng conversation_url khi người dùng bấm

const DEFAULTS = {
  serverUrl: 'http://127.0.0.1:8765',
  token: '',
  conversationUrl: ''
};

async function getOpts() {
  const o = await chrome.storage.local.get(DEFAULTS);
  return { ...DEFAULTS, ...o };
}

async function api(path, { method = 'GET', body = null } = {}) {
  const { serverUrl, token } = await getOpts();
  const res = await fetch(`${serverUrl}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Bridge-Token': token
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await res.json().catch(() => ({ ok: false, error: 'phản hồi không phải JSON' }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// So khớp URL hội thoại: chỉ so phần path, bỏ query/hash để không mở nhầm.
function sameConversation(a, b) {
  try {
    const ua = new URL(a), ub = new URL(b);
    const norm = (u) => u.pathname.replace(/\/+$/, '');
    return ua.host === ub.host && norm(ua) === norm(ub);
  } catch {
    return false;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      switch (msg.type) {
        case 'health':
          sendResponse(await api('/health'));
          break;
        case 'jobs':
          sendResponse(await api('/jobs'));
          break;
        case 'job':
          sendResponse(await api(`/job/${encodeURIComponent(msg.jobId)}`));
          break;
        case 'event': {
          sendResponse(await api(`/event/${msg.kind}`, {
            method: 'POST',
            body: { job_id: msg.jobId, detail: msg.detail || '' }
          }));
          break;
        }
        case 'openConversation': {
          const { conversationUrl } = await getOpts();
          const target = msg.url || conversationUrl;
          if (!target) throw new Error('chưa cấu hình conversation_url trong Options');
          const tabs = await chrome.tabs.query({});
          const hit = tabs.find((t) => t.url && sameConversation(t.url, target));
          // ghi lại URL ĐÃ YÊU CẦU MỞ (để người vận hành kiểm chứng được
          // extension mở đúng cuộc hội thoại, không phải chat mới)
          await chrome.storage.local.set({
            lastOpened: { url: target, at: Date.now(), reused: !!hit }
          });
          if (hit) {
            await chrome.tabs.update(hit.id, { active: true });
            if (hit.windowId != null) await chrome.windows.update(hit.windowId, { focused: true });
            sendResponse({ ok: true, reused: true, tabId: hit.id, url: target });
          } else {
            const tab = await chrome.tabs.create({ url: target, active: true });
            sendResponse({ ok: true, reused: false, tabId: tab.id, url: target });
          }
          break;
        }
        default:
          sendResponse({ ok: false, error: `unknown message: ${msg.type}` });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e.message || e) });
    }
  })();
  return true; // giữ kênh mở cho sendResponse bất đồng bộ
});
