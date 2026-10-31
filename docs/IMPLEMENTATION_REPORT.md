# IMPLEMENTATION_REPORT — GPT_BRIDGE V1.0

**Ngày:** 2026-10-24 · **Thư mục:** `E:\CLAUDE\YOUTUBE\GPT_BRIDGE`
**Phạm vi:** chỉ bước VIẾT KỊCH BẢN (thay thao tác trung gian bước 5–7 của H2DEV).
Không sửa H2DEV, không đụng pipeline khác.

---

## 1. Trả lời trực tiếp các câu hỏi nghiệm thu

### 1.1 Số test PASS/FAIL
- **77 PASS / 0 FAIL** (`BRIDGE_LIVE=1 python -m pytest tests/ -q`)
- Mặc định offline: **74 passed, 3 skipped** (3 test `live` cần credential thật).
- Chi tiết từng test: `docs/TEST_REPORT.md`.

### 1.2 Khả năng Google Drive và Sheet **thực tế**
**CÓ, đã đo trên dữ liệu thật.**
- Đọc bảng `YOUTUBE`, tab `Lane-N` (sheetId `1555179355`), cột K — **đọc được**.
- `01_INBOX` (`<DRIVE_FOLDER_ID>`) có 3 folder tập thật:
  `N-ep17_v01`, `L-ep27_v04`, `M-ep18_v04`.
- Resolve `N-ep17_v01` → INPUT `<DRIVE_FOLDER_ID>`,
  OUTPUT `<DRIVE_FOLDER_ID>` — **bằng Drive API**, không ghép chuỗi URL.
- Tải ZIP thật `LaneN_tap17_GPT_SCRIPT_HANDOFF.zip` (50.085 bytes),
  SHA-256 `e28e118a...0e0779`, 11 file hồ sơ.
- **Chưa làm:** upload thật lên OUTPUT thật (dry-run dừng trước upload theo thiết kế).

### 1.3 Khả năng DeepSeek **thực tế**
**Chưa dùng DeepSeek trên đường tới hạn.**
Đường tới hạn chạy hoàn toàn bằng luật tất định (Python): parse URL, lấy ID, đọc metadata,
kiểm file, SHA-256, đếm ký tự, lưu trạng thái, upload/download, đối chiếu job ID — đúng như
spec §6 yêu cầu ("các thao tác xác định phải dùng Python, không sử dụng AI").
DeepSeek để dành cho các việc cần suy luận (phân loại yêu cầu mơ hồ, tóm tắt lỗi) — **chưa nối**.
Không có chỗ nào DeepSeek tự viết kịch bản. Không có chỗ nào DeepSeek tự thay writer.

### 1.4 Khả năng điều khiển ChatGPT **đã chứng minh**
**CHƯA chứng minh được điều khiển tự động.** Chỉ chứng minh được:
- Giao diện adapter 3 phương thức hoạt động, 3 adapter cùng hợp đồng.
- Adapter web **chặn đúng** khi không có CDP (`E_ADAPTER_BLOCKED`).
- Adapter web **không** tự mở Chrome, **không** có mã vượt CAPTCHA (test quét mã nguồn).
- Adapter API **chặn đúng** khi chưa duyệt ngân sách.

Chưa từng chạy với phiên ChatGPT thật. Chi tiết + checklist điều kiện bật AUTO:
`docs/ADAPTER_FEASIBILITY.md`.

### 1.5 Khả năng nhận ZIP kết quả **thực tế**
**CÓ, ở chế độ MANUAL, đã kiểm bằng test.**
- Người thả ZIP vào `jobs/_inbox/` → adapter phát hiện (`test_status_result_available_when_zip_dropped`).
- Collector chuẩn hoá ZIP lồng folder về 4 file phẳng (`test_normalize_extracts_flat_zip`).
- QC đầy đủ; upload + verify hash (Drive giả) — `test_full_pipeline_to_completed`.
- **Chưa** nhận ZIP tự động từ ChatGPT (chưa có adapter thật).

### 1.6 Những bước còn **thủ công**
1. Mở ChatGPT Web, chọn model GPT-6.
2. Dán `GPT6_TASK_MESSAGE.txt`.
3. Đính kèm hồ sơ (INPUT thật chỉ có ZIP → phải tải về, giải nén, đính kèm file).
4. Hỏi kiểm tra ChatGPT đã đọc đúng hồ sơ (bắt buộc).
5. Chờ viết xong, tải 4 file kết quả.
6. Đóng 4 file thành 1 ZIP, thả vào `jobs/_inbox/`.
7. Chạy `bridge.py resume`.
8. Duyệt nội dung kịch bản bằng QC của dự án (ngoài phạm vi repo này).

### 1.7 Giới hạn ngăn tự động hoàn toàn
- **L1** Chưa có adapter ChatGPT đã kiểm chứng → đây là chặn lớn nhất.
- **L2** Chưa biết ChatGPT có đọc được ZIP qua connector không.
- **L3** QC chỉ kiểm hình thức, không kiểm nội dung.
- **L4** DeepSeek không nằm trên đường tới hạn.
- **L5** Cột K thật **không có link Drive** (khác mẫu đặc tả) → phải tra `01_INBOX`.
- **L6** Thứ tự dòng ô K là "mới nhất ở TRÊN".
- **L8** OUTPUT thật đã có file lần trước → uploader không ghi đè.
- **L16** Điều khoản sử dụng ChatGPT chưa xác nhận.
Danh sách đầy đủ: `docs/KNOWN_LIMITATIONS.md`.

---

## 2. Kiến trúc đã dựng

```
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\
├── bridge.py                    CLI 14 lệnh (doctor/scan/detect/validate/prepare/
│                                submit/finish/run/loop/resume/status/inbox/stop)
├── config.yaml                  cấu hình ĐO THẬT (không phải giá trị mẫu)
├── requirements.txt
├── start.ps1                    -Doctor / -Loop / -Stop
├── adapters\
│   ├── base.py                  GPTWriterAdapter (3 phương thức)
│   ├── mock_chatgpt.py          sinh ZIP đúng chuẩn từ hồ sơ thật
│   ├── manual_chatgpt.py        chế độ người thao tác (ĐANG DÙNG)
│   ├── chatgpt_web.py           CDP + Playwright (MẶC ĐỊNH TẮT, chưa kiểm chứng)
│   └── official_api.py          API chính thức (MẶC ĐỊNH TẮT, chưa có ngân sách)
├── services\
│   ├── sheet_scanner.py         đọc tab lane, parse cột K
│   ├── drive_client.py          Drive API, resolve INPUT/OUTPUT
│   ├── orchestrator.py          vòng đời job 11 trạng thái
│   ├── prompt_builder.py        dựng prompt từ HỒ SƠ THẬT
│   ├── output_collector.py      AUTO/MANUAL -> cùng cấu trúc
│   ├── zip_qc.py                QC hồ sơ vào + kết quả ra
│   ├── drive_uploader.py        upload + verify
│   ├── state.py                 SQLite checkpoint, chống trùng
│   ├── errors.py                40 mã lỗi có hướng xử lý
│   └── common.py                config, log, heartbeat, stop
├── prompts\h2dev_script_writer.md
├── tests\                       4 file, 77 test
├── docs\                        5 tài liệu bắt buộc
└── vendor\autowebprompt-main\   repo tham chiếu (đã đọc mã, không import)
```

## 3. Những quyết định thiết kế quan trọng

### 3.1 Không tin mẫu trong đặc tả — đo dữ liệu thật
Đặc tả cho rằng cột K chứa `ZIP hồ sơ: <link>` + `Thư mục Drive: <link>`. **Dữ liệu thật khác:**
ô K18 là nhật ký trạng thái + đường dẫn **local**, không có link Drive.
→ Parser chấp nhận **cả hai** dạng: có link thì dùng link; không có thì tra `01_INBOX` theo
`lane + ep`, chọn version cao nhất. Không đoán ID.

### 3.2 Thứ tự dòng trong ô K
Sheet ghi **mới nhất ở TRÊN**. Ô K18: dòng 0 `Kịch bản xong- chờ dựng hook`,
dòng 1 `Đã xong hook+ sườn- cần kịch bản` → ep17 đã xong kịch bản.
→ `status` lấy dòng **đầu tiên** khớp marker. Có test hồi quy khoá hành vi này.

### 3.3 Prompt dựng ĐỘNG từ hồ sơ, không hardcode
Template `prompts/h2dev_script_writer.md` chỉ chứa quy tắc chung. Ngân sách ký tự
(`4.535–5.324`), hook (`297` ký tự), tiêu đề, danh sách file — đều đọc từ ZIP thật.
Đo được: prompt 2.332 ký tự, khớp 100% số liệu hồ sơ.

### 3.4 Bẫy "Tiêu đề chương" — đã đóng
`07_WRITING_TASK.md` thật có 2 dòng cùng chứa "tiêu đề":
`Tiêu đề dự kiến | 【漫画】...` và `Tiêu đề chương | KHÔNG có trong bản đọc...`.
Parse lỏng sẽ lấy câu quy định định dạng làm tiêu đề video.
→ Chỉ nhận title từ key có `dự kiến`/`video`/`title`, loại key có `chương`/`section`.
Có test hồi quy.

### 3.5 INPUT chỉ có ZIP → prompt nói thật
`N-ep17_v01/INPUT` chỉ có 1 file ZIP, không có file rời. Prompt builder phát hiện và chuyển
sang chế độ "chỉ có ZIP": liệt kê tên 11 file cần đọc bên trong, nói rõ không có link file rời.
**Không** bịa link không tồn tại.

### 3.6 Ba lớp chặn "báo xong khi chưa xong"
1. ZIP mock → `E_MOCK_NOT_UPLOADABLE`, không upload.
2. Upload xong nhưng hash lệch → `E_DRIVE_UPLOAD_VERIFY`, không COMPLETED.
3. QC hình thức fail → `NEEDS_REVIEW`, không upload.

### 3.7 Chống gửi trùng prompt
`submit` chỉ nhận khi `status == READY_TO_SUBMIT`. Đã `WAITING_GPT` → từ chối.
Có mã `E_SUBMIT_UNCERTAIN` cho trường hợp "không rõ GPT đã nhận" — **không** tự gửi lại.

### 3.8 An toàn máy người dùng
- Không mở cửa sổ, không chiếm chuột/bàn phím, không đóng Chrome chính.
- Adapter web mặc định `allow_launch: false` — không tự bật Chrome.
- Dừng an toàn qua `temp/STOP`, kiểm mỗi giây.
- Không tự khởi động cùng Windows.

### 3.9 Multi-project nhưng chỉ bật H2DEV
`projects:` có khung `GEO` / `MG_STUDIO` với `enabled: false`, `spreadsheet_id: null`.
Bật lên sẽ lỗi thiếu template — đúng ý spec §16 (không làm trước khi có yêu cầu).

## 4. Về DeepSeek (nói rõ để không hiểu nhầm)

Spec §6 liệt kê DeepSeek cho: phân loại yêu cầu, phát hiện hồ sơ thiếu, chọn template,
tổng hợp lỗi, quyết định cần người xem. V1 **không** gọi DeepSeek ở đường tới hạn, vì:
- Mọi thao tác trên đường tới hạn là **tất định** — spec yêu cầu dùng Python.
- Gọi AI ở đây chỉ thêm độ trễ và khả năng sai, không thêm năng lực.
- Yêu cầu mơ hồ đã có đường xử lý an toàn: `NEEDS_REVIEW`, để người xem.

Hệ quả trung thực: **pipeline chạy được mà không cần DeepSeek.** Nếu muốn dùng DeepSeek để
phân loại ô K lạ hoặc tóm tắt lỗi thành văn, đó là việc mở rộng, chưa làm.

## 5. Đã kiểm chứng end-to-end ở đâu

| Mức | Bằng chứng |
|---|---|
| Unit | 77 test PASS |
| E2E với Drive giả | `COMPLETED`, OUTPUT đúng 1 file, hash khớp |
| E2E với Drive THẬT (dry-run) | `temp/live_dryrun.py`: đọc sheet thật, resolve INPUT/OUTPUT thật, tải ZIP thật, dựng prompt thật, QC thật — dừng trước upload |
| Upload thật lên OUTPUT thật | **CHƯA LÀM** (cần người cho phép) |

## 6. Cần người quyết định trước khi chạy thật

1. **OUTPUT `N-ep17_v01` đã có** `LaneN_tap17_GPT6_SCRIPT_OUTPUT.zip` — đổi tên, xoá, hay bật overwrite?
2. **Chọn tập giao thử** — ep17 hiện là `script_done`, không phải `need_script`.
3. **Có dùng ChatGPT Web thật không** — nếu có, cần một lần chạy kiểm chứng có giám sát.
4. **Có ngân sách API không** — nếu có, `official_api` là đường hợp pháp và ổn định nhất.

## 7. Tài liệu

| File | Nội dung |
|---|---|
| `docs/IMPLEMENTATION_REPORT.md` | tài liệu này |
| `docs/TEST_REPORT.md` | 77 test, bằng chứng từng nhóm |
| `docs/ADAPTER_FEASIBILITY.md` | đánh giá trung thực khả năng điều khiển ChatGPT |
| `docs/OPERATIONS.md` | vận hành, 20 mã lỗi, quy trình |
| `docs/KNOWN_LIMITATIONS.md` | 18 giới hạn thật |
