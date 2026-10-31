# GPT_BRIDGE — Báo cáo bàn giao OUTPUT Phase 5.2

**Ngày kiểm tra:** 24/10/2026  
**Phạm vi:** kiểm tra điểm nối `chatgpt_web_handoff` → collector → QC → Google Drive → xác minh SHA256 → `COMPLETED`.  
**Nguyên tắc:** không upload vào OUTPUT sản xuất, không sửa H2DEV, không dùng quota Codex/Sol.

## 1. Kết luận

**Phase 5.2 đạt trên môi trường thử nghiệm: 23/23 kiểm tra PASS.**

Đường đi đầy đủ đã chạy bằng ZIP thật của job Phase 5.1:

```text
HandoffStore: DOWNLOADED
  → output_collector.normalize()
  → zip_qc.inspect_result()
  → DriveUploader.upload_and_verify()
  → tải lại độc lập từ Google Drive
  → đối chiếu SHA256
  → StateStore: COMPLETED (chỉ trong job thử nghiệm)
```

**Không có upload nào vào OUTPUT sản xuất N-ep17.** File thử nghiệm trên Google Drive đã được xoá sau khi xác minh; folder thử nghiệm vẫn được giữ lại nhưng hiện rỗng.

## 2. Điểm nối DOWNLOADED → uploader

Đã xác nhận các kho trạng thái hiện là hai lớp riêng:

- `chatgpt_web_handoff/HandoffStore`: giữ trạng thái nhận ZIP từ ChatGPT, trong đó job thật `SMOKE51-N-ep17-GPT6-v01` đang `DOWNLOADED`.
- `services/StateStore`: giữ trạng thái pipeline Phase 1–4 và trạng thái upload.
- `services/output_collector.py`: chuẩn hoá ZIP về 4 file phẳng.
- `services/zip_qc.py`: kiểm tra cấu trúc, UTF-8, định danh và báo cáo QC.
- `services/drive_uploader.py`: upload, xác minh và chặn bản trùng.

Điểm cần lưu ý: trước Phase 5.2 không có đoạn code tự động chuyển trực tiếp bản ghi HandoffStore thật sang StateStore thật. Bài thử đã dựng job cầu nối riêng `PHASE52-N-ep17-GPT6-v01`, không thay đổi job sản xuất, để kiểm tra toàn bộ hợp đồng dữ liệu.

## 3. Kết quả upload thử nghiệm thực tế

Folder thử nghiệm riêng:

```text
Tên: PHASE52_TEST_OUTPUT
ID: <DRIVE_FOLDER_ID>
```

Artifact được tạo từ ZIP N-ep17 thật, sau đó chuẩn hoá:

```text
Đường dẫn cục bộ:
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\jobs\PHASE52-N-ep17-GPT6-v01\PHASE52-N-ep17-GPT6-v01_GPT_SCRIPT_DELIVERY.zip

Tên trên Drive:
LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip

SHA256:
e4afc501a63069c2c5e5aa25a4566ec79b67175583f1169cb81a165f45033906

Kích thước:
9952 byte
```

Kết quả xác minh:

- Upload vào đúng folder thử nghiệm: **PASS**.
- Tải lại file từ Google Drive: **PASS**.
- SHA256 local và SHA256 file tải lại: **khớp hoàn toàn**.
- Kích thước local và remote: **khớp, 9952 byte**.
- Tên file remote: **đúng**.
- `COMPLETED` chỉ được ghi sau khi Drive trả về file và SHA256 đã xác minh: **PASS**.
- File thử nghiệm đã được xoá sau kiểm tra; không để lại artifact thử nghiệm trên Drive.

## 4. Phát hiện và đã sửa lỗi tất định SHA256

Lần chạy đầu phát hiện lỗi thực tế: `output_collector.normalize()` dùng `zipfile.write()`, làm timestamp file nguồn lọt vào metadata ZIP. Vì vậy cùng một nội dung đóng gói ở các thời điểm khác nhau tạo SHA256 khác nhau. Kết quả kiểm chứng trước sửa:

```text
cùng input, cách nhau vài giây → SHA256 khác nhau
```

Lỗi này có thể làm hỏng chống upload trùng và làm verify hash không ổn định.

Đã sửa **chỉ trong GPT_BRIDGE**: đóng ZIP bằng `ZipInfo` với timestamp cố định và thuộc tính file cố định. Đã thêm regression test.

Sau sửa, chạy 4 lần liên tiếp:

```text
cùng input → cùng SHA256 e4afc501a63069c2…
```

Đây là thay đổi cần thiết cho Phase 5.2, không phải viết lại pipeline.

## 5. Chống upload trùng khi retry/restart

Đã chạy toàn bộ bài thử hai lần:

- Lần 1: upload thành công, Drive xác minh thành công.
- Lần 2: cùng tên file trong folder thử nghiệm bị chặn, không ghi đè.
- Trong mỗi lần, thử upload lần thứ hai ngay trong cùng một tiến trình cũng bị chặn.
- Folder thử nghiệm luôn chỉ có 1 bản trong lúc kiểm tra.
- Kết quả cuối mỗi lần: **23/23 PASS**.

Sau đó bản thử nghiệm đã được xoá khỏi Drive. Không có bản trùng tồn tại.

## 6. OUTPUT sản xuất N-ep17

Đã đọc lại folder sản xuất, không ghi hoặc xoá:

```text
Folder sản xuất: <DRIVE_FOLDER_ID>
File hiện có: LaneN_tap17_GPT6_SCRIPT_OUTPUT.zip
File ID: <DRIVE_FOLDER_ID>
Kích thước: 10000 byte
MD5 Drive: 634d0c2560f17a1a840c45f78de0c42a
modifiedTime: 2026-10-24T08:05:10.636Z
```

`LaneN_tap17_GPT6_SCRIPT_OUTPUT.zip` vẫn là file sản xuất duy nhất. Không có file thử nghiệm trong folder sản xuất. **Không upload/ghi đè OUTPUT N-ep17.**

## 7. Kiểm tra H2DEV nhận diện bàn giao

H2DEV không đọc trực tiếp ZIP của GPT_BRIDGE. Quy ước nhận thực tế của H2DEV là:

```text
FlowHandoff_local/jobs/<tab>/<slug>/script.txt
FlowHandoff_local/jobs/<tab>/<slug>/script.lock.json
```

Bài thử sandbox đã:

1. Đọc ZIP chuẩn hoá với 4 file phẳng ở gốc.
2. Xác nhận đúng bộ file:
   - `SCRIPT_FINAL_JP.txt` — kịch bản cuối bằng tiếng Nhật;
   - `SCRIPT_ISSUES.md` — các vấn đề kịch bản;
   - `SCRIPT_QC_REPORT.md` — báo cáo kiểm soát chất lượng;
   - `SCRIPT_STRUCTURE.md` — cấu trúc kịch bản.
3. Chép `SCRIPT_FINAL_JP.txt` thành `script.txt` theo quy ước H2DEV.
4. Dùng chính `gate_common.ghi_lock()` của H2DEV để ghi khóa SHA256.
5. Dùng chính `gate_common.kiem_lock()` để đọc lại.
6. Sửa thử `script.txt` sau khi khóa: H2DEV phát hiện và chặn `FINAL_QC_STALE`.
7. Khôi phục đúng nội dung: H2DEV trở lại `PASS`.

Kết quả: **15/15 PASS**. Sandbox nằm ngoài H2DEV, tại:

```text
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\jobs\PHASE52_H2DEV_SANDBOX\ep17-phase52
```

Không có file nào được ghi vào H2DEV thật.

## 8. Watcher production handoff

Watcher vẫn được giữ hoạt động nền theo phê duyệt:

- Một watcher logic duy nhất.
- PID shim `34700` → PID interpreter `39608`, cùng thời điểm khởi động `22:23:43`; đây là một lần gọi watcher gồm shim và interpreter, không phải hai watcher độc lập.
- Log chỉ có một chu kỳ scan cho mỗi khoảng thời gian.
- Không tìm thấy `pyautogui`, `pynput`, `SendInput`, `SetCursorPos`, `mouse_event` hoặc điều khiển CDP trong package watcher.
- Watcher không chiếm chuột/bàn phím.
- Không tạo watcher phụ hoặc tiến trình upload phụ.
- Tiến trình `AGENT_BRIDGE` được xác định là project khác, không phải GPT_BRIDGE.

## 9. H2DEV, Codex và Sol

- H2DEV: chỉ đọc mã, quy ước và metadata; không sửa hoặc ghi đè.
- Codex: không gọi.
- Sol: không gọi.
- OUTPUT sản xuất: không upload, không ghi đè, không xoá.
- Không có thay đổi quota ngoài các thao tác Drive thử nghiệm đã được yêu cầu; artifact thử nghiệm đã được dọn.

## 10. Regression

```text
pytest tests/
150 passed, 3 skipped in 13.89s
```

Con số tăng từ 149 lên 150 vì đã thêm test khoá lỗi ZIP không tất định:

```text
test_normalize_is_deterministic_across_runs
```

## 11. Lỗi còn tồn tại / giới hạn

1. **Cầu nối tự động HandoffStore → StateStore chưa tồn tại trong production code.** Phase 5.2 đã chứng minh hợp đồng bằng job test riêng, nhưng chưa tự động hoá việc tạo StateStore job và chuyển trạng thái cho job thật. Không tự thêm vào production vì yêu cầu giữ code đã PASS và không upload sản xuất khi chưa được duyệt.
2. **H2DEV không nhận ZIP trực tiếp.** Cần một bước giao file có kiểm soát: giải nén `SCRIPT_FINAL_JP.txt` thành `script.txt`, sau đó ký `script.lock.json`; bài thử đã chứng minh quy ước này hoạt động trong sandbox.
3. **`SCRIPT_FINAL_JP.txt` là tên kỹ thuật hiện tại của artifact;** nội dung được dùng làm kịch bản cuối để giao H2DEV. Không có thay đổi nào trong H2DEV để tự động nhận tên này.
4. **Folder thử nghiệm Drive hiện rỗng nhưng vẫn tồn tại** để tái sử dụng cho lần test được duyệt tiếp theo.

## 12. Đề xuất tập mới để thử nghiệm sản xuất thật

Đề xuất: **Lane N — tập 17**, nhưng phải tạo kịch bản mới trước khi chạy thật vì hiện tại:

```text
E:\CLAUDE\YOUTUBE\H2DEV\assets\N-senior_korekara\ep17-*       chưa tồn tại
E:\CLAUDE\YOUTUBE\H2DEV\FlowHandoff_local\jobs\Lane-N\ep17-* chưa tồn tại
```

Tập này phù hợp để làm pilot mới vì `ep16-tatamiya-41nen` là tập cao nhất hiện có của Lane N và `ep17` chưa có kịch bản, chưa có thư mục bàn giao, chưa có OUTPUT sản xuất. Tuy nhiên, **chưa được tự động tạo kịch bản, chưa tạo job upload và chưa upload sản xuất**.

Cần phê duyệt riêng trước khi:

1. tạo kịch bản Lane N tập 17;
2. đưa qua cổng kiểm tra và khóa SHA256 của H2DEV;
3. tạo job production;
4. upload vào OUTPUT sản xuất.

## 13. Artifact và file thay đổi

Đã tạo:

```text
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\PHASE52_OUTPUT_HANDOFF_REPORT.md
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\scratch_analysis\phase52_upload_dryrun.py
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\scratch_analysis\phase52_h2dev_handoff.py
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\jobs\PHASE52_H2DEV_SANDBOX\...
```

Đã sửa cần thiết:

```text
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\services\output_collector.py
E:\CLAUDE\YOUTUBE\GPT_BRIDGE\tests\test_phase3_pipeline.py
```

Không commit, không push, không sửa H2DEV.

---

**Trạng thái cuối:** Phase 5.2 test handoff đạt; production OUTPUT chưa bị đụng; watcher tiếp tục chạy nền; chưa tự động upload tập mới.
