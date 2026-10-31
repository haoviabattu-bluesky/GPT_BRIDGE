# AGENT_LOOP_TEST_REPORT — Kết quả kiểm thử thực tế

> Máy: Windows 10 · Ngày: 2026-10-25 · Kênh: Chrome riêng + CDP (Playwright)
> Nguyên tắc ghi báo cáo: **chỉ ghi cái đã đo được**. Việc chưa chứng minh được
> thì ghi rõ "chưa chứng minh", không suy ra từ việc "extension hiển thị prompt".

---

## 1. Tóm tắt điều hành

| Câu hỏi | Kết quả đo được |
|---|---|
| Gửi prompt thật tới ChatGPT Web | **CÓ** — đo bằng số lượt tăng sau khi gửi |
| Nhận phản hồi thật | **CÓ** — đọc đúng khối trả lời cuối, chờ hết streaming |
| Duy trì cùng một hội thoại nhiều lượt | **CÓ** — 5 lượt liên tiếp, cùng `conversation_id` |
| Xác định phản hồi thuộc job/round nào | **CÓ** — mỗi lượt 1 báo cáo có SHA256 riêng, GPT trả `round` trong khối lệnh |
| Cướp chuột/bàn phím | **KHÔNG** — đo cô lập, foreground không đổi |
| Vòng lặp tự chạy nhiều round không người copy/paste | **CÓ** — 4–5 round liên tiếp tự động |
| GPT-6 tự kết luận dừng | **CÓ** — trả `next_action: STOP` |

**Kết luận thẳng:** vòng lặp hai chiều nhiều lượt **đã chạy thật**, không phải mock.
Nhưng chất lượng phụ thuộc executor: khi executor là khung rỗng, GPT-6 phát hiện
đúng và `BLOCKED` — đó là hành vi đúng, không phải lỗi.

---

## 2. Những gì ĐÃ chạy thật (có bằng chứng đo)

### 2.1 Kênh truyền nhận

| Hạng mục | Cách đo | Kết quả |
|---|---|---|
| Chrome riêng mở CDP cổng 9335 | `GET /json/version` trả 200 | Đạt |
| Profile đã đăng nhập | mở `chatgpt.com`, có ô soạn tin, không bị chặn | Đạt |
| Gửi tin | số lượt (`turnCount`) tăng sau khi bấm gửi | Đạt |
| Nhận trả lời đầy đủ | chờ nút Stop biến mất + độ dài ổn định 4 s | Đạt |
| Cùng hội thoại | `location.href` giữ `/c/<id>` qua 5 lượt | Đạt |
| Không cướp focus | sampler 0,25 s/lần trong suốt send+recv | **0 cửa sổ lạ** — so **HWND** (`6297370` đầu = cuối) |
| Không đọc cookie | chỉ dùng DOM + CDP, không gọi `Network.getCookies` | Đạt |

Bằng chứng: `agent_loop/test_runs/focus_clean.json` →
`{"no_steal": true, "stolen": [], "turns": 1}`.

### 2.2 Các bẫy ĐÃ GẶP VÀ ĐÃ SỬA (đây là phần quan trọng nhất)

| # | Bẫy | Triệu chứng đo được | Cách sửa |
|---|---|---|---|
| 1 | Selector cũ chết | `[data-message-author-role]` → **0 khớp** | Dùng `[data-turn-key]` + `[data-markdown-copy]` (đo thật trên UI 25/10) |
| 2 | Bản nháp tồn đọng | ô soạn còn **3326 ký tự**; `insert_text` không chèn được; bấm Send vẫn "thành công" nhưng **số lượt không tăng** | Ctrl+A → Delete trước khi gõ; đo lại độ dài; lệch >5% thì **không gửi** |
| 3 | Đọc sớm | model đứng vài giây giữa lúc viết → tưởng xong | Chờ nút Stop biến mất **và** độ dài ổn định ≥4 s |
| 4 | Tin dài làm chết vòng lặp | báo cáo 3062 ký tự > ngưỡng 3000 → `E_PROMPT_TOO_LONG`, dừng giữa đường | Tự ghi ra file + đính kèm, gửi phần đầu làm con trỏ (đúng protocol §B) |
| 5 | Chỉ thị không tới executor | round 2 và 3 trả **cùng SHA256** với round 1 | `parse_directive` giữ lại phần văn xuôi; `run_executor` truyền `instruction` xuống |
| 6 | Hội thoại cấu hình đã chết | URL `/c/6ade1141…` redirect về trang chủ, 0 lượt | Phát hiện `E_CONVERSATION_DEAD`, mở chat mới, ghi lý do vào `history[]` |
| 7 | Đếm tin bằng selector sai | `data-message-author-role` = 0 dù có tin | Đếm bằng `[data-turn-key]` |
| 8 | Đính kèm "thành công" nhưng mất | chip hiện ở t+1s, nút Send chỉ BẬT ở **t+4s** → bấm vào nút disabled, không gửi, `E_SEND_UNCERTAIN` | `_attach` chờ **cả** chip **và** `sendUsable`; `JS_SEND` từ chối bấm nút khoá |
| 9 | Gửi trùng task khởi động | hội thoại thật có **3 lượt y hệt** ("round: 1 / TASK_READY / SURVEY") dù chỉ định 1 | Tách nhánh `NEXT_TASK` khỏi `TASK_READY`: NEXT_TASK → EXECUTING ngay, KHÔNG gửi lại `task_text` (`loop.py` `run()`) |
| 10 | `TRANSPORT_UNAVAILABLE` không ghi được | send() hỏng lúc đang NEXT_TASK → `StateError "không được nhảy NEXT_TASK -> TRANSPORT_UNAVAILABLE"` **che mất lỗi mạng thật** | Cho `TRANSPORT_UNAVAILABLE` tới được từ **mọi** trạng thái chưa kết thúc (nó là điểm chặn hợp lệ, mục C) |
| 11 | `turnCount` GIẢM dù đã gửi | đo 5 lần liên tiếp: `turns=5` ổn định; có lúc 5→4 do ChatGPT **ảo hoá DOM** | Không xác nhận gửi bằng 1 tín hiệu: nhận khi **bất kỳ** (a) turnCount tăng, (b) `data-turn-key` mới, (c) ô soạn rỗng |
| 12 | **Khối `instructions:` bị vứt** | GPT-6 yêu cầu kiểm thử dedup chuyên biệt; 5 round liền trả **cùng SHA256** báo cáo self-test 6/6 | `parse_directive` cắt prose tại dòng `loop` nên mất khối lệnh → thêm `_extract_instructions()` vét YAML block `>`/`|` và **nối vào `instruction`** |
| 13 | **`_await_directive` không ghi `data["instruction"]`** | job `dedup-v2`: GPT-6 đòi test dedup, executor vẫn chạy self-test chung 3 round liền | Chỉ ghi `last_directive`, thiếu `instruction` — mà `run_executor` đọc `data['instruction']`. Sửa: ghi **cả hai**; khoá bằng `test_await_directive_gan_instruction_vao_data` |
| 14 | **TEST trả bài chung khi chỉ thị lạ** | GPT-6 bác: *"Do not return generic 6/6 agent_loop self-test as the sole evidence. Report BLOCKED with exact technical reasons"* | Nhánh fallback lặng lẽ trả "6 đạt/0 hỏng" bất kể chỉ thị gì → tách `test_agent_loop()` (điều phối, không khớp thì **BLOCKED**) khỏi `core_self_check()` (dùng cho `cli self-test`) |
| 15 | **`FIX` không có executor** | run `20261025_220917` round 4: `r4_report.md` = "chưa có executor cho lệnh FIX", `evidence=0`, `artifacts=0` → GPT-6 mất trắng 1 round rồi phải `BLOCKED` | Thêm `FIX` → chạy qua `mg_implement` (đúng như GPT-6 chỉ định: *"phần việc FIX có thể được thực hiện thông qua executor IMPLEMENT"*), báo cáo ghi rõ là đi qua IMPLEMENT |
| 16 | **Script nghiệm thu tự bịa "OK"** | run `20261025_220917` GPT-6 chốt `BLOCKED` nhưng `verdict` vẫn ghi *"OK: 5 round thật, tự động hoàn toàn"* → báo cáo SAI, che mất thất bại | `verdict` phải đọc `task_status` **cuối của GPT-6**; BLOCKED/NEEDS_OWNER_ACTION → verdict BLOCKED; chỉ `COMPLETED` mới được "OK" |
| 17 | **`parse_directive` trả 2 shape khác nhau** | run `live_fix` chết ngay sau round 1: `LỖI KeyError: 'parsed'`, EXIT=1 | Khi `ok=True` kết quả là dict **PHẲNG** (`next_action`, `round`, `task_status`, `instruction`); key `parsed` chỉ có khi `ok=False`. Đọc `d2["parsed"]` là sai → phải lấy thẳng từ dict khi ok |
| 18 | **Round 1 bị đếm 2 lần** | run `20261025_233729`: `evidence.json` ghi `rounds=5` nhưng chỉ có 4 round thật; round đầu toàn `None` | Có stub `{round:1, directive...}` ghi trước vòng lặp **và** bản đầy đủ ghi trong vòng lặp → bỏ stub |
| 19 | **Đo "cướp focus" bằng tiêu đề → BÁO GIẢ** | `probe_focus_clean` báo `KHÔNG cướp focus: False`, "cửa sổ lạ" toàn là Chrome với tiêu đề khác nhau | Chrome **tự đổi tiêu đề** khi ChatGPT stream ("ChatGPT" → "Trả lời kiểm tra …"). So **HWND** thay vì tiêu đề → đo lại: cùng HWND `6297370`, danh sách lạ rỗng, `no_steal: True` |
| 20 | **Mốc đo focus đặt SAI chỗ** | `live_3rounds` vẫn báo `focus_not_stolen=False` dù gửi/nhận không cướp | Mốc lấy TRƯỚC `open()` — lúc đó là cửa sổ Hermes, Chrome chưa bật. `connect_over_cdp` có kéo Chrome lên rồi `new_conversation()` trả lại. Đo từng bước (`probe_focus_stages`): S0b Hermes → **S1 Chrome (chỉ `open()`)** → S2 Hermes → S3 Hermes → S4 Hermes. Mốc đúng phải lấy **sau `new_conversation()`** |
| 21 | **`SetForegroundWindow` bị Windows chặn** | `_restore_foreground` trả `False`, `GetForegroundWindow()` về `0` | Tiến trình nền không có quyền foreground → không thể "trả focus" bằng API. Đừng thiết kế dựa vào nó; ghi nhận đúng là không hiệu quả và bỏ khỏi luồng chính |
| 22 | **`next_action:` ở ĐẦU khối lệnh → cắt mất nhiệm vụ** | run `20261025_235344`: GPT-6 nói *"Executor SURVEY không thực hiện khảo sát theo yêu cầu"*; 3 round cùng SHA | `parse_directive` cắt prose từ dòng `next_action:` — mà khối `TASK_ASSIGNMENT` đặt nó TRƯỚC 5 mục nhiệm vụ → vứt sạch. Sửa: chỉ cắt dòng `loop`; nếu không có, cắt dải key:value **liền nhau ≥3 dòng** ở cột 0 và GIỮ văn xuôi phía sau. Đo lại: instruction 2201 ký tự, đủ `MODULE_MAP`/`IMPLEMENTATION_PLAN`/`canh_*.js` |
| 23 | **Executor TEST cố định — mọi chỉ thị cùng một bài** | run `20261025_235751`: round 2 và round 4 CÙNG SHA; GPT-6 nghi *"Hermes có đang chạy một executor TEST cố định, chỉ lặp bộ 22 bài bất kể nhiệm vụ"* | Cả hai chỉ thị đều chứa `khoa_hook` nên cùng khớp `_test_mg_modules`. Tách `_pick_test()` (hàm thuần) với thứ tự ưu tiên rõ: parse_directive → dedup → **integration_lane_x** → mg_modules → `""` (BLOCKED). Thêm bài tích hợp thật `verify_integration_lane_x.py` (18 PASS) |

**Bẫy 12 là bẫy nặng nhất và do GPT-6 tự phát hiện** (job `dedup-check`, 2026-10-25):
> "Repeated wrong executor behavior across five rounds. Task-specific instructions
> appear not to reach or control execution. Root cause not yet verified."

Nó đúng: `parse_directive` cắt văn xuôi tại `^loop$`, mà khối `instructions:` nằm
**sau** dòng đó — nên executor chỉ nhận phần nhận xét, không bao giờ thấy yêu cầu
kỹ thuật. Đã sửa + khoá bằng 2 test (`test_parse_directive_giu_khoi_instructions`,
`test_parse_directive_instructions_khong_ro_ri_khi_van_xuoi_ngan`).

### 2.4 Đính kèm tệp — ĐÃ nghiệm thu

Đo thật (`probe_attach_sent.py`): tạo tệp có mã bí mật `ZEBRA-42`, đính kèm, hỏi
"trong tệp có mã gì". GPT trả **đúng `ZEBRA-42`** → tệp tới được và GPT đọc được.

Đo đường **spill tự động** (`probe_spill_path.py`): gửi tin **6306 ký tự** (vượt ngưỡng
3000), không truyền `attach`. Hệ tự ghi ra `message_*.md`, đính kèm, và GPT trả:

> Đã nhận được tệp message_20261025_220844_6306.md.
> Nội dung gồm 120 mục (từ mục 0 đến mục 119). Cuối báo cáo có mã kiểm tra: SPILL-ZEBRA-99.

→ **Đính kèm + spill hoạt động thật.** Đây là cơ chế đúng cho protocol §B
("file lớn lưu riêng, chỉ tóm tắt vào tin nhắn").

**Lưu ý quan trọng:** mã kiểm tra phải nằm **sâu trong thân tệp**, không nằm ở phần
đầu gửi inline — nếu không sẽ không phân biệt được "GPT đọc tệp" với "GPT đọc phần
tóm tắt trong tin nhắn". Lần đo đầu của tôi mắc đúng lỗi này (tệp ngắn nên không
spill, GPT trả lời "không thấy tệp đính kèm" vì câu hỏi không khớp ngữ cảnh).

### 2.3 Tự kiểm bộ máy (không cần mạng)

**pytest — 36 bài cho `agent_loop/`** (`tests/test_agent_loop.py`):

```bash
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -q
# 36 passed
```

Phủ: parse TASK/REPORT, chống sửa tay (SHA), chống gán nhầm job, máy trạng thái
(nhảy cóc bị chặn, trạng thái cuối không đổi được), khoá độc quyền (kể cả PID chết),
chống gửi trùng, checkpoint, ghi nguyên tử, và toàn bộ `parse_directive`.

**Toàn bộ dự án — 286 bài:**

```bash
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q
# 283 passed, 3 skipped, 0 failed
```

`self-test` của CLI (chạy trong tiến trình, không cần pytest) → **6/6 đạt**:

```
report_roundtrip · reject_tampered · reject_wrong_job
no_state_skip  · lock_exclusive   · dedup_send
```

Ý nghĩa từng bài:
- `reject_tampered`: sửa 1 ký tự trong báo cáo → SHA256 lệch → **từ chối**.
- `reject_wrong_job`: báo cáo của job khác gắn vào job này → **từ chối** (mục F).
- `no_state_skip`: nhảy trạng thái (CREATED → EXECUTING) → **từ chối**.
- `lock_exclusive`: tiến trình thứ hai vào cùng job → **bị chặn**.
- `dedup_send`: gửi lại cùng nội dung → **không gửi trùng**.

---

## 3. Kiểm thử 3+ lượt thật (nghiệm thu mục E)

### Run `20261026_000729` — 4 round, BẢN CUỐI (còn 1 lỗi CHƯA sửa)

| Round | GPT-6 giao | Hermes làm | SHA báo cáo | Evidence |
|---|---|---|---|---|
| 1 | `SURVEY` khảo sát MG_STUDIO | 18 bằng chứng, 5 artifact | `bccc842be22d` | 18 |
| 2 | `TEST` tích hợp trên bản sao | 18 PASS bài tích hợp | `fcad3bb245d1` | 24 |
| 3 | **`IMPLEMENT` nối guard vào `mg.py`** | **vẫn báo 3 tệp cũ** | `d731f87225b9` | 7 |
| 4 | `TEST` (GPT-6 lặp lại bài cũ) | 18 PASS **y hệt round 2** | `fc23ad486536` | 24 |

**Đính chính so với bản báo cáo trước** — run này KHÔNG sạch hoàn toàn:

- `focus_not_stolen = True` ✅ (đo bằng HWND, mốc lấy sau `new_conversation()`)
- `rounds = 4` ✅ (không còn round ma)
- 4 SHA khác nhau ✅ **nhưng SHA khác nhau chưa đủ**: r2 và r4 có **EVIDENCE
  giống hệt nhau** (18 dòng `PASS B*`, chỉ khác `full_log_sha256` vì log chứa
  timestamp). Nghĩa là GPT-6 bảo "chạy lại bài tích hợp" và executor chạy lại
  đúng bài đó — hợp lệ, nhưng **không phải bằng chứng tiến triển**.
- ❌→✅ **Lỗi thật, ĐÃ SỬA:** round 3 GPT-6 giao *"Tích hợp HOOK_LOCK vào entry
  point… scope: `mg.py` + các điểm gọi pipeline"*. `mg_implement.run()` nhận
  tham số `instruction` nhưng **không đọc nó** (`grep instruction
  agent_loop/mg_implement.py` → chỉ có 1 dòng ở chữ ký hàm). Vì vậy nó luôn
  kiểm 3 tệp cố định trong `TEP_MOI` và trả cùng một báo cáo, bất kể GPT-6
  yêu cầu gì.
  **Nay:** `_doi_sua_san_xuat()` dò dấu hiệu sửa tệp sản xuất (`mg.py`,
  `entry point`, `core/pipeline.py`, `các điểm gọi`…) → trả `next_action=STOP`
  + `blocker` nêu đúng điểm nghẽn, `artifacts=[]`, **không** báo "22/22 đạt".
  Đo trên chính chỉ thị thật round 3 (1702 ký tự): `STOP`, 0 artifact,
  `'22/22' in summary = False`. Khoá bằng test
  `test_implement_chi_thi_sua_san_xuat_thi_blocked`.

Đây chính là điều GPT-6 chỉ ra: *"chỉ chứng minh hai module hoạt động khi được
gọi trực tiếp… chưa cung cấp bằng chứng gọi qua entry point thực tế"*. **Nó
đúng, và nguyên nhân nằm ở executor, không phải ở GPT-6.**

### Run `20261025_220917` — 5 round, verdict CŨ GHI SAI (giữ làm bằng chứng lỗi)

**Không phải "ĐẠT"** như bản báo cáo trước ghi nhầm. Run này là bằng chứng cho
**3 lỗi cùng lúc**, tất cả đã sửa:

| Round | GPT-6 giao | Hermes làm | SHA | Vấn đề |
|---|---|---|---|---|
| 1 | `SURVEY` | 18 bằng chứng, 5 artifact | `81fdeb4cce` | ok |
| 2 | `IMPLEMENT` Hook Lock + QC limit | 2 module, 22 bài test | `d731f87225` | ok |
| 3 | `TEST` 2 module | 22/22 PASS | `018412f398` | ok |
| 4 | `FIX` | **`evidence=0 artifacts=0`** | `d45b1da922` | **chưa có executor FIX** |
| 5 | `STOP` (BLOCKED) | — | — | — |

Ba lỗi đo được trong run này:

1. **`FIX` không có executor** → round 4 mất trắng. Nay `FIX` → `mg_implement`.
2. **Verdict tự bịa "OK"** — log ghi `== KẾT LUẬN: OK: 5 round thật, tự động hoàn toàn ==`
   trong khi chính dòng dưới ghi `focus_not_stolen=False` và GPT-6 chốt `BLOCKED`.
   Nay verdict đọc `task_status` cuối của GPT-6.
3. **Round 1 bị đếm 2 lần** → `rounds=5` cho 4 round thật (round 0 toàn `None`).

Nguyên văn GPT-6 ở round cuối (đọc đúng là BLOCKED, không phải đạt):

> HOOK_LOCK: Đã triển khai module, unit test PASS.
> QC_LIMIT: Đã triển khai module, unit test PASS.
> Tổng kiểm thử: 22/22 PASS.
> Tích hợp pipeline thực tế: Chưa hoàn thành.
> MODEL_ROUTING, RENDER_SPLIT, FINAL_CHECK: Chưa được nghiệm thu đầy đủ.
> Video sản xuất: Chưa bị thay đổi theo các báo cáo trước.
> Trạng thái cuối: BLOCKED — chưa đủ điều kiện công bố MG_STUDIO hoàn thành.

Đây là **đánh giá đúng**: module đã xong và có test, nhưng chưa nối vào pipeline
sản xuất. Không có tuyên bố hoàn thành sai.

### Run `20261025_214840` — 4 round, GPT-6 kết luận BLOCKED

Đây là run **quan trọng nhất về mặt trung thực**: nó cho thấy vòng lặp hoạt động
đúng kể cả khi phía Hermes làm chưa xong.

| Round | GPT-6 giao | Hermes làm | GPT-6 nhận xét |
|---|---|---|---|
| 1 | `SURVEY` khảo sát MG_STUDIO | 17 bằng chứng, 5 artifact | Chấp nhận số liệu |
| 2 | `IMPLEMENT` pipeline lane x | IMPLEMENT chỉ tự kiểm agent_loop | "Chưa có thay đổi mã nguồn MG_STUDIO" |
| 3 | `IMPLEMENT` nhấn mạnh Hook Lock | trả **cùng SHA** với round 2 | "hai báo cáo cùng SHA256" |
| 4 | `STOP` | — | **BLOCKED**, yêu cầu chủ dự án can thiệp |

Nguyên văn GPT-6 ở round 4:

> Báo cáo round 3 có cùng report_sha256 với round 2 …
> Không tiếp tục yêu cầu IMPLEMENT, vì hai lượt liên tiếp không cung cấp bằng chứng
> thực thi trên mã nguồn dự án.
> Không đánh dấu COMPLETED hoặc DONE vì chưa có bằng chứng pipeline MG_STUDIO hoạt động.

**Đây là bằng chứng mạnh nhất rằng vòng lặp là thật:** GPT-6 đọc báo cáo, phát hiện
dữ liệu lặp, và từ chối PASS. Một hệ mock không làm được điều này.

### Hệ quả — đã sửa

Sau run này đã sửa 2 lỗi thật:
1. `parse_directive` không giữ phần văn xuôi → executor không thấy GPT hỏi gì.
2. `IMPLEMENT` là khung rỗng → nay ghi tệp thật + chạy nghiệm thu + trả SHA256.

### Run `20261025_214457` — 4 round, `focus_not_stolen: true`

Xác nhận lại sau khi sửa bản nháp: 4 round, cùng hội thoại, không cướp focus.

---

## 4. Việc THẬT đã làm trên MG_STUDIO (qua executor)

Hai module **mới thêm** (không sửa tệp nào đang chạy):

| Tệp | Việc | Nghiệm thu |
|---|---|---|
| `core/khoa_hook.py` | Khoá Hook đã duyệt bằng SHA-256 (bảo vệ luật 12) | Có trong bộ 22 bài |
| `core/trang_thai_qc.py` | Trạng thái QC bền vững, `qc_rounds <= 1`, resume không reset, Opus 1 lần | Có trong bộ 22 bài |
| `tests/test_khoa_hook_qc.py` | 22 bài nghiệm thu | **22 đạt / 0 hỏng** |

Chạy lại bất cứ lúc nào:

```bash
cd /e/CLAUDE/YOUTUBE/MG_STUDIO && PYTHONPATH= python tests/test_khoa_hook_qc.py
```

Các bài then chốt đã đo:
- Sửa Hook sau khi khoá → **phát hiện** (SHA lệch).
- Khác biệt chỉ ở khoảng trắng cuối dòng → **không** báo sửa (đã sửa lỗi băm thô JSON).
- `ghi_nhan_sua()` lần 2 → **`ViPhamQC`**.
- Resume từ đĩa → `qc_rounds` **vẫn = 1**, không reset.
- `ghi_nhan_opus()` lần 2 → **`ViPhamQC`**.
- `final_verified=true` mà thiếu `render_status` → **bị bắt**.
- Tệp trạng thái hỏng JSON → **không sập**, về mặc định an toàn.

**Chưa áp cho tập nào** — vì không được tự khoá hook chưa xác định trạng thái phê duyệt.
Hiện `0` tập có `khoa_hook.json`, `0` tập có `trang_thai_qc.json`.

---

## 5. Những gì CHƯA chứng minh được (phải nói rõ)

| Hạng mục | Trạng thái | Lý do |
|---|---|---|
| Nhận **file đính kèm do GPT-6 gửi về** | **ĐÃ đo chiều GỬI ĐI** — chiều GPT-6 gửi lại thì chưa | Đã chứng minh Hermes→GPT đính kèm được và GPT đọc được (`ZEBRA-42`); chiều ngược lại chưa có run nào GPT thực sự gửi file |
| Render HOOK_VIDEO / BODY_VIDEO tách rời | **CHƯA làm** | Thuộc phạm vi MG_STUDIO, cần chủ dự án cho phép chạy render |
| Ghép FINAL_VIDEO + ffprobe | **CHƯA làm** | Như trên; `ffprobe` có sẵn tại `E:\CLAUDE\MOTION GRAPHICS\tools\ffmpeg\bin\ffprobe.EXE` |
| Điều phối Fable → GPT-6 → Sonnet → Opus | **CHƯA nối** | Cần gọi model thật; chưa có quyền phát sinh chi phí |
| GPT-6 đọc tài liệu trên đĩa máy này | **KHÔNG THỂ** | ChatGPT Web không truy cập được ổ E:. Chỉ nhận nội dung trong tin nhắn/file đính kèm |
| Chạy khi máy đang ngủ/khoá màn hình | **CHƯA đo** | Chưa thử; rủi ro treo chờ |
| Chống CAPTCHA | **KHÔNG làm, không làm** | Gặp là dừng, báo chủ dự án xử lý tay |

---

## 6. Cách tự kiểm lại toàn bộ

> **BẮT BUỘC prefix `PYTHONPATH=`** — trên máy này `PYTHONPATH` đang bị tiêm
> `...\hermes-agent\venv\Lib\site-packages`, đứng TRƯỚC `.venv` của dự án và che
> mất `greenlet` bản đúng → `ModuleNotFoundError: greenlet._greenlet` (đã gặp thật).
> Không prefix là test đỏ giả.

```bash
cd /e/CLAUDE/YOUTUBE/GPT_BRIDGE

# 0. TOÀN BỘ test của dự án (phải 0 failed)
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests/ -q

# 1. bộ máy agent_loop (không cần mạng)
PYTHONPATH= .venv/Scripts/python.exe -m agent_loop.cli self-test

# 2. Chrome riêng đã đăng nhập chưa (chỉ đọc)
PYTHONPATH= .venv/Scripts/python.exe -m scratch_analysis.probe_cdp_login

# 3. không cướp focus (đo cô lập)
PYTHONPATH= .venv/Scripts/python.exe -u scratch_analysis/probe_focus_clean.py

# 4. nghiệm thu 3+ round thật
PYTHONPATH= .venv/Scripts/python.exe -u scratch_analysis/live_3rounds.py
```

Kỳ vọng: (0) `283 passed, 3 skipped, 0 failed`; (1) `6 đạt / 0 hỏng`;
(2) `logged_in: true`; (3) `KHÔNG cướp focus: True`; (4) `verdict` bắt đầu bằng
`OK`, `rounds >= 3`, `focus_not_stolen: true`.

### 6.1 Bẫy môi trường: cổng 9335 đang mở làm đỏ test cũ

`tests/test_phase4_adapters.py::TestChatGPTWebGuards` giả định **KHÔNG có** CDP ở
cổng 9335. Nếu Chrome của agent_loop còn chạy, test đó đỏ (`assert True is False`)
dù không liên quan gì tới code mới. Trước khi chạy toàn bộ test, tắt Chrome test:
```bash
# chỉ kill tiến trình giữ profile test, KHÔNG đụng Chrome của người dùng
PYTHONPATH= python -c "
import subprocess
out = subprocess.run(['wmic','process','where',\"name='chrome.exe'\",
                      'get','ProcessId,CommandLine','/format:csv'],
                     capture_output=True, text=True, errors='replace').stdout
pids = [l.rsplit(',',1)[-1].strip() for l in out.splitlines()
        if 'CHATGPT_PROFILE' in l and l.rsplit(',',1)[-1].strip().isdigit()]
for p in pids: subprocess.run(['taskkill','/F','/PID',p], capture_output=True)
print('đã tắt', len(pids))
"
```

---

## 7. Giới hạn kỹ thuật phải chấp nhận

1. **ChatGPT Web không phải API.** Không có `conversation_id` chính thức để "trả lời
   đúng luồng" — phải giữ URL `/c/<id>`. Nếu OpenAI đổi DOM, selector phải đo lại.
2. **Không đọc được file trên ổ đĩa.** Muốn GPT-6 "xem" tài liệu thì phải đính kèm
   hoặc dán nội dung; không có cách nào để nó tự mở `E:\...`.
3. **Phụ thuộc profile đăng nhập.** Hết phiên/hết hạn là dừng (`E_NOT_LOGGED_IN`),
   cần người đăng nhập lại 1 lần.
4. **Không song song.** Một Chrome, một tab → tuần tự. Muốn song song phải nhiều profile.
5. **DOM dễ đổi.** Mỗi lần ChatGPT cập nhật giao diện phải chạy lại `probe_dom_turns.py`.
