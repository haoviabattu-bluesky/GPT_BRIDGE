// popup.js — hiển thị job, mở ĐÚNG cuộc hội thoại, sao chép prompt.
// Không tự gửi, không đọc nội dung chat.

const $ = (id) => document.getElementById(id);
let selected = null;

function setStatus(text, isErr = false) {
  const el = $('status');
  el.textContent = text;
  el.classList.toggle('err', !!isErr);
}

async function send(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) => resolve(r)));
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function jobCard(j) {
  const div = document.createElement('div');
  div.className = 'job' + (selected && selected.job_id === j.job_id ? ' active' : '');
  const lane = j.lane ? `lane ${esc(j.lane)}` : '';
  const ep = j.episode_id ? esc(j.episode_id) : '';
  div.innerHTML = `
    <div class="jid">${esc(j.job_id)}<span class="state ${esc(j.state)}">${esc(j.state)}</span></div>
    <div class="meta">${[lane, ep].filter(Boolean).join(' · ')}</div>
    <div class="acts">
      <button data-act="select">Xem prompt</button>
      <button data-act="open">Mở đúng chat</button>
    </div>`;
  div.querySelector('[data-act="select"]').onclick = () => selectJob(j.job_id);
  div.querySelector('[data-act="open"]').onclick = () => openConversation(j);
  return div;
}

async function loadJobs() {
  setStatus('đang tải job…');
  const r = await send({ type: 'jobs' });
  if (!r || !r.ok) {
    setStatus('Không kết nối được Hermes: ' + ((r && r.error) || 'lỗi'), true);
    $('jobs').innerHTML = '';
    return;
  }
  const jobs = r.jobs || [];
  $('jobs').innerHTML = '';
  if (!jobs.length) {
    setStatus('Không có job nào. Hermes sẽ đẩy job vào khi có hồ sơ.');
    return;
  }
  jobs.forEach((j) => $('jobs').appendChild(jobCard(j)));
  const waiting = jobs.filter((j) => ['QUEUED', 'OPENED', 'SENT'].includes(j.state));
  setStatus(`${jobs.length} job · ${waiting.length} đang chờ bạn thao tác`);
}

async function selectJob(jobId) {
  const r = await send({ type: 'job', jobId });
  if (!r || !r.ok) { setStatus('Không lấy được job: ' + ((r && r.error) || ''), true); return; }
  selected = r.job;
  $('prompt').value = r.job.prompt || '';
  await loadJobs();
}

async function openConversation(j) {
  const r = await send({ type: 'openConversation', url: j.conversation_url });
  if (!r || !r.ok) { setStatus('Không mở được: ' + ((r && r.error) || ''), true); return; }
  setStatus(r.reused ? 'Đã chuyển sang tab hội thoại đang mở.'
                     : 'Đã mở đúng cuộc hội thoại đã cấu hình.');
}

$('refresh').onclick = loadJobs;
$('opts').onclick = () => chrome.runtime.openOptionsPage();

// "Mở đúng chat": dùng conversation_url của job đang chọn, hoặc URL trong Options.
$('open').onclick = async () => {
  const url = (selected && selected.conversation_url) || '';
  const r = await send({ type: 'openConversation', url });
  if (!r || !r.ok) { setStatus('Không mở được: ' + ((r && r.error) || ''), true); return; }
  setStatus(r.reused ? 'Đã chuyển sang tab hội thoại đang mở.'
                     : 'Đã mở đúng cuộc hội thoại đã cấu hình.');
};

// "Tôi đã gửi": người dùng xác nhận thủ công (dự phòng khi content.js không
// nhận diện được nút gửi của ChatGPT). KHÔNG gửi gì thay người dùng.
$('sent').onclick = async () => {
  if (!selected) { setStatus('Chưa chọn job nào.', true); return; }
  const r = await send({ type: 'event', kind: 'sent', jobId: selected.job_id });
  if (!r || !r.ok) { setStatus('Không báo được: ' + ((r && r.error) || ''), true); return; }
  setStatus(`Đã ghi nhận bạn gửi job ${selected.job_id}. Chờ ZIP kết quả.`);
  await loadJobs();
};

$('copy').onclick = async () => {
  const v = $('prompt').value;
  if (!v) { setStatus('Chưa chọn job nào.', true); return; }
  try {
    await navigator.clipboard.writeText(v);
    setStatus('Đã sao chép prompt — dán vào ô chat rồi bấm gửi.');
  } catch (e) {
    $('prompt').select();
    setStatus('Không tự sao chép được, đã bôi đen: bấm Ctrl+C.', true);
  }
};

loadJobs();
