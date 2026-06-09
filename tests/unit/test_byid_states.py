"""Unit tests for the three-state by-id contract (offline, network mocked).

The by-id tools must distinguish:
  1. event not found        -> found=False + actionable message
  2. event found, data       -> found=True, no message
  3. event found, no data    -> found=True, count=0 + explanatory message

This guards against the failure mode where a hallucinated eventid (e.g. 123456)
returned an empty payload indistinguishable from "event exists but has no data",
giving the model no signal to self-correct.
"""

import asyncio
import json

import pytest
from obspy.core.event import Catalog, Event, Magnitude
from obspy.core.event.base import ResourceIdentifier

import fdsnws_event_server.server as server


def run(coro):
    return asyncio.run(coro)


def make_event(event_id: str = "46166442") -> Event:
    """A minimal ObsPy Event whose resource_id carries a numeric eventId."""
    return Event(
        resource_id=ResourceIdentifier(
            id=f"smi:webservices.ingv.it/fdsnws/event/1/query?eventId={event_id}"
        )
    )


def patch_fetch(monkeypatch, name: str, catalog: Catalog):
    """Patch a by-id fetch function in the server namespace to return ``catalog``."""

    async def fake(eventid, datacenter="INGV"):
        return catalog, f"https://example/query?eventid={eventid}"

    monkeypatch.setattr(server, name, fake)


# --- State 1: event not found (empty catalog) -------------------------------

NOT_FOUND_CASES = [
    ("fdsn_get_arrivals_by_id", "get_arrivals_by_id", "arrivals_count"),
    ("fdsn_get_allmagnitudes_by_id", "get_allmagnitudes_by_id", "magnitudes_count"),
    ("fdsn_get_allorigins_by_id", "get_allorigins_by_id", "origins_count"),
    ("fdsn_get_focalmechanism_by_id", "get_focalmechanism_by_id", "focal_mechanisms_count"),
]


@pytest.mark.parametrize("tool_name,fetch_name,count_key", NOT_FOUND_CASES)
def test_not_found_signals_found_false_with_message(
    monkeypatch, tool_name, fetch_name, count_key
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


def test_get_earthquake_by_id_not_found(monkeypatch):
    # fdsn_get_earthquake_by_id has only 2 states (found / not found).
    patch_fetch(monkeypatch, "get_event_by_id", Catalog())
    out = json.loads(run(server.fdsn_get_earthquake_by_id(eventid=999999)))

    assert out["found"] is False
    assert out["event"] is None
    assert "999999" in out["message"]


# --- State 3: event found but sub-resource absent ---------------------------

ABSENT_CASES = [
    ("fdsn_get_arrivals_by_id", "get_arrivals_by_id", "arrivals_count", "arrivals"),
    ("fdsn_get_allmagnitudes_by_id", "get_allmagnitudes_by_id", "magnitudes_count", "magnitude"),
    ("fdsn_get_allorigins_by_id", "get_allorigins_by_id", "origins_count", "origin"),
    (
        "fdsn_get_focalmechanism_by_id",
        "get_focalmechanism_by_id",
        "focal_mechanisms_count",
        "focal mechanism",
    ),
]


@pytest.mark.parametrize("tool_name,fetch_name,count_key,word", ABSENT_CASES)
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
    patch_fetch(monkeypatch, "get_allmagnitudes_by_id", Catalog(events=[event]))

    out = json.loads(run(server.fdsn_get_allmagnitudes_by_id(eventid=46166442)))

    assert out["found"] is True
    assert out["magnitudes_count"] == 1
    assert "message" not in out


# --- Prevention (#1): eventid description must carry provenance + anti-invention ---

from fdsnws_event_server.models import (  # noqa: E402
    GetAllMagnitudesByIdInput,
    GetAllOriginsByIdInput,
    GetArrivalsByIdInput,
    GetEarthquakeByIdInput,
    GetFocalMechanismByIdInput,
)


@pytest.mark.parametrize(
    "model",
    [
        GetEarthquakeByIdInput,
        GetArrivalsByIdInput,
        GetAllMagnitudesByIdInput,
        GetAllOriginsByIdInput,
        GetFocalMechanismByIdInput,
    ],
)
def test_eventid_description_states_provenance(model):
    desc = model.model_fields["eventid"].description
    assert "fdsn_query_earthquakes" in desc
    assert "Do NOT invent" in desc
