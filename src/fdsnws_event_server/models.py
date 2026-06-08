"""Pydantic input models for FDSNWS Event MCP tools."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of events to return (default: 100)",
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
            "FDSN datacenter to query (e.g., INGV, IRIS, EMSC, GFZ). "
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


class GetEarthquakeByIdInput(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    eventid: int = Field(..., gt=0, description="FDSN event ID (positive integer)")
    datacenter: str = Field(
        default="INGV",
        description="FDSN datacenter to query (e.g., INGV, IRIS, EMSC, GFZ)",
    )


class GetArrivalsByIdInput(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    eventid: int = Field(..., gt=0, description="FDSN event ID")
    datacenter: str = Field(
        default="INGV",
        description="FDSN datacenter to query (e.g., INGV, IRIS, EMSC, GFZ)",
    )


class GetAllOriginsByIdInput(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    eventid: int = Field(..., gt=0, description="FDSN event ID")
    datacenter: str = Field(
        default="INGV",
        description="FDSN datacenter to query (e.g., INGV, IRIS, EMSC, GFZ)",
    )


class GetAllMagnitudesByIdInput(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    eventid: int = Field(..., gt=0, description="FDSN event ID")
    datacenter: str = Field(
        default="INGV",
        description="FDSN datacenter to query (e.g., INGV, IRIS, EMSC, GFZ)",
    )


class GetFocalMechanismByIdInput(BaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
    )

    eventid: int = Field(..., gt=0, description="FDSN event ID")
    datacenter: str = Field(
        default="INGV",
        description="FDSN datacenter to query (e.g., INGV, IRIS, EMSC, GFZ)",
    )
