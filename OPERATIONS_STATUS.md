# GPT_BRIDGE — TRẠNG THÁI VẬN HÀNH

**Cập nhật:** 2026-10-25 · **Phase 5.4:** đã chốt · **Điều kiện #8:** đã siết
**Test:** 247 passed, 3 skipped
**Chế độ:** theo dõi hồ sơ. Không tự khởi chạy tập mới.

---

## 1. Dịch vụ đang chạy

| Thành phần | Trạng thái | Bằng chứng |
|---|---|---|
| HTTP server `127.0.0.1:8765` | ✅ | `/health` → `ok:true`, `version 5.0.0`, đúng `conversation_url`; listener PID 33384 |
| Watcher (`cli watch`) | ✅ | PID 34700 (shim) + 39608 (interpreter thật) — **1 watcher logic** |
| Extension đọc `jobs/_handoff/` | ✅ | 1 file, khớp job `SMOKE51-N-ep17-GPT6-v01` |
| Prompt integrity | ✅ | `/job/<id>` trả **2402 = 2402** ký tự, bằng tuyệt đối bản đã lưu |
| Hàng đợi | 1 job `COMPLETED` | không có job chờ |

> Server đã **tắt** trước phiên này (chỉ watcher sống). Đã khởi động lại và verify `/health`.
> Watcher đang chủ động chặn đúng: log ghi `E_HANDOFF_NOT_APPROVED`, `E_HANDOFF_OUTPUT_OCCUPIED`,
> `E_HANDOFF_TEST_EQUALS_PRODUCTION` cho job smoke — tức là nó **đang làm việc**, không nằm im.

## 2. Trạng thái hồ sơ (baseline, không đổi so với Phase 5.4)

| | Số tập | |
|---|---|---|
| **Đủ điều kiện (QUEUED)** | **0** | chưa có tập nào |
| Chờ Fabel (RESEARCH OK) | 5 | O-ep23 · P-ep14 · Q-ep7 · F-ep22 · M-ep19 |
| Đã có kịch bản | 4 | N-ep17 · M-ep18 · L-ep27 · E-ep5 |

## 3. Giám sát tự động

Cron `25f507723ba5` — mỗi **30 phút**, chạy `gate_watch.py`, chế độ `no_agent`.

- **Im lặng** khi chưa có tập đủ điều kiện (không gửi tin nhắn rỗng).
- **Báo ngay** khi xuất hiện tập `QUEUED`: lane-tập, tab, dòng, tên ZIP, SHA256, INPUT/OUTPUT, số chữ hook, kèm lệnh `--apply` để anh duyệt.
- Đã test cả hai nhánh bằng dữ liệu tiêm: lần đầu **báo**, lần hai **im** (chống lặp).
- Lỗi hạ tầng → **báo**, không im lặng.
- Chỉ đọc: không `--apply`, không tạo job, không ghi Sheet.

## 4. Điều kiện vào hàng đợi (yêu cầu #8) — ĐÃ SIẾT

Job chỉ vào `QUEUED` khi **8 điều kiện kỹ thuật PASS *và* có phê duyệt production**.
Trước đây gate chỉ kiểm 8 điều kiện + `--apply`; nay đã thêm cửa phê duyệt.

| Tình huống | Kết quả |
|---|---|
| Ô K = RESEARCH OK | `WAITING_OUTLINE` |
| SƯỜN OK, thiếu ZIP | `WAITING_HANDOFF_ZIP` |
| ZIP sai lane/tập | `REJECTED` `E_GATE_ZIP_WRONG_EPISODE` |
| ZIP bị sửa sau đóng gói | `REJECTED` `E_GATE_ZIP_REVISION` |
| OUTPUT đã có ZIP | `REJECTED` `E_GATE_OUTPUT_OCCUPIED` |
| Đủ 8 điều kiện, **chưa duyệt** | **`WAITING_APPROVAL`** `E_GATE_NOT_APPROVED` |
| Đủ 8 điều kiện, **đã duyệt**, có `--apply` | `QUEUED` |

`WAITING_APPROVAL` **không phải lỗi** — là trạng thái chờ người. `--apply` **không**
thay thế phê duyệt. Gate chỉ **đọc** cờ `production_upload_enabled`, không bao giờ tự bật.

Thứ tự kiểm quan trọng: phê duyệt là **cửa cuối**. Hồ sơ còn thiếu ZIP vẫn báo
`WAITING_HANDOFF_ZIP`, không nhảy sang `WAITING_APPROVAL` — để người vận hành không
tưởng chỉ cần bật cờ là chạy được.

Nguồn phê duyệt (cùng nguồn với coordinator): cờ trên bản ghi job, hoặc
`handoff.production_upload_enabled`, hoặc `upload.production_upload_enabled`.
Hiện **cả hai đều `false`** → không job nào vào hàng đợi.

## 5. Cam kết đang giữ

- ❌ Không tự tạo job production khi chưa có ZIP Fabel hợp lệ
- ❌ Không gọi Fabel · không sửa trạng thái Sheet
- ❌ Không dùng Codex / Sol / API writer — writer là **ChatGPT Web** trong hội thoại đã cấu hình
- ❌ Không sửa / ghi đè OUTPUT đã có
- ❌ Không đánh dấu kịch bản đã được Sol duyệt — việc đó thuộc H2DEV

## 6. Khi có ZIP Fabel

```bash
cd E:\CLAUDE\YOUTUBE\GPT_BRIDGE
.venv\Scripts\python.exe bridge.py gate --tab <tab-lane> --only <lane>-ep<N>
```

Đủ 8 điều kiện → `QUEUED`. Sau đó (theo #9): nhận ZIP từ GPT-6 → tự QC → upload OUTPUT →
kiểm SHA256 → đánh dấu `GPT_DELIVERED`. Duyệt kịch bản + chạy tiếp: **H2DEV**.
