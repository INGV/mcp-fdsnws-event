"""The schema this server publishes must say what the Pydantic models enforce.

FastMCP builds each tool's JSON schema from the decorated function's signature.
The Pydantic model inside the body validates what arrives and is invisible to
the caller, so a constraint written only on the model is a rule the caller is
punished for breaking without ever being told.

That is not hypothetical. Through 2.0.0 the published `eventid` was a bare
``{"anyOf": [{"type": "integer"}, {"type": "string"}]}``: no pattern, and none
of the wording that tells a caller to copy the identifier verbatim. A model
asked for INGV event 47219912 and sent ``4.7219912e+07`` -- the right event,
rendered as a float, because the schema had said the field could be a number
and nothing had said it must not be reformatted. The value was refused, as it
had to be, but only after a wasted call. Every `limit` had the same hole: no
maximum was advertised, so a request for 1000 rows could only fail.

These tests are the guard. They read the schema as an MCP client would.
"""

import asyncio
import json

import pytest

from fdsnws_event_server import config, models
from fdsnws_event_server.server import mcp


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in run(mcp.list_tools())}


def _params(tool):
    return tool.inputSchema.get("properties", {})


def test_every_published_parameter_carries_a_description(tools):
    """A parameter with no description is a parameter the caller must guess.

    The descriptions exist; for 2.0.0 they simply never left `models.py`.
    """
    missing = [
        f"{name}.{param}"
        for name, tool in tools.items()
        for param, schema in _params(tool).items()
        if not schema.get("description")
    ]
    assert not missing, f"parameters published without a description: {missing}"


def test_eventid_is_published_as_a_constrained_string(tools):
    """No integer branch, and the pattern and the verbatim rule both visible.

    The integer branch was backward compatibility for clients primed by the
    pre-1.4 schema. Publishing it invited the numeric formatting that opaque
    event ids exist to prevent, so it is gone from the schema while the
    BeforeValidator still accepts an integer at runtime.
    """
    for name, tool in tools.items():
        schema = _params(tool).get("eventid")
        if schema is None:
            continue
        assert schema.get("type") == "string", f"{name}: eventid is not a plain string"
        assert "anyOf" not in schema, f"{name}: eventid still advertises a type union"
        assert schema.get("pattern") == models._EVENTID_PATTERN, (
            f"{name}: eventid publishes no pattern, so a caller cannot see which "
            "characters are legal"
        )
        assert "VERBATIM" in schema["description"], (
            f"{name}: the copy-verbatim instruction did not reach the schema"
        )


def test_eventid_accepts_an_integer_at_runtime(tools):
    """The schema says string; the server must still take the integer.

    Callers and models primed by the old schema send a JSON number, and the
    deployed client was observed doing exactly that. Tightening what is
    advertised must not break what already works.
    """
    parsed = models.GetEarthquakeByEventIdInput(eventid=47219912)
    assert parsed.eventid == "47219912"

    with pytest.raises(Exception):
        models.GetEarthquakeByEventIdInput(eventid="4.7219912e+07")


@pytest.mark.parametrize(
    "tool_name,maximum",
    [
        ("fdsn_query_earthquakes", config.MAX_ROWS_EVENTS),
        ("fdsn_get_arrivals_by_eventid", config.MAX_ROWS_ARRIVALS),
        ("fdsn_get_stationmagnitudes_by_eventid", config.MAX_ROWS_STATIONMAGNITUDES),
        ("fdsn_get_amplitudes_by_eventid", config.MAX_ROWS_AMPLITUDES),
    ],
)
def test_row_limits_are_advertised(tools, tool_name, maximum):
    """The ceiling has to be in the schema, not only in the validator.

    A maximum the caller cannot see turns an ordinary bound into an error it can
    only discover by tripping over it.
    """
    schema = _params(tools[tool_name])["limit"]
    assert schema.get("maximum") == maximum
    assert schema.get("minimum") == 1
    assert schema.get("default") is not None


def test_orderby_publishes_its_allowed_values(tools):
    """An enum in the schema is worth more than a sentence in the prose."""
    schema = _params(tools["fdsn_query_earthquakes"])["orderby"]
    assert sorted(schema["enum"]) == ["magnitude", "magnitude-asc", "time", "time-asc"]


def test_numeric_ranges_reach_the_schema(tools):
    """Magnitude and coordinate bounds are validated; they must also be stated."""
    params = _params(tools["fdsn_query_earthquakes"])
    expected = {
        "minmag": (-2.0, 10.0),
        "maxmag": (-2.0, 10.0),
        "minlat": (-90.0, 90.0),
        "maxlon": (-180.0, 180.0),
    }
    for name, (low, high) in expected.items():
        # Optional parameters publish as a union with null; the bounds live on
        # the numeric arm of it.
        arms = params[name].get("anyOf") or [params[name]]
        numeric = next(a for a in arms if a.get("type") == "number")
        assert numeric.get("minimum") == low, f"{name}: lower bound not published"
        assert numeric.get("maximum") == high, f"{name}: upper bound not published"


def test_schema_is_json_serializable_and_self_contained(tools):
    """Whatever we publish has to survive the wire to the client."""
    for name, tool in tools.items():
        blob = json.dumps(tool.inputSchema)
        assert "$ref" not in blob, f"{name}: schema references a definition by pointer"
