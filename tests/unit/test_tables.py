"""Unit tests for the tabular serializer (offline, real captured QuakeML).

The design these tests guard is documented in
``docs/design/2026-09-context-budgeted-outputs.md``. Three constraints pull
against each other there -- fit a context budget (C1), leave no data hole (C2),
behave identically on every FDSN node (C3) -- and almost every test below exists
because one of them can be broken silently: a column quietly dropped, an
identifier quietly shortened, a station quietly left null, a page quietly
overlapping the previous one. None of those raise on their own.
"""

import json
from pathlib import Path

import pytest
from obspy import read_events
from obspy.core.event import Amplitude, Arrival, Pick, StationMagnitude

from fdsnws_event_server import tables
from fdsnws_event_server.obspy_client import parse_fdsn_text

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# The captured events, and what each one is here to prove. Together they cover
# every branch the datacenter-agnostic rules have to absorb (C3).
FIXTURE_FILES = {
    # 1 origin, waveform_id on every amplitude and station magnitude.
    "ingv": "ingv_arrivals.quakeml.xml",
    # 10 origins (multi-origin ordering), no waveform_id anywhere: amplitudes
    # recover the station through pick_id, station magnitudes cannot recover it
    # at all.
    "emsc": "emsc_arrivals.quakeml.xml",
    # 1 origin, arrivals only -- no station magnitudes, no amplitudes.
    "gfz": "gfz_arrivals.quakeml.xml",
    # The empty case: an event with no station-level collections at all.
    "empty": "ingv_no_arrivals.quakeml.xml",
}

# Reading the EMSC file costs seconds, so it is parsed once for the whole module.


@pytest.fixture(scope="module")
def events():
    return {
        name: read_events(str(FIXTURES / filename))[0]
        for name, filename in FIXTURE_FILES.items()
    }


# The three tables, each as (columns, row builder). Parametrizing on this keeps
# the contract tests from silently covering only the table someone remembered.
TABLES = {
    "arrivals": (tables.ARRIVAL_COLUMNS, tables.build_arrival_rows),
    "station_magnitudes": (
        tables.STATIONMAGNITUDE_COLUMNS,
        tables.build_station_magnitude_rows,
    ),
    "amplitudes": (tables.AMPLITUDE_COLUMNS, tables.build_amplitude_rows),
}

ALL_COMBINATIONS = [(f, t) for f in FIXTURE_FILES for t in TABLES]


# ---------------------------------------------------------------------------
# 1. Column contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "columns",
    [
        tables.ARRIVAL_COLUMNS,
        tables.STATIONMAGNITUDE_COLUMNS,
        tables.AMPLITUDE_COLUMNS,
        tables.EVENT_COLUMNS,
    ],
    ids=["arrival", "station_magnitude", "amplitude", "event"],
)
def test_column_names_are_unique(columns):
    """A duplicated name would make one of the two columns unaddressable.

    Rows are positional, so a repeated header is not an error anywhere in the
    pipeline -- the consumer just cannot tell which position it is looking at,
    and one of the two fields becomes unreachable (C2).
    """
    assert len(columns) == len(set(columns))


@pytest.mark.parametrize("fixture_name,table_name", ALL_COMBINATIONS)
def test_every_row_matches_its_column_count(events, fixture_name, table_name):
    """A row of the wrong arity misaligns every value after the gap.

    The builders splat helpers that return lists (``_waveform``, ``_creation``),
    so a helper that returned three values instead of four would shift the rest
    of the row rather than fail. Checked on every fixture because the builders
    take different branches per node.
    """
    columns, build = TABLES[table_name]
    rows = build(events[fixture_name])
    assert all(len(row) == len(columns) for row in rows)


def test_row_counts_match_the_captured_fixtures(events):
    """Pins what each fixture actually contains, so later tests mean something.

    Every assertion below about resolved-vs-null stations or page walks is read
    against these totals; if a fixture were replaced, those tests would keep
    passing while measuring a different event.
    """
    assert len(tables.build_arrival_rows(events["ingv"])) == 150
    assert len(tables.build_station_magnitude_rows(events["ingv"])) == 575
    assert len(tables.build_amplitude_rows(events["ingv"])) == 1235

    assert len(events["emsc"].origins) == 10
    assert len(tables.build_arrival_rows(events["emsc"])) == 632
    assert len(tables.build_station_magnitude_rows(events["emsc"])) == 225
    assert len(tables.build_amplitude_rows(events["emsc"])) == 632

    assert len(tables.build_arrival_rows(events["gfz"])) == 125
    assert tables.build_station_magnitude_rows(events["gfz"]) == []
    assert tables.build_amplitude_rows(events["gfz"]) == []

    assert tables.build_arrival_rows(events["empty"]) == []
    assert tables.build_station_magnitude_rows(events["empty"]) == []
    assert tables.build_amplitude_rows(events["empty"]) == []


# ---------------------------------------------------------------------------
# 2. The guard against a silent ObsPy upgrade
# ---------------------------------------------------------------------------


def _expand(field: str, prefix: str = "") -> set[str]:
    """The module's documented expansion conventions, applied to one field."""
    if field == "waveform_id":
        return {prefix + n for n in ("network", "station", "location", "channel")}
    if field == "creation_info":
        return {prefix + n for n in ("agency", "author", "creation_time")}
    if field.endswith("_errors"):
        return {prefix + field[: -len("_errors")] + "_uncertainty"}
    return {prefix + field}


def _fields(cls) -> list[str]:
    """Every attribute ObsPy models for the class: scalars plus containers.

    ``comments`` is a container, not a property, so ``_property_dict`` alone
    would understate the class by one field and the guard below would pass while
    the comment column was unaccounted for.
    """
    return list(cls._property_dict.keys()) + list(cls._containers)


def _expected_columns(cls, id_name: str, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for field in _fields(cls):
        if field == "resource_id":
            out.add(id_name)
            continue
        out |= _expand(field, prefix)
    return out


def test_columns_cover_quakeml_classes():
    """The hand-written column lists must still match ObsPy's classes exactly.

    This is the whole reason the lists are written out instead of generated from
    ``_property_dict`` at import. Generated, an ObsPy upgrade that adds a field
    would silently change the advertised column set under a model that had
    already learned it; written out, the new field would simply never be
    serialized and the data would disappear without a sound (C2).

    So this test is the alarm. If an ObsPy upgrade adds, removes or renames a
    field on Arrival, Pick, StationMagnitude or Amplitude, this fails loudly at
    test time -- which is the point -- instead of shipping a table that quietly
    drops what the datacenter sent. Fixing it means updating the column tuple
    and the row builder together, never loosening the assertion.

    Equality is asserted in both directions. A subset check would let a new
    ObsPy field through, which is exactly the failure being guarded.
    """
    # Arrivals: the arrival's own fields, plus the joined pick's under "pick_".
    # Pick.resource_id is not a separate column: it is the same identifier as
    # Arrival.pick_id, already carried once.
    expected_arrival = _expected_columns(Arrival, "arrival_id")
    expected_arrival |= _expected_columns(Pick, "pick_id", prefix="pick_")
    # Not Arrival fields: the origin the arrival hangs off, and the flag that
    # lets a caller narrow the multi-origin table back down (design D3).
    expected_arrival |= {"origin_id", "is_preferred_origin"}
    assert set(tables.ARRIVAL_COLUMNS) == expected_arrival

    expected_stationmagnitude = _expected_columns(
        StationMagnitude, "station_magnitude_id"
    )
    # Not StationMagnitude fields: the preferred-origin flag, and three columns
    # joined in from the amplitude the reading was computed from, so one row
    # answers "what was the ML at station X and from what amplitude".
    expected_stationmagnitude |= {
        "is_preferred_origin",
        "amp_generic_amplitude",
        "amp_period",
        "amp_unit",
    }
    assert set(tables.STATIONMAGNITUDE_COLUMNS) == expected_stationmagnitude

    # Amplitudes hang off the Event, not an Origin, so there is nothing extra.
    assert set(tables.AMPLITUDE_COLUMNS) == _expected_columns(
        Amplitude, "amplitude_id"
    )


# ---------------------------------------------------------------------------
# 3-4. Identifier prefixes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name,table_name", ALL_COMBINATIONS)
def test_strip_id_prefixes_round_trips(events, fixture_name, table_name):
    """prefix + remainder must rebuild the identifier character for character.

    Lifting the shared prefix out of the rows is an encoding, not an omission,
    and C2 allows a shortening only if it is reversible. Run on all three
    datacenters because their identifier shapes differ -- INGV ids end in a
    unique number, EMSC ids end in a suffix that repeats across origins, GFZ ids
    are opaque -- so a prefix bug can be invisible on one node and fatal on
    another.
    """
    columns, build = TABLES[table_name]
    rows = build(events[fixture_name])
    stripped, prefixes = tables.strip_id_prefixes(columns, rows)

    assert len(stripped) == len(rows)
    for original, encoded in zip(rows, stripped):
        assert len(encoded) == len(columns)
        for index, name in enumerate(columns):
            value = original[index]
            if name in prefixes and isinstance(value, str):
                assert prefixes[name] + encoded[index] == value
            else:
                # Everything not an id string, and every null, is untouched.
                assert encoded[index] == value


@pytest.mark.parametrize("fixture_name", ["ingv", "emsc", "gfz"])
def test_strip_id_prefixes_actually_fires_on_real_data(events, fixture_name):
    """The encoding must still engage on every node's real identifiers.

    Nothing fails if it stops. Raise the ``len(column) + 8`` threshold too far,
    or change how a node spells its identifiers, and the round-trip test still
    passes -- rows are simply returned whole and every response quietly grows
    back to its pre-encoding size, which on the arrival table was 494 B/row
    against 260 and the difference between 124 and 236 rows inside the budget.
    """
    rows = tables.build_arrival_rows(events[fixture_name])
    _, prefixes = tables.strip_id_prefixes(tables.ARRIVAL_COLUMNS, rows)
    assert prefixes


def test_strip_id_prefixes_needs_at_least_two_values():
    """One value is not a shared prefix, it is the value itself.

    Declaring it would move the whole identifier into the envelope and save
    nothing, while making the row look truncated.
    """
    columns = ("pick_id", "phase")
    long_id = "smi:webservices.ingv.it/fdsnws/event/1/query?pickid=" + "1" * 40
    rows = [[long_id, "P"], [None, "S"]]
    stripped, prefixes = tables.strip_id_prefixes(columns, rows)
    assert prefixes == {}
    assert stripped == rows


def test_strip_id_prefixes_ignores_a_prefix_that_does_not_pay_for_itself():
    """Below len(column) + 8 the envelope entry costs more than the rows save.

    Tested exactly on the boundary in both directions, because an off-by-one
    here is a silent size regression rather than a failure: the map entry is
    written, every row is shortened, and the response gets bigger.
    """
    name = "pick_id"
    threshold = len(name) + 8  # 15

    at_threshold = "x" * threshold
    rows = [[at_threshold + "a"], [at_threshold + "b"]]
    stripped, prefixes = tables.strip_id_prefixes((name,), rows)
    assert prefixes == {}
    assert stripped == rows

    over_threshold = "x" * (threshold + 1)
    rows = [[over_threshold + "a"], [over_threshold + "b"]]
    stripped, prefixes = tables.strip_id_prefixes((name,), rows)
    assert prefixes == {name: over_threshold}
    assert stripped == [["a"], ["b"]]


def test_strip_id_prefixes_leaves_non_id_columns_alone():
    """Only the columns known to hold QuakeML URIs may be encoded.

    A station name or a phase hint that happened to share a long prefix is data,
    not boilerplate, and moving it into the envelope would change its meaning.
    """
    columns = ("phase", "station")
    rows = [["PgPgPgPgPgPgPgPgPgPgPg1"], ["PgPgPgPgPgPgPgPgPgPgPg2"]]
    rows = [[r[0], r[0]] for r in rows]
    stripped, prefixes = tables.strip_id_prefixes(columns, rows)
    assert prefixes == {}
    assert stripped == rows


# ---------------------------------------------------------------------------
# 5. Station identity, recovered through the QuakeML graph
# ---------------------------------------------------------------------------


def _picks_by_id(event):
    return {str(p.resource_id): p for p in event.picks}


def _amplitudes_by_id(event):
    return {str(a.resource_id): a for a in event.amplitudes}


def test_amplitude_waveform_uses_its_own_when_present(events):
    """INGV publishes waveformID on every amplitude: the first branch wins.

    If the fallback ever ran first, the station would still be filled in and
    nothing would look wrong -- the values would just come from the wrong side
    of the link on any node where the two disagree.
    """
    event = events["ingv"]
    picks = _picks_by_id(event)
    amplitude = event.amplitudes[0]
    assert amplitude.waveform_id is not None
    resolved = tables.resolve_amplitude_waveform(amplitude, picks)
    assert resolved == [
        amplitude.waveform_id.network_code,
        amplitude.waveform_id.station_code,
        amplitude.waveform_id.location_code,
        amplitude.waveform_id.channel_code,
    ]


def test_amplitude_waveform_falls_back_to_the_pick(events):
    """EMSC publishes pickID and no waveformID: the station comes one hop away.

    Both shapes are standard QuakeML, so the recovery walks the specification's
    own link rather than knowing what EMSC omits (C3).
    """
    event = events["emsc"]
    picks = _picks_by_id(event)
    amplitude = event.amplitudes[0]
    assert amplitude.waveform_id is None
    assert amplitude.pick_id is not None
    pick = picks[str(amplitude.pick_id)]
    assert tables.resolve_amplitude_waveform(amplitude, picks) == [
        pick.waveform_id.network_code,
        pick.waveform_id.station_code,
        pick.waveform_id.location_code,
        pick.waveform_id.channel_code,
    ]


def test_amplitude_waveform_is_null_without_either_link():
    """No waveformID and no pickID is a legitimate answer of null, not a crash.

    A node is allowed to publish an amplitude with neither, and the column set
    is fixed, so the four station columns have to come back as nulls of the
    right arity.
    """
    assert tables.resolve_amplitude_waveform(Amplitude(), {}) == [
        None,
        None,
        None,
        None,
    ]


def test_station_magnitude_waveform_uses_its_own_when_present(events):
    """INGV station magnitudes carry waveformID directly."""
    event = events["ingv"]
    sm = event.station_magnitudes[0]
    assert sm.waveform_id is not None
    resolved = tables.resolve_station_magnitude_waveform(
        sm, _amplitudes_by_id(event), _picks_by_id(event)
    )
    assert resolved == [
        sm.waveform_id.network_code,
        sm.waveform_id.station_code,
        sm.waveform_id.location_code,
        sm.waveform_id.channel_code,
    ]


def test_station_magnitude_waveform_hops_through_the_amplitude(events):
    """The amplitude hop is reachable on no captured fixture, so it is forced.

    INGV station magnitudes have their own waveformID (first branch) and EMSC
    have neither waveformID nor amplitudeID (null branch), which leaves the
    middle branch -- no waveformID but an amplitudeID -- untested by any real
    response. It is still the branch that a node publishing that combination
    would take, so the INGV reading is stripped of its waveformID here and the
    station must arrive through its amplitude.
    """
    event = events["ingv"]
    amplitudes = _amplitudes_by_id(event)
    picks = _picks_by_id(event)
    sm = [s for s in event.station_magnitudes if s.amplitude_id][0]
    amplitude = amplitudes[str(sm.amplitude_id)]

    stripped = sm.copy()
    stripped.waveform_id = None

    assert tables.resolve_station_magnitude_waveform(
        stripped, amplitudes, picks
    ) == tables.resolve_amplitude_waveform(amplitude, picks)
    assert tables._has_station(
        tables.resolve_station_magnitude_waveform(stripped, amplitudes, picks)
    )


def test_station_magnitude_waveform_is_null_when_the_link_is_private(events):
    """EMSC hides the station magnitude to amplitude link in the publicID path.

    That is a private identifier convention, and parsing it is exactly what C3
    forbids, so the station stays null and the envelope has to say so. Asserting
    the count rather than "some row is null" keeps a regression in either branch
    visible: if the fallback started guessing, this number would move.
    """
    event = events["emsc"]
    amplitudes = _amplitudes_by_id(event)
    picks = _picks_by_id(event)
    assert all(s.waveform_id is None for s in event.station_magnitudes)
    assert all(s.amplitude_id is None for s in event.station_magnitudes)
    assert all(
        tables.resolve_station_magnitude_waveform(s, amplitudes, picks)
        == [None, None, None, None]
        for s in event.station_magnitudes
    )


@pytest.mark.parametrize(
    "fixture_name,table_name,resolved,total",
    [
        ("ingv", "amplitudes", 1235, 1235),
        ("emsc", "amplitudes", 632, 632),  # every one through the pick hop
        ("ingv", "station_magnitudes", 575, 575),
        ("emsc", "station_magnitudes", 0, 225),  # link is private, stays null
    ],
)
def test_station_resolution_counts(events, fixture_name, table_name, resolved, total):
    """Exact resolved-vs-null counts per fixture, so a half-working hop shows.

    A regression that resolved most rows and dropped the rest would still look
    healthy under a "some rows are filled" assertion.
    """
    columns, build = TABLES[table_name]
    rows = build(events[fixture_name])
    index = columns.index("station")
    assert len(rows) == total
    assert sum(1 for row in rows if row[index] is not None) == resolved


# ---------------------------------------------------------------------------
# 6. Ordering
# ---------------------------------------------------------------------------


_PREFERRED = tables.ARRIVAL_COLUMNS.index("is_preferred_origin")
_DISTANCE = tables.ARRIVAL_COLUMNS.index("distance")
_PICK_TIME = tables.ARRIVAL_COLUMNS.index("pick_time")
_PICK_ID = tables.ARRIVAL_COLUMNS.index("pick_id")
_ARRIVAL_ID = tables.ARRIVAL_COLUMNS.index("arrival_id")


def test_order_arrival_rows_puts_the_preferred_origin_first(events):
    """With every origin in the table, the plain question is answered at the top.

    EMSC returns ten origins for one event and half its arrivals hang off the
    non-preferred ones; a caller that reads only the first page must still get
    the preferred solution rather than an arbitrary mixture.
    """
    rows = tables.build_arrival_rows(events["emsc"])
    ordered, _ = tables.order_arrival_rows(rows)
    flags = [row[_PREFERRED] for row in ordered]
    assert any(flags) and not all(flags)
    assert flags == sorted(flags, reverse=True)
    assert flags.count(True) == 316


def test_order_arrival_rows_reports_distance_when_every_row_has_one(events):
    """Distance is the order a phase list is read in, so it wins when complete."""
    rows = tables.build_arrival_rows(events["emsc"])
    ordered, ordered_by = tables.order_arrival_rows(rows)
    assert ordered_by == "is_preferred_origin, distance"
    preferred = [r for r in ordered if r[_PREFERRED]]
    assert [r[_DISTANCE] for r in preferred] == sorted(
        r[_DISTANCE] for r in preferred
    )


def test_order_arrival_rows_falls_back_to_pick_time_on_one_missing_distance(events):
    """A single missing distance is enough to disqualify the distance ordering.

    INGV populates distance on reviewed bulletins but not on fresh automatic
    solutions, and a sort that used it where present and something else where
    absent would be arbitrary. Only one row is emptied here, because that is
    what proves the condition is "every row" and not "no row".
    """
    rows = [list(r) for r in tables.build_arrival_rows(events["ingv"])]
    assert all(r[_DISTANCE] is not None for r in rows)
    rows[7][_DISTANCE] = None

    ordered, ordered_by = tables.order_arrival_rows(rows)
    assert ordered_by == "is_preferred_origin, pick_time"
    times = [r[_PICK_TIME] for r in ordered]
    assert times == sorted(times)


@pytest.mark.parametrize("fixture_name", ["ingv", "emsc", "gfz"])
def test_order_arrival_rows_is_a_total_order(events, fixture_name):
    """Offset paging is only coherent if no two rows compare equal.

    The same pick appears in several rows with an identical time when different
    origins associate it, so without the pick and arrival tiebreaks ``sorted``
    would be free to interleave them differently between two calls -- and a page
    walk would then duplicate some rows and skip others, with no error anywhere.
    """
    rows = tables.build_arrival_rows(events[fixture_name])
    ordered, ordered_by = tables.order_arrival_rows(rows)
    primary = _DISTANCE if ordered_by.endswith("distance") else _PICK_TIME
    keys = [
        (not r[_PREFERRED], r[primary], r[_PICK_ID], r[_ARRIVAL_ID]) for r in ordered
    ]
    assert len(set(map(str, keys))) == len(keys)
    # And the sort is stable under repetition: same input, same output.
    assert tables.order_arrival_rows(list(reversed(rows)))[0] == ordered


def test_order_arrival_rows_handles_an_empty_table(events):
    """No arrivals is a normal answer, and `all()` on an empty list is True.

    Without the explicit emptiness guard the distance branch would be chosen for
    a table that has no rows to order, advertising an ordering never applied.
    """
    ordered, ordered_by = tables.order_arrival_rows([])
    assert ordered == []
    assert ordered_by == "is_preferred_origin, pick_time"


def test_order_station_rows_sorts_by_station_then_id(events):
    """Station tables are read station by station, and nulls must not break it.

    EMSC leaves the whole station quadruple null, so the comparison has to cope
    with None in the primary key instead of raising a TypeError mid-sort.
    """
    rows = tables.build_station_magnitude_rows(events["ingv"])
    ordered, ordered_by = tables.order_station_rows(
        rows, tables.STATIONMAGNITUDE_COLUMNS, "station_magnitude_id"
    )
    assert ordered_by == "network, station, channel"
    index = [
        tables.STATIONMAGNITUDE_COLUMNS.index(n)
        for n in ("network", "station", "channel")
    ]
    keys = [tuple(r[i] or "" for i in index) for r in ordered]
    assert keys == sorted(keys)

    emsc = tables.build_station_magnitude_rows(events["emsc"])
    ordered_emsc, _ = tables.order_station_rows(
        emsc, tables.STATIONMAGNITUDE_COLUMNS, "station_magnitude_id"
    )
    assert len(ordered_emsc) == len(emsc)


# ---------------------------------------------------------------------------
# 7. Pagination
# ---------------------------------------------------------------------------

_ROOMY = 10_000_000  # a budget large enough that only `limit` is in play


def _ordered_arrivals(event):
    return tables.order_arrival_rows(tables.build_arrival_rows(event))[0]


def test_paginate_offset_is_one_based(events):
    """ADR-0003's vocabulary: offset 1 is the first row, not the second.

    Applied here to rows this server already holds, so unlike the event query
    there is no upstream off-by-one to compensate for -- which means an
    off-by-one introduced here would be entirely ours.
    """
    rows = _ordered_arrivals(events["ingv"])
    page = tables.paginate(
        {}, tables.ARRIVAL_COLUMNS, rows, limit=5, offset=1, max_bytes=_ROOMY
    )
    assert page["rows"][0] == rows[0]
    assert page["offset"] == 1
    assert page["total_count"] == len(rows)
    assert page["returned_count"] == 5
    assert page["limit"] == 5
    assert page["columns"] == list(tables.ARRIVAL_COLUMNS)

    second = tables.paginate(
        {}, tables.ARRIVAL_COLUMNS, rows, limit=5, offset=6, max_bytes=_ROOMY
    )
    assert second["rows"][0] == rows[5]


def _walk(rows, columns, limit, max_bytes):
    """Follow next_offset from 1 and return every row the walk handed back."""
    collected: list = []
    offset = 1
    pages = 0
    while True:
        page = tables.paginate(
            {}, columns, rows, limit=limit, offset=offset, max_bytes=max_bytes
        )
        assert page["offset"] == offset
        assert page["returned_count"] == len(page["rows"])
        collected.extend(page["rows"])
        if not page["has_more"]:
            assert "next_offset" not in page
            break
        assert page["returned_count"] > 0
        assert page["next_offset"] == offset + page["returned_count"]
        offset = page["next_offset"]
        pages += 1
        assert pages < 5000, "next_offset is not advancing"
    return collected


def test_paginate_walk_covers_every_row_exactly_once(events):
    """The whole point of offset paging: no duplicate, no gap, a clean stop.

    Walked with a roomy budget so only limit/offset are exercised.
    """
    rows = _ordered_arrivals(events["emsc"])
    assert _walk(rows, tables.ARRIVAL_COLUMNS, limit=50, max_bytes=_ROOMY) == rows


def test_paginate_walk_stays_coherent_when_the_budget_shrinks_pages(events):
    """The two mechanisms have to compose, not just work separately.

    ``limit`` is the contract the model sees; the byte budget is the backstop
    underneath it. When the backstop cuts a page short, ``next_offset`` must
    follow the rows actually returned rather than the rows asked for -- get that
    wrong and the walk skips exactly the trimmed rows, silently.
    """
    rows = _ordered_arrivals(events["ingv"])
    walked = _walk(rows, tables.ARRIVAL_COLUMNS, limit=150, max_bytes=4000)
    assert walked == rows
    # The budget, not the limit, is what ended the first page.
    first = tables.paginate(
        {}, tables.ARRIVAL_COLUMNS, rows, limit=150, offset=1, max_bytes=4000
    )
    assert 0 < first["returned_count"] < 150


def test_paginate_last_page_reports_no_more(events):
    """has_more false and next_offset absent are one statement, made twice.

    A next_offset left on the final page invites one extra empty call; a
    has_more left true on the final page invites an infinite one.
    """
    rows = _ordered_arrivals(events["gfz"])
    page = tables.paginate(
        {},
        tables.ARRIVAL_COLUMNS,
        rows,
        limit=len(rows),
        offset=1,
        max_bytes=_ROOMY,
    )
    assert page["returned_count"] == len(rows)
    assert page["has_more"] is False
    assert "next_offset" not in page


def test_paginate_past_the_end_is_an_empty_page_not_an_error(events):
    """A model that over-shoots must get an empty answer it can read.

    Raising here would turn a harmless paging mistake into a failed tool call
    the model cannot act on.
    """
    rows = _ordered_arrivals(events["gfz"])
    page = tables.paginate(
        {}, tables.ARRIVAL_COLUMNS, rows, limit=10, offset=len(rows) + 500,
        max_bytes=_ROOMY,
    )
    assert page["rows"] == []
    assert page["returned_count"] == 0
    assert page["has_more"] is False
    assert "next_offset" not in page
    assert page["total_count"] == len(rows)


def test_paginate_keeps_the_envelope(events):
    """Envelope keys travel with every page, including the counts context.

    The envelope is where the datacenter, the event id and the ADR-0006
    found/message contract live; a page that dropped them would strand the rows.
    """
    rows = _ordered_arrivals(events["gfz"])
    envelope = {"found": True, "datacenter": "GFZ", "origins_count": 1}
    page = tables.paginate(
        {**envelope}, tables.ARRIVAL_COLUMNS, rows, limit=3, offset=1,
        max_bytes=_ROOMY,
    )
    for key, value in envelope.items():
        assert page[key] == value


# ---------------------------------------------------------------------------
# 8. The size guard
# ---------------------------------------------------------------------------


def _serialized_size(envelope, columns, page):
    """Exactly what trim_to_budget measures, so the tests agree with the code."""
    candidate = {**envelope, "columns": list(columns), "rows": page}
    return len(json.dumps(candidate, separators=(",", ":"), default=str))


def test_trim_to_budget_does_nothing_when_the_page_already_fits(events):
    """The guard is a backstop, not the first line of defence.

    The row maxima in ``config`` are measured from real responses, so on a
    normal page this must be the identity -- if it trimmed anyway, every call
    would page more often than the advertised limit implies.
    """
    rows = _ordered_arrivals(events["gfz"])[:20]
    assert tables.trim_to_budget({}, tables.ARRIVAL_COLUMNS, rows, _ROOMY) == rows


def test_trim_to_budget_shrinks_a_page_that_does_not_fit(events):
    """A denser-than-measured page shortens rather than failing the call.

    The caller simply pages once more; refusing would hand the model an error it
    has no way to act on.
    """
    rows = _ordered_arrivals(events["ingv"])
    assert _serialized_size({}, tables.ARRIVAL_COLUMNS, rows) > 8000
    trimmed = tables.trim_to_budget({}, tables.ARRIVAL_COLUMNS, rows, 8000)
    assert 0 < len(trimmed) < len(rows)


@pytest.mark.parametrize("max_bytes", [3000, 8000, 20_000])
def test_trim_to_budget_result_always_fits(events, max_bytes):
    """Whatever comes back must serialize inside the budget it was given.

    The convergence step scales to the overshoot instead of dropping one row at
    a time, which is fast but easy to get wrong by one row in the wrong
    direction.
    """
    rows = _ordered_arrivals(events["ingv"])
    trimmed = tables.trim_to_budget({}, tables.ARRIVAL_COLUMNS, rows, max_bytes)
    assert trimmed
    assert _serialized_size({}, tables.ARRIVAL_COLUMNS, trimmed) <= max_bytes


def test_trim_to_budget_drops_from_the_end_only(events):
    """Rows must be dropped off the end, never from the middle.

    ``next_offset`` counts the rows returned, so a page that kept row 1 and row
    3 would make the following page start after row 3 and lose row 2 for good.
    """
    rows = _ordered_arrivals(events["ingv"])
    trimmed = tables.trim_to_budget({}, tables.ARRIVAL_COLUMNS, rows, 8000)
    assert trimmed == rows[: len(trimmed)]


def test_trim_to_budget_accounts_for_the_envelope(events):
    """The budget covers the whole response, not just the rows.

    A large envelope has to cost the page rows; measuring the rows alone would
    let the result overrun the context budget it exists to respect.
    """
    rows = _ordered_arrivals(events["ingv"])
    small = tables.trim_to_budget({}, tables.ARRIVAL_COLUMNS, rows, 12_000)
    fat = tables.trim_to_budget(
        {"message": "x" * 6000}, tables.ARRIVAL_COLUMNS, rows, 12_000
    )
    assert len(fat) < len(small)


def test_trim_to_budget_raises_when_one_row_cannot_fit(events):
    """No amount of paging fixes a single row over budget, so it must say so.

    Returning an empty page instead would be a silent hole: the caller would see
    rows it can never reach, with has_more flapping forever.
    """
    rows = _ordered_arrivals(events["ingv"])[:1]
    with pytest.raises(tables.RowTooLargeError):
        tables.trim_to_budget({}, tables.ARRIVAL_COLUMNS, rows, 100)


def test_trim_to_budget_on_no_rows_is_not_an_error():
    """An empty page trivially fits any budget, including an absurd one."""
    assert tables.trim_to_budget({}, tables.ARRIVAL_COLUMNS, [], 1) == []


# ---------------------------------------------------------------------------
# 9. Event column normalization
# ---------------------------------------------------------------------------


def test_normalize_event_columns_agrees_across_datacenters():
    """INGV writes "Depth/Km", everyone else "Depth/km": same column either way.

    Column names that depend on who answered are exactly the datacenter-visible
    behaviour C3 forbids, and a model that learned one spelling would fail on
    the other node with no error to explain it.
    """
    assert tables.normalize_event_columns(["Depth/Km"]) == ["depth_km"]
    assert tables.normalize_event_columns(["Depth/km"]) == ["depth_km"]


@pytest.mark.parametrize("node", ["ingv", "emsc", "gfz", "usgs"])
def test_normalize_event_columns_on_real_headers(node):
    """Run against the four headers actually captured, not an idealized one.

    EMSC and USGS omit EventType entirely, so the normalized set differs in
    length between nodes; what must not differ is the spelling of the columns
    they do share.
    """
    header = (FIXTURES / f"{node}_honshu_2024-01-01.txt").read_text()
    columns, _ = parse_fdsn_text(header)
    normalized = tables.normalize_event_columns(columns)
    assert "depth_km" in normalized
    assert set(normalized) <= set(tables.EVENT_COLUMNS)
    assert normalized == [c for c in tables.EVENT_COLUMNS if c in set(normalized)]


def test_normalize_event_columns_maps_all_fourteen_fdsn_names():
    """The full FDSN 1.2 text profile header must land on EVENT_COLUMNS in order.

    The column tuple is the published contract; if the map and the tuple drifted
    apart, a node sending the complete header would produce a table whose names
    no longer match the ones advertised.
    """
    header = (
        "#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|"
        "ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType"
    )
    columns, _ = parse_fdsn_text(header + "\n")
    assert tables.normalize_event_columns(columns) == list(tables.EVENT_COLUMNS)
    assert len(tables.EVENT_COLUMNS) == 14


def test_normalize_event_columns_passes_an_unknown_column_through():
    """A node that publishes an extra field must not lose it (C2).

    We cannot know in advance what a node will call it, so the only safe
    behaviour is to lowercase and keep it. Dropping it would be invisible: the
    row values would still be there, just one column short of a name.
    """
    assert tables.normalize_event_columns(["#Foo Bar"]) == ["foo bar"]
    assert tables.normalize_event_columns(
        ["EventID", "SomethingNew"]
    ) == ["event_id", "somethingnew"]


def test_normalize_event_columns_tolerates_header_punctuation():
    """The leading '#', stray spaces and underscores are formatting, not names.

    Different nodes decorate the same header differently, and the key has to
    survive all of it to keep the mapping datacenter-independent.
    """
    assert tables.normalize_event_columns(
        ["#EventID", " Event_Location_Name ", "MAGTYPE"]
    ) == ["event_id", "location_name", "mag_type"]


@pytest.mark.parametrize("budget", [36_000, 20_000, 8_000, 3_000])
@pytest.mark.parametrize("fixture_name", ["ingv", "emsc", "gfz"])
def test_paginate_payload_never_exceeds_the_budget(events, fixture_name, budget):
    """The budget binds the WHOLE response, not the part trim happened to see.

    `trim_to_budget` measures envelope + columns + rows, but `paginate` then adds
    total_count, returned_count, limit, offset, has_more and next_offset on top
    of the page trim had already approved. Measuring the bare envelope let the
    finished payload run past the ceiling by the width of those keys. Small at
    36 kB, and precisely the kind of slack that stops being harmless the day the
    budget is lowered -- which is a planned task for this release, so pin it
    here rather than rediscover it in production.

    Parametrized down to budgets far below any real setting because the overrun
    is a fixed number of bytes: it is invisible at 36 kB and dominant at 3 kB.
    """
    event = events[fixture_name]
    rows, _ = tables.order_arrival_rows(tables.build_arrival_rows(event))
    rows, prefixes = tables.strip_id_prefixes(tables.ARRIVAL_COLUMNS, rows)
    if not rows:
        pytest.skip(f"{fixture_name} fixture carries no arrivals")

    envelope = {
        "datacenter": fixture_name.upper(),
        "api_url": "https://example.invalid/fdsnws/event/1/query?eventid=x",
        "found": True,
        "event_id": "x",
        "origins_count": len(event.origins),
        "ordered_by": "is_preferred_origin, distance",
        "id_prefixes": prefixes,
    }
    out = tables.paginate(
        envelope, tables.ARRIVAL_COLUMNS, rows,
        limit=len(rows), offset=1, max_bytes=budget,
    )
    size = len(json.dumps(out, separators=(",", ":"), default=str))
    assert size <= budget, (
        f"{fixture_name}: paginate returned {out['returned_count']} rows "
        f"serializing to {size} bytes, over the {budget} byte budget"
    )
    assert out["returned_count"] >= 1
