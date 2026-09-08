from datetime import datetime, timedelta, timezone

import pytest

from src.fdc_runs import Run, parse_ts, run_at, runs_by_equipment, span

UTC = timezone.utc


def _fact(lot, step, eqp, start, end, defect=None):
    return {
        "lot_id": lot, "step_code": step, "eqp_id": eqp,
        "in_time": start, "out_time": end, "defect_code": defect,
    }


class _Facts:
    """MesFacts 의 최소 대역. 이 모듈은 세 필드만 본다."""

    def __init__(self, rows):
        self.process_results = rows
        self.route = []
        self.equipment = []


ROWS = [
    _fact("LOT0002", "ETCH", "EQP-ETCH01",
          "2026-09-03T04:00:00+00:00", "2026-09-03T05:00:00+00:00"),
    _fact("LOT0001", "ETCH", "EQP-ETCH01",
          "2026-09-03T01:00:00+00:00", "2026-09-03T02:00:00+00:00", "Particle"),
    _fact("LOT0001", "METRO", None,
          "2026-09-03T02:30:00+00:00", "2026-09-03T02:45:00+00:00"),
]


def test_parse_ts_is_utc_aware():
    got = parse_ts("2026-09-03T01:00:00+00:00")
    assert got == datetime(2026, 9, 3, 1, tzinfo=UTC)
    assert got.tzinfo is not None


def test_parse_ts_accepts_z_suffix():
    assert parse_ts("2026-09-03T01:00:00Z") == datetime(2026, 9, 3, 1, tzinfo=UTC)


def test_parse_ts_treats_naive_as_utc():
    assert parse_ts("2026-09-03T01:00:00") == datetime(2026, 9, 3, 1, tzinfo=UTC)


def test_runs_are_grouped_by_equipment():
    assert set(runs_by_equipment(_Facts(ROWS))) == {"EQP-ETCH01"}


def test_runs_without_equipment_are_dropped():
    """METRO 는 설비를 배정받지 않으므로 센서 데이터가 존재할 수 없다."""
    got = runs_by_equipment(_Facts(ROWS))
    assert not any(r.step_code == "METRO" for runs in got.values() for r in runs)


def test_runs_are_sorted_by_start():
    got = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    assert [r.lot_id for r in got] == ["LOT0001", "LOT0002"]


def test_run_carries_defect_code():
    got = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    assert got[0].defect_code == "Particle"
    assert got[1].defect_code is None


def test_run_at_is_half_open():
    runs = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    start = datetime(2026, 9, 3, 1, tzinfo=UTC)
    end = datetime(2026, 9, 3, 2, tzinfo=UTC)
    assert run_at(runs, start).lot_id == "LOT0001"
    assert run_at(runs, end - timedelta(seconds=1)).lot_id == "LOT0001"
    assert run_at(runs, end) is None


def test_run_at_returns_none_between_runs():
    runs = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    assert run_at(runs, datetime(2026, 9, 3, 3, tzinfo=UTC)) is None


def test_run_at_on_empty_list():
    assert run_at([], datetime(2026, 9, 3, tzinfo=UTC)) is None


def test_span_includes_rows_without_equipment():
    lo, hi = span(_Facts(ROWS))
    assert lo == datetime(2026, 9, 3, 1, tzinfo=UTC)
    assert hi == datetime(2026, 9, 3, 5, tzinfo=UTC)


def test_span_raises_on_empty():
    with pytest.raises(ValueError):
        span(_Facts([]))


def test_real_facts_produce_runs_for_every_tool(facts):
    assert set(runs_by_equipment(facts)) == {e["eqp_id"] for e in facts.equipment}


def test_real_runs_never_overlap_on_one_tool(facts):
    for eqp_id, runs in runs_by_equipment(facts).items():
        for prev, cur in zip(runs, runs[1:]):
            assert prev.end <= cur.start, eqp_id


def test_real_span_is_between_two_and_four_days(facts):
    lo, hi = span(facts)
    assert timedelta(days=2) < hi - lo < timedelta(days=4)


def test_real_defect_runs_exist(facts):
    runs = [r for rs in runs_by_equipment(facts).values() for r in rs]
    assert sum(1 for r in runs if r.defect_code) > 10
