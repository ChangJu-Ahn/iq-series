"""판독값 생성. 전부 순수 함수다.

이 모듈의 모든 함수는 같은 (설비, 센서, 타임스탬프)에 대해 언제 어디서
불러도 같은 값을 낸다. 순차 상태도, 실행 시각 의존도 없다.

그래야 하는 이유는 재실행 때문이다. 노트북은 3분마다 새 프로세스로 돌고,
장애로 걸렀던 구간을 나중에 백필한다. 값이 실행 시점에 좌우되면 백필한
구간과 정상 적재한 구간이 이어지지 않아 시계열에 계단이 생긴다.

`random` 모듈 전역 함수는 쓰지 않는다. 전역 상태를 공유하므로 호출 순서가
값에 영향을 준다. 항상 `random.Random(seed(...))` 인스턴스를 만들어 쓴다.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from src.fdc_anomaly import (
    EXCURSION_PERIOD_SEC,
    EquipmentProfile,
    build_profiles,
    excursion_for,
    excursion_sign,
    seed,
)
from src.fdc_sensors import SAMPLE_INTERVAL_SEC, SensorDef, sensors_for

NORMAL = "Normal"
WARNING = "Warning"
ALARM = "Alarm"


def align_to_grid(moment: datetime, interval_sec: int = SAMPLE_INTERVAL_SEC) -> datetime:
    """격자 시각으로 내림. 격자는 epoch 기준이라 실행 시각과 무관하다."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % interval_sec, tz=timezone.utc)


def grid_timestamps(
    start: datetime, end: datetime, interval_sec: int = SAMPLE_INTERVAL_SEC
) -> list[datetime]:
    """start 초과 end 이하의 격자 시각.

    start 를 제외하는 이유는 watermark 를 그대로 넘겨받기 때문이다. 이미
    적재한 마지막 시각을 다시 만들면 중복 행이 생긴다.
    """
    if end < start:
        return []
    first = align_to_grid(start, interval_sec) + timedelta(seconds=interval_sec)
    last = align_to_grid(end, interval_sec)
    out = []
    current = first
    while current <= last:
        out.append(current)
        current += timedelta(seconds=interval_sec)
    return out


def diurnal(sensor: SensorDef, eqp_id: str, moment: datetime) -> float:
    """하루 주기 성분. 24시간 백필 차트에서 눈에 보이는 패턴을 만든다."""
    seconds_of_day = moment.hour * 3600 + moment.minute * 60 + moment.second
    phase = 2 * math.pi * (seed(eqp_id, sensor.sensor_code, "phase") / 2**32)
    return sensor.diurnal_amp * math.sin(2 * math.pi * seconds_of_day / 86400 + phase)


def noise(sensor: SensorDef, eqp_id: str, moment: datetime) -> float:
    """가우시안 잡음. 타임스탬프마다 고정이다."""
    rng = random.Random(seed(eqp_id, sensor.sensor_code, int(moment.timestamp())))
    return rng.gauss(0.0, sensor.sigma)


def excursion_offset(
    sensor: SensorDef, eqp_id: str, moment: datetime, profile: EquipmentProfile
) -> float:
    """이상 구간의 이탈량.

    진폭은 MES 불량 실적에서 오고, 시간에 따른 세기는 느린 파형으로 준다.
    항상 최대로 이탈하면 경보가 끊이지 않아 실습자가 '언제 이상해졌나'를
    물을 수 없다. 파형을 제곱해 대부분의 시간은 잠잠하고 가끔 크게 튀게 한다.
    """
    amplitude = excursion_for(profile, sensor.sensor_code)
    if amplitude <= 0.0:
        return 0.0
    phase = 2 * math.pi * (seed(eqp_id, sensor.sensor_code, "excursion") / 2**32)
    wave = math.sin(2 * math.pi * moment.timestamp() / EXCURSION_PERIOD_SEC + phase)
    pulse = max(0.0, wave) ** 2
    half = (sensor.normal_max - sensor.normal_min) / 2
    return excursion_sign(eqp_id, sensor.sensor_code) * amplitude * half * pulse


def reading_value(
    sensor: SensorDef, eqp_id: str, moment: datetime, profile: EquipmentProfile
) -> float:
    value = (
        sensor.base
        + diurnal(sensor, eqp_id, moment)
        + noise(sensor, eqp_id, moment)
        + excursion_offset(sensor, eqp_id, moment, profile)
    )
    return round(value, 4)


def classify(sensor: SensorDef, value: float) -> str:
    if value < sensor.alarm_min or value > sensor.alarm_max:
        return ALARM
    if value < sensor.normal_min or value > sensor.normal_max:
        return WARNING
    return NORMAL


def build_readings(facts, start: datetime, end: datetime) -> list[dict]:
    """구간 안의 모든 설비·센서 판독값.

    행 개수는 설비 8대 x 센서 6종 x 격자 수다. 30초 격자에서 3분 구간이면
    8 x 6 x 6 = 288 행이다.
    """
    profiles = build_profiles(facts)
    moments = grid_timestamps(start, end)
    rows: list[dict] = []
    for profile in profiles.values():
        for sensor in sensors_for(profile.eqp_type):
            for moment in moments:
                value = reading_value(sensor, profile.eqp_id, moment, profile)
                rows.append(
                    {
                        "reading_ts": moment,
                        "eqp_id": profile.eqp_id,
                        "eqp_type": profile.eqp_type,
                        "step_code": profile.step_code,
                        "sensor_code": sensor.sensor_code,
                        "value": value,
                        "unit": sensor.unit,
                        "status": classify(sensor, value),
                    }
                )
    return rows
