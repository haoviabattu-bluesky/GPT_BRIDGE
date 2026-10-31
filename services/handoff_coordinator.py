# -*- coding: utf-8 -*-
"""handoff_coordinator.py — Phase 5.3: mối nối TỰ ĐỘNG HandoffStore → QC → Drive OUTPUT.

Vị trí trong kiến trúc (KHÔNG viết lại module nào đã hoạt động):

    chatgpt_web_handoff.watcher        <- MỘT watcher logic duy nhất (đã có, không đụng)
        tải ZIP kết quả + kiểm cấu trúc/định danh + ghi HandoffStore = DOWNLOADED
            |
    HandoffCoordinator                 <- module NÀY (chỉ ĐỌC HandoffStore, không quét lại)
        output_collector.normalize()        chuẩn hoá về 4 file phẳng, tên cố định
        zip_qc.inspect_result()             hình thức / độ dài / hook / định danh
        DriveUploader.upload_and_verify()   upload + đọc lại + so SHA-256
        StateStore                          bản ghi pipeline, khóa idempotency
        HandoffStore.set_state("COMPLETED")  khi và chỉ khi đã xác nhận đầy đủ
            |
    h2dev_delivery                     <- adapter đầu ra riêng, ghi trong SANDBOX

Mười bước theo yêu cầu Phase 5.3 nằm trong `process_one()` theo đúng thứ tự; mỗi
bước có mã lỗi riêng trong `services/errors.py`.

## Bất biến (invariants) — có test riêng ở `tests/test_phase53_handoff_coordinator.py`

1. **Một watcher logic duy nhất**: module này KHÔNG quét thư mục nhận, KHÔNG đọc
   `jobs/_inbox`, KHÔNG gọi `scan_once()`/`iterdir()`/`run_forever()` của watcher.
   Nguồn sự thật duy nhất là trạng thái `DOWNLOADED` trong `HandoffStore`.
2. **Không chiếm chuột/bàn phím**: không `pyautogui`, không `pynput`, không
   `computer_use`, không mở Chrome, không CDP.
3. **Không upload hai lần**: khóa idempotency `row_key = handoff:<job_id>` +
   kiểm tên file trên Drive TRƯỚC khi upload + journal ghi TRƯỚC khi upload.
4. **Không COMPLETED khi chưa verify**: chỉ khi đọc lại file trên Drive thấy
   đúng SHA-256 thì cả hai store mới chuyển COMPLETED.
5. **SMOKE không bao giờ chạm OUTPUT sản xuất**: job SMOKE chỉ được ghi vào
   folder thử nghiệm; trùng OUTPUT sản xuất thì CHẶN (không fallback).
6. **Production mặc định TẮT**: `production_upload_enabled: false`; chỉ bật cho
   đúng job đã được phê duyệt bằng cờ trên bản ghi job.

## Phục hồi sau crash (giữa upload và cập nhật StateStore)

Journal: `jobs/<job_id>/handoff_journal.json`, ghi theo thứ tự
`prepared → uploading → uploaded → completed`. Khi khởi động lại:

* `stage = uploaded|completed` mà StateStore chưa COMPLETED → **kiểm Drive trước**:
  tìm file theo TÊN trong folder đích.
  - 1 kết quả, SHA-256 khớp → **nhận lại** (adopt), KHÔNG upload lại.
  - 0 kết quả → Drive xác nhận lần trước không đáp được → upload lại (an toàn).
  - >1 kết quả hoặc SHA lệch → `E_HANDOFF_CONFLICT`, dừng cho người kiểm.
* `stage = uploading` (crash giữa upload) → trạng thái KHÔNG chắc chắn → áp dụng
  đúng quy trình trên (kiểm Drive trước, không upload mù).

Trung thực: `inspect_result()` chỉ kiểm HÌNH THỨC / ĐỘ DÀI / HOOK / ĐỊNH DANH.
Chưa đánh giá nội dung tiếng Nhật hay độ chính xác dữ kiện.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from chatgpt_web_handoff.store import HandoffStore
from chatgpt_web_handoff.watcher import identity_problems, inspect_zip, zip_identity

from . import output_collector, zip_qc
from .common import get_logger, heartbeat, stop_requested
from .drive_client import DriveClient
from .drive_uploader import DriveUploader
from .errors import BridgeError
from .prompt_builder import parse_budget, parse_hook
from .state import StateStore, job_dir

# ---------------------------------------------------------------- phân loại job
JOB_CLASS_SMOKE = "SMOKE"
JOB_CLASS_PRODUCTION = "PRODUCTION"
JOB_CLASSES = (JOB_CLASS_SMOKE, JOB_CLASS_PRODUCTION)

DEST_TEST = "TEST"
DEST_PRODUCTION = "PRODUCTION"

# ---------------------------------------------------------------- giai đoạn journal
STAGE_PREPARED = "prepared"
STAGE_UPLOADING = "uploading"
STAGE_UPLOADED = "uploaded"
STAGE_COMPLETED = "completed"
STAGE_BLOCKED = "blocked"

# Thứ tự tiến của journal: chỉ được ĐI TỚI, không được lùi.
# Ghi đè `uploaded` bằng `blocked` sẽ xoá mất manh mối phục hồi sau crash.
STAGE_RANK = {STAGE_PREPARED: 1, STAGE_UPLOADING: 2, STAGE_UPLOADED: 3,
              STAGE_COMPLETED: 4, STAGE_BLOCKED: 0}

# ---------------------------------------------------------------- kết quả trả về
STATUS_COMPLETED = "COMPLETED"
STATUS_BLOCKED = "BLOCKED"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"
STATUS_DRY = "DRY_RUN"

# Phase 5.4 — dấu bàn giao kịch bản. CHỈ có một giá trị: đã giao.
# KHÔNG có "SOL_APPROVED" ở đây: duyệt kịch bản thuộc H2DEV, bridge không được tự đánh dấu.
GPT_STATE_DELIVERED = "GPT_DELIVERED"

# Lỗi thuộc nhóm "nội dung/kết quả": cần GPT xuất lại -> job chuyển NEEDS_REVIEW.
CONTENT_BLOCK_CODES = {
    "E_HANDOFF_ZIP_MISSING", "E_HANDOFF_ZIP_CHANGED", "E_HANDOFF_WRONG_EPISODE",
    "E_HANDOFF_BAD_ZIP", "E_RESULT_MISSING_FILES", "E_RESULT_ENCODING",
    "E_RESULT_EMPTY_SCRIPT", "E_RESULT_LENGTH", "E_RESULT_HOOK_CHANGED",
    "E_RESULT_JOB_MISMATCH", "E_MANIFEST_MISSING", "E_HANDOFF_INCOMPLETE",
    "E_ZIP_CORRUPT", "E_ZIP_TRAVERSAL", "E_ZIP_TOO_BIG", "E_NEEDS_REVIEW",
}

# Lỗi thuộc nhóm "hành chính": thiếu phê duyệt / thiếu folder thử nghiệm / OUTPUT đã có bài.
# Job Ở LẠI DOWNLOADED để tick sau tự đánh giá lại khi người vận hành đổi cờ/config.
ADMIN_BLOCK_CODES = {
    "E_HANDOFF_NO_TEST_FOLDER", "E_HANDOFF_TEST_EQUALS_PRODUCTION",
    "E_HANDOFF_NOT_APPROVED", "E_HANDOFF_OUTPUT_OCCUPIED",
    "E_HANDOFF_JOB_CONFLICT", "E_HANDOFF_CONFLICT",
}

_ID_IN_URL = re.compile(r"/(?:d|folders)/([A-Za-z0-9_-]{5,})")


def _ep_num(episode_id) -> int | None:
    m = re.search(r"(\d+)", str(episode_id or ""))
    return int(m.group(1)) if m else None


def folder_id_from_url(url: str | None) -> str | None:
    """Lấy file/folder id từ link Drive bất kỳ (/file/d/<id>/view | /drive/folders/<id>)."""
    if not url:
        return None
    m = _ID_IN_URL.search(str(url))
    return m.group(1) if m else None


def classify_job(job: dict) -> str:
    """SMOKE/TEST hay PRODUCTION. Khai báo tường minh thắng; đoán theo tên chỉ là fallback.

    Mặc định khi không khai gì = PRODUCTION (an toàn: production tắt thì bị chặn,
    không thể vô tình ghi vào OUTPUT sản xuất).
    """
    raw = str(job.get("job_class") or job.get("class") or "").strip().upper()
    if raw in ("SMOKE", "TEST"):
        return JOB_CLASS_SMOKE
    if raw == "PRODUCTION":
        return JOB_CLASS_PRODUCTION
    jid = str(job.get("job_id") or "").upper()
    if jid.startswith(("SMOKE", "TEST", "DRYRUN")) or job.get("smoke") is True:
        return JOB_CLASS_SMOKE
    return JOB_CLASS_PRODUCTION


# =================================================================== coordinator
class HandoffCoordinator:
    """Điều phối bàn giao: HandoffStore(DOWNLOADED) → QC → Drive → COMPLETED.

    Không tự quét thư mục, không điều khiển UI. Mọi thao tác ghi Drive đi qua
    `DriveUploader` đã có (không sửa) và đều có bước đọc lại xác minh.
    """

    def __init__(self, cfg: dict, *, root: str | Path | None = None,
                 handoff_store: HandoffStore | None = None, state: StateStore | None = None,
                 drive=None, uploader: DriveUploader | None = None, logger=None):
        self.cfg = cfg
        base = root or cfg.get("_root") or "."
        self.root = Path(base)
        self.log = logger or get_logger(cfg, "handoff")
        self.handoff = handoff_store or HandoffStore(self.root)
        self.drive = drive
        self._state = state
        self._uploader = uploader
        self.journal_name = str(self.hcfg.get("journal_name") or "handoff_journal.json")
        self._ctx_cache: dict[str, dict | None] = {}

    # ------------------------------------------------------------ lazy collaborator
    @property
    def hcfg(self) -> dict:
        """Đọc TRỰC TIẾP từ cfg mỗi lần dùng — người vận hành sửa config.yaml không
        cần khởi động lại tiến trình để cờ phê duyệt có hiệu lực."""
        return dict(self.cfg.get("handoff_coordinator") or {})

    @property
    def ucfg(self) -> dict:
        return dict(self.cfg.get("upload") or {})

    @property
    def state(self) -> StateStore:
        if self._state is None:
            self._state = StateStore(
                self.root / str((self.cfg.get("app") or {}).get("state_db", "jobs/bridge.db")))
        return self._state

    @property
    def uploader(self) -> DriveUploader:
        if self._uploader is None:
            self._uploader = DriveUploader(self.cfg, self.drive, self.log)
        return self._uploader

    def _need_drive(self):
        if self.drive is None:
            self.drive = DriveClient(self.cfg, self.log)
        return self.drive

    # ================================================================== 1) hàng chờ
    def jobs_to_process(self) -> list[dict]:
        """Job đang ở DOWNLOADED trong HandoffStore — nguồn sự thật duy nhất."""
        return list(self.handoff.by_state("DOWNLOADED"))

    def journal_path(self, job_id: str) -> Path:
        return job_dir(self.root, job_id) / self.journal_name

    def read_journal(self, job_id: str) -> dict:
        p = self.journal_path(job_id)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # journal hỏng -> coi như không có, vẫn an toàn (kiểm Drive)
            self.log.warning("journal hỏng: %s", p)
            return {}

    def write_journal(self, job_id: str, **fields) -> dict:
        """Ghi journal theo kiểu TIẾN-MỘT-CHIỀU cho `stage`.

        Một lỗi xảy ra SAU khi upload (ví dụ verify lỗi mạng) không được phép hạ
        `uploaded` xuống `blocked` — nếu hạ, lần chạy sau sẽ mất manh mối "có thể đã
        upload" và có nguy cơ upload trùng. Các trường khác vẫn ghi đè bình thường.
        """
        cur = self.read_journal(job_id)
        prev = cur.get("stage")
        new = fields.get("stage")
        if prev and new and new != prev:
            if STAGE_RANK.get(new, 0) < STAGE_RANK.get(prev, 0):
                fields = {k: v for k, v in fields.items() if k != "stage"}
                cur.setdefault("stage_history", []).append(
                    {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "refused_stage": new, "kept_stage": prev})
        cur.update(fields)
        cur["job_id"] = job_id
        cur["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        d = self.journal_path(job_id)
        tmp = d.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(d)
        return cur

    # ================================================================== 3) đích đến
    def _production_output_id(self, job: dict) -> tuple[str | None, str]:
        """OUTPUT sản xuất của tập, theo HAI nguồn độc lập. Không dùng để quyết định upload."""
        rec_id = folder_id_from_url(job.get("output_folder_url"))
        live_id, err = None, ""
        lane, ep = job.get("lane"), _ep_num(job.get("episode_id"))
        if lane and ep:
            try:
                fold = self._need_drive().resolve_episode_folder(lane, ep)
                live_id = self._need_drive().resolve_pair(fold["id"])["output_folder_id"]
            except BridgeError as e:
                err = f"{e.code}: {e.detail}"
        if live_id and rec_id and live_id != rec_id:
            err = (err + " | " if err else "") + f"bản ghi trỏ {rec_id} nhưng Drive là {live_id}"
        return (live_id or rec_id), err

    def destination(self, job: dict, job_class: str) -> dict:
        """Folder đích + loại. SMOKE → folder thử nghiệm; PRODUCTION → OUTPUT sản xuất."""
        prod_id, prod_note = self._production_output_id(job)
        if job_class == JOB_CLASS_SMOKE:
            test_id = (job.get("test_output_folder_id")
                       or self.ucfg.get("test_output_folder_id")
                       or self.hcfg.get("test_output_folder_id"))
            if not test_id:
                raise BridgeError(
                    "E_HANDOFF_NO_TEST_FOLDER",
                    f"{job['job_id']} là SMOKE và chưa có folder thử nghiệm — "
                    f"KHÔNG fallback về OUTPUT sản xuất")
            if prod_id and str(test_id) == str(prod_id):
                raise BridgeError("E_HANDOFF_TEST_EQUALS_PRODUCTION",
                                  f"folder thử nghiệm {test_id} trùng OUTPUT sản xuất {prod_id}")
            note = f"SMOKE → folder thử nghiệm {test_id}"
            if prod_note:
                note += f" (cảnh báo resolve: {prod_note})"
            return {"folder_id": str(test_id), "kind": DEST_TEST,
                    "production_output_id": prod_id, "note": note}

        approved = bool(job.get("production_upload_enabled")
                        or self.hcfg.get("production_upload_enabled")
                        or self.ucfg.get("production_upload_enabled"))
        if not approved:
            raise BridgeError(
                "E_HANDOFF_NOT_APPROVED",
                f"{job['job_id']} là PRODUCTION và production_upload_enabled=false "
                f"— cần chủ dự án phê duyệt đúng job này")
        if not prod_id:
            raise BridgeError("E_DRIVE_OUTPUT_MISSING", f"{job['job_id']}: không xác định OUTPUT")
        note = f"PRODUCTION → OUTPUT {prod_id}"
        if prod_note:
            note += f" (cảnh báo resolve: {prod_note})"
        return {"folder_id": str(prod_id), "kind": DEST_PRODUCTION,
                "production_output_id": prod_id, "note": note}

    def preflight_destination(self, job: dict, job_class: str, dest: dict,
                              out_name: str) -> list[dict]:
        """Chặn ghi đè: OUTPUT sản xuất đã có ZIP thì dừng (trừ revision được duyệt)."""
        kids = [k for k in self._need_drive().list_children(dest["folder_id"])
                if not str(k.get("mimeType", "")).endswith("folder")]
        zips = [k for k in kids if str(k.get("name", "")).lower().endswith(".zip")]
        if job_class == JOB_CLASS_PRODUCTION and zips and not job.get("revision_approved"):
            raise BridgeError(
                "E_HANDOFF_OUTPUT_OCCUPIED",
                f"{dest['folder_id']} đã có {len(zips)} ZIP: "
                f"{[k['name'] for k in zips]} — không ghi đè")
        return kids

    def output_name(self, job: dict, dest: dict) -> str:
        """Tên ZIP trên Drive theo quy ước dự án; fallback tên ZIP đã chuẩn hoá."""
        proj = (self.cfg.get("projects") or {}).get(str(job.get("project") or "")) or {}
        pat = proj.get("output_naming")
        if pat:
            try:
                return str(pat).format(lane=job.get("lane"),
                                       ep_num=_ep_num(job.get("episode_id")),
                                       writer=proj.get("writer") or "GPT6",
                                       project=job.get("project") or "H2DEV",
                                       episode=job.get("episode_id") or "",
                                       job_id=job.get("job_id") or "")
            except (KeyError, IndexError):
                self.log.warning("output_naming không format được: %s", pat)
        return f"{job['job_id']}_GPT_SCRIPT_DELIVERY.zip"

    # ================================================================== 4) StateStore
    def row_key_of(self, job: dict) -> str:
        """Khóa idempotency ổn định — cùng job luôn ra cùng khóa, restart không nhân bản."""
        return f"handoff:{job['job_id']}"

    def _find_or_create_state_job(self, job: dict, job_class: str, dest: dict) -> tuple[dict, bool]:
        jid = job["job_id"]
        rk = self.row_key_of(job)
        cur = self.state.by_row_key(rk) or self.state.get(jid)
        proj = str(job.get("project") or "H2DEV")
        lane, ep = job.get("lane"), job.get("episode_id")
        other = self.state.active_for_episode(proj, lane, ep) if (lane and ep) else None
        if other and other["job_id"] != jid:
            raise BridgeError("E_HANDOFF_JOB_CONFLICT",
                              f"tập {ep} đang có job dở {other['job_id']} ({other['status']})")
        if cur:
            return cur, False
        rec = self.state.create({
            "job_id": jid, "project": proj, "lane": lane, "episode_id": ep,
            "episode_slug": job.get("episode_slug"),
            "source_sheet_name": "HANDOFF_BRIDGE",
            "row_key": rk,
            "input_zip_file_id": folder_id_from_url(job.get("input_zip_url")),
            "input_zip_name": job.get("input_zip_name"),
            "input_zip_url": job.get("input_zip_url"),
            "input_sha256": job.get("input_sha256") or job.get("handoff_sha256"),
            "output_folder_id": dest["folder_id"],
            "output_folder_url": job.get("output_folder_url"),
            "conversation_url": job.get("conversation_url"),
            "status": "VERIFYING",
            "notes": json.dumps({"job_class": job_class, "dest_kind": dest["kind"],
                                 "source": "handoff_coordinator"},
                                ensure_ascii=False),
        })
        self.log.info("tạo bản ghi StateStore %s (row_key=%s, %s)", jid, rk, dest["kind"])
        return rec, True

    # ================================================================== ngữ cảnh QC
    def qc_context(self, job: dict) -> dict | None:
        """Tải ZIP hồ sơ vào (một lần) → ngân sách + hook khoá để QC đối chiếu.

        Không bắt buộc phải có: thiếu thì QC bỏ qua phần ngân sách/hook và ghi rõ
        trong báo cáo. KHÔNG coi thiếu ngữ cảnh là PASS nội dung.
        """
        key = str(job.get("input_zip_url") or job.get("input_zip_name") or "")
        if key in self._ctx_cache:
            return self._ctx_cache[key]
        ctx = None
        fid = folder_id_from_url(job.get("input_zip_url"))
        if fid:
            try:
                dest = job_dir(self.root, job["job_id"]) / "input"
                dest.mkdir(parents=True, exist_ok=True)
                local = dest / str(job.get("input_zip_name") or f"{job['job_id']}_HANDOFF.zip")
                self._need_drive().download(fid, local)
                man = zip_qc.inspect_handoff(local, self.cfg)
                texts = man.get("texts") or {}
                budget = parse_budget(texts.get("07_WRITING_TASK.md", ""),
                                      texts.get("08_QC_CHECKLIST.md", ""))
                hook = parse_hook(texts.get("01_HOOK_OUTLINE.txt", ""))
                ctx = {"budget": budget, "hook_text": hook.get("text"),
                       "source": "drive", "handoff_job_id": (man.get("manifest") or {}).get("job_id")}
            except Exception as e:  # noqa: BLE001 — thiếu ngữ cảnh QC không được làm chết job
                self.log.warning("%s: không lấy được ngữ cảnh QC (%s)", job["job_id"], e)
        self._ctx_cache[key] = ctx
        return ctx

    # ================================================================== 8/9/10 — đích
    def _adopt_or_upload(self, job: dict, dest: dict, out_name: str, local_zip: Path,
                         sha_local: str, tmp_dir: Path) -> tuple[dict, int, str]:
        """Upload có chống trùng: kiểm TÊN trên Drive TRƯỚC, đọc lại SAU. -> (upload, n_lần, note)"""
        drive = self._need_drive()
        hits = drive.find_in_folder(dest["folder_id"], out_name)
        if len(hits) > 1:
            raise BridgeError("E_HANDOFF_CONFLICT",
                              f"{len(hits)} file cùng tên '{out_name}' trong {dest['folder_id']}")
        if hits:
            got = drive.download(hits[0]["id"], tmp_dir / f"adopt_{hits[0]['id']}.zip")
            if got["sha256"] != sha_local:
                raise BridgeError(
                    "E_HANDOFF_CONFLICT",
                    f"file '{out_name}' đã có trên Drive nhưng SHA-256 khác "
                    f"({got['sha256'][:12]} ≠ {sha_local[:12]}) — không ghi đè, cần người kiểm")
            up = {"file_id": hits[0]["id"], "name": out_name, "size": int(hits[0].get("size") or 0),
                  "sha256_local": sha_local, "sha256_remote": got["sha256"],
                  "url": drive.web_link(hits[0]["id"]), "verified": True}
            return up, 0, f"nhận lại file đã có (không upload lần hai): {hits[0]['id']}"

        up_job = dict(job)
        up_job["output_folder_id"] = dest["folder_id"]
        up = self.uploader.upload_and_verify(local_zip, up_job, expect_sha=sha_local, name=out_name)
        return up, 1, f"upload mới: {up['file_id']}"

    # ================================================================== quy trình chính
    def process_one(self, job: dict, *, apply: bool = True) -> dict:
        """Xử lý MỘT job DOWNLOADED. apply=False = chỉ đo, không ghi gì (kể cả StateStore)."""
        jid = job["job_id"]
        job_class = classify_job(job)
        res: dict = {"job_id": jid, "job_class": job_class, "status": None, "stage": None,
                     "code": None, "detail": "", "sha256_in": None, "sha256_out": None,
                     "uploads": 0, "file_id": None, "dest_kind": None, "dest_folder_id": None,
                     "output_name": None, "applied": bool(apply), "notes": []}
        log = self.log
        try:
            # -- 2/3. metadata + ZIP: đường dẫn tuyệt đối, tồn tại, SHA-256 khớp bản ghi
            zpath = Path(str(job.get("result_zip_path") or ""))
            if not str(job.get("result_zip_path") or "") or not zpath.is_absolute():
                raise BridgeError("E_HANDOFF_ZIP_MISSING",
                                  f"result_zip_path không phải đường dẫn tuyệt đối: {zpath}")
            if not zpath.exists():
                raise BridgeError("E_HANDOFF_ZIP_MISSING", str(zpath))
            sha_in = zip_qc.sha256_file(zpath)
            res["sha256_in"] = sha_in
            rec_sha = job.get("result_sha256")
            if rec_sha and sha_in != rec_sha:
                raise BridgeError("E_HANDOFF_ZIP_CHANGED",
                                  f"đĩa {sha_in[:12]} ≠ bản ghi {str(rec_sha)[:12]}")

            # -- 3b. cấu trúc ZIP (đủ 4 file, ở gốc, UTF-8, không rác) rồi mới tới định danh tập
            iz = inspect_zip(zpath)
            res["files_in"] = iz.get("files")
            if not iz.get("ok"):
                raise BridgeError("E_HANDOFF_BAD_ZIP", "; ".join(iz.get("problems") or []))
            ident = zip_identity(zpath)
            probs = identity_problems(job, ident)
            res["identity"] = ident
            if probs:
                raise BridgeError("E_HANDOFF_WRONG_EPISODE", "; ".join(probs))

            # -- 8 (chuẩn bị). folder đích + tên file xuất
            dest = self.destination(job, job_class)
            res["notes"].append(dest["note"])
            out_name = self.output_name(job, dest)
            res.update(dest_kind=dest["kind"], dest_folder_id=dest["folder_id"],
                       output_name=out_name)

            if not apply:
                res["status"] = STATUS_DRY
                res["stage"] = STAGE_PREPARED
                res["detail"] = "chỉ đo (--dry-run): không tạo bản ghi, không upload"
                return res

            # -- 4. bản ghi StateStore theo khóa idempotency
            rec, created = self._find_or_create_state_job(job, job_class, dest)
            res["state_created"] = created
            if rec.get("status") == STATUS_COMPLETED:
                res.update(status=STATUS_SKIPPED, stage=STAGE_COMPLETED,
                           file_id=rec.get("output_file_id"),
                           output_name=rec.get("output_name"),
                           sha256_out=rec.get("output_sha256"),
                           detail="StateStore đã COMPLETED — không xử lý lại")
                return res
            self.state.transition(jid, "VERIFYING",
                                  f"coordinator nhận job {jid} ({job_class}, {dest['kind']})")
            self.state.update(jid, output_folder_id=dest["folder_id"])

            # -- 5. chuẩn hoá ZIP (tất định: cùng nội dung → cùng SHA-256)
            jr = self.read_journal(jid)
            norm = output_collector.normalize(zpath, job_dir(self.root, jid) / "normalized", jid)
            norm_zip, sha_out = Path(norm["zip_path"]), norm["sha256"]
            res["sha256_out"] = sha_out
            res["normalized_name"] = norm_zip.name
            mode = ("recover" if jr.get("stage") in (STAGE_UPLOADING, STAGE_UPLOADED,
                                                     STAGE_COMPLETED) else "fresh")
            res["mode"] = mode
            if mode == "recover":
                res["notes"].append(f"journal stage={jr.get('stage')} → kiểm Drive trước, không upload mù")

            # -- 6/7. QC hình thức + độ dài + hook + định danh
            ctx = self.qc_context(job)
            if ctx:
                res["notes"].append(f"ngữ cảnh QC từ hồ sơ (job {ctx.get('handoff_job_id')})")
            report = zip_qc.inspect_result(norm_zip, self.cfg, job, ctx)
            res["qc_verdict"] = report.get("verdict")
            res["qc_checks"] = report.get("checks")
            res["chars"] = report.get("chars")
            self.state.transition(jid, "UPLOADING",
                                  f"QC {report.get('verdict')} · {report.get('chars')} ký tự")

            # -- 8b. chặn ghi đè OUTPUT sản xuất
            self.preflight_destination(job, job_class, dest, out_name)

            # -- journal ghi TRƯỚC khi upload (mốc phục hồi crash)
            self.write_journal(jid, stage=STAGE_UPLOADING, job_class=job_class,
                               dest_kind=dest["kind"], dest_folder_id=dest["folder_id"],
                               out_name=out_name, sha256_in=sha_in, sha256_out=sha_out,
                               normalized=norm_zip.name)

            # -- 8c/9. upload (hoặc nhận lại) + đọc lại + so SHA-256
            tmp_dir = job_dir(self.root, jid) / "verify"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            up, n_uploads, up_note = self._adopt_or_upload(job, dest, out_name, norm_zip,
                                                           sha_out, tmp_dir)
            res["uploads"] = n_uploads
            res["notes"].append(up_note)
            self.write_journal(jid, stage=STAGE_UPLOADED, file_id=up["file_id"],
                               file_size=up.get("size"), remote_sha256=up.get("sha256_remote"))
            self.state.update(jid, output_file_id=up["file_id"], output_name=out_name,
                              output_sha256=up.get("sha256_remote") or sha_out,
                              output_folder_id=dest["folder_id"],
                              output_folder_url=self._need_drive().web_link(
                                  dest["folder_id"], kind="folder"))

            # đọc lại ĐỘC LẬP lần nữa trên Drive (bước 9) — không tin kết quả cũ
            back = self._need_drive().download(up["file_id"], tmp_dir / "readback.zip")
            if back["sha256"] != sha_out:
                raise BridgeError("E_HANDOFF_NOT_COMPLETED",
                                  f"đọc lại Drive {back['sha256'][:12]} ≠ local {sha_out[:12]}")
            res["verified_sha256"] = back["sha256"]

            # -- 10. COMPLETED cả hai store (chỉ tới đây nếu đã xác nhận đầy đủ)
            note = (f"coordinator: {dest['kind']} {dest['folder_id']} · "
                    f"{n_uploads} upload · sha {sha_out[:12]} · QC {report.get('verdict')}")
            self.write_journal(jid, stage=STAGE_COMPLETED, verified_sha256=back["sha256"])
            self.state.transition(jid, STATUS_COMPLETED, note)
            self.handoff.set_state(jid, STATUS_COMPLETED, note)
            # Phase 5.4: ranh giới trách nhiệm — bridge CHỈ đánh dấu đã giao kịch bản.
            # KHÔNG đánh dấu Sol đã duyệt; việc duyệt + chạy tiếp thuộc H2DEV.
            self.handoff.update(jid, gpt_state=GPT_STATE_DELIVERED,
                                script_approved=False)
            self.handoff.update(jid, coordinator={
                "job_class": job_class, "dest_kind": dest["kind"],
                "dest_folder_id": dest["folder_id"], "output_name": out_name,
                "output_file_id": up["file_id"], "output_sha256": sha_out,
                "sha256_in": sha_in, "uploads": n_uploads, "qc": report.get("verdict"),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            res.update(status=STATUS_COMPLETED, stage=STAGE_COMPLETED,
                       file_id=up["file_id"], url=up.get("url"), detail=note)
            log.info("HOÀN TẤT %s → %s %s (%d upload)", jid, dest["kind"], up["file_id"], n_uploads)
            return res

        except BridgeError as e:
            status = (STATUS_NEEDS_REVIEW if e.code in CONTENT_BLOCK_CODES
                      else STATUS_BLOCKED if e.code in ADMIN_BLOCK_CODES
                      else STATUS_FAILED)
            res.update(status=status, code=e.code, detail=e.detail or e.message)
            self._record_block(job, res, apply=apply)
            log.warning("%s: %s [%s] %s", jid, status, e.code, e.detail)
            return res
        except Exception as e:  # noqa: BLE001 — không được để vòng lặp chết vì 1 job
            res.update(status=STATUS_FAILED, code="E_UNKNOWN", detail=f"{type(e).__name__}: {e}")
            self._record_block(job, res, apply=apply)
            log.exception("%s: lỗi không lường trước", jid)
            return res

    def _record_block(self, job: dict, res: dict, *, apply: bool) -> None:
        """Ghi dấu chặn. Nhóm hành chính giữ nguyên DOWNLOADED để tick sau tự đánh giá lại."""
        if not apply:
            return
        jid = job["job_id"]
        try:
            self.write_journal(jid, stage=STAGE_BLOCKED, status=res["status"],
                               code=res["code"], detail=res["detail"])
        except Exception:  # noqa: BLE001
            self.log.warning("%s: không ghi được journal", jid)
        if res["status"] in (STATUS_NEEDS_REVIEW,) or res["status"] == STATUS_FAILED:
            try:
                self.handoff.set_state(jid, res["status"],
                                       f"{res['code']}: {res['detail']}"[:400])
            except Exception:  # noqa: BLE001
                self.log.warning("%s: không đổi được trạng thái handoff", jid)
        try:
            rec = self.state.get(jid) or self.state.by_row_key(self.row_key_of(job))
            if rec is None:
                # Job bị chặn TRƯỚC bước tạo bản ghi (vd thiếu phê duyệt): vẫn phải có
                # dấu trong StateStore để người vận hành truy được, và để lần chạy sau
                # dùng lại đúng bản ghi đó thay vì tạo bản mới.
                rec = self.state.create({
                    "job_id": jid, "project": str(job.get("project") or "H2DEV"),
                    "lane": job.get("lane"), "episode_id": job.get("episode_id"),
                    "episode_slug": job.get("episode_slug"),
                    "source_sheet_name": "HANDOFF_BRIDGE", "row_key": self.row_key_of(job),
                    "input_zip_file_id": folder_id_from_url(job.get("input_zip_url")),
                    "input_zip_name": job.get("input_zip_name"),
                    "input_zip_url": job.get("input_zip_url"),
                    "input_sha256": job.get("result_sha256"),
                    "output_folder_url": job.get("output_folder_url"),
                    "conversation_url": job.get("conversation_url"),
                    "status": "DETECTED",
                    "notes": json.dumps({"source": "handoff_coordinator", "blocked_at": "pre-state"},
                                        ensure_ascii=False),
                })
            if rec and rec["status"] != STATUS_COMPLETED:
                self.state.update(jid, error_code=res["code"],
                                  error_detail=str(res["detail"])[:400])
                self.state.transition(jid, res["status"], str(res["detail"])[:400])
        except Exception:  # noqa: BLE001
            self.log.warning("%s: không ghi được StateStore", jid)

    # ================================================================== vòng
    def tick(self, *, apply: bool = True, limit: int | None = None) -> list[dict]:
        cap = int(limit or self.hcfg.get("max_jobs_per_tick") or 3)
        out = []
        for job in self.jobs_to_process()[:cap]:
            out.append(self.process_one(job, apply=apply))
        return out

    def run_forever(self, interval: float | None = None, *, on_result=None,
                    max_ticks: int | None = None, apply: bool = True) -> list[dict]:
        """Vòng lặp duy nhất. Dừng bằng file STOP (giống orchestrator). Không đụng UI."""
        every = float(interval or self.hcfg.get("poll_sec") or 10)
        all_res: list[dict] = []
        n = 0
        while True:
            if stop_requested(self.cfg):
                self.log.info("có file STOP — dừng coordinator")
                break
            try:
                res = self.tick(apply=apply)
                all_res.extend(res)
                heartbeat(self.cfg, {"role": "handoff_coordinator", "jobs": len(res)})
                if on_result:
                    on_result(res)
            except Exception:  # noqa: BLE001
                self.log.exception("tick lỗi")
            n += 1
            if max_ticks is not None and n >= max_ticks:
                break
            time.sleep(every)
        return all_res

    # ================================================================== tiện ích
    def recover(self, *, apply: bool = True) -> list[dict]:
        """Chỉ trả về các job mà journal cho thấy trạng thái KHÔNG chắc chắn.

        Dùng để vận hành hỏi 'có gì cần kiểm sau crash không' mà không phải chạy cả tick.
        """
        out = []
        for jid in {p.parent.name for p in
                    self.root.glob(f"jobs/*/{self.journal_name}")}:
            jr = self.read_journal(jid)
            if jr.get("stage") not in (STAGE_UPLOADING, STAGE_UPLOADED):
                continue
            rec = self.state.get(jid)
            job = self.handoff.get(jid)
            item = {"job_id": jid, "journal_stage": jr.get("stage"),
                    "state_status": (rec or {}).get("status"),
                    "handoff_state": (job or {}).get("state"),
                    "drive_file_id": jr.get("file_id"), "out_name": jr.get("out_name"),
                    "needs_check": True}
            if rec and rec.get("status") == STATUS_COMPLETED:
                item["needs_check"] = False
            elif apply and job:
                item["resolved"] = self.process_one(job, apply=True)
            out.append(item)
        return out

    def doctor(self) -> dict:
        """Kiểm tra nhanh mối nối: có job nào chờ, folder thử nghiệm đã đặt chưa, cờ production."""
        pending = self.jobs_to_process()
        test_id = (self.ucfg.get("test_output_folder_id")
                   or self.hcfg.get("test_output_folder_id"))
        return {
            "handoff_pending": [{"job_id": j["job_id"], "class": classify_job(j),
                                 "state": j.get("state"),
                                 "result_zip_path": j.get("result_zip_path")}
                                for j in pending],
            "test_output_folder_id": test_id,
            "production_upload_enabled": bool(self.hcfg.get("production_upload_enabled")
                                              or self.ucfg.get("production_upload_enabled")),
            "job_classes": {j["job_id"]: classify_job(j) for j in pending},
            "state_jobs": self.state.stats(),
            "journals": sorted(p.parent.name for p in
                               self.root.glob(f"jobs/*/{self.journal_name}")),
        }

    def close(self) -> None:
        if self._state is not None:
            self._state.close()


def build(cfg: dict, **kw) -> HandoffCoordinator:
    return HandoffCoordinator(cfg, **kw)