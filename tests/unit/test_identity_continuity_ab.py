"""Offline checks for the identity-continuity A/B harness (tests/ab/).

No model and no datacenter is contacted. What is tested is the experiment: that
its stimulus is what the server really emits, that the two arms differ by one
sentence, and that the scorer puts each kind of answer in the right class.
"""

import asyncio
import importlib.util
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from fdsnws_event_server import obspy_client, server
from obspy.clients.fdsn.header import URL_MAPPINGS

_PATH = Path(__file__).parents[1] / "ab" / "identity_continuity_ab.py"
FIXTURES = Path(__file__).parents[1] / "fixtures"
_spec = importlib.util.spec_from_file_location("identity_continuity_ab", _PATH)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)

GFZ = ("GFZ", "gfz2024aati")


def msg(*calls):
    """An assistant message with one tool call per (arguments, name) given."""
    return {"role": "assistant", "content": "", "tool_calls": [
        {"type": "function", "function": {
            "name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
        for args, name in calls]}


def one(args, name=ab.TOOL_NAME):
    return msg((args, name))


@pytest.mark.parametrize("args, expected", [
    ({"eventid": "gfz2024aati", "datacenter": "GFZ"}, "CORRECT"),
    ({"eventid": "gfz2024aati", "datacenter": "gfz"}, "CORRECT"),
    # Another URL_MAPPINGS key for the same service is the same datacenter.
    ({"eventid": "gfz2024aati", "datacenter": "GEOFON"}, "CORRECT"),
    # The server ignores keys the tool does not define, so the call still lands.
    ({"eventid": "gfz2024aati", "datacenter": "GFZ", "limit": 1}, "CORRECT"),
    ({"eventid": "gfz2024abmz", "datacenter": "GFZ"}, "WRONG_EVENTID"),
    ({"eventid": "GFZ2024AATI", "datacenter": "GFZ"}, "WRONG_EVENTID"),
    ({"eventid": "gfz2024aati ", "datacenter": "GFZ"}, "WRONG_EVENTID"),
    ({"eventid": "gfz2024aati", "datacenter": "EMSC"}, "WRONG_DATACENTER"),
    # The silent failure the experiment exists for: no datacenter means INGV.
    ({"eventid": "gfz2024aati"}, "WRONG_DATACENTER"),
    ({"eventid": "20240101_0000127", "datacenter": "EMSC"}, "WRONG_BOTH"),
    ({"datacenter": "GFZ"}, "MALFORMED_ARGS"),
    ({"eventid": ["gfz2024aati"], "datacenter": "GFZ"}, "MALFORMED_ARGS"),
    ({"eventid": "gfz2024aati", "datacenter": "NOSUCHDC"}, "WRONG_DATACENTER"),
    ("{not json", "MALFORMED_ARGS"),
])
def test_classify_single_call(args, expected):
    assert ab.classify(one(args), GFZ) == expected


def test_integer_eventid_is_compared_as_its_string():
    assert ab.classify(one({"eventid": 37258271, "datacenter": "INGV"}),
                       ("INGV", "37258271")) == "CORRECT"


def test_no_call_and_unexpected_calls():
    assert ab.classify({"role": "assistant", "content": "hi"}, GFZ) == "NO_TOOL_CALL"
    assert ab.classify(one({"eventid": "gfz2024aati"}, name="fdsn_query_earthquakes"),
                       GFZ) == "UNEXPECTED_TOOL_CALL"
    # A correct call next to a wrong one is not credited.
    good, bad = {"eventid": "gfz2024aati", "datacenter": "GFZ"}, {"eventid": "gfz2024aati"}
    assert ab.classify(msg((good, ab.TOOL_NAME), (bad, ab.TOOL_NAME)), GFZ) == "UNEXPECTED_TOOL_CALL"


def test_requery_is_unexpected_when_the_result_is_already_in_context():
    assert ab.classify(one({"datacenter": "GFZ"}, name=ab.QUERY_TOOL_NAME), GFZ) \
        == "UNEXPECTED_TOOL_CALL"


def test_no_target_scenario_scores_the_decision_to_call_not_its_arguments():
    detail = {"eventid": "gfz2024aati", "datacenter": "GFZ"}
    requery = {**ab.QUERY_ARGS, "datacenter": "GFZ"}
    assert ab.classify({"role": "assistant", "content": "The query failed."}, None) == "CORRECT"
    assert ab.classify(one(requery, name=ab.QUERY_TOOL_NAME), None) == "CORRECT"
    assert ab.classify(one(detail), None) == "UNEXPECTED_TOOL_CALL"
    assert ab.classify(msg((requery, ab.QUERY_TOOL_NAME), (detail, ab.TOOL_NAME)), None) \
        == "UNEXPECTED_TOOL_CALL"


class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.mark.parametrize("post, expected", [
    (lambda *a, **k: _Resp({"choices": [{"message": one(
        {"eventid": "20240101_0000127", "datacenter": "EMSC"})}]}), "CORRECT"),
    (lambda *a, **k: _Resp({"detail": "boom"}), "HTTP_ERROR"),
    (lambda *a, **k: (_ for _ in ()).throw(ab.requests.ConnectionError("sk-secret")), "HTTP_ERROR"),
])
def test_call_wraps_transport_and_never_leaks_exception_bodies(monkeypatch, post, expected):
    monkeypatch.setattr(ab.requests, "post", post)
    result = ab.call("http://x", "sk-secret", "m", "B", "bound", 0.7)
    assert result["status"] == expected and result["extra_args"] == []
    assert "sk-secret" not in json.dumps(result)


def test_summary_excludes_http_errors_from_the_denominator():
    s = ab.summarize(Counter(CORRECT=3, WRONG_DATACENTER=1, HTTP_ERROR=2))
    assert (s["total"], s["evaluable"], s["correct_rate"]) == (6, 4, 0.75)
    assert set(s["counts"]) == set(ab.STATUSES) and sum(s["counts"].values()) == 6
    assert ab.summarize(Counter(HTTP_ERROR=1))["correct_rate"] is None


def test_extra_args_are_recorded_without_changing_the_class(monkeypatch):
    reply = one({"eventid": "20240101_0000127", "datacenter": "EMSC", "limit": 1})
    monkeypatch.setattr(ab.requests, "post", lambda *a, **k: _Resp({"choices": [{"message": reply}]}))
    result = ab.call("http://x", "k", "m", "B", "baseline", 0.7)
    assert (result["status"], result["extra_args"]) == ("CORRECT", ["limit"])


def test_baseline_carries_the_eventid_note_the_bound_arm_extends():
    assert server._EVENTID_NOTE in ab.detail_tool("baseline")["function"]["description"]


@pytest.mark.parametrize("scenario", list(ab.SCENARIOS))
def test_arms_differ_only_by_the_binding_note(scenario):
    base = ab.build_payload(scenario, "baseline", "m", 0.7)
    bound = ab.build_payload(scenario, "bound", "m", 0.7)
    b_query, b_tool = base.pop("tools")
    x_query, x_tool = bound.pop("tools")
    b_fn, x_fn = b_tool["function"], x_tool["function"]
    assert base == bound
    assert b_query == x_query == ab.QUERY_TOOL
    assert b_fn["parameters"] == x_fn["parameters"]
    assert ab.BINDING_NOTE not in b_fn["description"]
    assert x_fn["description"].count(ab.BINDING_NOTE) == 1
    assert x_fn["description"].replace(" " + ab.BINDING_NOTE, "") == b_fn["description"]
    # The shared datacenter note, also published by the query tool, is untouched.
    assert x_fn["description"].endswith(server._DATACENTER_NOTE)


@pytest.mark.parametrize("scenario", ["A", "B", "C"])
def test_expected_id_is_the_unique_argmax_of_the_target_result(scenario):
    datacenter, eventid = ab.SCENARIOS[scenario]["expected"]
    target_dc, result = ab.SCENARIOS[scenario]["queries"][-1]
    assert target_dc == datacenter == result["datacenter"]
    mag, eid = result["columns"].index("magnitude"), result["columns"].index("event_id")
    mags = [float(r[mag]) for r in result["rows"]]
    assert mags.count(max(mags)) == 1
    assert result["rows"][mags.index(max(mags))][eid] == eventid


def _aliases(datacenter):
    """Every URL_MAPPINGS key that routes to the same service, GEOFON for GFZ."""
    target = URL_MAPPINGS[datacenter]
    return {k.upper() for k, v in URL_MAPPINGS.items() if v == target}


@pytest.mark.parametrize("scenario", list(ab.SCENARIOS))
def test_no_user_or_system_turn_names_a_datacenter_or_the_id(scenario):
    messages = ab.build_messages(scenario)
    said = " ".join(m["content"] for m in messages if m["role"] in ("system", "user")).upper()
    for datacenter, _ in ab.SCENARIOS[scenario]["queries"]:
        assert not any(alias in said for alias in _aliases(datacenter))
    expected = ab.SCENARIOS[scenario]["expected"]
    if expected:
        assert expected[1].upper() not in said
        assert any(expected[1] in m["content"] for m in messages if m["role"] == "tool")


def _replay(monkeypatch, datacenter, fixture=None, raises=None):
    """Run the real fdsn_query_earthquakes with the upstream HTTP call replaced."""
    def get(url, timeout):
        if raises:
            raise raises
        return SimpleNamespace(status_code=200, text=(FIXTURES / fixture).read_text())
    monkeypatch.setattr(obspy_client.requests, "get", get)
    return asyncio.run(server.fdsn_query_earthquakes(**ab.QUERY_ARGS, datacenter=datacenter))


@pytest.mark.parametrize("datacenter, fixture, name", [
    ("GFZ", "gfz_honshu_2024-01-01.txt", "GFZ_RESULT"),
    ("EMSC", "emsc_honshu_2024-01-01.txt", "EMSC_RESULT"),
])
def test_replayed_result_is_byte_identical_to_the_server_output(monkeypatch, datacenter, fixture, name):
    assert _replay(monkeypatch, datacenter, fixture) \
        == json.dumps(getattr(ab, name), separators=(",", ":"))


def test_replayed_failure_is_byte_identical_to_the_server_output(monkeypatch):
    timeout = requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='geofon.gfz.de', port=443): Read timed out. (read timeout=45)")
    assert _replay(monkeypatch, "GFZ", raises=timeout) == ab.GFZ_FAILURE
