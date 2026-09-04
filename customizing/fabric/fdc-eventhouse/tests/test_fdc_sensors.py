import pytest

from src.fdc_sensors import (
    COMMON_SENSORS,
    EQP_TYPES,
    SAMPLE_INTERVAL_SEC,
    TYPE_SENSORS,
    build_sensor_spec_rows,
    sensor_by_code,
    sensors_for,
)

ALL = [(t, s) for t in EQP_TYPES for s in sensors_for(t)]


def test_seven_types_six_sensors_each():
    assert len(EQP_TYPES) == 7
    for eqp_type in EQP_TYPES:
        assert len(sensors_for(eqp_type)) == 6


def test_spec_rows_total_42():
    rows = build_sensor_spec_rows()
    assert len(rows) == 42
    keys = {(r["eqp_type"], r["sensor_code"]) for r in rows}
    assert len(keys) == 42


@pytest.mark.parametrize("eqp_type,sensor", ALL, ids=[f"{t}.{s.sensor_code}" for t, s in ALL])
def test_base_is_center_of_normal(eqp_type, sensor):
    """이상 주입이 중심 대칭을 전제한다. 어긋나면 정상 신호가 한쪽으로 쏠린다."""
    center = (sensor.normal_min + sensor.normal_max) / 2
    assert sensor.base == pytest.approx(center), f"{eqp_type}.{sensor.sensor_code}"


@pytest.mark.parametrize("eqp_type,sensor", ALL, ids=[f"{t}.{s.sensor_code}" for t, s in ALL])
def test_alarm_strictly_contains_normal(eqp_type, sensor):
    assert sensor.alarm_min < sensor.normal_min
    assert sensor.normal_max < sensor.alarm_max


@pytest.mark.parametrize("eqp_type,sensor", ALL, ids=[f"{t}.{s.sensor_code}" for t, s in ALL])
def test_noise_cannot_alone_breach_normal(eqp_type, sensor):
    """일주기 진폭과 잡음의 합이 정상범위 반폭의 90%를 넘으면 안 된다.

    넘으면 이상 주입이 없는 설비에서도 Warning 이 뜬다. 실습자가 이상 설비를
    골라내는 질의를 짤 수 없게 된다.
    """
    half = (sensor.normal_max - sensor.normal_min) / 2
    assert sensor.diurnal_amp + sensor.sigma < half * 0.9, f"{eqp_type}.{sensor.sensor_code}"


def test_common_sensors_present_in_every_type():
    for eqp_type in EQP_TYPES:
        codes = {s.sensor_code for s in sensors_for(eqp_type)}
        for common in COMMON_SENSORS:
            assert common.sensor_code in codes


def test_unknown_type_yields_common_only():
    assert sensors_for("NoSuchType") == COMMON_SENSORS


def test_sensor_by_code_lookup():
    assert sensor_by_code("Furnace", "CHAMBER_TEMP").base == 1050.0
    assert sensor_by_code("CVD", "CHAMBER_TEMP").base == 420.0
    with pytest.raises(KeyError):
        sensor_by_code("Furnace", "RF_POWER")


def test_shared_code_differs_across_types():
    """CHAMBER_TEMP 는 Furnace 1050degC, CVD 420degC. 스펙 키가 (유형, 코드)여야 하는 이유."""
    assert sensor_by_code("Furnace", "CHAMBER_TEMP").base != sensor_by_code("CVD", "CHAMBER_TEMP").base


def test_spec_rows_carry_interval_and_active_flag():
    for row in build_sensor_spec_rows():
        assert row["sample_interval_sec"] == SAMPLE_INTERVAL_SEC
        assert row["is_active"] is True
        assert row["unit"]
        assert row["sensor_name_ko"]


def test_no_forbidden_lot_columns():
    """센서 스펙은 로트를 모른다. 교차 질의 학습 목표가 여기에 걸려 있다."""
    forbidden = {"lot_id", "product_code", "wafer_qty", "defect_code", "judgment", "result", "operator"}
    for row in build_sensor_spec_rows():
        assert not (forbidden & set(row))


def test_type_sensors_have_no_common_duplicates():
    common_codes = {s.sensor_code for s in COMMON_SENSORS}
    for eqp_type, sensors in TYPE_SENSORS.items():
        assert not (common_codes & {s.sensor_code for s in sensors}), eqp_type
