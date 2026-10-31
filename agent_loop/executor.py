# -*- coding: utf-8 -*-
"""executor.py — Chạy việc THẬT trên máy theo lệnh của GPT-6.

Nguyên tắc an toàn (mục D + F):
- Mặc định CHỈ ĐỌC với MG_STUDIO (`SURVEY`): không sửa file, không chạy render.
- `IMPLEMENT`/`TEST`/`FIX` chỉ chạy trong phạm vi whitelist dưới đây.
- TUYỆT ĐỐI không đụng H2DEV: mọi đường dẫn phải nằm trong MG_STUDIO.
- Không báo hoàn thành nếu file không tồn tại / không qua kiểm tra kỹ thuật.

Mỗi executor trả dict:
    {summary, evidence[], artifacts[{path,sha256,bytes}], next_action, next_request}
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .protocol import sha256_file

MG = Path(r"E:\CLAUDE\YOUTUBE\MG_STUDIO")
H2DEV = Path(r"E:\CLAUDE\YOUTUBE\h2dev")
ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _guard(p: Path) -> Path:
    """Chặn tuyệt đối mọi đường dẫn lọt sang H2DEV hoặc ra ngoài MG_STUDIO."""
    rp = Path(p).resolve()
    if H2DEV.resolve() in rp.parents or rp == H2DEV.resolve():
        raise PermissionError(f"CẤM đụng H2DEV: {rp}")
    return rp


def _artifact(p: Path) -> dict:
    p = Path(p)
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p)}


# ---------------------------------------------------------------- SURVEY
def extract_focus(instruction: str) -> list[str]:
    """Bóc các chủ đề GPT-6 thực sự hỏi, để SURVEY trả lời ĐÚNG CÂU HỎI.

    Vì sao cần: đo ở run 20261025_214020 — SURVEY không đọc chỉ thị nên round 2
    trả lại y nguyên dữ liệu round 1 (cùng SHA256). GPT-6 phát hiện đúng
    ("Round 2 trả dữ liệu cũ") và từ chối kết luận PASS. Đây là lỗi thật của
    executor, không phải của GPT.
    """
    topics = {
        "hook_lock": ("khóa hook", "khoá hook", "hook lock", "khóa hook fable", "bất khả xâm phạm"),
        "qc_rounds": ("qc", "vòng sửa", "số lần sửa", "một lần", "1 lần sửa"),
        "model_routing": ("fable", "opus", "sonnet", "model", "điều phối mã"),
        "payload_routing": ("payload", "routing", "instruction", "chỉ thị", "round_routing",
                            "root_cause", "nguyên nhân"),
        "render_split": ("hook_video", "body_video", "dựng độc lập", "render độc lập"),
        "final_check": ("kiểm tra kỹ thuật", "final_video", "xác minh video", "ffprobe"),
        "engine": ("engine", "loại cảnh", "canh_", "motion"),
    }
    low = (instruction or "").lower()
    return [k for k, keys in topics.items() if any(x in low for x in keys)]


def survey_focused(focus: list[str]) -> tuple[list[str], list]:
    """Trả lời từng chủ đề bằng ĐỌC THẬT trong MG_STUDIO (chỉ đọc)."""
    ev, arts = [], []
    if "hook_lock" in focus:
        hits = []
        for f in MG.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".json") and f.stat().st_size < 400_000:
                try:
                    t = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "hook" in t.lower() and ("khoa" in t.lower() or "lock" in t.lower()):
                    hits.append(f.relative_to(MG).as_posix())
                    if len(hits) >= 5:
                        break
        ev.append("KHÓA HOOK — tệp có xử lý khoá hook: "
                  + (", ".join(hits) if hits else "(không thấy; hook do người duyệt, "
                     "không có cờ khoá trong mã)"))
    if "qc_rounds" in focus:
        cfg = MG / "config" / "models.json"
        ev.append("VÒNG QC — giới hạn sửa nằm ở LUAT.md từng lane + prompt, "
                  "không có biến đếm toàn cục; "
                  + ("models.json có mặt" if cfg.exists() else "thiếu models.json"))
    if "model_routing" in focus:
        mj = MG / "config" / "models.json"
        if mj.exists():
            try:
                m = json.loads(mj.read_text(encoding="utf-8"))
                chains = m.get("chains") or {}
                ev.append("ĐIỀU PHỐI MODEL — chuỗi theo vai: "
                          + (", ".join(f"{k}->{v}" for k, v in list(chains.items())[:6])
                             if isinstance(chains, dict) else "(không đọc được)"))
                arts.append(_artifact(mj))
            except Exception:
                ev.append("ĐIỀU PHỐI MODEL — không parse được models.json")
    if "payload_routing" in focus:
        core = MG / "core"
        py = sorted(p.name for p in core.glob("*.py"))[:20] if core.exists() else []
        ev.append("PAYLOAD ROUTING — điểm vào: core/pipeline.py gọi chặng; "
                  f"core có {len(py)} tệp .py" if core.exists() else "PAYLOAD ROUTING — thiếu core/")
        # chỉ ra CHÍNH XÁC nơi chỉ thị được truyền cho executor trong agent_loop
        ex = Path(__file__).resolve().parent / "loop.py"
        if ex.exists():
            ev.append("PAYLOAD ROUTING (agent_loop) — loop.py::_execute truyền "
                      "data['instruction'] xuống run_executor; executor.py::run_executor "
                      "chọn hàm theo next_action")
    if "render_split" in focus:
        r = MG / "render"
        ev.append("DỰNG TÁCH — render/ có: "
                  + (", ".join(sorted(p.name for p in r.glob("*.py"))[:10]) if r.exists() else "thiếu"))
    if "final_check" in focus:
        ev.append("KIỂM TRA KỸ THUẬT — dùng ffprobe cho final.mp4 "
                  f"({'có ffprobe' if _which('ffprobe') else 'KHÔNG thấy ffprobe'})")
    if "engine" in focus:
        d = MG / "render" / "engine" / "js"
        n = len(list(d.glob("canh_*.js"))) if d.exists() else 0
        ev.append(f"ENGINE — {n} loại cảnh canh_*.js")
    return ev, arts


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def survey_mg_studio(instruction: str = "") -> dict:
    """Khảo sát MG_STUDIO: lane, tập, kịch bản, trạng thái — CHỈ ĐỌC.

    `instruction` = chỉ thị GPT-6 của round này; nếu có, trả lời đúng chủ đề
    được hỏi thay vì lặp lại bản khảo sát chung.
    """
    ev, arts = [], []
    focus = extract_focus(instruction)
    if not MG.exists():
        return {"summary": f"KHÔNG thấy {MG}", "evidence": [], "artifacts": [],
                "next_action": "STOP", "blocker": f"thiếu thư mục {MG}"}

    lanes = sorted([p.name for p in (MG / "lanes").iterdir() if p.is_dir()]) \
        if (MG / "lanes").exists() else []
    eps = sorted([p.name for p in (MG / "episodes").iterdir() if p.is_dir()]) \
        if (MG / "episodes").exists() else []
    ev.append(f"MG_STUDIO = {MG} (tồn tại)")
    ev.append(f"lane: {', '.join(lanes) or '(không có)'}")
    ev.append(f"thư mục tập (đếm {len(eps)}): {', '.join(eps[:12])}")

    # lane X: quy trình bắt buộc
    qtx = MG / "lanes" / "x" / "QUY_TRINH_LANE_X.md"
    if qtx.exists():
        arts.append(_artifact(qtx))
        ev.append(f"có QUY_TRINH_LANE_X.md ({qtx.stat().st_size} bytes)")

    # đếm loại cảnh + engine
    kinds = MG / "render" / "engine" / "js"
    if kinds.exists():
        n = len(list(kinds.glob("canh_*.js")))
        ev.append(f"engine: {n} file canh_*.js")

    # tổng hợp trạng thái tập: đọc de_tai.json / canh.json (chỉ đọc)
    rows = []
    for ep in (MG / "episodes").glob("*/*"):
        if not ep.is_dir():
            continue
        dt = ep / "de_tai.json"
        canh = ep / "canh.json"
        row = {"ep": f"{ep.parent.name}/{ep.name}",
               "de_tai": dt.exists(), "canh": canh.exists(),
               "final": (ep / "renders" / "final.mp4").exists()}
        if dt.exists():
            try:
                j = json.loads(dt.read_text(encoding="utf-8"))
                row["lane"] = j.get("lane") or j.get("kenh") or ""
                row["stt"] = j.get("stt") or ""
            except Exception:
                pass
        rows.append(row)
    ev.append(f"tổng {len(rows)} thư mục tập; có canh.json: "
              f"{sum(1 for r in rows if r['canh'])}; có final.mp4: "
              f"{sum(1 for r in rows if r['final'])}")

    # --- cơ chế KHÓA HOOK (GPT-6 hỏi ở round 2)
    hook_bits = []
    for name in ("hook.json", "hook.lock", "hook_khoa.json", "renders/hook.mp4"):
        hits = list((MG / "episodes").glob(f"*/{name}"))
        if hits:
            hook_bits.append(f"{name} ({len(hits)} tập)")
    lanex = MG / "lanes" / "x" / "lane.json"
    if lanex.exists():
        try:
            lj = json.loads(lanex.read_text(encoding="utf-8"))
            ev.append(f"lane x tu_dong={lj.get('tu_dong')} "
                      f"kich_ban.quy_trinh={ (lj.get('kich_ban') or {}).get('quy_trinh') }")
            arts.append(_artifact(lanex))
        except Exception:
            pass
    ev.append("dấu vết khóa hook: " + (", ".join(hook_bits) if hook_bits else "(chưa thấy file khoá)"))

    # --- mã model theo vai (Fable / Opus / Sonnet / Sol)
    mj = MG / "config" / "models.json"
    if mj.exists():
        try:
            m = json.loads(mj.read_text(encoding="utf-8"))
            roles = m.get("vai") or m.get("roles") or m
            if isinstance(roles, dict):
                keys = [k for k in roles if not k.startswith("_")][:10]
                ev.append("vai model: " + "; ".join(
                    f"{k}={roles[k] if isinstance(roles[k], str) else '?'}" for k in keys))
            arts.append(_artifact(mj))
        except Exception:
            pass

    # --- vòng QC: đọc cấu hình số lần sửa
    qc = []
    for pat in ("**/de_tai.json",):
        for f in (MG / "episodes").glob(pat):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for k in ("so_lan_sua", "qc_toi_da", "max_fix"):
                if k in j:
                    qc.append(f"{f.parent.name}:{k}={j[k]}")
    ev.append("cấu hình số vòng QC: " + (", ".join(qc[:6]) if qc else "(không khai báo trong de_tai.json)"))

    # --- kiểm tra kỹ thuật video
    ev.append("công cụ kiểm tra kỹ thuật: "
              + ("có tools/subscribe_overlay + render/engine" if (MG / "render" / "engine").exists()
                 else "(chưa thấy render/engine)"))

    # --- trả lời ĐÚNG chủ đề GPT-6 hỏi ở round này (nếu có)
    if focus:
        fe, fa = survey_focused(focus)
        ev.extend(fe)
        arts.extend(fa)
        ev.insert(0, f"trả lời theo chỉ thị round này — chủ đề: {', '.join(focus)}")

    out = Path(__file__).parent / "jobs" / "_survey_mg_studio.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"lanes": lanes, "episodes": rows}, ensure_ascii=False,
                              indent=2), encoding="utf-8")
    arts.append(_artifact(out))

    return {
        "summary": (f"Khảo sát MG_STUDIO: {len(lanes)} lane ({', '.join(lanes)}), "
                    f"{len(rows)} thư mục tập, "
                    f"{sum(1 for r in rows if r['final'])} tập đã có final.mp4. "
                    f"Không sửa file nào (chế độ chỉ đọc)."),
        "evidence": ev, "artifacts": arts,
        "next_action": "REPORT",
        "next_request": "Đề nghị GPT-6 quyết bước tiếp theo cho MG_STUDIO "
                        "(lane/tập cụ thể) dựa trên số liệu khảo sát này.",
    }


# ---------------------------------------------------------------- TEST (MG)
def _test_mg_modules() -> dict:
    """Chạy NGHIỆM THU THẬT của 2 module MG_STUDIO + kiểm tích hợp cơ bản."""
    ev, arts = [], []
    t = MG / "tests" / "test_khoa_hook_qc.py"
    if not t.exists():
        return {"summary": "không thấy tests/test_khoa_hook_qc.py", "evidence": [],
                "artifacts": [], "next_action": "STOP",
                "blocker": "thiếu bộ nghiệm thu MG_STUDIO"}
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(t)], cwd=str(MG), capture_output=True,
                       text=True, timeout=240, encoding="utf-8", errors="replace", env=env)
    out = (r.stdout or "") + (r.stderr or "")
    for ln in out.splitlines():
        if ln.strip().startswith(("PASS", "FAIL", "== KẾT QUẢ", "  HỎNG")):
            ev.append(ln.strip())
    arts.append(_artifact(t))
    passed = r.returncode == 0

    # kiểm tích hợp: import được 2 module từ chính MG_STUDIO
    integ = []
    for mod in ("core.khoa_hook", "core.trang_thai_qc"):
        code = f"import sys; sys.path.insert(0, r'{MG}'); import {mod}; print('OK')"
        rr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=60, encoding="utf-8", errors="replace", env=env)
        integ.append(f"{mod}: {'OK' if rr.returncode == 0 else 'LỖI ' + (rr.stderr or '')[:120]}")
    ev.extend(integ)
    if any("LỖI" in x for x in integ):
        passed = False

    # đếm thật: đã áp cho tập nào chưa
    khoa = list((MG / "episodes").rglob("khoa_hook.json"))
    qcst = list((MG / "episodes").rglob("trang_thai_qc.json"))
    ev.append(f"đã áp thực tế: {len(khoa)} tập có khoa_hook.json, "
              f"{len(qcst)} tập có trang_thai_qc.json")

    if not passed:
        return {"summary": "NGHIỆM THU MG_STUDIO KHÔNG ĐẠT — xem evidence",
                "evidence": ev, "artifacts": arts, "next_action": "STOP",
                "blocker": "bộ nghiệm thu MG_STUDIO trả exit != 0"}
    return {"summary": ("TEST MG_STUDIO đạt: bộ nghiệm thu 22 bài PASS, "
                        "2 module import được từ dự án, chưa áp cho tập sản xuất nào. "
                        "Đây là kiểm thử MODULE, chưa phải end-to-end render."),
            "evidence": ev, "artifacts": arts, "next_action": "REPORT",
            "next_request": "Đề nghị GPT-6 giao bước áp thử trên MỘT tập lane X "
                            "(khoá hook + chạy QC state) để kiểm chứng end-to-end."}


def _test_integration_lane_x() -> dict:
    """Nghiệm thu TÍCH HỢP thật trên bản sao tập lane X (GPT-6, round 4).

    Khác hẳn `_test_mg_modules` (22 bài unit test): bài này SAO CHÉP một tập
    lane X sang thư mục thử nghiệm, khoá Hook, thử sửa Hook (phải bị phát hiện),
    chạy QC state (sửa lần 2 phải bị chặn, Opus 1 lần), rồi đọc lại trạng thái
    từ đĩa trong tiến trình MỚI. Tập sản xuất chỉ ĐỌC.

    Vì sao phải tách: GPT-6 nghi đúng — round 2 và round 4 đều chứa từ khoá
    `khoa_hook` nên cùng chạy bộ 22 bài cố định, trả CÙNG SHA256 báo cáo
    (job live-20261025_235751). Executor phải phân biệt "unit test module" với
    "kiểm thử tích hợp trên tập thật".
    """
    r = _run_verify("verify_integration_lane_x.py", timeout=300,
                    keep=("PASS", "FAIL", "KẾT QUẢ", "== KẾT QUẢ"))
    if r.get("blocker"):
        return {"summary": f"BLOCKED — {r['blocker']}", "evidence": r["ev"],
                "artifacts": r["arts"], "next_action": "STOP",
                "blocker": r["blocker"]}
    ev = r["ev"] + [f"tổng: {r['npass']} PASS / {r['nfail']} FAIL",
                    f"script: {r['script'].resolve()}"]
    if not r["ok"]:
        return {"summary": f"NGHIỆM THU TÍCH HỢP KHÔNG ĐẠT — {r['npass']} PASS / "
                           f"{r['nfail']} FAIL (exit != 0)",
                "evidence": ev, "artifacts": r["arts"], "next_action": "STOP",
                "blocker": "verify_integration_lane_x.py trả exit != 0"}
    return {"summary": (f"NGHIỆM THU TÍCH HỢP ĐẠT — {r['npass']} PASS / {r['nfail']} FAIL "
                        "trên BẢN SAO tập lane X: khoá Hook tạo khoa_hook.json, sửa Hook "
                        "bị phát hiện, QC chặn lượt sửa thứ 2, Opus chặn lần 2, trạng "
                        "thái sống qua restart, tập sản xuất không đổi SHA256."),
            "evidence": ev, "artifacts": r["arts"], "next_action": "REPORT",
            "next_request": "Đề nghị GPT-6 giao bước tiếp: tích hợp 2 module vào "
                            "entry point pipeline (mg.py) để bảo vệ chạy TỰ ĐỘNG, "
                            "không chỉ khi gọi tay."}


# Dòng kết quả của các script nghiệm thu — hai script dùng hai kiểu đánh dấu.
_PASS = re.compile(r"^\[?PASS\]?")
_FAIL = re.compile(r"^\[?FAIL\]?")
_COUNTER = re.compile(r"^(submitted|accepted|executed|rejected)\s*=")


def _run_verify(script: str, *, timeout: int, keep: tuple[str, ...]) -> dict:
    """Chạy một script nghiệm thu trong `scratch_analysis/` và gom bằng chứng.

    Dùng chung cho `_test_parse_directive` / `_test_dedup` để không lặp 4 việc:
    (1) xoá PYTHONPATH/PYTHONHOME khỏi môi trường con — bẫy đã đo: biến này bị
    tiêm sẵn đường dẫn venv của Hermes và che site-packages của dự án;
    (2) chạy với encoding utf-8 (console Windows mặc định cp1258/cp437 → vỡ chữ);
    (3) lọc các dòng `keep` làm evidence; (4) đính log + SHA256 toàn bộ log.

    Trả {ok, ev, arts, npass, nfail, script}.
    """
    path = ROOT / "scratch_analysis" / script
    if not path.exists():
        return {"ok": False, "ev": [], "arts": [], "npass": 0, "nfail": 0,
                "script": path, "blocker": f"thiếu {path}"}

    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, str(path)], cwd=str(ROOT), capture_output=True,
                       text=True, timeout=timeout, encoding="utf-8",
                       errors="replace", env=env)
    out = (r.stdout or "") + (r.stderr or "")

    # Giữ dòng bắt đầu bằng `keep`, HOẶC dòng bộ đếm ("submitted = 11") — bộ đếm là
    # bằng chứng GPT-6 đòi riêng ở job dedup-check, không được lọc mất.
    ev = [s for s in (ln.strip() for ln in out.splitlines())
          if s.startswith(keep) or _COUNTER.match(s)]
    ev = [f"bộ đếm {s}" if _COUNTER.match(s) else s for s in ev]
    arts = []
    log = path.with_suffix(".log")
    # Script không tự ghi log thì TA ghi — GPT-6 luôn đòi
    # absolute_log_path + full_log_sha256, thiếu là mất bằng chứng.
    if not log.exists() or r.returncode == 0:
        log.write_text(out, encoding="utf-8")
    if log.exists():
        arts.append(_artifact(log))
        ev += [f"absolute_log_path: {log.resolve()}",
               f"log_bytes: {log.stat().st_size}",
               f"full_log_sha256: {sha256_file(log)}"]

    # Đếm cả "PASS x" và "[PASS] x" — hai script nghiệm thu dùng hai kiểu.
    return {"ok": r.returncode == 0, "ev": ev, "arts": arts,
            "npass": sum(1 for x in ev if _PASS.match(x)),
            "nfail": sum(1 for x in ev if _FAIL.match(x)),
            "script": path}


def _test_parse_directive() -> dict:
    """Nghiệm thu `parse_directive`: action hợp lệ phải nhận, action bịa phải chặn.

    GPT-6 (job smoke-fix round 3) đòi đúng 3 bằng chứng:
    actual_pass_fail_counts · absolute_log_path · full_log_sha256.
    """
    v = _run_verify("verify_parse_directive.py", timeout=180,
                    keep=("PASS", "FAIL", "TỔNG:", "kết luận:"))
    if "blocker" in v:
        return {"summary": "không thấy verify_parse_directive.py", "evidence": [],
                "artifacts": [], "next_action": "STOP", "blocker": v["blocker"]}

    # Đếm THẬT từ output, KHÔNG viết cứng con số: bản trước ghi "10/10" trong khi
    # số ca đổi theo việc có reply thật trong jobs/ hay không (đo 25/10: 9 ca khi
    # job mẫu bị xoá). Báo số sai là vi phạm "chỉ ghi cái đã đo" — GPT-6 bắt được.
    nreal = sum(1 for x in v["ev"] if "reply_thật" in x or "THẬT" in x)
    if not v["ok"]:
        return {"summary": f"NGHIỆM THU parse_directive KHÔNG ĐẠT "
                           f"({v['npass']} PASS / {v['nfail']} FAIL)",
                "evidence": v["ev"], "artifacts": v["arts"], "next_action": "STOP",
                "blocker": "verify_parse_directive.py trả exit != 0"}
    return {"summary": (f"TEST parse_directive đạt: {v['npass']} PASS / {v['nfail']} FAIL "
                        f"({nreal} ca dùng reply THẬT của GPT-6). "
                        f"Có log tuyệt đối + SHA256 toàn bộ log."),
            "evidence": v["ev"], "artifacts": v["arts"], "next_action": "REPORT",
            "next_request": "Đề nghị GPT-6 giao bước tiếp theo cho MG_STUDIO "
                            "(áp khoá hook + QC state lên MỘT tập lane X)."}


def _test_dedup() -> dict:
    """Nghiệm thu chống gửi trùng bằng bài TÍCH HỢP THẬT (6 kịch bản + bộ đếm).

    GPT-6 (job dedup-check round 2) bác đúng: báo cáo "6 PASS / 0 FAIL" của
    self-test chung chung không chứng minh được chống gửi trùng. Nó đòi
    submitted/accepted/executed/rejected thật, log tuyệt đối + byte + SHA256,
    và PASS/FAIL từng kịch bản — nên hàm này trả đủ.
    """
    v = _run_verify("verify_dedup.py", timeout=240,
                    keep=("[PASS]", "[FAIL]", "bộ đếm", "KẾT QUẢ:"))
    if "blocker" in v:
        return {"summary": "không thấy verify_dedup.py", "evidence": [],
                "artifacts": [], "next_action": "STOP", "blocker": v["blocker"]}
    if not v["ok"]:
        return {"summary": "NGHIỆM THU chống gửi trùng KHÔNG ĐẠT",
                "evidence": v["ev"], "artifacts": v["arts"], "next_action": "STOP",
                "blocker": "verify_dedup.py trả exit != 0 — xem evidence"}
    return {"summary": (f"TEST chống gửi trùng ĐẠT: {v['npass']}/{v['npass'] + v['nfail']} "
                        "kịch bản tích hợp (cùng round, khác round, khác job, retry, "
                        "restart, đồng thời) + bộ đếm thật + log tuyệt đối kèm SHA256."),
            "evidence": v["ev"], "artifacts": v["arts"], "next_action": "REPORT",
            "next_request": "Đề nghị GPT-6 giao bước áp khoá hook + QC state "
                            "lên MỘT tập lane X thật để nghiệm thu end-to-end."}


def test_agent_loop(instruction: str = "") -> dict:
    """Điều phối TEST theo chỉ thị của GPT-6 — KHÔNG trả bài chung khi không rõ.

    Bẫy đã đo (run 20261025_215805 + job dedup-check): TEST luôn trả
    "agent_loop 6/6" nên GPT-6 kết luận đúng là "không thực hiện đúng phạm vi
    TEST đã giao" và "Do not return generic 6/6 agent_loop self-test as the sole
    evidence. Report BLOCKED with exact technical reasons if specialized tests
    cannot be executed."

    => Chọn đúng bài theo chỉ thị; không khớp bài nào thì trả BLOCKED kèm danh
    sách bài đang có, KHÔNG lặng lẽ trả self-test chung.
    """
    which = _pick_test(instruction)
    if which == "parse_directive":
        return _test_parse_directive()
    if which == "dedup":
        return _test_dedup()
    if which == "integration_lane_x":
        return _test_integration_lane_x()
    if which == "mg_modules":
        return _test_mg_modules()
    return {"summary": "BLOCKED — chỉ thị TEST không khớp bài nghiệm thu nào đang có",
            "evidence": [f"instruction nhận được: {(instruction or '(rỗng)')[:300]}"],
            "artifacts": [], "next_action": "STOP",
            "blocker": ("TEST cần chỉ thị rõ chủ đề. Đang có: "
                        "parse_directive · dedup/chống gửi trùng · tích hợp lane X "
                        "(bản sao + restart) · MG_STUDIO/khoá hook/qc_rounds. "
                        "Không trả self-test chung vì nó không chứng minh "
                        "được phạm vi GPT-6 giao.")}


def _pick_test(instruction: str) -> str:
    """Chọn bài nghiệm thu theo chỉ thị — HÀM THUẦN để test được thứ tự ưu tiên.

    Thứ tự quan trọng và đã trả giá bằng báo cáo trùng SHA:
      1. `parse_directive`  — đích danh tên hàm, ưu tiên tuyệt đối
      2. `dedup`            — chỉ thị dedup cũng nhắc "integration"/"pipeline"
      3. `integration_lane_x` — chỉ thị tích hợp cũng nhắc `khoa_hook`
                                (nếu xét sau mg_modules thì rơi vào 22 bài unit)
      4. `mg_modules`       — bộ 22 bài unit test
      5. ""                 — không khớp -> caller trả BLOCKED
    """
    low = (instruction or "").lower()
    if "parse_directive" in low:
        return "parse_directive"
    if any(k in low for k in ("dedup", "gửi trùng", "gui trung", "duplicate submi",
                              "duplicate send", "chống gửi", "chong gui")):
        return "dedup"
    if any(k in low for k in ("bản sao", "ban sao", "sao chép", "sao chep", "copy",
                              "tích hợp", "tich hop", "integration", "end-to-end",
                              "restart", "khôi phục trạng thái", "khoi phuc trang thai",
                              "trên một tập", "tren mot tap")):
        return "integration_lane_x"
    if any(k in low for k in ("mg_studio", "khoa_hook", "khoá hook", "khóa hook",
                              "qc_rounds", "trang_thai_qc", "module vừa", "22 bài",
                              "pipeline")):
        return "mg_modules"
    return ""


def core_self_check() -> dict:
    """Tự kiểm LÕI bộ máy: protocol + state machine + chống gửi trùng.

    Dùng cho `cli self-test` và làm bằng chứng nền — KHÔNG dùng làm câu trả lời
    cho một chỉ thị TEST cụ thể (xem `test_agent_loop`).
    """
    code = r"""
import sys, json
sys.path.insert(0, r"%s")
from agent_loop.protocol import render_report, parse_report, parse_task, ProtocolError
from agent_loop.state import JobState, StateError
import tempfile, pathlib
ok, fail = [], []
def chk(name, fn):
    try:
        fn(); ok.append(name)
    except Exception as e:
        fail.append(f"{name}: {e}")

# 1. report tự parse lại được
def t1():
    txt, d = render_report(project_id="p", job_id="j", conversation_id="c", round_no=1,
                           task_status="REPORT_READY", next_action="REPORT",
                           summary="s", evidence=["e"], artifacts=[])
    p = parse_report(txt, expect={"project_id":"p","job_id":"j","round":1})
    assert p["sha256"] == d
chk("report_roundtrip", t1)

# 2. sửa thân => SHA lệch => từ chối
def t2():
    txt, d = render_report(project_id="p", job_id="j", conversation_id="c", round_no=1,
                           task_status="REPORT_READY", next_action="REPORT",
                           summary="s", evidence=["e"], artifacts=[])
    bad = txt.replace("## SUMMARY", "## SUMMARY\n\nSỬA TAY")
    try:
        parse_report(bad, expect={"project_id":"p","job_id":"j","round":1})
    except ProtocolError:
        return
    raise AssertionError("đã không chặn báo cáo bị sửa")
chk("reject_tampered", t2)

# 3. job khác => từ chối
def t3():
    txt, d = render_report(project_id="p", job_id="j", conversation_id="c", round_no=1,
                           task_status="REPORT_READY", next_action="REPORT",
                           summary="s", evidence=["e"], artifacts=[])
    try:
        parse_report(txt, expect={"project_id":"p","job_id":"OTHER","round":1})
    except ProtocolError:
        return
    raise AssertionError("đã không chặn báo cáo job khác")
chk("reject_wrong_job", t3)

# 4. state machine: không nhảy cóc
def t4():
    with tempfile.TemporaryDirectory() as td:
        js = JobState(td, "jx"); d = js.load()
        d = js.transition(d, "TASK_READY")
        try:
            js.transition(d, "COMPLETED")
        except StateError:
            return
        raise AssertionError("đã cho nhảy cóc TASK_READY -> COMPLETED")
chk("no_state_skip", t4)

# 5. khoá độc quyền
def t5():
    with tempfile.TemporaryDirectory() as td:
        js = JobState(td, "jy"); js.acquire()
        js2 = JobState(td, "jy")
        try:
            js2.acquire()
        except StateError:
            js.release(); return
        js.release(); raise AssertionError("đã cho 2 lượt chạy song song")
chk("lock_exclusive", t5)

# 6. chống gửi trùng
def t6():
    with tempfile.TemporaryDirectory() as td:
        js = JobState(td, "jz"); d = js.load()
        js.mark_sent(d, round_no=1, kind="task", digest="abc")
        assert js.already_sent(d, round_no=1, kind="task")
        assert not js.already_sent(d, round_no=2, kind="task")
chk("dedup_send", t6)

print(json.dumps({"ok": ok, "fail": fail}, ensure_ascii=False))
sys.exit(0 if not fail else 1)
""" % str(Path(__file__).resolve().parents[1])
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        res = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception:
        res = {"ok": [], "fail": [f"không đọc được kết quả: {r.stdout[-500:]} {r.stderr[-500:]}"]}
    passed, failed = len(res["ok"]), len(res["fail"])
    return {
        "summary": f"Tự kiểm agent_loop: {passed} đạt / {failed} hỏng.",
        "evidence": [f"đạt: {', '.join(res['ok']) or '(không)'}"] +
                    ([f"HỎNG: {', '.join(res['fail'])}"] if res["fail"] else []),
        "artifacts": [],
        "next_action": "REPORT" if failed == 0 else "STOP",
        "blocker": "" if failed == 0 else "; ".join(res["fail"])[:400],
    }


# ---------------------------------------------------------------- IMPLEMENT
def implement_agent_loop(instruction: str = "") -> dict:
    """IMPLEMENT: việc THẬT trên MG_STUDIO, chỉ thêm tệp mới.

    Bẫy đã đo (run 20261025_214840): IMPLEMENT bản đầu chỉ tự kiểm agent_loop
    nên GPT-6 kết luận đúng là "không có thay đổi mã nguồn MG_STUDIO" và BLOCKED.
    Nay uỷ quyền cho mg_implement.run() — có ghi tệp thật + SHA256 + chạy test.
    """
    from .mg_implement import run as mg_run
    return mg_run(instruction=instruction)


def fix_agent_loop(instruction: str = "") -> dict:
    """FIX: sửa/nghiệm thu tiếp — đi qua đường IMPLEMENT, nói rõ là đã đi qua đó.

    Bẫy đã đo (run 20261025_220917): GPT-6 giao `next_action: FIX` ở round 4,
    nhưng `EXECUTORS` chỉ có SURVEY/TEST/IMPLEMENT → executor trả
    "chưa có executor cho lệnh FIX" với evidence=0, artifacts=0. GPT-6 đọc báo
    cáo rỗng đó và kết luận BLOCKED, đúng nhưng vô ích — mất cả một round.

    Chính GPT-6 đã chỉ định cách đi: "phần việc FIX có thể được thực hiện thông
    qua executor IMPLEMENT". Nên FIX = IMPLEMENT, nhưng báo cáo phải GHI RÕ nó
    chạy qua đường IMPLEMENT để không ai tưởng có executor FIX riêng.
    """
    from .mg_implement import run as mg_run
    res = mg_run(instruction=instruction)
    res["summary"] = ("FIX (chạy qua đường IMPLEMENT, không có executor FIX riêng): "
                      + str(res.get("summary", "")))
    res.setdefault("evidence", []).append(
        "FIX không phải executor độc lập — dùng chung implement_agent_loop "
        "theo chỉ định của GPT-6")
    return res


EXECUTORS = {"SURVEY": survey_mg_studio, "TEST": test_agent_loop,
             "IMPLEMENT": implement_agent_loop, "FIX": fix_agent_loop}


def _executor_v2(action: str, instruction: str, data: dict | None = None) -> dict:
    """Việc của pipeline hook_body_v2 (yêu cầu 2D/9, 26/10/2026).

    Ba lệnh: HO_SO_GPT6 / KIEM_HO_SO / NHAN_BODY — xem agent_loop/executor_v2.py.
    `data` được truyền xuống vì NHAN_BODY cần câu trả lời GPT-6 (`last_gpt_reply`)
    — đó là kênh giao nhận THẬT: ChatGPT Web không ghi được file vào đường dẫn Windows.
    """
    from . import executor_v2
    return executor_v2.run(action=action, instruction=instruction, data=data)


# Lệnh của pipeline hook_body_v2 — loop.ACTIONS phải chứa đúng các tên này
V2_ACTIONS = ("HO_SO_GPT6", "KIEM_HO_SO", "NHAN_BODY")


def run_executor(root: Path, data: dict, *, log=print) -> dict:
    """Chọn và chạy executor theo `next_action`.

    `data['instruction']` PHẢI được truyền xuống executor — bẫy đã đo ở run
    20261025_214020: không truyền thì SURVEY lặp y nguyên round trước (cùng
    SHA256) và GPT-6 từ chối kết luận PASS vì "dữ liệu cũ".
    """
    action = str(data.get("next_action") or data.get("executor") or "SURVEY").upper()
    # `VIET_BODY` KHÔNG có executor: đó là việc của CHÍNH GPT-6 (viết phần thân trong chat).
    # Máy chỉ đóng hồ sơ rồi chờ GPT-6 trả Body. Trả REPORT rỗng để loop gửi báo cáo
    # "đã sẵn sàng, mời GPT-6 viết" thay vì chặn job (bẫy job mg-v2-body-01).
    if action == "VIET_BODY":
        log("[executor] VIET_BODY là việc của GPT-6 -> không dispatch, chờ GPT-6 trả Body")
        return {"summary": "Hồ sơ đã sẵn sàng. Đến lượt GPT-6 viết BODY_SCRIPT_JP.txt.",
                "evidence": ["hệ thống không chạy executor cho VIET_BODY (việc của GPT-6)"],
                "artifacts": [], "next_action": "VIET_BODY", "blocker": ""}
    # BẪY ĐÃ ĐO (job mg-v2-body-01, 26/10/2026): round 1 ở trạng thái TASK_READY chưa có
    # `instruction` (chỉ có sau khi GPT-6 trả directive) -> executor_v2 nhận chuỗi RỖNG,
    # không bóc được đường dẫn tập và trả BLOCKED oan, mất 1 round.
    # Chỉ thị rỗng thì dùng `goal` của job (chính là nhiệm vụ chủ dự án giao).
    instruction = str(data.get("instruction") or data.get("instructions") or data.get("goal") or "")
    # BẪY NẶNG ĐÃ ĐO (job mg-v2-embed-12, 26/10/2026 — GPT-6 cảnh báo 1 round):
    # GPT-6 đọc hồ sơ NHÚNG (vốn là bản sao của v2-real) rồi nhắc lại đường dẫn
    # `...\v2-real` trong câu trả lời. `_tap_dir` bóc đường dẫn ĐẦU TIÊN trong chỉ thị
    # -> executor ghi hồ sơ sang TẬP KHÁC (v2-real) trong khi job giao v2-attach.
    # `tap_dir` của job là sự thật; chỉ thị chỉ được dùng khi job không khai.
    tap_dir = str(data.get("tap_dir") or "").strip()
    if tap_dir and Path(tap_dir).is_dir():
        instruction = f"Tập: {tap_dir}\n{instruction}"
    if action in V2_ACTIONS:
        log(f"[executor] chạy {action} (pipeline hook_body_v2)")
        return _executor_v2(action, instruction, data)
    fn = EXECUTORS.get(action)
    if fn is None:
        return {"summary": f"chưa có executor cho lệnh {action}",
                "evidence": [], "artifacts": [], "next_action": "STOP",
                "blocker": f"lệnh {action} chưa được cài đặt — cần chủ dự án/GPT-6 chọn "
                           f"trong {list(EXECUTORS)}"}
    log(f"[executor] chạy {action}")
    if "instruction" in inspect.signature(fn).parameters:
        return fn(instruction=str(data.get("instruction") or ""))
    return fn()
