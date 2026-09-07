"""판독값 생성. MES 스냅샷을 고정하면 전부 순수 함수다.

이 모듈의 모든 함수는 주어진 MES 스냅샷 아래에서 같은 (설비, 센서,
타임스탬프)에 대해 언제 어디서 불러도 같은 값을 낸다. 순차 상태도, 실행
시각 의존도 없다.

그래야 하는 이유는 재실행 때문이다. 노트북은 3분마다 새 프로세스로 돌고,
장애로 걸렀던 구간을 나중에 백필한다. 값이 실행 시점에 좌우되면 백필한
구간과 정상 적재한 구간이 이어지지 않아 시계열에 계단이 생긴다.

MES 스냅샷이 입력인 것은 설계 목표다. "불량률이 높은 설비일수록 크게
이탈한다"는 관계가 이 데이터셋의 존재 이유이므로 실적을 없앨 수 없다.
다만 한 설비의 값은 **그 설비 자신의 실적**에만 의존한다(fdc_anomaly의
severity 참고). 그래서 `EQP-CMP01` 에 공정 실적이 하나 늘어도 나머지 일곱
대의 판독값은 한 행도 바뀌지 않는다.

`random` 모듈 전역 함수는 쓰지 않는다. 전역 상태를 공유하므로 호출 순서가
값에 영향을 준다. 항상 `random.Random(seed(...))` 인스턴스를 만들어 쓴다.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from src.fdc_anomaly import (
    EquipmentProfile,
    build_profiles,
    run_excursion,
    seed,
)
from src.fdc_runs import Run, run_at, runs_by_equipment
from src.fdc_sensors import (
    SAMPLE_INTERVAL_SEC,
    SensorDef,
    idle_sensors,
    sensors_for,
)

NORMAL = "Normal"
WARNING = "Warning"
ALARM = "Alarm"

RUNNING = "Run"
IDLE = "Idle"

# 유휴 샘플링 간격. 30초의 배수여야 한다. align_to_grid 가 epoch 기준이라
# 배수이기만 하면 유휴 격자가 런 격자의 부분집합이 되어 중복이 없다.
IDLE_INTERVAL_SEC = 300


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
    sensor: SensorDef, run: Run | None, moment: datetime, profile: EquipmentProfile
) -> float:
    """이상 구간의 이탈량. 런 밖이면 0 이다.

    진폭도 대상 센서도 시점도 전부 MES 불량 실적에서 온다. 그래야
    Eventhouse 에서 찾은 이상이 MES 의 실제 공정이력과 맞아떨어진다.
    """
    if run is None:
        return 0.0
    scale = run_excursion(run, sensor.sensor_code, moment, profile)
    if scale == 0.0:
        return 0.0
    half = (sensor.normal_max - sensor.normal_min) / 2
    return scale * half


def reading_value(
    sensor: SensorDef,
    eqp_id: str,
    moment: datetime,
    profile: EquipmentProfile,
    run: Run | None = None,
) -> float:
    value = (
        sensor.base
        + diurnal(sensor, eqp_id, moment)
        + noise(sensor, eqp_id, moment)
        + excursion_offset(sensor, run, moment, profile)
    )
    return round(value, 4)


def classify(sensor: SensorDef, value: float) -> str:
    if value < sensor.alarm_min or value > sensor.alarm_max:
        return ALARM
    if value < sensor.normal_min or value > sensor.normal_max:
        return WARNING
    return NORMAL


def build_readings(facts, start: datetime, end: datetime) -> list[dict]:
    """구간 안의 모든 판독값.

    30초 격자를 하나만 깔고 각 시각을 런/유휴로 나눈다. 격자를 둘 만들지
    않는 이유는 한 시각이 양쪽에 속해 중복 행이 생기는 것을 막기 위해서다.
    IDLE_INTERVAL_SEC 가 30초의 배수이고 격자가 epoch 기준이라, 유휴 격자는
    런 격자의 부분집합이다.

    런 중에는 센서 6종을 30초마다, 유휴에는 공통 2종을 5분마다 낸다. 멈춘
    설비의 챔버 압력을 30초마다 적는 FDC 는 없다.

    로트 번호는 이상 배치 계산에만 쓰고 행에는 넣지 않는다. 어느 로트였는지는
    MES 에 물어야 한다.
    """
    profiles = build_profiles(facts)
    all_runs = runs_by_equipment(facts)
    moments = grid_timestamps(start, end)
    idle = idle_sensors()
    rows: list[dict] = []

    for profile in profiles.values():
        runs = all_runs.get(profile.eqp_id, [])
        running_sensors = sensors_for(profile.eqp_type)
        for moment in moments:
            run = run_at(runs, moment)
            if run is not None:
                active, run_status = running_sensors, RUNNING
            elif int(moment.timestamp()) % IDLE_INTERVAL_SEC == 0:
                active, run_status = idle, IDLE
            else:
                continue
            for sensor in active:
                value = reading_value(sensor, profile.eqp_id, moment, profile, run)
                rows.append(
                    {
                        "reading_ts": moment,
                        "eqp_id": profile.eqp_id,
                        "eqp_type": profile.eqp_type,
                        "step_code": profile.step_code,
                        "run_status": run_status,
                        "sensor_code": sensor.sensor_code,
                        "value": value,
                        "unit": sensor.unit,
                        "status": classify(sensor, value),
                    }
                )
    return rows
