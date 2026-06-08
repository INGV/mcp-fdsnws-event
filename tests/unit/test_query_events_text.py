"""Unit tests for query_events_text with the network mocked (offline)."""

import asyncio

import pytest
import requests

import fdsnws_event_server.obspy_client as oc
from fdsnws_event_server.obspy_client import DatacenterError, query_events_text


class FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def run(coro):
    return asyncio.run(coro)


def test_200_parses_and_builds_url(monkeypatch):
    sample = "#EventID|Time|MagType|Magnitude\n123|2020-01-01T00:00:00|ML|3.1\n"
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return FakeResp(200, sample)

    monkeypatch.setattr(oc.requests, "get", fake_get)
    cols, rows, api_url = run(query_events_text(
        starttime="2020-01-01T00:00:00", endtime="2020-01-02T00:00:00",
        limit=50, offset=5, orderby="time", datacenter="INGV",
    ))
    assert cols == ["EventID", "Time", "MagType", "Magnitude"]
    assert rows == [["123", "2020-01-01T00:00:00", "ML", "3.1"]]
    for token in ("format=text", "offset=5", "orderby=time", "limit=50"):
        assert token in captured["url"]


def test_default_offset_is_omitted_from_url(monkeypatch):
    # Omitting offset at its default (1) avoids INGV's off-by-one on the common query.
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return FakeResp(200, "#EventID|Time\n1|2020-01-01T00:00:00\n")

    monkeypatch.setattr(oc.requests, "get", fake_get)
    run(query_events_text(datacenter="INGV"))  # offset defaults to 1
    assert "offset=" not in captured["url"]


def test_204_returns_empty(monkeypatch):
    monkeypatch.setattr(oc.requests, "get", lambda url, timeout=None: FakeResp(204, ""))
    cols, rows, _ = run(query_events_text(datacenter="INGV"))
    assert cols == [] and rows == []


def test_400_raises_with_verbatim_message(monkeypatch):
    body = 'Error 400\n\nBad Request: \n "starttime" must be before "endtime"'
    monkeypatch.setattr(oc.requests, "get", lambda url, timeout=None: FakeResp(400, body))
    with pytest.raises(DatacenterError) as ei:
        run(query_events_text(datacenter="INGV"))
    assert "must be before" in ei.value.message
    assert ei.value.status == 400
    assert ei.value.datacenter == "INGV"
    assert ei.value.api_url


def test_network_error_wrapped(monkeypatch):
    def boom(url, timeout=None):
        raise requests.ConnectionError("dns failure")

    monkeypatch.setattr(oc.requests, "get", boom)
    with pytest.raises(DatacenterError) as ei:
        run(query_events_text(datacenter="INGV"))
    assert "Network error" in ei.value.message


def test_unknown_datacenter_raises_value_error():
    with pytest.raises(ValueError):
        run(query_events_text(datacenter="NOT_A_DATACENTER"))
