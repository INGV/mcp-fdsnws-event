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
docker run --rm fdsnws-event-server pytest                 # unit
docker run --rm fdsnws-event-server pytest -m integration  # live
```

## Layout

```
tests/
├── fixtures/
│   └── ingv_emilia_2012-05-29.txt   # real FDSN format=text response (8 events)
├── unit/
│   ├── test_parse_fdsn_text.py      # header-driven parsing, edge cases
│   ├── test_query_events_text.py    # 200 / 204 / 400 / network error, URL building
│   └── test_models.py               # bbox-vs-radial, offset, orderby, ranges
└── integration/
    └── test_live_ingv.py            # live query, offset paging, error passthrough, by-id
```

## Conventions

- Unit tests must not touch the network — mock `requests.get` (see
  `test_query_events_text.py`).
- Add new live checks under `integration/` with `pytestmark = pytest.mark.integration`.
- When a datacenter quirk is found, capture a fixture and add an offline test for the
  parser/handler, plus a live test documenting the real behaviour.
