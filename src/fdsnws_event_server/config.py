"""Runtime limits, read from the environment once at import time.

These are not ordinary runtime knobs. Every ``MAX_ROWS_*`` value below becomes a
Pydantic ``Field(le=...)`` constraint on a tool's input model, and Pydantic bakes
those constraints into the JSON schema this server publishes in ``tools/list``.
The numbers are therefore part of what the model is *told* it may ask for, not
just what the server happens to honour, so they have to be settled before the
tool models are constructed -- which means at import, not per request.

The defaults are derived from a context budget rather than chosen, and both
halves of that derivation are measured rather than assumed.

The density of this JSON is 1.87 bytes per token. That is not an estimate: it is
the slope of prompt_eval_count against payload size on the deployed model
(qwen3.8:27b on Ollama), 1511 bytes costing 476 tokens and 48 667 costing
25 744, linear in between. Guessing it was the more expensive mistake of the
two. An earlier 2.4 bytes/token assumption made a 60 000 byte budget look like
25k tokens and therefore comfortable; at the real density it is 32 150 tokens,
98% of a 32k context window -- the entire thing, leaving nothing for the
question being answered. That is a milder form of the failure this whole module
exists to prevent.

``MAX_RESULT_BYTES`` of 36 000 is about 19 250 tokens, 59% of a 32k window, so
the conversation the result has to live inside still has room.

Each table's maximum is that budget divided by the widest row *the finished tool
actually emitted*, measured on real responses from INGV, EMSC, GFZ and USGS
rather than estimated from the row builders. Estimating that was the other
mistake: an estimate ignoring the envelope and the id-prefix map put arrivals at
180, and the guard then trimmed every full page back to 170. A maximum the
server cannot honour is a lie told to the model in the tool schema, so the
maxima sit below the worst measured row instead:

    arrival            391 B/row (GFZ)   ->   90
    station magnitude  159 B/row (INGV)  ->  220
    amplitude          279 B/row (INGV)  ->  125
    event              149 B/row (INGV)  ->  240

GFZ sets the arrival worst case because its identifiers are long opaque strings
that share almost no common prefix, so little can be lifted out of the rows.

These numbers are calibrated for a 32k window because that is what the
deployment this release was written for runs. A larger window is exactly what
``FDSN_MAX_RESULT_BYTES`` and the per-table maxima are there to be raised for.

MAX_RESULT_BYTES itself is the backstop for a page denser than any of these, not
the first line of defence: see ``tables.paginate``.
"""

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 45.0


def _int_env(name: str, default: int) -> int:
    """Read a positive integer from the environment, falling back on nonsense.

    A bad value must not take the server down at import: it degrades to the
    default and says so, the same contract ``FDSN_TIMEOUT`` has always had.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default
    if value < 1:
        logger.warning("%s=%s must be >= 1, using default %s", name, value, default)
        return default
    return value


def get_timeout() -> float:
    """Request timeout in seconds, from the FDSN_TIMEOUT env var (default 45).

    Unlike the row limits this one is read per call: it shapes no schema, so
    there is nothing to freeze at import and an operator can change it without
    the advertised tool contract shifting underneath the model.
    """
    raw = os.environ.get("FDSN_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid FDSN_TIMEOUT=%r, using default %s", raw, DEFAULT_TIMEOUT)
        return DEFAULT_TIMEOUT


MAX_RESULT_BYTES = _int_env("FDSN_MAX_RESULT_BYTES", 36_000)

MAX_ROWS_ARRIVALS = _int_env("FDSN_MAX_ROWS_ARRIVALS", 90)
MAX_ROWS_STATIONMAGNITUDES = _int_env("FDSN_MAX_ROWS_STATIONMAGNITUDES", 220)
MAX_ROWS_AMPLITUDES = _int_env("FDSN_MAX_ROWS_AMPLITUDES", 125)
MAX_ROWS_EVENTS = _int_env("FDSN_MAX_ROWS_EVENTS", 240)

DEFAULT_ROWS_ARRIVALS = _int_env("FDSN_DEFAULT_ROWS_ARRIVALS", 90)
DEFAULT_ROWS_STATIONMAGNITUDES = _int_env("FDSN_DEFAULT_ROWS_STATIONMAGNITUDES", 200)
DEFAULT_ROWS_AMPLITUDES = _int_env("FDSN_DEFAULT_ROWS_AMPLITUDES", 125)
DEFAULT_ROWS_EVENTS = _int_env("FDSN_DEFAULT_ROWS_EVENTS", 100)


def _clamp_default(name: str, default: int, maximum: int) -> int:
    """Keep a default at or below its own maximum.

    The two are independent env vars, so an operator can lower a maximum and
    forget the matching default. Pydantic would then reject its own field
    default and the tool would fail to build at import -- the whole server dead
    on a config typo. Clamping keeps it alive and loud instead.
    """
    if default > maximum:
        logger.warning(
            "%s=%s exceeds its maximum %s, clamping to %s", name, default, maximum, maximum
        )
        return maximum
    return default


DEFAULT_ROWS_ARRIVALS = _clamp_default(
    "FDSN_DEFAULT_ROWS_ARRIVALS", DEFAULT_ROWS_ARRIVALS, MAX_ROWS_ARRIVALS
)
DEFAULT_ROWS_STATIONMAGNITUDES = _clamp_default(
    "FDSN_DEFAULT_ROWS_STATIONMAGNITUDES",
    DEFAULT_ROWS_STATIONMAGNITUDES,
    MAX_ROWS_STATIONMAGNITUDES,
)
DEFAULT_ROWS_AMPLITUDES = _clamp_default(
    "FDSN_DEFAULT_ROWS_AMPLITUDES", DEFAULT_ROWS_AMPLITUDES, MAX_ROWS_AMPLITUDES
)
DEFAULT_ROWS_EVENTS = _clamp_default(
    "FDSN_DEFAULT_ROWS_EVENTS", DEFAULT_ROWS_EVENTS, MAX_ROWS_EVENTS
)
