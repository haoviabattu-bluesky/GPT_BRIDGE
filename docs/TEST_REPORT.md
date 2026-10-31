# TEST_REPORT — GPT_BRIDGE V1.0

**Ngày đo:** 2026-10-24 · **Môi trường:** Windows 10 · Python 3.13.5 (venv `.venv`) ·
pytest 8.x

## 1. Kết quả tổng

| Lần chạy | Lệnh | Kết quả |
|---|---|---|
| Offline (mặc định) | `python -m pytest tests/ -q` | **74 passed, 3 skipped** |
| Có Drive/Sheets THẬT | `BRIDGE_LIVE=1 python -m pytest tests/ -q` | **77 passed** |

3 test bị skip ở lần offline là các test `live` — chúng **đã PASS thật** khi bật
`BRIDGE_LIVE=1`. Không có test nào FAIL.

## 2. Chi tiết theo file

| File | Số test | PASS | Nội dung |
|---|---:|---:|---|
| `tests/test_phase1_parser.py` | 20 | 20 | Parser cột K + resolve INPUT/OUTPUT |
| `tests/test_phase2_orchestrator.py` | 17 | 17 | State SQLite, parse ngân sách/hook/meta, prompt builder |
| `tests/test_phase3_pipeline.py` | 21 | 21 | QC ZIP, collector, E2E tới COMPLETED (Drive giả) |
| `tests/test_phase4_adapters.py` | 16 | 16 | Hợp đồng adapter, mock, manual, API tắt, chống gửi trùng |
| `tests/test_phase4_adapters.py -k Live` | 3 | 3 | Drive/Sheets THẬT, ID trong đặc tả còn tồn tại |

## 3. Test then chốt (bằng chứng, không phải khẩu hiệu)

### 3.1 Một hàng cột K → một job
`TestEndToEnd::test_full_pipeline_to_completed` chạy trọn:
`validate → prepare → submit → collect → QC → upload OUTPUT → verify → COMPLETED`.
Khẳng định sau khi chạy:
- `job.status == "COMPLETED"`
- OUTPUT folder có **đúng 1** file: `LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip`
- INPUT vẫn giữ nguyên ZIP hồ sơ (không ghi nhầm INPUT)
- `sha256(file trên Drive) == job.output_sha256`

### 3.2 ZIP nằm ngoài INPUT → từ chối
`test_zip_not_in_input_rejected`: ZIP ở folder khác → `E_ZIP_NOT_IN_INPUT`, job sang
`NEEDS_REVIEW`, **không** upload gì.

### 3.3 Chống trùng sau restart
- `test_no_duplicate_job_on_restart`: cùng `row_key` → không tạo job thứ 2.
- `test_persist_across_reopen`: đóng/mở lại SQLite, job còn nguyên.
- `test_second_submit_refused`: đã `WAITING_GPT` thì submit lần 2 bị từ chối
  (chống gửi trùng prompt — rủi ro spec §18).
- `test_attempt_counter_increments`: `attempt` tăng đúng.

### 3.4 Không tuyên bố thành công khi chưa xác minh
- `test_upload_verify_detects_hash_mismatch`: file trên Drive bị đổi sau upload →
  `E_DRIVE_UPLOAD_VERIFY`, **không** chuyển COMPLETED.
- `test_mock_upload_blocked_by_default`: ZIP do mock sinh → `E_MOCK_NOT_UPLOADABLE`,
  OUTPUT trống. Chỉ khi `upload.allow_mock_upload=true` (dùng cho test đường ống) mới cho qua.

### 3.5 An toàn ZIP
- `test_blocks_traversal`: `../../etc/passwd` → `E_ZIP_TRAVERSAL`
- `test_blocks_executable`: `.exe` → `E_ZIP_TRAVERSAL`
- `test_detects_corrupt_zip`: file rác → `E_ZIP_CORRUPT`
- `test_missing_required` → `E_HANDOFF_INCOMPLETE`
- `test_manifest_checksum_mismatch_reported`: manifest sai hash → **báo** `checksum_mismatch`

### 3.6 QC kết quả
| Test | Mã lỗi |
|---|---|
| thiếu file | `E_RESULT_MISSING_FILES` |
| script rỗng | `E_RESULT_EMPTY_SCRIPT` |
| sai độ dài | `E_RESULT_LENGTH` |
| hook bị sửa | `E_RESULT_HOOK_CHANGED` |
| ZIP của tập khác | `E_RESULT_JOB_MISMATCH` |
| sai encoding | `E_RESULT_ENCODING` |

### 3.7 Adapter
- `test_all_adapters_share_interface`: 3 adapter cùng hợp đồng `GPTWriterAdapter`.
- `test_submit_blocked_when_disabled`: `official_api` chưa duyệt → `E_ADAPTER_NOT_READY`.
- `test_no_launch_by_default`: adapter web **không** tự mở Chrome.
- `test_submit_blocked_without_cdp`: không có CDP → `E_ADAPTER_BLOCKED`.
- `test_never_bypasses_captcha`: quét mã nguồn, không có `solve_captcha`/`2captcha`/
  `anticaptcha`/`bypass`/`stealth`.

### 3.8 Bẫy dữ liệu thật đã đóng bằng test hồi quy
- `test_episode_meta_ignores_format_row_title`: hồ sơ N-ep17 có dòng
  `Tiêu đề chương | KHÔNG có trong bản đọc...` — nếu parse lỏng sẽ lấy câu quy định làm
  tiêu đề video. Test khoá hành vi đúng.
- `test_budget_with_thousand_separators`: ngân sách thật `4,535 / 4,930 / 5,324`.
- `test_top_line_wins_status`: ô K thật ghi **mới nhất ở TRÊN**.

## 4. Chạy thật trên dữ liệu THẬT (dry-run, không upload)

`temp/live_dryrun.py` — chạy trên Drive/Sheet thật, dừng trước upload:

```
BƯỚC 1  bảng YOUTUBE · tab Lane-N (sheetId=1555179355)   -> đọc OK
BƯỚC 2  01_INBOX có 3 folder: N-ep17_v01, L-ep27_v04, M-ep18_v04
        N-ep17_v01 -> INPUT <DRIVE_FOLDER_ID>
                   -> OUTPUT <DRIVE_FOLDER_ID>
        INPUT có 1 file: LaneN_tap17_GPT_SCRIPT_HANDOFF.zip (50.085 bytes)
BƯỚC 3  tải ZIP thật, SHA-256 = e28e118a736b250285937eef2fd5193b08adf7b8e1a5d5d605fb4f0c590e0779
        hồ sơ 11 file — đủ bộ bắt buộc
BƯỚC 4  prompt thật 2.332 ký tự · ngân sách 4.535–5.324 (target 4.930)
        hook 297 ký tự (hồ sơ khai 297) — khớp
BƯỚC 5  mock sinh ZIP -> QC PASS_FORMAT_ONLY, đo 4.937 ký tự, hook verbatim OK
        OUTPUT THẬT: không ghi gì (dry-run). Trước/sau đều 1 file.
```

## 5. Điều các test này **KHÔNG** chứng minh

- **Không** chứng minh ChatGPT Web tự động nhận/gửi được. Adapter web chỉ được kiểm ở
  mức "không có CDP thì chặn đúng" — chưa từng chạy với phiên ChatGPT thật.
- **Không** chứng minh chất lượng kịch bản. QC chỉ kiểm **hình thức** (đủ file, UTF-8,
  độ dài, hook nguyên văn, định danh). Nội dung phải qua gate_check + QC Sol của dự án.
- **Không** chứng minh DeepSeek bắt buộc. Đường tới hạn chạy hoàn toàn bằng luật tất định;
  xem `IMPLEMENTATION_REPORT.md §4`.
- **Không** chứng minh upload thật lên OUTPUT thật. Toàn bộ upload trong test dùng Drive
  giả; dry-run dừng trước upload đúng theo thiết kế.

## 6. Cách tái lập

```bash
cd E:\CLAUDE\YOUTUBE\GPT_BRIDGE
.venv\Scripts\python.exe -m pytest tests/ -q                     # 74 passed, 3 skipped
set BRIDGE_LIVE=1 && .venv\Scripts\python.exe -m pytest tests/ -q  # 77 passed
.venv\Scripts\python.exe temp/live_dryrun.py                     # dry-run thật
```
