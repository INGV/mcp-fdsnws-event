# Tests

Two layers:

- **`unit/`** — fast, **offline**, deterministic. No network. The FDSN text parser,
  `query_events_text` (with the network mocked), and the Pydantic models. Uses a
  captured real response in `fixtures/` so the parser is tested against actual data.
- **`integration/`** — **live**, hits INGV over the network. Marked
  `@pytest.mark.integration` and **excluded by default**.

## Run

```bash
# Unit only (default — offline, fast). This is what CI and run_tests.sh use.
pytest

# Integration only (requires network access to INGV)
pytest -m integration

# Everything
pytest -m "unit or integration"   # or: pytest -m ''
```

Inside Docker (the primary workflow):

```bash
docker run --rm mcp-fdsnws-event-server pytest                 # unit
docker run --rm mcp-fdsnws-event-server pytest -m integration  # live
```

## Layout

```
tests/
├── fixtures/
│   ├── ingv_emilia_2012-05-29.txt   # real FDSN format=text response (8 events)
│   ├── ingv_honshu_2024-01-01.txt   # the same query at four providers, for the
│   ├── emsc_honshu_2024-01-01.txt   #   compatibility matrix: identifier formats
│   ├── gfz_honshu_2024-01-01.txt    #   and column sets differ between them
│   └── usgs_honshu_2024-01-01.txt
├── unit/
│   ├── test_parse_fdsn_text.py      # header-driven parsing, edge cases
│   ├── test_query_events_text.py    # 200 / 204 / 400 / network error, URL building
│   ├── test_models.py               # bbox-vs-radial, offset, orderby, ranges
│   ├── test_byid_states.py          # three-state found / not-found contract
│   └── test_multidatacenter.py      # per-provider parsing, ids, error mapping
└── integration/
    └── test_live_datacenters.py     # live query + by-id, parametrised per provider
```

### Provider fixtures

The four `*_honshu_2024-01-01.txt` files are the **same** query
(`minmagnitude=5.0`, 2024-01-01, `limit=5`) captured from four datacenters, so
provider differences are visible side by side and the compatibility matrix stays
reproducible when the services are unreachable:

| Provider | Cols | Depth header | `EventType` | Example EventID |
|---|---|---|---|---|
| INGV | 14 | `Depth/Km` | yes | `37258271` |
| EMSC | 13 | `Depth/km` | no | `20240101_0000328` |
| GFZ | 14 | `Depth/km` | yes | `gfz2024abmz` |
| USGS | 13 | `Depth/km` | no | `us6000m0yg` |

Note the capital `K` at INGV only, and that three of the four use non-numeric
identifiers. Both are why parsing is header-driven and why `eventid` is a string.

IRIS/EarthScope is deliberately absent: its FDSNWS **event** service returns
HTTP 410, so there is nothing to capture.

The `*.quakeml.xml` files do the same for the **by-id** path: one captured QuakeML
document per provider and per include-flag set (`plain`, `arrivals`,
`allmagnitudes`, `allorigins`), parsed through ObsPy exactly as in production. What
they test is our serialization of each provider's real event structure, so the
by-id half of the matrix also survives an outage. `focalmechanism` shares the
`allmagnitudes` document, because it sends the same flag.

All five by-id tools against all four providers is twenty cells. Three have no
document to capture, because the provider does not implement the flag: EMSC refuses
`includeallmagnitudes` (HTTP 400), which takes out both `allmagnitudes` and
`focalmechanism`, and USGS refuses `includearrivals` (HTTP 501). Those responses are
captured verbatim as `*.error.txt` and asserted instead, which is what pins the three
cells. The remaining seventeen are driven from QuakeML.

The INGV event is `45376822` (Mw 3.5, Moggio Udinese, 2026-03-19), chosen because it
is complete: 150 arrivals, 6 origins, 6 magnitudes, 575 station magnitudes, 1235
amplitudes, and a focal mechanism with a moment tensor. Every INGV cell therefore has
real content to serialize rather than an empty subresource.

`ingv_no_arrivals.quakeml.xml` is a second INGV event, `37258271`, kept only because
it has **no** arrivals and so is the one document that exercises the three-state
contract from captured data: event found, subresource absent, explained in `message`
rather than returned as an empty payload. That is a property of that event, **not** of
INGV, which implements `includearrivals` and publishes arrivals for others.

Sizes are deliberate, not an oversight. The INGV documents run to ~2 MB each and the
EMSC arrivals one to ~580 kB, because these are real, fully populated events; picking
smaller ones would drop the only fixtures that exercise large nested payloads. XML
this repetitive compresses about thirtyfold, so the four INGV documents cost ~260 kB
of history despite occupying ~8 MB in a checkout.

## A/B harnesses (`ab/`)

Live experiments against a model behind OpenWebUI. Not collected by pytest; they
need `OPENWEBUI_API_KEY` and network access. `eventid_hallucination_ab.py` is the
artifact behind published numbers and is not modified.

`identity_continuity_ab.py` measures whether, after a `fdsn_query_earthquakes`
result from GFZ or EMSC, the model calls `fdsn_get_earthquake_by_eventid` with
both the right `eventid` **and** the right `datacenter`: omitting `datacenter`
silently queries INGV. The query results are rebuilt from the Honshu fixtures by
the server's own parser; the `baseline` arm offers the deployed tool (description
and schema read from the server), the `bound` arm adds one sentence,
`BINDING_NOTE`, binding the id to the datacenter that returned it. No user turn
names a datacenter or an id: both appear only in the replayed tool-call arguments
and tool results. The idea and
the scoring classes come from PR #1 by @joy7758.

| Scenario | Replayed history | Expected call |
|---|---|---|
| A | one GFZ query | `GFZ`, `gfz2024aati` |
| B | one EMSC query | `EMSC`, `20240101_0000127` |
| C | GFZ query, then EMSC query; asks for the second | `EMSC`, `20240101_0000127` |
| D | one failed GFZ query | no detail call |

The deployed `fdsn_query_earthquakes` is offered too, identical in both arms. In
A-C calling it is `UNEXPECTED_TOOL_CALL` (the result is already in context); in D a
re-query with no detail call is `CORRECT`, and any detail call is
`UNEXPECTED_TOOL_CALL`. Datacenters are compared as the server routes them, so
`GEOFON` and `GFZ` match; argument keys the tool does not define are ignored, as
the server ignores them, and listed in the attempt's `extra_args`.

The harness imports `fdsnws_event_server`, so run it from the project venv or
after `pip install -e .`.

```bash
export OPENWEBUI_API_KEY=sk-...
python tests/ab/identity_continuity_ab.py --base-url http://host:8586 \
    --model qwen2.5:72b-instruct --repeat 10 --variant both > run.jsonl
```

Output is JSON Lines: a `config` record, one `attempt` record per call (status and
raw assistant message), and a `summary` per scenario and arm with the eight class
counts and `correct_rate` (HTTP errors excluded from the denominator). The
production wording in `src/` changes only after a live run shows `bound` helps.
`unit/test_identity_continuity_ab.py` checks the scorer offline, and that the
replayed query results are byte-identical to what `fdsn_query_earthquakes` returns
for the same fixtures.

## Conventions

- Unit tests must not touch the network — mock `requests.get` (see
  `test_query_events_text.py`).
- Add new live checks under `integration/` with `pytestmark = pytest.mark.integration`.
- When a datacenter quirk is found, capture a fixture and add an offline test for the
  parser/handler, plus a live test documenting the real behaviour.
