# FDC Eventhouse 스키마 — 에이전트 지식

Fabric Data Agent나 Foundry 에이전트에 이 Eventhouse를 붙일 때 지식으로 넣는 문서입니다.
에이전트가 **언제 다른 시스템에 물어야 하는지** 알게 하는 것이 목적입니다.

## 이 데이터가 답할 수 있는 것

- 설비별 센서 판독값의 시계열 추이
- 어느 설비의 어느 센서가 정상 범위를 벗어났는가
- 이상이 언제 시작되고 언제 끝났는가
- 설비 간 환경 조건 비교 (주변 온·습도는 전 설비 공통 센서)

## 이 데이터가 **답할 수 없는** 것

**이 절이 가장 중요합니다.** 아래 질문을 받으면 FDC만으로 답하지 말고 MES나 QMS에
질의해야 합니다.

| 질문 | 물어야 할 곳 |
|---|---|
| 그 시각에 그 설비에 어떤 로트가 있었나 | MES `list_process_results` |
| 그 로트는 어떤 제품인가 | MES `list_lots` |
| 그 로트는 합격인가 | QMS `qms_inspection_result` |
| 어떤 결함이 나왔나 | QMS `qms_nonconformance` |
| 몇 장을 투입해 몇 장이 나왔나 | MES `process_results` 의 `in_qty`/`out_qty` |
| 누가 작업했나 | MES `operator` |

FDC 판독값에는 `lot_id`, `product_code`, `wafer_qty`, `defect_code`, `judgment`,
`result`, `operator` 컬럼이 **존재하지 않습니다.** 센서는 지금 어떤 로트가 올라와
있는지 모르기 때문입니다. 추측해서 답하지 말고 반드시 MES에 질의하세요.

## 시스템 간 연결 키

```
FDC.eqp_id  ==  MES.process_results.eqp_id
FDC.step_code  ==  MES.route.step_code  ==  QMS.step_code
FDC.eqp_type  ==  MES.route.eqp_type
```

**시간으로 조인하지 마세요.** MES `process_results` 는 `in_time == out_time` 이고 전
레코드가 2초 안에 몰려 있어 공정 구간이 존재하지 않습니다. 시간 범위 조인은 빈 결과나
잘못된 결과를 냅니다. 항상 `eqp_id` 로 이으세요.

## 테이블

### `fdc_sensor_reading` — 계속 증가

30초마다 설비 8대 × 센서 6종 = 48행이 쌓입니다. 하루 약 138,000행입니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `reading_ts` | datetime | UTC. 30초 격자에 정렬 |
| `eqp_id` | string | **MES와 이어지는 키** |
| `eqp_type` | string | `Furnace`/`Scanner`/`Etcher`/`Implanter`/`CVD`/`Polisher`/`Prober` |
| `step_code` | string | 설비가 속한 공정 |
| `sensor_code` | string | `fdc_sensor_spec` 참조 |
| `value` | real | 판독값 |
| `unit` | string | 단위 |
| `status` | string | `Normal`/`Warning`/`Alarm` |

### `fdc_sensor_spec` — 42행 정적

센서 마스터입니다. 한계치를 판독값마다 복사하지 않고 이 테이블과 조인해 얻습니다.
기본 키는 (`eqp_type`, `sensor_code`) **조합**입니다. `sensor_code` 만으로는 유일하지
않습니다. 예를 들어 `CHAMBER_TEMP` 는 Furnace에서 1050°C, CVD에서 420°C입니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `sensor_code` | string | 예 `CHAMBER_TEMP` |
| `sensor_name_ko` | string | 예 "챔버 온도" |
| `eqp_type` | string | 설비 유형 |
| `unit` | string | 단위 |
| `normal_min` / `normal_max` | real | 정상 운전 범위 |
| `alarm_min` / `alarm_max` | real | 경보 범위 |
| `sample_interval_sec` | int | 30 |
| `is_active` | bool | 사용 여부 |

## 설비 유형별 센서

모든 유형이 공통 센서 2종(`AMBIENT_TEMP`, `AMBIENT_HUMIDITY`)을 갖습니다. 전 설비를
한 차트에서 비교할 수 있는 유일한 계열입니다.

| eqp_type | step | 고유 센서 4종 |
|---|---|---|
| Furnace | DIFF | CHAMBER_TEMP, RAMP_RATE, O2_CONC, N2_FLOW |
| Scanner | PHOTO | STAGE_TEMP, FOCUS_OFFSET, ILLUM_DOSE, RETICLE_TEMP |
| Etcher | ETCH | RF_POWER, CHAMBER_PRESSURE, GAS_FLOW, CHAMBER_TEMP |
| Implanter | IMPL | BEAM_CURRENT, BEAM_ENERGY, VACUUM, SRC_TEMP |
| CVD | CVD | CHAMBER_TEMP, CHAMBER_PRESSURE, PRECURSOR_FLOW, DEP_RATE |
| Polisher | CMP | PAD_PRESSURE, SLURRY_FLOW, MOTOR_CURRENT, PAD_TEMP |
| Prober | TEST | CHUCK_TEMP, CONTACT_RES, PROBE_FORCE, TOUCHDOWN_CNT |

## status 산출 규칙

```
alarm_min..alarm_max 밖  →  Alarm
normal_min..normal_max 밖  →  Warning
그 외  →  Normal
```

정상이 94% 정도이고 경보는 0.3% 안팎입니다. 경보가 여러 설비에 고르게 퍼져 있으면
데이터가 잘못된 것입니다. 특정 설비에 몰려 있는 것이 정상입니다.

## 자주 쓰는 KQL

```kusto
// 이상이 가장 많은 설비
fdc_sensor_reading
| where reading_ts > ago(24h) and status != "Normal"
| summarize 이상 = count() by eqp_id, sensor_code, status
| order by 이상 desc
```

```kusto
// 설비별 주변 온도 추이
fdc_sensor_reading
| where reading_ts > ago(24h) and sensor_code == "AMBIENT_TEMP"
| make-series avg(value) default=0 on reading_ts step 10m by eqp_id
| render timechart
```

```kusto
// 한계치까지 남은 여유. 스펙 테이블과 조인해 얻습니다.
fdc_sensor_reading
| where reading_ts > ago(1h)
| join kind=inner fdc_sensor_spec on eqp_type, sensor_code
| extend 여유 = normal_max - value
| project reading_ts, eqp_id, sensor_code, value, normal_min, normal_max, 여유
```

```kusto
// 이상 구간의 시작과 끝
fdc_sensor_reading
| where reading_ts > ago(24h) and status == "Alarm"
| summarize 시작 = min(reading_ts), 종료 = max(reading_ts), 건수 = count()
    by eqp_id, sensor_code
| order by 건수 desc
```

## 답할 때의 원칙

1. 센서 이상을 찾았으면 거기서 멈추지 말고 **그 설비에서 처리한 로트를 MES에 물으세요.**
2. 한계치를 인용할 때는 `fdc_sensor_spec` 과 조인해 실제 값을 쓰세요. 외우지 마세요.
3. `sensor_code` 만으로 센서를 특정하지 마세요. `eqp_type` 과 함께 봐야 합니다.
4. 시간 범위로 MES와 조인하지 마세요. `eqp_id` 로 이으세요.
5. FDC에 없는 컬럼을 지어내지 마세요. 모르면 모른다고 하고 어디에 물어야 하는지
   알려주세요.
