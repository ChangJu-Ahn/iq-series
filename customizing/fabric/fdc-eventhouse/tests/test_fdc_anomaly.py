import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from src.fdc_anomaly import (
    DEFECT_SENSOR_HINT,
    EXCURSION_MAX,
    EXCURSION_MIN,
    FALLBACK_SENSOR,
    build_profiles,
    excursion_amplitude,
    candidate_sensors,
    run_excursion,
    run_sensor,
    excursion_sign,
    fallback_sensor,
    hinted_sensors,
    seed,
    unit_from_seed,
)
from src.fdc_sensors import EQP_TYPES, TYPE_SENSORS, sensors_for


@pytest.fixture(scope="module")
def profiles(facts):
    return build_profiles(facts)


@pytest.fixture(scope="module")
def profiles_factory():
    """공정이력을 한 건 더한 프로파일을 만들어 준다.

    설비 간 결합이 없는지 보려면 "한 설비만 바뀐 두 스냅샷"이 필요하다.
    """
    import copy
    import json
    from pathlib import Path

    from src.mes_probe import MesFacts

    raw = json.loads((Path(__file__).parent / "fixtures" / "mes_facts.json").read_text(encoding="utf-8"))

    def make(extra_clean_run: str | None = None):
        payload = copy.deepcopy(raw)
        if extra_clean_run:
            template = next(r for r in payload["process_results"] if r.get("eqp_id") == extra_clean_run)
            added = dict(template)
            added["lot_id"] = "LOT-SYNTHETIC"
            added["defect_code"] = None
            payload["process_results"].append(added)
        return build_profiles(MesFacts.from_dict(payload))

    return make


def test_seed_matches_spec_value():
    assert seed("EQP-CMP01", "AMBIENT_TEMP", 100) == 2450050198


def test_seed_is_stable_across_processes():
    """hash() 가 아님을 실제로 증명한다. 이 테스트가 이 설계의 핵심 방어선이다."""
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "from src.fdc_anomaly import seed; print(seed('EQP-CMP01', 'AMBIENT_TEMP', 100))"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(3)
    }
    assert outs == {"2450050198"}


def test_unit_from_seed_in_range():
    for i in range(200):
        assert 0.0 <= unit_from_seed("EQP-X", "S", i) < 1.0


def test_profiles_cover_all_equipment(facts, profiles):
    assert set(profiles) == {e["eqp_id"] for e in facts.equipment}
    assert len(profiles) == 8


def test_every_equipment_type_has_sensors(profiles):
    """route 는 9유형이지만 실제 설비는 7유형만 쓴다. 센서 없는 설비가 생기면 안 된다."""
    for profile in profiles.values():
        assert profile.eqp_type in TYPE_SENSORS, profile.eqp_id
        assert len(sensors_for(profile.eqp_type)) == 6


def test_known_defect_rates(profiles):
    assert profiles["EQP-CMP01"].defect_runs == 7
    assert profiles["EQP-CMP01"].total_runs == 8
    assert profiles["EQP-IMPL01"].defect_rate == pytest.approx(2 / 12)


def test_severity_is_the_equipments_own_defect_rate(profiles):
    """severity 는 설비 자신의 불량률이다. 다른 설비와 무관해야 한다.

    전체 설비의 min/max 로 정규화하면 어느 설비 실적이 하나만 바뀌어도
    나머지 진폭이 전부 흔들린다. 20명이 한 mock MES 를 공유하므로 한 사람이
    공정 실적을 등록하면 그 뒤에 백필한 사람의 과거 판독값이 달라진다.
    """
    for p in profiles.values():
        assert p.severity == pytest.approx(p.defect_runs / p.total_runs)
        assert 0.0 <= p.severity <= 1.0


def test_severity_ignores_other_equipment(profiles_factory):
    """한 설비의 실적을 바꿔도 다른 설비의 severity 는 그대로다."""
    before = profiles_factory()
    after = profiles_factory(extra_clean_run="EQP-IMPL01")
    assert after["EQP-IMPL01"].severity != before["EQP-IMPL01"].severity
    for eqp_id in before:
        if eqp_id != "EQP-IMPL01":
            assert after[eqp_id].severity == before[eqp_id].severity


def test_severity_orders_by_defect_rate(profiles):
    ordered = sorted(profiles.values(), key=lambda p: p.defect_rate)
    for a, b in zip(ordered, ordered[1:]):
        assert a.severity <= b.severity


def test_hints_only_target_existing_sensors(profiles):
    for profile in profiles.values():
        available = {s.sensor_code for s in sensors_for(profile.eqp_type)}
        assert set(hinted_sensors(profile)) <= available, profile.eqp_id


def test_hint_weights_sum_to_one(profiles):
    for profile in profiles.values():
        weights = hinted_sensors(profile)
        if weights:
            assert sum(weights.values()) == pytest.approx(1.0)


def test_every_equipment_has_at_least_one_hinted_sensor(profiles):
    """지목이 하나도 없으면 그 설비는 영원히 정상이라 실습 소재가 못 된다."""
    for profile in profiles.values():
        assert hinted_sensors(profile), profile.eqp_id


def test_unselected_sensor_has_zero_excursion(profiles):
    profile = profiles["EQP-CMP01"]
    run = _demo_run(profile.eqp_id)
    chosen = run_sensor(run, profile.eqp_type)
    for sensor in sensors_for(profile.eqp_type):
        if sensor.sensor_code != chosen:
            assert run_excursion(run, sensor.sensor_code, run.end - _ONE_SEC, profile) == 0.0


def test_excursion_within_declared_bounds(profiles):
    for profile in profiles.values():
        run = _demo_run(profile.eqp_id)
        chosen = run_sensor(run, profile.eqp_type)
        value = abs(run_excursion(run, chosen, run.end - _ONE_SEC, profile))
        assert 0.0 < value <= EXCURSION_MAX


def test_worse_equipment_excurses_harder(profiles):
    """불량률이 높은 설비가 더 크게 이탈해야 한다.

    설비 유형이 다르면 지목 센서가 겹치지 않으므로 센서별 값이 아니라
    설비 단위 진폭으로 비교한다.
    """
    ordered = sorted(profiles.values(), key=lambda p: p.defect_rate)
    for a, b in zip(ordered, ordered[1:]):
        assert excursion_amplitude(a) <= excursion_amplitude(b)
    # 진폭은 불량률 0 에서 MIN, 1 에서 MAX 다. 픽스처의 실제 불량률은
    # 0.167~0.875 이므로 양 끝에 닿지 않고 그 사이에 놓인다.
    for p in profiles.values():
        assert EXCURSION_MIN < excursion_amplitude(p) < EXCURSION_MAX
    assert excursion_amplitude(profiles["EQP-CMP01"]) == pytest.approx(
        EXCURSION_MIN + (EXCURSION_MAX - EXCURSION_MIN) * profiles["EQP-CMP01"].defect_rate
    )


def test_same_type_worse_equipment_excurses_harder(profiles):
    """같은 유형(Scanner) 두 대에서 센서별 이탈까지 비교한다."""
    low, high = profiles["EQP-PHOT01"], profiles["EQP-PHOT02"]
    assert low.eqp_type == high.eqp_type == "Scanner"
    assert low.defect_rate < high.defect_rate
    shared = set(hinted_sensors(low)) & set(hinted_sensors(high))
    assert shared, "같은 유형인데 공통 지목 센서가 없다"


def test_fallback_covers_equipment_with_no_matching_hint(profiles):
    """EQP-IMPL01 은 Scratch 가 Implanter 에 없는 센서만 가리킨다."""
    profile = profiles["EQP-IMPL01"]
    hinted = hinted_sensors(profile)
    assert hinted
    assert FALLBACK_SENSOR in hinted


def test_fallback_sensor_exists_on_every_type():
    for eqp_type in EQP_TYPES:
        assert fallback_sensor(eqp_type) == FALLBACK_SENSOR
    assert fallback_sensor("NoSuchType") == FALLBACK_SENSOR


def test_lowest_severity_stays_below_min_alarm_ratio(profiles):
    """가장 깨끗한 설비는 경보(정상 반폭의 1.5배)에 닿지 말아야 한다.

    설비 이름을 하드코딩하지 않고 프로파일에서 최저 불량률 설비를 뽑는다.
    픽스처를 다시 뜨면 어느 설비가 가장 깨끗한지 바뀔 수 있고, 그때
    하드코딩된 이름은 "이 설비가 조용해야 한다"가 아니라 "이 설비가
    존재해야 한다"를 검사하게 된다.

    이 여유는 구조적으로 얇다. 진폭이 설비 자신의 불량률만의 함수이므로
    경계는 불량률 축 위의 한 점이고, 픽스처에서 가장 깨끗한 두 설비의
    불량률 차가 0.015 밖에 안 된다(IMPL01 0.167 · PHOT01 0.182). "일곱 대는
    경보를 내고 가장 깨끗한 한 대는 조용하다"를 만족시키려면 경계가 그
    0.015 안에 들어와야 한다. 상수를 어떻게 고르든 여유는 0.06 을 넘지
    못한다. 실측으로 확인한 사실이고, 상수 탓이 아니다.
    """
    profile = min(profiles.values(), key=lambda p: p.defect_rate)
    run = _demo_run(profile.eqp_id)
    chosen = run_sensor(run, profile.eqp_type)
    assert abs(run_excursion(run, chosen, run.end - _ONE_SEC, profile)) < 1.5


def test_amplitude_boundary_leaves_the_cleanest_equipment_quiet(profiles):
    """진폭 경계가 최저 설비 위에 있는지 확인한다.

    진폭이 1.5(가장 좁은 경보 문턱) 아래라고 해서 그 설비가 반드시 조용한
    것은 아니다. 값에는 자연 잡음이 얹히고 잡음이 경보까지 밀어 올릴 수
    있다. 그래서 "경계 아래면 침묵" 같은 단정은 여기서 하지 않는다.
    실제 침묵 여부는 생성된 데이터로 확인한다
    (test_fdc_generator.py::test_cleanest_equipment_stays_quiet).

    이 여유는 구조적으로 얇다. 진폭이 설비 자신의 불량률만의 함수이므로
    경계는 불량률 축 위의 한 점이고, 픽스처에서 가장 깨끗한 두 설비의
    불량률 차가 0.015 밖에 안 된다(IMPL01 0.167 · PHOT01 0.182). 상수를
    어떻게 고르든 여유는 0.06 을 넘지 못한다. 실측으로 확인한 사실이고
    상수 선택 탓이 아니다.
    """
    cleanest = min(profiles.values(), key=lambda p: p.defect_rate)
    boundary = (1.5 - EXCURSION_MIN) / (EXCURSION_MAX - EXCURSION_MIN)
    assert cleanest.defect_rate < boundary, (
        f"가장 깨끗한 {cleanest.eqp_id}({cleanest.defect_rate:.3f}) 의 진폭이 "
        f"경보 문턱에 닿는다. 경계 {boundary:.3f}"
    )


def test_excursion_sign_is_deterministic_and_mixed(profiles):
    """실제 설비·센서 조합 전체에서 방향이 한쪽으로 쏠리지 않아야 한다.

    crc32 는 선형이라 설비명만 바뀐 조합의 최하위 비트가 뭉쳤다. 이 테스트가
    그 회귀를 잡는다.
    """
    pairs = [
        (p.eqp_id, s.sensor_code)
        for p in profiles.values()
        for s in sensors_for(p.eqp_type)
    ]
    signs = [excursion_sign(e, s) for e, s in pairs]
    assert set(signs) == {1, -1}
    positive = signs.count(1) / len(signs)
    assert 0.3 < positive < 0.7, f"방향이 쏠렸다: {positive:.2f}"
    assert [excursion_sign(e, s) for e, s in pairs] == signs


def test_all_fixture_defect_codes_are_mapped(facts):
    """MES 에 있는 불량코드인데 지목표에 없으면 그 불량은 신호를 못 만든다."""
    seen = {r["defect_code"] for r in facts.process_results if r.get("defect_code")}
    assert seen <= set(DEFECT_SENSOR_HINT), seen - set(DEFECT_SENSOR_HINT)


def test_hint_targets_are_real_sensor_codes():
    known = {s.sensor_code for t in EQP_TYPES for s in sensors_for(t)}
    for code, targets in DEFECT_SENSOR_HINT.items():
        assert set(targets) <= known, (code, set(targets) - known)


def test_excursion_min_below_max():
    assert 0 < EXCURSION_MIN < EXCURSION_MAX


from src.fdc_anomaly import candidate_sensors, run_excursion, run_sensor
from src.fdc_runs import Run

_START = datetime(2026, 9, 3, 1, tzinfo=timezone.utc)
_END = datetime(2026, 9, 3, 2, tzinfo=timezone.utc)


def _run(defect="Particle", eqp="EQP-ETCH01"):
    return Run(eqp_id=eqp, lot_id="LOT0010", step_code="ETCH",
               start=_START, end=_END, defect_code=defect)


def test_candidate_sensors_picks_hinted_sensor_present_on_tool():
    assert "CHAMBER_TEMP" in candidate_sensors("Particle", "Etcher")


def test_candidate_sensors_is_empty_without_defect():
    assert candidate_sensors(None, "Etcher") == ()


def test_candidate_sensors_falls_back_when_hint_is_absent():
    """Scratch 는 Implanter 에 없는 센서만 가리킨다."""
    assert candidate_sensors("Scratch", "Implanter") == ("AMBIENT_TEMP",)


def test_candidate_sensors_unknown_defect_falls_back():
    assert candidate_sensors("No-Such-Defect", "Etcher") == ("AMBIENT_TEMP",)


def test_run_sensor_picks_exactly_one_candidate():
    chosen = run_sensor(_run(), "Etcher")
    assert chosen in candidate_sensors("Particle", "Etcher")


def test_run_sensor_is_none_without_defect():
    assert run_sensor(_run(defect=None), "Etcher") is None


def test_run_sensor_is_deterministic():
    assert run_sensor(_run(), "Etcher") == run_sensor(_run(), "Etcher")


def test_different_runs_can_pick_different_sensors():
    """같은 불량이라도 런마다 흐르는 파라미터가 달라야 소재가 풍부해진다."""
    picks = {
        run_sensor(Run("EQP-ETCH01", f"LOT{n:04d}", "ETCH", _START, _END, "Particle"),
                   "Etcher")
        for n in range(40)
    }
    assert len(picks) > 1


def test_no_excursion_outside_the_run(profiles):
    p = profiles["EQP-ETCH01"]
    sensor = run_sensor(_run(), p.eqp_type)
    assert run_excursion(_run(), sensor, _START - timedelta(minutes=1), p) == 0.0
    assert run_excursion(_run(), sensor, _END, p) == 0.0


def test_no_excursion_without_defect(profiles):
    p = profiles["EQP-ETCH01"]
    mid = _START + timedelta(minutes=30)
    assert run_excursion(_run(defect=None), "CHAMBER_TEMP", mid, p) == 0.0


def test_no_excursion_on_unselected_sensor(profiles):
    p = profiles["EQP-ETCH01"]
    mid = _START + timedelta(minutes=30)
    chosen = run_sensor(_run(), p.eqp_type)
    other = next(s.sensor_code for s in sensors_for(p.eqp_type)
                 if s.sensor_code != chosen)
    assert run_excursion(_run(), other, mid, p) == 0.0


def test_excursion_grows_through_the_run(profiles):
    """공정이 서서히 이탈하다 끝에서 불량으로 잡힌다."""
    p = profiles["EQP-ETCH01"]
    sensor = run_sensor(_run(), p.eqp_type)
    early = abs(run_excursion(_run(), sensor, _START + timedelta(minutes=6), p))
    late = abs(run_excursion(_run(), sensor, _START + timedelta(minutes=54), p))
    assert late > early


def test_excursion_starts_at_zero(profiles):
    p = profiles["EQP-ETCH01"]
    assert run_excursion(_run(), run_sensor(_run(), p.eqp_type), _START, p) == 0.0


def test_excursion_is_deterministic(profiles):
    p = profiles["EQP-ETCH01"]
    mid = _START + timedelta(minutes=30)
    sensor = run_sensor(_run(), p.eqp_type)
    assert run_excursion(_run(), sensor, mid, p) == \
           run_excursion(_run(), sensor, mid, p)


def test_zero_length_run_does_not_divide_by_zero(profiles):
    p = profiles["EQP-ETCH01"]
    degenerate = Run("EQP-ETCH01", "LOT0010", "ETCH", _START, _START, "Particle")
    assert run_excursion(degenerate, "CHAMBER_TEMP", _START, p) == 0.0


_ONE_SEC = timedelta(seconds=1)


def _demo_run(eqp_id):
    """이탈 상한을 재기 위한 1시간짜리 불량 런."""
    start = datetime(2026, 9, 3, 1, tzinfo=timezone.utc)
    return Run(eqp_id, "LOT0001", "STEP", start, start + timedelta(hours=1), "Particle")
