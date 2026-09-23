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
        # Restated for the published schema. Pydantic drops `pattern` from the
        # JSON schema of any type carrying a BeforeValidator, reasonably enough:
        # a validator may rewrite the input, so a constraint on the validated
        # value need not describe what a caller may send. Here the validator only
        # turns an integer into its digits, which the pattern already allows, so
        # the constraint does describe valid input -- and a caller that cannot
        # see it is the caller that sends `4.7219912e+07`.
        json_schema_extra={"pattern": _EVENTID_PATTERN},
    ),
]

# ---------------------------------------------------------------------------
# Parameter types, shared by the validation models and the tool signatures
# ---------------------------------------------------------------------------
#
# These exist because a Pydantic model is NOT what the MCP client sees. FastMCP
# builds each tool's published JSON schema from the decorated function's
# signature; the model below only validates what arrives. So for 2.0.0 every
# constraint and description written here reached no one: the schema advertised
# a bare `{"anyOf": [{"type": "integer"}, {"type": "string"}]}` for `eventid`,
# with no pattern and none of the wording telling a caller to copy the value
# verbatim. A model then sent `4.7219912e+07` -- the right event id, formatted
# as a float, because the schema had told it the field was a number and nothing
# had told it otherwise. The value was rejected, correctly, but only after a
# wasted round trip.
#
# Declaring each parameter once here and annotating both the model field and the
# function parameter with it means the published schema and the validation can
# no longer disagree; `test_published_schema_matches_models` pins that.

EventId = Annotated[
    str,
    BeforeValidator(_coerce_eventid),
    Field(
        min_length=1,
        max_length=64,
        pattern=_EVENTID_PATTERN,
        description=_EVENTID_DESCRIPTION,
        # Restated for the published schema. Pydantic drops `pattern` from the
        # JSON schema of any type carrying a BeforeValidator, reasonably enough:
        # a validator may rewrite the input, so a constraint on the validated
        # value need not describe what a caller may send. Here the validator only
        # turns an integer into its digits, which the pattern already allows, so
        # the constraint does describe valid input -- and a caller that cannot
        # see it is the caller that sends `4.7219912e+07`.
        json_schema_extra={"pattern": _EVENTID_PATTERN},
    ),
]

# `str`, not `Union[int, str]`. The integer branch was backward compatibility for
# clients primed by the pre-1.4 schema, and publishing it invited exactly the
# numeric formatting that ADR-0007 exists to prevent. The BeforeValidator still
# accepts an integer at runtime, so nothing that used to work stops working; the
# difference is that the schema no longer suggests it.
EventIdInput = EventId

DataCenter = Annotated[
    str,
    Field(
        description=(
            "FDSN datacenter to query. Known values: INGV (default), EMSC, GFZ, "
            "USGS, and the other identifiers ObsPy maps. Default INGV is an "
            "overridable convenience, not a binding"
        ),
    ),
]

StartTime = Annotated[
    Optional[str],
    Field(description="Start time in YYYY-MM-DDTHH:MM:SS format (default: today 00:00:00)"),
]
EndTime = Annotated[
    Optional[str],
    Field(description="End time in YYYY-MM-DDTHH:MM:SS format (default: today 23:59:59)"),
]
UpdatedAfter = Annotated[
    Optional[str],
    Field(description="Return events updated after this time (ISO 8601: YYYY-MM-DDTHH:MM:SS)"),
]

MinMag = Annotated[
    Optional[float],
    Field(ge=-2.0, le=10.0, description="Minimum magnitude (e.g., 4.0 for significant events)"),
]
MaxMag = Annotated[Optional[float], Field(ge=-2.0, le=10.0, description="Maximum magnitude")]

MinLat = Annotated[
    Optional[float],
    Field(ge=-90.0, le=90.0, description="Minimum latitude (WGS84), for bounding-box filtering"),
]
MaxLat = Annotated[
    Optional[float],
    Field(ge=-90.0, le=90.0, description="Maximum latitude (WGS84), for bounding-box filtering"),
]
MinLon = Annotated[
    Optional[float],
    Field(ge=-180.0, le=180.0, description="Minimum longitude (WGS84), for bounding-box filtering"),
]
MaxLon = Annotated[
    Optional[float],
    Field(ge=-180.0, le=180.0, description="Maximum longitude (WGS84), for bounding-box filtering"),
]

MinDepth = Annotated[Optional[float], Field(ge=0.0, description="Minimum depth in kilometers")]
MaxDepth = Annotated[Optional[float], Field(ge=0.0, description="Maximum depth in kilometers")]

Latitude = Annotated[
    Optional[float],
    Field(ge=-90.0, le=90.0, description="Center latitude for radial search (WGS84)"),
]
Longitude = Annotated[
    Optional[float],
    Field(ge=-180.0, le=180.0, description="Center longitude for radial search (WGS84)"),
]
MinRadiusKm = Annotated[
    Optional[float], Field(ge=0.0, description="Minimum radius in km for radial search")
]
MaxRadiusKm = Annotated[
    Optional[float], Field(ge=0.0, description="Maximum radius in km for radial search")
]

OrderBy = Annotated[
    Literal["time", "time-asc", "magnitude", "magnitude-asc"],
    Field(
        description=(
            "Sort order of results: time (most recent first, default), time-asc, "
            "magnitude (largest first), magnitude-asc"
        ),
    ),
]

EventsOffset = Annotated[
    int,
    Field(
        ge=1,
        description=(
            "1-based index of the first event to return, per the FDSN spec "
            "(default: 1). Use with limit to page: next_offset = offset + "
            "returned_count. Note: offset indexing follows the datacenter "
            "implementation"
        ),
    ),
]

NetworkFilter = Annotated[
    Optional[str],
    Field(
        max_length=8,
        description=(
            "Filter rows by network code (exact, case-insensitive), e.g. 'IV'. "
            "Applied after the fetch"
        ),
    ),
]
StationFilter = Annotated[
    Optional[str],
    Field(
        max_length=8,
        description=(
            "Filter rows by station code (exact, case-insensitive), e.g. 'SGRT'. "
            "Applied after the fetch"
        ),
    ),
]
MagnitudeTypeFilter = Annotated[
    Optional[str],
    Field(
        max_length=16,
        description=(
            "Filter rows by station magnitude type (exact, case-insensitive), "
            "e.g. 'ML'. Useful where an origin carries more than one magnitude"
        ),
    ),
]
RowOffset = Annotated[
    int,
    Field(
        ge=1,
        description=(
            "1-based index of the first row to return (default: 1). Page with "
            "the next_offset the previous response suggests"
        ),
    ),
]


EventsLimit = Annotated[
    int,
    Field(
        ge=1,
        le=config.MAX_ROWS_EVENTS,
        description=(
            "Maximum number of events to return "
            f"(default: {config.DEFAULT_ROWS_EVENTS}, max: {config.MAX_ROWS_EVENTS})"
        ),
    ),
]


class QueryEarthquakesInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )

    starttime: StartTime = None
    endtime: EndTime = None
    updatedafter: UpdatedAfter = None
    minmag: MinMag = None
    maxmag: MaxMag = None
    minlat: MinLat = None
    maxlat: MaxLat = None
    minlon: MinLon = None
    maxlon: MaxLon = None
    mindepth: MinDepth = None
    maxdepth: MaxDepth = None
    latitude: Latitude = None
    longitude: Longitude = None
    minradiuskm: MinRadiusKm = None
    maxradiuskm: MaxRadiusKm = None
    limit: EventsLimit = config.DEFAULT_ROWS_EVENTS
    offset: EventsOffset = 1
    orderby: OrderBy = "time"
    datacenter: DataCenter = "INGV"

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

class _ByEventIdInput(BaseModel):
    """Shared shape of every by-event-id tool input."""

    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    eventid: EventId
    datacenter: DataCenter = "INGV"


class _TabularByEventIdInput(_ByEventIdInput):
    """A by-event-id tool that returns a paginated table.

    The station filters are applied by this server after the fetch, because no
    FDSN event service filters a subresource by station. They exist so that
    "the amplitude at station XYZ" costs one small answer instead of paging
    through every reading of the event.
    """

    network: NetworkFilter = None
    station: StationFilter = None
    offset: RowOffset = 1


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
    magnitude_type: MagnitudeTypeFilter = None
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
