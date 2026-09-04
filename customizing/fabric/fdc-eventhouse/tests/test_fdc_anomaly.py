import subprocess
import sys

import pytest

from src.fdc_anomaly import (
    DEFECT_SENSOR_HINT,
    EXCURSION_MAX,
    EXCURSION_MIN,
    FALLBACK_SENSOR,
    build_profiles,
    excursion_amplitude,
    excursion_for,
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


def test_severity_spans_zero_to_one(profiles):
    sev = {p.eqp_id: p.severity for p in profiles.values()}
    assert sev["EQP-CMP01"] == pytest.approx(1.0)
    assert sev["EQP-IMPL01"] == pytest.approx(0.0)
    assert all(0.0 <= v <= 1.0 for v in sev.values())


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


def test_unhinted_sensor_has_zero_excursion(profiles):
    profile = profiles["EQP-CMP01"]
    hinted = hinted_sensors(profile)
    for sensor in sensors_for(profile.eqp_type):
        if sensor.sensor_code not in hinted:
            assert excursion_for(profile, sensor.sensor_code) == 0.0


def test_excursion_within_declared_bounds(profiles):
    for profile in profiles.values():
        for sensor_code in hinted_sensors(profile):
            value = excursion_for(profile, sensor_code)
            assert 0.0 < value <= EXCURSION_MAX


def test_worse_equipment_excurses_harder(profiles):
    """불량률이 높은 설비가 더 크게 이탈해야 한다.

    설비 유형이 다르면 지목 센서가 겹치지 않으므로 센서별 값이 아니라
    설비 단위 진폭으로 비교한다.
    """
    ordered = sorted(profiles.values(), key=lambda p: p.defect_rate)
    for a, b in zip(ordered, ordered[1:]):
        assert excursion_amplitude(a) <= excursion_amplitude(b)
    assert excursion_amplitude(profiles["EQP-CMP01"]) == pytest.approx(EXCURSION_MAX)
    assert excursion_amplitude(profiles["EQP-IMPL01"]) == pytest.approx(EXCURSION_MIN)


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
    """가장 깨끗한 설비는 경보(정상 반폭의 1.5배)에 닿지 말아야 한다."""
    profile = profiles["EQP-IMPL01"]
    for sensor_code in hinted_sensors(profile):
        assert excursion_for(profile, sensor_code) < 1.5


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
