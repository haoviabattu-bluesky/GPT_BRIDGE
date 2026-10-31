# GPT_BRIDGE — PHASE 5.4: PRODUCTION READINESS — BÁO CÁO NGHIỆM THU

**Ngày:** 2026-10-25 · **Kết quả:** PASS — **236 passed, 3 skipped**
**Phạm vi:** thêm lớp preflight quyết định điều kiện viết kịch bản. Không viết lại hệ thống.

---

## 1. Kết luận trước, bằng chứng sau

**Hôm nay KHÔNG có tập nào đủ điều kiện viết kịch bản.** Đã quét 8 tab lane thật:

| Kết quả | Số tập | Nghĩa |
|---|---|---|
| **QUEUED** (đủ điều kiện) | **0** | không có tập nào |
| WAITING_OUTLINE | 5 | mới RESEARCH OK, chờ Fabel làm Hook + Outline |
| ALREADY_SCRIPTED | 4 | đã có kịch bản rồi, không phải job viết |
| WAITING_HANDOFF_ZIP | 0 | — |
| REJECTED | 0 | — |

5 tập đang chờ Fabel:

| Lane | Tập | Tab | Trạng thái ô K |
|---|---|---|---|
| O | ep23 | Lane-O | RESEARCH OK 2026-10-23 12:01 |
| P | ep14 | Lane-P | RESEARCH OK 2026-10-23 12:03 |
| Q | ep7 | Lane-Q | RESEARCH OK 2026-10-23 12:02 |
| F | ep22 | F-teinengo-real | RESEARCH OK 2026-10-23 11:59 |
| M | ep19 | M-teinengo-anaba | RESEARCH OK 2026-10-23 13:37 |

**O-ep23 trả `WAITING_OUTLINE`** — đúng quyết định của anh. Không tạo job GPT, không gọi Drive.

4 tập đã có kịch bản (N-ep17, M-ep18, L-ep27, E-ep5) → `ALREADY_SCRIPTED`, bridge không đụng.

---

## 2. Đã làm gì

### 2.1 Module mới: `services/script_gate.py` (1 file, 590 dòng)

Cổng quyết định 8 bước, dừng ở điều kiện đầu tiên không đạt:

| Bước | Kiểm | Không đạt → |
|---|---|---|
| 1 | Marker ô K (dòng ĐẦU TIÊN thắng) | `WAITING_OUTLINE` / `ALREADY_SCRIPTED` / `E_GATE_NO_OUTLINE` |
| 2 | Folder tập → INPUT/OUTPUT bằng API | `E_DRIVE_*` |
| 3 | ZIP hồ sơ Fabel **có thật** trong INPUT | `WAITING_HANDOFF_ZIP` / `E_GATE_ZIP_NOT_FABEL` |
| 4 | Mã tập + lane trong ZIP trùng job | `E_GATE_ZIP_WRONG_EPISODE` |
| 5 | Revision: checksum khớp byte thật | `E_GATE_ZIP_REVISION` |
| 6 | Không có job khác đang dở | `E_GATE_DUPLICATE_JOB` |
| 7 | OUTPUT chưa có ZIP kịch bản | `E_GATE_OUTPUT_OCCUPIED` |
| 8 | Tất cả đạt | **`QUEUED`** |

**Mặc định CHỈ ĐỌC.** Chỉ `--apply` mới ghi `HandoffStore.enqueue()`. Không gửi gì vào ChatGPT.

### 2.2 Sửa nhỏ, có chủ đích (3 điểm)

| File | Thay đổi | Vì sao |
|---|---|---|
| `services/sheet_scanner.py` | thêm `scan_gate()` | `scan()` bỏ hàng `RESEARCH OK` (coi là `ignore`) → preflight không thấy được. `scan()` **không đổi**. |
| `services/handoff_coordinator.py` | +2 dòng: `gpt_state=GPT_DELIVERED`, `script_approved=False` tại điểm COMPLETED | ranh giới trách nhiệm. Không có hằng `SOL_APPROVED` nào. |
| `services/errors.py` | +8 mã `E_GATE_*` | mỗi tình huống một mã để người vận hành xử lý đúng |

### 2.3 CLI: `bridge.py gate`

```
bridge.py gate [--apply] [--only O-ep23 N-ep17] [--tab Lane-O ...] [--all]
```

Mặc định in báo cáo và **không xếp hàng**. Dùng `--apply` mới đưa job đủ điều kiện vào hàng đợi.

---

## 3. Hai phát hiện từ dữ liệu THẬT (quan trọng)

### 3.1 `hook_outline_checksum` KHÔNG phải sha256 file

Lần đầu tôi giả định nó là hash của `01_HOOK_OUTLINE.txt` và so trực tiếp. **Sai.** Đo trên hồ sơ N-ep17 thật:

```
hook_outline_checksum      = e2a9a774…   ← ID nội bộ
per_file_checksums[01_…]   = e7f1f6ef…   ← = sha256(file thật)      ✅ đo được
hook_sol.hook_sha256       = 932bbb5d…   ← = sha256(KHỐI HOOK tách) ✅ đo được
```

Nếu giữ giả định sai, **mọi hồ sơ thật sẽ bị chặn**. Đã sửa sang `per_file_checksums` + `hook_sol.hook_sha256`, và khoá lại bằng `tests/test_phase54_real_manifest.py` (8 test).

### 3.2 Không dùng được `identity_problems()` của watcher cho hồ sơ vào

Watcher được thiết kế cho **ZIP kết quả** (có `SCRIPT_QC_REPORT.md` khai tập). Hồ sơ Fabel không có file đó và đặt tên `LaneN_tap17_…` (`tap17`, không phải `ep17`) → watcher kết luận *"ZIP không khai số tập"* và **chặn nhầm hồ sơ hợp lệ**.

Đã viết `handoff_identity()` + `identity_mismatch()` đọc thêm manifest (nguồn chuẩn nhất) và hiểu quy ước `tap<N>`. Có test `test_old_watcher_identity_would_have_rejected_this` ghi lại đúng lý do.

---

## 4. Ma trận nghiệm thu

| # | Tình huống | Kết quả | Test |
|---|---|---|---|
| 1 | RESEARCH OK | `WAITING_OUTLINE`, không tạo job | `test_research_ok_returns_waiting_outline` |
| 2 | SƯỜN OK thiếu ZIP | `WAITING_HANDOFF_ZIP` | `test_suon_ok_without_zip_returns_waiting_handoff_zip` |
| 3 | SƯỜN OK đủ ZIP | `QUEUED` | `test_suon_ok_with_valid_zip_is_queued` |
| 4 | Sai lane/tập | `REJECTED` `E_GATE_ZIP_WRONG_EPISODE` | `test_wrong_lane_in_zip_is_rejected`, `test_wrong_episode_in_zip_is_rejected` |
| 5 | Đã có OUTPUT | `REJECTED` `E_GATE_OUTPUT_OCCUPIED` | `test_existing_output_blocks_queue` |

**Cộng thêm** (bất biến Phase 5.4):

| # | Bất biến | Test |
|---|---|---|
| 6 | ZIP không phải hồ sơ Fabel | 4 test (`junk`, thiếu KHUNG SƯỜN, thiếu manifest, thiếu phiên Fabel) |
| 7 | Hồ sơ bị sửa sau đóng gói | `test_tampered_hook_file_is_rejected` |
| 8 | Tập đã có job đang dở | 2 test (StateStore + HandoffStore) |
| 9 | Ô K "cần kịch bản" không có sườn | `test_need_script_without_outline_marker_is_rejected` |
| 10 | Mặc định chỉ đọc | `test_dry_preflight_writes_nothing` |
| 11 | `--apply` chỉ ghi QUEUED | `test_apply_enqueues_only_after_all_checks` |
| 12 | Chỉ `GPT_DELIVERED`, không duyệt Sol | 4 test trong `test_phase54_boundary.py` |
| 13 | Không Codex/Sol/API writer | `test_gate_does_not_import_forbidden_writers` (kiểm AST, không grep chữ) |
| 14 | Không tạo ZIP giả / không ghi Sheet | `test_gate_does_not_create_zip`, `test_gate_does_not_touch_sheet` |

### Kiểm chứng trên Drive THẬT (không chỉ test)

Chạy preflight thật cho N-ep17 (`LaneN_tap17_GPT_SCRIPT_HANDOFF.zip` có thật trong INPUT):

```
hook_chars        : 297          (khớp header "297 chữ")
outline_present   : True
fabel.model       : claude-fable-5-1
fabel.sessions    : 4
revision.file     : e7f1f6ef… == e7f1f6ef…   ✅
revision.hook     : 932bbb5d… == 932bbb5d…   ✅
identity          : lane N · ep17             ✅
→ STATUS: REJECTED · E_GATE_OUTPUT_OCCUPIED
```

Hồ sơ Fabel hợp lệ đi qua **đủ 6 bước đầu**, dừng đúng ở bước 7 vì OUTPUT đã có `LaneN_tap17_GPT6_SCRIPT_OUTPUT.zip`. Đây là hành vi đúng: **không ghi đè**.

---

## 5. Phase 5.3 không bị ảnh hưởng

```
tests/test_phase53_handoff_coordinator.py   28 passed   (nguyên vẹn)
```

Toàn bộ 181 test cũ vẫn xanh. Thay đổi duy nhất trong `handoff_coordinator.py` là 2 dòng gán thêm sau khi đã `COMPLETED` — không đổi luồng, không đổi thứ tự, không đổi điều kiện.

Tổng: **236 passed, 3 skipped** (3 skip là test `live` cần `BRIDGE_LIVE=1`).

---

## 6. Ranh giới trách nhiệm (đã khoá bằng test)

| Việc | Ai làm | Bridge có làm? |
|---|---|---|
| Research, tải hồ sơ video gốc | lane_feed | ❌ |
| Hook + Outline | **Fabel** | ❌ không tự gọi |
| Đóng gói ZIP hồ sơ | Fabel / H2DEV | ❌ không tạo ZIP giả |
| Quyết định đủ điều kiện | **bridge (Phase 5.4)** | ✅ |
| Viết kịch bản | ChatGPT Web | ❌ không gửi hộ |
| Upload OUTPUT + xác minh | bridge (Phase 5.3) | ✅ |
| Đánh dấu `GPT_DELIVERED` | **bridge** | ✅ |
| **Duyệt kịch bản (Sol) + chạy tiếp** | **H2DEV** | ❌ **không đánh dấu** |
| Sửa trạng thái Sheet | người vận hành | ❌ |

---

## 7. Cách dùng

```bash
cd E:\CLAUDE\YOUTUBE\GPT_BRIDGE

# Xem tập nào đủ điều kiện (CHỈ ĐỌC — mặc định)
.venv\Scripts\python.exe bridge.py gate --tab Lane-N Lane-O Lane-P Lane-Q F-teinengo-real M-teinengo-anaba L-shakai-no-naze E-70dai-kenko

# Kiểm một tập cụ thể
.venv\Scripts\python.exe bridge.py gate --tab Lane-O --only O-ep23

# Đưa job đủ điều kiện vào hàng đợi (chỉ khi đã được duyệt)
.venv\Scripts\python.exe bridge.py gate --tab Lane-O --apply
```

Sau `--apply`, job ở `state=QUEUED` trong `HandoffStore` — extension đọc hàng đợi đó. Vẫn **không tự gửi** gì vào ChatGPT.

---

## 8. Việc chưa làm (theo đúng yêu cầu)

- ❌ **Không** tự khởi chạy production — chỉ báo cáo.
- ❌ **Không** tạo job cho O-ep23 (mới RESEARCH OK).
- ❌ **Không** chạy smoke test N17.
- ❌ **Không** dùng Codex CLI / Sol / API writer.
- ❌ **Không** đánh dấu kịch bản nào đã được Sol duyệt.

---

## 9. File thay đổi

| File | Loại | Dòng |
|---|---|---|
| `services/script_gate.py` | **mới** | 590 |
| `tests/test_phase54_script_gate.py` | **mới** | 38 test |
| `tests/test_phase54_boundary.py` | **mới** | 12 test |
| `tests/test_phase54_real_manifest.py` | **mới** | 8 test |
| `services/sheet_scanner.py` | sửa | +`scan_gate()`, tách `_scan_rows()` |
| `services/handoff_coordinator.py` | sửa | +6 dòng |
| `services/errors.py` | sửa | +8 mã lỗi |
| `bridge.py` | sửa | +`gate` command, `_lane_of_req`, `_brief` |
| `scratch_analysis/probe_phase54_*.py` | mới (công cụ đo, không thuộc production) | 3 file |

---

## 10. Đề xuất bước tiếp theo

1. **Fabel chạy Hook + Outline cho 5 tập đang chờ** (O-ep23, P-ep14, Q-ep7, F-ep22, M-ep19). Đây là việc của H2DEV, không phải bridge.
2. Khi ZIP hồ sơ lên INPUT, chạy `gate --tab <lane>` — hệ thống tự báo `QUEUED`.
3. **Chỉ khi anh duyệt** mới chạy `--apply` để xếp hàng.

Bridge đã sẵn sàng. Nó sẽ không tự làm gì cho tới khi có ZIP thật và có lệnh của anh.
