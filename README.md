# FDSNWS Event MCP Server

An MCP (Model Context Protocol) server for querying the FDSN Web Service Event APIs of
multiple seismological datacenters (INGV, IRIS, EMSC, GFZ, etc.) and retrieving
earthquake information as JSON.

## Features

- **Multi-datacenter**: works with any FDSN-compliant datacenter (INGV, IRIS, EMSC, GFZ, and others)
- **6 MCP tools**: event search, single-event detail, arrivals, magnitudes, origins, focal mechanisms
- **Two output levels**: a compact tabular search result (from FDSN `format=text`) and a full
  QuakeML→JSON detail for a single event (the `*_by_id` tools)
- **stdio transport**: JSON-RPC 2.0 over stdin/stdout
- **Containerized**: ready to use with Docker

## Installation

### Prerequisites

- Docker
- Python 3.11+ (for local development)

### Build the container

```bash
# Clone the repository
git clone <repository-url>
cd mcp-fdsnws-event

# Build the Docker image
docker build --no-cache -t mcp-fdsnws-event-server .
```

## Usage

### Start the MCP server

```bash
# Start the MCP server
docker run -i mcp-fdsnws-event-server
```

The server listens for MCP connections over stdio.

### Testing

```bash
# Full unified test suite (recommended)
./run_tests.sh

# Individual runs (if needed)
# Unit tests (offline)
docker run --rm mcp-fdsnws-event-server pytest

# MCP protocol smoke test
echo -e '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0.0"}}}\n{"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}\n{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}' | docker run -i --rm mcp-fdsnws-event-server
```

`run_tests.sh` automatically runs:
1. Docker image build
2. Unit tests (offline)
3. MCP protocol smoke test

To also run the live tests against INGV: `./run_tests.sh --integration`. This is the
recommended way to validate all functionality.

### Available tools

The server exposes 6 tools. They all accept an optional `datacenter` parameter
(default: `"INGV"`). Supported datacenters: INGV, IRIS, EMSC, GFZ, and other
FDSN-compliant services.

#### 1. `fdsn_query_earthquakes`

Search seismic events with flexible filters. With no parameters it returns today's events.

**Parameters (all optional):**
- `starttime` / `endtime`: time window (ISO format: `YYYY-MM-DDTHH:MM:SS`). Default: today 00:00:00–23:59:59 UTC
- `updatedafter`: only events updated after this date/time
- `minmag` / `maxmag`: magnitude range
- `mindepth` / `maxdepth`: depth range (in km, the FDSN query-parameter unit)
- `minlat` / `maxlat` / `minlon` / `maxlon`: geographic bounding box
- `latitude` / `longitude` / `minradiuskm` / `maxradiuskm`: radial search
- `limit`: maximum number of events (default: 100, max: 1000)
- `offset`: 1-based index of the first event (default: 1), to paginate together with `limit`
- `orderby`: sort order — `time` (default, most recent first), `time-asc`, `magnitude`, `magnitude-asc`
- `datacenter`: FDSN datacenter to query (default: `"INGV"`, overridable)

> **Note:** the bounding-box parameters and the radial-search parameters are mutually exclusive.

**Output:** a compact tabular result (`columns` + `rows`, one row per event with the
preferred origin/magnitude; **depth in km**) plus a `pagination` block
(`returned_count`, `has_more`, `next_offset`). For the full detail of a single event,
use the `*_by_id` tools (complete QuakeML, depth in meters).

**Examples:**

```json
// Today's events (default)
{}

// Significant events (M>=4.0) over the last week
{"minmag": 4.0, "starttime": "2025-07-08T00:00:00", "limit": 50}

// Specific geographic area (Central Italy)
{"minlat": 41.0, "maxlat": 43.0, "minlon": 12.0, "maxlon": 15.0, "minmag": 2.0}

// Radial search (50 km around Rome)
{"latitude": 41.9, "longitude": 12.5, "maxradiuskm": 50}

// Query IRIS instead of INGV
{"minmag": 5.0, "starttime": "2025-01-01T00:00:00", "datacenter": "IRIS"}
```

#### 2. `fdsn_get_earthquake_by_id`

Returns the basic information for a single event: preferred origin, preferred magnitude,
station magnitudes, and amplitudes.

**Parameters:**
- `eventid` (required): event ID (integer)
- `datacenter` (optional): default `"INGV"`

#### 3. `fdsn_get_arrivals_by_id`

Returns all seismic phase arrivals for an event, with the associated picks (station,
arrival time, phase). Useful to know which stations recorded the event.

**Parameters:**
- `eventid` (required): event ID (integer)
- `datacenter` (optional): default `"INGV"`

#### 4. `fdsn_get_allmagnitudes_by_id`

Returns all magnitude solutions computed for an event (ML, Mw, Mb, Md, etc.), indicating
which one is preferred. Useful for comparing magnitude types or agencies.

**Parameters:**
- `eventid` (required): event ID (integer)
- `datacenter` (optional): default `"INGV"`

#### 5. `fdsn_get_allorigins_by_id`

Returns all origin solutions (hypocenter locations) for an event, indicating which one is
preferred. Useful to compare locations computed by different agencies.

**Parameters:**
- `eventid` (required): event ID (integer)
- `datacenter` (optional): default `"INGV"`

#### 6. `fdsn_get_focalmechanism_by_id`

Returns the focal mechanisms and moment tensors for an event: nodal planes (strike, dip,
rake), principal axes (T, P, N), and moment tensor components.

**Parameters:**
- `eventid` (required): event ID (integer)
- `datacenter` (optional): default `"INGV"`

## MCP client configuration

To use this server with an MCP client (such as Claude Desktop), add the following
configuration:

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

## Example queries

### Today's events
```
"Show me today's earthquakes in Italy"
```

### Significant events
```
"What were the strongest earthquakes of the last week?"
```

### Events in a specific region
```
"Find earthquakes with magnitude above 3.0 in central Italy over the last 30 days"
```

## Development

### Project structure

```
mcp-fdsnws-event/
├── src/fdsnws_event_server/
│   ├── __init__.py
│   ├── server.py          # MCP server (FastMCP tool definitions)
│   ├── models.py          # Pydantic input validation models
│   └── obspy_client.py    # FDSN format=text query + ObsPy QuakeML→JSON detail
├── tests/                 # pytest: unit (offline) + integration (live)
│   ├── fixtures/          # Real FDSN format=text responses
│   ├── unit/              # Parser, query, models (network mocked)
│   ├── integration/       # Live INGV tests (@pytest.mark.integration)
│   └── README.md          # How to run the tests
├── pyproject.toml         # Python configuration
├── Dockerfile             # Docker container
├── run_tests.sh           # Full test suite
└── README.md
```

### Local testing

```bash
# Install dependencies (with dev extras for pytest)
pip install -e ".[dev]"

# Run the tests
pytest                 # unit (offline, default)
pytest -m integration  # live tests against INGV (network required)
```

Test details and conventions in [`tests/README.md`](tests/README.md).

### FDSN API

The server queries any FDSN-compliant datacenter:
- **INGV** (default): `https://webservices.ingv.it/fdsnws/event/1/query`
- **IRIS**: `https://service.iris.edu/fdsnws/event/1/query`
- **EMSC**: `https://www.seismicportal.eu/fdsnws/event/1/query`
- **GFZ**: `https://geofon.gfz-potsdam.de/fdsnws/event/1/query`
- **Format**: search via `format=text` (tabular); detail via QuakeML (XML) → JSON
- **Documentation**: [FDSNWS Event API](https://www.fdsn.org/webservices/fdsnws-event-1.1.pdf)

## OpenWebUI integration (mcpo)

[OpenWebUI](https://docs.openwebui.com/features/extensibility/mcp/) talks to MCP servers
through [`mcpo`](https://github.com/open-webui/mcpo), a proxy that exposes an MCP server as
an OpenAPI/REST endpoint. This repository ships an `mcpo` wrapper that runs the server
directly (no docker-in-docker, no Docker socket mount): see `Dockerfile.mcpo` and
`compose.mcpo.yml`.

### Run

```bash
# Build the base image once
docker build -t fdsnws-event-server .

# Start mcpo (builds the wrapper image and exposes it on :8000)
docker compose -f compose.mcpo.yml up -d --build
```

Equivalent without compose:

```bash
docker build -t fdsnws-event-server .
docker build -f Dockerfile.mcpo -t fdsnws-event-mcpo .
docker run -d -p 8000:8000 --name fdsnws-mcpo fdsnws-event-mcpo
```

This exposes:
- OpenAPI spec: `http://<host>:8000/openapi.json`
- Swagger UI: `http://<host>:8000/docs`
- One endpoint per tool, e.g. `POST http://<host>:8000/fdsn_query_earthquakes`

### Connect OpenWebUI

In OpenWebUI go to **Settings → Tools** (or **Admin → Settings → Tools**) and add the URL
`http://<host>:8000`.

- **If OpenWebUI itself runs in Docker**, `localhost:8000` from inside its container will not
  reach the host. Use `http://host.docker.internal:8000` (Docker Desktop) or the host LAN IP,
  or put both services on the same Docker network.
- **Securing the endpoint**: by default mcpo is exposed without authentication. To protect it,
  uncomment the `command:` line in `compose.mcpo.yml` to add `--api-key "<your-key>"`, then set
  the same key in OpenWebUI.

> The wrapper image bundles `mcpo` and the server in a single image and runs
> `mcpo ... -- python -m fdsnws_event_server.server`, so it does **not** mount the Docker
> socket or spawn nested containers.

## License

This project is released under the MIT license.

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a branch for your feature
3. Commit your changes
4. Open a Pull Request

## Support

For problems or questions, open an issue in the GitHub repository.
