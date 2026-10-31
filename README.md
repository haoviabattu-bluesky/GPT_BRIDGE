# GPT_BRIDGE — cầu nối giao nhận kịch bản H2DEV → GPT-6

Thay thao tác trung gian bước 5–7 của H2DEV: phát hiện yêu cầu ở cột K tab `lane`,
kiểm hồ sơ trên Google Drive, dựng chỉ thị viết, giao GPT-6, nhận ZIP, QC, upload OUTPUT.

**Không** thay Fabel. **Không** đổi Hook/Outline đã duyệt. **Không** để DeepSeek viết thay GPT-6.

## Trạng thái thật (2026-10-24)

| Hạng mục | Trạng thái |
|---|---|
| Test | **77 PASS / 0 FAIL** (`BRIDGE_LIVE=1 pytest tests/ -q`) |
| Google Sheets (bảng `YOUTUBE`, tab `Lane-N`) | ✅ đọc thật được |
| Google Drive (resolve INPUT/OUTPUT bằng API) | ✅ đo thật trên `N-ep17_v01` |
| Tải ZIP hồ sơ thật + SHA-256 + QC 11 file | ✅ đo thật |
| Upload thật lên OUTPUT thật | ⛔ **chưa làm** (dry-run dừng trước upload) |
| Điều khiển ChatGPT tự động | ⛔ **chưa chứng minh** — đang chạy MANUAL |
| DeepSeek trên đường tới hạn | ⛔ không dùng (đường tất định bằng Python) |

Đọc `docs/KNOWN_LIMITATIONS.md` trước khi tin bất kỳ con số nào ở trên.

## Cài đặt

```powershell
git clone https://github.com/haoviabattu-bluesky/GPT_BRIDGE.git
cd GPT_BRIDGE
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

## Cấu hình

Kho mã này **không chứa** cấu hình thật. `config.yaml` nằm trong `.gitignore` vì nó giữ
token, ID Drive/Sheet và đường dẫn thông tin xác thực. Bản mẫu đã che: `config.example.yaml`.

```powershell
Copy-Item config.example.yaml config.yaml
# rồi điền giá trị THẬT của máy bạn vào config.yaml
.\start.ps1 -Doctor
```

> **Bảo mật:** không commit `config.yaml`, thư mục `jobs/ logs/ temp/ scratch_analysis/`,
> hay bất kỳ `token.json` / `credentials.json` / service-account key nào. Xem `.gitignore`.

## Dùng

```powershell
.\start.ps1                  # một vòng: detect -> validate -> prepare -> submit
.\start.ps1 -Loop            # chạy nền (60s/vòng)
.\start.ps1 -Stop            # dừng an toàn
```

Chi tiết + 20 mã lỗi: `docs/OPERATIONS.md`.

## Quy trình một tập

```
H2DEV:  hồ sơ ZIP -> Drive INPUT, ghi trạng thái cột K
Hermes: detect -> validate -> prepare  (tự động)
NGƯỜI:  dán prompt vào ChatGPT Web, tải 4 file, đóng ZIP, thả jobs/_inbox/
Hermes: resume -> QC -> upload OUTPUT -> verify -> COMPLETED  (tự động)
```

Phần **sau khi có ZIP là tự động**. Phần **giao/nhận với ChatGPT là thủ công** — đây là
mức tự động thật của V1.

## Tài liệu

| File | Nội dung |
|---|---|
| `docs/IMPLEMENTATION_REPORT.md` | báo cáo triển khai + trả lời nghiệm thu |
| `docs/TEST_REPORT.md` | 77 test, bằng chứng |
| `docs/ADAPTER_FEASIBILITY.md` | đánh giá trung thực khả năng điều khiển ChatGPT |
| `docs/OPERATIONS.md` | vận hành |
| `docs/KNOWN_LIMITATIONS.md` | 18 giới hạn thật |

## Nguyên tắc bất di bất dịch

1. Không đoán ID Drive — luôn resolve bằng API.
2. Không ghép chuỗi `/input` `/output` vào link.
3. Không upload ZIP mock như sản phẩm thật.
4. Không gửi lại prompt khi chưa biết GPT đã nhận.
5. Không đánh dấu COMPLETED khi Drive chưa xác nhận.
6. Không chiếm chuột/bàn phím, không đóng Chrome người dùng.
7. Không sửa dữ liệu H2DEV (V1 read-only với bảng tính).
