# -*- coding: utf-8 -*-
"""Phase 5 — chạy job GIẢ LẬP qua ĐÚNG đường ống thật (Orchestrator + QC + upload).

Khác với unit test: ở đây dùng Orchestrator thật, zip_qc thật, output_collector thật,
drive giả trong RAM (FakeDrive của conftest). Chỉ thay đúng một chỗ: tiến trình
`codex` được thay bằng stub, vì tài khoản ChatGPT hiện chưa đăng nhập lại được.

Mục đích: chứng minh hợp đồng adapter khớp pipeline, ZIP đóng đúng, QC đọc được,
uploader verify được — tức là khi đăng nhập Codex xong thì KHÔNG phải sửa gì thêm.

Chạy:  .venv/Scripts/python.exe tools/phase5_sim_job.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STUB = ROOT / "tests" / "fixtures" / "stub_codex.py"

# Hồ sơ giả lập — dựng ĐÚNG cấu trúc ZIP hồ sơ H2DEV (bọc 1 folder con + manifest).
HOOK = ("その日、父は台所で静かに茶を淹れていた。"
        "四十年ぶりに開けた引き出しから出てきたのは、"
        "母の字で書かれた一枚のメモだった。")
FILLER = "私はその意味を、まだ理解していなかった。"

HANDOFF = {
    "01_HOOK_OUTLINE.txt": (
        f"=== HOOK (KHOÁ — chép nguyên văn ở đầu kịch bản; {len(HOOK)} chữ) ===\n"
        f"{HOOK}\n=== KHUNG SƯỜN ===\nPHẦN 1 — Mở\nPHẦN 2 — Thân\nPHẦN 3 — Kết\n"),
    "02_SCRIPT_RULES.md": "# 02_SCRIPT_RULES\nMục tiêu 300 · chấp nhận 250 – 400.\n",
    "03_RESEARCH_FACTS.md": "# 03_RESEARCH_FACTS\n| Trường | Giá trị | Nhãn |\n|---|---|---|\n"
                            "| Tiêu đề | abc | VERIFIED |\n",
    "04_CHANNEL_PROFILE.md": "# 04_CHANNEL_PROFILE\n",
    "05_CONTINUITY.md": "# 05_CONTINUITY\n| 戸倉誠一 | 68 |\n",
    "06_REFERENCE_SCRIPT.txt": "参考脚本。\n",
    "07_WRITING_TASK.md": (
        "# 07_WRITING_TASK — N-ep99\n\n| Mục | Giá trị |\n|---|---|\n"
        "| Job ID | `N-ep99` |\n"
        "| Số ký tự | min **250** · target **300** · max **400** |\n"),
    "08_QC_CHECKLIST.md": "# 08_QC_CHECKLIST\n| 1 | Độ dài | 250 ≤ ký tự ≤ 400 | ☐ |\n",
    "10_GPT6_START_HERE.md": "# 10_GPT6_START_HERE\nSao chép khối dưới đây...\n",
    "HANDOFF_ISSUES.md": "# HANDOFF_ISSUES\n- cảnh báo\n",
}
MANIFEST = {"job_id": "N-ep99", "episode_id": "ep99",
            "required_files": list(HANDOFF), "per_file_checksums": {},
            "title": "Giả lập Phase 5"}


def build_handoff_zip(dest: Path) -> Path:
    root = "LaneN_tap99_GPT_SCRIPT_HANDOFF"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, body in HANDOFF.items():
            zf.writestr(f"{root}/{n}", body)
        zf.writestr(f"{root}/09_HANDOFF_MANIFEST.json",
                    json.dumps(MANIFEST, ensure_ascii=False, indent=2))
    return dest


def main() -> int:
    import os
    os.environ["STUB_CODEX_MODE"] = "ok"
    os.environ.pop("STUB_CODEX_RECORD", None)

    td = Path(tempfile.mkdtemp(prefix="phase5_sim_"))
    print(f"[1] thư mục làm việc: {td}")

    # --- cfg: dựng bằng fixture thật của dự án để không lệch hành vi
    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import FakeDrive  # type: ignore

    cfg = {
        "_root": str(td), "_config_path": str(td / "config.yaml"),
        "app": {"name": "GPT_BRIDGE", "log_level": "ERROR",
                "state_db": str(td / "jobs" / "bridge.db"),
                "stop_file": str(td / "temp" / "STOP"),
                "heartbeat_file": str(td / "logs" / "heartbeat.json")},
        "credentials": {"oauth_token": str(td / "token.json"),
                        "service_account": str(td / "sa.json")},
        "drive": {"input_folder_name": "INPUT", "output_folder_name": "OUTPUT",
                  "inbox_folder_id": "INBOX_FAKE",
                  "episode_folder_pattern": r"^(?P<lane>[A-Za-z]+)-ep(?P<ep>\d+)_v(?P<ver>\d+)$",
                  "max_download_mb": 200,
                  "retry": {"attempts": 2, "base_sleep": 0, "max_sleep": 0}},
        "sheets": {"scan_interval_seconds": 60, "scan_range": "A1:M100",
                   "columns": {"episode_number": "A", "title": "B",
                               "local_folder": "C", "request": "K"},
                   "status_rules": {"latest_line_wins": True,
                                    "need_script": ["can kich ban"],
                                    "script_done": ["kich ban xong"]},
                   "link_labels": {"zip": ["zip"], "folder": ["thu muc"]}},
        "projects": {"H2DEV": {
            "enabled": True, "spreadsheet_id": "SHEET_FAKE", "sheet_name": "Lane-N",
            "sheet_id": 1, "request_column": "K",
            "prompt_template": "h2dev_script_writer.md", "writer": "GPT6",
            "lane_prefix": "N", "profile": "senior_korekara",
            "output_naming": "Lane{lane}_tap{ep_num}_{writer}_SCRIPT_DELIVERY.zip",
            "job_id_pattern": "{project}-{lane}-{episode}-{writer}-v{version:02d}"}},
        "writer": {"adapter": "codex_cli", "mode": "auto",
                   "codex_cli": {"executable": [sys.executable, str(STUB)],
                                 "model": "gpt-6.1-sol", "sandbox": "read-only",
                                 "timeout_sec": 120, "allow_model_fallback": False}},
        "qc": {"required_files": ["SCRIPT_FINAL_JP.txt", "SCRIPT_QC_REPORT.md",
                                  "SCRIPT_ISSUES.md", "SCRIPT_STRUCTURE.md"],
               "script_file": "SCRIPT_FINAL_JP.txt",
               "char_count_exclude": ["\n", "\r", " ", "\u3000", "\t"],
               "max_uncompressed_mb": 200, "max_files": 200,
               "require_hook_verbatim": True,
               "default_budget": {"min": 0, "target": 0, "max": 0}},
        "upload": {"verify_hash": True, "overwrite": False, "allow_mock_upload": False},
        "limits": {"max_attempts": 3, "unknown_submit_grace_sec": 120,
                   "max_prompt_chars_inline": 6000},
    }
    # CODEX_HOME giả: catalog có gpt-6.1-sol
    home = td / "codex_home"
    home.mkdir()
    (home / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "gpt-6.1-sol"}]}), encoding="utf-8")
    os.environ["CODEX_HOME"] = str(home)
    for d in ("jobs", "jobs/_inbox", "temp", "logs", "docs"):
        (td / d).mkdir(parents=True, exist_ok=True)

    # --- Drive giả: INBOX -> N-ep99_v01/INPUT+OUTPUT, INPUT chứa ZIP hồ sơ
    drv = FakeDrive()
    # FakeDrive.resolve_episode_folder() tra cứng "INBOX_FAKE" => dựng folder tập dưới ID đó.
    ep = drv.add_folder("N-ep99_v01", "INBOX_FAKE")
    inp = drv.add_folder("INPUT", ep)
    out = drv.add_folder("OUTPUT", ep)
    cfg["drive"]["inbox_folder_id"] = "INBOX_FAKE"
    hz = build_handoff_zip(td / "handoff.zip")
    zip_id = drv.add_file("N-ep99_GPT_SCRIPT_HANDOFF.zip", inp, hz.read_bytes())
    print(f"[2] Drive giả: episode={ep} INPUT={inp} OUTPUT={out} zip={zip_id}")

    # --- Orchestrator THẬT
    from services.orchestrator import Orchestrator
    orch = Orchestrator(cfg, "H2DEV", drive=drv)

    # a) Bỏ qua detect: detect cần Google Sheets THẬT (Phase 1–4 đã nghiệm thu).
    #    Ở đây chỉ kiểm phần Phase 5 = writer -> ZIP -> QC -> upload.
    print("[3] bỏ qua detect (cần Sheets thật) — tạo job trực tiếp")

    # b) tạo job thủ công đúng schema store (không phụ thuộc Sheets)
    job_id = "H2DEV-N-ep99-GPT6-v01"
    orch.store.create({"job_id": job_id, "project": "H2DEV", "lane": "N",
                       "episode_id": "ep99", "writer": "GPT6",
                       "input_zip_file_id": zip_id, "input_zip_name": "handoff.zip",
                       "input_folder_id": inp, "output_folder_id": out})
    orch.store.transition(job_id, "DETECTED", "giả lập Phase 5")
    print(f"[4] job: {job_id} -> {orch.store.get(job_id)['status']}")

    # c) validate (tải ZIP từ Drive giả)
    v = orch.validate(job_id)
    print(f"[5] validate: ok={v.get('ok')} files={len((v.get('handoff') or {}).get('files') or [])}")
    if not v.get("ok"):
        print("    LỖI:", v.get("error"))
        return 1

    # d) prepare
    p = orch.prepare(job_id)
    print(f"[6] prepare: ok={p.get('ok')} prompt={p['meta']['task_message_chars']} ký tự")

    # e) submit qua codex_cli
    s = orch.submit(job_id, confirm=True)
    print(f"[7] submit: ok={s.get('ok')} note={(s.get('adapter') or {}).get('note')}")
    if not s.get("ok"):
        print("    LỖI:", s.get("error"))
        return 1

    # f) chờ adapter xong
    import time
    t0 = time.time()
    while time.time() - t0 < 120:
        st = orch.adapter.status(orch.store.get(job_id) | {"_workdir": str(
            Path(td) / "jobs" / job_id)})
        if st["state"] != "waiting":
            print(f"[8] adapter status: {st['state']} ({st['detail']})")
            break
        time.sleep(0.3)
    else:
        print("[8] TIMEOUT chờ adapter")
        return 1

    # g) finish: collect -> QC -> upload -> verify -> COMPLETED
    f = orch.finish(job_id)
    ok = f.get("ok")
    print(f"[9] finish: ok={ok}")
    if not ok:
        print("    LỖI:", json.dumps(f.get("error"), ensure_ascii=False))
        return 1

    qc = f.get("qc") or {}
    checks = qc.get("checks") or {}
    print(f"[10] QC verdict={qc.get('verdict')} chars={qc.get('chars')}")
    print(f"     required_files={checks.get('required_files')}")
    print(f"     length={checks.get('length')}")
    print(f"     hook_verbatim={checks.get('hook_verbatim')}")
    print(f"     identity={checks.get('identity')}")
    print(f"     sha256(zip)={(qc.get('sha256') or '')[:16]}… size={qc.get('size')}")

    job = orch.store.get(job_id)
    print(f"[11] trạng thái cuối: {job['status']}")
    print(f"     output_file_id={job.get('output_file_id')}")
    print(f"     output_name={job.get('output_name')}")
    print(f"     output_sha256={(job.get('output_sha256') or '')[:16]}…")

    # h) kiểm chứng ZIP đã nằm trên Drive giả + đọc lại được
    if job.get("output_file_id"):
        blob = drv.blobs.get(job["output_file_id"])
        if blob:
            zpath = td / "readback.zip"
            zpath.write_bytes(blob)
            with zipfile.ZipFile(zpath) as zf:
                names = zf.namelist()
                script = zf.read("SCRIPT_FINAL_JP.txt").decode("utf-8")
            print(f"[12] đọc lại ZIP từ Drive giả: {len(names)} file {names}")
            print(f"     SCRIPT_FINAL_JP.txt: {len(script)} ký tự, "
                  f"mở đầu: {script[:40]!r}")

    print("\n=== KẾT QUẢ GIẢ LẬP ===")
    print(f"job_id            : {job_id}")
    print(f"trạng thái        : {job['status']}")
    print(f"adapter           : {orch.adapter.name} (model={orch.adapter.model})")
    print(f"QC                : {qc.get('verdict')}")
    print(f"output trên Drive : {job.get('output_name')}")
    print(f"thư mục làm việc  : {td}")
    return 0 if job["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
