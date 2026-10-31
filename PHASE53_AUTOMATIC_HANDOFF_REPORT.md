# PHASE 5.3 — BÁO CÁO NGHIỆM THU: BÀN GIAO ĐẦU RA TỰ ĐỘNG

**Dự án:** `E:\CLAUDE\YOUTUBE\GPT_BRIDGE`
**Ngày nghiệm thu:** 2026-10-24
**Phạm vi:** nối tự động `HandoffStore` (Chrome Extension) → QC → Google Drive OUTPUT → adapter bàn giao H2DEV
**Cơ sở:** Phase 5.2 đã đạt 23/23 PASS và 150 test PASS — Phase 5.3 **không viết lại** module nào đã hoạt động.

---

## 1. KẾT LUẬN NGẮN

| Hạng mục | Kết quả |
|---|---|
| Test bắt buộc Phase 5.3 (11 mục) | **PASS 11/11** — 28 test cụ thể, 0 FAIL |
| Toàn bộ test Phase 1–5.3 | **178 passed, 3 skipped** (150 test cũ vẫn PASS) |
| Nghiệm thu đầu-cuối trên Drive thật | **PASS** — DOWNLOADED → QC → upload → verify → COMPLETED |
| Số lần upload thật trong nghiệm thu | **1** (chạy lại: 0 — không tạo bản trùng) |
| StateStore và HandoffStore đồng bộ | **Có** — cả hai COMPLETED, cùng file_id / SHA-256 |
| Ảnh hưởng OUTPUT sản xuất | **KHÔNG** — OUTPUT `175S_W4K…` giữ nguyên 1 file, mtime không đổi |
| Ảnh hưởng H2DEV | **KHÔNG** — không ghi byte nào vào `E:\CLAUDE\YOUTUBE\h2dev` |
| Phần còn thủ công | Xem §8 |

---

## 2. KIẾN TRÚC ĐÃ NỐI (không sửa module cũ)

```
chatgpt_web_handoff.watcher        ← MỘT watcher logic duy nhất (nguyên trạng)
   tải ZIP + kiểm cấu trúc/định danh + ghi HandoffStore = DOWNLOADED
        │
        ▼
services/handoff_coordinator.py    ← MỚI (Phase 5.3)
   ①  jobs_to_process()            đọc HandoffStore.by_state("DOWNLOADED")  — không quét đĩa
   ②  metadata job                 result_zip_path tuyệt đối, tồn tại, SHA-256 khớp bản ghi
   ③  inspect_zip + zip_identity   4 file ở gốc, UTF-8, đúng lane/tập
   ④  StateStore                   row_key = "handoff:<job_id>" (khóa idempotency)
   ⑤  output_collector.normalize() đóng ZIP tất định → cùng nội dung = cùng SHA-256
   ⑥  zip_qc.inspect_result()      hình thức + độ dài + hook nguyên văn + định danh
   ⑦  destination()                SMOKE → folder thử nghiệm | PRODUCTION → OUTPUT (cần duyệt)
   ⑧  DriveUploader.upload_and_verify()   upload + đọc lại + so SHA-256
   ⑨  download lại ĐỘC LẬP          không tin kết quả bước ⑧
   ⑩  COMPLETED                     ghi StateStore + HandoffStore sau khi đã xác nhận
        │
        ▼
services/h2dev_delivery.py         ← MỚI (adapter đầu ra, chỉ ghi trong sandbox)
```

File mới: `services/handoff_coordinator.py` (674 dòng), `services/h2dev_delivery.py` (≈340 dòng),
`tests/test_phase53_handoff_coordinator.py` (28 test).
File sửa **chỉ để thêm**: `services/errors.py` (+13 mã lỗi), `config.yaml` (+2 khối), `bridge.py` (+5 lệnh con).

---

## 3. KẾT QUẢ TEST BẮT BUỘC (11 mục)

| # | Yêu cầu | Test | Kết quả |
|---|---|---|---|
| 1 | Job DOWNLOADED được coordinator tự phát hiện | `test_01_job_downloaded_duoc_tu_phat_hien` | **PASS** |
| 2 | Sai lane/episode bị chặn | `test_02_sai_lane_episode_bi_chan` | **PASS** (`E_HANDOFF_WRONG_EPISODE`) |
| 3 | ZIP lỗi bị chặn | `test_03_zip_loi_bi_chan`, `test_03b_zip_hong_han_bi_chan`, `test_03c_sha_doi_tren_dia_bi_chan` | **PASS** (`E_HANDOFF_BAD_ZIP`, `E_HANDOFF_ZIP_CHANGED`) |
| 4 | Một job không upload hai lần | `test_04_khong_upload_hai_lan` | **PASS** (lần 2: `SKIPPED`, 0 upload) |
| 5 | Restart sau upload, trước COMPLETED, không tạo bản trùng | `test_05_restart_sau_upload_khong_nhan_ban`, `test_05b`, `test_05c` | **PASS** (`mode=recover`, `uploads=0`) |
| 6 | Không COMPLETED nếu Drive verify thất bại | `test_06_khong_completed_khi_verify_that_bai` | **PASS** (`E_HANDOFF_NOT_COMPLETED`, hai store KHÔNG COMPLETED) |
| 7 | Job SMOKE không upload vào OUTPUT sản xuất | `test_07`, `test_07b`, `test_07c` | **PASS** (thiếu folder thử nghiệm → CHẶN, không fallback) |
| 8 | Job production không ghi đè ZIP đã có | `test_08`, `test_08b`, `test_08c` | **PASS** (`E_HANDOFF_OUTPUT_OCCUPIED`) |
| 9 | Một watcher logic duy nhất | `test_09`, `test_09b` | **PASS** (AST: không `scan_once`/`iterdir`/`rglob`/`walk`/`listdir`; đúng 1 `run_forever`) |
| 10 | Không chiếm chuột/bàn phím | `test_10_khong_chiem_chuot_ban_phim` | **PASS** (AST: không pyautogui/pynput/playwright/selenium/ctypes…) |
| 11 | Tất cả test Phase 1–5.2 vẫn PASS | toàn bộ `pytest` | **PASS** — 178 passed, 3 skipped |

Chi tiết theo file:

```
test_phase1_parser.py                    20 passed
test_phase2_orchestrator.py              17 passed
test_phase3_pipeline.py                  22 passed
test_phase4_adapters.py                  16 passed  3 skipped (cần BRIDGE_LIVE=1)
test_phase5_codex.py                     28 passed
test_phase5_handoff.py                   47 passed
test_phase53_handoff_coordinator.py      28 passed   ← mới
                                        ─────────────
                              TỔNG     178 passed, 3 skipped
```

Ngoài 11 mục bắt buộc, bộ test còn phủ: dry-run không ghi gì · phân loại SMOKE/PRODUCTION ·
`doctor` · xung đột với job khác đang dở · adapter H2DEV dựng gói + từ chối ghi ngoài sandbox.

---

## 4. NGHIỆM THU ĐẦU-CUỐI TRÊN DRIVE THẬT

Chạy bằng `scratch_analysis/phase53_acceptance.py`, **không thao tác tay nào tạo job trung gian**.

Chuỗi thực thi:

1. Job `SMOKE51-N-ep17-GPT6-v01` đặt về `SENT` (chờ người tải ZIP) — đúng điểm bắt đầu thật.
2. `ResultWatcher.scan_once()` thấy ZIP trong thư mục nhận → tự băm → tự chuyển `DOWNLOADED`.
3. `HandoffCoordinator.tick()` xử lý trọn 10 bước.
4. Kiểm lại **độc lập** bằng Drive API + tải file về băm lại.

| Bước | Kết quả thật |
|---|---|
| Watcher phát hiện | 1 job → `DOWNLOADED`, `result_sha256=6014dc51b13ce773…` |
| SHA-256 ZIP **đầu vào** | `6014dc51b13ce773afeb8b6f6e4f0eaf70f795b52b4022d18ca053373e49c994` |
| Chuẩn hoá (normalize) | `SMOKE51-N-ep17-GPT6-v01_GPT_SCRIPT_DELIVERY.zip` |
| SHA-256 ZIP **đầu ra** (đã chuẩn hoá) | `e4afc501a63069c2c5e5aa25a4566ec79b67175583f1169cb81a165f45033906` |
| QC | `PASS_FORMAT_ONLY` · **5.296 ký tự** · ngân sách 4.535–5.324 · hook 297 ký tự nguyên văn |
| Folder đích | `PHASE52_TEST_OUTPUT` (`<DRIVE_FOLDER_ID>`) — loại **TEST** |
| File trên Drive | `LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip` · id `<DRIVE_FOLDER_ID>` · 9.952 bytes |
| Đọc lại độc lập | `e4afc501a63069c2c5e5aa25a4566ec79b67175583f1169cb81a165f45033906` — **khớp** |
| Link | https://drive.google.com/file/d/<DRIVE_FOLDER_ID>/view |
| QC lại trên bản tải từ Drive | `PASS_FORMAT_ONLY` · 5.296 ký tự |
| Thời gian một tick | 9,8 giây (gồm tải hồ sơ vào + upload + đọc lại) |

**Số lần upload thật: 1.** Chạy lại cùng job ngay sau đó: `SKIPPED`, `uploads=0`, folder vẫn đúng 1 file.

---

## 5. ĐỒNG BỘ HAI STORE

Sau nghiệm thu:

| Store | Trạng thái | Dữ liệu |
|---|---|---|
| `StateStore` (`jobs/bridge.db`) | `COMPLETED` | `output_file_id=1P2pKaDX…`, `output_sha256=e4afc501a630…`, `output_name=LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip`, `row_key=handoff:SMOKE51-N-ep17-GPT6-v01` |
| `HandoffStore` (`jobs/_handoff/SMOKE51-N-ep17-GPT6-v01.json`) | `COMPLETED` | `coordinator.output_file_id` / `.output_sha256` / `.output_name` trùng khớp từng trường |

Vòng đời ghi nhận trong `StateStore`: `VERIFYING → UPLOADING → COMPLETED`.

Chính sách khi job bị chặn (đã kiểm bằng test):

* Chặn **nội dung** (`E_HANDOFF_BAD_ZIP`, `E_RESULT_LENGTH`, …) → HandoffStore `NEEDS_REVIEW`, người xử lý.
* Chặn **hành chính** (`E_HANDOFF_NOT_APPROVED`, `E_HANDOFF_NO_TEST_FOLDER`, `E_HANDOFF_OUTPUT_OCCUPIED`) → HandoffStore **giữ `DOWNLOADED`**, StateStore `BLOCKED` + `error_code`. Tick sau tự đánh giá lại ngay khi chủ dự án bật cờ — không phải tạo lại job.

---

## 6. TÌNH HUỐNG RESTART (crash giữa upload và cập nhật StateStore)

Cơ chế: journal `jobs/<job_id>/handoff_journal.json`, ghi **trước** khi upload
(`prepared → uploading → uploaded → completed`). Stage chỉ được **đi tới**, không lùi —
một lỗi sau upload không xoá mất manh mối phục hồi.

Quy tắc khi khởi động lại (đúng như yêu cầu: trạng thái không chắc chắn thì **kiểm Drive trước**):

| Journal | Drive | Hành động |
|---|---|---|
| `uploading` / `uploaded` | 1 file, SHA khớp | **Nhận lại** (`uploads=0`) — không upload lần hai |
| `uploading` / `uploaded` | 0 file | Upload lại (an toàn, Drive xác nhận trước đó không đáp) |
| bất kỳ | >1 file cùng tên, hoặc SHA lệch | `E_HANDOFF_CONFLICT` → dừng, người kiểm |

**Kiểm trên Drive thật** (`scratch_analysis/phase53_restart_live.py`): hạ `StateStore → UPLOADING`,
`HandoffStore → DOWNLOADED`, giữ journal `completed` + file trên Drive, rồi khởi động coordinator **mới**:

```
trước : folder thử nghiệm 1 mục, journal=completed, file=1P2pKaDX…
sau   : status=COMPLETED · mode=recover · uploads=0 · file_id=1P2pKaDX… (đúng file cũ)
folder: vẫn 1 mục — KHÔNG tăng
=> KHÔNG upload trùng: True
```

Test offline tương ứng: `test_05`, `test_05b`, `test_05c` (gồm cả nhánh crash thật giữa upload,
nhánh Drive trống, nhánh trùng tên nhưng SHA khác).

---

## 7. ẢNH HƯỞNG TỚI OUTPUT SẢN XUẤT VÀ H2DEV

**OUTPUT sản xuất — không bị chạm.** Trước và sau nghiệm thu, OUTPUT của `N-ep17_v01`
(`<DRIVE_FOLDER_ID>`) đều đúng 1 mục:

```
LaneN_tap17_GPT6_SCRIPT_OUTPUT.zip  ·  id <DRIVE_FOLDER_ID>
modifiedTime 2026-10-24T08:05:10.636Z   (không đổi)
```

Ba lớp chặn bảo vệ OUTPUT sản xuất:

1. `production_upload_enabled: false` ở cấp hệ thống — bật theo **từng job**, không bật toàn cục.
2. `preflight_destination()` — OUTPUT đã có ZIP thì `E_HANDOFF_OUTPUT_OCCUPIED`, không ghi đè
   (trừ khi job có cờ `revision_approved`).
3. Job SMOKE **không có đường** tới OUTPUT sản xuất: thiếu folder thử nghiệm → CHẶN;
   folder thử nghiệm trùng OUTPUT → CHẶN. Không có fallback.

**H2DEV — không bị sửa.** Adapter chỉ ghi trong `jobs/_delivery/`. Đã kiểm:
`E:\CLAUDE\YOUTUBE\h2dev\assets\O-kaigo-sukatto\ep23-yasuuri-shinai-yakuzaishi` **không tồn tại**
sau khi chạy `handoff deliver`. Ghi ra `h2dev/assets/**` bị `E_H2DEV_WRITE_DENIED` khi
`delivery.allow_production_write=false` (mặc định).

---

## 8. ADAPTER BÀN GIAO H2DEV (sandbox)

Lệnh: `python bridge.py handoff deliver SMOKE51-N-ep17-GPT6-v01 --slug ep17-kei-campingcar-kari-1kagetsu`

Kết quả thật trong `jobs/_delivery/ep17-kei-campingcar-kari-1kagetsu/`:

```
script.txt                       16.018 bytes · 5.296 ký tự
HANDOFF_DELIVERY_MANIFEST.json    2.137 bytes
_delivery_src/                    4 file gốc của ZIP
```

Manifest ghi: job ID, lane `N`, tập `ep17`, SHA-256 kịch bản, SHA-256 ZIP, Drive file ID,
`write_scope: sandbox`, và dòng trung thực: *"CHƯA qua QC nội dung/Sol của H2DEV;
KHÔNG kèm script.lock.json."*

**Tương thích `gate_common`** — dò bằng chính mã của H2DEV (nạp theo đường dẫn file,
không đưa vào `sys.path` toàn cục):

```
gate_module      E:\CLAUDE\YOUTUBE\h2dev\pipeline\gate_common.py
ghi_lock(PASS) → kiem_lock()  = PASS, khớp sha 595fe137cd…
ghi_lock(BLOCK) → kiem_lock() = FAIL  ("state=BLOCK — CHƯA được Sol PASS")
lock_removed     True   ← lock dò đã xoá, KHÔNG để lại lock PASS giả
```

Điểm quan trọng về tính trung thực: `script.lock.json` nghĩa là "Sol đã chấm PASS".
Kịch bản GPT6 trong Phase 5.3 **chưa qua Sol**, nên adapter cố ý không giữ lock —
chỉ chứng minh kịch bản này *sẽ* tương thích khi H2DEV ký lock thật.

---

## 9. NHỮNG GÌ CÒN THỦ CÔNG

| Việc | Ai làm | Vì sao chưa tự động |
|---|---|---|
| Bấm tải ZIP kết quả trong ChatGPT | Người vận hành (Chrome Extension) | Ngoài phạm vi Phase 5.3; extension đã lo phần prompt/queue |
| Bật cờ phê duyệt cho từng job production | Chủ dự án | Yêu cầu an toàn: `production_upload_enabled: false` mặc định |
| Chạy Sol để ký `script.lock.json` thật | H2DEV | Không sửa H2DEV ở Phase 5.3; Sol PASS là cổng của H2DEV |
| Chép gói bàn giao từ sandbox vào `h2dev/assets/<lane>/<slug>/` | Chủ dự án | `allow_production_write=false`; cần phê duyệt tường minh |
| Đặt `upload.test_output_folder_id` khi dựng máy mới | Người vận hành | Đã điền `<DRIVE_FOLDER_ID>` trên máy này |
| QC nội dung tiếng Nhật / mạch truyện / dữ kiện | H2DEV | `inspect_result()` chỉ kiểm hình thức, độ dài, hook, định danh |

---

## 10. CẤU HÌNH ĐÃ THÊM

```yaml
upload:
  test_output_folder_id: "<DRIVE_FOLDER_ID>"   # PHASE52_TEST_OUTPUT
  production_upload_enabled: false

handoff_coordinator:
  poll_sec: 10
  max_jobs_per_tick: 3
  journal_name: handoff_journal.json
  production_upload_enabled: false    # phê duyệt theo TỪNG job
  test_output_folder_id: null         # trống = dùng upload.test_output_folder_id

delivery:
  sandbox_dir: jobs/_delivery
  h2dev_root: E:/CLAUDE/YOUTUBE/h2dev
  production_root: E:/CLAUDE/YOUTUBE/h2dev/assets
  allow_production_write: false
  script_lock_probe: true
```

Lệnh vận hành:

```
python bridge.py handoff doctor              # kiểm mối nối
python bridge.py handoff tick --dry-run      # đo, không ghi gì
python bridge.py handoff tick                # một lượt thật
python bridge.py handoff watch               # vòng lặp duy nhất (dừng bằng temp/STOP)
python bridge.py handoff recover             # soi journal sau crash
python bridge.py handoff deliver <job_id> --slug <slug>
```

---

## 11. ĐỀ XUẤT TẬP PRODUCTION KẾ TIẾP

Đã kiểm **cả ba nguồn** (Drive · Sheet · H2DEV). Hai ứng viên sạch:

| | **Lane O · ep23** (khuyến nghị) | Lane M · ep19 |
|---|---|---|
| Thư mục Drive tập | **không tồn tại** (`E_DRIVE_NOT_FOUND`) | **không tồn tại** |
| Đối chứng tập liền trước | ep22: có folder + có slug H2DEV ✔ | ep18: `M-ep18_v04` OUTPUT có ZIP ✔ |
| Slug H2DEV | **không có** (22 slug, cao nhất ep22) | **không có** (16 slug, cao nhất ep16) |
| Dòng Sheet | `Lane-O` dòng 24 · STT 23 | `M-teinengo-anaba` dòng 20 · STT 19 |
| Cột D | trống | trống |
| Cột K | `RESEARCH OK 2026-10-23 12:01` | `RESEARCH OK 2026-10-23 13:37` |
| Cột C | `…\O-kaigo-sukatto\ep23-yasuuri-shinai-yakuzaishi` — **thư mục chưa tồn tại** | `…\M-teinengo-anaba\ep19-hanashikata-7sen-shigoto-dekinai` — **chưa tồn tại** |

**Khuyến nghị: Lane O · tập 23 · slug `ep23-yasuuri-shinai-yakuzaishi`**

> Nguyên tác: 【漫画】「私、安売りはしないので」が口ぐせの薬剤師。断った男性は後輩と結婚
> Dịch: *Dược sĩ có câu cửa miệng "Tôi không bán rẻ mình đâu". Người đàn ông bị cô từ chối đã cưới đàn em của cô.*

Lý do chọn O-ep23 thay vì M-ep19:

1. Lane O đang chạy liền mạch tới ep22 — ep23 là tập kế tự nhiên, ít rủi ro ngữ cảnh series.
2. Lane M có 2 slug trùng số (`ep16-5thoi-quen` và `ep16-mitsumori`) và ep17 đã `Done` nhưng **chưa có** trong H2DEV → dữ liệu lane M đang lệch, nên xử lý riêng trước khi đưa vào production.
3. Cả hai đều đã có research (cột K = `RESEARCH OK`) nhưng **chưa có kịch bản** — đúng trạng thái cần GPT6 viết.

**Chưa khởi chạy gì.** Theo yêu cầu, production chỉ bật sau khi chủ dự án duyệt. Khi duyệt, thứ tự sẽ là:

1. Tạo `h2dev/assets/O-kaigo-sukatto/ep23-yasuuri-shinai-yakuzaishi/` + hồ sơ tập (H2DEV, không phải Phase 5.3).
2. Đặt `job_class: PRODUCTION` + `production_upload_enabled: true` **cho đúng job đó**.
3. Chạy `bridge.py handoff tick` — coordinator upload vào OUTPUT `O-ep23_vXX` sau khi QC PASS.

---

## 12. GHI CHÚ TRUNG THỰC

* `inspect_result()` chỉ kiểm **hình thức / độ dài / hook nguyên văn / định danh**. Kết quả
  `PASS_FORMAT_ONLY` **không** có nghĩa kịch bản đúng nội dung — vẫn phải qua QC của H2DEV.
* Nghiệm thu dùng job `SMOKE51` vì đây là job duy nhất đang ở `DOWNLOADED` và đã có ZIP sản xuất
  trong Drive OUTPUT từ trước. **Không** dùng N-ep17 làm tập production mới.
* Folder thử nghiệm `PHASE52_TEST_OUTPUT` là folder riêng, **không** phải OUTPUT của tập nào.
  File nghiệm thu `LaneN_tap17_GPT6_SCRIPT_DELIVERY.zip` nằm ở đó, không nằm trong OUTPUT sản xuất.
* Không có credential/khóa nào bị in ra trong quá trình nghiệm thu.
* Không dùng Codex, Sol hay API writer. Không sửa, không ghi đè dữ liệu H2DEV.
