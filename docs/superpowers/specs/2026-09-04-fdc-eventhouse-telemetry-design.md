# FDC Eventhouse 설비 센서 텔레메트리 설계

작성일: 2026-09-04
상태: 검토 대기

## 1. 목적

Mock MES(생산)와 QMS 레이크하우스(품질)에 이어 **세 번째 시스템**으로 설비 센서
데이터를 Microsoft Fabric Eventhouse(KQL DB)에 적재한다. 반도체 현장에서 이 영역을
FDC(Fault Detection and Classification)라 부르므로 그 이름을 쓴다.

Fabric 노트북이 일정 주기로 실행되어 센서 판독값을 Eventhouse에 append 한다.
참가자는 KQL로 시계열을 분석하고, `eqp_id`를 다리 삼아 MES·QMS로 건너간다.

최종 그림에서 세 시스템은 서로 다른 솔루션으로 남는다.

| 시스템 | 저장소 | 에이전트 연결 |
|---|---|---|
| MES | 외부 Mock 서버 | MCP 툴 |
| QMS | Fabric 레이크하우스 | Fabric Data Agent |
| FDC | Fabric Eventhouse (KQL DB) | KQL 질의 / Data Agent |

## 2. 대상과 제약

개인별 핸즈온 자료다. 참가자 약 20명이 **각자 자기 Fabric 작업 영역**을 만들고 같은
자료로 실습한다. 여기서 따라오는 제약이 설계를 지배한다.

- **Eventhouse 접속에 비밀값이 없어야 한다.** 20명이 SAS 키를 붙여넣는 순간 사고가 난다.
- **수동 DDL이 없어야 한다.** KQL 테이블 생성 스크립트를 따로 실행시키지 않는다.
- **몇 번을 다시 돌려도 같아야 한다.** 실습 중 재실행은 반드시 일어난다.
- **자료가 자기 완결적이어야 한다.** `pip install`, jar 업로드, 파일 업로드가 없어야 한다.

MES API 키는 예외다. 기존 `qms-lakehouse` 노트북이 이미 같은 키를 요구하므로 참가자는
이미 그것을 손에 쥐고 있다. 새로 늘리는 비밀값이 없다는 뜻이지, 하나도 없다는 뜻이 아니다.

## 3. MES 실측 제약 — 시간축을 쓸 수 없다

설계에 앞서 Mock MES를 실측했다. 결정적인 사실이 나왔다.

| 항목 | 실측값 |
|---|---|
| `process_results` 행수 | 91 |
| `in_time == out_time` 인 행 | 91 (전부) |
| 서로 다른 `in_time` 값 | 2종 (`06:53:09`, `06:53:10`) |

**MES에는 공정 구간(window)이 없다.** 91건의 공정이력이 2초 안에 몰려 있고 시작·종료
시각이 동일하다. 이 타임스탬프는 공정 시각이 아니라 Mock 서버의 시드 시각이며, 컨테이너가
재시드되면 통째로 바뀐다.

따라서 **"센서 판독 시각이 로트의 공정 구간에 들어가는가"라는 시간 범위 조인은 성립할 수
없다.** 이를 억지로 만들려면 MES에 없는 구간을 QMS와 FDC 양쪽에서 똑같이 날조해야 하고,
Mock MES가 재시드되는 순간 전부 깨진다.

### 3.1 결론: `eqp_id`를 다리로 쓴다

시간축 대신 **설비 식별자**로 세 시스템을 잇는다.

```
FDC (KQL)                MES (MCP)                    QMS (Data Agent)
eqp_id, reading_ts  ──▶  process_results.eqp_id  ──▶  qms_inspection.lot_id
센서 이상 탐지            그 설비가 처리한 로트          그 로트의 검사·부적합
```

시간은 Eventhouse **내부**에서 시계열 분석용으로 쓴다. 그것이 KQL의 본령이고,
시스템 간 조인은 비즈니스 키가 맡는다. 이 분리 덕분에 Mock MES가 재시드돼도
설계가 살아남는다.

## 4. 설계 원칙

### 4.1 무중복 원칙 승계

QMS 설계의 계약을 그대로 잇는다. 같은 사실을 두 시스템이 중복 보유하지 않는다.

FDC 테이블에 다음 컬럼은 **존재하지 않는다.**

`lot_id`, `product_code`, `wafer_qty`, `defect_code`, `judgment`, `result`, `operator`

설비 센서는 지금 어떤 로트가 올라와 있는지 모른다. 알고 싶으면 MES에 물어야 한다.
"온도가 튄 그 설비에서 뭘 만들고 있었나"가 곧 교차 질의이며, 이 자료의 핵심 학습 목표다.

`step_code`와 `eqp_type`은 보유한다. 이 둘은 설비 자체의 속성이지 생산 사실이 아니다.

### 4.2 판독값은 순수 함수다

모든 판독값은 `(eqp_id, sensor_code, reading_ts)` 만의 함수다. 순차 상태가 없다.

```
value = f(eqp_id, sensor_code, reading_ts)
```

같은 타임스탬프는 몇 번을 계산해도 같은 값이 나온다. 여기서 두 가지가 따라온다.

- **백필과 라이브 추가가 일치한다.** 생성 경로가 갈라져도 값이 어긋나지 않는다.
- **중복 적재가 무해하다.** 재실행으로 같은 구간이 두 번 써져도 값이 동일하다.

이것이 append-only 스트림에서 멱등성을 확보하는 방식이다.

### 4.3 난수 시드는 프로세스 간 안정적이어야 한다

파이썬 내장 `hash()`는 문자열에 대해 **프로세스마다 다른 값**을 낸다(`PYTHONHASHSEED`
무작위화). 실측으로 확인했다.

```
$ python3 -c 'print(hash("EQP-CMP01"))'   # 6352192405693204750
$ python3 -c 'print(hash("EQP-CMP01"))'   # 4561593927473588131
```

노트북은 3분마다 **새 프로세스**에서 실행된다. 시드에 `hash()`를 쓰면 같은 타임스탬프가
실행마다 다른 값을 내고 §4.2가 통째로 무너진다. 백필과 라이브 추가의 값이 어긋나
시계열에 계단이 생긴다.

따라서 시드는 표준 라이브러리의 결정적 해시로 만든다.

처음에는 `zlib.crc32`를 썼으나 구현 중 문제가 드러났다. crc32는 GF(2) 위의 **선형**
함수라 입력이 몇 비트만 달라지면 출력 비트가 함께 움직인다. 실제 설비·센서 48개
조합에서 이탈 방향(`seed(...) % 2`)을 뽑아 보니 설비명만 바뀐 조합끼리 부호가 뭉쳐
`EQP-CMP01`/`EQP-DIFF01`/`EQP-CVD01`/`EQP-ETCH01` 네 대가 같은 센서에서 모두 같은
방향으로 이탈했다. 전 설비가 한쪽으로 드리프트하면 시연이 인위적으로 보인다.

`hashlib.sha256`은 비선형이라 이런 뭉침이 없다(48조합 중 양의 방향 46%). 여기서
sha256은 보안 용도가 아니라 **결정적 혼합기**로만 쓴다.

```python
def seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")
```

검증값: `seed("EQP-CMP01", "AMBIENT_TEMP", 100) == 2450050198`. 테스트가 이 값을
별도 프로세스 3개에서 확인해 `hash()` 회귀를 막는다.

`random.Random(seed(...))`만 쓰고, 모듈 전역 `random.*` 함수는 쓰지 않는다.

## 5. 데이터 모델

KQL DB에 테이블 2개를 둔다. 둘 다 노트북이 자동 생성한다.

### 5.1 `fdc_sensor_spec` — 42행

센서 마스터다. 설비 유형마다 어떤 센서가 달렸고 정상 범위가 얼마인지 정의한다.
작고 정적이므로 판독 테이블과 같은 KQL DB에 둔다. 판독값 행마다 한계치를 복사해 넣지
않고 이 테이블과 조인하게 한다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `sensor_code` | string | 예 `CHAMBER_TEMP` |
| `sensor_name_ko` | string | 예 "챔버 온도" |
| `eqp_type` | string | `Furnace`/`Scanner`/`Etcher`/`Implanter`/`CVD`/`Polisher`/`Prober` |
| `unit` | string | 예 `degC`, `%`, `W`, `mTorr`, `sccm`, `uA`, `kPa`, `A`, `nm`, `nm/min`, `ppm`, `mohm`, `degC/min`, `cnt` |
| `normal_min` | real | 정상 운전 하한 |
| `normal_max` | real | 정상 운전 상한 |
| `alarm_min` | real | 경보 하한 |
| `alarm_max` | real | 경보 상한 |
| `sample_interval_sec` | int | 30 |
| `is_active` | bool | 사용 여부 |

기본 키는 (`eqp_type`, `sensor_code`) 조합이다.

### 5.2 `fdc_sensor_reading` — 계속 증가

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `reading_ts` | datetime | UTC. 30초 격자에 정렬 |
| `eqp_id` | string `MES` | 세 시스템을 잇는 다리 |
| `eqp_type` | string `MES` | 설비 유형 |
| `step_code` | string `MES` | 설비가 속한 공정 |
| `sensor_code` | string | `fdc_sensor_spec` 참조 |
| `value` | real | 판독값 |
| `unit` | string | 단위 |
| `status` | string | `Normal`/`Warning`/`Alarm` |

`status` 산출 규칙은 다음과 같다.

- `alarm_min..alarm_max` 밖 → `Alarm`
- `normal_min..normal_max` 밖 → `Warning`
- 그 외 → `Normal`

## 6. 센서 정의

설비 유형 7종 × 센서 6종 = 42행. 모든 유형이 **공통 센서 2종**(주변 온도·습도)을 갖는다.
설비 온·습도를 전 설비 공통 시계열로 확보해 `make-series` 비교 실습이 가능하게 한다.

| eqp_type | step | 공통 2종 | 고유 4종 |
|---|---|---|---|
| Furnace | DIFF | AMBIENT_TEMP, AMBIENT_HUMIDITY | CHAMBER_TEMP, RAMP_RATE, O2_CONC, N2_FLOW |
| Scanner | PHOTO | 〃 | STAGE_TEMP, FOCUS_OFFSET, ILLUM_DOSE, RETICLE_TEMP |
| Etcher | ETCH | 〃 | RF_POWER, CHAMBER_PRESSURE, GAS_FLOW, CHAMBER_TEMP |
| Implanter | IMPL | 〃 | BEAM_CURRENT, BEAM_ENERGY, VACUUM, SRC_TEMP |
| CVD | CVD | 〃 | CHAMBER_TEMP, CHAMBER_PRESSURE, PRECURSOR_FLOW, DEP_RATE |
| Polisher | CMP | 〃 | PAD_PRESSURE, SLURRY_FLOW, MOTOR_CURRENT, PAD_TEMP |
| Prober | TEST | 〃 | CHUCK_TEMP, CONTACT_RES, PROBE_FORCE, TOUCHDOWN_CNT |

설비 목록은 하드코딩하지 않고 MES `process_results`의 `eqp_id` 고유값에서 유도한다.
기존 `derive_equipment()`와 같은 방식이다. 실측 기준 설비는 8대이며 Scanner가 2대다.

### 6.1 신호 합성

```
value = base
      + diurnal_amplitude * sin(2π * seconds_of_day / 86400)
      + gaussian_noise(sigma)
      + excursion_offset(eqp_id, sensor_code, reading_ts)
```

`base`와 `diurnal_amplitude`, `sigma`는 센서 정의에 함께 둔다. 일주기 성분을 넣는 이유는
24시간 백필 후 KQL 차트에서 눈에 보이는 패턴이 나오게 하기 위해서다.

## 7. 이상 주입 — MES에서 유도한다

하드코딩한 "고장 설비 목록"을 두지 않는다. MES 실적에서 유도해, Mock MES가 바뀌면
이상 패턴도 따라 바뀌게 한다.

### 7.1 유도 절차

1. MES `process_results`에서 설비별 불량률을 집계한다.
2. 불량률에 비례해 그 설비의 **이상 구간 발생 확률**을 정한다.
3. 그 설비의 지배적 불량코드를 물리적으로 설명하는 센서를 고른다.

실측 불량률은 다음과 같다.

| eqp_id | 실행 | 불량 | 불량률 | 지배 불량코드 |
|---|---|---|---|---|
| EQP-CMP01 | 8 | 7 | 87.5% | Overlay(3) |
| EQP-CVD01 | 11 | 5 | 45.5% | Etch-Residue(4) |
| EQP-DIFF01 | 16 | 7 | 43.8% | Particle(3), Scratch(3) |
| EQP-PHOT02 | 5 | 2 | 40.0% | Overlay(1), Contamination(1) |
| EQP-TEST01 | 6 | 2 | 33.3% | Particle(1), Contamination(1) |
| EQP-ETCH01 | 15 | 4 | 26.7% | Particle(3) |
| EQP-PHOT01 | 11 | 2 | 18.2% | Overlay(1), Scratch(1) |
| EQP-IMPL01 | 12 | 2 | 16.7% | Overlay(1), Scratch(1) |

`process_results` 중 7건은 `eqp_id`가 `null`이다. 설비 유도와 불량률 집계에서 모두 제외한다.

### 7.2 불량코드 → 센서 매핑

물리적 인과가 성립하는 것만 넣는다.

| MES 불량코드 | 후보 센서 | 근거 |
|---|---|---|
| `Particle` | CHAMBER_TEMP, AMBIENT_HUMIDITY | 온도 급변 시 챔버 박리물 발생 |
| `Scratch` | PAD_PRESSURE, MOTOR_CURRENT, PROBE_FORCE | 기계적 접촉 과다 |
| `Overlay` | AMBIENT_TEMP, STAGE_TEMP, RETICLE_TEMP | 열팽창에 의한 정렬 오차 |
| `Etch-Residue` | GAS_FLOW, PRECURSOR_FLOW, RF_POWER | 반응 가스 부족 |
| `Contamination` | AMBIENT_HUMIDITY, VACUUM | 습도 상승·진공도 저하 |
| `CD-OOS` | FOCUS_OFFSET, RF_POWER, ILLUM_DOSE | 노광·식각 조건 이탈 |

후보 센서 중 **그 설비 유형에 실제로 존재하는 것**만 대상이 된다. 교집합이 비면 공통
센서(AMBIENT_TEMP)로 대체한다.

### 7.3 이상 구간 생성

시간을 30분 버킷으로 나눈다. 버킷 인덱스는 epoch 기준 고정이라 어느 실행에서 계산해도
같다.

```
bucket = floor(epoch_seconds / 1800)
rng    = Random(hash((eqp_id, bucket)))
```

이 `rng`로 해당 버킷의 이상 발생 여부, 대상 센서, 이상 형태(드리프트/스파이크), 진폭을
정한다. 판독값 계산은 자기 버킷만 보므로 순차 상태가 없고 §4.2의 순수 함수 조건을 지킨다.

이상 진폭은 `Warning`에 머무는 경우와 `Alarm`까지 가는 경우를 나눈다. 불량률이 높은
설비일수록 `Alarm` 비중이 커진다.

## 8. 적재 경로

Fabric 공식 샘플(`NYC_GreenTaxi_KQL_notebook.ipynb`)이 쓰는 Spark 커넥터를 그대로 쓴다.

```python
df.write.format("com.microsoft.kusto.spark.synapse.datasource") \
    .option("kustoCluster", KUSTO_URI) \
    .option("kustoDatabase", KQL_DATABASE) \
    .option("kustoTable", table) \
    .option("accessToken", notebookutils.credentials.getToken(KUSTO_URI)) \
    .option("tableCreateOptions", "CreateIfNotExist") \
    .mode("Append").save()
```

이 경로를 고른 이유는 §2의 제약을 전부 만족하기 때문이다.

- `getToken()`이 실행 주체의 신원으로 토큰을 발급한다 → **복사할 비밀값이 없다.**
- `CreateIfNotExist`가 DataFrame 스키마로 테이블을 만든다 → **수동 DDL이 없다.**
- Fabric 런타임에 커넥터가 이미 있다 → **jar·pip 설치가 없다.**
- Fabric 공식 튜토리얼 샘플과 동일한 API → 참가자가 문서를 찾아볼 수 있다.

참가자가 노트북에 넣는 값은 KQL 데이터베이스 상세 카드의 **Query URI 문자열 하나**다.
비밀값이 아니므로 채팅이나 슬라이드로 공유해도 안전하다.

### 8.1 검토했으나 채택하지 않은 대안

**Eventstream Custom endpoint.** Eventstream에 custom endpoint를 만들면 Event Hub /
AMQP / Kafka 연결 문자열과 SAS 키가 나오고 외부에서 이벤트를 보낼 수 있다. 생산자 →
스트림 → Eventhouse라는 구조가 현실에 더 가깝다. 그러나 참가자마다 **SAS 키를 복사**해야
하고 `azure-eventhub` 설치가 필요하다. §2의 첫 두 제약을 정면으로 위반한다. README에
"실제 외부 장비를 붙일 때의 경로"로 설명만 남기고 구현하지 않는다.

**Kusto 스트리밍 수집 REST API.** `ingest-` 엔드포인트에 직접 POST 한다. Entra 토큰을
직접 다뤄야 하고, 클러스터에 스트리밍 수집이 켜져 있지 않으면
`NotFound_StreamingIngestionDisabledForCluster`로 실패한다. 핸즈온에서 진단하기 어려운
실패 모드다.

## 9. 실행 모드와 멱등성

노트북은 단일 진입점이며 모드를 자동 판별한다.

```
watermark = KQL: fdc_sensor_reading | summarize max(reading_ts)
```

| 상황 | 동작 |
|---|---|
| 테이블 없음 / 조회 실패 | 첫 실행. `BACKFILL_HOURS`(기본 24) 만큼 백필 |
| watermark 가 `MAX_CATCHUP_HOURS`(기본 6)보다 오래됨 | 6시간으로 잘라 적재 |
| 그 외 | watermark ~ now 구간만 적재 |

테이블이 없을 때 읽기는 예외를 던진다. 이를 첫 실행 신호로 해석한다.

`fdc_sensor_spec`은 비어 있을 때만 쓴다. Kusto 커넥터의 `Overwrite` 동작에 의존하지
않기 위해서다.

생성 구간의 경계는 30초 격자에 맞춰 내림한다. watermark 자체는 제외하고 그 다음
격자부터 생성해 경계 중복을 없앤다.

## 10. 스케줄과 용량

Fabric 노트북 스케줄러는 분 단위를 지원한다(1~720분). 3분 주기가 가능하다.

다만 **잡 주기와 데이터 해상도는 분리돼 있다.** 판독값은 watermark부터 현재까지의 30초
격자를 채우므로, 15분마다 실행해도 30초 해상도 데이터가 그대로 나온다. 달라지는 것은
데이터가 도착하는 묶음 크기뿐이다.

| 주기 | 1회 적재 행수 | 하루 실행 | 성격 |
|---|---|---|---|
| 3분 | 288 | 480회 | 요청값. 실시간감이 가장 좋음 |
| 15분 | 1,440 | 96회 | 용량 1/5. 해상도 동일 |

3분 주기는 Spark 세션을 하루 480번 새로 띄운다. 실행 시간의 대부분이 세션 기동에 쓰이고
용량 소모가 크다. **기본값은 요청대로 3분으로 두되**, 실습을 몇 시간 넘겨 돌릴 때는 15분으로
올리라고 README에 명시한다.

백필 규모는 24시간 × 48계열 × 120판독/시간 = 138,240행이다. 1회 Spark 쓰기로 충분하다.

## 11. 검증

QMS 노트북과 같이 **적재 전에** 검증하고, 치명 항목이 걸리면 쓰지 않고 중단한다.
Eventhouse는 append-only라 잘못 쓴 데이터를 되돌리기 번거롭다.

| # | 항목 | 치명 |
|---|---|---|
| 1 | 무중복 원칙: 금지 컬럼(`lot_id` 등)이 스키마에 없다 | 예 |
| 2 | 모든 판독의 `eqp_id`가 MES 설비 목록에 있다 | 예 |
| 3 | 모든 판독의 `sensor_code`가 `fdc_sensor_spec`에 있다 | 예 |
| 4 | `reading_ts`가 30초 격자에 정렬돼 있다 | 예 |
| 5 | 생성 구간이 watermark보다 엄격히 크다 | 예 |
| 6 | `status`가 `fdc_sensor_spec` 한계와 일치한다 | 아니오 |
| 7 | `Alarm` 비율이 0보다 크고 5% 미만이다 | 아니오 |
| 8 | 불량률 상위 설비의 `Alarm` 수가 하위 설비보다 많다 | 아니오 |

## 12. 패키지 구조

기존 `qms-lakehouse`와 같은 형태를 따른다. `src/`가 원본이고 노트북은 빌드 산출물이다.

```
customizing/fabric/fdc-eventhouse/
├── README.md
├── data-agent-schema.md        # KQL DB용 에이전트 지식
├── pyproject.toml
├── build_notebook.py           # src/*.py 를 셀로 인라인
├── fdc_eventhouse_stream.ipynb # 빌드 산출물
├── src/
│   ├── mes_probe.py            # MCP 전용 최소 클라이언트 (§12.1)
│   ├── fdc_sensors.py          # 센서 정의 42종
│   ├── fdc_anomaly.py          # MES 유도 이상 주입
│   ├── fdc_generator.py        # 순수 함수 판독값 생성
│   ├── fdc_schema.py           # 테이블 스키마와 조립
│   └── fdc_validate.py         # 검증 8항목
└── tests/                      # 오프라인 pytest
```

### 12.1 MES 접속은 자기 완결형으로 둔다

`qms-lakehouse`의 `mes_client.py`를 공유 모듈로 추출하는 안을 검토했으나 채택하지 않는다.

이 패키지가 MES에서 필요로 하는 것은 MCP 호출 두 개뿐이다.

- `list_process_results` → 설비 목록과 설비별 불량률
- `get_process_route` → `step_code`별 `eqp_type`

REST 호출(`/api/products`, `/api/materials`, `/api/bom`)과 `MesSnapshot` 전체는 쓰지
않는다. 190행 클라이언트를 공유하면 절반이 사용되지 않는 채로 딸려온다.

더 큰 이유는 두 노트북이 **각자 자기 완결적이어야 한다**는 기존 제약이다. `src/` 모듈은
런타임 import가 아니라 빌드 시점에 노트북 셀로 인라인된다. 따라서 "공유"는 코드 중복을
없애는 게 아니라 빌드 스크립트의 include 경로를 하나 늘릴 뿐이다.

중복되는 것은 MCP SSE 응답 파서 약 30행이다. 그 대가로 미병합 브랜치 의존과 기존
테스트 105개에 대한 회귀 위험을 없앤다. 값싼 거래다.

따라서 `mes_probe.py`는 MCP 호출만 하는 최소 클라이언트로 새로 쓴다. 이 패키지는
`main` 브랜치 위에서 단독으로 구현·테스트된다.

## 13. 핸즈온 절차

1. Eventhouse를 만든다. 같은 이름의 KQL DB가 함께 생성된다.
2. KQL DB 상세 카드에서 **Query URI**를 복사한다.
3. 노트북을 업로드하고 파라미터 셀에 Query URI와 DB 이름, MES API 키를 넣는다.
4. 전체 실행한다. 테이블 2개가 자동 생성되고 24시간치가 백필된다.
5. 노트북에 3분 주기 스케줄을 건다.
6. KQL 질의로 시계열을 본다.
7. 이 KQL DB를 Foundry 에이전트에 붙이고, MES MCP·QMS Data Agent와 함께 질문한다.

## 14. 세 시스템을 함께 봐야 답이 나오는 질문

이 자료의 도달점이다. 어느 하나로는 답이 안 나온다.

- 센서 경보가 가장 잦은 설비 3대는? 그 설비들이 처리한 로트의 품질 부적합 건수는?
- 주변 습도가 규격을 벗어난 시간대에 돌던 설비에서 오염(Contamination) 부적합이 실제로 나왔나?
- Overlay 부적합이 난 로트들의 설비에서 정렬에 영향 주는 온도 이상이 관측되나?
- 센서는 계속 정상인데 품질 부적합이 난 설비가 있나? 그렇다면 원인은 설비가 아니다.
- CMP 설비의 패드 압력 드리프트가 시작된 시점 이후 그 설비 로트의 불량률이 올라갔나?

마지막 두 개가 특히 중요하다. **센서 정상 + 품질 불량**은 "설비 탓이 아니다"라는 결론을
내리게 하고, 이는 세 시스템을 모두 조회해야만 도달할 수 있다.

## 15. 비범위

- Eventstream, Activator 경보, 실시간 대시보드 — 별도 회차 주제다.
- 실제 IoT 장비·시뮬레이터 연결.
- 로트 단위 FDC 요약 통계 테이블. 필요하면 KQL로 집계하면 된다.
- 시간 범위 조인. §3에서 성립하지 않음을 확인했다.

## 16. 리스크

| 리스크 | 대응 |
|---|---|
| Mock MES 재시드로 설비·불량률이 바뀜 | 전부 유도값이라 자동으로 따라간다. 하드코딩 없음 |
| Kusto Spark 커넥터가 런타임에 없음 | Fabric 공식 샘플이 쓰는 API. 첫 셀에서 조기 실패시키고 안내 문구를 띄운다 |
| 3분 주기가 용량을 소진 | 15분 권고를 README에 명시. 해상도는 그대로임을 함께 설명 |
| 스케줄 중단 후 재개 시 대량 적재 | `MAX_CATCHUP_HOURS`로 6시간에서 자른다 |
| `getToken()`이 스케줄 실행에서 실패 | 잡 소유자 신원으로 발급된다. 소유자에게 KQL DB 권한이 필요함을 README에 명시 |
