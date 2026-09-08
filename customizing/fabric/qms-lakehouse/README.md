# QMS 가상 데이터 시드

제조 현장의 품질관리(QMS) 데이터를 Microsoft Fabric 레이크하우스에 만들어 넣습니다.
데이터는 Mock MES를 실시간으로 조회해 그와 정합되도록 생성됩니다.

## 무엇이 만들어지나

8개 Delta 테이블, 합계 1,004행입니다.

| 테이블 | 행수 | 내용 |
|---|---|---|
| `qms_defect_code` | 24 | MES 상위 불량코드 6종을 전개한 QMS 세부 불량코드 |
| `qms_inspection_spec` | 108 | 제품·공정·특성별 검사 기준과 규격 |
| `qms_inspector` | 15 | 검사원. MES 작업자와 별개 인력 |
| `qms_inspection` | 207 | 공정검사·재검사·출하검사·공정능력조사·설비검증 |
| `qms_measurement` | 260 | 규격 대비 실측값 |
| `qms_incoming_inspection` | 200 | 자재 입고검사 |
| `qms_nonconformance` | 95 | 부적합 보고서 |
| `qms_disposition` | 95 | 부적합 처리 결정 |

## MES와의 관계

두 시스템은 별개입니다. 외래키가 아니라 `lot_id`, `product_code`, `step_code`,
`material_code` 라는 비즈니스 키로만 이어집니다. 같은 사실을 양쪽이 중복해서 갖지
않으므로 QMS에는 `mes_result`, `scrap_qty`, `operator`, `in_qty`, `out_qty` 컬럼이
없습니다. 생산이 어떻게 됐는지 알고 싶으면 MES에 물어야 합니다.

이 데이터에는 두 시스템을 함께 조회해야만 풀리는 상황이 13건 의도적으로 심겨 있습니다.
자세한 내용은 `data-agent-schema.md`를 보세요.

## 실행

### 1. MES API 키 준비

노트북 파라미터 셀의 `MES_API_KEY`에 키를 넣습니다. 작업 영역이 공유될 수 있고
Fabric 노트북은 자동 저장되므로, 실행이 끝나면 지우고 저장하세요.

Fabric 데이터 파이프라인으로 돌린다면 그 셀에 `parameters` 태그가 붙어 있으므로
Notebook 액티비티에서 런타임에 값을 넘길 수 있습니다. 그러면 노트북에는 키가
남지 않습니다.

### 2. 노트북 빌드

```bash
python3 build_notebook.py
```

`qms_lakehouse_seed.ipynb`가 생성됩니다. `src/` 모듈이 셀로 인라인되어 있어
파일 업로드나 `pip install` 없이 단독 실행됩니다.

### 3. Fabric에 업로드하고 실행

노트북을 업로드한 뒤 **오른쪽 패널에서 대상 레이크하우스를 Attach** 합니다. 적재
위치는 이 Attach 로 정해집니다. 파라미터 셀에 레이크하우스 이름을 적는 자리는 없습니다 —
`saveAsTable`에 `이름.테이블` 같은 2단 이름을 주면 Spark가 그것을 `스키마.테이블`로
해석해 실패하기 때문입니다. 스키마 사용 레이크하우스라면 `TARGET_SCHEMA`만 채웁니다.

그다음 전체 실행합니다. 고정 시드와 `overwrite` 모드를 쓰므로 몇 번을 다시 돌려도
결과가 같습니다.

노트북은 적재 전에 9개 항목을 검증합니다. 고아 키, MES 중복 컬럼, 내부 참조 깨짐,
타임존 통일은 치명 항목이라 실패하면 적재하지 않고 중단합니다.

### 시각은 전부 MES에서 유도됩니다

QMS에는 날짜 상수가 없습니다. 모든 시각의 기준점은 MES 공정이력의 마지막 종료
시각(`mes_anchor`)이고, 나머지는 거기서 상대적으로 만들어집니다. MES 앵커가 배포
시점에 평가되므로, 벽시계 상수를 두면 MES를 재배포할 때 QMS만 제자리에 남아
출하검사가 생산 시작보다 앞서는 상태가 됩니다.

모든 `TIMESTAMP` 값은 tz-aware UTC입니다. naive 시각이 섞이면 PySpark가 그것만
드라이버 로컬 타임존으로 저장해, 적재는 성공하는데 일부 행의 값만 밀립니다.

> **MES를 재배포하면** `tests/fixtures/mes_snapshot.json`을 다시 떠야 합니다.
> 픽스처는 오프라인 테스트의 기반일 뿐이고 노트북 실행에는 쓰이지 않으므로,
> 재배포 직후에도 노트북은 실 MES를 조회해 정상 동작합니다.

## 개발

```bash
python3 -m pytest -q                 # 오프라인 테스트 전체
MES_API_KEY=<키> python3 -m pytest -m live -q   # 실 MES 접속 테스트
```

오프라인 테스트는 `tests/fixtures/mes_snapshot.json`에 저장된 실 MES 스냅샷 위에서
돕니다. MES가 바뀌면 스냅샷을 다시 뜨고 live 테스트로 기대값을 갱신하세요.

`src/`가 원본이고 노트북은 빌드 산출물입니다. 노트북을 직접 고치지 마세요.
`src/`를 고치고 `python3 build_notebook.py`를 다시 실행합니다.

## 다음 단계

이 레이크하우스로 Fabric Data Agent를 만들고, `data-agent-schema.md`를 에이전트
지식으로 넣습니다. 그다음 Foundry 에이전트에 이 Data Agent와 MES MCP 엔드포인트를
함께 붙이면 두 시스템에 걸친 질문에 답할 수 있습니다.
