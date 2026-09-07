# FDC 설비 텔레메트리 → Fabric Eventhouse

설비 센서 판독값을 만들어 Fabric **Eventhouse(KQL DB)** 에 계속 적재하는 노트북입니다.
Fabric 스케줄러로 몇 분마다 돌리면 실제 팹처럼 시계열이 쌓입니다.

이 실습은 20명이 각자 자기 작업 영역에서 따로 돌리는 것을 전제로 만들었습니다.
준비 단계가 하나 늘 때마다 20번 막히므로, 참가자가 복사할 값은 **Query URI 하나**뿐이고
설치할 패키지는 없습니다.

## 세 번째 시스템

| 시스템 | 저장소 | 아는 것 |
|---|---|---|
| MES | Mock API (REST + MCP) | 무엇을 언제 어느 설비에서 만들었는가 |
| QMS | Lakehouse (Delta) | 그것이 합격인가, 결함은 무엇인가 |
| **FDC** | **Eventhouse (KQL)** | **설비가 그때 어떤 상태였는가** |

셋은 서로 다른 시스템입니다. DB 수준의 외래키는 없고 `eqp_id` 와 `step_code` 라는
비즈니스 키로만 이어집니다.

### FDC가 모르는 것

`fdc_sensor_reading` 에는 다음 컬럼이 **없습니다**.

```
lot_id  product_code  wafer_qty  defect_code  judgment  result  operator
```

센서는 지금 어떤 로트가 올라와 있는지 모릅니다. 그래서 "챔버 온도가 튄 그 시각에
어떤 로트가 그 설비에 있었나"를 알려면 **그 시각을 들고 MES에 물어야** 합니다.
이 경계가 이 실습의 전부입니다. 편의를 위해 `lot_id` 를 판독값에 넣는 순간 교차
질의를 할 이유가 사라집니다.

대신 `run_status` 컬럼이 `Run` / `Idle` 로 설비가 돌고 있었는지만 알려줍니다.
SEMI E10 의 설비 상태이지 로트 정보가 아닙니다. 어떤 로트였는지는 여전히 모릅니다.

테스트가 이 경계를 강제합니다(`test_rows_carry_no_lot_columns`, 검증 1번 항목).

## 시간축이 세 시스템을 잇습니다

MES `process_results` 는 로트를 공정 순서대로 흘려보내며 `in_time` / `out_time` 을
겹치지 않게 배정합니다. 설비 하나가 같은 시각에 두 로트를 처리하지 않습니다.
FDC는 이 구간을 그대로 읽어 **설비가 돌던 시간에만** 공정 센서를 내보냅니다.

```
MES  LOT0005 CMP  EQP-CMP01  09-02 09:10 ~ 09:40   (Scratch)
                                   │
FDC  EQP-CMP01  PAD_PRESSURE  09-02 09:34 Alarm ───┘  같은 시각·같은 설비
```

두 시스템에 공유된 키는 `eqp_id` 와 **시각**뿐입니다. 이 둘로 범위 조인을 해야
"이상이 난 그때 무엇을 만들고 있었나"에 답할 수 있습니다.

기본 픽스처 기준으로 데이터는 **약 65시간**(`2026-09-01 07:13` ~ `09-04 00:00`)을
덮습니다. 벽시계로 최근 몇 시간이 아닙니다. MES가 아는 구간을 통째로 덮지 않으면
FDC에서 찾은 이상을 MES에서 확인할 수 없습니다.

### 가동 중과 멈춰 있을 때

| `run_status` | 센서 | 간격 | 행 수(MES 구간) |
|---|---|---|---|
| `Run` | 공정 센서 6종 | 30초 | 93,210 |
| `Idle` | `AMBIENT_TEMP`, `AMBIENT_HUMIDITY` 2종 | 5분 | 9,342 |

`Run` 93,210행은 MES 공정이력이 있는 구간에서만 나옵니다. MES 종료 이후는 돌고
있는 로트가 없으므로 전부 `Idle` 이고, 그래서 `Idle` 행만 하루 4,608행씩
늘어납니다.

`fdc_sensor_spec.sample_interval_sec` 은 **가동 중 간격**입니다. 유휴에는 챔버가
비어 있으니 진공도나 RF 출력을 재봐야 의미가 없고, 클린룸 환경만 계속 기록됩니다.
경보는 가동 중에만 납니다.

## 이상은 MES에서 유도합니다

"고장 설비 목록"을 하드코딩하지 않습니다. Mock MES가 바뀌면 이상 패턴도 따라 바뀝니다.

1. `process_results` 에서 설비별 불량률을 집계합니다(`eqp_id` 가 빈 7건은 제외).
2. 불량률이 높은 설비일수록 이탈 진폭을 키웁니다. 진폭은 **그 설비 자신의
   불량률**만으로 정합니다(불량률 0 이면 1.0, 1 이면 3.4 — 정상 범위 반폭 기준).
   전체 설비의 최소·최대로 정규화하지 않는 이유는 아래 "재실행해도 안전한 이유"에
   있습니다.
3. 불량코드가 물리적으로 지목하는 센서에만 이탈을 싣습니다.
4. **이탈은 그 로트를 처리한 런 구간 안에서만** 일어납니다. 설비가 상시 고장인 게
   아니라 그 런에서 문제가 생긴 것입니다.

| 불량코드 | 지목 센서 | 근거 |
|---|---|---|
| `Particle` | CHAMBER_TEMP, AMBIENT_HUMIDITY | 온도 급변 시 챔버 박리물 발생 |
| `Scratch` | PAD_PRESSURE, MOTOR_CURRENT, PROBE_FORCE | 기계적 접촉 과다 |
| `Overlay` | AMBIENT_TEMP, STAGE_TEMP, RETICLE_TEMP | 열팽창에 의한 정렬 오차 |
| `Etch-Residue` | GAS_FLOW, PRECURSOR_FLOW, RF_POWER | 반응 가스 부족 |
| `Contamination` | AMBIENT_HUMIDITY, VACUUM | 습도 상승·진공도 저하 |
| `CD-OOS` | FOCUS_OFFSET, RF_POWER, ILLUM_DOSE | 노광·식각 조건 이탈 |

지목 센서가 그 설비 유형에 하나도 없으면 모든 유형이 갖는 `AMBIENT_TEMP` 로 넘깁니다.
Mock MES가 불량코드를 공정 단계와 무관하게 붙이기 때문입니다. 실제로 `EQP-IMPL01` 은
Implanter인데 `Scratch` 불량이 달려 있고, `Scratch` 가 가리키는 센서는 Implanter에
하나도 없습니다.

지목 센서가 여러 개여도 **런마다 그중 하나만** 고릅니다. 나눠 실으면 어느 것도
경보 문턱에 못 닿아 전부 조용해집니다. 같은 설비·같은 불량코드라도 런이 다르면
흐른 센서가 다를 수 있습니다.

결과적으로 **MES 공정이력 65시간 구간**의 분포는 다음과 같습니다.

| 상태 | 행 수 | 비율 |
|---|---|---|
| Normal | 100,924 | 98.41% |
| Warning | 1,327 | 1.29% |
| Alarm | 301 | 0.29% |

> 이 표는 MES 구간만 센 값입니다. 노트북은 MES 종료 시각 이후 현재까지도
> 채우는데, 그 구간은 전부 유휴라 **하루 약 4,608행씩 Normal 만 늘어납니다.**
> 그래서 실제로 보게 될 총 행 수는 이 표보다 크고 비율은 조금 내려갑니다.
> **`Alarm` 301건은 변하지 않습니다** — 유휴에는 경보가 나지 않기 때문입니다.
> 예를 들어 MES 배포 일주일 뒤에 실습하면 총 134,808행 · Alarm 301건입니다.

불량 런 31건 중 **23건**에서 경보가 났고, 정상 런에서 난 경보는 **0건**입니다.
유휴 중 경보도 0건입니다. `Alarm` 이 가장 많은 설비는 불량률 1위인
`EQP-CMP01`(130행)이고, 가장 깨끗한 `EQP-IMPL01` 은 0행입니다. Eventhouse에서
찾아낸 이상이 MES 실적과 맞아떨어집니다.

정상 런에도 `Warning` 은 가끔 뜹니다. 자연 잡음이고 실제 팹도 그렇습니다.
불량과 이어지는 신호를 보려면 `status == "Alarm"` 으로 거르세요.

## 3단 교차 질의

FDC 혼자서는 절반까지밖에 못 갑니다.

**1단계 — FDC: 경보가 몰린 설비와 시각을 찾는다**

```kusto
fdc_sensor_reading
| where status == "Alarm" and run_status == "Run"
| summarize alarms = count(), ['시작'] = min(reading_ts), ['끝'] = max(reading_ts)
        by eqp_id, sensor_code
| order by alarms desc
```

기본 픽스처에서는 이렇게 나옵니다.

```
EQP-DIFF01   AMBIENT_HUMIDITY    82   09-01 20:37 ~ 09-03 20:42
EQP-CMP01    AMBIENT_TEMP        81   09-01 17:07 ~ 09-03 10:51
EQP-CMP01    AMBIENT_HUMIDITY    36   09-03 13:02 ~ 09-03 13:19
EQP-CVD01    PRECURSOR_FLOW      22   09-01 20:55 ~ 09-03 09:24
EQP-DIFF01   AMBIENT_TEMP        16   09-01 08:49 ~ 09-02 20:58
EQP-CVD01    AMBIENT_TEMP        15   09-02 11:43 ~ 09-02 11:52
EQP-PHOT02   AMBIENT_HUMIDITY    15   09-02 19:22 ~ 09-02 19:34
EQP-CMP01    PAD_PRESSURE        13   09-02 09:32 ~ 09-02 09:39
EQP-PHOT02   STAGE_TEMP           9   09-03 11:10 ~ 09-03 11:18
EQP-ETCH01   CHAMBER_TEMP         5   09-01 16:51 ~ 09-01 16:58
EQP-ETCH01   AMBIENT_HUMIDITY     3   09-02 09:06 ~ 09-02 09:10
EQP-PHOT01   AMBIENT_TEMP         3   09-02 11:00 ~ 09-02 11:02
EQP-TEST01   AMBIENT_HUMIDITY     1   09-02 13:26 ~ 09-02 13:26
```

상위가 `AMBIENT_*` 로 덮여 있습니다. 클린룸 온습도는 설비 하나의 문제가 아니라
주변 환경이라, 실제 FDC 분석에서도 먼저 걷어내고 봅니다.

```kusto
fdc_sensor_reading
| where status == "Alarm" and run_status == "Run"
| where sensor_code !startswith "AMBIENT_"
| summarize alarms = count(), ['시작'] = min(reading_ts), ['끝'] = max(reading_ts)
        by eqp_id, sensor_code
| order by alarms desc
```

그러면 설비 고유의 신호 네 개만 남고, 각각이 아래 2·3단계로 이어집니다.

```
EQP-CVD01    PRECURSOR_FLOW      22   09-01 20:55 ~ 09-03 09:24
EQP-CMP01    PAD_PRESSURE        13   09-02 09:32 ~ 09-02 09:39
EQP-PHOT02   STAGE_TEMP           9   09-03 11:10 ~ 09-03 11:18
EQP-ETCH01   CHAMBER_TEMP         5   09-01 16:51 ~ 09-01 16:58
```

**2단계 — MES: 그 설비가 그 시각에 무엇을 돌렸는지 묻는다**

```
"EQP-CMP01 이 2026-09-02 09:10 ~ 09:40 에 처리한 로트와 불량코드를 알려줘"
→ LOT0005 / CMP / Scratch
```

**3단계 — QMS: 그 로트의 검사 결과를 확인한다**

```
"LOT0005 의 검사 이력과 NCR 을 보여줘"
```

`PAD_PRESSURE` 이탈 → `Scratch` 불량은 물리적으로 이어집니다. CMP 패드 압력이 과하면
웨이퍼에 긁힘이 생깁니다. FDC의 센서 이상과 MES의 불량코드가 같은 이야기를 하고
있는지 확인하는 것이 이 실습의 목적입니다.

다른 후보들도 같은 방식으로 따라가 볼 수 있습니다.

```
LOT0011 CVD    EQP-CVD01   Etch-Residue   09-03 07:40~09:25  PRECURSOR_FLOW 17건
LOT0013 PHOTO  EQP-PHOT02  Overlay        09-03 10:06~11:19  STAGE_TEMP      9건
LOT0002 ETCH   EQP-ETCH01  Particle       09-01 15:13~17:01  CHAMBER_TEMP    5건
```

`AMBIENT_*` 경보는 클린룸 환경이라 설비 자체 문제가 아닐 수 있습니다. 같은 시간대에
다른 설비도 함께 튀었는지 보면 구분됩니다.

**METRO(계측) 공정에는 FDC 데이터가 없습니다.** 설비를 배정받지 않기 때문입니다.
모든 공정에 센서가 붙어 있지는 않다는 것도 현실입니다.

## 실행 방법

### 1. Eventhouse 준비

작업 영역에서 **Eventhouse** 를 만들고 그 안의 KQL 데이터베이스 페이지 오른쪽 위에서
**Query URI** 를 복사합니다.

```
https://trd-xxxxxxx.z9.kusto.fabric.microsoft.com
```

Query URI는 비밀값이 아닙니다. 인증은 `mssparkutils.credentials.getToken()` 이 실행자
신원으로 토큰을 발급해 처리하므로 **복사해 둘 키가 없습니다.**

이어서 같은 페이지의 **KQL 쿼리셋**에 아래 두 줄을 붙여넣고 실행해 테이블을 미리
만듭니다.

```kusto
.create-merge table fdc_sensor_spec (sensor_code:string, sensor_name_ko:string, eqp_type:string, unit:string, normal_min:real, normal_max:real, alarm_min:real, alarm_max:real, sample_interval_sec:int, is_active:bool)
.create-merge table fdc_sensor_reading (reading_ts:datetime, eqp_id:string, eqp_type:string, step_code:string, run_status:string, sensor_code:string, value:real, unit:string, status:string)
```

**노트북이 알아서 만들기 때문에 꼭 필요한 단계는 아니지만, 해 두면 좋습니다.**
노트북은 첫 실행에서 "아직 테이블이 없는" 상태를 다뤄야 하는데, 그 경로만은 실제
Eventhouse 없이 검증할 수 없습니다(아래 "알려진 한계" 참고). 미리 만들어 두면 그
상태 자체가 사라져서 첫 실행이 두 번째 실행과 똑같은 평범한 경로를 탑니다.

`.create-merge` 는 멱등이라 여러 번 실행해도 안전하고, 이미 있는 테이블의 데이터를
지우지 않습니다. 컬럼 타입은 노트북이 쓰는 것과 같습니다.

### 2. 노트북 가져오기

`fdc_eventhouse_stream.ipynb` 를 작업 영역에 업로드합니다. 파일 업로드나
`pip install` 은 필요 없습니다. `src/` 모듈이 노트북 셀에 인라인돼 있고 표준
라이브러리만 씁니다.

### 3. 파라미터 채우고 실행

```python
KUSTO_URI = "https://trd-xxxxxxx.z9.kusto.fabric.microsoft.com"
KUSTO_DATABASE = "fdc"
MES_API_KEY = "..."
```

전체 실행하면 첫 실행에서 **MES 공정이력이 시작하는 시각부터 지금까지**를 백필하고
테이블 2개를 만듭니다. 배포 직후라면 공정이력 구간 65시간이 거의 전부라 10만 행
안팎이고, 배포 후 며칠이 지났다면 그 사이가 유휴로 채워져 하루 약 4,608행씩 더
붙습니다. 이후 실행은 마지막으로 적재한 시각부터 지금까지만 채웁니다.

> **`MES_API_KEY` 는 노트북 셀에 남습니다.** Fabric 노트북은 자동 저장되고 작업
> 영역은 공유될 수 있습니다. 스케줄을 걸어 계속 돌릴 것이 아니라면 실행 후 이 줄을
> 비우고 저장하세요. 스케줄을 건다면 작업 영역 접근 권한을 확인하세요.

### 4. 스케줄 걸기

> **3단계 백필이 끝난 것을 확인하고 거세요.** 스케줄을 먼저 켜면 백필이 끝나기 전에
> 다음 실행이 시작될 수 있고, 두 실행이 같은 watermark를 읽어 **같은 10만 행을 두 번**
> 씁니다. Eventhouse에는 유니크 제약이 없어서 조용히 2배가 됩니다. 아래 "겹치면
> 중복이 쌓입니다"를 읽어 주세요.

**Run > Schedule** 에서 분 단위 반복을 켭니다. 최소 주기는 1분입니다.

| 주기 | 하루 Spark 세션 | 데이터 해상도 |
|---|---|---|
| **15분 (권장)** | 96회 | 가동 30초 · 유휴 5분 |
| 3분 | 480회 | 가동 30초 · 유휴 5분 |

**주기와 해상도는 별개입니다.** 매 실행이 watermark부터 지금까지의 격자를 통째로
채우므로, 15분 주기로 돌려도 해상도는 그대로입니다. 3분으로 돌린다고 더 촘촘해지지
않습니다. 비용만 5배입니다.

15분을 권하는 이유는 비용보다 **겹칠 여지를 줄이기 위해서**입니다. Microsoft Learn은
starter pool 세션 기동만으로 상황에 따라
[2~5분](https://learn.microsoft.com/fabric/data-engineering/spark-compute#starter-pools)이
걸릴 수 있다고 적고 있습니다. 3분 주기는 실행이 주기보다 길어지는 구간이 생깁니다.

MES 공정이력이 끝난 뒤 시각은 전부 유휴로 채워집니다. 5분 간격 2종이라 며칠이
밀려도 수만 행에 그칩니다. 그래서 생성 구간에 상한을 두지 않습니다. 잘라내면
watermark가 잘린 지점이 아니라 현재 시각으로 가버려서 건너뛴 구간을 다시는
채우지 못합니다.

#### 겹치면 중복이 쌓입니다

이 노트북은 **읽고 → 만들고 → 쓰는** 구조입니다.

```
max(reading_ts) 읽기  ─┐
                       │  이 사이에 다른 실행이 끼어들면 둘 다 같은 구간을 만듭니다
readings 쓰기         ─┘
```

Kusto에는 유니크 제약도, 우리가 쓸 CAS나 잠금도 없습니다. `writeMode=Transactional`은
**각각의** 쓰기를 원자적으로 만들 뿐, 두 쓰기가 모두 안착하는 것을 막지 않습니다.
노트북의 검사 9개도 전부 통과합니다 — 각 실행은 **자기 자신과는 일관되기** 때문입니다.

Fabric이 이걸 대신 막아 주지 않습니다. Microsoft Learn은 같은 노트북의 이전 예약
실행이 아직 도는 중일 때 다음 실행을 병렬 실행할지, 기다릴지, 건너뛸지를 **명시하지
않습니다.** Spark의
[job queueing](https://learn.microsoft.com/fabric/data-engineering/spark-job-concurrency-and-queueing)은
Spark vCore가 모자랄 때 적용되는 대기열이지 중복 실행 방지 장치가 아닙니다. 용량이
넉넉하면 두 job이 함께 admission될 수 있습니다.

**겹치는 경로는 세 가지입니다.**

| 경로 | 막는 법 |
|---|---|
| 백필이 끝나기 전에 스케줄이 켜져 있다 | 3단계를 끝내고 4단계로 |
| 실행이 주기보다 길다 | 15분 주기 |
| 멈춘 줄 알고 "Run all"을 다시 누른다 | 쓰기 전에 안내 문구가 출력됩니다. 기다리세요 |

세 번째가 실제로 가장 흔합니다. Transactional 쓰기는 임시 테이블 → 폴링 → extent
이동 순서라 몇 분 동안 아무 출력이 없습니다.

**중복을 확인하는 법**

```kusto
fdc_sensor_reading
| summarize n = count() by reading_ts, eqp_id, sensor_code
| where n > 1
| count
```

0이면 깨끗합니다. 0이 아니면 두 테이블을 모두 지우고 다시 실행하세요.

```kusto
.drop table fdc_sensor_spec
.drop table fdc_sensor_reading
```

`fdc_sensor_spec`만 지우면 판독 테이블은 계속 2배인 채로 남습니다. 노트북이 스펙
행 수로 멈춰 섰을 때도 마찬가지라, 그 에러 메시지에 같은 확인 쿼리를 넣어 뒀습니다.

## 재실행해도 안전한 이유

판독값은 **고정된 MES 스냅샷에 대해 (설비, 센서, 타임스탬프)만의 순수 함수**입니다.
실행 시각도, 순차 상태도, 호출 순서도 값에 영향을 주지 않습니다.

"고정된 MES 스냅샷에 대해"라는 단서가 붙는 이유는 설비의 이탈 진폭이 그 설비의
MES 불량률에서 나오기 때문입니다. 불량률이 높은 설비일수록 크게 이탈한다는 것이
이 데이터셋의 설계 목표라, MES 실적은 없앨 수 없는 입력입니다. 다만 진폭은 **그
설비 자신의 불량률**만 씁니다. 전체 설비의 최소·최대로 정규화하면 어느 설비
실적 하나가 바뀔 때 나머지 일곱 대의 값이 전부 흔들립니다. mock MES 는 쓰기
도구를 열어 두고 20명이 한 인스턴스를 공유하므로, 그렇게 두면 한 사람이 공정
실적을 등록했을 뿐인데 뒤이어 백필한 사람의 과거 판독값이 앞사람과 달라집니다.

즉 `register_process_result` 로 `EQP-CMP01` 실적을 등록하면 그 뒤로 생성되는
`EQP-CMP01` 판독값의 진폭은 달라지고, 나머지 일곱 대는 그대로입니다.

**실습 중 MES 에 쓰기를 하면 두 가지가 생깁니다.**

하나는 시계열의 계단입니다. 쓰기 시점 이전은 옛 진폭, 이후는 새 진폭으로 생성돼
경계에서 값이 튑니다.

다른 하나는 더 조용합니다. 등록한 실적의 `in_time`/`out_time` 이 **이미 적재한
구간 안**이면, 그 런의 `Run` 판독값은 **영영 생성되지 않습니다.** 노트북은
watermark 다음부터만 만들기 때문입니다. MES 는 "그 로트가 그 설비에서 돌았다" 고
말하는데 FDC 에는 그 시각에 `Idle` 밖에 없어서, 교차 질의가 빈 결과를 냅니다.
에러는 나지 않습니다.

둘 다 설비 단위로는 고칠 수 없습니다. `START` 는 전역 watermark 하나에서 나오고
생성은 모든 설비를 한 번에 돕니다. 유일한 복구는 아래 두 줄로 전부 지우고 다시
실행하는 것입니다.

```kusto
.drop table fdc_sensor_spec
.drop table fdc_sensor_reading
```

실습 중 MES 쓰기를 시연할 계획이라면, **FDC 백필보다 먼저** 하거나 시연 뒤에 위
두 줄로 다시 만드세요.

시드에 파이썬 내장 `hash()` 를 쓰지 않습니다. `hash()` 는 문자열에 대해 프로세스마다
다른 값을 냅니다.

```
$ python3 -c 'print(hash("EQP-CMP01"))'   # 6352192405693204750
$ python3 -c 'print(hash("EQP-CMP01"))'   # 4561593927473588131
```

노트북은 3분마다 **새 프로세스**로 실행되므로 `hash()` 를 쓰면 백필한 구간과 라이브로
적재한 구간의 값이 어긋나 시계열에 계단이 생깁니다. 대신 `hashlib.sha256` 기반의
고정 시드를 씁니다. 테스트가 별도 프로세스 3개에서 같은 값이 나오는지 확인합니다.

격자도 epoch에 고정돼 있어 잡이 몇 초 늦게 떠도 같은 타임스탬프가 나옵니다.

## 적재 전 검증

Eventhouse는 append-only입니다. 잘못 쓴 행을 지우려면 익스텐트 단위로 지워야 하고
같은 익스텐트의 정상 행까지 날아갑니다. 그래서 쓰기 **전에** 검사하고 치명 항목이
걸리면 아무것도 쓰지 않습니다.

| # | 항목 | 치명 |
|---|---|---|
| 1 | 로트 관련 컬럼이 없다 | 예 |
| 2 | 모든 `eqp_id` 가 MES 설비 목록에 있다 | 예 |
| 3 | 모든 `sensor_code` 가 센서 스펙에 있다 | 예 |
| 4 | `reading_ts` 가 30초 격자에 정렬돼 있다 | 예 |
| 5 | 생성 구간이 watermark보다 뒤에 있다 | 예 |
| 6 | `status` 가 센서 한계와 일치한다 | 아니오 |
| 7 | `Alarm` 비율이 0 초과 5% 미만이다 | 아니오 |
| 8 | 불량률 상위 설비의 이상이 하위보다 많다 | 아니오 |
| 9 | `run_status` 가 `Run`/`Idle` 뿐이고 유휴에 공정 센서가 없다 | 예 |

6~8번은 데이터가 틀린 게 아니라 실습 소재로서 쓸모가 떨어지는 경우라 적재를 막지
않습니다.

## 알려진 한계

**데이터는 나이를 먹습니다.** MES 시드는 배포 시점을 기준으로 공정이력 시각을
고정합니다. 배포 후 며칠이 지나면 가동 구간은 그만큼 과거가 되고 그 사이는 전부
유휴로 채워집니다. 실습 자체는 그대로 되지만 "지난 24시간" 같은 상대 시간 질의는
빈 결과를 냅니다. **코호트마다 MES를 다시 배포하세요.**

**MES를 다시 배포하면 Eventhouse도 비우세요.** 재배포하면 공정이력 시각이 새로
잡힙니다. 그런데 이 노트북은 이미 적재한 마지막 시각(watermark) *다음*부터만
만들기 때문에, 새 가동 구간이 기존 watermark보다 과거에 놓이면 그 구간은 영구히
빈 채로 남고 화면에는 유휴만 보입니다.

```kusto
.drop table fdc_sensor_reading
.drop table fdc_sensor_spec
```

두 테이블을 지운 뒤 노트북을 한 번 돌리면 새 구간이 처음부터 다시 채워집니다.

`ago(24h)` 로 거르기 전에 실제 구간을 먼저 확인하는 습관이 필요합니다.

```kusto
fdc_sensor_reading
| summarize 시작 = min(reading_ts), 종료 = max(reading_ts), 행 = count() by run_status
```

**실행이 겹치면 조용히 2배가 됩니다.** 이 노트북은 읽고 → 만들고 → 쓰는 구조인데
Kusto에는 유니크 제약도, 우리가 쓸 잠금도 없습니다. 두 실행이 같은 watermark를
읽으면 같은 행을 두 번 씁니다. 노트북의 검사 9개는 전부 통과합니다 — 각 실행은
자기 자신과는 일관되기 때문입니다. Fabric이 예약 실행을 직렬화한다는 보장도
문서에 없습니다. 막는 법과 확인 쿼리는 위 "겹치면 중복이 쌓입니다"에 있습니다.

**METRO(계측) 공정에는 FDC 데이터가 없습니다.** 설비를 배정받지 않기 때문입니다.
위 "3단 교차 질의" 절을 보세요.

**빈 Eventhouse 를 상대하는 두 쿼리는 실제 Kusto 로만 최종 검증됩니다.**
`watermark_query()` 와 `spec_count_query()` 는 테이블이 아직 없을 때도 예외 대신
결과를 돌려줘야 합니다. 그래야 '첫 실행' 과 '조회 실패' 를 구분할 수 있습니다.
`union isfuzzy=true` 만으로는 부족한데, isfuzzy 는 여러 레그 중 일부가 없을 때만
무시하고 [공식 문서](https://learn.microsoft.com/kusto/query/union-operator)가
*"If no resolutions were successful, the query returns an error"* 라고 명시하기
때문입니다. 그래서 항상 해석되는 빈 `datatable` 레그를 하나 붙여 뒀습니다.

이 동작은 단위 테스트로 재현할 수 없습니다. 테스트도 노트북 하네스도 Kusto 를
스텁하므로 같은 가정을 되풀이할 뿐입니다. **두 쿼리를 바꾸게 되면 테이블이 없는
빈 데이터베이스에 대고 KQL 쿼리셋에서 직접 실행해 확인하세요.**

이 가정이 신경 쓰인다면 **위 1단계에서 테이블을 미리 만들어 두세요.** 그러면
"테이블 없음" 상태 자체가 사라져서 이 경로를 아예 타지 않습니다. 20명이 한꺼번에
도는 워크숍이라면 붙여넣기 한 번으로 검증 불가능한 가정 하나를 없애는 셈입니다.
**설비 상태는 `Run` / `Idle` 둘뿐입니다.** SEMI E10 은 여섯 가지를 정의하지만
예방정비(PM)나 고장 정지는 모델에 없습니다. 확산로 배치 공정(여러 로트 동시 투입)도
다루지 않습니다. 한 시각에 설비 하나는 런 하나만 처리합니다.

## 테이블은 누가 만드나

Spark 커넥터의 `CreateIfNotExist` 가 만듭니다. 컬럼 타입은 `spark_schema()` 가
데이터프레임에 명시해 고정합니다. 타입을 추론에 맡기면 한 배치의 판독값이 우연히 모두
정수인 순간 `long` 컬럼이 만들어지고, 이후 실수 적재가 **오류 없이 잘립니다.**

노트북은 같은 스키마의 KQL DDL과 30일 보존 정책 명령을 **출력만** 합니다. Spark
커넥터는 데이터 평면 전용이라 `.create-merge` 같은 제어 명령을 보낼 수 없고, 보내려면
`azure-kusto-data` 를 따로 설치해야 해서 실습자마다 설치 단계가 하나 늘기 때문입니다.
스키마를 직접 보거나 다른 작업 영역으로 옮길 때 KQL 쿼리셋에 붙여넣으세요.

```kusto
.alter-merge table fdc_sensor_reading policy retention
    softdelete = 30d recoverability = disabled
```

## 개발

```bash
cd customizing/fabric/fdc-eventhouse
python3 -m pytest -q          # 전체 테스트
python3 build_notebook.py     # 노트북 재생성
```

`src/` 를 고쳤으면 반드시 `build_notebook.py` 를 다시 돌리세요. 노트북은 생성물이고
직접 고치면 다음 빌드에서 덮어써집니다.

깜빡해도 테스트가 잡습니다. `test_committed_notebook_matches_a_fresh_build` 가
커밋된 `.ipynb` 를 지금 `src/` 로 빌드한 것과 바이트까지 비교합니다. 이게 실패하면
답은 하나입니다 — `python3 build_notebook.py`.

셀 `id` 는 역할에서 나옵니다(`fdc-watermark`, `fdc-module-fdc-runs` …). 그래서
내용이 그대로면 몇 번을 다시 빌드해도 `.ipynb` 가 바뀌지 않고, `git diff` 에는 실제로
고친 셀만 뜹니다.

### 구조

```
src/mes_probe.py      MES MCP 프로브. 설비 목록과 불량 실적만 가져온다
src/fdc_runs.py       MES 공정이력 → 설비별 런 구간(가동/유휴 판정)
src/fdc_sensors.py    설비 유형 7종 x 센서 6종 = 42종 정의
src/fdc_anomaly.py    MES 불량 실적 → 이상 주입 대상과 진폭
src/fdc_generator.py  순수 함수 판독값 생성
src/fdc_schema.py     KQL DDL, Spark 스키마, 행 변환
src/fdc_validate.py   적재 전 9항목 검증
build_notebook.py     위 모듈을 인라인 전개해 노트북 조립
```

`src/` 모듈은 **표준 라이브러리만** import합니다. 노트북에 그대로 인라인되므로 외부
패키지를 쓰면 참가자가 설치해야 합니다.
