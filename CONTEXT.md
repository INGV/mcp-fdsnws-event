# FDSNWS Event MCP

Glossary for the seismic-event domain this MCP server exposes to MCP clients. Terms follow FDSN / QuakeML usage as surfaced through the tools.

## Language

**Event**:
A single seismic occurrence (typically an earthquake) identified by a numeric event ID. The top-level entity every tool returns.
_Avoid_: quake, record.

**Datacenter**:
An FDSN-compliant provider queried for events (INGV by default; also IRIS, EMSC, GFZ, …). Chosen per request.
_Avoid_: server, source, agency, provider.

**Origin**:
A computed hypocentre solution for an Event — time, latitude, longitude, depth. An Event may have several.
_Avoid_: location, hypocenter.

**Magnitude**:
A computed size solution for an Event (ML, Mw, Mb, Md, …). An Event may have several.
_Avoid_: mag, size.

**Preferred solution**:
The Origin / Magnitude the Datacenter marks as authoritative for an Event (`preferred_origin_id` / `preferred_magnitude_id`). Summary queries expose only the preferred Origin and Magnitude.
_Avoid_: main, default, best.

**Pick**:
An observed seismic-phase reading at a single station (the arrival time of a P or S phase).
_Avoid_: detection.

**Arrival**:
The association of a Pick with an Origin — links a station reading to a specific hypocentre solution.
_Avoid_: phase (phase is an attribute of a Pick/Arrival, not a synonym).

**Focal mechanism**:
The rupture-geometry solution for an Event — nodal planes (strike, dip, rake), principal axes (T, P, N) and moment tensor components.
_Avoid_: beachball, source mechanism.
