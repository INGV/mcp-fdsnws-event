#!/usr/bin/env python3
"""A/B harness: does the model carry the datacenter along with the event id?

An event id is only meaningful at the datacenter that issued it. When a model
queries GFZ or EMSC and then asks for one of the returned events, the follow-up
call must name that datacenter too: omitting it is not an error the server can
catch, because ``datacenter`` defaults to INGV and the call silently asks the
wrong catalog. The eventid harness (eventid_hallucination_ab.py) scores the id
alone; this one scores the (eventid, datacenter) pair.

It replays a conversation whose fdsn_query_earthquakes results are rebuilt from
recorded fixtures by the server's own parser, offers fdsn_get_earthquake_by_eventid,
and classifies the one action the model takes. Two tool-description variants:
  - baseline : the deployed description, imported from the server
  - bound    : baseline plus BINDING_NOTE, one sentence tying the id to the
               datacenter of the query that returned it

Scenarios (no user turn names a datacenter or an id: both reach the model only
through the replayed tool-call arguments and tool results):
  A  one GFZ query                      -> GFZ  gfz2024aati
  B  one EMSC query                     -> EMSC 20240101_0000127 (underscore kept)
  C  GFZ query, then EMSC query; asks
     for the second result's largest    -> EMSC 20240101_0000127
  D  one failed GFZ query               -> no detail call

The deployed fdsn_query_earthquakes is offered too, identical in both arms, so a
model can re-query instead of guessing. In A-C that is UNEXPECTED_TOOL_CALL: the
result it needs is already in context. In D a re-query with no detail call is
CORRECT, since retrying the failed query is a sound recovery; any detail call
there is UNEXPECTED_TOOL_CALL, because no id exists to call it with.

The harness imports fdsnws_event_server (the fixtures are parsed and the tool
definitions read by the server's own code), so run it from the project venv or
after ``pip install -e .``.

Usage:
    export OPENWEBUI_API_KEY=sk-...
    python tests/ab/identity_continuity_ab.py \\
        --base-url http://host:8586 --model qwen2.5:72b-instruct \\
        --repeat 10 --variant both --temperature 0.7 > run.jsonl

Output is JSON Lines on stdout: one config record, one record per attempt, one
summary per scenario x variant. Exit 2 on preflight failure, 1 if any attempt
hit an HTTP error, else 0. Requires network access to the OpenWebUI instance.
Not collected by pytest.

The scoring design and the idea of the experiment come from PR #1 by @joy7758.
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

import requests

from fdsnws_event_server import server, tables
from fdsnws_event_server.obspy_client import _event_query_url, parse_fdsn_text
from obspy.clients.fdsn.header import URL_MAPPINGS

TOOL_NAME = "fdsn_get_earthquake_by_eventid"
QUERY_TOOL_NAME = "fdsn_query_earthquakes"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
STATUSES = ("CORRECT", "WRONG_EVENTID", "WRONG_DATACENTER", "WRONG_BOTH",
            "NO_TOOL_CALL", "UNEXPECTED_TOOL_CALL", "MALFORMED_ARGS", "HTTP_ERROR")

# The one sentence the bound arm adds. It goes into the eventid note and not the
# datacenter note, because _DATACENTER_NOTE is shared with fdsn_query_earthquakes
# and the experiment must not change what the query tool says. src/ is left alone:
# the production wording changes only once a live run says it helps.
BINDING_NOTE = (
    "Pass the datacenter of the fdsn_query_earthquakes result the event_id was "
    "copied from: event ids are only meaningful at the datacenter that issued them, "
    "and omitting datacenter silently queries INGV."
)

# The fixtures are one recorded query (2024-01-01, minmagnitude=5.0, limit=5),
# so the replayed call carries exactly those arguments and the echo agrees with it.
QUERY_ARGS = {"starttime": "2024-01-01T00:00:00", "endtime": "2024-01-01T23:59:59",
              "minmag": 5.0, "limit": 5}


def query_result(datacenter: str, fixture: str) -> dict:
    """Rebuild the fdsn_query_earthquakes envelope the server emits for a fixture.

    Mirrors server.py step by step: the api_url is built the way query_events_text
    builds it (offset omitted at its default), the echo includes the defaults the
    model did not send, and five rows at limit=5 fill the page, so has_more is true.
    """
    columns, rows = parse_fdsn_text((FIXTURES / fixture).read_text())
    upstream = {"format": "text", "limit": QUERY_ARGS["limit"], "orderby": "time",
                "starttime": QUERY_ARGS["starttime"], "endtime": QUERY_ARGS["endtime"],
                "minmagnitude": QUERY_ARGS["minmag"]}
    payload = {
        "datacenter": datacenter,
        "api_url": f"{_event_query_url(datacenter)}?{urlencode(upstream)}",
        "query": {"starttime": QUERY_ARGS["starttime"], "endtime": QUERY_ARGS["endtime"],
                  "minmag": QUERY_ARGS["minmag"], "limit": QUERY_ARGS["limit"],
                  "offset": 1, "orderby": "time"},
        "ordered_by": "time",
        "id_prefixes": {},
        "returned_count": len(rows),
        "limit": QUERY_ARGS["limit"],
        "offset": 1,
        "has_more": len(rows) >= QUERY_ARGS["limit"],
        "columns": tables.normalize_event_columns(columns),
        "rows": rows,
    }
    if payload["has_more"]:
        payload["next_offset"] = 1 + len(rows)
        payload["note"] = server._TRUNCATION_NOTE
    return payload


GFZ_RESULT = query_result("GFZ", "gfz_honshu_2024-01-01.txt")
EMSC_RESULT = query_result("EMSC", "emsc_honshu_2024-01-01.txt")

# The shape of server._error_payload for a query that never reached GFZ. The
# message is the one query_events_text writes on a network failure, and the
# payload is indented because _error_payload indents it.
GFZ_FAILURE = json.dumps({
    "error": True,
    "datacenter": "GFZ",
    "api_url": GFZ_RESULT["api_url"],
    "message": (
        "Network error contacting datacenter 'GFZ': HTTPSConnectionPool("
        "host='geofon.gfz.de', port=443): Read timed out. (read timeout=45)"
    ),
}, indent=2)

LARGEST = "Get the details of the largest-magnitude event in that result."
SCENARIOS = {
    "A": {"queries": [("GFZ", GFZ_RESULT)], "request": LARGEST,
          "expected": ("GFZ", "gfz2024aati")},
    "B": {"queries": [("EMSC", EMSC_RESULT)], "request": LARGEST,
          "expected": ("EMSC", "20240101_0000127")},
    # Both fixtures describe mostly the same physical events under different ids,
    # so a model that remembers "the M5+ off Honshu" but not where it read it
    # has two plausible answers. Only the second result is the target.
    "C": {"queries": [("GFZ", GFZ_RESULT), ("EMSC", EMSC_RESULT)],
          "request": ("Get the details of the largest-magnitude event in the second "
                      "query result, not the first one."),
          "expected": ("EMSC", "20240101_0000127")},
    "D": {"queries": [("GFZ", GFZ_FAILURE)], "request": LARGEST, "expected": None},
}


def _deployed_tool(name: str):
    tools = asyncio.run(server.mcp.list_tools())
    return next(t for t in tools if t.name == name)


# Taken from the running server rather than transcribed, so the baseline arm is
# always what a client of this checkout would see, parameter descriptions included:
# the datacenter one already says the INGV default is "not a binding", and leaving
# it out would credit the bound arm with guidance the baseline already has.
_DEPLOYED = _deployed_tool(TOOL_NAME)
BASELINE_DESCRIPTION = _DEPLOYED.description
PARAMETERS = _DEPLOYED.inputSchema
_BOUND_EVENTID_NOTE = server._EVENTID_NOTE.replace("\n\n", " " + BINDING_NOTE + "\n\n")
DESCRIPTIONS = {
    "baseline": BASELINE_DESCRIPTION,
    "bound": BASELINE_DESCRIPTION.replace(server._EVENTID_NOTE, _BOUND_EVENTID_NOTE),
}


def detail_tool(variant: str) -> dict:
    return {"type": "function", "function": {
        "name": TOOL_NAME, "description": DESCRIPTIONS[variant], "parameters": PARAMETERS}}


_QUERY = _deployed_tool(QUERY_TOOL_NAME)
QUERY_TOOL = {"type": "function", "function": {
    "name": QUERY_TOOL_NAME, "description": _QUERY.description, "parameters": _QUERY.inputSchema}}


def build_messages(scenario: str) -> list:
    messages = [
        {"role": "system", "content": "You are a seismology assistant. Use the provided tools to answer."},
    ]
    # The user never names a datacenter. If they did, the model could copy it from
    # the conversation text, and the experiment would measure recall of the user's
    # words instead of whether the id stays bound to the result it came from.
    for i, (datacenter, result) in enumerate(SCENARIOS[scenario]["queries"], 1):
        messages.append({"role": "user", "content": (
            "List the earthquakes of magnitude 5 or more on 2024-01-01." if i == 1
            else "Now run the same query against another catalog.")})
        messages.append({"role": "assistant", "content": "", "tool_calls": [{
            "id": f"call_query_{i}", "type": "function",
            "function": {"name": QUERY_TOOL_NAME,
                         "arguments": json.dumps({**QUERY_ARGS, "datacenter": datacenter})}}]})
        content = result if isinstance(result, str) else json.dumps(result, separators=(",", ":"))
        messages.append({"role": "tool", "tool_call_id": f"call_query_{i}", "content": content})
    messages.append({"role": "user", "content": SCENARIOS[scenario]["request"]})
    return messages


def build_payload(scenario: str, variant: str, model: str, temperature: float) -> dict:
    return {"model": model, "messages": build_messages(scenario), "tools": [QUERY_TOOL, detail_tool(variant)],
            "tool_choice": "auto", "temperature": temperature, "stream": False}


def _endpoint(datacenter: str) -> str:
    """What the server would route a datacenter name to.

    The server matches URL_MAPPINGS keys case-insensitively, and ObsPy maps more
    than one key to the same service (GEOFON and GFZ are both geofon.gfz.de), so
    two names are the same datacenter when they reach the same URL. An unknown
    name, which the server would reject, stays itself.
    """
    key = next((k for k in URL_MAPPINGS if k.upper() == datacenter.upper()), None)
    return URL_MAPPINGS[key].rstrip("/") if key else datacenter.upper()


def parse_args(call: dict):
    """The call's arguments as a dict, or None if they are not a JSON object."""
    raw = call["function"].get("arguments")
    try:
        args = raw if isinstance(raw, dict) else json.loads(raw)
    except (TypeError, ValueError):
        return None
    return args if isinstance(args, dict) else None


def classify(message, expected) -> str:
    """Score one assistant message against the expected (datacenter, eventid) pair.

    ``expected`` is None when there is no valid target (scenario D). The order of
    the checks is the precedence: whether a call was due, whether there was exactly
    one call to the detail tool, whether its arguments are usable at all, and only
    then which half of the identity is wrong.
    """
    calls = message.get("tool_calls") or []
    names = [(c.get("function") or {}).get("name") for c in calls]
    if expected is None:
        # Re-running the failed query is a recovery, not a guess; anything else
        # would need an id that the conversation never produced.
        return "CORRECT" if all(n == QUERY_TOOL_NAME for n in names) else "UNEXPECTED_TOOL_CALL"
    if not calls:
        return "NO_TOOL_CALL"
    # More than one call counts as a failure even when one of them is right:
    # a correct call next to a wrong one still sends a request to the wrong place.
    if len(calls) > 1 or names[0] != TOOL_NAME:
        return "UNEXPECTED_TOOL_CALL"
    args = parse_args(calls[0])
    if args is None:
        return "MALFORMED_ARGS"
    # Extra keys are not an error here because they are not one at the server:
    # it ignores them and still routes on eventid and datacenter. call() reports
    # them alongside the status instead.
    eventid, datacenter = args.get("eventid"), args.get("datacenter", "INGV")
    # An integer is what a model emits for a numeric-looking id despite a string
    # schema, and the server coerces it, so it is str()-ed. Nothing else is
    # normalised: case or whitespace in an opaque id makes it a different id.
    if type(eventid) is int:
        eventid = str(eventid)
    if not isinstance(eventid, str) or not isinstance(datacenter, str):
        return "MALFORMED_ARGS"
    wrong_dc = _endpoint(datacenter) != _endpoint(expected[0])
    wrong_id = eventid != expected[1]
    if wrong_id and wrong_dc:
        return "WRONG_BOTH"
    return "WRONG_EVENTID" if wrong_id else "WRONG_DATACENTER" if wrong_dc else "CORRECT"


def extra_args(message) -> list:
    """Argument keys of detail calls that the tool does not define, for the record."""
    extra = set()
    for c in message.get("tool_calls") or []:
        if (c.get("function") or {}).get("name") == TOOL_NAME:
            extra |= set(parse_args(c) or {}) - {"eventid", "datacenter"}
    return sorted(extra)


def summarize(counts: Counter) -> dict:
    """HTTP errors say nothing about the model, so they leave the rate's denominator."""
    total = sum(counts.values())
    evaluable = total - counts["HTTP_ERROR"]
    return {"total": total, "evaluable": evaluable,
            "correct_rate": counts["CORRECT"] / evaluable if evaluable else None,
            "counts": {s: counts[s] for s in STATUSES}}


def call(base_url, api_key, model, scenario, variant, temperature) -> dict:
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=build_payload(scenario, variant, model, temperature), timeout=120,
            allow_redirects=False,
        )
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        if not isinstance(message, dict):
            raise TypeError("message")
    # Only the class name is kept: an exception body can echo the request URL
    # or headers, and with them the API key.
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
        return {"status": "HTTP_ERROR", "error": type(e).__name__, "extra_args": [], "message": None}
    try:
        status = classify(message, SCENARIOS[scenario]["expected"])
        extra = extra_args(message)
    except (AttributeError, KeyError, TypeError):
        # A tool_calls entry that is not the dict the protocol promises is still
        # the model's output, so it is scored against the model, not the transport.
        status, extra = "MALFORMED_ARGS", []
    return {"status": status, "error": None, "extra_args": extra, "message": message}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True, help="OpenWebUI base URL, e.g. http://host:8586")
    ap.add_argument("--model", required=True, help="Model id as listed in OpenWebUI")
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--variant", choices=["baseline", "bound", "both"], default="both")
    ap.add_argument("--scenario", choices=list(SCENARIOS), help="Run one scenario (default: all)")
    args = ap.parse_args()

    api_key = os.environ.get("OPENWEBUI_API_KEY")
    if not api_key:
        print("ERROR: set OPENWEBUI_API_KEY in the environment.", file=sys.stderr)
        return 2
    try:
        resp = requests.get(f"{args.base_url.rstrip('/')}/api/models",
                            headers={"Authorization": f"Bearer {api_key}"}, timeout=30,
                            allow_redirects=False)
        # A redirect (typically http -> https) passes this GET but turns every
        # later POST into a GET that returns the web UI's HTML, so all attempts
        # would end as HTTP_ERROR. Refuse it here, where the cause is still visible.
        if resp.is_redirect:
            print(f"ERROR: {args.base_url} redirects to {resp.headers.get('location')}; "
                  "pass the redirect target's base URL as --base-url.", file=sys.stderr)
            return 2
        resp.raise_for_status()
        available = {m.get("id") for m in resp.json().get("data", [])}
    except (requests.RequestException, ValueError, AttributeError, TypeError) as e:
        print(f"ERROR: model preflight failed ({type(e).__name__}).", file=sys.stderr)
        return 2
    if args.model not in available:
        print(f"ERROR: model '{args.model}' not found. Available: {sorted(map(str, available))}",
              file=sys.stderr)
        return 2

    variants = ["baseline", "bound"] if args.variant == "both" else [args.variant]
    scenarios = [args.scenario] if args.scenario else list(SCENARIOS)
    print(json.dumps({"kind": "config", "model": args.model,
                      "repeat": args.repeat, "temperature": args.temperature,
                      "variants": variants, "scenarios": scenarios,
                      "binding_note": BINDING_NOTE}), flush=True)
    counts = {(s, v): Counter() for s in scenarios for v in variants}
    for scenario in scenarios:
        for i in range(args.repeat):
            # Alternating which arm goes first keeps any drift in the backend
            # (cache warm-up, load) from landing on one arm only.
            for variant in variants if i % 2 == 0 else variants[::-1]:
                result = call(args.base_url, api_key, args.model, scenario, variant,
                              args.temperature)
                counts[scenario, variant][result["status"]] += 1
                print(json.dumps({"kind": "attempt", "scenario": scenario, "variant": variant,
                                  "attempt": i + 1, **result}), flush=True)
    for (scenario, variant), seen in counts.items():
        print(json.dumps({"kind": "summary", "scenario": scenario, "variant": variant,
                          **summarize(seen)}), flush=True)
    return 1 if any(c["HTTP_ERROR"] for c in counts.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
