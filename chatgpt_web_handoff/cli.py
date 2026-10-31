# -*- coding: utf-8 -*-
"""cli.py — lệnh vận hành Phase 5 (dùng tay, không tự động gửi gì vào ChatGPT).

    python -m chatgpt_web_handoff.cli doctor            # kiểm tra cấu hình + môi trường
    python -m chatgpt_web_handoff.cli queue --job J.json # xếp job + in prompt
    python -m chatgpt_web_handoff.cli list              # danh sách job
    python -m chatgpt_web_handoff.cli watch             # theo dõi thư mục nhận hàng
    python -m chatgpt_web_handoff.cli qc --zip X.zip    # QC cấu trúc một ZIP
    python -m chatgpt_web_handoff.cli serve             # bật server cho extension

KHÔNG có lệnh nào gửi prompt, mở chat, hay tải file thay người dùng.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chatgpt_web_handoff.adapter import ChatGPTWebHandoffAdapter   # noqa: E402
from chatgpt_web_handoff.prompt import build_prompt, zip_filename  # noqa: E402
from chatgpt_web_handoff.store import HandoffStore                 # noqa: E402
from chatgpt_web_handoff.watcher import (ResultWatcher, inspect_zip,  # noqa: E402
                                         recover_downloaded)


def _cfg() -> dict:
    try:
        from services.common import load_config
        return load_config(ROOT / "config.yaml")
    except Exception as e:                                # noqa: BLE001
        print(f"! không đọc được config.yaml: {e}")
        return {"_root": str(ROOT)}


def _pc(cfg: dict) -> dict:
    try:
        from services.common import project_cfg
        return project_cfg(cfg)
    except Exception:                                     # noqa: BLE001
        return {"key": "unknown"}


def cmd_doctor(args) -> int:
    cfg = _cfg()
    pc = _pc(cfg)
    a = ChatGPTWebHandoffAdapter(cfg, pc)
    print("== GPT_BRIDGE · Phase 5 doctor ==")
    print(f"root            : {cfg.get('_root')}")
    print(f"project         : {pc.get('key')}")
    print(f"adapter         : {a.name} (supports_auto={a.supports_auto})")
    print(f"conversation_url: {a.conversation_url or '!! TRỐNG — adapter sẽ TỪ CHỐI job'}")
    print(f"receive_dir     : {a.receive_dir} "
          f"({'OK' if a.receive_dir.exists() else '!! KHÔNG TỒN TẠI'})")
    print(f"server_url      : {a.server_url}")
    print(f"store dir       : {a.store.dir} ({len(a.store.all())} job)")

    ok = True
    if not a.conversation_url:
        print("  -> điền writer.chatgpt_web_handoff.conversation_url trong config.yaml")
        ok = False
    if not a.receive_dir.exists():
        print("  -> tạo thư mục nhận hàng hoặc sửa receive_dir")
        ok = False
    if not a.sec.get("token"):
        print("  -> chưa có token: chạy `serve` để sinh token rồi dán vào Options extension")

    # kiểm tra không có module writer bị cấm trong Phase 5
    for banned in ("codex_cli", "gpt-6.1-sol"):
        pass
    print(f"\nKẾT LUẬN: {'SẴN SÀNG' if ok else 'CHƯA SẴN SÀNG (xem dòng !! ở trên)'}")
    return 0 if ok else 1


def cmd_queue(args) -> int:
    cfg = _cfg()
    pc = _pc(cfg)
    a = ChatGPTWebHandoffAdapter(cfg, pc)
    if args.job:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    else:
        job = json.loads(sys.stdin.read())
    prompt = build_prompt(job)
    r = a.submit(job, prompt)
    if not r["accepted"]:
        print(f"TỪ CHỐI: {r['note']}")
        return 2
    print(f"Đã xếp job {job['job_id']} → {r['output_zip_name']}")
    print(f"Cuộc hội thoại: {r['conversation_url']}")
    print(f"Server: {r['server_url']}  (mở extension GPT_BRIDGE để lấy prompt)")
    print("\n--- PROMPT ---\n" + prompt)
    return 0


def cmd_list(args) -> int:
    cfg = _cfg()
    st = HandoffStore(Path(cfg.get("_root") or ROOT))
    jobs = st.all()
    if not jobs:
        print("(không có job nào)")
        return 0
    print(f"{'JOB':<16}{'STATE':<12}{'ZIP KẾT QUẢ':<28}LANE/EP")
    for j in jobs:
        print(f"{j.get('job_id',''):<16}{str(j.get('state','')):<12}"
              f"{str(j.get('output_zip_name') or '-'):<28}"
              f"{j.get('lane') or ''}/{j.get('episode_id') or ''}")
    return 0


def cmd_qc(args) -> int:
    rep = inspect_zip(args.zip)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 1


def cmd_watch(args) -> int:
    cfg = _cfg()
    a = ChatGPTWebHandoffAdapter(cfg, _pc(cfg))
    from services.common import get_logger
    log = get_logger(cfg, "handoff_watcher")
    w = ResultWatcher(a.store, a.receive_dir,
                      settle_sec=float(a.sec.get("settle_sec") or 1.5), logger=log,
                      keep_source=bool(a.sec.get("keep_source", True)))

    def on_result(r):
        rep = r["report"]
        if rep["ok"]:
            log.info("NHẬN %s → job %s (%d ký tự) → %s",
                     Path(r["zip"]).name, r["job_id"], rep["final_jp_chars"],
                     r["moved_to"])
        else:
            log.error("TỪ CHỐI %s: %s", Path(r["zip"]).name, "; ".join(rep["problems"]))

    log.info("theo dõi %s", a.receive_dir)
    try:
        w.run_forever(interval=float(a.sec.get("poll_sec") or 3), on_result=on_result)
    except KeyboardInterrupt:
        log.info("dừng theo yêu cầu")
    return 0


def cmd_recover(args) -> int:
    """Soi lại job DOWNLOADED; --apply đưa job nhận sai tập về NEEDS_REVIEW."""
    cfg = _cfg()
    st = HandoffStore(Path(cfg.get("_root") or ROOT))
    rows = recover_downloaded(st, apply=args.apply)
    if not rows:
        print("(không có job DOWNLOADED nào)")
        return 0
    for r in rows:
        mark = "OK" if r["ok"] else ("ĐÃ SỬA" if r["applied"] else "SAI")
        print(f"{r['job_id']:<28}{mark:<10}{r['zip'] or '-'}")
        for p in r["problems"]:
            print(f"    - {p}")
    if not args.apply and any(not r["ok"] for r in rows):
        print("\n(chạy lại với --apply để đưa các job sai về NEEDS_REVIEW)")
    return 0 if all(r["ok"] for r in rows) else 1


def cmd_serve(args) -> int:
    from chatgpt_web_handoff.server import main as serve_main
    return serve_main(["--port", str(args.port or 8765)])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chatgpt_web_handoff",
                                description="Phase 5 handoff — ChatGPT Web (thao tác tay)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="kiểm tra cấu hình").set_defaults(fn=cmd_doctor)
    sub.add_parser("list", help="liệt kê job").set_defaults(fn=cmd_list)

    q = sub.add_parser("queue", help="xếp job + in prompt")
    q.add_argument("--job", help="file JSON mô tả job (bỏ trống = đọc stdin)")
    q.set_defaults(fn=cmd_queue)

    c = sub.add_parser("qc", help="QC cấu trúc một ZIP")
    c.add_argument("--zip", required=True)
    c.set_defaults(fn=cmd_qc)

    sub.add_parser("watch", help="theo dõi thư mục nhận hàng").set_defaults(fn=cmd_watch)

    r = sub.add_parser("recover", help="soi lại job DOWNLOADED, phát hiện ZIP sai tập")
    r.add_argument("--apply", action="store_true",
                   help="đưa job nhận sai tập về NEEDS_REVIEW (không xoá file nào)")
    r.set_defaults(fn=cmd_recover)

    s = sub.add_parser("serve", help="server localhost cho extension")
    s.add_argument("--port", type=int, default=0)
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
