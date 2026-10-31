"""Tải tệp GPT-6 tạo trong sandbox ChatGPT về máy.

GPT-6 giao BODY bằng tệp nằm trong sandbox Python của nó
(link dạng ``sandbox:/mnt/data/BODY_SCRIPT_JP.txt``), KHÔNG phải tệp đính kèm
thường và KHÔNG in JSON ra chat. Module này lấy tệp đó về, giữ nguyên từng byte.

Ba bước (đều chạy trong page context để dùng cookie + accessToken của phiên):
  1. GET /backend-api/conversations/<cid>?num_turns=N  → tìm message_id có link sandbox
  2. GET /backend-api/conversation/<cid>/interpreter/download?message_id=..&sandbox_path=..
     → trả JSON {download_url, file_name, ...}
  3. GET download_url → nội dung tệp (base64 để giữ nguyên byte)

Kiểm chứng 26/10/2026 (job mg-v2-final-13): tải đúng 99.740 byte,
SHA-256 ``bdfa9c08…`` trùng khít giá trị GPT-6 tự khai trong chat.

KHÔNG dùng API trả phí. Chỉ đọc những gì ChatGPT đã hiển thị cho chính phiên.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

# Lấy link sandbox trong nội dung tin nhắn: sandbox:/mnt/data/<ten>
RE_SANDBOX = re.compile(r"sandbox:(/mnt/data/[^\s\)\]\"']+)")


def _js_lay_phien() -> str:
    """Đoạn JS lấy accessToken của phiên hiện tại."""
    return (
        "const s = await (await fetch('/api/auth/session',"
        " {credentials:'include'})).json();"
        "const H = {'Authorization': 'Bearer ' + s.accessToken};"
    )


def tim_message_co_tep(hoi_thoai: dict, ten_tep: str) -> list[str]:
    """Trả danh sách message_id có link sandbox trỏ tới ``ten_tep``.

    Duyệt cả ``messages`` (định dạng hiện tại) và ``mapping`` (định dạng cũ)
    để không phụ thuộc một hình dạng duy nhất.
    """
    ra: list[str] = []
    ung_vien = list(hoi_thoai.get("messages") or [])
    if not ung_vien and hoi_thoai.get("mapping"):
        ung_vien = [m.get("message") or {} for m in hoi_thoai["mapping"].values()]
    for m in ung_vien:
        if not isinstance(m, dict):
            continue
        chuoi = json.dumps(m, ensure_ascii=False)
        if ten_tep not in chuoi:
            continue
        for duong in RE_SANDBOX.findall(chuoi):
            if duong.endswith("/" + ten_tep) or duong.endswith(ten_tep):
                mid = m.get("id")
                if mid:
                    ra.append(mid)
                break
    return ra


def tai_tep_sandbox(page, cid: str, ten_tep: str, cho_ms: int = 7000) -> dict:
    """Tải tệp ``ten_tep`` từ sandbox của hội thoại ``cid``.

    ``page`` là Playwright Page (đã đăng nhập ChatGPT). Trả
    ``{ok, bytes, sha256, noi_dung, ten_tep_goc, message_id, loi}``.
    ``noi_dung`` là chuỗi UTF-8; ``sha256`` tính trên đúng byte đã tải.
    """
    kq = {"ok": False, "bytes": 0, "sha256": "", "noi_dung": "",
          "ten_tep_goc": "", "message_id": "", "loi": ""}

    # 1. Mở hội thoại thật; bắt JSON UI trả về nếu có. Endpoint trực tiếp có thể 404 trên UI mới.
    hoi_thoai = {}
    page.on("response", lambda r: hoi_thoai.update(r.json()) if "/backend-api/conversations/" in r.url and getattr(r, "status", 200) == 200 else None)
    page.goto(f"https://chatgpt.com/c/{cid}", wait_until="domcontentloaded")
    page.wait_for_timeout(cho_ms)
    if not hoi_thoai:
        try:
            hoi_thoai = page.evaluate("""async (cid) => {
              const r = await fetch(`/backend-api/conversations/${cid}?num_turns=80&include_has_versions=true`, {credentials:'include'});
              if (!r.ok) return {error:'HTTP '+r.status};
              return await r.json();
            }""", cid)
        except Exception:
            hoi_thoai = {}
    if not isinstance(hoi_thoai, dict):
        hoi_thoai = {}
    mids = tim_message_co_tep(hoi_thoai, ten_tep)
    if not mids:
        try:
            content = page.content() or ""
        except Exception:
            content = ""
    else:
        content = ""
    if not mids and "sandbox:" in content:
        try:
            mids = page.evaluate("""(name) => [...document.querySelectorAll('[data-dil-message-id]')]
              .filter(e => (e.innerText||'').includes(name))
              .map(e => e.getAttribute('data-dil-message-id')).filter(Boolean)""", ten_tep)
        except Exception:
            mids = []
    # Thu ca nguon DOM khi response API bi ghi de/khong on dinh.
    if "sandbox:" in content:
        dom_mids = page.evaluate("""(name) => [...document.querySelectorAll('[data-dil-message-id]')]
          .filter(e => (e.innerText||'').includes(name))
          .map(e => e.getAttribute('data-dil-message-id')).filter(Boolean)""", ten_tep)
        mids = list(dict.fromkeys(mids + dom_mids))
    if not mids:
        kq["loi"] = f"không thấy link sandbox cho {ten_tep} trong hội thoại"
        return kq

    # 2+3. Gọi endpoint tải rồi tải download_url — trong page context.
    js = (
        "async ([cid, mid, path]) => {" + _js_lay_phien() +
        "  const meta = await (await fetch("
        "`/backend-api/conversation/${cid}/interpreter/download"
        "?download_intent=false&message_id=${mid}&sandbox_path=${path}`,"
        " {credentials:'include', headers:H})).json();"
        "  if (!meta.download_url) return {loi: JSON.stringify(meta).slice(0,300)};"
        "  const r2 = await fetch(meta.download_url, {credentials:'include', headers:H});"
        "  if (!r2.ok) return {loi: 'tai that bai HTTP ' + r2.status};"
        "  const buf = await r2.arrayBuffer();"
        "  const h = await crypto.subtle.digest('SHA-256', buf);"
        "  const sha = [...new Uint8Array(h)]"
        "    .map(b=>b.toString(16).padStart(2,'0')).join('');"
        "  const b = new Uint8Array(buf); let bin = '';"
        "  for (let i = 0; i < b.length; i++) bin += String.fromCharCode(b[i]);"
        "  return {sha, bytes: b.length, b64: btoa(bin), name: meta.file_name || ''};"
        "}"
    )

    duong = "/mnt/data/" + ten_tep
    loi_cuoi = ""
    for mid in reversed(mids):  # lượt mới nhất trước
        d = page.evaluate(js, [cid, mid, duong])
        if d.get("loi"):
            loi_cuoi = d["loi"]
            continue
        raw = base64.b64decode(d["b64"])
        sha = hashlib.sha256(raw).hexdigest()
        if sha != d["sha"]:
            loi_cuoi = "SHA lệch giữa trình duyệt và máy"
            continue
        kq.update({
            "ok": True, "bytes": len(raw), "sha256": sha,
            "noi_dung": raw.decode("utf-8", errors="replace"),
            "ten_tep_goc": d.get("name", ""), "message_id": mid,
        })
        return kq

    kq["loi"] = loi_cuoi or "không tải được tệp"
    return kq


def tai_body_tu_sandbox(page, data: dict, log=print, ten_tep: str = "BODY_SCRIPT_JP.txt") -> dict:
    """Tải Body GPT-6 giao bằng tệp sandbox và ghi vào thư mục tập.

    Trả ``data`` (đã cập nhật). Ghi tệp ``<tap>/body/BODY_SCRIPT_JP.txt`` để
    executor ``NHAN_BODY`` đọc như nguồn ``file`` — đúng thứ tự ưu tiên sẵn có.
    Không làm gì nếu không có hội thoại / không tìm thấy tệp (để executor tự
    báo BLOCKED với nguyên nhân thật).
    """
    cid = str(data.get("conversation_id") or "")
    tap = str(data.get("tap_dir") or "")
    if not cid or not tap:
        return data
    # conversation_id có thể là URL đầy đủ; endpoint cần đúng uuid
    if "/c/" in cid:
        cid = cid.rstrip("/").split("/c/")[-1].split("?")[0]
    try:
        kq = tai_tep_sandbox(page, cid, ten_tep)
    except Exception as e:                       # noqa: BLE001
        log(f"[tai_tep] không tải được {ten_tep}: {type(e).__name__}: {str(e)[:150]}")
        return data
    if not kq["ok"]:
        log(f"[tai_tep] bỏ qua: {kq['loi'][:150]}")
        return data
    from pathlib import Path
    f = Path(tap) / "body" / ten_tep
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(kq["noi_dung"].encode("utf-8"))
    data["body_tep_tai_ve"] = {
        "path": str(f), "bytes": kq["bytes"], "sha256": kq["sha256"],
        "ten_goc": kq["ten_tep_goc"], "message_id": kq["message_id"],
    }
    log(f"[tai_tep] đã tải {ten_tep} từ sandbox GPT-6: {kq['bytes']:,d} B, "
        f"sha {kq['sha256'][:16]} -> {f}")
    return data
