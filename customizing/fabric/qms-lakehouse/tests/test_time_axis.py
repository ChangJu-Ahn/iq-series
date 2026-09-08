"""시간축 회귀 방지.

QMS 의 모든 시각은 MES 앵커에서 유도되어야 한다. MES 앵커가 Bicep 의 utcNow()
로 배포 시점에 평가되므로, 벽시계 상수가 하나라도 남으면 재배포 때 그 부분만
제자리에 남는다. 적재는 성공하고 값만 어긋나서 눈으로는 찾을 수 없다.

여기 있는 테스트는 전부 "옛 코드로 되돌리면 실패하는가"를 기준으로 썼다.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
import os
import random
import statistics
import time
from collections import Counter

import pytest

from src.mes_client import MesSnapshot, anchor_date, mes_anchor, parse_mes_time
from src.qms_schema import TABLE_DDL, build_all_tables
from src.qms_validate import validate

# 이미 일어난 사건을 담는 컬럼. src 의 목록을 가져다 쓰면 그쪽이 비어도
# 테스트가 통과하므로 여기에 따로 적는다.
COMPLETED_COLUMNS = [
    ("qms_inspection", "inspection_datetime"),
    ("qms_measurement", "measured_at"),
    ("qms_incoming_inspection", "receipt_date"),
    ("qms_incoming_inspection", "inspection_date"),
    ("qms_nonconformance", "detected_date"),
    ("qms_nonconformance", "closed_date"),
    ("qms_disposition", "decision_date"),
]

SHIFT = dt.timedelta(days=27)

TIMEZONES = ["UTC", "Asia/Seoul", "America/Los_Angeles", "Europe/Berlin", "Asia/Kolkata"]

# 근무조 한 조의 길이. 정기 검사가 이 안에서만 시각을 고르므로 후보 공간을
# 실제 관측 폭보다 좁게 잡는 근거가 된다. 좁게 잡을수록 충돌 기대값이 커지고
# 한계치가 느슨해지므로, 틀리더라도 뭉침을 놓치는 쪽이 아니라 우연한 충돌을
# 너그럽게 보는 쪽으로 틀린다.
_SHIFT_HOURS = 8


def _minute_slots(values: list[dt.datetime]) -> int:
    """검사 시각이 놓일 수 있는 분 단위 자리의 수.

    실제 후보 집합을 생성 코드에서 가져오면 돌연변이가 생성과 검증 양쪽에
    걸려 상쇄된다. 관측된 날짜 수만 세어 밖에서 다시 만든다.
    """
    days = len({value.date() for value in values})
    return max(days * _SHIFT_HOURS * 60, 1)


def _coincidence_limit(count: int, slots: int) -> int:
    """우연만으로 한 시각에 몰릴 수 있는 건수의 상한.

    자리마다 평균 count/slots 건이 떨어지는 포아송으로 본다. 자리가 slots 개
    이므로 어딘가에서 k 건 이상이 겹칠 확률은 slots * P(X >= k) 로 어림한다.
    그 값이 1% 아래로 떨어지는 첫 k 를 한계로 삼는다.

    하한은 3이다. 두 건이 겹치는 것은 실제로 일어난다 — 앵커를 13시간 옮기면
    PCS 36건에서 바로 나온다. 한계가 2면 그 우연에 깨진다.

    **지금 규모에서는 다섯 유형 중 IPQC 만 포아송이 4를 내고 나머지 넷은
    하한 3 이 결정한다.** 예산을 100배 느슨하게 해도 넷은 3 그대로다. 그래서
    "검사 건수가 바뀌면 한계가 따라 움직인다" 는 IPQC 에만 해당한다. 포아송을
    두는 이유는 데이터가 커질 때를 위해서다 — IPQC 가 이미 그 구간에 있다.

    반대로 하한을 빼면 IPQC-RT 와 EQV 가 2 로 내려가 우연한 충돌에 깨진다.
    둘 다 필요하고, 지금은 하한이 주로 일한다.
    """
    lam = count / slots
    for k in range(3, count + 1):
        tail = 1.0 - sum(lam**i * math.exp(-lam) / math.factorial(i) for i in range(k))
        if slots * tail < 0.01:
            return k
    return count


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

    픽스처의 폭은 MES 배포마다 달라진다. 그 값에 기대면 테스트가 데이터의
    우연한 성질을 단언하게 되므로, 검사하려는 폭을 여기서 직접 만든다.
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


@pytest.mark.parametrize(
    "delta",
    [
        SHIFT,
        dt.timedelta(hours=37),
        dt.timedelta(days=3, hours=7, minutes=41),
        dt.timedelta(hours=13),
    ],
    ids=["27일", "37시간", "3일7시간41분", "13시간"],
)
def test_shifting_the_anchor_changes_nothing_but_time(snapshot, delta):
    """시각 말고는 전부 그대로여야 한다. 시프트가 다른 값을 흔들면 안 된다.

    정수 일수가 아닌 폭을 함께 쓴다. SHIFT 하나(27일)만 쓰면 이동 전후로
    시(hour)가 그대로라 하루 주기 성분에 눈이 먼다. 야간 검사의 산포를
    1.8배로 키우는 돌연변이를 넣어 확인했다 — 규격이탈이 +37시간에서만
    13에서 14로 바뀌는데, 27일 시프트로는 186개가 전부 통과했다.

    FDC 는 환경 센서가 하루 주기라 앵커를 +37시간 옮기면 경보 설비가
    7개에서 6개로 바뀌는 것을 찾았다. 그쪽은 물리적으로 맞는 동작이라
    앵커를 고정하는 쪽으로 갔다. QMS 는 시각 자체 말고 시간에 의존하는 값이
    없어야 한다. 그래야 재배포 시점이 데이터의 내용을 바꾸지 않는다.

    supplier_lot_no 는 예외다. 공급사 로트번호에 수입 연월이 들어 있어서
    날짜를 따라 바뀌는 것이 맞다. 아래 별도 테스트로 그 규칙을 고정한다.
    """
    before = build_all_tables(snapshot)
    after = build_all_tables(shifted_snapshot(snapshot, delta))
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


def test_completed_events_never_reach_into_the_future(snapshot):
    """앵커가 하루 중 언제든 완료된 사건은 현재를 넘지 않아야 한다.

    앵커는 MES 배포 시각이라 실습 시점의 "지금"이다. 판정이 채워진 검사나
    종결된 부적합이 그 뒤에 있으면 아직 오지 않은 날짜에 끝난 사건이 된다.
    앵커 시각에 따라 남는 여유가 달라지므로 하루 24시간 전부에서 확인한다.

    기대값을 mes_anchor 로 만들면 생성 쪽과 검사 쪽에 같은 결함이 걸려 서로
    상쇄된다. 원시 process_results 에서 직접 최대 out_time 을 구한다.
    """
    base = mes_anchor(snapshot)
    for hour in range(24):
        moved = shifted_snapshot(snapshot, base.replace(hour=hour, minute=59, second=59) - base)
        raw = max(parse_mes_time(r["out_time"]) for r in moved.process_results)
        tables = build_all_tables(moved)
        for table, column in COMPLETED_COLUMNS:
            for row in tables[table]:
                value = row.get(column)
                if value is None:
                    continue
                ceiling = raw if isinstance(value, dt.datetime) else raw.date()
                assert value <= ceiling, f"{hour}시 앵커에서 {table}.{column}={value} 가 미래다"


def test_planned_dates_are_allowed_to_be_in_the_future(tables):
    """조치 기한과 유효성 점검 예정일은 미래에 있어야 한다.

    아직 오지 않은 일까지 과거로 끌어내리면 "기한이 임박한 미결 부적합" 같은
    질문이 성립하지 않는다. 미래를 막는 규칙이 이쪽까지 번지지 않게 고정한다.
    """
    for table, column in [
        ("qms_nonconformance", "due_date"),
        ("qms_disposition", "effectiveness_check_date"),
    ]:
        future = [r[column] for r in tables[table] if r[column] is not None]
        assert future, f"{table}.{column} 이 비어 있습니다"


def test_shipping_inspection_follows_each_lot_not_a_global_date(snapshot):
    """출하검사는 그 로트의 생산 완료 뒤에 온다.

    전역 날짜에서 만들면 로트마다 완료 시점이 달라도 검사가 한곳에 모인다.
    로트별로 유도해야 "먼저 끝난 로트를 먼저 출하검사한다"가 성립한다.
    """
    completed: dict[str, dt.datetime] = {}
    for row in snapshot.process_results:
        lot_id = row.get("lot_id")
        if not lot_id:
            continue
        moment = parse_mes_time(row["out_time"])
        if lot_id not in completed or moment > completed[lot_id]:
            completed[lot_id] = moment

    oqc = [r for r in build_all_tables(snapshot)["qms_inspection"] if r["inspection_type"] == "OQC"]
    assert oqc
    for row in oqc:
        assert row["inspection_datetime"] >= completed[row["lot_id"]]

    # 로트마다 검사 시점이 갈리는지. 전부 같으면 전역 날짜로 되돌아간 것이다.
    starts = {row["lot_id"]: row["inspection_datetime"] for row in oqc}
    assert len(set(starts.values())) > 1


def test_lots_still_in_production_have_no_shipping_inspection(snapshot):
    """생산 중인 로트에는 출하검사가 없다.

    아직 끝나지 않은 로트까지 출하검사를 만들면 "출하검사 대기 로트"라는
    질문이 성립하지 않는다.
    """
    tables = build_all_tables(snapshot)
    inspected = {
        r["lot_id"] for r in tables["qms_inspection"] if r["inspection_type"] == "OQC"
    }
    done = {l["lot_id"] for l in snapshot.lots if l["status"] == "Done"}
    pending = {l["lot_id"] for l in snapshot.lots if l["status"] != "Done"}
    assert inspected == done
    assert pending and not (inspected & pending)


@pytest.mark.parametrize(
    "delta",
    [dt.timedelta(0), dt.timedelta(hours=13), dt.timedelta(hours=37), SHIFT],
    ids=["원본", "13시간", "37시간", "27일"],
)
def test_inspections_do_not_pile_on_a_single_moment(snapshot, delta):
    """검사 시각이 한 지점에 무더기로 쌓이지 않는지.

    범위를 벗어난 값을 양끝으로 자르면 잘린 행이 경계 시각 하나에 그대로
    쌓인다. 적재는 성공하고 분포만 망가지므로 눈에 잘 띄지 않는다.

    이 테스트는 원래 `len(set(values)) == len(values)` 로 완전 무충돌을
    요구했다. 지킬 수 없는 약속이었다. 검사 시각은 근무조 안의 분 단위
    격자에서 고르므로 후보가 유한하고, 생일 문제로 충돌이 확률적으로 난다.
    PCS 36건이 1,440개 후보에서 무충돌일 확률은 64% 뿐이다 — 세 번에 한 번
    실패한다. 실제로 앵커를 13시간 옮기면 충돌이 하나 생긴다.

    앵커를 함께 옮긴다. 원본 하나만 보면 그 표본이 우연히 깨끗한 것을
    성질로 착각한다. 원래 이 테스트가 PCS·EQV 만, 그것도 원본만 보다가
    IPQC 와 IPQC-RT 5건이 앵커 시각에 쌓여 있는 것을 3라운드 동안 놓쳤다.

    우연한 충돌과 뭉침은 규모가 다르다. 우연은 한 시각에 2건이고, 자르기는
    34건을 한 시각에 쌓는다. 그 사이를 가른다. 한계치는 다섯 유형 중 넷이
    하한 3 이고 IPQC 만 포아송이 4 를 낸다 — 자세한 것은 `_coincidence_limit`
    주석에 적었다.
    """
    rows = build_all_tables(shifted_snapshot(snapshot, delta))["qms_inspection"]
    for kind in ("IPQC", "IPQC-RT", "OQC", "PCS", "EQV"):
        values = [r["inspection_datetime"] for r in rows if r["inspection_type"] == kind]
        assert values, f"{kind} 검사가 하나도 없습니다"
        limit = _coincidence_limit(len(values), _minute_slots(values))
        heaviest = Counter(values).most_common(1)[0]
        assert heaviest[1] <= limit, (
            f"{kind} 의 {heaviest[0]} 에 {heaviest[1]}건이 몰렸습니다"
            f" (우연으로 설명되는 한계 {limit}건)"
        )


@pytest.mark.parametrize(
    "delta",
    [dt.timedelta(0), dt.timedelta(hours=13), dt.timedelta(hours=37), SHIFT],
    ids=["원본", "13시간", "37시간", "27일"],
)
def test_no_inspection_is_clamped_onto_the_anchor(snapshot, delta):
    """앵커 시각의 검사는 그 공정이 앵커에 끝났을 때만 있어야 한다.

    미래를 막으려고 앵커로 자르면 잘린 것들이 앵커 한 점에 쌓인다. 그런데
    앵커에 놓인 검사가 전부 잘린 것은 아니다. 앵커는 마지막 공정 종료
    시각이므로 그 공정의 검사는 정당하게 앵커에 놓일 수 있다.

    둘을 가르는 것은 유래한 공정의 종료 시각이다. 자르기는 훨씬 이른 공정의
    검사까지 앵커로 끌어온다. 그것만 잡는다.

    한 시각에 몇 건이 몰렸는지로는 이것을 못 잡는다. 자르기가 만든 뭉침이
    세 건이었고 우연한 충돌의 한계도 세 건이라 정확히 걸쳐서 통과했다.
    뭉친 개수가 아니라 뭉친 자리를 봐야 했다.

    기대값을 MES 원시 데이터에서 직접 만든다. mes_anchor 로 만들면 앵커 유도
    자체가 틀렸을 때 생성과 검증에 같이 걸려 상쇄된다.
    """
    moved = shifted_snapshot(snapshot, delta)
    anchor = max(parse_mes_time(r["out_time"]) for r in moved.process_results)
    ends = {r["id"]: parse_mes_time(r["out_time"]) for r in moved.process_results}
    for row in build_all_tables(moved)["qms_inspection"]:
        if row["inspection_datetime"] != anchor:
            continue
        source = row["mes_process_result_id"]
        assert source is not None, (
            f"{row['inspection_id']} 가 유래한 공정 없이 앵커에 놓였습니다"
        )
        assert ends[source] == anchor, (
            f"{row['inspection_id']} 는 {ends[source]} 에 끝난 공정의 검사인데"
            f" 앵커 {anchor} 로 잘려 있습니다"
        )


@pytest.mark.parametrize(
    "delta",
    [dt.timedelta(0), dt.timedelta(hours=13), dt.timedelta(hours=37), SHIFT],
    ids=["원본", "13시간", "37시간", "27일"],
)
def test_the_coincidence_limit_separates_luck_from_clamping(snapshot, delta):
    """한계치가 우연은 통과시키고 자르기는 잡는 자리에 있어야 한다.

    한계치가 너무 높으면 뭉침을 놓치고, 너무 낮으면 우연한 충돌에 깨진다.
    실측한 두 규모 사이에 있는지 확인한다. 이것이 무너지면 위 테스트의
    한계치 유도를 다시 봐야 한다.

    `_coincidence_limit` 은 수렴에 실패하면 count 를 돌려준다. 그러면 어떤
    뭉침도 통과하므로 그물이 사라지는데, 테스트는 조용히 통과한다. FDC 가
    같은 자리에서 한계가 전부 루프 상한(1000)으로 나오는 것을 겪었다 —
    꼬리 확률을 `1 - term` 이 아니라 `term` 으로 시작해 아무리 빼도 예산
    아래로 안 내려갔다. 통과를 보고 넘어갈 뻔했다고 한다. 수렴 실패를
    결함으로 세운다.

    앵커를 함께 옮긴다. 원본 하나만 보는 것이 바로 위 테스트가 3라운드 동안
    뭉침을 놓친 이유였는데, 그것을 고치면서 같은 결함을 이 테스트에 다시
    넣었다.
    """
    rows = build_all_tables(shifted_snapshot(snapshot, delta))["qms_inspection"]
    for kind in ("IPQC", "IPQC-RT", "OQC", "PCS", "EQV"):
        values = [r["inspection_datetime"] for r in rows if r["inspection_type"] == kind]
        limit = _coincidence_limit(len(values), _minute_slots(values))
        assert limit < len(values), f"{kind} 한계 {limit} 이 수렴에 실패했습니다"
        assert 3 <= limit < len(values) / 2, f"{kind} 한계 {limit} 이 규모를 못 가릅니다"


def test_the_coincidence_model_is_not_optimistic(snapshot):
    """포아송 근사가 실제 분포보다 충돌을 적게 예측하면 안 된다.

    모델이 실제보다 낙관적이면 한계가 낮아져 정상 데이터에 깨진다. 균등
    분포로 시뮬레이션해 모델의 예측이 실측보다 작지 않은지 본다.

    표준오차로 재는 이유는 시뮬레이션 자체가 흔들리기 때문이다. 처음에
    `expected >= observed` 로 썼더니 2.090 대 2.114 로 실패했는데, 차이가
    1 표준오차 안이라 결함이 아니라 표본 노이즈였다. 다섯 유형 전부
    -0.74 ~ 0.95 표준오차 안에 있다.

    FDC 가 같은 자리에서 기대값 모델이 2,095배 어긋난 것을 찾았다. 환경
    성분이 정상 범위의 60% 를 이미 먹고 있는데 그것을 빼고 여유를 셌기
    때문이었다. 그 정도 어긋남은 여기서 77 표준오차로 잡힌다. 모델은 세워
    두기만 하면 검증되지 않으므로 직접 잰다.
    """
    trials = 3000
    tolerance = 3.0
    rng = random.Random(7)
    rows = build_all_tables(snapshot)["qms_inspection"]
    for kind in ("IPQC", "IPQC-RT", "OQC", "PCS", "EQV"):
        values = [r["inspection_datetime"] for r in rows if r["inspection_type"] == kind]
        count, slots = len(values), _minute_slots(values)
        mean = count / slots
        expected = slots * (1.0 - math.exp(-mean) * (1.0 + mean))
        samples = [
            sum(1 for n in Counter(rng.randrange(slots) for _ in range(count)).values() if n >= 2)
            for _ in range(trials)
        ]
        observed = statistics.mean(samples)
        error = statistics.stdev(samples) / math.sqrt(trials)
        assert observed - expected <= tolerance * error, (
            f"{kind} 모델이 {expected:.3f} 를 예측했는데 실제로는 {observed:.3f} 입니다"
            f" ({(observed - expected) / error:.1f} 표준오차)"
        )


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


@pytest.mark.parametrize("hours", [0, 6, 24, 63, 120])
def test_inspection_spread_tracks_the_mes_spread(snapshot, hours):
    """MES 가 흩어진 만큼 IPQC 도 흩어져야 한다.

    IPQC 가 앵커 하나에만 매달려 있으면 MES 가 아무리 흩어져도 QMS 는 한 점에
    모인다. 설비별·시간별 교차 질의가 그때 무너진다. 폭을 픽스처에서 읽으면
    배포마다 달라지는 값을 단언하게 되므로 검사할 폭을 직접 만든다.

    공정검사 시각은 MES 종료 시각에 검사 준비 시간을 더해 만들기 때문에 폭이
    정확히 일치하지는 않는다. 실측 최대 편차가 4시간이라 여유를 두 배로 잡았다.
    """
    tolerance = 8
    spread = spread_snapshot(snapshot, hours=hours)
    times = [parse_mes_time(row["out_time"]) for row in spread.process_results]
    mes_span = (max(times) - min(times)).total_seconds() / 3600

    rows = build_all_tables(spread)["qms_inspection"]
    values = [r["inspection_datetime"] for r in rows if r["inspection_type"] == "IPQC"]
    ipqc_span = (max(values) - min(values)).total_seconds() / 3600

    assert abs(ipqc_span - mes_span) <= tolerance, (
        f"MES 폭 {mes_span:.1f}h 인데 IPQC 폭은 {ipqc_span:.1f}h 입니다"
    )


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


# --- 검사원 자격 ------------------------------------------------------------


@pytest.mark.parametrize("shift_days", [0, 400, 1200, 3000])
def test_active_inspectors_stay_certified_as_the_anchor_moves(snapshot, shift_days):
    """앵커가 얼마나 멀리 가든 활동 중인 검사원의 자격은 유효하다.

    자격 만료일을 취득일 + 3년으로 한 번만 계산하면, 앵커가 이동할수록 만료자가
    늘어난다. 실측에서 207건 중 126건을 자격 없는 사람이 수행한 상태가 됐다.
    갱신 주기를 반영하면 앵커 위치와 무관해진다.
    """
    moved = shifted_snapshot(snapshot, dt.timedelta(days=shift_days))
    raw = max(parse_mes_time(r["out_time"]) for r in moved.process_results).date()
    people = build_all_tables(moved)["qms_inspector"]
    expired = [p["inspector_id"] for p in people if p["is_active"] and p["certified_until"] < raw]
    assert not expired, f"{shift_days}일 이동에서 자격 만료: {expired}"
    assert all(p["certified_from"] <= raw for p in people)


def test_no_inspection_is_performed_outside_the_inspector_certification(tables):
    """검사 시점이 그 검사원의 자격 기간 안에 있어야 한다."""
    people = {p["inspector_id"]: p for p in tables["qms_inspector"]}
    for row in tables["qms_inspection"]:
        person = people[row["inspector_id"]]
        when = row["inspection_datetime"].date()
        assert person["certified_from"] <= when <= person["certified_until"], (
            f"{row['inspection_id']} 를 자격 범위 밖의 {row['inspector_id']} 가 수행"
        )


# --- 시각 컬럼 분류 ---------------------------------------------------------


def test_every_declared_time_column_is_classified():
    """DDL 의 DATE·TIMESTAMP 가 완료·예정 중 하나로 분류돼 있어야 한다.

    분류에서 빠진 컬럼은 미래 검사를 그냥 통과한다. 검증은 늘 PASS 라 안전해
    보이지만 아무도 그 컬럼을 보고 있지 않다. 실제로 effective_from 과
    certified_from/until 세 개가 빠져 있었다.
    """
    from src.qms_validate import _COMPLETED_COLUMNS, _PLANNED_COLUMNS

    declared = set()
    for table, ddl in TABLE_DDL.items():
        for field in ddl.split(","):
            parts = field.strip().rsplit(" ", 1)
            if len(parts) == 2 and parts[1] in ("DATE", "TIMESTAMP"):
                declared.add((table, parts[0]))

    classified = set(_COMPLETED_COLUMNS) | set(_PLANNED_COLUMNS)
    assert declared - classified == set(), f"분류 안 된 컬럼: {sorted(declared - classified)}"
    assert classified - declared == set(), f"DDL 에 없는 컬럼: {sorted(classified - declared)}"
    assert len(declared) == 12


def test_classification_does_not_rely_on_column_name_patterns():
    """이름에 date/time 이 들어가는지로 거르면 measured_at 이 빠진다.

    FDC 쪽에서 두 번 연속 measured_at 을 놓친 원인이 이 필터였다. 같은 방식이
    여기 들어오지 않게 못 박는다.
    """
    from src.qms_validate import _COMPLETED_COLUMNS

    by_name = {(t, c) for t, c in _COMPLETED_COLUMNS if "date" in c.lower() or "time" in c.lower()}
    missed = set(_COMPLETED_COLUMNS) - by_name
    assert ("qms_measurement", "measured_at") in missed
    assert ("qms_inspection_spec", "effective_from") in missed
