"""Multi-datacenter compatibility matrix, offline (fixtures + mocked network).

The advertised providers do not agree on much: identifier format, column set,
column spelling, and which HTTP status means "no such event" all differ. This
module pins that behaviour down per provider so the matrix stays reproducible
when the services are unreachable, using responses captured in ``fixtures/``.

Live counterparts live in ``tests/integration/test_live_datacenters.py``; the two
must be kept in step. IRIS/EarthScope is absent on purpose: its FDSNWS event
service returns HTTP 410 (ADR-0007), so it cannot be covered at all.
"""

import asyncio
import json
from pathlib import Path

import pytest
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
