"""시간축 회귀 방지.

QMS 의 모든 시각은 MES 앵커에서 유도되어야 한다. MES 앵커가 Bicep 의 utcNow()
로 배포 시점에 평가되므로, 벽시계 상수가 하나라도 남으면 재배포 때 그 부분만
제자리에 남는다. 적재는 성공하고 값만 어긋나서 눈으로는 찾을 수 없다.

여기 있는 테스트는 전부 "옛 코드로 되돌리면 실패하는가"를 기준으로 썼다.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import time

import pytest

from src.mes_client import MesSnapshot, anchor_date, mes_anchor, parse_mes_time
from src.qms_schema import TABLE_DDL, build_all_tables
from src.qms_validate import validate

SHIFT = dt.timedelta(days=27)

TIMEZONES = ["UTC", "Asia/Seoul", "America/Los_Angeles", "Europe/Berlin", "Asia/Kolkata"]


def timestamp_columns() -> dict[str, list[str]]:
    return {
        name: [
            chunk.strip().split()[0]
            for chunk in ddl.split(",")
            if chunk.strip().upper().endswith("TIMESTAMP")
        ]
        for name, ddl in TABLE_DDL.items()
    }


def date_columns() -> dict[str, list[str]]:
    return {
        name: [
            chunk.strip().split()[0]
            for chunk in ddl.split(",")
            if chunk.strip().upper().endswith(" DATE")
        ]
        for name, ddl in TABLE_DDL.items()
    }


def shifted_snapshot(snapshot: MesSnapshot, delta: dt.timedelta) -> MesSnapshot:
    """MES 공정이력 시각을 통째로 옮긴 스냅샷. 재배포를 흉내낸다."""
    payload = copy.deepcopy(snapshot.to_dict())
    for row in payload["process_results"]:
        for field in ("in_time", "out_time"):
            row[field] = (parse_mes_time(row[field]) + delta).isoformat()
    return MesSnapshot(**payload)


@pytest.fixture
def shifted(snapshot):
    return shifted_snapshot(snapshot, SHIFT)


def spread_snapshot(snapshot: MesSnapshot, hours: float) -> MesSnapshot:
    """공정이력을 주어진 시간 폭에 고르게 흩뿌린 스냅샷.

    현재 픽스처는 옛 MES 라 91건이 사실상 한 순간에 몰려 있다. 그 상태로는
    "시각이 흩어진 데이터"를 한 번도 겪지 못한 채 테스트가 통과한다.
    mock-mes-kr#3 이후의 실제 분포(63시간)를 흉내내 그 공백을 메운다.
    """
    payload = copy.deepcopy(snapshot.to_dict())
    rows = sorted(payload["process_results"], key=lambda r: r["id"])
    start = min(parse_mes_time(r["out_time"]) for r in rows)
    step = dt.timedelta(hours=hours) / max(len(rows) - 1, 1)
    for index, row in enumerate(rows):
        out = start + step * index
        row["out_time"] = out.isoformat()
        row["in_time"] = (out - dt.timedelta(hours=1)).isoformat()
    payload["process_results"] = rows
    return MesSnapshot(**payload)


def test_anchor_is_the_last_mes_out_time(snapshot):
    expected = max(parse_mes_time(r["out_time"]) for r in snapshot.process_results)
    assert mes_anchor(snapshot) == expected
    assert mes_anchor(snapshot).tzinfo is not None


def test_anchor_moves_with_the_snapshot(snapshot, shifted):
    assert mes_anchor(shifted) == mes_anchor(snapshot) + SHIFT


def test_anchor_rejects_an_empty_snapshot():
    with pytest.raises(ValueError, match="기준점"):
        mes_anchor(MesSnapshot())


def test_naive_mes_time_is_read_as_utc():
    assert parse_mes_time("2026-09-04T06:53:10").tzinfo == dt.timezone.utc


def test_offset_mes_time_is_normalized_to_utc():
    parsed = parse_mes_time("2026-09-04T15:53:10+09:00")
    assert parsed.tzinfo == dt.timezone.utc
    assert parsed.hour == 6


# --- 앵커 독립성: 이번 작업의 핵심 테스트 -------------------------------------


def test_every_timestamp_moves_with_the_anchor(snapshot, shifted):
    """앵커를 옮기면 모든 시각 컬럼이 정확히 같은 폭으로 이동해야 한다.

    하나라도 제자리에 남으면 그게 벽시계 상수다.
    """
    before = build_all_tables(snapshot)
    after = build_all_tables(shifted)

    checked = 0
    for name, columns in timestamp_columns().items():
        for column in columns:
            pairs = list(zip(before[name], after[name], strict=True))
            assert pairs, f"{name} 이 비어 있어 검사할 수 없습니다"
            for old_row, new_row in pairs:
                old, new = old_row[column], new_row[column]
                if old is None:
                    assert new is None
                    continue
                assert new - old == SHIFT, f"{name}.{column} 이 앵커를 따라가지 않습니다"
                checked += 1
    assert checked > 0, "검사한 시각 값이 하나도 없습니다"


def test_every_date_moves_with_the_anchor(snapshot, shifted):
    before = build_all_tables(snapshot)
    after = build_all_tables(shifted)

    checked = 0
    for name, columns in date_columns().items():
        for column in columns:
            for old_row, new_row in zip(before[name], after[name], strict=True):
                old, new = old_row[column], new_row[column]
                if old is None:
                    assert new is None
                    continue
                assert new - old == SHIFT, f"{name}.{column} 이 앵커를 따라가지 않습니다"
                checked += 1
    assert checked > 0, "검사한 날짜 값이 하나도 없습니다"


def test_shifting_the_anchor_changes_nothing_but_time(snapshot, shifted):
    """시각 말고는 전부 그대로여야 한다. 시프트가 다른 값을 흔들면 안 된다.

    supplier_lot_no 는 예외다. 공급사 로트번호에 수입 연월이 들어 있어서
    날짜를 따라 바뀌는 것이 맞다. 아래 별도 테스트로 그 규칙을 고정한다.
    """
    before = build_all_tables(snapshot)
    after = build_all_tables(shifted)
    time_columns = {
        (name, column)
        for mapping in (timestamp_columns(), date_columns())
        for name, columns in mapping.items()
        for column in columns
    }
    time_columns.add(("qms_incoming_inspection", "supplier_lot_no"))
    for name in before:
        for old_row, new_row in zip(before[name], after[name], strict=True):
            for column, value in old_row.items():
                if (name, column) in time_columns:
                    continue
                assert new_row[column] == value, f"{name}.{column} 이 시프트에 흔들렸습니다"


def test_supplier_lot_number_carries_its_receipt_month(snapshot, shifted):
    """공급사 로트번호의 연월이 수입일과 항상 맞아야 한다."""
    for source in (snapshot, shifted):
        rows = build_all_tables(source)["qms_incoming_inspection"]
        assert rows
        for row in rows:
            assert row["supplier_lot_no"].split("-")[1] == f"{row['receipt_date']:%y%m}"


def test_inspections_stay_inside_the_mes_window_after_a_shift(shifted):
    """이동한 스냅샷에서도 검사가 MES 구간 안에 머물러야 한다."""
    tables = build_all_tables(shifted)
    window_start = min(parse_mes_time(r["out_time"]) for r in shifted.process_results)
    window_end = mes_anchor(shifted) + dt.timedelta(days=4)
    for row in tables["qms_inspection"]:
        assert window_start <= row["inspection_datetime"] <= window_end


def test_no_inspection_type_is_pinned_to_a_calendar_date(snapshot, shifted):
    """검사 유형별로 봐도 전부 이동해야 한다.

    이전 결함은 IPQC/IPQC-RT 만 MES 파생이고 OQC/PCS/EQV 76건이 하드코딩이었다.
    유형별로 나눠 보지 않으면 평균에 묻혀 안 보인다.
    """
    before = build_all_tables(snapshot)["qms_inspection"]
    after = build_all_tables(shifted)["qms_inspection"]
    seen = set()
    for old_row, new_row in zip(before, after, strict=True):
        kind = old_row["inspection_type"]
        seen.add(kind)
        delta = new_row["inspection_datetime"] - old_row["inspection_datetime"]
        assert delta == SHIFT, f"{kind} 이 앵커를 따라가지 않습니다"
    assert seen == {"IPQC", "IPQC-RT", "OQC", "PCS", "EQV"}


def test_validation_passes_on_a_shifted_snapshot(shifted):
    results = validate(shifted, build_all_tables(shifted))
    failed = [r.name for r in results if not r.passed]
    assert not failed


def test_anchor_date_is_the_utc_date_of_the_anchor(snapshot):
    assert anchor_date(snapshot) == mes_anchor(snapshot).date()


def test_post_production_inspections_never_precede_production(snapshot):
    """앵커가 하루 중 언제든 OQC/PCS/EQV 는 생산보다 뒤여야 한다.

    .date() 기반이라 앵커 시각에 따라 간격이 달라진다. 그 간격이 음수가 되지
    않는지를 하루 24시간 전부에서 확인한다.
    """
    base = mes_anchor(snapshot)
    for hour in range(24):
        moved = shifted_snapshot(snapshot, base.replace(hour=hour, minute=59, second=59) - base)
        anchor = mes_anchor(moved)
        tables = build_all_tables(moved)
        for row in tables["qms_inspection"]:
            if row["inspection_type"] in {"OQC", "PCS", "EQV"}:
                assert row["inspection_datetime"] > anchor


# --- 타임존 통일 -------------------------------------------------------------


def test_no_timestamp_is_naive(tables):
    naive = [
        f"{name}.{column}"
        for name, columns in timestamp_columns().items()
        for column in columns
        for row in tables[name]
        if row[column] is not None and row[column].tzinfo is None
    ]
    assert not naive


def test_every_timestamp_is_utc(tables):
    for name, columns in timestamp_columns().items():
        for column in columns:
            for row in tables[name]:
                value = row[column]
                if value is not None:
                    assert value.utcoffset() == dt.timedelta(0), f"{name}.{column} 이 UTC 가 아닙니다"


@pytest.mark.parametrize("timezone_name", TIMEZONES)
def test_generation_is_identical_across_driver_timezones(snapshot, timezone_name):
    """드라이버 타임존이 달라도 생성 결과가 같아야 한다.

    naive 시각이 있으면 PySpark 가 time.mktime(로컬 타임존)으로 저장해 일부 행만
    밀린다. 생성 단계에서 tz-aware 로 통일해 두면 그 경로 자체가 사라진다.
    """
    baseline = build_all_tables(snapshot)
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = timezone_name
        time.tzset()
        assert build_all_tables(snapshot) == baseline
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


@pytest.mark.parametrize("timezone_name", TIMEZONES)
def test_spark_stores_the_same_instant_across_driver_timezones(tables, timezone_name):
    """PySpark 의 TimestampType.toInternal 을 재현해 저장값이 같은지 본다.

    tz-aware 는 calendar.timegm 을 타므로 드라이버 타임존과 무관하다. 이 테스트는
    naive 값이 하나라도 섞이면 즉시 깨진다.
    """
    import calendar

    def to_internal(value: dt.datetime) -> int:
        seconds = (
            calendar.timegm(value.utctimetuple())
            if value.tzinfo
            else time.mktime(value.timetuple())
        )
        return int(seconds) * 1000000 + value.microsecond

    expected = [
        to_internal(row["inspection_datetime"]) for row in tables["qms_inspection"]
    ]
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = timezone_name
        time.tzset()
        actual = [to_internal(row["inspection_datetime"]) for row in tables["qms_inspection"]]
        assert actual == expected
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


# --- 흩어진 MES 분포 (mock-mes-kr#3 이후의 실제 모양) ------------------------


def test_generation_holds_on_a_spread_out_snapshot(snapshot):
    """63시간에 흩뿌려진 공정이력에서도 행수와 검증이 그대로여야 한다."""
    spread = spread_snapshot(snapshot, hours=63)
    assert len({r["out_time"] for r in spread.process_results}) == len(spread.process_results)

    tables = build_all_tables(spread)
    assert sum(len(rows) for rows in tables.values()) == 1004
    failed = [result.name for result in validate(spread, tables) if not result.passed]
    assert not failed


def test_spread_out_snapshot_keeps_the_anchor_relationship(snapshot):
    """흩어진 스냅샷을 다시 옮겨도 전부 따라와야 한다."""
    spread = spread_snapshot(snapshot, hours=63)
    moved = shifted_snapshot(spread, SHIFT)
    before = build_all_tables(spread)["qms_inspection"]
    after = build_all_tables(moved)["qms_inspection"]
    for old_row, new_row in zip(before, after, strict=True):
        assert new_row["inspection_datetime"] - old_row["inspection_datetime"] == SHIFT


def test_inspections_track_the_spread_not_just_the_anchor(snapshot):
    """MES 가 흩어지면 IPQC 도 같이 흩어져야 한다.

    IPQC 가 앵커 하나에만 매달려 있으면 MES 가 아무리 흩어져도 QMS 는 한 점에
    모인다. 설비별·시간별 교차 질의가 그때 무너진다.
    """
    narrow = build_all_tables(snapshot)["qms_inspection"]
    wide = build_all_tables(spread_snapshot(snapshot, hours=63))["qms_inspection"]

    def span(rows, kind):
        values = [r["inspection_datetime"] for r in rows if r["inspection_type"] == kind]
        return (max(values) - min(values)).total_seconds() / 3600

    assert span(narrow, "IPQC") < 12
    assert span(wide, "IPQC") > 50


def test_source_has_no_wall_clock_constants():
    """src/ 에 연·월·일 리터럴이 다시 들어오지 않게 막는다."""
    import pathlib
    import re

    pattern = re.compile(r"dt\.date(?:time)?\(\s*\d{4}\s*,")
    offenders = []
    for path in sorted(pathlib.Path("src").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{number}")
    assert not offenders, f"벽시계 상수가 남아 있습니다: {offenders}"
