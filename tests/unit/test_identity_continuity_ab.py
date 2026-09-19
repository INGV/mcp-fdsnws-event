"""Offline checks for the optional identity-continuity research harness.

No model or FDSN service is contacted. These tests validate the experiment and
its deterministic scorer, not the behavior or reliability of any agent model.
"""

import copy
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest
import requests


HARNESS_PATH = Path(__file__).parents[1] / "ab" / "identity_continuity_ab.py"
spec = importlib.util.spec_from_file_location("identity_continuity_ab", HARNESS_PATH)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

STATES = {
    "CORRECT", "WRONG_EVENTID", "WRONG_DATACENTER", "WRONG_BOTH",
    "NO_TOOL_CALL", "UNEXPECTED_TOOL_CALL", "MALFORMED_ARGS", "HTTP_ERROR",
}


def tool_message(arguments, name=None):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_detail",
            "type": "function",
            "function": {
                "name": name or harness.TOOL_NAME,
                "arguments": json.dumps(arguments),
            },
        }],
    }


def score(message, datacenter="GFZ", eventid="gfz2024abmz", **kwargs):
    return harness.classify(
        message, expected_datacenter=datacenter, expected_eventid=eventid, **kwargs
    )


@pytest.mark.parametrize("arguments,expected", [
    ({"datacenter": "GFZ", "eventid": "gfz2024abmz"}, "CORRECT"),
    ({"datacenter": "GFZ", "eventid": "gfz2024abmx"}, "WRONG_EVENTID"),
    ({"datacenter": "INGV", "eventid": "gfz2024abmz"}, "WRONG_DATACENTER"),
    ({"eventid": "gfz2024abmz"}, "WRONG_DATACENTER"),
    ({"datacenter": "INGV", "eventid": "37258271"}, "WRONG_BOTH"),
])
def test_scores_complete_pair(arguments, expected):
    assert score(tool_message(arguments)) == expected


@pytest.mark.parametrize("eventid,expected_id,expected", [
    ("20240101_0000328", "20240101_0000328", "CORRECT"),
    ("202401010000328", "20240101_0000328", "WRONG_EVENTID"),
    (202401010000328, "20240101_0000328", "WRONG_EVENTID"),
    ("GFZ2024ABMZ", "gfz2024abmz", "WRONG_EVENTID"),
    ("0000328", "0000328", "CORRECT"),
    ("328", "0000328", "WRONG_EVENTID"),
    (328, "0000328", "WRONG_EVENTID"),
    (37258271, "37258271", "CORRECT"),
    ("smi.local:2024-event_1", "smi.local:2024-event_1", "CORRECT"),
])
def test_eventid_is_opaque_with_legacy_integer_compatibility(
    eventid, expected_id, expected
):
    assert score(
        tool_message({"datacenter": "EMSC", "eventid": eventid}),
        datacenter="EMSC", eventid=expected_id,
    ) == expected


def test_omitted_datacenter_uses_actual_ingv_default():
    assert score(
        tool_message({"eventid": 37258271}),
        datacenter="INGV", eventid="37258271",
    ) == "CORRECT"


@pytest.mark.parametrize("arguments", [
    {},
    {"datacenter": "GFZ"},
    {"eventid": True},
    {"eventid": False},
    {"eventid": 37258271.0},
    {"eventid": None},
    {"eventid": []},
    {"eventid": {}},
    {"eventid": ""},
    {"eventid": "gfz2024abmz "},
    {"eventid": "gfz2024abmz\n"},
    {"eventid": "event/id"},
    {"eventid": "a" * 65},
    {"eventid": "gfz2024abmz", "datacenter": None},
    {"eventid": "gfz2024abmz", "datacenter": 42},
    {"eventid": "gfz2024abmz", "datacenter": "GFZ", "extra": True},
    [],
    None,
    "gfz2024abmz",
])
def test_malformed_arguments_are_not_identity_mismatches(arguments):
    assert score(tool_message(arguments)) == "MALFORMED_ARGS"


@pytest.mark.parametrize("arguments", ["{", "", None, 42, {}])
def test_invalid_argument_encoding(arguments):
    message = tool_message({})
    message["tool_calls"][0]["function"]["arguments"] = arguments
    assert score(message) == "MALFORMED_ARGS"


def test_missing_argument_field():
    message = tool_message({})
    del message["tool_calls"][0]["function"]["arguments"]
    assert score(message) == "MALFORMED_ARGS"


@pytest.mark.parametrize("message", [
    {}, {"content": "Please clarify the event."},
    {"tool_calls": None}, {"tool_calls": []},
])
def test_no_call_is_correct_only_without_a_target(message):
    assert score(message) == "NO_TOOL_CALL"
    assert score(message, datacenter=None, eventid=None) == "CORRECT"


def test_missing_message_is_not_a_successful_abstention():
    assert score(None) == "MALFORMED_ARGS"
    assert score(None, datacenter=None, eventid=None) == "MALFORMED_ARGS"


@pytest.mark.parametrize("datacenter,expected", [
    ("gfz", "CORRECT"), ("GfZ", "CORRECT"),
    ("GFZ ", "WRONG_DATACENTER"), ("GFZ_ALIAS", "WRONG_DATACENTER"),
    ("https://geofon.gfz.de", "WRONG_DATACENTER"),
])
def test_provider_name_case_matches_routing_without_guessing_aliases(datacenter, expected):
    assert score(tool_message({
        "datacenter": datacenter, "eventid": "gfz2024abmz"
    })) == expected


@pytest.mark.parametrize("name", ["other_tool", "fdsn_query_earthquakes"])
def test_other_tool_cannot_be_scored_as_a_correct_detail_call(name):
    assert score(tool_message(
        {"datacenter": "GFZ", "eventid": "gfz2024abmz"}, name=name
    )) == "UNEXPECTED_TOOL_CALL"


def test_multiple_calls_cannot_hide_an_incorrect_call():
    correct = tool_message({"datacenter": "GFZ", "eventid": "gfz2024abmz"})
    wrong = tool_message({"datacenter": "INGV", "eventid": "37258271"})
    for calls in (
        correct["tool_calls"] + wrong["tool_calls"],
        wrong["tool_calls"] + correct["tool_calls"],
        correct["tool_calls"] * 2,
    ):
        assert score({"tool_calls": calls}) == "UNEXPECTED_TOOL_CALL"


@pytest.mark.parametrize("message", [
    tool_message({"eventid": "invented"}),
    tool_message({}),
    tool_message({"eventid": "invented"}, name="other_tool"),
])
def test_no_target_call_is_unexpected_before_parsing_arguments(message):
    assert score(message, datacenter=None, eventid=None) == "UNEXPECTED_TOOL_CALL"


@pytest.mark.parametrize("message", [None, {}, tool_message({})])
def test_infrastructure_error_takes_precedence(message):
    assert score(message, http_error=True) == "HTTP_ERROR"
    assert score(
        message, datacenter=None, eventid=None, http_error=True
    ) == "HTTP_ERROR"


@pytest.mark.parametrize("scenario", ["A", "B", "C", "D"])
def test_ab_payloads_change_only_binding_description(scenario):
    baseline = harness.build_payload(scenario, "baseline", "test-model", 0.25)
    bound = harness.build_payload(scenario, "bound", "test-model", 0.25)
    expected = copy.deepcopy(baseline)
    expected["tools"][0]["function"]["description"] += harness.BINDING_NOTE
    assert bound == expected
    assert baseline["model"] == "test-model"
    assert baseline["temperature"] == 0.25
    assert baseline["tool_choice"] == "auto"
    assert baseline["stream"] is False
    assert len(baseline["tools"]) == 1
    assert baseline["tools"][0]["function"]["name"] == harness.TOOL_NAME


@pytest.mark.parametrize("scenario", ["A", "B", "C"])
def test_target_id_occurs_only_in_observed_query_results(scenario):
    payload = harness.build_payload(scenario, "bound", "test-model", 0.0)
    target = harness.SCENARIOS[scenario]["expected_eventid"]
    results = [m for m in payload["messages"] if m["role"] == "tool"]
    assert any(target in m["content"] for m in results)
    # Identity in source evidence is necessary; putting it in a tool schema or
    # the user request would hand the model the answer without result selection.
    without_results = copy.deepcopy(payload)
    without_results["messages"] = [
        m for m in without_results["messages"] if m["role"] != "tool"
    ]
    assert target not in json.dumps(without_results)
    assert "expected_eventid" not in json.dumps(payload)
    assert "expected_datacenter" not in json.dumps(payload)


def test_payload_building_does_not_mutate_shared_scenarios():
    original = copy.deepcopy(harness.SCENARIOS)
    first = harness.build_payload("A", "baseline", "test-model", 0.0)
    first["messages"].clear()
    first["tools"][0]["function"]["description"] = "modified by caller"
    second = harness.build_payload("A", "baseline", "test-model", 0.0)
    assert second["messages"]
    assert second["tools"][0]["function"]["description"] != "modified by caller"
    assert harness.SCENARIOS == original


def test_stale_scenario_distinguishes_old_and_crossed_identities():
    payload = harness.build_payload("C", "baseline", "test-model", 0.0)
    results = [
        json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool"
    ]
    assert len(results) == 2
    old, new = results
    old_target = max(old["rows"], key=lambda row: float(row[old["columns"].index("Magnitude")]))
    old_id = old_target[old["columns"].index("EventID")]
    expected = harness.SCENARIOS["C"]
    new_id = expected["expected_eventid"]
    new_dc = expected["expected_datacenter"]
    assert old["datacenter"] != new_dc == new["datacenter"]
    assert old_id != new_id
    assert any(row[new["columns"].index("EventID")] == new_id for row in new["rows"])
    for datacenter, eventid, outcome in [
        (new_dc, new_id, "CORRECT"),
        (old["datacenter"], old_id, "WRONG_BOTH"),
        (old["datacenter"], new_id, "WRONG_DATACENTER"),
        (new_dc, old_id, "WRONG_EVENTID"),
    ]:
        assert score(
            tool_message({"datacenter": datacenter, "eventid": eventid}),
            datacenter=new_dc, eventid=new_id,
        ) == outcome


def test_summary_excludes_infrastructure_errors_from_model_denominator():
    counts = Counter({"CORRECT": 3, "WRONG_DATACENTER": 1, "HTTP_ERROR": 6})
    summary = harness.summarize(counts)
    assert summary["total_attempts"] == 10
    assert summary["evaluable_attempts"] == 4
    assert summary["correct_rate"] == pytest.approx(0.75)
    assert set(summary["counts"]) == STATES
    assert summary["counts"]["HTTP_ERROR"] == 6
    assert summary["counts"]["MALFORMED_ARGS"] == 0


@pytest.mark.parametrize("counts", [Counter(), Counter({"HTTP_ERROR": 3})])
def test_summary_has_no_rate_without_evaluable_attempts(counts):
    summary = harness.summarize(counts)
    assert summary["evaluable_attempts"] == 0
    assert summary["correct_rate"] is None
    assert set(summary["counts"]) == STATES


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def invoke(monkeypatch, body, scenario="A"):
    monkeypatch.setattr(harness.requests, "post", lambda *args, **kwargs: FakeResponse(body))
    return harness.call("https://model.invalid/", "test-key", "test-model", scenario, "baseline", 0.0)


def completion(message, finish_reason="tool_calls"):
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


@pytest.mark.parametrize("scenario,message,finish,expected", [
    ("A", tool_message({"datacenter": "GFZ", "eventid": "gfz2024abmz"}), "tool_calls", "CORRECT"),
    ("A", tool_message({"datacenter": "GFZ", "eventid": "gfz2024abmx"}), "tool_calls", "WRONG_EVENTID"),
    ("A", tool_message({"datacenter": "INGV", "eventid": "gfz2024abmz"}), "tool_calls", "WRONG_DATACENTER"),
    ("A", tool_message({"datacenter": "INGV", "eventid": "37258271"}), "tool_calls", "WRONG_BOTH"),
    ("A", {"role": "assistant", "content": "Please clarify.", "tool_calls": None}, "stop", "NO_TOOL_CALL"),
    ("D", {"role": "assistant", "content": "Query failed.", "tool_calls": None}, "stop", "CORRECT"),
    ("D", tool_message({"eventid": "invented"}), "tool_calls", "UNEXPECTED_TOOL_CALL"),
    ("A", tool_message({"eventid": False}), "tool_calls", "MALFORMED_ARGS"),
])
def test_nullable_legacy_field_preserves_action_classification(
    monkeypatch, scenario, message, finish, expected
):
    assert invoke(monkeypatch, completion(message, finish), scenario)["status"] == expected
    with_null = {**message, "function_call": None}
    result = invoke(monkeypatch, completion(with_null, finish), scenario)
    assert result["status"] == expected
    assert result["message"] == with_null
    assert result["error"] is None


@pytest.mark.parametrize("legacy", [{}, {"name": "legacy", "arguments": "{}"}])
def test_nonnull_legacy_field_remains_unsupported(monkeypatch, legacy):
    message = tool_message({"datacenter": "GFZ", "eventid": "gfz2024abmz"})
    message["function_call"] = legacy
    result = invoke(monkeypatch, completion(message))
    assert result["status"] == "HTTP_ERROR"
    assert result["message"] is None


def test_nullable_legacy_field_keeps_no_call_in_rate_denominator(monkeypatch):
    correct = tool_message({"datacenter": "GFZ", "eventid": "gfz2024abmz"})
    no_call = {
        "role": "assistant", "content": "Please clarify.",
        "tool_calls": None, "function_call": None,
    }
    statuses = [
        invoke(monkeypatch, completion(correct))["status"],
        invoke(monkeypatch, completion(no_call, "stop"))["status"],
    ]
    summary = harness.summarize(Counter(statuses))
    assert summary["total_attempts"] == summary["evaluable_attempts"] == 2
    assert summary["counts"]["NO_TOOL_CALL"] == 1
    assert summary["counts"]["HTTP_ERROR"] == 0
    assert summary["correct_rate"] == 0.5


def test_network_wrapper_sends_frozen_payload_and_scores_response(monkeypatch):
    message = tool_message({"datacenter": "GFZ", "eventid": "gfz2024abmz"})
    sent = {}

    def post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return FakeResponse(completion(message))

    monkeypatch.setattr(harness.requests, "post", post)
    result = harness.call("https://model.invalid/", "test-key", "test-model", "A", "bound", 0.25)
    assert sent["url"] == "https://model.invalid/api/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["json"] == harness.build_payload("A", "bound", "test-model", 0.25)
    assert sent["timeout"] > 0
    assert result["status"] == "CORRECT"
    assert result["message"] == message
    assert not result["error"]


@pytest.mark.parametrize("exception", [requests.Timeout, requests.ConnectionError, requests.HTTPError])
def test_network_errors_are_infrastructure_failures(monkeypatch, exception):
    def post(*args, **kwargs):
        raise exception("synthetic endpoint failure")

    monkeypatch.setattr(harness.requests, "post", post)
    result = harness.call("https://model.invalid", "test-key", "test-model", "A", "baseline", 0.0)
    assert result["status"] == "HTTP_ERROR"
    assert result["message"] is None
    assert result["error"]


@pytest.mark.parametrize("body", [
    {}, [], None, {"choices": []}, {"choices": "invalid"},
    {"choices": [{}]}, {"choices": [{"message": None}]},
    {"choices": [{"message": "invalid"}]},
])
def test_invalid_response_envelope_is_an_infrastructure_failure(monkeypatch, body):
    result = invoke(monkeypatch, body)
    assert result["status"] == "HTTP_ERROR"
    assert result["error"]


def test_non_json_response_is_an_infrastructure_failure(monkeypatch):
    class NonJsonResponse(FakeResponse):
        def json(self):
            raise ValueError("synthetic invalid JSON response")

    monkeypatch.setattr(harness.requests, "post", lambda *args, **kwargs: NonJsonResponse(None))
    result = harness.call("https://model.invalid", "test-key", "test-model", "A", "baseline", 0.0)
    assert result["status"] == "HTTP_ERROR"


@pytest.mark.parametrize("message", [tool_message({}), tool_message({"eventid": False})])
def test_model_argument_errors_are_not_infrastructure_failures(monkeypatch, message):
    result = invoke(monkeypatch, completion(message))
    assert result["status"] == "MALFORMED_ARGS"
    assert result["message"] == message
    assert not result["error"]


@pytest.mark.parametrize("message,finish_reason", [
    ({"role": "assistant", "content": 42}, "stop"),
    ({"role": "assistant", "content": {}}, "stop"),
    ({"role": "assistant", "content": None}, "stop"),
    ({"role": "assistant", "tool_calls": []}, "stop"),
    ({"role": "assistant", "content": ""}, "tool_calls"),
    ({"role": "assistant", "content": "", "tool_calls": []}, "tool_calls"),
    ({"role": "assistant", "content": "", "tool_calls": None}, "tool_calls"),
    ({"role": "assistant", "content": "", "tool_calls": {}}, "tool_calls"),
    (tool_message({"eventid": "invented"}), "stop"),
])
def test_invalid_envelope_cannot_be_a_correct_no_target_abstention(
    monkeypatch, message, finish_reason
):
    result = invoke(monkeypatch, completion(message, finish_reason), scenario="D")
    assert result["status"] == "HTTP_ERROR"
    assert result["message"] is None
    assert result["error"]


@pytest.mark.parametrize("content", ["The query failed. Please retry before choosing an event.", ""])
def test_valid_no_target_abstention_is_correct(monkeypatch, content):
    message = {"role": "assistant", "content": content}
    result = invoke(monkeypatch, completion(message, "stop"), scenario="D")
    assert result["status"] == "CORRECT"
    assert result["message"] == message
    assert not result["error"]


def configure_cli(monkeypatch, *args, models=("test-model",)):
    monkeypatch.setattr(harness.sys, "argv", [
        "identity_continuity_ab.py", "--base-url", "https://model.invalid",
        "--model", "test-model", *args,
    ])
    monkeypatch.setenv("OPENWEBUI_API_KEY", "test-key")
    monkeypatch.setattr(harness.requests, "get", lambda *args, **kwargs: FakeResponse({
        "data": [{"id": model} for model in models]
    }))


def forbid_call(*args, **kwargs):
    pytest.fail("No completion or real network request is allowed in this test")


@pytest.mark.parametrize("missing", ["key", "model"])
def test_cli_preflight_failure_does_not_attempt_completions(monkeypatch, capsys, missing):
    configure_cli(monkeypatch, models=() if missing == "model" else ("test-model",))
    monkeypatch.setattr(harness, "call", forbid_call)
    monkeypatch.setattr(harness.requests, "post", forbid_call)
    if missing == "key":
        monkeypatch.delenv("OPENWEBUI_API_KEY")
        monkeypatch.setattr(harness.requests, "get", forbid_call)
    assert harness.main() == 2
    captured = capsys.readouterr()
    assert "LIVE_AB=NOT_RUN" in captured.err
    assert "test-key" not in captured.out + captured.err


def test_cli_filters_scenario_counterbalances_variants_and_reports_counts(monkeypatch, capsys):
    configure_cli(monkeypatch, "--scenario", "A", "--repeat", "2")
    attempts = []

    def fake_call(base_url, api_key, model, scenario, variant, temperature):
        attempts.append((scenario, variant))
        status = "WRONG_DATACENTER" if variant == "baseline" else "CORRECT"
        return {"status": status, "message": {"content": "synthetic response"}, "error": None}

    monkeypatch.setattr(harness, "call", fake_call)
    monkeypatch.setattr(harness.requests, "post", forbid_call)
    assert harness.main() == 0
    assert attempts == [("A", "baseline"), ("A", "bound"), ("A", "bound"), ("A", "baseline")]
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    summaries = {r["variant"]: r for r in records if r["kind"] == "summary"}
    assert len([r for r in records if r["kind"] == "attempt"]) == 4
    assert set(summaries) == {"baseline", "bound"}
    for variant, outcome, rate in [
        ("baseline", "WRONG_DATACENTER", 0.0), ("bound", "CORRECT", 1.0),
    ]:
        assert summaries[variant]["scenario"] == "A"
        assert summaries[variant]["total_attempts"] == 2
        assert summaries[variant]["evaluable_attempts"] == 2
        assert summaries[variant]["counts"][outcome] == 2
        assert summaries[variant]["correct_rate"] == rate


def test_cli_default_experiment_is_bounded_at_eighty_attempts(monkeypatch, capsys):
    configure_cli(monkeypatch)
    attempts = Counter()

    def fake_call(base_url, api_key, model, scenario, variant, temperature):
        attempts[scenario, variant] += 1
        return {"status": "CORRECT", "message": {"content": "synthetic response"}, "error": None}

    monkeypatch.setattr(harness, "call", fake_call)
    monkeypatch.setattr(harness.requests, "post", forbid_call)
    assert harness.main() == 0
    assert sum(attempts.values()) == 80
    assert attempts == Counter({
        (scenario, variant): 10
        for scenario in ("A", "B", "C", "D") for variant in ("baseline", "bound")
    })
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    summaries = [r for r in records if r["kind"] == "summary"]
    assert len(summaries) == 8
    assert all(r["total_attempts"] == 10 and r["counts"]["CORRECT"] == 10 for r in summaries)


def test_cli_infrastructure_failure_is_reported_and_returns_nonzero(monkeypatch, capsys):
    configure_cli(monkeypatch, "--scenario", "A", "--repeat", "1")

    def fake_call(base_url, api_key, model, scenario, variant, temperature):
        if variant == "baseline":
            return {"status": "HTTP_ERROR", "message": None, "error": "Timeout"}
        return {"status": "CORRECT", "message": {"content": "synthetic response"}, "error": None}

    monkeypatch.setattr(harness, "call", fake_call)
    monkeypatch.setattr(harness.requests, "post", forbid_call)
    assert harness.main() == 1
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    summaries = {r["variant"]: r for r in records if r["kind"] == "summary"}
    assert summaries["baseline"]["counts"]["HTTP_ERROR"] == 1
    assert summaries["baseline"]["evaluable_attempts"] == 0
    assert summaries["baseline"]["correct_rate"] is None
    assert summaries["bound"]["evaluable_attempts"] == 1
    assert summaries["bound"]["correct_rate"] == 1.0
