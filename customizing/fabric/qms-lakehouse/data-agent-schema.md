# QMS 레이크하우스 스키마 — Data Agent 지식

이 문서는 Fabric Data Agent에 넣을 컨텍스트입니다. 어떤 질문에 어떤 테이블을 어떻게
조인해야 하는지를 담고 있습니다.

## 이 데이터가 무엇인가

반도체 팹의 품질관리(QMS) 데이터입니다. 8개 테이블 1,004행이며, 생산 시스템인
Mock MES와 짝을 이룹니다.

## MES와의 관계 — 가장 먼저 읽을 것

QMS와 MES는 **별개 시스템**입니다. 데이터베이스 외래키가 없고, 다음 네 개의
비즈니스 키로만 이어집니다.

| 키 | 의미 |
|---|---|
| `lot_id` | 생산 로트. `LOT0001`~`LOT0016` |
| `product_code` | 제품. `DDR5`, `LX9`, `NAND`, `PMIC` |
| `step_code` | 공정. `DIFF`, `PHOTO`, `ETCH`, `IMPL`, `CVD`, `CMP`, `METRO`, `TEST`, `PKG` |
| `material_code` | 자재 |

추가로 `qms_inspection.mes_process_result_id` 가 MES 공정이력 레코드의 `id` 를
가리킵니다. 이것이 개별 검사와 개별 생산 기록을 잇는 가장 정밀한 연결점입니다.

QMS 내부 참조는 `inspection_id`, `ncr_id`, `spec_id`, `inspector_id`, `iqc_id` 이며
이들은 MES에 존재하지 않습니다.

### QMS에 **없는** 것 (중요)

다음 컬럼은 QMS 어느 테이블에도 없습니다. MES가 이미 가진 사실을 QMS가 중복
보유하지 않는다는 설계 계약 때문입니다.

`mes_result`, `scrap_qty`, `operator`, `in_qty`, `out_qty`

생산 결과가 Pass였는지 Fail이었는지, 폐기 수량이 몇 장인지, 누가 장비를 돌렸는지는
**MES에 물어야 합니다.** QMS에서 찾다가 없다고 "데이터가 없다"고 답하면 안 됩니다.

`qms_inspection.inspected_wafer_qty` 는 MES `in_qty` 의 복사본이 아니라 QMS가 실제로
집어 든 샘플 매수입니다. 둘은 다른 값입니다.

## 테이블

### `qms_defect_code` — 24행

MES의 상위 불량코드 6종을 QMS가 원인·조치 단위로 4단계씩 전개한 마스터입니다. MES는 `Particle` 까지만 알고, 그것이 챔버 박리물인지 인체 유래인지는 QMS만 압니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `defect_code` | string PK | QMS 세부 불량코드. 예 `DEF-PTC-001` |
| `defect_name_ko` | string | 한글 불량명. 예 "파티클 오염 0.12um 이상" |
| `defect_name_en` | string | 영문 불량명 |
| `mes_defect_code` | string `MES` | MES 상위 코드. Particle/Scratch/Overlay/Etch-Residue/Contamination/CD-OOS |
| `defect_category` | string | 오염/패턴/치수/막질/전기/외관 |
| `severity` | string | Critical/Major/Minor |
| `severity_score` | int | 9/6/3 |
| `typical_step_codes` | string `MES` | 주 발생 공정 CSV. 예 `DIFF,CVD` |
| `standard_cause_ko` | string | 표준 원인 |
| `standard_action_ko` | string | 표준 조치 |
| `is_active` | boolean | 사용 여부 |

### `qms_inspection_spec` — 108행

제품·공정·특성마다의 검사 기준입니다. 규격 상하한과 샘플링 방법이 여기 있습니다. MES에는 규격 개념이 아예 없습니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `spec_id` | string PK | 예 `SPEC-DDR5-PHOTO-CD` |
| `product_code` | string `MES` | 제품 코드 |
| `product_name` | string | 제품명 라벨 |
| `step_code` | string `MES` | 공정 코드 |
| `step_name` | string | 공정명 라벨 |
| `characteristic_code` | string | CD/OVL/THK/PTC/RS/WRP |
| `characteristic_name_ko` | string | 선폭/정렬도/막두께/파티클수/면저항/휨 |
| `measurement_type` | string | 계량형/계수형 |
| `unit` | string | nm/um/ea/Ω·sq/% |
| `target_value` | double | 목표치 |
| `lsl` | double | 하한 규격 |
| `usl` | double | 상한 규격 |
| `cpk_target` | double | 1.33 |
| `sampling_method` | string | 예 "5매 랜덤 9포인트" |
| `sample_size` | int | 샘플 매수 |
| `inspection_frequency` | string | 전수/로트별/시간별 |
| `control_method_ko` | string | 예 "X-bar R 관리도" |
| `spec_version` | string | 개정 버전 |
| `effective_from` | date | 적용 시작일 |
| `is_active` | boolean | 사용 여부 |

### `qms_inspector` — 15행

검사원 마스터입니다. MES의 `operator`(생산 작업자)와 겹치지 않는 별도 인력이며, 자격 보유 특성으로 누가 어떤 검사를 할 수 있는지 정해집니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `inspector_id` | string PK | 예 `QI-001` |
| `inspector_name` | string | 검사원명. MES `operator`와 겹치지 않는 별도 인력 |
| `team_ko` | string | 계측팀/입고검사팀/신뢰성팀/출하검사팀/품질보증팀 |
| `shift_code` | string | A/B/C |
| `qualification_level` | string | 초급/중급/선임/책임 |
| `certified_characteristics` | string | 자격 보유 특성 CSV |
| `certified_from` | date | 자격 취득일 |
| `certified_until` | date | 자격 만료일 |
| `is_active` | boolean | 재직 여부 |

### `qms_inspection` — 207행

이 데이터셋의 중심 테이블입니다. 공정검사·재검사·출하검사·공정능력조사·설비검증 다섯 유형이 한 테이블에 있습니다. `mes_process_result_id` 가 MES 공정이력과의 유일한 연결점입니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `inspection_id` | string PK | 예 `INS-2026-0001` |
| `inspection_type` | string | IPQC(공정검사)/IPQC-RT(재검사)/OQC(출하검사)/PCS(정기 공정능력)/EQV(설비 검증) |
| `lot_id` | string `MES` | 로트. PCS·EQV는 `null` |
| `product_code` | string `MES` | 제품. EQV는 `null` |
| `product_name` | string | 제품명 라벨 |
| `step_code` | string `MES` | 공정 |
| `step_name` | string | 공정명 라벨 |
| `eqp_id` | string `MES` | 생산설비 |
| `mes_process_result_id` | int `MES` | MES 공정이력 1건과 직접 연결. IPQC 계열만 값 보유 |
| `inspector_id` | string `FK` | 검사원 |
| `inspection_datetime` | timestamp | 검사 일시 |
| `sample_size` | int | 검사 샘플 수 |
| `inspected_wafer_qty` | int | 검사 웨이퍼 수 |
| `judgment` | string | 합격/조건부합격/불합격. MES `result`와 독립 |
| `judgment_basis_ko` | string | 판정 근거 |
| `defect_found_qty` | int | 검출 결함 수 |
| `measurement_count` | int | 측정 건수 |
| `has_nonconformance` | boolean | NCR 발행 여부 |
| `remark_ko` | string | 비고 |

### `qms_measurement` — 260행

규격 대비 실측값입니다. 검사 시점의 규격을 각 행에 스냅샷으로 복사해 두었으므로, 나중에 기준이 개정되어도 과거 판정이 흔들리지 않습니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `measurement_id` | string PK | 예 `MEA-2026-000001` |
| `inspection_id` | string `FK` | 검사 |
| `spec_id` | string `FK` | 검사기준 |
| `lot_id` | string `MES` | 하향 전개 조인키 |
| `product_code` | string `MES` | 하향 전개 조인키 |
| `step_code` | string `MES` | 하향 전개 조인키 |
| `characteristic_code` | string | 측정 특성 |
| `characteristic_name_ko` | string | 특성명 라벨 |
| `sample_no` | int | 웨이퍼 번호 |
| `site_no` | int | 측정 포인트 |
| `measured_value` | double | 측정값 |
| `unit` | string | 단위 |
| `target_value` | double | 규격 스냅샷. spec 개정 대비 |
| `lsl` | double | 규격 스냅샷 |
| `usl` | double | 규격 스냅샷 |
| `deviation_pct` | double | 목표 대비 편차율 |
| `is_out_of_spec` | boolean | 규격 이탈 여부 |
| `judgment` | string | OK/NG |
| `metrology_eqp_id` | string | QMS 계측기. 예 `MET-CD01`. MES 생산설비와 별개 |
| `measured_by` | string `FK` | 검사원 |
| `measured_at` | timestamp | 측정 시각 |

### `qms_incoming_inspection` — 200행

자재 입고검사입니다. MES는 자재를 BOM으로만 알 뿐 그것이 검사를 통과했는지 모릅니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `iqc_id` | string PK | 예 `IQC-2026-0001` |
| `material_code` | string `MES` | 자재 |
| `material_name` | string | 자재명 라벨 |
| `supplier_code` | string | 공급업체 코드. QMS 고유 |
| `supplier_name_ko` | string | 공급업체명 |
| `supplier_lot_no` | string | 공급업체 로트번호 |
| `receipt_date` | date | 입고일 |
| `received_qty` | double | 입고 수량 |
| `uom` | string | 단위 |
| `sample_size` | int | 샘플 수 |
| `inspection_items_ko` | string | 검사 항목. 예 "순도/입도/외관" |
| `judgment` | string | 합격/불합격/특채 |
| `defect_code` | string `FK` | 불합격 시 불량코드 |
| `coa_received` | boolean | 성적서 수령 여부 |
| `coa_conformance` | string | 일치/불일치/미제출 |
| `inspector_id` | string `FK` | 검사원 |
| `inspection_date` | date | 검사일 |
| `remark_ko` | string | 비고 |

### `qms_nonconformance` — 95행

부적합 보고서(NCR)입니다. 공정검사·출하검사·입고검사·고객제기 네 경로에서 발생합니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `ncr_id` | string PK | 예 `NCR-2026-0001` |
| `ncr_source` | string | 공정검사/입고검사/출하검사/고객제기 |
| `inspection_id` | string `FK` | 검사 출처일 때 |
| `iqc_id` | string `FK` | 입고검사 출처일 때 |
| `lot_id` | string `MES` | 로트. 해당 시 |
| `product_code` | string `MES` | 제품. 해당 시 |
| `product_name` | string | 제품명 라벨 |
| `step_code` | string `MES` | 공정. 해당 시 |
| `step_name` | string | 공정명 라벨 |
| `material_code` | string `MES` | 자재. 해당 시 |
| `eqp_id` | string `MES` | 설비. 해당 시 |
| `defect_code` | string `FK` | QMS 세부 불량코드 |
| `mes_defect_code` | string `MES` | MES 상위 불량코드. 양방향 조회용 |
| `severity` | string | Critical/Major/Minor |
| `affected_qty` | int | 영향 수량 |
| `detected_date` | date | 검출일 |
| `root_cause_category` | string | 설비/자재/작업방법/환경/측정 |
| `root_cause_ko` | string | 5-why 요약 |
| `immediate_action_ko` | string | 즉시 조치 |
| `owner_dept_ko` | string | 담당 부서 |
| `owner_name` | string | 담당자 |
| `status` | string | 접수/조사중/처리대기/완료/보류 |
| `due_date` | date | 완료 기한 |
| `closed_date` | date | 종결일 |
| `estimated_cost_krw` | long | 추정 손실액 |

### `qms_disposition` — 95행

부적합 처리 결정입니다. NCR과 1:1이며, 특채(예외 승인)는 MRB 심의를 거칩니다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `disposition_id` | string PK | 예 `DSP-2026-0001` |
| `ncr_id` | string `FK` | 부적합 |
| `lot_id` | string `MES` | 하향 전개 조인키 |
| `product_code` | string `MES` | 하향 전개 조인키 |
| `step_code` | string `MES` | 하향 전개 조인키 |
| `disposition_type` | string | 재작업/폐기/특채/반품/선별 |
| `disposition_qty` | int | 처리 수량 |
| `decision_date` | date | 결정일 |
| `decision_body_ko` | string | MRB/품질책임자/생산책임자 |
| `approver_name` | string | 승인자 |
| `approval_status` | string | 승인/반려/대기 |
| `rework_step_code` | string `MES` | 재작업 복귀 공정 |
| `rework_result` | string | 성공/실패/진행중 |
| `scrap_cost_krw` | long | 폐기 비용 |
| `effectiveness_check_date` | date | 유효성 확인일 |
| `effectiveness_result` | string | 유효/재발/확인중 |
| `reason_ko` | string | 처리 사유 |

## 6. 데이터 생성 로직

난수로 생성하면 "MES는 Pass인데 QMS는 폐기"같은 모순이 발생한다. 그래서 MES 91건 이력을
생성 앵커로 사용한다.

## 시간축 — 질의 전에 반드시 알아야 할 것

이 데이터의 **"지금"은 MES 공정이력의 마지막 종료 시각**입니다. QMS에는 고정 날짜가
없고 모든 시각이 그 기준점에서 유도됩니다. MES를 재배포하면 QMS 데이터도 통째로
따라 이동합니다.

**이미 일어난 일은 전부 "지금" 이하입니다.**
`inspection_datetime`, `measured_at`, `receipt_date`, `inspection_date`,
`detected_date`, `closed_date`, `decision_date`

**아직 오지 않은 일은 미래에 있습니다. 이상한 값이 아닙니다.**
`due_date`(조치 기한), `effectiveness_check_date`(유효성 점검 예정일)

여기서 나오는 질문들입니다.

- "기한이 지났는데 아직 종결 안 된 부적합" → `due_date < 지금 AND closed_date IS NULL`
- "출하검사 대기 로트" → MES에서 아직 Done이 아닌 로트. `qms_inspection`에 OQC가 없습니다
- "최근 검사 결과" → 가장 늦은 `inspection_datetime` 부근. 그보다 뒤의 데이터는 없습니다

출하검사(OQC)는 **로트마다 생산이 끝난 뒤 4~24시간 안에** 이뤄집니다. 전역 날짜가
아니므로 로트별로 시점이 다릅니다. 생산 중인 로트(Running·Hold)에는 OQC가 없습니다.

## null 이 정상인 경우

빈 값을 결측으로 오해하면 안 됩니다. 다음은 설계상 당연히 비어 있습니다.

| 위치 | 비는 이유 |
|---|---|
| `qms_inspection.lot_id` (PCS·EQV) | 공정능력조사와 설비검증은 특정 로트를 대상으로 하지 않습니다 |
| `qms_inspection.product_code` (EQV) | 설비검증은 제품과 무관합니다 |
| `qms_inspection.mes_process_result_id` (OQC·PCS·EQV) | MES 공정이력에 대응하는 사건이 없습니다 |
| `qms_nonconformance.lot_id`·`step_code` (고객제기) | 고객 클레임은 공정 시점이 특정되지 않습니다 |
| `qms_nonconformance.closed_date` (미종결 건) | 아직 종결되지 않았습니다. 종결 예정일이 "지금"을 넘는 건은 조사중으로 남습니다 |
| `qms_incoming_inspection.defect_code` (합격 건) | 결함이 없으니 코드도 없습니다 |

## 검사 유형 다섯 가지

| 유형 | 건수 | 뜻 |
|---|---|---|
| `IPQC` | 91 | 공정 중 검사. MES 공정이력 1건마다 1건 |
| `IPQC-RT` | 40 | 재검사. MES가 불량코드를 남겼거나 Pass가 아니었던 건 |
| `OQC` | 24 | 출하검사. 완료 로트 6개를 배치 4개로 나눔 |
| `PCS` | 36 | 정기 공정능력 조사. 제품 4 × 공정 9 |
| `EQV` | 16 | 설비 정기 검증. 설비 8 × 2회 |

측정치를 남기는 것은 `IPQC-RT`, `PCS`, `EQV` 뿐입니다. `IPQC`와 `OQC`는 판정만
기록합니다.

## 두 시스템을 함께 봐야만 답이 나오는 질문 13건

이 데이터에는 MES와 QMS가 서로 다른 말을 하는 상황이 의도적으로 심겨 있습니다.
표시용 컬럼은 없습니다. 값 조건만으로 찾아야 합니다.

| 장치 | 건수 | 질문 | 판별 조건 |
|---|---|---|---|
| ① MES Pass ↔ QMS 불합격 | 3 | 생산은 통과했는데 품질이 세운 로트는? | `qms_inspection.judgment='불합격'` 이고 `defect_found_qty=0`, 해당 MES 이력은 `Pass`이고 불량코드 없음. 연결 NCR의 `root_cause_category='측정'` |
| ② MES Fail ↔ QMS 특채 | 2 | 불합격인데 출하가 승인된 로트와 그 근거는? | `qms_disposition.disposition_type='특채'`, 연결 검사의 MES 이력이 `Fail` |
| ③ MES 불량코드 없음 ↔ QMS 결함 검출 | 4 | 설비는 못 잡고 검사가 잡은 불량은? | `qms_inspection.defect_found_qty>0` 이고 `judgment != '불합격'`, MES 이력에 불량코드 없음 |
| ④ IQC 불합격 자재가 투입됨 | 2 | 불합격난 자재가 들어간 로트는? | `qms_nonconformance.ncr_source='입고검사'` 이고 `lot_id` 가 채워짐. `material_code`로 MES BOM을 거쳐 제품·공정 추적 |
| ⑤ MES Rework ↔ 재작업 실패 후 폐기 | 2 | 재작업했지만 결국 폐기된 로트는? | `qms_disposition.rework_result='실패'` 이고 `disposition_type='폐기'` |

## 자주 쓰는 조인 경로

```sql
-- 로트 하나의 품질 이력 전체
SELECT i.lot_id, i.step_code, i.inspection_type, i.judgment,
       n.ncr_id, n.defect_code, d.disposition_type
FROM qms_inspection i
LEFT JOIN qms_nonconformance n ON n.inspection_id = i.inspection_id
LEFT JOIN qms_disposition d ON d.ncr_id = n.ncr_id
WHERE i.lot_id = 'LOT0011'
ORDER BY i.inspection_datetime;

-- 공정·특성별 규격 이탈률
SELECT step_code, characteristic_code,
       COUNT(*) AS measured,
       SUM(CASE WHEN is_out_of_spec THEN 1 ELSE 0 END) AS oos
FROM qms_measurement
GROUP BY step_code, characteristic_code
ORDER BY oos DESC;

-- 공급업체별 입고 불합격률
SELECT supplier_name_ko,
       COUNT(*) AS lots,
       SUM(CASE WHEN judgment = '불합격' THEN 1 ELSE 0 END) AS rejected
FROM qms_incoming_inspection
GROUP BY supplier_name_ko
ORDER BY rejected DESC;

-- 부적합 발생 경로별 처리 결과
SELECT n.ncr_source, d.disposition_type, COUNT(*) AS cases
FROM qms_nonconformance n
JOIN qms_disposition d ON d.ncr_id = n.ncr_id
GROUP BY n.ncr_source, d.disposition_type
ORDER BY cases DESC;
```
