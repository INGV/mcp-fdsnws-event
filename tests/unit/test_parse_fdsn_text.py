"""Unit tests for the FDSN text parser (offline, uses a captured real fixture)."""

from pathlib import Path

from fdsnws_event_server.obspy_client import parse_fdsn_text

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ingv_emilia_2012-05-29.txt"


def test_parse_header_and_rows():
    cols, rows = parse_fdsn_text(FIXTURE.read_text())
    assert cols[0] == "EventID"
    assert cols[4] == "Depth/Km"  # depth is in km in the text format
    assert len(cols) == 14
    assert len(rows) == 8
    assert rows[0][0] == "863301"
    assert rows[0][-1] == "earthquake"
    # every row has the same arity as the header
    assert all(len(r) == len(cols) for r in rows)


def test_empty_input():
    assert parse_fdsn_text("") == ([], [])


def test_only_header():
    cols, rows = parse_fdsn_text("#A|B|C\n")
    assert cols == ["A", "B", "C"]
    assert rows == []


def test_blank_lines_ignored():
    cols, rows = parse_fdsn_text("#A|B\n\n1|2\n\n")
    assert cols == ["A", "B"]
    assert rows == [["1", "2"]]
