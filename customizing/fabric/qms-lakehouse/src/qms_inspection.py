"""qms_inspection 207행.

구성은 스펙 6.5절 그대로다.
  IPQC     91  MES 공정이력 1:1
  IPQC-RT  40  결함 보유(35) ∪ non-Pass(7), 교집합 2
  OQC      24  Done 로트 6 × 4 배치
  PCS      36  제품 4 × 공정 9
  EQV      16  설비 8 × 2회
"""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import (
    MesSnapshot,
    mes_anchor,
    not_after,
    parse_mes_time,
    window_start,
)
from src.qms_reference import SEED_INSPECTION

INSPECTION_COUNTS = {"IPQC": 91, "IPQC-RT": 40, "OQC": 24, "PCS": 36, "EQV": 16}

# 정기 검사는 주간 근무조 안에서 수행한다. 시작 시각은 검사 종류마다 다르고
# 여기서는 그 근무조가 몇 시간짜리인지만 정한다.
_SHIFT_LENGTH_HOURS = 8

_TEAM_BY_TYPE = {
    "IPQC": "계측팀",
    "IPQC-RT": "계측팀",
    "OQC": "출하검사팀",
    "PCS": "품질보증팀",
    "EQV": "신뢰성팀",
}

_BASIS = {
    "합격": "전 항목 규격 내, 관리한계 이탈 없음",
    "조건부합격": "일부 항목 관리한계 근접, 후속 공정 모니터링 조건부 승인",
    "불합격": "규격 이탈 확인, 부적합 보고서 발행",
}


def mes_result_index(snapshot: MesSnapshot) -> dict[int, dict]:
    return {row["id"]: row for row in snapshot.process_results}


def _lot_completion(snapshot: MesSnapshot) -> dict[str, dt.datetime]:
    """로트별 마지막 공정 종료 시각. 출하검사의 출발점이다."""
    done: dict[str, dt.datetime] = {}
    for row in snapshot.process_results:
        lot_id = row.get("lot_id")
        if not lot_id:
            continue
        moment = parse_mes_time(row["out_time"])
        if lot_id not in done or moment > done[lot_id]:
            done[lot_id] = moment
    return done


def _after(start: dt.datetime, minutes: int, as_of: dt.datetime) -> dt.datetime:
    """start 로부터 minutes 뒤. 단 현재(앵커)를 넘지 않는다.

    검사는 공정이 끝난 뒤에 하므로 시각이 앞으로 간다. 그런데 앵커 직전에 끝난
    공정은 그 뒤에 검사할 시간이 아직 없다. 자르지 않으면 판정이 채워진 검사가
    미래에 놓인다.
    """
    return not_after(start + dt.timedelta(minutes=minutes), as_of)


def _within_window(
    rng: random.Random, window: tuple[dt.datetime, dt.datetime], shift_hour: int
) -> dt.datetime:
    """생산 구간 안, 주간 근무조 시간대의 한 시각.

    출하검사(OQC)·공정능력조사(PCS)·설비적격성(EQV)은 특정 공정이력에서
    파생되지 않고 정기적으로 수행한다. 그래도 생산 구간 밖에 두면 안 된다.
    앞으로 나가면 아직 오지 않은 날짜에 완료된 검사가 생기고, 뒤로 물러나면
    이번 생산과 무관한 기록이 된다.

    구간 안에서 근무조 시간대에 드는 시각만 후보로 모아 그중 하나를 고른다.
    범위를 벗어난 값을 양끝으로 자르는 방식을 쓰면 잘린 행들이 경계 시각
    하나에 그대로 쌓인다. 후보를 미리 거르면 그 뭉침이 생기지 않는다.
    """
    start, end = window
    span_hours = int((end - start).total_seconds() // 3600)
    candidates = [
        moment
        for offset in range(span_hours + 1)
        if (moment := start + dt.timedelta(hours=offset)) + dt.timedelta(minutes=59) <= end
        and shift_hour <= moment.hour < shift_hour + _SHIFT_LENGTH_HOURS
    ]
    if not candidates:
        # 구간이 근무조 하나보다 짧은 경우. 시간대를 포기하고 구간 안에서 고른다.
        seconds = max(int((end - start).total_seconds()), 0)
        return start + dt.timedelta(seconds=rng.randint(0, seconds))
    return rng.choice(candidates) + dt.timedelta(minutes=rng.randint(0, 59))


def _bounded(rng: random.Random, low: int, high: int, cap: int) -> int:
    """low..high 범위 정수를 cap 이하로 자른다. 수량 정합 검증을 항상 통과시킨다."""
    high = min(high, cap)
    low = min(low, high)
    return rng.randint(low, high)


def _sampling(rng: random.Random, wafer_cap: int) -> tuple[int, int]:
    """(inspected_wafer_qty, sample_size). sample_size 는 웨이퍼 매수 × 3포인트."""
    wafers = min(rng.randint(3, 5), wafer_cap)
    return wafers, min(wafers * 3, wafer_cap)


class _IdGen:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"INS-2026-{self.n:04d}"


def build_inspections(snapshot: MesSnapshot, inspectors: list[dict]) -> list[dict]:
    as_of = mes_anchor(snapshot)
    window = (window_start(snapshot), as_of)
    rng = random.Random(SEED_INSPECTION)
    ids = _IdGen()
    by_team: dict[str, list[dict]] = {}
    for person in inspectors:
        by_team.setdefault(person["team_ko"], []).append(person)

    lots = {lot["lot_id"]: lot for lot in snapshot.lots}
    results = sorted(snapshot.process_results, key=lambda r: r["id"])

    rows: list[dict] = []
    rows.extend(_build_ipqc(rng, ids, results, lots, by_team, as_of))
    rows.extend(_build_retest(rng, ids, results, lots, by_team, as_of))
    rows.extend(_build_oqc(rng, ids, snapshot, by_team, as_of))
    rows.extend(_build_pcs(rng, ids, snapshot, by_team, window))
    rows.extend(_build_eqv(rng, ids, snapshot, by_team, window))
    return rows


def _row(
    ids: _IdGen,
    *,
    inspection_type: str,
    lot_id,
    product_code,
    product_name,
    step_code,
    step_name,
    eqp_id,
    mes_id,
    inspector: dict,
    when: dt.datetime,
    wafers: int,
    sample_size: int,
    judgment: str,
    defect_found_qty: int,
    measurement_count: int,
    has_ncr: bool,
    remark: str,
) -> dict:
    return {
        "inspection_id": ids.next(),
        "inspection_type": inspection_type,
        "lot_id": lot_id,
        "product_code": product_code,
        "product_name": product_name,
        "step_code": step_code,
        "step_name": step_name,
        "eqp_id": eqp_id,
        "mes_process_result_id": mes_id,
        "inspector_id": inspector["inspector_id"],
        "inspection_datetime": when,
        "sample_size": sample_size,
        "inspected_wafer_qty": wafers,
        "judgment": judgment,
        "judgment_basis_ko": _BASIS[judgment],
        "defect_found_qty": defect_found_qty,
        "measurement_count": measurement_count,
        "has_nonconformance": has_ncr,
        "remark_ko": remark,
    }


def _build_ipqc(rng, ids, results, lots, by_team, as_of) -> list[dict]:
    """MES 91건 1:1. 판정 분포는 스펙 6.1절을 그대로 따른다."""
    fails = [r for r in results if r["result"] == "Fail"]
    reworks = [r for r in results if r["result"] == "Rework"]
    pass_def = [r for r in results if r["result"] == "Pass" and r.get("defect_code")]
    pass_clean = [r for r in results if r["result"] == "Pass" and not r.get("defect_code")]

    plan: dict[int, tuple[str, bool]] = {}
    for row in fails:
        plan[row["id"]] = ("불합격", True)

    shuffled = list(reworks)
    rng.shuffle(shuffled)
    plan[shuffled[0]["id"]] = ("불합격", True)
    # 두번째 재작업건은 MES 불량코드가 있을 때만 조건부합격으로 둔다. 코드가
    # 없는데 조건부합격+결함검출이 겹치면 장치③ 신호(코드 없음+결함검출+불합격
    # 아님)와 우연히 일치해버린다.
    second = shuffled[1]
    plan[second["id"]] = ("조건부합격", True) if second.get("defect_code") else ("불합격", True)

    shuffled = list(pass_def)
    rng.shuffle(shuffled)
    for row in shuffled[:10]:
        plan[row["id"]] = ("조건부합격", True)
    for row in shuffled[10:]:
        plan[row["id"]] = ("합격", False)

    # 장치①과 장치③은 같은 51건 풀에서 나온다. 슬라이스를 겹치지 않게 잘라
    # 서로소를 구조적으로 보장한다. 구분은 defect_found_qty 0 / >0 로 이뤄진다.
    shuffled = list(pass_clean)
    rng.shuffle(shuffled)
    device1 = {r["id"] for r in shuffled[:3]}
    device3 = {r["id"] for r in shuffled[3:7]}
    for row in shuffled[:3]:
        plan[row["id"]] = ("불합격", True)
    for row in shuffled[3:7]:
        plan[row["id"]] = ("조건부합격", True)
    for row in shuffled[7:]:
        plan[row["id"]] = ("합격", False)

    rows = []
    for mes in results:
        judgment, has_ncr = plan[mes["id"]]
        wafers, sample_size = _sampling(rng, mes["in_qty"])
        if mes["id"] in device1:
            found = 0
            remark = "생산 통과분에 대해 품질 홀드 적용. 계측 재현성 확인 필요"
        elif mes["id"] in device3:
            found = _bounded(rng, 1, 4, sample_size)
            remark = "설비 판정에는 없던 결함을 검사에서 검출"
        elif judgment == "합격":
            found = 0
            remark = "정상 공정검사"
        else:
            found = _bounded(rng, 2, 9, sample_size)
            remark = "공정검사 중 결함 검출"
        rows.append(
            _row(
                ids,
                inspection_type="IPQC",
                lot_id=mes["lot_id"],
                product_code=lots[mes["lot_id"]]["product_code"],
                product_name=lots[mes["lot_id"]]["product_name"],
                step_code=mes["step_code"],
                step_name=mes["step_name"],
                eqp_id=mes["eqp_id"],
                mes_id=mes["id"],
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["IPQC"]]),
                when=_after(parse_mes_time(mes["out_time"]), rng.randint(10, 240), as_of),
                wafers=wafers,
                sample_size=sample_size,
                judgment=judgment,
                defect_found_qty=found,
                measurement_count=0,
                has_ncr=has_ncr,
                remark=remark,
            )
        )
    return rows


def _build_retest(rng, ids, results, lots, by_team, as_of) -> list[dict]:
    """결함 보유 35 ∪ non-Pass 7 = 40건 재검사. 측정치 3점을 남긴다."""
    targets = [r for r in results if r.get("defect_code") or r["result"] != "Pass"]
    clean_mes = [r for r in targets if not r.get("defect_code")]
    coded_mes = [r for r in targets if r.get("defect_code")]

    # MES 불량코드가 없는 재검사 건은 전부 불합격으로 둔다. 그래야 장치③
    # (MES 코드 없음 + 결함 검출 + 불합격 아님)과 절대 충돌하지 않는다.
    plan = {r["id"]: ("불합격", True) for r in clean_mes}
    shuffled = list(coded_mes)
    rng.shuffle(shuffled)
    remaining = 10 - len(plan)
    for row in shuffled[:remaining]:
        plan[row["id"]] = ("불합격", True)
    for row in shuffled[remaining:remaining + 12]:
        plan[row["id"]] = ("조건부합격", False)
    for row in shuffled[remaining + 12:]:
        plan[row["id"]] = ("합격", False)

    rows = []
    for mes in targets:
        judgment, has_ncr = plan[mes["id"]]
        wafers, sample_size = _sampling(rng, mes["in_qty"])
        found = 0 if judgment == "합격" else _bounded(rng, 1, 8, sample_size)
        rows.append(
            _row(
                ids,
                inspection_type="IPQC-RT",
                lot_id=mes["lot_id"],
                product_code=lots[mes["lot_id"]]["product_code"],
                product_name=lots[mes["lot_id"]]["product_name"],
                step_code=mes["step_code"],
                step_name=mes["step_name"],
                eqp_id=mes["eqp_id"],
                mes_id=mes["id"],
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["IPQC-RT"]]),
                when=_after(parse_mes_time(mes["out_time"]), rng.randint(300, 720), as_of),
                wafers=wafers,
                sample_size=sample_size,
                judgment=judgment,
                defect_found_qty=found,
                measurement_count=3,
                has_ncr=has_ncr,
                remark="재검사 수행. 계측 3점 기록",
            )
        )
    return rows


def _build_oqc(rng, ids, snapshot, by_team, as_of) -> list[dict]:
    """Done 로트 6건을 출하 배치 4개로 나눠 24건.

    출하검사는 로트 생산이 끝난 뒤 4~24시간 안에 한다. 전역 날짜가 아니라
    그 로트의 마지막 공정 종료 시각에서 유도하므로 로트마다 시점이 다르다.
    아직 생산 중인 로트(Running/Hold)에는 출하검사가 없다. "출하검사 대기
    로트"가 자연히 생기고, 이것 자체가 교차 질의 소재가 된다.
    """
    done = sorted((l for l in snapshot.lots if l["status"] == "Done"), key=lambda l: l["lot_id"])
    completed = _lot_completion(snapshot)
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    plan_slots = [(lot, batch) for lot in done for batch in range(1, 5)]
    missing = [lot["lot_id"] for lot, _ in plan_slots if lot["lot_id"] not in completed]
    if missing:
        raise ValueError(f"Done 로트인데 MES 공정이력이 없습니다: {sorted(set(missing))}")
    order = list(range(len(plan_slots)))
    rng.shuffle(order)
    judgments = {}
    for rank, slot in enumerate(order):
        if rank < 2:
            judgments[slot] = ("불합격", True)
        elif rank < 6:
            judgments[slot] = ("조건부합격", True)
        else:
            judgments[slot] = ("합격", False)

    rows = []
    for slot, (lot, batch) in enumerate(plan_slots):
        judgment, has_ncr = judgments[slot]
        wafers, sample_size = _sampling(rng, lot["wafer_qty"])
        found = 0 if judgment == "합격" else _bounded(rng, 2, 9, sample_size)
        rows.append(
            _row(
                ids,
                inspection_type="OQC",
                lot_id=lot["lot_id"],
                product_code=lot["product_code"],
                product_name=lot["product_name"],
                step_code=lot["current_step"],
                step_name=step_names[lot["current_step"]],
                eqp_id=None,
                mes_id=None,
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["OQC"]]),
                when=_after(completed[lot["lot_id"]], rng.randint(240, 1440), as_of),
                wafers=wafers,
                sample_size=sample_size,
                judgment=judgment,
                defect_found_qty=found,
                measurement_count=0,
                has_ncr=has_ncr,
                remark=f"출하 배치 {batch}/4 검사",
            )
        )
    return rows


def _build_pcs(rng, ids, snapshot, by_team, window) -> list[dict]:
    """제품 4 × 공정 9 = 36건 정기 공정능력 조사. 로트에 매이지 않는다."""
    products = sorted(snapshot.products, key=lambda p: p["product_code"])
    steps = sorted(snapshot.route, key=lambda s: s["seq"])
    slots = [(p, s) for p in products for s in steps]
    order = list(range(len(slots)))
    rng.shuffle(order)
    flagged = set(order[:7])

    rows = []
    for slot, (product, step) in enumerate(slots):
        judgment, has_ncr = ("조건부합격", True) if slot in flagged else ("합격", False)
        rows.append(
            _row(
                ids,
                inspection_type="PCS",
                lot_id=None,
                product_code=product["product_code"],
                product_name=product["product_name"],
                step_code=step["step_code"],
                step_name=step["step_name"],
                eqp_id=None,
                mes_id=None,
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["PCS"]]),
                when=_within_window(rng, window, 9),
                wafers=3,
                sample_size=9,
                judgment=judgment,
                defect_found_qty=0,
                measurement_count=3,
                has_ncr=has_ncr,
                remark="정기 공정능력 조사. Cpk 산출용 3점 계측"
                if not has_ncr
                else "정기 공정능력 조사에서 Cpk 목표 1.33 미달",
            )
        )
    return rows


def _build_eqv(rng, ids, snapshot, by_team, window) -> list[dict]:
    """설비 8대 × 2회 = 16건 설비 검증. 제품과 로트 모두 무관하다."""
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    slots = [(e, run) for e in snapshot.equipment for run in (1, 2)]
    order = list(range(len(slots)))
    rng.shuffle(order)
    flagged = set(order[:2])

    rows = []
    for slot, (equipment, run) in enumerate(slots):
        judgment, has_ncr = ("불합격", True) if slot in flagged else ("합격", False)
        rows.append(
            _row(
                ids,
                inspection_type="EQV",
                lot_id=None,
                product_code=None,
                product_name=None,
                step_code=equipment["step_code"],
                step_name=step_names[equipment["step_code"]],
                eqp_id=equipment["eqp_id"],
                mes_id=None,
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["EQV"]]),
                when=_within_window(rng, window, 7),
                wafers=2,
                sample_size=6,
                judgment=judgment,
                defect_found_qty=_bounded(rng, 1, 5, 6) if has_ncr else 0,
                measurement_count=2,
                has_ncr=has_ncr,
                remark=f"설비 정기 검증 {run}회차",
            )
        )
    return rows
