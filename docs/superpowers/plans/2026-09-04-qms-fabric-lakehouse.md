# QMS Fabric Lakehouse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mock MES를 실시간 조회해 그와 무중복으로 맞물리는 가상 QMS 데이터 1,004행(8테이블)을 생성하고 Microsoft Fabric 레이크하우스에 적재하는, 단독 실행 가능한 노트북을 만든다.

**Architecture:** 생성 로직 전체를 표준 라이브러리 기반 순수 Python 모듈(`src/`)로 작성해 로컬에서 pytest로 TDD한다. `build_notebook.py`가 그 모듈 소스를 노트북 셀로 인라인 전개해 `qms_lakehouse_seed.ipynb`를 만든다. Fabric에 올리는 산출물은 파일 업로드나 `pip install` 없이 단독 실행되는 `.ipynb` 하나이며, 동시에 모든 로직이 pytest로 검증된 상태가 된다. Spark 의존 코드는 노트북 정적 셀에만 존재하고 로직 모듈에는 없다.

**Tech Stack:** Python 3.10+ 표준 라이브러리만(`urllib`, `json`, `random`, `datetime`, `dataclasses`, `re`), 테스트는 pytest, 노트북 조립은 nbformat, 런타임은 Fabric 기본 제공 PySpark.

## Global Constraints

이 절의 값은 모든 태스크의 요구사항에 암묵적으로 포함된다. 스펙 `docs/superpowers/specs/2026-09-04-qms-fabric-lakehouse-design.md`에서 그대로 옮긴 값이다.

- **작업 루트**: `customizing/fabric/qms-lakehouse/`. 이 디렉터리 밖의 파일은 수정하지 않는다.
- **MES base URL**: `https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io`
- **API 키 하드코딩 금지**. 어떤 커밋 파일에도 키 문자열이 들어가면 안 된다. 로컬 테스트는 환경변수 `MES_API_KEY`, 노트북은 파라미터 셀 → Key Vault → 환경변수 순으로 조달한다.
- **MCP 쓰기 툴 호출 금지**: `start_lot`, `register_process_result`는 어떤 코드에서도 호출하지 않는다. 읽기 전용 툴만 쓴다 — `list_lots`, `list_process_results`, `get_process_route`, `get_lot`, `get_wip`.
- **금지 컬럼**: QMS 어떤 테이블에도 `mes_result`, `scrap_qty`, `operator`, `in_qty`, `out_qty` 컬럼이 존재해선 안 된다. MES가 이미 보유한 사실을 QMS가 중복 보유하지 않는다는 설계 계약이다.
- **테이블 접두사**: `qms_`. 8개 테이블 — `qms_defect_code`, `qms_inspection_spec`, `qms_inspector`, `qms_inspection`, `qms_measurement`, `qms_incoming_inspection`, `qms_nonconformance`, `qms_disposition`.
- **적재 모드**: `overwrite`. 고정 시드와 함께 재실행 멱등성을 보장한다.
- **난수**: 전역 `random` 모듈 함수 사용 금지. 모듈마다 자기 시드로 만든 `random.Random` 인스턴스만 쓴다. 시드 상수는 `src/qms_reference.py`에 모은다.
- **의존성**: 로직 모듈은 표준 라이브러리만 import한다. `requests`, `pandas`, `mcp` 패키지 금지. `pyspark`는 노트북 정적 셀에서만 쓴다.
- **Spark 스키마**: `spark.createDataFrame`에 반드시 명시적 DDL 스키마를 넘긴다. 전부 `None`인 컬럼이 있어 타입 추론이 실패하기 때문이다.
- **한글**: 사용자 대면 문자열(불량명, 판정 근거, 원인, 조치)은 한글. 컬럼명·코드값은 영문.
- **Markdown**: ATX 스타일 헤더.

### 확정 행수

| 테이블 | 행수 | 산출식 |
|---|---|---|
| `qms_defect_code` | 24 | MES 불량코드 6종 × 세부 4 |
| `qms_inspection_spec` | 108 | 제품 4 × 공정 9 × 특성 3 |
| `qms_inspector` | 15 | 팀 5 × 교대 3 |
| `qms_inspection` | 207 | IPQC 91 + 재검사 40 + OQC 24 + PCS 36 + EQV 16 |
| `qms_measurement` | 260 | 재검사 40×3 + PCS 36×3 + EQV 16×2 |
| `qms_incoming_inspection` | 200 | 자재 12종 × 입고 회차 |
| `qms_nonconformance` | 95 | 스펙 6.4 배분표 |
| `qms_disposition` | 95 | NCR 1:1 |
| 합계 | 1,004 | |

### MES 실측 상수 (변경 금지)

- 제품 4: `DDR5`, `LX9`, `NAND`, `PMIC`
- 공정 9: `DIFF`, `PHOTO`, `ETCH`, `IMPL`, `CVD`, `CMP`, `METRO`, `TEST`, `PKG`
- 로트 16: `LOT0001`~`LOT0016`. Running 8 / Done 6 / Hold 2
- 공정이력 91: Pass 84 / Fail 5 / Rework 2. 결함코드 보유 35건
- 설비 8: 공정이력의 `eqp_id` 고유값에서 유도. `EQP-PKG01`은 미등장이라 제외
- MES 불량코드 6: `Particle`, `Scratch`, `Overlay`, `Etch-Residue`, `Contamination`, `CD-OOS`
- 자재 12종

### 앵커링 필수 — MES non-Pass 7건

| id | lot_id | step_code | result | defect_code |
|---|---|---|---|---|
| 27 | LOT0004 | ETCH | Fail | (없음) |
| 35 | LOT0005 | ETCH | Fail | (없음) |
| 63 | LOT0010 | ETCH | Rework | Particle |
| 64 | LOT0011 | DIFF | Rework | (없음) |
| 69 | LOT0011 | CMP | Fail | CD-OOS |
| 74 | LOT0012 | CVD | Fail | (없음) |
| 77 | LOT0013 | DIFF | Fail | (없음) |

### 의도적 불일치 장치 13건

검증 모듈이 데이터 질의만으로 검출한다. 이를 표시하는 전용 컬럼을 만들면 안 된다.

| # | 장치 | 건수 | 검출 조건 | 생성 태스크 |
|---|---|---|---|---|
| ① | MES Pass ↔ QMS 불합격(품질홀드) | 3 | MES `result=Pass` & `defect_code` null & `judgment='불합격'` & `defect_found_qty=0` & `root_cause_category='측정'` | 3 |
| ② | MES Fail ↔ QMS 특채 | 2 | 연결된 MES `result=Fail` & `disposition_type='특채'` | 6 |
| ③ | MES 불량코드 없음 ↔ QMS 결함 검출 | 4 | MES `defect_code` null & `defect_found_qty>0` & `judgment != '불합격'` | 3 |
| ④ | IQC 불합격 자재가 투입됨 | 2 | `ncr_source='입고검사'` & `lot_id` not null | 6 |
| ⑤ | MES Rework ↔ 재작업 실패 후 폐기 | 2 | `rework_result='실패'` & `disposition_type='폐기'` | 6 |

①과 ③은 서로소여야 한다. `defect_found_qty`가 0이냐 0 초과냐로 갈린다. 둘 다 MES `Pass`+결함없음 51건 풀에서 뽑되 겹치지 않는 슬라이스를 쓴다.

---
## File Structure

```
customizing/fabric/qms-lakehouse/
├── README.md                     설치·실행·Data Agent 연결 가이드          (T9)
├── data-agent-schema.md          Data Agent 지식용 스키마 설명서            (T9)
├── pyproject.toml                pytest 설정 (marker, testpaths)           (T1)
├── build_notebook.py             src/ 모듈 → .ipynb 조립                   (T8)
├── qms_lakehouse_seed.ipynb      빌드 산출물. Fabric 업로드 대상            (T8)
├── src/
│   ├── __init__.py                                                        (T1)
│   ├── mes_client.py             MES REST+MCP 클라이언트, MesSnapshot       (T1)
│   ├── qms_reference.py          시드·특성·불량분류·공급사 등 상수 전체      (T2)
│   ├── qms_masters.py            불량코드 24 / 검사기준 108 / 검사원 15     (T2)
│   ├── qms_inspection.py         검사 207 (장치 ①③ 포함)                   (T3)
│   ├── qms_measurement.py        측정치 260                                (T4)
│   ├── qms_incoming.py           입고검사 200                              (T5)
│   ├── qms_nonconformance.py     NCR 95 + 처리 95 (장치 ②④⑤)              (T6)
│   ├── qms_schema.py             테이블별 컬럼·Spark DDL                    (T8)
│   └── qms_validate.py           검증 8항목                                (T7)
└── tests/
    ├── conftest.py               fixture 로더                              (T1)
    ├── fixtures/mes_snapshot.json  실 MES 스냅샷. 오프라인 테스트용         (T1)
    ├── test_mes_client.py                                                  (T1)
    ├── test_qms_masters.py                                                 (T2)
    ├── test_qms_inspection.py                                              (T3)
    ├── test_qms_measurement.py                                             (T4)
    ├── test_qms_incoming.py                                                (T5)
    ├── test_qms_nonconformance.py                                          (T6)
    ├── test_qms_validate.py                                                (T7)
    └── test_build_notebook.py                                              (T8)
```

`src/`는 모듈당 하나의 테이블군만 책임진다. 모든 생성 함수는 `MesSnapshot`과 앞선 태스크의 산출 리스트만 입력으로 받는 순수 함수이며, 네트워크에 접근하지 않는다. 네트워크는 `mes_client.py`에만 있다. 이 분리 덕분에 태스크 2~7의 테스트는 고정 fixture로 오프라인·결정론적으로 돈다.

**모든 생성 함수는 `list[dict]`를 반환한다.** dict의 키 집합과 순서는 `qms_schema.py`의 컬럼 정의와 정확히 일치해야 하며, 태스크 8이 이를 테스트로 강제한다.

---

### Task 1: 스캐폴드와 MES 클라이언트

MES의 REST와 MCP 두 채널을 하나의 `MesSnapshot`으로 모으는 클라이언트를 만든다. 이후 모든 태스크가 이 스냅샷 위에서 동작한다.

**Files:**
- Create: `customizing/fabric/qms-lakehouse/pyproject.toml`
- Create: `customizing/fabric/qms-lakehouse/src/__init__.py`
- Create: `customizing/fabric/qms-lakehouse/src/mes_client.py`
- Create: `customizing/fabric/qms-lakehouse/tests/conftest.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_mes_client.py`
- Create: `customizing/fabric/qms-lakehouse/tests/fixtures/mes_snapshot.json` (스텝 9에서 실 MES 호출로 생성)

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `MesSnapshot` — frozen dataclass. 필드: `products: list[dict]`, `materials: list[dict]`, `bom: list[dict]`, `lots: list[dict]`, `process_results: list[dict]`, `route: list[dict]`, `equipment: list[dict]`
  - `MesSnapshot.to_dict() -> dict[str, list[dict]]` / `MesSnapshot.from_dict(d: dict) -> MesSnapshot`
  - `parse_mcp_body(raw: str) -> dict | None` — SSE 또는 순수 JSON 본문 파서
  - `derive_equipment(process_results: list[dict], route: list[dict]) -> list[dict]` — 각 원소 키 `eqp_id`, `eqp_type`, `step_code`
  - `MesClient(base_url: str, api_key: str, timeout: int = 60)` — 메서드 `rest(path: str) -> Any`, `mcp_call(tool: str, args: dict | None = None) -> Any`, `fetch_snapshot() -> MesSnapshot`
  - `MES_BASE_URL: str` 상수

- [ ] **Step 1: 디렉터리와 pytest 설정 생성**

```bash
cd customizing/fabric/qms-lakehouse 2>/dev/null || mkdir -p customizing/fabric/qms-lakehouse
cd "$(git rev-parse --show-toplevel)/customizing/fabric/qms-lakehouse"
mkdir -p src tests/fixtures
touch src/__init__.py
```

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = [
    "live: MES 서버에 실제로 접속하는 테스트. MES_API_KEY 필요",
]
addopts = "-m 'not live'"
```

`addopts`로 기본 실행에서 `live` 테스트를 제외한다. 실행하려면 `-m live`를 명시한다.

- [ ] **Step 2: SSE 파서 실패 테스트 작성**

`tests/test_mes_client.py`:

```python
import pytest

from src.mes_client import parse_mcp_body


def test_parse_plain_json_body():
    assert parse_mcp_body('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}') == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }


def test_parse_sse_body_starting_with_event_line():
    # 실제 mock-mes 응답 형태. 'event: message'가 먼저 오고 CRLF로 끝난다.
    raw = 'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\r\n\r\n'
    assert parse_mcp_body(raw) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_parse_multiline_sse_data_is_concatenated():
    raw = 'event: message\r\ndata: {"a":\r\ndata: 1}\r\n\r\n'
    assert parse_mcp_body(raw) == {"a": 1}


def test_parse_empty_body_returns_none():
    # notifications/initialized 는 본문 없는 202를 돌려준다.
    assert parse_mcp_body("") is None
    assert parse_mcp_body("   \r\n") is None
```

`event: message`로 시작하는 케이스가 핵심이다. 본문이 `data:`로 시작한다고 가정하는 파서는 이 서버에서 반드시 실패한다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_mes_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.mes_client'`

- [ ] **Step 4: 파서 구현**

`src/mes_client.py`:

```python
"""Mock MES 접속 클라이언트.

REST(/api)와 MCP(/mcp) 두 채널을 하나의 MesSnapshot으로 모은다.
표준 라이브러리만 사용한다. Fabric 노트북에 그대로 인라인되기 때문에
추가 패키지 설치가 있어선 안 된다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

MES_BASE_URL = "https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io"


def parse_mcp_body(raw: str) -> dict | None:
    """MCP 응답 본문을 파싱한다.

    이 서버는 Accept 헤더에 text/event-stream이 있으면 SSE로 답한다.
    본문은 'event: message'로 시작하므로 'data:' 접두 검사만으로는
    SSE를 인식할 수 없다. 순수 JSON을 먼저 시도하고 실패하면 data 행을 모은다.
    """
    body = raw.strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    chunks = [
        line[len("data:"):].strip()
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    if not chunks:
        raise ValueError(f"MCP 응답을 해석할 수 없습니다: {body[:200]!r}")
    return json.loads("".join(chunks))
```

- [ ] **Step 5: 파서 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_mes_client.py -q`
Expected: PASS — `4 passed`

- [ ] **Step 6: 설비 유도 함수 테스트 작성**

`tests/test_mes_client.py`에 추가:

```python
from src.mes_client import derive_equipment


def test_derive_equipment_from_distinct_eqp_ids():
    process_results = [
        {"eqp_id": "EQP-DIFF01", "step_code": "DIFF"},
        {"eqp_id": "EQP-DIFF01", "step_code": "DIFF"},
        {"eqp_id": "EQP-PHOT02", "step_code": "PHOTO"},
    ]
    route = [
        {"step_code": "DIFF", "eqp_type": "Furnace"},
        {"step_code": "PHOTO", "eqp_type": "Scanner"},
        {"step_code": "PKG", "eqp_type": "Bonder"},
    ]
    assert derive_equipment(process_results, route) == [
        {"eqp_id": "EQP-DIFF01", "eqp_type": "Furnace", "step_code": "DIFF"},
        {"eqp_id": "EQP-PHOT02", "eqp_type": "Scanner", "step_code": "PHOTO"},
    ]


def test_derive_equipment_skips_rows_without_eqp_id():
    process_results = [{"eqp_id": None, "step_code": "DIFF"}, {"step_code": "ETCH"}]
    route = [{"step_code": "DIFF", "eqp_type": "Furnace"}]
    assert derive_equipment(process_results, route) == []


def test_derive_equipment_is_sorted_by_eqp_id():
    process_results = [
        {"eqp_id": "EQP-TEST01", "step_code": "TEST"},
        {"eqp_id": "EQP-CMP01", "step_code": "CMP"},
    ]
    route = [
        {"step_code": "TEST", "eqp_type": "Prober"},
        {"step_code": "CMP", "eqp_type": "Polisher"},
    ]
    assert [e["eqp_id"] for e in derive_equipment(process_results, route)] == [
        "EQP-CMP01",
        "EQP-TEST01",
    ]
```

정렬을 명시적으로 요구하는 이유는 결정론 때문이다. 집합 순회 순서에 의존하면 재실행마다 EQV 검사 ID 배정이 달라진다.

- [ ] **Step 7: 테스트 실패 확인 후 구현**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_mes_client.py -q`
Expected: FAIL — `ImportError: cannot import name 'derive_equipment'`

`src/mes_client.py`에 추가:

```python
def derive_equipment(process_results: list[dict], route: list[dict]) -> list[dict]:
    """공정이력의 eqp_id 고유값에서 설비 목록을 만든다.

    MES는 설비 마스터를 REST에도 MCP에도 노출하지 않는다. /equipment 웹 페이지에만
    있지만 HTML 파싱은 페이지 구조 변경에 취약하므로 쓰지 않는다.
    PKG 단계에 도달한 로트가 없어 EQP-PKG01은 여기서 빠진다.
    """
    eqp_type_by_step = {s["step_code"]: s.get("eqp_type") for s in route}
    first_step: dict[str, str] = {}
    for row in process_results:
        eqp_id = row.get("eqp_id")
        if eqp_id and eqp_id not in first_step:
            first_step[eqp_id] = row["step_code"]
    return [
        {
            "eqp_id": eqp_id,
            "eqp_type": eqp_type_by_step.get(first_step[eqp_id]),
            "step_code": first_step[eqp_id],
        }
        for eqp_id in sorted(first_step)
    ]
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_mes_client.py -q`
Expected: PASS — `7 passed`

- [ ] **Step 9: MesSnapshot과 MesClient 구현**

`src/mes_client.py`에 추가:

```python
_SNAPSHOT_FIELDS = (
    "products",
    "materials",
    "bom",
    "lots",
    "process_results",
    "route",
    "equipment",
)


@dataclass(frozen=True)
class MesSnapshot:
    """QMS 생성기 전체의 유일한 입력. 네트워크 계층과 생성 계층의 경계다."""

    products: list[dict] = field(default_factory=list)
    materials: list[dict] = field(default_factory=list)
    bom: list[dict] = field(default_factory=list)
    lots: list[dict] = field(default_factory=list)
    process_results: list[dict] = field(default_factory=list)
    route: list[dict] = field(default_factory=list)
    equipment: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[dict]]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "MesSnapshot":
        return cls(**{name: payload[name] for name in _SNAPSHOT_FIELDS})


class MesApiKeyMissing(RuntimeError):
    """API 키가 조달되지 않았을 때. 노트북 게이트 셀이 이 예외를 잡아 안내한다."""


class MesClient:
    def __init__(self, base_url: str = MES_BASE_URL, api_key: str = "", timeout: int = 60):
        if not api_key:
            raise MesApiKeyMissing(
                "MES_API_KEY가 비어 있습니다. 노트북 파라미터, Key Vault, "
                "환경변수 MES_API_KEY 중 하나로 공급하세요."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._rpc_id = 0
        self._initialized = False

    def _open(self, url: str, data: bytes | None, headers: dict) -> str:
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data else "GET"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def rest(self, path: str) -> Any:
        """REST GET. path 예: '/api/products'"""
        raw = self._open(
            self.base_url + path,
            None,
            {"X-API-Key": self.api_key, "Accept": "application/json"},
        )
        return json.loads(raw)

    def _rpc(self, method: str, params: dict, notify: bool = False) -> dict | None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            self._rpc_id += 1
            payload["id"] = self._rpc_id
        raw = self._open(
            self.base_url + "/mcp",
            json.dumps(payload).encode("utf-8"),
            {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-API-Key": self.api_key,
            },
        )
        return parse_mcp_body(raw)

    def _handshake(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qms-seeder", "version": "1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True

    def mcp_call(self, tool: str, args: dict | None = None) -> Any:
        """읽기 전용 MCP 툴 호출. 결과 리스트를 그대로 돌려준다."""
        if tool in {"start_lot", "register_process_result"}:
            raise ValueError(f"쓰기 툴 호출 금지: {tool}")
        self._handshake()
        response = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        if response is None:
            raise RuntimeError(f"MCP 툴 {tool} 응답이 비어 있습니다.")
        if "error" in response:
            raise RuntimeError(f"MCP 툴 {tool} 오류: {response['error']}")
        result = response["result"]
        if result.get("isError"):
            raise RuntimeError(f"MCP 툴 {tool} 실패: {result.get('content')}")
        return result["structuredContent"]["result"]

    def fetch_snapshot(self) -> MesSnapshot:
        process_results = self.mcp_call("list_process_results", {"limit": 500})
        route = self.mcp_call("get_process_route")
        return MesSnapshot(
            products=self.rest("/api/products"),
            materials=self.rest("/api/materials"),
            bom=self.rest("/api/bom"),
            lots=self.mcp_call("list_lots", {"limit": 500}),
            process_results=process_results,
            route=route,
            equipment=derive_equipment(process_results, route),
        )
```

`limit`을 500으로 두는 이유는 두 툴의 기본값이 100이기 때문이다. 현재 데이터는 91건이라 기본값으로도 충분하지만, MES에 데이터가 늘어나면 조용히 잘린다.

- [ ] **Step 10: 쓰기 툴 차단 테스트 추가 후 통과 확인**

`tests/test_mes_client.py`에 추가:

```python
from src.mes_client import MesApiKeyMissing, MesClient, MesSnapshot


def test_client_requires_api_key():
    with pytest.raises(MesApiKeyMissing):
        MesClient(api_key="")


@pytest.mark.parametrize("tool", ["start_lot", "register_process_result"])
def test_write_tools_are_rejected(tool):
    client = MesClient(api_key="dummy")
    with pytest.raises(ValueError, match="쓰기 툴 호출 금지"):
        client.mcp_call(tool, {})


def test_snapshot_roundtrips_through_dict():
    snapshot = MesSnapshot(products=[{"product_code": "DDR5"}])
    assert MesSnapshot.from_dict(snapshot.to_dict()) == snapshot
```

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_mes_client.py -q`
Expected: PASS — `11 passed`

- [ ] **Step 11: 실 MES 통합 테스트 작성**

`tests/test_mes_client.py`에 추가:

```python
import os

from src.mes_client import MES_BASE_URL


@pytest.mark.live
def test_live_snapshot_matches_known_mes_state():
    api_key = os.environ.get("MES_API_KEY")
    assert api_key, "MES_API_KEY 환경변수가 필요합니다."
    snapshot = MesClient(MES_BASE_URL, api_key).fetch_snapshot()
    assert len(snapshot.products) == 4
    assert len(snapshot.materials) == 12
    assert len(snapshot.bom) == 48
    assert len(snapshot.lots) == 16
    assert len(snapshot.process_results) == 91
    assert len(snapshot.route) == 9
    assert len(snapshot.equipment) == 8
    assert sum(1 for r in snapshot.process_results if r["result"] == "Fail") == 5
    assert sum(1 for r in snapshot.process_results if r["result"] == "Rework") == 2
```

- [ ] **Step 12: 실 MES에 대해 통합 테스트 실행**

Run:
```bash
cd customizing/fabric/qms-lakehouse
MES_API_KEY=<키> python -m pytest tests/test_mes_client.py -m live -q
```
Expected: PASS — `1 passed`

키가 없으면 assert 메시지와 함께 실패한다. 키는 커밋 대상 파일에 절대 넣지 않는다.

- [ ] **Step 13: 오프라인 fixture 생성과 conftest 작성**

```bash
cd customizing/fabric/qms-lakehouse
MES_API_KEY=<키> python -c "
import json
from src.mes_client import MES_BASE_URL, MesClient
import os
snap = MesClient(MES_BASE_URL, os.environ['MES_API_KEY']).fetch_snapshot()
with open('tests/fixtures/mes_snapshot.json', 'w', encoding='utf-8') as fh:
    json.dump(snap.to_dict(), fh, ensure_ascii=False, indent=1, sort_keys=True)
print({k: len(v) for k, v in snap.to_dict().items()})
"
```
Expected: `{'bom': 48, 'equipment': 8, 'lots': 16, 'materials': 12, 'process_results': 91, 'products': 4, 'route': 9}`

`tests/conftest.py`:

```python
import json
from pathlib import Path

import pytest

from src.mes_client import MesSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "mes_snapshot.json"


@pytest.fixture(scope="session")
def snapshot() -> MesSnapshot:
    """실 MES에서 뜬 고정 스냅샷. 태스크 2~8의 모든 테스트가 이 위에서 돈다."""
    with FIXTURE.open(encoding="utf-8") as fh:
        return MesSnapshot.from_dict(json.load(fh))
```

- [ ] **Step 14: fixture 무결성 테스트 추가 후 전체 실행**

`tests/test_mes_client.py`에 추가:

```python
def test_fixture_matches_live_mes_state(snapshot):
    assert len(snapshot.products) == 4
    assert len(snapshot.lots) == 16
    assert len(snapshot.process_results) == 91
    assert len(snapshot.equipment) == 8
    assert [e["eqp_id"] for e in snapshot.equipment] == [
        "EQP-CMP01",
        "EQP-CVD01",
        "EQP-DIFF01",
        "EQP-ETCH01",
        "EQP-IMPL01",
        "EQP-PHOT01",
        "EQP-PHOT02",
        "EQP-TEST01",
    ]
```

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest -q`
Expected: PASS — `12 passed` (live 1건은 `addopts`로 제외됨)

- [ ] **Step 15: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Add MES REST and MCP client for QMS seeding

The mock MES splits its data across two channels: products, materials and
BOM come from REST, while lots, process results and the process route are
only reachable through MCP. MesClient merges both into one MesSnapshot so
the generators never touch the network.

Two details the server forces on us: its MCP responses are SSE bodies that
open with an 'event: message' line, so the parser cannot test for a 'data:'
prefix, and it publishes no equipment master at all, so equipment is derived
from distinct eqp_id values in the process history."
```

---
### Task 2: 참조 상수와 마스터 3종

QMS 도메인 상수를 한곳에 모으고, 그 위에서 불량코드 24행·검사기준 108행·검사원 15행을 만든다. 이 세 테이블은 MES를 거의 참조하지 않는 순수 QMS 마스터라 가장 먼저 안정화할 수 있다.

**Files:**
- Create: `customizing/fabric/qms-lakehouse/src/qms_reference.py`
- Create: `customizing/fabric/qms-lakehouse/src/qms_masters.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_qms_masters.py`

**Interfaces:**
- Consumes: `MesSnapshot` (Task 1)
- Produces:
  - 상수 — `BASE_DATE: datetime.date`, `SEED_MASTERS/SEED_INSPECTION/SEED_MEASUREMENT/SEED_INCOMING/SEED_NCR: int`, `CHARACTERISTIC_BASE: dict[str, tuple]`, `STEP_CHARACTERISTICS: dict[str, tuple[str, str, str]]`, `PRODUCT_CD_SCALE: dict[str, float]`, `DEFECT_TAXONOMY: tuple`, `DEFECT_DETAILS: tuple`, `SEVERITY_SCORE: dict[str, int]`, `METROLOGY_EQP: dict[str, str]`, `SUPPLIERS: tuple`, `INSPECTOR_TEAMS: tuple`, `MES_DEFECT_CODES: tuple`
  - `build_defect_codes() -> list[dict]` — 24행
  - `build_inspection_specs(snapshot: MesSnapshot) -> list[dict]` — 108행
  - `build_inspectors() -> list[dict]` — 15행
  - `spec_index(specs: list[dict]) -> dict[tuple[str, str, str], dict]` — `(product_code, step_code, characteristic_code)` → spec 행
  - `defect_codes_by_mes(defect_codes: list[dict]) -> dict[str, list[dict]]`

- [ ] **Step 1: 마스터 실패 테스트 작성**

`tests/test_qms_masters.py`:

```python
from src.qms_masters import (
    build_defect_codes,
    build_inspection_specs,
    build_inspectors,
    defect_codes_by_mes,
    spec_index,
)
from src.qms_reference import MES_DEFECT_CODES, STEP_CHARACTERISTICS

FORBIDDEN = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}


def test_defect_codes_expand_six_mes_codes_into_24():
    rows = build_defect_codes()
    assert len(rows) == 24
    assert len({r["defect_code"] for r in rows}) == 24
    assert {r["mes_defect_code"] for r in rows} == set(MES_DEFECT_CODES)
    by_mes = defect_codes_by_mes(rows)
    assert all(len(v) == 4 for v in by_mes.values())


def test_defect_code_severity_score_matches_severity():
    expected = {"Critical": 9, "Major": 6, "Minor": 3}
    for row in build_defect_codes():
        assert row["severity_score"] == expected[row["severity"]]


def test_defect_codes_carry_no_forbidden_columns():
    assert not (set(build_defect_codes()[0]) & FORBIDDEN)


def test_inspection_specs_are_product_by_step_by_characteristic(snapshot):
    rows = build_inspection_specs(snapshot)
    assert len(rows) == 108
    assert len({r["spec_id"] for r in rows}) == 108
    assert {r["product_code"] for r in rows} == {"DDR5", "LX9", "NAND", "PMIC"}
    for row in rows:
        assert row["characteristic_code"] in STEP_CHARACTERISTICS[row["step_code"]]
        assert row["lsl"] < row["target_value"] < row["usl"]
        assert row["cpk_target"] == 1.33


def test_finer_node_product_has_tighter_cd_spec(snapshot):
    index = spec_index(build_inspection_specs(snapshot))
    # LX9 는 5nm, PMIC 는 28nm. 선폭 목표치가 노드에 비례해야 한다.
    assert index[("LX9", "PHOTO", "CD")]["target_value"] < index[("PMIC", "PHOTO", "CD")]["target_value"]


def test_specs_carry_mes_labels(snapshot):
    index = spec_index(build_inspection_specs(snapshot))
    assert index[("DDR5", "PHOTO", "CD")]["step_name"] == "Photolithography"
    assert index[("DDR5", "PHOTO", "CD")]["product_name"] == "DDR5-16G"


def test_inspectors_are_five_teams_by_three_shifts():
    rows = build_inspectors()
    assert len(rows) == 15
    assert len({r["inspector_id"] for r in rows}) == 15
    assert len({r["team_ko"] for r in rows}) == 5
    assert {r["shift_code"] for r in rows} == {"A", "B", "C"}
    assert all(r["is_active"] for r in rows)


def test_inspector_names_do_not_collide_with_mes_operators(snapshot):
    mes_operators = {r.get("operator") for r in snapshot.process_results}
    names = {r["inspector_name"] for r in build_inspectors()}
    assert not (names & mes_operators)
    assert len(names) == 15


def test_masters_are_deterministic(snapshot):
    assert build_defect_codes() == build_defect_codes()
    assert build_inspectors() == build_inspectors()
    assert build_inspection_specs(snapshot) == build_inspection_specs(snapshot)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_masters.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.qms_masters'`

- [ ] **Step 3: 참조 상수 모듈 작성**

`src/qms_reference.py`:

```python
"""QMS 도메인 참조 상수.

MES에서 오지 않는 값은 전부 여기에 있다. 생성 모듈은 이 상수와 MesSnapshot만
읽고 동작하므로, 값을 바꾸면 결과가 어떻게 달라지는지 한곳에서 파악된다.
"""

from __future__ import annotations

import datetime as dt

BASE_DATE = dt.date(2026, 9, 4)

# 모듈마다 독립 시드를 둔다. 한 모듈의 난수 소비량이 바뀌어도
# 다른 모듈의 출력이 흔들리지 않게 하기 위함이다.
SEED_MASTERS = 20260904
SEED_INSPECTION = 20260905
SEED_MEASUREMENT = 20260906
SEED_INCOMING = 20260907
SEED_NCR = 20260908

MES_DEFECT_CODES = (
    "Particle",
    "Scratch",
    "Overlay",
    "Etch-Residue",
    "Contamination",
    "CD-OOS",
)

SEVERITY_SCORE = {"Critical": 9, "Major": 6, "Minor": 3}

# (mes_defect_code, 코드 접두, defect_category, 주 발생 공정 CSV)
DEFECT_TAXONOMY = (
    ("Particle", "PTC", "오염", "DIFF,CVD,CMP"),
    ("Scratch", "SCR", "외관", "CMP,PKG"),
    ("Overlay", "OVL", "패턴", "PHOTO,METRO"),
    ("Etch-Residue", "ETR", "막질", "ETCH,CVD"),
    ("Contamination", "CTM", "오염", "DIFF,IMPL,CVD"),
    ("CD-OOS", "CDO", "치수", "PHOTO,ETCH,METRO"),
)

# (mes_defect_code, 순번, 한글명, 영문명, severity, 표준원인, 표준조치)
DEFECT_DETAILS = (
    ("Particle", 1, "파티클 오염 0.12um 이상", "Particle contamination over 0.12um", "Critical", "챔버 내벽 박리물 낙하", "챔버 습식세정 및 시즈닝 재실시"),
    ("Particle", 2, "장비 유래 파티클", "Equipment-borne particle", "Major", "이송 로봇 마모 분진", "로봇 암 교체 및 파티클 카운트 재측정"),
    ("Particle", 3, "가스라인 유래 파티클", "Gas line particle", "Major", "가스 필터 수명 초과", "인라인 필터 교체 및 퍼지"),
    ("Particle", 4, "인체 유래 파티클", "Human-borne particle", "Minor", "방진복 착용 절차 미준수", "클린룸 입실 교육 및 에어샤워 점검"),
    ("Scratch", 1, "CMP 연마 스크래치", "CMP polish scratch", "Critical", "슬러리 내 응집 입자", "슬러리 필터 교체 및 유량 재설정"),
    ("Scratch", 2, "웨이퍼 핸들링 스크래치", "Wafer handling scratch", "Major", "척 표면 이물", "척 세정 및 진공압 점검"),
    ("Scratch", 3, "캐리어 접촉 흠집", "Carrier contact mark", "Minor", "FOUP 슬롯 변형", "FOUP 교체 및 정렬 보정"),
    ("Scratch", 4, "패키지 표면 손상", "Package surface damage", "Minor", "몰드 이형 불량", "이형제 도포량 조정"),
    ("Overlay", 1, "정렬도 X 방향 초과", "Overlay X out of tolerance", "Critical", "스테이지 열변형", "스캐너 열보정 및 재정렬"),
    ("Overlay", 2, "정렬도 Y 방향 초과", "Overlay Y out of tolerance", "Critical", "레티클 장착 편차", "레티클 재장착 및 얼라인 재수행"),
    ("Overlay", 3, "회전 성분 편차", "Rotation component deviation", "Major", "웨이퍼 노치 정렬 오차", "노치 얼라이너 캘리브레이션"),
    ("Overlay", 4, "배율 성분 편차", "Magnification deviation", "Minor", "렌즈 온도 드리프트", "렌즈 온도 제어 루프 재조정"),
    ("Etch-Residue", 1, "폴리머 잔류물", "Polymer residue", "Critical", "에천트 조성 이탈", "가스 유량비 재설정 및 챔버 컨디셔닝"),
    ("Etch-Residue", 2, "금속 잔류물", "Metal residue", "Major", "오버에치 시간 부족", "에치 타임 연장 및 EPD 신호 재검토"),
    ("Etch-Residue", 3, "하드마스크 잔류", "Hard mask residue", "Major", "스트립 공정 누락", "애싱 레시피 보완"),
    ("Etch-Residue", 4, "측벽 잔류물", "Sidewall residue", "Minor", "패시베이션 과다", "패시베이션 가스 비율 하향"),
    ("Contamination", 1, "금속 오염 Cu", "Metallic contamination Cu", "Critical", "금속 배선 공정 교차 오염", "전용 챔버 분리 및 웨이퍼 세정"),
    ("Contamination", 2, "유기물 오염", "Organic contamination", "Major", "포토레지스트 잔류", "UV 오존 세정 추가"),
    ("Contamination", 3, "수분 오염", "Moisture contamination", "Major", "로드락 퍼지 부족", "퍼지 시간 연장 및 진공도 확인"),
    ("Contamination", 4, "이온성 오염", "Ionic contamination", "Minor", "초순수 비저항 저하", "UPW 라인 재생 및 수질 재측정"),
    ("CD-OOS", 1, "선폭 상한 초과", "CD above upper limit", "Critical", "노광량 부족", "도즈 재설정 및 FEM 재평가"),
    ("CD-OOS", 2, "선폭 하한 미달", "CD below lower limit", "Critical", "현상 시간 과다", "현상 레시피 시간 단축"),
    ("CD-OOS", 3, "선폭 균일도 이탈", "CD uniformity out of spec", "Major", "핫플레이트 온도 편차", "베이크 플레이트 존별 온도 보정"),
    ("CD-OOS", 4, "라인 에지 러프니스", "Line edge roughness", "Minor", "레지스트 감도 편차", "레지스트 로트 교체 및 재평가"),
)

# characteristic_code -> (한글명, measurement_type, unit, target, lsl, usl)
CHARACTERISTIC_BASE = {
    "CD": ("선폭", "계량형", "nm", 45.0, 40.5, 49.5),
    "OVL": ("정렬도", "계량형", "nm", 2.0, 0.0, 4.0),
    "THK": ("막두께", "계량형", "um", 1.20, 1.10, 1.30),
    "PTC": ("파티클수", "계수형", "ea", 8.0, 0.0, 20.0),
    "RS": ("면저항", "계량형", "Ω·sq", 120.0, 108.0, 132.0),
    "WRP": ("휨", "계량형", "%", 0.35, 0.05, 0.80),
}

# 공정별 관리 특성 3종. 4제품 × 9공정 × 3특성 = 108 검사기준.
STEP_CHARACTERISTICS = {
    "DIFF": ("THK", "PTC", "RS"),
    "PHOTO": ("CD", "OVL", "PTC"),
    "ETCH": ("CD", "THK", "PTC"),
    "IMPL": ("RS", "PTC", "THK"),
    "CVD": ("THK", "PTC", "RS"),
    "CMP": ("THK", "WRP", "PTC"),
    "METRO": ("CD", "OVL", "THK"),
    "TEST": ("RS", "CD", "PTC"),
    "PKG": ("WRP", "PTC", "THK"),
}

# 미세 노드일수록 선폭 규격이 좁다. CD 특성에만 적용한다.
PRODUCT_CD_SCALE = {"LX9": 0.55, "DDR5": 1.00, "NAND": 1.60, "PMIC": 2.40}

# QMS 계측기. MES 생산설비(EQP-*)와 완전히 별개 자산이다.
METROLOGY_EQP = {
    "CD": "MET-CD01",
    "OVL": "MET-OVL01",
    "THK": "MET-THK01",
    "PTC": "MET-PTC01",
    "RS": "MET-RS01",
    "WRP": "MET-WRP01",
}

SAMPLING_METHODS = {
    "계량형": ("5매 랜덤 9포인트", 5),
    "계수형": ("3매 전면 스캔", 3),
}

INSPECTION_FREQUENCIES = {
    "CD": "로트별",
    "OVL": "로트별",
    "THK": "로트별",
    "PTC": "전수",
    "RS": "시간별",
    "WRP": "시간별",
}

CONTROL_METHODS = {
    "계량형": "X-bar R 관리도",
    "계수형": "u 관리도",
}

# (팀명, 자격 보유 특성 CSV)
INSPECTOR_TEAMS = (
    ("계측팀", "CD,OVL,THK"),
    ("입고검사팀", "PTC,THK"),
    ("신뢰성팀", "RS,WRP"),
    ("출하검사팀", "CD,RS,PTC"),
    ("품질보증팀", "CD,OVL,THK,PTC,RS,WRP"),
)

# MES operator(kim.js 등)와 겹치지 않는 별도 인력 15명.
INSPECTOR_NAMES = (
    "강민우", "노현서", "서지훈", "오세영", "유다은",
    "임채원", "한도윤", "홍서아", "문가온", "배준호",
    "신예린", "안태경", "윤소민", "조하람", "하시우",
)

QUALIFICATION_LEVELS = ("초급", "중급", "선임", "책임")

# (supplier_code, supplier_name_ko)
SUPPLIERS = (
    ("SUP-A01", "한빛머티리얼즈"),
    ("SUP-A02", "동방정밀소재"),
    ("SUP-B01", "세종케미칼"),
    ("SUP-B02", "대륙화학"),
    ("SUP-C01", "성진가스"),
    ("SUP-C02", "에어프로덕트코리아"),
    ("SUP-D01", "태성메탈"),
    ("SUP-D02", "글로벌타겟"),
)

INSPECTION_ITEMS = {
    "Raw Wafer": "평탄도/저항률/외관",
    "Chemical": "순도/입도/비중",
    "Gas": "순도/수분/파티클",
    "Metal": "조성비/밀도/외관",
    "Mask": "CD 정확도/결함수/투과율",
    "Package": "치수/접합강도/외관",
}

ROOT_CAUSE_CATEGORIES = ("설비", "자재", "작업방법", "환경", "측정")

OWNER_DEPTS = ("공정기술팀", "설비기술팀", "자재구매팀", "품질보증팀", "생산관리팀")

OWNER_NAMES = ("권도현", "남유진", "석민재", "천보람", "표현우")

APPROVER_NAMES = ("구자현", "명수린", "봉태식", "설유나", "탁현빈")
```

- [ ] **Step 4: 마스터 생성 모듈 작성**

`src/qms_masters.py`:

```python
"""QMS 마스터 3종. 불량코드 24 / 검사기준 108 / 검사원 15."""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import MesSnapshot
from src.qms_reference import (
    BASE_DATE,
    CHARACTERISTIC_BASE,
    CONTROL_METHODS,
    DEFECT_DETAILS,
    DEFECT_TAXONOMY,
    INSPECTION_FREQUENCIES,
    INSPECTOR_NAMES,
    INSPECTOR_TEAMS,
    PRODUCT_CD_SCALE,
    QUALIFICATION_LEVELS,
    SAMPLING_METHODS,
    SEED_MASTERS,
    SEVERITY_SCORE,
    STEP_CHARACTERISTICS,
)

_SHIFTS = ("A", "B", "C")


def build_defect_codes() -> list[dict]:
    """MES 상위 불량코드 6종을 QMS 세부코드 24종으로 전개한다."""
    meta = {code: (prefix, category, steps) for code, prefix, category, steps in DEFECT_TAXONOMY}
    rows = []
    for mes_code, seq, name_ko, name_en, severity, cause, action in DEFECT_DETAILS:
        prefix, category, steps = meta[mes_code]
        rows.append(
            {
                "defect_code": f"DEF-{prefix}-{seq:03d}",
                "defect_name_ko": name_ko,
                "defect_name_en": name_en,
                "mes_defect_code": mes_code,
                "defect_category": category,
                "severity": severity,
                "severity_score": SEVERITY_SCORE[severity],
                "typical_step_codes": steps,
                "standard_cause_ko": cause,
                "standard_action_ko": action,
                "is_active": True,
            }
        )
    return rows


def defect_codes_by_mes(defect_codes: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in defect_codes:
        grouped.setdefault(row["mes_defect_code"], []).append(row)
    return grouped


def build_inspection_specs(snapshot: MesSnapshot) -> list[dict]:
    """제품 4 × 공정 9 × 특성 3 = 108행. 규격은 제품 노드에 따라 조정된다."""
    product_names = {p["product_code"]: p["product_name"] for p in snapshot.products}
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    rows = []
    for product_code in sorted(product_names):
        scale = PRODUCT_CD_SCALE[product_code]
        for step in sorted(snapshot.route, key=lambda s: s["seq"]):
            step_code = step["step_code"]
            for char_code in STEP_CHARACTERISTICS[step_code]:
                name_ko, meas_type, unit, target, lsl, usl = CHARACTERISTIC_BASE[char_code]
                if char_code == "CD":
                    target, lsl, usl = target * scale, lsl * scale, usl * scale
                sampling, sample_size = SAMPLING_METHODS[meas_type]
                rows.append(
                    {
                        "spec_id": f"SPEC-{product_code}-{step_code}-{char_code}",
                        "product_code": product_code,
                        "product_name": product_names[product_code],
                        "step_code": step_code,
                        "step_name": step_names[step_code],
                        "characteristic_code": char_code,
                        "characteristic_name_ko": name_ko,
                        "measurement_type": meas_type,
                        "unit": unit,
                        "target_value": round(target, 3),
                        "lsl": round(lsl, 3),
                        "usl": round(usl, 3),
                        "cpk_target": 1.33,
                        "sampling_method": sampling,
                        "sample_size": sample_size,
                        "inspection_frequency": INSPECTION_FREQUENCIES[char_code],
                        "control_method_ko": CONTROL_METHODS[meas_type],
                        "spec_version": "v1.2",
                        "effective_from": BASE_DATE - dt.timedelta(days=180),
                        "is_active": True,
                    }
                )
    return rows


def spec_index(specs: list[dict]) -> dict[tuple[str, str, str], dict]:
    return {(s["product_code"], s["step_code"], s["characteristic_code"]): s for s in specs}


def build_inspectors() -> list[dict]:
    """팀 5 × 교대 3 = 15명."""
    rng = random.Random(SEED_MASTERS)
    rows = []
    for team_no, (team_ko, certified) in enumerate(INSPECTOR_TEAMS):
        for shift_no, shift in enumerate(_SHIFTS):
            index = team_no * len(_SHIFTS) + shift_no
            years = rng.randint(1, 6)
            certified_from = BASE_DATE - dt.timedelta(days=365 * years + rng.randint(0, 300))
            rows.append(
                {
                    "inspector_id": f"QI-{index + 1:03d}",
                    "inspector_name": INSPECTOR_NAMES[index],
                    "team_ko": team_ko,
                    "shift_code": shift,
                    "qualification_level": QUALIFICATION_LEVELS[index % len(QUALIFICATION_LEVELS)],
                    "certified_characteristics": certified,
                    "certified_from": certified_from,
                    "certified_until": certified_from + dt.timedelta(days=365 * 3),
                    "is_active": True,
                }
            )
    return rows
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_masters.py -q`
Expected: PASS — `9 passed`

- [ ] **Step 6: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Add QMS reference constants and three master tables

MES publishes only six coarse defect codes. Quality investigations need to
name an actual failure mode, so each MES code fans out into four QMS detail
codes that keep mes_defect_code for round-trip lookups.

Inspection specs are keyed by product, step and characteristic. Only CD
scales with the technology node, which is why LX9 at 5nm carries a tighter
linewidth window than PMIC at 28nm.

Inspectors deliberately use full Korean names so they never collide with the
MES operator IDs; they are separate people in separate teams."
```

---
### Task 3: 검사 207행

MES 공정이력 91건을 앵커로 삼아 5개 유형의 검사를 만든다. 불일치 장치 ①과 ③이 여기서 심긴다. 이 태스크의 출력이 태스크 4·6의 유일한 입력이므로 판정 분포를 정확히 맞춰야 한다.

**MES 레코드 필드(실측 확인됨):**
- `process_results`: `id`, `lot_id`, `step_code`, `eqp_id`, `in_qty`, `out_qty`, `scrap_qty`, `defect_code`, `in_time`, `out_time`, `operator`, `result`, `step_name`
- `lots`: `lot_id`, `product_code`, `tech_node`, `start_qty`, `wafer_qty`, `priority`, `current_step`, `status`, `start_date`, `product_name`
- `route`: `seq`, `step_code`, `step_name`, `operation`, `eqp_type`, `stage`
- `in_qty` 범위는 12~25, `out_time`은 전건 `2026-09-04`

**Files:**
- Create: `customizing/fabric/qms-lakehouse/src/qms_inspection.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_qms_inspection.py`

**Interfaces:**
- Consumes: `MesSnapshot` (T1), `build_inspectors()` (T2)
- Produces:
  - `build_inspections(snapshot: MesSnapshot, inspectors: list[dict]) -> list[dict]` — 207행
  - `mes_result_index(snapshot: MesSnapshot) -> dict[int, dict]` — MES `id` → 공정이력 행
  - `INSPECTION_COUNTS: dict[str, int]` — `{"IPQC": 91, "IPQC-RT": 40, "OQC": 24, "PCS": 36, "EQV": 16}`

- [ ] **Step 1: 구성·판정 분포 실패 테스트 작성**

`tests/test_qms_inspection.py`:

```python
import collections

from src.qms_inspection import INSPECTION_COUNTS, build_inspections, mes_result_index
from src.qms_masters import build_inspectors


def inspections(snapshot):
    return build_inspections(snapshot, build_inspectors())


def test_total_is_207_with_the_designed_type_mix(snapshot):
    rows = inspections(snapshot)
    assert len(rows) == 207
    assert collections.Counter(r["inspection_type"] for r in rows) == INSPECTION_COUNTS
    assert len({r["inspection_id"] for r in rows}) == 207


def test_ipqc_maps_one_to_one_onto_mes_process_results(snapshot):
    rows = [r for r in inspections(snapshot) if r["inspection_type"] == "IPQC"]
    assert {r["mes_process_result_id"] for r in rows} == {p["id"] for p in snapshot.process_results}


def test_retest_covers_union_of_defective_and_non_pass(snapshot):
    expected = {p["id"] for p in snapshot.process_results if p.get("defect_code") or p["result"] != "Pass"}
    assert len(expected) == 40
    rows = [r for r in inspections(snapshot) if r["inspection_type"] == "IPQC-RT"]
    assert {r["mes_process_result_id"] for r in rows} == expected


def test_lot_free_inspections_have_null_keys(snapshot):
    for row in inspections(snapshot):
        if row["inspection_type"] in {"PCS", "EQV"}:
            assert row["lot_id"] is None
            assert row["mes_process_result_id"] is None
        if row["inspection_type"] == "EQV":
            assert row["product_code"] is None
            assert row["eqp_id"] is not None
        if row["inspection_type"] in {"IPQC", "IPQC-RT"}:
            assert row["mes_process_result_id"] is not None


def test_measurement_counts_total_260(snapshot):
    rows = inspections(snapshot)
    assert sum(r["measurement_count"] for r in rows) == 260
    per_type = collections.defaultdict(set)
    for row in rows:
        per_type[row["inspection_type"]].add(row["measurement_count"])
    assert per_type["IPQC"] == {0}
    assert per_type["OQC"] == {0}
    assert per_type["IPQC-RT"] == {3}
    assert per_type["PCS"] == {3}
    assert per_type["EQV"] == {2}


def test_ipqc_judgment_follows_mes_state(snapshot):
    index = mes_result_index(snapshot)
    rows = [r for r in inspections(snapshot) if r["inspection_type"] == "IPQC"]
    by_state = collections.Counter()
    for row in rows:
        mes = index[row["mes_process_result_id"]]
        if mes["result"] == "Fail":
            assert row["judgment"] == "불합격"
        by_state[(mes["result"], bool(mes.get("defect_code")), row["judgment"])] += 1
    assert by_state[("Pass", True, "합격")] == 23
    assert by_state[("Pass", True, "조건부합격")] == 10
    assert by_state[("Pass", False, "불합격")] == 3
    assert by_state[("Pass", False, "조건부합격")] == 4
    assert by_state[("Pass", False, "합격")] == 44


def test_nonconformance_flag_count_matches_ncr_budget(snapshot):
    rows = inspections(snapshot)
    flagged = collections.Counter(r["inspection_type"] for r in rows if r["has_nonconformance"])
    # 스펙 6.4의 검사 출처 NCR 배분: 24 + 10 + 6 + 7 + 2 = 49
    assert flagged == {"IPQC": 24, "IPQC-RT": 10, "OQC": 6, "PCS": 7, "EQV": 2}


def test_device_1_and_3_are_disjoint_and_correctly_sized(snapshot):
    index = mes_result_index(snapshot)
    rows = inspections(snapshot)
    device1 = [
        r for r in rows
        if r["mes_process_result_id"] is not None
        and index[r["mes_process_result_id"]]["result"] == "Pass"
        and not index[r["mes_process_result_id"]].get("defect_code")
        and r["judgment"] == "불합격"
        and r["defect_found_qty"] == 0
    ]
    device3 = [
        r for r in rows
        if r["mes_process_result_id"] is not None
        and not index[r["mes_process_result_id"]].get("defect_code")
        and r["defect_found_qty"] > 0
        and r["judgment"] != "불합격"
    ]
    assert len(device1) == 3
    assert len(device3) == 4
    assert not ({r["inspection_id"] for r in device1} & {r["inspection_id"] for r in device3})


def test_quantities_and_causality_hold(snapshot):
    index = mes_result_index(snapshot)
    for row in inspections(snapshot):
        assert row["defect_found_qty"] <= row["sample_size"]
        assert row["inspected_wafer_qty"] <= row["sample_size"]
        if row["mes_process_result_id"] is not None:
            mes = index[row["mes_process_result_id"]]
            assert row["sample_size"] <= mes["in_qty"]
            assert row["inspection_datetime"] >= mes["out_time"]


def test_inspections_carry_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    assert not (set(inspections(snapshot)[0]) & forbidden)


def test_inspections_are_deterministic(snapshot):
    assert inspections(snapshot) == inspections(snapshot)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_inspection.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.qms_inspection'`

- [ ] **Step 3: 검사 생성 모듈 작성**

`src/qms_inspection.py`:

```python
"""qms_inspection 207행.

구성은 스펙 6.5절 그대로다.
  IPQC     91  MES 공정이력 1:1
  IPQC-RT  40  결함 보유(35) ∪ non-Pass(7), 교집합 2
  OQC      24  Done 로트 6 × 4 배치
  PCS      36  제품 4 × 공정 9
  EQV      16  설비 8 × 2회
"""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import MesSnapshot
from src.qms_reference import SEED_INSPECTION, STEP_CHARACTERISTICS

INSPECTION_COUNTS = {"IPQC": 91, "IPQC-RT": 40, "OQC": 24, "PCS": 36, "EQV": 16}

_TEAM_BY_TYPE = {
    "IPQC": "계측팀",
    "IPQC-RT": "계측팀",
    "OQC": "출하검사팀",
    "PCS": "품질보증팀",
    "EQV": "신뢰성팀",
}

_BASIS = {
    "합격": "전 항목 규격 내, 관리한계 이탈 없음",
    "조건부합격": "일부 항목 관리한계 근접, 후속 공정 모니터링 조건부 승인",
    "불합격": "규격 이탈 확인, 부적합 보고서 발행",
}


def mes_result_index(snapshot: MesSnapshot) -> dict[int, dict]:
    return {row["id"]: row for row in snapshot.process_results}


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts)


def _bounded(rng: random.Random, low: int, high: int, cap: int) -> int:
    """low..high 범위 정수를 cap 이하로 자른다. 수량 정합 검증을 항상 통과시킨다."""
    high = min(high, cap)
    low = min(low, high)
    return rng.randint(low, high)


def _sampling(rng: random.Random, wafer_cap: int) -> tuple[int, int]:
    """(inspected_wafer_qty, sample_size). sample_size 는 웨이퍼 매수 × 3포인트."""
    wafers = min(rng.randint(3, 5), wafer_cap)
    return wafers, min(wafers * 3, wafer_cap)


class _IdGen:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"INS-2026-{self.n:04d}"


def build_inspections(snapshot: MesSnapshot, inspectors: list[dict]) -> list[dict]:
    rng = random.Random(SEED_INSPECTION)
    ids = _IdGen()
    by_team: dict[str, list[dict]] = {}
    for person in inspectors:
        by_team.setdefault(person["team_ko"], []).append(person)

    lots = {lot["lot_id"]: lot for lot in snapshot.lots}
    results = sorted(snapshot.process_results, key=lambda r: r["id"])

    rows: list[dict] = []
    rows.extend(_build_ipqc(rng, ids, results, lots, by_team))
    rows.extend(_build_retest(rng, ids, results, lots, by_team))
    rows.extend(_build_oqc(rng, ids, snapshot, by_team))
    rows.extend(_build_pcs(rng, ids, snapshot, by_team))
    rows.extend(_build_eqv(rng, ids, snapshot, by_team))
    return rows


def _row(
    ids: _IdGen,
    *,
    inspection_type: str,
    lot_id,
    product_code,
    product_name,
    step_code,
    step_name,
    eqp_id,
    mes_id,
    inspector: dict,
    when: dt.datetime,
    wafers: int,
    sample_size: int,
    judgment: str,
    defect_found_qty: int,
    measurement_count: int,
    has_ncr: bool,
    remark: str,
) -> dict:
    return {
        "inspection_id": ids.next(),
        "inspection_type": inspection_type,
        "lot_id": lot_id,
        "product_code": product_code,
        "product_name": product_name,
        "step_code": step_code,
        "step_name": step_name,
        "eqp_id": eqp_id,
        "mes_process_result_id": mes_id,
        "inspector_id": inspector["inspector_id"],
        "inspection_datetime": when,
        "sample_size": sample_size,
        "inspected_wafer_qty": wafers,
        "judgment": judgment,
        "judgment_basis_ko": _BASIS[judgment],
        "defect_found_qty": defect_found_qty,
        "measurement_count": measurement_count,
        "has_nonconformance": has_ncr,
        "remark_ko": remark,
    }


def _build_ipqc(rng, ids, results, lots, by_team) -> list[dict]:
    """MES 91건 1:1. 판정 분포는 스펙 6.1절을 그대로 따른다."""
    fails = [r for r in results if r["result"] == "Fail"]
    reworks = [r for r in results if r["result"] == "Rework"]
    pass_def = [r for r in results if r["result"] == "Pass" and r.get("defect_code")]
    pass_clean = [r for r in results if r["result"] == "Pass" and not r.get("defect_code")]

    plan: dict[int, tuple[str, bool]] = {}
    for row in fails:
        plan[row["id"]] = ("불합격", True)

    shuffled = list(reworks)
    rng.shuffle(shuffled)
    plan[shuffled[0]["id"]] = ("불합격", True)
    plan[shuffled[1]["id"]] = ("조건부합격", True)

    shuffled = list(pass_def)
    rng.shuffle(shuffled)
    for row in shuffled[:10]:
        plan[row["id"]] = ("조건부합격", True)
    for row in shuffled[10:]:
        plan[row["id"]] = ("합격", False)

    # 장치①과 장치③은 같은 51건 풀에서 나온다. 슬라이스를 겹치지 않게 잘라
    # 서로소를 구조적으로 보장한다. 구분은 defect_found_qty 0 / >0 로 이뤄진다.
    shuffled = list(pass_clean)
    rng.shuffle(shuffled)
    device1 = {r["id"] for r in shuffled[:3]}
    device3 = {r["id"] for r in shuffled[3:7]}
    for row in shuffled[:3]:
        plan[row["id"]] = ("불합격", True)
    for row in shuffled[3:7]:
        plan[row["id"]] = ("조건부합격", True)
    for row in shuffled[7:]:
        plan[row["id"]] = ("합격", False)

    rows = []
    for mes in results:
        judgment, has_ncr = plan[mes["id"]]
        wafers, sample_size = _sampling(rng, mes["in_qty"])
        if mes["id"] in device1:
            found = 0
            remark = "생산 통과분에 대해 품질 홀드 적용. 계측 재현성 확인 필요"
        elif mes["id"] in device3:
            found = _bounded(rng, 1, 4, sample_size)
            remark = "설비 판정에는 없던 결함을 검사에서 검출"
        elif judgment == "합격":
            found = 0
            remark = "정상 공정검사"
        else:
            found = _bounded(rng, 2, 9, sample_size)
            remark = "공정검사 중 결함 검출"
        rows.append(
            _row(
                ids,
                inspection_type="IPQC",
                lot_id=mes["lot_id"],
                product_code=lots[mes["lot_id"]]["product_code"],
                product_name=lots[mes["lot_id"]]["product_name"],
                step_code=mes["step_code"],
                step_name=mes["step_name"],
                eqp_id=mes["eqp_id"],
                mes_id=mes["id"],
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["IPQC"]]),
                when=_parse(mes["out_time"]) + dt.timedelta(minutes=rng.randint(10, 240)),
                wafers=wafers,
                sample_size=sample_size,
                judgment=judgment,
                defect_found_qty=found,
                measurement_count=0,
                has_ncr=has_ncr,
                remark=remark,
            )
        )
    return rows


def _build_retest(rng, ids, results, lots, by_team) -> list[dict]:
    """결함 보유 35 ∪ non-Pass 7 = 40건 재검사. 측정치 3점을 남긴다."""
    targets = [r for r in results if r.get("defect_code") or r["result"] != "Pass"]
    clean_mes = [r for r in targets if not r.get("defect_code")]
    coded_mes = [r for r in targets if r.get("defect_code")]

    # MES 불량코드가 없는 재검사 건은 전부 불합격으로 둔다. 그래야 장치③
    # (MES 코드 없음 + 결함 검출 + 불합격 아님)과 절대 충돌하지 않는다.
    plan = {r["id"]: ("불합격", True) for r in clean_mes}
    shuffled = list(coded_mes)
    rng.shuffle(shuffled)
    remaining = 10 - len(plan)
    for row in shuffled[:remaining]:
        plan[row["id"]] = ("불합격", True)
    for row in shuffled[remaining:remaining + 12]:
        plan[row["id"]] = ("조건부합격", False)
    for row in shuffled[remaining + 12:]:
        plan[row["id"]] = ("합격", False)

    rows = []
    for mes in targets:
        judgment, has_ncr = plan[mes["id"]]
        wafers, sample_size = _sampling(rng, mes["in_qty"])
        found = 0 if judgment == "합격" else _bounded(rng, 1, 8, sample_size)
        rows.append(
            _row(
                ids,
                inspection_type="IPQC-RT",
                lot_id=mes["lot_id"],
                product_code=lots[mes["lot_id"]]["product_code"],
                product_name=lots[mes["lot_id"]]["product_name"],
                step_code=mes["step_code"],
                step_name=mes["step_name"],
                eqp_id=mes["eqp_id"],
                mes_id=mes["id"],
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["IPQC-RT"]]),
                when=_parse(mes["out_time"]) + dt.timedelta(minutes=rng.randint(300, 720)),
                wafers=wafers,
                sample_size=sample_size,
                judgment=judgment,
                defect_found_qty=found,
                measurement_count=3,
                has_ncr=has_ncr,
                remark="재검사 수행. 계측 3점 기록",
            )
        )
    return rows


def _build_oqc(rng, ids, snapshot, by_team) -> list[dict]:
    """Done 로트 6건을 출하 배치 4개로 나눠 24건."""
    done = sorted((l for l in snapshot.lots if l["status"] == "Done"), key=lambda l: l["lot_id"])
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    plan_slots = [(lot, batch) for lot in done for batch in range(1, 5)]
    order = list(range(len(plan_slots)))
    rng.shuffle(order)
    judgments = {}
    for rank, slot in enumerate(order):
        if rank < 2:
            judgments[slot] = ("불합격", True)
        elif rank < 6:
            judgments[slot] = ("조건부합격", True)
        else:
            judgments[slot] = ("합격", False)

    rows = []
    for slot, (lot, batch) in enumerate(plan_slots):
        judgment, has_ncr = judgments[slot]
        wafers, sample_size = _sampling(rng, lot["wafer_qty"])
        found = 0 if judgment == "합격" else _bounded(rng, 2, 9, sample_size)
        rows.append(
            _row(
                ids,
                inspection_type="OQC",
                lot_id=lot["lot_id"],
                product_code=lot["product_code"],
                product_name=lot["product_name"],
                step_code=lot["current_step"],
                step_name=step_names[lot["current_step"]],
                eqp_id=None,
                mes_id=None,
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["OQC"]]),
                when=dt.datetime(2026, 9, 5, 8, 0) + dt.timedelta(minutes=rng.randint(0, 600)),
                wafers=wafers,
                sample_size=sample_size,
                judgment=judgment,
                defect_found_qty=found,
                measurement_count=0,
                has_ncr=has_ncr,
                remark=f"출하 배치 {batch}/4 검사",
            )
        )
    return rows


def _build_pcs(rng, ids, snapshot, by_team) -> list[dict]:
    """제품 4 × 공정 9 = 36건 정기 공정능력 조사. 로트에 매이지 않는다."""
    products = sorted(snapshot.products, key=lambda p: p["product_code"])
    steps = sorted(snapshot.route, key=lambda s: s["seq"])
    slots = [(p, s) for p in products for s in steps]
    order = list(range(len(slots)))
    rng.shuffle(order)
    flagged = set(order[:7])

    rows = []
    for slot, (product, step) in enumerate(slots):
        judgment, has_ncr = ("조건부합격", True) if slot in flagged else ("합격", False)
        rows.append(
            _row(
                ids,
                inspection_type="PCS",
                lot_id=None,
                product_code=product["product_code"],
                product_name=product["product_name"],
                step_code=step["step_code"],
                step_name=step["step_name"],
                eqp_id=None,
                mes_id=None,
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["PCS"]]),
                when=dt.datetime(2026, 9, 5, 9, 0) + dt.timedelta(minutes=rng.randint(0, 1440)),
                wafers=3,
                sample_size=9,
                judgment=judgment,
                defect_found_qty=0,
                measurement_count=3,
                has_ncr=has_ncr,
                remark="정기 공정능력 조사. Cpk 산출용 3점 계측"
                if not has_ncr
                else "정기 공정능력 조사에서 Cpk 목표 1.33 미달",
            )
        )
    return rows


def _build_eqv(rng, ids, snapshot, by_team) -> list[dict]:
    """설비 8대 × 2회 = 16건 설비 검증. 제품과 로트 모두 무관하다."""
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    slots = [(e, run) for e in snapshot.equipment for run in (1, 2)]
    order = list(range(len(slots)))
    rng.shuffle(order)
    flagged = set(order[:2])

    rows = []
    for slot, (equipment, run) in enumerate(slots):
        judgment, has_ncr = ("불합격", True) if slot in flagged else ("합격", False)
        rows.append(
            _row(
                ids,
                inspection_type="EQV",
                lot_id=None,
                product_code=None,
                product_name=None,
                step_code=equipment["step_code"],
                step_name=step_names[equipment["step_code"]],
                eqp_id=equipment["eqp_id"],
                mes_id=None,
                inspector=rng.choice(by_team[_TEAM_BY_TYPE["EQV"]]),
                when=dt.datetime(2026, 9, 6, 7, 0) + dt.timedelta(minutes=rng.randint(0, 1440)),
                wafers=2,
                sample_size=6,
                judgment=judgment,
                defect_found_qty=_bounded(rng, 1, 5, 6) if has_ncr else 0,
                measurement_count=2,
                has_ncr=has_ncr,
                remark=f"설비 정기 검증 {run}회차",
            )
        )
    return rows


assert set(STEP_CHARACTERISTICS)  # 공정 특성 표가 비어 있으면 측정 생성이 불가능하다
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_inspection.py -q`
Expected: PASS — `11 passed`

판정 분포 테스트가 실패하면 슬라이스 경계(`shuffled[:10]`, `shuffled[:3]`, `shuffled[3:7]`)를 먼저 확인한다. `rng.shuffle` 호출 순서가 바뀌면 값이 달라지지만 건수는 유지되어야 한다.

- [ ] **Step 5: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Generate 207 QMS inspections anchored on MES process history

Random judgments would produce nonsense like a lot that MES passed but QMS
scrapped for an unrelated reason. Instead every IPQC row inherits its verdict
from the MES row it points at, and the extra inspection types that have no MES
counterpart carry null lot and process-result keys.

Devices 1 and 3 both draw from the 51 clean passes, so they are cut from
non-overlapping slices of one shuffle and further separated by
defect_found_qty being zero versus positive. Retest rows whose MES row has no
defect code are forced to 불합격 so they can never be mistaken for device 3."
```

---
### Task 4: 측정치 260행

측정치를 남기는 검사는 셋뿐이다. IPQC-RT 40건이 3점, PCS 36건이 3점, EQV 16건이 2점 — 합 260. 값은 검사기준의 `target/lsl/usl`에서 정규분포로 뽑고, 규격 스냅샷을 각 행에 복사해 기준 개정에 영향받지 않게 한다.

**Files:**
- Create: `customizing/fabric/qms-lakehouse/src/qms_measurement.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_qms_measurement.py`

**Interfaces:**
- Consumes: `build_inspections()` (T3), `build_inspection_specs()` / `spec_index()` (T2)
- Produces:
  - `build_measurements(inspections: list[dict], specs: list[dict]) -> list[dict]` — 260행
  - `EQV_SPEC_PRODUCT: str` — `"DDR5"`. 설비 검증은 기준 제품 규격으로 측정한다
  - `nominal_sigma(spec: dict) -> float` — `(usl - lsl) / 8`. 공칭 Cpk 1.33에 대응
  - `characteristics_for(inspection: dict) -> tuple[str, ...]`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_qms_measurement.py`:

```python
import collections

from src.qms_inspection import build_inspections
from src.qms_masters import build_inspection_specs, build_inspectors, spec_index
from src.qms_measurement import (
    EQV_SPEC_PRODUCT,
    build_measurements,
    characteristics_for,
    nominal_sigma,
)


def measurements(snapshot):
    inspections = build_inspections(snapshot, build_inspectors())
    return inspections, build_measurements(inspections, build_inspection_specs(snapshot))


def test_total_is_260_and_matches_declared_measurement_counts(snapshot):
    inspections, rows = measurements(snapshot)
    assert len(rows) == 260
    assert len({r["measurement_id"] for r in rows}) == 260
    declared = {i["inspection_id"]: i["measurement_count"] for i in inspections}
    actual = collections.Counter(r["inspection_id"] for r in rows)
    for inspection_id, count in declared.items():
        assert actual.get(inspection_id, 0) == count


def test_measurement_split_by_inspection_type(snapshot):
    inspections, rows = measurements(snapshot)
    type_by_id = {i["inspection_id"]: i["inspection_type"] for i in inspections}
    assert collections.Counter(type_by_id[r["inspection_id"]] for r in rows) == {
        "IPQC-RT": 120,
        "PCS": 108,
        "EQV": 32,
    }


def test_spec_id_is_always_consistent_with_its_join_keys(snapshot):
    _, rows = measurements(snapshot)
    for row in rows:
        assert row["spec_id"] == (
            f"SPEC-{row['product_code']}-{row['step_code']}-{row['characteristic_code']}"
        )


def test_equipment_verification_measures_against_reference_product(snapshot):
    inspections, rows = measurements(snapshot)
    eqv_ids = {i["inspection_id"] for i in inspections if i["inspection_type"] == "EQV"}
    eqv_rows = [r for r in rows if r["inspection_id"] in eqv_ids]
    assert len(eqv_rows) == 32
    assert {r["product_code"] for r in eqv_rows} == {EQV_SPEC_PRODUCT}
    assert {r["lot_id"] for r in eqv_rows} == {None}


def test_spec_snapshot_matches_master_spec(snapshot):
    index = spec_index(build_inspection_specs(snapshot))
    _, rows = measurements(snapshot)
    for row in rows:
        spec = index[(row["product_code"], row["step_code"], row["characteristic_code"])]
        assert row["target_value"] == spec["target_value"]
        assert row["lsl"] == spec["lsl"]
        assert row["usl"] == spec["usl"]
        assert row["unit"] == spec["unit"]


def test_out_of_spec_flag_agrees_with_limits(snapshot):
    _, rows = measurements(snapshot)
    for row in rows:
        outside = row["measured_value"] < row["lsl"] or row["measured_value"] > row["usl"]
        assert row["is_out_of_spec"] is outside
        assert row["judgment"] == ("NG" if outside else "OK")


def test_failed_inspections_contain_at_least_one_out_of_spec_point(snapshot):
    inspections, rows = measurements(snapshot)
    failed = {
        i["inspection_id"]
        for i in inspections
        if i["judgment"] == "불합격" and i["measurement_count"] > 0
    }
    assert failed
    by_inspection = collections.defaultdict(list)
    for row in rows:
        by_inspection[row["inspection_id"]].append(row)
    for inspection_id in failed:
        assert any(r["is_out_of_spec"] for r in by_inspection[inspection_id])


def test_nominal_sigma_targets_cpk_1_33(snapshot):
    spec = build_inspection_specs(snapshot)[0]
    sigma = nominal_sigma(spec)
    assert sigma == (spec["usl"] - spec["lsl"]) / 8
    cpk = (spec["usl"] - spec["lsl"]) / 2 / (3 * sigma)
    assert round(cpk, 2) == 1.33


def test_characteristics_per_inspection_type(snapshot):
    inspections, _ = measurements(snapshot)
    for inspection in inspections:
        chars = characteristics_for(inspection)
        assert len(chars) == inspection["measurement_count"]


def test_measurements_reference_the_inspection_inspector(snapshot):
    inspections, rows = measurements(snapshot)
    inspector_by_id = {i["inspection_id"]: i["inspector_id"] for i in inspections}
    for row in rows:
        assert row["measured_by"] == inspector_by_id[row["inspection_id"]]


def test_metrology_equipment_is_separate_from_mes_equipment(snapshot):
    _, rows = measurements(snapshot)
    assert all(r["metrology_eqp_id"].startswith("MET-") for r in rows)


def test_measurements_carry_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    _, rows = measurements(snapshot)
    assert not (set(rows[0]) & forbidden)


def test_measurements_are_deterministic(snapshot):
    assert measurements(snapshot)[1] == measurements(snapshot)[1]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_measurement.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.qms_measurement'`

- [ ] **Step 3: 측정치 모듈 작성**

`src/qms_measurement.py`:

```python
"""qms_measurement 260행.

IPQC-RT 40×3 = 120, PCS 36×3 = 108, EQV 16×2 = 32.
IPQC 기본과 OQC는 합부만 남기고 측정치를 기록하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import random

from src.qms_masters import spec_index
from src.qms_reference import (
    CHARACTERISTIC_BASE,
    METROLOGY_EQP,
    SEED_MEASUREMENT,
    STEP_CHARACTERISTICS,
)

# 설비 검증은 특정 제품을 위한 검사가 아니다. 규격이 있어야 값을 뽑을 수 있으므로
# 기준 제품 하나를 정해 그 규격으로 측정한다.
EQV_SPEC_PRODUCT = "DDR5"

# 공정능력 미달(PCS 조건부합격)은 산포를 키워 표현한다. Cpk 는 약 0.6이 된다.
_DEGRADED_SIGMA_FACTOR = 2.2


def nominal_sigma(spec: dict) -> float:
    """규격폭의 1/8. 공칭 Cpk 가 1.33이 되는 산포다."""
    return (spec["usl"] - spec["lsl"]) / 8


def characteristics_for(inspection: dict) -> tuple[str, ...]:
    """검사 유형별 측정 특성. 개수가 measurement_count 와 항상 일치한다."""
    count = inspection["measurement_count"]
    if count == 0:
        return ()
    return STEP_CHARACTERISTICS[inspection["step_code"]][:count]


def _spec_product(inspection: dict) -> str:
    return inspection["product_code"] or EQV_SPEC_PRODUCT


def _draw(rng: random.Random, spec: dict, sigma: float, force_out: bool) -> float:
    lsl, usl, target = spec["lsl"], spec["usl"], spec["target_value"]
    if force_out:
        margin = (usl - lsl) * rng.uniform(0.04, 0.12)
        # 계수형(파티클수)은 하한이 0이라 아래로 이탈시키면 물리적으로 말이 안 된다.
        if spec["measurement_type"] == "계수형" or rng.random() < 0.5:
            value = usl + margin
        else:
            value = lsl - margin
    else:
        value = rng.gauss(target, sigma)
    if spec["unit"] == "ea":
        return float(max(0, round(value)))
    return round(value, 4)


def build_measurements(inspections: list[dict], specs: list[dict]) -> list[dict]:
    rng = random.Random(SEED_MEASUREMENT)
    index = spec_index(specs)
    rows: list[dict] = []
    seq = 0

    for inspection in inspections:
        chars = characteristics_for(inspection)
        if not chars:
            continue
        product_code = _spec_product(inspection)
        degraded = inspection["judgment"] == "조건부합격" and inspection["has_nonconformance"]
        # 불합격 검사는 반드시 규격 이탈점을 하나 이상 남긴다. 판정과 측정이
        # 어긋나면 에이전트가 모순된 답을 하게 된다.
        force_index = 0 if inspection["judgment"] == "불합격" else -1

        for position, char_code in enumerate(chars):
            spec = index[(product_code, inspection["step_code"], char_code)]
            sigma = nominal_sigma(spec) * (_DEGRADED_SIGMA_FACTOR if degraded else 1.0)
            value = _draw(rng, spec, sigma, force_out=position == force_index)
            outside = value < spec["lsl"] or value > spec["usl"]
            seq += 1
            rows.append(
                {
                    "measurement_id": f"MEA-2026-{seq:06d}",
                    "inspection_id": inspection["inspection_id"],
                    "spec_id": spec["spec_id"],
                    "lot_id": inspection["lot_id"],
                    "product_code": product_code,
                    "step_code": inspection["step_code"],
                    "characteristic_code": char_code,
                    "characteristic_name_ko": CHARACTERISTIC_BASE[char_code][0],
                    "sample_no": position % max(inspection["inspected_wafer_qty"], 1) + 1,
                    "site_no": position + 1,
                    "measured_value": value,
                    "unit": spec["unit"],
                    "target_value": spec["target_value"],
                    "lsl": spec["lsl"],
                    "usl": spec["usl"],
                    "deviation_pct": round(
                        (value - spec["target_value"]) / spec["target_value"] * 100, 3
                    ),
                    "is_out_of_spec": outside,
                    "judgment": "NG" if outside else "OK",
                    "metrology_eqp_id": METROLOGY_EQP[char_code],
                    "measured_by": inspection["inspector_id"],
                    "measured_at": inspection["inspection_datetime"]
                    + dt.timedelta(minutes=5 * (position + 1)),
                }
            )
    return rows
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_measurement.py -q`
Expected: PASS — `12 passed`

- [ ] **Step 5: 전체 회귀 실행**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest -q`
Expected: PASS — `44 passed`

- [ ] **Step 6: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Generate 260 measurements from inspection specs

Values are drawn at a sigma of one eighth of the spec width, which is exactly
the spread that yields the 1.33 Cpk the specs target. Capability studies that
were flagged for low Cpk widen that sigma instead of being special-cased, so
their poor numbers come out of the same generator.

Every failed inspection is guaranteed at least one out-of-spec point.
Without that, an agent could read a 불합격 verdict next to three passing
measurements and have no way to reconcile them.

Equipment verification has no product of its own, so it measures against the
DDR5 reference spec and records that product code to keep spec_id consistent
with its own join keys."
```

---
### Task 5: 입고검사 200행

MES는 자재 마스터와 BOM만 갖고 있고 입고 품질에 대해서는 아무것도 남기지 않는다. 그 공백이 QMS 입고검사의 자리다. 판정 분포는 불합격 12% / 특채 8% / 합격 80%로 고정하며, 여기서 나온 불합격·특채 40건이 태스크 6의 NCR 입력이 된다.

**MES 자재·BOM 필드(실측 확인됨):**
- `materials`: `material_code`, `material_name`, `category`, `qty`, `uom`, `location`
- `category` 값: `Raw Wafer`, `Chemical`, `Gas`, `Metal`, `Mask`, `Package` — `qms_reference.INSPECTION_ITEMS`의 키와 정확히 일치한다
- `bom`: `id`, `product_code`, `step_code`, `step_name`, `material_code`, `material_name`, `qty_per_wafer`, `uom`

**Files:**
- Create: `customizing/fabric/qms-lakehouse/src/qms_incoming.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_qms_incoming.py`

**Interfaces:**
- Consumes: `MesSnapshot` (T1), `build_inspectors()` / `build_defect_codes()` (T2)
- Produces:
  - `build_incoming_inspections(snapshot: MesSnapshot, inspectors: list[dict], defect_codes: list[dict]) -> list[dict]` — 200행
  - `IQC_JUDGMENT_COUNTS: dict[str, int]` — `{"불합격": 24, "특채": 16, "합격": 160}`
  - `SUPPLIERS_BY_CATEGORY: dict[str, tuple[str, ...]]`
  - `MATERIAL_DEFECT_MAP: dict[str, tuple[str, ...]]` — 자재 카테고리 → MES 상위 불량코드 후보

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_qms_incoming.py`:

```python
import collections

from src.qms_incoming import IQC_JUDGMENT_COUNTS, build_incoming_inspections
from src.qms_masters import build_defect_codes, build_inspectors


def incoming(snapshot):
    return build_incoming_inspections(snapshot, build_inspectors(), build_defect_codes())


def test_total_is_200_with_unique_ids(snapshot):
    rows = incoming(snapshot)
    assert len(rows) == 200
    assert len({r["iqc_id"] for r in rows}) == 200


def test_judgment_distribution_is_12_8_80(snapshot):
    assert collections.Counter(r["judgment"] for r in incoming(snapshot)) == IQC_JUDGMENT_COUNTS


def test_every_material_is_inspected_and_all_codes_exist_in_mes(snapshot):
    rows = incoming(snapshot)
    mes_materials = {m["material_code"] for m in snapshot.materials}
    used = collections.Counter(r["material_code"] for r in rows)
    assert set(used) == mes_materials
    assert min(used.values()) >= 16


def test_defect_code_present_exactly_when_not_accepted(snapshot):
    valid = {d["defect_code"] for d in build_defect_codes()}
    for row in incoming(snapshot):
        if row["judgment"] == "합격":
            assert row["defect_code"] is None
        else:
            assert row["defect_code"] in valid


def test_material_labels_match_mes(snapshot):
    names = {m["material_code"]: m["material_name"] for m in snapshot.materials}
    uoms = {m["material_code"]: m["uom"] for m in snapshot.materials}
    for row in incoming(snapshot):
        assert row["material_name"] == names[row["material_code"]]
        assert row["uom"] == uoms[row["material_code"]]


def test_inspection_never_precedes_receipt(snapshot):
    for row in incoming(snapshot):
        assert row["inspection_date"] >= row["receipt_date"]
        assert row["sample_size"] <= row["received_qty"]


def test_missing_certificate_is_reported_as_not_submitted(snapshot):
    for row in incoming(snapshot):
        if not row["coa_received"]:
            assert row["coa_conformance"] == "미제출"
        else:
            assert row["coa_conformance"] in {"일치", "불일치"}


def test_incoming_inspections_are_handled_by_the_receiving_team(snapshot):
    receiving = {i["inspector_id"] for i in build_inspectors() if i["team_ko"] == "입고검사팀"}
    assert {r["inspector_id"] for r in incoming(snapshot)} <= receiving


def test_supplier_lot_numbers_are_unique(snapshot):
    rows = incoming(snapshot)
    assert len({r["supplier_lot_no"] for r in rows}) == 200


def test_incoming_carries_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    assert not (set(incoming(snapshot)[0]) & forbidden)


def test_incoming_is_deterministic(snapshot):
    assert incoming(snapshot) == incoming(snapshot)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_incoming.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.qms_incoming'`

- [ ] **Step 3: 입고검사 모듈 작성**

`src/qms_incoming.py`:

```python
"""qms_incoming_inspection 200행.

MES는 자재를 알지만 그 자재의 입고 품질은 남기지 않는다. 공급업체, 성적서,
샘플링 판정은 전부 QMS 고유 사실이다.
"""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import MesSnapshot
from src.qms_masters import defect_codes_by_mes
from src.qms_reference import BASE_DATE, INSPECTION_ITEMS, SEED_INCOMING, SUPPLIERS

IQC_JUDGMENT_COUNTS = {"불합격": 24, "특채": 16, "합격": 160}

SUPPLIERS_BY_CATEGORY = {
    "Raw Wafer": ("SUP-A01", "SUP-A02"),
    "Chemical": ("SUP-B01", "SUP-B02"),
    "Gas": ("SUP-C01", "SUP-C02"),
    "Metal": ("SUP-D01", "SUP-D02"),
    "Mask": ("SUP-A01", "SUP-D02"),
    "Package": ("SUP-B02", "SUP-D01"),
}

MATERIAL_DEFECT_MAP = {
    "Raw Wafer": ("Scratch", "Particle"),
    "Chemical": ("Contamination", "Particle"),
    "Gas": ("Particle", "Contamination"),
    "Metal": ("Contamination",),
    "Mask": ("CD-OOS", "Overlay"),
    "Package": ("Scratch", "Contamination"),
}

# uom 별 1회 입고 수량 범위. 병 단위 가스와 미터 단위 와이어는 자릿수가 다르다.
_RECEIPT_QTY = {
    "EA": (200, 2000),
    "L": (20, 200),
    "BTL": (10, 60),
    "SET": (1, 6),
    "M": (1000, 8000),
    "KG": (50, 400),
}

_REMARKS = {
    "합격": "규격 이내. 정상 입고 처리",
    "특채": "경미한 규격 이탈. 사용처 한정 조건으로 특채 승인",
    "불합격": "규격 이탈 확인. 격리 후 부적합 보고서 발행",
}


def build_incoming_inspections(
    snapshot: MesSnapshot, inspectors: list[dict], defect_codes: list[dict]
) -> list[dict]:
    rng = random.Random(SEED_INCOMING)
    suppliers = dict(SUPPLIERS)
    receiving = [i for i in inspectors if i["team_ko"] == "입고검사팀"]
    by_mes_defect = defect_codes_by_mes(defect_codes)
    materials = sorted(snapshot.materials, key=lambda m: m["material_code"])

    total = sum(IQC_JUDGMENT_COUNTS.values())
    slots = list(range(total))
    rng.shuffle(slots)
    judgments: dict[int, str] = {}
    cursor = 0
    for judgment in ("불합격", "특채", "합격"):
        count = IQC_JUDGMENT_COUNTS[judgment]
        for slot in slots[cursor : cursor + count]:
            judgments[slot] = judgment
        cursor += count

    rows = []
    for slot in range(total):
        material = materials[slot % len(materials)]
        category = material["category"]
        judgment = judgments[slot]
        supplier_code = SUPPLIERS_BY_CATEGORY[category][slot % 2]
        low, high = _RECEIPT_QTY[material["uom"]]
        received_qty = float(rng.randint(low, high))
        sample_size = min(rng.randint(3, 20), int(received_qty))
        receipt_date = BASE_DATE - dt.timedelta(days=rng.randint(0, 30))
        coa_received = rng.random() >= 0.10
        if not coa_received:
            coa_conformance = "미제출"
        elif judgment != "합격" and rng.random() < 0.7:
            coa_conformance = "불일치"
        else:
            coa_conformance = "일치"
        if judgment == "합격":
            defect_code = None
        else:
            mes_defect = rng.choice(MATERIAL_DEFECT_MAP[category])
            defect_code = rng.choice(by_mes_defect[mes_defect])["defect_code"]
        rows.append(
            {
                "iqc_id": f"IQC-2026-{slot + 1:04d}",
                "material_code": material["material_code"],
                "material_name": material["material_name"],
                "supplier_code": supplier_code,
                "supplier_name_ko": suppliers[supplier_code],
                "supplier_lot_no": f"{supplier_code[-3:]}-{receipt_date:%y%m}-{slot + 1:04d}",
                "receipt_date": receipt_date,
                "received_qty": received_qty,
                "uom": material["uom"],
                "sample_size": sample_size,
                "inspection_items_ko": INSPECTION_ITEMS[category],
                "judgment": judgment,
                "defect_code": defect_code,
                "coa_received": coa_received,
                "coa_conformance": coa_conformance,
                "inspector_id": rng.choice(receiving)["inspector_id"],
                "inspection_date": receipt_date + dt.timedelta(days=rng.randint(0, 2)),
                "remark_ko": _REMARKS[judgment],
            }
        )
    return rows
```

함수 안에서 로컬 모듈을 import하지 않는다. 태스크 8의 노트북 빌더가 로컬 import 행을 통째로 삭제하기 때문에, 들여쓰기된 import를 지우면 함수 본문이 비어 문법 오류가 난다. 로컬 import는 항상 모듈 최상단에만 둔다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_incoming.py -q`
Expected: PASS — `11 passed`

- [ ] **Step 5: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Generate 200 incoming material inspections

MES tracks which materials exist and how much of each a wafer consumes, but
never records whether a delivery was any good. Suppliers, certificates of
analysis and sampling verdicts are therefore entirely QMS facts.

A 20 percent reject-or-concession rate is high for a real fab. It is chosen
so the demo has enough material-quality stories to ask about, and the 40 rows
it produces are exactly the incoming-inspection share of the NCR budget."
```

---
### Task 6: 부적합 95행과 처리 95행

NCR과 처리는 1:1이고 날짜가 사슬로 이어지므로 한 함수에서 함께 만든다. 불일치 장치 ②④⑤가 여기서 심긴다.

**출처 배분(스펙 6.4):** 공정검사 43(IPQC 24 + IPQC-RT 10 + PCS 7 + EQV 2) + 출하검사 6 + 입고검사 40 + 고객제기 6 = 95

**Files:**
- Create: `customizing/fabric/qms-lakehouse/src/qms_nonconformance.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_qms_nonconformance.py`

**Interfaces:**
- Consumes: `MesSnapshot` (T1), `build_defect_codes()` (T2), `build_inspections()` / `mes_result_index()` (T3), `build_incoming_inspections()` (T5)
- Produces:
  - `build_nonconformances(snapshot, inspections, incoming, defect_codes) -> tuple[list[dict], list[dict]]` — `(ncrs 95, dispositions 95)`
  - `NCR_SOURCE_COUNTS: dict[str, int]` — `{"공정검사": 43, "출하검사": 6, "입고검사": 40, "고객제기": 6}`
  - `CUSTOMER_COMPLAINT_COUNT: int` — `6`
  - `STEP_DEFECT_MAP: dict[str, tuple[str, ...]]`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_qms_nonconformance.py`:

```python
import collections

from src.qms_incoming import build_incoming_inspections
from src.qms_inspection import build_inspections, mes_result_index
from src.qms_masters import build_defect_codes, build_inspectors
from src.qms_nonconformance import NCR_SOURCE_COUNTS, build_nonconformances


def build_ncr_bundle(snapshot):
    inspectors = build_inspectors()
    defect_codes = build_defect_codes()
    inspections = build_inspections(snapshot, inspectors)
    incoming = build_incoming_inspections(snapshot, inspectors, defect_codes)
    ncrs, dispositions = build_nonconformances(snapshot, inspections, incoming, defect_codes)
    return inspections, incoming, ncrs, dispositions


def test_ncr_and_disposition_are_95_and_one_to_one(snapshot):
    _, _, ncrs, dispositions = build_ncr_bundle(snapshot)
    assert len(ncrs) == 95
    assert len(dispositions) == 95
    assert len({n["ncr_id"] for n in ncrs}) == 95
    assert {d["ncr_id"] for d in dispositions} == {n["ncr_id"] for n in ncrs}


def test_ncr_sources_match_the_designed_split(snapshot):
    _, _, ncrs, _ = build_ncr_bundle(snapshot)
    assert collections.Counter(n["ncr_source"] for n in ncrs) == NCR_SOURCE_COUNTS


def test_every_flagged_inspection_produces_exactly_one_ncr(snapshot):
    inspections, _, ncrs, _ = build_ncr_bundle(snapshot)
    flagged = {i["inspection_id"] for i in inspections if i["has_nonconformance"]}
    linked = [n["inspection_id"] for n in ncrs if n["inspection_id"] is not None]
    assert len(linked) == len(flagged) == 49
    assert set(linked) == flagged


def test_every_rejected_or_concession_material_produces_one_ncr(snapshot):
    _, incoming, ncrs, _ = build_ncr_bundle(snapshot)
    expected = {r["iqc_id"] for r in incoming if r["judgment"] != "합격"}
    linked = [n["iqc_id"] for n in ncrs if n["iqc_id"] is not None]
    assert len(expected) == 40
    assert set(linked) == expected
    assert len(linked) == 40


def test_defect_code_and_parent_code_always_agree(snapshot):
    parent = {d["defect_code"]: d["mes_defect_code"] for d in build_defect_codes()}
    severity = {d["defect_code"]: d["severity"] for d in build_defect_codes()}
    _, _, ncrs, _ = build_ncr_bundle(snapshot)
    for ncr in ncrs:
        assert parent[ncr["defect_code"]] == ncr["mes_defect_code"]
        assert severity[ncr["defect_code"]] == ncr["severity"]


def test_customer_complaints_carry_product_but_no_lot(snapshot):
    _, _, ncrs, _ = build_ncr_bundle(snapshot)
    complaints = [n for n in ncrs if n["ncr_source"] == "고객제기"]
    assert len(complaints) == 6
    for ncr in complaints:
        assert ncr["lot_id"] is None
        assert ncr["inspection_id"] is None
        assert ncr["iqc_id"] is None
        assert ncr["product_code"] in {p["product_code"] for p in snapshot.products}


def test_device_2_two_mes_failures_were_shipped_under_concession(snapshot):
    inspections, _, ncrs, dispositions = build_ncr_bundle(snapshot)
    mes = mes_result_index(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    disposition_by_ncr = {d["ncr_id"]: d for d in dispositions}
    hits = []
    for ncr in ncrs:
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if not inspection or inspection["mes_process_result_id"] is None:
            continue
        if mes[inspection["mes_process_result_id"]]["result"] != "Fail":
            continue
        if disposition_by_ncr[ncr["ncr_id"]]["disposition_type"] == "특채":
            hits.append(ncr["ncr_id"])
    assert len(hits) == 2
    for ncr_id in hits:
        assert disposition_by_ncr[ncr_id]["decision_body_ko"] == "MRB"


def test_device_4_two_rejected_materials_reached_a_lot(snapshot):
    _, incoming, ncrs, _ = build_ncr_bundle(snapshot)
    iqc_by_id = {r["iqc_id"]: r for r in incoming}
    traced = [n for n in ncrs if n["ncr_source"] == "입고검사" and n["lot_id"] is not None]
    assert len(traced) == 2
    lots = {l["lot_id"]: l for l in snapshot.lots}
    bom_pairs = {(b["product_code"], b["material_code"], b["step_code"]) for b in snapshot.bom}
    for ncr in traced:
        material = iqc_by_id[ncr["iqc_id"]]["material_code"]
        assert ncr["material_code"] == material
        assert ncr["lot_id"] in lots
        assert lots[ncr["lot_id"]]["product_code"] == ncr["product_code"]
        assert (ncr["product_code"], material, ncr["step_code"]) in bom_pairs


def test_device_5_two_reworks_failed_and_were_scrapped(snapshot):
    _, _, _, dispositions = build_ncr_bundle(snapshot)
    hits = [
        d for d in dispositions
        if d["rework_result"] == "실패" and d["disposition_type"] == "폐기"
    ]
    assert len(hits) == 2
    for row in hits:
        assert row["rework_step_code"] is not None
        assert row["scrap_cost_krw"] > 0


def test_quality_hold_ncrs_blame_measurement(snapshot):
    inspections, _, ncrs, _ = build_ncr_bundle(snapshot)
    mes = mes_result_index(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    holds = []
    for ncr in ncrs:
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if not inspection or inspection["mes_process_result_id"] is None:
            continue
        source = mes[inspection["mes_process_result_id"]]
        if (
            source["result"] == "Pass"
            and not source.get("defect_code")
            and inspection["judgment"] == "불합격"
            and inspection["defect_found_qty"] == 0
        ):
            holds.append(ncr)
    assert len(holds) == 3
    assert all(n["root_cause_category"] == "측정" for n in holds)


def test_dates_and_quantities_form_a_valid_chain(snapshot):
    inspections, incoming, ncrs, dispositions = build_ncr_bundle(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    iqc_by_id = {r["iqc_id"]: r for r in incoming}
    disposition_by_ncr = {d["ncr_id"]: d for d in dispositions}
    for ncr in ncrs:
        disposition = disposition_by_ncr[ncr["ncr_id"]]
        if ncr["inspection_id"]:
            assert ncr["detected_date"] >= inspection_by_id[ncr["inspection_id"]][
                "inspection_datetime"
            ].date()
        if ncr["iqc_id"]:
            assert ncr["detected_date"] >= iqc_by_id[ncr["iqc_id"]]["inspection_date"]
        assert ncr["due_date"] > ncr["detected_date"]
        assert disposition["decision_date"] > ncr["detected_date"]
        assert disposition["effectiveness_check_date"] > disposition["decision_date"]
        assert 0 < disposition["disposition_qty"] <= ncr["affected_qty"]
        if ncr["closed_date"] is not None:
            assert ncr["closed_date"] >= disposition["decision_date"]


def test_affected_qty_never_exceeds_mes_output(snapshot):
    inspections, _, ncrs, _ = build_ncr_bundle(snapshot)
    mes = mes_result_index(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    for ncr in ncrs:
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if inspection and inspection["mes_process_result_id"] is not None:
            assert ncr["affected_qty"] <= mes[inspection["mes_process_result_id"]]["out_qty"]


def test_scrap_cost_only_on_scrapped_dispositions(snapshot):
    _, _, _, dispositions = build_ncr_bundle(snapshot)
    for row in dispositions:
        if row["disposition_type"] == "폐기":
            assert row["scrap_cost_krw"] > 0
        else:
            assert row["scrap_cost_krw"] == 0


def test_nonconformance_carries_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    _, _, ncrs, dispositions = build_ncr_bundle(snapshot)
    assert not (set(ncrs[0]) & forbidden)
    assert not (set(dispositions[0]) & forbidden)


def test_nonconformance_is_deterministic(snapshot):
    assert build_ncr_bundle(snapshot)[2:] == build_ncr_bundle(snapshot)[2:]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_nonconformance.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.qms_nonconformance'`

- [ ] **Step 3: 부적합·처리 모듈 작성**

BOM 실측 사실 두 가지를 코드가 전제한다. `RETICLE-5NM`은 BOM에 없어 로트로 추적할 수 없고, 실제 로트가 도달한 `(product_code, step_code)` 조합으로 좁히면 추적 가능한 자재는 7종이다. 장치④의 후보 풀은 이 7종에서만 뽑는다.

`src/qms_nonconformance.py`:

```python
"""qms_nonconformance 95행과 qms_disposition 95행.

둘은 1:1이고 날짜가 사슬로 이어지므로 한 함수에서 함께 만든다.
불일치 장치 ②(Fail인데 특채) ④(불합격 자재가 로트에 투입) ⑤(재작업 실패 후 폐기)가
여기서 심긴다.
"""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import MesSnapshot
from src.qms_inspection import mes_result_index
from src.qms_reference import (
    APPROVER_NAMES,
    BASE_DATE,
    OWNER_DEPTS,
    OWNER_NAMES,
    SEED_NCR,
)

NCR_SOURCE_COUNTS = {"공정검사": 43, "출하검사": 6, "입고검사": 40, "고객제기": 6}
CUSTOMER_COMPLAINT_COUNT = 6

_SOURCE_BY_INSPECTION_TYPE = {
    "IPQC": "공정검사",
    "IPQC-RT": "공정검사",
    "PCS": "공정검사",
    "EQV": "공정검사",
    "OQC": "출하검사",
}

STEP_DEFECT_MAP = {
    "DIFF": ("Contamination", "Particle"),
    "PHOTO": ("CD-OOS", "Overlay"),
    "ETCH": ("Etch-Residue", "CD-OOS"),
    "IMPL": ("Contamination",),
    "CVD": ("Particle", "Etch-Residue"),
    "CMP": ("Scratch", "Particle"),
    "METRO": ("CD-OOS", "Overlay"),
    "TEST": ("Contamination",),
    "PKG": ("Scratch",),
}

# '측정'은 장치① 전용으로 예약한다. 다른 NCR이 같은 원인을 쓰면 이야기가 흐려진다.
_GENERAL_CAUSES = ("설비", "자재", "작업방법", "환경")

_ROOT_CAUSE_TEXT = {
    "설비": "설비 파라미터가 점진적으로 드리프트해 관리한계를 벗어남",
    "자재": "입고 자재 로트 간 편차가 공정 결과로 전이됨",
    "작업방법": "개정된 작업표준이 현장 레시피에 반영되지 않음",
    "환경": "클린룸 온습도 변동이 공정 안정성에 영향을 줌",
    "측정": "계측 재현성 저하로 실제 품질과 판정이 어긋남",
}

_IMMEDIATE_ACTION = {
    "설비": "해당 설비 가동 중지 후 파라미터 재설정 및 검증 런 수행",
    "자재": "동일 공급 로트 전량 격리 및 대체 로트 투입",
    "작업방법": "작업표준 최신본 재배포 및 교대조 교육 실시",
    "환경": "공조 설정 재조정 및 파티클 모니터링 강화",
    "측정": "계측기 재교정 및 Gage R&R 재평가",
}

_ESTIMATED_COST = {"Critical": (20_000_000, 80_000_000), "Major": (5_000_000, 20_000_000), "Minor": (500_000, 5_000_000)}
_SCRAP_UNIT_COST = (1_200_000, 3_500_000)

_COMPLAINT_TEXT = "고객 현장에서 반환된 제품의 분석 결과 품질 이슈 확인"


def build_nonconformances(
    snapshot: MesSnapshot,
    inspections: list[dict],
    incoming: list[dict],
    defect_codes: list[dict],
) -> tuple[list[dict], list[dict]]:
    rng = random.Random(SEED_NCR)
    mes = mes_result_index(snapshot)
    defect_by_code = {d["defect_code"]: d for d in defect_codes}
    by_mes_defect: dict[str, list[dict]] = {}
    for row in defect_codes:
        by_mes_defect.setdefault(row["mes_defect_code"], []).append(row)
    lots = {l["lot_id"]: l for l in snapshot.lots}
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    products = sorted(snapshot.products, key=lambda p: p["product_code"])
    iqc_by_id = {r["iqc_id"]: r for r in incoming}

    seeds = _collect_seeds(inspections, incoming)
    devices = _pick_devices(rng, seeds, inspections, mes, snapshot, iqc_by_id)

    ncrs: list[dict] = []
    dispositions: list[dict] = []
    inspection_by_id = {i["inspection_id"]: i for i in inspections}

    for position, seed in enumerate(seeds):
        ncr_id = f"NCR-2026-{position + 1:04d}"
        if seed[0] == "inspection":
            ncr = _ncr_from_inspection(
                rng, ncr_id, inspection_by_id[seed[1]], mes, lots, by_mes_defect, devices
            )
        elif seed[0] == "iqc":
            ncr = _ncr_from_iqc(
                rng, ncr_id, iqc_by_id[seed[1]], defect_by_code, lots, step_names, devices
            )
        else:
            ncr = _ncr_from_complaint(rng, ncr_id, products[position % len(products)], defect_codes)
        disposition = _disposition_for(
            rng, f"DSP-2026-{position + 1:04d}", ncr, devices, step_names
        )
        if ncr["status"] == "완료":
            ncr["closed_date"] = disposition["decision_date"] + dt.timedelta(days=rng.randint(0, 3))
        ncrs.append(ncr)
        dispositions.append(disposition)
    return ncrs, dispositions


def _collect_seeds(inspections: list[dict], incoming: list[dict]) -> list[tuple[str, object]]:
    seeds: list[tuple[str, object]] = [
        ("inspection", i["inspection_id"]) for i in inspections if i["has_nonconformance"]
    ]
    seeds += [("iqc", r["iqc_id"]) for r in incoming if r["judgment"] != "합격"]
    seeds += [("complaint", n) for n in range(CUSTOMER_COMPLAINT_COUNT)]
    return seeds


def _pick_devices(rng, seeds, inspections, mes, snapshot, iqc_by_id) -> dict:
    """장치 ②④⑤가 붙을 대상을 미리 정한다. 건수를 확정적으로 고정하기 위함이다."""
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    fails, reworks = [], []
    for kind, key in seeds:
        if kind != "inspection":
            continue
        inspection = inspection_by_id[key]
        if inspection["inspection_type"] != "IPQC":
            continue
        result = mes[inspection["mes_process_result_id"]]["result"]
        if result == "Fail":
            fails.append(key)
        elif result == "Rework":
            reworks.append(key)

    rng.shuffle(fails)
    device2 = set(fails[:2])
    device5 = set(reworks)
    assert len(device2) == 2 and len(device5) == 2

    # 장치④: 불합격/특채 자재 중 BOM으로 실제 로트까지 이어지는 것만 후보다.
    # RETICLE-5NM 은 BOM에 없고, 로트가 도달하지 못한 공정의 자재도 이어지지 않는다.
    reachable = _reachable_bom(snapshot)
    candidates = [
        key for kind, key in seeds
        if kind == "iqc" and reachable.get(iqc_by_id[key]["material_code"])
    ]
    assert len(candidates) >= 2, "장치④ 후보 자재가 부족합니다."
    rng.shuffle(candidates)
    device4 = {}
    for key in candidates[:2]:
        material = iqc_by_id[key]["material_code"]
        product_code, step_code, lot_ids = rng.choice(reachable[material])
        device4[key] = (product_code, step_code, rng.choice(lot_ids))
    return {"device2": device2, "device4": device4, "device5": device5}


def _reachable_bom(snapshot: MesSnapshot) -> dict[str, list[tuple[str, str, list[str]]]]:
    """자재 → [(제품, 공정, 그 공정을 실제로 지난 로트들)]."""
    product_of = {l["lot_id"]: l["product_code"] for l in snapshot.lots}
    lots_at: dict[tuple[str, str], list[str]] = {}
    for row in snapshot.process_results:
        key = (product_of[row["lot_id"]], row["step_code"])
        bucket = lots_at.setdefault(key, [])
        if row["lot_id"] not in bucket:
            bucket.append(row["lot_id"])
    reachable: dict[str, list[tuple[str, str, list[str]]]] = {}
    for entry in snapshot.bom:
        key = (entry["product_code"], entry["step_code"])
        if key in lots_at:
            reachable.setdefault(entry["material_code"], []).append(
                (entry["product_code"], entry["step_code"], sorted(lots_at[key]))
            )
    return reachable


def _base_ncr(rng, ncr_id: str, source: str, defect: dict, detected: dt.date) -> dict:
    cause = rng.choice(_GENERAL_CAUSES)
    low, high = _ESTIMATED_COST[defect["severity"]]
    status = rng.choices(
        ("완료", "조사중", "처리대기", "접수", "보류"), weights=(45, 20, 15, 15, 5)
    )[0]
    return {
        "ncr_id": ncr_id,
        "ncr_source": source,
        "inspection_id": None,
        "iqc_id": None,
        "lot_id": None,
        "product_code": None,
        "product_name": None,
        "step_code": None,
        "step_name": None,
        "material_code": None,
        "eqp_id": None,
        "defect_code": defect["defect_code"],
        "mes_defect_code": defect["mes_defect_code"],
        "severity": defect["severity"],
        "affected_qty": 1,
        "detected_date": detected,
        "root_cause_category": cause,
        "root_cause_ko": _ROOT_CAUSE_TEXT[cause],
        "immediate_action_ko": _IMMEDIATE_ACTION[cause],
        "owner_dept_ko": rng.choice(OWNER_DEPTS),
        "owner_name": rng.choice(OWNER_NAMES),
        "status": status,
        "due_date": detected + dt.timedelta(days=rng.randint(7, 14)),
        "closed_date": None,
        "estimated_cost_krw": rng.randrange(low, high, 100_000),
    }


def _ncr_from_inspection(rng, ncr_id, inspection, mes, lots, by_mes_defect, devices) -> dict:
    mes_row = mes.get(inspection["mes_process_result_id"]) if inspection["mes_process_result_id"] else None
    if mes_row and mes_row.get("defect_code"):
        parent = mes_row["defect_code"]
    else:
        parent = rng.choice(STEP_DEFECT_MAP[inspection["step_code"]])
    defect = rng.choice(by_mes_defect[parent])
    detected = inspection["inspection_datetime"].date() + dt.timedelta(days=rng.randint(0, 2))
    ncr = _base_ncr(
        rng, ncr_id, _SOURCE_BY_INSPECTION_TYPE[inspection["inspection_type"]], defect, detected
    )
    ncr["inspection_id"] = inspection["inspection_id"]
    ncr["lot_id"] = inspection["lot_id"]
    ncr["product_code"] = inspection["product_code"]
    ncr["product_name"] = inspection["product_name"]
    ncr["step_code"] = inspection["step_code"]
    ncr["step_name"] = inspection["step_name"]
    ncr["eqp_id"] = inspection["eqp_id"]

    if mes_row is not None:
        ncr["affected_qty"] = min(rng.randint(1, 12), mes_row["out_qty"])
        is_quality_hold = (
            mes_row["result"] == "Pass"
            and not mes_row.get("defect_code")
            and inspection["judgment"] == "불합격"
            and inspection["defect_found_qty"] == 0
        )
        if is_quality_hold:
            ncr["root_cause_category"] = "측정"
            ncr["root_cause_ko"] = _ROOT_CAUSE_TEXT["측정"]
            ncr["immediate_action_ko"] = _IMMEDIATE_ACTION["측정"]
    elif inspection["lot_id"]:
        ncr["affected_qty"] = min(rng.randint(1, 12), lots[inspection["lot_id"]]["wafer_qty"])
    else:
        ncr["affected_qty"] = rng.randint(1, 8)
    return ncr


def _ncr_from_iqc(rng, ncr_id, iqc, defect_by_code, lots, step_names, devices) -> dict:
    defect = defect_by_code[iqc["defect_code"]]
    detected = iqc["inspection_date"] + dt.timedelta(days=rng.randint(0, 2))
    ncr = _base_ncr(rng, ncr_id, "입고검사", defect, detected)
    ncr["iqc_id"] = iqc["iqc_id"]
    ncr["material_code"] = iqc["material_code"]
    ncr["affected_qty"] = max(1, min(rng.randint(1, 50), int(iqc["received_qty"])))
    ncr["root_cause_category"] = "자재"
    ncr["root_cause_ko"] = _ROOT_CAUSE_TEXT["자재"]
    ncr["immediate_action_ko"] = _IMMEDIATE_ACTION["자재"]

    traced = devices["device4"].get(iqc["iqc_id"])
    if traced:
        product_code, step_code, lot_id = traced
        ncr["product_code"] = product_code
        ncr["product_name"] = lots[lot_id]["product_name"]
        ncr["step_code"] = step_code
        ncr["step_name"] = step_names[step_code]
        ncr["lot_id"] = lot_id
        ncr["immediate_action_ko"] = "해당 자재가 투입된 로트를 역추적해 후속 공정 홀드"
    return ncr


def _ncr_from_complaint(rng, ncr_id, product, defect_codes) -> dict:
    defect = rng.choice(defect_codes)
    detected = BASE_DATE + dt.timedelta(days=rng.randint(3, 10))
    ncr = _base_ncr(rng, ncr_id, "고객제기", defect, detected)
    ncr["product_code"] = product["product_code"]
    ncr["product_name"] = product["product_name"]
    ncr["affected_qty"] = rng.randint(1, 10)
    ncr["root_cause_ko"] = _COMPLAINT_TEXT
    return ncr


def _disposition_for(rng, disposition_id: str, ncr: dict, devices: dict, step_names: dict) -> dict:
    inspection_id = ncr["inspection_id"]
    rework_step, rework_result, scrap_cost = None, None, 0

    if inspection_id in devices["device2"]:
        disposition_type, decision_body = "특채", "MRB"
    elif inspection_id in devices["device5"]:
        # 재작업을 시도했으나 실패해 결국 폐기된 건. 두 사실이 한 행에 함께 남는다.
        disposition_type, decision_body = "폐기", "MRB"
        rework_step, rework_result = ncr["step_code"], "실패"
    elif ncr["ncr_source"] == "입고검사":
        disposition_type = "반품" if ncr["defect_code"] and ncr["severity"] != "Minor" else "특채"
        decision_body = "품질책임자"
    elif ncr["ncr_source"] == "고객제기":
        disposition_type = rng.choice(("선별", "폐기", "반품"))
        decision_body = "품질책임자"
    else:
        # 특채는 장치②와 입고검사에만 허용한다. 여기서 새면 장치② 건수가 흔들린다.
        disposition_type = rng.choice(("재작업", "선별", "폐기"))
        decision_body = rng.choice(("MRB", "품질책임자", "생산책임자"))
        if disposition_type == "재작업":
            rework_step = ncr["step_code"]
            rework_result = rng.choice(("성공", "성공", "진행중"))

    disposition_qty = rng.randint(1, ncr["affected_qty"])
    if disposition_type == "폐기":
        scrap_cost = disposition_qty * rng.randrange(*_SCRAP_UNIT_COST, 100_000)
    decision_date = ncr["detected_date"] + dt.timedelta(days=rng.randint(1, 5))
    return {
        "disposition_id": disposition_id,
        "ncr_id": ncr["ncr_id"],
        "lot_id": ncr["lot_id"],
        "product_code": ncr["product_code"],
        "step_code": ncr["step_code"],
        "disposition_type": disposition_type,
        "disposition_qty": disposition_qty,
        "decision_date": decision_date,
        "decision_body_ko": decision_body,
        "approver_name": rng.choice(APPROVER_NAMES),
        "approval_status": rng.choices(("승인", "대기", "반려"), weights=(85, 10, 5))[0],
        "rework_step_code": rework_step,
        "rework_result": rework_result,
        "scrap_cost_krw": scrap_cost,
        "effectiveness_check_date": decision_date + dt.timedelta(days=rng.randint(7, 21)),
        "effectiveness_result": rng.choices(("유효", "재발", "확인중"), weights=(70, 10, 20))[0],
        "reason_ko": f"{ncr['severity']} 등급 부적합에 대한 {disposition_type} 결정",
    }

```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_nonconformance.py -q`
Expected: PASS — `15 passed`

실패 시 확인 순서: ① `_collect_seeds` 가 49 + 40 + 6 = 95를 만드는지, ② `_pick_devices` 의 `assert` 가 걸리는지, ③ `_disposition_for` 에서 `특채`가 장치② 밖으로 새는지.

- [ ] **Step 5: 전체 회귀 실행**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest -q`
Expected: PASS — `70 passed`

- [ ] **Step 6: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Generate 95 nonconformance reports with matching dispositions

NCRs and dispositions are built together because their dates form a chain:
detection follows inspection, decision follows detection, effectiveness check
follows decision. Splitting them into two passes would mean threading dates
through a second lookup for no benefit.

Concession is deliberately restricted to device 2 and to incoming material.
If any other process NCR could be dispositioned as 특채, the query behind
'which failed lots shipped anyway' would return more than the two rows the
design promises.

Device 4 can only use materials that BOM actually connects to a lot that
reached the consuming step. RETICLE-5NM has no BOM entry at all, and several
steps have no lot history, which leaves seven traceable materials."
```

---
### Task 7: 검증 8항목

스펙 8절의 8개 항목을 데이터만 보고 판정하는 순수 함수로 구현한다. 항목 2·3·4는 치명적이라 실패 시 예외를 던져 적재를 막는다. 항목 8의 질의는 노트북 사용자가 나중에 SQL로 그대로 재현할 수 있어야 하므로, 전용 표시 컬럼 없이 오직 값 조건만으로 장치를 찾아낸다.

**Files:**
- Create: `customizing/fabric/qms-lakehouse/src/qms_validate.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_qms_validate.py`
- Modify: `customizing/fabric/qms-lakehouse/tests/conftest.py` (`tables` fixture 추가)

**Interfaces:**
- Consumes: 태스크 1~6의 모든 생성 함수
- Produces:
  - `ValidationResult` — frozen dataclass. 필드 `name: str`, `passed: bool`, `detail: str`, `fatal: bool`
  - `validate(snapshot: MesSnapshot, tables: dict[str, list[dict]]) -> list[ValidationResult]`
  - `raise_on_fatal(results: list[ValidationResult]) -> None`
  - `format_report(results: list[ValidationResult]) -> str`
  - `TABLE_ROW_TARGETS: dict[str, int]`
  - `FORBIDDEN_COLUMNS: frozenset[str]`
  - `DEVICE_TARGETS: dict[str, int]`
  - `ValidationError(RuntimeError)`

- [ ] **Step 1: conftest에 tables fixture 추가**

`tests/conftest.py`에 추가:

```python
from src.qms_incoming import build_incoming_inspections
from src.qms_inspection import build_inspections
from src.qms_masters import build_defect_codes, build_inspection_specs, build_inspectors
from src.qms_measurement import build_measurements
from src.qms_nonconformance import build_nonconformances


@pytest.fixture(scope="session")
def tables(snapshot) -> dict[str, list[dict]]:
    inspectors = build_inspectors()
    defect_codes = build_defect_codes()
    specs = build_inspection_specs(snapshot)
    inspections = build_inspections(snapshot, inspectors)
    measurements = build_measurements(inspections, specs)
    incoming = build_incoming_inspections(snapshot, inspectors, defect_codes)
    ncrs, dispositions = build_nonconformances(snapshot, inspections, incoming, defect_codes)
    return {
        "qms_defect_code": defect_codes,
        "qms_inspection_spec": specs,
        "qms_inspector": inspectors,
        "qms_inspection": inspections,
        "qms_measurement": measurements,
        "qms_incoming_inspection": incoming,
        "qms_nonconformance": ncrs,
        "qms_disposition": dispositions,
    }
```

- [ ] **Step 2: 실패 테스트 작성**

`tests/test_qms_validate.py`:

```python
import copy

import pytest

from src.qms_validate import (
    DEVICE_TARGETS,
    TABLE_ROW_TARGETS,
    ValidationError,
    format_report,
    raise_on_fatal,
    validate,
)


def test_all_eight_checks_pass_on_generated_data(snapshot, tables):
    results = validate(snapshot, tables)
    assert len(results) == 8
    failed = [r for r in results if not r.passed]
    assert not failed, format_report(results)


def test_row_counts_total_1004(tables):
    assert sum(TABLE_ROW_TARGETS.values()) == 1004
    for name, target in TABLE_ROW_TARGETS.items():
        assert len(tables[name]) == target


def test_device_targets_are_3_2_4_2_2():
    assert list(DEVICE_TARGETS.values()) == [3, 2, 4, 2, 2]


def test_orphan_lot_id_is_detected_and_fatal(snapshot, tables):
    broken = copy.deepcopy(tables)
    broken["qms_inspection"][0]["lot_id"] = "LOT9999"
    result = next(r for r in validate(snapshot, broken) if r.name == "고아 키")
    assert not result.passed
    assert result.fatal
    assert "LOT9999" in result.detail


def test_forbidden_column_is_detected_and_fatal(snapshot, tables):
    broken = copy.deepcopy(tables)
    for row in broken["qms_inspection"]:
        row["scrap_qty"] = 0
    result = next(r for r in validate(snapshot, broken) if r.name == "무중복 위반")
    assert not result.passed
    assert result.fatal
    assert "scrap_qty" in result.detail


def test_broken_internal_reference_is_detected_and_fatal(snapshot, tables):
    broken = copy.deepcopy(tables)
    broken["qms_measurement"][0]["spec_id"] = "SPEC-NOPE-NOPE-NOPE"
    result = next(r for r in validate(snapshot, broken) if r.name == "내부 FK")
    assert not result.passed
    assert result.fatal


def test_mismatched_out_of_spec_flag_is_detected(snapshot, tables):
    broken = copy.deepcopy(tables)
    row = broken["qms_measurement"][0]
    row["is_out_of_spec"] = not row["is_out_of_spec"]
    result = next(r for r in validate(snapshot, broken) if r.name == "측정치 규격")
    assert not result.passed
    assert not result.fatal


def test_removed_device_row_is_detected(snapshot, tables):
    broken = copy.deepcopy(tables)
    for row in broken["qms_disposition"]:
        if row["rework_result"] == "실패":
            row["rework_result"] = "성공"
            break
    result = next(r for r in validate(snapshot, broken) if r.name == "불일치 장치")
    assert not result.passed


def test_raise_on_fatal_only_raises_for_fatal_failures(snapshot, tables):
    raise_on_fatal(validate(snapshot, tables))
    broken = copy.deepcopy(tables)
    broken["qms_inspection"][0]["product_code"] = "NOPE"
    with pytest.raises(ValidationError):
        raise_on_fatal(validate(snapshot, broken))
    tolerable = copy.deepcopy(tables)
    row = tolerable["qms_measurement"][0]
    row["is_out_of_spec"] = not row["is_out_of_spec"]
    raise_on_fatal(validate(snapshot, tolerable))


def test_report_lists_every_check(snapshot, tables):
    report = format_report(validate(snapshot, tables))
    for name in ("행수", "고아 키", "무중복 위반", "내부 FK", "시간 인과", "수량 정합", "측정치 규격", "불일치 장치"):
        assert name in report
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_validate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.qms_validate'`

- [ ] **Step 4: 검증 모듈 작성**

`src/qms_validate.py`:

```python
"""스펙 8절 검증 8항목.

장치 검출 질의는 데이터 값만으로 성립해야 한다. 전용 플래그 컬럼을 두면
검증은 쉬워지지만 '두 시스템을 함께 봐야 답이 나온다'는 전제가 무너진다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from src.mes_client import MesSnapshot

TABLE_ROW_TARGETS = {
    "qms_defect_code": 24,
    "qms_inspection_spec": 108,
    "qms_inspector": 15,
    "qms_inspection": 207,
    "qms_measurement": 260,
    "qms_incoming_inspection": 200,
    "qms_nonconformance": 95,
    "qms_disposition": 95,
}

FORBIDDEN_COLUMNS = frozenset({"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"})

DEVICE_TARGETS = {
    "① MES Pass ↔ QMS 불합격": 3,
    "② MES Fail ↔ QMS 특채": 2,
    "③ MES 불량코드 없음 ↔ QMS 결함 검출": 4,
    "④ IQC 불합격 자재가 로트에 투입": 2,
    "⑤ MES Rework ↔ 재작업 실패 후 폐기": 2,
}


class ValidationError(RuntimeError):
    """치명 항목이 실패했을 때. 적재를 중단시킨다."""


@dataclass(frozen=True)
class ValidationResult:
    name: str
    passed: bool
    detail: str
    fatal: bool


def validate(snapshot: MesSnapshot, tables: dict[str, list[dict]]) -> list[ValidationResult]:
    return [
        _check_row_counts(tables),
        _check_orphan_keys(snapshot, tables),
        _check_forbidden_columns(tables),
        _check_internal_references(tables),
        _check_time_causality(snapshot, tables),
        _check_quantities(tables),
        _check_measurement_limits(tables),
        _check_devices(snapshot, tables),
    ]


def raise_on_fatal(results: list[ValidationResult]) -> None:
    fatal = [r for r in results if r.fatal and not r.passed]
    if fatal:
        raise ValidationError("치명 검증 실패:\n" + "\n".join(f"- {r.name}: {r.detail}" for r in fatal))


def format_report(results: list[ValidationResult]) -> str:
    width = max(len(r.name) for r in results)
    lines = [f"{'항목'.ljust(width)}  결과  상세", "-" * (width + 40)]
    for result in results:
        lines.append(f"{result.name.ljust(width)}  {'PASS' if result.passed else 'FAIL'}  {result.detail}")
    return "\n".join(lines)


def _result(name: str, problems: list[str], ok_detail: str, fatal: bool = False) -> ValidationResult:
    if problems:
        return ValidationResult(name, False, "; ".join(problems[:5]), fatal)
    return ValidationResult(name, True, ok_detail, fatal)


def _check_row_counts(tables) -> ValidationResult:
    problems = [
        f"{name} {len(tables[name])}행 (목표 {target})"
        for name, target in TABLE_ROW_TARGETS.items()
        if len(tables[name]) != target
    ]
    return _result("행수", problems, f"8개 테이블 총 {sum(len(v) for v in tables.values())}행")


def _check_orphan_keys(snapshot, tables) -> ValidationResult:
    known = {
        "lot_id": {l["lot_id"] for l in snapshot.lots},
        "product_code": {p["product_code"] for p in snapshot.products},
        "step_code": {s["step_code"] for s in snapshot.route},
        "material_code": {m["material_code"] for m in snapshot.materials},
        "eqp_id": {e["eqp_id"] for e in snapshot.equipment},
    }
    problems = []
    for table_name, rows in tables.items():
        for row in rows:
            for column, allowed in known.items():
                value = row.get(column)
                if value is not None and value not in allowed:
                    problems.append(f"{table_name}.{column}={value}")
    return _result("고아 키", problems, "MES 미존재 키 0건", fatal=True)


def _check_forbidden_columns(tables) -> ValidationResult:
    problems = []
    for table_name, rows in tables.items():
        if rows:
            for column in sorted(set(rows[0]) & FORBIDDEN_COLUMNS):
                problems.append(f"{table_name}.{column}")
    return _result("무중복 위반", problems, "MES 중복 컬럼 0건", fatal=True)


def _check_internal_references(tables) -> ValidationResult:
    inspection_ids = {r["inspection_id"] for r in tables["qms_inspection"]}
    spec_ids = {r["spec_id"] for r in tables["qms_inspection_spec"]}
    inspector_ids = {r["inspector_id"] for r in tables["qms_inspector"]}
    defect_ids = {r["defect_code"] for r in tables["qms_defect_code"]}
    ncr_ids = {r["ncr_id"] for r in tables["qms_nonconformance"]}
    iqc_ids = {r["iqc_id"] for r in tables["qms_incoming_inspection"]}

    references = [
        ("qms_inspection.inspector_id", [r["inspector_id"] for r in tables["qms_inspection"]], inspector_ids),
        ("qms_measurement.inspection_id", [r["inspection_id"] for r in tables["qms_measurement"]], inspection_ids),
        ("qms_measurement.spec_id", [r["spec_id"] for r in tables["qms_measurement"]], spec_ids),
        ("qms_measurement.measured_by", [r["measured_by"] for r in tables["qms_measurement"]], inspector_ids),
        ("qms_incoming_inspection.inspector_id", [r["inspector_id"] for r in tables["qms_incoming_inspection"]], inspector_ids),
        ("qms_incoming_inspection.defect_code", [r["defect_code"] for r in tables["qms_incoming_inspection"]], defect_ids),
        ("qms_nonconformance.inspection_id", [r["inspection_id"] for r in tables["qms_nonconformance"]], inspection_ids),
        ("qms_nonconformance.iqc_id", [r["iqc_id"] for r in tables["qms_nonconformance"]], iqc_ids),
        ("qms_nonconformance.defect_code", [r["defect_code"] for r in tables["qms_nonconformance"]], defect_ids),
        ("qms_disposition.ncr_id", [r["ncr_id"] for r in tables["qms_disposition"]], ncr_ids),
    ]
    problems = []
    for label, values, allowed in references:
        missing = {v for v in values if v is not None and v not in allowed}
        if missing:
            problems.append(f"{label} → {sorted(missing)[:3]}")
    return _result("내부 FK", problems, "내부 참조 전건 유효", fatal=True)


def _check_time_causality(snapshot, tables) -> ValidationResult:
    out_time = {r["id"]: dt.datetime.fromisoformat(r["out_time"]) for r in snapshot.process_results}
    inspection_by_id = {r["inspection_id"]: r for r in tables["qms_inspection"]}
    ncr_by_id = {r["ncr_id"]: r for r in tables["qms_nonconformance"]}
    problems = []

    for row in tables["qms_inspection"]:
        mes_id = row["mes_process_result_id"]
        if mes_id is not None and row["inspection_datetime"] < out_time[mes_id]:
            problems.append(f"{row['inspection_id']} 검사시각이 MES 종료시각보다 이르다")
    for row in tables["qms_nonconformance"]:
        inspection = inspection_by_id.get(row["inspection_id"])
        if inspection and row["detected_date"] < inspection["inspection_datetime"].date():
            problems.append(f"{row['ncr_id']} 검출일이 검사일보다 이르다")
    for row in tables["qms_disposition"]:
        if row["decision_date"] <= ncr_by_id[row["ncr_id"]]["detected_date"]:
            problems.append(f"{row['disposition_id']} 결정일이 검출일 이전이다")
    return _result("시간 인과", problems, "검사 → 부적합 → 처리 순서 성립")


def _check_quantities(tables) -> ValidationResult:
    affected = {r["ncr_id"]: r["affected_qty"] for r in tables["qms_nonconformance"]}
    problems = []
    for row in tables["qms_inspection"]:
        if row["defect_found_qty"] > row["sample_size"]:
            problems.append(f"{row['inspection_id']} 검출수 > 샘플수")
    for row in tables["qms_disposition"]:
        if row["disposition_qty"] > affected[row["ncr_id"]]:
            problems.append(f"{row['disposition_id']} 처리수량 > 영향수량")
    return _result("수량 정합", problems, "수량 대소관계 성립")


def _check_measurement_limits(tables) -> ValidationResult:
    problems = []
    for row in tables["qms_measurement"]:
        outside = row["measured_value"] < row["lsl"] or row["measured_value"] > row["usl"]
        if outside != row["is_out_of_spec"]:
            problems.append(f"{row['measurement_id']} 규격이탈 플래그 불일치")
        if row["judgment"] != ("NG" if outside else "OK"):
            problems.append(f"{row['measurement_id']} 판정 불일치")
    return _result("측정치 규격", problems, "규격 이탈 플래그 전건 일치")


def _check_devices(snapshot, tables) -> ValidationResult:
    counts = count_devices(snapshot, tables)
    problems = [
        f"{name} {counts[name]}건 (설계 {target}건)"
        for name, target in DEVICE_TARGETS.items()
        if counts[name] != target
    ]
    return _result("불일치 장치", problems, "5종 13건 전부 검출")


def count_devices(snapshot: MesSnapshot, tables: dict[str, list[dict]]) -> dict[str, int]:
    """전용 표시 컬럼 없이 값 조건만으로 장치를 세어 본다."""
    mes = {r["id"]: r for r in snapshot.process_results}
    inspection_by_id = {r["inspection_id"]: r for r in tables["qms_inspection"]}
    disposition_by_ncr = {r["ncr_id"]: r for r in tables["qms_disposition"]}
    lots = {l["lot_id"] for l in snapshot.lots}

    device1 = device3 = device2 = device4 = device5 = 0

    for row in tables["qms_inspection"]:
        mes_id = row["mes_process_result_id"]
        if mes_id is None:
            continue
        source = mes[mes_id]
        if source.get("defect_code"):
            continue
        if source["result"] == "Pass" and row["judgment"] == "불합격" and row["defect_found_qty"] == 0:
            device1 += 1
        if row["defect_found_qty"] > 0 and row["judgment"] != "불합격":
            device3 += 1

    for ncr in tables["qms_nonconformance"]:
        disposition = disposition_by_ncr[ncr["ncr_id"]]
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if (
            inspection
            and inspection["mes_process_result_id"] is not None
            and mes[inspection["mes_process_result_id"]]["result"] == "Fail"
            and disposition["disposition_type"] == "특채"
        ):
            device2 += 1
        if ncr["ncr_source"] == "입고검사" and ncr["lot_id"] in lots:
            device4 += 1

    for row in tables["qms_disposition"]:
        if row["rework_result"] == "실패" and row["disposition_type"] == "폐기":
            device5 += 1

    return dict(zip(DEVICE_TARGETS, (device1, device2, device3, device4, device5)))
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_validate.py -q`
Expected: PASS — `10 passed`

- [ ] **Step 6: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Add eight-item validation with fatal gating

Orphan keys, duplicated MES columns and broken internal references make the
data actively misleading rather than merely imperfect, so those three abort
the load. A stale out-of-spec flag is reported and survives.

The device counter deliberately re-derives all thirteen planted mismatches
from column values alone. If a marker column were added to make counting
easy, the demo question 'which lots did production pass but quality reject'
would be answerable from one table, which defeats the point."
```

---
### Task 8: 테이블 스키마와 노트북 빌드

Fabric에 올릴 최종 산출물을 만든다. Spark 스키마는 DDL 문자열로 표현해 pyspark 없이도 로컬에서 검증한다. `build_notebook.py`는 `src/` 모듈 소스를 노트북 셀로 인라인 전개해, 파일 업로드나 `pip install` 없이 단독 실행되는 `.ipynb`를 만든다.

**Files:**
- Create: `customizing/fabric/qms-lakehouse/src/qms_schema.py`
- Create: `customizing/fabric/qms-lakehouse/build_notebook.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_qms_schema.py`
- Create: `customizing/fabric/qms-lakehouse/tests/test_build_notebook.py`
- Modify: `customizing/fabric/qms-lakehouse/tests/conftest.py` (`tables` fixture를 `build_all_tables` 위임으로 교체)
- Create: `customizing/fabric/qms-lakehouse/qms_lakehouse_seed.ipynb` (빌드 산출물)

**Interfaces:**
- Consumes: 태스크 1~7의 모든 생성·검증 함수
- Produces:
  - `TABLE_COLUMNS: dict[str, tuple[str, ...]]` — 테이블별 컬럼 순서
  - `TABLE_DDL: dict[str, str]` — Spark DDL 문자열
  - `ddl_columns(ddl: str) -> tuple[str, ...]`
  - `build_all_tables(snapshot: MesSnapshot) -> dict[str, list[dict]]`
  - `to_rows(table_name: str, rows: list[dict]) -> list[tuple]`
  - `MODULE_ORDER: tuple[str, ...]`, `strip_local_imports(source: str) -> str`, `build_notebook(root: Path) -> nbformat.NotebookNode`, `main() -> None`

- [ ] **Step 1: 스키마 실패 테스트 작성**

`tests/test_qms_schema.py`:

```python
from src.qms_schema import (
    TABLE_COLUMNS,
    TABLE_DDL,
    build_all_tables,
    ddl_columns,
    to_rows,
)
from src.qms_validate import TABLE_ROW_TARGETS


def test_every_target_table_has_columns_and_ddl():
    assert set(TABLE_COLUMNS) == set(TABLE_DDL) == set(TABLE_ROW_TARGETS)


def test_ddl_column_names_match_declared_column_order():
    for name, ddl in TABLE_DDL.items():
        assert ddl_columns(ddl) == TABLE_COLUMNS[name]


def test_generated_rows_match_declared_columns_including_order(tables):
    for name, rows in tables.items():
        assert tuple(rows[0]) == TABLE_COLUMNS[name]
        for row in rows:
            assert set(row) == set(TABLE_COLUMNS[name])


def test_to_rows_produces_tuples_in_column_order(tables):
    for name, rows in tables.items():
        tuples = to_rows(name, rows)
        assert len(tuples) == len(rows)
        assert all(len(t) == len(TABLE_COLUMNS[name]) for t in tuples)
        first_column = TABLE_COLUMNS[name][0]
        assert tuples[0][0] == rows[0][first_column]


def test_build_all_tables_matches_the_fixture(snapshot, tables):
    assert build_all_tables(snapshot) == tables


def test_no_ddl_declares_a_forbidden_column():
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    for name, ddl in TABLE_DDL.items():
        assert not (set(ddl_columns(ddl)) & forbidden), name
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.qms_schema'`

- [ ] **Step 3: 스키마 모듈 작성**

`src/qms_schema.py`:

```python
"""Spark 테이블 스키마와 전체 조립.

pyspark 를 로컬에서 import 할 수 없으므로 스키마를 DDL 문자열로 표현한다.
문자열은 pytest 에서 검증할 수 있고, Fabric 에서는
spark.createDataFrame(rows, schema=TABLE_DDL[name]) 로 그대로 쓰인다.
전부 None 인 컬럼이 있어 타입 추론에 맡길 수 없다.
"""

from __future__ import annotations

from src.mes_client import MesSnapshot
from src.qms_incoming import build_incoming_inspections
from src.qms_inspection import build_inspections
from src.qms_masters import build_defect_codes, build_inspection_specs, build_inspectors
from src.qms_measurement import build_measurements
from src.qms_nonconformance import build_nonconformances

TABLE_DDL = {
    "qms_defect_code": (
        "defect_code STRING, defect_name_ko STRING, defect_name_en STRING, "
        "mes_defect_code STRING, defect_category STRING, severity STRING, "
        "severity_score INT, typical_step_codes STRING, standard_cause_ko STRING, "
        "standard_action_ko STRING, is_active BOOLEAN"
    ),
    "qms_inspection_spec": (
        "spec_id STRING, product_code STRING, product_name STRING, step_code STRING, "
        "step_name STRING, characteristic_code STRING, characteristic_name_ko STRING, "
        "measurement_type STRING, unit STRING, target_value DOUBLE, lsl DOUBLE, "
        "usl DOUBLE, cpk_target DOUBLE, sampling_method STRING, sample_size INT, "
        "inspection_frequency STRING, control_method_ko STRING, spec_version STRING, "
        "effective_from DATE, is_active BOOLEAN"
    ),
    "qms_inspector": (
        "inspector_id STRING, inspector_name STRING, team_ko STRING, shift_code STRING, "
        "qualification_level STRING, certified_characteristics STRING, "
        "certified_from DATE, certified_until DATE, is_active BOOLEAN"
    ),
    "qms_inspection": (
        "inspection_id STRING, inspection_type STRING, lot_id STRING, product_code STRING, "
        "product_name STRING, step_code STRING, step_name STRING, eqp_id STRING, "
        "mes_process_result_id INT, inspector_id STRING, inspection_datetime TIMESTAMP, "
        "sample_size INT, inspected_wafer_qty INT, judgment STRING, judgment_basis_ko STRING, "
        "defect_found_qty INT, measurement_count INT, has_nonconformance BOOLEAN, remark_ko STRING"
    ),
    "qms_measurement": (
        "measurement_id STRING, inspection_id STRING, spec_id STRING, lot_id STRING, "
        "product_code STRING, step_code STRING, characteristic_code STRING, "
        "characteristic_name_ko STRING, sample_no INT, site_no INT, measured_value DOUBLE, "
        "unit STRING, target_value DOUBLE, lsl DOUBLE, usl DOUBLE, deviation_pct DOUBLE, "
        "is_out_of_spec BOOLEAN, judgment STRING, metrology_eqp_id STRING, "
        "measured_by STRING, measured_at TIMESTAMP"
    ),
    "qms_incoming_inspection": (
        "iqc_id STRING, material_code STRING, material_name STRING, supplier_code STRING, "
        "supplier_name_ko STRING, supplier_lot_no STRING, receipt_date DATE, "
        "received_qty DOUBLE, uom STRING, sample_size INT, inspection_items_ko STRING, "
        "judgment STRING, defect_code STRING, coa_received BOOLEAN, coa_conformance STRING, "
        "inspector_id STRING, inspection_date DATE, remark_ko STRING"
    ),
    "qms_nonconformance": (
        "ncr_id STRING, ncr_source STRING, inspection_id STRING, iqc_id STRING, "
        "lot_id STRING, product_code STRING, product_name STRING, step_code STRING, "
        "step_name STRING, material_code STRING, eqp_id STRING, defect_code STRING, "
        "mes_defect_code STRING, severity STRING, affected_qty INT, detected_date DATE, "
        "root_cause_category STRING, root_cause_ko STRING, immediate_action_ko STRING, "
        "owner_dept_ko STRING, owner_name STRING, status STRING, due_date DATE, "
        "closed_date DATE, estimated_cost_krw BIGINT"
    ),
    "qms_disposition": (
        "disposition_id STRING, ncr_id STRING, lot_id STRING, product_code STRING, "
        "step_code STRING, disposition_type STRING, disposition_qty INT, decision_date DATE, "
        "decision_body_ko STRING, approver_name STRING, approval_status STRING, "
        "rework_step_code STRING, rework_result STRING, scrap_cost_krw BIGINT, "
        "effectiveness_check_date DATE, effectiveness_result STRING, reason_ko STRING"
    ),
}


def ddl_columns(ddl: str) -> tuple[str, ...]:
    return tuple(part.strip().split()[0] for part in ddl.split(","))


TABLE_COLUMNS = {name: ddl_columns(ddl) for name, ddl in TABLE_DDL.items()}


def build_all_tables(snapshot: MesSnapshot) -> dict[str, list[dict]]:
    """8개 테이블 전체를 만든다. 노트북과 테스트가 공유하는 단일 진입점이다."""
    inspectors = build_inspectors()
    defect_codes = build_defect_codes()
    specs = build_inspection_specs(snapshot)
    inspections = build_inspections(snapshot, inspectors)
    measurements = build_measurements(inspections, specs)
    incoming = build_incoming_inspections(snapshot, inspectors, defect_codes)
    ncrs, dispositions = build_nonconformances(snapshot, inspections, incoming, defect_codes)
    return {
        "qms_defect_code": defect_codes,
        "qms_inspection_spec": specs,
        "qms_inspector": inspectors,
        "qms_inspection": inspections,
        "qms_measurement": measurements,
        "qms_incoming_inspection": incoming,
        "qms_nonconformance": ncrs,
        "qms_disposition": dispositions,
    }


def to_rows(table_name: str, rows: list[dict]) -> list[tuple]:
    """dict 를 컬럼 순서 tuple 로 바꾼다.

    spark.createDataFrame 에 dict 를 넘기면 키 순서 경고와 함께 스키마가
    알파벳순으로 재정렬된다. tuple 로 넘기면 DDL 순서가 그대로 지켜진다.
    """
    columns = TABLE_COLUMNS[table_name]
    return [tuple(row[column] for column in columns) for row in rows]
```

- [ ] **Step 4: conftest의 tables fixture를 위임으로 교체**

`tests/conftest.py`의 `tables` fixture 본문 전체를 다음으로 교체한다. 상단의 개별 빌더 import 6줄도 함께 제거한다.

```python
from src.qms_schema import build_all_tables


@pytest.fixture(scope="session")
def tables(snapshot) -> dict[str, list[dict]]:
    return build_all_tables(snapshot)
```

- [ ] **Step 5: 스키마 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_qms_schema.py tests/test_qms_validate.py -q`
Expected: PASS — `16 passed`

`test_generated_rows_match_declared_columns_including_order` 가 실패하면 DDL 컬럼 순서를 생성기 dict 리터럴 순서에 맞춘다. 생성기를 고치는 게 아니라 DDL을 맞추는 방향이 옳다. 생성기 순서는 스펙 5.3절 표 순서를 따르고 있다.

- [ ] **Step 6: 노트북 빌더 실패 테스트 작성**

`tests/test_build_notebook.py`:

```python
from pathlib import Path

import build_notebook as builder

ROOT = Path(__file__).resolve().parents[1]


def test_strip_removes_single_and_multiline_local_imports():
    source = (
        "from __future__ import annotations\n"
        "import json\n"
        "from src.mes_client import MesSnapshot\n"
        "from src.qms_reference import (\n"
        "    BASE_DATE,\n"
        "    SEED_MASTERS,\n"
        ")\n"
        "VALUE = 1\n"
    )
    stripped = builder.strip_local_imports(source)
    assert "src." not in stripped
    assert "import json" in stripped
    assert "VALUE = 1" in stripped


def test_strip_keeps_standard_library_imports():
    source = "import datetime as dt\nimport random\nfrom dataclasses import dataclass\n"
    assert builder.strip_local_imports(source) == source


def test_notebook_has_no_local_imports_left():
    notebook = builder.build_notebook(ROOT)
    for cell in notebook.cells:
        assert "from src." not in cell.source
        assert "import src" not in cell.source


def test_notebook_starts_with_markdown_and_has_a_parameters_cell():
    notebook = builder.build_notebook(ROOT)
    assert notebook.cells[0].cell_type == "markdown"
    assert notebook.cells[-1].cell_type == "markdown"
    tagged = [c for c in notebook.cells if "parameters" in c.metadata.get("tags", [])]
    assert len(tagged) == 1
    assert "LAKEHOUSE_NAME" in tagged[0].source


def test_notebook_contains_no_api_key_literal():
    notebook = builder.build_notebook(ROOT)
    joined = "\n".join(c.source for c in notebook.cells)
    assert "changjuahn" not in joined
    assert 'MES_API_KEY = ""' in joined


def test_inlined_module_cells_execute_and_expose_the_entry_points():
    notebook = builder.build_notebook(ROOT)
    module_cells = [
        c.source for c in notebook.cells if c.metadata.get("qms_cell") == "module"
    ]
    assert len(module_cells) == len(builder.MODULE_ORDER)
    namespace: dict = {}
    # 셀 단위로 실행한다. 각 모듈이 'from __future__ import annotations' 로 시작하는데,
    # 이어 붙여 한 번에 컴파일하면 future 문이 파일 첫머리가 아니라며 SyntaxError 가 난다.
    # Jupyter 도 셀을 각각 컴파일하므로 이쪽이 실제 실행과 같다.
    for index, source in enumerate(module_cells):
        exec(compile(source, f"<cell {index}>", "exec"), namespace)
    for symbol in ("MesClient", "build_all_tables", "validate", "TABLE_DDL", "to_rows"):
        assert symbol in namespace


def test_notebook_uses_overwrite_mode_and_the_qms_prefix():
    notebook = builder.build_notebook(ROOT)
    joined = "\n".join(c.source for c in notebook.cells)
    assert 'WRITE_MODE = "overwrite"' in joined
    assert 'TABLE_PREFIX = "qms_"' in joined


def test_written_notebook_is_valid_and_current():
    import nbformat

    path = ROOT / "qms_lakehouse_seed.ipynb"
    assert path.exists(), "build_notebook.py 를 실행해 노트북을 생성하세요."
    on_disk = nbformat.read(path, as_version=4)
    nbformat.validate(on_disk)
    fresh = builder.build_notebook(ROOT)
    assert [c.source for c in on_disk.cells] == [c.source for c in fresh.cells]
```

- [ ] **Step 7: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_build_notebook.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_notebook'`

- [ ] **Step 8: 노트북 빌더 작성**

노트북은 17셀이다. 스펙 7절의 13셀 표는 개념 배치를 보여준 것이고, 모듈이 9개로 나뉘면서 실제 셀 수가 늘었다. 순서도 한 군데 다르다. 스펙은 적재 후 검증이지만, 여기서는 **검증을 먼저 하고 적재한다**. 치명 항목이 실패했을 때 이미 쓴 테이블을 되돌릴 방법이 없기 때문이다.

| 셀 | 유형 | 내용 |
|---|---|---|
| 1 | MD | 목적, 아키텍처, MES 연동 원칙 |
| 2 | 코드 | 파라미터 (`parameters` 태그) |
| 3 | 코드 | API 키 조달: 파라미터 → Key Vault → 환경변수 |
| 4~12 | 코드 | `src/` 모듈 9개 인라인 (`qms_cell: module`) |
| 13 | 코드 | 연결 게이트. MES 두 채널 확인 후 스냅샷 확보 |
| 14 | 코드 | 8개 테이블 생성 |
| 15 | 코드 | 검증 8항목. 치명 실패 시 중단 |
| 16 | 코드 | Delta 적재 |
| 17 | MD | 요약과 Data Agent 연결 안내 |

`build_notebook.py`:

```python
"""src/ 모듈을 인라인 전개해 Fabric 노트북을 조립한다.

Fabric 노트북은 파일 업로드나 pip install 없이 단독 실행되어야 한다. 그래서
모듈을 그대로 셀에 붙이고 로컬 import 행만 지운다. 지운 뒤에도 모든 이름이
한 네임스페이스에 모이므로 참조는 그대로 성립한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import nbformat

MODULE_ORDER = (
    "qms_reference",
    "mes_client",
    "qms_masters",
    "qms_inspection",
    "qms_measurement",
    "qms_incoming",
    "qms_nonconformance",
    "qms_validate",
    "qms_schema",
)

# 괄호 묶음 여러 줄 import 도 통째로 잡는다. import 목록에 중첩 괄호가 없어
# [^)]* 로 충분하다.
_LOCAL_IMPORT = re.compile(
    r"^from\s+src\.[a-z_]+\s+import\s+(?:\([^)]*\)|[^\n]*)\n",
    re.MULTILINE,
)

_INTRO = """# QMS 가상 데이터 시드 노트북

Mock MES를 실시간 조회해 그와 맞물리는 QMS 데이터 1,004행(8테이블)을 만들고
이 레이크하우스에 Delta 테이블로 적재합니다.

## 연동 원칙

MES와 QMS는 서로 다른 시스템입니다. DB 수준의 외래키는 없고
`lot_id`, `product_code`, `step_code`, `material_code` 라는 비즈니스 키로만 이어집니다.
같은 사실을 양쪽이 중복해서 갖지 않습니다. QMS에는 `mes_result`, `scrap_qty`,
`operator`, `in_qty`, `out_qty` 컬럼이 없습니다. 생산 결과를 알고 싶으면 MES에 물어야 합니다.

## 실행 순서

파라미터 셀에서 레이크하우스 이름과 MES API 키 조달 방법을 정한 뒤 전체 실행하세요.
고정 시드와 `overwrite` 모드를 쓰므로 몇 번을 다시 돌려도 결과가 같습니다.
"""

_PARAMETERS = '''# Fabric 파이프라인에서 이 셀의 값을 덮어쓸 수 있습니다.
LAKEHOUSE_NAME = "QMS_LH"
MES_BASE_URL = "https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io"

# 키를 여기에 적지 마세요. 비워 두면 Key Vault, 그다음 환경변수 순으로 찾습니다.
MES_API_KEY = ""
KEY_VAULT_URL = ""
KEY_VAULT_SECRET_NAME = "mes-api-key"

TABLE_PREFIX = "qms_"
WRITE_MODE = "overwrite"
'''

_RESOLVE_KEY = '''import os


def resolve_api_key() -> str:
    """파라미터 → Key Vault → 환경변수 순으로 MES API 키를 찾는다."""
    if MES_API_KEY:
        return MES_API_KEY
    if KEY_VAULT_URL:
        try:
            import notebookutils

            secret = notebookutils.credentials.getSecret(KEY_VAULT_URL, KEY_VAULT_SECRET_NAME)
            if secret:
                return secret
        except Exception as exc:
            print(f"Key Vault 조회를 건너뜁니다: {exc}")
    return os.environ.get("MES_API_KEY", "")


RESOLVED_API_KEY = resolve_api_key()
print("API 키 확보됨" if RESOLVED_API_KEY else "API 키 없음. 파라미터 셀, Key Vault, 환경변수 중 하나를 설정하세요.")
'''

_GATE = '''# MES 연결 게이트. REST 와 MCP 두 채널이 모두 살아 있어야 진행합니다.
CLIENT = MesClient(MES_BASE_URL, RESOLVED_API_KEY)
try:
    SNAPSHOT = CLIENT.fetch_snapshot()
except Exception as exc:
    raise RuntimeError(
        "MES 연결에 실패했습니다. 확인할 것: "
        "(1) API 키가 올바른가 (2) Fabric Spark 풀에서 외부 인터넷 아웃바운드가 허용되는가 "
        f"(3) {MES_BASE_URL} 가 응답하는가. 원인: {exc}"
    ) from exc

print(
    f"제품 {len(SNAPSHOT.products)} · 자재 {len(SNAPSHOT.materials)} · BOM {len(SNAPSHOT.bom)} · "
    f"로트 {len(SNAPSHOT.lots)} · 공정이력 {len(SNAPSHOT.process_results)} · "
    f"라우트 {len(SNAPSHOT.route)} · 설비 {len(SNAPSHOT.equipment)}"
)
'''

_BUILD = '''TABLES = build_all_tables(SNAPSHOT)
for name, rows in TABLES.items():
    print(f"{name:26} {len(rows):5}행")
print(f"{'합계':24} {sum(len(rows) for rows in TABLES.values()):5}행")
'''

_VALIDATE = '''# 적재 전에 검증합니다. 이미 쓴 Delta 테이블은 되돌릴 수 없기 때문입니다.
RESULTS = validate(SNAPSHOT, TABLES)
print(format_report(RESULTS))
raise_on_fatal(RESULTS)
'''

_LOAD = '''for name, rows in TABLES.items():
    assert name.startswith(TABLE_PREFIX), f"테이블 접두사 규칙 위반: {name}"
    target = f"{LAKEHOUSE_NAME}.{name}" if LAKEHOUSE_NAME else name
    frame = spark.createDataFrame(to_rows(name, rows), schema=TABLE_DDL[name])
    frame.write.format("delta").mode(WRITE_MODE).saveAsTable(target)
    print(f"{target:34} {frame.count():5}행 적재")
'''

_OUTRO = """## 다음 단계

1. 레이크하우스에서 `qms_` 8개 테이블을 확인합니다.
2. 이 레이크하우스를 원본으로 Fabric Data Agent를 만들고, `data-agent-schema.md`의
   설명을 에이전트 지식으로 넣습니다.
3. Foundry 에이전트에 이 Data Agent와 MES MCP 엔드포인트를 함께 붙입니다.

## 두 시스템을 함께 봐야 답이 나오는 질문

- 생산은 통과했는데 품질이 잡아 세운 로트는 무엇이고 왜 그런가요? (3건)
- 생산에서 불합격인데 출하가 승인된 로트가 있나요? 승인 근거는요? (2건)
- 설비는 불량으로 판정하지 않았는데 검사에서 결함이 나온 건은요? (4건)
- 입고검사에서 불합격난 자재가 실제로 투입된 로트를 추적해 주세요. (2건)
- 재작업을 했지만 결국 폐기된 로트는요? (2건)
"""


def strip_local_imports(source: str) -> str:
    """src. 로 시작하는 import 행만 제거한다. 표준 라이브러리 import 는 남긴다."""
    return _LOCAL_IMPORT.sub("", source)


def _code(source: str, **metadata) -> nbformat.NotebookNode:
    cell = nbformat.v4.new_code_cell(source.rstrip() + "\n")
    cell.metadata.update(metadata)
    return cell


def build_notebook(root: Path) -> nbformat.NotebookNode:
    notebook = nbformat.v4.new_notebook()
    notebook.metadata.update(
        {
            "kernelspec": {
                "display_name": "Synapse PySpark",
                "language": "Python",
                "name": "synapse_pyspark",
            },
            "language_info": {"name": "python"},
        }
    )
    cells = [nbformat.v4.new_markdown_cell(_INTRO)]
    cells.append(_code(_PARAMETERS, tags=["parameters"]))
    cells.append(_code(_RESOLVE_KEY))
    for module in MODULE_ORDER:
        source = (root / "src" / f"{module}.py").read_text(encoding="utf-8")
        cells.append(_code(strip_local_imports(source), qms_cell="module", qms_module=module))
    cells.append(_code(_GATE))
    cells.append(_code(_BUILD))
    cells.append(_code(_VALIDATE))
    cells.append(_code(_LOAD))
    cells.append(nbformat.v4.new_markdown_cell(_OUTRO))
    notebook.cells = cells
    return notebook


def main() -> None:
    root = Path(__file__).resolve().parent
    target = root / "qms_lakehouse_seed.ipynb"
    notebook = build_notebook(root)
    nbformat.validate(notebook)
    nbformat.write(notebook, target)
    print(f"{target} 생성됨 · {len(notebook.cells)}셀")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9: 노트북 생성 후 테스트 통과 확인**

```bash
cd customizing/fabric/qms-lakehouse
python build_notebook.py
```
Expected: `.../qms_lakehouse_seed.ipynb 생성됨 · 17셀`

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_build_notebook.py -q`
Expected: PASS — `8 passed`

- [ ] **Step 10: 전체 회귀 실행**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest -q`
Expected: PASS — `94 passed`

- [ ] **Step 11: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Assemble the Fabric seed notebook from the source modules

A Fabric notebook has to run on its own: no file upload, no pip install. So
the build inlines each module into a cell and deletes only the src imports.
Everything lands in one namespace, so the references still resolve, and the
same code stays importable and unit-tested locally.

Schemas are DDL strings rather than pyspark StructType objects because
pyspark cannot be imported outside Fabric, and an explicit schema is not
optional here: several columns are null for every row and would otherwise
fail type inference. Rows are passed as tuples so the DDL column order wins.

The notebook validates before it writes. The spec had it the other way round,
but a fatal check that fires after saveAsTable leaves bad tables behind."
```

---
### Task 9: 문서

두 문서를 쓴다. `README.md`는 사람이 읽고 실행하기 위한 것이고, `data-agent-schema.md`는 Fabric Data Agent에 지식으로 넣기 위한 것이다. 후자는 에이전트가 어떤 질문에 어떤 테이블을 조인해야 하는지 알아낼 수 있을 만큼 구체적이어야 한다.

**Files:**
- Create: `customizing/fabric/qms-lakehouse/README.md`
- Create: `customizing/fabric/qms-lakehouse/data-agent-schema.md`
- Create: `customizing/fabric/qms-lakehouse/tests/test_docs.py`

**Interfaces:**
- Consumes: `TABLE_COLUMNS` / `TABLE_DDL` (T8), `TABLE_ROW_TARGETS` / `DEVICE_TARGETS` (T7)
- Produces: 없음 (최종 태스크)

- [ ] **Step 1: 문서 일관성 테스트 작성**

문서가 코드와 어긋나는 것이 가장 흔한 실패다. 표에 적힌 행수와 컬럼명을 코드에서 직접 대조한다.

`tests/test_docs.py`:

```python
from pathlib import Path

from src.qms_schema import TABLE_COLUMNS
from src.qms_validate import DEVICE_TARGETS, TABLE_ROW_TARGETS

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
AGENT_DOC = ROOT / "data-agent-schema.md"


def test_both_documents_exist():
    assert README.exists()
    assert AGENT_DOC.exists()


def test_agent_doc_describes_every_table_and_column():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for table, columns in TABLE_COLUMNS.items():
        assert f"`{table}`" in text, table
        for column in columns:
            assert f"`{column}`" in text, f"{table}.{column}"


def test_agent_doc_states_the_correct_row_counts():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for table, count in TABLE_ROW_TARGETS.items():
        assert f"{count}행" in text, f"{table} {count}"
    assert "1,004행" in text


def test_agent_doc_lists_all_five_mismatch_devices():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for marker in DEVICE_TARGETS:
        assert marker[0] in text, marker


def test_agent_doc_states_the_forbidden_columns():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for column in ("mes_result", "scrap_qty", "operator", "in_qty", "out_qty"):
        assert f"`{column}`" in text


def test_readme_documents_the_build_and_test_commands():
    text = README.read_text(encoding="utf-8")
    assert "python build_notebook.py" in text
    assert "python -m pytest" in text
    assert "-m live" in text
    assert "qms_lakehouse_seed.ipynb" in text


def test_documents_contain_no_api_key():
    for path in (README, AGENT_DOC):
        assert "changjuahn" not in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_docs.py -q`
Expected: FAIL — `assert False` (README.md 없음)

- [ ] **Step 3: README 작성**

`README.md`:

````markdown
# QMS 가상 데이터 시드

제조 현장의 품질관리(QMS) 데이터를 Microsoft Fabric 레이크하우스에 만들어 넣습니다.
데이터는 Mock MES를 실시간으로 조회해 그와 정합되도록 생성됩니다.

## 무엇이 만들어지나

8개 Delta 테이블, 합계 1,004행입니다.

| 테이블 | 행수 | 내용 |
|---|---|---|
| `qms_defect_code` | 24행 | MES 상위 불량코드 6종을 전개한 QMS 세부 불량코드 |
| `qms_inspection_spec` | 108행 | 제품·공정·특성별 검사 기준과 규격 |
| `qms_inspector` | 15행 | 검사원. MES 작업자와 별개 인력 |
| `qms_inspection` | 207행 | 공정검사·재검사·출하검사·공정능력조사·설비검증 |
| `qms_measurement` | 260행 | 규격 대비 실측값 |
| `qms_incoming_inspection` | 200행 | 자재 입고검사 |
| `qms_nonconformance` | 95행 | 부적합 보고서 |
| `qms_disposition` | 95행 | 부적합 처리 결정 |

## MES와의 관계

두 시스템은 별개입니다. 외래키가 아니라 `lot_id`, `product_code`, `step_code`,
`material_code` 라는 비즈니스 키로만 이어집니다. 같은 사실을 양쪽이 중복해서 갖지
않으므로 QMS에는 `mes_result`, `scrap_qty`, `operator`, `in_qty`, `out_qty` 컬럼이
없습니다. 생산이 어떻게 됐는지 알고 싶으면 MES에 물어야 합니다.

이 데이터에는 두 시스템을 함께 조회해야만 풀리는 상황이 13건 의도적으로 심겨 있습니다.
자세한 내용은 `data-agent-schema.md`를 보세요.

## 실행

### 1. MES API 키 준비

키를 파일에 적지 마세요. 셋 중 하나로 공급합니다.

1. 노트북 파라미터 셀의 `MES_API_KEY`
2. Key Vault — `KEY_VAULT_URL`과 `KEY_VAULT_SECRET_NAME`을 채우면 조회합니다
3. 환경변수 `MES_API_KEY`

### 2. 노트북 빌드

```bash
python build_notebook.py
```

`qms_lakehouse_seed.ipynb`가 생성됩니다. `src/` 모듈이 셀로 인라인되어 있어
파일 업로드나 `pip install` 없이 단독 실행됩니다.

### 3. Fabric에 업로드하고 실행

레이크하우스에 노트북을 올린 뒤 파라미터 셀의 `LAKEHOUSE_NAME`을 실제 이름으로
바꾸고 전체 실행합니다. 고정 시드와 `overwrite` 모드를 쓰므로 몇 번을 다시 돌려도
결과가 같습니다.

노트북은 적재 전에 8개 항목을 검증합니다. 고아 키, MES 중복 컬럼, 내부 참조 깨짐은
치명 항목이라 실패하면 적재하지 않고 중단합니다.

## 개발

```bash
python -m pytest -q                       # 오프라인 테스트 전체
python -m pytest -m live -q               # 실 MES 접속 테스트 (MES_API_KEY 필요)
```

오프라인 테스트는 `tests/fixtures/mes_snapshot.json`에 저장된 실 MES 스냅샷 위에서
돕니다. MES가 바뀌면 스냅샷을 다시 뜨고 `-m live` 테스트로 기대값을 갱신하세요.

`src/`가 원본이고 노트북은 빌드 산출물입니다. 노트북을 직접 고치지 마세요.
`src/`를 고치고 `python build_notebook.py`를 다시 실행합니다.

## 다음 단계

이 레이크하우스로 Fabric Data Agent를 만들고, `data-agent-schema.md`를 에이전트
지식으로 넣습니다. 그다음 Foundry 에이전트에 이 Data Agent와 MES MCP 엔드포인트를
함께 붙이면 두 시스템에 걸친 질문에 답할 수 있습니다.
````

- [ ] **Step 4: Data Agent 스키마 문서 작성**

이 문서는 `TABLE_COLUMNS`의 모든 컬럼명을 백틱으로 언급해야 테스트를 통과한다. 8개 테이블 각각에 대해 컬럼 표를 쓰고, 조인 경로와 대표 질문을 덧붙인다.

`data-agent-schema.md` 구성:

1. **개요** — QMS가 무엇이고 MES와 어떻게 이어지는지. `1,004행`, 8테이블 명시
2. **조인 키** — `lot_id`, `product_code`, `step_code`, `material_code`가 MES와의 유일한 연결 고리라는 설명. QMS 내부 키(`inspection_id`, `ncr_id`, `spec_id`, `inspector_id`, `iqc_id`)와 구분
3. **QMS에 없는 것** — `mes_result`, `scrap_qty`, `operator`, `in_qty`, `out_qty`는 MES에만 있다는 명시. 에이전트가 QMS에서 찾다 실패하지 않게 하는 목적
4. **테이블 8개** — 각 테이블마다 행수(`24행` 등)와 전체 컬럼 표. 컬럼명은 백틱으로, 설명은 스펙 5.3절 표를 그대로 옮긴다
5. **null 이 정상인 경우** — `PCS`·`EQV` 검사의 `lot_id`, `EQV`의 `product_code`, 고객제기 NCR의 `lot_id`·`step_code`, 합격 입고검사의 `defect_code`
6. **검사 유형** — `IPQC`/`IPQC-RT`/`OQC`/`PCS`/`EQV` 각각의 의미와 건수
7. **두 시스템 교차 질문 5종** — 아래 표를 그대로 싣는다

| 장치 | 건수 | 질문 | 판별 조건 |
|---|---|---|---|
| ① | 3 | 생산은 통과했는데 품질이 세운 로트는? | `qms_inspection.judgment='불합격'` + `defect_found_qty=0`, MES 해당 이력은 `Pass`이고 불량코드 없음. 연결 NCR의 `root_cause_category='측정'` |
| ② | 2 | 불합격인데 출하 승인된 로트와 사유는? | `qms_disposition.disposition_type='특채'` + `decision_body_ko='MRB'`, 연결 검사의 MES 이력이 `Fail` |
| ③ | 4 | 설비는 못 잡고 검사가 잡은 불량은? | `qms_inspection.defect_found_qty>0` + `judgment≠'불합격'`, MES 해당 이력에 불량코드 없음 |
| ④ | 2 | 불합격 자재가 들어간 로트는? | `qms_nonconformance.ncr_source='입고검사'` + `lot_id` 값 존재. `material_code`로 MES BOM을 거쳐 제품·공정까지 추적 |
| ⑤ | 2 | 재작업했지만 결국 폐기된 로트는? | `qms_disposition.rework_result='실패'` + `disposition_type='폐기'` |

8. **자주 쓰는 조인 경로**

```sql
-- 로트별 품질 이력 전체
SELECT i.lot_id, i.step_code, i.judgment, n.defect_code, d.disposition_type
FROM qms_inspection i
LEFT JOIN qms_nonconformance n ON n.inspection_id = i.inspection_id
LEFT JOIN qms_disposition d ON d.ncr_id = n.ncr_id
WHERE i.lot_id = 'LOT0011';

-- 공정별 규격 이탈률
SELECT m.step_code, m.characteristic_code,
       AVG(CASE WHEN m.is_out_of_spec THEN 1.0 ELSE 0.0 END) AS oos_rate
FROM qms_measurement m
GROUP BY m.step_code, m.characteristic_code;

-- 공급업체별 입고 불합격률
SELECT supplier_name_ko,
       SUM(CASE WHEN judgment = '불합격' THEN 1 ELSE 0 END) / COUNT(*) AS reject_rate
FROM qms_incoming_inspection
GROUP BY supplier_name_ko;
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest tests/test_docs.py -q`
Expected: PASS — `7 passed`

컬럼 누락으로 실패하면 실패 메시지가 `테이블.컬럼` 형태로 어떤 것이 빠졌는지 알려준다. 해당 컬럼을 문서 표에 추가한다.

- [ ] **Step 6: 전체 회귀 실행**

Run: `cd customizing/fabric/qms-lakehouse && python -m pytest -q`
Expected: PASS — `101 passed`

- [ ] **Step 7: 실 MES 대상 최종 확인**

```bash
cd customizing/fabric/qms-lakehouse
MES_API_KEY=<키> python -m pytest -m live -q
```
Expected: PASS — `1 passed`

스냅샷 fixture가 여전히 실 MES와 일치하는지 마지막으로 확인한다.

- [ ] **Step 8: 커밋**

```bash
cd "$(git rev-parse --show-toplevel)"
git add customizing/fabric/qms-lakehouse
git commit -m "Document the QMS dataset for humans and for the data agent

The agent-facing document has to say what is *not* in QMS as loudly as what
is. An agent that goes looking for scrap_qty in a QMS table and finds nothing
will report that the data is missing, when the correct move is to ask MES.

Doc tests read the column list straight out of qms_schema, so a schema change
that nobody documented fails the suite instead of quietly shipping a stale
table description to the agent's knowledge base."
```

---

## 완료 기준

- `python -m pytest -q` 가 101건 통과
- `python -m pytest -m live -q` 가 실 MES에 대해 통과
- `python build_notebook.py` 가 17셀 노트북을 생성
- 노트북을 Fabric에서 실행하면 8개 테이블 1,004행이 적재되고 검증 8항목이 모두 PASS
- 저장소 어디에도 API 키 문자열이 없음
