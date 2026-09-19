#!/usr/bin/env python3
"""Optional live A/B replay of (datacenter, EventID) continuity.

The four fixed conversations are synthetic fixtures, not FDSN observations.
Only the by-id tool description gains BINDING_NOTE in the bound variant.
No tool is executed: this measures the next model action after replayed query
turns, not live FDSN resolution or general agent reliability. See tests/README.md.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from urllib.parse import urlsplit

import requests

TOOL_NAME = "fdsn_get_earthquake_by_id"
STATUSES = (
    "CORRECT", "WRONG_EVENTID", "WRONG_DATACENTER", "WRONG_BOTH",
    "NO_TOOL_CALL", "UNEXPECTED_TOOL_CALL", "MALFORMED_ARGS", "HTTP_ERROR",
)
IDENTITY_NOTE = (
    "The eventid is an opaque, provider-specific string and MUST be copied verbatim "
    "from a prior fdsn_query_earthquakes result (EventID column). Never invent, "
    "guess, reformat, or use placeholder values."
)
BINDING_NOTE = (
    " The target identity is the pair (datacenter, EventID): copy both values "
    "from the selected prior query result, without silently falling back to "
    "another provider or the default datacenter."
)


def query_result(datacenter: str, rows: list) -> dict:
    """A deliberately small synthetic projection of the production table."""
    return {
        "datacenter": datacenter,
        "columns": ["EventID", "Time", "Magnitude"],
        "rows": rows,
    }


GFZ_RESULT = query_result("GFZ", [
    ["gfz2024abna", "2024-01-01T10:00:00", "5.2"],
    ["gfz2024abmz", "2024-01-01T07:10:00", "6.1"],
    ["gfz2024abmc", "2024-01-01T06:00:00", "4.8"],
])
EMSC_RESULT = query_result("EMSC", [
    ["20240101_0000412", "2024-01-01T11:00:00", "4.9"],
    ["20240101_0000375", "2024-01-01T09:00:00", "5.3"],
    ["20240101_0000328", "2024-01-01T07:10:00", "6.1"],
])
SCENARIOS = {
    "A": {
        "description": "GFZ alphanumeric identity",
        "queries": [GFZ_RESULT],
        "request": "Get the basic details for the largest-magnitude event in that result.",
        "expected_datacenter": "GFZ", "expected_eventid": "gfz2024abmz",
    },
    "B": {
        "description": "EMSC underscore identity",
        "queries": [EMSC_RESULT],
        "request": "Get the basic details for the largest-magnitude event in that result.",
        "expected_datacenter": "EMSC", "expected_eventid": "20240101_0000328",
    },
    "C": {
        "description": "Stale object from a different datacenter",
        "queries": [GFZ_RESULT, EMSC_RESULT],
        "request": (
            "Get the basic details for the largest-magnitude event in the SECOND "
            "query result, not the first result."
        ),
        "expected_datacenter": "EMSC", "expected_eventid": "20240101_0000328",
    },
    "D": {
        "description": "Failed query with no valid target",
        "queries": [{
            "error": True, "datacenter": "GFZ",
            "message": "Upstream query failed: service temporarily unavailable.",
        }],
        "request": "Get the basic details for the largest-magnitude event in that result.",
        "expected_datacenter": None, "expected_eventid": None,
    },
}


def detail_tool(variant: str) -> dict:
    if variant not in ("baseline", "bound"):
        raise ValueError("Unknown variant")
    description = "Get basic information about a specific earthquake event. " + IDENTITY_NOTE
    if variant == "bound":
        description += BINDING_NOTE
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    # Reflect the current signature's legacy integer compatibility.
                    # No provider-specific examples: the targets must come from rows.
                    "eventid": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    "datacenter": {"type": "string", "default": "INGV"},
                },
                "required": ["eventid"],
                "additionalProperties": False,
            },
        },
    }


def build_payload(scenario: str, variant: str, model: str, temperature: float) -> dict:
    fixture = SCENARIOS[scenario]
    messages = [{
        "role": "system",
        "content": (
            "You are a seismology assistant. Use the provided tool when needed to "
            "answer the user. Make at most one detail-tool call."
        ),
    }]
    for index, result in enumerate(fixture["queries"], 1):
        call_id = f"call_query_{index}"
        messages.extend([
            {"role": "user", "content": (
                f"Query {result['datacenter']} events on 2024-01-01, newest first."
            )},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": call_id, "type": "function", "function": {
                    "name": "fdsn_query_earthquakes",
                    "arguments": json.dumps({
                        "datacenter": result["datacenter"],
                        "starttime": "2024-01-01T00:00:00",
                        "endtime": "2024-01-01T23:59:59",
                        "orderby": "time", "limit": 3,
                    }),
                },
            }]},
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)},
            {"role": "assistant", "content": f"Query {index} has returned."},
        ])
    messages.append({"role": "user", "content": fixture["request"]})
    return {
        "model": model, "messages": messages, "tools": [detail_tool(variant)],
        "tool_choice": "auto", "temperature": temperature, "stream": False,
    }


def _unique_keys(pairs: list) -> dict:
    """Ambiguous duplicate JSON keys must not hide an earlier wrong identity."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate argument key")
        result[key] = value
    return result


def classify(message: dict | None, *, expected_datacenter: str | None,
             expected_eventid: str | None, http_error: bool = False) -> str:
    """Score one assistant action without network access.

    Precedence: infrastructure; malformed call container; no-target abstention;
    absent/unexpected/multiple calls; malformed arguments; exact identity pair.
    Missing datacenter resolves to INGV, as in the frozen production contract.
    None/None denotes no valid target; partial expected identities are invalid.
    """
    if (expected_datacenter is None) != (expected_eventid is None):
        raise ValueError("Expected identity must be a pair or None/None")
    if http_error:
        return "HTTP_ERROR"
    if not isinstance(message, dict):
        return "MALFORMED_ARGS"
    calls = message.get("tool_calls")
    if calls is None:
        calls = []
    if not isinstance(calls, list):
        return "MALFORMED_ARGS"
    # An upstream failure in the replay is task data, not an HTTP_ERROR here.
    if expected_eventid is None:
        return "UNEXPECTED_TOOL_CALL" if calls else "CORRECT"
    if not calls:
        return "NO_TOOL_CALL"
    if len(calls) != 1:
        return "UNEXPECTED_TOOL_CALL"
    call = calls[0]
    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
        return "MALFORMED_ARGS"
    function = call["function"]
    if call.get("type") != "function" or function.get("name") != TOOL_NAME:
        return "UNEXPECTED_TOOL_CALL"
    try:
        args = json.loads(function["arguments"], object_pairs_hook=_unique_keys)
    except (KeyError, TypeError, ValueError):
        return "MALFORMED_ARGS"
    if not isinstance(args, dict) or set(args) - {"eventid", "datacenter"}:
        return "MALFORMED_ARGS"
    eventid = args.get("eventid")
    if type(eventid) is int:
        eventid = str(eventid)
    if (not isinstance(eventid, str) or not 1 <= len(eventid) <= 64
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", eventid) is None):
        return "MALFORMED_ARGS"
    datacenter = args.get("datacenter", "INGV")
    if not isinstance(datacenter, str):
        return "MALFORMED_ARGS"
    wrong_id = eventid != expected_eventid
    # Named datacenters resolve case-insensitively in the production ObsPy path.
    # Do not apply this normalization to opaque event IDs, whitespace or aliases.
    wrong_dc = datacenter.upper() != expected_datacenter.upper()
    if wrong_id and wrong_dc:
        return "WRONG_BOTH"
    if wrong_id:
        return "WRONG_EVENTID"
    if wrong_dc:
        return "WRONG_DATACENTER"
    return "CORRECT"


def summarize(counts: Counter) -> dict:
    """Mutually exclusive counts; correct rate excludes infrastructure failures."""
    total = sum(counts.values())
    evaluable = total - counts["HTTP_ERROR"]
    return {
        "total_attempts": total,
        "evaluable_attempts": evaluable,
        "correct_rate": counts["CORRECT"] / evaluable if evaluable else None,
        "counts": {status: counts[status] for status in STATUSES},
    }


def api_root(base_url: str) -> str:
    """OpenWebUI host defaults to /api; explicit /api or /v1 roots also work."""
    parsed = urlsplit(base_url)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Use an HTTP(S) base URL without credentials, query or fragment")
    root = base_url.rstrip("/")
    return root if root.endswith(("/api", "/v1")) else root + "/api"


def _response_json(response) -> dict:
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Expected HTTP 200 (redirects are not followed)")
    data = response.json()
    if not isinstance(data, dict) or "error" in data:
        raise ValueError("Invalid API response")
    return data


def call(base_url: str, api_key: str, model: str, scenario: str,
         variant: str, temperature: float) -> dict:
    """One completion, no retry; transport/envelope failures are unscored."""
    fixture = SCENARIOS[scenario]
    try:
        response = requests.post(
            f"{api_root(base_url)}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=build_payload(scenario, variant, model, temperature),
            timeout=120, allow_redirects=False,
        )
        data = _response_json(response)
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Expected one completion")
        choice = choices[0]
        message = choice["message"]
        if (not isinstance(message, dict) or message.get("role") != "assistant"
                or not ({"content", "tool_calls"} & message.keys())):
            raise ValueError("Invalid assistant envelope")
        content = message.get("content")
        calls = message.get("tool_calls")
        if content is not None and not isinstance(content, str):
            raise ValueError("Invalid assistant content")
        # Legacy function_call is not the tools protocol offered in this experiment.
        finish = choice.get("finish_reason")
        if "function_call" in message or finish not in ("stop", "tool_calls"):
            raise ValueError("Unsupported or incomplete completion")
        if finish == "tool_calls" and (not isinstance(calls, list) or not calls):
            raise ValueError("Missing calls in a tool completion")
        if finish == "stop" and (calls or not isinstance(content, str)):
            raise ValueError("Invalid non-tool completion")
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        # Never print exception bodies/URLs: these can contain credentials.
        return {"status": "HTTP_ERROR", "message": None, "error": type(exc).__name__}
    return {
        "status": classify(message, expected_datacenter=fixture["expected_datacenter"],
                           expected_eventid=fixture["expected_eventid"]),
        "message": message, "error": None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True, help="OpenWebUI host or explicit /api or /v1 root")
    ap.add_argument("--model", required=True, help="Exact model id returned by /models")
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--variant", choices=["baseline", "bound", "both"], default="both")
    ap.add_argument("--scenario", choices=list(SCENARIOS), help="Optional fixed scenario A, B, C or D")
    args = ap.parse_args()
    if args.repeat < 1 or not math.isfinite(args.temperature) or args.temperature < 0:
        ap.error("repeat must be positive and temperature finite and non-negative")
    try:
        root = api_root(args.base_url)
    except ValueError as exc:
        ap.error(str(exc))
    api_key = os.environ.get("OPENWEBUI_API_KEY")
    if not api_key:
        print("LIVE_AB=NOT_RUN: set OPENWEBUI_API_KEY in the environment.", file=sys.stderr)
        return 2
    try:
        response = requests.get(
            f"{root}/models", headers={"Authorization": f"Bearer {api_key}"},
            timeout=30, allow_redirects=False,
        )
        models = _response_json(response)["data"]
        if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
            raise ValueError("Invalid model list")
        if args.model not in {m.get("id") for m in models}:
            print("LIVE_AB=NOT_RUN: requested model not found.", file=sys.stderr)
            return 2
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print(f"LIVE_AB=NOT_RUN: model preflight failed ({type(exc).__name__}).", file=sys.stderr)
        return 2

    variants = ["baseline", "bound"] if args.variant == "both" else [args.variant]
    scenarios = [args.scenario] if args.scenario else list(SCENARIOS)
    print(json.dumps({"kind": "config", "base_url": root, "model": args.model,
                      "repeat": args.repeat, "temperature": args.temperature,
                      "variants": variants, "scenarios": scenarios,
                      "fixtures": "synthetic", "tool_execution": False}), flush=True)
    counts = {(s, v): Counter() for s in scenarios for v in variants}
    for scenario in scenarios:
        for index in range(args.repeat):
            # Counterbalance order over repeats; no shared live conversational state.
            order = variants if index % 2 == 0 else variants[::-1]
            for variant in order:
                result = call(args.base_url, api_key, args.model, scenario, variant, args.temperature)
                counts[scenario, variant][result["status"]] += 1
                print(json.dumps({"kind": "attempt", "scenario": scenario, "variant": variant,
                                  "attempt": index + 1, **result}), flush=True)
    for (scenario, variant), seen in counts.items():
        print(json.dumps({"kind": "summary", "scenario": scenario, "variant": variant,
                          **summarize(seen)}), flush=True)
    # Nonzero signals infrastructure trouble, not a test of model correctness.
    return 1 if any(c["HTTP_ERROR"] for c in counts.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
