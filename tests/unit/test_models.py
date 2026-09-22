"""Unit tests for the Pydantic input models (structural validation only)."""

import pytest
from pydantic import ValidationError

from fdsnws_event_server import config
from fdsnws_event_server.models import (
    GetAmplitudesByEventIdInput,
    GetArrivalsByEventIdInput,
    GetEarthquakeByEventIdInput,
    GetStationMagnitudesByEventIdInput,
    QueryEarthquakesInput,
)


def test_defaults():
    m = QueryEarthquakesInput()
    # Compared against config, not against a literal: the row limits are read
    # from the environment at import and baked into the published JSON schema,
    # so the model's own default is whatever config settled on.
    assert m.limit == config.DEFAULT_ROWS_EVENTS
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


def test_event_limit_is_capped():
    """The cap is a context budget, so asking past it is a client error, not a trim."""
    with pytest.raises(ValidationError):
        QueryEarthquakesInput(limit=config.MAX_ROWS_EVENTS + 1)


# --- Tabular by-eventid models ---------------------------------------------
#
# The three table tools share _TabularByEventIdInput (network, station, offset)
# and differ only in the limit each one can afford, because a row of arrivals is
# far wider than a row of station magnitudes. These tests pin the shared shape
# and the per-tool limit against config, which is where the numbers are decided.

TABULAR_MODELS = [
    (
        GetArrivalsByEventIdInput,
        config.DEFAULT_ROWS_ARRIVALS,
        config.MAX_ROWS_ARRIVALS,
    ),
    (
        GetStationMagnitudesByEventIdInput,
        config.DEFAULT_ROWS_STATIONMAGNITUDES,
        config.MAX_ROWS_STATIONMAGNITUDES,
    ),
    (
        GetAmplitudesByEventIdInput,
        config.DEFAULT_ROWS_AMPLITUDES,
        config.MAX_ROWS_AMPLITUDES,
    ),
]
TABULAR_IDS = [m[0].__name__ for m in TABULAR_MODELS]


@pytest.mark.parametrize("model,default_rows,max_rows", TABULAR_MODELS, ids=TABULAR_IDS)
def test_tabular_defaults(model, default_rows, max_rows):
    m = model(eventid="37258271")
    assert m.limit == default_rows
    assert m.offset == 1
    assert m.network is None
    assert m.station is None
    assert m.datacenter == "INGV"


@pytest.mark.parametrize("model,default_rows,max_rows", TABULAR_MODELS, ids=TABULAR_IDS)
def test_tabular_limit_is_capped(model, default_rows, max_rows):
    """Each table's maximum is what a full page of *its* rows costs in bytes."""
    assert model(eventid="37258271", limit=max_rows).limit == max_rows
    with pytest.raises(ValidationError):
        model(eventid="37258271", limit=max_rows + 1)


@pytest.mark.parametrize("model,default_rows,max_rows", TABULAR_MODELS, ids=TABULAR_IDS)
def test_tabular_offset_is_one_based(model, default_rows, max_rows):
    """Offset 0 is not "the first row", it is a caller that thinks it is 0-based."""
    with pytest.raises(ValidationError):
        model(eventid="37258271", offset=0)


@pytest.mark.parametrize("model,default_rows,max_rows", TABULAR_MODELS, ids=TABULAR_IDS)
def test_station_filters_are_bounded(model, default_rows, max_rows):
    """A code longer than any SEED code is a mistake worth refusing client-side."""
    with pytest.raises(ValidationError):
        model(eventid="37258271", station="x" * 9)
    with pytest.raises(ValidationError):
        model(eventid="37258271", network="x" * 9)


def test_magnitude_type_filter_belongs_to_station_magnitudes_only():
    """It exists where an origin can carry several magnitudes, and nowhere else.

    Accepting it silently on the other tables would let a model believe it had
    narrowed a result that was never narrowed.
    """
    assert (
        GetStationMagnitudesByEventIdInput(eventid="37258271", magnitude_type="ML").magnitude_type
        == "ML"
    )
    with pytest.raises(ValidationError):
        GetArrivalsByEventIdInput(eventid="37258271", magnitude_type="ML")


def test_non_tabular_model_rejects_table_parameters():
    """The plain by-eventid tools return one solution, so there is nothing to page."""
    with pytest.raises(ValidationError):
        GetEarthquakeByEventIdInput(eventid="37258271", limit=5)
    with pytest.raises(ValidationError):
        GetEarthquakeByEventIdInput(eventid="37258271", station="SGRT")
