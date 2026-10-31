# AGENT_LOOP_ARCHITECTURE — Kiến trúc điều phối GPT-6 ↔ Hermes

> Cập nhật: 2026-10-25 · Trạng thái: **đã đo thật trên máy**, xem `AGENT_LOOP_TEST_REPORT.md`
> Kênh: **Chrome riêng + CDP** (`CHATGPT_PROFILE`, cổng 9335). Không API trả phí,
> không đọc cookie, không né CAPTCHA, không chiếm chuột/bàn phím.

---

## 1. Sơ đồ luồng

```
        Chủ dự án giao 1 nhiệm vụ (1 lần)
                     │
                     ▼
        ┌────────────────────────────┐
        │  agent_loop/loop.py        │
        │  vòng điều phối            │
        └────────────┬───────────────┘
                     │
      ┌──────────────┴───────────────┐
      │                              │
      ▼                              ▼
┌───────────────┐          ┌──────────────────────┐
│ transport.py  │          │  executor.py         │
│ Chrome riêng  │          │  SURVEY / IMPLEMENT  │
│ CDP 9335      │          │  TEST / (FIX chưa có)│
└───────┬───────┘          └──────────┬───────────┘
        │                             │
        ▼                             ▼
┌───────────────┐          ┌──────────────────────┐
│ send_recv.py  │          │  MG_STUDIO           │
│ gửi + nhận    │          │  core/khoa_hook.py   │
│ đủ, chống bẫy │          │  core/trang_thai_qc  │
└───────┬───────┘          └──────────────────────┘
        │
        ▼
┌────────────────────────────────────────────┐
│ ChatGPT Web (hội thoại /c/<id> giữ nguyên) │
│ GPT-6 đọc báo cáo → xuất lệnh round tiếp   │
└────────────────────────────────────────────┘
        │
        ▼
   protocol.py: EXECUTION_REPORT.md + SHA256
        │
        └──────────► quay lại loop (round+1)
```

**Điểm cốt lõi:** không có bước nào cần người chuyển tin. Chủ dự án chạy **1 lệnh**,
đọc kết quả cuối.

---

## 2. Vì sao chọn Chrome riêng + CDP

| Phương án | Đã cân nhắc | Kết luận |
|---|---|---|
| **CDP trên Chrome riêng** | Playwright nối vào cổng debug của một profile riêng | **CHỌN** — đo được: gửi/nhận thật, giữ hội thoại, **không cướp focus** |
| Extension hiện có (port 8765) | Chỉ hiển thị prompt + đọc danh sách job | **KHÔNG dùng làm bằng chứng** — đúng như yêu cầu mục A. Nó không tự trò chuyện hai chiều |
| Google Drive | Chậm, lệch nhịp, không cần thiết | **KHÔNG dùng** — file local + CDP đã đủ |
| API trả phí | Sinh chi phí khi chưa được phép | **KHÔNG dùng** — theo ràng buộc mục A |
| `pyautogui` (chuột/bàn phím thật) | Cướp focus, ảnh hưởng người dùng | **CẤM** — theo mục F |

Chrome riêng chạy với:
```
--user-data-dir=E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE
--remote-debugging-port=9335
--window-position=-32000,-32000     # ngoài màn hình
```

---

## 3. Giao thức thông điệp

### 3.1 GPT-6 → Hermes: `TASK_ASSIGNMENT`

GPT-6 trả lời kèm một khối lệnh:

````
```loop
next_action: SURVEY | IMPLEMENT | TEST | FIX | REPORT | DONE | STOP
round: <số>
task_status: TASK_READY | NEXT_TASK | COMPLETED | BLOCKED
```
````

**Phần văn xuôi phía trên khối lệnh chính là chỉ thị thật.** `parse_directive`
giữ lại toàn bộ phần này vào `instruction` — đây là bài học đo được: nếu chỉ lấy
khối `key: value` thì executor không biết GPT hỏi gì và trả lặp dữ liệu cũ.

### 3.2 Hermes → GPT-6: `EXECUTION_REPORT`

```markdown
# EXECUTION_REPORT

- project_id: MG_STUDIO
- job_id: live-20261026_000729
- conversation_id: https://chatgpt.com/c/6ade...
- round: 2
- task_status: REPORT_READY
- next_action: TEST
- created_at: 2026-10-25 22:10:34
- report_sha256: <sha256 của thân bài>

## SUMMARY
...
## EVIDENCE
- <số đo thật, đường dẫn thật>
## ARTIFACTS
- path · bytes · sha256
## NEXT_REQUEST
...
```

**Chống gán nhầm job:** `parse_report(expect={project_id, job_id, round})` từ chối
nếu lệch. Có bài tự kiểm `reject_wrong_job` chứng minh.

### 3.3 Nội dung lớn

Tin >3000 ký tự **tự động** ghi ra `jobs/_spill/message_*.md` + đính kèm, chỉ phần
đầu vào tin nhắn. Đã nghiệm thu: gửi tin 6306 ký tự, GPT đọc đúng mã nằm sâu
trong tệp (`SPILL-ZEBRA-99`).

---

## 4. Máy trạng thái

```
CREATED → TASK_READY → EXECUTING → REPORT_READY → GPT_REVIEW → NEXT_TASK → EXECUTING
                                                                    │
                                                                    ▼
                                                              COMPLETED

Chặn: BLOCKED · TRANSPORT_UNAVAILABLE · NEEDS_OWNER_ACTION
```

### Bất biến đã cưỡng chế bằng mã

| Bất biến | Cách cưỡng chế | Bài tự kiểm |
|---|---|---|
| Không 2 lượt đồng thời cùng job | `run.lock` ghi PID; tiến trình 2 bị từ chối | `lock_exclusive` |
| Không nhảy trạng thái | bảng chuyển trạng thái hợp lệ | `no_state_skip` |
| Không gửi trùng | băm nội dung, đã gửi thì bỏ qua | `dedup_send` |
| Không coi "chưa nhận trả lời" là PASS | `send()` chỉ đạt khi `turnCount` **tăng** | — |
| Không nhận báo cáo job khác | `parse_report(expect=...)` | `reject_wrong_job` |
| Không nhận báo cáo bị sửa | SHA256 thân bài | `reject_tampered` |
| Không báo hoàn thành giả | `final_verified` cần `render_status` | trong bộ 22 bài MG |

---

## 5. Executor — nơi quyết định chất lượng

| Lệnh | Việc thật | Trả về |
|---|---|---|
| `SURVEY` | Đọc lane/tập/engine/model/QC của MG_STUDIO — **chỉ đọc** | bằng chứng + SHA256 tệp đã đọc |
| `IMPLEMENT` | Thêm 2 module mới, chạy 22 bài test, **không ghi đè** | SHA256 từng tệp, kết quả test |
| `TEST` | Điều phối theo chỉ thị: `parse_directive` → `verify_parse_directive.py`; dedup → `verify_dedup.py`; MG → nghiệm thu 22 bài; còn lại → tự kiểm agent_loop | từng dòng PASS/FAIL + bộ đếm |
| `FIX` | **chưa có** → trả `STOP` + blocker trung thực | — |

Executor **đọc `instruction`** để trả đúng chủ đề. Bóc chủ đề bằng `extract_focus()`
(`hook_lock`, `qc_rounds`, `model_routing`, `payload_routing`, `render_split`,
`final_check`, `engine`).

### 5.1 Đường dây chỉ thị — chỗ ĐÃ HỎNG 2 LẦN (đọc kỹ trước khi sửa)

Đây là nơi GPT-6 bắt lỗi hai lần liên tiếp, và cả hai đều là lỗi **thật**:

```
GPT reply
  └─ parse_directive()          # (1) phải vét được khối `instructions:` YAML block
       ├─ instruction  ─────────┐
       └─ next_action           │
                                ▼
       _await_directive()       # (2) PHẢI gán data["instruction"] = d["instruction"]
                                │      chỉ gán last_directive là KHÔNG ĐỦ
                                ▼
       run_executor(data)       # (3) đọc data["instruction"] -> chọn nhánh
                                ▼
       executor cụ thể          # (4) trả bằng chứng THẬT, không phải self-test chung
```

| # | Lỗi đã đo | Triệu chứng | Sửa |
|---|---|---|---|
| 1 | `parse_directive` cắt prose tại dòng `loop` → vứt khối `instructions:` | 5 round trả **cùng SHA256** | `_extract_instructions()` vét YAML block `>`/`\|`, nối vào `instruction` |
| 2 | `_await_directive` chỉ gán `last_directive`, không gán `data["instruction"]` | executor nhận chuỗi rỗng → luôn rơi nhánh mặc định | gán `data["instruction"]` + `data["instructions"]` |
| 3 | `NEXT_TASK` nằm chung nhánh `TASK_READY` | hội thoại có **3 lượt y hệt** (gửi trùng) | tách nhánh, NEXT_TASK → `EXECUTING` ngay |

**Bài học:** khi GPT-6 nói "instructions không tới được executor", nó thường ĐÚNG.
Đừng sửa bằng cách thêm từ khoá vào dispatcher — phải lần theo đường dây trên.

**Nguyên tắc:** executor không được trả `next_action: DONE` khi chưa kiểm tra thật.
Thà `BLOCKED` còn hơn báo hoàn thành giả — GPT-6 sẽ phát hiện và mất tin cậy.
Executor phải trả **bộ đếm thật** khi được hỏi (submitted/accepted/executed/rejected),
không được trả "6 PASS / 0 FAIL" chung chung — GPT-6 bác đúng như vậy ở job `dedup-check`.

---

## 6. An toàn

| Ràng buộc (mục F) | Cách làm |
|---|---|
| Không sửa H2DEV production | `_guard()` chặn mọi đường dẫn chứa `h2dev` |
| Không ghi đè artifact đã nghiệm thu | `IMPLEMENT` chỉ **thêm** tệp mới |
| Giới hạn retry | timeout + số lần thử hữu hạn, hết là dừng |
| Không gán nhầm báo cáo | `expect` + SHA256 |
| Không gửi mật khẩu/token | không đọc cookie; log chỉ ghi **độ dài** ô soạn |
| Không chiếm chuột/bàn phím | Chrome ngoài màn hình; đo cô lập **0 lần đổi foreground** |
| Không tự phát sinh phí API | dùng phiên web có sẵn, không gọi API trả phí |
| Không báo video hoàn thành giả | `final_verified` chỉ sau khi có `render_status` |

---

## 7. Giới hạn phải chấp nhận

1. **ChatGPT Web không phải API** — giữ hội thoại bằng URL `/c/<id>`. OpenAI đổi DOM
   là phải đo lại selector (`scratch_analysis/probe_dom_turns.py`).
2. **GPT-6 không đọc được ổ E:** — muốn nó "xem" tài liệu thì phải đính kèm.
3. **Phụ thuộc profile đăng nhập** — hết phiên là `E_NOT_LOGGED_IN`, cần đăng nhập tay 1 lần.
4. **Tuần tự** — 1 Chrome, 1 tab. Song song cần nhiều profile.
5. **Gặp CAPTCHA là DỪNG** — không tìm cách vượt; báo chủ dự án xử lý tay.

---

## 8. Cấu trúc thư mục

```
GPT_BRIDGE/
├── AGENT_LOOP_ARCHITECTURE.md          ← tệp này
├── AGENT_LOOP_IMPLEMENTATION_REPORT.md
├── AGENT_LOOP_TEST_REPORT.md
├── AGENT_LOOP_RUNBOOK.md
├── agent_loop/
│   ├── transport.py      Chrome riêng + CDP
│   ├── send_recv.py      gửi/nhận, chống bẫy
│   ├── protocol.py       TASK_ASSIGNMENT / EXECUTION_REPORT
│   ├── state.py          máy trạng thái + khoá + checkpoint
│   ├── loop.py           vòng điều phối
│   ├── executor.py       SURVEY / IMPLEMENT / TEST
│   ├── mg_implement.py   việc thật trên MG_STUDIO
│   ├── cli.py            new / run / show / self-test
│   ├── jobs/             trạng thái từng job + _spill/
│   └── test_runs/        bằng chứng từng lần nghiệm thu
└── scratch_analysis/     script ĐO (không phải sản phẩm)
    ├── probe_cdp_login.py        profile đã đăng nhập?
    ├── probe_dom_turns.py        đo selector khi UI đổi
    ├── probe_focus_clean.py      đo cô lập việc cướp focus
    ├── probe_attach_sent.py      đính kèm có tới không
    ├── probe_spill_path.py       spill tự động
    └── live_3rounds.py           nghiệm thu 3+ round thật
```
