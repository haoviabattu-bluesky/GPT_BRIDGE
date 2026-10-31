# -*- coding: utf-8 -*-
"""tests/test_agent_loop.py — nghiệm thu pytest cho `agent_loop/` (offline).

Không chạm mạng, không mở Chrome. Chỉ kiểm những bất biến đã hứa trong
AGENT_LOOP_ARCHITECTURE.md §4 + các bẫy đã đo ở AGENT_LOOP_TEST_REPORT.md.

Chạy:  py -3.13 -m pytest tests/test_agent_loop.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_loop.loop import ACTIONS, parse_directive
from agent_loop.protocol import (ProtocolError, parse_report, parse_task,
                                 render_report, render_task, sha256_text,
                                 split_message)
from agent_loop.state import ALLOWED, JobState, StateError

# ============================================================ protocol: task
TASK_OK = """# TASK_ASSIGNMENT

project_id: MG_STUDIO
job_id: j-01
conversation_id: https://chatgpt.com/c/abc
round: 2
task_status: TASK_READY
next_action: SURVEY

---

## GOAL

Khảo sát MG_STUDIO.
"""


def test_parse_task_happy():
    t = parse_task(TASK_OK)
    assert t["project_id"] == "MG_STUDIO"
    assert t["round"] == 2                    # ép int, không phải "2"
    assert t["task_status"] == "TASK_READY"
    assert "Khảo sát" in t["body"]


@pytest.mark.parametrize("field", ["project_id", "job_id", "round", "task_status"])
def test_parse_task_thieu_truong(field):
    raw = "\n".join(ln for ln in TASK_OK.splitlines()
                    if not ln.lower().startswith(field + ":"))
    with pytest.raises(ProtocolError, match="thiếu trường"):
        parse_task(raw)


def test_parse_task_round_khong_phai_so():
    with pytest.raises(ProtocolError, match="round không phải số"):
        parse_task(TASK_OK.replace("round: 2", "round: hai"))


def test_parse_task_status_la():
    with pytest.raises(ProtocolError, match="task_status lạ"):
        parse_task(TASK_OK.replace("task_status: TASK_READY", "task_status: DANG_CHAY"))


def test_split_message_chap_nhan_thieu_dau_gach():
    """GPT quên dòng --- => header ăn hết, thân rỗng. Không được nổ."""
    head, body = split_message("project_id: p\njob_id: j\nround: 1\ntask_status: CREATED")
    assert head["job_id"] == "j" and body == ""


# ========================================================== protocol: report
def _report(**kw):
    args = dict(project_id="MG_STUDIO", job_id="j-01", conversation_id="https://c/1",
                round_no=1, task_status="REPORT_READY", next_action="TEST",
                summary="đã khảo sát", evidence=["e1", "e2"],
                artifacts=[{"path": r"E:\x.json", "bytes": 3, "sha256": "a" * 64}])
    args.update(kw)
    return render_report(**args)


def test_report_roundtrip_sha_khop():
    body, digest = _report()
    p = parse_report(body, expect={"project_id": "MG_STUDIO", "job_id": "j-01", "round": 1})
    assert p["sha256"] == digest
    assert p["round"] == 1 and p["next_action"] == "TEST"


def test_report_bi_sua_thi_tu_choi():
    """Bất biến: sửa 1 ký tự trong thân => SHA lệch => TỪ CHỐI."""
    body, _ = _report()
    tampered = body.replace("đã khảo sát", "đã khảo sát XONG")
    with pytest.raises(ProtocolError, match="report_sha256 không khớp"):
        parse_report(tampered)


@pytest.mark.parametrize("field,val", [("project_id", "H2DEV"), ("job_id", "j-99")])
def test_report_cua_job_khac_thi_tu_choi(field, val):
    """Bất biến (mục F): báo cáo job khác không được gán nhầm."""
    body, _ = _report()
    expect = {"project_id": "MG_STUDIO", "job_id": "j-01", "round": 1}
    expect[field] = val                      # đúng MỘT trường lệch
    with pytest.raises(ProtocolError, match="không khớp"):
        parse_report(body, expect=expect)


def test_report_sai_round_thi_tu_choi():
    body, _ = _report(round_no=2)
    with pytest.raises(ProtocolError, match="round không khớp"):
        parse_report(body, expect={"project_id": "MG_STUDIO", "job_id": "j-01", "round": 1})


@pytest.mark.parametrize("field", ["conversation_id", "report_sha256"])
def test_report_thieu_truong(field):
    body, _ = _report()
    raw = "\n".join(ln for ln in body.splitlines()
                    if not ln.lower().startswith(field + ":"))
    with pytest.raises(ProtocolError, match="thiếu trường"):
        parse_report(raw)


def test_report_khong_co_digest_thi_van_parse():
    """Thiếu report_sha256 là thiếu trường bắt buộc, nhưng digest rỗng thì không so."""
    body, _ = _report()
    head, _body = split_message(body)
    assert head["report_sha256"] == sha256_text(_body.strip())


# ============================================================== state machine
@pytest.fixture
def job(tmp_path):
    j = JobState(tmp_path, "j-01")
    yield j
    j.release()


def test_job_id_khong_hop_le(tmp_path):
    with pytest.raises(ValueError, match="job_id không hợp lệ"):
        JobState(tmp_path, "../etc/passwd")


def test_state_mac_dinh_la_created(job):
    d = job.load()
    assert d["task_status"] == "CREATED" and d["round"] == 0
    assert d["history"] == [] and d["sent"] == []


def test_nhay_trang_thai_bi_chan(job):
    """CREATED -> EXECUTING là nhảy cóc, phải chặn."""
    d = job.load()
    with pytest.raises(StateError, match="không được nhảy"):
        job.transition(d, "EXECUTING")


def test_chuoi_trang_thai_hop_le(job):
    d = job.load()
    for s in ("TASK_READY", "EXECUTING", "REPORT_READY", "GPT_REVIEW", "NEXT_TASK"):
        d = job.transition(d, s)
    assert d["task_status"] == "NEXT_TASK"
    assert [h["to"] for h in d["history"]] == ["TASK_READY", "EXECUTING",
                                               "REPORT_READY", "GPT_REVIEW", "NEXT_TASK"]


def test_trang_thai_cuoi_khong_doi_duoc(job):
    d = job.transition(job.load(), "BLOCKED")
    with pytest.raises(StateError, match="trạng thái cuối"):
        job.transition(d, "EXECUTING")


def test_moi_trang_thai_trong_allowed_deu_hop_le():
    from agent_loop.protocol import TASK_STATES
    for src, dsts in ALLOWED.items():
        assert src in TASK_STATES
        assert dsts <= set(TASK_STATES), f"{src} trỏ tới trạng thái lạ"


def test_transport_unavailable_tu_moi_trang_thai_chua_ket_thuc():
    """Bất biến (mục C): lỗi truyền nhận phải ghi nhận được từ BẤT KỲ trạng thái nào.

    Bẫy thật (job smoke-fix, 2026-10-25): send() hỏng lúc đang NEXT_TASK ->
    StateError "không được nhảy NEXT_TASK -> TRANSPORT_UNAVAILABLE" che mất lỗi
    mạng thật. TRANSPORT_UNAVAILABLE là điểm chặn hợp lệ nên phải tới được.
    """
    from agent_loop.protocol import TERMINAL
    for src in ALLOWED:
        if src in TERMINAL:
            continue
        assert "TRANSPORT_UNAVAILABLE" in ALLOWED[src], \
            f"từ {src} không ghi nhận được lỗi truyền nhận"


def test_parse_directive_khoi_lenh_dat_next_action_truoc_nhiem_vu():
    """Bất biến: `next_action:` nằm ĐẦU khối lệnh KHÔNG được cắt mất nhiệm vụ.

    Bẫy thật (run 20261025_235344, GPT-6 chỉ ra: *"Executor SURVEY không thực
    hiện khảo sát theo yêu cầu"*): khối `TASK_ASSIGNMENT` xếp `next_action:`
    NGAY ĐẦU rồi mới tới 5 mục nhiệm vụ. Bản cũ cắt prose từ dòng `next_action:`
    => vứt sạch nhiệm vụ, executor chỉ thấy tiêu đề rỗng, 3 round trả CÙNG
    SHA256 báo cáo.

    Đúng: chỉ cắt dòng đánh dấu `loop`; nếu không có, chỉ cắt dải key:value
    liền nhau >= 3 dòng ở cột 0 và GIỮ phần văn xuôi phía sau.
    """
    raw = (
        "TASK_ASSIGNMENT\n\n"
        "project_id: MG_STUDIO\n"
        "job_id: live-x\n"
        "round: 3\n"
        "task_status: NEXT_TASK\n"
        "next_action: SURVEY\n"
        "\nĐÁNH GIÁ BÁO CÁO LƯỢT 2\n\n"
        "Báo cáo lượt 2 trùng lượt 1. Chưa có MODULE_MAP.\n\n"
        "NHIỆM VỤ LƯỢT 3 — KHẢO SÁT CÓ MỤC TIÊU\n"
        "1. Kiểm tra executor SURVEY\n"
        "2. Liệt kê 11 file canh_*.js\n"
    )
    d = parse_directive(raw)
    assert d["ok"] and d["next_action"] == "SURVEY"
    ins = d["instruction"]
    assert "NHIỆM VỤ LƯỢT 3" in ins, "nhiệm vụ bị cắt mất khi next_action ở đầu khối"
    assert "MODULE_MAP" in ins
    assert "canh_*.js" in ins
    # Không được rò cả khối lệnh vào instruction
    assert "task_status: NEXT_TASK" not in ins


def test_parse_directive_giu_khoi_instructions():
    """Bất biến (mục B): chỉ thị kỹ thuật của GPT-6 PHẢI tới được executor.

    Bẫy thật (job dedup-check, 2026-10-25 — GPT-6 tự chỉ ra sau 5 round): khối
    lệnh nằm SAU dòng "loop", nên bước cắt prose VỨT LUÔN `instructions:`.
    Executor chỉ thấy nhận xét, không thấy yêu cầu -> 5 round trả cùng một báo
    cáo (SHA trùng) và GPT-6 kết luận "instructions appear not to reach execution".
    """
    raw = (
        "ĐÁNH GIÁ ROUND 2\n\nKết quả: PARTIAL PASS\n\n"
        "loop\nnext_action: TEST\nproject_id: MG_STUDIO\nround: 3\n"
        "task_status: TASK_READY\n"
        "instructions: >\n"
        "  Execute targeted dedup integration tests, not generic self-test.\n"
        "  Record actual submitted, accepted, executed, and rejected counts.\n"
        "  Do not return generic 6/6 agent_loop self-test.\n"
    )
    d = parse_directive(raw)
    assert d["ok"] and d["next_action"] == "TEST"
    ins = d["instruction"]
    assert "dedup integration tests" in ins, "khối instructions: bị vứt mất"
    assert "submitted, accepted, executed" in ins
    assert "generic 6/6" in ins
    assert d.get("instructions", "").startswith("YÊU CẦU KỸ THUẬT")


def test_parse_directive_instructions_khong_ro_ri_khi_van_xuoi_ngan():
    """Khối lệnh không được rò vào phần văn xuôi (giữ bất biến cũ)."""
    raw = (
        "Nhận xét ngắn.\n\nloop\nnext_action: SURVEY\nround: 2\n"
        "instructions: kiểm tra khoá hook\n"
    )
    d = parse_directive(raw)
    assert d["ok"] and d["next_action"] == "SURVEY"
    assert "kiểm tra khoá hook" in d["instruction"]
    # không được lẫn các khoá lệnh vào văn xuôi
    assert "next_action: SURVEY" not in d["instruction"].split("YÊU CẦU")[0]


def test_await_directive_gan_instruction_vao_data():
    """Bất biến (mục B): `instruction` phải được GHI vào data, không chỉ last_directive.

    Bẫy thật (job dedup-v2, 2026-10-25 — GPT-6 chỉ ra lần 2): `_await_directive`
    chỉ gán `data['last_directive']`, còn `run_executor` đọc `data['instruction']`.
    Kết quả: executor luôn nhận chuỗi rỗng -> rơi về nhánh mặc định -> mọi round
    trả cùng báo cáo self-test 6/6 (SHA trùng), GPT-6 kết luận
    DISPATCHER_UNABLE_TO_ROUTE_TASK.
    """
    import inspect
    from agent_loop.loop import AgentLoop

    src = inspect.getsource(AgentLoop._await_directive)
    assert 'data["instruction"]' in src, \
        "thiếu gán data['instruction'] -> executor không thấy chỉ thị"
    assert 'data["instructions"]' in src
    # và run_executor phải thực sự đọc instruction
    from agent_loop.executor import run_executor
    esrc = inspect.getsource(run_executor)
    assert "instruction" in esrc


def test_executor_nhan_duoc_instruction_dedup():
    """Đường dây thật: chỉ thị dedup -> dispatch sang bài kiểm thử tích hợp."""
    import inspect
    from agent_loop.executor import test_agent_loop

    src = inspect.getsource(test_agent_loop)
    assert "_test_dedup()" in src, "TEST không dispatch được yêu cầu dedup"
    assert "parse_directive" in src


def test_test_khong_khop_thi_blocked_chu_khong_tra_self_test_chung():
    """Bất biến (mục E): chỉ thị lạ -> BLOCKED, KHÔNG trả "6/6 self-test" chung.

    GPT-6 nói thẳng ở job dedup-check: "Do not return generic 6/6 agent_loop
    self-test as the sole evidence. Report BLOCKED with exact technical reasons
    if specialized tests cannot be executed." Trả bài chung là báo cáo sai —
    nó không chứng minh phạm vi được giao.
    """
    from agent_loop.executor import test_agent_loop

    for instr in ("", "hãy làm việc gì đó khác", "please do something unrelated"):
        r = test_agent_loop(instr)
        assert r["next_action"] == "STOP", f"chỉ thị {instr!r} phải BLOCKED"
        assert r.get("blocker"), "BLOCKED phải kèm lý do kỹ thuật"
        assert "Tự kiểm agent_loop" not in r["summary"], \
            "không được trả self-test chung khi chỉ thị không khớp"


def test_test_tich_hop_khong_roi_vao_bo_22_bai():
    """Bất biến: chỉ thị TÍCH HỢP phải chạy bài tích hợp, không phải 22 bài unit.

    Bẫy thật (job live-20261025_235751): round 2 và round 4 đều chứa `khoa_hook`
    nên cùng rơi vào `_test_mg_modules` → CÙNG SHA256 báo cáo. GPT-6 nghi đúng:
    *"cần kiểm tra Hermes có đang chạy một executor TEST cố định"*.
    """
    from agent_loop.executor import _pick_test

    integration = ("TASK_ASSIGNMENT\nnext_action: TEST\n\n"
                   "Kiểm chứng khóa Hook và QC state trên một bản sao tập lane X.\n"
                   "Sao chép sang thư mục thử nghiệm. Kiểm tra restart.\n")
    unit = ("TASK_ASSIGNMENT\nnext_action: TEST\n\n"
            "Chạy bộ nghiệm thu module core/khoa_hook.py và core/trang_thai_qc.py.\n")
    assert _pick_test(integration) == "integration_lane_x"
    assert _pick_test(unit) == "mg_modules"
    # dedup vẫn ưu tiên hơn cả hai
    assert _pick_test("dedup integration submitted accepted") == "dedup"


def test_implement_chi_thi_sua_san_xuat_thi_blocked():
    """Bất biến: chỉ thị đòi SỬA tệp sản xuất -> BLOCKED, không báo thành công giả.

    Bẫy thật (job live-20261026_000729 round 3): GPT-6 giao "Tích hợp HOOK_LOCK
    vào entry point, scope: mg.py + các điểm gọi pipeline". `mg_implement.run()`
    nhận `instruction` nhưng KHÔNG đọc, nên vẫn kiểm 3 tệp cố định rồi trả
    "IMPLEMENT THẬT … 22/22 đạt". GPT-6 phải tự phát hiện thiếu bằng chứng.
    """
    from agent_loop import mg_implement as MI

    chi_thi = ("TASK_ASSIGNMENT — Round 3\n"
               "mode: IMPLEMENT\n"
               "scope: mg.py + các điểm gọi pipeline liên quan\n"
               "Tích hợp HOOK_LOCK vào entry point. Bắt buộc xác thực Hook "
               "trước mọi công đoạn sử dụng Hook đã khóa.")

    # 1) hàm dò phải thấy dấu hiệu
    assert MI._doi_sua_san_xuat(chi_thi), "phải nhận ra chỉ thị đòi sửa sản xuất"
    assert MI._doi_sua_san_xuat("") == "", "chỉ thị rỗng thì không kết luận"
    assert not MI._doi_sua_san_xuat("chạy lại bộ 22 bài test"), \
        "chỉ thị test thường không được coi là sửa sản xuất"

    # 2) run() phải trả BLOCKED + nêu điểm nghẽn, KHÔNG được báo 22/22
    r = MI.run(instruction=chi_thi, log=lambda *a, **k: None)
    assert r["next_action"] == "STOP", r["next_action"]
    assert r["blocker"], "BLOCKED phải nêu điểm nghẽn cụ thể"
    assert r["artifacts"] == [], "không được tạo artifact nào"
    assert "BLOCKED" in r["summary"], r["summary"]
    assert "22/22" not in r["summary"], "không được báo thành công giả"


def test_fix_co_executor_va_di_qua_implement():
    """Bất biến: `next_action: FIX` phải có đường chạy, không trả báo cáo rỗng.

    Bẫy thật (run 20261025_220917): GPT-6 giao FIX ở round 4 → executor trả
    evidence=0/artifacts=0 → mất trắng một round và GPT-6 phải kết luận BLOCKED.
    """
    from agent_loop.executor import EXECUTORS, fix_agent_loop

    assert "FIX" in EXECUTORS, "FIX phải có trong EXECUTORS"
    import inspect
    src = inspect.getsource(fix_agent_loop)
    assert "mg_run" in src, "FIX phải đi qua mg_implement"
    assert "IMPLEMENT" in src, "báo cáo FIX phải nói rõ nó chạy qua IMPLEMENT"


def test_core_self_check_van_chay_doc_lap():
    """`cli self-test` dùng core_self_check — phải chạy được độc lập."""
    from agent_loop.executor import core_self_check

    r = core_self_check()
    assert "6 đạt" in r["summary"], r["summary"]
    assert not r.get("blocker")


def test_next_task_khong_gui_lai_task_khoi_dong():
    """Bất biến (mục C): chống gửi trùng — NEXT_TASK KHÔNG được gửi lại task_text.

    Bẫy thật (job smoke-fix, 2026-10-25): `run()` gộp NEXT_TASK chung nhánh với
    TASK_READY nên mỗi vòng lặp gửi LẠI thông điệp khởi động của round 1. Hội
    thoại thật có 3 lượt y hệt nhau ("round: 1 / TASK_READY / SURVEY"), GPT-6
    trả lời sai ngữ cảnh. Test này khoá hành vi: NEXT_TASK -> EXECUTING ngay.
    """
    import inspect
    from agent_loop.loop import AgentLoop

    src = inspect.getsource(AgentLoop.run)
    # nhánh gửi task chỉ được nhận TASK_READY/CREATED, không được có NEXT_TASK
    assert 'state in ("TASK_READY", "CREATED")' in src, \
        "nhánh gửi task đã bị gộp lại với NEXT_TASK -> sẽ gửi trùng"
    assert "state in (\"TASK_READY\", \"NEXT_TASK\", \"CREATED\")" not in src, \
        "NEXT_TASK không được nằm trong nhánh gửi task khởi động"
    assert 'elif state == "NEXT_TASK":' in src, "thiếu nhánh NEXT_TASK riêng"


def test_khoa_doc_quyen(job):
    """Bất biến (mục C): không 2 lượt đồng thời trên cùng job."""
    job.acquire()
    assert job.lock.exists()
    # cùng PID thì vẫn phải chặn (đang chạy)
    with pytest.raises(StateError, match="KHÔNG chạy song song"):
        job.acquire()


def test_khoa_pid_chet_thi_thu_hoi(job):
    """Lock của tiến trình đã chết không được chặn vĩnh viễn."""
    job.lock.write_text(json.dumps({"pid": 999_999_999, "at": 0}), encoding="utf-8")
    job.acquire()                       # không được ném
    assert json.loads(job.lock.read_text(encoding="utf-8"))["pid"] > 0


def test_release_chi_xoa_lock_cua_minh(job):
    job.lock.write_text(json.dumps({"pid": 1, "at": 0}), encoding="utf-8")
    job.release()
    assert job.lock.exists()            # lock của PID khác -> giữ nguyên


def test_chong_gui_trung(job):
    d = job.load()
    job.mark_sent(d, round_no=1, kind="report", digest="abc")
    assert job.already_sent(d, round_no=1, kind="report")["sha256"] == "abc"
    assert job.already_sent(d, round_no=1, kind="task") is None
    assert job.already_sent(d, round_no=2, kind="report") is None


def test_checkpoint_ghi_lai(job):
    d = job.load()
    job.checkpoint(d, "task_sent")
    on_disk = json.loads(job.path.read_text(encoding="utf-8"))
    assert on_disk["checkpoints"][-1]["label"] == "task_sent"


def test_ghi_nguyen_tu_khong_de_lai_tmp(job):
    d = job.load()
    job.save(d)
    assert not list(job.dir.glob(".tmp_*"))


# ========================================================== parse_directive
def test_directive_co_fence():
    d = parse_directive("Mô tả ngắn.\n\n```loop\nnext_action: SURVEY\nround: 2\n"
                        "task_status: TASK_READY\n```\n")
    assert d["ok"] and d["next_action"] == "SURVEY" and d["round"] == "2"
    assert d["instruction"].startswith("Mô tả ngắn")


def test_directive_khong_fence():
    """BẪY ĐÃ ĐO (job smoke-cli2): markdown lấy từ DOM mất dấu ```.

    Nếu parser chỉ tìm fence thì vòng lặp chết giữa đường dù GPT đã trả lệnh đúng.
    """
    d = parse_directive("GPT trả lời dài.\n\nloop\nnext_action: SURVEY\n"
                        "project_id: MG_STUDIO\nround: 2\ntask_status: TASK_READY\n")
    assert d["ok"], d
    assert d["next_action"] == "SURVEY" and d["round"] == "2"


def test_directive_lay_lan_cuoi_next_action():
    """GPT hay lặp khối lệnh; phải lấy lần CUỐI, không lấy lần đầu."""
    txt = ("x\n\nloop\nnext_action: SURVEY\nround: 1\ntask_status: TASK_READY\n\n"
           "loop\nnext_action: STOP\nround: 4\ntask_status: BLOCKED\n")
    d = parse_directive(txt)
    assert d["next_action"] == "STOP" and d["round"] == "4"
    assert d["task_status"] == "BLOCKED"


def test_directive_bo_muc_con_yaml():
    """Chỉ nhận dòng KHÔNG thụt lề — mục con `- Khảo sát...` không được đè."""
    txt = ("x\n\nloop\nnext_action: SURVEY\nround: 3\ntask_status: TASK_READY\n"
           "instructions:\n  - next_action: KHONG_PHAI\n  - round: 99\n")
    d = parse_directive(txt)
    assert d["next_action"] == "SURVEY" and d["round"] == "3"


def test_directive_khong_co_lenh_thi_tra_ok_false():
    d = parse_directive("Chỉ là văn xuôi, không có khối lệnh nào.")
    assert d["ok"] is False and "next_action" in d["reason"]


def test_directive_action_la_thi_tu_choi():
    d = parse_directive("x\n\nloop\nnext_action: BAY_LUON\nround: 1\n"
                        "task_status: TASK_READY\n")
    assert d["ok"] is False


def test_directive_giu_van_xuoi_lam_instruction():
    """BẪY ĐÃ ĐO: bỏ văn xuôi => executor không biết GPT hỏi gì => lặp dữ liệu cũ."""
    d = parse_directive("Cần xác minh KHÓA HOOK và số vòng QC.\n\nloop\n"
                        "next_action: SURVEY\nround: 2\ntask_status: TASK_READY\n")
    assert "KHÓA HOOK" in d["instruction"]
    assert "next_action" not in d["instruction"]


def test_render_task_parse_lai_duoc():
    txt = render_task(project_id="MG_STUDIO", job_id="j-01", conversation_id="c",
                      round_no=1, task_status="TASK_READY", next_action="SURVEY",
                      goal="khảo sát", steps=["a", "b"], acceptance=["ok"])
    t = parse_task(txt)
    assert t["job_id"] == "j-01" and t["next_action"] == "SURVEY"


def test_render_task_co_allowed_actions_thi_neu_ro():
    """BẪY ĐÃ ĐO (job mg-v2-body-01, 26/10/2026): TASK_ASSIGNMENT không nêu danh sách
    lệnh -> GPT-6 tự chọn lệnh legacy SURVEY/IMPLEMENT/TEST, job chết ở
    NEEDS_OWNER_ACTION sau 5 round dù hồ sơ v2 đã đóng xong."""
    txt = render_task(project_id="MG_STUDIO", job_id="j-v2", conversation_id="",
                      round_no=1, task_status="TASK_READY", next_action="HO_SO_GPT6",
                      goal="viết Body", steps=["a"], acceptance=["ok"],
                      allowed_actions=["HO_SO_GPT6", "KIEM_HO_SO", "VIET_BODY", "NHAN_BODY"])
    assert "## ALLOWED ACTIONS" in txt
    for a in ("HO_SO_GPT6", "KIEM_HO_SO", "VIET_BODY", "NHAN_BODY"):
        assert f"`{a}`" in txt
    # vẫn parse lại được, và KHÔNG làm hỏng job cũ (không truyền allowed_actions)
    assert parse_task(txt)["next_action"] == "HO_SO_GPT6"
    cu = render_task(project_id="MG_STUDIO", job_id="j-cu", conversation_id="",
                     round_no=1, task_status="TASK_READY", next_action="SURVEY",
                     goal="g", steps=["a"], acceptance=["ok"])
    assert "ALLOWED ACTIONS" not in cu


def test_viet_body_nam_trong_actions_va_khong_dispatch():
    """VIET_BODY là việc của CHÍNH GPT-6 -> phải nằm trong ACTIONS (để không bị chặn)
    nhưng KHÔNG được dispatch sang executor (không có executor nào tên đó)."""
    assert "VIET_BODY" in ACTIONS
    from agent_loop.executor import run_executor as _run
    d = parse_directive("loop\nnext_action: VIET_BODY\nround: 3\ntask_status: TASK_READY\n")
    assert d["ok"] and d["next_action"] == "VIET_BODY"
    r = _run(Path("."), {"next_action": "VIET_BODY", "goal": "x"}, log=lambda *_: None)
    assert r["next_action"] == "VIET_BODY" and not r.get("blocker")
    assert "GPT-6" in r["summary"]
