"""Live integration tests against real datacenters (opt-in: `pytest -m integration`).

Parametrised over the advertised providers so the multi-datacenter claim is actually
exercised rather than only asserted. The offline mirror of this file is
``tests/unit/test_multidatacenter.py``, which runs the same matrix against captured
fixtures; keep the two in step.

Only behaviour that is genuinely uniform across providers is parametrised. Providers
disagree sharply on invalid input -- an inverted time window yields HTTP 400 at INGV,
204 at EMSC and GFZ, and is silently *ignored* by USGS, which answers 200 with data --
so those cases are asserted per provider instead of pretending to a common contract.

IRIS/EarthScope is not in the provider list: its FDSNWS event service returns HTTP 410
(ADR-0007). That retirement is itself pinned by a test at the end of this module.
"""

import asyncio

import pytest

from fdsnws_event_server.obspy_client import (
    DatacenterError,
    _extract_event_id,
    get_event_by_id,
    query_events_text,
)

pytestmark = pytest.mark.integration

# A window every provider has data for: the 2024-01-01 Noto (Honshu) M7.4 sequence.
# The same window backs the fixtures in tests/fixtures/*_honshu_2024-01-01.txt.
WINDOW = {"starttime": "2024-01-01T00:00:00", "endtime": "2024-01-02T00:00:00"}

# One real, historical event id per provider, taken from those fixtures. Historical
# events are used deliberately: their ids are stable, so these tests do not rot.
KNOWN_EVENTS = [
    ("INGV", "37258271"),
    ("EMSC", "20240101_0000328"),
    ("GFZ", "gfz2024abmz"),
    ("USGS", "us6000m0yg"),
]
PROVIDERS = [dc for dc, _ in KNOWN_EVENTS]

# Well-formed but non-existent ids, each following its provider's own grammar.
ABSENT_EVENTS = [
    ("INGV", "999999999999"),
    ("EMSC", "20240101_9999999"),
    ("GFZ", "gfz9999zzzz"),
    ("USGS", "us9999zzzzzz"),
]

# The original INGV window, retained for the pagination check tied to ADR-0003.
INGV_WINDOW = {"starttime": "2012-05-29T00:00:00", "endtime": "2012-05-29T23:59:59"}


def run(coro):
    return asyncio.run(coro)


# --- Search path, all providers --------------------------------------------

@pytest.mark.parametrize("datacenter", PROVIDERS)
def test_query_returns_rows(datacenter):
    cols, rows, _ = run(
        query_events_text(**WINDOW, minmag=5.0, limit=5, datacenter=datacenter)
    )
    assert cols[0] == "EventID"
    assert rows, f"{datacenter} returned no rows for a window known to have events"
    # Header-driven parsing: every row must match the provider's own header width.
    for row in rows:
        assert len(row) == len(cols)


@pytest.mark.parametrize("datacenter", PROVIDERS)
def test_query_depth_column_present_whatever_its_spelling(datacenter):
    """INGV spells it Depth/Km, the others Depth/km. Both must survive as given."""
    cols, _, _ = run(
        query_events_text(**WINDOW, minmag=5.0, limit=1, datacenter=datacenter)
    )
    assert any(c.lower() == "depth/km" for c in cols)


# --- Detail path, all providers --------------------------------------------

@pytest.mark.parametrize("datacenter,eventid", KNOWN_EVENTS, ids=PROVIDERS)
def test_get_event_by_id_returns_quakeml(datacenter, eventid):
    """The by-id path must work with each provider's native identifier format.

    This is the check that was impossible before `eventid` became a string: GFZ and
    USGS ids were rejected by input validation, and EMSC's was silently mangled.
    """
    catalog, _ = run(get_event_by_id(eventid=eventid, datacenter=datacenter))
    assert len(catalog) == 1


@pytest.mark.parametrize("datacenter,eventid", KNOWN_EVENTS, ids=PROVIDERS)
def test_event_id_round_trips(datacenter, eventid):
    """The id extracted from the returned QuakeML must match the one requested."""
    catalog, _ = run(get_event_by_id(eventid=eventid, datacenter=datacenter))
    assert _extract_event_id(catalog[0]) == eventid


@pytest.mark.parametrize("datacenter,eventid", ABSENT_EVENTS, ids=PROVIDERS)
def test_absent_event_returns_empty_catalog(datacenter, eventid):
    """Absence must surface as an empty catalog, not as an error.

    Providers signal it differently -- 204 at INGV/EMSC/GFZ, 404 at USGS -- and both
    have to arrive here as "no data" for the found/not-found contract to hold.
    """
    catalog, _ = run(get_event_by_id(eventid=eventid, datacenter=datacenter))
    assert len(catalog) == 0


# --- Provider-specific behaviour -------------------------------------------

def test_ingv_rejects_inverted_window_with_verbatim_message():
    """INGV is the only advertised provider that validates the time window.

    EMSC and GFZ answer 204 and USGS ignores the inversion entirely, so this is
    asserted for INGV alone rather than parametrised (see the module docstring).
    """
    with pytest.raises(DatacenterError) as ei:
        run(query_events_text(
            starttime="2012-05-29T00:00:00", endtime="2010-01-01T00:00:00",
            datacenter="INGV",
        ))
    assert ei.value.status == 400
    assert ei.value.message  # carries the datacenter's verbatim text


def test_ingv_offset_changes_results():
    """Pagination is FDSN passthrough; see ADR-0003 on INGV's off-by-one."""
    _, page1, _ = run(
        query_events_text(**INGV_WINDOW, limit=3, offset=1, datacenter="INGV")
    )
    _, page2, _ = run(
        query_events_text(**INGV_WINDOW, limit=3, offset=4, datacenter="INGV")
    )
    ids1 = {r[0] for r in page1}
    ids2 = {r[0] for r in page2}
    assert ids1 and ids2 and ids1 != ids2


@pytest.mark.parametrize("datacenter", PROVIDERS)
def test_pagination_offset_advances(datacenter):
    """A second page must not repeat the first, on every provider that pages."""
    _, page1, _ = run(
        query_events_text(**WINDOW, minmag=4.0, limit=2, offset=1, datacenter=datacenter)
    )
    _, page2, _ = run(
        query_events_text(**WINDOW, minmag=4.0, limit=2, offset=3, datacenter=datacenter)
    )
    if not page1 or not page2:
        pytest.skip(f"{datacenter} returned too few events to page")
    assert {r[0] for r in page1} != {r[0] for r in page2}


def test_iris_event_service_is_retired():
    """IRIS/EarthScope no longer serves FDSNWS event: HTTP 410 Gone (ADR-0007).

    Pinned as a test so that if the service ever comes back, this fails and prompts
    us to re-advertise it, instead of the omission quietly outliving its reason.
    """
    with pytest.raises(DatacenterError) as ei:
        run(query_events_text(**WINDOW, minmag=5.0, limit=1, datacenter="IRIS"))
    assert ei.value.status == 410
