# AGENT_LOOP_IMPLEMENTATION_REPORT — Báo cáo triển khai

> Ngày: 2026-10-25 · Thư mục: `E:\CLAUDE\YOUTUBE\GPT_BRIDGE\agent_loop\`
> Nguyên tắc: mỗi dòng dưới đây đều là thứ **đã ghi ra đĩa và đã chạy được**, không phải thiết kế trên giấy.

---

## 1. Đã tạo những gì

### 1.1 Bộ máy `agent_loop/` (mới hoàn toàn)

| Tệp | Dòng | Việc |
|---|---|---|
| `transport.py` | 187 | Mở/giữ Chrome riêng qua CDP cổng 9335, cửa sổ ngoài màn hình, không cướp focus |
| `send_recv.py` | 274 | Gửi tin + chờ nhận đủ; chống bẫy bản nháp, đọc sớm, tin quá dài |
| `protocol.py` | 209 | `TASK_ASSIGNMENT.md` / `EXECUTION_REPORT.md`, SHA256, manifest, chống gán nhầm job |
| `state.py` | 168 | Máy trạng thái, khoá độc quyền theo PID, checkpoint, chống gửi trùng |
| `loop.py` | 264 | Vòng điều phối: gửi → nhận → parse → chạy → lặp |
| `executor.py` | 460 | `SURVEY` / `IMPLEMENT` / `TEST`, đọc chỉ thị để trả đúng câu hỏi |
| `mg_implement.py` | 130 | Việc THẬT trên MG_STUDIO, chỉ thêm tệp mới, trả SHA256 |
| `cli.py` | 109 | `new` / `run` / `show` / `self-test` |
| `__init__.py` | 29 | Điểm vào gói |

### 1.2 Hai module mới cho MG_STUDIO

| Tệp | Việc | Vì sao cần |
|---|---|---|
| `core/khoa_hook.py` | Khoá Hook đã duyệt bằng SHA-256 | Luật 12 nói hook là bất khả xâm phạm nhưng **trước đây không có cờ máy nào chặn**. `core/khoa.py` chỉ khoá `canh.json`, không khoá hook |
| `core/trang_thai_qc.py` | `qc_rounds <= 1`, resume không reset, Opus 1 lần | Quy trình mục tiêu nói "sửa tối đa 1 lần" nhưng ràng buộc chỉ nằm trong văn bản, không có biến đếm |
| `tests/test_khoa_hook_qc.py` | 22 bài nghiệm thu | Chứng minh 2 module trên chạy thật |

**Không sửa một dòng nào** trong các tệp đang vận hành của MG_STUDIO/H2DEV.
Đây là lý do chọn cách "thêm module mới": pipeline hiện tại không đổi hành vi.

### 1.3 Tài liệu

- `AGENT_LOOP_ARCHITECTURE.md` — kiến trúc, state machine, giao thức
- `AGENT_LOOP_IMPLEMENTATION_REPORT.md` — tệp này
- `AGENT_LOOP_TEST_REPORT.md` — kết quả đo thật
- `AGENT_LOOP_RUNBOOK.md` — sổ tay vận hành + bảng lỗi

---

## 2. Giao thức (mục B) — đã cài đặt

Mỗi nhiệm vụ có đủ 9 trường yêu cầu:

```
project_id · job_id · conversation_id · round · task_status
report_path · report_sha256 · next_action · created_at
```

**Chống gán nhầm job** (mục F): mỗi báo cáo mang `job_id` + `round` + SHA256.
`parse_report(expect={...})` từ chối nếu lệch bất kỳ trường nào. Có bài tự kiểm
`reject_wrong_job` chứng minh.

**Nội dung lớn**: báo cáo >3000 ký tự **tự động** ghi ra `jobs/_spill/` và đính kèm,
chỉ phần đầu vào tin nhắn — đúng yêu cầu "file lớn lưu riêng + manifest SHA256".

---

## 3. Máy trạng thái (mục C) — đã cài đặt

```
CREATED → TASK_READY → EXECUTING → REPORT_READY → GPT_REVIEW → NEXT_TASK → EXECUTING
Kết thúc: COMPLETED
Chặn:     BLOCKED · TRANSPORT_UNAVAILABLE · NEEDS_OWNER_ACTION
```

Đã cưỡng chế bằng mã:
- **Không hai lượt đồng thời**: khoá file `run.lock` ghi PID; tiến trình thứ hai
  bị từ chối (`lock_exclusive` PASS).
- **Không nhảy trạng thái**: `no_state_skip` PASS.
- **Chống gửi trùng**: băm nội dung, nếu đã gửi thì bỏ qua (`dedup_send` PASS).
- **Không coi "chưa nhận được trả lời" là PASS**: `send()` chỉ thành công khi
  `turnCount` **tăng**; hết thời gian chờ → `E_SEND_UNCERTAIN`, không đoán.

---

## 4. Bốn bẫy lớn đã gặp và cách sửa

Đây là phần đáng đọc nhất — mỗi bẫy đều được **đo**, không phải suy đoán.

### Bẫy 1 — Selector cũ đã chết
`[data-message-author-role]` trả **0 khớp** dù trang có tin nhắn.
→ Đo lại DOM, dùng `[data-turn-key]` (mỗi lượt) + `[data-markdown-copy]` (khối nội dung).
Ghi chú trong mã để lần sau không dùng lại selector cũ.

### Bẫy 2 — Bản nháp tồn đọng nuốt mọi lần gửi (nguy hiểm nhất)
Đo được ô soạn còn **3326 ký tự** bản nháp cũ. Khi đó `insert_text` **không chèn gì**,
nút Send vẫn bấm được, nhưng **số lượt không tăng** → mọi lần gửi thất bại **âm thầm**.
→ Trước khi gõ: `Ctrl+A` → `Delete`, đo lại độ dài, nếu còn >2 ký tự thì xoá bằng
`execCommand`. Sau khi gõ: đo lại, lệch >5% → **không gửi** (`E_TYPE_INCOMPLETE`).

### Bẫy 3 — Chỉ thị của GPT-6 không tới executor
Round 2 và 3 trả **cùng SHA256** với round 1. GPT-6 phát hiện và `BLOCKED`.
Nguyên nhân: `parse_directive` chỉ bóc khối `key: value`, **vứt phần văn xuôi**;
`run_executor` lại không truyền `instruction`.
→ `parse_directive` giữ `instruction` (bỏ khối lệnh); `run_executor` truyền xuống
qua `inspect.signature`; executor bóc chủ đề (`extract_focus`) và trả lời đúng câu hỏi.

### Bẫy 4 — Tin dài giết cả vòng lặp
Báo cáo 3062 ký tự > ngưỡng 3000 → `E_PROMPT_TOO_LONG` → dừng giữa đường.
→ Tự ghi ra file + đính kèm + gửi phần đầu làm con trỏ.

### Bẫy 5 — Đường dây `instructions:` đứt ở HAI chỗ (2026-10-25, GPT-6 bắt 2 lần)

Sau khi sửa Bẫy 3, tưởng đã xong — nhưng GPT-6 vẫn bác thêm 2 lần liên tiếp, và
**cả hai lần nó đều đúng**. Đây là phần đáng đọc nhất của báo cáo:

| Lần | GPT-6 nói | Nguyên nhân thật |
|---|---|---|
| job `dedup-check` | "Task-specific instructions appear not to reach or control execution" | `parse_directive` cắt văn xuôi tại dòng `loop`, mà khối `instructions:` nằm **sau** đó → bị vứt sạch. Executor chỉ thấy phần nhận xét. |
| job `dedup-v2` | "DISPATCHER_UNABLE_TO_ROUTE_TASK … action-based generic dispatch" | `_await_directive` chỉ gán `data["last_directive"]`, còn `run_executor` đọc `data["instruction"]` → executor **luôn nhận chuỗi rỗng**, rơi về nhánh mặc định. |

→ Sửa: `_extract_instructions()` vét YAML block `>`/`|` và nối vào `instruction`;
`_await_directive` gán thẳng `data["instruction"]`. Khoá bằng 3 test.

**Bài học quan trọng hơn cả code:** báo cáo "6 PASS / 0 FAIL" chung chung **không**
được coi là bằng chứng. GPT-6 bác đúng vì nó đòi bộ đếm thật
(`submitted/accepted/executed/rejected`) + log tuyệt đối + SHA256 + PASS/FAIL từng
kịch bản. Đã làm `scratch_analysis/verify_dedup.py` (6 kịch bản tích hợp thật) và
`verify_parse_directive.py` (10 ca, gồm 1 reply thật của GPT-6).

### Bẫy 6 — Gửi trùng task khởi động
`NEXT_TASK` nằm chung nhánh `TASK_READY` nên mỗi vòng lặp **gửi lại** `task_text`
của round 1. Hội thoại thật có 3 lượt y hệt nhau. → Tách nhánh: `NEXT_TASK` đi
thẳng sang `EXECUTING`.

### Bẫy 7 — Lỗi mạng bị bảng trạng thái che
`TRANSPORT_UNAVAILABLE` chỉ tới được từ 2/6 trạng thái → send() hỏng lúc `NEXT_TASK`
ném `StateError`, **che mất lỗi thật**. → Cho phép từ mọi trạng thái chưa kết thúc
(nó là điểm chặn hợp lệ theo mục C).

### Bẫy 8 — `turnCount` không đơn điệu
ChatGPT **ảo hoá DOM**: số `[data-turn-key]` có lúc **giảm** (5→4) dù tin đã gửi.
→ Xác nhận gửi bằng **bất kỳ** 3 tín hiệu: turnCount tăng / `data-turn-key` mới /
ô soạn rỗng.

**Điểm chung của các bẫy:** đều **không báo lỗi rõ ràng**. Nếu không đo `turnCount`
và so SHA256 giữa các round thì sẽ tưởng là chạy được. Đây chính là lý do yêu cầu
"không lấy việc extension hiển thị prompt làm bằng chứng".

---

## 5. Executor — cái quyết định chất lượng

Thiết kế ban đầu của tôi có `IMPLEMENT` là **khung rỗng** (chỉ tự kiểm agent_loop).
GPT-6 phát hiện đúng và từ chối PASS. Đã sửa thành việc thật:

| Lệnh | Việc thật | Bằng chứng trả về |
|---|---|---|
| `SURVEY` | Đọc lane/tập/engine/model/QC — **chỉ đọc** | đường dẫn + SHA256 tệp đọc |
| `IMPLEMENT` | Thêm 2 module MG_STUDIO + chạy 22 bài test | SHA256 từng tệp, kết quả test |
| `TEST` | Chạy nghiệm thu MG_STUDIO (nếu chỉ thị nói về MG) hoặc tự kiểm agent_loop | từng dòng PASS/FAIL |

`SURVEY` và `TEST` **đọc chỉ thị** để trả đúng chủ đề, không lặp lại bản cũ.

---

## 6. An toàn (mục F) — đã cưỡng chế

| Yêu cầu | Cách làm |
|---|---|
| Không sửa H2DEV production | `_guard()` chặn mọi đường dẫn chứa `h2dev` |
| Không ghi đè artifact đã nghiệm thu | `IMPLEMENT` chỉ **thêm** tệp; tệp đã có thì báo PARTIAL, không ghi đè |
| Giới hạn retry | `send()` có timeout + số lần thử hữu hạn; hết là dừng, không lặp vô hạn |
| Không gán nhầm báo cáo job khác | `parse_report(expect=...)` + bài tự kiểm `reject_wrong_job` |
| Không gửi mật khẩu/token | Không đọc cookie; log chỉ ghi **độ dài** ô soạn, không ghi nội dung |
| Không chiếm chuột/bàn phím | Chrome riêng ở `-32000,-32000`; đo cô lập: **0 lần đổi foreground** |
| Không tự phát sinh phí API | Chỉ dùng phiên web có sẵn của chủ dự án, không gọi API trả phí |
| Không báo hoàn thành giả | `final_verified` chỉ bật sau khi có `render_status` (có bài test chặn) |

---

## 7. Cách chủ dự án dùng (1 lệnh)

```bash
cd /e/CLAUDE/YOUTUBE/GPT_BRIDGE
PYTHONPATH= python -m agent_loop.cli new --job mg-01 --project MG_STUDIO \
  --goal "Khảo sát MG_STUDIO rồi triển khai Hook Lock" --action SURVEY
PYTHONPATH= python -m agent_loop.cli run --job mg-01 --max-rounds 5
```

Rồi chỉ đọc `agent_loop/jobs/mg-01/` (state + history + checkpoints) và
`agent_loop/test_runs/<run>/` (nguyên văn từng lượt).

---

## 8. Việc chưa làm (nói thẳng)

1. **Chưa nối Fable → GPT-6 → Sonnet → Opus thật.** Cần quyền gọi model và
   chủ dự án xác nhận chi phí. Hiện `models.json` chỉ được **đọc** để báo cáo.
2. **Chưa render HOOK_VIDEO / BODY_VIDEO tách rời.** Chưa được phép chạy render.
3. **Chưa nhận file đính kèm do GPT-6 gửi về** — có hàm nhưng chưa có run nghiệm thu.
4. **Chưa áp Hook Lock cho tập nào** — không tự khoá hook chưa xác định phê duyệt.
5. **GPT-6 không đọc được ổ E:** — giới hạn của ChatGPT Web, phải đính kèm nội dung.
