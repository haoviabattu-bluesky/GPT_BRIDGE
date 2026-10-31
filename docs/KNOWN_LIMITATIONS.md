# KNOWN_LIMITATIONS — GPT_BRIDGE V1.0

Danh sách này là **giới hạn thật**, không phải ghi chú cho có. Mỗi mục nêu rõ hệ quả.

## 1. Giới hạn ngăn tự động hoàn toàn

### L1 — Không có adapter ChatGPT đã kiểm chứng
Đây là giới hạn lớn nhất. V1 chạy MANUAL: người dán prompt, người tải ZIP, người thả vào
`jobs/_inbox/`. Adapter `chatgpt_web.py` đã viết nhưng **chưa chạy với phiên thật**, mặc định tắt.
**Hệ quả:** hệ thống KHÔNG tự động 100%. Xem `ADAPTER_FEASIBILITY.md`.

### L2 — Không kiểm chứng được ChatGPT có đọc được ZIP
Spec §11 cảnh báo: connector Google Drive có thể không tồn tại hoặc không đọc ZIP trong
phiên được dùng. Prompt đã có nhánh dự phòng ("nếu không mở được file nào → DỪNG và báo"),
nhưng **chưa đo** hành vi thật.
**Hệ quả:** có thể phải tải ZIP về, giải nén, đính kèm từng file bằng tay.

### L3 — Không kiểm được chất lượng nội dung kịch bản
QC trong repo chỉ kiểm **hình thức**: đủ 4 file, UTF-8, độ dài trong ngân sách, hook nguyên
văn, định danh job/episode. Không có bước nào đọc hiểu nội dung.
**Hệ quả:** `COMPLETED` nghĩa là "đã giao đúng hình thức", KHÔNG có nghĩa "kịch bản hay/đúng".
Nội dung vẫn phải qua gate_check + QC Sol của dự án H2DEV.

### L4 — DeepSeek không nằm trên đường tới hạn
Spec yêu cầu DeepSeek phân loại/hỗ trợ suy luận. V1 chạy được **hoàn toàn** bằng luật tất
định (Python). DeepSeek là tuỳ chọn, chưa nối vào pipeline.
**Hệ quả:** không có "AI hiểu yêu cầu mơ hồ". Ô K lạ → `NEEDS_REVIEW` để người xem, không đoán.

## 2. Giới hạn dữ liệu / cấu trúc thật

### L5 — Cột K thật KHÔNG chứa link Drive
Đo trên tab `Lane-N` ngày 2026-10-24: ô K là **nhật ký trạng thái + đường dẫn LOCAL**
(`E:\CLAUDE\YOUTUBE\h2dev\assets\N-senior_korekara\ep17-...`), không có link Drive như mẫu
trong đặc tả. Folder tập được tìm qua Drive API trong `01_INBOX` (`N-ep17_v01`).
**Hệ quả:** parser ưu tiên link trong ô K nếu có, nhưng **không bắt buộc**. Không có link vẫn
chạy được nhờ tra `01_INBOX`. Nếu cả hai đều không có → `E_NO_LINK`.

### L6 — Thứ tự dòng trong ô K là "mới nhất ở TRÊN"
Ô K18 hiện tại: dòng 0 = `Kịch bản xong- chờ dựng hook`, dòng 1 = `Đã xong hook+ sườn- cần
kịch bản`. Nghĩa là ep17 **đã xong kịch bản**, không phải đang chờ.
**Hệ quả:** `status` lấy theo **dòng đầu tiên** khớp marker. Nếu H2DEV đổi cách ghi (mới nhất
ở dưới) thì phải đổi `status_rules` trong `config.yaml`, không sửa code.

### L7 — INPUT thật chỉ có 1 file ZIP, không có file rời
`N-ep17_v01/INPUT` chỉ chứa `LaneN_tap17_GPT_SCRIPT_HANDOFF.zip`. Prompt builder đã phát hiện
và chuyển sang chế độ "chỉ có ZIP", nhưng **không tạo link từng file** vì không được upload lại.
**Hệ quả:** người thao tác phải tải ZIP → giải nén → đính kèm file. Không thể "chỉ gửi link".

### L8 — OUTPUT thật đã có file của lần chạy trước
`N-ep17_v01/OUTPUT` đang có `LaneN_tap17_GPT6_SCRIPT_OUTPUT.zip` (10.000 bytes, 2026-10-24).
**Hệ quả:** uploader mặc định `overwrite: false`. Nếu tên file trùng, nó **không ghi đè** mà
báo `E_DRIVE_UPLOAD_VERIFY`. Cần người quyết định: đổi tên, xoá bản cũ, hay bật overwrite.

### L9 — Nhiều phiên bản folder tập
`01_INBOX` có `L-ep27_v04`, `M-ep18_v04` (v04) nhưng `N-ep17_v01` (v01).
**Hệ quả:** `resolve_episode_folder` chọn **version cao nhất** cho mỗi (lane, ep). Nếu folder
v04 là bản nháp và v01 là bản chính thức thì cần đổi quy ước, không sửa code.

## 3. Giới hạn kỹ thuật

### L10 — Không có retry vô hạn
`limits.max_attempts: 3`. Sau đó job sang `NEEDS_REVIEW`.
Cố ý: gửi lại prompt khi chưa rõ GPT đã nhận là rủi ro trùng job (spec §18).

### L11 — `E_SUBMIT_UNCERTAIN` không tự xử lý được
Nếu adapter gửi đi mà không xác nhận được, job dừng ở `SUBMITTING`. Không có code nào tự
gửi lại. Người phải mở chat kiểm tra **trước**.
**Hệ quả:** an toàn hơn, nhưng cần người can thiệp.

### L12 — SQLite một tiến trình ghi
`StateStore` dùng SQLite mặc định. Chạy 2 tiến trình `loop` cùng lúc trên cùng DB sẽ tranh
chấp khoá.
**Hệ quả:** chỉ chạy **một** instance. Không thiết kế cho đa máy.

### L13 — Chưa có dashboard
Chỉ có `bridge.py status` (JSON). Spec cho phép ("không cần dashboard phức tạp").

### L14 — Chưa hỗ trợ GEO / MG_STUDIO
Config có khung `enabled: false`. Chưa có `geo_writer.md` / `mg_studio_writer.md`.
**Hệ quả:** bật lên sẽ lỗi thiếu template — đúng như thiết kế (spec §16: không làm trước).

### L15 — Prompt template mới có 1 bản cho H2DEV
`prompts/h2dev_script_writer.md` là bản dự phòng. Bản **thực tế** được dựng động từ hồ sơ
(`PromptBuilder`) vì yêu cầu cụ thể của tập nằm trong ZIP, không nằm trong template.

## 4. Giới hạn vận hành / pháp lý

### L16 — Điều khoản sử dụng ChatGPT chưa xác nhận
Spec §9 yêu cầu kiểm tra. Chưa có xác nhận nào từ OpenAI.
**Hệ quả:** bật adapter web là quyết định của người vận hành, có rủi ro.

### L17 — Không tự khởi động cùng Windows
Spec §17 cấm khi chưa được phép. `start.ps1` phải gọi tay.

### L18 — Không có thông báo đẩy
Không gửi Telegram/email khi job xong. Phải xem `bridge.py status`.
(Lý do: spec không yêu cầu; thêm vào sẽ mở rộng phạm vi.)

## 5. Những gì KHÔNG phải giới hạn (đã làm được thật)

- Đọc bảng thật `YOUTUBE` / tab `Lane-N` / cột K — đã đo.
- Resolve INPUT/OUTPUT bằng Drive API, **không ghép chuỗi URL** — đã đo trên `N-ep17_v01`.
- Tải ZIP thật, tính SHA-256, kiểm 11 file hồ sơ — đã đo.
- Parse ngân sách `4,535–5,324` và hook `297` ký tự từ hồ sơ thật — đã đo.
- QC ZIP kết quả (traversal, encoding, độ dài, hook verbatim, định danh) — có test.
- Chống trùng job qua restart — có test.
- Upload + verify hash trên Drive (dùng Drive giả) — có test.
