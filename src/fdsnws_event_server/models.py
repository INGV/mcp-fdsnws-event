"""Pydantic input models for FDSNWS Event MCP tools."""

from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from . import config

# FDSN event identifiers are opaque, provider-defined strings, not numbers. Only
# some providers happen to use decimal digits:
#
#   INGV  37258271           IRIS/EarthScope  (event service retired, HTTP 410)
#   EMSC  20240101_0000328   GFZ  gfz2024abmz        USGS  us6000m0yg
#
# Typing this field as `int` was therefore wrong in two distinct ways: it rejected
# GFZ and USGS identifiers outright, and it *silently corrupted* EMSC's, because
# Python reads the underscore in "20240101_0000328" as a digit separator and yields
# 202401010000328 -- a different, non-existent event, which the datacenter then
# answers with HTTP 204 and the server reports as a confident "event not found".
_EVENTID_PATTERN = r"^[A-Za-z0-9_.:-]+$"


def _coerce_eventid(value: object) -> object:
    """Accept a JSON number for an event id and hand on its digits as a string.

    Kept for backward compatibility: the pre-1.4 schema advertised `eventid` as an
    integer, so existing clients (and models primed by that schema) still emit one.
    Pydantic v2 does not coerce int to str on its own, so without this the change
    from `int` to `str` would break every such caller.

    Only int is converted, never a str: re-parsing a string through `int` is exactly
    the EMSC-corrupting step described above. A JSON number can never carry an
    underscore, so this direction is lossless.
    """
    if isinstance(value, bool):
        return value  # let str validation reject it, rather than yielding "True"
    if isinstance(value, int):
        return str(value)
    return value


_EVENTID_DESCRIPTION = (
    "FDSN event ID, as an opaque string. Identifier formats are provider-specific "
    "(e.g. INGV '37258271', EMSC '20240101_0000328', GFZ 'gfz2024abmz', "
    "USGS 'us6000m0yg'), so copy the value VERBATIM from the event_id column of a "
    "prior fdsn_query_earthquakes result. Do NOT invent, guess, reformat, strip "
    "characters from, or use placeholder values."
)

# Shared definition so the five by-id models cannot drift apart. The pattern also
# keeps the value safe to interpolate into an upstream query string.
EventId = Annotated[
    str,
    BeforeValidator(_coerce_eventid),
    Field(
        min_length=1,
        max_length=64,
        pattern=_EVENTID_PATTERN,
        description=_EVENTID_DESCRIPTION,
    ),
]

# What the MCP tool signatures accept, before EventId normalizes it. Declaring the
# union (rather than plain str) keeps the generated JSON schema tolerant of the
# integer that the old schema taught clients to send. Pydantic's smart union leaves
# a string as a string, so an EMSC identifier is never routed through int.
EventIdInput = Union[int, str]


class QueryEarthquakesInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )

    starttime: Optional[str] = Field(
        default=None,
        description="Start time in YYYY-MM-DDTHH:MM:SS format (default: today 00:00:00)",
    )
    endtime: Optional[str] = Field(
        default=None,
        description="End time in YYYY-MM-DDTHH:MM:SS format (default: today 23:59:59)",
    )
    updatedafter: Optional[str] = Field(
        default=None,
        description="Return events updated after this time (ISO 8601: YYYY-MM-DDTHH:MM:SS)",
    )
    minmag: Optional[float] = Field(
        default=None,
        ge=-2.0,
        le=10.0,
        description="Minimum magnitude (e.g., 4.0 for significant events)",
    )
    maxmag: Optional[float] = Field(
        default=None, ge=-2.0, le=10.0, description="Maximum magnitude"
    )
    minlat: Optional[float] = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="Minimum latitude (WGS84) - for geographic filtering",
    )
    maxlat: Optional[float] = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="Maximum latitude (WGS84) - for geographic filtering",
    )
    minlon: Optional[float] = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="Minimum longitude (WGS84) - for geographic filtering",
    )
    maxlon: Optional[float] = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="Maximum longitude (WGS84) - for geographic filtering",
    )
    mindepth: Optional[float] = Field(
        default=None, ge=0.0, description="Minimum depth in kilometers"
    )
    maxdepth: Optional[float] = Field(
        default=None, ge=0.0, description="Maximum depth in kilometers"
    )
    latitude: Optional[float] = Field(
        default=None, ge=-90.0, le=90.0,
        description="Center latitude for radial search (WGS84)",
    )
    longitude: Optional[float] = Field(
        default=None, ge=-180.0, le=180.0,
        description="Center longitude for radial search (WGS84)",
    )
    minradiuskm: Optional[float] = Field(
        default=None, ge=0.0,
        description="Minimum radius in km for radial search",
    )
    maxradiuskm: Optional[float] = Field(
        default=None, ge=0.0,
        description="Maximum radius in km for radial search",
    )
    limit: int = Field(
        default=config.DEFAULT_ROWS_EVENTS,
        ge=1,
        le=config.MAX_ROWS_EVENTS,
        description=(
            "Maximum number of events to return "
            f"(default: {config.DEFAULT_ROWS_EVENTS}, max: {config.MAX_ROWS_EVENTS})"
        ),
    )
    offset: int = Field(
        default=1,
        ge=1,
        description=(
            "1-based index of the first event to return, per the FDSN spec "
            "(default: 1). Use with limit to page: next_offset = offset + returned_count. "
            "Note: offset indexing follows the datacenter implementation."
        ),
    )
    orderby: Literal["time", "time-asc", "magnitude", "magnitude-asc"] = Field(
        default="time",
        description=(
            "Sort order of results: time (most recent first, default), time-asc, "
            "magnitude (largest first), magnitude-asc"
        ),
    )
    datacenter: str = Field(
        default="INGV",
        description=(
            "FDSN datacenter to query (e.g., INGV, EMSC, GFZ, USGS). "
            "Default INGV is an overridable convenience, not a binding"
        ),
    )

    @field_validator("starttime", "endtime", "updatedafter", mode="before")
    @classmethod
    def validate_datetime_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(f"Invalid datetime format '{v}'. Use YYYY-MM-DDTHH:MM:SS")
        return v

    @model_validator(mode="after")
    def validate_radial_vs_bbox(self) -> "QueryEarthquakesInput":
        has_lat = self.latitude is not None
        has_lon = self.longitude is not None
        has_radial = has_lat or has_lon

        has_bbox = any(
            v is not None
            for v in (self.minlat, self.maxlat, self.minlon, self.maxlon)
        )

        if has_lat != has_lon:
            raise ValueError(
                "latitude and longitude must be provided together for radial search"
            )

        if has_radial and has_bbox:
            raise ValueError(
                "Radial search parameters (latitude, longitude, minradiuskm, maxradiuskm) "
                "and bounding-box parameters (minlat, maxlat, minlon, maxlon) "
                "are mutually exclusive"
            )

        return self




# ---------------------------------------------------------------------------
# By-event-id models
# ---------------------------------------------------------------------------
#
# Every tool below takes an event id, which is why each is named for that input
# rather than for the QuakeML class its rows come from. Arrivals and station
# magnitudes belong to an Origin, not to an Event, so `_by_originid` was the
# obvious name -- and the wrong one: no FDSN node accepts an origin id as a
# query parameter, so the input would still have been an event id, and a tool
# called `_by_originid` invites a model to invent an origin id. That is the
# failure the opaque-string typing of `eventid` exists to prevent.

_DATACENTER_FIELD_DESCRIPTION = "FDSN datacenter to query (e.g., INGV, EMSC, GFZ, USGS)"


class _ByEventIdInput(BaseModel):
    """Shared shape of every by-event-id tool input."""

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    eventid: EventId
    datacenter: str = Field(
        default="INGV",
        description=_DATACENTER_FIELD_DESCRIPTION,
    )


class _TabularByEventIdInput(_ByEventIdInput):
    """A by-event-id tool that returns a paginated table.

    The station filters are applied by this server after the fetch, because no
    FDSN event service filters a subresource by station. They exist so that
    "the amplitude at station XYZ" costs one small answer instead of paging
    through every reading of the event.
    """

    network: Optional[str] = Field(
        default=None,
        max_length=8,
        description=(
            "Filter rows by network code (exact, case-insensitive), e.g. 'IV'. "
            "Applied after the fetch"
        ),
    )
    station: Optional[str] = Field(
        default=None,
        max_length=8,
        description=(
            "Filter rows by station code (exact, case-insensitive), e.g. 'SGRT'. "
            "Applied after the fetch"
        ),
    )
    offset: int = Field(
        default=1,
        ge=1,
        description=(
            "1-based index of the first row to return (default: 1). Page with "
            "next_offset from the previous response"
        ),
    )


class GetEarthquakeByEventIdInput(_ByEventIdInput):
    pass


class GetAllOriginsByEventIdInput(_ByEventIdInput):
    pass


class GetAllMagnitudesByEventIdInput(_ByEventIdInput):
    pass


class GetFocalMechanismByEventIdInput(_ByEventIdInput):
    pass


class GetArrivalsByEventIdInput(_TabularByEventIdInput):
    limit: int = Field(
        default=config.DEFAULT_ROWS_ARRIVALS,
        ge=1,
        le=config.MAX_ROWS_ARRIVALS,
        description=(
            "Maximum number of arrival rows to return "
            f"(default: {config.DEFAULT_ROWS_ARRIVALS}, max: {config.MAX_ROWS_ARRIVALS})"
        ),
    )


class GetStationMagnitudesByEventIdInput(_TabularByEventIdInput):
    magnitude_type: Optional[str] = Field(
        default=None,
        max_length=16,
        description=(
            "Filter rows by station magnitude type (exact, case-insensitive), "
            "e.g. 'ML'. Useful where an origin carries more than one magnitude"
        ),
    )
    limit: int = Field(
        default=config.DEFAULT_ROWS_STATIONMAGNITUDES,
        ge=1,
        le=config.MAX_ROWS_STATIONMAGNITUDES,
        description=(
            "Maximum number of station magnitude rows to return "
            f"(default: {config.DEFAULT_ROWS_STATIONMAGNITUDES}, "
            f"max: {config.MAX_ROWS_STATIONMAGNITUDES})"
        ),
    )


class GetAmplitudesByEventIdInput(_TabularByEventIdInput):
    limit: int = Field(
        default=config.DEFAULT_ROWS_AMPLITUDES,
        ge=1,
        le=config.MAX_ROWS_AMPLITUDES,
        description=(
            "Maximum number of amplitude rows to return "
            f"(default: {config.DEFAULT_ROWS_AMPLITUDES}, max: {config.MAX_ROWS_AMPLITUDES})"
        ),
    )
