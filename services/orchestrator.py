# -*- coding: utf-8 -*-
"""orchestrator.py — vòng đời job từ phát hiện đến COMPLETED.

Vai trò (spec Phần IV):
- Python làm MỌI thao tác xác định: parse URL, lấy ID, đọc metadata, kiểm file,
  SHA-256, đếm file, lưu trạng thái, upload/download, đối chiếu job ID.
- AI (DeepSeek) CHỈ dùng cho việc cần suy luận: phân loại yêu cầu mơ hồ, tóm tắt lỗi,
  đề xuất bước tiếp theo. V1 KHÔNG bắt buộc gọi AI trong đường tới hạn — pipeline
  chạy được hoàn toàn bằng luật tất định. Xem docs/IMPLEMENTATION_REPORT.md §DeepSeek.

Đường tới hạn (không phụ thuộc AI):
    scan -> validate Drive -> build prompt -> submit adapter -> collect
    -> zip_qc -> upload OUTPUT -> verify -> COMPLETED
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import output_collector, zip_qc
from .common import get_logger, project_cfg, stop_requested
from .drive_client import DriveClient
from .drive_uploader import DriveUploader
from .errors import BridgeError
from .prompt_builder import PromptBuilder, parse_budget, parse_hook
from .sheet_scanner import SheetScanner, ParsedRequest
from .state import StateStore, job_dir

READ_FILES = ["10_GPT6_START_HERE.md", "07_WRITING_TASK.md", "01_HOOK_OUTLINE.txt",
              "02_SCRIPT_RULES.md", "05_CONTINUITY.md", "03_RESEARCH_FACTS.md",
              "04_CHANNEL_PROFILE.md", "06_REFERENCE_SCRIPT.txt", "08_QC_CHECKLIST.md",
              "HANDOFF_ISSUES.md"]


def build_adapter(cfg: dict, log):
    w = cfg.get("writer") or {}
    kind = str(w.get("adapter") or "mock").lower()
    if kind == "mock":
        from adapters.mock_chatgpt import MockChatGPTAdapter
        return MockChatGPTAdapter(cfg, log)
    if kind == "manual":
        from adapters.manual_chatgpt import ManualChatGPTAdapter
        return ManualChatGPTAdapter(cfg, log)
    if kind == "chatgpt_web":
        from adapters.chatgpt_web import ChatGPTWebAdapter
        return ChatGPTWebAdapter(cfg, log)
    if kind == "official_api":
        from adapters.official_api import OfficialAPIAdapter
        return OfficialAPIAdapter(cfg, log)
    if kind == "codex_cli":
        from adapters.codex_cli_writer import CodexCLIWriterAdapter
        return CodexCLIWriterAdapter(cfg, log)
    if kind in ("chatgpt_web_handoff", "chatgpt_handoff"):
        # Phase 5: writer = ChatGPT Web trong ĐÚNG cuộc hội thoại đã cấu hình.
        # Không gửi hộ, không tải hộ — người dùng thao tác qua extension.
        from chatgpt_web_handoff.adapter import ChatGPTWebHandoffAdapter
        return ChatGPTWebHandoffAdapter(cfg, logger=log)
    raise BridgeError("E_CONFIG", f"writer.adapter không hợp lệ: {kind}")


class Orchestrator:
    def __init__(self, cfg: dict, project: str | None = None, *, adapter=None, drive=None):
        self.cfg = cfg
        self.log = get_logger(cfg, "bridge")
        self.pc = project_cfg(cfg, project)
        self.root = Path(cfg.get("_root") or ".")
        self.store = StateStore(self.root / str((cfg.get("app") or {}).get("state_db",
                                                                         "jobs/bridge.db")))
        self.scanner = SheetScanner(cfg, self.pc)
        self.drive = drive or DriveClient(cfg, self.log)
        self.adapter = adapter or build_adapter(cfg, self.log)
        self.builder = PromptBuilder(cfg, self.pc, self.log)
        self.uploader = DriveUploader(cfg, self.drive, self.log)

    # ================================================================ scan
    def scan(self) -> list[ParsedRequest]:
        return self.scanner.scan()

    # ================================================================ detect
    def detect(self, *, dry_run: bool = False) -> list[dict]:
        """Tạo job cho mọi hàng need_script chưa có job. KHÔNG tạo trùng sau restart."""
        made = []
        for req in self.scanner.scan_pending():
            exist = self.store.by_row_key(req.row_key)
            if exist:
                self.log.info("bỏ qua dòng %s — job %s đã có (%s)",
                              req.row, exist["job_id"], exist["status"])
                continue
            lane = self._lane_of(req)
            ep = req.episode_id
            if req.ep_num is None:
                self.log.warning("dòng %s: không xác định được số tập — cần người xem", req.row)
                continue
            dup = self.store.active_for_episode(self.pc["key"], lane, ep)
            if dup:
                self.log.warning("tập %s đã có job đang chạy (%s) — không tạo mới",
                                 ep, dup["job_id"])
                continue

            job_id = self._job_id(lane, ep)
            row = {
                "job_id": job_id, "project": self.pc["key"], "lane": lane,
                "episode_id": ep, "episode_slug": req.episode_slug,
                "source_spreadsheet_id": req.spreadsheet_id,
                "source_sheet_id": req.sheet_id, "source_sheet_name": req.sheet_name,
                "source_row": req.row, "row_key": req.row_key,
                "input_zip_file_id": req.zip_file_id, "input_zip_url": req.zip_url,
                "notes": json.dumps({"status_line": req.status_line,
                                     "extra_notes": req.extra_notes,
                                     "local_folder": req.local_folder,
                                     "folder_id_in_cell": req.folder_id},
                                    ensure_ascii=False),
                "status": "DETECTED",
            }
            if dry_run:
                made.append({"dry_run": True, **row})
                continue
            job = self.store.create(row)
            self.store.transition(job_id, "VALIDATING", "scanner phát hiện yêu cầu cột K")
            made.append(job)
            self.log.info("DETECTED %s (dòng %s)", job_id, req.row)
        return made

    def _lane_of(self, req: ParsedRequest) -> str:
        """Lane lấy từ slug/đường dẫn local ('N-senior_korekara') hoặc config."""
        for src in (req.local_folder, req.episode_slug):
            m = re.search(r"(?:assets[\\/])?([A-Za-z]{1,3})-([A-Za-z0-9_]+)", str(src or ""))
            if m and m.group(1).isupper() is False:
                pass
        m = re.search(r"assets[\\/]([A-Za-z]{1,3})-", str(req.local_folder or ""))
        if m:
            return m.group(1).upper()
        return str(self.pc.get("lane_prefix") or "?").upper()

    def _job_id(self, lane: str, ep: str) -> str:
        pat = self.pc.get("job_id_pattern") or "{project}-{lane}-{episode}-{writer}-v{version:02d}"
        base = pat.format(project=self.pc["key"], lane=lane, episode=ep,
                          writer=self.pc.get("writer", "GPT6"), version=1)
        if not self.store.get(base):
            return base
        for v in range(2, 100):
            cand = pat.format(project=self.pc["key"], lane=lane, episode=ep,
                              writer=self.pc.get("writer", "GPT6"), version=v)
            if not self.store.get(cand):
                return cand
        raise BridgeError("E_CONFIG", f"hết version cho {lane}/{ep}")

    # ================================================================ validate
    def validate(self, job_id: str) -> dict:
        """Kiểm Drive + hồ sơ. Chỉ tiếp nhận job khi TẤT CẢ kiểm tra đạt."""
        job = self.store.get(job_id)
        if not job:
            raise KeyError(job_id)
        wd = job_dir(self.root, job_id)
        notes = json.loads(job.get("notes") or "{}")

        try:
            # 1) folder tập: ưu tiên folder_id trong ô K, không có thì tra 01_INBOX
            folder_id = notes.get("folder_id_in_cell")
            if folder_id:
                meta = self.drive.get(folder_id, "id,name,mimeType")
                if meta.get("mimeType") != "application/vnd.google-apps.folder":
                    raise BridgeError("E_DRIVE_NOT_FOUND",
                                      f"ID trong ô K không phải folder: {folder_id}")
                ep_folder = {"id": folder_id, "name": meta.get("name"), "version": 0}
            else:
                ep_folder = self.drive.resolve_episode_folder(job["lane"], int(re.sub(r"\D", "", job["episode_id"])))
            self.log.info("%s folder tập: %s (%s)", job_id, ep_folder["name"], ep_folder["id"])

            # 2) INPUT/OUTPUT bằng API — KHÔNG ghép chuỗi URL
            pair = self.drive.resolve_pair(ep_folder["id"])

            # 3) ZIP: dùng file_id trong ô K nếu có; nếu không, tìm trong INPUT
            zip_id = job.get("input_zip_file_id")
            if zip_id:
                zm = self.drive.get(zip_id, "id,name,size,mimeType,parents")
                self.drive.assert_in_folder(zip_id, pair["input_folder_id"])
                zip_name, zip_size = zm.get("name"), int(zm.get("size") or 0)
            else:
                cands = [f for f in self.drive.list_children(pair["input_folder_id"])
                         if f["name"].lower().endswith(".zip")]
                if not cands:
                    raise BridgeError("E_ZIP_NOT_IN_INPUT",
                                      f"INPUT {pair['input_folder_id']} không có .zip nào")
                cands.sort(key=lambda f: f.get("modifiedTime") or "", reverse=True)
                zip_id, zip_name = cands[0]["id"], cands[0]["name"]
                zip_size = int(cands[0].get("size") or 0)
                self.log.info("%s ô K không có link ZIP — lấy ZIP mới nhất trong INPUT: %s",
                              job_id, zip_name)

            # 4) tải + hash + kiểm cấu trúc hồ sơ
            local = wd / "input" / zip_name
            dl = self.drive.download(zip_id, local)
            handoff = zip_qc.inspect_handoff(local, self.cfg)
            if handoff["checksum_mismatch"]:
                self.log.warning("%s checksum lệch ở: %s", job_id, handoff["checksum_mismatch"])

            # 5) ghi lại
            self.store.update(
                job_id,
                input_zip_file_id=zip_id, input_zip_name=zip_name, input_zip_url=dl and
                self.drive.web_link(zip_id, "file"),
                input_sha256=dl["sha256"],
                input_folder_id=pair["input_folder_id"], output_folder_id=pair["output_folder_id"],
                input_folder_url=self.drive.web_link(pair["input_folder_id"], "folder"),
                output_folder_url=self.drive.web_link(pair["output_folder_id"], "folder"),
                notes=json.dumps({**notes, "episode_folder_id": ep_folder["id"],
                                  "episode_folder_name": ep_folder["name"],
                                  "zip_size": zip_size,
                                  "handoff_files": handoff["files"]}, ensure_ascii=False))
            (wd / "input" / "handoff_summary.json").write_text(json.dumps(
                {k: handoff[k] for k in ("sha256", "size", "files", "checksum_mismatch",
                                         "root_prefix")}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self.store.transition(job_id, "VALIDATING",
                                  f"hồ sơ OK: {len(handoff['files'])} file, INPUT/OUTPUT đã resolve")
            return {"ok": True, "job": self.store.get(job_id), "handoff": handoff}
        except BridgeError as e:
            code = e.code
            state = "BLOCKED" if code in ("E_DRIVE_AUTH", "E_DRIVE_NO_PERMISSION",
                                          "E_NO_LINK") else "NEEDS_REVIEW"
            self.store.update(job_id, error_code=code, error_detail=e.detail)
            self.store.transition(job_id, state, f"{code}: {e.detail}"[:400])
            return {"ok": False, "error": e.as_dict(), "job": self.store.get(job_id)}

    # ================================================================ prompt
    def prepare(self, job_id: str) -> dict:
        """Dựng GPT6_TASK_MESSAGE.txt + guide, kèm link TỪNG file trong INPUT."""
        job = self.store.get(job_id)
        wd = job_dir(self.root, job_id)
        handoff = zip_qc.inspect_handoff(wd / "input" / job["input_zip_name"], self.cfg)

        # link từng file rời trong INPUT (đã có sẵn — không upload lại)
        in_id = job["input_folder_id"]
        children = {f["name"]: f for f in self.drive.list_children(in_id)
                    if not f["mimeType"].endswith("folder")}
        drive_files = {n: {"id": children[n]["id"], "url": self.drive.web_link(children[n]["id"])}
                       for n in READ_FILES if n in children}

        job["input_zip_name"] = job["input_zip_name"]
        job["input_zip_url"] = job["input_zip_url"] or self.drive.web_link(job["input_zip_file_id"])
        job["input_folder_url"] = job["input_folder_url"] or self.drive.web_link(in_id, "folder")
        job["output_folder_url"] = job["output_folder_url"] or self.drive.web_link(
            job["output_folder_id"], "folder")

        built = self.builder.build(job, {
            "manifest": handoff["manifest"], "writing_task": handoff["texts"].get("07_WRITING_TASK.md", ""),
            "hook_outline": handoff["texts"].get("01_HOOK_OUTLINE.txt", ""),
            "qc_checklist": handoff["texts"].get("08_QC_CHECKLIST.md", ""),
            "files": handoff["texts"]}, drive_files)
        paths = PromptBuilder.write(wd, built)

        missing_links = [n for n in READ_FILES if n not in drive_files]
        self.store.transition(job_id, "READY_TO_SUBMIT",
                              f"prompt {len(built['task_message'])} ký tự, "
                              f"{len(drive_files)} link file"
                              + (f", thiếu link: {missing_links}" if missing_links else ""))
        return {"ok": True, "paths": {k: str(v) for k, v in paths.items()},
                "meta": built["meta"], "task_message": built["task_message"],
                "missing_links": missing_links}

    # ================================================================ submit
    def submit(self, job_id: str, *, confirm: bool = False) -> dict:
        """Giao cho adapter. Với adapter thật, BẮT BUỘC confirm=True để tránh gửi trùng."""
        job = self.store.get(job_id)
        if job["status"] not in ("READY_TO_SUBMIT",):
            return {"ok": False, "error": f"trạng thái {job['status']} không cho submit"}
        if not self.adapter.supports_auto and not confirm:
            return {"ok": False, "error": "adapter manual — cần --confirm để ghi gói giao việc"}

        wd = job_dir(self.root, job_id)
        prompt = (wd / "GPT6_TASK_MESSAGE.txt").read_text(encoding="utf-8")
        job["_workdir"] = str(wd)
        try:
            self.store.transition(job_id, "SUBMITTING", f"adapter={self.adapter.name}")
            res = self.adapter.submit(job, prompt)
            if not res.get("accepted"):
                self.store.update(job_id, error_code="E_ADAPTER_BLOCKED",
                                  error_detail=res.get("note", ""))
                self.store.transition(job_id, "BLOCKED", res.get("note", "")[:400])
                return {"ok": False, "adapter": res, "job": self.store.get(job_id)}
            self.store.update(job_id, conversation_url=res.get("conversation_url"),
                              attempt=int(job.get("attempt") or 0) + 1,
                              notes=json.dumps({**json.loads(job.get("notes") or "{}"),
                                                "adapter": self.adapter.name,
                                                "submit_note": res.get("note", "")},
                                               ensure_ascii=False))
            self.store.transition(job_id, "WAITING_GPT", res.get("note", "")[:400])
            return {"ok": True, "adapter": res, "job": self.store.get(job_id)}
        except BridgeError as e:
            self.store.update(job_id, error_code=e.code, error_detail=e.detail)
            self.store.transition(job_id, "FAILED", f"{e.code}: {e.detail}"[:400])
            return {"ok": False, "error": e.as_dict()}

    # ================================================================ finish
    def finish(self, job_id: str, *, dry_run_upload: bool = False) -> dict:
        """collect -> QC -> upload OUTPUT -> verify -> COMPLETED.

        COMPLETED chỉ khi Drive xác nhận + đọc lại ZIP thành công.
        """
        job = self.store.get(job_id)
        wd = job_dir(self.root, job_id)
        job["_workdir"] = str(wd)
        notes = json.loads(job.get("notes") or "{}")

        try:
            # 0) nạp hồ sơ vào job để adapter/QC dùng chung (mock cần budget+hook)
            handoff_zip = wd / "input" / job["input_zip_name"]
            handoff = zip_qc.inspect_handoff(handoff_zip, self.cfg)
            budget = parse_budget(handoff["texts"].get("07_WRITING_TASK.md", ""),
                                  handoff["texts"].get("08_QC_CHECKLIST.md", ""))
            hook = parse_hook(handoff["texts"].get("01_HOOK_OUTLINE.txt", ""))
            job["_handoff"] = {"budget": budget, "hook_text": hook["text"],
                               "manifest": handoff["manifest"], "files": handoff["files"]}

            # 1) nhận ZIP
            self.store.transition(job_id, "RESULT_AVAILABLE", "adapter trả kết quả")
            got = output_collector.collect(self.adapter, job, wd)
            (wd / "collect.json").write_text(json.dumps(got, ensure_ascii=False, indent=2,
                                                        default=str), encoding="utf-8")

            # 2) QC hình thức
            self.store.transition(job_id, "VERIFYING", f"nguồn={got['source']}")
            report = zip_qc.inspect_result(got["zip_path"], self.cfg, job,
                                           {"budget": budget, "hook_text": hook["text"]})
            zip_qc.write_report(report, wd / "qc_report.json")

            # 3) CHẶN: ZIP do MOCK sinh không được coi là sản phẩm bàn giao
            #    (dry-run không ghi gì lên Drive nên không cần chặn)
            if (not dry_run_upload and got.get("source") == "mock"
                    and not (self.cfg.get("upload") or {}).get("allow_mock_upload", False)):
                raise BridgeError("E_MOCK_NOT_UPLOADABLE",
                                  "adapter mock — đặt upload.allow_mock_upload=true nếu chỉ muốn "
                                  "kiểm thử đường ống")

            # 4) upload + verify
            if dry_run_upload:
                self.store.transition(job_id, "UPLOADING", "DRY-RUN: không upload")
                return {"ok": True, "dry_run": True, "qc": report, "collected": got}

            self.store.transition(job_id, "UPLOADING", "upload OUTPUT")
            name = self._output_name(job, notes)
            up = self.uploader.upload_and_verify(got["zip_path"], job,
                                                 expect_sha=got["sha256"], name=name)
            self.store.update(job_id, output_file_id=up["file_id"], output_sha256=up["sha256_remote"],
                              output_name=up["name"])
            self.uploader.write_receipt(wd, {**job, "updated_at": self.store.get(job_id)["updated_at"]},
                                        report, up)
            self.uploader.write_json(wd, "qc_report.json", report)
            self.uploader.write_json(wd, "upload.json", up)
            self.store.transition(job_id, "COMPLETED",
                                  f"OUTPUT {up['file_id']} verified ({up['size']} bytes)")
            return {"ok": True, "qc": report, "upload": up, "job": self.store.get(job_id)}

        except BridgeError as e:
            state = "NEEDS_REVIEW" if e.code.startswith(("E_RESULT_", "E_NEEDS_REVIEW",
                                                        "E_MOCK_NOT_UPLOADABLE")) else "FAILED"
            if e.code == "E_RESULT_MISSING":
                state = "WAITING_GPT"
            self.store.update(job_id, error_code=e.code, error_detail=e.detail)
            self.store.transition(job_id, state, f"{e.code}: {e.detail}"[:400])
            return {"ok": False, "error": e.as_dict(), "job": self.store.get(job_id)}

    def _output_name(self, job: dict, notes: dict) -> str:
        pat = self.pc.get("output_naming") or "Lane{lane}_tap{ep_num}_{writer}_SCRIPT_DELIVERY.zip"
        ep_num = re.sub(r"\D", "", job.get("episode_id") or "") or "0"
        return pat.format(lane=job.get("lane") or "?", ep_num=ep_num,
                          writer=self.pc.get("writer", "GPT6"), job_id=job["job_id"])

    # ================================================================ run once
    def run_once(self, *, confirm_submit: bool = False, dry_run_upload: bool = False,
                 auto: bool = False) -> dict:
        """Một vòng: detect -> validate -> prepare -> submit -> (auto? finish)."""
        out = {"detected": [], "validated": [], "prepared": [], "submitted": [],
               "finished": [], "skipped": [], "errors": []}
        if stop_requested(self.cfg):
            return {"stopped": True}

        for j in self.detect():
            out["detected"].append(j["job_id"])
            v = self.validate(j["job_id"])
            if not v["ok"]:
                out["errors"].append({"job_id": j["job_id"], **v["error"]})
                continue
            out["validated"].append(j["job_id"])
            p = self.prepare(j["job_id"])
            out["prepared"].append(j["job_id"])

            s = self.submit(j["job_id"], confirm=confirm_submit)
            if not s.get("ok"):
                out["errors"].append({"job_id": j["job_id"], **(s.get("error") or {})})
                continue
            out["submitted"].append(j["job_id"])

            if auto and self.adapter.supports_auto:
                f = self.finish(j["job_id"], dry_run_upload=dry_run_upload)
                (out["finished"] if f.get("ok") else out["errors"]).append(
                    j["job_id"] if f.get("ok") else {"job_id": j["job_id"], **f.get("error", {})})
        return out

    def resume(self, *, dry_run_upload: bool = False) -> dict:
        """Xử lý tiếp job đang dở (chạy lại sau restart)."""
        out = {"finished": [], "waiting": [], "errors": []}
        for st in ("WAITING_GPT", "RESULT_AVAILABLE", "VERIFYING"):
            for job in self.store.list(st):
                if stop_requested(self.cfg):
                    return {**out, "stopped": True}
                jid = job["job_id"]
                try:
                    job["_workdir"] = str(job_dir(self.root, jid))
                    s = self.adapter.status(job)
                except Exception as e:  # noqa: BLE001
                    out["errors"].append({"job_id": jid, "code": "E_UNKNOWN", "detail": str(e)})
                    continue
                if s["state"] == "result_available":
                    f = self.finish(jid, dry_run_upload=dry_run_upload)
                    (out["finished"] if f.get("ok") else out["errors"]).append(
                        jid if f.get("ok") else {"job_id": jid, **f.get("error", {})})
                elif s["state"] == "failed":
                    self.store.update(jid, error_code="E_WRITER_TIMEOUT", error_detail=s["detail"])
                    self.store.transition(jid, "NEEDS_REVIEW", s["detail"][:400])
                    out["errors"].append({"job_id": jid, "code": "E_WRITER_TIMEOUT"})
                else:
                    out["waiting"].append(jid)
        return out

    # ================================================================ status
    def status_report(self) -> dict:
        return {"stats": self.store.stats(),
                "jobs": [{k: j.get(k) for k in ("job_id", "lane", "episode_id", "status",
                                                "attempt", "error_code", "output_file_id",
                                                "output_name", "updated_at")}
                         for j in self.store.list(limit=100)]}
