# -*- coding: utf-8 -*-
"""bridge.py — CLI điều khiển GPT_BRIDGE.

Lệnh:
    doctor              kiểm tra môi trường, credential, Drive, Sheets, adapter
    scan                liệt kê yêu cầu cột K (không tạo job)
    detect              tạo job từ yêu cầu mới
    validate <job_id>   kiểm Drive + hồ sơ
    prepare  <job_id>   dựng GPT6_TASK_MESSAGE.txt + guide
    submit   <job_id>   giao cho adapter (manual cần --confirm)
    finish   <job_id>   QC -> upload OUTPUT -> verify -> COMPLETED
    run                 một vòng đầy đủ
    loop                chạy nền theo scan_interval_seconds
    resume              xử lý tiếp job đang dở
    status [job_id]     xem trạng thái
    inbox               xem ZIP đang chờ trong jobs/_inbox
    stop                dừng an toàn (tạo temp/STOP)
    start-clear         xoá temp/STOP

KHÔNG chiếm chuột/bàn phím. Không mở cửa sổ. Không đụng Chrome của người dùng.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import common, zip_qc                       # noqa: E402
from services.errors import BridgeError                    # noqa: E402


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_doctor(cfg: dict, args) -> int:
    """Kiểm tra thật: import, credential, Sheets, Drive, INPUT/OUTPUT, adapter."""
    out: dict = {"checks": {}, "verdict": ""}
    ok = True

    def rec(name: str, okk: bool, **kw):
        nonlocal ok
        out["checks"][name] = {"ok": okk, **kw}
        if not okk:
            ok = False

    # 1) import
    mods = {}
    for m in ("googleapiclient", "google.oauth2", "yaml", "requests"):
        try:
            __import__(m); mods[m] = True
        except Exception:  # noqa: BLE001
            mods[m] = False
    rec("python_deps", all(mods.values()), modules=mods)

    # 2) credential
    c = cfg.get("credentials") or {}
    for k in ("oauth_token", "service_account"):
        p = Path(str(c.get(k) or ""))
        rec(f"cred_{k}", p.exists(), path=str(p))

    # 3) Sheets
    try:
        from services.sheet_scanner import SheetScanner
        from services.common import project_cfg
        pc = project_cfg(cfg, args.project)
        meta = SheetScanner(cfg, pc).sheet_meta()
        rec("sheets", True, title=meta["title"], tab=pc["sheet_name"],
            sheet_id=meta["tab"].get("sheetId"))
        try:
            rows = SheetScanner(cfg, pc).scan()
            rec("sheets_scan", True, rows_with_status=len(rows),
                pending=sum(1 for r in rows if r.status == "need_script"))
        except BridgeError as e:
            rec("sheets_scan", False, code=e.code, detail=e.detail)
    except Exception as e:  # noqa: BLE001
        rec("sheets", False, detail=f"{type(e).__name__}: {e}")

    # 4) Drive + INPUT/OUTPUT thật của tập mẫu
    try:
        from services.drive_client import DriveClient
        d = DriveClient(cfg)
        inbox = cfg["drive"].get("inbox_folder_id")
        kids = d.list_children(inbox, folders_only=True)
        rec("drive", True, inbox=inbox, episode_folders=len(kids),
            sample=[k["name"] for k in kids][:6])
        # kiểm 1 tập thật nếu có
        for k in kids:
            try:
                pair = d.resolve_pair(k["id"])
                rec("drive_input_output", True, episode=k["name"],
                    input_folder_id=pair["input_folder_id"],
                    output_folder_id=pair["output_folder_id"])
                break
            except BridgeError:
                continue
    except Exception as e:  # noqa: BLE001
        rec("drive", False, detail=f"{type(e).__name__}: {e}")

    # 5) adapter
    try:
        from services.orchestrator import build_adapter
        a = build_adapter(cfg, common.get_logger(cfg))
        rec("adapter", True, adapter_name=a.name, supports_auto=a.supports_auto)
        if a.name in ("official_api", "codex_cli"):
            h = a.health()
            rec("adapter_health", h.get("ok", False), health=h)
    except Exception as e:  # noqa: BLE001
        rec("adapter", False, detail=f"{type(e).__name__}: {e}")

    out["verdict"] = "PASS" if ok else "FAIL"
    _print(out)
    return 0 if ok else 1


def cmd_scan(cfg: dict, args) -> int:
    from services.common import project_cfg
    from services.sheet_scanner import SheetScanner
    pc = project_cfg(cfg, args.project)
    rows = SheetScanner(cfg, pc).scan()
    _print([{ "row": r.row, "episode": r.episode_id, "slug": r.episode_slug,
              "status": r.status, "status_line": r.status_line,
              "zip_file_id": r.zip_file_id, "folder_id": r.folder_id,
              "local_folder": r.local_folder} for r in rows])
    return 0


def _orch(cfg, args, **kw):
    from services.orchestrator import Orchestrator
    return Orchestrator(cfg, args.project, **kw)


def cmd_detect(cfg, args) -> int:
    _print(_orch(cfg, args).detect(dry_run=args.dry_run))
    return 0


def cmd_validate(cfg, args) -> int:
    r = _orch(cfg, args).validate(args.job_id)
    _print({k: v for k, v in r.items() if k != "handoff"} | (
        {"handoff_files": r["handoff"]["files"]} if r.get("handoff") else {}))
    return 0 if r.get("ok") else 1


def cmd_prepare(cfg, args) -> int:
    r = _orch(cfg, args).prepare(args.job_id)
    _print({k: v for k, v in r.items() if k != "task_message"})
    if args.show:
        print("\n===== GPT6_TASK_MESSAGE.txt =====\n")
        print(r["task_message"])
    return 0 if r.get("ok") else 1


def cmd_submit(cfg, args) -> int:
    _print(_orch(cfg, args).submit(args.job_id, confirm=args.confirm))
    return 0


def cmd_finish(cfg, args) -> int:
    r = _orch(cfg, args).finish(args.job_id, dry_run_upload=args.dry_run_upload)
    _print(r)
    return 0 if r.get("ok") else 1


def cmd_run(cfg, args) -> int:
    r = _orch(cfg, args).run_once(confirm_submit=args.confirm, dry_run_upload=args.dry_run_upload,
                                  auto=args.auto)
    _print(r)
    return 0


def cmd_loop(cfg, args) -> int:
    orch = _orch(cfg, args)
    interval = int((cfg.get("sheets") or {}).get("scan_interval_seconds", 60))
    common.clear_stop(cfg) if args.clear_stop else None
    n = 0
    while not common.stop_requested(cfg):
        n += 1
        try:
            r = orch.run_once(confirm_submit=args.confirm, dry_run_upload=args.dry_run_upload,
                              auto=args.auto)
            common.heartbeat(cfg, {"cycle": n, "result": r})
            print(f"[{time.strftime('%H:%M:%S')}] vòng {n}: detected={len(r.get('detected', []))} "
                  f"submitted={len(r.get('submitted', []))} errors={len(r.get('errors', []))}",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{time.strftime('%H:%M:%S')}] vòng {n} LỖI: {type(e).__name__}: {e}", flush=True)
            common.heartbeat(cfg, {"cycle": n, "error": str(e)})
        if args.once:
            break
        for _ in range(interval):
            if common.stop_requested(cfg):
                break
            time.sleep(1)
    print("đã dừng an toàn", flush=True)
    return 0


def cmd_resume(cfg, args) -> int:
    _print(_orch(cfg, args).resume(dry_run_upload=args.dry_run_upload))
    return 0


def cmd_handoff(cfg, args) -> int:
    """Phase 5.3 — điều phối bàn giao tự động (HandoffStore DOWNLOADED -> Drive)."""
    co = _coord(cfg)
    try:
        if args.handoff_cmd == "doctor":
            _print(co.doctor())
            return 0
        if args.handoff_cmd == "recover":
            r = co.recover(apply=not args.dry_run)
            _print({"recover": r})
            return 0
        if args.handoff_cmd == "deliver":
            return _handoff_deliver(cfg, co, args)
        if args.once:
            r = co.tick(apply=not args.dry_run)
            _print({"tick": r})
            return 0 if all(x["status"] in ("COMPLETED", "SKIPPED", "DRY_RUN") for x in r) else 1
        r = co.run_forever(interval=args.interval, apply=not args.dry_run)
        _print({"ran": len(r), "results": r})
        return 0
    finally:
        co.close()


def _coord(cfg):
    from services.handoff_coordinator import HandoffCoordinator
    return HandoffCoordinator(cfg)


def _handoff_deliver(cfg, co, args) -> int:
    """Dựng gói bàn giao H2DEV trong SANDBOX từ ZIP đã upload (không ghi vào H2DEV)."""
    from services.h2dev_delivery import H2DevDelivery
    job = co.handoff.get(args.job_id)
    if not job:
        _print({"error": f"không có job handoff {args.job_id}"}); return 1
    rec = co.state.get(args.job_id) or {}
    src = Path(args.zip) if args.zip else Path(str(job.get("result_zip_path") or ""))
    if not src.exists():
        _print({"error": f"không thấy ZIP: {src}"}); return 1
    d = H2DevDelivery(cfg)
    man = d.prepare(src, job, slug=args.slug,
                    dest_root=args.dest_root,
                    drive={"file_id": rec.get("output_file_id"),
                           "name": rec.get("output_name"),
                           "sha256": rec.get("output_sha256"),
                           "dest_kind": (job.get("coordinator") or {}).get("dest_kind"),
                           "folder_id": rec.get("output_folder_id")})
    v = d.verify(Path(man["slug_dir"]))
    _print({"manifest": man, "verify": v})
    return 0 if v["ok"] else 1


def cmd_gate(cfg, args) -> int:
    """Phase 5.4 — preflight: tập nào THỰC SỰ đủ điều kiện viết kịch bản.

    Mặc định CHỈ ĐỌC và báo cáo. Không tự khởi chạy production.
    Chỉ `--apply` mới đưa job đủ điều kiện vào HandoffStore (state=QUEUED).
    """
    from services.common import project_cfg
    from services import script_gate as G
    from services.script_gate import ScriptGate, STATUS_QUEUED, WAITING_STATES
    from services.sheet_scanner import SheetScanner

    pc = project_cfg(cfg, args.project)
    gate = ScriptGate(cfg)

    # Tab nào cần xét: --tab chỉ định, mặc định tab của project. Preflight cần thấy
    # MỌI tab lane vì mỗi lane là một tab riêng trong bảng YOUTUBE.
    tabs = list(args.tab or [pc["sheet_name"]])
    results: list[dict] = []
    for tab in tabs:
        pc_tab = {**pc, "sheet_name": tab}
        try:
            rows = SheetScanner(cfg, pc_tab).scan_gate()
        except BridgeError as e:
            results.append({"tab": tab, "status": "REJECTED", "code": e.code,
                            "detail": e.detail or e.message})
            continue
        for req in rows:
            lane = _lane_of_req(req, pc_tab)
            ep_num = req.ep_num
            # Hàng không có marker trạng thái nào KHÔNG phải ứng viên — bỏ khỏi
            # báo cáo để danh sách "tập đủ điều kiện" không bị nhiễu (kể cả hàng
            # tiêu đề của tab). Muốn thấy cả hàng trống thì dùng --all.
            if not args.all:
                st, _ = G.sheet_decision(req.cell_raw, gate.markers)
                if st == G.SHEET_IGNORE:
                    continue
            if ep_num is None:
                results.append({"row": req.row, "tab": tab, "status": "REJECTED",
                                "code": "E_GATE_NO_EPISODE",
                                "detail": "không xác định được số tập từ cột A/B/C"})
                continue
            if args.only and f"{lane}-ep{ep_num}" not in args.only:
                continue
            try:
                results.append(gate.preflight(req, lane=lane, ep_num=ep_num,
                                              project=pc["key"], apply=args.apply))
            except BridgeError as e:
                results.append({"lane": lane, "episode_id": f"ep{ep_num}", "row": req.row,
                                "tab": tab, "status": "REJECTED",
                                "code": e.code, "detail": e.detail or e.message})

    queued = [r for r in results if r["status"] == STATUS_QUEUED]
    waiting = [r for r in results if r["status"] in WAITING_STATES]
    blocked = [r for r in results if r["status"] not in (STATUS_QUEUED, *WAITING_STATES)]

    report = {
        "project": pc["key"], "tabs": tabs,
        "applied": bool(args.apply),
        "summary": {"total": len(results), "queued": len(queued),
                    "waiting": len(waiting), "blocked": len(blocked)},
        "queued": [_brief(r) for r in queued],
        "waiting": [_brief(r) for r in waiting],
        "blocked": [_brief(r) for r in blocked],
        "results": results,
    }
    _print(report)
    if args.apply:
        print(f"\nĐÃ ĐƯA {len(queued)} JOB VÀO HÀNG ĐỢI (state=QUEUED). "
              f"Chưa gửi gì vào ChatGPT.", flush=True)
    else:
        print(f"\nCHỈ ĐỌC — chưa đưa job nào vào hàng đợi. "
              f"Đủ điều kiện: {len(queued)}. Dùng --apply để xếp hàng.", flush=True)
    return 0


def _brief(r: dict) -> dict:
    """Bản rút gọn cho báo cáo — đủ để người vận hành quyết định."""
    out = {"lane": r.get("lane"), "episode": r.get("episode_id"),
           "tab": r.get("tab"), "row": r.get("row"),
           "sheet_state": r.get("sheet_state"), "line": r.get("line"),
           "status": r.get("status"), "code": r.get("code"), "detail": r.get("detail")}
    z = (r.get("checks") or {}).get("zip") or {}
    if z.get("ok"):
        out["zip"] = {"name": z.get("name"), "hook_chars": z.get("hook_chars"),
                      "fabel_model": (z.get("fabel") or {}).get("model"),
                      "title": z.get("title")}
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _lane_of_req(req, pc) -> str:
    """Lane của một dòng. Thứ tự: đường dẫn local → tên tab → config.

    Dữ liệu thật: cùng một bảng YOUTUBE có nhiều tab lane (`Lane-N`, `Lane-O`,
    `F-teinengo-real`, `M-teinengo-anaba`…). Đường dẫn local KHÔNG phải lúc nào
    cũng có tiền tố lane (`assets\\shakai_no_naze\\...`), nên nếu chỉ dựa vào nó
    thì mọi tab đều bị gán lane_prefix của config — sai tập, sai lane.
    """
    local = str(getattr(req, "local_folder", "") or "")
    for pat in (r"assets[\\/]([A-Za-z]{1,3})-",                    # assets\N-senior_korekara\
                r"episodes[\\/]([a-z]{1,3})[\\/]",                 # episodes\q\ep07
                r"MG_STUDIO[\\/]episodes[\\/]([a-z]{1,3})[\\/]"):
        m = re.search(pat, local)
        if m:
            return m.group(1).upper()

    tab = str(getattr(req, "sheet_name", "") or "")
    m = re.match(r"^\s*lane[ _-]*([A-Za-z])\s*$", tab, re.I)       # Lane-N / Lane O
    if m:
        return m.group(1).upper()
    m = re.match(r"^\s*([A-Za-z])-[A-Za-z0-9_-]+$", tab)           # F-teinengo-real
    if m:
        return m.group(1).upper()

    return str(pc.get("lane_prefix") or "?").upper()


def cmd_status(cfg, args) -> int:
    orch = _orch(cfg, args)
    if args.job_id:
        j = orch.store.get(args.job_id)
        if not j:
            _print({"error": f"không có job {args.job_id}"}); return 1
        _print({"job": j, "events": orch.store.events(args.job_id)})
    else:
        _print(orch.status_report())
    return 0


def cmd_inbox(cfg, args) -> int:
    d = Path(cfg["_root"]) / ((cfg.get("writer") or {}).get("manual") or {}).get("inbox_dir",
                                                                                "jobs/_inbox")
    files = sorted(d.glob("*.zip")) if d.exists() else []
    _print({"inbox": str(d), "zips": [{"name": f.name, "size": f.stat().st_size,
                                       "mtime": time.ctime(f.stat().st_mtime)} for f in files]})
    return 0


def cmd_stop(cfg, args) -> int:
    p = common.request_stop(cfg)
    _print({"stopped": True, "stop_file": str(p)})
    return 0


def cmd_clear(cfg, args) -> int:
    common.clear_stop(cfg)
    _print({"stop_file_cleared": True})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bridge.py", description="GPT_BRIDGE CLI")
    ap.add_argument("--config", default=None, help="đường dẫn config.yaml")
    ap.add_argument("--project", default=None, help="project key (mặc định: project enabled)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("doctor", cmd_doctor), ("scan", cmd_scan)):
        p = sub.add_parser(name); p.set_defaults(fn=fn)

    p = sub.add_parser("detect"); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_detect)

    p = sub.add_parser("validate"); p.add_argument("job_id"); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("prepare"); p.add_argument("job_id")
    p.add_argument("--show", action="store_true"); p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("submit"); p.add_argument("job_id")
    p.add_argument("--confirm", action="store_true"); p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("finish"); p.add_argument("job_id")
    p.add_argument("--dry-run-upload", action="store_true"); p.set_defaults(fn=cmd_finish)

    p = sub.add_parser("run")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--auto", action="store_true", help="adapter tự trả file thì finish luôn")
    p.add_argument("--dry-run-upload", action="store_true"); p.set_defaults(fn=cmd_run)

    p = sub.add_parser("loop")
    p.add_argument("--once", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--auto", action="store_true")
    p.add_argument("--dry-run-upload", action="store_true")
    p.add_argument("--clear-stop", action="store_true"); p.set_defaults(fn=cmd_loop)

    p = sub.add_parser("resume"); p.add_argument("--dry-run-upload", action="store_true")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("status"); p.add_argument("job_id", nargs="?"); p.set_defaults(fn=cmd_status)

    # ---- Phase 5.4: preflight điều kiện viết kịch bản
    p = sub.add_parser("gate", help="Phase 5.4 — preflight: tập nào đủ điều kiện viết kịch bản")
    p.add_argument("--apply", action="store_true",
                   help="đưa job đủ điều kiện vào HandoffStore (mặc định CHỈ ĐỌC)")
    p.add_argument("--only", nargs="*", default=None,
                   help="chỉ xét các tập này, vd: O-ep23 N-ep17")
    p.add_argument("--tab", nargs="*", default=None,
                   help="tab lane cần xét (mặc định: tab của project)")
    p.add_argument("--all", action="store_true",
                   help="kể cả hàng không có marker trạng thái (mặc định: bỏ)")
    p.set_defaults(fn=cmd_gate)
    p = sub.add_parser("inbox"); p.set_defaults(fn=cmd_inbox)
    p = sub.add_parser("stop"); p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("start-clear"); p.set_defaults(fn=cmd_clear)

    # ---- Phase 5.3: điều phối bàn giao tự động
    p = sub.add_parser("handoff", help="Phase 5.3 — điều phối bàn giao tự động")
    hs = p.add_subparsers(dest="handoff_cmd", required=True)
    q = hs.add_parser("tick", help="một lượt: nhận job DOWNLOADED -> QC -> upload -> COMPLETED")
    q.add_argument("--once", action="store_true", default=True)
    q.add_argument("--dry-run", action="store_true", help="chỉ đo, KHÔNG ghi gì")
    q.add_argument("--interval", type=float, default=None)
    q.set_defaults(fn=cmd_handoff)
    q = hs.add_parser("watch", help="chạy vòng lặp duy nhất (dừng bằng temp/STOP)")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--interval", type=float, default=None)
    q.set_defaults(fn=cmd_handoff)
    q = hs.add_parser("recover", help="kiểm các job có journal không chắc chắn sau crash")
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(fn=cmd_handoff)
    q = hs.add_parser("doctor", help="kiểm mối nối: job chờ, folder thử nghiệm, cờ production")
    q.set_defaults(fn=cmd_handoff)
    q = hs.add_parser("deliver", help="dựng gói bàn giao H2DEV trong sandbox")
    q.add_argument("job_id")
    q.add_argument("--zip", default=None, help="ZIP nguồn (mặc định: ZIP kết quả của job)")
    q.add_argument("--slug", default=None, help="tên thư mục tập kiểu H2DEV")
    q.add_argument("--dest-root", default=None,
                   help="thư mục gốc (mặc định: sandbox; ghi ra H2DEV cần allow_production_write)")
    q.set_defaults(fn=cmd_handoff)

    args = ap.parse_args(argv)
    cfg = common.load_config(args.config)
    common.ensure_dirs(cfg)
    try:
        return args.fn(cfg, args)
    except BridgeError as e:
        _print({"error": e.as_dict()})
        return 2
    except KeyboardInterrupt:
        print("\nCtrl-C — dừng.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
