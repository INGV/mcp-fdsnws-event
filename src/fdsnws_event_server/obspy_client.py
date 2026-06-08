"""FDSN data access: summary queries via the text format, details via ObsPy QuakeML.

The summary query path (``query_events_text``) talks to the datacenter directly with
``format=text`` and parses the pipe-delimited response, to keep responses small and to
pass parameters (e.g. ``offset``) straight through without ObsPy's WADL validation.
The by-id paths use ObsPy to retrieve and serialize full QuakeML. The server is
datacenter-agnostic: no datacenter-specific behaviour lives here (see docs/adr).
"""

import asyncio
import logging
import os
import re
from enum import Enum
from typing import Optional
from urllib.parse import urlencode

import requests
from obspy import Catalog, UTCDateTime
from obspy.clients.fdsn import Client
from obspy.clients.fdsn.header import FDSNException, FDSNNoDataException, URL_MAPPINGS
from obspy.core.event import ResourceIdentifier

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45.0


class DatacenterError(Exception):
    """An upstream failure from an FDSN datacenter (HTTP >= 400 or network error).

    Carries the datacenter's own message verbatim so it can be surfaced to the caller.
    """

    def __init__(self, message: str, *, status: Optional[int] = None,
                 datacenter: Optional[str] = None, api_url: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.datacenter = datacenter
        self.api_url = api_url


def _get_timeout() -> float:
    """Request timeout in seconds, from the FDSN_TIMEOUT env var (default 45)."""
    raw = os.environ.get("FDSN_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid FDSN_TIMEOUT=%r, using default %s", raw, DEFAULT_TIMEOUT)
        return DEFAULT_TIMEOUT


def _validate_datacenter(datacenter: str) -> None:
    """Raise ValueError if the datacenter name is not known to ObsPy."""
    valid = sorted(URL_MAPPINGS.keys())
    if datacenter.upper() not in (dc.upper() for dc in valid):
        raise ValueError(
            f"Unknown datacenter '{datacenter}'. Valid datacenters: {', '.join(valid)}"
        )


def _get_client(datacenter: str) -> Client:
    """Create an ObsPy FDSN Client for the given datacenter (used by the QuakeML paths)."""
    _validate_datacenter(datacenter)
    return Client(datacenter, timeout=_get_timeout())


def _event_query_url(datacenter: str) -> str:
    """Resolve the FDSN event query endpoint for a datacenter from URL_MAPPINGS.

    No network round-trip and no ObsPy service discovery: URL_MAPPINGS is a static map.
    """
    _validate_datacenter(datacenter)
    key = next(k for k in URL_MAPPINGS if k.upper() == datacenter.upper())
    base = URL_MAPPINGS[key].rstrip("/")
    return f"{base}/fdsnws/event/1/query"


def parse_fdsn_text(raw: str) -> tuple[list[str], list[list[str]]]:
    """Parse an FDSN ``format=text`` response into (columns, rows).

    Columns come from the ``#``-prefixed header line (not positional), so different
    datacenters' column sets are handled. Returns empty lists for empty input.
    """
    columns: list[str] = []
    rows: list[list[str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            if not columns:
                columns = line[1:].split("|")
            continue
        rows.append(line.split("|"))
    return columns, rows


async def query_events_text(
    starttime: Optional[str] = None,
    endtime: Optional[str] = None,
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
    updatedafter: Optional[str] = None,
    datacenter: str = "INGV",
) -> tuple[list[str], list[list[str]], str]:
    """Query events as FDSN text and return (columns, rows, api_url).

    Raises DatacenterError on HTTP >= 400 (carrying the datacenter's verbatim body)
    or on a network failure. HTTP 204/404 (no data) returns empty columns/rows.
    """
    endpoint = _event_query_url(datacenter)

    params: dict = {"format": "text", "limit": limit, "orderby": orderby}
    # Omit offset at its default (1): omitting is equivalent to the FDSN spec default
    # and avoids INGV's off-by-one dropping the first event on the common single-page
    # query. Explicit offset > 1 is passed straight through (see docs/adr/0003).
    if offset and offset > 1:
        params["offset"] = offset
    optional = {
        "starttime": starttime, "endtime": endtime, "updatedafter": updatedafter,
        "minmagnitude": minmag, "maxmagnitude": maxmag,
        "minlatitude": minlat, "maxlatitude": maxlat,
        "minlongitude": minlon, "maxlongitude": maxlon,
        "mindepth": mindepth, "maxdepth": maxdepth,
        "latitude": latitude, "longitude": longitude,
        "minradiuskm": minradiuskm, "maxradiuskm": maxradiuskm,
    }
    params.update({k: v for k, v in optional.items() if v is not None})

    api_url = f"{endpoint}?{urlencode(params)}"
    logger.info("Querying events (text): %s", api_url)

    try:
        resp = await asyncio.to_thread(requests.get, api_url, timeout=_get_timeout())
    except requests.RequestException as e:
        raise DatacenterError(
            f"Network error contacting datacenter '{datacenter}': {e}",
            datacenter=datacenter, api_url=api_url,
        ) from e

    if resp.status_code in (204, 404):
        return [], [], api_url
    if resp.status_code >= 400:
        raise DatacenterError(
            resp.text.strip() or f"HTTP {resp.status_code} from datacenter '{datacenter}'",
            status=resp.status_code, datacenter=datacenter, api_url=api_url,
        )
    return (*parse_fdsn_text(resp.text), api_url)


def obspy_to_dict(obj):
    """Recursively convert an ObsPy object tree to a JSON-serializable dict.

    Handles all ObsPy types: UTCDateTime, ResourceIdentifier, Enum, AttribDict,
    and nested objects. None values are omitted to reduce payload size.
    ResourceIdentifiers are serialized as strings to avoid circular references.
    All values remain in original QuakeML units (depth in meters, etc.).
    """
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, UTCDateTime):
        return str(obj)
    if isinstance(obj, ResourceIdentifier):
        return str(obj)
    if isinstance(obj, Enum):
        return str(obj)
    # numpy types (ObsPy may use numpy internally)
    type_name = type(obj).__module__
    if type_name == "numpy" or type_name.startswith("numpy."):
        if hasattr(obj, "item"):
            return obj.item()
        if hasattr(obj, "tolist"):
            return obj.tolist()
    if isinstance(obj, (list, tuple)):
        result = [obspy_to_dict(item) for item in obj]
        return result
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            converted = obspy_to_dict(value)
            if converted is not None:
                result[str(key)] = converted
        return result or None
    if hasattr(obj, "__dict__"):
        result = {}
        for key, value in vars(obj).items():
            if key.startswith("_"):
                continue
            converted = obspy_to_dict(value)
            if converted is not None:
                result[key] = converted
        return result or None
    return str(obj)


def event_to_full_dict(event) -> dict:
    """Convert an ObsPy Event to a full recursive dict with convenience fields."""
    d = obspy_to_dict(event) or {}
    d["event_id"] = _extract_event_id(event)
    return d


def _extract_event_id(event) -> str:
    """Extract numeric event ID from an ObsPy Event resource_id."""
    rid = str(event.resource_id)
    # resource_id may be "smi:...fdsnws/event/1/query?eventId=12345"
    m = re.search(r"eventId=(\d+)", rid)
    return m.group(1) if m else rid.split("/")[-1]


async def _get_events_quakeml(eventid: int, datacenter: str, **extra) -> tuple[Catalog, str]:
    """Shared QuakeML fetch by event id for the detail tools.

    Raises DatacenterError on upstream HTTP/network failure; HTTP 204 returns an
    empty Catalog. ``extra`` carries include flags (includearrivals, etc.).
    """
    fdsn_client = _get_client(datacenter)
    endpoint = _event_query_url(datacenter)
    kwargs = {"eventid": eventid, **extra}
    api_url = f"{endpoint}?{urlencode(kwargs)}"
    logger.info("Fetching event (QuakeML): %s", api_url)

    try:
        catalog = await asyncio.to_thread(fdsn_client.get_events, **kwargs)
    except FDSNNoDataException:
        logger.info("No data for eventid=%s (HTTP 204)", eventid)
        return (Catalog(), api_url)
    except FDSNException as e:
        raise DatacenterError(str(e), datacenter=datacenter, api_url=api_url) from e

    return (catalog, api_url)


async def get_event_by_id(eventid: int, datacenter: str = "INGV") -> tuple[Catalog, str]:
    """Fetch a single event by ID (basic info: preferred origin/magnitude, station
    magnitudes, amplitudes). Alternative origins/magnitudes/arrivals need the
    specialized tools."""
    return await _get_events_quakeml(eventid, datacenter)


async def get_arrivals_by_id(eventid: int, datacenter: str = "INGV") -> tuple[Catalog, str]:
    """Fetch a single event with all arrivals and picks."""
    return await _get_events_quakeml(eventid, datacenter, includearrivals=True)


async def get_allmagnitudes_by_id(eventid: int, datacenter: str = "INGV") -> tuple[Catalog, str]:
    """Fetch a single event with all magnitude solutions."""
    return await _get_events_quakeml(eventid, datacenter, includeallmagnitudes=True)


async def get_allorigins_by_id(eventid: int, datacenter: str = "INGV") -> tuple[Catalog, str]:
    """Fetch a single event with all origin solutions."""
    return await _get_events_quakeml(eventid, datacenter, includeallorigins=True)


async def get_focalmechanism_by_id(eventid: int, datacenter: str = "INGV") -> tuple[Catalog, str]:
    """Fetch a single event with focal mechanism data.

    Uses includeallmagnitudes=True because moment tensors are linked to magnitudes.
    """
    return await _get_events_quakeml(eventid, datacenter, includeallmagnitudes=True)


def _items_with_preferred(items, preferred_id) -> list[dict]:
    """Serialize a list of ObsPy objects and add is_preferred flag."""
    preferred_str = str(preferred_id) if preferred_id else None
    result = []
    for item in items:
        d = obspy_to_dict(item) or {}
        d["is_preferred"] = str(item.resource_id) == preferred_str
        result.append(d)
    return result
