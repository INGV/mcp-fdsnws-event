#!/usr/bin/env python3
"""FDSNWS Event MCP Server: earthquake data from any FDSN-compliant datacenter."""

import json
import logging
from datetime import date
from typing import Annotated, Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from . import config, tables
from .models import (
    EventIdInput,
    GetAllMagnitudesByEventIdInput,
    GetAllOriginsByEventIdInput,
    GetAmplitudesByEventIdInput,
    GetArrivalsByEventIdInput,
    GetEarthquakeByEventIdInput,
    GetFocalMechanismByEventIdInput,
    GetStationMagnitudesByEventIdInput,
    QueryEarthquakesInput,
)
from .obspy_client import (
    DatacenterError,
    _extract_event_id,
    _items_with_preferred,
    get_allmagnitudes_by_eventid,
    get_allorigins_by_eventid,
    get_amplitudes_by_eventid,
    get_arrivals_by_eventid,
    get_event_by_eventid,
    get_focalmechanism_by_eventid,
    get_stationmagnitudes_by_eventid,
    obspy_to_dict,
    query_events_text,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("fdsn_event_mcp")

_QUERY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

_DATACENTER_NOTE = (
    "Available datacenters: INGV (default), EMSC, GFZ, USGS, and others supported by ObsPy."
)

_EVENTID_NOTE = (
    "The eventid is an opaque, provider-specific string and MUST be copied verbatim "
    "from a prior fdsn_query_earthquakes result (event_id column). Never invent, guess, "
    "reformat, or use placeholder values.\n\n"
)

# FastMCP derives each tool's JSON schema from the *function signature*, not from
# the Pydantic input model validated inside the body. A bare `limit: int = 150`
# therefore publishes no maximum at all, and the model -- told nothing -- asks
# for 1000 and gets a ValueError it cannot learn from. These annotated aliases
# put the bounds where tools/list can see them, so the limit is advertised and
# refused by the protocol layer rather than discovered by trial.
Offset = Annotated[
    int,
    Field(
        ge=1,
        description=(
            "1-based index of the first row to return (default: 1). Page with the "
            "next_offset the previous response suggests"
        ),
    ),
]


def _limit_field(default: int, maximum: int, what: str):
    """An advertised, bounded `limit` for a table-returning tool."""
    return Annotated[
        int,
        Field(
            ge=1,
            le=maximum,
            description=(
                f"Maximum number of {what} to return (default: {default}, "
                f"max: {maximum}). The maximum is set so a full page fits the "
                "model's context window"
            ),
        ),
    ]


ArrivalsLimit = _limit_field(
    config.DEFAULT_ROWS_ARRIVALS, config.MAX_ROWS_ARRIVALS, "arrival rows"
)
StationMagnitudesLimit = _limit_field(
    config.DEFAULT_ROWS_STATIONMAGNITUDES,
    config.MAX_ROWS_STATIONMAGNITUDES,
    "station magnitude rows",
)
AmplitudesLimit = _limit_field(
    config.DEFAULT_ROWS_AMPLITUDES, config.MAX_ROWS_AMPLITUDES, "amplitude rows"
)
EventsLimit = _limit_field(
    config.DEFAULT_ROWS_EVENTS, config.MAX_ROWS_EVENTS, "events"
)

StationFilter = Annotated[
    Optional[str],
    Field(
        default=None,
        max_length=8,
        description=(
            "Filter rows by station code (exact, case-insensitive), e.g. 'SGRT'. "
            "Applied after the fetch"
        ),
    ),
]

NetworkFilter = Annotated[
    Optional[str],
    Field(
        default=None,
        max_length=8,
        description=(
            "Filter rows by network code (exact, case-insensitive), e.g. 'IV'. "
            "Applied after the fetch"
        ),
    ),
]

_TABLE_NOTE = (
    "Returns a table: 'columns' names the fields once and 'rows' carries one list "
    "of values per record, in the same order. Identifiers are split between "
    "'id_prefixes' and the rows: the full value is the prefix for that column "
    "concatenated with the value in the row. Page with 'limit' and 'offset' while "
    "'has_more' is true, using the 'next_offset' the response suggests.\n\n"
)


_TRUNCATION_NOTE = "Result truncated at limit. Fetch next_offset or narrow the filters."


def _error_payload(e: DatacenterError) -> str:
    """Render an upstream datacenter failure as a structured JSON error result."""
    return json.dumps(
        {"error": True, "datacenter": e.datacenter, "api_url": e.api_url, "message": e.message},
        indent=2,
    )


def _not_found_payload(datacenter: str, api_url: str, eventid: str, **empty_fields) -> str:
    """Render the 'event not found' state (empty catalog) with an actionable message.

    ``empty_fields`` carries the tool-specific empty result keys (e.g.
    ``arrivals_count=0, rows=[]``) so the response shape stays stable.
    """
    return json.dumps(
        {
            "datacenter": datacenter,
            "api_url": api_url,
            "found": False,
            "event_id": None,
            "message": (
                f"No event with eventid={eventid} found at {datacenter}. "
                "The eventid must come from a fdsn_query_earthquakes result "
                "(event_id column); verify it and retry."
            ),
            **empty_fields,
        },
        indent=2,
    )


def _absent_resource_message(event_id, datacenter: str, resource: str) -> str:
    """Message for the 'event found but the requested sub-resource is absent' state."""
    return f"Event {event_id} exists at {datacenter} but has no {resource}."


def _guarded_dump(payload: dict) -> str:
    """Serialize a non-tabular result, refusing one that cannot fit the budget.

    A table can always be trimmed to fit by returning fewer rows, so it is never
    refused. A solution-level object has no such dial: an event with fifty
    alternative origins is one indivisible answer. Handing it over anyway is the
    failure this whole release exists to prevent -- an oversized tool result
    pushes the user's own question out of the model's context window and the
    turn dies with an error that names neither cause. Better to say so.
    """
    body = json.dumps(payload, indent=2, default=str)
    if len(body) <= config.MAX_RESULT_BYTES:
        return body
    return json.dumps(
        {
            "error": True,
            "datacenter": payload.get("datacenter"),
            "api_url": payload.get("api_url"),
            "message": (
                f"Result is {len(body)} bytes, over the {config.MAX_RESULT_BYTES} byte "
                "limit, and would not fit the model's context window. This event "
                "carries an unusually large solution set; query a narrower "
                "sub-resource with one of the table tools "
                "(fdsn_get_arrivals_by_eventid, fdsn_get_stationmagnitudes_by_eventid, "
                "fdsn_get_amplitudes_by_eventid), or raise FDSN_MAX_RESULT_BYTES."
            ),
        },
        indent=2,
    )


def _filter_station_rows(
    columns: tuple[str, ...],
    rows: list[list[Any]],
    network: Optional[str],
    station: Optional[str],
    network_column: str = "network",
    station_column: str = "station",
) -> list[list[Any]]:
    """Keep the rows matching the network and station filters, if any."""
    if not network and not station:
        return rows
    net_i = columns.index(network_column)
    sta_i = columns.index(station_column)
    wanted_net = network.upper() if network else None
    wanted_sta = station.upper() if station else None

    def matches(row):
        if wanted_net and (row[net_i] or "").upper() != wanted_net:
            return False
        if wanted_sta and (row[sta_i] or "").upper() != wanted_sta:
            return False
        return True

    return [row for row in rows if matches(row)]


def _table_response(
    *,
    params,
    api_url: str,
    event,
    columns: tuple[str, ...],
    rows: list[list[Any]],
    ordered_by: str,
    count_key: str,
    total_count: int,
    resource_label: str,
    network_column: str = "network",
    station_column: str = "station",
) -> str:
    """Assemble the common envelope shared by every table-returning tool.

    The order of operations is load-bearing. Filtering runs before ordering so a
    filtered page is still sorted; prefixes are computed over the whole filtered
    set before pagination, so a prefix stays valid for every page and does not
    shift as the caller walks through them; and the prefix map is in the
    envelope before ``paginate`` measures it, because the budget has to cover
    what is actually sent.
    """
    rows = _filter_station_rows(
        columns, rows,
        getattr(params, "network", None), getattr(params, "station", None),
        network_column=network_column, station_column=station_column,
    )
    rows, prefixes = tables.strip_id_prefixes(columns, rows)
    event_id = _extract_event_id(event)

    # Everything the response will carry except the page itself, so the guard
    # measures what is actually sent. A key added after `paginate` has approved a
    # page -- as `<resource>_count` used to be -- silently widens the result past
    # the ceiling the guard just certified.
    envelope = {
        "datacenter": params.datacenter,
        "api_url": api_url,
        "found": True,
        "event_id": event_id,
        "origins_count": len(event.origins),
        "ordered_by": ordered_by,
        "id_prefixes": prefixes,
        count_key: total_count,
    }

    try:
        payload = tables.paginate(
            envelope,
            columns,
            rows,
            limit=params.limit,
            offset=params.offset,
            max_bytes=config.MAX_RESULT_BYTES,
        )
    except tables.RowTooLargeError as e:
        return json.dumps(
            {
                "error": True,
                "datacenter": params.datacenter,
                "api_url": api_url,
                "message": str(e),
            },
            indent=2,
        )

    # `count_key` is already in the envelope: the event's own count for this
    # collection, untouched by any filter, answering "does this event have
    # station magnitudes at all" whether or not the caller narrowed to one
    # station. `total_count` from paginate answers the different question, "how
    # many rows match this query".
    #
    # Test the page, not the filtered set: an offset past the end yields an
    # empty page out of a perfectly full result, and that deserves its own
    # explanation rather than silence.
    if not payload["rows"]:
        # "No rows" has two causes that must not be conflated. If the event has
        # none of this resource at all, that is the plain absence state and
        # the caller should stop asking. If it has some and the filters matched
        # none, saying "this event has no station magnitudes" would be a plain
        # falsehood about an event carrying 677 of them, and would send the
        # caller away from data that is right there.
        if total_count:
            applied = {
                name: value
                for name, value in (
                    ("network", getattr(params, "network", None)),
                    ("station", getattr(params, "station", None)),
                    ("magnitude_type", getattr(params, "magnitude_type", None)),
                )
                if value
            }
            if not rows:
                payload["message"] = (
                    f"Event {event_id} has {total_count} {resource_label} at "
                    f"{params.datacenter}, but none match the filters applied "
                    f"({applied}). Relax or drop them."
                )
            else:
                payload["message"] = (
                    f"offset {params.offset} is past the end of this result, "
                    f"which has {len(rows)} rows. Start again from offset 1."
                )
        else:
            payload["message"] = _absent_resource_message(
                event_id, params.datacenter, resource_label
            )
    return json.dumps(payload, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Event query
# ---------------------------------------------------------------------------


@mcp.tool(
    name="fdsn_query_earthquakes",
    description=(
        "Query earthquake events from an FDSN datacenter with flexible parameters.\n\n"
        "Returns one row per event, using the preferred origin and magnitude. "
        "Depth is in KILOMETERS. For the detail of a single event (all origins or "
        "magnitudes, arrivals, station magnitudes, amplitudes, focal mechanism) use "
        "the by-eventid tools.\n\n"
        + _TABLE_NOTE
        + "Common usage examples:\n"
        '- Recent events (today): {} (no parameters needed)\n'
        '- Significant events: {"minmag": 4.0, "starttime": "YYYY-MM-DDTHH:MM:SS"}\n'
        '- Geographic area: {"minlat": 41.0, "maxlat": 43.0, "minlon": 12.0, "maxlon": 15.0}\n'
        '- Radial search: {"latitude": 41.9, "longitude": 12.5, "maxradiuskm": 50}\n'
        '- Strongest first: {"orderby": "magnitude", "limit": 10}\n'
        '- Next page: {"offset": 101} (offset + returned_count from the previous call)\n\n'
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_query_earthquakes(
    starttime: Optional[str] = None,
    endtime: Optional[str] = None,
    updatedafter: Optional[str] = None,
    minmag: Optional[float] = None,
    maxmag: Optional[float] = None,
    minlat: Optional[float] = None,
    maxlat: Optional[float] = None,
    minlon: Optional[float] = None,
    maxlon: Optional[float] = None,
    mindepth: Optional[float] = None,
    maxdepth: Optional[float] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    minradiuskm: Optional[float] = None,
    maxradiuskm: Optional[float] = None,
    limit: EventsLimit = config.DEFAULT_ROWS_EVENTS,
    offset: Offset = 1,
    orderby: str = "time",
    datacenter: str = "INGV",
) -> str:
    try:
        params = QueryEarthquakesInput(
            starttime=starttime, endtime=endtime, updatedafter=updatedafter,
            minmag=minmag, maxmag=maxmag,
            minlat=minlat, maxlat=maxlat, minlon=minlon, maxlon=maxlon,
            mindepth=mindepth, maxdepth=maxdepth,
            latitude=latitude, longitude=longitude,
            minradiuskm=minradiuskm, maxradiuskm=maxradiuskm,
            limit=limit, offset=offset, orderby=orderby, datacenter=datacenter,
        )
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    today = date.today()
    start = params.starttime or f"{today.isoformat()}T00:00:00"
    end = params.endtime or f"{today.isoformat()}T23:59:59"

    logger.info(
        "Querying earthquakes: %s to %s, minmag=%s, limit=%s, offset=%s, orderby=%s, datacenter=%s",
        start, end, params.minmag, params.limit, params.offset, params.orderby, params.datacenter,
    )

    try:
        columns, rows, api_url = await query_events_text(
            starttime=start, endtime=end,
            minmag=params.minmag, maxmag=params.maxmag,
            minlat=params.minlat, maxlat=params.maxlat,
            minlon=params.minlon, maxlon=params.maxlon,
            mindepth=params.mindepth, maxdepth=params.maxdepth,
            latitude=params.latitude, longitude=params.longitude,
            minradiuskm=params.minradiuskm, maxradiuskm=params.maxradiuskm,
            limit=params.limit, offset=params.offset, orderby=params.orderby,
            updatedafter=params.updatedafter, datacenter=params.datacenter,
        )
    except DatacenterError as e:
        return _error_payload(e)

    columns = tables.normalize_event_columns(columns)

    returned_count = len(rows)
    # No total is available: FDSN text carries no count, so "there may be more"
    # is inferred from having filled the page exactly.
    has_more = returned_count >= params.limit

    query_echo = {
        k: v for k, v in {
            "starttime": start, "endtime": end, "updatedafter": params.updatedafter,
            "minmag": params.minmag, "maxmag": params.maxmag,
            "minlat": params.minlat, "maxlat": params.maxlat,
            "minlon": params.minlon, "maxlon": params.maxlon,
            "mindepth": params.mindepth, "maxdepth": params.maxdepth,
            "latitude": params.latitude, "longitude": params.longitude,
            "minradiuskm": params.minradiuskm, "maxradiuskm": params.maxradiuskm,
            "limit": params.limit, "offset": params.offset, "orderby": params.orderby,
        }.items() if v is not None
    }

    envelope = {
        "datacenter": params.datacenter,
        "api_url": api_url,
        "query": query_echo,
        "ordered_by": params.orderby,
        "id_prefixes": {},
    }
    # The same byte budget every other table honours. This page was already cut
    # upstream by the datacenter's own limit, so the trim normally does nothing;
    # it catches the page whose rows are wider than the maximum was set for, for
    # instance a run of events with very long location names.
    #
    # Measured against everything the response will carry, the pagination keys
    # and the truncation note included. Trimming against the bare envelope let
    # the emitted JSON run past the ceiling by their width, which makes the
    # budget an estimate rather than the guarantee it is documented to be.
    accounted = {
        **envelope,
        "returned_count": returned_count,
        "limit": params.limit,
        "offset": params.offset,
        "has_more": True,
        "next_offset": params.offset + returned_count,
        "note": _TRUNCATION_NOTE,
    }
    try:
        rows = tables.trim_to_budget(
            accounted, tuple(columns), rows, config.MAX_RESULT_BYTES
        )
    except tables.RowTooLargeError as e:
        return json.dumps(
            {
                "error": True,
                "datacenter": params.datacenter,
                "api_url": api_url,
                "message": str(e),
            },
            indent=2,
        )
    if len(rows) < returned_count:
        returned_count = len(rows)
        has_more = True

    payload = {
        **envelope,
        "returned_count": returned_count,
        "limit": params.limit,
        "offset": params.offset,
        "has_more": has_more,
        "columns": columns,
        "rows": rows,
    }
    if has_more:
        payload["next_offset"] = params.offset + returned_count
        payload["note"] = _TRUNCATION_NOTE

    return json.dumps(payload, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Solution-level detail, full QuakeML
# ---------------------------------------------------------------------------


@mcp.tool(
    name="fdsn_get_earthquake_by_eventid",
    description=(
        "Get the core information about a specific earthquake event by event ID.\n\n"
        "Returns the event metadata, the preferred origin, the preferred magnitude "
        "and the focal mechanisms, as full QuakeML detail. Note that the preferred "
        "magnitude does not always belong to the preferred origin; both identifiers "
        "are reported.\n\n"
        "Station-level collections are NOT included, because a single event can carry "
        "thousands of them. Use fdsn_get_arrivals_by_eventid, "
        "fdsn_get_stationmagnitudes_by_eventid and fdsn_get_amplitudes_by_eventid; "
        "the station magnitude and amplitude counts are reported here so you know "
        "whether there is anything to fetch; for arrivals, ask the arrivals tool.\n\n"
        + _EVENTID_NOTE
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_earthquake_by_eventid(
    eventid: EventIdInput, datacenter: str = "INGV"
) -> str:
    try:
        params = GetEarthquakeByEventIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_event_by_eventid(
            eventid=params.eventid, datacenter=params.datacenter
        )
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return _not_found_payload(params.datacenter, api_url, params.eventid, event=None)

    event = catalog[0]
    # Everything except the station-level collections. Dropping them here is the
    # single biggest saving in this release: with them, one INGV Mw 6.1 event
    # serialized to 3510 kB, of which 1886 kB was amplitudes alone.
    event_data = {
        "event_id": _extract_event_id(event),
        "resource_id": str(event.resource_id),
        "event_type": str(event.event_type) if event.event_type else None,
        "preferred_origin_id": str(event.preferred_origin_id)
        if event.preferred_origin_id
        else None,
        "preferred_magnitude_id": str(event.preferred_magnitude_id)
        if event.preferred_magnitude_id
        else None,
        "preferred_focal_mechanism_id": str(event.preferred_focal_mechanism_id)
        if event.preferred_focal_mechanism_id
        else None,
        "event_descriptions": obspy_to_dict(event.event_descriptions),
        "comments": obspy_to_dict(event.comments),
        "creation_info": obspy_to_dict(event.creation_info),
        "preferred_origin": obspy_to_dict(event.preferred_origin()),
        "preferred_magnitude": obspy_to_dict(event.preferred_magnitude()),
        "focal_mechanisms": obspy_to_dict(event.focal_mechanisms),
        "origins_count": len(event.origins),
        "magnitudes_count": len(event.magnitudes),
        # No arrivals_count here on purpose. This fetch sends no includearrivals,
        # so every origin comes back with an empty arrival list and any count
        # taken from it would read as a confident "this event has no phases" --
        # worse than saying nothing, because it would stop the caller from
        # asking the tool that does know.
        "station_magnitudes_count": len(event.station_magnitudes),
        "amplitudes_count": len(event.amplitudes),
    }
    return _guarded_dump(
        {
            "datacenter": params.datacenter,
            "api_url": api_url,
            "found": True,
            "event": event_data,
        }
    )


@mcp.tool(
    name="fdsn_get_allorigins_by_eventid",
    description=(
        "Get all computed origin (hypocentre) solutions for an earthquake event. "
        "Use this when asked about alternative locations, revisions, or which agency "
        "located the event.\n\n"
        + _EVENTID_NOTE
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_allorigins_by_eventid(
    eventid: EventIdInput, datacenter: str = "INGV"
) -> str:
    try:
        params = GetAllOriginsByEventIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting all origins for earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_allorigins_by_eventid(
            eventid=params.eventid, datacenter=params.datacenter
        )
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return _not_found_payload(
            params.datacenter, api_url, params.eventid, origins_count=0, origins=[]
        )

    event = catalog[0]
    event_id = _extract_event_id(event)
    origins = _items_with_preferred(event.origins, event.preferred_origin_id)
    result = {
        "datacenter": params.datacenter, "api_url": api_url,
        "found": True, "event_id": event_id,
        "origins_count": len(origins), "origins": origins,
    }
    if len(origins) == 0:
        result["message"] = _absent_resource_message(
            event_id, params.datacenter, "origin solutions"
        )
    return _guarded_dump(result)


@mcp.tool(
    name="fdsn_get_allmagnitudes_by_eventid",
    description=(
        "Get all computed magnitude solutions for an earthquake event. "
        "Use this when asked about different magnitude types (ML, Mw, Mb, Md), "
        "magnitude comparisons across agencies, or station counts. For the "
        "per-station readings behind a magnitude use "
        "fdsn_get_stationmagnitudes_by_eventid.\n\n"
        + _EVENTID_NOTE
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_allmagnitudes_by_eventid(
    eventid: EventIdInput, datacenter: str = "INGV"
) -> str:
    try:
        params = GetAllMagnitudesByEventIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info(
        "Getting all magnitudes for earthquake %s from %s", params.eventid, params.datacenter
    )

    try:
        catalog, api_url = await get_allmagnitudes_by_eventid(
            eventid=params.eventid, datacenter=params.datacenter
        )
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return _not_found_payload(
            params.datacenter, api_url, params.eventid, magnitudes_count=0, magnitudes=[]
        )

    event = catalog[0]
    event_id = _extract_event_id(event)
    magnitudes = _items_with_preferred(event.magnitudes, event.preferred_magnitude_id)
    result = {
        "datacenter": params.datacenter, "api_url": api_url,
        "found": True, "event_id": event_id,
        "magnitudes_count": len(magnitudes), "magnitudes": magnitudes,
    }
    if len(magnitudes) == 0:
        result["message"] = _absent_resource_message(
            event_id, params.datacenter, "magnitude solutions"
        )
    return _guarded_dump(result)


@mcp.tool(
    name="fdsn_get_focalmechanism_by_eventid",
    description=(
        "Get the focal mechanisms of an earthquake event: nodal planes (strike, dip, "
        "rake), principal axes (T, P, N) and moment tensor components.\n\n"
        + _EVENTID_NOTE
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_focalmechanism_by_eventid(
    eventid: EventIdInput, datacenter: str = "INGV"
) -> str:
    try:
        params = GetFocalMechanismByEventIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info(
        "Getting focal mechanism for earthquake %s from %s", params.eventid, params.datacenter
    )

    try:
        catalog, api_url = await get_focalmechanism_by_eventid(
            eventid=params.eventid, datacenter=params.datacenter
        )
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return _not_found_payload(
            params.datacenter, api_url, params.eventid,
            focal_mechanisms_count=0, focal_mechanisms=[],
        )

    event = catalog[0]
    event_id = _extract_event_id(event)
    focal_mechanisms = _items_with_preferred(
        event.focal_mechanisms, event.preferred_focal_mechanism_id,
    )
    result = {
        "datacenter": params.datacenter, "api_url": api_url,
        "found": True, "event_id": event_id,
        "focal_mechanisms_count": len(focal_mechanisms), "focal_mechanisms": focal_mechanisms,
    }
    if len(focal_mechanisms) == 0:
        result["message"] = _absent_resource_message(
            event_id, params.datacenter, "focal mechanism solutions"
        )
    return _guarded_dump(result)


# ---------------------------------------------------------------------------
# Station-level detail, tabular
# ---------------------------------------------------------------------------


@mcp.tool(
    name="fdsn_get_arrivals_by_eventid",
    description=(
        "Get the seismic phase arrivals of an earthquake event, each joined with the "
        "pick it associates (station, time, phase, residual). Use this when asked "
        "about recorded phases, station readings, or seismic wave arrivals.\n\n"
        "Rows cover every origin the datacenter returned, not only the preferred one; "
        "'is_preferred_origin' marks the rows of the preferred solution and "
        "'origin_id' identifies the others. Rows are ordered by distance from the "
        "hypocentre when the datacenter provides it, otherwise by pick time.\n\n"
        + _TABLE_NOTE
        + _EVENTID_NOTE
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_arrivals_by_eventid(
    eventid: EventIdInput,
    datacenter: str = "INGV",
    network: NetworkFilter = None,
    station: StationFilter = None,
    limit: ArrivalsLimit = config.DEFAULT_ROWS_ARRIVALS,
    offset: Offset = 1,
) -> str:
    try:
        params = GetArrivalsByEventIdInput(
            eventid=eventid, datacenter=datacenter,
            network=network, station=station, limit=limit, offset=offset,
        )
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting arrivals for earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_arrivals_by_eventid(
            eventid=params.eventid, datacenter=params.datacenter
        )
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return _not_found_payload(
            params.datacenter, api_url, params.eventid,
            arrivals_count=0, columns=list(tables.ARRIVAL_COLUMNS), rows=[],
        )

    event = catalog[0]
    rows, ordered_by = tables.order_arrival_rows(tables.build_arrival_rows(event))
    return _table_response(
        params=params, api_url=api_url, event=event,
        columns=tables.ARRIVAL_COLUMNS, rows=rows, ordered_by=ordered_by,
        count_key="arrivals_count",
        total_count=sum(len(o.arrivals) for o in event.origins),
        resource_label="phase arrivals",
        # An arrival has no station of its own: the station is the pick's, and
        # the columns are named for where the value comes from.
        network_column="pick_network",
        station_column="pick_station",
    )


@mcp.tool(
    name="fdsn_get_stationmagnitudes_by_eventid",
    description=(
        "Get the per-station magnitude readings of an earthquake event, each joined "
        "with the amplitude it was computed from. Use this when asked which stations "
        "contributed to a magnitude, or for the magnitude measured at one station.\n\n"
        "A station magnitude belongs to an origin, given by 'origin_id'; where an "
        "origin carries more than one magnitude, 'station_magnitude_type' tells the "
        "readings apart and can be filtered on. Not every datacenter publishes these: "
        "an empty result with a message means the event exists but carries none.\n\n"
        + _TABLE_NOTE
        + _EVENTID_NOTE
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_stationmagnitudes_by_eventid(
    eventid: EventIdInput,
    datacenter: str = "INGV",
    network: NetworkFilter = None,
    station: StationFilter = None,
    magnitude_type: Annotated[Optional[str], Field(default=None, max_length=16, description="Filter rows by station magnitude type (exact, case-insensitive), e.g. 'ML'. Useful where an origin carries more than one magnitude")] = None,
    limit: StationMagnitudesLimit = config.DEFAULT_ROWS_STATIONMAGNITUDES,
    offset: Offset = 1,
) -> str:
    try:
        params = GetStationMagnitudesByEventIdInput(
            eventid=eventid, datacenter=datacenter,
            network=network, station=station, magnitude_type=magnitude_type,
            limit=limit, offset=offset,
        )
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info(
        "Getting station magnitudes for earthquake %s from %s",
        params.eventid, params.datacenter,
    )

    try:
        catalog, api_url = await get_stationmagnitudes_by_eventid(
            eventid=params.eventid, datacenter=params.datacenter
        )
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return _not_found_payload(
            params.datacenter, api_url, params.eventid,
            station_magnitudes_count=0,
            columns=list(tables.STATIONMAGNITUDE_COLUMNS), rows=[],
        )

    event = catalog[0]
    rows = tables.build_station_magnitude_rows(event)
    if params.magnitude_type:
        type_i = tables.STATIONMAGNITUDE_COLUMNS.index("station_magnitude_type")
        wanted = params.magnitude_type.upper()
        rows = [r for r in rows if (r[type_i] or "").upper() == wanted]
    rows, ordered_by = tables.order_station_rows(
        rows, tables.STATIONMAGNITUDE_COLUMNS, "station_magnitude_id"
    )
    return _table_response(
        params=params, api_url=api_url, event=event,
        columns=tables.STATIONMAGNITUDE_COLUMNS, rows=rows, ordered_by=ordered_by,
        count_key="station_magnitudes_count",
        total_count=len(event.station_magnitudes),
        resource_label="station magnitudes",
    )


@mcp.tool(
    name="fdsn_get_amplitudes_by_eventid",
    description=(
        "Get the measured amplitudes of an earthquake event (value, unit, period, "
        "time window, station). Use this when asked what amplitudes were recorded, or "
        "for the amplitude measured at one station.\n\n"
        "In QuakeML an amplitude belongs to the event, not to an origin, so all of "
        "them are returned; many are not referenced by any station magnitude. Not "
        "every datacenter publishes them: an empty result with a message means the "
        "event exists but carries none.\n\n"
        + _TABLE_NOTE
        + _EVENTID_NOTE
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_amplitudes_by_eventid(
    eventid: EventIdInput,
    datacenter: str = "INGV",
    network: NetworkFilter = None,
    station: StationFilter = None,
    limit: AmplitudesLimit = config.DEFAULT_ROWS_AMPLITUDES,
    offset: Offset = 1,
) -> str:
    try:
        params = GetAmplitudesByEventIdInput(
            eventid=eventid, datacenter=datacenter,
            network=network, station=station, limit=limit, offset=offset,
        )
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting amplitudes for earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_amplitudes_by_eventid(
            eventid=params.eventid, datacenter=params.datacenter
        )
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return _not_found_payload(
            params.datacenter, api_url, params.eventid,
            amplitudes_count=0, columns=list(tables.AMPLITUDE_COLUMNS), rows=[],
        )

    event = catalog[0]
    rows, ordered_by = tables.order_station_rows(
        tables.build_amplitude_rows(event), tables.AMPLITUDE_COLUMNS, "amplitude_id"
    )
    return _table_response(
        params=params, api_url=api_url, event=event,
        columns=tables.AMPLITUDE_COLUMNS, rows=rows, ordered_by=ordered_by,
        count_key="amplitudes_count",
        total_count=len(event.amplitudes),
        resource_label="amplitudes",
    )


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
