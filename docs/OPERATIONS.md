# OPERATIONS — vận hành GPT_BRIDGE V1.0

## 1. Cài đặt (một lần)

```powershell
cd E:\CLAUDE\YOUTUBE\GPT_BRIDGE
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe bridge.py doctor      # phải ra "verdict": "PASS"
```

Credential (đã cấu hình sẵn trong `config.yaml`, KHÔNG ghi vào tài liệu):
- `credentials.oauth_token` — token OAuth có quyền Sheets + Drive
- `credentials.service_account` — service account key

## 2. Cấu hình đã đo thật

| Khóa | Giá trị thật |
|---|---|
| `spreadsheet_id` | `<SPREADSHEET_ID>` (bảng `YOUTUBE`) |
| `sheet_name` | `Lane-N` (sheetId `1555179355`) |
| `request_column` | `K` |
| `drive.inbox_folder_id` | `<DRIVE_FOLDER_ID>` (`01_INBOX`) |
| `scan_interval_seconds` | `60` |

Sửa cấu hình: chỉ sửa `config.yaml`, **không** sửa code.

## 3. Lệnh thường dùng

```powershell
.\start.ps1 -Doctor          # kiểm môi trường + Drive + Sheets + adapter
.\start.ps1                  # chạy MỘT vòng (detect -> validate -> prepare -> submit)
.\start.ps1 -Loop            # chạy nền, lặp theo scan_interval_seconds
.\start.ps1 -Stop            # dừng an toàn (tạo temp/STOP, vòng lặp thoát sau ≤1s)
```

Hoặc gọi trực tiếp `bridge.py`:

```powershell
.venv\Scripts\python.exe bridge.py scan                      # xem yêu cầu cột K
.venv\Scripts\python.exe bridge.py detect --dry-run          # xem sẽ tạo job gì
.venv\Scripts\python.exe bridge.py validate <job_id>
.venv\Scripts\python.exe bridge.py prepare  <job_id> --show  # in prompt ra màn hình
.venv\Scripts\python.exe bridge.py submit   <job_id> --confirm
.venv\Scripts\python.exe bridge.py finish   <job_id> --dry-run-upload
.venv\Scripts\python.exe bridge.py resume                    # xử lý tiếp job đang dở
.venv\Scripts\python.exe bridge.py status                    # toàn bộ job
.venv\Scripts\python.exe bridge.py status <job_id>           # chi tiết + lịch sử
.venv\Scripts\python.exe bridge.py inbox                     # ZIP đang chờ trong _inbox
```

## 4. Quy trình một tập (MANUAL — mức tự động thật của V1)

```
[H2DEV]  nghiên cứu -> Fabel Hook+Outline -> đóng ZIP -> upload INPUT
[H2DEV]  ghi trạng thái vào cột K tab lane
   |
   v
[Hermes] bridge.py detect      -> tạo job, KHÔNG trùng sau restart
[Hermes] bridge.py validate    -> resolve INPUT/OUTPUT qua API, tải ZIP, kiểm 11 file
[Hermes] bridge.py prepare     -> GPT6_TASK_MESSAGE.txt + CHATGPT_WEB_HANDOFF_GUIDE.md
   |
   v
[NGƯỜI]  mở jobs/<job_id>/CHATGPT_WEB_HANDOFF_GUIDE.md, làm theo
[NGƯỜI]  dán prompt vào ChatGPT Web (GPT-6), kiểm nó đã đọc đúng hồ sơ
[NGƯỜI]  tải 4 file kết quả, đóng thành MỘT ZIP
[NGƯỜI]  thả ZIP vào jobs/_inbox/
   |
   v
[Hermes] bridge.py resume      -> QC -> upload OUTPUT -> verify hash -> COMPLETED
```

**Điểm kiểm soát bắt buộc trước khi cho GPT viết:** hỏi nó
"Liệt kê tên các file bạn đã đọc và chép lại câu đầu tiên của hook". Sai → đính kèm lại.

## 5. Đọc kết quả

Mỗi job có thư mục `jobs/<job_id>/`:

| File | Nội dung |
|---|---|
| `GPT6_TASK_MESSAGE.txt` | Nội dung dán vào ChatGPT |
| `CHATGPT_WEB_HANDOFF_GUIDE.md` | Hướng dẫn từng bước |
| `MANUAL_ACTION_REQUIRED.txt` | Nhắc việc còn phải làm tay |
| `prompt_meta.json` | Ngân sách, hook, danh sách file |
| `input/` | ZIP hồ sơ đã tải + `handoff_summary.json` |
| `qc_report.json` | Kết quả QC hình thức (verdict `PASS_FORMAT_ONLY`) |
| `upload.json` | file_id, hash remote, kích thước |
| `DELIVERY_RECEIPT.md` | Báo cáo bàn giao |

## 6. Xử lý sự cố theo mã lỗi

| Mã | Nghĩa | Làm gì |
|---|---|---|
| `E_SHEET_AUTH` | Không đọc được bảng | Chia sẻ bảng cho service account |
| `E_DRIVE_AUTH` | Token hết hạn | Lấy token OAuth mới |
| `E_NO_LINK` | Ô K không link + không thấy folder tập | Thêm link hoặc tạo folder `Lane-epN_vNN` trong `01_INBOX` |
| `E_DRIVE_INPUT_MISSING` | Folder tập thiếu `INPUT` | Tạo `INPUT` bên trong folder tập |
| `E_DRIVE_OUTPUT_MISSING` | Folder tập thiếu `OUTPUT` | Tạo `OUTPUT` bên trong folder tập |
| `E_DRIVE_FOLDER_AMBIGUOUS` | Có 2 folder cùng tên | Chỉ để 1 `INPUT`, 1 `OUTPUT` |
| `E_ZIP_NOT_IN_INPUT` | ZIP nằm ngoài INPUT | Chuyển ZIP vào INPUT của tập |
| `E_ZIP_CORRUPT` | ZIP hỏng | Đóng gói lại |
| `E_ZIP_TRAVERSAL` | ZIP có file nguy hiểm | **Không** giải nén; kiểm nguồn |
| `E_HANDOFF_INCOMPLETE` | Thiếu file bắt buộc | Bổ sung rồi upload lại ZIP |
| `E_MANIFEST_MISSING` | Thiếu/sai `09_HANDOFF_MANIFEST.json` | Đóng gói lại kèm manifest |
| `E_RESULT_MISSING` | Chưa có ZIP kết quả | Thả ZIP vào `jobs/_inbox/` |
| `E_RESULT_MISSING_FILES` | ZIP thiếu file | Yêu cầu GPT xuất đủ 4 file |
| `E_RESULT_LENGTH` | Sai độ dài | Yêu cầu viết lại đúng `min–max` |
| `E_RESULT_HOOK_CHANGED` | Hook bị sửa | Yêu cầu chép nguyên văn |
| `E_RESULT_JOB_MISMATCH` | ZIP của tập khác | Kiểm lại tập |
| `E_RESULT_ENCODING` | Không phải UTF-8 | Yêu cầu xuất lại |
| `E_MOCK_NOT_UPLOADABLE` | ZIP do mock | Dùng adapter thật |
| `E_ADAPTER_BLOCKED` | Không có CDP / CAPTCHA | Làm thủ công |
| `E_SUBMIT_UNCERTAIN` | Không rõ GPT đã nhận | **Mở chat kiểm tra TRƯỚC khi gửi lại** |
| `E_WRITER_TIMEOUT` | Quá hạn | Kiểm ChatGPT, không tự gửi lại |

Chi tiết + hướng xử lý nằm trong `services/errors.py`.

## 7. Dừng an toàn

```powershell
.\start.ps1 -Stop          # tạo temp/STOP
# hoặc
$env:BRIDGE_STOP = "1"
```

Vòng lặp kiểm `temp/STOP` mỗi giây → thoát trong ≤1s, không cắt giữa giao dịch Drive.
Xoá cờ: `bridge.py start-clear`.

## 8. Vòng đời trạng thái

```
DETECTED -> VALIDATING -> READY_TO_SUBMIT -> SUBMITTING -> WAITING_GPT
   -> RESULT_AVAILABLE -> VERIFYING -> UPLOADING -> COMPLETED

Nhánh lỗi: NEEDS_REVIEW | FAILED | BLOCKED
```

**COMPLETED chỉ đạt khi** Drive trả `file_id` + đọc lại metadata đúng folder/tên/kích thước
+ (tuỳ chọn) hash khớp. Gửi prompt thành công **không** đủ để đánh dấu COMPLETED.

## 9. Sao lưu & khôi phục

- Trạng thái: `jobs/bridge.db` (SQLite) — sao lưu file này là đủ.
- Job đang dở: cả thư mục `jobs/`.
- `bridge.py resume` sau restart sẽ xử lý tiếp job `WAITING_GPT` / `RESULT_AVAILABLE`.
- Không tạo job trùng vì khoá `row_key` (spreadsheet+sheet+row+file_id+revision).

## 10. Kiểm tra sức khoẻ định kỳ

```powershell
.venv\Scripts\python.exe bridge.py doctor
.venv\Scripts\python.exe bridge.py status
.venv\Scripts\python.exe -m pytest tests/ -q
```

`logs/heartbeat.json` cập nhật mỗi vòng khi chạy `-Loop`.

## 11. Nguyên tắc không được vi phạm

1. Không sửa dữ liệu H2DEV khi chưa có giao thức (V1 read-only với bảng tính).
2. Không ghép chuỗi `/input` `/output` vào link Drive — luôn resolve bằng API.
3. Không upload ZIP mock như sản phẩm thật.
4. Không gửi lại prompt khi chưa biết GPT đã nhận.
5. Không đánh dấu COMPLETED khi chưa verify trên Drive.
6. Không tự mở Chrome, không chiếm chuột/bàn phím, không đóng Chrome người dùng.
