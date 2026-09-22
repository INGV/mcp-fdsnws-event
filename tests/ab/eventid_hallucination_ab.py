#!/usr/bin/env python3
"""A/B harness: does the model reuse the correct event id, or hallucinate one?

Reproduces the OpenWebUI failure mode in isolation. It replays the conversation
up to the follow-up question ("how many arrivals for the M2.5 event?") with the
prior fdsn_query_earthquakes tabular result already in context, then offers the
by-eventid tool and inspects which ``eventid`` the model passes.

It compares two tool-description variants:
  - baseline : the old bare "FDSN event ID" description (no provenance hint)
  - fixed    : provenance + anti-invention wording (the shipped fix)

Only the tool *description* changes between variants, so the measured delta is
attributable to the server-side mitigation (axis #1), not to prompt wording.

Usage:
    export OPENWEBUI_API_KEY=sk-...
    python tests/ab/eventid_hallucination_ab.py \
        --base-url http://host:8586 \
        --model qwen2.5:72b-instruct \
        --repeat 10 --variant both --temperature 0.7

Requires network access to the OpenWebUI instance. Not collected by pytest.

Tracks the 2.0.0 tool surface: the tool is ``fdsn_get_arrivals_by_eventid``,
``eventid`` is an opaque string rather than an integer, and the recorded
query result carries the normalized FDSN 1.2 column names. Those are
transcriptions of what the server now publishes, not changes to the experiment: the
two variants still differ in the ``eventid`` description and in nothing else.
"""

import argparse
import json
import os
import sys
from collections import Counter

import requests

# The correct answer: event_id of the M2.5 event from the recorded query result.
# A string, not an integer: event ids are opaque provider-specific tokens and the
# tool schema types them as strings, so the comparison has to be a string one too.
CORRECT_EVENTID = "46166442"

# Recorded fdsn_query_earthquakes result (orderby=magnitude), trimmed to the
# columns that matter for the follow-up question. Column names are the normalized
# FDSN 1.2 ones the server now emits; the values are unchanged from the recording.
QUERY_RESULT = {
    "datacenter": "INGV",
    "columns": ["event_id", "time", "latitude", "longitude", "depth_km",
                "mag_type", "magnitude", "location_name"],
    "rows": [
        ["46166442", "2026-06-09T04:43:13.930000", "44.4908", "9.5997", "7.2", "ML", "2.5", "2 km W Tornolo (PR)"],
        ["46167402", "2026-06-09T06:13:24.380000", "44.4973", "9.5862", "7.2", "ML", "2.4", "3 km W Tornolo (PR)"],
        ["46165592", "2026-06-09T01:24:20.220000", "38.6887", "15.5158", "159.4", "ML", "2.2", "Tirreno Meridionale [Mare]"],
        ["46166812", "2026-06-09T05:37:29.640000", "44.5072", "9.6028", "9.9", "ML", "2.2", "2 km W Bedonia (PR)"],
        ["46166572", "2026-06-09T04:48:50.280000", "44.506", "9.5923", "8.3", "ML", "1.8", "3 km W Bedonia (PR)"],
    ],
}

# Only this pair differs between the two arms of the experiment. The "fixed" text is
# the shipped one, quoted from the server's own _EVENTID_NOTE so the measurement
# keeps describing what is deployed.
#
# That quotation was re-synchronised in 2.0.0 and the previous wording is recorded
# here rather than lost, because the run this harness first produced is cited in a
# published paper and a reader must be able to see exactly what was measured then:
#
#     "Numeric FDSN event ID. MUST be taken from the EventID column of a prior
#      fdsn_query_earthquakes result. Never invent or guess an ID."
#
# It was replaced, not edited for taste: 2.0.0 types eventid as an opaque string
# and renames the column to event_id, so the old text
# contradicted the schema it was attached to on both counts. An arm whose prompt
# describes a tool that no longer exists measures nothing. Re-running this harness
# therefore reproduces the *method* of the published experiment against the current
# server, not its exact stimulus; to reproduce the original stimulus, restore the
# text above together with "type": "integer" on the eventid property.
EVENTID_DESC = {
    "baseline": "FDSN event ID",
    "fixed": (
        "The eventid is an opaque, provider-specific string and MUST be copied "
        "verbatim from a prior fdsn_query_earthquakes result (event_id column). "
        "Never invent, guess, reformat, or use placeholder values."
    ),
}


def arrivals_tool(variant: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "fdsn_get_arrivals_by_eventid",
            "description": (
                "Get all seismic phase arrivals for an earthquake event, including "
                "linked pick data (station, time, phase)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "eventid": {"type": "string", "description": EVENTID_DESC[variant]},
                    "datacenter": {"type": "string", "default": "INGV"},
                },
                "required": ["eventid"],
            },
        },
    }


def build_messages() -> list:
    return [
        {
            "role": "system",
            "content": "You are a seismology assistant. Use the provided tools to answer.",
        },
        {"role": "user", "content": "Mi puoi dire i piu forti terremoti di oggi, 2026-06-09?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_query_1",
                    "type": "function",
                    "function": {
                        "name": "fdsn_query_earthquakes",
                        "arguments": json.dumps({"orderby": "magnitude", "limit": 5}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_query_1",
            "content": json.dumps(QUERY_RESULT),
        },
        {
            "role": "user",
            "content": (
                "Per il terremoto piu forte, quello di magnitudo 2.5, "
                "dimmi quanti arrival ci sono."
            ),
        },
    ]


def call(base_url: str, api_key: str, model: str, variant: str, temperature: float):
    """Return the eventid the model passed, or a sentinel string."""
    payload = {
        "model": model,
        "messages": build_messages(),
        "tools": [arrivals_tool(variant)],
        "tool_choice": "auto",
        "temperature": temperature,
        "stream": False,
    }
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    tcs = msg.get("tool_calls") or []
    for tc in tcs:
        if tc.get("function", {}).get("name") == "fdsn_get_arrivals_by_eventid":
            try:
                args = json.loads(tc["function"]["arguments"])
            except (KeyError, json.JSONDecodeError):
                return "BAD_ARGS"
            return args.get("eventid", "MISSING_EVENTID")
    return "NO_TOOL_CALL"


def run_variant(base_url, api_key, model, variant, repeat, temperature) -> Counter:
    seen = Counter()
    for i in range(repeat):
        try:
            eid = call(base_url, api_key, model, variant, temperature)
        except requests.RequestException as e:
            eid = f"HTTP_ERROR:{e.__class__.__name__}"
        seen[str(eid)] += 1
        # Compared as strings: a model handed a string-typed schema still sometimes
        # emits the bare number, and scoring that as a miss would report a failure
        # the experiment is not measuring.
        ok = str(eid) == CORRECT_EVENTID
        print(f"  [{variant}] {i + 1}/{repeat}: eventid={eid} {'OK' if ok else 'X'}")
    return seen


def report(variant: str, seen: Counter, repeat: int) -> None:
    correct = seen.get(str(CORRECT_EVENTID), 0)
    print(f"\n=== {variant}: {correct}/{repeat} correct ({100 * correct / repeat:.0f}%) ===")
    for value, n in seen.most_common():
        tag = "  <- correct" if value == str(CORRECT_EVENTID) else ""
        print(f"    {value}: {n}{tag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True, help="OpenWebUI base URL, e.g. http://host:8586")
    ap.add_argument("--model", required=True, help="Model id as listed in OpenWebUI")
    ap.add_argument("--repeat", type=int, default=10)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--variant", choices=["baseline", "fixed", "both"], default="both")
    args = ap.parse_args()

    api_key = os.environ.get("OPENWEBUI_API_KEY")
    if not api_key:
        print("ERROR: set OPENWEBUI_API_KEY in the environment.", file=sys.stderr)
        return 2

    # Fail fast if the model is not available on this instance.
    models_resp = requests.get(
        f"{args.base_url.rstrip('/')}/api/models",
        headers={"Authorization": f"Bearer {api_key}"}, timeout=30,
    )
    models_resp.raise_for_status()
    available = {m.get("id") for m in models_resp.json().get("data", [])}
    if args.model not in available:
        print(f"ERROR: model '{args.model}' not found. Available: {sorted(available)}",
              file=sys.stderr)
        return 2

    variants = ["baseline", "fixed"] if args.variant == "both" else [args.variant]
    results = {}
    for v in variants:
        print(f"\n--- Running variant: {v} ---")
        results[v] = run_variant(
            args.base_url, api_key, args.model, v, args.repeat, args.temperature
        )

    print("\n" + "=" * 50)
    for v in variants:
        report(v, results[v], args.repeat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
