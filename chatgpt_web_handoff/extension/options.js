// options.js — lưu cấu hình extension (serverUrl, token, conversationUrl).

const DEFAULTS = { serverUrl: 'http://127.0.0.1:8765', token: '', conversationUrl: '' };
const $ = (id) => document.getElementById(id);

function msg(text, cls = '') {
  const el = $('msg');
  el.textContent = text;
  el.className = cls;
}

async function load() {
  const o = await chrome.storage.local.get(DEFAULTS);
  $('serverUrl').value = o.serverUrl || DEFAULTS.serverUrl;
  $('token').value = o.token || '';
  $('conversationUrl').value = o.conversationUrl || '';
}

function validUrl(u, { allowLocal = false } = {}) {
  try {
    const p = new URL(u);
    if (allowLocal) return p.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(p.hostname);
    return p.protocol === 'https:' && /(^|\.)(chatgpt\.com|chat\.openai\.com)$/.test(p.hostname);
  } catch {
    return false;
  }
}

$('save').onclick = async () => {
  const serverUrl = $('serverUrl').value.trim().replace(/\/+$/, '');
  const token = $('token').value.trim();
  const conversationUrl = $('conversationUrl').value.trim();
  if (!validUrl(serverUrl, { allowLocal: true })) {
    msg('Địa chỉ Hermes phải là http://127.0.0.1:<port> hoặc http://localhost:<port>', 'err');
    return;
  }
  if (!token) { msg('Thiếu token — lấy ở dòng Hermes in ra khi khởi động.', 'err'); return; }
  if (conversationUrl && !validUrl(conversationUrl)) {
    msg('URL hội thoại phải là https://chatgpt.com/c/... (không phải link Drive).', 'err');
    return;
  }
  await chrome.storage.local.set({ serverUrl, token, conversationUrl });
  msg('Đã lưu. Mở popup để tải job.', 'ok');
};

$('test').onclick = async () => {
  const serverUrl = $('serverUrl').value.trim().replace(/\/+$/, '') || DEFAULTS.serverUrl;
  try {
    const res = await fetch(`${serverUrl}/health`);
    const d = await res.json();
    if (d.ok) msg(`Kết nối OK · service ${d.service} v${d.version}`, 'ok');
    else msg('Server trả lỗi: ' + JSON.stringify(d), 'err');
  } catch (e) {
    msg('Không kết nối được: ' + (e.message || e), 'err');
  }
};

load();
