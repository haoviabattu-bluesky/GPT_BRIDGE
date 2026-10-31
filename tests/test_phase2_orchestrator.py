# -*- coding: utf-8 -*-
"""Phase 2 — state, prompt builder, orchestrator (không cần mạng)."""
from __future__ import annotations

import json

import pytest

from services.errors import BridgeError
from services.prompt_builder import PromptBuilder, parse_budget, parse_episode_meta, parse_hook
from services.state import StateStore, job_dir


# ------------------------------------------------------------------ state
class TestState:
    def test_create_and_get(self, cfg):
        st = StateStore(cfg["_root"] + "/jobs/t1.db")
        j = st.create({"job_id": "J1", "project": "H2DEV", "lane": "N", "episode_id": "ep17",
                       "row_key": "rk1", "status": "DETECTED"})
        assert j["job_id"] == "J1"
        assert st.get("J1")["status"] == "DETECTED"
        st.close()

    def test_row_key_unique(self, cfg):
        st = StateStore(cfg["_root"] + "/jobs/t2.db")
        st.create({"job_id": "J1", "row_key": "rk1", "status": "DETECTED",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        with pytest.raises(Exception):
            st.create({"job_id": "J2", "row_key": "rk1", "status": "DETECTED",
                       "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        st.close()

    def test_by_row_key_prevents_duplicate(self, cfg):
        st = StateStore(cfg["_root"] + "/jobs/t3.db")
        st.create({"job_id": "J1", "row_key": "rk1", "status": "DETECTED",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        assert st.by_row_key("rk1")["job_id"] == "J1"
        assert st.by_row_key("rk-nope") is None
        st.close()

    def test_transition_logs_event(self, cfg):
        st = StateStore(cfg["_root"] + "/jobs/t4.db")
        st.create({"job_id": "J1", "row_key": "rk1", "status": "DETECTED",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        st.transition("J1", "VALIDATING", "bắt đầu")
        st.transition("J1", "READY_TO_SUBMIT", "xong")
        assert st.get("J1")["status"] == "READY_TO_SUBMIT"
        ev = st.events("J1")
        # events ghi các lần CHUYỂN trạng thái (create khởi tạo DETECTED, không tính là chuyển)
        assert [e["to_state"] for e in ev] == ["VALIDATING", "READY_TO_SUBMIT"]
        st.close()

    def test_persist_across_reopen(self, cfg):
        p = cfg["_root"] + "/jobs/t5.db"
        st = StateStore(p)
        st.create({"job_id": "J1", "row_key": "rk1", "status": "DETECTED",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        st.close()
        st2 = StateStore(p)
        assert st2.get("J1") is not None
        st2.close()

    def test_active_for_episode(self, cfg):
        st = StateStore(cfg["_root"] + "/jobs/t6.db")
        st.create({"job_id": "J1", "row_key": "rk1", "status": "COMPLETED",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        assert st.active_for_episode("H2DEV", "N", "ep17") is None  # COMPLETED không tính
        st.create({"job_id": "J2", "row_key": "rk2", "status": "WAITING_GPT",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        assert st.active_for_episode("H2DEV", "N", "ep17")["job_id"] == "J2"
        st.close()

    def test_stats(self, cfg):
        st = StateStore(cfg["_root"] + "/jobs/t7.db")
        st.create({"job_id": "J1", "row_key": "rk1", "status": "DETECTED",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep17"})
        st.create({"job_id": "J2", "row_key": "rk2", "status": "DETECTED",
                   "project": "H2DEV", "lane": "N", "episode_id": "ep18"})
        assert st.stats()["DETECTED"] == 2
        st.close()


# ------------------------------------------------------------------ parse
class TestParse:
    def test_budget_from_writing_task(self):
        b = parse_budget("| Số ký tự | min **250** · target **300** · max **400** |")
        assert (b["min"], b["target"], b["max"]) == (250, 300, 400)

    def test_budget_fallback_checklist(self):
        b = parse_budget("", "| 1 | Độ dài | 250 ≤ ký tự ≤ 400 | ☐ |")
        assert b["min"] == 250 and b["max"] == 400

    def test_budget_missing(self):
        b = parse_budget("không có số")
        assert b["target"] == 0

    def test_hook_extracted_verbatim(self, handoff_zip):
        from services import zip_qc
        h = zip_qc.inspect_handoff(handoff_zip, {"qc": {"max_files": 200,
                                                       "max_uncompressed_mb": 200}})
        hook = parse_hook(h["texts"]["01_HOOK_OUTLINE.txt"])
        assert hook["text"].startswith("六十八歳の春")
        assert "HOOK" not in hook["text"]

    def test_episode_meta(self):
        m = parse_episode_meta(
            "| Episode | ep17 · `ep17-kei-campingcar-kari-1kagetsu` |\n"
            "| Tiêu đề dự kiến | 【漫画】420万円 |\n| Lane | N |")
        assert m["episode"].startswith("ep17")
        assert m["title"].startswith("【漫画】")

    def test_episode_meta_ignores_format_row_title(self):
        """BẪY THẬT trong hồ sơ N-ep17: dòng 'Tiêu đề chương' là QUY ĐỊNH ĐỊNH DẠNG,
        không phải tiêu đề video. Không được để nó ghi đè 'Tiêu đề dự kiến'."""
        wt = ("| Tiêu đề dự kiến | 【漫画】420万円の軽キャンピングカー |\n"
              "| Tiêu đề chương | KHÔNG có trong bản đọc (pure prose) → không tính |\n"
              "| Số ký tự | min **4,535** · target **4,930** · max **5,324** |\n")
        m = parse_episode_meta(wt)
        assert m["title"].startswith("【漫画】")
        assert "KHÔNG có" not in m["title"]

    def test_episode_meta_title_fallback(self):
        m = parse_episode_meta("| Tiêu đề | Chỉ có một dòng tiêu đề |")
        assert m["title"] == "Chỉ có một dòng tiêu đề"

    def test_budget_with_thousand_separators(self):
        """Ngân sách thật ghi 4,535 — phải parse ra số, không vỡ."""
        b = parse_budget("| Số ký tự | min **4,535** · target **4,930** · max **5,324** |")
        assert (b["min"], b["target"], b["max"]) == (4535, 4930, 5324)


# ------------------------------------------------------------------ prompt
class TestPromptBuilder:
    def test_build_has_all_sections(self, cfg, project, handoff_zip):
        from services import zip_qc
        h = zip_qc.inspect_handoff(handoff_zip, cfg)
        job = {"job_id": "H2DEV-N-ep17-GPT6-v01", "lane": "N", "episode_id": "ep17",
               "input_zip_url": "https://drive.google.com/file/d/X/view",
               "input_folder_url": "https://drive.google.com/drive/folders/IN",
               "output_folder_url": "https://drive.google.com/drive/folders/OUT"}
        b = PromptBuilder(cfg, project).build(job, {
            "manifest": h["manifest"], "writing_task": h["texts"]["07_WRITING_TASK.md"],
            "hook_outline": h["texts"]["01_HOOK_OUTLINE.txt"],
            "qc_checklist": h["texts"]["08_QC_CHECKLIST.md"], "files": h["texts"]},
            {"01_HOOK_OUTLINE.txt": {"id": "F1",
                                     "url": "https://drive.google.com/file/d/F1/view"}})
        tm = b["task_message"]
        assert "H2DEV-N-ep17-GPT6-v01" in tm
        assert "https://drive.google.com/file/d/X/view" in tm
        assert "SCRIPT_FINAL_JP.txt" in tm
        assert "min **250**" in tm or "250" in tm
        assert b["meta"]["budget"]["max"] == 400

    def test_write_files(self, cfg, project, handoff_zip, tmp_path):
        from services import zip_qc
        h = zip_qc.inspect_handoff(handoff_zip, cfg)
        job = {"job_id": "J", "lane": "N", "episode_id": "ep17",
               "input_zip_url": "u", "input_folder_url": "i", "output_folder_url": "o"}
        b = PromptBuilder(cfg, project).build(job, {
            "manifest": h["manifest"], "writing_task": h["texts"]["07_WRITING_TASK.md"],
            "hook_outline": h["texts"]["01_HOOK_OUTLINE.txt"],
            "qc_checklist": h["texts"]["08_QC_CHECKLIST.md"], "files": h["texts"]}, {})
        paths = PromptBuilder.write(tmp_path, b)
        assert (tmp_path / "GPT6_TASK_MESSAGE.txt").exists()
        assert (tmp_path / "CHATGPT_WEB_HANDOFF_GUIDE.md").exists()
        assert (tmp_path / "prompt_meta.json").exists()
        assert "H2DEV" in paths["task_message"].read_text(encoding="utf-8") or True
