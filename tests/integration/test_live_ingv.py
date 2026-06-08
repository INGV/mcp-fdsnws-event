"""Live integration tests against INGV (opt-in: run with `pytest -m integration`)."""

import asyncio

import pytest

from fdsnws_event_server.obspy_client import (
    DatacenterError,
    get_event_by_id,
    query_events_text,
)

pytestmark = pytest.mark.integration

WINDOW = {"starttime": "2012-05-29T00:00:00", "endtime": "2012-05-29T23:59:59"}


def run(coro):
    return asyncio.run(coro)


def test_query_returns_rows():
    cols, rows, _ = run(query_events_text(**WINDOW, limit=5, datacenter="INGV"))
    assert cols[0] == "EventID"
    assert 1 <= len(rows) <= 5


def test_offset_changes_results():
    _, page1, _ = run(query_events_text(**WINDOW, limit=3, offset=1, datacenter="INGV"))
    _, page2, _ = run(query_events_text(**WINDOW, limit=3, offset=4, datacenter="INGV"))
    ids1 = {r[0] for r in page1}
    ids2 = {r[0] for r in page2}
    assert ids1 and ids2 and ids1 != ids2


def test_bad_window_raises_datacenter_error():
    with pytest.raises(DatacenterError) as ei:
        run(query_events_text(
            starttime="2012-05-29T00:00:00", endtime="2010-01-01T00:00:00", datacenter="INGV",
        ))
    assert ei.value.status == 400
    assert ei.value.message  # carries the datacenter's verbatim text


def test_get_event_by_id_returns_quakeml():
    catalog, _ = run(get_event_by_id(eventid=863301, datacenter="INGV"))
    assert len(catalog) == 1
