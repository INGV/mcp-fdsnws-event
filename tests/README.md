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

## Conventions

- Unit tests must not touch the network — mock `requests.get` (see
  `test_query_events_text.py`).
- Add new live checks under `integration/` with `pytestmark = pytest.mark.integration`.
- When a datacenter quirk is found, capture a fixture and add an offline test for the
  parser/handler, plus a live test documenting the real behaviour.
