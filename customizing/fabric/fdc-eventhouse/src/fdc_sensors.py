"""설비 유형별 센서 정의.

설비 유형 7종이 각각 공통 센서 2종과 고유 센서 4종을 갖는다. 합계 42종이며
이것이 `fdc_sensor_spec` 테이블이 된다.

공통 센서(주변 온도·습도)를 모든 유형에 두는 이유는 두 가지다. 클린룸 환경은
설비 종류와 무관하게 측정되고, 전 설비에 걸친 동일 계열이 있어야 KQL
`make-series` 로 설비 간 비교 실습이 가능하다.

`base` 는 반드시 정상범위의 중심이어야 한다. 이상 주입(fdc_anomaly)이 중심
대칭을 전제로 이탈 폭을 계산하기 때문이다. 테스트가 이 불변식을 강제한다.
"""

from __future__ import annotations

from dataclasses import dataclass

SAMPLE_INTERVAL_SEC = 30


@dataclass(frozen=True)
class SensorDef:
    sensor_code: str
    sensor_name_ko: str
    unit: str
    base: float
    diurnal_amp: float
    sigma: float
    normal_min: float
    normal_max: float
    alarm_min: float
    alarm_max: float


# 모든 설비 유형이 갖는다. 클린룸 환경 계측.
COMMON_SENSORS: tuple[SensorDef, ...] = (
    SensorDef("AMBIENT_TEMP", "주변 온도", "degC", 22.0, 0.6, 0.12, 21.0, 23.0, 20.0, 24.0),
    SensorDef("AMBIENT_HUMIDITY", "주변 습도", "%", 45.0, 2.5, 0.6, 40.0, 50.0, 35.0, 55.0),
)

TYPE_SENSORS: dict[str, tuple[SensorDef, ...]] = {
    "Furnace": (
        SensorDef("CHAMBER_TEMP", "챔버 온도", "degC", 1050.0, 1.5, 1.2, 1040.0, 1060.0, 1030.0, 1070.0),
        SensorDef("RAMP_RATE", "승온 속도", "degC/min", 8.0, 0.1, 0.08, 7.5, 8.5, 7.0, 9.0),
        SensorDef("O2_CONC", "산소 농도", "ppm", 120.0, 3.0, 2.0, 100.0, 140.0, 80.0, 160.0),
        SensorDef("N2_FLOW", "질소 유량", "sccm", 2000.0, 15.0, 8.0, 1950.0, 2050.0, 1900.0, 2100.0),
    ),
    "Scanner": (
        SensorDef("STAGE_TEMP", "스테이지 온도", "degC", 23.0, 0.03, 0.02, 22.9, 23.1, 22.8, 23.2),
        SensorDef("FOCUS_OFFSET", "포커스 오프셋", "nm", 0.0, 2.0, 1.5, -15.0, 15.0, -25.0, 25.0),
        SensorDef("ILLUM_DOSE", "노광 도즈", "mJ/cm2", 30.0, 0.15, 0.1, 29.4, 30.6, 29.0, 31.0),
        SensorDef("RETICLE_TEMP", "레티클 온도", "degC", 23.0, 0.02, 0.012, 22.95, 23.05, 22.9, 23.1),
    ),
    "Etcher": (
        SensorDef("RF_POWER", "RF 파워", "W", 1500.0, 8.0, 6.0, 1450.0, 1550.0, 1400.0, 1600.0),
        SensorDef("CHAMBER_PRESSURE", "챔버 압력", "mTorr", 45.0, 0.6, 0.5, 42.0, 48.0, 40.0, 50.0),
        SensorDef("GAS_FLOW", "공정가스 유량", "sccm", 180.0, 2.0, 1.5, 172.0, 188.0, 165.0, 195.0),
        SensorDef("CHAMBER_TEMP", "챔버 온도", "degC", 65.0, 0.8, 0.5, 62.0, 68.0, 60.0, 70.0),
    ),
    "Implanter": (
        SensorDef("BEAM_CURRENT", "빔 전류", "uA", 500.0, 5.0, 4.0, 480.0, 520.0, 460.0, 540.0),
        SensorDef("BEAM_ENERGY", "빔 에너지", "keV", 80.0, 0.4, 0.3, 78.0, 82.0, 76.0, 84.0),
        SensorDef("VACUUM", "진공도", "uTorr", 2.0, 0.08, 0.06, 1.5, 2.5, 1.0, 3.5),
        SensorDef("SRC_TEMP", "이온소스 온도", "degC", 320.0, 3.0, 2.0, 305.0, 335.0, 290.0, 350.0),
    ),
    "CVD": (
        SensorDef("CHAMBER_TEMP", "챔버 온도", "degC", 420.0, 2.0, 1.5, 410.0, 430.0, 400.0, 440.0),
        SensorDef("CHAMBER_PRESSURE", "챔버 압력", "Torr", 5.0, 0.08, 0.06, 4.6, 5.4, 4.2, 5.8),
        SensorDef("PRECURSOR_FLOW", "전구체 유량", "sccm", 250.0, 2.5, 2.0, 240.0, 260.0, 230.0, 270.0),
        SensorDef("DEP_RATE", "증착 속도", "nm/min", 12.0, 0.15, 0.1, 11.4, 12.6, 11.0, 13.0),
    ),
    "Polisher": (
        SensorDef("PAD_PRESSURE", "패드 압력", "kPa", 35.0, 0.3, 0.25, 33.0, 37.0, 31.0, 39.0),
        SensorDef("SLURRY_FLOW", "슬러리 유량", "ml/min", 200.0, 2.0, 1.6, 190.0, 210.0, 180.0, 220.0),
        SensorDef("MOTOR_CURRENT", "모터 전류", "A", 18.0, 0.25, 0.2, 17.0, 19.0, 16.0, 20.0),
        SensorDef("PAD_TEMP", "패드 온도", "degC", 42.0, 0.6, 0.45, 39.0, 45.0, 37.0, 47.0),
    ),
    "Prober": (
        SensorDef("CHUCK_TEMP", "척 온도", "degC", 25.0, 0.1, 0.05, 24.7, 25.3, 24.5, 25.5),
        SensorDef("CONTACT_RES", "접촉 저항", "mohm", 50.0, 1.0, 0.8, 45.0, 55.0, 40.0, 60.0),
        SensorDef("PROBE_FORCE", "프로브 압력", "mN", 30.0, 0.3, 0.25, 28.0, 32.0, 26.0, 34.0),
        SensorDef("TOUCHDOWN_CNT", "터치다운 횟수", "cnt", 1200.0, 40.0, 25.0, 1000.0, 1400.0, 900.0, 1500.0),
    ),
}

EQP_TYPES: tuple[str, ...] = tuple(sorted(TYPE_SENSORS))


def sensors_for(eqp_type: str) -> tuple[SensorDef, ...]:
    """공통 2종 + 유형 고유 4종. 미지의 유형이면 공통 2종만 돌려준다."""
    return COMMON_SENSORS + TYPE_SENSORS.get(eqp_type, ())



def idle_sensors() -> tuple[SensorDef, ...]:
    """설비가 멈춰 있을 때도 의미가 있는 센서.

    챔버 압력이나 RF 파워는 멈춘 설비에서 측정 자체가 무의미하다. 그렇다고
    0 을 내보내면 RF_POWER 의 alarm_min 이 1400 이라 유휴 내내 경보가 된다.
    클린룸 주변 온도·습도는 설비 가동과 무관하게 계속 측정된다.
    """
    return COMMON_SENSORS

def sensor_by_code(eqp_type: str, sensor_code: str) -> SensorDef:
    for sensor in sensors_for(eqp_type):
        if sensor.sensor_code == sensor_code:
            return sensor
    raise KeyError(f"{eqp_type} 에 {sensor_code} 센서가 없습니다.")


def build_sensor_spec_rows() -> list[dict]:
    """fdc_sensor_spec 42행. 키는 (eqp_type, sensor_code)."""
    return [
        {
            "sensor_code": sensor.sensor_code,
            "sensor_name_ko": sensor.sensor_name_ko,
            "eqp_type": eqp_type,
            "unit": sensor.unit,
            "normal_min": sensor.normal_min,
            "normal_max": sensor.normal_max,
            "alarm_min": sensor.alarm_min,
            "alarm_max": sensor.alarm_max,
            "sample_interval_sec": SAMPLE_INTERVAL_SEC,
            "is_active": True,
        }
        for eqp_type in EQP_TYPES
        for sensor in sensors_for(eqp_type)
    ]
