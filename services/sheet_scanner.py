# -*- coding: utf-8 -*-
"""sheet_scanner.py — đọc bảng YOUTUBE/tab lane, phát hiện yêu cầu viết kịch bản ở cột K.

DỮ LIỆU THẬT ĐO ĐƯỢC (2026-10-24, tab Lane-N):
    A = STT / số tập            ('17')
    B = NỘI DUNG                ('ep17-kei-campingcar-kari-1kagetsu — Gốc: ...')
    C = FOLDER (đường dẫn local) ('E:\\CLAUDE\\YOUTUBE\\h2dev\\assets\\N-senior_korekara\\ep17-...')
    K = GHI CHÚ                 nhật ký nhiều dòng, KHÔNG chứa link Drive

Ô K mẫu thật:
    Kịch bản xong- chờ dựng hook, hồ sơ tại E:\\...\\GPT6_SCRIPT_APPROVED
    Đã xong hook+ sườn- cần kịch bản
    Tab mới — làm tập tiếp theo 19/10 06:20: Làm theo
    E:\\...\\HUONG_DAN_TAP_TIEP_THEO.md

=> Dòng ĐẦU TIÊN có marker trạng thái là trạng thái HIỆN TẠI (dòng mới chèn lên đầu).
=> Link Drive lấy từ ô K nếu có; KHÔNG có thì orchestrator resolve qua 01_INBOX.

Nguyên tắc (spec Phần III):
- Đọc theo từng hàng, KHÔNG trộn thông tin giữa các tập.
- Parse bằng CODE (regex), KHÔNG để AI suy đoán ID.
- Bảo toàn row number, mã tập, file ID, folder ID.
- READ-ONLY ở V1: module này KHÔNG có hàm ghi.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from .errors import BridgeError

# ---------------------------------------------------------------- regex

RE_FILE = re.compile(
    r"https?://(?:drive|docs)\.google\.com/(?:drive/(?:u/\d+/)?)?file/d/([A-Za-z0-9_-]{10,})", re.I)
RE_FOLDER = re.compile(
    r"https?://(?:drive|docs)\.google\.com/(?:drive/(?:u/\d+/)?)?folders/([A-Za-z0-9_-]{10,})", re.I)
RE_OPEN_ID = re.compile(r"https?://drive\.google\.com/open\?id=([A-Za-z0-9_-]{10,})", re.I)
RE_LABELED = re.compile(
    r"^\s*(?P<label>[^:\n]{1,40}?)\s*[:：]\s*(?P<url>https?://\S+)", re.I | re.M)
# slug tập: ep17-kei-campingcar-kari-1kagetsu
RE_SLUG = re.compile(r"\bep(?P<ep>\d+)-(?P<slug>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)")


def _norm(s: str) -> str:
    """Chuẩn hoá để so marker: bỏ dấu tiếng Việt, lowercase, gộp khoảng trắng."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass
class ParsedRequest:
    """Một hàng cột K đã parse. Không chứa suy đoán của AI."""
    spreadsheet_id: str
    sheet_name: str
    sheet_id: int | None
    row: int
    ep_num: int | None
    episode_slug: str | None
    local_folder: str | None
    cell_raw: str
    status: str                      # need_script | script_done | ignore
    status_line: str                 # dòng quyết định trạng thái (nguyên văn)
    zip_file_id: str | None
    zip_url: str | None
    folder_id: str | None
    folder_url: str | None
    extra_notes: list[str] = field(default_factory=list)
    revision: str = ""
    row_key: str = ""

    @property
    def episode_id(self) -> str:
        return f"ep{self.ep_num}" if self.ep_num is not None else "ep?"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["episode_id"] = self.episode_id
        return d


# ---------------------------------------------------------------- parse

def _find_links(cell: str) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in RE_FILE.finditer(cell):
        if m.group(1) not in seen:
            seen.add(m.group(1)); out.append(("file", m.group(1), m.group(0)))
    for m in RE_FOLDER.finditer(cell):
        if m.group(1) not in seen:
            seen.add(m.group(1)); out.append(("folder", m.group(1), m.group(0)))
    for m in RE_OPEN_ID.finditer(cell):
        if m.group(1) not in seen:
            seen.add(m.group(1)); out.append(("file", m.group(1), m.group(0)))
    return out


def _labeled(cell: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in RE_LABELED.finditer(cell):
        out.setdefault(_norm(m.group("label")), m.group("url"))
    return out


def _status_of(cell: str, rules: dict) -> tuple[str, str]:
    """-> (status, dòng quyết định). latest_line_wins: dòng ĐẦU TIÊN có marker thắng."""
    lines = [ln.strip() for ln in str(cell).splitlines() if ln.strip()]
    need = [_norm(x) for x in rules.get("need_script", [])]
    done = [_norm(x) for x in rules.get("script_done", [])]

    def classify(line: str) -> str | None:
        n = _norm(line)
        # "chưa có kịch bản" phải thắng "có kịch bản" => xét need trước
        if any(k in n for k in need):
            return "need_script"
        if any(k in n for k in done):
            return "script_done"
        return None

    for line in lines:
        st = classify(line)
        if st:
            return st, line
    return "ignore", ""


def _ep_from_cells(col_a: str, col_b: str, col_c: str) -> tuple[int | None, str | None]:
    """Số tập + slug. Ưu tiên slug trong C (đường dẫn local) rồi B, cuối cùng A."""
    for src in (col_c, col_b):
        m = RE_SLUG.search(str(src or ""))
        if m:
            return int(m.group("ep")), f"ep{m.group('ep')}-{m.group('slug')}"
    a = str(col_a or "").strip()
    if a.isdigit():
        return int(a), None
    return None, None


def parse_cell(cell: str, *, spreadsheet_id: str, sheet_name: str, sheet_id: int | None,
               row: int, col_a: str = "", col_b: str = "", col_c: str = "",
               cfg: dict) -> ParsedRequest:
    """Parse 1 hàng thành ParsedRequest. Không đoán ID; thiếu link => orchestrator báo lỗi."""
    rules = (cfg.get("sheets") or {}).get("status_rules") or {}
    labels = (cfg.get("sheets") or {}).get("link_labels") or {}
    status, status_line = _status_of(cell, rules)

    lab = _labeled(cell)
    links = _find_links(cell)

    zip_url = folder_url = None
    for lb in labels.get("zip", []):
        if _norm(lb) in lab:
            zip_url = lab[_norm(lb)]; break
    for lb in labels.get("folder", []):
        if _norm(lb) in lab:
            folder_url = lab[_norm(lb)]; break
    if not zip_url:
        zip_url = next((u for k, _i, u in links if k == "file"), None)
    if not folder_url:
        folder_url = next((u for k, _i, u in links if k == "folder"), None)

    def _id(url: str | None, kind: str) -> str | None:
        if not url:
            return None
        if kind == "file":
            m = RE_FILE.search(url) or RE_OPEN_ID.search(url)
        else:
            m = RE_FOLDER.search(url)
        return m.group(1) if m else None

    zip_id = _id(zip_url, "file")
    folder_id = _id(folder_url, "folder")

    extra = [ln.strip() for ln in str(cell).splitlines()
             if ln.strip() and ln.strip() != status_line and not _find_links(ln)]

    ep_num, slug = _ep_from_cells(col_a, col_b, col_c)
    revision = hashlib.sha256(str(cell).encode("utf-8")).hexdigest()[:16]
    row_key = f"{spreadsheet_id}|{sheet_id}|{row}|{zip_id or '-'}|{revision}"

    return ParsedRequest(
        spreadsheet_id=spreadsheet_id, sheet_name=sheet_name, sheet_id=sheet_id, row=row,
        ep_num=ep_num, episode_slug=slug, local_folder=(str(col_c).strip() or None),
        cell_raw=str(cell), status=status, status_line=status_line,
        zip_file_id=zip_id, zip_url=zip_url, folder_id=folder_id, folder_url=folder_url,
        extra_notes=extra, revision=revision, row_key=row_key)


# ---------------------------------------------------------------- scanner

class SheetScanner:
    """Đọc read-only tab yêu cầu. KHÔNG có method ghi — V1 chỉ đọc."""

    def __init__(self, cfg: dict, project_cfg: dict):
        self.cfg = cfg
        self.pc = project_cfg
        self._svc = None

    def _api(self):
        if self._svc is None:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            sa = self.cfg["credentials"]["service_account"]
            creds = service_account.Credentials.from_service_account_file(
                sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
            self._svc = build("sheets", "v4", credentials=creds,
                              cache_discovery=False).spreadsheets()
        return self._svc

    def sheet_meta(self) -> dict:
        try:
            m = self._api().get(
                spreadsheetId=self.pc["spreadsheet_id"],
                fields="properties(title),sheets(properties(sheetId,title,index))").execute()
        except Exception as e:  # noqa: BLE001
            raise BridgeError("E_SHEET_AUTH", f"{type(e).__name__}: {e}")
        tabs = {s["properties"]["title"]: s["properties"] for s in m.get("sheets", [])}
        want = self.pc["sheet_name"]
        if want not in tabs:
            raise BridgeError("E_SHEET_READ",
                              f"Tab '{want}' không tồn tại. Có: {', '.join(sorted(tabs))[:400]}")
        return {"title": m["properties"]["title"], "tabs": tabs, "tab": tabs[want]}

    def read_rows(self) -> list[list[str]]:
        rng = f"'{self.pc['sheet_name']}'!{(self.cfg.get('sheets') or {}).get('scan_range', 'A1:M3000')}"
        try:
            res = self._api().values().get(
                spreadsheetId=self.pc["spreadsheet_id"], range=rng,
                valueRenderOption="FORMATTED_VALUE").execute()
        except Exception as e:  # noqa: BLE001
            raise BridgeError("E_SHEET_READ", f"{type(e).__name__}: {e}")
        return res.get("values", [])

    def scan(self) -> list[ParsedRequest]:
        """Quét toàn bộ tab, trả MỌI hàng có marker trạng thái (kể cả script_done để dedupe)."""
        return self._scan_rows(keep_unmarked=False)

    def scan_gate(self) -> list[ParsedRequest]:
        """Phase 5.4: trả MỌI hàng có ô K khác rỗng, kể cả hàng mà `scan()` coi là 'ignore'.

        Vì sao cần riêng: preflight phải THẤY được hàng chỉ ghi `RESEARCH OK` (chưa đủ
        điều kiện) để báo WAITING_OUTLINE. `scan()` bỏ các hàng đó nên không dùng lại được.
        Hàm này KHÔNG đổi hành vi của `scan()`/`scan_pending()` — Phase 1–4 giữ nguyên.
        """
        return self._scan_rows(keep_unmarked=True)

    def _scan_rows(self, *, keep_unmarked: bool) -> list[ParsedRequest]:
        meta = self.sheet_meta()
        rows = self.read_rows()
        cols = (self.cfg.get("sheets") or {}).get("columns") or {}
        rules = (self.cfg.get("sheets") or {}).get("status_rules") or {}

        def idx(letter: str, default: int) -> int:
            s = str(cols.get(letter) or letter).strip().upper()
            n = 0
            for ch in s:
                n = n * 26 + (ord(ch) - 64)
            return (n - 1) if n else default

        i_ep = idx("episode_number", 0)
        i_title = idx("title", 1)
        i_local = idx("local_folder", 2)
        i_req = idx("request", 10)

        def cell(row: list[str], i: int) -> str:
            return str(row[i]) if len(row) > i else ""

        out: list[ParsedRequest] = []
        for r_i, row in enumerate(rows, 1):
            raw = cell(row, i_req)
            if not raw.strip():
                continue
            st, _ = _status_of(raw, rules)
            if st == "ignore" and not keep_unmarked:
                continue
            out.append(parse_cell(
                raw, spreadsheet_id=self.pc["spreadsheet_id"], sheet_name=self.pc["sheet_name"],
                sheet_id=meta["tab"].get("sheetId"), row=r_i,
                col_a=cell(row, i_ep), col_b=cell(row, i_title), col_c=cell(row, i_local),
                cfg=self.cfg))
        return out

    def scan_pending(self) -> list[ParsedRequest]:
        return [r for r in self.scan() if r.status == "need_script"]

    def read_cell(self, row: int) -> str:
        col = str(self.pc.get("request_column", "K"))
        rng = f"'{self.pc['sheet_name']}'!{col}{row}"
        res = self._api().values().get(
            spreadsheetId=self.pc["spreadsheet_id"], range=rng,
            valueRenderOption="FORMATTED_VALUE").execute()
        v = res.get("values", [])
        return str(v[0][0]) if v and v[0] else ""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
