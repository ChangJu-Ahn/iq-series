"""MES 공정이력을 설비별 런 구간으로 바꾼다. 전부 순수 함수다.

판독값을 가동과 유휴로 나누고 이상을 어느 구간에 실을지 정하려면 "이 시각에
이 설비가 무엇을 하고 있었나"를 알아야 한다. 그 정보는 MES 공정이력의
in_time/out_time 에만 있다.

구간은 [start, end) 반열림이다. 한 설비의 두 런이 경계에서 맞닿아도 한 시각이
양쪽에 속하지 않는다.

Run.lot_id 는 이상 배치 계산에만 쓰고 판독값에는 싣지 않는다. FDC 가 로트를
들고 있으면 실습자가 Eventhouse 하나로 답을 내버려서 MES 에 물을 이유가
사라진다. 자세한 근거는 계획서 "설계 결정" 절에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Run:
    eqp_id: str
    lot_id: str
    step_code: str
    start: datetime
    end: datetime
    defect_code: str | None


def parse_ts(value: str) -> datetime:
    """MES 시각 문자열을 tz-aware UTC datetime 으로."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def runs_by_equipment(facts) -> dict[str, list[Run]]:
    """설비별 런 구간. start 오름차순.

    eqp_id 가 없는 공정이력은 버린다. METRO(계측)는 설비를 배정받지 않으므로
    센서 데이터가 존재할 수 없다. 모든 공정에 FDC 가 붙어 있지는 않다는 것
    자체가 현실적인 교육 소재다.
    """
    grouped: dict[str, list[Run]] = {}
    for row in facts.process_results:
        eqp_id = row.get("eqp_id")
        if not eqp_id:
            continue
        grouped.setdefault(eqp_id, []).append(
            Run(
                eqp_id=eqp_id,
                lot_id=row["lot_id"],
                step_code=row["step_code"],
                start=parse_ts(row["in_time"]),
                end=parse_ts(row["out_time"]),
                defect_code=row.get("defect_code") or None,
            )
        )
    for runs in grouped.values():
        runs.sort(key=lambda r: r.start)
    return grouped


def run_at(runs: list[Run], moment: datetime) -> Run | None:
    """이 시각에 돌고 있던 런. 없으면 None.

    선형 탐색이다. 설비당 런이 20건 미만이라 이분 탐색을 넣을 이유가 없다.
    start 오름차순이므로 시작이 moment 를 지나면 더 볼 필요가 없다.
    """
    for run in runs:
        if run.start <= moment < run.end:
            return run
        if run.start > moment:
            break
    return None


def span(facts) -> tuple[datetime, datetime]:
    """공정이력 전체가 걸쳐 있는 구간.

    설비가 없는 행도 포함한다. 생성 범위의 시작점을 정하는 용도라
    "MES 가 아는 가장 이른 시각"이어야 한다.
    """
    rows = facts.process_results
    if not rows:
        raise ValueError("공정이력이 비어 있어 생성 구간을 정할 수 없습니다.")
    return (
        min(parse_ts(r["in_time"]) for r in rows),
        max(parse_ts(r["out_time"]) for r in rows),
    )
