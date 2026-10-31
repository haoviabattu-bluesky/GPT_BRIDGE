# ADAPTER_FEASIBILITY — có điều khiển được ChatGPT Web không?

**Kết luận ngắn: CHƯA chứng minh được.** V1 chạy ở chế độ **MANUAL** (người thao tác
dán prompt + thả ZIP). Adapter tự động đã viết nhưng **mặc định TẮT** và **chưa được
kiểm chứng với phiên ChatGPT thật**. Không có tuyên bố tự động 100% nào trong tài liệu này.

## 1. Đã kiểm chứng được gì (bằng chứng thật)

| Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|
| Giao diện adapter 3 phương thức | ✅ Chạy | `test_all_adapters_share_interface` |
| Mock adapter sinh ZIP đúng chuẩn | ✅ Chạy | `test_collect_produces_4_files`, dry-run bước 5 |
| Manual adapter ghi gói giao việc | ✅ Chạy | `test_submit_writes_handoff_package` |
| Manual nhận ZIP thả vào inbox | ✅ Chạy | `test_collect_from_inbox`, `test_status_result_available_when_zip_dropped` |
| Web adapter **chặn** khi không có CDP | ✅ Chạy | `test_submit_blocked_without_cdp` → `E_ADAPTER_BLOCKED` |
| Web adapter **không** tự mở Chrome | ✅ Chạy | `test_no_launch_by_default` |
| Web adapter **không** vượt CAPTCHA | ✅ Chạy (quét mã) | `test_never_bypasses_captcha` |
| API chính thức **bị chặn** khi chưa duyệt | ✅ Chạy | `test_submit_blocked_when_disabled` |

## 2. CHƯA kiểm chứng (và không được nói là đã xong)

| Hạng mục | Vì sao chưa |
|---|---|
| Gửi prompt vào đúng cuộc chat GPT-6 | Chưa chạy với tài khoản ChatGPT thật |
| Chọn đúng model GPT-6 trên UI | UI đổi thường xuyên; selector chưa đối chiếu DOM thật |
| ChatGPT đọc được ZIP qua Drive connector | Spec §11 cảnh báo rõ: không suy luận từ session này sang session khác |
| Phát hiện "đã viết xong" | Dựa vào nút/stop-generating — chưa đo trên UI thật |
| Nhận link artifact / tải ZIP kết quả | Chưa có lần chạy thật nào |
| Chạy trong phiên trình duyệt độc lập, không chiếm desktop | Cơ chế đã viết (`--user-data-dir` riêng + CDP), **chưa đo** |
| Tuân thủ điều khoản sử dụng ChatGPT | **Chưa xác nhận với OpenAI** — rủi ro pháp lý thuộc người vận hành |

## 3. Đánh giá repo tham chiếu `autowebprompt`

Đã tải mã nguồn về `vendor/autowebprompt-main/` và đọc trực tiếp, **không dựa vào README**.

Những gì repo làm được (theo mã):
- Điều khiển Chrome qua CDP/Playwright, tạo prompt từ template.
- Có lớp "agent" cho chatgpt.com: tìm ô nhập, gõ, bấm gửi.

Những gì repo **không** giải quyết cho bài toán này:
- Không có khái niệm job/state/retry như GPT_BRIDGE cần.
- Không có QC ZIP, không có upload Drive, không có chống trùng.
- Không xử lý trường hợp ChatGPT không đọc được ZIP.
- Không có cơ chế "không rõ GPT đã nhận job hay chưa" → dễ gửi trùng.

→ Kết luận: **học được cách attach CDP và thao tác composer**, nhưng **không** dùng repo
như một giải pháp hoàn chỉnh. Đã vendor để tham chiếu, không import vào đường tới hạn.

## 4. Vì sao adapter web mặc định TẮT

1. **Chưa kiểm chứng** — bật một adapter chưa đo là tự lừa mình.
2. **Điều khoản** — tự động hoá ChatGPT Web không phải API chính thức (spec §9).
3. **Rủi ro gửi trùng** — nếu submit xong mà không chắc GPT đã nhận, gửi lại sẽ tạo
   job trùng. Đây là lý do có mã lỗi `E_SUBMIT_UNCERTAIN`.
4. **An toàn máy người dùng** — spec §17 cấm chiếm chuột/bàn phím, cấm đóng Chrome chính.

## 5. Điều kiện để được bật AUTO (checklist, chưa đạt mục nào)

- [ ] Chạy được trên Chrome riêng (`--user-data-dir` tách biệt), **không** cửa sổ nào hiện ra
      trước mặt người dùng.
- [ ] Đo được: gửi 1 prompt, nhận đúng 1 câu trả lời, không nhiễm context tập khác.
- [ ] Đo được: phát hiện đúng thời điểm GPT viết xong (không cắt giữa).
- [ ] Đo được: lấy được 4 file kết quả **nguyên vẹn** (so hash với bản GPT hiển thị).
- [ ] Chứng minh: không có hành vi vượt CAPTCHA/đăng nhập hộ.
- [ ] Xác nhận phương thức được phép dùng (điều khoản).
- [ ] Chạy 5 tập liên tiếp không trùng job, không mất dữ liệu khi restart.

Chỉ khi **tất cả** ô trên được tick bằng bằng chứng thật, mới đổi
`writer.adapter: chatgpt_web` và bật `supports_auto` trong báo cáo.

## 6. Đường đang dùng thay thế (MANUAL) — hoạt động thật hôm nay

```
bridge.py detect → validate → prepare
   -> jobs/<job_id>/GPT6_TASK_MESSAGE.txt     (nội dung dán vào ChatGPT)
   -> jobs/<job_id>/CHATGPT_WEB_HANDOFF_GUIDE.md (hướng dẫn từng bước)
   -> jobs/<job_id>/MANUAL_ACTION_REQUIRED.txt   (nhắc việc)
Người: dán prompt vào ChatGPT Web (GPT-6), tải 4 file, đóng 1 ZIP
Người: thả ZIP vào jobs/_inbox/
Hermes: bridge.py resume  -> QC -> upload OUTPUT -> verify -> COMPLETED
```

Phần **sau** khi có ZIP là tự động hoàn toàn. Phần **gửi/nhận với ChatGPT** là thủ công.
Đây là mức tự động thật của V1 — không hơn.

## 7. Đường nâng cấp rẻ nhất (khuyến nghị)

Nếu OpenAI cấp API key cho GPT-6: bật `writer.adapter: official_api`,
đặt `OPENAI_API_KEY`. Adapter đã viết sẵn, đã bị chặn đúng khi thiếu key.
Khi đó AUTO là **hợp pháp và ổn định**, không phụ thuộc UI.
