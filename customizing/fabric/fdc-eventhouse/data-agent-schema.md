# FDC Eventhouse 스키마 — 에이전트 지식

Fabric Data Agent나 Foundry 에이전트에 이 Eventhouse를 붙일 때 지식으로 넣는 문서입니다.
에이전트가 **언제 다른 시스템에 물어야 하는지** 알게 하는 것이 목적입니다.

## 이 데이터가 답할 수 있는 것

- 설비별 센서 판독값의 시계열 추이
- 어느 설비의 어느 센서가 정상 범위를 벗어났는가
- 이상이 언제 시작되고 언제 끝났는가
- 설비가 언제 돌고 있었고 언제 멈춰 있었는가 (`run_status`)
- 설비 간 환경 조건 비교 (주변 온·습도는 전 설비 공통 센서)

## 이 데이터가 **답할 수 없는** 것

**이 절이 가장 중요합니다.** 아래 질문을 받으면 FDC만으로 답하지 말고 MES나 QMS에
질의해야 합니다.

| 질문 | 물어야 할 곳 |
|---|---|
| 그 시각에 그 설비에 어떤 로트가 있었나 | MES `list_process_results` — **경보 시각을 함께 넘기세요** |
| 그 로트는 어떤 제품인가 | MES `list_lots` |
| 그 로트는 합격인가 | QMS `qms_inspection_result` |
| 어떤 결함이 나왔나 | QMS `qms_nonconformance` |
| 몇 장을 투입해 몇 장이 나왔나 | MES `process_results` 의 `in_qty`/`out_qty` |
| 누가 작업했나 | MES `operator` |

FDC 판독값에는 `lot_id`, `product_code`, `wafer_qty`, `defect_code`, `judgment`,
`result`, `operator` 컬럼이 **존재하지 않습니다.** 센서는 지금 어떤 로트가 올라와
있는지 모르기 때문입니다. 추측해서 답하지 말고 반드시 MES에 질의하세요.

`run_status` 는 설비가 돌고 있었는지(`Run`) 멈춰 있었는지(`Idle`)만 알려줍니다.
SEMI E10 설비 상태이지 로트 정보가 아닙니다. `run_status == "Run"` 이라고 해서
어떤 로트였는지 알 수 있는 것은 아닙니다.

## 시스템 간 연결 키

```
FDC.eqp_id  ==  MES.process_results.eqp_id
FDC.reading_ts  ∈  [MES.process_results.in_time, out_time)
FDC.step_code  ==  MES.route.step_code  ==  QMS.step_code
FDC.eqp_type  ==  MES.route.eqp_type
```

**`eqp_id` 와 시각을 함께 써서 이으세요.** MES `process_results` 는 로트마다
`in_time` / `out_time` 구간을 갖고, 같은 설비에서 구간이 겹치지 않습니다. FDC는
그 구간에만 공정 센서를 내보내므로 경보 시각 하나로 로트가 유일하게 특정됩니다.

`eqp_id` 만으로 이으면 그 설비가 처리한 **모든** 로트가 걸려서 어느 런의 문제였는지
구분할 수 없습니다. 이상은 설비 단위가 아니라 런 단위로 일어납니다.

FDC 데이터는 MES 공정이력이 걸친 구간(기본 약 65시간)을 덮고, 그 이후 시각은 전부
`Idle` 입니다. **`ago(24h)` 같은 상대 시간 필터를 쓰면 가동 구간을 통째로 놓칠 수
있습니다.** 범위를 좁히기 전에 `summarize min(reading_ts), max(reading_ts)` 로 실제
구간을 먼저 확인하세요.

## 테이블

### `fdc_sensor_reading` — 계속 증가

설비가 **돌고 있을 때만** 공정 센서 6종을 30초 간격으로 남깁니다. 멈춰 있는 동안에는
주변 온·습도 2종만 5분 간격입니다. MES 공정이력이 덮는 65시간 구간이 약 102,000행이고,
그중 가동이 93,210행 · 유휴가 9,342행입니다.

**총 행 수를 고정값으로 알고 있으면 안 됩니다.** 데이터는 MES 공정이력 시작 시각부터
현재까지 이어지고, MES 종료 이후 구간은 돌고 있는 로트가 없어 전부 유휴입니다. 그래서
유휴 행이 하루 약 4,608행씩 계속 늘어납니다. 행 수를 알아야 하면 추측하지 말고
`fdc_sensor_reading | summarize count() by run_status` 로 직접 세세요.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `reading_ts` | datetime | UTC. 30초 격자에 정렬 |
| `eqp_id` | string | **MES와 이어지는 키** |
| `eqp_type` | string | `Furnace`/`Scanner`/`Etcher`/`Implanter`/`CVD`/`Polisher`/`Prober` |
| `step_code` | string | 설비가 속한 공정 |
| `run_status` | string | `Run`(가동 중) / `Idle`(유휴). 로트 정보가 아님 |
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
| `sample_interval_sec` | int | 30. **가동 중 간격**이다. 유휴에는 300초 |
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

경보는 **가동 중에만** 납니다. 유휴 구간에는 0건입니다. 그래서 전체 경보 건수는
MES 공정이력 구간에서 결정되고 **약 300건으로 고정**입니다. 반면 유휴 행은 계속
쌓이므로 전체 대비 **비율**은 시간이 갈수록 내려갑니다. 비율을 기준으로 판단하지
말고 건수와 설비별 분포를 보세요.

경보가 여러 설비에 고르게 퍼져 있으면 데이터가 잘못된 것입니다. 불량률이 높은
설비에 몰려 있는 것이 정상입니다.

정상 런에도 `Warning` 은 자연 잡음으로 가끔 뜹니다. 불량과 이어지는 신호를 보려면
`status == "Alarm"` 으로 거르세요.

**`AMBIENT_TEMP` 와 `AMBIENT_HUMIDITY` 는 클린룸 환경 센서입니다.** 전 설비가 공통으로
갖기 때문에 경보 순위 상위를 이 둘이 덮습니다. 실측 상위 3개가 전부 `AMBIENT_*` 이고
설비 고유 신호는 4위에서야 나옵니다. 환경 경보는 설비 자체 문제가 아니라 그 시간대에
클린룸이 흔들린 것일 수 있으므로, **어느 설비를 조사할지 고를 때는 `AMBIENT_*` 를 먼저
걷어내고 보세요.** 그러면 `PRECURSOR_FLOW`(CVD), `PAD_PRESSURE`(CMP),
`STAGE_TEMP`(PHOTO), `CHAMBER_TEMP`(ETCH) 처럼 공정에 직접 묶인 신호만 남습니다.

환경 경보를 조사해야 할 때는 같은 시간대에 다른 설비도 함께 튀었는지 보세요. 여러
설비가 동시에 튀었으면 설비가 아니라 환경 문제입니다.

## 자주 쓰는 KQL

```kusto
// 0. 데이터가 어느 구간을 덮는지 먼저 확인합니다. ago() 를 쓰기 전에 이걸 보세요.
fdc_sensor_reading
| summarize 시작 = min(reading_ts), 종료 = max(reading_ts), 행 = count() by run_status
```

```kusto
// 1. 경보가 몰린 설비와 시각. 교차 질의의 출발점입니다.
//    AMBIENT_* 는 전 설비 공통 환경 센서라 상위를 덮습니다. 어느 설비를
//    조사할지 고르는 단계에서는 걷어내고 봅니다.
fdc_sensor_reading
| where status == "Alarm" and run_status == "Run"
| where sensor_code !startswith "AMBIENT_"
| summarize 건수 = count(), 시작 = min(reading_ts), 종료 = max(reading_ts)
    by eqp_id, sensor_code
| order by 건수 desc
```

```kusto
// 1b. 환경 경보까지 포함한 전체 순위. 클린룸이 흔들린 시간대를 찾을 때 씁니다.
//     여러 설비가 같은 시각에 함께 튀었으면 설비가 아니라 환경 문제입니다.
fdc_sensor_reading
| where status == "Alarm" and run_status == "Run"
| summarize 건수 = count(), 시작 = min(reading_ts), 종료 = max(reading_ts)
    by eqp_id, sensor_code
| order by 건수 desc
```

```kusto
// 2. 설비별 주변 온도 추이. 전 설비 공통 센서라 함께 비교됩니다.
fdc_sensor_reading
| where sensor_code == "AMBIENT_TEMP"
| make-series avg(value) default=0 on reading_ts step 10m by eqp_id
| render timechart
```

```kusto
// 3. 한계치까지 남은 여유. 스펙 테이블과 조인해 얻습니다.
fdc_sensor_reading
| where run_status == "Run"
| join kind=inner fdc_sensor_spec on eqp_type, sensor_code
| extend 여유 = normal_max - value
| project reading_ts, eqp_id, sensor_code, value, normal_min, normal_max, 여유
```

```kusto
// 4. 설비 가동 구간. MES 에 어느 로트였는지 물을 시각 범위를 여기서 얻습니다.
fdc_sensor_reading
| where run_status == "Run"
| summarize 시작 = min(reading_ts), 종료 = max(reading_ts) by eqp_id, bin(reading_ts, 1h)
| order by eqp_id asc, 시작 asc
```

## 답할 때의 원칙

1. 센서 이상을 찾았으면 거기서 멈추지 말고 **그 시각에 그 설비에서 처리한 로트를
   MES에 물으세요.** 설비 이름만 넘기지 말고 경보가 난 시각 범위를 함께 넘기세요.
2. 한계치를 인용할 때는 `fdc_sensor_spec` 과 조인해 실제 값을 쓰세요. 외우지 마세요.
3. `sensor_code` 만으로 센서를 특정하지 마세요. `eqp_type` 과 함께 봐야 합니다.
4. MES와 이을 때는 `eqp_id` **와 시각 범위**를 함께 쓰세요. `eqp_id` 만으로 이으면
   그 설비의 모든 로트가 걸려 어느 런의 문제였는지 알 수 없습니다.
5. `ago(24h)` 같은 상대 시간 필터를 습관적으로 붙이지 마세요. 가동 구간이 과거라
   빈 결과가 나올 수 있습니다. 먼저 실제 데이터 구간을 확인하세요.
6. FDC에 없는 컬럼을 지어내지 마세요. 모르면 모른다고 하고 어디에 물어야 하는지
   알려주세요.
