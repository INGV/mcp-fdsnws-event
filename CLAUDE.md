# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An MCP (Model Context Protocol) server that wraps FDSN Web Service Event APIs (multi-datacenter: INGV, IRIS, EMSC, GFZ, etc.), providing earthquake data to MCP clients (e.g., Claude Desktop). The server communicates over stdio (JSON-RPC 2.0) and uses ObsPy to query any FDSN-compliant datacenter.

## Commands

### Local development

```bash
# Install dependencies (with dev extras for pytest)
pip install -e ".[dev]"

# Run the server directly (stdio MCP)
python -m fdsnws_event_server.server

# Run tests locally
pytest                 # unit only (offline, default)
pytest -m integration  # live tests against INGV (network required)
```

### Docker (primary workflow)

```bash
# Build image
docker build -t mcp-fdsnws-event-server .

# Run the full test suite (build + API tests + MCP protocol test)
./run_tests.sh

# Run server for MCP client connection
docker run -i mcp-fdsnws-event-server

# Run tests inside Docker
docker run --rm mcp-fdsnws-event-server pytest                 # unit (offline)
docker run --rm mcp-fdsnws-event-server pytest -m integration  # live INGV

# Manual MCP protocol test
echo -e '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0.0"}}}\n{"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}\n{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}' | docker run -i --rm mcp-fdsnws-event-server

# Interactive shell inside container (for debugging)
docker run -it --rm mcp-fdsnws-event-server bash
```

**Rule**: after every code change, run `./run_tests.sh` to validate.

### CI / Release (ADR-0005)

GitHub Actions (`.github/workflows/docker-build-push.yml`) builds multi-arch images
(amd64/arm64) and pushes them to the single Docker Hub repo `ingv/mcp-fdsnws-event`:

- push to `main` → `main` (base) + `main-mcpo`
- tag `vX.Y.Z` → `X.Y.Z` (the `v` is stripped) + `X.Y.Z-mcpo`, and `latest` / `latest-mcpo` on the newest tag
- pull request → build only, no push

The mcpo image builds `FROM` the just-pushed base via `ARG BASE_IMAGE` (digest passed
by the workflow). On every tag push a retention step keeps only the **5 latest semver
versions** (base + `-mcpo`); `latest*` / `main*` are never deleted. Requires
`DOCKER_HUB_USERNAME` / `DOCKER_HUB_ACCESS_TOKEN` secrets (the token needs **delete**
scope for retention). License: **AGPL-3.0-or-later** (ADR-0004).

### Claude Desktop integration

```json
{
  "mcpServers": {
    "fdsnws-event": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "mcp-fdsnws-event-server"]
    }
  }
}
```

## Architecture

The server follows a four-layer pipeline:

```
MCP Client (stdio JSON-RPC 2.0)
         ↓
  server.py  (FastMCP tools)           ← MCP protocol, tool definitions, request routing
         ↓
  models.py  (Pydantic input models)   ← validation, datacenter parameter
         ↓
  obspy_client.py                      ← ObsPy FDSN Client, per-request datacenter selection
         ↓
  Any FDSN datacenter (INGV, IRIS, EMSC, GFZ, ...)
```

### Component responsibilities

| File | Role | Pattern |
|------|------|---------|
| `server.py` | MCP tool definitions, request routing | Command/Factory via `@mcp.tool()` decorators |
| `models.py` | Pydantic input validation models | Each model includes `datacenter` field (default: `"INGV"`) |
| `obspy_client.py` | Summary queries via `requests` + `format=text` parser; detail (by-id) via ObsPy QuakeML + `obspy_to_dict()` serializer | Two paths: `query_events_text()` (text) and `_get_events_quakeml()` (ObsPy) |

> Architecture decisions are recorded in `docs/adr/` and `docs/design/`; domain terms in `CONTEXT.md`.

### Key design details

- **Transport**: stdio only — no network port exposed; this is why `docker run -i` is required.
- **MCP SDK compatibility**: handlers must be registered via `@mcp.tool()` decorators (FastMCP pattern).
- **Two data paths** (ADR-0001): `fdsn_query_earthquakes` fetches `format=text` directly via `requests` and returns a compact tabular JSON (`columns` + `rows`), parsed by `parse_fdsn_text()` from the `#` header. The `*_by_id` tools fetch full QuakeML via ObsPy and serialize the whole tree with `obspy_to_dict()`.
- **Datacenter-agnostic** (ADR-0002): no INGV-specific behaviour. `datacenter` (default `"INGV"`, overridable) validated against `obspy.clients.fdsn.header.URL_MAPPINGS`. Semantic/cross-field validation is deferred to the datacenter; Pydantic does structural checks only (plus the bbox/radial mutual-exclusion guard).
- **Depth units**: summary (text) depth is in **km** (`Depth/Km`); detail (QuakeML) depth is in **meters**. Deliberate, see ADR-0001.
- **Pagination** (ADR-0003): `offset`/`limit` are FDSN passthrough; response carries `returned_count`, `has_more`, `next_offset`. `offset` is omitted at its default (1) to avoid INGV's off-by-one on the common query. `orderby` exposed (default `time`).
- **Error handling**: upstream HTTP ≥ 400 / network failures raise `DatacenterError` → returned as a structured `{error, datacenter, api_url, message}` payload carrying the datacenter's message verbatim. HTTP 204/404 = no data → empty result.
- **Timeout**: request timeout from `FDSN_TIMEOUT` env (default 45s).
- **Full QuakeML serialization** (by-id only): `obspy_to_dict()` recursively converts the ObsPy tree to JSON; no fields dropped. Values stay in original QuakeML units. `preferred_origin_id`/`preferred_magnitude_id` serialized as ResourceIdentifier strings; `event_id` (numeric suffix via `_extract_event_id()`) added for convenience.
- **Default time window**: today 00:00:00–23:59:59 UTC when `starttime`/`endtime` are omitted.

### MCP tools exposed

The server exposes 6 tools (all JSON-only output). `fdsn_query_earthquakes` returns a compact tabular result (`columns` + `rows`, preferred solution only, depth in km); the `*_by_id` tools return full QuakeML detail.

| Tool | Required params | Optional params |
|------|----------------|-----------------|
| `fdsn_query_earthquakes` | none | `starttime`, `endtime`, `updatedafter`, `minmag`, `maxmag`, `minlat`, `maxlat`, `minlon`, `maxlon`, `mindepth`, `maxdepth`, `latitude`, `longitude`, `minradiuskm`, `maxradiuskm`, `limit` (default 100, max 1000), `offset` (default 1), `orderby` (`time`/`time-asc`/`magnitude`/`magnitude-asc`, default `time`), `datacenter` (default "INGV"). Note: radial params (`latitude`/`longitude`/`minradiuskm`/`maxradiuskm`) and bounding-box params (`minlat`/`maxlat`/`minlon`/`maxlon`) are mutually exclusive. |
| `fdsn_get_earthquake_by_id` | `eventid` (int) | `datacenter` (default "INGV") |
| `fdsn_get_arrivals_by_id` | `eventid` (int) | `datacenter` (default "INGV") — returns arrivals cross-referenced with picks (station, time, phase) |
| `fdsn_get_allmagnitudes_by_id` | `eventid` (int) | `datacenter` (default "INGV") — returns all magnitude solutions |
| `fdsn_get_allorigins_by_id` | `eventid` (int) | `datacenter` (default "INGV") — returns all origin solutions |
| `fdsn_get_focalmechanism_by_id` | `eventid` (int) | `datacenter` (default "INGV") — returns focal mechanisms with nodal planes (strike, dip, rake), principal axes (T, P, N), and moment tensor components |

### Runtime requirements

- Docker Engine ≥ 20.10
- ~100 MB Docker image (python:3.11-slim + libxml2/libxslt)
- ~80–128 MB RAM at runtime
- HTTPS outbound access to FDSN datacenters (e.g., `webservices.ingv.it`, `service.iris.edu`, `www.seismicportal.eu`, `geofon.gfz-potsdam.de`)
- Container runs as non-root user `mcp` (uid 1000)

### Extending the server

To add a new MCP tool:
1. Create a Pydantic input model in `models.py`
2. Implement the ObsPy query function in `obspy_client.py`
3. Add an `@mcp.tool()` decorated async function in `server.py`

## AI Implementation user guide
Only respond with short and concise answers.

### Test your changes
After completing a change or a feature, always kick off one to three Code Review Agents to ensure that the code is of a high quality.

### Development
Whenever possible, try not to implement the changes yourself. Depending on the complexity of the change, kick off several coder agents to run in the background and, if possible, in parallel.