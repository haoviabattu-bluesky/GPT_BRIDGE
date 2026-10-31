# -*- coding: utf-8 -*-
"""script_gate.py — Phase 5.4: PREFLIGHT quyết định một tập có đủ điều kiện viết kịch bản.

Vị trí trong kiến trúc (KHÔNG thay module nào đã PASS ở Phase 5.3):

    Sheet cột K  ──►  script_gate.decide()  ──►  WAITING_OUTLINE
                                              ├─►  WAITING_HANDOFF_ZIP
                                              ├─►  ALREADY_SCRIPTED
                                              ├─►  REJECTED  (sai lane/tập, OUTPUT đã có, …)
                                              └─►  QUEUED    (đủ mọi điều kiện)
                                                        │
                                          HandoffStore.enqueue()  ──►  Chrome Extension
                                                        │
                                          HandoffCoordinator (Phase 5.3, KHÔNG đổi)
                                                        │
                                          OUTPUT + GPT_DELIVERED

## Ranh giới trách nhiệm (chốt với chủ dự án)

* Module này **chỉ** trả lời "tập này có được phép vào hàng đợi viết kịch bản không".
  Nó KHÔNG gọi Fabel, KHÔNG tạo ZIP, KHÔNG sửa trạng thái H2DEV, KHÔNG gọi
  Codex CLI / Sol / API writer.
* Sau khi upload OUTPUT, Phase 5.3 đánh dấu `COMPLETED` và module này ghi thêm
  `gpt_state = GPT_DELIVERED`. **KHÔNG** đánh dấu kịch bản đã được Sol duyệt —
  việc duyệt và chạy tiếp thuộc H2DEV.

## Thứ tự quyết định (dừng ở điều kiện đầu tiên không đạt)

1. Dòng quyết định trong ô K (dòng ĐẦU TIÊN có marker thắng — dòng mới chèn lên đầu):
   * `kịch bản xong` / `đã có kịch bản` / `đã giao GPT`  → `ALREADY_SCRIPTED`
   * `RESEARCH OK`                                        → `WAITING_OUTLINE`
   * `đã xong hook+ sườn` / `sườn OK`                     → sang bước 2
2. Resolve folder tập → INPUT/OUTPUT bằng API (không ghép chuỗi URL).
3. ZIP hồ sơ Fabel phải có THẬT trong INPUT và đủ hồ sơ:
   `01_HOOK_OUTLINE.txt` có khối HOOK đo được + có phần KHUNG SƯỜN,
   `09_HANDOFF_MANIFEST.json` khai `fabel`/`_model`, và checksum hook khớp.
   Thiếu ZIP → `WAITING_HANDOFF_ZIP`.
4. Mã tập + lane trong ZIP phải trùng job (xem `identity_mismatch`).
5. Revision: đo lại byte thật trong ZIP — `per_file_checksums[01_HOOK_OUTLINE.txt]`
   và `hook_sol.hook_sha256` (so với KHỐI HOOK đã tách). Lệch → hồ sơ bị sửa.
   ⚠️ `hook_outline_checksum` là ID nội bộ, KHÔNG phải sha256 file — đừng dùng để so.
6. Không có job khác đang dở cho cùng tập.
7. OUTPUT sản xuất chưa có ZIP kịch bản (không ghi đè).
8. Tất cả đạt → `QUEUED`.

Mọi bước là READ-ONLY trừ khi gọi `apply=True` (lúc đó mới `HandoffStore.enqueue`).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from chatgpt_web_handoff.store import HandoffStore
from chatgpt_web_handoff.watcher import zip_identity

from .errors import BridgeError
from .prompt_builder import parse_hook

# ---------------------------------------------------------------- kết quả gate
STATUS_QUEUED = "QUEUED"
STATUS_WAITING_OUTLINE = "WAITING_OUTLINE"
STATUS_WAITING_HANDOFF_ZIP = "WAITING_HANDOFF_ZIP"
STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
STATUS_ALREADY_SCRIPTED = "ALREADY_SCRIPTED"
STATUS_REJECTED = "REJECTED"

# Trạng thái "đang chờ" — không phải lỗi, chỉ là chưa tới lượt.
# WAITING_APPROVAL: đã đủ 8 điều kiện kỹ thuật nhưng CHƯA có phê duyệt production,
# nên KHÔNG được vào hàng đợi (yêu cầu vận hành #8).
WAITING_STATES = (STATUS_WAITING_OUTLINE, STATUS_WAITING_HANDOFF_ZIP,
                  STATUS_WAITING_APPROVAL)

# ---------------------------------------------------------------- trạng thái ô K
SHEET_SCRIPT_DONE = "script_done"
SHEET_RESEARCH_OK = "research_ok"
SHEET_SUON_OK = "suon_ok"
SHEET_NEED_SCRIPT = "need_script"
SHEET_IGNORE = "ignore"

# Thứ tự xét TRONG một dòng: mạnh nhất trước.
# "Đã xong hook+ sườn- cần kịch bản" chứa cả "cần kịch bản" nên phải xét sườn trước.
DEFAULT_MARKERS: dict[str, list[str]] = {
    SHEET_SCRIPT_DONE: ["kich ban xong", "da co kich ban", "script done", "da giao gpt"],
    SHEET_SUON_OK: ["da xong hook", "xong hook", "hook+ suon", "hook + suon",
                    "suon ok", "khung suon ok", "da co suon"],
    SHEET_RESEARCH_OK: ["research ok"],
    SHEET_NEED_SCRIPT: ["can kich ban", "can viet kich ban", "chua co kich ban",
                        "can gpt viet kich ban"],
}
MARKER_ORDER = (SHEET_SCRIPT_DONE, SHEET_SUON_OK, SHEET_RESEARCH_OK, SHEET_NEED_SCRIPT)


def norm(s: str) -> str:
    """Chuẩn hoá để so marker: bỏ dấu tiếng Việt, lowercase, gộp khoảng trắng."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", s).strip().lower()


def markers_from_cfg(cfg: dict | None) -> dict[str, list[str]]:
    """Marker lấy từ config nếu có, thiếu thì dùng mặc định."""
    rules = ((cfg or {}).get("sheets") or {}).get("status_rules") or {}
    out = {k: [norm(x) for x in v] for k, v in DEFAULT_MARKERS.items()}
    for key, cfg_key in ((SHEET_RESEARCH_OK, "research_ok"), (SHEET_SUON_OK, "suon_ok")):
        extra = rules.get(cfg_key)
        if extra:
            out[key] = out[key] + [norm(x) for x in extra]
    return out


def classify_line(line: str, markers: dict[str, list[str]]) -> str | None:
    n = norm(line)
    for key in MARKER_ORDER:
        if any(m and m in n for m in markers.get(key, [])):
            return key
    return None


def sheet_decision(cell: str, markers: dict[str, list[str]]) -> tuple[str, str]:
    """-> (trạng thái, dòng quyết định). Dòng ĐẦU TIÊN có marker thắng.

    Dữ liệu thật: người vận hành chèn dòng mới LÊN ĐẦU ô K, nên dòng đầu là hiện tại.
    """
    for line in (str(cell or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        st = classify_line(line, markers)
        if st:
            return st, line
    return SHEET_IGNORE, ""


# ---------------------------------------------------------------- ZIP hồ sơ Fabel
_HOOK_FILE = "01_HOOK_OUTLINE.txt"
_MANIFEST_FILE = "09_HANDOFF_MANIFEST.json"
_OUTLINE_RE = re.compile(r"^=+\s*(KHUNG|PHẦN|OUTLINE)\b", re.I | re.M)
_DANGEROUS = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".sh",
              ".py", ".lnk", ".msi", ".scr", ".com", ".pif", ".hta"}


def _members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    out: dict[str, zipfile.ZipInfo] = {}
    for i in zf.infolist():
        if i.is_dir() or i.filename.startswith("__MACOSX/"):
            continue
        name = i.filename.replace("\\", "/")
        out.setdefault(PurePosixPath(name).name, i)
    return out


def fabel_profile_problems(zip_path: str | Path, *, cfg: dict | None = None) -> dict:
    """Kiểm ZIP có phải HỒ SƠ FABEL THẬT (Hook + Outline) không.

    Trả `{'ok', 'problems', 'hook_chars', 'outline_present', 'fabel', 'revision'}`.
    KHÔNG giải nén ra đĩa, KHÔNG ghi gì.
    """
    p = Path(zip_path)
    res: dict = {"ok": False, "problems": [], "revision_problems": [],
                 "hook_chars": 0, "outline_present": False,
                 "fabel": {}, "revision": {}, "title": ""}
    try:
        zf = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError) as e:
        res["problems"].append(f"ZIP không mở được: {type(e).__name__}: {e}")
        return res

    with zf:
        bad = zf.testzip()
        if bad:
            res["problems"].append(f"CRC lỗi ở {bad}")
        infos = [i for i in zf.infolist() if not i.is_dir()]
        for i in infos:
            name = i.filename.replace("\\", "/")
            pp = PurePosixPath(name)
            if pp.is_absolute() or ".." in pp.parts or re.match(r"^[A-Za-z]:", name):
                res["problems"].append(f"đường dẫn nguy hiểm: {i.filename}")
            if pp.suffix.lower() in _DANGEROUS:
                res["problems"].append(f"tệp nguy hiểm: {i.filename}")
        by = _members(zf)

        hook_info = by.get(_HOOK_FILE)
        if hook_info is None:
            res["problems"].append(f"thiếu {_HOOK_FILE} — không phải hồ sơ Fabel Hook+Outline")
            return res
        raw = zf.read(hook_info)
        try:
            hook_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            res["problems"].append(f"{_HOOK_FILE} không phải UTF-8")
            return res

        hook = parse_hook(hook_text)
        res["hook_chars"] = int(hook.get("measured") or 0)
        res["hook_declared"] = int(hook.get("declared") or 0)
        if not (hook.get("text") or "").strip():
            res["problems"].append("không tìm thấy khối HOOK đo được trong "
                                   f"{_HOOK_FILE} (thiếu header '=== HOOK … ===')")
        elif res["hook_chars"] < 50:
            res["problems"].append(f"khối HOOK chỉ {res['hook_chars']} ký tự — nghi hồ sơ rỗng")

        res["outline_present"] = bool(_OUTLINE_RE.search(hook_text))
        if not res["outline_present"]:
            res["problems"].append("thiếu phần KHUNG SƯỜN trong "
                                   f"{_HOOK_FILE} — Fabel chưa hoàn thành Outline")

        # tiêu đề dự kiến (nếu có) — chỉ để báo cáo, không chặn
        m = re.search(r"===\s*TIÊU ĐỀ DỰ KIẾN\s*===\s*\n(.+)", hook_text)
        if m:
            res["title"] = m.group(1).strip()[:200]

        man_info = by.get(_MANIFEST_FILE)
        if man_info is None:
            res["problems"].append(f"thiếu {_MANIFEST_FILE} — không xác minh được nguồn Fabel")
        else:
            try:
                man = json.loads(zf.read(man_info).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                man = None
                res["problems"].append(f"{_MANIFEST_FILE} hỏng: {e}")
            if isinstance(man, dict):
                res["fabel"] = {"model": (man.get("fabel") or {}).get("model")
                                or (man.get("input_files") or {}).get("_model")
                                or man.get("model"),
                                "job_id": man.get("job_id"),
                                "lane_id": man.get("lane_id"),
                                "episode_id": man.get("episode_id"),
                                "sessions": len((man.get("fabel") or {}).get("sessions") or [])}
                if not res["fabel"]["model"]:
                    res["problems"].append(
                        "manifest không khai model Fabel (fabel.model / input_files._model)")
                if not (man.get("fabel") or {}).get("sessions"):
                    res["problems"].append(
                        "manifest không khai phiên Fabel (fabel.sessions) — không chứng minh "
                        "được Hook + Outline do Fabel sinh ra")

                # ---- revision: ĐO LẠI byte thật trong ZIP, không tin manifest tự khai
                # Dữ liệu thật (N-ep17): hook_outline_checksum là ID nội bộ, KHÔNG phải
                # sha256 file. Cái đo được là per_file_checksums[01_HOOK_OUTLINE.txt].
                # Nếu manifest khai hook_sol.hook_sha256 thì so với KHỐI HOOK đã tách.
                per = man.get("per_file_checksums") or {}
                file_want = per.get(_HOOK_FILE)
                file_got = hashlib.sha256(raw).hexdigest()
                hook_want = (man.get("hook_sol") or {}).get("hook_sha256")
                hook_got = hashlib.sha256(
                    (hook.get("text") or "").encode("utf-8")).hexdigest()

                res["revision"] = {
                    "file_declared": file_want, "file_measured": file_got,
                    "file_matches": bool(file_want) and file_want == file_got,
                    "hook_declared": hook_want, "hook_measured": hook_got,
                    "hook_matches": bool(hook_want) and hook_want == hook_got,
                    "hook_outline_checksum": man.get("hook_outline_checksum"),
                }
                if file_want and file_want != file_got:
                    res["revision_problems"].append(
                        f"{_HOOK_FILE} bị sửa sau khi đóng gói: manifest "
                        f"{str(file_want)[:12]} ≠ file {file_got[:12]}")
                elif not file_want:
                    res["problems"].append(
                        f"manifest thiếu per_file_checksums[{_HOOK_FILE}] — "
                        "không xác minh được hồ sơ không bị sửa")
                if hook_want and hook_want != hook_got:
                    res["revision_problems"].append(
                        "khối HOOK bị sửa sau khi Sol chấm: "
                        f"hook_sol.hook_sha256 {str(hook_want)[:12]} ≠ {hook_got[:12]}")

                res["source_research_version"] = man.get("source_research_version")
                res["profile_id"] = man.get("profile_id")
                if not res.get("title"):
                    res["title"] = str(man.get("title") or "")[:200]

    res["ok"] = not (res["problems"] or res["revision_problems"])
    return res


# ---------------------------------------------------------------- danh tính hồ sơ
_TAP_RE = re.compile(r"tap[ _-]*0*(\d+)", re.I)


def handoff_identity(zip_path: str | Path, prof: dict | None = None) -> dict:
    """Danh tính hồ sơ = khai trong manifest ∪ tên file ∪ header .md.

    Vì sao không dùng mỗi `zip_identity()`: ZIP hồ sơ Fabel đặt tên kiểu
    `LaneN_tap17_GPT_SCRIPT_HANDOFF` ("tap17", không phải "ep17") và KHÔNG có
    SCRIPT_QC_REPORT.md, nên `identity_problems()` kết luận "ZIP không khai số tập"
    và chặn nhầm hồ sơ hợp lệ. Ở đây đọc thêm manifest (nguồn chuẩn nhất).
    """
    base = zip_identity(zip_path)
    lanes = set(base.get("lanes") or [])
    eps = set(base.get("episodes") or [])
    source = {"file": True, "manifest": False}

    # "tap17" trong tên file
    m = _TAP_RE.search(Path(zip_path).name)
    if m:
        eps.add(int(m.group(1)))

    prof = prof or {}
    f = prof.get("fabel") or {}
    if f.get("lane_id"):
        lanes.add(str(f["lane_id"]).strip().upper())
        source["manifest"] = True
    if f.get("episode_id"):
        mm = re.search(r"(\d+)", str(f["episode_id"]))
        if mm:
            eps.add(int(mm.group(1)))
            source["manifest"] = True

    return {"file": Path(zip_path).name, "lanes": sorted(lanes), "episodes": sorted(eps),
            "manifest_job_id": f.get("job_id"), "declared": base.get("declared") or {},
            "source": source}


def identity_mismatch(job: dict, ident: dict) -> list[str]:
    """So danh tính hồ sơ với job. Rỗng = khớp.

    Khác `identity_problems()`: nếu hồ sơ khai lane/tập ở BẤT KỲ nguồn nào (manifest,
    tên file, header) thì phải TRÙNG job; chỉ khi không nguồn nào khai mới coi là
    "không xác minh được" và chặn — thà dừng còn hơn ghi sai tập.
    """
    problems: list[str] = []
    job_lane = str(job.get("lane") or "").strip().upper() or None
    job_ep = _ep_num(job.get("episode_id"))
    lanes, eps = ident.get("lanes") or [], ident.get("episodes") or []

    if job_lane:
        wrong = [x for x in lanes if x != job_lane]
        if wrong:
            problems.append(f"hồ sơ khai lane {', '.join(wrong)} nhưng job là lane {job_lane}")
        elif not lanes:
            problems.append(f"hồ sơ không khai lane — không xác minh được là lane {job_lane}")

    if job_ep is not None:
        wrong_ep = [e for e in eps if e != job_ep]
        if wrong_ep:
            problems.append("hồ sơ khai tập " + ", ".join(f"ep{e}" for e in wrong_ep)
                            + f" nhưng job là ep{job_ep}")
        elif not eps:
            problems.append(f"hồ sơ không khai số tập — không xác minh được là ep{job_ep}")
    return problems


def _ep_num(episode_id) -> int | None:
    m = re.search(r"(\d+)", str(episode_id or ""))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- preflight
class ScriptGate:
    """Cổng quyết định. Chỉ ĐỌC Drive/Sheet/Store; `apply=True` mới ghi hàng đợi."""

    def __init__(self, cfg: dict, *, drive=None, state=None, handoff=None,
                 root: str | Path | None = None, logger=None):
        self.cfg = cfg
        self.root = Path(root or cfg.get("_root") or ".")
        self.log = logger
        self.drive = drive
        self._state = state
        self.handoff = handoff or HandoffStore(self.root)
        self.markers = markers_from_cfg(cfg)
        self._zip_cache: dict[str, dict] = {}

    # ------------------------------------------------------------ lazy
    @property
    def gcfg(self) -> dict:
        return dict(self.cfg.get("gate") or {})

    @property
    def state(self):
        if self._state is None:
            from .state import StateStore
            self._state = StateStore(
                self.root / str((self.cfg.get("app") or {}).get("state_db", "jobs/bridge.db")))
        return self._state

    def _need_drive(self):
        if self.drive is None:
            from .drive_client import DriveClient
            self.drive = DriveClient(self.cfg, self.log)
        return self.drive

    def tmp_dir(self, lane: str, ep_num: int | None) -> Path:
        d = self.root / "jobs" / "_gate" / f"{lane}-ep{ep_num if ep_num is not None else '?'}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------ 1) ô K
    def decide(self, cell: str) -> dict:
        """Quyết định THUẦN trên ô K — không đụng Drive. Dùng được trong test offline."""
        st, line = sheet_decision(cell, self.markers)
        if st == SHEET_SCRIPT_DONE:
            return {"status": STATUS_ALREADY_SCRIPTED, "sheet_state": st, "line": line,
                    "detail": "ô K đã ghi kịch bản xong — không phải job viết kịch bản"}
        if st == SHEET_RESEARCH_OK:
            return {"status": STATUS_WAITING_OUTLINE, "sheet_state": st, "line": line,
                    "detail": "mới RESEARCH OK — chờ Fabel hoàn thành Hook + Outline"}
        if st == SHEET_SUON_OK:
            return {"status": STATUS_WAITING_HANDOFF_ZIP, "sheet_state": st, "line": line,
                    "detail": "đã xong Hook + Sườn — chờ ZIP hồ sơ GPT-6 trong INPUT",
                    "needs_zip_check": True}
        if st == SHEET_NEED_SCRIPT:
            return {"status": STATUS_REJECTED, "sheet_state": st, "line": line,
                    "code": "E_GATE_NO_OUTLINE",
                    "detail": "ô K ghi 'cần kịch bản' nhưng KHÔNG có marker đã xong "
                              "Hook + Sườn — không đủ điều kiện viết kịch bản"}
        return {"status": STATUS_REJECTED, "sheet_state": SHEET_IGNORE, "line": line,
                "code": "E_GATE_NO_MARKER",
                "detail": "ô K không có marker trạng thái nào"}

    # ------------------------------------------------------------ 2–7) Drive
    def _episode_folder(self, req, lane: str, ep_num: int) -> dict:
        fid = getattr(req, "folder_id", None)
        if fid:
            meta = self._need_drive().get(fid, "id,name,mimeType")
            if str(meta.get("mimeType", "")).endswith("folder"):
                return {"id": fid, "name": meta.get("name"), "version": 0}
        return self._need_drive().resolve_episode_folder(lane, ep_num)

    def _input_zips(self, folder_id: str) -> list[dict]:
        kids = [k for k in self._need_drive().list_children(folder_id)
                if str(k.get("name", "")).lower().endswith(".zip")]
        kids.sort(key=lambda f: f.get("modifiedTime") or "", reverse=True)
        return kids

    def inspect_zip(self, file_id: str, name: str, tmp: Path) -> dict:
        """Tải ZIP về (cache theo file_id) rồi kiểm hồ sơ Fabel."""
        if file_id in self._zip_cache:
            return self._zip_cache[file_id]
        local = tmp / re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        dl = self._need_drive().download(file_id, local)
        prof = fabel_profile_problems(local, cfg=self.cfg)
        out = {"file_id": file_id, "name": name, "path": str(local),
               "sha256": dl.get("sha256"), **prof}
        self._zip_cache[file_id] = out
        return out

    def preflight(self, req, *, lane: str, ep_num: int, project: str = "H2DEV",
                  apply: bool = False) -> dict:
        """Chạy đủ 8 bước cho MỘT tập. Trả dict kết quả, không raise."""
        base = self.decide(getattr(req, "cell_raw", "") or "")
        out = {"lane": lane, "episode_id": f"ep{ep_num}", "project": project,
               "row": getattr(req, "row", None), "tab": getattr(req, "sheet_name", None),
               "local_folder": getattr(req, "local_folder", None),
               "sheet_state": base.get("sheet_state"), "line": base.get("line"),
               "status": base["status"], "code": base.get("code", ""),
               "detail": base.get("detail", ""), "checks": {}}
        if not base.get("needs_zip_check"):
            out["checks"]["sheet"] = {"ok": base["status"] in (STATUS_ALREADY_SCRIPTED,
                                                              STATUS_WAITING_OUTLINE),
                                      "state": base.get("sheet_state")}
            return out

        # ---- 2) folder tập + INPUT/OUTPUT
        try:
            ep_folder = self._episode_folder(req, lane, ep_num)
            pair = self._need_drive().resolve_pair(ep_folder["id"])
        except BridgeError as e:
            out.update(status=STATUS_REJECTED, code=e.code,
                       detail=f"không resolve được INPUT/OUTPUT: {e.detail}")
            out["checks"]["folders"] = {"ok": False, "code": e.code}
            return out
        out["checks"]["folders"] = {"ok": True, "episode_folder_id": ep_folder["id"],
                                    "episode_folder_name": ep_folder.get("name"),
                                    "input_folder_id": pair["input_folder_id"],
                                    "output_folder_id": pair["output_folder_id"]}
        out["input_folder_id"] = pair["input_folder_id"]
        out["output_folder_id"] = pair["output_folder_id"]

        # ---- 3) ZIP hồ sơ Fabel có THẬT trong INPUT
        try:
            zips = self._input_zips(pair["input_folder_id"])
        except BridgeError as e:
            out.update(status=STATUS_REJECTED, code=e.code,
                       detail=f"không đọc được INPUT: {e.detail}")
            return out
        if not zips:
            out.update(status=STATUS_WAITING_HANDOFF_ZIP, code="E_GATE_ZIP_MISSING",
                       detail=f"INPUT {pair['input_folder_id']} chưa có ZIP hồ sơ GPT-6")
            out["checks"]["zip"] = {"ok": False, "reason": "INPUT rỗng"}
            return out

        job = {"job_id": f"{lane}-ep{ep_num}", "lane": lane, "episode_id": f"ep{ep_num}"}
        tmp = self.tmp_dir(lane, ep_num)
        chosen = None
        reasons: list[str] = []            # lý do dạng chữ để người đọc
        kinds: set[str] = set()            # loại lý do để quyết định mã lỗi
        for z in zips:
            try:
                info = self.inspect_zip(z["id"], z["name"], tmp)
            except BridgeError as e:
                reasons.append(f"{z['name']}: {e.code} {e.detail}")
                kinds.add("unreadable")
                continue
            ident = handoff_identity(info["path"], info)
            idp = identity_mismatch(job, ident)
            if idp:
                reasons.append(f"{z['name']}: " + "; ".join(idp))
                # khai SAI lane/tập khác với "không khai gì" — hai chuyện khác nhau
                declared = bool(ident.get("lanes")) and bool(ident.get("episodes"))
                kinds.add("wrong_identity" if declared else "unknown_identity")
                continue
            if not info["ok"]:
                all_p = list(info["problems"]) + list(info.get("revision_problems") or [])
                reasons.append(f"{z['name']}: " + "; ".join(all_p))
                # hồ sơ ĐÚNG cấu trúc nhưng bị sửa sau khi đóng gói là chuyện khác
                # với hồ sơ không đủ tư cách — phải báo đúng mã để người vận hành xử lý.
                kinds.add("revision" if (info.get("revision_problems")
                                         and not info["problems"]) else "not_fabel")
                continue
            chosen = {**info, "identity": ident}
            break

        if chosen is None:
            # ZIP có mặt nhưng không cái nào dùng được. Thứ tự ưu tiên mã lỗi:
            # sai lane/tập > hồ sơ bị sửa > không đủ tư cách hồ sơ Fabel.
            if "wrong_identity" in kinds:
                code = "E_GATE_ZIP_WRONG_EPISODE"
                detail = "ZIP trong INPUT khai sai lane/tập so với dòng Sheet"
            elif "revision" in kinds:
                code = "E_GATE_ZIP_REVISION"
                detail = ("hồ sơ bị sửa sau khi Fabel/Sol chốt — checksum không khớp; "
                          "đóng gói lại từ bản gốc, KHÔNG sửa tay")
            else:
                code = "E_GATE_ZIP_NOT_FABEL"
                detail = ("không có ZIP hồ sơ Fabel (Hook + Outline) hợp lệ "
                          "cho tập này trong INPUT")
            out.update(status=STATUS_REJECTED, code=code, detail=detail)
            out["checks"]["zip"] = {"ok": False, "candidates": [z["name"] for z in zips],
                                    "reasons": reasons[:8], "kinds": sorted(kinds)}
            return out

        out["checks"]["zip"] = {"ok": True, "name": chosen["name"], "file_id": chosen["file_id"],
                                "sha256": chosen["sha256"], "hook_chars": chosen["hook_chars"],
                                "outline_present": chosen["outline_present"],
                                "fabel": chosen["fabel"],
                                "revision": chosen["revision"],
                                "title": chosen.get("title", "")}
        out["zip"] = {"file_id": chosen["file_id"], "name": chosen["name"],
                      "sha256": chosen["sha256"]}

        # ---- 4) mã tập + lane (đã kiểm ở bước 3 bằng identity_problems)
        out["checks"]["identity"] = {"ok": True, "lanes": chosen["identity"].get("lanes"),
                                     "episodes": chosen["identity"].get("episodes")}

        # ---- 5) revision
        rev = chosen["revision"]
        if not (rev.get("file_matches") and (rev.get("hook_matches") or not rev.get("hook_declared"))):
            out.update(status=STATUS_REJECTED, code="E_GATE_ZIP_REVISION",
                       detail="revision hồ sơ không khớp checksum — "
                              "hồ sơ đã bị sửa hoặc đóng gói lại sau khi Fabel/Sol chốt")
            out["checks"]["revision"] = {"ok": False, **rev}
            return out
        out["checks"]["revision"] = {"ok": True, **rev}

        # ---- 6) không có job khác đang dở cho cùng tập
        try:
            other = self.state.active_for_episode(project, lane, f"ep{ep_num}")
        except Exception:                                     # noqa: BLE001
            other = None
        if other and other.get("job_id") != job["job_id"]:
            out.update(status=STATUS_REJECTED, code="E_GATE_DUPLICATE_JOB",
                       detail=f"tập ep{ep_num} đang có job dở {other['job_id']} "
                              f"({other.get('status')})")
            return out
        busy = [j for j in self.handoff.all()
                if str(j.get("lane") or "").upper() == lane.upper()
                and str(j.get("episode_id") or "").lower() == f"ep{ep_num}"
                and str(j.get("state") or "").upper() in ("QUEUED", "OPENED", "SENT", "DOWNLOADED")]
        if busy:
            out.update(status=STATUS_REJECTED, code="E_GATE_DUPLICATE_JOB",
                       detail=f"tập ep{ep_num} đã có job handoff {busy[0]['job_id']} "
                              f"({busy[0].get('state')})")
            return out
        out["checks"]["duplicate"] = {"ok": True}

        # ---- 7) OUTPUT chưa có ZIP kịch bản (không ghi đè)
        try:
            out_zips = [k for k in self._need_drive().list_children(pair["output_folder_id"])
                        if str(k.get("name", "")).lower().endswith(".zip")]
        except BridgeError as e:
            out.update(status=STATUS_REJECTED, code=e.code,
                       detail=f"không đọc được OUTPUT: {e.detail}")
            return out
        if out_zips:
            out.update(status=STATUS_REJECTED, code="E_GATE_OUTPUT_OCCUPIED",
                       detail=f"OUTPUT đã có {len(out_zips)} ZIP "
                              f"{[k['name'] for k in out_zips]} — không ghi đè")
            out["checks"]["output"] = {"ok": False, "zips": [k["name"] for k in out_zips]}
            return out
        out["checks"]["output"] = {"ok": True, "zips": []}

        # ---- 8) đủ 8 điều kiện kỹ thuật — nhưng PHẢI có phê duyệt production
        # (yêu cầu vận hành #8). Chưa duyệt thì KHÔNG vào hàng đợi, cũng KHÔNG
        # phải lỗi: đây là trạng thái chờ người. `apply` không thay thế phê duyệt.
        if not self.production_approved():
            out.update(status=STATUS_WAITING_APPROVAL, code="E_GATE_NOT_APPROVED",
                       detail="đủ 8 điều kiện nhưng chưa có phê duyệt production "
                              "(production_upload_enabled=false)")
            out["checks"]["approval"] = {"ok": False,
                                         "production_upload_enabled": False}
            return out
        out["checks"]["approval"] = {"ok": True, "production_upload_enabled": True}

        out.update(status=STATUS_QUEUED, code="", detail="đủ điều kiện viết kịch bản")
        if apply:
            out["queued"] = self.enqueue(req, lane=lane, ep_num=ep_num, project=project,
                                        zip_info=chosen)
        return out

    def production_approved(self) -> bool:
        """Phê duyệt production — ĐỌC cờ của chủ dự án, không tự bật.

        Cùng nguồn với `handoff_coordinator`: cờ trên bản ghi job, hoặc
        `handoff.production_upload_enabled`, hoặc `upload.production_upload_enabled`.
        """
        h = self.cfg.get("handoff") or {}
        u = self.cfg.get("upload") or {}
        return bool(h.get("production_upload_enabled") or u.get("production_upload_enabled"))

    # ------------------------------------------------------------ ghi hàng đợi
    def enqueue(self, req, *, lane: str, ep_num: int, project: str, zip_info: dict) -> dict:
        """Đưa job vào HandoffStore với state=QUEUED. KHÔNG gửi gì vào ChatGPT."""
        job_id = f"{project}-{lane}-ep{ep_num}-GPT6-v01"
        rec = self.handoff.enqueue({
            "job_id": job_id,
            "project": project,
            "lane": lane,
            "episode_id": f"ep{ep_num}",
            "episode_slug": getattr(req, "episode_slug", None),
            "source_tab": getattr(req, "sheet_name", None),
            "source_row": getattr(req, "row", None),
            "input_zip_name": zip_info["name"],
            "input_zip_url": self._need_drive().web_link(zip_info["file_id"], "file"),
            "input_sha256": zip_info["sha256"],
            "input_folder_id": zip_info.get("input_folder_id"),
            "gpt_state": "",
            "script_approved": False,
            "gate": {"decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "hook_chars": zip_info["hook_chars"],
                     "fabel_model": (zip_info.get("fabel") or {}).get("model")},
        })
        self.handoff.add_event(job_id, "gate_queued",
                              f"Phase 5.4 preflight đạt: {zip_info['name']}")
        return rec
