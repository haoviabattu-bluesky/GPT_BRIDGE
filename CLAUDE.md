# CLAUDE.md

Tệp này hướng dẫn Claude Code (claude.ai/code) khi làm việc với mã nguồn trong thư mục này.

GPT_BRIDGE là cầu nối giữa dự án H2DEV (kênh YouTube không lộ mặt) và GPT-6 chạy trên **ChatGPT Web**. Tài liệu, nhật ký, mã lỗi, chú thích trong mã đều bằng tiếng Việt — giữ nguyên như vậy. Thư mục gốc: `E:\CLAUDE\YOUTUBE\GPT_BRIDGE`.

## Lệnh thường dùng

> **Luôn đặt `PYTHONPATH=` trước lệnh** (bash) hoặc `$env:PYTHONPATH=''` (PowerShell). Máy này bị chèn sẵn `PYTHONPATH=...\hermes-agent\venv\Lib\site-packages`, che mất `greenlet` của `.venv` → báo `ModuleNotFoundError: greenlet._greenlet` (kiểm thử đỏ GIẢ).

```bash
# cài đặt (Python 3.13, dùng uv)
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt

# chạy toàn bộ kiểm thử (ngoại tuyến, không gọi mạng)
PYTHONPATH= /e/CLAUDE/YOUTUBE/GPT_BRIDGE/.venv/Scripts/python.exe -m pytest tests/ -q
# chạy một tệp / một bài kiểm thử
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -q
PYTHONPATH= .venv/Scripts/python.exe -m pytest "tests/test_agent_loop.py::<TenLop>::<ten_bai>" -q
# kiểm thử thật với Drive/Sheets (cần thông tin xác thực trong D:\CLAUDE\credentials\)
BRIDGE_LIVE=1 PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q -m live
```

- Trước khi chạy `pytest tests/`: phải tắt Chrome kiểm thử ở cổng **9335** (hồ sơ `E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE`), nếu không nhóm `TestChatGPTWebGuards` sẽ đỏ (nhóm này giả định cổng 9335 đang đóng). Chỉ tắt đúng tiến trình (theo PID) có `--user-data-dir=E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE` — không tắt Chrome theo tên.
- Dự án chưa cấu hình công cụ soát lỗi/định dạng mã.

Luồng giao kịch bản H2DEV (`bridge.py`, khởi chạy qua `start.ps1`):
```powershell
.\start.ps1 -Doctor        # bridge.py doctor — kiểm tra môi trường
.\start.ps1                # bridge.py run — 1 vòng: phát hiện → kiểm hồ sơ → dựng chỉ thị → giao
.\start.ps1 -Loop          # bridge.py loop (dừng: .\start.ps1 -Stop → tạo tệp temp\STOP)
.venv\Scripts\python.exe bridge.py gate --tab <tab-lane> --only <lane>-ep<N>   # kiểm 8 điều kiện trước khi vào hàng đợi
.venv\Scripts\python.exe bridge.py handoff tick|watch|recover|doctor|deliver
.venv\Scripts\python.exe -m chatgpt_web_handoff.cli serve|watch|queue|qc|list
```

Vòng điều phối tự động (GPT-6 ↔ Hermes qua cổng gỡ lỗi Chrome):
```bash
PYTHONPATH= python -m agent_loop.cli new --job <ma-viec> --project MG_STUDIO --goal "..." --action SURVEY
PYTHONPATH= python -m agent_loop.cli run --job <ma-viec> --max-rounds 4 [--conversation https://chatgpt.com/c/<ma>]
PYTHONPATH= python -m agent_loop.cli show --job <ma-viec>
PYTHONPATH= .venv/Scripts/python.exe -m agent_loop.cli self-test        # tự kiểm, không cần mạng
PYTHONPATH= .venv/Scripts/python.exe -m scratch_analysis.probe_cdp_login # hồ sơ Chrome đã đăng nhập ChatGPT chưa
```

## Kiến trúc — hai hệ thống độc lập trong cùng thư mục

### 1. Luồng giao kịch bản H2DEV (`bridge.py` + `services/` + `chatgpt_web_handoff/`)
Đường tới hạn chạy bằng luật cố định (không dùng AI): `sheet_scanner` (đọc cột K các tab `Lane-*` trong bảng `YOUTUBE`, dòng trạng thái mới nhất được tính) → `drive_client` (tìm thư mục INPUT/OUTPUT bằng API) → `prompt_builder` → bộ giao việc cho người viết → `output_collector` → `zip_qc` → `drive_uploader` (tải lên + đọc lại + so SHA-256) → `COMPLETED`.
- Trạng thái việc lưu trong SQLite `jobs/bridge.db` qua `services/state.py` (`STATES`, `TERMINAL`).
- Bộ giao việc chọn bằng khoá `writer.adapter` trong `config.yaml` (thư mục `adapters/`: mock, manual_chatgpt, chatgpt_web, official_api, codex_cli_writer; hiện dùng `chatgpt_web_handoff`). Hướng Codex CLI / API trả phí / `gpt-6.1-sol` **đã dừng** — người viết là ChatGPT Web, đúng cuộc hội thoại ghi ở `conversation_url`.
- `chatgpt_web_handoff/`: hàng đợi dạng JSON (`store.py`) + máy chủ HTTP `127.0.0.1:8765` cho tiện ích trình duyệt + `watcher.py` (CHỈ MỘT bộ theo dõi duy nhất, phát hiện tệp ZIP người dùng tải về, ghi trạng thái `DOWNLOADED`).
- `services/handoff_coordinator.py` (giai đoạn 5.3): chỉ ĐỌC các việc ở trạng thái `DOWNLOADED` trong `HandoffStore` → chuẩn hoá → QC → tải lên → `COMPLETED`. Không được tự quét lại thư mục nhận (bất biến này có bài kiểm thử khoá). Khoá chống làm lặp: `handoff:<job_id>`; nhật ký ghi TRƯỚC khi tải lên.
- `bridge.py gate` (giai đoạn 5.4): 8 điều kiện kỹ thuật + phê duyệt sản xuất (`production_upload_enabled`, hiện đang `false`) là cửa CUỐI; cờ `--apply` không thay được phê duyệt. Cửa này chỉ đọc cờ, không bao giờ tự bật.
- Mã lỗi tập trung ở `services/errors.py` (`E_GATE_*`, `E_HANDOFF_*`…).

### 2. Vòng điều phối tự động (`agent_loop/`)
GPT-6 ra lệnh ↔ Hermes thực thi, không cần người chuyển tin. Kênh liên lạc: Chrome riêng `--user-data-dir=E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE --remote-debugging-port=9335 --window-position=-32000,-32000` + Playwright nối qua cổng gỡ lỗi.
- `transport.py` (nối Chrome) → `send_recv.py` (gửi/nhận; tin dài hơn 3000 ký tự tự ghi ra `agent_loop/jobs/_spill/` rồi đính kèm) → `protocol.py` (khối lệnh ```` ```loop ```` = `TASK_ASSIGNMENT`; báo cáo `EXECUTION_REPORT.md` có SHA-256, `parse_report(expect=…)` chống nhận nhầm việc) → `state.py` (máy trạng thái + khoá `run.lock` ghi PID + chống gửi trùng) → `loop.py` → `executor.py` / `executor_v2*.py` / `mg_implement.py` (thực thi trên MG_STUDIO).
- **Đường truyền chỉ thị đã hỏng 2 lần** — đọc `AGENT_LOOP_ARCHITECTURE.md` mục 5.1 trước khi sửa: `parse_directive()` phải lấy cả phần văn xuôi + khối `instructions:` → `_await_directive()` PHẢI gán `data["instruction"]` → bộ thực thi đọc nó để chọn nhánh. Dấu hiệu hỏng: nhiều vòng trả báo cáo cùng SHA-256. Không sửa bằng cách thêm từ khoá vào bộ phân nhánh.
- Thêm lệnh mới: thêm hàm vào `executor.EXECUTORS` **và** thêm tên vào `loop.ACTIONS`. Bộ thực thi không được trả `DONE` hay "N PASS" khi chưa kiểm thật — thà trả `STOP` + `blocker`. Mọi đường dẫn đi qua `_guard()` (chặn ghi vào h2dev).
- Bằng chứng nghiệm thu: `agent_loop/test_runs/<ma_lan_chay>/evidence.json` — đọc `final_task_status`, chỉ `COMPLETED` mới là đạt.
- `scratch_analysis/` là các tệp lệnh ĐO/thăm dò (giao diện trang, tiêu điểm cửa sổ, đính kèm), không phải sản phẩm.

## Nguyên tắc bất di bất dịch (từ README + chú thích đầu mô-đun)
1. Không đoán mã thư mục Drive — luôn tra bằng API; không ghép chuỗi `/input` `/output` vào đường liên kết.
2. Không tải ZIP giả lên như sản phẩm thật; không ghi đè OUTPUT đã có.
3. Không gửi lại chỉ thị khi chưa biết GPT đã nhận; không đánh dấu `COMPLETED` khi Drive chưa xác nhận SHA-256.
4. Không chiếm chuột/bàn phím (cấm pyautogui/pynput), không đóng/điều khiển Chrome của người dùng, không đọc cookie, gặp CAPTCHA là DỪNG.
5. Không sửa dữ liệu H2DEV / Google Sheet (bản 1 chỉ đọc), không gọi Fabel, không đổi Hook/Dàn ý đã duyệt.
6. DeepSeek/AI không nằm trên đường tới hạn — mọi thao tác xác định làm bằng Python.

## Tài liệu tham khảo
- `E:\CLAUDE\YOUTUBE\GPT_BRIDGE\AGENT_LOOP_RUNBOOK.md` — bảng sự cố + mã lỗi của vòng điều phối (`E_CHROME_NO_CDP`, `E_SEND_UNCERTAIN`, `E_ATTACH_NOT_READY`…).
- `E:\CLAUDE\YOUTUBE\GPT_BRIDGE\OPERATIONS_STATUS.md` — trạng thái dịch vụ/hàng đợi hiện tại.
- `E:\CLAUDE\YOUTUBE\GPT_BRIDGE\docs\OPERATIONS.md`, `E:\CLAUDE\YOUTUBE\GPT_BRIDGE\docs\KNOWN_LIMITATIONS.md` — vận hành luồng giao kịch bản, giới hạn thật (đọc trước khi tin số liệu trong README).
- `E:\CLAUDE\YOUTUBE\GPT_BRIDGE\config.yaml` chứa mã bí mật cục bộ + đường dẫn thông tin xác thực — không đưa ra ngoài.
