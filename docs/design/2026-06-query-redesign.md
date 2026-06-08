# Design: query redesign + datacenter-agnostic hardening

Consolidated outcome of the design review (grill-with-docs session, 2026-06).
Decisions that are hard to reverse are recorded as ADRs in `../adr/`; this document
is the implementation plan. Domain terms are defined in `../../CONTEXT.md`.

## Goals

1. Keep the LLM context budget under control on list queries (token economy).
2. Make the server work with any FDSNWS-Event datacenter (no INGV-specific code).
3. Honest, robust behaviour: spec-compliant pagination, real timeouts, error
   messages that surface the datacenter's own text, deterministic tests.

## Decisions (summary)

| # | Area | Decision | ADR |
|---|------|----------|-----|
| 1 | Query output | `fdsn_query_earthquakes` uses FDSN `format=text` → tabular JSON (`columns` + `rows`), header-driven parse | 0001 |
| 2 | Detail tools | `*_by_id` stay full QuakeML via ObsPy | 0001 |
| 3 | Depth units | summary = km, detail = metres (documented) | 0001 |
| 4 | Errors | `FDSNException`/network → structured JSON `{error, datacenter, api_url, message}`, datacenter message verbatim; 204 → empty; `ValidationError` → short, client-side | 0002 |
| 5 | Validation boundary | Pydantic structural only; semantic cross-field → datacenter. Exception: bbox/radial guard | 0002 |
| 6 | Datacenter-agnostic | remove `INGV_WADL_PARAMS` / `_validate_params` and all INGV references in code | 0002 |
| 7 | Default datacenter | `INGV`, overridable, documented as a mere default | 0002 |
| 8 | Pagination | `offset` + `limit` spec passthrough; response `returned_count` + `has_more` + `next_offset`; no INGV-bug workaround | 0003 |
| 9 | Orderby | exposed, default `time` | — |
| 10 | Timeout | default 45s, env `FDSN_TIMEOUT` | — |
| 11 | Client construction | plain `Client(datacenter, timeout=...)` per request | — |
| 12 | Extra params | `eventtype`/`magnitudetype`/`catalog`/`contributor` NOT exposed (INGV ignores them silently); revisit when implemented | 0002 |
| 13 | Testing | pytest unit(offline/mock) + integration(live, opt-in) + tests README | — |

## Tabular query response (shape)

```json
{
  "datacenter": "INGV",
  "api_url": "https://webservices.ingv.it/fdsnws/event/1/query?...&format=text",
  "query": { "...echoed params..." },
  "pagination": { "returned_count": 100, "limit": 100, "offset": 1,
                  "has_more": true, "next_offset": 101 },
  "columns": ["EventID","Time","Latitude","Longitude","Depth/Km","Author",
              "Catalog","Contributor","ContributorID","MagType","Magnitude",
              "MagAuthor","EventLocationName","EventType"],
  "rows": [["863301","2012-05-29T23:58:58.770000","44.8797","11.0305","8.1","REMO-INGV",
            "","","","ML","2.1","--","3 km E San Possidonio (MO)","earthquake"]]
}
```

- `columns` come from the `#`-prefixed header line of the FDSN text response (not
  positional), so other datacenters' column sets are handled.
- Empty result (HTTP 204) → `columns` from a default header or `[]`, `rows: []`,
  `has_more: false`.

## Structured error response (shape)

```json
{ "error": true, "datacenter": "INGV",
  "api_url": "https://webservices.ingv.it/fdsnws/event/1/query?...",
  "message": "<verbatim datacenter body, e.g. the 400 'starttime must be before endtime'>" }
```

## Implementation plan

`src/fdsnws_event_server/obspy_client.py`
- Remove `INGV_WADL_PARAMS` and `_validate_params`.
- `_get_client(datacenter)`: read timeout from `FDSN_TIMEOUT` env (default 45.0),
  `Client(datacenter, timeout=...)`.
- New `query_events_text(...)`: build kwargs (add `offset`, `orderby`), call
  `get_events(format="text", filename=io.BytesIO(), **kwargs)` in a thread, capture
  raw text, parse header + rows → `(columns, rows, api_url)`. Catch
  `FDSNNoDataException` → empty; catch `FDSNException`/`requests` errors → raise a
  typed `DatacenterError(message, status)` carrying the verbatim body.
- `*_by_id` paths: unchanged QuakeML flow, but wrapped in the same
  `DatacenterError` handling.
- Add `parse_fdsn_text(raw: str) -> tuple[list[str], list[list[str]]]` (pure, unit-testable).

`src/fdsnws_event_server/models.py`
- `QueryEarthquakesInput`: add `offset: int = Field(default=1, ge=1)` and
  `orderby: Literal["time","time-asc","magnitude","magnitude-asc"] = "time"`.
- Replace "INGV event ID" → "FDSN event ID" in the by-id models.
- Keep the bbox/radial model validator.

`src/fdsnws_event_server/server.py`
- Module docstring: drop "INGV".
- `fdsn_query_earthquakes`: pass `offset`/`orderby`; build the tabular response with
  the pagination block; on `DatacenterError` return the structured error payload.
- Map `DatacenterError` → structured payload in every tool.

`pyproject.toml`
- description: "MCP server for the FDSNWS Event API" (drop "INGV").
- `[project.optional-dependencies] dev = ["pytest", ...]`.

`tests/`
- `tests/fixtures/ingv_emilia_2012-05-29.txt` — captured real FDSN text.
- `tests/unit/test_parse_fdsn_text.py`, `test_pagination.py`, `test_errors.py`,
  `test_models.py` — offline, network mocked.
- `tests/integration/test_live_ingv.py` — `@pytest.mark.integration`, opt-in.
- `tests/README.md` — how to run unit vs integration, what each covers.
- `pyproject.toml` `[tool.pytest.ini_options]`: register the `integration` marker,
  default run excludes it.

`run_tests.sh` — keep Docker build + MCP `tools/list` smoke test; replace the three
ad-hoc scripts with `pytest` (unit by default, integration on a flag).

## Verification

- `pytest` (unit) green offline.
- `pytest -m integration` against INGV returns events and paginates.
- `./run_tests.sh` builds and the MCP `tools/list` smoke test passes.
- Manual: `fdsn_query_earthquakes` returns tabular JSON within budget; a bad query
  (e.g. endtime < starttime) returns the structured error with INGV's verbatim text.
