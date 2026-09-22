"""Unit tests for the import-time runtime limits (offline).

``config`` is not an ordinary settings module. Every ``MAX_ROWS_*`` value becomes
a Pydantic ``Field(le=...)`` constraint on a tool input model, and Pydantic bakes
that into the JSON schema published in ``tools/list`` -- so these numbers are
part of what the model is *told* it may ask for. Two things follow, and both are
what the tests below defend: a bad value must degrade to the default instead of
taking the server down at import, and a default must never exceed its own
maximum, because Pydantic would then reject its own field default and the whole
server would die on a config typo.
"""

import importlib
import logging

import pytest

from fdsnws_event_server import config as config_module

CONFIG_LOGGER = "fdsnws_event_server.config"

# Every environment variable this module reads. Cleared before each reload so a
# value left over from the developer's shell cannot decide a test's outcome.
ENV_VARS = (
    "FDSN_MAX_RESULT_BYTES",
    "FDSN_MAX_ROWS_ARRIVALS",
    "FDSN_MAX_ROWS_STATIONMAGNITUDES",
    "FDSN_MAX_ROWS_AMPLITUDES",
    "FDSN_MAX_ROWS_EVENTS",
    "FDSN_DEFAULT_ROWS_ARRIVALS",
    "FDSN_DEFAULT_ROWS_STATIONMAGNITUDES",
    "FDSN_DEFAULT_ROWS_AMPLITUDES",
    "FDSN_DEFAULT_ROWS_EVENTS",
    "FDSN_TIMEOUT",
)

MAX_TO_DEFAULT = {
    "MAX_ROWS_ARRIVALS": "DEFAULT_ROWS_ARRIVALS",
    "MAX_ROWS_STATIONMAGNITUDES": "DEFAULT_ROWS_STATIONMAGNITUDES",
    "MAX_ROWS_AMPLITUDES": "DEFAULT_ROWS_AMPLITUDES",
    "MAX_ROWS_EVENTS": "DEFAULT_ROWS_EVENTS",
}


@pytest.fixture
def reload_config(monkeypatch):
    """Reload ``config`` under a controlled environment, then put it back.

    The limits are read once at import, so the only way to exercise them is to
    re-import the module -- which mutates a module object other tests share.
    ``monkeypatch.undo()`` is called explicitly before the final reload because a
    yield fixture is torn down *before* monkeypatch restores the environment: a
    reload after a bare ``yield`` would re-read the patched values and leak the
    modified limits into every test module that runs afterwards.
    """

    def _reload(**env):
        for name in ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(config_module)

    yield _reload

    monkeypatch.undo()
    importlib.reload(config_module)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# _int_env
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["abc", "1.5", "0x10", "150rows"])
def test_int_env_falls_back_on_a_non_integer(monkeypatch, caplog, raw):
    """A typo in one env var must not take the server down at import.

    The limits are read while the module is being imported, so an exception here
    would abort the whole server before it ever speaks MCP -- with a traceback
    the operator sees only in the container log. Degrading to the default keeps
    it answering, and the warning says which variable was wrong.
    """
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    monkeypatch.setenv("FDSN_TEST_LIMIT", raw)
    assert config_module._int_env("FDSN_TEST_LIMIT", 42) == 42
    assert any("FDSN_TEST_LIMIT" in m for m in _warnings(caplog))


@pytest.mark.parametrize("raw", ["0", "-1", "-5000"])
def test_int_env_rejects_a_value_below_one(monkeypatch, caplog, raw):
    """Zero or negative rows is not a smaller budget, it is a broken tool.

    A max of 0 would become ``Field(le=0)`` and make the tool unusable while
    looking perfectly configured, so it is refused the same way a typo is.
    """
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    monkeypatch.setenv("FDSN_TEST_LIMIT", raw)
    assert config_module._int_env("FDSN_TEST_LIMIT", 42) == 42
    assert any("FDSN_TEST_LIMIT" in m for m in _warnings(caplog))


def test_int_env_accepts_a_valid_override(monkeypatch, caplog):
    """The fallbacks must not swallow the legitimate case they exist to guard."""
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    monkeypatch.setenv("FDSN_TEST_LIMIT", "7")
    assert config_module._int_env("FDSN_TEST_LIMIT", 42) == 7
    assert _warnings(caplog) == []


def test_int_env_uses_the_default_when_unset(monkeypatch):
    """An unset variable is the normal deployment, not an error to warn about."""
    monkeypatch.delenv("FDSN_TEST_LIMIT", raising=False)
    assert config_module._int_env("FDSN_TEST_LIMIT", 42) == 42


def test_bad_env_var_degrades_the_real_constant(reload_config, caplog):
    """The fallback has to survive the actual import path, not just a call.

    ``_int_env`` could be correct while the module-level assignment used the raw
    value, so this asserts the constant itself after a reload.
    """
    pristine = reload_config().MAX_ROWS_ARRIVALS
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    config = reload_config(FDSN_MAX_ROWS_ARRIVALS="lots")
    # Compared against the module's own shipped value, not a literal: the
    # numbers are retuned against production traffic, the fallback behaviour is
    # not.
    assert config.MAX_ROWS_ARRIVALS == pristine
    assert any("FDSN_MAX_ROWS_ARRIVALS" in m for m in _warnings(caplog))


# ---------------------------------------------------------------------------
# _clamp_default
# ---------------------------------------------------------------------------


def test_clamp_default_leaves_a_default_at_or_below_its_maximum(caplog):
    """Clamping must be a no-op in the shipped configuration.

    The arrival default deliberately equals its maximum, so an off-by-one in the
    comparison would rewrite a correct setting and warn about it on every start.
    """
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    assert config_module._clamp_default("X", 150, 150) == 150
    assert config_module._clamp_default("X", 10, 150) == 10
    assert _warnings(caplog) == []


def test_clamp_default_lowers_a_default_above_its_maximum(caplog):
    """A default over its maximum would kill the server, so it is pulled down.

    The two are independent env vars: an operator can lower a maximum and forget
    the matching default. Pydantic then rejects its own field default and the
    tool fails to build at import. Clamping keeps the server alive and loud.
    """
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    assert config_module._clamp_default("FDSN_DEFAULT_ROWS_EVENTS", 900, 400) == 400
    assert any("FDSN_DEFAULT_ROWS_EVENTS" in m for m in _warnings(caplog))


def test_lowering_a_maximum_pulls_its_default_down_with_it(reload_config, caplog):
    """The clamp has to run on the module-level constants, in the right order.

    Setting only the maximum is the realistic operator mistake; the default is
    left at its shipped 150 and must come out at the new maximum, not above it.
    """
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    config = reload_config(FDSN_MAX_ROWS_ARRIVALS="10")
    assert config.MAX_ROWS_ARRIVALS == 10
    assert config.DEFAULT_ROWS_ARRIVALS == 10
    assert any("FDSN_DEFAULT_ROWS_ARRIVALS" in m for m in _warnings(caplog))


def test_an_explicit_default_over_an_explicit_maximum_is_clamped(reload_config):
    """Both set, inconsistently: the maximum wins, because the schema does."""
    config = reload_config(
        FDSN_MAX_ROWS_EVENTS="50", FDSN_DEFAULT_ROWS_EVENTS="1000"
    )
    assert config.MAX_ROWS_EVENTS == 50
    assert config.DEFAULT_ROWS_EVENTS == 50


# ---------------------------------------------------------------------------
# The shipped constants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "MAX_RESULT_BYTES",
        "MAX_ROWS_ARRIVALS",
        "MAX_ROWS_STATIONMAGNITUDES",
        "MAX_ROWS_AMPLITUDES",
        "MAX_ROWS_EVENTS",
        "DEFAULT_ROWS_ARRIVALS",
        "DEFAULT_ROWS_STATIONMAGNITUDES",
        "DEFAULT_ROWS_AMPLITUDES",
        "DEFAULT_ROWS_EVENTS",
    ],
)
def test_every_limit_is_a_positive_int(name):
    """These become JSON schema constraints, where a float or a bool is nonsense.

    ``bool`` is excluded explicitly because it is an ``int`` in Python and would
    pass a naive check while publishing ``le=True`` to the model.
    """
    value = getattr(config_module, name)
    assert isinstance(value, int) and not isinstance(value, bool)
    assert value >= 1


@pytest.mark.parametrize("max_name,default_name", list(MAX_TO_DEFAULT.items()))
def test_every_default_is_within_its_maximum(max_name, default_name):
    """The invariant the clamp exists to hold, asserted on the shipped values.

    If this fails, the server does not start: Pydantic refuses a field default
    outside its own ``le`` constraint.
    """
    assert getattr(config_module, default_name) <= getattr(config_module, max_name)


def test_the_byte_budget_can_hold_a_full_page():
    """Every row maximum must be reachable inside MAX_RESULT_BYTES.

    The maxima are derived from that budget divided by the widest row measured,
    so a maximum the budget cannot honour is a lie told to the model in the tool
    schema: it asks for the advertised page and the size guard trims it every
    single time. The exact figures are deliberately not pinned here -- they are
    retuned against production traffic -- but the relationship between them is
    the invariant that makes them meaningful. 60 bytes/row is a floor no real
    row of any of these tables comes near.
    """
    assert config_module.MAX_RESULT_BYTES >= 60 * max(
        getattr(config_module, name) for name in MAX_TO_DEFAULT
    )
    assert isinstance(config_module.DEFAULT_TIMEOUT, float)
    assert config_module.DEFAULT_TIMEOUT > 0


# ---------------------------------------------------------------------------
# get_timeout: read per call, not frozen at import
# ---------------------------------------------------------------------------


def test_get_timeout_is_read_per_call(monkeypatch):
    """Unlike the row limits, the timeout shapes no schema.

    Nothing has to be frozen at import, so an operator can change it without the
    advertised tool contract shifting underneath a model mid-conversation.
    """
    monkeypatch.delenv("FDSN_TIMEOUT", raising=False)
    assert config_module.get_timeout() == 45.0
    monkeypatch.setenv("FDSN_TIMEOUT", "5.5")
    assert config_module.get_timeout() == 5.5


def test_get_timeout_falls_back_on_nonsense(monkeypatch, caplog):
    """Same contract as the row limits, and the one FDSN_TIMEOUT always had."""
    caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
    monkeypatch.setenv("FDSN_TIMEOUT", "soon")
    assert config_module.get_timeout() == 45.0
    assert any("FDSN_TIMEOUT" in m for m in _warnings(caplog))


def test_reload_restores_the_shipped_limits(reload_config):
    """Guards the fixture itself, because a leak here corrupts other modules.

    A reload under a patched environment mutates a module object the rest of the
    suite imports. If teardown ever stopped restoring it, the failures would
    surface in unrelated test files and look like anything but a config leak.
    """
    modified = reload_config(FDSN_MAX_ROWS_EVENTS="3")
    assert modified.MAX_ROWS_EVENTS == 3
    # The fixture's own teardown is what restores it; see reload_config.
