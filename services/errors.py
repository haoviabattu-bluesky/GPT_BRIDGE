# -*- coding: utf-8 -*-
"""errors.py — mã lỗi, thông báo, hướng xử lý.

Mỗi lỗi phải có mã + thông báo + hướng xử lý (spec §18).
Không retry vô hạn; không gửi lại prompt khi chưa rõ GPT đã nhận job.
"""
from __future__ import annotations

# code: (nhóm, thông báo, hướng xử lý)
CATALOG: dict[str, tuple[str, str, str]] = {
    # ---- cấu hình / môi trường
    "E_CONFIG": ("config", "Cấu hình thiếu hoặc sai", "Kiểm tra config.yaml"),
    "E_CREDENTIALS": ("config", "Không đọc được credential", "Kiểm tra file trong credentials:"),
    "E_UNKNOWN": ("internal", "Lỗi không xác định", "Xem log và báo người vận hành"),

    # ---- Sheets
    "E_SHEET_AUTH": ("sheets", "Không truy cập được bảng tính",
                     "Kiểm tra service account đã được chia sẻ quyền xem bảng"),
    "E_SHEET_READ": ("sheets", "Không đọc được tab/dòng", "Kiểm tra tên tab và vùng scan_range"),
    "E_SHEET_PARSE": ("sheets", "Không parse được ô yêu cầu", "Đọc tay ô K và sửa định dạng"),

    # ---- Drive
    "E_DRIVE_AUTH": ("drive", "Token Drive không hợp lệ/hết hạn",
                     "Chạy lại bước OAuth để lấy token mới"),
    "E_DRIVE_NOT_FOUND": ("drive", "Không tìm thấy đối tượng trên Drive",
                          "Kiểm tra ID folder/file còn tồn tại và chưa bị xoá"),
    "E_DRIVE_NO_PERMISSION": ("drive", "Tài khoản Drive không đủ quyền",
                              "Chia sẻ folder cho đúng tài khoản trong credentials"),
    "E_DRIVE_INPUT_MISSING": ("drive", "Folder tập không có thư mục INPUT",
                              "Tạo INPUT trong folder tập rồi chạy lại"),
    "E_DRIVE_OUTPUT_MISSING": ("drive", "Folder tập không có thư mục OUTPUT",
                               "Tạo OUTPUT trong folder tập rồi chạy lại"),
    "E_DRIVE_FOLDER_AMBIGUOUS": ("drive", "Có nhiều folder cùng tên INPUT/OUTPUT",
                                 "Chỉ để đúng 1 folder mỗi loại trong folder tập"),
    "E_DRIVE_DOWNLOAD": ("drive", "Tải file từ Drive thất bại", "Kiểm tra mạng/quyền rồi chạy lại"),
    "E_DRIVE_UPLOAD": ("drive", "Upload lên Drive thất bại", "Kiểm tra mạng/quyền rồi chạy lại"),
    "E_DRIVE_UPLOAD_VERIFY": ("drive", "Upload xong nhưng xác minh không khớp",
                              "Kiểm tra file trên Drive; không đánh dấu COMPLETED"),

    # ---- hồ sơ vào
    "E_ZIP_CORRUPT": ("input", "ZIP không mở được", "Đóng gói lại hồ sơ"),
    "E_ZIP_TRAVERSAL": ("input", "ZIP có đường dẫn/tệp nguy hiểm",
                        "KHÔNG giải nén; kiểm tra nguồn ZIP"),
    "E_ZIP_TOO_BIG": ("input", "ZIP vượt giới hạn", "Kiểm tra kích thước hồ sơ"),
    "E_ZIP_NOT_IN_INPUT": ("input", "ZIP không nằm trong folder INPUT của tập",
                           "Chuyển ZIP vào đúng INPUT hoặc sửa link cột K"),
    "E_MANIFEST_MISSING": ("input", "Thiếu/hỏng 09_HANDOFF_MANIFEST.json",
                           "Đóng gói lại hồ sơ kèm manifest"),
    "E_HANDOFF_INCOMPLETE": ("input", "Hồ sơ thiếu file bắt buộc",
                             "Bổ sung file còn thiếu rồi upload lại ZIP"),
    "E_NO_LINK": ("input", "Ô yêu cầu không có link Drive và không tìm được folder tập",
                  "Thêm link vào ô K hoặc tạo folder tập trong 01_INBOX"),

    # ---- Phase 5.4: preflight điều kiện viết kịch bản (services/script_gate.py)
    "E_GATE_NO_MARKER": ("gate", "Ô K không có marker trạng thái nào",
                         "Ghi trạng thái vào ô K: RESEARCH OK / đã xong hook+ sườn"),
    "E_GATE_NO_OUTLINE": ("gate", "Ô K ghi 'cần kịch bản' nhưng chưa có Hook + Sườn",
                          "Để Fabel hoàn thành Hook + Outline trước; KHÔNG tự gọi Fabel"),
    "E_GATE_ZIP_MISSING": ("gate", "INPUT chưa có ZIP hồ sơ GPT-6",
                           "Chờ Fabel đóng gói hồ sơ; KHÔNG tạo ZIP giả"),
    "E_GATE_ZIP_NOT_FABEL": ("gate", "ZIP trong INPUT không phải hồ sơ Fabel Hook+Outline",
                             "Kiểm ZIP có 01_HOOK_OUTLINE.txt + 09_HANDOFF_MANIFEST.json"),
    "E_GATE_ZIP_WRONG_EPISODE": ("gate", "ZIP hồ sơ khai sai lane/tập so với dòng Sheet",
                                 "Đóng gói lại đúng tập; không đưa nhầm tập vào hàng đợi"),
    "E_GATE_ZIP_REVISION": ("gate", "hook_outline_checksum lệch hoặc thiếu trong manifest",
                            "Đóng gói lại hồ sơ từ đúng bản Fabel; không sửa tay"),
    "E_GATE_DUPLICATE_JOB": ("gate", "Tập đã có job đang dở (StateStore hoặc HandoffStore)",
                             "Kết thúc job cũ trước khi tạo job mới"),
    "E_GATE_OUTPUT_OCCUPIED": ("gate", "OUTPUT sản xuất đã có ZIP kịch bản",
                               "Không ghi đè; chỉ chạy khi có quy trình revision được duyệt"),
    "E_GATE_NOT_APPROVED": ("gate", "Đủ 8 điều kiện nhưng CHƯA có phê duyệt production",
                            "Chủ dự án bật production_upload_enabled (config) hoặc "
                            "phê duyệt đúng job; KHÔNG tự bật"),

    # ---- kết quả ra
    "E_RESULT_MISSING": ("output", "Chưa có ZIP kết quả", "Đặt ZIP vào jobs/_inbox/"),
    "E_RESULT_MISSING_FILES": ("output", "ZIP kết quả thiếu file bắt buộc",
                               "Yêu cầu GPT xuất đủ 4 file"),
    "E_RESULT_ENCODING": ("output", "File kết quả không phải UTF-8", "Yêu cầu xuất lại UTF-8"),
    "E_RESULT_EMPTY_SCRIPT": ("output", "SCRIPT_FINAL_JP.txt rỗng", "Yêu cầu viết lại"),
    "E_RESULT_LENGTH": ("output", "Độ dài ngoài ngân sách hồ sơ",
                        "Yêu cầu GPT viết lại đúng khoảng ký tự"),
    "E_RESULT_HOOK_CHANGED": ("output", "Hook không còn nguyên văn",
                              "Yêu cầu chép lại nguyên văn hook"),
    "E_RESULT_JOB_MISMATCH": ("output", "ZIP kết quả thuộc tập khác", "Kiểm tra lại tập"),
    "E_MOCK_NOT_UPLOADABLE": ("output", "ZIP do MOCK sinh, không được upload",
                              "Dùng adapter thật hoặc đặt upload.allow_mock_upload"),

    # ---- writer / adapter
    "E_ADAPTER_NOT_READY": ("writer", "Adapter chưa được xác nhận hoạt động",
                            "Chạy bridge.py doctor để kiểm tra"),
    "E_ADAPTER_BLOCKED": ("writer", "Adapter bị chặn (CAPTCHA/login/không có CDP)",
                          "Người vận hành xử lý thủ công rồi chạy lại"),
    "E_WRITER_TIMEOUT": ("writer", "Quá hạn chờ writer", "Kiểm tra ChatGPT; không tự gửi lại"),
    "E_SUBMIT_UNCERTAIN": ("writer", "Không rõ GPT đã nhận job hay chưa",
                           "Mở chat kiểm tra TRƯỚC khi gửi lại — tránh gửi trùng"),
    "E_NEEDS_REVIEW": ("writer", "Kết quả cần người xem", "Đọc SCRIPT_ISSUES.md và quyết định"),

    # ---- Phase 5: Codex CLI (chỉ THÊM, không đổi mã cũ)
    "E_CODEX_NOT_INSTALLED": ("writer", "Không tìm thấy Codex CLI",
                              "Cài Codex CLI hoặc sửa writer.codex_cli.executable"),
    "E_CODEX_NOT_LOGGED_IN": ("writer", "Codex CLI chưa đăng nhập ChatGPT",
                              "Chạy: codex login --device-auth rồi mở link, nhập mã"),
    "E_CODEX_MODEL_UNAVAILABLE": ("writer", "Model yêu cầu không có trong Codex CLI",
                                  "Chọn model có trong catalog; KHÔNG tự đổi model"),
    "E_CODEX_RUN_FAILED": ("writer", "codex exec kết thúc lỗi hoặc thiếu file kết quả",
                           "Xem codex_out/codex_stdout.log rồi chạy lại"),

    # ---- Phase 5.3: điều phối bàn giao tự động (chỉ THÊM, không đổi mã cũ)
    "E_HANDOFF_ZIP_MISSING": ("output", "ZIP kết quả của job handoff không còn trên đĩa",
                              "Tải lại từ ChatGPT rồi chạy lại watcher"),
    "E_HANDOFF_ZIP_CHANGED": ("output", "SHA256 ZIP kết quả khác bản ghi HandoffStore",
                              "KHÔNG upload — kiểm tra file có bị ghi đè/đổi không"),
    "E_HANDOFF_WRONG_EPISODE": ("output", "ZIP kết quả khai sai lane/tập so với job",
                                "Mở ZIP kiểm định danh; yêu cầu GPT xuất lại đúng tập"),
    "E_HANDOFF_BAD_ZIP": ("output", "ZIP kết quả hỏng cấu trúc/thiếu file bắt buộc",
                          "Yêu cầu GPT xuất lại đủ 4 file"),
    "E_HANDOFF_NO_TEST_FOLDER": ("upload", "Job SMOKE chưa có folder thử nghiệm được chỉ định",
                                 "Đặt upload.test_output_folder_id — KHÔNG fallback về OUTPUT sản xuất"),
    "E_HANDOFF_TEST_EQUALS_PRODUCTION": ("upload", "Folder thử nghiệm trùng OUTPUT sản xuất",
                                         "Chỉ định folder thử nghiệm khác; không upload SMOKE vào OUTPUT"),
    "E_HANDOFF_NOT_APPROVED": ("upload", "Job production chưa được phê duyệt upload",
                               "Chủ dự án bật cờ phê duyệt cho ĐÚNG job này rồi chạy lại"),
    "E_HANDOFF_OUTPUT_OCCUPIED": ("upload", "OUTPUT sản xuất đã có ZIP kịch bản",
                                  "Không ghi đè; chỉ chạy khi có quy trình revision được phê duyệt"),
    "E_HANDOFF_CONFLICT": ("upload", "Trạng thái Drive không rõ sau upload (crash/giữa chừng)",
                           "Người vận hành kiểm Drive; KHÔNG upload lại mù"),
    "E_HANDOFF_JOB_CONFLICT": ("state", "Có job khác đang dở cho cùng tập",
                               "Kết thúc job cũ trước, tránh hai đường cùng ghi một tập"),
    "E_HANDOFF_NOT_COMPLETED": ("upload", "Upload xong nhưng đọc lại Drive không khớp SHA256",
                                "KHÔNG đánh dấu COMPLETED; kiểm tra file trên Drive"),
    "E_H2DEV_WRITE_DENIED": ("writer", "Từ chối ghi gói bàn giao ra ngoài sandbox",
                             "Chỉ ghi trong sandbox, hoặc bật phê duyệt production có kiểm soát"),
    "E_H2DEV_PACKAGE_INVALID": ("writer", "Gói bàn giao H2DEV thiếu/hỏng file bắt buộc",
                                "Kiểm tra ZIP nguồn và SCRIPT_FINAL_JP.txt"),
}


class BridgeError(Exception):
    def __init__(self, code: str, detail: str = "", retryable: bool = False):
        self.code = code
        self.detail = detail
        self.retryable = retryable
        grp, msg, fix = CATALOG.get(code, CATALOG["E_UNKNOWN"])
        self.group, self.message, self.fix = grp, msg, fix
        super().__init__(f"[{code}] {msg}" + (f" — {detail}" if detail else ""))

    def as_dict(self) -> dict:
        return {"code": self.code, "group": self.group, "message": self.message,
                "detail": self.detail, "fix": self.fix, "retryable": self.retryable}


def describe(code: str) -> dict:
    grp, msg, fix = CATALOG.get(code, CATALOG["E_UNKNOWN"])
    return {"code": code, "group": grp, "message": msg, "fix": fix}
