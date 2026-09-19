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

## Conventions

- Unit tests must not touch the network — mock `requests.get` (see
  `test_query_events_text.py`).
- Add new live checks under `integration/` with `pytestmark = pytest.mark.integration`.
- When a datacenter quirk is found, capture a fixture and add an offline test for the
  parser/handler, plus a live test documenting the real behaviour.

## Optional identity-continuity A/B research harness

`ab/identity_continuity_ab.py` measures whether the next by-id call preserves
**(datacenter, EventID)** from a selected prior query result. Unlike the historical
`eventid_hallucination_ab.py` (left unchanged), it checks provider context as well
as the ID, using the current string-ID contract. It is an optional live model
experiment, **not run by pytest/CI**; its deterministic classifier and mocked
transport tests in `unit/test_identity_continuity_ab.py` do run offline.

The production contract is unchanged: `EventID` is an opaque provider-specific
string; a legacy JSON integer is accepted and converted to decimal digits, but
strings are never reformatted. Query tables contain `datacenter` and an `EventID`
column. By-id tools require `eventid` and accept `datacenter`, defaulting to INGV.
Omitting it for a GFZ/EMSC target therefore selects the wrong provider.

Four fixed **synthetic** conversations replay the query result and ask for basic
event details through `fdsn_get_earthquake_by_id`:

| Scenario | Selected identity / expected action |
|---|---|
| A | GFZ, `gfz2024abmz` |
| B | EMSC, `20240101_0000328` (underscore preserved) |
| C | EMSC identity from the second query, after a GFZ result |
| D | Failed query, no valid target: no tool call is correct |

The tables use multiple rows; the final request selects by magnitude (and query
order in C), without repeating an ID or provider. Fixture values are not claims
about real earthquakes. The harness does not execute the generated tool calls or
contact FDSN services. It evaluates one completion after replayed turns, not an
autonomous search-and-retrieval loop.

`baseline` uses normal verbatim EventID instructions. `bound` adds **only one
sentence to the same tool description**: copy the identity pair from the selected
query result without falling back to another provider/default. Messages, tools,
argument types, model and temperature otherwise stay identical. Both prompts
specify at most one detail call. Attempts alternate baseline/bound order across
repeats, with a fresh replay for every completion.

```bash
# Set OPENWEBUI_API_KEY in your environment; do not commit credentials.
python tests/ab/identity_continuity_ab.py \
  --base-url http://localhost:8080 --model YOUR_MODEL_ID \
  --repeat 10 --temperature 0.7 --variant both > identity-ab.jsonl

# Optional: --scenario A (or B/C/D); --variant baseline or --variant bound.
# OpenAI-compatible endpoints: supply the explicit API root, e.g. https://HOST/v1.
```

An OpenWebUI host defaults to `/api`; explicit `/api` and `/v1` roots are accepted.
The script first checks `/models` for the requested model. Preflight failure exits
2 with `LIVE_AB=NOT_RUN`; there are no retries or automatic sample increases.
The default full run makes 80 completions plus one model-list request. JSON Lines
output retains configuration, each assistant response/classification, and a
summary per scenario × variant. Keep the raw output and code revision with any
reported experiment. Review responses before sharing them.

Classification is deterministic, with mutually exclusive attempt counts:

- `CORRECT`: both fields match; in D, no tool call.
- `WRONG_EVENTID`, `WRONG_DATACENTER`, `WRONG_BOTH`: valid arguments with the
  respective mismatch. Missing `datacenter` means INGV. Named datacenters are
  case-insensitive, as in the production ObsPy path; ID strings stay exact.
  Whitespace, provider aliases and URLs are not normalized.
- `NO_TOOL_CALL`: no call when a valid target exists.
- `UNEXPECTED_TOOL_CALL`: any call in D, an unoffered tool, or multiple calls.
  A correct call cannot hide an additional wrong call.
- `MALFORMED_ARGS`: malformed call structure/JSON, duplicate keys, missing
  `eventid`, invalid types/ID syntax, or extra arguments. D's any-call rule takes
  precedence over argument parsing.
- `HTTP_ERROR`: transport, HTTP, invalid API envelope, unsupported legacy
  function-call protocol, or incomplete completion. These attempts are unscored;
  error output contains exception classes, not response bodies or credentials.
  The simulated failed FDSN query in D is task input, not an infrastructure failure.

Each summary reports total attempts, evaluable attempts (`total − HTTP_ERROR`),
all eight counts and `correct_rate = CORRECT / evaluable` (null if none). The
three identity-error counts are separate categories; `WRONG_BOTH` is not also
counted in either single-field category. Exit 1 indicates infrastructure failures;
exit 0 means the run completed, **not** that every model action was correct.
Small fixed scenarios and small samples do not establish statistical significance
or general agent reliability, nor guarantee that the added wording helps.
