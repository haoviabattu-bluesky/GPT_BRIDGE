# -*- coding: utf-8 -*-
"""loop.py — Vòng điều phối GPT-6 <-> Hermes.

MỘT nhiệm vụ = nhiều round:
    gửi TASK_ASSIGNMENT / EXECUTION_REPORT -> chờ GPT trả lời -> parse -> chạy -> lặp

Điểm cốt lõi chống "báo xong khống":
- `wait_reply()` phải trả về text THẬT; lỗi truyền nhận => TRANSPORT_UNAVAILABLE,
  KHÔNG phải PASS.
- Mọi câu trả lời phải parse được thành lệnh (`next_action`) thì mới chạy tiếp.
  Không parse được => BLOCKED, báo chủ dự án.
- Tối đa `max_rounds` (chống vòng lặp vô hạn).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .protocol import ProtocolError, parse_report, render_report, sha256_text
from .send_recv import ChatSession
from .state import JobState, StateError
from .transport import ChatGPTransport, TransportError

# Lệnh GPT-6 được phép yêu cầu. Ngoài danh sách => BLOCKED (không đoán bừa).
# 26/10/2026: thêm 3 lệnh của pipeline hook_body_v2 (xem agent_loop/executor_v2.py)
# `VIET_BODY` KHÔNG phải lệnh executor: nó là việc của CHÍNH GPT-6 (viết phần thân).
# Vì vậy nó nằm trong ACTIONS để parse_directive không chặn, nhưng executor coi là
# "để GPT-6 tự làm" -> không dispatch. Thiếu nó ở đây từng làm job mg-v2-body-01
# rơi về action legacy (SURVEY/IMPLEMENT/TEST) rồi chết ở NEEDS_OWNER_ACTION.
ACTIONS = ("SURVEY", "IMPLEMENT", "TEST", "FIX", "REPORT", "DONE", "STOP",
           "HO_SO_GPT6", "KIEM_HO_SO", "NHAN_BODY", "VIET_BODY")


def parse_directive(text: str) -> dict:
    """Bóc khối lệnh trong câu trả lời của GPT-6.

    GPT-6 thường trả:
        ```loop
        next_action: IMPLEMENT
        round: 2
        task_status: TASK_READY
        ```
    Nhưng KHÔNG được phụ thuộc vào dấu ``` — đo ở job smoke-cli2: phần markdown
    lấy từ DOM **không còn dấu fence**, chỉ còn chữ "loop" và các dòng key:value.
    Vì vậy: quét TOÀN BỘ văn bản, lấy lần xuất hiện CUỐI của `next_action:`,
    chỉ nhận dòng KHÔNG thụt lề (để bỏ qua các mục con kiểu YAML lồng nhau).
    Không có => trả {"ok": False}. KHÔNG đoán.
    """
    raw = text or ""
    out: dict = {}
    lines = raw.splitlines()

    # thu thập mọi dòng key:value ở mức ngoài cùng (không thụt lề, không phải gạch đầu dòng)
    cand: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if not line or line[0].isspace():      # bỏ dòng thụt lề (mục con YAML)
            continue
        mm = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", line)
        if mm:
            cand.append((i, mm.group(1).lower(), mm.group(2).strip().strip("`").strip()))

    # chốt theo lần xuất hiện CUỐI của next_action, rồi lấy các khoá quanh nó
    idx_action = [k for k, (_, key, _) in enumerate(cand) if key == "next_action"]
    if idx_action:
        start = idx_action[-1]
        for _, key, val in cand[start:]:
            if key not in out:
                out[key] = val
        # bổ sung các khoá thiếu bằng lần xuất hiện cuối trước đó
        for _, key, val in reversed(cand[:start]):
            out.setdefault(key, val)

    action = (out.get("next_action") or "").upper()
    if action not in ACTIONS:
        return {"ok": False,
                "reason": f"không thấy next_action hợp lệ (nhận {action!r})",
                "parsed": out, "found_keys": [k for _, k, _ in cand][:20]}
    out["next_action"] = action
    out["ok"] = True
    # GIỮ LẠI PHẦN VĂN XUÔI (chỉ thị thật) — bỏ khối lệnh và nút "Show more".
    # Bẫy đã đo (run 20261025_214457): chỉ lấy khối key:value thì executor không
    # bao giờ thấy GPT hỏi gì, nên round 2/3 trả lại y nguyên round 1 (cùng SHA)
    # và GPT-6 kết luận BLOCKED vì "dữ liệu cũ".
    prose = re.sub(r"```.*?```", " ", raw, flags=re.S)
    prose = re.sub(r"^\s*Show more\s*$", " ", prose, flags=re.M)
    # BẪY ĐÃ ĐO (run 20261025_235344, GPT-6 chỉ ra: "Executor SURVEY không thực
    # hiện khảo sát theo yêu cầu"): khối lệnh `TASK_ASSIGNMENT` đặt
    # `next_action:` NGAY ĐẦU khối, TRƯỚC phần nhiệm vụ. Cắt từ dòng
    # `next_action:` (bản cũ) => vứt sạch 5 mục nhiệm vụ GPT-6 giao, executor
    # chỉ thấy dòng tiêu đề rỗng nên 3 round trả CÙNG SHA256.
    #
    # Đúng: chỉ cắt phần ĐUÔI khối lệnh (các dòng `key: value` liền nhau ở cột 0
    # theo sau dấu hiệu kết thúc), KHÔNG cắt từ `next_action` khi phía sau còn
    # văn xuôi nhiệm vụ. Nhận diện khối lệnh bằng dòng đánh dấu `loop` đứng riêng
    # hoặc bằng cụm key:value sát nhau; phần còn lại giữ nguyên làm instruction.
    cut = re.search(r"^[ \t]*loop[ \t]*$", prose, re.M)
    if cut:
        prose = prose[:cut.start()]
    else:
        # Không có dòng `loop`: cắt dải key:value LIÊN TIẾP ở cột 0 (khối lệnh),
        # giữ lại mọi đoạn văn xuôi. Chỉ cắt khi dải đó dài >= 3 dòng, để không
        # ăn mất câu văn có dấu hai chấm.
        lines_all = prose.splitlines()
        start = None
        for i, ln in enumerate(lines_all):
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*:\s*\S", ln):
                if start is None:
                    start = i
            elif start is not None:
                if i - start >= 3:
                    prose = "\n".join(lines_all[:start] + lines_all[i:])
                break
    prose = re.sub(r"\n{3,}", "\n\n", prose).strip()

    # BẪY NẶNG ĐÃ ĐO (job dedup-check, 2026-10-25, GPT-6 tự chỉ ra):
    # khối lệnh nằm SAU dòng "loop", nên bước cắt ở trên VỨT LUÔN `instructions:`
    # — tức toàn bộ yêu cầu kỹ thuật GPT-6 đặt ra. Executor chỉ thấy phần nhận
    # xét, nên 5 round liền trả cùng một báo cáo (SHA trùng) và GPT-6 kết luận
    # đúng: "task-specific instructions appear not to reach execution".
    # => phải VÉT RIÊNG khối `instructions:` (kể cả YAML block `>` / `|` nhiều
    #    dòng thụt lề) rồi NỐI vào instruction.
    blk = _extract_instructions(lines)
    if blk:
        out["instructions"] = blk
        prose = (prose + "\n\n" + blk).strip() if prose else blk

    out["instruction"] = prose[:8000]
    return out


def _extract_instructions(lines: list[str]) -> str:
    """Vét khối `instructions:` (YAML block `>` / `|` nhiều dòng thụt lề).

    Lấy lần xuất hiện CUỐI (giống cách chốt next_action) để khớp khối lệnh
    cuối cùng GPT-6 gửi, bỏ qua bản trùng phía trên.
    """
    found: list[int] = []
    for i, line in enumerate(lines):
        if re.match(r"^instructions?\s*:\s*[>|]?\s*$", line):
            found.append(i)
        else:
            # dạng một dòng: `instructions: làm việc X`
            m = re.match(r"^instructions?\s*:\s*(\S.*)$", line)
            if m and not m.group(1).startswith((">", "|")):
                found.append(i)
    if not found:
        return ""
    start = found[-1]
    head = re.match(r"^instructions?\s*:\s*(.*)$", lines[start])
    inline = (head.group(1).strip() if head else "")
    body: list[str] = []
    if inline in (">", "|", ">-", "|-", ""):
        for line in lines[start + 1:]:
            if line.strip() and not line[0].isspace():
                break                     # hết khối thụt lề
            body.append(line.strip())
    else:
        body.append(inline)
    txt = "\n".join(x for x in body if x).strip()
    return f"YÊU CẦU KỸ THUẬT CỦA GPT-6:\n{txt}" if txt else ""


class AgentLoop:
    """Chạy một job tới khi COMPLETED / BLOCKED / hết round."""

    def __init__(self, root: str | Path, job_id: str, *, conversation_url: str = "",
                 max_rounds: int = 6, stable_sec: int = 4, timeout_sec: int = 900,
                 log=print):
        self.root = Path(root)
        self.js = JobState(self.root, job_id)
        self.conversation_url = conversation_url
        self.max_rounds = max_rounds
        self.stable = stable_sec
        self.timeout = timeout_sec
        self.log = log
        self.session: ChatSession | None = None

    # ------------------------------------------------------------ transport
    def _connect(self):
        tp = ChatGPTransport(self.conversation_url, log=self.log).open()
        self.session = ChatSession(tp, stable_sec=self.stable,
                                   timeout_sec=self.timeout, log=self.log)
        return tp

    def _ensure_conversation(self, data: dict) -> None:
        """Có conversation_url thì MỞ LẠI đúng hội thoại; không thì tạo chat mới."""
        url = data.get("conversation_id") or self.conversation_url
        if url:
            try:
                self.session.tp.assert_conversation(url)
                self.log(f"[loop] dùng lại hội thoại {url}")
                return
            except TransportError as e:
                if e.code != "E_CONVERSATION_DEAD":
                    raise
                self.log(f"[loop] hội thoại cũ ĐÃ CHẾT ({e.detail}) -> mở chat mới")
                data["conversation_id"] = ""
        st = self.session.tp.new_conversation()
        self.log(f"[loop] mở chat mới, turns={st.get('turnCount')}")

    def _capture_conv_id(self, data: dict) -> None:
        url = self.session.tp.page.evaluate("() => location.href") or ""
        if "/c/" in url:
            if data.get("conversation_id") != url:
                data["conversation_id"] = url
                self.log(f"[loop] conversation_id = {url}")

    # ------------------------------------------------------------ one round
    def run(self) -> dict:
        self.js.acquire()
        data = self.js.load()
        try:
            self._connect()
            self._ensure_conversation(data)
            self._capture_conv_id(data)

            while int(data.get("round", 0)) < self.max_rounds:
                state = str(data.get("task_status")).upper()
                if state in ("COMPLETED", "BLOCKED", "NEEDS_OWNER_ACTION"):
                    break

                # --- 1. gửi thông điệp KHỞI ĐỘNG (chỉ 1 lần cho cả job)
                if state in ("TASK_READY", "CREATED"):
                    self._send_task(data)
                    # BẪY ĐÃ ĐO: chat MỚI chỉ có URL /c/<id> SAU khi gửi tin đầu.
                    # Gọi _capture_conv_id trước khi gửi thì conversation_id rỗng,
                    # và báo cáo sau đó bị từ chối vì "thiếu conversation_id".
                    self._capture_conv_id(data)
                    data = self.js.transition(data, "EXECUTING", "đã gửi yêu cầu")
                    data = self.js.checkpoint(data, "task_sent")
                elif state == "NEXT_TASK":
                    # BẪY ĐÃ ĐO (job smoke-fix, 2026-10-25): nhánh này TRƯỚC ĐÂY nằm
                    # chung với TASK_READY nên mỗi vòng lặp lại GỬI LẠI task_text của
                    # round 1. Hội thoại thật có 3 lượt y hệt nhau (round: 1) — vi phạm
                    # "chống gửi trùng" mục C và làm GPT-6 trả lời sai ngữ cảnh.
                    # NEXT_TASK nghĩa là GPT-6 VỪA giao việc mới: thực thi ngay,
                    # KHÔNG gửi lại thông điệp khởi động.
                    data = self.js.transition(
                        data, "EXECUTING", f"thực thi {data.get('next_action')}")
                    self.js.save(data)
                elif state == "REPORT_READY":
                    self._send_report(data)
                    data = self.js.transition(data, "GPT_REVIEW", "đã gửi báo cáo")
                    data = self.js.checkpoint(data, "report_sent")
                elif state == "GPT_REVIEW":
                    directive = self._await_directive(data)
                    if directive is None:
                        break
                    if directive["next_action"] in ("DONE", "STOP"):
                        data = self.js.transition(
                            data, "COMPLETED" if directive["next_action"] == "DONE"
                            else "NEEDS_OWNER_ACTION", "GPT-6 kết luận")
                        self.js.save(data)
                        break
                    data = self.js.transition(data, "NEXT_TASK", directive["next_action"])
                    data["round"] = int(data.get("round", 0)) + 1
                    data["next_action"] = directive["next_action"]
                    self.js.save(data)
                elif state == "EXECUTING":
                    # chưa có báo cáo -> chạy việc
                    data = self._execute(data)
                else:
                    self.log(f"[loop] trạng thái {state} không tự xử lý -> dừng")
                    break
        except TransportError as e:
            data = self.js.load()
            data = self.js.transition(data, "TRANSPORT_UNAVAILABLE", f"{e.code}: {e.detail}")
            self.js.save(data)
            self.log(f"[loop] LỖI TRUYỀN NHẬN {e.code}: {e.detail}")
        except (ProtocolError, StateError) as e:
            data = self.js.load()
            try:
                data = self.js.transition(data, "BLOCKED", str(e)[:300])
            except StateError:
                data["task_status"] = "BLOCKED"
            self.js.save(data)
            self.log(f"[loop] CHẶN: {e}")
        finally:
            self.js.release()
            try:
                if self.session and self.session.tp:
                    self.session.tp.close()
            except Exception:
                pass
        return self.js.load()

    # ------------------------------------------------------------ steps
    def _send_task(self, data: dict) -> None:
        text = data.get("task_text") or ""
        if not text:
            raise ProtocolError("job chưa có task_text để gửi")
        digest = sha256_text(text)
        prev = self.js.already_sent(data, round_no=int(data["round"]), kind="task")
        if prev and prev.get("sha256") == digest:
            self.log("[loop] task này đã gửi rồi -> không gửi trùng")
            return
        # BẪY NẶNG ĐÃ ĐO (job mg-v2-body-09/10, 26/10/2026 — GPT-6 chỉ ra 5 round liền):
        # hồ sơ nằm trên đĩa Windows, TASK_ASSIGNMENT chỉ ghi ĐƯỜNG DẪN. ChatGPT Web
        # KHÔNG đọc được `E:\...` -> GPT-6 trả "thiếu schema JSON cảnh / tư liệu chỉ là
        # dữ liệu thử" rồi lặp vô hạn. Cách ĐÃ CHỨNG MINH CHẠY ĐƯỢC (job 08): NHÚNG
        # NGUYÊN VĂN nội dung hồ sơ vào tin nhắn. Đính kèm tệp thì ChatGPT hay kẹt
        # nút Send (E_ATTACH_NOT_READY, job 11) nên chỉ dùng khi job khai rõ.
        attach = [Path(x) for x in (data.get("attach_files") or []) if Path(x).exists()]
        text = self._nhung_ho_so(data, text)
        r = self.session.send(text, attach=attach[0] if attach else None)
        for p in attach[1:]:
            self.session.send(f"(tệp hồ sơ: {p.name})", attach=p)
        self.js.mark_sent(data, round_no=int(data["round"]), kind="task", digest=digest)
        self.js.save(data)
        self.log(f"[loop] đã gửi task {len(text)} ký tự (+{len(attach)} tệp), "
                 f"GPT trả {r['reply']['len']} ký tự")

    def _nhung_ho_so(self, data: dict, text: str) -> str:
        """Nhúng NGUYÊN VĂN hồ sơ `body/` vào tin nhắn — ChatGPT Web không đọc `E:\\...`.

        Chỉ đọc trong thư mục tập (không gửi tệp ngoài). Schema đặt ĐẦU TIÊN vì thiếu
        nó GPT-6 chặn ngay. Không có `tap_dir` => trả nguyên văn (job cũ giữ hành vi).
        """
        tap = data.get("tap_dir") or ""
        if not tap:
            return text
        body = Path(tap) / "body"
        if not body.is_dir():
            return text
        thu_tu = ("SCHEMA_CANH.md", "GPT6_WRITING_TASK.md", "BODY_OUTLINE.md",
                  "CONTINUITY.md", "WRITING_RULES.md", "RESEARCH_FACTS.md",
                  "HO_SO_BIEN_TAP.md", "HOOK_APPROVED_JP.txt", "GPT6_REPAIR1_TASK.md")
        phan = []
        for nm in thu_tu:
            p = body / nm
            if p.exists():
                phan.append(f"\n\n===== {nm} (nguyên văn) =====\n"
                            + p.read_text(encoding="utf-8", errors="replace"))
        if not phan:
            return text
        return text + "\n\n---\n\n# HỒ SƠ ĐẦY ĐỦ (đọc kỹ trước khi viết)" + "".join(phan)

    def _send_report(self, data: dict) -> None:
        path = Path(data.get("report_path") or "")
        if not path.exists():
            raise ProtocolError(f"report_path không tồn tại: {path}")
        text = path.read_text(encoding="utf-8")
        digest = sha256_text(text)
        prev = self.js.already_sent(data, round_no=int(data["round"]), kind="report")
        if prev and prev.get("sha256") == digest:
            self.log("[loop] báo cáo này đã gửi rồi -> không gửi trùng")
            return
        r = self.session.send(text)
        self.js.mark_sent(data, round_no=int(data["round"]), kind="report", digest=digest)
        self.js.save(data)
        self.log(f"[loop] đã gửi báo cáo, GPT trả {r['reply']['len']} ký tự")

    def _await_directive(self, data: dict) -> dict | None:
        """Đọc câu trả lời của GPT-6 sau khi gửi báo cáo."""
        rep = self.session.wait_reply()
        txt = rep["text"]
        out_dir = self.js.dir / "rounds"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"r{data['round']}_gpt_reply.md").write_text(txt, encoding="utf-8")

        d = parse_directive(txt)
        if not d.get("ok"):
            data = self.js.transition(data, "BLOCKED", f"GPT trả lời không có lệnh: {d['reason']}")
            data["last_gpt_reply"] = txt[:4000]
            self.js.save(data)
            self.log(f"[loop] BLOCKED: {d['reason']}")
            return None
        data["last_directive"] = d
        # BẪY NẶNG ĐÃ ĐO (job dedup-v2, 2026-10-25 — GPT-6 chỉ ra lần 2):
        # chỉ ghi vào `last_directive` là KHÔNG ĐỦ. `run_executor` đọc
        # `data['instruction']`, nên nếu không gán ở đây thì executor luôn nhận
        # chuỗi rỗng -> rơi về nhánh mặc định -> mọi round trả cùng một báo cáo
        # self-test (SHA trùng) bất kể GPT-6 yêu cầu gì.
        data["instruction"] = d.get("instruction") or ""
        data["instructions"] = d.get("instructions") or ""
        data["last_gpt_reply"] = txt[:4000]
        self.js.save(data)
        return d

    def _execute(self, data: dict) -> dict:
        """Chạy việc local (DeepSeek/công cụ) rồi dựng EXECUTION_REPORT.md.

        Việc thật do `executor.py` cung cấp qua `data['executor']`.
        """
        from .executor import run_executor
        # NHAN_BODY cần NỘI DUNG GPT-6 viết, mà nội dung đó nằm ở lượt nó viết Body —
        # không phải lượt tổng kết cuối. Lấy nguyên văn lượt dài nhất có JSON cảnh.
        if str(data.get("next_action") or "").upper() == "NHAN_BODY":
            try:
                turn = self.session.find_turn('"canh"', 3000)
                if turn:
                    data["last_gpt_reply"] = turn["text"]
                    self.log(f"[loop] tìm thấy Body ở lượt dài {len(turn['text'])} ký tự")
            except Exception as e:      # noqa: BLE001
                self.log(f"[loop] không đọc lại được các lượt: {type(e).__name__}: {str(e)[:120]}")
            # GPT-6 có thể GIAO BẰNG TỆP trong sandbox (link `sandbox:/mnt/data/...`)
            # thay vì in JSON ra chat. Đo thật 26/10/2026 (job mg-v2-final-13): tệp
            # 99.740 byte, SHA `bdfa9c08…` khớp đúng giá trị GPT-6 tự khai. Tải tệp
            # đó về trước; nếu được thì nội dung tệp là nguồn chân lý (đầy đủ hơn
            # câu trả lời chat vốn bị thu gọn "Show more").
            from .tai_tep import tai_body_tu_sandbox
            data = tai_body_tu_sandbox(self.session.tp.page, data, log=self.log)
        result = run_executor(self.root, data, log=self.log)

        body, digest = render_report(
            project_id=data["project_id"], job_id=self.js.job_id,
            conversation_id=data.get("conversation_id", ""),
            round_no=int(data["round"]), task_status="REPORT_READY",
            next_action=result.get("next_action", "REPORT"),
            summary=result.get("summary", ""),
            evidence=result.get("evidence", []),
            artifacts=result.get("artifacts", []),
            next_request=result.get("next_request", ""),
            blocker=result.get("blocker", ""))
        rp = self.js.dir / f"EXECUTION_REPORT_r{data['round']}.md"
        rp.write_text(body, encoding="utf-8")

        # TỰ KIỂM: báo cáo vừa ghi phải parse lại được và SHA khớp
        chk = parse_report(rp.read_text(encoding="utf-8"),
                           expect={"project_id": data["project_id"],
                                   "job_id": self.js.job_id,
                                   "round": int(data["round"])})
        if chk["sha256"] != digest:
            raise ProtocolError("SHA256 báo cáo không khớp sau khi ghi")

        data["report_path"] = str(rp)
        data["report_sha256"] = digest
        data = self.js.transition(data, "REPORT_READY", f"báo cáo {rp.name}")
        self.js.save(data)
        self.log(f"[loop] xong việc -> {rp}")
        return data
