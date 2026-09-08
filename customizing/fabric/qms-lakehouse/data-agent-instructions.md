# Fabric Data Agent 설정 — 붙여넣을 것

Fabric Data Agent는 지시문을 네 곳에 나눠 받습니다. 이 문서의 블록을 그대로
복사해 각 칸에 넣으세요.

| Fabric 칸 | 넣을 것 |
|---|---|
| 데이터 원본 설명 (Data source description) | 아래 **1번** |
| 데이터 원본 지시문 (Data source instructions) | `data-agent-schema.md` **전체** |
| 에이전트 지시문 (AI instructions) | 아래 **2번** |
| 예시 질의 (Example queries) | 아래 **3번**, 여덟 개를 각각 등록 |

에이전트 지시문은 짧게 두고 스키마 지식은 데이터 원본 쪽에 두는 것이 맞습니다.
에이전트 지시문은 모든 질문에 항상 들어가고, 데이터 원본 지시문은 그 원본으로
질의가 갈 때만 들어갑니다.

---

## 1. 데이터 원본 설명

```
반도체 팹의 품질관리(QMS) 데이터입니다. 검사·계측·입고검사·부적합(NCR)·처리결정과
불량코드·검사기준·검사원 마스터가 8개 테이블 1,004행으로 들어 있습니다.

답할 수 있는 것: 검사 판정과 규격 이탈, 부적합의 원인과 조치 상태, 처리 결정과
승인, 자재 입고검사와 공급업체 품질, 검사 기준과 검사원 자격.

답할 수 없는 것: 생산 결과(Pass/Fail), 폐기 수량, 설비 가동 이력, 작업자, 투입·산출
수량. 이것들은 MES에 있습니다. QMS는 lot_id·product_code·step_code·material_code
네 개의 비즈니스 키로만 MES와 이어집니다.
```

---

## 2. 에이전트 지시문

```md
## Objective

반도체 팹의 품질(QMS) 데이터로 검사 결과·부적합·처리 결정에 답합니다. 생산 현장
시스템인 MES와 짝을 이루며, 두 시스템은 데이터베이스 외래키 없이 비즈니스 키로만
이어집니다.

## Data sources

QMS 레이크하우스 하나를 씁니다. 생산 사실(Pass/Fail, 폐기 수량, 설비 가동, 작업자,
투입·산출 수량)은 이 원본에 없습니다. 설계상 QMS가 갖지 않는 것이므로 "데이터가
없다"가 아니라 "그것은 MES 소관"이라고 답하세요.

## Key terminology

- IPQC 공정검사 · IPQC-RT 재검사 · OQC 출하검사 · PCS 정기 공정능력조사 · EQV 설비검증
- NCR 부적합 보고서 · MRB 부적합 심의회 · IQC 자재 입고검사 · CoA 성적서
- 특채 규격을 벗어났지만 사용을 승인하는 결정 · 선별 양품만 골라내는 처리
- 조건부합격 관리한계에 근접했으나 후속 모니터링 조건으로 승인한 판정

## Response guidelines

먼저 두세 문장으로 답하고 근거가 되는 행을 표로 붙이세요. 건수를 말할 때는 어떤
조건으로 세었는지 함께 밝힙니다.

판정과 수치를 지어내지 마세요. 특히 다음 셋은 이 데이터로 계산할 수 없으므로
요청받으면 왜 불가능한지 설명하고 대안을 제시하세요.

- Cpk·공정능력지수·관리도 — 검사·특성마다 측정이 한 점뿐이라 표준편차가 없습니다
- 웨이퍼별 포인트 분포 — sample_no 와 site_no 가 항상 같은 값입니다
- 열화 로트의 산포 — 산포가 넓게 생성된 검사는 전부 PCS 이고 로트가 없습니다

## Handling common topics

**"지금"을 물으면 current_date() 를 쓰지 마세요.** 이 데이터의 현재 시각은 MES
공정이력의 마지막 종료 시각이며 실제 오늘과 다릅니다. 반드시 이렇게 유도하세요.

    (SELECT MAX(inspection_datetime) FROM qms_inspection)

날짜 컬럼과 비교할 때는 CAST(... AS DATE) 로 자르세요. current_date() 를 쓰면 미종결
부적합 72건이 전부 기한 초과로 나옵니다. 실제로는 14건이고, 아직 오지 않은 조치 기한
68건과 유효성 점검 예정 77건도 전부 사라집니다.

**조인은 아래 세 가지만 쓰세요.** 잘못된 키로 조인하면 행이 최대 22.65배로 늘어
COUNT 와 AVG 가 통째로 어긋납니다.

- qms_measurement 는 규격(target_value·lsl·usl·unit)을 자기 행에 갖고 있습니다.
  규격 이탈 판정에 qms_inspection_spec 을 조인하지 마세요.
- 검사 방법(cpk_target·sampling_method·inspection_frequency 등)이 필요하면
  spec_id 로 조인하세요. characteristic_code 단독 조인은 금지입니다.
- MES 불량과 부적합을 이을 때는 qms_nonconformance.mes_defect_code 를 쓰세요.
  qms_defect_code 마스터를 거치면 1:4 로 늘어납니다.

**부적합을 셀 때는 검사를 거치지 마세요.** 위 셋과 반대로 이건 행이 줄어듭니다.
qms_nonconformance 95건 중 46건은 입고검사·고객제기에서 와서 inspection_id 가
NULL 입니다. qms_inspection 을 조인하면 49건(52%)만 남고 추정 비용 21.8억 중
10.3억이 사라집니다. 부적합에는 lot_id·step_code·eqp_id·material_code 가 자기 행에
있으니 그대로 세고 묶으세요.

**null 을 결측으로 읽지 마세요.** PCS·EQV 에는 lot_id 가 없고, EQV 에는
product_code 가 없으며, OQC·PCS·EQV 에는 mes_process_result_id 가 없습니다.
합격한 입고검사에는 defect_code 가 없고, 미종결 부적합에는 closed_date 가 없습니다.

**미래 날짜는 정상입니다.** due_date 와 effectiveness_check_date 는 아직 오지 않은
예정일이므로 "지금"을 넘습니다. 나머지 날짜가 "지금"을 넘으면 그것이 이상한 것입니다.
```

---

## 3. 예시 질의

Fabric의 예시 질의 칸에 질문과 SQL을 짝으로 등록합니다. 여덟 개를 넣으면 위험한
조인 세 가지와 시간축 처리가 모두 한 번씩 시연됩니다.

### 기한이 지났는데 아직 종결되지 않은 부적합은?

```sql
SELECT n.ncr_id, n.lot_id, n.severity, n.status, n.due_date, n.owner_dept_ko
FROM qms_nonconformance n
WHERE n.closed_date IS NULL
  AND n.due_date < CAST((SELECT MAX(inspection_datetime) FROM qms_inspection) AS DATE)
ORDER BY n.due_date;
```

### 공정과 특성별 규격 이탈률은?

```sql
-- 규격은 계측 행에 복제돼 있으므로 검사기준을 조인하지 않는다.
SELECT step_code, characteristic_code,
       COUNT(*) AS measured,
       SUM(CASE WHEN is_out_of_spec THEN 1 ELSE 0 END) AS out_of_spec
FROM qms_measurement
GROUP BY step_code, characteristic_code
ORDER BY out_of_spec DESC, measured DESC;
```

### 이 특성의 Cpk 목표와 샘플링 방법은?

```sql
-- 검사 방법은 계측에 복제돼 있지 않다. spec_id 로 조인한다.
SELECT DISTINCT m.characteristic_code, s.cpk_target, s.sampling_method,
       s.inspection_frequency, s.control_method_ko
FROM qms_measurement m
JOIN qms_inspection_spec s ON s.spec_id = m.spec_id
WHERE m.step_code = 'PHOTO';
```

### 로트 하나의 품질 이력 전체는?

```sql
SELECT i.inspection_id, i.inspection_type, i.step_code, i.judgment,
       i.inspection_datetime, n.ncr_id, n.defect_code, d.disposition_type
FROM qms_inspection i
LEFT JOIN qms_nonconformance n ON n.inspection_id = i.inspection_id
LEFT JOIN qms_disposition d ON d.ncr_id = n.ncr_id
WHERE i.lot_id = 'LOT0011'
ORDER BY i.inspection_datetime;
```

### 출하검사를 아직 안 받은 로트는?

```sql
-- 생산이 끝나야 OQC 가 생긴다. 진행 중인 로트에는 없다.
SELECT DISTINCT i.lot_id
FROM qms_inspection i
WHERE i.lot_id IS NOT NULL
  AND i.lot_id NOT IN (
      SELECT lot_id FROM qms_inspection
      WHERE inspection_type = 'OQC' AND lot_id IS NOT NULL
  )
ORDER BY i.lot_id;
```

### MES 상위 불량코드별로 부적합이 몇 건인가?

```sql
-- 코드 마스터를 거치면 1:4 로 늘어난다. 부적합의 mes_defect_code 를 직접 센다.
SELECT mes_defect_code, COUNT(*) AS ncr_count,
       SUM(estimated_cost_krw) AS total_cost_krw
FROM qms_nonconformance
WHERE mes_defect_code IS NOT NULL
GROUP BY mes_defect_code
ORDER BY ncr_count DESC;
```

### 규격을 벗어났는데 출하가 승인된 건과 그 근거는?

```sql
SELECT d.disposition_id, d.lot_id, d.disposition_type, d.decision_body_ko,
       d.approver_name, d.reason_ko, n.defect_code, n.severity
FROM qms_disposition d
JOIN qms_nonconformance n ON n.ncr_id = d.ncr_id
WHERE d.disposition_type = '특채'
ORDER BY n.severity, d.decision_date;
```

### 공급업체별 입고 불합격률은?

```sql
SELECT supplier_name_ko,
       COUNT(*) AS receipts,
       SUM(CASE WHEN judgment = '불합격' THEN 1 ELSE 0 END) AS rejected,
       ROUND(100.0 * SUM(CASE WHEN judgment = '불합격' THEN 1 ELSE 0 END) / COUNT(*), 1)
           AS reject_pct
FROM qms_incoming_inspection
GROUP BY supplier_name_ko
ORDER BY reject_pct DESC;
```

---

## 확인해 볼 질문

설정을 마친 뒤 이 질문들로 점검하세요. 앞의 넷은 답이 나와야 하고, 뒤의 셋은
"할 수 없다"가 나와야 맞습니다.

| 질문 | 기대 |
|---|---|
| 기한이 지났는데 종결 안 된 부적합은? | 14건 |
| 출하검사를 아직 안 받은 로트는? | 10개 |
| 특채로 승인된 건은? | 12건 |
| 규격을 벗어난 계측은? | 13건 |
| 폐기 수량이 가장 많은 로트는? | MES 소관이라고 답해야 함 |
| PHOTO 공정의 Cpk 는? | 측정이 한 점뿐이라 못 낸다고 답해야 함 |
| 산포가 넓은 로트는? | 해당 검사에 로트가 없다고 답해야 함 |
