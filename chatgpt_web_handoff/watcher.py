# -*- coding: utf-8 -*-
"""watcher.py — tự phát hiện ZIP kết quả trong thư mục nhận hàng của ChatGPT.

Luồng (Phase 5 mục 7):
    người dùng bấm tải ZIP trong ChatGPT  ->  ZIP rơi vào thư mục Downloads
    -> watcher thấy ZIP mới, khớp ĐÚNG TÊN job đang chờ  ->  xác minh danh tính tập
    -> QC cấu trúc ZIP  ->  chuyển ZIP vào thư mục làm việc của job
    -> đặt state=DOWNLOADED  ->  orchestrator Phase 1–4 (QC + uploader) nhận tiếp.

KHÔNG tải hộ, KHÔNG đọc nội dung ChatGPT, KHÔNG giả lập tải xuống: chỉ theo dõi
thư mục mà trình duyệt của người dùng ghi ra.

QC ở đây là QC CẤU TRÚC (đủ file, đúng gốc ZIP, không rỗng). QC nội dung
(số ký tự, hook, dữ kiện) vẫn do services/zip_qc.py của Phase 1–4 làm.

🔴 LUẬT GÁN ZIP (chốt sau sự cố Phase 5.1 — ZIP L-ep27 bị gán vào job N-ep17):

1. Tên file phải khớp CHÍNH XÁC `output_zip_name` của job (chỉ tha hậu tố trùng
   ` (1)` do Chrome thêm khi tải nhiều lần). Không dùng `startswith`, không dùng
   "khớp mờ".
2. KHÔNG BAO GIỜ gán theo tiêu chí "chỉ có một job đang chờ". Số job chờ không
   phải bằng chứng về danh tính tập.
3. Trước khi chuyển DOWNLOADED phải xác minh danh tính: lane + số tập khai trong
   TÊN ZIP và trong header của SCRIPT_QC_REPORT.md / SCRIPT_STRUCTURE.md /
   SCRIPT_ISSUES.md phải trùng job. Lệch -> KHÔNG nhận.
4. ZIP lạ (tên không khớp job nào) chỉ bị bỏ qua, KHÔNG đổi tên, KHÔNG di chuyển,
   KHÔNG đụng vào job. Thà báo "không khớp" còn hơn ghi sai tập.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import time
import zipfile
from pathlib import Path

from .prompt import REQUIRED_FILES, zip_filename
from .store import HandoffStore

# ZIP của ChatGPT có thể đặt tên kèm hậu tố khi tải nhiều lần: name (1).zip
_DL_SUFFIXES = (".zip", ".crdownload", ".part")

# Chrome/Edge thêm " (1)", "(2)" ngay trước .zip khi file đã tồn tại
_CHROME_DUP = re.compile(r"\s*\(\d+\)\s*$")

# Danh tính tập: "ep17", "ep-17", "ep 17", "EP017"
_EP_RE = re.compile(r"(?<![A-Za-z0-9])ep[ _-]*0*(\d+)(?!\d)", re.IGNORECASE)
# Lane: "LaneN", "lane-N", hoặc dạng ngắn "N-ep17"
_LANE_RES = (
    re.compile(r"(?<![A-Za-z0-9])lane[ _-]*([A-Za-z])(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])([A-Za-z])[ _-]*ep[ _-]*\d+", re.IGNORECASE),
)

# File tự khai danh tính tập ở DÒNG ĐẦU (header), ví dụ "# SCRIPT_QC_REPORT — L-ep27"
_DECLARE_FILES = ("SCRIPT_QC_REPORT.md", "SCRIPT_STRUCTURE.md", "SCRIPT_ISSUES.md")


def _is_ready(p: Path, *, settle_sec: float) -> bool:
    """Chỉ nhận file đã ghi xong: không tăng kích thước trong `settle_sec`."""
    try:
        s1 = p.stat().st_size
        if s1 <= 0:
            return False
        time.sleep(settle_sec)
        s2 = p.stat().st_size
        return s1 == s2
    except OSError:
        return False


def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ danh tính
def parse_identity(text: str) -> tuple[str | None, int | None]:
    """Rút (lane, số tập) từ một chuỗi. Không thấy -> None.

    'SMOKE51-N-ep17-GPT6-v01' -> ('N', 17)
    'LaneL_tap27_GPT6_SCRIPT_DELIVERY' -> ('L', None)
    'M-ep18_GPT6_SCRIPT_DELIVERY' -> ('M', 18)
    """
    s = text or ""
    lane: str | None = None
    ep: int | None = None
    m = _EP_RE.search(s)
    if m:
        ep = int(m.group(1))
    for rx in _LANE_RES:
        m = rx.search(s)
        if m:
            lane = m.group(1).upper()
            break
    return lane, ep


def expected_zip_name(job: dict) -> str:
    return str(job.get("output_zip_name") or zip_filename(job["job_id"]))


def _stem(name: str) -> str:
    """Bỏ .zip và hậu tố trùng ' (1)' của trình duyệt."""
    n = str(name or "").strip()
    if n.lower().endswith(".zip"):
        n = n[:-4]
    return _CHROME_DUP.sub("", n)


def name_matches(job: dict, filename: str) -> bool:
    """Tên file có khớp CHÍNH XÁC ZIP kết quả kỳ vọng của job không."""
    return _stem(filename).casefold() == _stem(expected_zip_name(job)).casefold()


def zip_identity(path: str | Path) -> dict:
    """Danh tính ZIP: lane/tập khai trong TÊN FILE và trong header 3 file .md."""
    p = Path(path)
    lanes: set[str] = set()
    eps: set[int] = set()
    declared: dict[str, str] = {}

    lane, ep = parse_identity(p.name)
    if lane:
        lanes.add(lane)
    if ep is not None:
        eps.add(ep)

    try:
        with zipfile.ZipFile(p) as zf:
            by_name: dict[str, zipfile.ZipInfo] = {}
            for i in zf.infolist():
                if i.is_dir() or i.filename.startswith("__MACOSX/"):
                    continue
                by_name.setdefault(Path(i.filename.replace("\\", "/")).name, i)
            for fname in _DECLARE_FILES:
                info = by_name.get(fname)
                if info is None:
                    continue
                raw = zf.read(info)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("utf-8", errors="replace")
                head = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:200]
                declared[fname] = head
                dl, de = parse_identity(head)
                if dl:
                    lanes.add(dl)
                if de is not None:
                    eps.add(de)
    except (zipfile.BadZipFile, OSError):
        pass

    return {"file": p.name, "declared": declared,
            "lanes": sorted(lanes), "episodes": sorted(eps)}


def job_identity(job: dict) -> tuple[str | None, int | None]:
    """Danh tính job: ưu tiên lane/episode_id, thiếu thì đọc từ job_id."""
    lane = str(job.get("lane") or "").strip().upper() or None
    ep = parse_identity(str(job.get("episode_id") or ""))[1]
    if ep is None:
        ep = parse_identity(str(job.get("job_id") or ""))[1]
    if lane is None:
        lane = parse_identity(str(job.get("job_id") or ""))[0]
    return lane, ep


def identity_problems(job: dict, ident: dict) -> list[str]:
    """So danh tính ZIP với job. Rỗng = khớp (hoặc job không khai để so)."""
    problems: list[str] = []
    job_lane, job_ep = job_identity(job)
    lanes: list[str] = ident.get("lanes") or []
    eps: list[int] = ident.get("episodes") or []

    if job_lane:
        wrong = [x for x in lanes if x != job_lane]
        if wrong:
            problems.append(
                f"ZIP khai lane {', '.join(wrong)} nhưng job là lane {job_lane}")
    if job_ep is not None:
        if not eps:
            problems.append(
                f"ZIP không khai số tập ở đâu cả — không xác minh được là ep{job_ep}")
        else:
            wrong_ep = [e for e in eps if e != job_ep]
            if wrong_ep:
                problems.append(
                    "ZIP khai tập " + ", ".join(f"ep{e}" for e in wrong_ep)
                    + f" nhưng job là ep{job_ep}")
    return problems


# ------------------------------------------------------------------ QC cấu trúc
def inspect_zip(path: str | Path) -> dict:
    """QC cấu trúc ZIP kết quả. -> {'ok','problems','files','final_jp_chars'}."""
    p = Path(path)
    problems: list[str] = []
    names: list[str] = []
    final_chars = 0
    try:
        with zipfile.ZipFile(p) as zf:
            bad = zf.testzip()
            if bad:
                problems.append(f"CRC lỗi ở {bad}")
            infos = [i for i in zf.infolist() if not i.is_dir()]
            names = [i.filename for i in infos]
            # bỏ rác macOS
            real = [n for n in names
                    if not n.startswith("__MACOSX/") and not n.endswith(".DS_Store")]
            for req in REQUIRED_FILES:
                hits = [n for n in real if Path(n).name == req]
                if not hits:
                    problems.append(f"thiếu {req}")
                    continue
                info = next(i for i in infos if i.filename == hits[0])
                if info.file_size <= 0:
                    problems.append(f"{req} rỗng (0 byte)")
                # file phải nằm ở GỐC ZIP (không thư mục con)
                if "/" in hits[0].replace("\\", "/"):
                    problems.append(f"{req} nằm trong thư mục con, phải ở gốc ZIP")
            if "SCRIPT_FINAL_JP.txt" in [Path(n).name for n in real]:
                target = next(n for n in real if Path(n).name == "SCRIPT_FINAL_JP.txt")
                raw = zf.read(target)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    problems.append("SCRIPT_FINAL_JP.txt không phải UTF-8")
                    text = raw.decode("utf-8", errors="replace")
                final_chars = len("".join(text.split()))
                if final_chars < 200:
                    problems.append(
                        f"SCRIPT_FINAL_JP.txt quá ngắn ({final_chars} ký tự) — có thể là bản nháp")
            extra = [n for n in real if Path(n).name not in REQUIRED_FILES]
            if extra:
                problems.append("có file ngoài 4 file bắt buộc: " + ", ".join(extra[:5]))
    except zipfile.BadZipFile:
        problems.append("không phải ZIP hợp lệ")
    except OSError as e:
        problems.append(f"không đọc được file: {e}")
    return {"ok": not problems, "problems": problems, "files": names,
            "final_jp_chars": final_chars}


def _workdir_of(store: HandoffStore, job: dict) -> Path:
    """Thư mục làm việc TUYỆT ĐỐI của job.

    `workdir` có thể thiếu, là '.', hoặc là đường dẫn tương đối (job tạo tay).
    Dùng thẳng sẽ ghi `result_zip_path` tương đối vào store — sau đó adapter /
    orchestrator chạy ở cwd khác sẽ không tìm thấy ZIP. Luôn resolve về tuyệt đối
    và luôn nằm dưới root của store.
    """
    raw = job.get("workdir")
    p = Path(raw) if raw else (store.root / "jobs" / job["job_id"])
    if not p.is_absolute():
        p = store.root / p
    return p.resolve()


class ResultWatcher:
    """Theo dõi MỘT thư mục nhận hàng, gán ZIP về đúng job đang chờ."""

    def __init__(self, store: HandoffStore, receive_dir: str | Path, *,
                 settle_sec: float = 1.5, logger=None, keep_source: bool = False):
        self.store = store
        self.receive_dir = Path(receive_dir)
        self.settle_sec = settle_sec
        self.log = logger
        self._seen: set[str] = set()
        # keep_source=True: COPY sang workdir và GIỮ bản gốc trong thư mục nhận
        # (yêu cầu vận hành: không xóa/mất file người dùng đã tải).
        self.keep_source = keep_source

    def _pending_jobs(self) -> list[dict]:
        """Job còn chờ ZIP: QUEUED/OPENED/SENT — và cả NEEDS_REVIEW.

        NEEDS_REVIEW nằm trong danh sách này để job bị gán sai còn ĐƯỜNG PHỤC HỒI:
        người dùng tải lại đúng file, ZIP đúng danh tính tới -> job được cứu về
        DOWNLOADED. Orchestrator chỉ ăn DOWNLOADED nên NEEDS_REVIEW vẫn an toàn.
        """
        return [j for j in self.store.all()
                if str(j.get("state", "")).upper()
                in ("QUEUED", "OPENED", "SENT", "NEEDS_REVIEW")]

    def match(self, zip_path: Path) -> dict | None:
        """Khớp ZIP với job theo TÊN FILE CHÍNH XÁC; không khớp thì trả None.

        KHÔNG đoán theo thời gian, KHÔNG đoán theo "chỉ có một job chờ".
        Số job đang chờ không nói gì về việc ZIP này thuộc tập nào.
        """
        name = Path(zip_path).name
        for j in self._pending_jobs():
            if name_matches(j, name):
                return j
        return None

    # -------------------------------------------------------------- xác minh
    def verify(self, job: dict, zip_path: Path) -> dict:
        """Xác minh danh tính + cấu trúc.

        -> {'ok','problems','identity_problems','structure_problems','identity','report'}

        Tách riêng hai loại lỗi vì hậu quả khác nhau:
          - identity_problems : ZIP khai SAI TẬP -> cần NGƯỜI đưa đúng file
                                (NEEDS_REVIEW), không phải lỗi kỹ thuật.
          - structure_problems: ZIP đúng tập nhưng hỏng/thiếu file -> FAILED.
        """
        ident = zip_identity(zip_path)
        id_problems = identity_problems(job, ident)
        report = inspect_zip(zip_path) if not id_problems else {
            "ok": False, "problems": [], "files": [], "final_jp_chars": 0}
        st_problems = list(report["problems"])
        problems = id_problems + st_problems
        return {"ok": not problems, "problems": problems,
                "identity_problems": id_problems,
                "structure_problems": st_problems,
                "identity": ident, "report": report}

    def scan_once(self, *, move: bool = True) -> list[dict]:
        """Quét một lượt. -> danh sách {'job_id','zip','report','moved_to'}.

        ZIP không khớp tên job nào -> bỏ qua, KHÔNG đổi tên, KHÔNG di chuyển.
        ZIP khớp tên nhưng SAI danh tính tập -> job NEEDS_REVIEW, file giữ tại chỗ.
        ZIP đúng danh tính nhưng hỏng cấu trúc -> job FAILED, file giữ tại chỗ.
        """
        out: list[dict] = []
        if not self.receive_dir.exists():
            return out
        for p in sorted(self.receive_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() != ".zip":
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            key = f"{p.name}:{st.st_size}:{st.st_mtime_ns}"
            if key in self._seen:
                continue
            if not _is_ready(p, settle_sec=self.settle_sec):
                continue

            job = self.match(p)
            if job is None:
                self._seen.add(key)
                if self.log:
                    ident = zip_identity(p)
                    self.log.info(
                        "ZIP không khớp tên job nào đang chờ: %s (lane=%s, tập=%s) — giữ nguyên",
                        p.name, ident["lanes"] or "?", ident["episodes"] or "?")
                continue

            job_id = job["job_id"]
            ver = self.verify(job, p)
            entry = {"job_id": job_id, "zip": str(p), "report": ver["report"],
                     "identity": ver["identity"], "moved_to": None}

            if not ver["ok"]:
                detail = "; ".join(ver["problems"])
                if ver["identity_problems"]:
                    # Sai danh tính tập: KHÔNG phải lỗi kỹ thuật — cần người đưa
                    # đúng file. Giữ job ở NEEDS_REVIEW để không bị tự động chạy tiếp.
                    self.store.add_event(
                        job_id, "zip_wrong_identity",
                        f"{p.name} (sha256 {sha256_file(p)}) — {detail}")
                    self.store.set_state(
                        job_id, "NEEDS_REVIEW",
                        "ZIP nhận được là tập khác: " + detail)
                else:
                    self.store.add_event(
                        job_id, "zip_rejected",
                        f"{p.name} (sha256 {sha256_file(p)}) — {detail}")
                    self.store.set_state(
                        job_id, "FAILED", "ZIP nhận được không đạt xác minh: " + detail)
                self._seen.add(key)
                out.append(entry)
                continue

            dest_dir = _workdir_of(self.store, job)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / expected_zip_name(job)
            replaced = dest.exists()
            if move:
                if replaced:
                    dest.unlink()
                if self.keep_source:
                    # giữ nguyên bản gốc trong thư mục nhận, chỉ sao sang workdir
                    shutil.copy2(str(p), str(dest))
                else:
                    shutil.move(str(p), str(dest))
                entry["moved_to"] = str(dest)
            else:
                dest = p
                entry["moved_to"] = str(dest)

            digest = sha256_file(dest)
            self.store.update(
                job_id,
                result_zip_path=str(dest),
                result_final_jp_chars=ver["report"]["final_jp_chars"],
                result_files=ver["report"]["files"],
                result_sha256=digest,
                result_identity={
                    "expected_name": expected_zip_name(job),
                    "received_name": p.name,
                    "lanes": ver["identity"]["lanes"],
                    "episodes": ver["identity"]["episodes"],
                    "declared": ver["identity"]["declared"],
                },
            )
            self.store.set_state(
                job_id, "DOWNLOADED",
                f"nhận ZIP {Path(dest).name} (lane {job.get('lane') or '?'} · "
                f"{job.get('episode_id') or '?'} · {ver['report']['final_jp_chars']} ký tự · "
                f"sha256 {digest[:16]}) — chờ QC/upload")
            if replaced:
                self.store.add_event(job_id, "zip_replaced", f"ghi đè {dest.name} cũ")
            self._seen.add(key)
            out.append(entry)
        return out

    def run_forever(self, *, interval: float = 3.0, on_result=None) -> None:
        if self.log:
            self.log.info("watcher: theo dõi %s (mỗi %.1fs)", self.receive_dir, interval)
        while True:
            try:
                for r in self.scan_once():
                    if on_result:
                        on_result(r)
            except Exception as e:                       # noqa: BLE001 - watcher không được chết
                if self.log:
                    self.log.warning("watcher lỗi lượt quét: %s", e)
            time.sleep(interval)


# ------------------------------------------------------------------ phục hồi
def recover_downloaded(store: HandoffStore, *, apply: bool = False) -> list[dict]:
    """Soi lại mọi job DOWNLOADED: ZIP đã nhận có đúng danh tính job không.

    Dùng để sửa hậu quả của lần gán sai (Phase 5.1). Không xoá file nào.
    -> [{'job_id','zip','ok','problems','applied'}]
    """
    out: list[dict] = []
    for job in store.by_state("DOWNLOADED"):
        jid = job["job_id"]
        raw = job.get("result_zip_path")
        p = Path(raw) if raw else None
        problems: list[str] = []
        if not p or not p.exists():
            problems.append("ZIP kết quả không còn trên đĩa")
        else:
            problems = identity_problems(job, zip_identity(p))
            if not problems:
                # Danh tính khai đúng nhưng ruột có thể vẫn không phải ZIP kết quả
                # (ví dụ ZIP bàn giao bị đổi tên) -> soi luôn cấu trúc.
                problems = list(inspect_zip(p)["problems"])
            if p.name != expected_zip_name(job):
                problems.append(
                    f"tên file trên đĩa '{p.name}' != '{expected_zip_name(job)}'")
        rec = {"job_id": jid, "zip": str(p) if p else None,
               "ok": not problems, "problems": problems, "applied": False}
        if problems and apply:
            store.add_event(
                jid, "recovered_needs_review",
                "ZIP đã nhận không đúng danh tính tập: " + "; ".join(problems))
            store.set_state(jid, "NEEDS_REVIEW",
                            "ZIP nhận sai tập — đưa về NEEDS_REVIEW: " + "; ".join(problems))
            store.update(jid, result_zip_path=None, result_final_jp_chars=None,
                         result_files=None, result_sha256=None, result_identity=None)
            rec["applied"] = True
        out.append(rec)
    return out
