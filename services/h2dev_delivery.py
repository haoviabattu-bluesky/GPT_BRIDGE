# -*- coding: utf-8 -*-
"""h2dev_delivery.py — Phase 5.3: adapter đầu ra RIÊNG để bàn giao kịch bản cho H2DEV.

KHÔNG sửa H2DEV. Module này chỉ ĐỌC quy ước của H2DEV và ghi vào **sandbox** của
GPT_BRIDGE; muốn ghi vào vùng sản xuất phải được phê duyệt tường minh.

Quy ước H2DEV đã kiểm chứng bằng mã nguồn thật (`E:/CLAUDE/YOUTUBE/h2dev/pipeline/gate_common.py`):

* kịch bản của một tập nằm ở `<slug_dir>/script.txt` (UTF-8)
* `gate_common.ghi_lock(slug_dir, state, **info)` ghi `<slug_dir>/script.lock.json`
  kèm `script_sha256 = sha256_file(script.txt)` (đã chuẩn hoá CRLF→LF)
* `gate_common.kiem_lock(slug_dir)` -> `(ok, msg, level)`; level `FAIL` khi thiếu/`state≠PASS`/sha lệch
  (`FINAL_QC_STALE`)

Do đó adapter làm 4 việc:

1. `prepare()`  — giải nén ZIP đã QC, chép `SCRIPT_FINAL_JP.txt` thành `script.txt`
   theo quy ước H2DEV, viết `HANDOFF_DELIVERY_MANIFEST.json` (job ID, lane, tập,
   SHA-256 kịch bản, SHA-256 ZIP, Drive file ID).
2. `compat_probe()` — chạy THẬT `ghi_lock()` + `kiem_lock()` trong sandbox để chứng minh
   kịch bản này tương thích cổng khoá của H2DEV, RỒI XOÁ lock dò đi.
3. `verify()` — kiểm gói bàn giao đủ/hợp lệ.
4. Chặn ghi ra ngoài sandbox (`E_H2DEV_WRITE_DENIED`).

**Trung thực về cổng khoá:** `script.lock.json` nghĩa là "Sol đã chấm PASS kịch bản".
Kịch bản GPT6 trong Phase 5.3 CHƯA qua Sol, nên adapter **cố ý KHÔNG để lại lock PASS**;
`compat_probe()` chỉ dò khả năng tương thích rồi dọn sạch. Việc chạy Sol để ký lock
vẫn thuộc H2DEV và phải do chủ dự án kích hoạt.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import time
import zipfile
from pathlib import Path

from .errors import BridgeError
from .zip_qc import _safe_members, sha256_file

SCRIPT_NAME = "script.txt"
LANG_SCRIPT = "SCRIPT_FINAL_JP.txt"
REF_FILES = ("SCRIPT_QC_REPORT.md", "SCRIPT_ISSUES.md", "SCRIPT_STRUCTURE.md")
MANIFEST_NAME = "HANDOFF_DELIVERY_MANIFEST.json"
EXTRACT_DIR = "_delivery_src"
LOCK_NAME = "script.lock.json"

H2DEV_ROOT_MAC_DINH = "E:/CLAUDE/YOUTUBE/h2dev"


def sha256_text_normalized(path: Path) -> str:
    """SHA-256 đã chuẩn hoá CRLF→LF — CÙNG thuật toán `gate_common.sha256_file`."""
    data = Path(path).read_bytes()
    import hashlib
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


class H2DevDelivery:
    """Cầu nối kết quả GPT6 → quy ước thư mục H2DEV, chỉ ghi trong sandbox."""

    def __init__(self, cfg: dict, *, root: str | Path | None = None,
                 h2dev_root: str | Path | None = None, logger=None):
        self.cfg = cfg
        self.root = Path(root or cfg.get("_root") or ".")
        dcfg = dict(cfg.get("delivery") or {})
        self.dcfg = dcfg
        self.log = logger
        self.sandbox_root = Path(self._abs(dcfg.get("sandbox_dir") or "jobs/_delivery"))
        hr = h2dev_root or dcfg.get("h2dev_root") or H2DEV_ROOT_MAC_DINH
        self.h2dev_root = Path(hr) if hr else None
        pr = dcfg.get("production_root")
        self.production_root = Path(pr) if pr else (
            (self.h2dev_root / "assets") if self.h2dev_root else None)
        self.allow_production = bool(dcfg.get("allow_production_write"))
        self.script_lock_probe = bool(dcfg.get("script_lock_probe", True))
        self._gate = None
        self._gate_error = ""

    def _abs(self, p) -> str:
        q = Path(p)
        return str(q if q.is_absolute() else self.root / q)

    # ------------------------------------------------------------------ cổng ghi
    def _assert_writable(self, dest: Path) -> str:
        d = Path(dest).resolve()
        sb = self.sandbox_root.resolve()
        if d == sb or sb in d.parents:
            return "sandbox"
        pr = self.production_root.resolve() if self.production_root else None
        if pr and (d == pr or pr in d.parents):
            if not self.allow_production:
                raise BridgeError(
                    "E_H2DEV_WRITE_DENIED",
                    f"{d} thuộc vùng SẢN XUẤT H2DEV nhưng delivery.allow_production_write=false")
            return "production"
        raise BridgeError(
            "E_H2DEV_WRITE_DENIED",
            f"{d} nằm ngoài sandbox {sb} và ngoài vùng sản xuất được phê duyệt")

    # ------------------------------------------------------------------ gate_common
    def gate_module(self):
        """Nạp `gate_common` của H2DEV theo ĐƯỜNG DẪN file (không nhập vào sys.path toàn cục)."""
        if self._gate is not None:
            return self._gate
        if not self.h2dev_root:
            raise BridgeError("E_H2DEV_PACKAGE_INVALID", "chưa cấu hình delivery.h2dev_root")
        p = Path(self.h2dev_root) / "pipeline" / "gate_common.py"
        if not p.exists():
            raise BridgeError("E_H2DEV_PACKAGE_INVALID", f"không thấy {p}")
        try:
            spec = importlib.util.spec_from_file_location("h2dev_gate_common", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001 — báo rõ, không đoán
            self._gate_error = f"{type(e).__name__}: {e}"
            raise BridgeError("E_H2DEV_PACKAGE_INVALID",
                              f"không nạp được gate_common của H2DEV: {self._gate_error}")
        self._gate = mod
        return mod

    def compat_probe(self, slug_dir: Path, *, cleanup: bool = True) -> dict:
        """Chứng minh `script.txt` tương thích ghi_lock/kiem_lock, KHÔNG để lại lock PASS giả."""
        slug_dir = Path(slug_dir)
        script = slug_dir / SCRIPT_NAME
        if not script.exists():
            raise BridgeError("E_H2DEV_PACKAGE_INVALID", f"thiếu {script}")
        lock = slug_dir / LOCK_NAME
        pre_existing = lock.exists()
        mod = self.gate_module()
        out: dict = {"gate_module": str(getattr(mod, "__file__", "")),
                     "pre_existing_lock": pre_existing, "probe": False}
        if pre_existing:
            # Đã có lock thật (do H2DEV ký) -> chỉ ĐỌC, tuyệt đối không ghi đè.
            ok, msg, level = mod.kiem_lock(slug_dir)
            out.update(verify_ok=bool(ok), verify_msg=msg, verify_level=level,
                       note="đã có script.lock.json của H2DEV — chỉ đọc, không ghi đè")
            return out
        meta = mod.ghi_lock(slug_dir, "PASS", source="phase53_compat_probe",
                            probe=True, job_id=None)
        ok1, msg1, lv1 = mod.kiem_lock(slug_dir)
        mod.ghi_lock(slug_dir, "BLOCK", source="phase53_compat_probe", probe=True)
        ok2, msg2, lv2 = mod.kiem_lock(slug_dir)
        if cleanup and lock.exists():
            lock.unlink()
        out.update(probe=True, script_sha256_lock=meta.get("script_sha256"),
                   verify_ok=bool(ok1), verify_msg=msg1, verify_level=lv1,
                   mismatch_detected=(ok2 is False and lv2 == "FAIL"), mismatch_msg=msg2,
                   lock_removed=bool(cleanup and not lock.exists()),
                   note="lock dò đã được xoá — kịch bản chưa qua Sol nên KHÔNG giữ lock PASS")
        return out

    # ------------------------------------------------------------------ bàn giao
    def prepare(self, zip_path: Path, job: dict, *, slug: str | None = None,
                dest_root: str | Path | None = None, drive: dict | None = None,
                probe: bool | None = None) -> dict:
        """Dựng gói bàn giao H2DEV trong sandbox. Trả manifest (đã ghi ra đĩa)."""
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise BridgeError("E_H2DEV_PACKAGE_INVALID", f"không thấy ZIP: {zip_path}")
        slug = slug or suggest_slug(job)
        base = Path(dest_root) if dest_root else self.sandbox_root
        where = self._assert_writable(base)
        slug_dir = Path(base).resolve() / slug
        slug_dir.mkdir(parents=True, exist_ok=True)

        # 1) giải nén AN TOÀN
        src = slug_dir / EXTRACT_DIR
        if src.exists():
            shutil.rmtree(src)
        src.mkdir(parents=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                infos = _safe_members(zf, self.cfg)
                for i in infos:
                    name = Path(i.filename.replace("\\", "/")).name
                    if not name:
                        continue
                    with zf.open(i) as fsrc, open(src / name, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
        except zipfile.BadZipFile as e:
            raise BridgeError("E_ZIP_CORRUPT", str(e)) from e

        # 2) lấy SCRIPT_FINAL_JP.txt -> script.txt theo quy ước H2DEV
        scripts = list(src.glob(LANG_SCRIPT))
        if not scripts:
            raise BridgeError("E_H2DEV_PACKAGE_INVALID", f"ZIP không có {LANG_SCRIPT}")
        raw = scripts[0].read_bytes()
        text = raw.decode("utf-8")            # UTF-8 bắt buộc (đã QC)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text.endswith("\n"):
            text += "\n"
        script = slug_dir / SCRIPT_NAME
        script.write_bytes(text.encode("utf-8"))
        #
        # KHÔNG ghi script.txt vào thư mục H2DEV production khi chưa được phê duyệt:
        # `_assert_writable()` đã chặn ở trên; ở đây ghi trong sandbox (hoặc
        # production chỉ khi allow_production_write=true).

        # 3) manifest bàn giao
        drive = dict(drive or {})
        manifest = {
            "schema": "gpt_bridge.h2dev_delivery/1",
            "job_id": job.get("job_id"),
            "lane": job.get("lane"),
            "episode": job.get("episode_id"),
            "project": job.get("project"),
            "job_class": job.get("job_class"),
            "slug": slug,
            "slug_dir": str(slug_dir),
            "write_scope": where,
            "script": {"file": SCRIPT_NAME, "chars": _chars(text),
                       "sha256": sha256_file(script),
                       "sha256_gate_normalized": sha256_text_normalized(script),
                       "source_file": LANG_SCRIPT},
            "zip": {"name": zip_path.name, "sha256": sha256_file(zip_path),
                    "size": zip_path.stat().st_size},
            "drive": {"file_id": drive.get("file_id"), "name": drive.get("name"),
                      "sha256": drive.get("sha256"), "url": drive.get("url"),
                      "dest_kind": drive.get("dest_kind"),
                      "folder_id": drive.get("folder_id")},
            "reference_files": [p.name for p in src.iterdir() if p.is_file()],
            "produced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "honesty": ("Gói này dựng từ ZIP do ChatGPT/GPT6 xuất và đã qua QC HÌNH THỨC của "
                        "GPT_BRIDGE. CHƯA qua QC nội dung/Sol của H2DEV; KHÔNG kèm script.lock.json."),
        }
        if probe is None:
            probe = self.script_lock_probe
        if probe:
            try:
                manifest["gate_probe"] = self.compat_probe(slug_dir)
            except BridgeError as e:
                manifest["gate_probe"] = {"ok": False, "error": e.code, "detail": e.detail}
        mp = slug_dir / MANIFEST_NAME
        mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["manifest_path"] = str(mp)
        return manifest

    def verify(self, slug_dir: Path) -> dict:
        """Kiểm gói bàn giao tại chỗ: đủ file, sha khớp manifest, không có lock giả."""
        slug_dir = Path(slug_dir)
        script = slug_dir / SCRIPT_NAME
        mp = slug_dir / MANIFEST_NAME
        probs: list[str] = []
        if not script.exists():
            probs.append(f"thiếu {SCRIPT_NAME}")
        if not mp.exists():
            probs.append(f"thiếu {MANIFEST_NAME}")
            return {"ok": False, "problems": probs, "slug_dir": str(slug_dir)}
        man = json.loads(mp.read_text(encoding="utf-8"))
        want = (man.get("script") or {}).get("sha256")
        got = sha256_file(script) if script.exists() else None
        if want and got != want:
            probs.append(f"SHA-256 script.txt lệch ({str(got)[:12]} ≠ {str(want)[:12]})")
        lock = slug_dir / LOCK_NAME
        if lock.exists():
            probs.append("có script.lock.json — kịch bản chưa qua Sol, không được kèm lock")
        return {"ok": not probs, "problems": probs, "slug_dir": str(slug_dir),
                "manifest": man, "script_sha256": got}


def _chars(text: str) -> int:
    from .zip_qc import count_chars
    return count_chars(text)


def suggest_slug(job: dict) -> str:
    """Slug thư mục tập theo kiểu H2DEV: khai sẵn thì dùng, không thì `epNN`."""
    s = str(job.get("episode_slug") or "").strip()
    if s:
        return s
    ep = job.get("episode_id") or ""
    m = __import__("re").search(r"(\d+)", str(ep))
    return f"ep{int(m.group(1)):02d}" if m else str(job.get("job_id") or "unknown")


def build(cfg: dict, **kw) -> H2DevDelivery:
    return H2DevDelivery(cfg, **kw)