"""Live integration tests against real datacenters (opt-in: `pytest -m integration`).

Parametrised over the advertised providers so the multi-datacenter claim is actually
exercised rather than only asserted. The offline mirror of this file is
``tests/unit/test_multidatacenter.py``, which runs the same matrix against captured
fixtures; keep the two in step.

Only behaviour that is genuinely uniform across providers is parametrised. Providers
disagree sharply on invalid input -- an inverted time window yields HTTP 400 at INGV,
204 at EMSC and GFZ, and is silently *ignored* by USGS, which answers 200 with data --
so those cases are asserted per provider instead of pretending to a common contract.

IRIS/EarthScope is not in the provider list: its FDSNWS event service is retired.
That retirement is itself pinned by a test at the end of this module.

The lower half of the module tests the 2.0.0 tool surface through
the tool functions in ``server``, not through ``obspy_client``, because the claims it
has to pin are properties of the *envelope* the model sees. Four of them are claims
about live services that no fixture can settle, and they are the reason this file
exists at all rather than being folded into the offline mirror:

  - USGS answers HTTP 501 to ``includearrivals``. The station magnitude and amplitude
    tools retry without the flag and return the absence state; the arrivals
    tool, which has nothing to fall back on, surfaces the 501 as a structured error.
  - EMSC publishes station magnitudes and amplitudes *only* when ``includearrivals``
    is sent, which is why the two new tools always send it.
  - EMSC returns many origins for one event id although ``includeallorigins`` is never
    sent, so arrival rows span origins and ``is_preferred_origin`` earns its column.
  - identifier prefixes are computed from the data, so the round trip
    ``id_prefixes[col] + row value`` has to be checked against what each node really
    publishes, not against a captured sample of it.

Live catalogues are revised, so the counts above are asserted as relations
(``> 1``, ``> 0``, ``== total_count``) and never as the numbers observed on the day.
Events are likewise chosen by querying for one, falling back on the historical ids
below only while they still resolve.
"""

import asyncio
import json
from functools import lru_cache

import pytest
import requests

from fdsnws_event_server import config, server, tables
from fdsnws_event_server.obspy_client import (
    DatacenterError,
    _event_query_url,
    _extract_event_id,
    get_arrivals_by_eventid,
    get_event_by_eventid,
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
KNOWN_EVENT_BY_PROVIDER = dict(KNOWN_EVENTS)

# Well-formed but non-existent ids, each following its provider's own grammar.
ABSENT_EVENTS = [
    ("INGV", "999999999999"),
    ("EMSC", "20240101_9999999"),
    ("GFZ", "gfz9999zzzz"),
    ("USGS", "us9999zzzzzz"),
]

# The original INGV window, retained for the pagination check.
INGV_WINDOW = {"starttime": "2012-05-29T00:00:00", "endtime": "2012-05-29T23:59:59"}

# Where to look for an event that actually carries station-level data, when the
# historical id above carries none. INGV needs this: 37258271 is a Japanese event
# in the INGV catalogue and has no Italian picks at all, and neither do the
# largest recent magnitudes, which are foreign events relayed without phases --
# hence the box around Italy and the modest magnitude floor rather than
# `orderby=magnitude` on its own.
RICH_EVENT_SEARCH = {
    "INGV": {
        "starttime": "2026-01-01T00:00:00", "endtime": "2026-06-01T00:00:00",
        "minmag": 3.5,
        "minlat": 36.0, "maxlat": 47.5, "minlon": 6.0, "maxlon": 19.0,
    },
    "EMSC": {**WINDOW, "minmag": 5.0},
    "GFZ": {**WINDOW, "minmag": 5.0},
    "USGS": {**WINDOW, "minmag": 5.0},
}

# How many candidates from that search to probe before giving up. Each probe is a
# full QuakeML download (2 MB on a well-recorded INGV event), so the walk is kept
# short; a window that needs more than this is the wrong window.
MAX_EVENT_CANDIDATES = 8


def run(coro):
    return asyncio.run(coro)


def tool_result(coro) -> dict:
    """Run a tool coroutine and parse the JSON string it returns."""
    return json.loads(run(coro))


@lru_cache(maxsize=None)
def rich_event(datacenter: str) -> str:
    """An event id at ``datacenter`` that really carries arrivals, found live.

    The historical id is tried first, so a test keeps using the same event as the
    fixtures for as long as that event still answers. When it carries no phases --
    which is a property of the event, never of the node -- the catalogue is
    queried and the candidates are walked until one does. Skips rather than fails
    if the search comes up empty: that means the window has gone stale or the
    service is down, and neither is the behaviour under test.
    """
    candidates = [KNOWN_EVENT_BY_PROVIDER[datacenter]]
    try:
        _, rows, _ = run(
            query_events_text(
                **RICH_EVENT_SEARCH[datacenter],
                limit=MAX_EVENT_CANDIDATES, orderby="magnitude", datacenter=datacenter,
            )
        )
    except DatacenterError as e:
        pytest.skip(f"{datacenter} is not answering the candidate search: {e}")
    candidates += [row[0] for row in rows]

    for candidate in candidates[: MAX_EVENT_CANDIDATES + 1]:
        result = tool_result(
            server.fdsn_get_arrivals_by_eventid(
                eventid=candidate, datacenter=datacenter, limit=1
            )
        )
        if not result.get("error") and result.get("total_count"):
            return candidate
    pytest.skip(f"no event with arrivals found at {datacenter} among {len(candidates)} candidates")


@lru_cache(maxsize=None)
def walk_arrival_pages(datacenter: str, eventid: str, limit: int) -> tuple:
    """Page a whole arrival table with ``next_offset`` and keep every page.

    Returns ``(pages, rows)`` with the pages as parsed envelopes in order, so a
    test can assert on the walk itself as well as on what it collected. Cached
    because the walk is several full QuakeML downloads and two claims need it.
    """
    pages = []
    rows = []
    offset = 1
    while True:
        page = tool_result(
            server.fdsn_get_arrivals_by_eventid(
                eventid=eventid, datacenter=datacenter, limit=limit, offset=offset
            )
        )
        assert not page.get("error"), page.get("message")
        pages.append(page)
        rows.extend(page["rows"])
        if not page["has_more"]:
            break
        # A next_offset that does not advance would loop forever against a live
        # service; fail the test instead of hanging the suite.
        assert page["next_offset"] > offset, "next_offset did not advance"
        offset = page["next_offset"]
    return tuple(pages), tuple(tuple(r) for r in rows)


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


@pytest.mark.parametrize("datacenter", PROVIDERS)
def test_query_tool_normalizes_columns_and_flattens_pagination(datacenter):
    """The tool output is normalized where the raw header above is not.

    Whatever each node calls its columns, the model is shown the FDSN 1.2 names,
    and the paging keys sit at the top level rather than in a nested object.
    EventType is absent from the EMSC and USGS headers, so only the thirteen
    columns every provider does publish are asserted in order.
    """
    result = tool_result(
        server.fdsn_query_earthquakes(**WINDOW, minmag=5.0, limit=3, datacenter=datacenter)
    )
    assert result["columns"][:13] == list(tables.EVENT_COLUMNS[:13])
    assert result["rows"]
    assert "pagination" not in result
    for key in ("datacenter", "api_url", "ordered_by", "returned_count",
                "limit", "offset", "has_more"):
        assert key in result, f"{key} missing from the {datacenter} envelope"


# --- Detail path, all providers --------------------------------------------

@pytest.mark.parametrize("datacenter,eventid", KNOWN_EVENTS, ids=PROVIDERS)
def test_get_event_by_eventid_returns_quakeml(datacenter, eventid):
    """The by-eventid path must work with each provider's native identifier format.

    This is the check that was impossible before `eventid` became a string: GFZ and
    USGS ids were rejected by input validation, and EMSC's was silently mangled.
    """
    catalog, _ = run(get_event_by_eventid(eventid=eventid, datacenter=datacenter))
    assert len(catalog) == 1


@pytest.mark.parametrize("datacenter,eventid", KNOWN_EVENTS, ids=PROVIDERS)
def test_event_id_round_trips(datacenter, eventid):
    """The id extracted from the returned QuakeML must match the one requested."""
    catalog, _ = run(get_event_by_eventid(eventid=eventid, datacenter=datacenter))
    assert _extract_event_id(catalog[0]) == eventid


@pytest.mark.parametrize("datacenter,eventid", ABSENT_EVENTS, ids=PROVIDERS)
def test_absent_event_returns_empty_catalog(datacenter, eventid):
    """Absence must surface as an empty catalog, not as an error.

    Providers signal it differently -- 204 at INGV/EMSC/GFZ, 404 at USGS -- and both
    have to arrive here as "no data" for the found/not-found contract to hold.
    """
    catalog, _ = run(get_event_by_eventid(eventid=eventid, datacenter=datacenter))
    assert len(catalog) == 0


@pytest.mark.parametrize("datacenter,eventid", KNOWN_EVENTS, ids=PROVIDERS)
def test_earthquake_tool_reports_counts_but_no_arrival_count(datacenter, eventid):
    """The event tool stopped inlining the station-level collections.

    It reports how many station magnitudes and amplitudes there are, so the caller
    knows whether the dedicated tools have anything to fetch, and deliberately
    reports no arrival count: this fetch sends no includearrivals, so any number it
    could print would be a confident zero.
    """
    result = tool_result(
        server.fdsn_get_earthquake_by_eventid(eventid=eventid, datacenter=datacenter)
    )
    assert result["found"] is True
    event = result["event"]
    assert event["event_id"] == eventid
    for key in ("preferred_origin", "preferred_magnitude", "origins_count",
                "magnitudes_count", "station_magnitudes_count", "amplitudes_count"):
        assert key in event, f"{key} missing from the {datacenter} event payload"
    assert "arrivals_count" not in event
    assert "picks" not in event
    assert "amplitudes" not in event
    assert "station_magnitudes" not in event


# --- USGS: a node that does not implement includearrivals ------------------

@pytest.mark.parametrize(
    "tool,count_key",
    [
        (server.fdsn_get_stationmagnitudes_by_eventid, "station_magnitudes_count"),
        (server.fdsn_get_amplitudes_by_eventid, "amplitudes_count"),
    ],
    ids=["stationmagnitudes", "amplitudes"],
)
def test_usgs_station_tools_recover_from_501(tool, count_key):
    """USGS answers 501 to includearrivals; the retry must not become an error.

    The two tools always send the flag because EMSC needs it, so USGS refuses every
    first attempt. Retrying without it is what turns a dead end into the
    absence state: the event is found, the table is empty and the message says so,
    which the caller can act on. The retried URL carries no flag, which is what
    distinguishes a real recovery from a node that simply has no data.
    """
    eventid = KNOWN_EVENT_BY_PROVIDER["USGS"]
    result = tool_result(tool(eventid=eventid, datacenter="USGS"))
    assert "error" not in result, result.get("message")
    assert result["found"] is True
    assert result["event_id"] == eventid
    assert result["rows"] == []
    assert result["total_count"] == 0
    assert result[count_key] == 0
    assert result["message"]
    assert "includearrivals" not in result["api_url"]


def test_usgs_arrivals_surfaces_the_501_as_a_structured_error():
    """The arrivals tool has nothing to fall back on, so the 501 must reach the caller.

    Dropping includearrivals here would answer "this event has no phase arrivals",
    which is a different and false statement. The datacenter's own words are passed
    through verbatim so the caller can tell the two apart.
    """
    result = tool_result(
        server.fdsn_get_arrivals_by_eventid(
            eventid=KNOWN_EVENT_BY_PROVIDER["USGS"], datacenter="USGS"
        )
    )
    assert result["error"] is True
    assert result["datacenter"] == "USGS"
    assert "501" in result["message"]
    assert "includearrivals" in result["api_url"]


# --- EMSC: station-level data only arrives with includearrivals ------------

@pytest.mark.parametrize(
    "tool,count_key",
    [
        (server.fdsn_get_stationmagnitudes_by_eventid, "station_magnitudes_count"),
        (server.fdsn_get_amplitudes_by_eventid, "amplitudes_count"),
    ],
    ids=["stationmagnitudes", "amplitudes"],
)
def test_emsc_publishes_station_level_data_only_with_includearrivals(tool, count_key):
    """A plain EMSC fetch returns neither collection; with the flag, both arrive.

    This is the whole reason the two tools send a flag that looks irrelevant to
    what they return. The assertion pins the mechanism as well as the outcome: the
    URL must carry the flag, and rows must come back.
    """
    eventid = KNOWN_EVENT_BY_PROVIDER["EMSC"]
    result = tool_result(tool(eventid=eventid, datacenter="EMSC"))
    assert "error" not in result, result.get("message")
    assert "includearrivals" in result["api_url"]
    assert result["found"] is True
    assert result[count_key] > 0, f"EMSC published no {count_key} for {eventid}"
    assert result["total_count"] > 0
    assert result["rows"]

    plain, _ = run(get_event_by_eventid(eventid=eventid, datacenter="EMSC"))
    collection = ("station_magnitudes" if "station" in count_key else "amplitudes")
    assert len(getattr(plain[0], collection)) == 0, (
        f"EMSC now publishes {collection} without includearrivals; the flag may no "
        "longer be load-bearing, so re-check why the flag is sent before relaxing anything"
    )


def test_emsc_arrivals_span_several_origins():
    """One EMSC event id yields many origins although includeallorigins is never sent.

    Filtering to the preferred origin would therefore have hidden half the arrivals,
    which is why the rows carry origin_id and is_preferred_origin instead. Both
    values have to appear across the whole table -- not in the first page, where the
    preferred rows sort first by design.
    """
    eventid = KNOWN_EVENT_BY_PROVIDER["EMSC"]
    pages, rows = walk_arrival_pages("EMSC", eventid, config.DEFAULT_ROWS_ARRIVALS)

    assert pages[0]["origins_count"] > 1, "EMSC returned a single origin"
    assert "includeallorigins" not in pages[0]["api_url"]

    columns = pages[0]["columns"]
    preferred_i = columns.index("is_preferred_origin")
    origin_i = columns.index("origin_id")
    flags = {row[preferred_i] for row in rows}
    assert flags == {True, False}, f"expected both origin flags, got {flags}"
    assert len({row[origin_i] for row in rows}) > 1


# --- Identifier prefixes and pagination, on live data ----------------------

@pytest.mark.parametrize("datacenter", ["INGV", "EMSC", "GFZ"])
def test_id_prefix_round_trip_reconstructs_live_identifiers(datacenter):
    """prefix + row value must be the identifier the node actually published.

    The prefix is derived from the response, so this is the one property that a
    captured fixture cannot pin: it has to hold against whatever identifier grammar
    each node is using today -- INGV's query-string ids, EMSC's path ids and GFZ's
    opaque ones. Checked against the resource ids parsed straight out of the same
    QuakeML, so an encoding that lost a character would fail here.
    """
    eventid = rich_event(datacenter)
    result = tool_result(
        server.fdsn_get_arrivals_by_eventid(
            eventid=eventid, datacenter=datacenter,
            limit=min(20, config.MAX_ROWS_ARRIVALS),
        )
    )
    assert not result.get("error"), result.get("message")
    assert result["rows"]

    catalog, _ = run(get_arrivals_by_eventid(eventid=eventid, datacenter=datacenter))
    event = catalog[0]
    published = {
        "arrival_id": {str(a.resource_id) for o in event.origins for a in o.arrivals},
        "pick_id": {str(p.resource_id) for p in event.picks},
        "origin_id": {str(o.resource_id) for o in event.origins},
    }

    prefixes = result["id_prefixes"]
    assert prefixes, f"{datacenter} identifiers were not factored at all"
    checked = 0
    for column, truth in published.items():
        if column not in prefixes or not truth:
            continue
        i = result["columns"].index(column)
        for row in result["rows"]:
            if not isinstance(row[i], str):
                continue
            full = prefixes[column] + row[i]
            assert full in truth, f"{datacenter} {column} did not reconstruct: {full}"
            checked += 1
    assert checked, f"no prefixed identifier column to check at {datacenter}"


def test_paging_by_next_offset_visits_every_row_exactly_once():
    """Walking next_offset must yield the whole table, with nothing lost or repeated.

    Offset paging is only coherent over a total order, so this is the test that the
    ordering really is total on live data: the EMSC event spreads its arrivals over
    several origins and reuses pick identifiers between them, which is exactly the
    shape that breaks a partial sort. Row identity is the whole row, because the
    trailing segment of an EMSC arrival id repeats across origins.
    """
    eventid = KNOWN_EVENT_BY_PROVIDER["EMSC"]
    limit = config.DEFAULT_ROWS_ARRIVALS
    pages, rows = walk_arrival_pages("EMSC", eventid, limit)

    total = pages[0]["total_count"]
    assert total > limit, "this event no longer needs paging; pick a busier one"
    assert len(pages) > 1
    assert len(rows) == total
    assert len(set(rows)) == total, "paging returned a row twice"

    for page in pages[:-1]:
        assert page["has_more"] is True
        assert page["next_offset"] == page["offset"] + page["returned_count"]
    last = pages[-1]
    assert last["has_more"] is False
    assert "next_offset" not in last
    assert sum(p["returned_count"] for p in pages) == total


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
    """Pagination is FDSN passthrough; INGV has an off-by-one on explicit offset."""
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
    """IRIS/EarthScope still serves no FDSNWS event data.

    Pinned as a test so that if the service ever comes back, this fails and prompts
    us to re-advertise it, instead of the omission quietly outliving its reason.

    How the retirement is *signalled* has moved and the assertion moved with it.
    ObsPy 1.5.0 remapped IRIS (and IRISDMC, IRISPH5, EARTHSCOPE) from
    service.iris.edu to service.earthscope.org, which answers the retired event
    endpoint with HTTP 404 and the body "This service has been retired" instead of
    the 410 service.iris.edu used to return. 404 is the FDSN code for "no data", so
    it reaches this server as an empty result rather than as a DatacenterError --
    correctly, since nothing distinguishes it from any other empty catalogue at the
    HTTP layer. The raw status is therefore asserted separately from the client
    behaviour, so a resurrected service fails this test either way.
    """
    columns, rows, _ = run(
        query_events_text(**WINDOW, minmag=5.0, limit=1, datacenter="IRIS")
    )
    assert not rows and not columns, "IRIS/EarthScope is serving events again"

    endpoint = _event_query_url("IRIS")
    assert "earthscope" in endpoint, f"IRIS now maps to {endpoint}; re-check this test"
    resp = requests.get(endpoint, params={"format": "text", "limit": 1}, timeout=30)
    assert resp.status_code in (404, 410), f"unexpected {resp.status_code} from {endpoint}"
    assert "retired" in resp.text.lower()
