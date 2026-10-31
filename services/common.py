# -*- coding: utf-8 -*-
"""common.py — nạp cấu hình, đường dẫn, logging.

Không hardcode H2DEV: mọi thứ đi theo project key trong config.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"


def load_config(path: str | Path | None = None) -> dict:
    import yaml
    p = Path(path) if path else ROOT / "config.yaml"
    if not p.exists():
        raise FileNotFoundError(f"không thấy config: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg["_root"] = str(ROOT)
    cfg["_config_path"] = str(p)
    return cfg


def project_cfg(cfg: dict, name: str | None = None) -> dict:
    projects = cfg.get("projects") or {}
    key = name or next((k for k, v in projects.items() if (v or {}).get("enabled")), None)
    if not key or key not in projects:
        raise KeyError(f"project không có trong config: {name}")
    pc = dict(projects[key])
    pc["key"] = key
    if not pc.get("enabled", False):
        raise ValueError(f"project '{key}' đang disabled trong config")
    return pc


def project_path(cfg: dict, *parts: str) -> Path:
    p = Path(cfg.get("_root") or ROOT).joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ensure_dirs(cfg: dict) -> None:
    for d in ("jobs", "jobs/_inbox", "temp", "logs", "docs"):
        (Path(cfg.get("_root") or ROOT) / d).mkdir(parents=True, exist_ok=True)


def get_logger(cfg: dict | None = None, name: str = "bridge") -> logging.Logger:
    lg = logging.getLogger(name)
    if lg.handlers:
        return lg
    level = "INFO"
    if cfg:
        level = str((cfg.get("app") or {}).get("log_level", "INFO")).upper()
    lg.setLevel(getattr(logging, level, logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / f"{name}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    lg.addHandler(fh)
    return lg


def stop_requested(cfg: dict) -> bool:
    """Dừng an toàn: có file temp/STOP hoặc biến môi trường BRIDGE_STOP=1."""
    if os.environ.get("BRIDGE_STOP") == "1":
        return True
    f = Path(cfg.get("_root") or ROOT) / str((cfg.get("app") or {}).get("stop_file", "temp/STOP"))
    return f.exists()


def request_stop(cfg: dict) -> Path:
    f = Path(cfg.get("_root") or ROOT) / str((cfg.get("app") or {}).get("stop_file", "temp/STOP"))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return f


def clear_stop(cfg: dict) -> None:
    f = Path(cfg.get("_root") or ROOT) / str((cfg.get("app") or {}).get("stop_file", "temp/STOP"))
    if f.exists():
        f.unlink()


def heartbeat(cfg: dict, extra: dict | None = None) -> Path:
    import json
    f = Path(cfg.get("_root") or ROOT) / str((cfg.get("app") or {}).get("heartbeat_file",
                                                                       "logs/heartbeat.json"))
    f.parent.mkdir(parents=True, exist_ok=True)
    data = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pid": os.getpid()}
    if extra:
        data.update(extra)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f
