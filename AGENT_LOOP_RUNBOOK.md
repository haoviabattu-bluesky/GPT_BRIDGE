# AGENT_LOOP_RUNBOOK — Sổ tay vận hành

> Mọi lệnh chạy từ `E:\CLAUDE\YOUTUBE\GPT_BRIDGE`, dùng `python` (3.11) đã có Playwright.
> Luôn prefix `PYTHONPATH=` để tránh PYTHONPATH lạ trên máy này.

---

## 1. Chạy một nhiệm vụ (đường ngắn nhất)

```bash
cd /e/CLAUDE/YOUTUBE/GPT_BRIDGE
PYTHONPATH= python -m agent_loop.cli new \
  --job mg-survey-01 --project MG_STUDIO \
  --goal "Khảo sát MG_STUDIO rồi báo cáo cấu trúc lane/tập/engine" \
  --action SURVEY

PYTHONPATH= python -m agent_loop.cli run --job mg-survey-01 --max-rounds 4
```

`run` in ra JSON trạng thái cuối. Xem lại bất cứ lúc nào:

```bash
PYTHONPATH= python -m agent_loop.cli show --job mg-survey-01
```

---

## 2. Chạy lại đúng hội thoại cũ

`conversation_id` được lưu trong `state.json` sau lần chạy đầu. Lần sau chỉ cần:

```bash
PYTHONPATH= python -m agent_loop.cli run --job mg-survey-01
```

Muốn **ép** sang hội thoại khác:

```bash
PYTHONPATH= python -m agent_loop.cli run --job mg-survey-01 \
  --conversation "https://chatgpt.com/c/<id>"
```

Nếu hội thoại đã bị xoá, transport tự phát hiện (`E_CONVERSATION_DEAD`), mở chat mới
và ghi lý do vào `history[]`.

---

## 3. Kiểm tra sức khoẻ trước khi chạy việc thật

> **Luôn prefix `PYTHONPATH=`.** Máy này bị tiêm
> `PYTHONPATH=...\hermes-agent\venv\Lib\site-packages`, đứng trước `.venv` của dự án
> và che `greenlet` bản đúng → `ModuleNotFoundError: greenlet._greenlet` (test đỏ GIẢ).

```bash
# 0. toàn bộ test dự án (phải 0 failed)
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q

# 1. tự kiểm protocol + state machine + khoá + chống gửi trùng (không cần mạng)
PYTHONPATH= .venv/Scripts/python.exe -m agent_loop.cli self-test

# 2. Chrome riêng đã đăng nhập chatgpt.com chưa? (chỉ đọc)
PYTHONPATH= .venv/Scripts/python.exe -m scratch_analysis.probe_cdp_login
```

Kỳ vọng: `pytest` → `283 passed, 3 skipped, 0 failed`; `self-test` → `6 đạt / 0 hỏng`;
`probe_cdp_login` → `logged_in: true`.

**Trước khi chạy `pytest tests/`:** phải tắt Chrome test (giữ profile
`CHATGPT_PROFILE`), nếu không `TestChatGPTWebGuards` sẽ đỏ vì nó giả định cổng 9335
KHÔNG mở. Xem `AGENT_LOOP_TEST_REPORT.md` §6.1 để có lệnh kill đúng (chỉ kill
tiến trình giữ profile test, không đụng Chrome của anh).

---

## 4. Nghiệm thu nhiều round thật (không copy/paste)

```bash
PYTHONPATH= python -u scratch_analysis/live_3rounds.py
```

Kết quả ghi vào `agent_loop/test_runs/<run_id>/`:

| Tệp | Nội dung |
|---|---|
| `r1_gpt_reply.md` … | câu trả lời nguyên văn của GPT-6 từng round |
| `rN_report.md` | `EXECUTION_REPORT.md` Hermes đã gửi |
| `evidence.json` | số round, SHA báo cáo, lệnh GPT, focus, kết luận |
| `transcript.log` | nhật ký có mốc thời gian |

Tiêu chí ĐẠT: `verdict` bắt đầu bằng `OK`, `rounds ≥ 3`, mọi round
`same_conversation: true`, `focus_not_stolen: true`.

---

## 5. Sự cố thường gặp

| Mã lỗi | Nghĩa | Xử lý |
|---|---|---|
| `E_CHROME_NO_CDP` | Chrome riêng không mở cổng 9335 | Đóng hết Chrome của profile đó rồi chạy lại; kiểm `curl 127.0.0.1:9335/json/version` |
| `E_NOT_LOGGED_IN` | Profile chưa đăng nhập ChatGPT | Đăng nhập **tay 1 lần** trên cửa sổ Chrome riêng (nó nằm ngoài màn hình — dùng `--window-position=0,0` tạm để thấy) |
| `E_COMPOSER_DIRTY` | Ô soạn còn bản nháp không xoá được | Bấm tay vào ô soạn, Ctrl+A, Delete; chạy lại |
| `E_TYPE_INCOMPLETE` | Gõ thiếu ký tự | Chạy lại; nếu lặp lại, giảm độ dài prompt / gửi bằng file đính kèm |
| `E_SEND_UNCERTAIN` | Bấm gửi mà số lượt không tăng | Thường do bản nháp cũ; đã có fix tự xoá. Nếu vẫn lặp: reload tab |
| `E_CAPTCHA` | Có thử thách xác minh | **DỪNG** — mở cửa sổ lên cho chủ dự án xử lý tay. Không tìm cách vượt |
| `E_STILL_STREAMING` | Quá `--timeout` vẫn đang trả lời | Tăng `--timeout`, hoặc chờ rồi chạy lại (không gửi chồng) |
| `E_CONVERSATION_DEAD` | URL hội thoại redirect về trang chủ | Tự mở chat mới; nếu cần ngữ cảnh cũ, gửi kèm file tóm tắt |
| `E_PROMPT_TOO_LONG` | Prompt > 3000 ký tự | Gửi bằng file đính kèm (`attach=`) |
| `StateError: đang chạy ở PID …` | Có tiến trình khác đang giữ job | Đợi, hoặc kiểm `tasklist` xem PID còn sống; **không** xoá `run.lock` khi tiến trình còn chạy |
| `BLOCKED: không thấy next_action` | GPT trả lời không có khối lệnh, hoặc khối bị cắt | Đọc `jobs/<job>/rounds/` + `show --job`; `parse_directive` nay quét cả văn bản **không cần dấu ```** — nếu vẫn lỗi thì xem `found_keys` trong log để biết GPT trả khoá gì |
| `BLOCKED: EXECUTION_REPORT thiếu conversation_id` | Chat mới chưa có URL `/c/<id>` ở thời điểm tạo báo cáo | Đã sửa: `_capture_conv_id` chạy **sau** khi gửi tin đầu. Nếu vẫn gặp, chạy lại job (chat đã có URL thì lần sau dùng lại được) |
| `E_ATTACH_NOT_READY` | Tệp đã đính nhưng nút Send vẫn khoá >180 s | Chờ thêm hoặc thử tệp nhỏ hơn; **không** bấm Send khi nút còn disabled (sẽ mất lượt âm thầm) |
| `E_SEND_UNCERTAIN` | Bấm gửi nhưng số lượt không tăng | Thường do bản nháp cũ hoặc nút disabled; đã có fix tự xoá + chờ `sendUsable`. Kiểm `jobs/_spill/` xem tin có bị đẩy sang file không |
| `lệnh X chưa được cài đặt` | GPT yêu cầu action chưa có executor | Thêm hàm vào `executor.EXECUTORS`; xem §6. Executor trả `STOP` + blocker là hành vi ĐÚNG. Riêng `FIX` **đã có** (đi qua `mg_implement`). |
| Báo cáo trùng SHA256 giữa các round | GPT-6 nói "dữ liệu cũ" / nghi executor cố định | Kiểm 2 chỗ: (1) `parse_directive` có cắt mất nhiệm vụ không — chạy `verify_parse_directive.py` và in `len(instruction)`; (2) `_pick_test()` chọn đúng bài chưa — in `_pick_test(instruction)`. Thứ tự ưu tiên phải là parse_directive → dedup → integration_lane_x → mg_modules. |
| `focus_not_stolen=False` | nghi cướp chuột/bàn phím | Đừng so **tiêu đề** (Chrome tự đổi khi stream) và đừng lấy mốc trước `open()`. Dùng `probe_focus_stages.py`: mốc đúng là **sau `new_conversation()`**; `open()` có kéo Chrome lên rồi tự trả. `SetForegroundWindow` từ tiến trình nền bị Windows chặn — đừng dựa vào. |
| 2 test `TestChatGPTWebGuards` fail | `test_health_fails_without_cdp`, `test_submit_blocked_without_cdp` | Chrome test còn sống ở cổng 9335. Tắt hết tiến trình có `CHATGPT_PROFILE` rồi chạy lại pytest. |
| Script nghiệm thu khoe **"OK: N round thật, tự động hoàn toàn"** dù GPT-6 chốt `BLOCKED` | `verdict` cũ không đọc phán quyết của GPT-6 — run `20261025_220917` báo OK trong khi `final_task_status=BLOCKED` | Đã sửa: verdict đọc `task_status` cuối; chỉ `COMPLETED` mới được "OK". **Đọc `final_task_status` trong `evidence.json` trước khi tin verdict**. File log của run lỗi đã đổi tên thành `scratch_analysis/live_stale_verdict_bug.out` để không ai đọc nhầm là bản mới |
| Round trả `evidence=0 artifacts=0` | Executor không có đường chạy cho action đó | Xem `r<N>_report.md` mục SUMMARY — sẽ ghi thẳng "chưa có executor cho lệnh X" |
| Round `IMPLEMENT` trả **cùng nội dung** dù GPT-6 giao việc khác | `mg_implement.run()` nhận `instruction` nhưng **không đọc** — luôn kiểm 3 tệp cố định trong `TEP_MOI` | Bẫy thật của job `live-20261026_000729` round 3 (GPT-6 đòi nối guard vào `mg.py`, executor báo lại 3 tệp cũ). **Đã sửa:** `_doi_sua_san_xuat()` dò dấu hiệu sửa tệp sản xuất → trả `STOP` + `blocker`, `artifacts=[]`, không báo "22/22". Khoá bằng `test_implement_chi_thi_sua_san_xuat_thi_blocked` |
| Báo cáo mọi round **cùng SHA256** | Executor không thấy chỉ thị nên rơi về nhánh mặc định | Kiểm 3 chỗ: (a) `parse_directive` có vét được khối `instructions:` không (`_extract_instructions`); (b) `_await_directive` có gán `data["instruction"]` không (thiếu (b) là lỗi job `dedup-v2`); (c) `parse_directive` có cắt mất nhiệm vụ tại dòng `next_action:` không — khối `TASK_ASSIGNMENT` đặt nó TRƯỚC nhiệm vụ nên bản cũ vứt sạch (job `live-20261025_235344`). In `len(instruction)` để xác nhận |
| Hội thoại có nhiều lượt **y hệt nhau** | Gửi trùng task khởi động | `NEXT_TASK` phải đi thẳng sang `EXECUTING`, không được nằm chung nhánh `TASK_READY` (đã sửa; khoá bằng test) |
| `StateError: không được nhảy NEXT_TASK -> TRANSPORT_UNAVAILABLE` | Lỗi mạng bị che bởi bảng chuyển trạng thái | Đã sửa: `TRANSPORT_UNAVAILABLE` tới được từ mọi trạng thái chưa kết thúc |
| `E_SEND_UNCERTAIN` dù tin đã đi | Xác nhận gửi chỉ dựa `turnCount`, mà ChatGPT **ảo hoá DOM** nên số này có lúc giảm | Đã sửa: nhận khi **bất kỳ** (a) turnCount tăng, (b) `data-turn-key` mới, (c) ô soạn rỗng. Log ghi `xác nhận đã gửi: <tín hiệu>` |

---

## 6. Thêm một executor mới

```python
# agent_loop/executor.py
def my_action() -> dict:
    return {
        "summary": "mô tả NGẮN việc đã làm",
        "evidence": ["số đo thật 1", "đường dẫn thật 2"],   # phải là dữ kiện đo được
        "artifacts": [_artifact(Path(r"...\file.json"))],    # có SHA256
        "next_action": "REPORT",
        "next_request": "đề nghị cho GPT-6",
        "blocker": "",                                       # rỗng nếu không tắc
    }

EXECUTORS["MY_ACTION"] = my_action
```

Đồng thời thêm tên vào `loop.ACTIONS` để `parse_directive` chấp nhận lệnh đó.

**Luật:** executor không được trả `next_action: DONE` khi chưa kiểm tra thật.
`SURVEY` phải chỉ đọc. Mọi đường dẫn đi qua `_guard()` (chặn H2DEV).

---

## 7. Vận hành dài / tự động theo lịch

Chạy nền, không giữ phiên:

```bash
cd /e/CLAUDE/YOUTUBE/GPT_BRIDGE
nohup env PYTHONPATH= python -m agent_loop.cli run --job mg-01 --max-rounds 6 \
  > agent_loop/jobs/mg-01/run.log 2>&1 &
```

Kiểm tiến trình: `python -m agent_loop.cli show --job mg-01` → `task_status`,
`history[]`, `checkpoints[]`. Nếu chết giữa đường, chạy lại **cùng lệnh**: nó đọc
`state.json`, không gửi trùng (dedup theo SHA), và tiếp tục từ trạng thái đã lưu.

---

## 8. Dọn dẹp

```bash
# xem các lần nghiệm thu
ls agent_loop/test_runs/
# xoá bớt (giữ lần mới nhất)
rm -rf agent_loop/test_runs/<run_id_cu>
```

`agent_loop/jobs/<job>/` giữ nguyên nếu cần tra cứu; xoá cả thư mục là xoá job.

---

## 9. Bảo mật

- `CHATGPT_PROFILE` = `E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE` — chỉ dùng cho đăng nhập
  có sẵn. **Không** copy profile này sang máy khác, không commit.
- Không đưa token/mật khẩu vào prompt gửi ChatGPT.
- Log không ghi nội dung ô soạn, chỉ ghi độ dài.
- Cửa sổ Chrome đặt ở `-32000,-32000` — không nhìn thấy, không cướp focus.
