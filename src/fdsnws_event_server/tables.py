"""Tabular serialization of station-level QuakeML collections.

Arrivals, station magnitudes and amplitudes are collections with one entry per
station reading, and they are the reason this server used to overflow an LLM's
context window: serialized as a list of full QuakeML dicts, one INGV Mw 6.1
event's arrivals came to 805 kB, about 343k tokens against a 32k window. The
same information as ``columns`` + ``rows`` is 87 kB, because a table states each
field name once instead of once per row and drops the repeated ``smi:`` URIs.

Three rules shape everything here, and they pull against each other:

  1. the result has to fit a context budget (see ``config``);
  2. nothing present in the datacenter's QuakeML may become unreachable;
  3. behaviour must not branch on which datacenter answered.

Rule 2 is why the column sets below are exhaustive rather than curated: every
field ObsPy models for the class is a column, with no judgement about what is
useful. Rule 1 is why identifiers are written as an envelope-level prefix plus a
per-row remainder. Rule 3 is why a missing ``waveform_id`` is recovered by
walking QuakeML's own links rather than by knowing what EMSC omits.
"""

import json
import os
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Column sets
# ---------------------------------------------------------------------------
#
# Written out rather than generated from ObsPy's ``_property_dict`` at import.
# Generating them would track ObsPy silently: a field added by an upgrade would
# shift the advertised column set under a model that had already learned it, and
# a field removed would change it without anyone noticing. These lists are the
# published contract; ``test_columns_cover_quakeml_classes`` asserts they still
# match ObsPy exactly, so the upgrade fails a test instead of shipping a
# surprise.
#
# Expansion conventions, applied uniformly:
#   waveform_id    -> network, station, location, channel
#   creation_info  -> agency, author, creation_time
#   <field>_errors -> <field>_uncertainty (only the uncertainty is ever populated
#                     in practice; the other three RealQuantity slots are dead
#                     weight on every row of every node measured)
#   resource_id    -> a name saying what it identifies, never the bare word

ARRIVAL_COLUMNS: tuple[str, ...] = (
    "arrival_id",
    "pick_id",
    "phase",
    "time_correction",
    "azimuth",
    "distance",
    "takeoff_angle",
    "takeoff_angle_uncertainty",
    "time_residual",
    "horizontal_slowness_residual",
    "backazimuth_residual",
    "time_weight",
    "horizontal_slowness_weight",
    "backazimuth_weight",
    "earth_model_id",
    "comments",
    "agency",
    "author",
    "creation_time",
    "pick_time",
    "pick_time_uncertainty",
    "pick_network",
    "pick_station",
    "pick_location",
    "pick_channel",
    "pick_filter_id",
    "pick_method_id",
    "pick_horizontal_slowness",
    "pick_horizontal_slowness_uncertainty",
    "pick_backazimuth",
    "pick_backazimuth_uncertainty",
    "pick_slowness_method_id",
    "pick_onset",
    "pick_phase_hint",
    "pick_polarity",
    "pick_evaluation_mode",
    "pick_evaluation_status",
    "pick_comments",
    "pick_agency",
    "pick_author",
    "pick_creation_time",
    "origin_id",
    "is_preferred_origin",
)

STATIONMAGNITUDE_COLUMNS: tuple[str, ...] = (
    "station_magnitude_id",
    "origin_id",
    "is_preferred_origin",
    "mag",
    "mag_uncertainty",
    "station_magnitude_type",
    "amplitude_id",
    "method_id",
    "network",
    "station",
    "location",
    "channel",
    "comments",
    "agency",
    "author",
    "creation_time",
    # The amplitude this reading was computed from, joined in so one row answers
    # "what was the ML at station X and from what amplitude". Null wherever the
    # node does not publish <amplitudeID> -- EMSC hides that link in the
    # publicID path, which is a private convention and not parsed (rule 3).
    "amp_generic_amplitude",
    "amp_period",
    "amp_unit",
)

AMPLITUDE_COLUMNS: tuple[str, ...] = (
    "amplitude_id",
    "generic_amplitude",
    "generic_amplitude_uncertainty",
    "type",
    "category",
    "unit",
    "method_id",
    "period",
    "period_uncertainty",
    "snr",
    "time_window",
    "pick_id",
    "network",
    "station",
    "location",
    "channel",
    "filter_id",
    "scaling_time",
    "scaling_time_uncertainty",
    "magnitude_hint",
    "evaluation_mode",
    "evaluation_status",
    "comments",
    "agency",
    "author",
    "creation_time",
)

# The fourteen columns of the FDSN 1.2 text profile, in spec order. Datacenters
# spell their own header differently -- INGV writes "Depth/Km", EMSC, GFZ and
# USGS write "Depth/km", and EventType is absent from EMSC and USGS entirely --
# so passing the header through made the column names depend on who answered.
EVENT_COLUMNS: tuple[str, ...] = (
    "event_id",
    "time",
    "latitude",
    "longitude",
    "depth_km",
    "author",
    "catalog",
    "contributor",
    "contributor_id",
    "mag_type",
    "magnitude",
    "mag_author",
    "location_name",
    "event_type",
)

_EVENT_HEADER_MAP = {
    "eventid": "event_id",
    "time": "time",
    "latitude": "latitude",
    "longitude": "longitude",
    "depth/km": "depth_km",
    "author": "author",
    "catalog": "catalog",
    "contributor": "contributor",
    "contributorid": "contributor_id",
    "magtype": "mag_type",
    "magnitude": "magnitude",
    "magauthor": "mag_author",
    "eventlocationname": "location_name",
    "eventtype": "event_type",
}

# Columns whose values are QuakeML resource identifiers. These carry the shared
# prefix that ``strip_id_prefixes`` lifts into the envelope.
_ID_COLUMNS = frozenset(
    {
        "arrival_id",
        "pick_id",
        "earth_model_id",
        "pick_filter_id",
        "pick_method_id",
        "pick_slowness_method_id",
        "origin_id",
        "station_magnitude_id",
        "amplitude_id",
        "method_id",
        "filter_id",
    }
)


def normalize_event_columns(columns: list[str]) -> list[str]:
    """Map a datacenter's text header onto the FDSN 1.2 column names.

    An unrecognised column is lowercased and passed through rather than dropped:
    a node that publishes an extra field must not lose it (rule 2), and we
    cannot know in advance what it will be called.
    """
    out = []
    for raw in columns:
        key = raw.strip().lstrip("#").replace(" ", "").replace("_", "").lower()
        out.append(_EVENT_HEADER_MAP.get(key, raw.strip().lstrip("#").lower()))
    return out


# ---------------------------------------------------------------------------
# Value extraction
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> Any:
    """Reduce an ObsPy attribute to something json can serialize.

    Numbers, booleans and None pass through untouched so the table keeps its
    types; everything else -- UTCDateTime, ResourceIdentifier, enums -- becomes
    its string form, which for all three is the QuakeML spelling.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _uncertainty(errors: Any) -> Optional[float]:
    """The uncertainty out of an ObsPy ``*_errors`` QuantityError, if set."""
    return getattr(errors, "uncertainty", None) if errors is not None else None


def _comments(obj: Any) -> Optional[list[str]]:
    """Comment texts as a plain list, or None when there are none.

    An empty list would cost two bytes on every row of every table to say
    nothing; None collapses to ``null``, which is what "no comments" means.
    """
    comments = getattr(obj, "comments", None)
    if not comments:
        return None
    return [c.text for c in comments if c.text]


def _creation(obj: Any) -> list[Any]:
    """agency, author, creation_time out of an ObsPy CreationInfo."""
    info = getattr(obj, "creation_info", None) if obj is not None else None
    if info is None:
        return [None, None, None]
    return [info.agency_id, info.author, _scalar(info.creation_time)]


def _waveform(obj: Any) -> list[Any]:
    """network, station, location, channel out of an ObsPy WaveformStreamID."""
    wid = getattr(obj, "waveform_id", None) if obj is not None else None
    if wid is None:
        return [None, None, None, None]
    return [wid.network_code, wid.station_code, wid.location_code, wid.channel_code]


def _has_station(values: list[Any]) -> bool:
    """True when a waveform quadruple carries any identification at all."""
    return any(v for v in values)


# ---------------------------------------------------------------------------
# Station identity, recovered through the QuakeML graph
# ---------------------------------------------------------------------------


def resolve_amplitude_waveform(amplitude: Any, picks_by_id: dict) -> list[Any]:
    """Station of an amplitude: its own waveform_id, else its pick's.

    INGV publishes <waveformID> on every amplitude and no <pickID>; EMSC does
    the exact opposite, on all 274 amplitudes of the event measured. Both are
    standard QuakeML, so following the link costs nothing and no code has to
    know which node is on the other end (rule 3).
    """
    own = _waveform(amplitude)
    if _has_station(own):
        return own
    pick = picks_by_id.get(str(amplitude.pick_id)) if amplitude.pick_id else None
    return _waveform(pick) if pick is not None else [None, None, None, None]


def resolve_station_magnitude_waveform(
    station_magnitude: Any, amplitudes_by_id: dict, picks_by_id: dict
) -> list[Any]:
    """Station of a station magnitude: its own waveform_id, else its amplitude's.

    Same shape as the amplitude case, one hop longer. Ends up null on EMSC,
    whose station magnitudes carry neither <waveformID> nor <amplitudeID>: the
    link to the amplitude is encoded in the publicID path instead, and parsing
    a node's private identifier convention is exactly what rule 3 forbids.
    """
    own = _waveform(station_magnitude)
    if _has_station(own):
        return own
    amplitude = (
        amplitudes_by_id.get(str(station_magnitude.amplitude_id))
        if station_magnitude.amplitude_id
        else None
    )
    if amplitude is None:
        return [None, None, None, None]
    return resolve_amplitude_waveform(amplitude, picks_by_id)


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def build_arrival_rows(event: Any) -> list[list[Any]]:
    """One row per arrival, across every origin the fetch delivered.

    Not just the preferred origin. On EMSC an event comes back with six origins
    carrying 137 preferred arrivals and 137 more elsewhere, so filtering to the
    preferred one would silently halve the answer (rule 2). On INGV every
    arrival already sits on the preferred origin, so the rule costs nothing
    there. ``is_preferred_origin`` lets the caller narrow it back down.
    """
    picks_by_id = {str(p.resource_id): p for p in event.picks}
    preferred = str(event.preferred_origin_id) if event.preferred_origin_id else None

    rows: list[list[Any]] = []
    for origin in event.origins:
        origin_id = str(origin.resource_id)
        for arrival in origin.arrivals:
            pick = picks_by_id.get(str(arrival.pick_id)) if arrival.pick_id else None
            rows.append(
                [
                    _scalar(arrival.resource_id),
                    _scalar(arrival.pick_id),
                    arrival.phase,
                    arrival.time_correction,
                    arrival.azimuth,
                    arrival.distance,
                    arrival.takeoff_angle,
                    _uncertainty(arrival.takeoff_angle_errors),
                    arrival.time_residual,
                    arrival.horizontal_slowness_residual,
                    arrival.backazimuth_residual,
                    arrival.time_weight,
                    arrival.horizontal_slowness_weight,
                    arrival.backazimuth_weight,
                    _scalar(arrival.earth_model_id),
                    _comments(arrival),
                    *_creation(arrival),
                    _scalar(pick.time) if pick is not None else None,
                    _uncertainty(pick.time_errors) if pick is not None else None,
                    *_waveform(pick),
                    _scalar(pick.filter_id) if pick is not None else None,
                    _scalar(pick.method_id) if pick is not None else None,
                    pick.horizontal_slowness if pick is not None else None,
                    _uncertainty(pick.horizontal_slowness_errors) if pick is not None else None,
                    pick.backazimuth if pick is not None else None,
                    _uncertainty(pick.backazimuth_errors) if pick is not None else None,
                    _scalar(pick.slowness_method_id) if pick is not None else None,
                    _scalar(pick.onset) if pick is not None else None,
                    pick.phase_hint if pick is not None else None,
                    _scalar(pick.polarity) if pick is not None else None,
                    _scalar(pick.evaluation_mode) if pick is not None else None,
                    _scalar(pick.evaluation_status) if pick is not None else None,
                    _comments(pick) if pick is not None else None,
                    *_creation(pick),
                    origin_id,
                    origin_id == preferred,
                ]
            )
    return rows


def build_station_magnitude_rows(event: Any) -> list[list[Any]]:
    """One row per station magnitude, with its amplitude joined in."""
    picks_by_id = {str(p.resource_id): p for p in event.picks}
    amplitudes_by_id = {str(a.resource_id): a for a in event.amplitudes}
    preferred = str(event.preferred_origin_id) if event.preferred_origin_id else None

    rows: list[list[Any]] = []
    for sm in event.station_magnitudes:
        amplitude = (
            amplitudes_by_id.get(str(sm.amplitude_id)) if sm.amplitude_id else None
        )
        origin_id = _scalar(sm.origin_id)
        rows.append(
            [
                _scalar(sm.resource_id),
                origin_id,
                origin_id == preferred if origin_id else None,
                sm.mag,
                _uncertainty(sm.mag_errors),
                sm.station_magnitude_type,
                _scalar(sm.amplitude_id),
                _scalar(sm.method_id),
                *resolve_station_magnitude_waveform(sm, amplitudes_by_id, picks_by_id),
                _comments(sm),
                *_creation(sm),
                amplitude.generic_amplitude if amplitude is not None else None,
                amplitude.period if amplitude is not None else None,
                amplitude.unit if amplitude is not None else None,
            ]
        )
    return rows


def build_amplitude_rows(event: Any) -> list[list[Any]]:
    """One row per amplitude.

    Amplitudes hang off the Event in QuakeML, not off an Origin: they carry no
    originID and, on INGV, no pickID either. There is no origin to filter them
    by, so all of them are returned. Of 2152 amplitudes on the INGV event
    measured, only 677 are referenced by a station magnitude; restricting to
    those would have dropped 69% of the data on the floor.
    """
    picks_by_id = {str(p.resource_id): p for p in event.picks}

    rows: list[list[Any]] = []
    for amp in event.amplitudes:
        rows.append(
            [
                _scalar(amp.resource_id),
                amp.generic_amplitude,
                _uncertainty(amp.generic_amplitude_errors),
                amp.type,
                _scalar(amp.category),
                amp.unit,
                _scalar(amp.method_id),
                amp.period,
                _uncertainty(amp.period_errors),
                amp.snr,
                _time_window(amp.time_window),
                _scalar(amp.pick_id),
                *resolve_amplitude_waveform(amp, picks_by_id),
                _scalar(amp.filter_id),
                _scalar(amp.scaling_time),
                _uncertainty(amp.scaling_time_errors),
                _scalar(amp.magnitude_hint),
                _scalar(amp.evaluation_mode),
                _scalar(amp.evaluation_status),
                _comments(amp),
                *_creation(amp),
            ]
        )
    return rows


def _time_window(window: Any) -> Optional[dict]:
    """An ObsPy TimeWindow as a small dict, or None when absent."""
    if window is None:
        return None
    out = {
        "begin": window.begin,
        "end": window.end,
        "reference": _scalar(window.reference),
    }
    return out if any(v is not None for v in out.values()) else None


# ---------------------------------------------------------------------------
# Identifier prefixes
# ---------------------------------------------------------------------------


def strip_id_prefixes(
    columns: tuple[str, ...], rows: list[list[Any]]
) -> tuple[list[list[Any]], dict[str, str]]:
    """Lift each id column's shared prefix into a map, leaving remainders in rows.

    QuakeML identifiers are URIs and nearly all of each one is boilerplate: an
    INGV pick id is 59 characters of which 44 are the same query endpoint on
    every row. Four such columns cost ~240 bytes per row, which is why the full
    arrival table measured 494 B/row before this and 260 B/row after -- the
    difference between 124 and 236 rows inside the budget.

    This is a pure encoding, not an omission: prefix + remainder is the original
    identifier, character for character, which is what rule 2 requires of any
    shortening. There is no portable alternative -- INGV ids end in a unique
    number, EMSC ids end in "/pick/1" which repeats across origins, and GFZ ids
    are opaque -- so a "short form" would have been lossy on two nodes out of
    three.

    A prefix is only worth declaring if it actually saves more than it costs, so
    single-value columns and short prefixes are left alone.
    """
    prefixes: dict[str, str] = {}
    indices = [i for i, name in enumerate(columns) if name in _ID_COLUMNS]

    for i in indices:
        values = [row[i] for row in rows if isinstance(row[i], str)]
        if len(values) < 2:
            continue
        prefix = os.path.commonprefix(values)
        # Below this the map entry costs more than the rows save.
        if len(prefix) <= len(columns[i]) + 8:
            continue
        prefixes[columns[i]] = prefix

    if not prefixes:
        return rows, {}

    cut = {i: len(prefixes[columns[i]]) for i in indices if columns[i] in prefixes}
    stripped = [
        [
            value[cut[j] :] if (j in cut and isinstance(value, str)) else value
            for j, value in enumerate(row)
        ]
        for row in rows
    ]
    return stripped, prefixes


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def _sort_key_factory(columns: tuple[str, ...], names: tuple[str, ...]):
    """A key function reading the named columns, with None sorting last."""
    idx = [columns.index(n) for n in names]

    def key(row):
        out = []
        for i in idx:
            value = row[i]
            out.append((value is None, value if value is not None else ""))
        return tuple(out)

    return key


def order_arrival_rows(rows: list[list[Any]]) -> tuple[list[list[Any]], str]:
    """Sort arrivals and report the ordering used.

    Distance from the hypocentre is the order a seismologist reads a phase list
    in, so it wins when every row has one. EMSC and GFZ populate it; INGV does
    on reviewed bulletins but not on fresh automatic solutions, and a partial
    sort would be arbitrary, so time is the fallback.

    Preferred-origin rows come first either way: with every origin now in the
    table (rule 2), the answer to the plain question has to be at the top.

    The tiebreak matters more than it looks. Offset pagination is only coherent
    if the order is total, and the same pick can appear in several rows with an
    identical time when different origins associate it, so ties are broken on
    pick and then on the arrival's own id.
    """
    columns = ARRIVAL_COLUMNS
    has_distance = rows and all(
        row[columns.index("distance")] is not None for row in rows
    )
    primary = "distance" if has_distance else "pick_time"
    key = _sort_key_factory(columns, (primary, "pick_id", "arrival_id"))
    preferred_idx = columns.index("is_preferred_origin")
    ordered = sorted(rows, key=lambda r: (not r[preferred_idx], key(r)))
    return ordered, f"is_preferred_origin, {primary}"


def order_station_rows(
    rows: list[list[Any]], columns: tuple[str, ...], id_column: str
) -> tuple[list[list[Any]], str]:
    """Sort station magnitudes or amplitudes by station, then by id."""
    key = _sort_key_factory(columns, ("network", "station", "channel", id_column))
    return sorted(rows, key=key), "network, station, channel"


# ---------------------------------------------------------------------------
# Pagination and the size guard
# ---------------------------------------------------------------------------


class RowTooLargeError(Exception):
    """A single row does not fit the byte budget, so no page can be built."""


def trim_to_budget(
    envelope: dict,
    columns: tuple[str, ...],
    page: list[list[Any]],
    max_bytes: int,
) -> list[list[Any]]:
    """Drop rows off the end of a page until the whole response fits.

    The row maxima in ``config`` are set from the widest rows measured, so this
    should normally do nothing. It exists for the page that is denser than any
    of those: a station with a long comment, an event whose location name runs
    on, a node that starts spelling identifiers differently. Trimming keeps the
    call succeeding -- the caller just pages once more -- where refusing would
    hand back an error it cannot act on.

    Raises ``RowTooLargeError`` when even one row will not fit, which no amount
    of paging can fix.
    """
    while page:
        candidate = {**envelope, "columns": list(columns), "rows": page}
        size = len(json.dumps(candidate, separators=(",", ":"), default=str))
        if size <= max_bytes:
            return page
        if len(page) == 1:
            # Separate the fixed cost from the row's own. A table carries its
            # envelope and its full column list whatever the page holds -- over a
            # kilobyte for arrivals, against a row of a few hundred bytes -- so
            # blaming "a single row" for the overrun points the reader at the
            # data when the budget is what is too small, and no filter or
            # narrower query can shrink either part.
            empty = len(
                json.dumps(
                    {**envelope, "columns": list(columns), "rows": []},
                    separators=(",", ":"), default=str,
                )
            )
            raise RowTooLargeError(
                f"A one-row response needs {size} bytes, over the {max_bytes} "
                f"byte budget: {empty} bytes of envelope and column names that "
                f"every page carries, plus {size - empty} for the row. Raise "
                f"FDSN_MAX_RESULT_BYTES above {size}."
            )
        # Scale to the overshoot rather than dropping one row at a time: a page
        # at twice the budget converges in one step instead of hundreds.
        keep = max(1, int(len(page) * max_bytes / size))
        page = page[: keep if keep < len(page) else len(page) - 1]
    return page


def paginate(
    envelope: dict,
    columns: tuple[str, ...],
    rows: list[list[Any]],
    *,
    limit: int,
    offset: int,
    max_bytes: int,
) -> dict:
    """Cut a page out of ``rows`` and fit it inside the byte budget.

    Two mechanisms, in order. ``limit`` is the contract the caller sees and the
    schema advertises; the byte budget is the backstop underneath it. The budget
    shrinks the page rather than failing the call, so ``returned_count`` simply
    comes back below ``limit`` with ``has_more`` set and a ``next_offset`` that
    still lines up.

    ``offset`` is 1-based, matching ADR-0003's vocabulary. Unlike the event
    query it is applied here, to rows this server already holds, so the INGV
    off-by-one that ADR-0003 documents cannot arise.
    """
    total = len(rows)
    start = max(offset - 1, 0)

    # Trim against the envelope the response will really carry, pagination keys
    # included. Measuring the bare envelope instead let the finished payload run
    # past the budget by the width of those keys -- small, but it made the
    # advertised ceiling a soft one, and a budget that is only approximately
    # honoured is the kind of thing that stops being harmless the day someone
    # lowers it.
    accounted = {
        **envelope,
        "total_count": total,
        "returned_count": limit,
        "limit": limit,
        "offset": offset,
        "has_more": True,
        "next_offset": offset + limit,
    }
    page = trim_to_budget(accounted, columns, rows[start : start + limit], max_bytes)

    returned = len(page)
    has_more = start + returned < total
    out = {
        **envelope,
        "total_count": total,
        "returned_count": returned,
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "columns": list(columns),
        "rows": page,
    }
    if has_more:
        out["next_offset"] = offset + returned
    return out
