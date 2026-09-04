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
어떤 로트가 그 설비에 있었나"를 알려면 MES에 물어야 합니다. 이 경계가 이 실습의
전부입니다. 편의를 위해 `lot_id` 를 판독값에 넣는 순간 교차 질의를 할 이유가 사라집니다.

테스트가 이 경계를 강제합니다(`test_rows_carry_no_lot_columns`, 검증 1번 항목).

## 시간축을 쓰지 않는 이유

MES `process_results` 91건은 **전부 `in_time == out_time`** 이고, 서로 다른 값이 두 종류
(`06:53:09`, `06:53:10`)뿐입니다. 공정 구간이 존재하지 않으므로 "이 로트가 설비에 올라가
있던 시간대"를 시간 범위로 조인할 수 없습니다. Mock MES를 다시 시드하면 통째로 바뀌므로
시간 기반 설계는 애초에 취약합니다.

그래서 세 시스템을 잇는 다리는 **`eqp_id`** 입니다. 시간은 Eventhouse 안에서의 시계열
분석에만 씁니다.

## 이상은 MES에서 유도합니다

"고장 설비 목록"을 하드코딩하지 않습니다. Mock MES가 바뀌면 이상 패턴도 따라 바뀝니다.

1. `process_results` 에서 설비별 불량률을 집계합니다(`eqp_id` 가 빈 7건은 제외).
2. 불량률이 높은 설비일수록 이탈 진폭을 키웁니다.
3. 불량코드가 물리적으로 지목하는 센서에만 이탈을 싣습니다.

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

결과적으로 24시간 기준 분포는 다음과 같습니다.

| 상태 | 비율 |
|---|---|
| Normal | 94.3% |
| Warning | 5.4% |
| Alarm | 0.3% |

경보 454건 중 84%가 불량률 1위 `EQP-CMP01`(87.5%)에 몰립니다. 하위권인 `EQP-ETCH01`,
`EQP-PHOT01` 은 경보가 0건이고 `EQP-IMPL01` 은 2건입니다. 경고 비율도 불량률 순서를
거의 그대로 따라갑니다. Eventhouse에서 찾아낸 이상 설비가 MES 실적과 맞아떨어집니다.

## 실행 방법

### 1. Eventhouse 준비

작업 영역에서 **Eventhouse** 를 만들고 그 안의 KQL 데이터베이스 페이지 오른쪽 위에서
**Query URI** 를 복사합니다.

```
https://trd-xxxxxxx.z9.kusto.fabric.microsoft.com
```

Query URI는 비밀값이 아닙니다. 인증은 `mssparkutils.credentials.getToken()` 이 실행자
신원으로 토큰을 발급해 처리하므로 **복사해 둘 키가 없습니다.**

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

전체 실행하면 첫 실행에서 지난 24시간을 백필하고 테이블 2개를 만듭니다.

### 4. 스케줄 걸기

**Run > Schedule** 에서 분 단위 반복을 켭니다.

| 주기 | 하루 Spark 세션 | 데이터 해상도 |
|---|---|---|
| 3분 | 480회 | 30초 |
| 15분 | 96회 | 30초 |

**주기와 해상도는 별개입니다.** 매 실행이 watermark부터 지금까지의 30초 격자를 통째로
채우므로, 15분 주기로 돌려도 해상도는 그대로 30초입니다. 용량이 걱정되면 15분을
권합니다. 비용이 1/5이고 데이터는 같습니다.

## 재실행해도 안전한 이유

판독값은 **(설비, 센서, 타임스탬프)만의 순수 함수**입니다. 실행 시각도, 순차 상태도,
호출 순서도 값에 영향을 주지 않습니다.

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

6~8번은 데이터가 틀린 게 아니라 실습 소재로서 쓸모가 떨어지는 경우라 적재를 막지
않습니다.

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
python3 -m pytest -q          # 252개
python3 build_notebook.py     # 노트북 재생성
```

`src/` 를 고쳤으면 반드시 `build_notebook.py` 를 다시 돌리세요. 노트북은 생성물이고
직접 고치면 다음 빌드에서 덮어써집니다.

### 구조

```
src/mes_probe.py      MES MCP 프로브. 설비 목록과 불량 실적만 가져온다
src/fdc_sensors.py    설비 유형 7종 x 센서 6종 = 42종 정의
src/fdc_anomaly.py    MES 불량 실적 → 이상 주입 대상과 진폭
src/fdc_generator.py  순수 함수 판독값 생성
src/fdc_schema.py     KQL DDL, Spark 스키마, 행 변환
src/fdc_validate.py   적재 전 8항목 검증
build_notebook.py     위 모듈을 인라인 전개해 노트북 조립
```

`src/` 모듈은 **표준 라이브러리만** import합니다. 노트북에 그대로 인라인되므로 외부
패키지를 쓰면 참가자가 설치해야 합니다.
