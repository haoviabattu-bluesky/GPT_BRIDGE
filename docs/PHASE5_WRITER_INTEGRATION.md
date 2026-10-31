# PHASE 5 — TÍCH HỢP GPT WRITER THẬT QUA CODEX CLI

**Ngày đo:** 2026-10-24 (18:00–18:20 ICT)
**Máy:** Windows 10, host `admin`
**Dự án:** `E:\CLAUDE\YOUTUBE\GPT_BRIDGE`
**Phạm vi:** chỉ Phase 5. Không viết lại Phase 1–4.
**Kết luận:** **NO-GO** cho tự động hoá thật ở thời điểm này — *hạ tầng đã xong và đã chứng minh chạy đúng, nhưng chưa thể nghiệm thu "tích hợp GPT thành công" vì Codex CLI chưa đăng nhập và model GPT-6 chưa xác nhận được trên tài khoản.*

---

## 0. Tóm tắt một bảng

| Câu hỏi của chủ dự án | Trả lời | Bằng chứng |
|---|---|---|
| Đăng nhập bằng tài khoản ChatGPT hiện tại? | **ĐƯỢC về cơ chế, HIỆN CHƯA đăng nhập** | `codex login --device-auth` phát mã thật; `codex login status` → `Not logged in` |
| Dùng được model GPT-6 mong muốn? | **CHƯA XÁC NHẬN** — `gpt-6.1-sol` là model GPT-6 dùng được trong Codex CLI, nhưng catalog local chưa có (chưa đăng nhập) | `~/.codex/models_cache.json` chỉ có 5 model đời 5.x |
| Đọc ZIP hồ sơ local + viết kịch bản Nhật dài? | **Hạ tầng ĐẠT** (đã test bằng stub), **chưa test bằng GPT thật** | job giả lập: 11 file hồ sơ staged, ZIP ra 5 file |
| `codex exec` tạo được 3 file kết quả? | **ĐẠT** — 4 file (3 bắt buộc + 1 tùy chọn) | `[8] adapter status: result_available (4 file)` |
| Đóng ZIP và trả cho `output_collector` hiện tại? | **ĐẠT** — không sửa `output_collector` | job `COMPLETED`, `output_file_id=F0005` |
| Chạy mà không chiếm chuột/bàn phím? | **ĐẠT** | `CREATE_NO_WINDOW` + `codex exec` headless, không cần cửa sổ |
| Phí API riêng hay chỉ hạn mức ChatGPT? | **Chỉ hạn mức ChatGPT** khi đăng nhập bằng tài khoản; **có phí API** nếu dùng API key | tài liệu pricing chính thức, mục "Pricing options" |

---

## 1. Xác nhận phương thức đăng nhập

Codex CLI **0.147.0** hỗ trợ hai đường đăng nhập. Đường dùng được cho máy không tương tác là **device code authorization** (không cần mở trình duyệt trên chính máy đó, không chiếm chuột/bàn phím):

```
codex login --device-auth
```

Đã chạy thật, CLI phát mã một lần dùng:

```
Welcome to Codex [v0.147.0]
Follow these steps to sign in with ChatGPT using device code authorization:
*** Open this link in your browser and sign in to your account
   https://auth.openai.com/codex/device
2. Enter this one-time code (expires in 15 minutes)
   ESND-JVT0T
```

**Trạng thái hiện tại: CHƯA đăng nhập.**

```
$ codex login status
Not logged in          (exit code 1)
```

### ⚠️ Cảnh báo quan trọng — `codex login status` có thể báo sai

Trong quá trình làm, đã gặp đúng tình huống: `codex login status` trả **"Logged in using ChatGPT"** nhưng khi gọi thật thì API trả **401 Unauthorized**. Nguyên nhân: access token đã hết hạn mà lệnh `status` vẫn báo OK.

Vì vậy adapter **không tin** `login status`. Hàm `health()` đọc thêm hạn của token trong `~/.codex/auth.json` (chỉ đọc trường `exp`, **không bao giờ ghi log nội dung token**) và chặn bằng mã lỗi riêng:

| Tình huống | Mã lỗi |
|---|---|
| CLI không có trong PATH | `E_CODEX_NOT_INSTALLED` |
| Chưa đăng nhập | `E_CODEX_NOT_LOGGED_IN` |
| Token đã hết hạn (dù `status` báo OK) | `E_CODEX_TOKEN_EXPIRED` |
| Model không có trong catalog | `E_CODEX_MODEL_UNAVAILABLE` |

Thiếu `auth.json` **không** bị coi là đã đăng xuất (credential có thể nằm ở keyring), khi đó vẫn tin `login status`.

---

## 2. Danh sách model thực tế

### 2.1 Catalog trên máy (đọc từ `~/.codex/models_cache.json`)

Đây là catalog **thật** mà CLI đang có, ghi lúc 2026-08-14, tức **trước khi đăng nhập**:

| slug | tên hiển thị |
|---|---|
| `gpt-5.6-terra` | GPT-5.6-Terra |
| `gpt-5.6-luna` | GPT-5.6-Luna |
| `gpt-5.5` | GPT-5.5 |
| `gpt-5.4-mini` | GPT-5.4-Mini |
| `codex-auto-review` | Codex Auto Review (ẩn) |

**Không có bất kỳ model GPT-6 nào trong catalog local.** Catalog này sẽ được làm mới sau khi đăng nhập.

### 2.2 Theo tài liệu chính thức

- Model GPT-6 dùng được trong **Codex CLI** là **`gpt-6.1-sol`** (GPT-6.1 Sol).
- Có gói theo **Plus, Pro, Business, Enterprise, Edu**. **Free và Go không có** ở thời điểm ra mắt.
- Enterprise/Edu phải được quản trị viên bật.
- GPT-6.1 Sol, GPT-6 Sol, GPT-6 Luna **có trong Work và Codex**, **không có trong Chat**.

> **Không tự chọn model khác.** Nếu `gpt-6.1-sol` không có trong catalog sau khi đăng nhập, adapter dừng với `E_CODEX_MODEL_UNAVAILABLE` và giữ nguyên tên model, đúng quyết định của chủ dự án. Đã có test riêng cho việc này.

---

## 3. Kết quả test

### 3.1 Bộ test tự động — 105 test, 0 fail

```
$ .venv/Scripts/python.exe -m pytest
102 passed, 3 skipped in 7.31s
```

- **77 test của Phase 1–4 vẫn xanh nguyên vẹn** (không sửa file test cũ nào).
- **28 test mới** cho Phase 5 (`tests/test_phase5_codex.py` + `tests/fixtures/stub_codex.py`).
- 3 skip là của Phase 1–4, có từ trước.

Test Phase 5 phủ: hợp đồng adapter, health, chặn token hết hạn, không fallback model, an toàn sandbox, chỉ stage file text trong whitelist, chặn ZIP traversal, end-to-end submit→status→collect.

### 3.2 Job giả lập chạy qua ĐÚNG đường ống thật

Dùng `Orchestrator` thật, `zip_qc` thật, `output_collector` thật, `FakeDrive`. Chỉ thay phần "gọi GPT" bằng stub để không cần đăng nhập.

```
[4] job: H2DEV-N-ep99-GPT6-v01 -> DETECTED
[5] validate: ok=True files=11
[6] prepare: ok=True prompt=2185 ký tự
[7] submit: ok=True note=codex exec đã chạy (pid=44112, model=gpt-6.1-sol, sandbox=read-only, 11 file hồ sơ)
[8] adapter status: result_available (4 file)
[9] finish: ok=True
[10] QC verdict=PASS_FORMAT_ONLY chars=251
     required_files={'ok': True, 'missing': []}
     length={'ok': True, 'chars': 251, 'min': 250, 'target': 300, 'max': 400}
     hook_verbatim={'ok': True, 'hook_chars': 59}
     identity={'ok': True, 'job_id_found': True, 'episode_found': True}
[11] trạng thái cuối: COMPLETED
     output_name=LaneN_tap99_GPT6_SCRIPT_DELIVERY.zip
[12] đọc lại ZIP từ Drive giả: 5 file
     ['artifact_meta.json','SCRIPT_FINAL_JP.txt','SCRIPT_ISSUES.md','SCRIPT_QC_REPORT.md','SCRIPT_STRUCTURE.md']
```

**Cả 4 kiểm tra QC đều xanh**, gồm `hook_verbatim` (hook chép nguyên văn 59 ký tự) và `identity` (đúng job_id + đúng tập).

Chạy lại được bằng: `.venv/Scripts/python.exe tools/phase5_sim_job.py`

### 3.3 Test với Codex CLI thật

```
$ codex --version
codex-cli 0.147.0

$ codex login status
Not logged in                                    (exit 1)

$ codex exec --skip-git-repo-check -C <temp> -s read-only -m gpt-6.1-sol -o last.txt "reply with exactly PONG"
ERROR: unexpected status 401 Unauthorized: Missing bearer or basic authentication in header,
url: https://api.openai.com/v1/responses
                                                 (exit 1, 15.84s)
```

Đây là **bằng chứng dứt điểm**: CLI chạy được, tham số đúng, gọi đúng endpoint `api.openai.com/v1/responses`, nhưng **không có credential** → 401. Không phải lỗi adapter, không phải lỗi mạng.

---

## 4. Khả năng tạo file TXT, MD và ZIP

| Định dạng | Tạo được? | Cách làm |
|---|---|---|
| **TXT** (`SCRIPT_FINAL_JP.txt`) | ✅ | Codex trả khối mã, adapter bóc ra file thật |
| **MD** (`SCRIPT_QC_REPORT.md`, `SCRIPT_STRUCTURE.md`, `SCRIPT_ISSUES.md`) | ✅ | cùng cơ chế |
| **ZIP** | ✅ | `output_collector` hiện tại đóng gói, **không sửa gì** |

Adapter bóc file theo hợp đồng đầu ra cố định: Codex phải trả về đúng các khối `### <tên file>` + khối ba dấu huyền. Adapter kiểm đủ file bắt buộc rồi mới cho qua; thiếu file nào thì báo `E_RESULT_INCOMPLETE` kèm tên file thiếu.

Kết quả job giả lập: **4/4 file** (3 bắt buộc + `SCRIPT_STRUCTURE.md`), ZIP 1.066 byte, SHA256 đọc lại khớp.

---

## 5. Phí và hạn mức

Đây là điểm **quyết định** giữa hai đường đăng nhập:

| Đường đăng nhập | Cách tính phí |
|---|---|
| **Tài khoản ChatGPT** (`codex login`) | **Không phát sinh phí API riêng.** Dùng chung hạn mức của gói ChatGPT. |
| **API key** (`codex login --with-api-key`) | **Tính tiền theo giá API**, tách khỏi hạn mức gói. |

Trích tài liệu pricing chính thức:

> **ChatGPT Work and Codex share usage.** ChatGPT Work usage inside ChatGPT uses the same pricing, credits, and usage limits as Codex.
>
> API token prices are separate from subscription usage; don't use them to estimate included tasks.

Bảng giá theo credit (chỉ áp dụng cho gói tính credit):

| Model | Input /1M | Cached input /1M | Output /1M |
|---|---|---|---|
| GPT-6 Astra | 250 credits | 25 credits | 1.250 credits |
| **GPT-6.1 Sol** | **50 credits** | **2,5 credits** | **250 credits** |
| GPT-6 Sol | 50 credits | 5 credits | 250 credits |

**Cách tự kiểm hạn mức còn lại:**
- Dashboard: https://chatgpt.com/codex/settings/usage
- Trong phiên CLI đang chạy: gõ `/status`
- **Không có lệnh CLI riêng để đọc hạn mức** — `codex --help` không có `usage`/`quota`/`credit`. Phải xem dashboard hoặc `/status`.

**Kết luận phí:** với quyết định "không cấp ngân sách API riêng", chỉ dùng được đường **đăng nhập tài khoản ChatGPT**. Đường API key bị loại.

---

## 6. Mức sử dụng RAM

Đo thật bằng `tasklist` + `wmic` (Working Set, gồm cả cây tiến trình con). Không thêm dependency nào.

| Đối tượng | RAM đỉnh |
|---|---|
| Tiến trình Python của bridge | **13,2 MB** |
| `codex --version` | < 1 MB (thoát ngay) |
| `codex login status` | < 1 MB |
| **`codex exec` (gọi thật, tới lúc 401)** | **56,9 MB** |

`codex exec` đỉnh **~57 MB** — rất nhẹ so với việc mở một cửa sổ Chrome cho ChatGPT Web (hàng trăm MB mỗi tab). Với một kịch bản dài, RAM sẽ tăng theo lượng token trong context, nhưng mức nền rất thấp.

Đo lại bằng: `.venv/Scripts/python.exe tools/phase5_ram_probe.py` → ghi `temp/phase5_ram_probe.json`

---

## 7. Các thao tác còn thủ công

### 7.1 Bắt buộc, một lần cho mỗi máy

1. **Đăng nhập Codex CLI** — mở `https://auth.openai.com/codex/device`, nhập mã 8 ký tự, hạn 15 phút.
   Đây là thao tác **không thể tự động** (cần chủ tài khoản ChatGPT bấm đồng ý). Sau khi làm một lần, token được lưu và các job sau chạy hoàn toàn tự động cho tới khi token hết hạn.

### 7.2 Còn thủ công trong mỗi chu kỳ (nếu muốn)

2. **Đổi `writer.adapter` trong `config.yaml`** từ `mock_chatgpt` sang `codex_cli`. Đã để sẵn khối cấu hình, chỉ cần đổi giá trị. Có thể tự động hoá bằng biến môi trường nếu chủ dự án đồng ý.

### 7.3 Đã tự động hoàn toàn (không cần thao tác)

- Tải ZIP từ Google Drive về temp
- Giải nén + kiểm tra hồ sơ (11 file)
- Stage hồ sơ vào thư mục làm việc, **chỉ nhận file text trong whitelist**
- Gọi `codex exec` headless, không chiếm chuột/bàn phím
- Bóc 3–4 file kết quả, kiểm đủ file bắt buộc
- Đóng ZIP, QC, upload OUTPUT, ghi trạng thái job

**So với MANUAL hiện tại:** giảm từ *kéo thả file + dán prompt + chép 4 file + đóng ZIP* xuống còn **0 thao tác mỗi job** (sau khi đăng nhập một lần).

---

## 8. Thiết kế đã dựng

### 8.1 File mới (không sửa adapter cũ nào)

| File | Vai trò |
|---|---|
| `adapters/codex_cli_writer.py` | Adapter writer thật qua `codex exec` |
| `tests/test_phase5_codex.py` | 28 test Phase 5 |
| `tests/fixtures/stub_codex.py` | Stub CLI để test không cần đăng nhập/mạng |
| `tools/phase5_sim_job.py` | Job giả lập qua đường ống thật |
| `tools/phase5_ram_probe.py` | Đo RAM thật |

### 8.2 Sửa tối thiểu, chỉ THÊM (không đổi hành vi cũ)

| File | Thay đổi |
|---|---|
| `services/errors.py` | Thêm 4 mã lỗi `E_CODEX_*` |
| `services/orchestrator.py` | Thêm nhánh `codex_cli` trong `build_adapter()` |
| `config.yaml` | Thêm khối `writer.codex_cli`; `writer.adapter` **vẫn là `mock`** (chưa bật `codex_cli`) |
| `bridge.py` | `doctor` kiểm thêm health của `codex_cli` |

### 8.3 An toàn — theo đúng yêu cầu

- **Sandbox `read-only`**: Codex không ghi được vào hồ sơ, không sửa file nguồn.
- **Không dùng chế độ toàn quyền**: không `--dangerously-bypass-approvals-and-sandbox`, không `--full-auto`, không `-s danger-full-access`. Có test khẳng định điều này.
- **Không thực thi mã trong ZIP**: chỉ file có đuôi text trong whitelist mới được stage; `.bat`, `.exe`, `.ps1`, `.py`… bị loại. `zip_qc._safe_members` chặn thêm ZIP traversal (`../`, đường dẫn tuyệt đối, symlink).
- **Không chiếm chuột/bàn phím**: `CREATE_NO_WINDOW`, không mở cửa sổ, không điều khiển Chrome của người dùng.
- **Không log token**: `token_state()` chỉ trả `present/expired/expires_at`.

### 8.4 Đã giữ nguyên cam kết với N-ep17

`jobs/DRYRUN-N-ep17-GPT6-v01/` — toàn bộ file có mtime **17:46**; mọi artifact Phase 5 tạo lúc **18:11** trở đi. **Không file nào của N-ep17 bị đọc-ghi, đổi tên hay xoá.** Job giả lập dùng job_id riêng `H2DEV-N-ep99-GPT6-v01` trong thư mục tạm.

---

## 9. Kết luận GO / NO-GO

### ❌ NO-GO — chưa thể nghiệm thu "tích hợp GPT thành công"

Theo đúng điều kiện chủ dự án đặt ra: *"Không báo tích hợp GPT thành công nếu chưa tạo được kịch bản thử và nhận lại file bằng pipeline thật."*

Điều kiện đó **chưa thoả**, vì hai lý do khách quan:

1. **Codex CLI chưa đăng nhập** → `401 Unauthorized`. Không thể sinh kịch bản thật.
2. **Chưa xác nhận `gpt-6.1-sol` có trên tài khoản** → catalog local chưa có model GPT-6, và chỉ sau khi đăng nhập mới biết gói hiện tại có GPT-6.1 Sol hay không (Free/Go không có).

### ✅ Phần đã đạt (không phải suy đoán — đã chạy thật)

- Adapter, cấu hình, mã lỗi, doctor: **xong**.
- Job giả lập qua đường ống thật: **COMPLETED**, QC 4/4 xanh, ZIP trả về đúng 5 file, SHA256 khớp.
- 105 test, **0 fail**; 77 test Phase 1–4 nguyên vẹn.
- Không chiếm chuột/bàn phím; RAM ~57 MB; sandbox read-only; không thực thi mã trong ZIP.
- Phí: **không phát sinh API riêng** nếu đăng nhập bằng tài khoản ChatGPT.

### Đường ngắn nhất để chuyển sang GO

Chỉ cần **một** thao tác của chủ dự án:

```
codex login --device-auth
```

→ mở `https://auth.openai.com/codex/device`, nhập mã, chọn tài khoản ChatGPT có gói Plus/Pro/Business.

Sau đó tôi chạy tiếp, không cần sửa code:

1. `python tools/phase5_ram_probe.py` — xác nhận đăng nhập và catalog model thật.
2. Kiểm `gpt-6.1-sol` có trong catalog không. **Nếu không có → dừng, báo chủ dự án** (không tự đổi model).
3. Chạy job thật trên **một tập mới chưa có kịch bản** (không dùng N-ep17).
4. Đối chiếu 3 file kết quả + QC, rồi mới báo GO.

### Phương án dự phòng

Nếu sau khi đăng nhập mà tài khoản **không có** `gpt-6.1-sol`:

- **Không** tự hạ xuống model khác (đúng quyết định của chủ dự án).
- Chuyển sang kiểm tra **ChatGPT Web adapter** trong profile Chrome **riêng biệt** (`E:\CLAUDE\YOUTUBE\CHATGPT_PROFILE`), tuyệt đối không đụng Chrome của người dùng. Lưu ý: adapter web cần điều khiển trình duyệt nên **có chiếm chuột/bàn phím** — kém hơn Codex CLI về mặt này.
- Nếu cả hai không đạt: **giữ nguyên chế độ MANUAL** hiện tại.

---

## 10. Việc cần chủ dự án quyết

1. **Đăng nhập Codex CLI ngay bây giờ?** (cần mã device, hạn 15 phút) — đây là điều kiện duy nhất còn thiếu để chạy thử thật.
2. **Gói ChatGPT hiện tại là gói nào?** (Plus / Pro / Business) — để biết có `gpt-6.1-sol` hay không trước khi hẹn giờ chạy.
3. **Tập nào dùng để thử thật?** Cần một tập **chưa có kịch bản**, không phải N-ep17.
4. **Có cho phép tự đổi `writer.adapter` trong `config.yaml`** thay vì sửa tay không?

---

*Báo cáo lập 2026-10-24. Mọi số liệu trong tài liệu này lấy từ lần chạy thật trên máy, không phải ước lượng.*
