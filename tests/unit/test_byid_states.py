"""Unit tests for the three-state by-eventid contract (offline, network mocked).

The by-eventid tools must distinguish:
  1. event not found        -> found=False + actionable message
  2. event found, data       -> found=True, no message
  3. event found, no data    -> found=True, count=0 + explanatory message

This guards against the failure mode where a hallucinated eventid (e.g. 123456)
returned an empty payload indistinguishable from "event exists but has no data",
giving the model no signal to self-correct.

Since 2.0.0 three of these tools answer with a table (columns + rows) instead of
nested QuakeML, which adds a fourth state the contract has to keep apart from
state 3: the event *does* carry the resource, but the caller's own network or
station filter matched none of it. Saying "this event has no station magnitudes"
there would be a plain falsehood and would send the caller away from data that
is right in front of it, so it gets its own message.
"""

import asyncio
import json

import pytest
from obspy import UTCDateTime
from obspy.core.event import Arrival, Catalog, Event, Magnitude, Origin, Pick
from obspy.core.event.base import ResourceIdentifier, WaveformStreamID

import fdsnws_event_server.server as server
from fdsnws_event_server import tables


def run(coro):
    return asyncio.run(coro)


def make_event(event_id: str = "46166442") -> Event:
    """A minimal ObsPy Event whose resource_id carries a numeric eventId."""
    return Event(
        resource_id=ResourceIdentifier(
            id=f"smi:webservices.ingv.it/fdsnws/event/1/query?eventId={event_id}"
        )
    )


def make_event_with_one_arrival(event_id: str = "46166442") -> Event:
    """An event carrying exactly one arrival, its pick, and one origin.

    Hand-built rather than loaded from a fixture because the states under test
    are about counts and messages, not about any provider's QuakeML dialect: one
    row is the smallest thing that is not zero. The waveform id is populated so
    the station filter has something to match, and to miss.
    """
    event = make_event(event_id)
    pick = Pick(
        resource_id=ResourceIdentifier(id="smi:test/pick/1"),
        time=UTCDateTime("2024-01-01T00:00:01"),
        phase_hint="P",
        waveform_id=WaveformStreamID(
            network_code="IV", station_code="SGRT", channel_code="HHZ"
        ),
    )
    origin = Origin(
        resource_id=ResourceIdentifier(id="smi:test/origin/1"),
        time=UTCDateTime("2024-01-01T00:00:00"),
        arrivals=[
            Arrival(
                resource_id=ResourceIdentifier(id="smi:test/arrival/1"),
                pick_id=pick.resource_id,
                phase="P",
                distance=0.5,
            )
        ],
    )
    event.picks = [pick]
    event.origins = [origin]
    event.preferred_origin_id = origin.resource_id
    return event


def patch_fetch(monkeypatch, name: str, catalog: Catalog):
    """Patch a by-eventid fetch function in the server namespace to return ``catalog``."""

    async def fake(eventid, datacenter="INGV"):
        return catalog, f"https://example/query?eventid={eventid}"

    monkeypatch.setattr(server, name, fake)


# --- State 1: event not found (empty catalog) -------------------------------
#
# The fourth element is the column tuple for a table-returning tool, or None for
# one that answers with nested QuakeML under a key named after the count.

NOT_FOUND_CASES = [
    (
        "fdsn_get_arrivals_by_eventid",
        "get_arrivals_by_eventid",
        "arrivals_count",
        tables.ARRIVAL_COLUMNS,
    ),
    (
        "fdsn_get_stationmagnitudes_by_eventid",
        "get_stationmagnitudes_by_eventid",
        "station_magnitudes_count",
        tables.STATIONMAGNITUDE_COLUMNS,
    ),
    (
        "fdsn_get_amplitudes_by_eventid",
        "get_amplitudes_by_eventid",
        "amplitudes_count",
        tables.AMPLITUDE_COLUMNS,
    ),
    (
        "fdsn_get_allmagnitudes_by_eventid",
        "get_allmagnitudes_by_eventid",
        "magnitudes_count",
        None,
    ),
    ("fdsn_get_allorigins_by_eventid", "get_allorigins_by_eventid", "origins_count", None),
    (
        "fdsn_get_focalmechanism_by_eventid",
        "get_focalmechanism_by_eventid",
        "focal_mechanisms_count",
        None,
    ),
]
NOT_FOUND_IDS = [c[0].removeprefix("fdsn_get_") for c in NOT_FOUND_CASES]


@pytest.mark.parametrize(
    "tool_name,fetch_name,count_key,columns", NOT_FOUND_CASES, ids=NOT_FOUND_IDS
)
def test_not_found_signals_found_false_with_message(
    monkeypatch, tool_name, fetch_name, count_key, columns
):
    patch_fetch(monkeypatch, fetch_name, Catalog())
    tool = getattr(server, tool_name)
    out = json.loads(run(tool(eventid=123456)))

    assert out["found"] is False
    assert out["event_id"] is None
    assert out[count_key] == 0
    # The bad id must be echoed so the model sees the mismatch and can retry.
    assert "123456" in out["message"]
    assert "fdsn_query_earthquakes" in out["message"]
    # The empty answer still has the shape of a full one, so a caller that reads
    # the result positionally does not break on the not-found path.
    if columns is None:
        assert out[count_key.removesuffix("_count")] == []
    else:
        assert out["columns"] == list(columns)
        assert out["rows"] == []


def test_get_earthquake_by_eventid_not_found(monkeypatch):
    # fdsn_get_earthquake_by_eventid has only 2 states (found / not found).
    patch_fetch(monkeypatch, "get_event_by_eventid", Catalog())
    out = json.loads(run(server.fdsn_get_earthquake_by_eventid(eventid=999999)))

    assert out["found"] is False
    assert out["event"] is None
    assert "999999" in out["message"]


def test_get_earthquake_by_eventid_reports_counts_but_never_arrivals(monkeypatch):
    """The event summary says what is worth fetching, and stays silent on arrivals.

    This fetch sends no includearrivals, so every origin comes back with an empty
    arrival list. A count taken from it would read as a confident "this event has
    no phases" and would stop the caller from asking the tool that does know, so
    the key is deliberately absent -- while the two counts that *are* reliable are
    reported, because they are how the caller decides whether to page a table at all.
    """
    patch_fetch(
        monkeypatch, "get_event_by_eventid", Catalog(events=[make_event_with_one_arrival()])
    )
    out = json.loads(run(server.fdsn_get_earthquake_by_eventid(eventid=46166442)))

    assert out["found"] is True
    assert out["event"]["event_id"] == "46166442"
    assert "arrivals_count" not in out["event"]
    assert out["event"]["station_magnitudes_count"] == 0
    assert out["event"]["amplitudes_count"] == 0


# --- State 3: event found but sub-resource absent ---------------------------

ABSENT_CASES = [
    ("fdsn_get_arrivals_by_eventid", "get_arrivals_by_eventid", "arrivals_count", "arrivals"),
    (
        "fdsn_get_stationmagnitudes_by_eventid",
        "get_stationmagnitudes_by_eventid",
        "station_magnitudes_count",
        "station magnitudes",
    ),
    (
        "fdsn_get_amplitudes_by_eventid",
        "get_amplitudes_by_eventid",
        "amplitudes_count",
        "amplitudes",
    ),
    (
        "fdsn_get_allmagnitudes_by_eventid",
        "get_allmagnitudes_by_eventid",
        "magnitudes_count",
        "magnitude",
    ),
    ("fdsn_get_allorigins_by_eventid", "get_allorigins_by_eventid", "origins_count", "origin"),
    (
        "fdsn_get_focalmechanism_by_eventid",
        "get_focalmechanism_by_eventid",
        "focal_mechanisms_count",
        "focal mechanism",
    ),
]
ABSENT_IDS = [c[0].removeprefix("fdsn_get_") for c in ABSENT_CASES]


@pytest.mark.parametrize("tool_name,fetch_name,count_key,word", ABSENT_CASES, ids=ABSENT_IDS)
def test_found_but_absent_resource(monkeypatch, tool_name, fetch_name, count_key, word):
    patch_fetch(monkeypatch, fetch_name, Catalog(events=[make_event("46166442")]))
    tool = getattr(server, tool_name)
    out = json.loads(run(tool(eventid=46166442)))

    assert out["found"] is True
    assert out["event_id"] == "46166442"
    assert out[count_key] == 0
    assert word in out["message"]
    # must NOT claim the event itself is missing
    assert "No event with eventid" not in out["message"]


# --- State 2: event found with data -> no message ---------------------------

def test_found_with_data_has_no_message(monkeypatch):
    event = make_event("46166442")
    mag = Magnitude(resource_id=ResourceIdentifier(), mag=2.5, magnitude_type="ML")
    event.magnitudes = [mag]
    event.preferred_magnitude_id = mag.resource_id
    patch_fetch(monkeypatch, "get_allmagnitudes_by_eventid", Catalog(events=[event]))

    out = json.loads(run(server.fdsn_get_allmagnitudes_by_eventid(eventid=46166442)))

    assert out["found"] is True
    assert out["magnitudes_count"] == 1
    assert "message" not in out


def test_table_found_with_data_has_no_message(monkeypatch):
    """Same state for a table tool: a populated page explains nothing.

    ``arrivals_count`` is the event's own count and ``returned_count`` the size of
    this page; on an unpaged, unfiltered result the two agree, and both must line
    up with the rows actually sent.
    """
    patch_fetch(
        monkeypatch,
        "get_arrivals_by_eventid",
        Catalog(events=[make_event_with_one_arrival()]),
    )

    out = json.loads(run(server.fdsn_get_arrivals_by_eventid(eventid=46166442)))

    assert out["found"] is True
    assert out["arrivals_count"] == 1
    assert out["total_count"] == 1
    assert out["returned_count"] == len(out["rows"]) == 1
    assert out["columns"] == list(tables.ARRIVAL_COLUMNS)
    assert len(out["rows"][0]) == len(out["columns"])
    assert out["has_more"] is False
    assert "next_offset" not in out
    assert "message" not in out


# --- State 4: the resource is there, the caller's filter matched none of it ---

def test_filtered_to_nothing_is_not_reported_as_absence(monkeypatch):
    """An empty *page* must not be worded as an empty *event*.

    The two collapse into the same "no rows" if only the row count is looked at,
    and conflating them is how a caller ends up told that an event with arrivals
    has none. The message therefore has to name the filters and the count they
    were applied to.
    """
    patch_fetch(
        monkeypatch,
        "get_arrivals_by_eventid",
        Catalog(events=[make_event_with_one_arrival()]),
    )

    out = json.loads(
        run(server.fdsn_get_arrivals_by_eventid(eventid=46166442, station="NOPE"))
    )

    assert out["found"] is True
    assert out["arrivals_count"] == 1  # the event's own count ignores the filter
    assert out["rows"] == []
    assert "none match the filters" in out["message"]
    assert "NOPE" in out["message"]
    # The absence wording of state 3 must not appear for a filtered-out result.
    assert "has no" not in out["message"]


def test_matching_filter_keeps_the_row(monkeypatch):
    """The counterpart: the filter is a real filter, not a way to empty the table."""
    patch_fetch(
        monkeypatch,
        "get_arrivals_by_eventid",
        Catalog(events=[make_event_with_one_arrival()]),
    )

    out = json.loads(
        run(
            server.fdsn_get_arrivals_by_eventid(
                eventid=46166442, network="iv", station="sgrt"
            )
        )
    )

    # Matching is case-insensitive: a model that types the code in lower case is
    # asking the same question as one that shouts it.
    assert out["returned_count"] == 1
    assert "message" not in out


# --- Prevention (#1): eventid description must carry provenance + anti-invention ---

from fdsnws_event_server.models import (  # noqa: E402
    GetAllMagnitudesByEventIdInput,
    GetAllOriginsByEventIdInput,
    GetAmplitudesByEventIdInput,
    GetArrivalsByEventIdInput,
    GetEarthquakeByEventIdInput,
    GetFocalMechanismByEventIdInput,
    GetStationMagnitudesByEventIdInput,
)

BY_EVENTID_MODELS = [
    GetEarthquakeByEventIdInput,
    GetArrivalsByEventIdInput,
    GetAllMagnitudesByEventIdInput,
    GetAllOriginsByEventIdInput,
    GetFocalMechanismByEventIdInput,
    GetStationMagnitudesByEventIdInput,
    GetAmplitudesByEventIdInput,
]


@pytest.mark.parametrize("model", BY_EVENTID_MODELS, ids=[m.__name__ for m in BY_EVENTID_MODELS])
def test_eventid_description_states_provenance(model):
    desc = model.model_fields["eventid"].description
    assert "fdsn_query_earthquakes" in desc
    assert "Do NOT invent" in desc


@pytest.fixture(scope="module")
def fixture_catalog():
    """A captured INGV event with all three station-level collections populated.

    The synthetic events above carry one arrival apiece, which is enough for the
    state contract and far too little to make a page overflow anything. Sizing
    needs real width: 150 arrivals, 575 station magnitudes, 1235 amplitudes.
    """
    from pathlib import Path

    from obspy import read_events

    path = Path(__file__).resolve().parents[1] / "fixtures" / "ingv_arrivals.quakeml.xml"
    return read_events(str(path))


# --- The budget binds the string the tool returns, not an intermediate ---------
#
# `paginate` measures the dict it is about to return, which is the right place to
# trim but the wrong place to *verify*: every key the tool adds afterwards widens
# the JSON past the size the guard certified. Two such keys existed and neither
# was caught, because the table-level test measures paginate's own output. These
# tests measure `len(tool(...))` -- the bytes that actually reach the model.

@pytest.mark.parametrize("budget", [36_000, 20_000, 10_000, 6_000])
@pytest.mark.parametrize(
    "tool_name,fetch_name,limit_name",
    [
        ("fdsn_get_arrivals_by_eventid", "get_arrivals_by_eventid", "MAX_ROWS_ARRIVALS"),
        (
            "fdsn_get_stationmagnitudes_by_eventid",
            "get_stationmagnitudes_by_eventid",
            "MAX_ROWS_STATIONMAGNITUDES",
        ),
        ("fdsn_get_amplitudes_by_eventid", "get_amplitudes_by_eventid", "MAX_ROWS_AMPLITUDES"),
    ],
)
def test_table_tool_output_never_exceeds_the_budget(
    monkeypatch, fixture_catalog, tool_name, fetch_name, limit_name, budget
):
    """The returned string must fit the budget, envelope keys and all.

    A result one byte over is harmless; a result over by the width of whatever
    key was appended last is a guarantee that quietly is not one, and this
    release exists because an oversized tool result costs the user their own
    question. Parametrized to budgets well below the shipped default because the
    overrun is a fixed number of bytes: invisible at 36 kB, decisive at 6 kB.
    """
    monkeypatch.setattr(server.config, "MAX_RESULT_BYTES", budget)
    patch_fetch(monkeypatch, fetch_name, fixture_catalog)

    # The tool's own advertised maximum, so the request is one the schema allows
    # and the guard is the only thing that can shorten the page.
    limit = getattr(server.config, limit_name)
    out = run(getattr(server, tool_name)(eventid="45376822", limit=limit))
    payload = json.loads(out)
    if payload.get("error"):
        pytest.skip("budget below the fixed envelope cost; covered by RowTooLargeError")

    assert len(out) <= budget, (
        f"{tool_name} returned {len(out)} bytes for "
        f"{payload['returned_count']} rows, over the {budget} byte budget"
    )
    assert payload["returned_count"] >= 1


def test_event_query_output_never_exceeds_the_budget(monkeypatch):
    """Same guarantee for fdsn_query_earthquakes, which trims on a different path.

    Its rows come from the datacenter already cut to `limit`, so it cannot
    reuse `paginate`; it calls `trim_to_budget` directly and then adds its own
    pagination keys and truncation note. Those were not accounted for.
    """
    budget = 4_000
    monkeypatch.setattr(server.config, "MAX_RESULT_BYTES", budget)

    columns = ["#EventID", "Time", "Latitude", "Longitude", "Depth/Km",
               "Author", "MagType", "Magnitude", "EventLocationName"]
    rows = [
        [f"4616{i:04d}", "2026-06-09T01:24:20.220000", "38.6887", "15.5158",
         "159.4", "SURVEY-INGV", "ML", "2.2", "Tirreno Meridionale [Mare]"]
        for i in range(400)
    ]

    async def fake_query(**kwargs):
        return columns, rows, "https://example.invalid/query"

    monkeypatch.setattr(server, "query_events_text", fake_query)

    out = run(server.fdsn_query_earthquakes(limit=240))
    payload = json.loads(out)
    assert len(out) <= budget, (
        f"fdsn_query_earthquakes returned {len(out)} bytes for "
        f"{payload['returned_count']} rows, over the {budget} byte budget"
    )
    assert payload["has_more"] is True
    assert payload["next_offset"] == payload["offset"] + payload["returned_count"]


def test_table_tool_budget_holds_across_a_dense_sweep(monkeypatch, fixture_catalog):
    """Sweep consecutive budgets, because an overrun of a few bytes hides.

    The trim loop stops at the first page that fits, so the finished result
    normally lands comfortably under the ceiling and a fixed overshoot of twenty
    or thirty bytes -- the width of one key appended after the measurement --
    only shows when the page happens to land within that distance of the budget.
    Sampling a handful of round numbers can miss it for a whole release; that is
    how `arrivals_count` and the truncation note got through review. Stepping
    through a range guarantees the boundary is hit.
    """
    patch_fetch(monkeypatch, "get_arrivals_by_eventid", fixture_catalog)
    limit = server.config.MAX_ROWS_ARRIVALS

    over = []
    for budget in range(12_000, 12_600):
        monkeypatch.setattr(server.config, "MAX_RESULT_BYTES", budget)
        out = run(server.fdsn_get_arrivals_by_eventid(eventid="45376822", limit=limit))
        if len(out) > budget:
            over.append((budget, len(out), len(out) - budget))

    assert not over, (
        f"{len(over)} of 600 budgets produced an oversized result; "
        f"first three: {over[:3]} (budget, bytes, overrun)"
    )
