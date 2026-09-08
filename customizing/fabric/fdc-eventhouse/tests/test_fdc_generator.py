import math
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.fdc_anomaly import build_profiles, hinted_sensors
from src.fdc_generator import (
    ALARM,
    NORMAL,
    WARNING,
    align_to_grid,
    build_readings,
    classify,
    grid_timestamps,
    reading_value,
)
from src.fdc_runs import runs_by_equipment
from src.fdc_sensors import SAMPLE_INTERVAL_SEC, sensor_by_code, sensors_for

T0 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def profiles(facts):
    return build_profiles(facts)


@pytest.fixture(scope="module")
def day_rows(facts, busy_window):
    """공정이력이 있는 24시간. 이 규모에서만 드러나는 성질을 검사한다."""
    return build_readings(facts, *busy_window)


def test_align_to_grid_floors_to_interval():
    assert align_to_grid(datetime(2026, 9, 4, 12, 0, 47, tzinfo=timezone.utc)).second == 30
    assert align_to_grid(datetime(2026, 9, 4, 12, 0, 29, tzinfo=timezone.utc)).second == 0


def test_align_to_grid_assumes_utc_for_naive():
    assert align_to_grid(datetime(2026, 9, 4, 12, 0, 47)).tzinfo == timezone.utc


def test_grid_excludes_start_includes_end():
    stamps = grid_timestamps(T0, T0 + timedelta(minutes=3))
    assert stamps[0] == T0 + timedelta(seconds=SAMPLE_INTERVAL_SEC)
    assert stamps[-1] == T0 + timedelta(minutes=3)
    assert len(stamps) == 6


def test_grid_empty_when_end_before_start():
    assert grid_timestamps(T0, T0 - timedelta(minutes=1)) == []


def test_grid_empty_within_one_interval():
    assert grid_timestamps(T0, T0 + timedelta(seconds=29)) == []


def test_grid_is_anchored_to_epoch_not_to_start():
    """실행 시각이 어긋나도 격자는 같아야 한다. 백필과 라이브가 이어지는 근거다."""
    a = grid_timestamps(T0 + timedelta(seconds=7), T0 + timedelta(minutes=3))
    b = grid_timestamps(T0 + timedelta(seconds=19), T0 + timedelta(minutes=3))
    assert a == b


def test_reading_value_is_deterministic(profiles):
    profile = profiles["EQP-DIFF01"]
    sensor = sensor_by_code(profile.eqp_type, "CHAMBER_TEMP")
    values = {
        reading_value(sensor, profile.eqp_id, T0, profile, anchor=T0) for _ in range(5)
    }
    assert len(values) == 1


def test_reading_value_stable_across_processes():
    """3분마다 새 프로세스로 도는 잡이 같은 값을 내야 한다."""
    code = (
        "import sys, json; sys.path.insert(0, '.'); "
        "from datetime import datetime, timezone; "
        "from src.mes_probe import MesFacts; "
        "from src.fdc_anomaly import build_profiles; "
        "from src.fdc_generator import reading_value; "
        "from src.fdc_runs import span; "
        "from src.fdc_sensors import sensor_by_code; "
        "f = MesFacts.from_dict(json.load(open('tests/fixtures/mes_facts.json'))); "
        "p = build_profiles(f)['EQP-DIFF01']; "
        "s = sensor_by_code(p.eqp_type, 'CHAMBER_TEMP'); "
        "print(reading_value(s, p.eqp_id, datetime(2026,9,4,12,0,tzinfo=timezone.utc), p,"
        " anchor=span(f)[1]))"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(outs) == 1, outs


def test_backfill_and_live_agree_on_overlap(facts, busy_window):
    """같은 타임스탬프를 백필로 만들든 라이브로 만들든 값이 같아야 한다."""
    base = busy_window[0]
    live = build_readings(facts, base + timedelta(minutes=3), base + timedelta(minutes=6))
    backfill = build_readings(facts, base - timedelta(hours=2), base + timedelta(minutes=6))
    key = lambda r: (r["reading_ts"], r["eqp_id"], r["sensor_code"])
    back_by_key = {key(r): r["value"] for r in backfill}
    assert live
    for row in live:
        assert back_by_key[key(row)] == row["value"]


def test_running_row_count_matches_grid(facts):
    """가동 중인 설비 하나는 격자 한 칸마다 그 유형의 센서를 전부 낸다."""
    runs = runs_by_equipment(facts)
    busiest = max(runs, key=lambda k: len(runs[k]))
    run = runs[busiest][0]
    start = align_to_grid(run.start)
    rows = [r for r in build_readings(facts, start, start + timedelta(minutes=3))
            if r["eqp_id"] == busiest and r["run_status"] == RUNNING]
    eqp_type = next(e["eqp_type"] for e in facts.equipment if e["eqp_id"] == busiest)
    assert len(rows) == len(sensors_for(eqp_type)) * 6 == 36


def test_rows_have_no_duplicate_keys(day_rows):
    keys = Counter((r["reading_ts"], r["eqp_id"], r["sensor_code"]) for r in day_rows)
    assert not [k for k, n in keys.items() if n > 1]


def test_rows_carry_no_lot_columns(day_rows):
    """FDC 는 로트를 모른다. 이 경계가 무너지면 교차 질의 실습이 사라진다."""
    forbidden = {"lot_id", "product_code", "wafer_qty", "defect_code", "judgment", "result", "operator"}
    assert not (forbidden & set(day_rows[0]))


def test_rows_have_expected_columns(day_rows):
    assert set(day_rows[0]) == {
        "reading_ts", "eqp_id", "eqp_type", "step_code", "run_status",
        "sensor_code", "value", "unit", "status",
    }


def test_all_timestamps_are_utc_and_aligned(day_rows):
    for row in day_rows[:500]:
        assert row["reading_ts"].tzinfo == timezone.utc
        assert int(row["reading_ts"].timestamp()) % SAMPLE_INTERVAL_SEC == 0


def test_classify_boundaries():
    sensor = sensor_by_code("Furnace", "CHAMBER_TEMP")  # normal 1040~1060, alarm 1030~1070
    assert classify(sensor, 1050.0) == NORMAL
    assert classify(sensor, 1040.0) == NORMAL
    assert classify(sensor, 1060.0) == NORMAL
    assert classify(sensor, 1039.9) == WARNING
    assert classify(sensor, 1069.9) == WARNING
    assert classify(sensor, 1029.9) == ALARM
    assert classify(sensor, 1070.1) == ALARM


def test_status_matches_recomputed_classification(day_rows):
    by_type = {}
    for row in day_rows[::37]:
        sensor = by_type.setdefault(
            (row["eqp_type"], row["sensor_code"]),
            sensor_by_code(row["eqp_type"], row["sensor_code"]),
        )
        assert row["status"] == classify(sensor, row["value"])


def test_alarm_ratio_is_visible_but_rare(day_rows):
    """경보가 0이면 실습 소재가 없고, 너무 많으면 정상이 무엇인지 알 수 없다."""
    counts = Counter(r["status"] for r in day_rows)
    ratio = counts[ALARM] / len(day_rows)
    assert 0 < ratio < 0.05, counts


def test_warning_ratio_is_meaningful(day_rows):
    counts = Counter(r["status"] for r in day_rows)
    ratio = counts[WARNING] / len(day_rows)
    assert 0.005 < ratio < 0.25, counts


def test_majority_of_readings_are_normal(day_rows):
    counts = Counter(r["status"] for r in day_rows)
    assert counts[NORMAL] / len(day_rows) > 0.75, counts


def test_unhinted_sensors_never_alarm(day_rows, profiles):
    """지목받지 않은 센서는 잡음만으로 경보를 내면 안 된다.

    경보만 금지한다. 이전에는 `!= NORMAL` 로 검사해 주의까지 막았는데,
    그것은 지킬 수 없는 약속이었다. 경보 한계는 어느 센서든 10σ 밖이라
    잡음으로 닿지 않지만 정상범위는 4.17~10σ 라서 주의는 확률적으로 나온다.
    `RETICLE_TEMP` 는 10만 표본에 3건이 기대값이다.

    통과하고 있었던 것은 잡음 시드가 절대 시각이던 시절의 우연이다. 시드를
    앵커 상대로 바꾸자마자 `GAS_FLOW` 가 주의를 냈다. 이름은 never_alarm
    인데 내용이 주의까지 막고 있었던 것을 그때 알았다.
    """
    for row in day_rows:
        if row["status"] != ALARM:
            continue
        hinted = hinted_sensors(profiles[row["eqp_id"]])
        assert row["sensor_code"] in hinted, (row["eqp_id"], row["sensor_code"])


def test_unhinted_warnings_stay_at_the_noise_floor(day_rows, profiles):
    """무지목 주의는 나되 잡음이 낼 수 있는 만큼만 나야 한다.

    개수를 상수로 박으면 시드가 바뀔 때마다 갱신하게 된다. 정규분포에서
    직접 기대값을 구해 대조한다. σ 나 정상범위를 조정하면 기대값이 따라
    움직이므로 이 검사는 낡지 않는다.

    상한이 기대값의 8배인 것은 건수가 한 자리라 포아송 요동이 크기
    때문이다. 진짜로 막으려는 것은 자릿수가 다른 이탈 — 이탈 주입이 엉뚱한
    센서에 걸리면 수백 건이 된다.
    """
    hinted_by_eqp = {eqp: hinted_sensors(p) for eqp, p in profiles.items()}
    expected = 0.0
    seen = 0
    for row in day_rows:
        if row["sensor_code"] in hinted_by_eqp[row["eqp_id"]]:
            continue
        seen += 1
        sensor = sensor_by_code(row["eqp_type"], row["sensor_code"])
        half = (sensor.normal_max - sensor.normal_min) / 2
        expected += math.erfc(half / sensor.sigma / math.sqrt(2))

    actual = sum(
        1
        for row in day_rows
        if row["status"] == WARNING
        and row["sensor_code"] not in hinted_by_eqp[row["eqp_id"]]
    )
    assert seen, "무지목 판독이 없으면 비교가 성립하지 않는다"
    assert actual <= max(3.0, expected * 8), (
        f"무지목 주의 {actual}건은 잡음 기대 {expected:.2f}건(표본 {seen:,})으로"
        " 설명되지 않는다. 이탈이 지목 밖 센서로 새는지 보라"
    )


def test_worst_equipment_has_more_alarms_than_best(day_rows, profiles):
    """Eventhouse 에서 이상 설비를 찾으면 MES 불량률 상위 설비가 나와야 한다.

    `Alarm` 으로 센다. 이전에는 `!= NORMAL`(Warning+Alarm)로 세면서 이름만
    alarms 였는데, 그 혼동이 README 에 그대로 새어 나가 참가자 화면과 2.4배
    어긋나는 수치가 실렸다. README 의 대표 질의는 `status == "Alarm"` 이다.
    """
    ordered = sorted(profiles.values(), key=lambda p: p.defect_rate)
    best, worst = ordered[0].eqp_id, ordered[-1].eqp_id
    alarms = Counter(r["eqp_id"] for r in day_rows if r["status"] == ALARM)
    assert alarms[worst] > alarms[best], dict(alarms)


def test_cleanest_equipment_stays_quiet(day_rows, profiles):
    """가장 깨끗한 설비는 경보를 내지 않는다. 데모의 대조군이다.

    진폭 공식만으로는 확인할 수 없다. 값에 자연 잡음이 얹히므로 진폭이
    문턱 아래여도 경보가 날 수 있다. 그래서 생성된 데이터로 직접 센다.
    """
    cleanest = min(profiles.values(), key=lambda p: p.defect_rate)
    alarms = [r for r in day_rows if r["eqp_id"] == cleanest.eqp_id and r["status"] == ALARM]
    assert not alarms, (cleanest.eqp_id, cleanest.defect_rate, len(alarms))


def test_every_equipment_appears(day_rows, profiles):
    assert {r["eqp_id"] for r in day_rows} == set(profiles)


def test_values_are_rounded(day_rows):
    for row in day_rows[:200]:
        assert row["value"] == round(row["value"], 4)


from src.fdc_generator import IDLE, IDLE_INTERVAL_SEC, RUNNING
from src.fdc_runs import runs_by_equipment, span
from src.fdc_sensors import COMMON_SENSORS
from src.mes_probe import MesFacts


def _busiest_run_window(facts):
    """런이 가장 많은 설비의 첫 런 앞뒤로 10분씩."""
    runs = runs_by_equipment(facts)
    busiest = max(runs, key=lambda k: len(runs[k]))
    first = runs[busiest][0]
    return first.start - timedelta(minutes=10), first.end + timedelta(minutes=10)


def test_every_row_has_run_status(facts, busy_window):
    rows = build_readings(facts, *busy_window)
    assert rows
    assert {r["run_status"] for r in rows} <= {RUNNING, IDLE}


def test_both_states_appear(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    assert any(r["run_status"] == RUNNING for r in rows)
    assert any(r["run_status"] == IDLE for r in rows)


def test_idle_rows_use_only_common_sensors(facts):
    common = {s.sensor_code for s in COMMON_SENSORS}
    rows = build_readings(facts, *_busiest_run_window(facts))
    for row in rows:
        if row["run_status"] == IDLE:
            assert row["sensor_code"] in common


def test_idle_rows_sit_on_the_five_minute_grid(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    for row in rows:
        if row["run_status"] == IDLE:
            assert int(row["reading_ts"].timestamp()) % IDLE_INTERVAL_SEC == 0


def test_running_rows_use_the_full_sensor_set(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    seen = {}
    for row in rows:
        if row["run_status"] == RUNNING:
            seen.setdefault(row["eqp_id"], set()).add(row["sensor_code"])
    assert seen
    for eqp_id, codes in seen.items():
        eqp_type = next(
            e["eqp_type"] for e in facts.equipment if e["eqp_id"] == eqp_id
        )
        assert codes == {s.sensor_code for s in sensors_for(eqp_type)}


def test_idle_is_far_cheaper_than_running(facts, busy_window):
    """유휴는 5분에 2종, 가동은 30초에 6종이다."""
    rows = build_readings(facts, *busy_window)
    running = sum(1 for r in rows if r["run_status"] == RUNNING)
    assert 0 < len(rows) - running < running


def test_no_duplicate_readings(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    keys = [(r["eqp_id"], r["sensor_code"], r["reading_ts"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_backfill_and_live_agree_on_run_status(facts):
    """구간을 반으로 갈라 만들어도 통째로 만든 것과 같아야 한다."""
    lo, hi = _busiest_run_window(facts)
    mid = lo + (hi - lo) / 2
    key = lambda r: (r["eqp_id"], r["sensor_code"], r["reading_ts"])
    whole = {key(r): r["run_status"] for r in build_readings(facts, lo, hi)}
    halves = {key(r): r["run_status"]
              for r in build_readings(facts, lo, mid) + build_readings(facts, mid, hi)}
    assert whole == halves


def test_rows_after_the_mes_span_are_all_idle(facts):
    _lo, hi = span(facts)
    rows = build_readings(facts, hi, hi + timedelta(hours=1))
    assert rows
    assert all(r["run_status"] == IDLE for r in rows)


def test_lot_id_never_leaks_into_rows(facts, busy_window):
    """FDC 는 로트를 모른다. 계획서 '설계 결정' 참조."""
    rows = build_readings(facts, *busy_window)
    assert all("lot_id" not in r for r in rows)


def test_a_result_registered_behind_the_watermark_never_gets_run_rows(facts):
    """README '알려진 한계' 의 720행 표를 고정한다.

    실습 중 MES 쓰기 도구로 과거 시각에 실적을 등록하면 그 런의 Run 판독값은
    영영 생기지 않는다. watermark 다음부터만 만들기 때문이다. 에러는 안 난다 —
    MES 는 돌았다고 하는데 FDC 에는 Idle 만 있어서 교차 질의가 빈 결과를 낸다.

    한계를 못박아 두는 특성화 테스트다. 동작이 달라지면 README 를 같이 고쳐야
    한다는 뜻이지, 이대로가 옳다는 뜻이 아니다.
    """
    runs = sorted(runs_by_equipment(facts)["EQP-ETCH01"], key=lambda r: r.start)
    gap_lo, gap_hi = max(
        ((runs[i].end, runs[i + 1].start) for i in range(len(runs) - 1)),
        key=lambda g: g[1] - g[0],
    )
    lo = gap_lo + timedelta(minutes=10)
    hi = lo + timedelta(hours=1)
    assert hi < gap_hi, "고른 창이 유휴 틈 안에 들어와야 한다"

    mes_from, mes_to = span(facts)
    watermark = max(r["reading_ts"] for r in build_readings(facts, mes_from, mes_to))
    assert watermark > hi, "등록한 런이 watermark 뒤에 있어야 시나리오가 성립한다"

    payload = dict(facts.process_results[0])
    payload.update(
        id=9999, lot_id="LOT9999", step_code="ETCH", eqp_id="EQP-ETCH01",
        in_time=lo.isoformat(), out_time=hi.isoformat(),
        result="Fail", defect_code="Particle",
    )
    after = MesFacts(
        equipment=facts.equipment,
        process_results=[*facts.process_results, payload],
        route=facts.route,
    )

    def in_window(rows, status):
        return [r for r in rows
                if lo <= r["reading_ts"] <= hi
                and r["eqp_id"] == "EQP-ETCH01"
                and r["run_status"] == status]

    incremental = build_readings(after, watermark, mes_to + timedelta(minutes=30))
    backfilled = build_readings(after, mes_from, mes_to)

    assert in_window(incremental, RUNNING) == [], "증분 실행은 과거를 다시 만들지 않는다"
    assert len(in_window(backfilled, RUNNING)) == 720, "README 표의 720행"
    assert len(in_window(build_readings(facts, mes_from, mes_to), IDLE)) == 24

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "| Eventhouse 최종 | **0** | 24 |" in readme
    assert "| 등록 후 처음부터 백필했다면 | **720** | 0 |" in readme


def _shifted_facts(delta):
    """공정이력 시각을 통째로 옮긴 사본. MES 콜드스타트와 같은 효과다.

    MES 는 앵커를 마지막에 평행이동에만 쓴다(`Random(42)` 고정). 어느 로트가
    어느 설비에서 몇 분 걸렸는지는 앵커가 어디든 불변이다. 실 API 와 픽스처의
    상대구조 해시가 실제로 같다.
    """
    import copy
    import json
    from datetime import datetime

    from src.mes_probe import MesFacts

    root = Path(__file__).resolve().parent.parent
    raw = json.loads(
        (root / "tests" / "fixtures" / "mes_facts.json").read_text(encoding="utf-8")
    )
    moved = copy.deepcopy(raw)
    for result in moved["process_results"]:
        for field in ("in_time", "out_time"):
            if result.get(field):
                result[field] = (
                    datetime.fromisoformat(result[field]) + delta
                ).isoformat()
    return MesFacts.from_dict(moved)


def _generate(facts):
    from src.fdc_runs import span

    start, end = span(facts)
    return build_readings(facts, start, end)


def test_moving_the_anchor_keeps_the_shape_but_moves_the_alarms():
    """앵커가 옮겨져도 골격은 같지만 하루 주기가 어긋나면 경보가 달라진다.

    참가자 20명이 각자 노트북을 돌린다. MES 앵커가 고정돼 있지 않으면 각자
    다른 시각에 다른 앵커를 받는다. 그때 무엇이 같고 무엇이 다른지가
    README 의 "앵커 고정은 선택이 아니다" 주장을 떠받친다.

    남은 앵커 의존은 `diurnal` 하나뿐이라 **하루의 정수배 이동에는 값이 한
    행도 안 변한다.** 그것이 이 의존이 물리라는 증거다 — 같은 시각에 도는
    공정은 같은 환경을 본다. 잡음까지 앵커에 묶여 있던 시절에는 정수 일수를
    옮겨도 10만 행이 전부 달라졌고, 그때는 이 구분이 불가능했다.

    이 테스트가 실패하면 README 의 경고를 지워야 한다는 신호다.
    """
    from collections import Counter
    from datetime import timedelta

    base = _generate(_shifted_facts(timedelta(0)))
    base_runs = Counter(row["run_status"] for row in base)
    base_alarm_pairs = {
        (row["eqp_id"], row.get("lot_id")) for row in base if row["status"] == "Alarm"
    }
    assert base_alarm_pairs, "원본에 경보가 없으면 비교가 성립하지 않는다"

    def moved_rows(hours):
        moved = _generate(_shifted_facts(timedelta(hours=hours)))
        assert len(moved) == len(base), (
            f"{hours}시간 옮겼더니 행 수가 {len(base):,} → {len(moved):,} 로 변했다."
            " 런 구조는 MES 에서 오므로 평행이동에 영향받지 않아야 한다"
        )
        assert Counter(row["run_status"] for row in moved) == base_runs, (
            "가동/유휴 배치가 변했다. MES 런 구간이 그대로인데 달라질 수 없다"
        )
        return moved

    for whole_days in (11, 30):
        moved = moved_rows(24 * whole_days)
        differing = sum(1 for a, b in zip(base, moved) if a["value"] != b["value"])
        assert differing == 0, (
            f"{whole_days}일은 하루의 정수배라 판독값이 한 행도 달라지면 안 되는데"
            f" {differing:,}행이 달라졌다. 잡음이나 이탈이 절대 시각에 묶여 있는지 보라"
        )

    off_grid = [moved_rows(hours) for hours in (1, 37)]
    assert all(
        sum(1 for a, b in zip(base, moved) if a["value"] != b["value"]) > len(base) // 2
        for moved in off_grid
    ), "하루 주기가 어긋났는데 값이 그대로면 환경 성분이 죽은 것이다"

    assert any(
        {(row["eqp_id"], row.get("lot_id")) for row in moved if row["status"] == "Alarm"}
        != base_alarm_pairs
        for moved in off_grid
    ), (
        "앵커를 어긋나게 옮겨도 경보 설비가 그대로라면 README 의 '참가자마다 경보"
        " 설비가 달라진다' 경고가 근거를 잃는다. 데이터가 아니라 경고를 지워야 한다"
    )


def test_readme_alarm_drift_numbers_are_measured():
    """README 표의 수치가 실측과 맞아야 한다.

    손으로 적어 두면 낡는다. 재배포로 구성이 달라지면 숫자만 맞춰 넣게 되므로
    픽스처에서 직접 세어 대조한다.
    """
    import re
    from collections import Counter
    from datetime import timedelta

    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    marker = "### 그리고 참가자마다 경보 설비가 달라집니다"
    assert marker in readme, "앵커가 경보를 옮긴다는 절이 사라졌다"
    section = readme[readme.index(marker) :]
    section = section[: section.index("\n### ", 1)]

    base = _generate(_shifted_facts(timedelta(0)))
    runs = Counter(row["run_status"] for row in base)

    def cells(label):
        line = next(ln for ln in section.splitlines() if ln.strip().startswith(f"| {label}"))
        return line, [c.strip() for c in line.strip().strip("|").split("|")][1:]

    # 골격 두 줄은 평행이동해도 안 변하므로 네 칸이 전부 같은 값이어야 한다.
    # 집합 포함으로 검사하면 한 칸만 틀려도 나머지가 통과시킨다.
    for label, value in (("판독 행", len(base)), ("Run / Idle", runs["Run"])):
        line, got = cells(label)
        assert len(got) == 4, f"'{label}' 칸 수가 달라졌다: {line.strip()}"
        for index, cell in enumerate(got):
            numbers = {int(n.replace(",", "")) for n in re.findall(r"\b\d[\d,]*\b", cell)}
            assert value in numbers, (
                f"'{label}' {index + 1}번째 칸이 실측 {value:,} 과 다르다: {cell}"
            )

    def alarms_of(rows):
        return sum(1 for row in rows if row["status"] == "Alarm")

    def only_number(cell):
        found = {int(n.replace(",", "")) for n in re.findall(r"\b\d[\d,]*\b", cell)}
        assert len(found) == 1, f"칸에 숫자가 하나여야 대조가 성립한다: {cell}"
        return found.pop()

    # 네 칸을 전부 실측한다. 첫 칸만 보면 나머지 셋은 손으로 적은 채 낡는다.
    shifts = (0, 37, 24 * 11, 24 * 30)
    moved = [base] + [_generate(_shifted_facts(timedelta(hours=h))) for h in shifts[1:]]

    _, alarm_cells = cells("**Alarm 행**")
    assert len(alarm_cells) == 4, "Alarm 행 칸 수가 표 머리와 다르다"
    for index, (cell, rows) in enumerate(zip(alarm_cells, moved)):
        assert only_number(cell) == alarms_of(rows), (
            f"Alarm {index + 1}번째 칸이 +{shifts[index]}시간 실측"
            f" {alarms_of(rows):,} 과 다르다: {cell}"
        )

    _, diff_cells = cells("원본과 값이 다른 행")
    assert len(diff_cells) == 4, "값 차이 행 칸 수가 표 머리와 다르다"
    assert not re.search(r"\d", diff_cells[0]), (
        f"원본 칸은 자기 자신과 비교할 수 없으므로 숫자가 없어야 한다: {diff_cells[0]}"
    )
    for index, (cell, rows) in enumerate(zip(diff_cells[1:], moved[1:]), start=1):
        actual = sum(1 for a, b in zip(base, rows) if a["value"] != b["value"])
        assert only_number(cell) == actual, (
            f"값 차이 {index + 1}번째 칸이 +{shifts[index]}시간 실측"
            f" {actual:,} 과 다르다: {cell}"
        )
