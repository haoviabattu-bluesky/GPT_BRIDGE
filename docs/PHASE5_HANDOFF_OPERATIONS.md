# Phase 5 — Bàn giao cho ChatGPT Web (module `chatgpt_web_handoff`)

Tài liệu vận hành. Đọc hết phần **Giới hạn thật** trước khi tin bất cứ dòng nào ở trên.

---

## 1. Quyết định của chủ dự án (đã chốt)

- **DỪNG** hướng Codex CLI làm writer. **Không** dùng `gpt-6.1-sol`, **không** tiêu quota Sol.
- Writer của Phase 5 là **ChatGPT Web, trong CHÍNH cuộc hội thoại đã chỉ định**
  (`conversation_url`) — để dùng ngữ cảnh đã thiết lập, Memory và Google Drive connector
  của tài khoản hiện tại.
- Giữ nguyên Phase 1–4: Drive scanner, Sheet parser, ZIP QC, output collector, uploader.
- Module `chatgpt_web_handoff` là module **độc lập**; Phase 1–4 không bị viết lại.

Phase 5 **không** thêm Codex CLI, **không** thêm Sol, **không** thêm API writer.

---

## 2. Kiến trúc

```
Hermes (GPT_BRIDGE)                    Người dùng                    ChatGPT Web
──────────────────                     ──────────                    ───────────
adapter.submit(job)
  -> dựng prompt đầy đủ
  -> ghi jobs/_handoff/<id>.json
        │
        ├── server 127.0.0.1:8765 ──► Extension popup
        │      (token bắt buộc)        - hiện danh sách job
        │                              - hiện prompt
        │                              - "Mở đúng chat" ──────────► mở ĐÚNG /c/<id>
        │                              - người dùng dán + bấm gửi ─► ChatGPT viết
        │                              - "Tôi đã gửi" ──► state SENT
        │                                                            │
        │                              người dùng bấm tải ZIP ◄──────┘
        │                                   │
        │                                   ▼
        └── watcher quét receive_dir ──► QC cấu trúc ──► state DOWNLOADED
                                            │
                                            └──► output_collector (Phase 1–4) ──► uploader
```

### Vì sao Chrome Extension, không phải Playwright làm trung tâm

| Tiêu chí | Chrome Extension | Playwright/CDP |
|---|---|---|
| Giữ đúng chat hiện tại | Có | Có |
| Dùng phiên đăng nhập sẵn | Có, không cần cấu hình | Cần trỏ vào profile |
| Không chiếm chuột/bàn phím | Có | Có (nhưng dễ vô tình) |
| Không phải mở Chrome riêng | Có | Thường phải mở với `--remote-debugging-port` |
| Gắn với Hermes local | localhost HTTP | tiến trình Python |
| Rủi ro vi phạm điều khoản | Thấp (chỉ mở tab, không đọc/ghi nội dung) | Cao hơn (điều khiển DOM, gửi hộ) |
| Độ ổn định | Phụ thuộc giao diện web | Phụ thuộc giao diện web |

Extension **không** đọc nội dung hội thoại, **không** lấy cookie/token, **không** gọi
`/backend-api/`, **không** chèn hay gửi prompt. Nó chỉ: mở tab, đọc `location.pathname`,
đếm click nút gửi, và gọi localhost.

---

## 3. Cài đặt (làm một lần)

### 3.1 Cấu hình

Sửa `config.yaml`:

```yaml
writer:
  adapter: chatgpt_web_handoff          # bật Phase 5
  chatgpt_web_handoff:
    conversation_url: "https://chatgpt.com/c/<id-cuoc-hoi-thoai>"
    receive_dir: "C:/Users/<ban>/Downloads"
    server_host: "127.0.0.1"            # CHỈ loopback, không đổi
    server_port: 8765
    token: ""                            # để trống -> `serve` tự sinh và in ra
    poll_sec: 3
    settle_sec: 1.5
```

`conversation_url` là **bắt buộc**. Thiếu nó, adapter **từ chối nhận job** và nói rõ
lý do — đúng yêu cầu "không tự mở chat mới".

### 3.2 Nạp extension

1. Mở `chrome://extensions`
2. Bật **Developer mode**
3. **Load unpacked** → chọn `chatgpt_web_handoff/extension`

Extension ID sẽ hiện ra. (ID thay đổi theo đường dẫn tuyệt đối của thư mục.)

### 3.3 Nối extension với Hermes

```bash
PYTHONPATH= .venv/Scripts/python.exe -m chatgpt_web_handoff.cli serve
```

Lần đầu, server sinh token và in ra. Dán token + URL vào **Options** của extension
(chuột phải icon extension → Options), cùng `conversation_url`.

Kiểm tra: bấm icon extension → phải thấy "N job · M đang chờ bạn thao tác".

---

## 4. Vận hành một job

```bash
# 1. kiểm tra môi trường
PYTHONPATH= .venv/Scripts/python.exe -m chatgpt_web_handoff.cli doctor

# 2. xếp job (in ra prompt đầy đủ)
PYTHONPATH= .venv/Scripts/python.exe -m chatgpt_web_handoff.cli queue --job job.json

# 3. bật watcher (cửa sổ riêng)
PYTHONPATH= .venv/Scripts/python.exe -m chatgpt_web_handoff.cli watch
```

Sau đó, **thao tác tay**:

1. Bấm icon extension → chọn job → **Xem prompt**
2. **Mở đúng chat** (mở `conversation_url`, không mở chat mới)
3. **Sao chép prompt** → dán vào ô chat → bấm gửi
4. Bấm **Tôi đã gửi** trong extension (hoặc để `content.js` tự nhận diện)
5. Chờ ChatGPT viết xong → bấm nút tải xuống của ChatGPT để lấy ZIP
6. Watcher tự phát hiện ZIP → QC → chuyển vào `jobs/_handoff/<job>/` → state `DOWNLOADED`

Prompt bàn giao gồm đúng 4 phần theo yêu cầu:

- **job ID**
- **đường dẫn ZIP hồ sơ trên Google Drive**
- **yêu cầu đọc hồ sơ trước khi viết**
- **định dạng ZIP kết quả**: `SCRIPT_FINAL_JP.txt`, `SCRIPT_QC_REPORT.md`,
  `SCRIPT_ISSUES.md`, `SCRIPT_STRUCTURE.md`, để **ngay ở gốc ZIP**

---

## 5. Trạng thái job

| State | Nghĩa |
|---|---|
| `QUEUED` | Hermes đã dựng prompt, chờ người dùng thao tác |
| `OPENED` | Extension xác nhận đã mở đúng `conversation_url` |
| `SENT` | Người dùng đã gửi prompt vào ChatGPT |
| `DOWNLOADED` | Watcher đã nhận ZIP và **QC cấu trúc đạt** |
| `FAILED` | ZIP nhận được không đạt QC (xem `last_error`) |

`FAILED` **không** lọt sang uploader: `collect_result()` trả `zip_path=None`.

---

## 6. Kiểm chứng (đã chạy thật, không mock)

| Bộ | Lệnh | Kết quả |
|---|---|---|
| Unit test Phase 5 + Phase 1–4 | `pytest` | **136 passed, 3 skipped** |
| Logic extension (chrome API giả) | `node scratch_analysis/test_extension.js` | **28/28** |
| E2E luồng dữ liệu | `python scratch_analysis/e2e_phase5.py` | **34/34** |
| Extension THẬT + server THẬT | `python scratch_analysis/e2e_extension_live.py` | **12/12** |
| Chrome nạp extension MV3 | `python scratch_analysis/verify_extension_loads.py` | **8/8** |

`e2e_extension_live.py` chứng minh bằng bằng chứng không thể giả: extension tự ghi
`chrome.storage.local.lastOpened.url`, test đọc lại và so với `conversation_url`.

---

## 7. Giới hạn thật — ĐỌC KỸ

### 7.1 Thao tác thủ công còn lại: **5 bước cho mỗi job**

1. Chọn job trong extension
2. Bấm "Mở đúng chat"
3. Sao chép prompt → dán → bấm gửi trong ChatGPT
4. Bấm "Tôi đã gửi"
5. Bấm tải ZIP trong ChatGPT

Không thể giảm xuống 0 mà không vi phạm điều khoản OpenAI. Bước 3 và 5 **bắt buộc**
là thao tác người dùng — đó là ranh giới chủ dự án đã đặt.

### 7.2 Chức năng CHƯA đạt / chưa kiểm chứng được

| Hạng mục | Trạng thái |
|---|---|
| Mở đúng `conversation_url` | **Đã chứng minh** — extension ghi `lastOpened.url` khớp tuyệt đối |
| Giữ đúng cuộc hội thoại khi ChatGPT redirect | **Phụ thuộc đăng nhập.** Profile chưa đăng nhập → ChatGPT redirect `/c/<id>` về trang chủ. Đã đối chứng: `goto` thẳng cũng bị redirect y hệt ⇒ không phải lỗi extension |
| ChatGPT đọc ZIP Drive bằng connector | **CHƯA kiểm chứng** — cần tài khoản thật, thao tác tay (Bước 2) |
| GPT-6 viết & xuất ZIP thật theo hồ sơ Fabel | **CHƯA kiểm chứng** — cần tài khoản thật (Bước 3) |
| Watcher nhận ZIP + QC | **Đã chứng minh** — 34/34, gồm cả ca ZIP lỗi bị chặn |
| Upload Drive OUTPUT | **Chưa chạy trong Phase 5** — dùng uploader Phase 1–4 nguyên trạng, chưa nối đầu-cuối với ZIP thật |
| Nhận diện nút gửi của ChatGPT | **Có thể vỡ** khi ChatGPT đổi giao diện → đã có nút "Tôi đã gửi" dự phòng |
| Chạy trên Chrome chính của chủ dự án | **CHƯA làm** — Chrome stable 137+ chặn `--load-extension`; phải nạp unpacked bằng tay ở `chrome://extensions` |

### 7.3 Ranh giới tuân thủ (đã giữ)

- Không lấy cookie, không lấy token ChatGPT
- Không vượt CAPTCHA
- Không gọi `/backend-api/` (test L10 xác nhận **0 request**)
- Không đọc nội dung tin nhắn/câu trả lời (test tĩnh xác nhận content.js không chạm
  `document.cookie`, `localStorage`, `.value`, `execCommand`)
- Không tự gửi prompt, không tự tải file
- Server chỉ bind `127.0.0.1`, bắt buộc token
- Không đụng Chrome của chủ dự án (cổng 9350 / `TUAN_PROFILE`) — mọi test dùng
  Chromium riêng của Playwright, profile tạm, cửa sổ ngoài màn hình

### 7.4 Bước 5 — phương thức tích hợp chính thức

**Chưa có.** OpenAI không cung cấp API để một extension bên thứ ba gửi prompt vào
một cuộc hội thoại web hiện có. `autowebprompt` (Playwright/CDP) làm được về mặt kỹ
thuật nhưng **không phải tích hợp được OpenAI hỗ trợ chính thức**, và đi ngược ranh
giới "không tự động gửi" ở mục 9–10 của yêu cầu.

Đường chính thức duy nhất để giảm thao tác là **API** (Assistants/Responses) — nhưng
API **không** dùng được Memory và Google Drive connector của tài khoản ChatGPT web,
tức là mất đúng lý do chủ dự án chọn ChatGPT Web. Vì vậy Phase 5 dừng ở mô hình
"người dùng bấm gửi" và ghi rõ 5 thao tác thủ công.

---

## 8. Cấu trúc module

```
chatgpt_web_handoff/
├── __init__.py     mô tả phạm vi + export
├── adapter.py      GPTWriterAdapter (supports_auto=False)
├── prompt.py       dựng prompt bàn giao đầy đủ
├── store.py        hàng đợi job (JSON, ghi nguyên tử, chặn path traversal)
├── watcher.py      phát hiện ZIP + QC cấu trúc
├── server.py       HTTP localhost cho extension (token bắt buộc, chỉ loopback)
├── cli.py          doctor / queue / list / qc / watch / serve
└── extension/      Chrome Extension Manifest V3
    ├── manifest.json
    ├── background.js   service worker: gọi localhost, mở đúng chat
    ├── content.js      chỉ đọc location.pathname + đếm click nút gửi
    ├── popup.html/js   hiện job, prompt, nút mở chat / đã gửi / sao chép
    └── options.html/js lưu serverUrl + token + conversationUrl
```

---

## 9. Xử lý sự cố

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `doctor` báo `conversation_url` trống | chưa cấu hình | điền `writer.chatgpt_web_handoff.conversation_url` |
| Popup "Không kết nối được Hermes" | server chưa chạy / sai port | chạy `cli serve`, kiểm tra Options |
| Popup báo 401 | token sai | chép lại token từ output `cli serve` |
| Mở chat nhưng về trang chủ ChatGPT | **chưa đăng nhập** ChatGPT trong profile đó | đăng nhập rồi thử lại |
| Watcher không thấy ZIP | ZIP tải vào thư mục khác | sửa `receive_dir` đúng thư mục Downloads của Chrome |
| ZIP bị `FAILED` | thiếu 1 trong 4 file, hoặc không UTF-8 | xem `last_error`, yêu cầu ChatGPT xuất lại |
| `Tôi đã gửi` không đổi state | server không chạy | kiểm tra `cli serve` |
