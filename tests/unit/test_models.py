"""Unit tests for the Pydantic input models (structural validation only)."""

import pytest
from pydantic import ValidationError

from fdsnws_event_server.models import QueryEarthquakesInput


def test_defaults():
    m = QueryEarthquakesInput()
    assert m.limit == 100
    assert m.offset == 1
    assert m.orderby == "time"
    assert m.datacenter == "INGV"


def test_bbox_and_radial_mutually_exclusive():
    with pytest.raises(ValidationError):
        QueryEarthquakesInput(minlat=41, maxlat=43, latitude=42, longitude=12)


def test_latitude_requires_longitude():
    with pytest.raises(ValidationError):
        QueryEarthquakesInput(latitude=42)


def test_offset_must_be_positive():
    with pytest.raises(ValidationError):
        QueryEarthquakesInput(offset=0)


def test_orderby_is_constrained():
    with pytest.raises(ValidationError):
        QueryEarthquakesInput(orderby="depth")


def test_extra_parameters_forbidden():
    with pytest.raises(ValidationError):
        QueryEarthquakesInput(foo="bar")


def test_invalid_datetime_rejected():
    with pytest.raises(ValidationError):
        QueryEarthquakesInput(starttime="not-a-date")


def test_valid_radial_search():
    m = QueryEarthquakesInput(latitude=41.9, longitude=12.5, maxradiuskm=50)
    assert m.maxradiuskm == 50
