#!/usr/bin/env python3
"""FDSNWS Event MCP Server: earthquake data from any FDSN-compliant datacenter."""

import json
import logging
from datetime import date
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from .models import (
    GetAllMagnitudesByIdInput,
    GetAllOriginsByIdInput,
    GetArrivalsByIdInput,
    GetEarthquakeByIdInput,
    GetFocalMechanismByIdInput,
    QueryEarthquakesInput,
)
from .obspy_client import (
    DatacenterError,
    query_events_text,
    get_event_by_id,
    get_arrivals_by_id,
    get_allmagnitudes_by_id,
    get_allorigins_by_id,
    get_focalmechanism_by_id,
    event_to_full_dict,
    obspy_to_dict,
    _extract_event_id,
    _items_with_preferred,
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
    "Available datacenters: INGV (default), IRIS, EMSC, GFZ, and others supported by ObsPy."
)


def _error_payload(e: DatacenterError) -> str:
    """Render an upstream datacenter failure as a structured JSON error result."""
    return json.dumps(
        {"error": True, "datacenter": e.datacenter, "api_url": e.api_url, "message": e.message},
        indent=2,
    )


@mcp.tool(
    name="fdsn_query_earthquakes",
    description=(
        "Query earthquake events from an FDSN datacenter with flexible parameters.\n\n"
        "Returns a compact tabular result (columns + rows), one row per event, using "
        "the preferred origin and magnitude. Depth is in KILOMETERS. For the full detail "
        "of a single event (all origins/magnitudes, arrivals, focal mechanism) use the "
        "by-id tools.\n\n"
        "Common usage examples:\n"
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
    limit: int = 100,
    offset: int = 1,
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

    returned_count = len(rows)
    has_more = returned_count >= params.limit
    pagination: dict = {
        "returned_count": returned_count,
        "limit": params.limit,
        "offset": params.offset,
        "has_more": has_more,
    }
    if has_more:
        pagination["next_offset"] = params.offset + returned_count
        pagination["note"] = "Result truncated at limit. Fetch next_offset or narrow the filters."

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

    return json.dumps(
        {
            "datacenter": params.datacenter,
            "query": query_echo,
            "api_url": api_url,
            "pagination": pagination,
            "columns": columns,
            "rows": rows,
        },
        separators=(",", ":"),
    )


@mcp.tool(
    name="fdsn_get_earthquake_by_id",
    description=(
        "Get basic information about a specific earthquake event by event ID.\n\n"
        "Returns the preferred origin, preferred magnitude, station magnitudes, and amplitudes.\n"
        "For detailed data (all alternative origins, all alternative magnitudes, or seismic "
        "arrivals/picks), use the specialized tools instead.\n\n"
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_earthquake_by_id(eventid: int, datacenter: str = "INGV") -> str:
    try:
        params = GetEarthquakeByIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_event_by_id(eventid=params.eventid, datacenter=params.datacenter)
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return json.dumps(
            {"datacenter": params.datacenter, "api_url": api_url, "event": None}, indent=2
        )

    event_data = event_to_full_dict(catalog[0])
    return json.dumps(
        {"datacenter": params.datacenter, "api_url": api_url, "event": event_data},
        indent=2, default=str,
    )


@mcp.tool(
    name="fdsn_get_arrivals_by_id",
    description=(
        "Get all seismic phase arrivals for an earthquake event, including linked "
        "pick data (station, time, phase). Use this when asked about recorded phases, "
        "station readings, or seismic wave arrivals.\n\n"
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_arrivals_by_id(eventid: int, datacenter: str = "INGV") -> str:
    try:
        params = GetArrivalsByIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting arrivals for earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_arrivals_by_id(eventid=params.eventid, datacenter=params.datacenter)
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return json.dumps(
            {"datacenter": params.datacenter, "api_url": api_url, "event_id": None,
             "arrivals_count": 0, "arrivals": []}, indent=2,
        )

    event = catalog[0]
    origin = event.preferred_origin()
    arrivals = [obspy_to_dict(a) for a in origin.arrivals] if origin else []
    picks = [obspy_to_dict(p) for p in event.picks]

    return json.dumps(
        {
            "datacenter": params.datacenter, "api_url": api_url,
            "event_id": _extract_event_id(event),
            "arrivals_count": len(arrivals), "arrivals": arrivals, "picks": picks,
        },
        indent=2, default=str,
    )


@mcp.tool(
    name="fdsn_get_allmagnitudes_by_id",
    description=(
        "Get all computed magnitude solutions for an earthquake event. "
        "Use this when asked about different magnitude types (ML, Mw, Mb, Md), "
        "magnitude comparisons across agencies, or station counts.\n\n"
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_allmagnitudes_by_id(eventid: int, datacenter: str = "INGV") -> str:
    try:
        params = GetAllMagnitudesByIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting all magnitudes for earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_allmagnitudes_by_id(eventid=params.eventid, datacenter=params.datacenter)
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return json.dumps(
            {"datacenter": params.datacenter, "api_url": api_url, "event_id": None,
             "magnitudes_count": 0, "magnitudes": []}, indent=2,
        )

    event = catalog[0]
    magnitudes = _items_with_preferred(event.magnitudes, event.preferred_magnitude_id)
    return json.dumps(
        {
            "datacenter": params.datacenter, "api_url": api_url,
            "event_id": _extract_event_id(event),
            "magnitudes_count": len(magnitudes), "magnitudes": magnitudes,
        },
        indent=2, default=str,
    )


@mcp.tool(
    name="fdsn_get_allorigins_by_id",
    description=(
        "Get all computed origin solutions (hypocenter locations) for an earthquake event. "
        "Use this when asked about alternative locations, origin comparisons, or which "
        "agencies computed origins.\n\n"
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_allorigins_by_id(eventid: int, datacenter: str = "INGV") -> str:
    try:
        params = GetAllOriginsByIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting all origins for earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_allorigins_by_id(eventid=params.eventid, datacenter=params.datacenter)
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return json.dumps(
            {"datacenter": params.datacenter, "api_url": api_url, "event_id": None,
             "origins_count": 0, "origins": []}, indent=2,
        )

    event = catalog[0]
    origins = _items_with_preferred(event.origins, event.preferred_origin_id)
    return json.dumps(
        {
            "datacenter": params.datacenter, "api_url": api_url,
            "event_id": _extract_event_id(event),
            "origins_count": len(origins), "origins": origins,
        },
        indent=2, default=str,
    )


@mcp.tool(
    name="fdsn_get_focalmechanism_by_id",
    description=(
        "Get focal mechanism and moment tensor data for an earthquake event. "
        "Returns nodal planes (strike, dip, rake), principal axes (T, P, N), "
        "and moment tensor components when available. Use this for understanding "
        "the rupture geometry and seismic source characteristics.\n\n"
        + _DATACENTER_NOTE
    ),
    annotations=_QUERY_ANNOTATIONS,
)
async def fdsn_get_focalmechanism_by_id(eventid: int, datacenter: str = "INGV") -> str:
    try:
        params = GetFocalMechanismByIdInput(eventid=eventid, datacenter=datacenter)
    except ValidationError as e:
        raise ValueError(f"Invalid parameters: {e}") from e

    logger.info("Getting focal mechanism for earthquake %s from %s", params.eventid, params.datacenter)

    try:
        catalog, api_url = await get_focalmechanism_by_id(eventid=params.eventid, datacenter=params.datacenter)
    except DatacenterError as e:
        return _error_payload(e)

    if len(catalog) == 0:
        return json.dumps(
            {"datacenter": params.datacenter, "api_url": api_url, "event_id": None,
             "focal_mechanisms_count": 0, "focal_mechanisms": []}, indent=2,
        )

    event = catalog[0]
    focal_mechanisms = _items_with_preferred(
        event.focal_mechanisms, event.preferred_focal_mechanism_id,
    )
    return json.dumps(
        {
            "datacenter": params.datacenter, "api_url": api_url,
            "event_id": _extract_event_id(event),
            "focal_mechanisms_count": len(focal_mechanisms), "focal_mechanisms": focal_mechanisms,
        },
        indent=2, default=str,
    )


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
