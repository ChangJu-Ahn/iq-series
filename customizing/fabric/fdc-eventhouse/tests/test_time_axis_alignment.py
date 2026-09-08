"""MES 공정이력과 FDC 센서가 같은 시간축 위에 있는지 전수로 확인한다.

FDC 의 존재 이유는 단 하나다. MES 가 "LOT0001 의 2번째 공정은 EQP-PHOT01 에서
09:14~10:09 에 돌았다" 고 말할 때, 그 55분 동안의 센서 값이 실제로 있어야 한다.
없으면 에이전트는 "그 시간 데이터가 없습니다" 라고 답하고 실습이 거기서 끝난다.

다른 테스트들은 이 정합을 **표본으로만** 본다.
:func:`test_running_row_count_matches_grid` 는 가장 바쁜 설비의 **첫 런 3분**만
센다. 84 개 런 중 한 곳이 통째로 비어도 통과한다.

## 왜 픽스처 JSON 을 직접 읽는가

이 모듈은 `runs_by_equipment` 도 `span` 도 `parse_ts` 도 쓰지 않는다. 원시
JSON 에서 런 구간을 직접 만든다. 번거롭지만 그래야만 한다.

처음에는 `runs_by_equipment(facts)` 로 기대값을 만들었다. 그리고 돌연변이를
넣어봤다 -- 설비별 마지막 런을 통째로 버리게 했다. **일곱 테스트가 전부
통과했다.** 생성 쪽과 검증 쪽이 같은 함수를 부르니 돌연변이가 양쪽에 똑같이
적용되어 상쇄된 것이다. `span` 을 한 시간 늦추는 돌연변이도 같은 이유로
통과했다.

자기 자신과 비교하는 어서션은 실패할 수 없다. 기대값은 검증 대상 밖에서
와야 한다.

## 앵커

MES 앵커는 `utcNow()` 로 **배포할 때마다** 움직인다. 스케줄 전체가 평행이동하고
구간 폭과 내부 관계만 남는다. 그래서 여기에도 벽시계 상수가 없다. 픽스처를
다시 떠도 그대로 돌아야 한다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.fdc_generator import build_readings

FIXTURE = Path(__file__).parent / "fixtures" / "mes_facts.json"

RUNNING = "Run"
ALARM = "Alarm"


def _ts(value: str) -> datetime:
    """`src.fdc_runs.parse_ts` 를 쓰지 않고 직접 파싱한다.

    검증 대상 밖에서 기대값을 만들기 위해서다. 모듈 docstring 참고.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@pytest.fixture(scope="module")
def raw_runs():
    """픽스처 JSON 에서 직접 만든 설비별 (시작, 끝, 불량코드) 목록.

    설비가 없는 행(METRO)은 뺀다. 센서가 존재할 수 없기 때문이다.
    """
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    for row in payload["process_results"]:
        eqp_id = row.get("eqp_id")
        if not eqp_id:
            continue
        grouped[eqp_id].append(
            (_ts(row["in_time"]), _ts(row["out_time"]), row.get("defect_code") or None)
        )
    for runs in grouped.values():
        runs.sort()
    return dict(grouped)


@pytest.fixture(scope="module")
def raw_span():
    """픽스처 JSON 에서 직접 계산한 (가장 이른 in_time, 가장 늦은 out_time)."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = payload["process_results"]
    return (
        min(_ts(r["in_time"]) for r in rows),
        max(_ts(r["out_time"]) for r in rows),
    )


@pytest.fixture(scope="module")
def full_span_rows(facts, raw_span):
    """공정이력 구간 전체의 센서 행.

    생성 창도 원시 구간으로 준다. `span()` 이 창을 잘못 잡으면 여기서 드러난다.
    """
    lo, hi = raw_span
    return build_readings(facts, lo, hi)


@pytest.fixture(scope="module")
def samples_by_equipment(full_span_rows):
    """설비별 (시각, 가동상태, 값판정) 목록. 시각 오름차순."""
    grouped = defaultdict(list)
    for row in full_span_rows:
        ts = row["reading_ts"]
        moment = _ts(ts) if isinstance(ts, str) else ts.astimezone(timezone.utc)
        grouped[row["eqp_id"]].append((moment, row["run_status"], row["status"]))
    for samples in grouped.values():
        samples.sort()
    return grouped


def test_every_run_is_covered_by_sensor_readings(raw_runs, samples_by_equipment):
    """설비를 배정받은 런은 하나도 빠짐없이 센서가 있어야 한다.

    이게 깨지면 "그 로트의 그 공정에서 온도가 어땠나" 라는 질문이 빈 답을
    받는다. 실습의 첫 단추다.
    """
    empty = []
    for eqp_id, runs in raw_runs.items():
        samples = samples_by_equipment.get(eqp_id, [])
        for start, end, _ in runs:
            if not any(start <= t < end for t, _, _ in samples):
                minutes = (end - start).total_seconds() / 60
                empty.append(f"{eqp_id} {start.isoformat()} ({minutes:.0f}분)")

    assert not empty, f"센서가 하나도 없는 런 {len(empty)}개: {empty[:5]}"


def test_readings_inside_a_run_are_all_marked_running(raw_runs, samples_by_equipment):
    """런 구간 안의 값은 전부 ``run_status="Run"`` 이어야 한다.

    FDC 는 `lot_id` 를 싣지 않는다. 어떤 값이 실제 가공 중에 나온 것인지
    구별하는 유일한 단서가 이 컬럼이다. 여기가 틀리면 에이전트가 대기 중
    상온 데이터를 공정 데이터로 보고 답한다.
    """
    wrong = []
    for eqp_id, runs in raw_runs.items():
        samples = samples_by_equipment.get(eqp_id, [])
        for start, end, _ in runs:
            bad = [t for t, status, _ in samples if start <= t < end and status != RUNNING]
            if bad:
                wrong.append(f"{eqp_id} {start.isoformat()} {len(bad)}건")

    assert not wrong, f"런 안인데 Run 이 아닌 표본: {wrong[:5]}"


def test_running_readings_never_appear_outside_a_run(raw_runs, samples_by_equipment):
    """역방향. 런 밖에 ``Run`` 이 새면 안 된다.

    앞 테스트만으로는 부족하다. 모든 표본을 무조건 ``Run`` 으로 찍어도
    앞 테스트는 통과한다. 두 방향을 같이 봐야 경계가 고정된다.
    """
    leaked = 0
    for eqp_id, samples in samples_by_equipment.items():
        runs = raw_runs.get(eqp_id, [])
        for moment, status, _ in samples:
            if status == RUNNING and not any(s <= moment < e for s, e, _ in runs):
                leaked += 1

    assert leaked == 0, f"런 밖에 Run 표시가 {leaked}건 샜다"


def test_alarms_only_happen_inside_runs_that_mes_calls_defective(
    raw_runs, samples_by_equipment
):
    """Alarm 이 뜬 런은 MES 가 불량으로 기록한 런이어야 한다.

    실습의 핵심 질문이 "설비 이상이 불량으로 이어졌나" 다. 헛경보가 섞이면
    그 질문의 답이 흐려진다. 반대 방향(불량인데 조용한 런)은 일부러 남겨
    둔다 -- 센서로 설명되지 않는 불량이 있어야 QMS 를 볼 이유가 생긴다.
    """
    false_alarms = []
    for eqp_id, runs in raw_runs.items():
        samples = samples_by_equipment.get(eqp_id, [])
        for start, end, defect in runs:
            if defect:
                continue
            noisy = sum(
                1 for t, _, value_status in samples
                if start <= t < end and value_status == ALARM
            )
            if noisy:
                false_alarms.append(f"{eqp_id} {start.isoformat()} {noisy}건")

    assert not false_alarms, f"불량이 아닌 런에서 Alarm: {false_alarms[:5]}"


def test_some_defective_runs_stay_quiet(raw_runs, samples_by_equipment):
    """모든 불량이 센서에 잡히지는 않아야 한다.

    전부 잡히면 QMS 를 볼 이유가 사라진다. FDC 하나로 끝나는 실습은
    세 시스템을 잇는 실습이 아니다.
    """
    quiet = loud = 0
    for eqp_id, runs in raw_runs.items():
        samples = samples_by_equipment.get(eqp_id, [])
        for start, end, defect in runs:
            if not defect:
                continue
            if any(
                start <= t < end and value_status == ALARM
                for t, _, value_status in samples
            ):
                loud += 1
            else:
                quiet += 1

    assert loud, "센서에 잡히는 불량이 하나도 없다 — 상관 시나리오가 죽었다"
    assert quiet, "모든 불량이 센서에 잡힌다 — QMS 를 볼 이유가 없어졌다"


def test_generator_span_matches_the_process_history(facts, raw_span):
    """`span()` 이 공정이력 구간을 그대로 돌려줘야 한다.

    센서 생성 창이 여기서 나온다. 창이 어긋나면 앞뒤가 잘린 채로도 다른
    테스트는 통과한다 -- 잘린 창 안에서는 모든 것이 정합하기 때문이다.
    """
    from src.fdc_runs import span

    assert span(facts) == raw_span


def test_sensor_coverage_fills_the_whole_process_history(full_span_rows, raw_span):
    """센서가 공정이력 구간을 벗어나거나 모자라면 안 된다."""
    lo, hi = raw_span
    moments = [
        _ts(r["reading_ts"]) if isinstance(r["reading_ts"], str)
        else r["reading_ts"].astimezone(timezone.utc)
        for r in full_span_rows
    ]

    assert min(moments) >= lo, "공정이력 시작 전의 센서 값이 있다"
    assert max(moments) <= hi, "공정이력 끝 이후의 센서 값이 있다"

    # 한 격자(30초) 안에서 시작하고 끝나야 한다.
    assert (min(moments) - lo).total_seconds() <= 60, (
        f"센서 시작이 공정이력 시작보다 {(min(moments) - lo).total_seconds():.0f}초 늦다"
    )
    assert (hi - max(moments)).total_seconds() <= 60, (
        f"센서 끝이 공정이력 끝보다 {(hi - max(moments)).total_seconds():.0f}초 이르다"
    )


def test_every_run_bearing_tool_appears_in_the_sensor_stream(
    raw_runs, samples_by_equipment
):
    """런이 있는 설비는 전부 센서에 나와야 한다.

    설비 하나가 통째로 빠지는 것은 개별 런이 비는 것보다 눈에 안 띈다.
    행 수는 여전히 십만 단위라 총량 검사로는 드러나지 않는다.
    """
    missing = sorted(set(raw_runs) - set(samples_by_equipment))
    assert not missing, f"런은 있는데 센서가 없는 설비: {missing}"

    extra = sorted(set(samples_by_equipment) - set(raw_runs))
    assert not extra, f"MES 가 모르는 설비의 센서: {extra}"


def test_metrology_steps_have_no_sensor_data(samples_by_equipment):
    """계측(METRO)은 설비를 배정받지 않으므로 FDC 에 없어야 한다.

    이건 결함이 아니라 설계다. 다만 에이전트가 "METRO 공정의 온도" 를 물으면
    답이 없다는 뜻이므로, 문서가 이 한계를 밝혀야 한다.
    """
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    toolless = [p for p in payload["process_results"] if not p.get("eqp_id")]
    assert toolless, "픽스처에 설비 없는 공정이 하나도 없다 — 픽스처가 바뀌었다"

    assert None not in samples_by_equipment
    assert "" not in samples_by_equipment
