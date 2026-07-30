"""Multi-datacenter compatibility matrix, offline (fixtures + mocked network).

The advertised providers do not agree on much: identifier format, column set,
column spelling, and which HTTP status means "no such event" all differ. This
module pins that behaviour down per provider so the matrix stays reproducible
when the services are unreachable, using responses captured in ``fixtures/``.

Live counterparts live in ``tests/integration/test_live_datacenters.py``; the two
must be kept in step. IRIS/EarthScope is absent on purpose: its FDSNWS event
service returns HTTP 410, so it cannot be covered at all.
"""

import asyncio
import json
from pathlib import Path

import pytest
from obspy import read_events
from obspy.core.event import Catalog, Event
from obspy.core.event.base import ResourceIdentifier
from pydantic import ValidationError

import fdsnws_event_server.obspy_client as oc
import fdsnws_event_server.server as server
from fdsnws_event_server.models import GetEarthquakeByIdInput
from fdsnws_event_server.obspy_client import (
    DatacenterError,
    _extract_event_id,
    parse_fdsn_text,
    query_events_text,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def run(coro):
    return asyncio.run(coro)


class FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


# (datacenter, fixture, column count, depth header, has EventType, first EventID)
PROVIDERS = [
    ("INGV", "ingv_honshu_2024-01-01.txt", 14, "Depth/Km", True, "37258271"),
    ("EMSC", "emsc_honshu_2024-01-01.txt", 13, "Depth/km", False, "20240101_0000328"),
    ("GFZ", "gfz_honshu_2024-01-01.txt", 14, "Depth/km", True, "gfz2024abmz"),
    ("USGS", "usgs_honshu_2024-01-01.txt", 13, "Depth/km", False, "us6000m0yg"),
]
PROVIDER_IDS = [p[0] for p in PROVIDERS]


# --- Column sets: parsing is header-driven, not positional ------------------

@pytest.mark.parametrize(
    "datacenter,fixture,ncols,depth_header,has_eventtype,first_id",
    PROVIDERS, ids=PROVIDER_IDS,
)
def test_provider_response_parses(
    datacenter, fixture, ncols, depth_header, has_eventtype, first_id
):
    columns, rows = parse_fdsn_text((FIXTURES / fixture).read_text())

    assert len(columns) == ncols
    assert columns[0] == "EventID"
    # Depth is spelled Depth/Km at INGV and Depth/km everywhere else. Nothing in
    # the server may key on the spelling; the column list is passed through as-is.
    assert depth_header in columns
    assert ("EventType" in columns) is has_eventtype
    assert rows and rows[0][0] == first_id
    # Header-driven parsing means every row must line up with the header.
    for row in rows:
        assert len(row) == ncols


@pytest.mark.parametrize(
    "datacenter,fixture,ncols,depth_header,has_eventtype,first_id",
    PROVIDERS, ids=PROVIDER_IDS,
)
def test_query_returns_provider_columns_unmodified(
    monkeypatch, datacenter, fixture, ncols, depth_header, has_eventtype, first_id
):
    raw = (FIXTURES / fixture).read_text()
    monkeypatch.setattr(oc.requests, "get", lambda url, timeout=None: FakeResp(200, raw))

    columns, rows, api_url = run(query_events_text(datacenter=datacenter, limit=5))

    assert columns == raw.splitlines()[0][1:].split("|")
    assert len(rows) == len(
        [line for line in raw.splitlines() if line.strip() and not line.startswith("#")]
    )
    assert datacenter.lower() in api_url.lower() or api_url.startswith("http")


# --- Identifier formats ----------------------------------------------------

@pytest.mark.parametrize(
    "datacenter,fixture,ncols,depth_header,has_eventtype,first_id",
    PROVIDERS, ids=PROVIDER_IDS,
)
def test_real_provider_identifiers_are_accepted(
    datacenter, fixture, ncols, depth_header, has_eventtype, first_id
):
    """Every EventID a provider actually returns must survive input validation.

    Before `eventid` became a string this failed for three providers out of four:
    GFZ and USGS were rejected outright, and EMSC's was silently coerced.
    """
    params = GetEarthquakeByIdInput(eventid=first_id, datacenter=datacenter)
    assert params.eventid == first_id


def test_emsc_identifier_is_not_mangled_by_int_coercion():
    """Regression: the underscore in an EMSC id must not be read as a separator.

    ``int("20240101_0000328")`` is 202401010000328 -- Python treats the underscore
    as a digit separator -- which is a different, non-existent event. The datacenter
    answers HTTP 204 for it, so the bug surfaced as a confident "event not found"
    for a perfectly valid identifier.
    """
    emsc_id = "20240101_0000328"
    assert GetEarthquakeByIdInput(eventid=emsc_id).eventid == emsc_id
    assert str(int(emsc_id)) != emsc_id  # the trap this guards against


def test_integer_eventid_still_accepted_for_backward_compatibility():
    """The pre-1.4 schema advertised an integer, so clients still send one."""
    assert GetEarthquakeByIdInput(eventid=37258271).eventid == "37258271"


@pytest.mark.parametrize("bad", ["", "has space", "a&b=1", "../etc/passwd", "x" * 65])
def test_malformed_identifiers_rejected(bad):
    """Malformed ids are refused client-side, before any upstream request."""
    with pytest.raises(ValidationError):
        GetEarthquakeByIdInput(eventid=bad)


# QuakeML resource_id spellings observed per provider -> expected extracted id.
RESOURCE_IDS = [
    ("INGV", "smi:webservices.ingv.it/fdsnws/event/1/query?eventId=37258271", "37258271"),
    (
        "USGS",
        "quakeml:earthquake.usgs.gov/fdsnws/event/1/query"
        "?eventid=us6000m0yg&format=quakeml",
        "us6000m0yg",
    ),
    ("GFZ", "smi:org.gfz-potsdam.de/geofon/gfz2024abmz", "gfz2024abmz"),
    ("EMSC", "quakeml:eu.emsc/event/20240101_0000328", "20240101_0000328"),
]


@pytest.mark.parametrize(
    "datacenter,resource_id,expected", RESOURCE_IDS,
    ids=[r[0] for r in RESOURCE_IDS],
)
def test_extract_event_id_per_provider(datacenter, resource_id, expected):
    """The USGS shape used to leak the whole query string into the event_id."""
    event = Event(resource_id=ResourceIdentifier(id=resource_id))
    assert _extract_event_id(event) == expected


# --- HTTP status mapping ---------------------------------------------------

# Providers disagree on how absence is signalled. INGV/EMSC/GFZ answer 204 for an
# unknown event, USGS answers 404, and only INGV validates the id format upstream
# (400). All of these must reach the caller as "no data", never as an error --
# which is why query_events_text tests 204/404 before the >= 400 branch.
NO_DATA_STATUSES = [("INGV", 204), ("EMSC", 204), ("GFZ", 204), ("USGS", 404)]


@pytest.mark.parametrize("datacenter,status", NO_DATA_STATUSES,
                         ids=[f"{d}-{s}" for d, s in NO_DATA_STATUSES])
def test_no_data_status_maps_to_empty_result(monkeypatch, datacenter, status):
    monkeypatch.setattr(oc.requests, "get", lambda url, timeout=None: FakeResp(status, ""))
    columns, rows, _ = run(query_events_text(datacenter=datacenter))
    assert columns == [] and rows == []


def test_ingv_malformed_id_error_is_passed_through_verbatim(monkeypatch):
    """Only INGV rejects a malformed id upstream; its message must survive intact."""
    body = 'Error 400\n\nBad Request: \n "eventId" (eventId=not-an-id) must be numeric format.'
    monkeypatch.setattr(oc.requests, "get", lambda url, timeout=None: FakeResp(400, body))

    with pytest.raises(DatacenterError) as ei:
        run(query_events_text(datacenter="INGV"))

    assert "must be numeric format" in ei.value.message
    assert ei.value.status == 400
    assert ei.value.datacenter == "INGV"


@pytest.mark.parametrize("datacenter", PROVIDER_IDS)
def test_upstream_error_payload_names_the_datacenter(monkeypatch, datacenter):
    """A failing provider must be identifiable from the error payload alone."""
    monkeypatch.setattr(
        oc.requests, "get", lambda url, timeout=None: FakeResp(503, "upstream down")
    )
    out = json.loads(run(server.fdsn_query_earthquakes(datacenter=datacenter)))

    assert out["error"] is True
    assert out["datacenter"] == datacenter
    assert out["message"] == "upstream down"
    assert out["api_url"]


# --- Not-found contract holds for non-numeric ids --------------------------

@pytest.mark.parametrize(
    "datacenter,eventid",
    [("EMSC", "20240101_0000328"), ("GFZ", "gfz2024abmz"), ("USGS", "us6000m0yg")],
)
def test_not_found_message_echoes_non_numeric_id(monkeypatch, datacenter, eventid):
    """The recovery message must quote the id verbatim, whatever its format."""

    async def fake(eventid, datacenter="INGV"):
        return Catalog(), f"https://example/query?eventid={eventid}"

    monkeypatch.setattr(server, "get_event_by_id", fake)
    out = json.loads(
        run(server.fdsn_get_earthquake_by_id(eventid=eventid, datacenter=datacenter))
    )

    assert out["found"] is False
    assert eventid in out["message"]
    assert datacenter in out["message"]


@pytest.mark.parametrize(
    "datacenter,resource_id,expected", RESOURCE_IDS,
    ids=[r[0] for r in RESOURCE_IDS],
)
def test_found_event_reports_provider_event_id(
    monkeypatch, datacenter, resource_id, expected
):
    async def fake(eventid, datacenter="INGV"):
        return (
            Catalog(events=[Event(resource_id=ResourceIdentifier(id=resource_id))]),
            "https://example/query",
        )

    monkeypatch.setattr(server, "get_allorigins_by_id", fake)
    out = json.loads(
        run(server.fdsn_get_allorigins_by_id(eventid=expected, datacenter=datacenter))
    )

    assert out["found"] is True
    assert out["event_id"] == expected


# --- Providers that do not implement a subresource -------------------------------
#
# EMSC serves events but not includeallmagnitudes, and USGS not includearrivals.
# ObsPy catches the first case itself, from the provider's WADL, and raises a bare
# TypeError before any request goes out -- which used to escape the tool as an
# unhandled exception instead of the structured error contract.


def _obspy_rejects(parameter):
    """Stand in for ObsPy refusing an include flag the provider does not advertise."""

    def get_events(**kwargs):
        raise TypeError(f"The parameter '{parameter}' is not supported by the service.")

    return get_events


def test_unsupported_include_flag_becomes_a_datacenter_error(monkeypatch):
    class FakeClient:
        get_events = staticmethod(_obspy_rejects("includeallmagnitudes"))

    monkeypatch.setattr(oc, "_get_client", lambda datacenter: FakeClient())

    with pytest.raises(DatacenterError) as ei:
        run(oc.get_allmagnitudes_by_id(eventid="20240101_0000328", datacenter="EMSC"))

    assert "includeallmagnitudes" in str(ei.value)
    assert ei.value.datacenter == "EMSC"


def test_unsupported_include_flag_is_reported_in_band(monkeypatch):
    """The tool must answer with the error payload, not raise at the MCP boundary."""

    async def fake(eventid, datacenter="INGV"):
        raise DatacenterError(
            "The parameter 'includeallmagnitudes' is not supported by the service.",
            datacenter=datacenter,
            api_url="https://example/query",
        )

    monkeypatch.setattr(server, "get_allmagnitudes_by_id", fake)
    out = json.loads(
        run(
            server.fdsn_get_allmagnitudes_by_id(
                eventid="20240101_0000328", datacenter="EMSC"
            )
        )
    )

    assert out["error"] is True
    assert out["datacenter"] == "EMSC"


def test_our_own_bad_keyword_still_raises_type_error(monkeypatch):
    """A typo in our kwargs is worded identically by ObsPy; it must stay a crash."""

    class FakeClient:
        get_events = staticmethod(_obspy_rejects("not_a_real_kwarg"))

    monkeypatch.setattr(oc, "_get_client", lambda datacenter: FakeClient())

    with pytest.raises(TypeError):
        run(oc._get_events_quakeml("37258271", "INGV", not_a_real_kwarg=True))


# --- By-id contracts driven by real provider QuakeML -----------------------------
#
# The fixtures above cover the search path. These cover the by-id path from captured
# QuakeML, one document per provider and per include-flag set, so the whole
# tool-by-provider matrix stays reproducible while the services are unreachable.
# Parsing goes through ObsPy exactly as in production, which is the point: what is
# under test is our serialization of each provider's real event structure, not a
# hand-built stand-in.

QUAKEML_EVENTS = {
    "INGV": "37258271",
    "EMSC": "20240101_0000328",
    "GFZ": "gfz2024abmz",
    "USGS": "us6000m0yg",
}

# (fixture variant, tool, fetch function it calls, key holding the item count)
BYID_TOOLS = [
    ("plain", "fdsn_get_earthquake_by_id", "get_event_by_id", None),
    ("arrivals", "fdsn_get_arrivals_by_id", "get_arrivals_by_id", "arrivals_count"),
    ("allmagnitudes", "fdsn_get_allmagnitudes_by_id", "get_allmagnitudes_by_id",
     "magnitudes_count"),
    ("allorigins", "fdsn_get_allorigins_by_id", "get_allorigins_by_id",
     "origins_count"),
]

# EMSC does not implement includeallmagnitudes and USGS does not implement
# includearrivals, so no document exists to capture; those two cells are covered by
# the captured error bodies below instead.
BYID_CASES = [
    (dc, variant, tool, fetch, count_key)
    for dc in QUAKEML_EVENTS
    for variant, tool, fetch, count_key in BYID_TOOLS
    if (FIXTURES / f"{dc.lower()}_{variant}.quakeml.xml").exists()
]


def _serve_fixture(monkeypatch, datacenter, variant, fetch_name):
    """Make the tool's fetch function return the captured document for this cell."""
    catalog = read_events(str(FIXTURES / f"{datacenter.lower()}_{variant}.quakeml.xml"))

    async def fake(eventid, datacenter="INGV"):
        return (catalog, f"https://example/query?eventid={eventid}")

    monkeypatch.setattr(server, fetch_name, fake)


@pytest.mark.parametrize(
    "datacenter,variant,tool,fetch,count_key", BYID_CASES,
    ids=[f"{c[0]}-{c[1]}" for c in BYID_CASES],
)
def test_byid_serializes_real_provider_quakeml(
    monkeypatch, datacenter, variant, tool, fetch, count_key
):
    _serve_fixture(monkeypatch, datacenter, variant, fetch)
    eventid = QUAKEML_EVENTS[datacenter]

    out = json.loads(
        run(getattr(server, tool)(eventid=eventid, datacenter=datacenter))
    )

    assert out["found"] is True
    assert out["datacenter"] == datacenter

    if count_key is None:
        # fdsn_get_earthquake_by_id reports the event itself, with no count.
        assert out["event"]
        return

    # The identifier must survive the round trip through the provider's own
    # resource_id form -- the shape that used to be mis-parsed for USGS.
    assert out["event_id"] == eventid
    assert isinstance(out[count_key], int)
    if out[count_key] == 0:
        # Three-state contract: the event exists but carries no such subresource,
        # which must be said rather than returned as an empty payload. Hit by
        # INGV/arrivals -- a property of event 37258271, not of INGV, which does
        # implement includearrivals and does publish arrivals for other events.
        assert out["message"]
    else:
        # "origins_count" counts "origins", and so on.
        assert out[count_key] == len(out[count_key.removesuffix("_count")])


@pytest.mark.parametrize(
    "datacenter,variant,parameter",
    [
        ("EMSC", "allmagnitudes", "includeallmagnitudes"),
        ("USGS", "arrivals", "includearrivals"),
    ],
)
def test_provider_without_subresource_is_reported_not_crashed(
    monkeypatch, datacenter, variant, parameter
):
    """The provider's own refusal, captured verbatim, must reach the client in band.

    EMSC answers 400 and USGS 501; ObsPy turns the first into a TypeError of its own
    before any request leaves. Either way the tool must return the error payload.
    """
    body = (FIXTURES / f"{datacenter.lower()}_{variant}.error.txt").read_text()
    assert parameter in body, "captured fixture no longer names the parameter"

    async def fake(eventid, datacenter="INGV"):
        raise DatacenterError(body, datacenter=datacenter, api_url="https://example/q")

    tool = {"allmagnitudes": "fdsn_get_allmagnitudes_by_id",
            "arrivals": "fdsn_get_arrivals_by_id"}[variant]
    fetch = {"allmagnitudes": "get_allmagnitudes_by_id",
             "arrivals": "get_arrivals_by_id"}[variant]
    monkeypatch.setattr(server, fetch, fake)

    out = json.loads(
        run(getattr(server, tool)(
            eventid=QUAKEML_EVENTS[datacenter], datacenter=datacenter
        ))
    )

    assert out["error"] is True
    assert out["datacenter"] == datacenter
    assert parameter in out["message"]
