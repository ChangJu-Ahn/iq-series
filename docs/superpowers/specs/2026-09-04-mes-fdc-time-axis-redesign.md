# MES·FDC 시간축 재설계

작성일: 2026-09-04
상태: 검토 대기 (rubber-duck 검토 1회 반영)
관련: `2026-09-04-fdc-eventhouse-telemetry-design.md` (이 문서가 시간 모델을 대체한다)

## 1. 왜 고치는가

세 시스템을 하나의 사건으로 엮으려면 공통 시간축이 있어야 한다. 지금은 없다.

| 시스템 | 시각 | 출처 |
|---|---|---|
| MES | 시드 실행 시점 **2초 안**에 91건 전부 | `db._now_iso()` 기본값 |
| QMS | MES `out_time` + 10~240분 | MES를 앵커로 삼음 (일부만) |
| FDC | wall-clock 최근 24시간 | MES와 무관 (이탈) |

### 1.1 MES 시각이 물리적으로 불가능한 증거

```
1. LOT0001 이 seq10 DIFF 부터 seq80 TEST 까지 전부 06:53:09  → 한 로트가 전 공정 동시 통과
2. 91건 전부 in_time == out_time                            → 공정 소요 0초
3. EQP-DIFF01 이 06:53:10 에 9개 로트, EQP-ETCH01 이 8개      → 낱장 설비의 동시 처리
```

고유 시각은 `06:53:09`(53건), `06:53:10`(38건) 둘뿐이다.

### 1.2 근본 원인

`mes_core/db.py:615` 의 `register_process_result()` 는 **이미 `in_time`/`out_time` 을
받는다**. `seed.py:146` 의 `_advance()` 가 안 넘겨서 둘 다 `_now_iso()` 로 떨어진다.

```python
# mes_core/db.py:615-618
def register_process_result(..., in_time=None, out_time=None):
    now = _now_iso()
    in_time = in_time or now      # ← seed 가 안 넘김
    out_time = out_time or now
```

### 1.3 DB 직접 수정은 불가능

| 증거 | 위치 |
|---|---|
| `storageType: 'EmptyDir'` | `infra/main.bicep:99` |
| `initContainers: [seed]` → `python -m mes_core.seed` | `main.bicep:102-111` |
| `minReplicas: 0` | `main.bicep:150` |
| `seed()` 가 `db.reset_db()` 호출 | `seed.py:155` |

볼륨이 레플리카와 함께 사라지고 콜드스타트마다 시드가 다시 돈다. 라이브 DB에
`UPDATE` 를 걸어도 다음 콜드스타트에 소멸한다. **소스를 고치는 것이 유일한 방법이다.**

## 2. 설계 원칙

1. **MES가 유일한 시간 원천이다.** FDC는 MES에서 구간을 끌어온다.
2. **앵커는 배포 시점에 고정된다.** 런타임 wall-clock으로 계산하지 않는다.
3. **기존 업무 데이터를 한 비트도 바꾸지 않는다.** 스케줄링은 별도 RNG를 쓴다.
4. **자원 경합을 지킨다.** 한 설비가 같은 시각에 두 로트를 처리하지 않는다.
5. **미래 시각을 만들지 않는다.**

## 3. 시간 앵커

### 3.1 런타임 계산은 안 된다

초안은 `anchor` 를 시드 실행 시점의 UTC 일 단위 내림으로 잡았다. **틀렸다.**

- UTC 자정은 **09:00 KST** — 핸즈온 시작 시각이다.
- 그 경계를 넘어 MES가 콜드스타트하면 모든 공정이 24시간 앞으로 밀린다.
- FDC는 전역 `max(reading_ts)` 하나만 워터마크로 쓴다(`fdc_schema.py:114`). 밀린
  구간은 워터마크보다 과거가 되어 **영구히 건너뛴다.**
- 이미 적재된 FDC 행과 QMS 레이크하우스는 옛 MES 세대에 묶여 stale이 된다.

즉 "FDC가 MES에서 구간을 가져오니 항상 정렬된다"는 **비어 있는 저장소와 불변 MES에만
참**이다.

### 3.2 배포 시점 앵커

```
env  MES_ANCHOR = "2026-09-04T00:00:00Z"      # ISO 8601 UTC
```

```bicep
param anchorUtc string = utcNow('yyyy-MM-ddT00:00:00Z')   // 배포 시각에 1회 평가
```

Bicep `utcNow()` 는 파라미터 기본값에서만 허용되고 **배포 시작 시 한 번** 평가된다.
값은 리비전 환경변수에 박히므로 콜드스타트가 몇 번 일어나도 동일하다.

`seed.py` 는 `MES_ANCHOR` 를 읽는다. 없으면 로컬 개발용으로 `now` 의 UTC 일 내림을
쓰되, 그 경우 배포본이 아님을 로그에 남긴다.

**운영 절차:** 코호트마다 재배포한다. 그러면 앵커가 갱신되어 데이터가 최신이 된다.
재배포하지 않으면 MES 데이터는 배포 시점에 머문다 — FDC가 §5.4로 그 간극을 메운다.

## 4. MES 공정 스케줄러

### 4.1 별도 RNG — 이것이 가장 중요하다

`seed()` 는 `random.Random(42)` 하나로 경로 깊이·스크랩·불량코드·판정·설비·작업자를
모두 뽑는다(`seed.py:153-173`). 여기에 `duration()`·`transfer()` 추출을 끼워 넣으면
**이후 모든 난수가 밀려** 91건이라는 개수부터 불량 35건, 스캐너 11:5 분배, Fail/Rework
분포, 재고까지 전부 달라진다. QMS는 `IPQC=91`, `IPQC-RT=40` 을 하드코딩하고 특정
Rework 레코드를 인덱스로 집는다 — 전부 깨진다.

**그래서 스케줄링은 독립 RNG를 쓴다.**

```python
rnd   = random.Random(42)          # 기존 업무 데이터 — 추출 순서 불변
sched = random.Random(20260904)    # 신규. 소요시간·이송시간 전용
```

이 하나로 §7의 QMS 파급이 대부분 사라진다. 업무 데이터가 비트 단위로 동일하다.

### 4.2 공정 소요시간

| step | 설비 유형 | 소요(분) | 비고 |
|---|---|---|---|
| DIFF | Furnace | 90~180 | |
| PHOTO | Scanner | 30~90 | PHOT01/PHOT02 2대 |
| ETCH | Etcher | 40~120 | |
| IMPL | Implanter | 30~60 | |
| CVD | CVD | 60~180 | |
| CMP | Polisher | 30~60 | |
| METRO | — | 15~30 | 설비 미할당, 설비 큐를 점유하지 않음 |
| TEST | Prober | 120~240 | |

스텝 사이 이송·대기 10~40분. **확산로를 배치로 모델링하지 않는다** — 여러 로트가
겹치면 FDC 판독값의 `lot_id` 가 단일 값으로 성립하지 않는다(§5.2). 낱장 처리로도
충분히 들어간다(§4.5).

### 4.3 스케줄 알고리즘

로트 릴리스는 **4시간 등간격**이다. 구간이 아니라 정확한 값이어야 재현된다.

```
release[lot_i] = i * 240분          (lot_id 순, i = 0..15)

eqp_free[eqp]  = 설비가 비는 시각
lot_ready[lot] = 로트가 다음 스텝에 갈 수 있는 시각

for lot in sorted(lots, key=(release, lot_id)):        # 디스패치 순서를 명시
    lot_ready[lot] = release[lot]
    for step in 그 로트의 route:
        eqp   = 기존 시드가 배정한 설비                  # 새로 뽑지 않는다
        start = max(lot_ready[lot], eqp_free[eqp]) if eqp else lot_ready[lot]
        end   = start + sched.randint(*DUR[step])
        lot_ready[lot] = end + sched.randint(10, 40)
        if eqp: eqp_free[eqp] = end

# 전체를 밀어 마지막 공정이 정확히 anchor 에 닿게 한다
offset = anchor - max(end)
모든 start/end 에 offset 을 더한다
```

**잘라내기(truncation)가 없다.** 초안은 `anchor` 를 넘는 스텝을 버리려 했으나 그러면
`register_process_result()` 가 `current_step` 을 완료 스텝으로 갱신하는 동작
(`db.py:656-664`)과 어긋나 "지금 그 공정에 있다"는 상태가 만들어지지 않는다. 대신
전체를 오프셋으로 밀면 91건이 모두 등록되면서 미래 시각도 안 생긴다.

**등록 순서.** 스케줄을 먼저 다 계산한 뒤 `out_time` 오름차순으로 `INSERT` 한다.
`db.py:587-608` 의 조회가 `ORDER BY pr.id DESC` 이므로 ID가 시간순이어야 "최근 공정"
화면이 맞다.

### 4.4 릴리스와 최근성

로트 1~6(Done, 8단계)이 먼저 나가고 9~16(Running, 2~5단계)이 나중에 나간다. 짧은
경로가 뒤에 오므로 **가장 최근 공정은 자연히 Running 로트의 것이 된다.** 별도 밴드
설정이 필요 없다.

### 4.5 실측 검증

`tests/fixtures/mes_facts.json` 의 실제 로트 구성(91런)으로 시뮬레이션한 결과다.

```
등록 런        91 / 91          ← 잘라내기 없음
구간           anchor-64.8h ~ anchor
고유 out_time  91
in < out       True
out <= anchor  True
로트 내 순서   True             ← out_time[n] < in_time[n+1]
설비 겹침      0건

가장 최근 공정
  LOT0015 CVD    EQP-CVD01    anchor-2.3h → anchor
  LOT0016 PHOTO  EQP-PHOT02   anchor-3.0h → anchor-1.9h
  LOT0015 IMPL   EQP-IMPL01   anchor-3.4h → anchor-2.9h
```

간격을 바꿔가며 최소/중간/최대 소요시간으로 makespan을 측정했다.

| 릴리스 간격 | 최소 | 중간 | 최대 |
|---|---|---|---|
| 180분 | 47.2h | 51.0h | 55.2h |
| **240분** | **62.2h** | **65.0h** | **69.2h** |
| 270분 | 69.7h | 72.0h | 76.2h |

240분이면 난수와 무관하게 62~69h — 3일 이내에 항상 들어간다. 270분은 최대 소요에서
72h를 넘는다.

병목은 EQP-DIFF01이다. 16런을 직렬 처리하므로 릴리스 간격이 2시간 이하면 makespan이
전혀 줄지 않는다(설비 포화). 설비 가동률은 25.0% (129.5h / 518 설비시간).

### 4.6 불변식

- 한 로트 안에서 `out_time[seq_n] < in_time[seq_n+1]`
- 같은 설비의 두 런은 구간이 겹치지 않는다
- 모든 `in_time < out_time`
- 모든 `out_time <= anchor`
- `process_result` 91건, 고유 `out_time` 91개
- 두 번 시드해도 모든 타임스탬프가 동일하다
- **기존 업무 데이터가 변경 전과 비트 단위로 같다** (설비 배정·불량·판정·수량)

마지막 항목은 변경 전 `mes_facts.json` 과 대조하는 테스트로 강제한다.

### 4.7 남는 wall-clock 의존

`db.py` 에 `_now_iso()` 를 그대로 쓰는 곳이 더 있다.

| 위치 | 대상 | 조치 |
|---|---|---|
| `db.py:490` | `lot.start_date` | 릴리스 날짜로 |
| `db.py:666-677` | AUTO_FAB `result_date` | TEST `out_time` 날짜로 |
| `db.py:710-735` | 포장 결과 | PKG 시각 또는 명시적 제외 |

이걸 두면 "3일 전 공정인데 완제품은 오늘 생산"이라는 모순이 남는다.

## 5. FDC 재설계

### 5.1 생성 구간

wall-clock 24시간 창을 버린다.

```
[ MES min(in_time) ─────────── MES max(out_time)=anchor ]────────── now
       런 30초 (6센서) / 유휴 5분 (2센서)                    유휴 5분 (2센서)
```

전체 범위는 **`[MES min(in_time), now]`** 다. 런 구간 밖은 전부 유휴로 본다 —
`anchor` 와 `now` 사이도 포함이다.

**24시간 캡을 제거해야 한다.** 현재 `build_notebook.py:177-189` 는 모든 구간을 24시간
으로 자른다. 그대로 두면 첫 백필이 최근 24시간만 남기고, 워터마크가 `now` 로 뛰어
나머지 구간을 **영구히 건너뛴다.** 백필에는 캡을 적용하지 않는다. 청크가 필요하면
MES 시작점부터 커서를 **전진**시킨다(끝에서 역산하지 않는다).

### 5.2 런 태깅

`READING_SCHEMA` 는 이미 `step_code` 를 갖는다(`fdc_schema.py:64`). 그 값은
`EquipmentProfile.step_code` — **설비가 담당하는 공정**이고 유휴에도 유효하므로
그대로 둔다. 신규 컬럼은 하나뿐이다.

| 컬럼 | 타입 | 값 |
|---|---|---|
| `lot_id` | string | 런 중이면 로트 ID, 유휴면 빈 값 |

`lot_id` 유무가 런/유휴 구분이므로 `run_status` 는 넣지 않는다. `step_seq` 는 MES
route에서 얻을 수 있어 넣지 않는다. 키는 `(eqp_id, sensor_code, reading_ts)` 다.

**격자 생성 방식을 바꾼다.** `grid_timestamps()` 는 `(start, end]` 반열림이다
(`fdc_generator.py:43-60`, 워터마크 중복 방지 목적). 이걸 런 구간에 그대로 쓰면
`in_time` 정각이 빠지고 `out_time` 정각이 런으로 태깅돼 판정 규칙과 어긋난다.

대신 **단일 타임라인을 만들고 각 시각을 분류한다.**

```
1. 전체 범위에 30초 격자를 깐다 (워터마크 초과분만)
2. 각 시각 t 에 대해 in_time <= t < out_time 인 런을 찾는다
3. 런이 있으면  → 센서 6종, lot_id 태깅
   런이 없으면  → t 가 5분 격자 위일 때만 공통 센서 2종
```

300초는 30초의 배수이고 `align_to_grid` 가 epoch 기준이므로(`fdc_generator.py:35`)
두 격자가 어긋나지 않는다. 한 시각이 런과 유휴 양쪽에 속하는 일도 없다.

`grid_timestamps()` 는 워터마크 의미로만 남긴다.

### 5.3 유휴 시 센서 — 공통 2종만

초안은 유휴에 공정 센서를 baseline/0으로 내보내려 했다. **위험하다.** `RF_POWER` 의
`alarm_min` 은 1400이고(`fdc_sensors.py:54`) 분류기는 그 아래를 전부 `Alarm` 으로
찍는다(`fdc_generator.py:107-112`). 유휴 0은 곧 경보다. 설비당 공정 센서가 4종이니
경보가 유휴로 도배되고 기존 "Alarm < 5%" 검증도 깨진다.

코드에 이미 경계가 있다. `sensors_for()` 는 `COMMON_SENSORS + TYPE_SENSORS[type]` 다
(`fdc_sensors.py:91`).

| 구간 | 센서 | 간격 |
|---|---|---|
| 런 중 | `COMMON_SENSORS + TYPE_SENSORS` (6종) | 30초 |
| 유휴 | `COMMON_SENSORS` 만 (주변 온도·습도 2종) | 5분 |

설비가 멈추면 챔버 압력·RF 파워는 측정 자체가 무의미하다. 주변 온도·습도는 계속
돈다. 물리적으로도 맞고, `fdc_sensor_spec` 을 건드리지 않아도 된다.

`sample_interval_sec` 이 30 고정인 문제는 남는다. `fdc_sensor_spec` 의 그 값은
**런 중 간격**이라고 문서에 명시한다.

### 5.4 볼륨

§4.5 시뮬레이션 기준 (구간 64.8h, 가동률 25.0%):

| 구간 | 간격 × 센서 | 행 수 |
|---|---|---|
| 런 | 30초 × 6종 | 93,216 |
| 유휴 | 5분 × 2종 | 9,330 |
| **MES 구간 합계** | | **102,546** |
| `anchor`→`now` 보충 | 5분 × 2종 | 하루당 4,608 |

균일 30초 6종(414,720행) 대비 25%다.

### 5.5 이상 주입

설비 단위 상시 이상을 **런 단위**로 바꾼다.

- `defect_code` 가 있는 `process_result` 의 `[in_time, out_time)` 에만 이탈을 싣는다
- 시드를 `seed(lot_id, step_code, eqp_id, sensor_code)` 로 확장
- `hash()` 금지, `hashlib.sha256` 유지

**주의: `defect_code` 와 `result` 는 독립이다.** `seed.py:140-147` 에서 불량코드는
스크랩이 있을 때만 붙고 판정은 따로 뽑는다. 그래서 `Fail` 인데 `defect_code` 가
`null` 인 런이 존재한다. 실측으로 둘 다 만족하는 런은 **2건뿐**이다.

```
LOT0011 CMP  EQP-CMP01   Fail    CD-OOS    → FOCUS_OFFSET/RF_POWER/ILLUM_DOSE
LOT0010 ETCH EQP-ETCH01  Rework  Particle  → CHAMBER_TEMP/AMBIENT_HUMIDITY
```

Polisher에는 CD-OOS가 지목한 센서가 하나도 없어 LOT0011은 `AMBIENT_TEMP` 로 폴백한다.
Etcher는 `CHAMBER_TEMP` 를 갖는다. 따라서 **대표 시나리오는 LOT0010 / ETCH /
EQP-ETCH01 / Particle → CHAMBER_TEMP** 하나다. 문서와 테스트는 이 런을 고정해 쓴다.

METRO 7런은 설비가 없어 FDC 데이터가 없다. 불량이 나도 센서로 확인할 수 없다 —
"모든 공정에 FDC가 붙어 있지는 않다"는 현실이므로 문서에 남긴다.

### 5.6 픽스처 갱신

MES 변경 머지 후 `tests/fixtures/mes_facts.json` 을 다시 뽑는다. 시각은 `anchor` 기준
상대값으로 저장해 테스트가 날짜에 묶이지 않게 한다.

### 5.7 스키마 이행

Kusto Spark 커넥터는 첫 쓰기 때 DataFrame 스키마로 테이블을 만들고 제어 명령은 못
보낸다. 컬럼이 늘면 기존 테이블에 쓰기가 실패한다. **이 노트북은 아직 Fabric에서
실행된 적이 없으므로 릴리스 전에 스키마 변경을 머지한다.** 수동 `.drop table` 을
설계에 남기지 않는다.

## 6. QMS 파급

§4.1의 별도 RNG 덕에 업무 데이터가 그대로이므로 `IPQC=91`, `IPQC-RT=40` 하드코딩과
Rework 인덱싱은 계속 성립한다. **QMS 코드 변경 없이 시각만 따라 퍼진다.**

다만 QMS는 이번 범위 밖이고, 다음 두 가지는 별도 작업으로 기록한다.

1. IPQC와 재검사만 MES `out_time` 에 앵커한다. OQC 등은 2026년 9월로 하드코딩돼
   있어(`qms_inspection.py:292,329,367`) 다음 코호트에서 생산보다 앞설 수 있다.
2. 인과성 검증에 상한이 없다. 재검사는 `out_time + 최대 720분` 이라 `anchor` 가
   자정이면 정오까지 나온다. 고객 클레임 NCR은 `BASE_DATE + 3~10일` 이라
   (`qms_nonconformance.py:279`) 명시적으로 미래다. `<= now` 검증을 추가해야 한다.

## 7. 알려진 한계 (이번에 고치지 않음)

| 항목 | 내용 |
|---|---|
| MCP 쓰기 | `mcp_server/server.py:33-56` 이 시각을 안 받아 학생이 공정을 등록하면 소요 0초 레코드가 다시 생기고 `max(out_time)` 이 앵커를 넘을 수 있다. 읽기 전용 안내 또는 서버측 시각 생성이 필요하다 |
| `MES_API_KEY` | `build_notebook.py:80-82` 가 노트북 셀에 붙여넣게 한다. Mock 서비스 키지만 "노트북에 비밀값 없음" 원칙과 어긋난다. Eventhouse 접속에는 비밀값이 없다는 점만 유효하다 |
| Fabric 미검증 | 노트북이 실제 Fabric에서 한 번도 실행되지 않았다. Kusto Spark 커넥터 사전 설치와 스케줄 실행 시 토큰 획득은 미확인 가정이다 |

## 8. 범위

| 포함 | 제외 |
|---|---|
| `mock-mes-kr` 스케줄러 + 별도 RNG | Eventstream 실시간 수집 |
| `MES_ANCHOR` env + Bicep 파라미터 | QMS 브랜치 수정 |
| §4.6 불변식 테스트 | MCP 쓰기 경로 |
| `db.py` wall-clock 잔여 3곳 | 설비 추가 |
| FDC 구간을 MES에서 유도, 24h 캡 제거 | 확산로 배치 모델링 |
| `lot_id` 컬럼 + 단일 타임라인 분류 | |
| 유휴는 공통 센서 2종만 | |
| 런 단위 이상 주입 | |
| 픽스처·문서 갱신 | |

## 9. 실행 순서

```
1. mock-mes-kr   스케줄러 + 앵커 + 테스트   → PR → 머지 → 재배포
2. iq-series     새 MES에서 픽스처 재생성
3. iq-series     FDC 재설계 (5.1~5.5)
4. iq-series     노트북 재빌드 · 문서 갱신
```

1번이 끝나야 2번이 가능하다. 1번은 별도 세션에서 진행한다.

## 10. 검증

**MES** — §4.6 불변식 전부, 그리고 변경 전 픽스처와의 업무 데이터 동일성.

**FDC**

- 모든 `reading_ts` 가 `[MES min(in_time), now]` 안
- `lot_id` 가 있는 판독값은 해당 런의 `[in_time, out_time)` 안
- 유휴 판독값의 `lot_id` 는 비어 있고 센서는 공통 2종뿐
- 같은 `(eqp_id, sensor_code, reading_ts)` 가 두 번 나오지 않는다
- 유휴 구간에 `Alarm` 이 없다 (§5.3 회귀 방지)
- LOT0010 ETCH 구간에 `CHAMBER_TEMP` 이탈이 있다
- 기존 252개 테스트가 갱신 후에도 통과

**교차 시스템 (수동)**

```
"LOT0010 은 왜 재작업했지?"
  MES  → ETCH / EQP-ETCH01 / 구간 / defect_code=Particle / result=Rework
  FDC  → 그 구간 CHAMBER_TEMP 이탈, lot_id=LOT0010
  QMS  → 해당 로트 검사 판정
```

세 답의 시각이 인과 순서를 이룬다.
