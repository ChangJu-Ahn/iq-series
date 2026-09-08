# FDC Eventhouse 설비 센서 텔레메트리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mock MES에서 유도한 설비 센서 텔레메트리를 생성해 Fabric Eventhouse(KQL DB)에 주기적으로 적재하는 자기 완결형 노트북 패키지를 만든다.

**Architecture:** `src/` 모듈이 원본이고 `build_notebook.py`가 이를 셀로 인라인해 단독 실행 가능한 노트북을 만든다. 판독값은 `(eqp_id, sensor_code, reading_ts)`의 순수 함수라 백필과 라이브 추가가 일치하고 재실행이 무해하다. 설비 목록과 이상 패턴은 MES 실적에서 유도하므로 Mock MES가 재시드돼도 따라간다.

**Tech Stack:** Python 3.10+, 표준 라이브러리만(`urllib`, `zlib`, `random`, `math`, `datetime`), 빌드에 `nbformat`, 테스트에 `pytest`. 런타임은 Fabric Spark + Kusto Spark 커넥터.

**설계 문서:** `docs/superpowers/specs/2026-09-04-fdc-eventhouse-telemetry-design.md`

## Global Constraints

- 작업 디렉터리는 `customizing/fabric/fdc-eventhouse/`. 모든 경로는 이 아래다.
- `src/` 모듈은 **표준 라이브러리만** import 한다. 노트북에 인라인되므로 `pip install`이 있어선 안 된다.
- 모듈 간 참조는 `from src.<module> import ...` 형태로만 쓴다. `build_notebook.py`가 이 행만 지운다.
- 난수는 `random.Random(_seed(...))`만 쓴다. 시드는 `zlib.crc32`. **`hash()` 금지** — 프로세스마다 값이 달라져 멱등성이 깨진다.
- `SAMPLE_INTERVAL_SEC = 30`, `EXCURSION_BUCKET_SEC = 1800`, `EXCURSION_SCALE = 0.25`.
- 테이블 접두사는 `fdc_`. 테이블은 `fdc_sensor_spec`, `fdc_sensor_reading` 둘뿐이다.
- FDC 테이블에 `lot_id`, `product_code`, `wafer_qty`, `defect_code`, `judgment`, `result`, `operator` 컬럼이 있으면 설계 위반이다.
- 모든 타임스탬프는 UTC(`datetime.timezone.utc`).
- 주석과 문서는 한국어. 기존 `qms-lakehouse` 패키지의 문체를 따른다.
- 테스트는 `python3 -m pytest -q`로 오프라인 실행된다. `-m live`만 실제 MES에 접속한다.

---

### Task 1: 패키지 스캐폴드와 MES 프로브

MES에서 필요한 사실은 MCP 호출 두 개뿐이다. 설비 목록과 설비별 불량률의 원천이다.

**Files:**
- Create: `customizing/fabric/fdc-eventhouse/pyproject.toml`
- Create: `customizing/fabric/fdc-eventhouse/src/__init__.py` (빈 파일)
- Create: `customizing/fabric/fdc-eventhouse/src/mes_probe.py`
- Create: `customizing/fabric/fdc-eventhouse/tests/conftest.py`
- Create: `customizing/fabric/fdc-eventhouse/tests/fixtures/mes_facts.json`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_mes_probe.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `MES_BASE_URL: str`
  - `parse_mcp_body(raw: str) -> dict | None`
  - `derive_equipment(process_results: list[dict], route: list[dict]) -> list[dict]` — `[{"eqp_id","eqp_type","step_code"}]`, `eqp_id`가 `None`인 행은 제외, `eqp_id` 오름차순
  - `class MesFacts` — 필드 `process_results: list[dict]`, `route: list[dict]`, `equipment: list[dict]`; `from_dict(dict) -> MesFacts`, `to_dict() -> dict`
  - `class MesProbe` — `__init__(base_url: str, api_key: str, timeout: int = 60)`, `mcp_call(tool: str, args: dict | None = None) -> Any`, `fetch_facts() -> MesFacts`
  - pytest fixture `facts` → `MesFacts`

- [ ] **Step 1: `pyproject.toml` 작성**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = [
    "live: MES 서버에 실제로 접속하는 테스트. MES_API_KEY 필요",
]
addopts = "-m 'not live'"
```

- [ ] **Step 2: 픽스처 생성**

`qms-lakehouse`의 `tests/fixtures/mes_snapshot.json`에서 `process_results`와 `route`만 뽑아
`tests/fixtures/mes_facts.json`으로 저장한다. 형식은 `{"process_results": [...], "route": [...]}`.
실측 기준 `process_results` 91행, `route` 9행이며 그중 `eqp_id`가 `null`인 행이 7건이다.

- [ ] **Step 3: 실패하는 테스트 작성**

```python
def test_derive_equipment_excludes_null_eqp(facts):
    eqp_ids = {e["eqp_id"] for e in facts.equipment}
    assert None not in eqp_ids
    assert len(facts.equipment) == 8


def test_derive_equipment_maps_type_from_route(facts):
    by_id = {e["eqp_id"]: e for e in facts.equipment}
    assert by_id["EQP-DIFF01"]["eqp_type"] == "Furnace"
    assert by_id["EQP-DIFF01"]["step_code"] == "DIFF"
    assert by_id["EQP-PHOT01"]["eqp_type"] == "Scanner"
    assert by_id["EQP-CMP01"]["eqp_type"] == "Polisher"


def test_parse_mcp_body_handles_plain_json():
    assert parse_mcp_body('{"a": 1}') == {"a": 1}


def test_parse_mcp_body_handles_sse():
    raw = "event: message\ndata: {\"a\":\n data: 1}\n"
    assert parse_mcp_body(raw) == {"a": 1}


def test_parse_mcp_body_empty_returns_none():
    assert parse_mcp_body("   ") is None


def test_probe_requires_api_key():
    with pytest.raises(ValueError):
        MesProbe(MES_BASE_URL, "")
```

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd customizing/fabric/fdc-eventhouse && python3 -m pytest -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.mes_probe'`

- [ ] **Step 5: `src/mes_probe.py` 구현**

`parse_mcp_body`는 순수 JSON을 먼저 시도하고 실패하면 `data:` 행을 이어 붙인다. 이 서버는
`Accept`에 `text/event-stream`이 있으면 SSE로 답하고 본문이 `event: message`로 시작하므로
`data:` 접두 검사만으로는 인식되지 않는다.

`derive_equipment`는 `process_results`의 `eqp_id` 고유값에서 설비를 만든다. MES가 설비
마스터를 노출하지 않기 때문이다. `eqp_type`은 그 설비가 처음 등장한 `step_code`를
`route`에서 조회해 얻는다.

`MesProbe.mcp_call`은 `POST /mcp`에 JSON-RPC로 `tools/call`을 보내고
`X-API-Key`, `Accept: application/json, text/event-stream`을 붙인다. 응답에서
`result.structuredContent.result`를 꺼낸다.

`fetch_facts`는 `list_process_results`(limit 500)와 `get_process_route`를 호출한다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `python3 -m pytest -q`
Expected: PASS — 6 passed

- [ ] **Step 7: 커밋**

```bash
git add customizing/fabric/fdc-eventhouse
git commit -m "feat(fdc): MES MCP 프로브와 설비 유도"
```

---

### Task 2: 센서 정의

설비 유형 7종 × 센서 6종 = 42행. 공통 2종은 모든 유형이 갖는다.

**Files:**
- Create: `customizing/fabric/fdc-eventhouse/src/fdc_sensors.py`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_fdc_sensors.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `SAMPLE_INTERVAL_SEC: int = 30`
  - `class SensorDef` — frozen dataclass. 필드: `sensor_code: str`, `sensor_name_ko: str`, `unit: str`, `base: float`, `diurnal_amp: float`, `sigma: float`, `normal_min: float`, `normal_max: float`, `alarm_min: float`, `alarm_max: float`
  - `COMMON_SENSORS: tuple[SensorDef, ...]` — 길이 2
  - `TYPE_SENSORS: dict[str, tuple[SensorDef, ...]]` — 7키, 각 길이 4
  - `EQP_TYPES: tuple[str, ...]` — `("CVD","Etcher","Furnace","Implanter","Polisher","Prober","Scanner")`
  - `sensors_for(eqp_type: str) -> tuple[SensorDef, ...]` — 공통 2 + 고유 4, 길이 6. 미지의 유형이면 공통 2만
  - `sensor_by_code(eqp_type: str, sensor_code: str) -> SensorDef` — 없으면 `KeyError`
  - `build_sensor_spec_rows() -> list[dict]` — 42행. 키: `sensor_code`, `sensor_name_ko`, `eqp_type`, `unit`, `normal_min`, `normal_max`, `alarm_min`, `alarm_max`, `sample_interval_sec`, `is_active`

- [ ] **Step 1: 실패하는 테스트 작성**

`base`가 정상범위의 중심이어야 한다는 불변식이 중요하다. Task 4의 이상 주입 계산이
이를 전제하기 때문이다.

```python
def test_seven_types_six_sensors_each():
    assert len(EQP_TYPES) == 7
    for t in EQP_TYPES:
        assert len(sensors_for(t)) == 6


def test_spec_rows_are_42():
    rows = build_sensor_spec_rows()
    assert len(rows) == 42
    assert len({(r["eqp_type"], r["sensor_code"]) for r in rows}) == 42


def test_every_type_has_ambient_temp_and_humidity():
    for t in EQP_TYPES:
        codes = {s.sensor_code for s in sensors_for(t)}
        assert {"AMBIENT_TEMP", "AMBIENT_HUMIDITY"} <= codes


def test_base_is_center_of_normal_range():
    """이상 주입이 base 중심 대칭을 전제한다."""
    for t in EQP_TYPES:
        for s in sensors_for(t):
            center = (s.normal_min + s.normal_max) / 2
            assert abs(s.base - center) < 1e-9, f"{t}.{s.sensor_code}"


def test_alarm_range_strictly_contains_normal_range():
    for t in EQP_TYPES:
        for s in sensors_for(t):
            assert s.alarm_min < s.normal_min < s.normal_max < s.alarm_max


def test_sigma_is_small_relative_to_normal_range():
    """잡음만으로 경고가 뜨면 이상 주입 신호가 묻힌다."""
    for t in EQP_TYPES:
        for s in sensors_for(t):
            half = (s.normal_max - s.normal_min) / 2
            assert s.sigma + s.diurnal_amp < half * 0.9, f"{t}.{s.sensor_code}"


def test_spec_rows_have_no_forbidden_columns():
    forbidden = {"lot_id", "product_code", "defect_code", "judgment", "result", "operator"}
    for r in build_sensor_spec_rows():
        assert not (forbidden & set(r))


def test_sensor_by_code_raises_for_unknown():
    with pytest.raises(KeyError):
        sensor_by_code("Furnace", "NO_SUCH_SENSOR")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_fdc_sensors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fdc_sensors'`

- [ ] **Step 3: `src/fdc_sensors.py` 구현**

공통 센서 2종:

```python
COMMON_SENSORS = (
    SensorDef("AMBIENT_TEMP", "주변 온도", "degC", 22.0, 0.8, 0.15, 21.0, 23.0, 20.0, 24.0),
    SensorDef("AMBIENT_HUMIDITY", "주변 습도", "%", 45.0, 2.5, 0.6, 40.0, 50.0, 35.0, 55.0),
)
```

유형별 고유 4종은 다음 값을 쓴다. 순서는 `sensor_code`, `sensor_name_ko`, `unit`,
`base`, `diurnal_amp`, `sigma`, `normal_min`, `normal_max`, `alarm_min`, `alarm_max`.

| eqp_type | 센서 | 값 |
|---|---|---|
| Furnace | CHAMBER_TEMP | 챔버 온도, degC, 1050, 1.5, 1.2, 1040, 1060, 1030, 1070 |
| Furnace | RAMP_RATE | 승온 속도, degC/min, 8.0, 0.1, 0.08, 7.5, 8.5, 7.0, 9.0 |
| Furnace | O2_CONC | 산소 농도, ppm, 120, 3.0, 2.0, 100, 140, 80, 160 |
| Furnace | N2_FLOW | 질소 유량, sccm, 2000, 15, 8, 1950, 2050, 1900, 2100 |
| Scanner | STAGE_TEMP | 스테이지 온도, degC, 23.0, 0.03, 0.02, 22.9, 23.1, 22.8, 23.2 |
| Scanner | FOCUS_OFFSET | 포커스 오프셋, nm, 0.0, 2.0, 1.5, -15, 15, -25, 25 |
| Scanner | ILLUM_DOSE | 노광 도즈, mJ/cm2, 30.0, 0.15, 0.1, 29.4, 30.6, 29.0, 31.0 |
| Scanner | RETICLE_TEMP | 레티클 온도, degC, 23.0, 0.02, 0.012, 22.95, 23.05, 22.9, 23.1 |
| Etcher | RF_POWER | RF 파워, W, 1500, 8, 6, 1450, 1550, 1400, 1600 |
| Etcher | CHAMBER_PRESSURE | 챔버 압력, mTorr, 45.0, 0.6, 0.5, 42, 48, 40, 50 |
| Etcher | GAS_FLOW | 공정가스 유량, sccm, 180, 2.0, 1.5, 172, 188, 165, 195 |
| Etcher | CHAMBER_TEMP | 챔버 온도, degC, 65.0, 0.8, 0.5, 62, 68, 60, 70 |
| Implanter | BEAM_CURRENT | 빔 전류, uA, 500, 5, 4, 480, 520, 460, 540 |
| Implanter | BEAM_ENERGY | 빔 에너지, keV, 80.0, 0.4, 0.3, 78, 82, 76, 84 |
| Implanter | VACUUM | 진공도, uTorr, 2.0, 0.08, 0.06, 1.5, 2.5, 1.0, 3.5 |
| Implanter | SRC_TEMP | 이온소스 온도, degC, 320, 3.0, 2.0, 305, 335, 290, 350 |
| CVD | CHAMBER_TEMP | 챔버 온도, degC, 420, 2.0, 1.5, 410, 430, 400, 440 |
| CVD | CHAMBER_PRESSURE | 챔버 압력, Torr, 5.0, 0.08, 0.06, 4.6, 5.4, 4.2, 5.8 |
| CVD | PRECURSOR_FLOW | 전구체 유량, sccm, 250, 2.5, 2.0, 240, 260, 230, 270 |
| CVD | DEP_RATE | 증착 속도, nm/min, 12.0, 0.15, 0.1, 11.4, 12.6, 11.0, 13.0 |
| Polisher | PAD_PRESSURE | 패드 압력, kPa, 35.0, 0.3, 0.25, 33, 37, 31, 39 |
| Polisher | SLURRY_FLOW | 슬러리 유량, ml/min, 200, 2.0, 1.6, 190, 210, 180, 220 |
| Polisher | MOTOR_CURRENT | 모터 전류, A, 18.0, 0.25, 0.2, 17, 19, 16, 20 |
| Polisher | PAD_TEMP | 패드 온도, degC, 42.0, 0.6, 0.45, 39, 45, 37, 47 |
| Prober | CHUCK_TEMP | 척 온도, degC, 25.0, 0.1, 0.05, 24.7, 25.3, 24.5, 25.5 |
| Prober | CONTACT_RES | 접촉 저항, mohm, 50.0, 1.0, 0.8, 45, 55, 40, 60 |
| Prober | PROBE_FORCE | 프로브 압력, mN, 30.0, 0.3, 0.25, 28, 32, 26, 34 |
| Prober | TOUCHDOWN_CNT | 터치다운 횟수, cnt, 1200, 40, 25, 1000, 1400, 900, 1500 |

`build_sensor_spec_rows()`는 `EQP_TYPES` × `sensors_for()`를 순회해 42행을 만든다.
`sample_interval_sec`는 전부 `SAMPLE_INTERVAL_SEC`, `is_active`는 전부 `True`.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_fdc_sensors.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: 커밋**

```bash
git add customizing/fabric/fdc-eventhouse
git commit -m "feat(fdc): 설비 유형별 센서 정의 42종"
```

---

### Task 3: MES 유도 이상 주입

"고장 설비 목록"을 하드코딩하지 않는다. MES 불량 실적에서 유도해 Mock MES가 바뀌면
따라가게 한다.

**Files:**
- Create: `customizing/fabric/fdc-eventhouse/src/fdc_anomaly.py`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_fdc_anomaly.py`

**Interfaces:**
- Consumes: `src.fdc_sensors.sensors_for`, `src.fdc_sensors.SensorDef`
- Produces:
  - `EXCURSION_BUCKET_SEC: int = 1800`
  - `EXCURSION_SCALE: float = 0.25`
  - `DEFECT_SENSOR_HINT: dict[str, tuple[str, ...]]`
  - `_seed(*parts: object) -> int` — `zlib.crc32` 기반
  - `defect_rates(process_results: list[dict]) -> dict[str, float]` — `eqp_id -> 0.0..1.0`. `eqp_id`가 `None`인 행 제외
  - `dominant_defects(process_results: list[dict]) -> dict[str, tuple[str, ...]]` — 건수 내림차순, 동수는 코드 오름차순
  - `target_sensors(eqp_id: str, eqp_type: str, process_results: list[dict]) -> tuple[str, ...]` — 교집합이 비면 `("AMBIENT_TEMP",)`
  - `class Excursion` — frozen dataclass. 필드 `sensor_code: str`, `severity: str`, `magnitude: float`, `direction: int`
  - `bucket_index(ts: datetime) -> int` — `int(ts.timestamp()) // EXCURSION_BUCKET_SEC`
  - `excursion_for(eqp_id: str, bucket: int, rate: float, candidates: tuple[str, ...]) -> Excursion | None`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_seed_is_stable_across_processes():
    """hash() 를 쓰면 이 값이 실행마다 달라진다."""
    assert _seed("EQP-CMP01", "AMBIENT_TEMP", 100) == 2931605645


def test_defect_rates_match_mes(facts):
    rates = defect_rates(facts.process_results)
    assert None not in rates
    assert rates["EQP-CMP01"] == pytest.approx(7 / 8)
    assert rates["EQP-DIFF01"] == pytest.approx(7 / 16)
    assert rates["EQP-IMPL01"] == pytest.approx(2 / 12)


def test_target_sensors_are_physically_relevant(facts):
    pr = facts.process_results
    assert target_sensors("EQP-CVD01", "CVD", pr) == ("PRECURSOR_FLOW",)
    cmp01 = target_sensors("EQP-CMP01", "Polisher", pr)
    assert cmp01[0] == "AMBIENT_TEMP"
    assert set(cmp01) <= {"AMBIENT_TEMP", "AMBIENT_HUMIDITY", "PAD_PRESSURE", "MOTOR_CURRENT"}


def test_target_sensors_only_returns_sensors_on_that_type(facts):
    for eqp in facts.equipment:
        available = {s.sensor_code for s in sensors_for(eqp["eqp_type"])}
        got = target_sensors(eqp["eqp_id"], eqp["eqp_type"], facts.process_results)
        assert got, eqp["eqp_id"]
        assert set(got) <= available, eqp["eqp_id"]


def test_excursion_is_deterministic():
    a = excursion_for("EQP-CMP01", 12345, 0.875, ("AMBIENT_TEMP",))
    b = excursion_for("EQP-CMP01", 12345, 0.875, ("AMBIENT_TEMP",))
    assert a == b


def test_excursion_frequency_tracks_defect_rate():
    hi = sum(excursion_for("EQP-CMP01", b, 0.875, ("AMBIENT_TEMP",)) is not None
             for b in range(2000))
    lo = sum(excursion_for("EQP-IMPL01", b, 0.167, ("AMBIENT_TEMP",)) is not None
             for b in range(2000))
    assert hi > lo * 2


def test_excursion_fields_are_valid():
    found = [e for b in range(500)
             if (e := excursion_for("EQP-CMP01", b, 0.875, ("AMBIENT_TEMP", "PAD_PRESSURE")))]
    assert found
    for e in found:
        assert e.sensor_code in ("AMBIENT_TEMP", "PAD_PRESSURE")
        assert e.severity in ("Warning", "Alarm")
        assert e.direction in (-1, 1)
        assert 1.0 < e.magnitude < 2.0


def test_zero_rate_yields_no_excursion():
    assert all(excursion_for("EQP-X", b, 0.0, ("AMBIENT_TEMP",)) is None for b in range(500))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_fdc_anomaly.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fdc_anomaly'`

- [ ] **Step 3: `src/fdc_anomaly.py` 구현**

```python
DEFECT_SENSOR_HINT = {
    "Particle": ("CHAMBER_TEMP", "AMBIENT_HUMIDITY"),
    "Scratch": ("PAD_PRESSURE", "MOTOR_CURRENT", "PROBE_FORCE"),
    "Overlay": ("AMBIENT_TEMP", "STAGE_TEMP", "RETICLE_TEMP"),
    "Etch-Residue": ("GAS_FLOW", "PRECURSOR_FLOW", "RF_POWER"),
    "Contamination": ("AMBIENT_HUMIDITY", "VACUUM"),
    "CD-OOS": ("FOCUS_OFFSET", "RF_POWER", "ILLUM_DOSE"),
}


def _seed(*parts: object) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode())
```

`excursion_for`의 본체:

```python
def excursion_for(eqp_id, bucket, rate, candidates):
    if rate <= 0.0 or not candidates:
        return None
    rng = random.Random(_seed("excursion", eqp_id, bucket))
    if rng.random() >= min(0.5, rate * EXCURSION_SCALE):
        return None
    severity = "Alarm" if rng.random() < rate else "Warning"
    return Excursion(
        sensor_code=rng.choice(candidates),
        severity=severity,
        magnitude=rng.uniform(1.15, 1.6) if severity == "Alarm" else rng.uniform(1.05, 1.45),
        direction=rng.choice((-1, 1)),
    )
```

`target_sensors`는 `dominant_defects`가 준 코드 순서대로 `DEFECT_SENSOR_HINT`를 펼치고
그 설비 유형에 실제 존재하는 센서만 남긴다. 순서를 유지하며 중복을 제거한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_fdc_anomaly.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: 커밋**

```bash
git add customizing/fabric/fdc-eventhouse
git commit -m "feat(fdc): MES 불량률에서 유도하는 이상 주입"
```

---

### Task 4: 판독값 생성

`(eqp_id, sensor_code, reading_ts)`의 순수 함수. 이 태스크가 멱등성의 핵심이다.

**Files:**
- Create: `customizing/fabric/fdc-eventhouse/src/fdc_generator.py`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_fdc_generator.py`

**Interfaces:**
- Consumes: `src.fdc_sensors`(`SensorDef`, `sensors_for`, `SAMPLE_INTERVAL_SEC`), `src.fdc_anomaly`(`_seed`, `bucket_index`, `excursion_for`, `defect_rates`, `target_sensors`, `EXCURSION_BUCKET_SEC`)
- Produces:
  - `grid_timestamps(start: datetime, end: datetime) -> list[datetime]` — `start` 초과 `end` 이하, 30초 격자 정렬
  - `excursion_offset(eqp_id: str, sensor: SensorDef, ts: datetime, rate: float, candidates: tuple[str, ...]) -> float`
  - `reading_value(eqp_id: str, sensor: SensorDef, ts: datetime, rate: float, candidates: tuple[str, ...]) -> float`
  - `classify(value: float, sensor: SensorDef) -> str`
  - `build_readings(facts: MesFacts, start: datetime, end: datetime) -> list[dict]` — 키: `reading_ts`, `eqp_id`, `eqp_type`, `step_code`, `sensor_code`, `value`, `unit`, `status`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
UTC = dt.timezone.utc


def test_grid_excludes_start_includes_end():
    start = dt.datetime(2026, 9, 4, 0, 0, 0, tzinfo=UTC)
    end = dt.datetime(2026, 9, 4, 0, 2, 0, tzinfo=UTC)
    got = grid_timestamps(start, end)
    assert got[0] == start + dt.timedelta(seconds=30)
    assert got[-1] == end
    assert len(got) == 4


def test_grid_aligns_unaligned_bounds():
    start = dt.datetime(2026, 9, 4, 0, 0, 7, tzinfo=UTC)
    end = dt.datetime(2026, 9, 4, 0, 1, 22, tzinfo=UTC)
    for ts in grid_timestamps(start, end):
        assert int(ts.timestamp()) % SAMPLE_INTERVAL_SEC == 0


def test_grid_empty_when_end_before_start():
    t = dt.datetime(2026, 9, 4, tzinfo=UTC)
    assert grid_timestamps(t, t) == []
    assert grid_timestamps(t, t - dt.timedelta(hours=1)) == []


def test_reading_value_is_pure(facts):
    sensor = sensor_by_code("Polisher", "PAD_PRESSURE")
    ts = dt.datetime(2026, 9, 4, 3, 30, tzinfo=UTC)
    a = reading_value("EQP-CMP01", sensor, ts, 0.875, ("PAD_PRESSURE",))
    b = reading_value("EQP-CMP01", sensor, ts, 0.875, ("PAD_PRESSURE",))
    assert a == b


def test_backfill_and_tail_agree_on_overlap(facts):
    """백필로 만든 값과 라이브 추가로 만든 값이 같아야 한다."""
    t0 = dt.datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    whole = {(r["reading_ts"], r["eqp_id"], r["sensor_code"]): r["value"]
             for r in build_readings(facts, t0, t0 + dt.timedelta(hours=2))}
    tail = {(r["reading_ts"], r["eqp_id"], r["sensor_code"]): r["value"]
            for r in build_readings(facts, t0 + dt.timedelta(hours=1),
                                    t0 + dt.timedelta(hours=2))}
    assert tail
    for key, value in tail.items():
        assert whole[key] == value


def test_classify_boundaries():
    s = sensor_by_code("Furnace", "CHAMBER_TEMP")   # normal 1040..1060, alarm 1030..1070
    assert classify(1050.0, s) == "Normal"
    assert classify(1065.0, s) == "Warning"
    assert classify(1075.0, s) == "Alarm"
    assert classify(1025.0, s) == "Alarm"


def test_build_readings_row_count(facts):
    t0 = dt.datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    rows = build_readings(facts, t0, t0 + dt.timedelta(minutes=3))
    assert len(rows) == 8 * 6 * 6


def test_build_readings_has_no_forbidden_columns(facts):
    t0 = dt.datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    forbidden = {"lot_id", "product_code", "defect_code", "judgment", "result", "operator"}
    for r in build_readings(facts, t0, t0 + dt.timedelta(minutes=1)):
        assert not (forbidden & set(r))


def test_alarms_appear_and_are_rare(facts):
    t0 = dt.datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    rows = build_readings(facts, t0, t0 + dt.timedelta(hours=12))
    statuses = collections.Counter(r["status"] for r in rows)
    assert statuses["Alarm"] > 0
    assert statuses["Alarm"] / len(rows) < 0.05


def test_high_defect_equipment_alarms_more(facts):
    t0 = dt.datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    rows = build_readings(facts, t0, t0 + dt.timedelta(hours=12))
    alarms = collections.Counter(r["eqp_id"] for r in rows if r["status"] == "Alarm")
    assert alarms["EQP-CMP01"] > alarms["EQP-IMPL01"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_fdc_generator.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fdc_generator'`

- [ ] **Step 3: `src/fdc_generator.py` 구현**

이상 구간은 버킷 안에서 삼각 포락선을 그린다. 계단이 아니라 서서히 오르내려야
현실적이고, 경보가 버킷 전체가 아니라 정점 부근에서만 뜬다.

```python
def excursion_offset(eqp_id, sensor, ts, rate, candidates):
    exc = excursion_for(eqp_id, bucket_index(ts), rate, candidates)
    if exc is None or exc.sensor_code != sensor.sensor_code:
        return 0.0
    pos = (int(ts.timestamp()) % EXCURSION_BUCKET_SEC) / EXCURSION_BUCKET_SEC
    envelope = 1.0 - abs(2.0 * pos - 1.0)
    if exc.severity == "Alarm":
        half = (sensor.alarm_max - sensor.alarm_min) / 2.0
    else:
        half = (sensor.normal_max - sensor.normal_min) / 2.0
    return exc.direction * half * exc.magnitude * envelope


def reading_value(eqp_id, sensor, ts, rate, candidates):
    seconds_of_day = int(ts.timestamp()) % 86400
    value = sensor.base
    value += sensor.diurnal_amp * math.sin(2.0 * math.pi * seconds_of_day / 86400.0)
    value += random.Random(_seed("noise", eqp_id, sensor.sensor_code,
                                 int(ts.timestamp()))).gauss(0.0, sensor.sigma)
    value += excursion_offset(eqp_id, sensor, ts, rate, candidates)
    return round(value, 4)
```

`build_readings`는 설비별로 `defect_rates`와 `target_sensors`를 한 번만 계산해 두고
격자 × 센서를 순회한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_fdc_generator.py -q`
Expected: PASS — 10 passed

만약 `test_alarms_appear_and_are_rare`가 5%를 넘으면 `EXCURSION_SCALE`을 0.20으로
낮춘다. 0이면 0.30으로 올린다. 스펙 §11의 7번 항목과 같은 기준이다.

- [ ] **Step 5: 커밋**

```bash
git add customizing/fabric/fdc-eventhouse
git commit -m "feat(fdc): 순수 함수 판독값 생성기"
```

---

### Task 5: 테이블 스키마와 조립

**Files:**
- Create: `customizing/fabric/fdc-eventhouse/src/fdc_schema.py`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_fdc_schema.py`

**Interfaces:**
- Consumes: `src.fdc_sensors.build_sensor_spec_rows`, `src.fdc_generator.build_readings`
- Produces:
  - `TABLE_PREFIX: str = "fdc_"`
  - `FORBIDDEN_COLUMNS: frozenset[str]`
  - `TABLE_DDL: dict[str, str]` — 키 `fdc_sensor_spec`, `fdc_sensor_reading`
  - `TABLE_COLUMNS: dict[str, tuple[str, ...]]`
  - `ddl_columns(ddl: str) -> tuple[str, ...]`
  - `to_rows(table_name: str, rows: list[dict]) -> list[tuple]`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_two_tables_with_prefix():
    assert set(TABLE_DDL) == {"fdc_sensor_spec", "fdc_sensor_reading"}
    assert all(n.startswith(TABLE_PREFIX) for n in TABLE_DDL)


def test_ddl_has_no_forbidden_columns():
    for name in TABLE_DDL:
        assert not (FORBIDDEN_COLUMNS & set(TABLE_COLUMNS[name]))


def test_reading_ddl_types():
    ddl = TABLE_DDL["fdc_sensor_reading"]
    assert "reading_ts TIMESTAMP" in ddl
    assert "value DOUBLE" in ddl


def test_spec_rows_match_ddl_columns():
    rows = build_sensor_spec_rows()
    assert set(rows[0]) == set(TABLE_COLUMNS["fdc_sensor_spec"])


def test_reading_rows_match_ddl_columns(facts):
    t0 = dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc)
    rows = build_readings(facts, t0, t0 + dt.timedelta(minutes=1))
    assert set(rows[0]) == set(TABLE_COLUMNS["fdc_sensor_reading"])


def test_to_rows_preserves_ddl_order():
    rows = build_sensor_spec_rows()
    tup = to_rows("fdc_sensor_spec", rows)[0]
    columns = TABLE_COLUMNS["fdc_sensor_spec"]
    assert tup[columns.index("sensor_code")] == rows[0]["sensor_code"]
    assert len(tup) == len(columns)


def test_to_rows_raises_on_missing_key():
    with pytest.raises(KeyError):
        to_rows("fdc_sensor_spec", [{"sensor_code": "X"}])
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_fdc_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fdc_schema'`

- [ ] **Step 3: `src/fdc_schema.py` 구현**

```python
TABLE_DDL = {
    "fdc_sensor_spec": (
        "sensor_code STRING, sensor_name_ko STRING, eqp_type STRING, unit STRING, "
        "normal_min DOUBLE, normal_max DOUBLE, alarm_min DOUBLE, alarm_max DOUBLE, "
        "sample_interval_sec INT, is_active BOOLEAN"
    ),
    "fdc_sensor_reading": (
        "reading_ts TIMESTAMP, eqp_id STRING, eqp_type STRING, step_code STRING, "
        "sensor_code STRING, value DOUBLE, unit STRING, status STRING"
    ),
}
```

`to_rows`는 `dict`를 DDL 컬럼 순서 `tuple`로 바꾼다. `spark.createDataFrame`에 `dict`를
넘기면 스키마가 알파벳순으로 재정렬되기 때문이다. `qms_schema.py`와 같은 이유다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_fdc_schema.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: 커밋**

```bash
git add customizing/fabric/fdc-eventhouse
git commit -m "feat(fdc): KQL 테이블 스키마와 행 변환"
```

---

### Task 6: 적재 전 검증

Eventhouse는 append-only라 잘못 쓴 데이터를 되돌리기 번거롭다. 쓰기 전에 막는다.

**Files:**
- Create: `customizing/fabric/fdc-eventhouse/src/fdc_validate.py`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_fdc_validate.py`

**Interfaces:**
- Consumes: `src.fdc_schema`(`TABLE_COLUMNS`, `FORBIDDEN_COLUMNS`), `src.fdc_sensors`(`SAMPLE_INTERVAL_SEC`, `sensor_by_code`), `src.mes_probe.MesFacts`
- Produces:
  - `class Check` — frozen dataclass. 필드 `name: str`, `passed: bool`, `fatal: bool`, `detail: str`
  - `validate(facts: MesFacts, spec_rows: list[dict], readings: list[dict], watermark: datetime | None) -> list[Check]` — 스펙 §11의 8항목, 순서 고정
  - `format_report(checks: list[Check]) -> str`
  - `raise_on_fatal(checks: list[Check]) -> None` — 치명 항목 실패 시 `RuntimeError`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def _readings(facts, hours=12):
    t0 = dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc)
    return build_readings(facts, t0, t0 + dt.timedelta(hours=hours))


def test_all_checks_pass_on_good_data(facts):
    checks = validate(facts, build_sensor_spec_rows(), _readings(facts), None)
    assert len(checks) == 8
    failed = [c.name for c in checks if not c.passed]
    assert failed == []


def test_unknown_eqp_id_is_fatal(facts):
    rows = _readings(facts, hours=1)
    rows[0] = {**rows[0], "eqp_id": "EQP-GHOST"}
    checks = validate(facts, build_sensor_spec_rows(), rows, None)
    bad = [c for c in checks if not c.passed]
    assert bad and all(c.fatal for c in bad)
    with pytest.raises(RuntimeError):
        raise_on_fatal(checks)


def test_forbidden_column_is_fatal(facts):
    rows = _readings(facts, hours=1)
    rows[0] = {**rows[0], "lot_id": "LOT0001"}
    checks = validate(facts, build_sensor_spec_rows(), rows, None)
    assert any(not c.passed and c.fatal for c in checks)


def test_unaligned_timestamp_is_fatal(facts):
    rows = _readings(facts, hours=1)
    rows[0] = {**rows[0], "reading_ts": rows[0]["reading_ts"] + dt.timedelta(seconds=7)}
    checks = validate(facts, build_sensor_spec_rows(), rows, None)
    assert any(not c.passed and c.fatal for c in checks)


def test_reading_at_or_before_watermark_is_fatal(facts):
    rows = _readings(facts, hours=1)
    watermark = max(r["reading_ts"] for r in rows)
    checks = validate(facts, build_sensor_spec_rows(), rows, watermark)
    assert any(not c.passed and c.fatal for c in checks)


def test_unknown_sensor_code_is_fatal(facts):
    rows = _readings(facts, hours=1)
    rows[0] = {**rows[0], "sensor_code": "NO_SUCH"}
    checks = validate(facts, build_sensor_spec_rows(), rows, None)
    assert any(not c.passed and c.fatal for c in checks)


def test_raise_on_fatal_ignores_non_fatal(facts):
    checks = [Check("경보 비율", passed=False, fatal=False, detail="0%")]
    raise_on_fatal(checks)


def test_format_report_marks_each_check(facts):
    text = format_report(validate(facts, build_sensor_spec_rows(), _readings(facts), None))
    assert text.count("\n") >= 7
    assert "통과" in text or "OK" in text
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_fdc_validate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fdc_validate'`

- [ ] **Step 3: `src/fdc_validate.py` 구현**

검사 순서는 스펙 §11 표와 같다.

1. 금지 컬럼 부재 (치명)
2. `eqp_id` ⊆ MES 설비 (치명)
3. `sensor_code` ⊆ `fdc_sensor_spec` — `(eqp_type, sensor_code)` 조합으로 검사 (치명)
4. `reading_ts` 30초 격자 정렬 (치명)
5. 모든 `reading_ts` > watermark (치명, watermark가 `None`이면 통과)
6. `status`가 센서 한계와 일치 (비치명)
7. `Alarm` 비율 0 초과 5% 미만 (비치명)
8. 불량률 상위 설비의 `Alarm` 수 ≥ 하위 설비 (비치명)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest -q`
Expected: PASS — 전체 통과

- [ ] **Step 5: 커밋**

```bash
git add customizing/fabric/fdc-eventhouse
git commit -m "feat(fdc): 적재 전 검증 8항목"
```

---

### Task 7: 노트북 빌드와 문서

**Files:**
- Create: `customizing/fabric/fdc-eventhouse/build_notebook.py`
- Create: `customizing/fabric/fdc-eventhouse/fdc_eventhouse_stream.ipynb` (빌드 산출물)
- Create: `customizing/fabric/fdc-eventhouse/README.md`
- Create: `customizing/fabric/fdc-eventhouse/data-agent-schema.md`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_build_notebook.py`
- Test: `customizing/fabric/fdc-eventhouse/tests/test_docs.py`

**Interfaces:**
- Consumes: 모든 `src/` 모듈
- Produces:
  - `MODULE_ORDER: tuple[str, ...]` — `("mes_probe","fdc_sensors","fdc_anomaly","fdc_generator","fdc_schema","fdc_validate")`
  - `strip_local_imports(source: str) -> str`
  - `build_notebook(root: Path) -> nbformat.NotebookNode`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
NB = Path(__file__).resolve().parents[1] / "fdc_eventhouse_stream.ipynb"


def test_strip_removes_only_local_imports():
    src = "import math\nfrom src.fdc_sensors import SensorDef\nimport zlib\n"
    got = strip_local_imports(src)
    assert "from src." not in got
    assert "import math" in got and "import zlib" in got


def test_strip_handles_parenthesised_imports():
    src = "from src.fdc_sensors import (\n    SensorDef,\n    sensors_for,\n)\nx = 1\n"
    assert "from src." not in strip_local_imports(src)
    assert "x = 1" in strip_local_imports(src)


def test_notebook_is_current():
    """src/ 를 고치고 build_notebook.py 를 다시 돌리지 않으면 실패한다."""
    root = Path(__file__).resolve().parents[1]
    built = build_notebook(root)
    on_disk = nbformat.read(NB, as_version=4)
    assert [c.source for c in built.cells] == [c.source for c in on_disk.cells]


def test_notebook_has_no_local_imports():
    nb = nbformat.read(NB, as_version=4)
    for cell in nb.cells:
        assert "from src." not in cell.source


def test_parameters_cell_is_tagged():
    nb = nbformat.read(NB, as_version=4)
    tagged = [c for c in nb.cells if "parameters" in c.get("metadata", {}).get("tags", [])]
    assert len(tagged) == 1
    assert "KUSTO_URI" in tagged[0].source
    assert "KQL_DATABASE" in tagged[0].source
    assert 'MES_API_KEY = ""' in tagged[0].source


def test_notebook_uses_kusto_connector():
    nb = nbformat.read(NB, as_version=4)
    body = "\n".join(c.source for c in nb.cells)
    assert "com.microsoft.kusto.spark.synapse.datasource" in body
    assert "CreateIfNotExist" in body
    assert "credentials.getToken" in body


def test_notebook_validates_before_writing():
    nb = nbformat.read(NB, as_version=4)
    sources = [c.source for c in nb.cells]
    validate_at = next(i for i, s in enumerate(sources) if "raise_on_fatal" in s)
    write_at = next(i for i, s in enumerate(sources) if ".mode(\"Append\")" in s)
    assert validate_at < write_at
```

`test_docs.py`는 문서와 코드가 어긋나지 않게 잡는다.

```python
def test_readme_row_counts_match_code():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert str(len(build_sensor_spec_rows())) in readme


def test_schema_doc_lists_every_table():
    doc = (Path(__file__).resolve().parents[1] / "data-agent-schema.md").read_text(encoding="utf-8")
    for name in TABLE_DDL:
        assert name in doc


def test_schema_doc_lists_every_column():
    doc = (Path(__file__).resolve().parents[1] / "data-agent-schema.md").read_text(encoding="utf-8")
    for columns in TABLE_COLUMNS.values():
        for column in columns:
            assert f"`{column}`" in doc


def test_schema_doc_states_no_lot_id():
    doc = (Path(__file__).resolve().parents[1] / "data-agent-schema.md").read_text(encoding="utf-8")
    assert "lot_id" in doc and "MES" in doc
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_build_notebook.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_notebook'`

- [ ] **Step 3: `build_notebook.py` 구현**

`qms-lakehouse/build_notebook.py`와 같은 구조다. 파라미터 셀:

```python
_PARAMETERS = '''# Fabric 파이프라인에서 이 셀의 값을 덮어쓸 수 있습니다.
# KQL 데이터베이스 상세 카드의 Query URI 를 그대로 붙여 넣으세요. 비밀값이 아닙니다.
KUSTO_URI = ""
KQL_DATABASE = ""

MES_BASE_URL = "https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io"
# MES 인증 키. 이 작업 영역은 공유될 수 있고 노트북은 자동 저장되니 실행 후 지우세요.
MES_API_KEY = ""

BACKFILL_HOURS = 24
MAX_CATCHUP_HOURS = 6
'''
```

watermark 조회 셀은 테이블이 없을 때의 예외를 첫 실행 신호로 해석한다.

```python
_WATERMARK = '''# 테이블이 없으면 읽기가 실패합니다. 그것을 첫 실행 신호로 씁니다.
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
try:
    _wm = (
        spark.read.format(KUSTO_FORMAT)
        .option("accessToken", TOKEN)
        .option("kustoCluster", KUSTO_URI)
        .option("kustoDatabase", KQL_DATABASE)
        .option("kustoQuery", "fdc_sensor_reading | summarize wm = max(reading_ts)")
        .load().collect()
    )
    WATERMARK = _wm[0]["wm"] if _wm and _wm[0]["wm"] else None
except Exception as exc:
    print(f"watermark 조회 실패. 첫 실행으로 간주합니다. 원인: {exc}")
    WATERMARK = None

if WATERMARK is None:
    START = NOW - dt.timedelta(hours=BACKFILL_HOURS)
    print(f"첫 실행. {BACKFILL_HOURS}시간 백필합니다.")
else:
    if WATERMARK.tzinfo is None:
        WATERMARK = WATERMARK.replace(tzinfo=dt.timezone.utc)
    START = max(WATERMARK, NOW - dt.timedelta(hours=MAX_CATCHUP_HOURS))
    print(f"watermark {WATERMARK} 이후를 적재합니다.")
'''
```

적재 셀은 두 테이블을 쓴다. `fdc_sensor_spec`은 비어 있을 때만 쓴다.

```python
_LOAD = '''def write_table(table, rows):
    frame = spark.createDataFrame(to_rows(table, rows), schema=TABLE_DDL[table])
    (frame.write.format(KUSTO_FORMAT)
        .option("kustoCluster", KUSTO_URI)
        .option("kustoDatabase", KQL_DATABASE)
        .option("kustoTable", table)
        .option("accessToken", TOKEN)
        .option("tableCreateOptions", "CreateIfNotExist")
        .mode("Append").save())
    print(f"{table:20} {len(rows):7,}행 적재")

if SPEC_EXISTS:
    print(f"{'fdc_sensor_spec':20} 이미 존재. 건너뜁니다.")
else:
    write_table("fdc_sensor_spec", SPEC_ROWS)

if READINGS:
    write_table("fdc_sensor_reading", READINGS)
else:
    print("추가할 판독값이 없습니다. 스케줄 주기가 30초보다 짧을 수 있습니다.")
'''
```

- [ ] **Step 4: 노트북 생성**

Run: `python3 build_notebook.py`
Expected: `fdc_eventhouse_stream.ipynb 생성됨 · N셀`

- [ ] **Step 5: README와 data-agent-schema 작성**

`README.md`는 스펙 §13의 7단계 절차, 센서 42종 표, 3분/15분 주기 트레이드오프,
Eventstream을 쓰지 않은 이유를 담는다.

`data-agent-schema.md`는 두 테이블의 전 컬럼을 백틱으로 감싸 설명하고, `lot_id`가
없다는 사실과 그것을 MES에 물어야 한다는 점을 명시한다. 스펙 §14의 질문 5개를 싣는다.

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `python3 -m pytest -q`
Expected: PASS — 전체 통과

- [ ] **Step 7: 커밋**

```bash
git add customizing/fabric/fdc-eventhouse
git commit -m "feat(fdc): 노트북 빌드와 핸즈온 문서"
```

---

## Self-Review

**Spec coverage**

| 스펙 절 | 담당 태스크 |
|---|---|
| §3 MES 시간축 제약 → eqp_id 조인 | Task 1 (`derive_equipment`), Task 4 (readings에 lot 없음) |
| §4.1 무중복 원칙 | Task 2·4 테스트, Task 5 `FORBIDDEN_COLUMNS`, Task 6 검사 1 |
| §4.2 순수 함수 | Task 4 `test_backfill_and_tail_agree_on_overlap` |
| §4.3 crc32 시드 | Task 3 `test_seed_is_stable_across_processes` |
| §5 데이터 모델 2테이블 | Task 5 |
| §6 센서 42종 | Task 2 |
| §6.1 신호 합성 | Task 4 `reading_value` |
| §7 MES 유도 이상 주입 | Task 3 |
| §8 Kusto Spark 커넥터 | Task 7 `test_notebook_uses_kusto_connector` |
| §9 실행 모드·멱등성 | Task 7 watermark 셀, Task 6 검사 5 |
| §10 스케줄·용량 | Task 7 README |
| §11 검증 8항목 | Task 6 |
| §12 패키지 구조 | 전 태스크 |
| §13 핸즈온 절차 | Task 7 README |
| §14 세 시스템 질문 | Task 7 data-agent-schema |

빠진 요구사항 없음.

**Placeholder scan**

"TBD", "TODO", "적절히 처리", "위 내용에 대한 테스트 작성" 없음. 모든 코드 단계에 실제
코드 블록이 있다. 센서 28종의 수치는 Task 2 Step 3 표에 전부 명시했다.

**Type consistency**

- `_seed`는 Task 3에서 정의하고 Task 4에서 쓴다. 이름 일치.
- `bucket_index`, `excursion_for`, `EXCURSION_BUCKET_SEC`는 Task 3 정의 → Task 4 소비. 일치.
- `sensor_by_code`는 Task 2 정의 → Task 4·6 테스트에서 사용. 일치.
- `build_readings(facts, start, end)` 시그니처가 Task 4 정의와 Task 5·6 테스트에서 동일.
- `TABLE_COLUMNS`, `TABLE_DDL`, `to_rows`는 Task 5 정의 → Task 6·7 소비. 일치.
- `Check(name, passed, fatal, detail)` 필드 순서가 Task 6 정의와 테스트에서 동일.
- `MesFacts.equipment` 항목 키(`eqp_id`, `eqp_type`, `step_code`)가 Task 1 정의와
  Task 3·4 사용처에서 동일.

---

## 계획 밖에서 나온 것 (2026-09-08)

계획을 다 실행한 뒤 사용자가 "세 시스템 타임라인을 순차 검증해 달라"고 해서 실측했다.
계획서가 전제한 것 중 둘이 틀렸다.

### 전제 오류 1 — "MES 앵커는 배포 시점에 고정된다"

계획서는 MES 구간을 고정된 과거로 봤다. 실측하니 **앵커가 계속 움직인다.** `/data` 가
EmptyDir 이고 `minReplicas: 0` 이라 scale-to-zero 마다 SQLite 가 사라지고 재시드된다.
`resolve_anchor()` 가 env 없으면 `datetime.now()` 로 떨어진다.

20분 간격 관측에서 이동을 확인했다. 컨테이너가 사는 동안은 고정이라 짧게 보면 안 보인다.

계획서 Task 5 는 `span(FACTS)` 로 구간을 유도하므로 **매 실행은 내부 정합이 맞다.** 문제는
실행 사이다. 이미 적재한 데이터와 새로 읽은 MES 가 다른 앵커를 가리키면 한 테이블에 두
시간축이 섞인다. 4시간 이동 후 증분 1회로 106,768행 중 40%가 라이브와 모순되는데 에러는
없다.

`_WATERMARK` 셀에 드리프트 가드를 넣었다. 가동 표본 최댓값이 앵커보다 정확히 한
격자(30초) 이르다는 성질로 적재된 앵커를 복원해 대조한다. 임계값 5분 — 격자 흔들림은
1분 미만이고 관측된 최소 이동은 20분이다.

**계획서에 이 항목이 없었던 이유**: 시간축 재설계(`22c3894`) 때 MES 를 고쳤고, 고친
결과가 배포에 반영됐다고 가정했다. bicep 코드는 정확한데 실 배포가 그 bicep 으로 되지
않았다. 코드를 고친 것과 배포된 것을 구분하지 않은 게 원인이다.

### 전제 오류 2 — "스펙 조인은 `sensor_code` 로 충분하다"

Task 2 는 스펙 42행을 `(eqp_type, sensor_code)` 로 설계했다. 유일성은 맞는데, **문서가
조인 키를 말하지 않았다.**

```
판독            102,552행
on sensor_code  369,458행 (3.60배) · 틀린 짝 266,906 (72.2%)
복합키           102,552행 (1.00배) · 틀린 짝 0
```

`inner join` 은 하나를 고르지 않고 모든 짝을 만든다. 처음엔 "dedup 하면 40.8%"로 계산했는데
그건 검사 코드가 `{s["sensor_code"]: s}` 로 뭉갠 결과였다. **데이터는 멀쩡했고 문서가
비어 있었다.**

피해가 센서마다 다르다. `AMBIENT_*` 는 유형이 일곱인데 한계치가 같아 부풀림의 91%를
내면서 판정은 하나도 안 뒤집는다. 판정을 뒤집는 것은 `CHAMBER_*` 둘뿐이고, 그중
`CHAMBER_TEMP` 는 Furnace 1050 / CVD 420 / Etcher 65 가 **셋 다 `degC`** 라 단서가 없다.

`status` 와 `unit` 이 판독 행에 이미 있어 판정에는 조인이 필요 없다. 그 안내를 세우고,
복제된 값이 스펙과 일치하는지 전량 검사한다. 스펙 10컬럼 중 복제는 셋뿐이라 나머지
일곱은 조인해야 하고 그 목록도 적었다.

### 방법론 — 검사 범위를 넓히는 게 결함을 직접 찾는 것보다 낫다

QMS 세션과 네 라운드를 돌면서 매번 같은 순서였다.

```
검사 범위를 넓힘 → 데이터는 멀쩡 → 문서가 틀렸거나 비어 있음
  → 그 문서를 고정하려고 검사를 만듦 → 그 과정에서 옛 검사의 결함이 드러남
```

계획서 Task 6 검증기 7항목은 전부 통과하고 있었다. 결함은 그 **범위 밖**에 있었다.

### 반복해서 밟은 함정

**부분 문자열 검사** — `X in doc` 은 `X_Y` 도 통과한다. 두 번 연속 밟았다. 절로 잘라
검사하도록 고쳤는데, 이번엔 그 절 안에 컬럼 표가 통째로 들어 있어 또 뚫렸다. 중복이 절
경계보다 안쪽에 있으면 절도 소용없다.

**돌연변이 미적용을 통과로 읽기** — 치환 문자열이 안 맞아 아무것도 안 바뀐 것을
"테스트가 견뎠다"로 읽을 뻔한 게 두 번. 스크립트에 문자열 존재와 실제 변경 여부를 둘 다
확인하는 줄을 넣었다.

**숫자를 문서에 적기** — `assert "24" in readme` 는 낡아도 통과한다. 픽스처에서 직접 세어
대조하되 배수 ±0.5 / 비율 ±5%p 여유를 준다.

### 전제 오류 3 — "앵커는 시각만 옮긴다"

전제 오류 1 을 쓸 때 앵커 이동을 **적재 정합성** 문제로만 봤다. 실측하니 더 깊었다.

MES 는 앵커를 마지막 평행이동에만 쓴다. `Random(42)` 고정이라 로트별 in/out 간격도 설비
배정도 불량도 불변이다. 픽스처와 라이브의 상대구조 해시가 실제로 같았다 —
`c68979cc73011c78`, 앵커만 4일 2시간 차이. **그래서 픽스처 갱신은 처음부터 필요 없었다.**
재배포를 기다린 것 자체가 잘못된 전제였다.

그런데 FDC 판독은 평행이동에 불변이 아니었다. 원인을 `diurnal()` 하나로 단정하고 "물리적
으로 맞는 동작이라 고칠 것이 아니다"라고 적었다. **절반만 맞았다.**

### 전제 오류 4 — "경보를 옮기는 것은 하루 주기다"

QMS 가 "정수 일수 이동은 시(hour)를 보존하므로 하루 주기 성분을 못 잡는다"고 알려 왔다.
그 말이 맞다면 내 표의 `+11일` 열은 원본과 같아야 하는데 경보가 301 → 310 이었다.
**앞뒤가 안 맞아 성분을 하나씩 껐다.**

```
                    +37시간(비정수)    +264시간(정수 11일)
diurnal 만          설비 다름          설비 같음      ← QMS 말대로
noise 만            설비 다름          설비 다름      ← 범인
```

`noise()` 가 `random.Random(seed(eqp_id, code, int(moment.timestamp())))` 였다. **절대
시각이 시드였다.** 잡음은 재현성 장치지 물리가 아니므로 앵커에 묶일 이유가 없다. 앵커
상대로 바꾸니 정수 일수 이동에서 102,552 행 중 값이 다른 행이 **0** 이 됐다.

| | 원본 | +37시간 | +11일 | +30일 |
|---|---|---|---|---|
| Alarm 행 | 294 | 257 | 294 | 294 |
| 원본과 값이 다른 행 | — | 102,540 | **0** | **0** |

이제 남은 앵커 의존은 `diurnal` 하나뿐이고, 그것만이 진짜로 물리다. **정수 일수에서 값
차이가 0 이라는 것이 그 증거다** — 고치기 전에는 이 구분 자체가 불가능했다.

`anchor` 에 기본값을 두지 않았다. 빠뜨리면 `TypeError` 로 즉시 터진다. 기본값을 두면
호출부에서 넘기는 것을 잊었을 때 조용히 옛 동작으로 돌아간다.

### 그 수정이 드러낸 것 — 우연히 통과하던 불변식

시드를 바꾸자 `test_unhinted_sensors_never_alarm` 이 깨졌다. 지목받지 않은 `GAS_FLOW`
가 주의를 냈다.

이름은 `never_alarm` 인데 내용이 `!= NORMAL` 이라 **주의까지 막고 있었다.** 그것은 지킬
수 없는 약속이다.

```
경보 한계   10σ 밖   →  잡음으로 닿지 않는다
정상 범위   4.17~10σ →  주의는 확률적으로 나온다
```

`RETICLE_TEMP` 는 10만 표본에 3.17건이 기대값이다. 일곱 개 시프트에서 재니 무지목 경보는
**전부 0건**, 무지목 주의는 0~2건이었다. 경보만 금지하도록 고치고, 주의는 정규분포에서
기대값을 구해 대조한다. σ 나 정상범위를 조정하면 기대값이 따라 움직이므로 낡지 않는다.

**시드에 기대던 불변식이었다.** 절대 시각 시드가 우연히 그 표본에서 주의를 안 냈을 뿐이다.

### 실전 증명 — 라이브가 픽스처와 같은 값을 냈다

사용자가 앵커를 고정한 뒤 실 API 를 떴다.

```
픽스처 앵커  2026-09-04T00:00:00+00:00
라이브 앵커  2026-09-01T00:00:00+00:00   20초 간격 2회 조회에 고정
차이         정확히 3일 — 하루의 정수배

판독 102,552 vs 102,552 · 값이 같은 행 102,552 (100.0%)
Alarm 294 · 경보 설비 CMP01,CVD01,DIFF01,ETCH01,PHOT02 — 픽스처와 동일
```

**수정 전이었다면 10만 행이 전부 달랐다.** 픽스처로 검증한 것과 참가자가 라이브로 돌린
것이 다른 데이터였을 것이고, 그 사실을 알 방법도 없었다. QMS 가 픽스처를 갈아본 뒤에야
자기 수정이 작동하는지 확인됐다고 했는데 같은 자리다.

### 계획에 없던 도구 — `mutants.py`

검증 항목이 늘면서 "테스트가 실제로 잡는가"를 매번 손으로 확인하게 됐다. 재사용 도구로
만들었다. 19종을 정의하고 네 가지 조용한 실패를 가른다.

| 실패 방식 | 증상 | 출처 |
|---|---|---|
| 치환 문자열 부재 | 파일이 안 바뀜 | QMS |
| 값 불변 | 바꿨는데 같음 | QMS |
| 코드 크래시 | `NameError` 로 45개 실패 | QMS |
| 산출물 미갱신 | 셋 다 통과인데 안 잡힘 | 이번 |

넷째는 빌드 산출물이 있는 프로젝트 공통이다. `build_notebook.py` 를 고쳐도 `.ipynb` 를
다시 만들지 않으면 테스트가 옛 산출물을 읽는다. 되돌릴 때도 재빌드해야 한다 — 원본만
복원했더니 돌연변이가 박힌 노트북이 남아 다음 테스트가 엉뚱하게 실패했다.

### 남은 것

앵커가 고정됐으므로 Fabric 실행만 남았다. Eventhouse 생성 → Query URI 를 노트북에 넣고
Run all(백필) → 3분 스케줄. 첫 실행에서 `maxif` 의 빈 `datatable` 레그와 백필 소요가
드러난다.
