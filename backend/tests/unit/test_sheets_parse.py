"""Reading spreadsheets: sniffing, header detection, profiling, schema repair."""
from __future__ import annotations

import io
import zipfile

import pytest

from sheets.services import parse
from sheets.services.profile import parse_number, profile_sheet
from sheets.services.schema import heuristic_schema, normalize
from tests.sheet_fixtures import EXPECTED_HEADERS, FIRST_DATA_ROW, arabic_csv, student_workbook


def _read(tmp_path, data: bytes, mime: str, **kwargs):
    path = tmp_path / "upload.bin"
    path.write_bytes(data)
    return parse.read_file(str(path), mime, max_rows=kwargs.pop("max_rows", 1000),
                           max_cols=kwargs.pop("max_cols", 50), **kwargs)


# ---- sniffing ----------------------------------------------------------------


def test_xlsx_is_recognised_by_content():
    assert parse.is_xlsx(student_workbook())
    assert not parse.is_xlsx(b"%PDF-1.4 not a zip")


def test_a_zip_that_is_not_a_workbook_is_not_xlsx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w/>")
    assert not parse.is_xlsx(buffer.getvalue())


def test_a_workbook_that_inflates_past_the_limit_is_refused():
    with pytest.raises(parse.SpreadsheetError):
        parse.is_xlsx(student_workbook(), max_uncompressed=100)


@pytest.mark.parametrize("data, expected", [
    (b"name,grade\nAhmed,90\nSara,45\n", True),
    (b"name;grade\nAhmed;90\n", True),
    (arabic_csv(), True),
    (b"not a document", False),
    (b"#!/bin/sh\necho hi", False),
    (b"\x00\x01binary,stuff", False),
])
def test_csv_sniffing(data, expected):
    assert parse.looks_like_csv(data) is expected


# ---- reading -----------------------------------------------------------------


def test_header_is_found_below_a_merged_title_and_stacked_headers_are_joined(tmp_path):
    [sheet] = _read(tmp_path, student_workbook(), parse.XLSX_MIME)

    assert sheet.name == "Grades 2024"  # the hidden sheet is skipped
    assert sheet.headers == EXPECTED_HEADERS
    assert sheet.header_row == FIRST_DATA_ROW - 1
    assert sheet.header_rows == 2
    assert [n for n, _ in sheet.rows] == list(range(FIRST_DATA_ROW, FIRST_DATA_ROW + 6))


def test_merged_cells_apply_to_every_row_they_cover(tmp_path):
    [sheet] = _read(tmp_path, student_workbook(), parse.XLSX_MIME)
    classes = [values[3] for _, values in sheet.rows]
    assert classes == ["10-A", "10-A", "10-A", "10-B", "10-B", "10-B"]


def test_cp1256_csv_is_decoded(tmp_path):
    [sheet] = _read(tmp_path, arabic_csv(), parse.CSV_MIME)
    assert sheet.headers == ["رقم الطالب", "اسم الطالب", "الدرجة"]
    assert sheet.rows[0] == (2, ["1", "أحمد علي", "90"])


def test_a_chosen_header_row_overrides_detection(tmp_path):
    [sheet] = _read(tmp_path, arabic_csv(), parse.CSV_MIME, header_overrides={0: None})
    assert sheet.headers == ["Column 1", "Column 2", "Column 3"]
    assert len(sheet.rows) == 4


def test_row_and_column_limits(tmp_path):
    with pytest.raises(parse.SpreadsheetError, match="rows"):
        _read(tmp_path, student_workbook(), parse.XLSX_MIME, max_rows=3)
    with pytest.raises(parse.SpreadsheetError, match="columns"):
        _read(tmp_path, student_workbook(), parse.XLSX_MIME, max_cols=4)


# ---- profiling ---------------------------------------------------------------


def test_profile_infers_types_ranges_and_fill_down(tmp_path):
    [sheet] = _read(tmp_path, student_workbook(), parse.XLSX_MIME)
    profile = {p["header"]: p for p in profile_sheet(sheet.headers, sheet.rows)}

    assert profile["Student ID"]["type"] == "id"
    assert profile["Grades / Math"]["type"] == "integer"
    assert (profile["Grades / Math"]["min"], profile["Grades / Math"]["max"]) == (38, 91)
    # Arabic-Indic digits are numbers too.
    assert profile["Grades / Physics"]["type"] == "integer"
    assert profile["Grades / Physics"]["max"] == 95
    assert profile["Passed"]["type"] == "boolean"
    assert set(profile["Class"]["options"]) == {"10-A", "10-B"}
    # Written once per group with blanks below: suggest filling down.
    assert profile["Section"]["fill_down_suggested"] is True
    assert profile["Class"]["fill_down_suggested"] is False  # merged, already filled


@pytest.mark.parametrize("value, expected", [
    ("1,234", 1234), ("٣٫٥", 3.5), ("0123", None), ("12a", None), (7, 7), (True, None),
])
def test_parse_number(value, expected):
    assert parse_number(value) == expected


# ---- schema repair ----------------------------------------------------------


def _profile():
    return profile_sheet(["Student ID", "Order", "اسم الطالب"], [(2, [1, "x", "Ahmed"]), (3, [2, "y", "Sara"])])


def test_normalize_repairs_whatever_the_model_returns():
    schema = normalize({
        "columns": [
            {"source": "Student ID", "key": "id", "label": "ID", "type": "id", "role": "identifier"},
            {"source": "Order", "key": "Order", "type": "nonsense", "role": "boss"},
            {"source": "invented column", "key": "ghost", "type": "text", "role": "attribute"},
        ],
        "combine": [{"key": "x", "from": ["id"]}],  # one source: nothing to combine
        "title_template": "{ghost} {id}",
    }, _profile())

    keys = [c["key"] for c in schema["columns"]]
    assert keys == ["id", "order_", "col_3"]  # reserved word suffixed; Arabic header gets a key
    assert schema["columns"][1]["type"] == "text"  # unknown type -> inferred
    assert schema["columns"][1]["role"] == "attribute"
    assert schema["columns"][2]["label"] == "اسم الطالب"
    assert schema["combine"] == []
    assert schema["title_template"] == "{id}"  # unknown placeholder -> default from roles


def test_heuristic_schema_picks_an_identifier_and_a_name():
    schema = heuristic_schema(_profile(), sheet_name="S", row_count=2)
    roles = {c["source"]: c["role"] for c in schema["columns"]}
    assert roles["Student ID"] == "identifier"
    assert roles["اسم الطالب"] == "title"
    assert schema["title_template"] == "{col_3} ({student_id})"
