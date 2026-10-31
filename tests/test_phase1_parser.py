# -*- coding: utf-8 -*-
"""Phase 1 — Parser cột K + Drive pair. Không cần mạng."""
from __future__ import annotations

import pytest

from services.errors import BridgeError
from services.sheet_scanner import parse_cell


def P(cell, **kw):
    base = dict(spreadsheet_id="SHEET_FAKE", sheet_name="Lane-N", sheet_id=1555179355,
                row=18, col_a="17", col_b="【漫画】420万円", col_c="assets/N-senior_korekara/ep17-x",
                cfg={"sheets": {
                    "status_rules": {
                        "latest_line_wins": True,
                        "need_script": ["can kich ban", "can viet kich ban", "chua co kich ban"],
                        "script_done": ["kich ban xong", "da co kich ban", "da giao gpt"]},
                    "link_labels": {"zip": ["zip ho so", "zip"],
                                    "folder": ["thu muc drive", "thu muc"]}}})
    base.update(kw)
    return parse_cell(cell, **base)


SPEC_CELL = (
    "Đã xong hook+ sườn- cần kịch bản\n"
    "ZIP hồ sơ: https://drive.google.com/file/d/1AB2E02356FC42D7D6B365B2ADD34BC70/view\n"
    "Thư mục Drive: https://drive.google.com/drive/folders/144FC78DACCFB031B7737DED39A2E501B"
)


class TestParseCell:
    def test_spec_cell_exact(self):
        """Đúng mẫu trong đặc tả: trạng thái + 2 link."""
        r = P(SPEC_CELL)
        assert r.status == "need_script"
        assert r.zip_file_id == "1AB2E02356FC42D7D6B365B2ADD34BC70"
        assert r.folder_id == "144FC78DACCFB031B7737DED39A2E501B"
        assert r.zip_url and "/file/d/" in r.zip_url
        assert r.ep_num == 17
        assert r.episode_id == "ep17"

    def test_link_with_query_params(self):
        """Link có tham số (?usp=sharing) vẫn phải lấy đúng ID."""
        r = P("cần kịch bản\nZIP: https://drive.google.com/file/d/"
              "1AB2E02356FC42D7D6B365B2ADD34BC70/view?usp=sharing&t=1\n"
              "Folder: https://drive.google.com/drive/folders/"
              "144FC78DACCFB031B7737DED39A2E501B?usp=drive_link")
        assert r.zip_file_id == "1AB2E02356FC42D7D6B365B2ADD34BC70"
        assert r.folder_id == "144FC78DACCFB031B7737DED39A2E501B"

    def test_no_space_after_colon(self):
        r = P("cần kịch bản\nZIP hồ sơ:https://drive.google.com/file/d/"
              "1AB2E02356FC42D7D6B365B2ADD34BC70/view")
        assert r.zip_file_id == "1AB2E02356FC42D7D6B365B2ADD34BC70"

    def test_extra_whitespace_and_blank_lines(self):
        r = P("   cần   kịch bản   \n\n\n   ZIP hồ sơ :  "
              "https://drive.google.com/file/d/1AB2E02356FC42D7D6B365B2ADD34BC70/view  \n\n")
        assert r.status == "need_script"
        assert r.zip_file_id == "1AB2E02356FC42D7D6B365B2ADD34BC70"

    def test_extra_notes_preserved(self):
        """Ghi chú thêm phải GIỮ NGUYÊN, chỉ trích trường cần thiết."""
        cell = SPEC_CELL + "\nGhi chú: đừng dùng bản Sol, hook Fabel đã duyệt."
        r = P(cell)
        assert "đừng dùng bản Sol" in " ".join(r.extra_notes)
        assert r.zip_file_id == "1AB2E02356FC42D7D6B365B2ADD34BC70"

    def test_top_line_wins_status(self):
        """Dòng ĐẦU (mới nhất) quyết định — sheet thật ghi mới nhất ở TRÊN.

        Đo thật 2026-10-24 ô K18 tab Lane-N: dòng 0 là 'Kịch bản xong- chờ dựng hook',
        dòng 1 mới là 'Đã xong hook+ sườn- cần kịch bản' => ep17 là script_done.
        """
        cell = "kịch bản xong\nĐã xong hook+ sườn- cần kịch bản"
        assert P(cell).status == "script_done"

    def test_need_script_when_top_line(self):
        cell = "Đã xong hook+ sườn- cần kịch bản\nkịch bản xong"
        assert P(cell).status == "need_script"

    def test_empty_cell(self):
        r = P("")
        assert r.status == "ignore"
        assert r.zip_file_id is None

    def test_no_link_at_all(self):
        r = P("cần kịch bản")
        assert r.status == "need_script"
        assert r.zip_file_id is None and r.folder_id is None

    def test_episode_from_col_a(self):
        r = P("cần kịch bản", col_a="17")
        assert r.ep_num == 17 and r.episode_id == "ep17"

    def test_episode_from_slug_when_col_a_empty(self):
        r = P("cần kịch bản", col_a="",
              col_c="assets/N-senior_korekara/ep17-kei-campingcar-kari-1kagetsu")
        assert r.ep_num == 17
        assert r.episode_slug == "ep17-kei-campingcar-kari-1kagetsu"

    def test_row_key_stable(self):
        a, b = P(SPEC_CELL), P(SPEC_CELL)
        assert a.row_key == b.row_key

    def test_row_key_changes_with_content(self):
        a = P(SPEC_CELL)
        b = P(SPEC_CELL.replace("1AB2E023", "1AB2E024"))
        assert a.row_key != b.row_key

    def test_rejects_folder_link_as_zip(self):
        """Link folder không được nhận làm ZIP file id."""
        r = P("cần kịch bản\nZIP: https://drive.google.com/drive/folders/144FC78DACCFB031B7737DED39A2E501B")
        assert r.zip_file_id is None
        assert r.folder_id == "144FC78DACCFB031B7737DED39A2E501B"


class TestDrivePair:
    def test_resolve_pair_ok(self, fake_drive):
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        inp = fake_drive.add_folder("INPUT", ep)
        out = fake_drive.add_folder("OUTPUT", ep)
        pair = fake_drive.resolve_pair(ep)
        assert pair["input_folder_id"] == inp
        assert pair["output_folder_id"] == out

    def test_missing_input(self, fake_drive):
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        fake_drive.add_folder("OUTPUT", ep)
        with pytest.raises(BridgeError) as ei:
            fake_drive.resolve_pair(ep)
        assert ei.value.code == "E_DRIVE_INPUT_MISSING"

    def test_missing_output(self, fake_drive):
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        fake_drive.add_folder("INPUT", ep)
        with pytest.raises(BridgeError) as ei:
            fake_drive.resolve_pair(ep)
        assert ei.value.code == "E_DRIVE_OUTPUT_MISSING"

    def test_ambiguous(self, fake_drive):
        ep = fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        fake_drive.add_folder("INPUT", ep)
        fake_drive.add_folder("INPUT", ep)
        fake_drive.add_folder("OUTPUT", ep)
        with pytest.raises(BridgeError) as ei:
            fake_drive.resolve_pair(ep)
        assert ei.value.code == "E_DRIVE_FOLDER_AMBIGUOUS"

    def test_episode_folder_highest_version(self, fake_drive):
        fake_drive.add_folder("N-ep17_v01", "INBOX_FAKE")
        fake_drive.add_folder("N-ep17_v04", "INBOX_FAKE")
        fake_drive.add_folder("N-ep16_v09", "INBOX_FAKE")
        got = fake_drive.resolve_episode_folder("N", 17)
        assert got["name"] == "N-ep17_v04"

    def test_episode_folder_not_found(self, fake_drive):
        with pytest.raises(BridgeError) as ei:
            fake_drive.resolve_episode_folder("N", 99)
        assert ei.value.code == "E_DRIVE_NOT_FOUND"
