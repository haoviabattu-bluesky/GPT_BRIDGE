# -*- coding: utf-8 -*-
"""zip_qc.py — kiểm tra ZIP hồ sơ (đầu vào) và ZIP kết quả (đầu ra).

BẮT BUỘC trước khi upload (spec §14):
- ZIP mở được.
- Chặn ZIP traversal + tệp nguy hiểm.
- Đủ file bắt buộc.
- SCRIPT_FINAL_JP.txt không rỗng.
- Đọc báo cáo QC.
- Đối chiếu job ID / episode ID / metadata.
- Đếm ký tự theo quy định hồ sơ.
- Kiểm tra UTF-8.
- Tạo SHA-256.
- Lưu báo cáo xác minh.

TUYÊN BỐ TRUNG THỰC: module này chỉ kiểm HÌNH THỨC + ĐỘ DÀI + HOOK + ĐỊNH DANH.
Nó KHÔNG kết luận kịch bản đạt về NỘI DUNG (mạch truyện, tiếng Nhật tự nhiên, độ chính xác
dữ kiện). Những mục đó phải qua gate_check / qc Sol của dự án.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from .errors import BridgeError

DANGEROUS_EXT = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".scr",
                 ".com", ".pif", ".msi", ".sh", ".py", ".lnk", ".hta"}
SCRIPT_DEFAULT = "SCRIPT_FINAL_JP.txt"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_chars(text: str, exclude: list[str] | None = None) -> int:
    """Đếm ký tự theo quy định hồ sơ: bỏ \\n \\r space U+3000 tab; GIỮ dấu câu và 「」."""
    excl = set(exclude or ["\n", "\r", " ", "\u3000", "\t"])
    return sum(1 for ch in text if ch not in excl)


# ---------------------------------------------------------------- an toàn

def _safe_members(zf: zipfile.ZipFile, cfg: dict) -> list[zipfile.ZipInfo]:
    """Chặn traversal, đường dẫn tuyệt đối, symlink, quá nhiều file, quá lớn."""
    qc = cfg.get("qc") or {}
    max_files = int(qc.get("max_files", 200))
    max_mb = int(qc.get("max_uncompressed_mb", 200))

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > max_files:
        raise BridgeError("E_ZIP_TOO_BIG", f"{len(infos)} file > giới hạn {max_files}")
    total = sum(i.file_size for i in infos)
    if total > max_mb * 1024 * 1024:
        raise BridgeError("E_ZIP_TOO_BIG", f"giải nén {total/1048576:.1f} MB > {max_mb} MB")

    for i in infos:
        name = i.filename.replace("\\", "/")
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", name):
            raise BridgeError("E_ZIP_TRAVERSAL", f"đường dẫn nguy hiểm: {i.filename}")
        mode = (i.external_attr >> 16) & 0o170000
        if mode == 0o120000:                                   # symlink
            raise BridgeError("E_ZIP_TRAVERSAL", f"symlink trong ZIP: {i.filename}")
        if p.suffix.lower() in DANGEROUS_EXT:
            raise BridgeError("E_ZIP_TRAVERSAL", f"tệp nguy hiểm: {i.filename}")
    return infos


def _read_text(zf: zipfile.ZipFile, info: zipfile.ZipInfo, code: str) -> str:
    try:
        raw = zf.read(info)
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise BridgeError(code, f"{info.filename} không phải UTF-8: {e}")


def _basename_map(infos: list[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    """{tên file không kèm thư mục: ZipInfo}. Hồ sơ thật bọc trong 1 folder con."""
    out: dict[str, zipfile.ZipInfo] = {}
    for i in infos:
        out.setdefault(PurePosixPath(i.filename.replace("\\", "/")).name, i)
    return out


# ---------------------------------------------------------------- hồ sơ vào

def inspect_handoff(zip_path: Path, cfg: dict) -> dict:
    """Đọc ZIP hồ sơ: manifest, 07, 01, 08, danh sách file. Không giải nén ra đĩa."""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise BridgeError("E_ZIP_CORRUPT", f"không thấy file: {zip_path}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BridgeError("E_ZIP_CORRUPT", str(e))
    with zf:
        infos = _safe_members(zf, cfg)
        by = _basename_map(infos)

        man_info = by.get("09_HANDOFF_MANIFEST.json")
        if not man_info:
            raise BridgeError("E_MANIFEST_MISSING", f"có: {', '.join(sorted(by))[:400]}")
        try:
            manifest = json.loads(_read_text(zf, man_info, "E_MANIFEST_MISSING"))
        except json.JSONDecodeError as e:
            raise BridgeError("E_MANIFEST_MISSING", f"manifest không phải JSON: {e}")

        texts: dict[str, str] = {}
        for name in ("07_WRITING_TASK.md", "01_HOOK_OUTLINE.txt", "08_QC_CHECKLIST.md",
                     "10_GPT6_START_HERE.md", "HANDOFF_ISSUES.md", "05_CONTINUITY.md",
                     "02_SCRIPT_RULES.md", "03_RESEARCH_FACTS.md", "04_CHANNEL_PROFILE.md",
                     "06_REFERENCE_SCRIPT.txt"):
            if name in by:
                texts[name] = _read_text(zf, by[name], "E_HANDOFF_INCOMPLETE")

        missing = [n for n in (manifest.get("required_files") or []) if n not in by]
        if missing:
            raise BridgeError("E_HANDOFF_INCOMPLETE", f"thiếu: {', '.join(missing)}")
        for n in ("01_HOOK_OUTLINE.txt", "02_SCRIPT_RULES.md", "03_RESEARCH_FACTS.md",
                  "05_CONTINUITY.md", "07_WRITING_TASK.md"):
            if n not in by:
                raise BridgeError("E_HANDOFF_INCOMPLETE", f"thiếu {n}")

        # Xác minh checksum từng file (manifest ghi sha256) — chỉ cảnh báo, không chặn
        per = manifest.get("per_file_checksums") or {}
        bad = []
        for name, want in per.items():
            if name in by:
                got = hashlib.sha256(zf.read(by[name])).hexdigest()
                if got != want:
                    bad.append(name)

        return {
            "zip_path": str(zip_path),
            "sha256": sha256_file(zip_path),
            "size": zip_path.stat().st_size,
            "members": [i.filename for i in infos],
            "files": sorted(by),
            "manifest": manifest,
            "texts": texts,
            "checksum_mismatch": bad,
            "root_prefix": str(PurePosixPath(infos[0].filename.replace("\\", "/")).parent) if infos else "",
        }


# ---------------------------------------------------------------- kết quả ra

def inspect_result(zip_path: Path, cfg: dict, job: dict, handoff: dict | None = None) -> dict:
    """Kiểm ZIP kết quả. Trả report; raise BridgeError khi FAIL.

    job     : bản ghi job (job_id, episode_id, lane)
    handoff : {'budget': {...}, 'hook_text': '...'} — ngân sách + hook khoá để đối chiếu
    """
    qc = cfg.get("qc") or {}
    required = qc.get("required_files") or ["SCRIPT_FINAL_JP.txt", "SCRIPT_QC_REPORT.md",
                                            "SCRIPT_ISSUES.md", "SCRIPT_STRUCTURE.md"]
    script_name = qc.get("script_file") or SCRIPT_DEFAULT
    exclude = qc.get("char_count_exclude")

    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise BridgeError("E_RESULT_MISSING", f"không thấy file: {zip_path}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BridgeError("E_ZIP_CORRUPT", str(e))

    report: dict = {"zip_path": str(zip_path), "checks": {}, "job_id": job.get("job_id"),
                    "episode_id": job.get("episode_id")}
    with zf:
        infos = _safe_members(zf, cfg)
        by = _basename_map(infos)
        report["sha256"] = sha256_file(zip_path)
        report["size"] = zip_path.stat().st_size
        report["members"] = [i.filename for i in infos]

        # 1) đủ file
        missing = [n for n in required if n not in by]
        report["checks"]["required_files"] = {"ok": not missing, "missing": missing}
        if missing:
            raise BridgeError("E_RESULT_MISSING_FILES", f"thiếu: {', '.join(missing)}")

        # 2) script đọc được + UTF-8 + không rỗng
        script = _read_text(zf, by[script_name], "E_RESULT_ENCODING")
        chars = count_chars(script, exclude)
        report["chars"] = chars
        report["checks"]["script_utf8"] = {"ok": True}
        report["checks"]["script_nonempty"] = {"ok": chars > 0, "chars": chars}
        if chars <= 0:
            raise BridgeError("E_RESULT_EMPTY_SCRIPT", "0 ký tự")

        # 3) báo cáo QC đọc được
        qc_text = _read_text(zf, by.get("SCRIPT_QC_REPORT.md", by[script_name]), "E_RESULT_ENCODING")
        issues_text = ""
        if "SCRIPT_ISSUES.md" in by:
            issues_text = _read_text(zf, by["SCRIPT_ISSUES.md"], "E_RESULT_ENCODING")
        structure_text = ""
        if "SCRIPT_STRUCTURE.md" in by:
            structure_text = _read_text(zf, by["SCRIPT_STRUCTURE.md"], "E_RESULT_ENCODING")
        report["checks"]["qc_report_present"] = {"ok": "SCRIPT_QC_REPORT.md" in by}

        # 4) định danh job/episode trong ZIP (nếu có khai)
        hay = "\n".join([qc_text, issues_text, structure_text])[:20000]
        want_job = str(job.get("job_id") or "")
        want_ep = str(job.get("episode_id") or "")
        found_job = want_job and want_job.lower() in hay.lower()
        found_ep = want_ep and re.search(rf"\b{re.escape(want_ep)}\b", hay, re.I) is not None
        report["checks"]["identity"] = {"ok": bool(found_job or found_ep),
                                        "job_id_found": bool(found_job),
                                        "episode_found": bool(found_ep),
                                        "want_job_id": want_job, "want_episode": want_ep}
        # chỉ chặn khi ZIP khai RÕ một job KHÁC
        other = re.findall(r"\b([A-Z]{1,3}-ep\d{1,4})\b", hay, re.I)
        others = {o.upper() for o in other} - {want_ep.upper()}
        if want_ep and others and not found_ep:
            raise BridgeError("E_RESULT_JOB_MISMATCH",
                              f"ZIP khai tập {sorted(others)} nhưng job là {want_ep}")

        # 5) độ dài theo ngân sách hồ sơ
        budget = (handoff or {}).get("budget") or {}
        bmin, bmax = int(budget.get("min") or 0), int(budget.get("max") or 0)
        if bmin and bmax:
            ok = bmin <= chars <= bmax
            report["checks"]["length"] = {"ok": ok, "chars": chars, "min": bmin,
                                          "target": budget.get("target"), "max": bmax}
            if not ok:
                raise BridgeError("E_RESULT_LENGTH", f"{chars} ngoài [{bmin}, {bmax}]")
        else:
            report["checks"]["length"] = {"ok": None, "chars": chars,
                                          "note": "hồ sơ không khai ngân sách — không chặn"}

        # 6) hook nguyên văn
        hook_text = ((handoff or {}).get("hook_text") or "").strip()
        if (qc.get("require_hook_verbatim") and hook_text):
            def norm(s: str) -> str:
                return re.sub(r"[\s\u3000]+", "", s)
            head = norm(script)[:len(norm(hook_text)) + 40]
            ok = norm(hook_text) in head
            report["checks"]["hook_verbatim"] = {"ok": ok,
                                                 "hook_chars": count_chars(hook_text, exclude)}
            if not ok:
                raise BridgeError("E_RESULT_HOOK_CHANGED",
                                  "không thấy nguyên văn hook ở đầu " + script_name)
        else:
            report["checks"]["hook_verbatim"] = {"ok": None, "note": "không đối chiếu"}

        # 7) QC report có nội dung
        report["checks"]["qc_report_content"] = {"ok": len(qc_text.strip()) > 50,
                                                 "chars": len(qc_text)}
        report["qc_report_excerpt"] = qc_text[:800]
        report["issues_excerpt"] = issues_text[:800]
        report["structure_excerpt"] = structure_text[:800]

    report["verdict"] = "PASS_FORMAT_ONLY"
    report["honesty_note"] = ("Chỉ kiểm HÌNH THỨC/ĐỘ DÀI/HOOK/ĐỊNH DANH. "
                              "KHÔNG kết luận đạt về nội dung — phải qua gate_check + qc Sol của dự án.")
    return report


def write_report(report: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
