# AGENT LOOP — TRẢ LỜI 7 CÂU HỎI §G

> Cập nhật 2026-10-26. Mọi số liệu dưới đây đo bằng chạy thật trên máy này,
> không suy đoán. Đường dẫn bằng chứng ghi kèm từng mục.

---

## 1. Khả năng gửi/nhận tự động thật

**CÓ, đã đo nhiều lần.** Kênh: Chrome riêng (`E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE`)
+ CDP cổng **9335** qua Playwright. Không dùng cookie/token, không né CAPTCHA,
không pyautogui, không API trả phí.

- Gửi: xác nhận bằng **3 tín hiệu độc lập** (turnCount tăng HOẶC `data-turn-key`
  mới HOẶC ô soạn rỗng) — không tin riêng turnCount vì ChatGPT ảo hoá DOM.
- Nhận: chờ nút Stop biến mất **và** độ dài ổn định 4 s (PITFALL #0).
- Đọc bài: gom mọi khối markdown của tin assistant cuối; bóc `innerText` của `<button>`.

Bằng chứng: `agent_loop/test_runs/20261026_000729/` (4 round liên tiếp, 4 SHA
báo cáo khác nhau) · `agent_loop/jobs/dedup-v3/` (EXIT=0, 4 SHA khác nhau).

## 2. Duy trì hội thoại qua nhiều round

**CÓ.** Giữ nguyên URL `/c/<id>` suốt 4–5 lượt; đo được `same_conversation=True`
ở từng round trong `evidence.json`. GPT-6 nhớ nội dung các lượt trước (đã kiểm
bằng cách hỏi lại mã `AAA-111` ở lượt 5 — trả lời đúng).

Lưu ý: hội thoại "cóng" sau ~5–6 lượt (PITFALL #4) → phát hiện và mở chat mới.

## 3. Kênh giao tiếp

**Chrome + CDP (Playwright) trên cổng 9335.** Không dùng Google Drive.

- Extension hiện có **chỉ để xem báo cáo** (report-only), KHÔNG phải kênh tự động.
  Không được lấy việc extension hiển thị prompt làm bằng chứng hai chiều.
- File lớn: tự đẩy sang tệp đính kèm khi > 3000 ký tự (`MAX_INLINE_CHARS`),
  kèm con trỏ trong tin nhắn. Manifest SHA256 cho tệp lớn.

## 4. Chỗ còn cần thao tác người dùng

| Việc | Có cần người? | Ghi chú |
|---|---|---|
| Giao nhiệm vụ ban đầu | Không | `cli new --job ... --goal ...` |
| Duyệt Hook / duyệt nội dung | **CÓ** | Điểm duyệt duy nhất theo luật cứng #5 (trừ lane X) |
| Đăng nhập ChatGPT lần đầu | CÓ (1 lần) | Phiên lưu trong profile; sau đó tự chạy |
| Gặp CAPTCHA | **CÓ** | Hệ dừng và báo `E_CAPTCHA`, KHÔNG né |
| **File GPT-6 gửi VỀ Hermes** | **CHƯA ĐO** | Chiều ra đã chứng minh (`ZEBRA-42`); chiều vào chưa có bài test |
| Duyệt cuối cùng | CÓ | Anh đọc báo cáo cuối |

## 5. Mức độ đáp ứng mục tiêu "không copy/paste"

**Đạt cho vòng lặp điều phối.** 4 round liên tiếp chạy hoàn toàn tự động:
GPT-6 giao việc → Hermes thực thi trên máy → gửi báo cáo về đúng hội thoại →
GPT-6 đọc và giao tiếp → Hermes tiếp tục. Không có bước copy/paste nào.

**Chưa đạt ở phần "GPT-6 kết luận hoàn thành"** — nhưng đó là vì GPT-6 đánh giá
công việc MG_STUDIO chưa xong, **không phải** vì cơ chế truyền nhận hỏng.
Đây là khác biệt quan trọng: cơ chế chạy đúng, nội dung bị chấm chưa đạt.

**Một lỗi thật của phía Hermes (ĐÃ sửa):** round 3 GPT-6 giao *"tích hợp
HOOK_LOCK vào entry point `mg.py`"*, nhưng `mg_implement.run()` không đọc tham
số `instruction` — nó luôn kiểm 3 tệp cố định và trả cùng một báo cáo. Vì vậy
vòng lặp **truyền nhận đúng nhưng thực thi không bám chỉ thị** ở riêng nhánh
IMPLEMENT. Nay executor dò dấu hiệu sửa tệp sản xuất và trả `BLOCKED` + điểm
nghẽn thay vì báo cáo thành công giả — trung thực, nhưng **nối guard vào `mg.py`
vẫn là việc chưa làm**, cần một job riêng có quyền sửa tệp sản xuất.

## 6. Kết quả kiểm thử thực tế

### Kiểm thử tự động (offline)

| Bộ | Kết quả |
|---|---|
| `pytest tests/` toàn dự án | **297 tests — 294 passed, 0 failed, 3 skipped** |
| `pytest tests/test_agent_loop.py` | **47/47** |
| `cli self-test` | exit 0, 6/6 |
| `verify_parse_directive.py` | **10 PASS / 0 FAIL** (có 1 reply THẬT của GPT-6) |
| `verify_dedup.py` | 6/6 + 4 bộ đếm thật (11 submitted / 7 accepted / 4 rejected) |
| `verify_integration_lane_x.py` | **18 PASS / 0 FAIL** |
| `MG_STUDIO tests/test_khoa_hook_qc.py` | 22/22 |

### Kiểm thử sống (live, 4 round thật)

Run `agent_loop/test_runs/20261026_000729`:

| Round | Action | SHA báo cáo | Evidence |
|---|---|---|---|
| 1 | SURVEY | `bccc842be22d` | 18 |
| 2 | TEST (unit) | `fcad3bb245d1` | 24 |
| 3 | IMPLEMENT | `d731f87225b9` | 7 |
| 4 | TEST (tích hợp) | `fc23ad486536` | 24 |

- **4 SHA khác nhau** → executor thực sự đọc chỉ thị, không lặp bài cố định.
- `focus_not_stolen = True` (so HWND, mốc sau `new_conversation()`).
- `outcome = GPT6_BLOCKED`, `rounds = 4` (đếm đúng, không có round ma).
- GPT-6 chấp nhận 18 PASS là thật, nhưng chỉ ra giới hạn: *"chỉ chứng minh hai
  module hoạt động khi được gọi trực tiếp… chưa cung cấp bằng chứng gọi qua
  entry point thực tế"* → đúng, chưa tích hợp vào `mg.py`.

## 7. Bước tiếp theo cho MG_STUDIO

Theo đúng yêu cầu GPT-6 ở round 4 (cần **job mới**, không mở round 5):

1. **Tích hợp 2 guard vào entry point `mg.py`** — để bảo vệ chạy TỰ ĐỘNG, không
   chỉ khi gọi tay. Đây là điều GPT-6 nói thiếu.
2. **Kiểm thử end-to-end một tập lane X có kiểm soát**: khoá hook → chạy pipeline
   → xác nhận guard chặn được sửa hook và chặn vượt QC **qua chính `mg.py`**.
3. **FFprobe xác minh `final.mp4`** trước khi báo hoàn thành (hiện chưa có bài test).
4. **Đo chiều file GPT-6 → Hermes** (hiện chưa đo, phải nói rõ là chưa biết).
5. Chỉ sau 4 việc trên mới được tuyên bố "AGENT LOOP hoàn toàn tự động end-to-end".

---

## Giới hạn phải nói rõ (không tô hồng)

- **Chiều file GPT-6 → Hermes chưa đo.** Chiều ra đã chứng minh.
- **`SetForegroundWindow` từ tiến trình nền bị Windows chặn** — không thể "trả
  focus" bằng API. Đo được: `open()` (connect_over_cdp) có kéo cửa sổ Chrome lên
  foreground, nhưng `new_conversation()` trả lại ngay; giai đoạn gửi/nhận **không**
  cướp focus.
- **Chưa có tích hợp vào `mg.py`** → guard mới bảo vệ khi được gọi, chưa bảo vệ
  tự động trong pipeline sản xuất.
- **GPT-6 chưa từng kết luận COMPLETED** cho MG_STUDIO trong bất kỳ job nào.
