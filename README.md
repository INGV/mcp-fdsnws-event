[![DOI](https://img.shields.io/badge/DOI-10.1016%2Fj.softx.2026.103001-blue)](https://doi.org/10.1016/j.softx.2026.103001)
[![Build Status](https://github.com/INGV/mcp-fdsnws-event/actions/workflows/docker-build-push.yml/badge.svg?branch=main)](https://github.com/INGV/mcp-fdsnws-event/actions/workflows/docker-build-push.yml?query=branch%3Amain)
[![Version](https://img.shields.io/badge/dynamic/yaml?label=ver&query=softwareVersion&url=https://raw.githubusercontent.com/INGV/mcp-fdsnws-event/main/publiccode.yml)](https://github.com/INGV/mcp-fdsnws-event/blob/main/publiccode.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/ingv/mcp-fdsnws-event)](https://hub.docker.com/r/ingv/mcp-fdsnws-event)
[![License](https://img.shields.io/github/license/INGV/mcp-fdsnws-event.svg)](https://github.com/INGV/mcp-fdsnws-event/blob/main/LICENSE)
[![GitHub issues](https://img.shields.io/github/issues/INGV/mcp-fdsnws-event.svg)](https://github.com/INGV/mcp-fdsnws-event/issues)

# FDSNWS Event MCP Server

An MCP (Model Context Protocol) server for querying the FDSN Web Service Event APIs of
multiple seismological datacenters (INGV, EMSC, GFZ, USGS, etc.) and retrieving
earthquake information as JSON.

## Features

- **Multi-datacenter**: works with any FDSN-compliant datacenter (INGV, EMSC, GFZ, USGS, and others)
- **8 MCP tools**: event search, single-event detail, arrivals, station magnitudes, amplitudes,
  magnitudes, origins, focal mechanisms
- **Two output shapes**: station-level collections (arrivals, station magnitudes, amplitudes,
  event lists) come back as a paginated table (`columns` + `rows`); solution-level objects
  (event, origins, magnitudes, focal mechanisms) come back as full QuakeML→JSON detail
- **Context-budgeted**: every result is bounded in bytes so a single tool call cannot push the
  user's own question out of an LLM's context window
- **stdio transport**: JSON-RPC 2.0 over stdin/stdout
- **Containerized**: ready to use with Docker

> **Upgrading from 1.x?** Release 2.0.0 renames every event-keyed tool and changes the
> response shape of three of them. See [Breaking changes in 2.0.0](#breaking-changes-in-200).

## Installation

### Prerequisites

- Docker
- Python 3.11+ (for local development)

### Option A: Pull from Docker Hub (recommended)

Prebuilt multi-arch images (linux/amd64, linux/arm64) are published on Docker Hub:

```bash
# Latest release
docker pull ingv/mcp-fdsnws-event:latest

# A specific version (replace X.Y.Z with a published tag)
docker pull ingv/mcp-fdsnws-event:X.Y.Z
```

> An `mcpo` variant (OpenAPI/REST wrapper, see below) is published under the same
> repository with a `-mcpo` suffix, e.g. `ingv/mcp-fdsnws-event:latest-mcpo` and
> `ingv/mcp-fdsnws-event:X.Y.Z-mcpo`.

### Option B: Build the container locally

```bash
# Clone the repository
git clone https://github.com/INGV/mcp-fdsnws-event.git
cd mcp-fdsnws-event

# Build the Docker image
docker build --no-cache -t ingv/mcp-fdsnws-event .
```

## Usage

### Start the MCP server

```bash
# Pull the published image (first run only)
docker pull ingv/mcp-fdsnws-event

# Start the MCP server (stdio)
docker run -i --rm ingv/mcp-fdsnws-event
```

The server listens for MCP connections over stdio.

### Testing

```bash
# Full unified test suite (recommended)
./run_tests.sh

# Individual runs (if needed)
# Unit tests (offline)
docker run --rm ingv/mcp-fdsnws-event pytest

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

The server exposes 8 tools. They all accept an optional `datacenter` parameter
(default: `"INGV"`). Supported datacenters: INGV, EMSC, GFZ, USGS, and other
FDSN-compliant services.

| Tool | Returns | Shape |
|------|---------|-------|
| [`fdsn_query_earthquakes`](#1-fdsn_query_earthquakes) | one row per event, preferred origin and magnitude | table |
| [`fdsn_get_earthquake_by_eventid`](#2-fdsn_get_earthquake_by_eventid) | event, preferred origin, preferred magnitude, focal mechanisms, counts | QuakeML detail |
| [`fdsn_get_arrivals_by_eventid`](#3-fdsn_get_arrivals_by_eventid) | phase arrivals, each joined with its pick | table |
| [`fdsn_get_allorigins_by_eventid`](#4-fdsn_get_allorigins_by_eventid) | all origin solutions | QuakeML detail |
| [`fdsn_get_allmagnitudes_by_eventid`](#5-fdsn_get_allmagnitudes_by_eventid) | all magnitude solutions | QuakeML detail |
| [`fdsn_get_focalmechanism_by_eventid`](#6-fdsn_get_focalmechanism_by_eventid) | focal mechanisms and moment tensors | QuakeML detail |
| [`fdsn_get_stationmagnitudes_by_eventid`](#7-fdsn_get_stationmagnitudes_by_eventid) | per-station magnitude readings, each joined with its amplitude | table |
| [`fdsn_get_amplitudes_by_eventid`](#8-fdsn_get_amplitudes_by_eventid) | measured amplitudes | table |

> **Note on IRIS.** IRIS/EarthScope no longer serves the FDSNWS *event* service:
> both `service.iris.edu` and `service.earthscope.org` answer
> `/fdsnws/event/1/query` with **HTTP 410 Gone**. It is therefore no longer
> advertised here; USGS is the recommended global substitute. Station and
> waveform services at EarthScope are unaffected (this server does not use them).

#### Table responses

Station-level collections have one entry per station reading, and an event can carry
thousands of them. Serialized as a list of QuakeML objects they overflow an LLM context
window on their own, so the four tools marked *table* above return a tabular payload
instead: the field names are stated once in `columns`, and `rows` carries one list of
values per record, in the same order. The column set is fixed per tool and always
complete — a field the datacenter does not populate comes back as `null` rather than
disappearing — so the shape does not change from one datacenter to the next.

**Identifiers are split in two.** QuakeML identifiers are URIs of which almost every
character is boilerplate repeated on every row, so the envelope carries an `id_prefixes`
map from column name to the prefix shared by that column's values, and the rows carry
only the remainder. The full identifier is the concatenation, character for character:

```json
{
  "id_prefixes": {
    "pick_id": "smi:webservices.ingv.it/fdsnws/event/1/query?pickId="
  },
  "columns": ["arrival_id", "pick_id", "phase", "..."],
  "rows": [["...", "817336441", "Pg", "..."]]
}
```

so the `pick_id` of that row is
`smi:webservices.ingv.it/fdsnws/event/1/query?pickId=817336441`. A column with no
worthwhile shared prefix is simply absent from the map and its rows already hold the
complete value. Nothing is dropped: this is an encoding, not a summary.

**Pagination.** Every table takes `limit` and `offset` (1-based) and answers with
`returned_count`, `has_more` and, when there is more, `next_offset`. Page by resending
the same call with `offset: next_offset` until `has_more` is false. `returned_count` can
come back below the `limit` asked for: the byte budget described under
[Configuration](#configuration) shrinks a page that would not fit rather than failing the
call, and `next_offset` still lines up. The three event-keyed tables also report
`total_count`, the number of rows matching the query once the filters below have been
applied, next to a `<resource>_count` (`arrivals_count`, `station_magnitudes_count`,
`amplitudes_count`) giving the size of the event's whole collection regardless of any
filter — which is how an empty page saying "this event has none at all" is told apart from
one saying "your filters matched none".

**Filters.** The three event-keyed tables accept `network` and `station` (exact,
case-insensitive), and the station-magnitude table also accepts `magnitude_type`. They are
applied by this server after the fetch, because no FDSN event service filters a
sub-resource by station; they exist so that "the amplitude at station SGRT" costs one
small answer instead of paging through every reading of the event.

**Every origin, not just the preferred one.** Arrival rows and station-magnitude rows span
every origin the datacenter returned for the event, with `origin_id` naming the origin and
`is_preferred_origin` marking the rows of the preferred solution, which are also sorted
first. Filtering to the preferred origin would be invisible data loss: an EMSC event comes
back with six origins carrying 137 preferred arrivals and 137 more elsewhere, while on INGV
every arrival already sits on the preferred origin and the rule costs nothing. The envelope
reports `origins_count` so the difference between datacenters is visible rather than
hidden. Amplitudes belong to the event rather than to an origin in QuakeML, so they carry
no `origin_id`.

#### 1. `fdsn_query_earthquakes`

Search seismic events with flexible filters. With no parameters it returns today's events.

**Parameters (all optional):**
- `starttime` / `endtime`: time window (ISO format: `YYYY-MM-DDTHH:MM:SS`). Default: today 00:00:00–23:59:59 UTC
- `updatedafter`: only events updated after this date/time
- `minmag` / `maxmag`: magnitude range
- `mindepth` / `maxdepth`: depth range (in km, the FDSN query-parameter unit)
- `minlat` / `maxlat` / `minlon` / `maxlon`: geographic bounding box
- `latitude` / `longitude` / `minradiuskm` / `maxradiuskm`: radial search
- `limit`: maximum number of events (default: 100, max: 240 — both configurable, see
  [Configuration](#configuration))
- `offset`: 1-based index of the first event (default: 1), to paginate together with `limit`
- `orderby`: sort order — `time` (default, most recent first), `time-asc`, `magnitude`, `magnitude-asc`
- `datacenter`: FDSN datacenter to query (default: `"INGV"`, overridable)

> **Note:** the bounding-box parameters and the radial-search parameters are mutually exclusive.

**Output:** a table (`columns` + `rows`, one row per event with the preferred origin and
magnitude; **depth in km**). `returned_count`, `limit`, `offset`, `has_more` and
`next_offset` sit at the top level of the response, next to a `query` echo of the
parameters actually sent upstream. The column names are normalised to the fourteen names
of the FDSN 1.2 text profile (`event_id`, `time`, `latitude`, `longitude`, `depth_km`,
`author`, `catalog`, `contributor`, `contributor_id`, `mag_type`, `magnitude`,
`mag_author`, `location_name`, `event_type`), because the datacenters spell their own
header differently — INGV writes `Depth/Km`, EMSC, GFZ and USGS write `Depth/km`, and
`EventType` is absent from EMSC and USGS entirely — and a column name that depends on who
answered is not something a client can be written against. An unrecognised column is
lowercased and passed through rather than dropped.

Unlike the event-keyed tables this one has no `total_count`: FDSN text carries no count,
so `has_more` is inferred from the page having been filled exactly. For the detail of a
single event use the `*_by_eventid` tools.

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

// Query USGS instead of INGV
{"minmag": 5.0, "starttime": "2025-01-01T00:00:00", "datacenter": "USGS"}
```

#### 2. `fdsn_get_earthquake_by_eventid`

Returns the core information for a single event: the event metadata, the preferred origin,
the preferred magnitude and the focal mechanisms, as full QuakeML detail. Note that the
preferred magnitude does not always belong to the preferred origin — on INGV event
`46107472` the preferred Mw 6.1 hangs off one origin while the preferred origin carries an
ML 6.2 — so `preferred_origin_id` and `preferred_magnitude_id` are both reported
explicitly.

**Station-level collections are no longer inlined.** Picks, amplitudes and station
magnitudes used to be serialized into this response, which is what made it the largest
answer this server could produce: 3510 kB for one INGV Mw 6.1 event, of which 1886 kB was
amplitudes alone. They now live in dedicated tools, and this response reports
`station_magnitudes_count` and `amplitudes_count` so a caller knows whether there is
anything to fetch. There is deliberately no `arrivals_count`: this fetch does not send
`includearrivals`, so every origin comes back with an empty arrival list, and a count
taken from it would read as a confident "this event has no phases" — ask
`fdsn_get_arrivals_by_eventid`, which does know.

**Parameters:**
- `eventid` (required): event identifier, as returned by `fdsn_query_earthquakes` in the
  `event_id` column. Treated as an opaque string matching `^[A-Za-z0-9_.:-]+$`, so the
  differing conventions of the providers are all accepted (`45376822` at INGV,
  `20240101_0000328` at EMSC, `gfz2024abmz` at GFZ, `us6000m0yg` at USGS). A JSON
  integer is still accepted and normalised.
- `datacenter` (optional): default `"INGV"`

#### 3. `fdsn_get_arrivals_by_eventid`

Returns the seismic phase arrivals of an event as a table, each row joined with the pick it
associates (station, time, phase, residual). Useful to know which stations recorded the
event and what was read on them.

Rows are ordered by distance from the hypocentre when the datacenter provides it —
the order a seismologist reads a phase list in — and by pick time otherwise, with the
preferred-origin rows first either way; `ordered_by` in the response says which was used.

**Parameters:**
- `eventid` (required): opaque event identifier, as described for
  `fdsn_get_earthquake_by_eventid` above.
- `datacenter` (optional): default `"INGV"`
- `network` / `station` (optional): exact, case-insensitive row filters
- `limit` (optional): default 90, max 90 (configurable)
- `offset` (optional): 1-based, default 1

#### 4. `fdsn_get_allorigins_by_eventid`

Returns all origin solutions (hypocentre locations) for an event, indicating which one is
preferred. Useful to compare locations computed by different agencies.

**Parameters:**
- `eventid` (required): opaque event identifier, as described for
  `fdsn_get_earthquake_by_eventid` above.
- `datacenter` (optional): default `"INGV"`

#### 5. `fdsn_get_allmagnitudes_by_eventid`

Returns all magnitude solutions computed for an event (ML, Mw, Mb, Md, etc.), indicating
which one is preferred. Useful for comparing magnitude types or agencies. For the
per-station readings behind a magnitude use `fdsn_get_stationmagnitudes_by_eventid`.

**Parameters:**
- `eventid` (required): opaque event identifier, as described for
  `fdsn_get_earthquake_by_eventid` above.
- `datacenter` (optional): default `"INGV"`

#### 6. `fdsn_get_focalmechanism_by_eventid`

Returns the focal mechanisms and moment tensors for an event: nodal planes (strike, dip,
rake), principal axes (T, P, N), and moment tensor components.

**Parameters:**
- `eventid` (required): opaque event identifier, as described for
  `fdsn_get_earthquake_by_eventid` above.
- `datacenter` (optional): default `"INGV"`

#### 7. `fdsn_get_stationmagnitudes_by_eventid`

Returns the per-station magnitude readings of an event as a table, each row joined with the
amplitude it was computed from. Useful when asked which stations contributed to a
magnitude, or for the magnitude measured at one station.

A station magnitude belongs to an origin, given by `origin_id`; where an origin carries
more than one magnitude, `station_magnitude_type` tells the readings apart and can be
filtered on. Rows are ordered by network, station and channel. Not every datacenter
publishes these: an empty table with a `message` means the event exists but carries none.

**Parameters:**
- `eventid` (required): opaque event identifier, as described for
  `fdsn_get_earthquake_by_eventid` above.
- `datacenter` (optional): default `"INGV"`
- `network` / `station` (optional): exact, case-insensitive row filters
- `magnitude_type` (optional): exact, case-insensitive filter on `station_magnitude_type`
- `limit` (optional): default 200, max 220 (configurable)
- `offset` (optional): 1-based, default 1

#### 8. `fdsn_get_amplitudes_by_eventid`

Returns the measured amplitudes of an event as a table: value, unit, period, signal-to-noise
ratio, time window and station. Useful when asked what was recorded, or for the amplitude
measured at one station.

In QuakeML an amplitude belongs to the event rather than to an origin, so all of them are
returned and many are not referenced by any station magnitude — of the 2152 amplitudes on
the INGV event measured, only 677 were. Rows are ordered by network, station and channel.
Not every datacenter publishes them: an empty table with a `message` means the event exists
but carries none.

**Parameters:**
- `eventid` (required): opaque event identifier, as described for
  `fdsn_get_earthquake_by_eventid` above.
- `datacenter` (optional): default `"INGV"`
- `network` / `station` (optional): exact, case-insensitive row filters
- `limit` (optional): default 125, max 125 (configurable)
- `offset` (optional): 1-based, default 1

## MCP client configuration

To use this server with an MCP client (such as Claude Desktop), add the following
configuration:

```json
{
  "mcpServers": {
    "fdsnws-event": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "ingv/mcp-fdsnws-event"]
    }
  }
}
```

To tune the server from here, pass the variables as `-e` flags among the `args`; see
[Configuration](#configuration) for why the client's `env` key does not reach the
container.

## Configuration

The server is configured entirely through environment variables. There is no
configuration file and nothing is read from a `.env` file inside the process: the
variables have to be present in the container's environment.

| Variable | Default | Effect |
|----------|---------|--------|
| `FDSN_TIMEOUT` | `45` | Timeout in seconds for a request to the datacenter. Read per call |
| `FDSN_MAX_RESULT_BYTES` | `36000` | Byte budget for a single tool result |
| `FDSN_MAX_ROWS_ARRIVALS` | `90` | Largest `limit` accepted by `fdsn_get_arrivals_by_eventid` |
| `FDSN_DEFAULT_ROWS_ARRIVALS` | `90` | `limit` used when the caller does not give one |
| `FDSN_MAX_ROWS_STATIONMAGNITUDES` | `220` | Largest `limit` accepted by `fdsn_get_stationmagnitudes_by_eventid` |
| `FDSN_DEFAULT_ROWS_STATIONMAGNITUDES` | `200` | `limit` used when the caller does not give one |
| `FDSN_MAX_ROWS_AMPLITUDES` | `125` | Largest `limit` accepted by `fdsn_get_amplitudes_by_eventid` |
| `FDSN_DEFAULT_ROWS_AMPLITUDES` | `125` | `limit` used when the caller does not give one |
| `FDSN_MAX_ROWS_EVENTS` | `240` | Largest `limit` accepted by `fdsn_query_earthquakes` |
| `FDSN_DEFAULT_ROWS_EVENTS` | `100` | `limit` used when the caller does not give one |

`.env.example` lists the same set, ready to copy to `.env` and pass with `--env-file`.

**Where the numbers come from.** `FDSN_MAX_RESULT_BYTES` is a context budget rather than a
storage limit, and both halves of the derivation are measured rather than assumed. The
density of this JSON is **1.87 bytes per token**: the slope of `prompt_eval_count` against
payload size measured on `qwen3.8:27b` under Ollama, 1511 bytes costing 476 tokens and
48 667 costing 25 744, linear in between. At that density 36 000 bytes is about 19 250
tokens, 59% of a 32k context window, leaving the rest for the conversation the result has to
live inside. Each table's maximum is then that budget divided by the widest row the finished
tool actually emitted against INGV, EMSC, GFZ and USGS — 391 B/row for arrivals (GFZ, whose
opaque identifiers share almost no prefix to lift out), 159 for station magnitudes, 279 for
amplitudes, 149 for events. The byte budget is the backstop underneath the row limits, not
the first line of defence: on a table it shrinks the page and sets `has_more`, so the call
still succeeds; on a solution-level object, which cannot be split, it returns a structured
error naming the size and pointing at the table tools.

These figures are calibrated for a 32k window, because that is the window of the deployment
this release was written for. **A larger window is exactly what these variables are there to
be raised for**: the numbers are policy, not a property of the data.

**Two things to know before changing a `MAX_ROWS_*`.** They are read once at import, because
each one is published as the `maximum` of that tool's `limit` parameter in the JSON schema
this server advertises in `tools/list` — they are not only what the server honours but what
the model is *told* it may ask for. So the container must be restarted for a change to take
effect, and any client that caches the tool schema has to be refreshed too: behind mcpo the
OpenAPI spec is generated at startup, so an Open WebUI tool server must be re-imported
before the new maximum is visible. A value that is not a positive integer is ignored with a
warning and the default is used; a `DEFAULT_ROWS_*` above its own `MAX_ROWS_*` is clamped
down with a warning rather than taking the server down on a config typo.

### Setting the variables

**1. Plain `docker run`** — how a stdio deployment normally runs:

```bash
docker run -i --rm \
  -e FDSN_TIMEOUT=60 \
  -e FDSN_MAX_ROWS_ARRIVALS=90 \
  ingv/mcp-fdsnws-event

# or, once repeating -e flags gets unwieldy
cp .env.example .env    # then edit
docker run -i --rm --env-file .env ingv/mcp-fdsnws-event
```

**2. The `mcpServers` JSON of an MCP client.** Mind the trap here: the `env` key of an
`mcpServers` entry sets the environment of the process the client spawns, which is the
`docker` binary — **not** the container it starts. Docker does not forward its own
environment into the container, so a variable placed in `env` silently has no effect. It
has to be carried in explicitly with `-e NAME=value` among the `args`:

```json
{
  "mcpServers": {
    "fdsnws-event": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "FDSN_MAX_RESULT_BYTES=36000",
        "-e", "FDSN_MAX_ROWS_ARRIVALS=90",
        "ingv/mcp-fdsnws-event"
      ]
    }
  }
}
```

(The `env` key is still the right place for variables meant for the `docker` client itself,
such as `DOCKER_HOST`.)

**3. Compose.** Both `compose.yml` and `compose.mcpo.yml` carry an `environment:` block
listing the whole set, commented out at its default; uncomment what you want to change.
The `mcpo` image runs the server as a child process inside the container, so there is no
`docker` binary in between and `environment:` — or `docker run -e` on that image — reaches
the server directly.

## Breaking changes in 2.0.0

Release 2.0.0 changes the tool surface. **Every mcpo and Open WebUI registration has to be
refreshed**, because the callable names change: in Open WebUI the generated names become
`tool_fdsn_get_arrivals_by_eventid_post` and so on, and a tool server imported before the
upgrade keeps advertising names that no longer exist.

**Tools renamed.** Every tool that takes an event identifier is now named `_by_eventid`,
after the input the caller must supply rather than after the QuakeML class the data comes
from. Naming a tool for a class it cannot be queried by — arrivals belong to an origin, but
no FDSN node accepts an origin id — invites a model to invent an identifier, which is the
exact failure this server already guards against elsewhere.

| 1.x | 2.0.0 |
|-----|-------|
| `fdsn_get_earthquake_by_id` | `fdsn_get_earthquake_by_eventid` |
| `fdsn_get_arrivals_by_id` | `fdsn_get_arrivals_by_eventid` |
| `fdsn_get_allorigins_by_id` | `fdsn_get_allorigins_by_eventid` |
| `fdsn_get_allmagnitudes_by_id` | `fdsn_get_allmagnitudes_by_eventid` |
| `fdsn_get_focalmechanism_by_id` | `fdsn_get_focalmechanism_by_eventid` |
| — | `fdsn_get_stationmagnitudes_by_eventid` (new) |
| — | `fdsn_get_amplitudes_by_eventid` (new) |

`fdsn_query_earthquakes` keeps its name.

**Response shape.** `fdsn_get_arrivals_by_eventid` no longer returns a list of QuakeML
objects: it returns `columns` + `rows` + `id_prefixes` with `limit`/`offset` pagination,
like the two new tools. Callers that walked an `arrivals` array must be rewritten against
the table.

**Flattened envelope on `fdsn_query_earthquakes`.** `returned_count`, `limit`, `offset`,
`has_more` and `next_offset` are now top-level keys of the response instead of a nested
`pagination` object, so all four tables share the same pagination keys.

**Normalized event column names.** `fdsn_query_earthquakes` no longer passes the
datacenter's own text header through. Columns are mapped to the FDSN 1.2 names, so
`Depth/Km` (INGV) and `Depth/km` (EMSC, GFZ, USGS) both become `depth_km`, `EventID`
becomes `event_id`, `EventLocationName` becomes `location_name`, and so on. Code that
indexed the header by its INGV spelling must be updated.

**No more station-level collections in `fdsn_get_earthquake_by_eventid`.** Picks,
amplitudes and station magnitudes are gone from that response; use the dedicated tools.
`station_magnitudes_count` and `amplitudes_count` are reported so you know whether to ask.

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
│   ├── config.py          # Env-driven limits (byte budget, row maxima/defaults)
│   ├── tables.py          # Tabular serialization: columns, rows, id prefixes, paging
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
- **EMSC**: `https://www.seismicportal.eu/fdsnws/event/1/query`
- **GFZ**: `https://geofon.gfz.de/fdsnws/event/1/query`
- **USGS**: `https://earthquake.usgs.gov/fdsnws/event/1/query`
- **Format**: search via `format=text` (tabular); detail via QuakeML (XML) → JSON
- **Documentation**: [FDSNWS Event API](https://www.fdsn.org/webservices/fdsnws-event-1.1.pdf)

## OpenWebUI integration (mcpo)

[OpenWebUI](https://docs.openwebui.com/features/extensibility/mcp/) talks to MCP servers
through [`mcpo`](https://github.com/open-webui/mcpo), a proxy that exposes an MCP server as
an OpenAPI/REST endpoint. This repository ships an `mcpo` wrapper that runs the server
directly (no docker-in-docker, no Docker socket mount): see `Dockerfile.mcpo` and
`compose.mcpo.yml`.

### Run

Pull and run the published `mcpo` image (recommended):

```bash
docker pull ingv/mcp-fdsnws-event:latest-mcpo
docker run -d -p 8000:8000 --name mcp-fdsnws-event_mcpo ingv/mcp-fdsnws-event:latest-mcpo
```

Or build it locally (for development):

```bash
docker build -t ingv/mcp-fdsnws-event .              # base image
docker compose -f compose.mcpo.yml up -d --build     # mcpo wrapper on :8000
```

This exposes:
- OpenAPI spec: `http://<host>:8000/openapi.json`
- Swagger UI: `http://<host>:8000/docs`
- One endpoint per tool, e.g. `POST http://<host>:8000/fdsn_query_earthquakes`

### Connect OpenWebUI

In OpenWebUI go to **Settings → Integrations** (or for _all_ users, **Admin Panel → Settings → Integrations**) and add a new "Tool Server" with the URL
`http://<host>:8000`.

- **If OpenWebUI itself runs in Docker**, `localhost:8000` from inside its container will not
  reach the host. Use `http://host.docker.internal:8000` (Docker Desktop) or the host LAN IP,
  or put both services on the same Docker network.
- **Securing the endpoint**: by default mcpo is exposed without authentication. To protect it,
  uncomment the `command:` line in `compose.mcpo.yml` to add `--api-key "<your-key>"`, then set
  the same key in OpenWebUI.

> **`compose.mcpo.yml` is a development configuration, not a hardened one.** It binds
> `0.0.0.0:8000` and publishes the port on every host interface, with authentication
> commented out, no rate limiting and no bound on concurrent upstream requests. That is
> fine on a workstation or inside a trusted network; it is not a public service. Exposing
> it beyond a trusted network means at minimum binding to `127.0.0.1` behind a reverse
> proxy that terminates TLS and enforces authentication and rate limits. Note that these
> are properties of the deployment rather than of the MCP server, which is read-only,
> stateless and holds no credentials — in stdio mode it opens no port at all.

> The wrapper image bundles `mcpo` and the server in a single image and runs
> `mcpo ... -- python -m fdsnws_event_server.server`, so it does **not** mount the Docker
> socket or spawn nested containers.

### Validating tool-call reliability (A/B harness)

When a client model loses an identifier across turns it may *invent* one — e.g.
calling `fdsn_get_arrivals_by_eventid` with a placeholder `eventid` (`123456`) instead
of the `event_id` returned by a prior `fdsn_query_earthquakes`. The server guards
against this with a three-state by-eventid contract (`found` / `message`), but the
behaviour itself lives in the OpenWebUI ↔ model loop and is best measured
empirically.

`tests/ab/eventid_hallucination_ab.py` is a standalone A/B harness (not run by
`pytest`) that replays the failing conversation against a live model through the
OpenWebUI OpenAI-compatible API and reports how often the model passes the correct
`eventid` vs an invented one. It takes the model and base URL as arguments:

```bash
export OPENWEBUI_API_KEY=sk-...        # OpenWebUI: Settings → Account → API Keys
python tests/ab/eventid_hallucination_ab.py \
    --base-url http://<host>:8080 \
    --model <model-id-as-listed-in-openwebui> \
    --repeat 10 --variant both --temperature 0.7
```

It fails fast if `--model` is not present on the instance, and compares two tool
descriptions (`baseline` vs `fixed`) so you can attribute any delta to the
server-side wording. Use it to check a new model, or to confirm that an OpenWebUI
configuration change (e.g. **Native** function calling) actually fixes id reuse.

## Citation

If you use this software in your research, please cite the accompanying paper,
published open access (CC BY 4.0) in *SoftwareX*:

> Lauciani, V., & Bailo, D. (2026). mcp-fdsnws-event: An MCP gateway for
> FDSNWS-event web services. *SoftwareX*, 35, 103001.
> https://doi.org/10.1016/j.softx.2026.103001

<details>
<summary>BibTeX</summary>

```bibtex
@article{lauciani2026mcpfdsnwsevent,
  title   = {mcp-fdsnws-event: An MCP gateway for FDSNWS-event web services},
  author  = {Lauciani, Valentino and Bailo, Daniele},
  journal = {SoftwareX},
  volume  = {35},
  pages   = {103001},
  year    = {2026},
  issn    = {2352-7110},
  doi     = {10.1016/j.softx.2026.103001}
}
```

</details>

A [`CITATION.cff`](CITATION.cff) file is also provided, so GitHub can generate the
citation in APA or BibTeX from the *Cite this repository* button.

## License

This project is released under the **GNU Affero General Public License v3.0 or
later** (AGPL-3.0-or-later). See the [`LICENSE`](LICENSE) file for the full text.

## Authors

See [`AUTHORS.md`](AUTHORS.md).

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch off `main`
3. Commit your changes (see [Conventional Commits](https://www.conventionalcommits.org/))
4. Open a Pull Request against `main`

By contributing you agree that your contributions are licensed under the
AGPL-3.0-or-later license of this project.

## Support

For problems or questions, open an issue in the
[GitHub repository](https://github.com/INGV/mcp-fdsnws-event/issues).
