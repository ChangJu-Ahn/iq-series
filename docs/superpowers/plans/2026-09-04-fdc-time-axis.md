# FDC 시간축 재설계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FDC 텔레메트리를 벽시계 24시간 창에서 떼어내, MES 공정이력의 런 구간에 맞춰 가동/유휴를 구분해 생성하고, 이상을 불량이 난 그 런에만 싣는다.

**Architecture:** MES 공정이력이 시간축을 갖게 되었으므로(별도 계획 `2026-09-04-mes-time-axis.md`), 설비마다 "언제 무슨 로트를 돌렸는지"를 알 수 있다. 그 정보로 30초 격자 위의 각 시각을 런/유휴로 분류한다. 런이면 센서 6종을 30초로, 유휴면 공통 센서 2종을 5분으로 낸다. **로트 번호는 내부 계산에만 쓰고 판독값에는 싣지 않는다**(아래 설계 결정 참조).

**Tech Stack:** Python 3.10+, 표준 라이브러리만(`datetime`, `math`, `random`, `hashlib`), pytest, Fabric 노트북(PySpark)

## 설계 결정: `lot_id` 를 싣지 않고 `run_status` 를 싣는다

스펙 §5.2 는 판독값에 `lot_id` 를 추가하라고 적었다. **이 계획은 그것을 따르지 않는다.** 스펙을 쓸 때 놓친 충돌이 있다.

`src/fdc_validate.py` 의 첫 번째 검사가 **치명(fatal)** 으로 이것을 금지하고 있다.

```python
FORBIDDEN_COLUMNS = frozenset(
    {"lot_id", "product_code", "wafer_qty", "defect_code", "judgment", "result", "operator"}
)
# Check 1 · fatal · "무중복 원칙: 로트 관련 컬럼이 없다" → "FDC 는 로트를 모른다"
```

검사를 지우고 `lot_id` 를 넣을 수도 있었지만, 세 가지 이유로 원래 원칙이 옳다고 판단했다.

1. **교육 목표를 무너뜨린다.** 이 핸즈온의 목적은 에이전트가 MES·QMS·FDC 세 시스템을 교차 질의하는 것이다. `lot_id` 가 FDC 안에 있으면 "어느 로트가 이상했나"를 Eventhouse 하나로 답해 버린다. 없으면 실습자는 "EQP-ETCH01 이 03:20 에 경보를 냈다" 까지만 알아내고, **그 시각에 무엇이 돌았는지 MES 에 물어야 한다.** 시스템 경계를 넘는 시간 조인이야말로 이 실습이 가르치려는 것이다.

2. **재배포하면 거짓이 된다.** MES 앵커는 배포 시점에 고정되므로 재배포할 때마다 로트-시각 대응이 통째로 바뀐다. Eventhouse 는 append-only 라 이미 쓴 행이 남는다. `lot_id` 는 그 순간 틀린 값이 되지만 `eqp_id` 는 재배포와 무관하게 유효하다. 에이전트가 자신 있게 인용하는 틀린 로트 번호는 상관없는 잡음보다 훨씬 해롭다.

3. **런/유휴 구분에는 로트가 필요 없다.** 필요한 건 "이 설비가 지금 돌고 있나"이고, 그것은 설비 상태(SEMI E10)이지 로트 정보가 아니다. `run_status` 컬럼 하나면 충분하고 `FORBIDDEN_COLUMNS` 와 충돌하지 않는다.

**대신 `run_status` 를 넣는다.** 값은 `"Run"` 과 `"Idle"` 두 가지다. `lot_id` 는 `build_readings` 내부에서 런 구간과 이상 배치를 계산하는 데만 쓰고 행에 남기지 않는다.

Task 2 를 시작하기 전에 스펙 §5.2 를 이 결정으로 고친다(Task 2 Step 1).

## Global Constraints

- **저장소:** 이 저장소(`iq-series`). 모든 경로는 `customizing/fabric/fdc-eventhouse/` 기준이며, 명령은 그 디렉터리에서 실행한다.
- **선행 조건:** `mock-mes-kr` 의 시간축 변경이 로컬 `main` 에 있어야 한다. Task 0 이 확인한다.
- **`src/` 는 표준 라이브러리만 import 한다.** 노트북에 그대로 인라인되므로 `pyspark` 나 서드파티 패키지를 쓰면 안 된다.
- **`src/` 에 모듈을 새로 만들면 `build_notebook.py` 의 `MODULE_ORDER` 에 등록한다.** 등록하지 않으면 노트북에서 `NameError` 가 난다.
- **`src/` 를 고치면 반드시 `python3 build_notebook.py` 를 다시 돌린다.** 노트북은 생성물이다.
- **`hash()` 금지.** `PYTHONHASHSEED` 때문에 프로세스마다 값이 달라진다. `src/fdc_anomaly.py` 의 `seed()`(sha256 기반)를 쓴다.
- **순수성:** 같은 `(설비, 센서, 타임스탬프)` 는 언제 어디서 불러도 같은 값이어야 한다. 백필 구간과 라이브 구간이 이어져야 하기 때문이다. 실행 시각에 의존하는 값을 만들지 마라.
- **테스트:** `python3 -m pytest tests -q`
- **커밋:** 한국어 메시지, 아래 트레일러 포함.
  ```
  Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
  ```
- **스펙:** `docs/superpowers/specs/2026-09-04-mes-fdc-time-axis-redesign.md` §5.

## 확인된 기존 심볼 (그대로 쓴다)

| 모듈 | 심볼 |
|---|---|
| `fdc_sensors` | `SAMPLE_INTERVAL_SEC=30`, `SensorDef`, `COMMON_SENSORS`, `TYPE_SENSORS`, `sensors_for(eqp_type)`, `build_sensor_spec_rows()` |
| `fdc_anomaly` | `EXCURSION_MIN=1.2`, `EXCURSION_MAX=2.8`, `EXCURSION_PERIOD_SEC=2400`, `DEFECT_SENSOR_HINT`, `FALLBACK_SENSOR`, `seed(*parts)`, `EquipmentProfile`, `build_profiles(facts)`, `fallback_sensor(eqp_type)`, `hinted_sensors(profile)`, `excursion_amplitude(profile)`, `excursion_for(profile, sensor_code)`, `excursion_sign(eqp_id, sensor_code)` |
| `fdc_generator` | `NORMAL`, `WARNING`, `ALARM`, `align_to_grid(moment, interval_sec)`, `grid_timestamps(start, end, interval_sec)`, `diurnal`, `noise`, `excursion_offset`, `reading_value`, `classify`, `build_readings(facts, start, end)` |
| `fdc_schema` | `READING_TABLE`, `READING_COLUMNS`, `READING_SCHEMA`, `TABLE_DDL`, `to_iso`, `to_rows`, `watermark_query`, `spark_schema` |
| `fdc_validate` | `FORBIDDEN_COLUMNS`, `MAX_ALARM_RATIO=0.05`, `Check(number, name, fatal, passed, detail="")`, `ValidationFailed`, `validate(readings, facts, watermark=None)`, `format_report`, `raise_on_fatal` |
| `mes_probe` | `MesFacts.from_dict(payload)`, `derive_equipment(process_results, route)` |
| `build_notebook` | `MODULE_ORDER`, `_PARAMETERS`, `_GATE`, `_WATERMARK`, `_BUILD`, `build_notebook(root)` |

`MesFacts.from_dict` 는 `payload["process_results"]`, `payload["route"]`, `payload.get("equipment")` 만 읽는다. 픽스처에 키를 더해도 무시된다. `equipment` 가 비어 있으면 `derive_equipment` 가 공정이력에서 만든다.

---

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `src/fdc_runs.py` | MES 공정이력 → 설비별 런 구간. 순수. | 신규 |
| `src/fdc_generator.py` | 단일 타임라인 분류, 런/유휴 판독값 | 대폭 수정 |
| `src/fdc_anomaly.py` | 런 단위 이상 주입 | 수정 |
| `src/fdc_schema.py` | `run_status` 컬럼 | 소폭 수정 |
| `src/fdc_sensors.py` | `idle_sensors()` | 소폭 수정 |
| `src/fdc_validate.py` | `run_status` 검증 | 수정 |
| `build_notebook.py` | `MODULE_ORDER` 등록, 24h 캡 제거 | 수정 |
| `tests/conftest.py` | `busy_window` 픽스처 | 수정 |
| `tests/fixtures/mes_facts.json` | 시간축 있는 스냅샷 | 재생성 |

---

### ✅ Task 0: 시간축 있는 픽스처를 만든다

MES 시드는 결정적이다. Azure 재배포를 기다리지 않고 로컬에서 시드를 돌려 픽스처를 뽑는다. 재배포는 실제 핸즈온 데이터에만 필요하다.

현재 픽스처는 91행의 고유 시각이 **2개**뿐이다(`2026-09-04T06:53:09Z`, `2026-09-04T06:53:10Z`). 런 구간을 만들 수 없다.

**Files:**
- Modify: `tests/fixtures/mes_facts.json`

**Interfaces:**
- Produces: `process_results` 각 행에 서로 다른 `in_time`/`out_time`. 필드 구성과 업무 값(설비 배정·불량코드·판정)은 기존과 동일. `anchor` 키가 추가된다.

- [x] **Step 1: MES 쪽 변경이 들어와 있는지 확인한다**

```bash
cd ~/Repo/mock-mes-kr && git --no-pager log --oneline -8 && ls mes_core/schedule.py
```

Expected: 시간축 커밋들이 보이고 `mes_core/schedule.py` 가 존재한다.

없으면 **여기서 멈춘다.** `mock-mes-kr` 의 `2026-09-04-mes-time-axis.md` 가 먼저 끝나고 머지돼야 한다.

- [x] **Step 2: 새 시드로 픽스처를 만든다**

앵커를 고정값으로 주고, 저장 시 `anchor` 를 함께 남긴다. 그래야 테스트가 특정 날짜에 묶이지 않는다(스펙 §5.6).

```bash
cd customizing/fabric/fdc-eventhouse && python3 - <<'PY'
import json, os, sqlite3, subprocess, sys, tempfile

MES = os.path.expanduser("~/Repo/mock-mes-kr")
ANCHOR = "2026-09-04T00:00:00Z"
db = tempfile.mktemp(suffix=".db")

env = dict(os.environ, MES_DB_PATH=db, MES_ANCHOR=ANCHOR)
subprocess.run([sys.executable, "-m", "mes_core.seed"], cwd=MES, env=env, check=True)

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
results = [dict(r) for r in conn.execute(
    "SELECT pr.*, ps.step_name FROM process_result pr"
    " LEFT JOIN process_step ps ON ps.step_code = pr.step_code"
    " ORDER BY pr.id DESC")]
route = [dict(r) for r in conn.execute("SELECT * FROM process_step ORDER BY seq")]
conn.close()
os.remove(db)

with open("tests/fixtures/mes_facts.json", "w", encoding="utf-8") as fh:
    json.dump({"anchor": ANCHOR, "process_results": results, "route": route},
              fh, ensure_ascii=False, indent=2)
    fh.write("\n")

outs = [r["out_time"] for r in results]
print(f"공정이력 {len(results)}건 · 고유 out_time {len(set(outs))}")
print(f"구간 {min(r['in_time'] for r in results)} ~ {max(outs)}")
print(f"불량코드 있는 행 {sum(1 for r in results if r.get('defect_code'))}")
PY
```

Expected: `공정이력 91건 · 고유 out_time 91`, 구간이 약 65시간, `불량코드 있는 행 35`.

이 SQL 이 만드는 컬럼 집합이 기존 픽스처와 다르면(예: `step_name` 누락) 아래 Step 3 이 잡아낸다.

- [x] **Step 3: 기존 테스트로 회귀를 본다**

업무 데이터는 안 바뀌었으므로 **전부 통과한다.**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests -q 2>&1 | tail -25
```

Expected: `252 passed`

지금 소스는 시간을 모른다 — `build_readings` 가 런과 무관하게 모든 설비·모든 격자에 값을 찍으므로 `T0` 가 MES 구간 밖이어도 행이 나온다. **이게 바로 고치려는 문제다.** 시간축이 들어오는 Task 4 에서 `T0` 를 쓰는 테스트가 깨지고, 거기서 `busy_window` 로 옮긴다.

여기서 하나라도 깨지면 픽스처의 컬럼 집합이 달라진 것이므로 Step 2 로 돌아간다.

- [x] **Step 4: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/tests/fixtures/mes_facts.json
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "시간축이 들어간 MES 스냅샷으로 픽스처를 갈아끼운다

공정이력 91건의 고유 시각이 2개뿐이라 런 구간을 만들 수 없었다. 업무
데이터는 그대로고 시각만 달라진다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### ✅ Task 1: 설비별 런 구간을 뽑는다

**Files:**
- Create: `src/fdc_runs.py`
- Create: `tests/test_fdc_runs.py`
- Modify: `build_notebook.py` (`MODULE_ORDER`)

**Interfaces:**
- Consumes: `MesFacts.process_results` (각 행에 `eqp_id`, `lot_id`, `step_code`, `in_time`, `out_time`, `defect_code`)
- Produces:
  - `@dataclass(frozen=True) Run`: `eqp_id: str`, `lot_id: str`, `step_code: str`, `start: datetime`, `end: datetime`, `defect_code: str | None`
  - `parse_ts(value: str) -> datetime`
  - `runs_by_equipment(facts) -> dict[str, list[Run]]`
  - `run_at(runs: list[Run], moment: datetime) -> Run | None`
  - `span(facts) -> tuple[datetime, datetime]`

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fdc_runs.py` 를 새로 만든다.

```python
from datetime import datetime, timedelta, timezone

import pytest

from src.fdc_runs import Run, parse_ts, run_at, runs_by_equipment, span

UTC = timezone.utc


def _fact(lot, step, eqp, start, end, defect=None):
    return {
        "lot_id": lot, "step_code": step, "eqp_id": eqp,
        "in_time": start, "out_time": end, "defect_code": defect,
    }


class _Facts:
    """MesFacts 의 최소 대역. 이 모듈은 세 필드만 본다."""

    def __init__(self, rows):
        self.process_results = rows
        self.route = []
        self.equipment = []


ROWS = [
    _fact("LOT0002", "ETCH", "EQP-ETCH01",
          "2026-09-03T04:00:00+00:00", "2026-09-03T05:00:00+00:00"),
    _fact("LOT0001", "ETCH", "EQP-ETCH01",
          "2026-09-03T01:00:00+00:00", "2026-09-03T02:00:00+00:00", "Particle"),
    _fact("LOT0001", "METRO", None,
          "2026-09-03T02:30:00+00:00", "2026-09-03T02:45:00+00:00"),
]


def test_parse_ts_is_utc_aware():
    got = parse_ts("2026-09-03T01:00:00+00:00")
    assert got == datetime(2026, 9, 3, 1, tzinfo=UTC)
    assert got.tzinfo is not None


def test_parse_ts_accepts_z_suffix():
    assert parse_ts("2026-09-03T01:00:00Z") == datetime(2026, 9, 3, 1, tzinfo=UTC)


def test_parse_ts_treats_naive_as_utc():
    assert parse_ts("2026-09-03T01:00:00") == datetime(2026, 9, 3, 1, tzinfo=UTC)


def test_runs_are_grouped_by_equipment():
    assert set(runs_by_equipment(_Facts(ROWS))) == {"EQP-ETCH01"}


def test_runs_without_equipment_are_dropped():
    """METRO 는 설비를 배정받지 않으므로 센서 데이터가 존재할 수 없다."""
    got = runs_by_equipment(_Facts(ROWS))
    assert not any(r.step_code == "METRO" for runs in got.values() for r in runs)


def test_runs_are_sorted_by_start():
    got = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    assert [r.lot_id for r in got] == ["LOT0001", "LOT0002"]


def test_run_carries_defect_code():
    got = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    assert got[0].defect_code == "Particle"
    assert got[1].defect_code is None


def test_run_at_is_half_open():
    runs = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    start = datetime(2026, 9, 3, 1, tzinfo=UTC)
    end = datetime(2026, 9, 3, 2, tzinfo=UTC)
    assert run_at(runs, start).lot_id == "LOT0001"
    assert run_at(runs, end - timedelta(seconds=1)).lot_id == "LOT0001"
    assert run_at(runs, end) is None


def test_run_at_returns_none_between_runs():
    runs = runs_by_equipment(_Facts(ROWS))["EQP-ETCH01"]
    assert run_at(runs, datetime(2026, 9, 3, 3, tzinfo=UTC)) is None


def test_run_at_on_empty_list():
    assert run_at([], datetime(2026, 9, 3, tzinfo=UTC)) is None


def test_span_includes_rows_without_equipment():
    lo, hi = span(_Facts(ROWS))
    assert lo == datetime(2026, 9, 3, 1, tzinfo=UTC)
    assert hi == datetime(2026, 9, 3, 5, tzinfo=UTC)


def test_span_raises_on_empty():
    with pytest.raises(ValueError):
        span(_Facts([]))


def test_real_facts_produce_runs_for_every_tool(facts):
    assert set(runs_by_equipment(facts)) == {e["eqp_id"] for e in facts.equipment}


def test_real_runs_never_overlap_on_one_tool(facts):
    for eqp_id, runs in runs_by_equipment(facts).items():
        for prev, cur in zip(runs, runs[1:]):
            assert prev.end <= cur.start, eqp_id


def test_real_span_is_between_two_and_four_days(facts):
    lo, hi = span(facts)
    assert timedelta(days=2) < hi - lo < timedelta(days=4)


def test_real_defect_runs_exist(facts):
    runs = [r for rs in runs_by_equipment(facts).values() for r in rs]
    assert sum(1 for r in runs if r.defect_code) > 10
```

- [x] **Step 2: 실패를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_fdc_runs.py -q 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'src.fdc_runs'`

- [x] **Step 3: 구현한다**

`src/fdc_runs.py` 를 새로 만든다.

```python
"""MES 공정이력을 설비별 런 구간으로 바꾼다. 전부 순수 함수다.

판독값을 가동과 유휴로 나누고 이상을 어느 구간에 실을지 정하려면 "이 시각에
이 설비가 무엇을 하고 있었나"를 알아야 한다. 그 정보는 MES 공정이력의
in_time/out_time 에만 있다.

구간은 [start, end) 반열림이다. 한 설비의 두 런이 경계에서 맞닿아도 한 시각이
양쪽에 속하지 않는다.

Run.lot_id 는 이상 배치 계산에만 쓰고 판독값에는 싣지 않는다. FDC 가 로트를
들고 있으면 실습자가 Eventhouse 하나로 답을 내버려서 MES 에 물을 이유가
사라진다. 자세한 근거는 계획서 "설계 결정" 절에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Run:
    eqp_id: str
    lot_id: str
    step_code: str
    start: datetime
    end: datetime
    defect_code: str | None


def parse_ts(value: str) -> datetime:
    """MES 시각 문자열을 tz-aware UTC datetime 으로."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def runs_by_equipment(facts) -> dict[str, list[Run]]:
    """설비별 런 구간. start 오름차순.

    eqp_id 가 없는 공정이력은 버린다. METRO(계측)는 설비를 배정받지 않으므로
    센서 데이터가 존재할 수 없다. 모든 공정에 FDC 가 붙어 있지는 않다는 것
    자체가 현실적인 교육 소재다.
    """
    grouped: dict[str, list[Run]] = {}
    for row in facts.process_results:
        eqp_id = row.get("eqp_id")
        if not eqp_id:
            continue
        grouped.setdefault(eqp_id, []).append(
            Run(
                eqp_id=eqp_id,
                lot_id=row["lot_id"],
                step_code=row["step_code"],
                start=parse_ts(row["in_time"]),
                end=parse_ts(row["out_time"]),
                defect_code=row.get("defect_code") or None,
            )
        )
    for runs in grouped.values():
        runs.sort(key=lambda r: r.start)
    return grouped


def run_at(runs: list[Run], moment: datetime) -> Run | None:
    """이 시각에 돌고 있던 런. 없으면 None.

    선형 탐색이다. 설비당 런이 20건 미만이라 이분 탐색을 넣을 이유가 없다.
    start 오름차순이므로 시작이 moment 를 지나면 더 볼 필요가 없다.
    """
    for run in runs:
        if run.start <= moment < run.end:
            return run
        if run.start > moment:
            break
    return None


def span(facts) -> tuple[datetime, datetime]:
    """공정이력 전체가 걸쳐 있는 구간.

    설비가 없는 행도 포함한다. 생성 범위의 시작점을 정하는 용도라
    "MES 가 아는 가장 이른 시각"이어야 한다.
    """
    rows = facts.process_results
    if not rows:
        raise ValueError("공정이력이 비어 있어 생성 구간을 정할 수 없습니다.")
    return (
        min(parse_ts(r["in_time"]) for r in rows),
        max(parse_ts(r["out_time"]) for r in rows),
    )
```

- [x] **Step 4: 노트북 모듈 목록에 등록한다**

등록하지 않으면 노트북 안에서 `span` 과 `runs_by_equipment` 가 정의되지 않아 `NameError` 가 난다. `fdc_runs` 는 다른 `src` 모듈에 의존하지 않으므로 `mes_probe` 바로 뒤에 둔다.

`build_notebook.py` 의 `MODULE_ORDER` 를 아래로 바꾼다.

```python
MODULE_ORDER = (
    "mes_probe",
    "fdc_runs",
    "fdc_sensors",
    "fdc_anomaly",
    "fdc_generator",
    "fdc_schema",
    "fdc_validate",
)
```

- [x] **Step 5: 셀 수 기대값을 고친다**

모듈이 하나 늘면 노트북 셀도 하나 는다. `tests/test_build_notebook.py::test_notebook_cell_count_is_stable` 의 마지막 리터럴만 바꾼다. 앞의 계산식은 `len(MODULE_ORDER)` 를 쓰므로 그대로 맞다.

```python
    # intro + parameters + 7 modules + gate + connect + watermark + build + validate + load + outro
    assert len(notebook.cells) == 1 + 1 + len(MODULE_ORDER) + 6 + 1 == 16
```

기존은 주석이 `6 modules`, 끝이 `== 15` 다.

- [x] **Step 6: 통과를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 build_notebook.py && \
  python3 -m pytest tests/test_fdc_runs.py tests/test_build_notebook.py -q 2>&1 | tail -5
```

Expected: 전부 통과. `fdc_runs` 를 `MODULE_ORDER` 에 넣고 노트북을 다시 만들지 않으면 `test_notebook_matches_sources` 가 깨진다.

- [x] **Step 7: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/src/fdc_runs.py \
        customizing/fabric/fdc-eventhouse/tests/test_fdc_runs.py \
        customizing/fabric/fdc-eventhouse/tests/test_build_notebook.py \
        customizing/fabric/fdc-eventhouse/build_notebook.py \
        customizing/fabric/fdc-eventhouse/fdc_eventhouse_stream.ipynb
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "공정이력에서 설비별 런 구간을 뽑는다

판독값을 가동과 유휴로 나누고 이상을 어느 구간에 실을지 정하려면 '이 시각에
이 설비가 무엇을 하고 있었나'를 알아야 한다. 구간은 반열림이라 맞닿은 두
런의 경계 시각이 양쪽에 속하지 않는다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### ✅ Task 2: 판독 스키마에 `run_status` 를 더한다

**Files:**
- Modify: `docs/superpowers/specs/2026-09-04-mes-fdc-time-axis-redesign.md` (§5.2)
- Modify: `src/fdc_schema.py` (`READING_COLUMNS`, `READING_SCHEMA`)
- Modify: `tests/test_fdc_schema.py`

**Interfaces:**
- Produces: `READING_COLUMNS` 와 `READING_SCHEMA` 에 `run_status: string` 이 `step_code` 뒤에 온다. `to_rows` 가 컬럼 순서대로 튜플을 만들므로 두 곳의 순서가 일치해야 한다.

- [x] **Step 1: 스펙 §5.2 를 고친다**

스펙이 `lot_id` 를 추가하라고 적혀 있는 채로 두면 다음 사람이 되돌린다. §5.2 의 해당 문단을 아래로 바꾼다.

```markdown
판독값에 **`run_status`**(`"Run"` | `"Idle"`) 한 컬럼만 더한다. `step_code` 는
이미 있고 설비 속성이라 유휴에도 유효하다.

`lot_id` 는 **넣지 않는다.** `src/fdc_validate.py` 의 첫 검사가 `lot_id` 를
치명으로 금지하고 있으며("무중복 원칙: FDC 는 로트를 모른다"), 그 원칙이 옳다.

1. FDC 가 로트를 들고 있으면 "어느 로트가 이상했나"를 Eventhouse 하나로
   답해 버려서, 시스템 경계를 넘는 시간 조인이라는 이 실습의 목표가 사라진다.
2. MES 앵커는 배포마다 바뀌는데 Eventhouse 는 append-only 다. 이미 쓴
   `lot_id` 는 재배포 순간 거짓이 되지만 `eqp_id` 는 그대로 유효하다.
3. 런/유휴 판정에 필요한 건 설비 상태(SEMI E10)이지 로트가 아니다.

로트는 `build_readings` 내부에서 이상 배치를 계산하는 데만 쓴다.
```

- [x] **Step 2: 실패하는 테스트를 쓴다**

`tests/test_fdc_schema.py` 끝에 붙인다. 이 파일은 이미 `fdc_schema` 의 심볼을 개별 import 하고 있으므로, 파일 상단 import 에 `READING_TABLE`, `TABLE_DDL`, `spark_schema` 가 없으면 더한다.

```python
def test_reading_schema_has_run_status():
    assert ("run_status", "string") in READING_SCHEMA


def test_run_status_follows_step_code():
    names = [n for n, _ in READING_SCHEMA]
    assert names.index("run_status") == names.index("step_code") + 1


def test_reading_columns_match_schema_order():
    """to_rows 가 컬럼 순서로 튜플을 만들므로 둘이 어긋나면 값이 밀린다."""
    assert READING_COLUMNS == tuple(n for n, _ in READING_SCHEMA)


def test_spark_schema_includes_run_status():
    assert "run_status STRING" in spark_schema(READING_TABLE)


def test_create_command_includes_run_status():
    assert "run_status:string" in TABLE_DDL[READING_TABLE]


def test_lot_id_stays_out_of_the_schema():
    """FDC 는 로트를 모른다. 계획서 '설계 결정' 참조."""
    assert "lot_id" not in {n for n, _ in READING_SCHEMA}
```

- [x] **Step 3: 실패를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_fdc_schema.py -q 2>&1 | tail -8
```

Expected: `run_status` 관련 **4건 FAIL**(`has_run_status`, `follows_step_code`, `spark_schema`, `create_command`). `test_reading_columns_match_schema_order` 와 `test_lot_id_stays_out_of_the_schema` 는 지금도 PASS 한다 — 앞의 것은 두 상수가 아직 나란히 틀렸기 때문이고, 뒤의 것은 애초에 `lot_id` 가 없기 때문이다. 둘 다 앞으로 어긋남을 막는 가드다.

- [x] **Step 4: 컬럼을 더한다**

`src/fdc_schema.py` 의 `READING_COLUMNS` 를 아래로 바꾼다.

```python
READING_COLUMNS: tuple[str, ...] = (
    "reading_ts",
    "eqp_id",
    "eqp_type",
    "step_code",
    "run_status",
    "sensor_code",
    "value",
    "unit",
    "status",
)
```

같은 파일의 `READING_SCHEMA` 를 아래로 바꾼다.

```python
READING_SCHEMA: tuple[tuple[str, str], ...] = (
    ("reading_ts", "datetime"),
    ("eqp_id", "string"),
    ("eqp_type", "string"),
    ("step_code", "string"),
    ("run_status", "string"),
    ("sensor_code", "string"),
    ("value", "real"),
    ("unit", "string"),
    ("status", "string"),
)
```

- [x] **Step 5: 통과를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_fdc_schema.py -q 2>&1 | tail -8
```

`build_readings` 가 아직 `run_status` 를 안 내므로 `to_rows` 를 부르는 기존 테스트 **2건**(`test_to_rows_preserves_column_order`, `test_to_rows_keeps_datetime_objects`)이 `KeyError: "행에 없는 컬럼: ['run_status']"` 로 깨진다. **그대로 둔다** — Task 4 에서 생성기가 컬럼을 내면 저절로 풀린다. 이게 스키마와 생성기가 어긋나면 즉시 드러나게 하는 장치다.

Expected: 신규 6건 전부 통과, 위 2건만 실패.

- [x] **Step 6: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/src/fdc_schema.py \
        customizing/fabric/fdc-eventhouse/tests/test_fdc_schema.py \
        docs/superpowers/specs/2026-09-04-mes-fdc-time-axis-redesign.md
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "판독값에 설비 가동 상태 컬럼을 더한다

스펙은 lot_id 를 넣으라고 했지만 fdc_validate 의 첫 검사가 그것을 치명으로
금지하고 있었다. 검사 쪽이 옳다. FDC 가 로트를 들고 있으면 Eventhouse
하나로 답이 나와서 MES 에 물을 이유가 사라지고, 재배포로 앵커가 바뀌면
append-only 인 Eventhouse 에 남은 로트 번호가 거짓이 된다.

런/유휴 판정에 필요한 건 설비 상태이지 로트가 아니므로 run_status 를 넣고
스펙 5.2 를 고쳤다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### ✅ Task 3: 이상 주입을 런 단위로 좁힌다

지금은 설비에 붙은 불량코드를 전부 모아 40분 주기 사인파로 상시 이탈을 만든다. 그래서 "언제 이상해졌나"에 답할 수 없고, 이상 시점이 MES 의 어느 공정이력과도 대응하지 않는다.

**Files:**
- Modify: `src/fdc_anomaly.py`
- Modify: `tests/test_fdc_anomaly.py`

**Interfaces:**
- Consumes: Task 1 의 `Run`
- Produces:
  - `candidate_sensors(defect_code: str | None, eqp_type: str) -> tuple[str, ...]` — 그 불량이 지목하고 그 설비에 실제로 달린 센서들
  - `run_sensor(run: Run, eqp_type: str) -> str | None` — 그중 **이 런에서 실제로 이탈할 센서 하나**
  - `run_excursion(run: Run, sensor_code: str, moment: datetime, profile: EquipmentProfile) -> float` — 정상범위 반폭 단위. 런 밖이거나 선택 안 된 센서면 `0.0`
- 변경: `EXCURSION_MIN` 1.2 → **1.4**, `EXCURSION_MAX` 2.8 → **2.9**
- 제거: `excursion_for`, `EXCURSION_PERIOD_SEC`
- 유지: `hinted_sensors` — `build_notebook.py` 의 `_GATE` 셀이 출력에 쓴다

**왜 센서를 하나만 고르나 (실측 근거).** 기존 `excursion_for` 는 지목된 센서 전부에 이탈을 나눠 실었다(`share`). 그래서 힌트가 2개면 진폭이 절반으로 희석돼 어느 쪽도 경보에 못 닿았다. 프로토타입에서 분산 방식은 불량 런 31건 중 12건만 경보를 냈고, 그 경보의 90%가 폴백 센서(`AMBIENT_*`)에 몰려 **README 가 데모로 지목한 EQP-ETCH01 은 경보가 아예 없었다**. 런마다 센서를 하나만 고르면 희석이 사라진다(`share` 개념 자체가 없어진다). 물리적으로도 이쪽이 옳다 — 실제 공정 이탈은 보통 파라미터 하나가 흐른다.

**왜 진폭 상한이 1.4 인가 (기존 불변식).** `tests/test_fdc_anomaly.py::test_lowest_severity_stays_below_min_alarm_ratio` 가 *"가장 깨끗한 설비는 경보(정상 반폭의 1.5배)에 닿지 말아야 한다"* 를 강제한다. 센서 스펙에서 경보 문턱이 가장 낮은 것이 Etcher `CHAMBER_TEMP` 의 1.67배이므로 1.5 는 안전한 하한이다. `excursion_amplitude` 는 severity 0 인 설비에 정확히 `EXCURSION_MIN` 을 주므로 **`EXCURSION_MIN` 은 반드시 1.5 미만이어야 한다.** 1.5 로 올리면 이 테스트가 깨지고, 동시에 "불량률 0 인 설비는 경보를 안 낸다"는 교육적 대비도 사라진다. 1.4 가 그 제약 안에서 가장 큰 값이다.

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_fdc_anomaly.py` 끝에 붙인다. 이 파일에는 이미 `profiles` 픽스처(module scope)가 있다.

```python
from src.fdc_anomaly import candidate_sensors, run_excursion, run_sensor
from src.fdc_runs import Run

_START = datetime(2026, 9, 3, 1, tzinfo=timezone.utc)
_END = datetime(2026, 9, 3, 2, tzinfo=timezone.utc)


def _run(defect="Particle", eqp="EQP-ETCH01"):
    return Run(eqp_id=eqp, lot_id="LOT0010", step_code="ETCH",
               start=_START, end=_END, defect_code=defect)


def test_candidate_sensors_picks_hinted_sensor_present_on_tool():
    assert "CHAMBER_TEMP" in candidate_sensors("Particle", "Etcher")


def test_candidate_sensors_is_empty_without_defect():
    assert candidate_sensors(None, "Etcher") == ()


def test_candidate_sensors_falls_back_when_hint_is_absent():
    """Scratch 는 Implanter 에 없는 센서만 가리킨다."""
    assert candidate_sensors("Scratch", "Implanter") == ("AMBIENT_TEMP",)


def test_candidate_sensors_unknown_defect_falls_back():
    assert candidate_sensors("No-Such-Defect", "Etcher") == ("AMBIENT_TEMP",)


def test_run_sensor_picks_exactly_one_candidate():
    chosen = run_sensor(_run(), "Etcher")
    assert chosen in candidate_sensors("Particle", "Etcher")


def test_run_sensor_is_none_without_defect():
    assert run_sensor(_run(defect=None), "Etcher") is None


def test_run_sensor_is_deterministic():
    assert run_sensor(_run(), "Etcher") == run_sensor(_run(), "Etcher")


def test_different_runs_can_pick_different_sensors():
    """같은 불량이라도 런마다 흐르는 파라미터가 달라야 소재가 풍부해진다."""
    picks = {
        run_sensor(Run("EQP-ETCH01", f"LOT{n:04d}", "ETCH", _START, _END, "Particle"),
                   "Etcher")
        for n in range(40)
    }
    assert len(picks) > 1


def test_no_excursion_outside_the_run(profiles):
    p = profiles["EQP-ETCH01"]
    sensor = run_sensor(_run(), p.eqp_type)
    assert run_excursion(_run(), sensor, _START - timedelta(minutes=1), p) == 0.0
    assert run_excursion(_run(), sensor, _END, p) == 0.0


def test_no_excursion_without_defect(profiles):
    p = profiles["EQP-ETCH01"]
    mid = _START + timedelta(minutes=30)
    assert run_excursion(_run(defect=None), "CHAMBER_TEMP", mid, p) == 0.0


def test_no_excursion_on_unselected_sensor(profiles):
    p = profiles["EQP-ETCH01"]
    mid = _START + timedelta(minutes=30)
    chosen = run_sensor(_run(), p.eqp_type)
    other = next(s.sensor_code for s in sensors_for(p.eqp_type)
                 if s.sensor_code != chosen)
    assert run_excursion(_run(), other, mid, p) == 0.0


def test_excursion_grows_through_the_run(profiles):
    """공정이 서서히 이탈하다 끝에서 불량으로 잡힌다."""
    p = profiles["EQP-ETCH01"]
    sensor = run_sensor(_run(), p.eqp_type)
    early = abs(run_excursion(_run(), sensor, _START + timedelta(minutes=6), p))
    late = abs(run_excursion(_run(), sensor, _START + timedelta(minutes=54), p))
    assert late > early


def test_excursion_starts_at_zero(profiles):
    p = profiles["EQP-ETCH01"]
    assert run_excursion(_run(), run_sensor(_run(), p.eqp_type), _START, p) == 0.0


def test_excursion_is_deterministic(profiles):
    p = profiles["EQP-ETCH01"]
    mid = _START + timedelta(minutes=30)
    sensor = run_sensor(_run(), p.eqp_type)
    assert run_excursion(_run(), sensor, mid, p) == \
           run_excursion(_run(), sensor, mid, p)


def test_zero_length_run_does_not_divide_by_zero(profiles):
    p = profiles["EQP-ETCH01"]
    degenerate = Run("EQP-ETCH01", "LOT0010", "ETCH", _START, _START, "Particle")
    assert run_excursion(degenerate, "CHAMBER_TEMP", _START, p) == 0.0
```

파일 상단에 `from datetime import datetime, timedelta, timezone` 이 없으면 더한다. `sensors_for` 는 이미 import 돼 있다.

- [x] **Step 2: 실패를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_fdc_anomaly.py -q 2>&1 | tail -5
```

Expected: `ImportError: cannot import name 'run_excursion' from 'src.fdc_anomaly'`

- [x] **Step 3: 기존 `excursion_for` 를 새 두 함수로 바꾼다**

`src/fdc_anomaly.py` 상단 import 에 `datetime` 을 더한다.

```python
from datetime import datetime
```

`EXCURSION_PERIOD_SEC` 상수와 그 주석 두 줄을 지운다.

```python
# 이탈이 최대치에 머무는 시간 비율을 정하는 주기(초). 40분.
EXCURSION_PERIOD_SEC = 2400
```

`excursion_for` 함수를 통째로 아래 세 함수로 바꾼다.

```python
def candidate_sensors(defect_code: str | None, eqp_type: str) -> tuple[str, ...]:
    """이 불량이 지목하는 센서 중 그 설비에 실제로 달린 것.

    Mock MES 는 불량코드를 공정과 무관하게 붙인다. Implanter 에 Scratch 가
    달리면 지목 센서가 하나도 없고, 그때는 모든 유형이 갖는 AMBIENT_TEMP 로
    넘긴다. 클린룸 열관리 실패는 실제로 여러 불량의 공통 원인이다.
    """
    if not defect_code:
        return ()
    available = {s.sensor_code for s in sensors_for(eqp_type)}
    hinted = tuple(
        s for s in DEFECT_SENSOR_HINT.get(defect_code, ()) if s in available
    )
    if hinted:
        return hinted
    chosen = fallback_sensor(eqp_type)
    return (chosen,) if chosen else ()


def run_sensor(run, eqp_type: str) -> str | None:
    """이 런에서 실제로 흐르는 센서 하나. 불량이 없으면 None.

    후보 전부에 이탈을 나눠 실으면 진폭이 희석돼 어느 쪽도 경보에 못 닿는다.
    실제 공정 이탈도 보통 파라미터 하나가 흐르지 여러 개가 동시에 흐르지
    않는다. 런 식별자로 고르므로 같은 설비·같은 불량이라도 런마다 다른
    센서가 걸려 실습 소재가 다양해진다.
    """
    candidates = candidate_sensors(run.defect_code, eqp_type)
    if not candidates:
        return None
    index = seed(run.lot_id, run.step_code, run.eqp_id, run.defect_code)
    return candidates[index % len(candidates)]


def run_excursion(
    run, sensor_code: str, moment: datetime, profile: EquipmentProfile
) -> float:
    """런 구간 안의 이탈량. 정상범위 반폭이 1.0 인 단위.

    런이 진행될수록 커진다(progress 의 제곱). 공정이 서서히 이탈하다 끝에서
    불량으로 잡히는 모습이라 실습자에게 설명하기 쉽고, 런 앞부분이 잠잠해서
    경보가 끊이지 않는 일도 없다.
    """
    if not run.start <= moment < run.end:
        return 0.0
    if sensor_code != run_sensor(run, profile.eqp_type):
        return 0.0
    length = (run.end - run.start).total_seconds()
    if length <= 0:
        return 0.0
    progress = (moment - run.start).total_seconds() / length
    return excursion_sign(run.eqp_id, sensor_code) * excursion_amplitude(profile) * progress**2
```

- [x] **Step 3b: 진폭 상한을 올린다**

센서를 하나만 고르게 되면서 희석(`share`)이 사라졌지만, 그것만으로는 데모가 서지 않았다. 프로토타입 실측에서 1.2~2.8 은 불량 런 31건 중 12건만 경보를 냈고 EQP-ETCH01 은 하나도 못 냈다.

```python
# 이탈 진폭 범위. 정상범위 반폭이 1.0 인 단위.
EXCURSION_MIN = 1.4
EXCURSION_MAX = 2.9
```

기존 값은 `EXCURSION_MIN = 1.2` / `EXCURSION_MAX = 2.8` 이다. **1.5 이상으로 올리면 안 된다** — `test_lowest_severity_stays_below_min_alarm_ratio` 가 깨진다(위 Interfaces 절 참고).

이 상수를 참조하는 기존 테스트(`test_excursion_within_declared_bounds`, `test_excursion_min_below_max`)는 값이 아니라 **심볼**을 보므로 그대로 통과한다.

- [x] **Step 4: 모듈 독스트링을 고친다**

파일 맨 위 독스트링의 "- 어디에: 불량코드가 지목하는 센서에만 이탈이 실린다" 아래에 한 줄을 더한다.

```
- 언제: 불량이 난 그 런의 [in_time, out_time) 구간에만 실린다
```

- [x] **Step 5: `excursion_for` 를 쓰던 기존 테스트 3건을 이식한다**

`excursion_for` 가 사라졌으므로 import 와 호출부를 고쳐야 한다. 지우지 말고 옮긴다 — 세 테스트 모두 새 모델에서도 지켜야 할 불변식이다.

파일 상단 import 에서 `excursion_for,` 를 지우고 `candidate_sensors,` / `run_excursion,` / `run_sensor,` 를 넣는다. 파일 끝에 헬퍼를 더한다.

```python
_ONE_SEC = timedelta(seconds=1)


def _demo_run(eqp_id):
    """이탈 상한을 재기 위한 1시간짜리 불량 런."""
    start = datetime(2026, 9, 3, 1, tzinfo=timezone.utc)
    return Run(eqp_id, "LOT0001", "STEP", start, start + timedelta(hours=1), "Particle")
```

세 테스트를 아래로 바꾼다.

```python
def test_unselected_sensor_has_zero_excursion(profiles):
    profile = profiles["EQP-CMP01"]
    run = _demo_run(profile.eqp_id)
    chosen = run_sensor(run, profile.eqp_type)
    for sensor in sensors_for(profile.eqp_type):
        if sensor.sensor_code != chosen:
            assert run_excursion(run, sensor.sensor_code, run.end - _ONE_SEC, profile) == 0.0


def test_excursion_within_declared_bounds(profiles):
    for profile in profiles.values():
        run = _demo_run(profile.eqp_id)
        chosen = run_sensor(run, profile.eqp_type)
        value = abs(run_excursion(run, chosen, run.end - _ONE_SEC, profile))
        assert 0.0 < value <= EXCURSION_MAX


def test_lowest_severity_stays_below_min_alarm_ratio(profiles):
    """가장 깨끗한 설비는 경보(정상 반폭의 1.5배)에 닿지 말아야 한다."""
    profile = profiles["EQP-IMPL01"]
    run = _demo_run(profile.eqp_id)
    chosen = run_sensor(run, profile.eqp_type)
    assert abs(run_excursion(run, chosen, run.end - _ONE_SEC, profile)) < 1.5
```

`run.end - _ONE_SEC` 를 쓰는 이유: 이탈은 `progress**2` 로 커지므로 상한은 런의 **끝 직전**에서 나온다. `run.end` 자체는 반열림 구간 밖이라 `0.0` 이다.

`hinted_sensors` / `excursion_amplitude` / `excursion_sign` 테스트는 그대로 둔다.

- [x] **Step 6: 통과를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_fdc_anomaly.py -q 2>&1 | tail -10
```

Expected: 전부 통과.

- [x] **Step 7: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/src/fdc_anomaly.py \
        customizing/fabric/fdc-eventhouse/tests/test_fdc_anomaly.py
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "이상을 설비 상시에서 런 단위로 좁힌다

설비에 붙은 불량코드를 전부 모아 40분 주기로 흘리면 '언제 이상해졌나'에
답할 수 없고, 이상 시점이 MES 의 어느 공정이력과도 대응하지 않는다.
불량이 난 그 런 구간에만 싣고, 런이 진행될수록 커지게 한다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### ✅ Task 4: 단일 타임라인으로 가동/유휴를 나눠 생성한다

**Files:**
- Modify: `src/fdc_sensors.py` (`idle_sensors`)
- Modify: `src/fdc_generator.py`
- Modify: `tests/conftest.py` (`busy_window`)
- Modify: `tests/test_fdc_generator.py`
- Modify: `tests/test_fdc_schema.py` (`readings` 픽스처)

**Interfaces:**
- Consumes: Task 1 `runs_by_equipment` / `run_at`, Task 3 `run_excursion`
- Produces:
  - `IDLE_INTERVAL_SEC = 300` (`fdc_generator`)
  - `RUNNING = "Run"`, `IDLE = "Idle"` (`fdc_generator`)
  - `idle_sensors() -> tuple[SensorDef, ...]` (`fdc_sensors`)
  - `build_readings(facts, start, end) -> list[dict]` — 각 행에 `run_status`
  - `busy_window` 픽스처 → `(start, end)` — MES 구간 안의 24시간

- [x] **Step 1: 테스트 창을 MES 구간 안으로 옮긴다**

`T0 = 2026-09-04T12:00Z` 는 이제 MES 구간(약 09-01 07:00 ~ 09-04 00:00) 밖이라 모든 행이 유휴가 된다. 다만 `grid_timestamps` 와 `to_iso` 테스트는 순수 함수라 T0 에 의존하지 않으므로 **그대로 둔다.** `build_readings` 를 부르는 곳만 옮긴다.

`tests/conftest.py` 를 아래로 바꾼다.

```python
import json
from datetime import timedelta
from pathlib import Path

import pytest

from src.fdc_generator import align_to_grid
from src.fdc_runs import span
from src.mes_probe import MesFacts

FIXTURE = Path(__file__).parent / "fixtures" / "mes_facts.json"


@pytest.fixture(scope="session")
def facts() -> MesFacts:
    """실 MES에서 뜬 고정 스냅샷. 오프라인 테스트 전체가 이 위에서 돈다."""
    with FIXTURE.open(encoding="utf-8") as fh:
        return MesFacts.from_dict(json.load(fh))


@pytest.fixture(scope="session")
def busy_window(facts):
    """공정이력이 실제로 있는 24시간.

    벽시계 상수를 쓰면 픽스처를 다시 뜰 때마다 창이 구간 밖으로 나간다.
    build_readings 를 부르는 테스트는 전부 이 창을 쓴다.
    """
    lo, hi = span(facts)
    start = align_to_grid(lo)
    return start, min(start + timedelta(hours=24), hi)
```

`tests/test_fdc_generator.py` 에서 `build_readings` 를 부르는 곳을 옮긴다.

`day_rows` 픽스처(29-32행 부근):

```python
@pytest.fixture(scope="module")
def day_rows(facts, busy_window):
    """공정이력이 있는 24시간. 이 규모에서만 드러나는 성질을 검사한다."""
    return build_readings(facts, *busy_window)
```

백필/라이브 일치 테스트(98-99행 부근)에서 `T0` 를 창 시작으로 바꾼다.

```python
    base = busy_window[0]
    live = build_readings(facts, base + timedelta(minutes=3), base + timedelta(minutes=6))
    backfill = build_readings(facts, base - timedelta(hours=2), base + timedelta(minutes=6))
```

그 테스트 함수 시그니처에 `busy_window` 를 더한다.

```python
def test_backfill_and_live_agree_on_overlap(facts, busy_window):
```

`tests/test_fdc_generator.py::test_row_count_matches_grid`(108행 부근)는 **다시 써야 한다.** 지금은 "설비 전부 × 센서 전부 × 격자 전부" 를 가정하는데, 가동/유휴가 갈리면 그 곱이 성립하지 않는다.

```python
def test_running_row_count_matches_grid(facts):
    """가동 중인 설비 하나는 격자 한 칸마다 그 유형의 센서를 전부 낸다."""
    runs = runs_by_equipment(facts)
    busiest = max(runs, key=lambda k: len(runs[k]))
    run = runs[busiest][0]
    start = align_to_grid(run.start)
    rows = [r for r in build_readings(facts, start, start + timedelta(minutes=3))
            if r["eqp_id"] == busiest and r["run_status"] == RUNNING]
    eqp_type = next(e["eqp_type"] for e in facts.equipment if e["eqp_id"] == busiest)
    assert len(rows) == len(sensors_for(eqp_type)) * 6 == 36
```

`align_to_grid(run.start)` 로 시작하면 그 3분이 통째로 런 안에 있음이 보장된다(최단 런이 3분보다 길다 — Task 0 에서 확인한다).

`tests/test_fdc_generator.py::test_rows_have_expected_columns` 의 기대 집합에 `run_status` 를 더한다.

```python
def test_rows_have_expected_columns(day_rows):
    assert set(day_rows[0]) == {
        "reading_ts", "eqp_id", "eqp_type", "step_code", "run_status",
        "sensor_code", "value", "unit", "status",
    }
```

`tests/test_fdc_validate.py` 에서도 세 곳의 시그니처를 고친다.

```python
def readings(facts, busy_window):
    return build_readings(facts, *busy_window)


def checks(readings, facts, busy_window):
    return validate(readings, facts, watermark=busy_window[0])


def test_zero_alarms_is_warning(facts, busy_window):
    short = build_readings(facts, busy_window[0], busy_window[0] + timedelta(seconds=30))
```

`tests/test_fdc_schema.py` 의 `readings` 픽스처(25-27행)를 바꾼다.

```python
@pytest.fixture(scope="module")
def readings(facts, busy_window):
    start = busy_window[0]
    return build_readings(facts, start, start + timedelta(minutes=3))
```

- [x] **Step 2: 실패하는 테스트를 쓴다**

`tests/test_fdc_generator.py` 끝에 붙인다.

```python
from src.fdc_generator import IDLE, IDLE_INTERVAL_SEC, RUNNING
from src.fdc_runs import runs_by_equipment, span
from src.fdc_sensors import COMMON_SENSORS


def _busiest_run_window(facts):
    """런이 가장 많은 설비의 첫 런 앞뒤로 10분씩."""
    runs = runs_by_equipment(facts)
    busiest = max(runs, key=lambda k: len(runs[k]))
    first = runs[busiest][0]
    return first.start - timedelta(minutes=10), first.end + timedelta(minutes=10)


def test_every_row_has_run_status(facts, busy_window):
    rows = build_readings(facts, *busy_window)
    assert rows
    assert {r["run_status"] for r in rows} <= {RUNNING, IDLE}


def test_both_states_appear(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    assert any(r["run_status"] == RUNNING for r in rows)
    assert any(r["run_status"] == IDLE for r in rows)


def test_idle_rows_use_only_common_sensors(facts):
    common = {s.sensor_code for s in COMMON_SENSORS}
    rows = build_readings(facts, *_busiest_run_window(facts))
    for row in rows:
        if row["run_status"] == IDLE:
            assert row["sensor_code"] in common


def test_idle_rows_sit_on_the_five_minute_grid(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    for row in rows:
        if row["run_status"] == IDLE:
            assert int(row["reading_ts"].timestamp()) % IDLE_INTERVAL_SEC == 0


def test_running_rows_use_the_full_sensor_set(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    seen = {}
    for row in rows:
        if row["run_status"] == RUNNING:
            seen.setdefault(row["eqp_id"], set()).add(row["sensor_code"])
    assert seen
    for eqp_id, codes in seen.items():
        eqp_type = next(
            e["eqp_type"] for e in facts.equipment if e["eqp_id"] == eqp_id
        )
        assert codes == {s.sensor_code for s in sensors_for(eqp_type)}


def test_idle_is_far_cheaper_than_running(facts, busy_window):
    """유휴는 5분에 2종, 가동은 30초에 6종이다."""
    rows = build_readings(facts, *busy_window)
    running = sum(1 for r in rows if r["run_status"] == RUNNING)
    assert 0 < len(rows) - running < running


def test_no_duplicate_readings(facts):
    rows = build_readings(facts, *_busiest_run_window(facts))
    keys = [(r["eqp_id"], r["sensor_code"], r["reading_ts"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_backfill_and_live_agree_on_run_status(facts):
    """구간을 반으로 갈라 만들어도 통째로 만든 것과 같아야 한다."""
    lo, hi = _busiest_run_window(facts)
    mid = lo + (hi - lo) / 2
    key = lambda r: (r["eqp_id"], r["sensor_code"], r["reading_ts"])
    whole = {key(r): r["run_status"] for r in build_readings(facts, lo, hi)}
    halves = {key(r): r["run_status"]
              for r in build_readings(facts, lo, mid) + build_readings(facts, mid, hi)}
    assert whole == halves


def test_rows_after_the_mes_span_are_all_idle(facts):
    _lo, hi = span(facts)
    rows = build_readings(facts, hi, hi + timedelta(hours=1))
    assert rows
    assert all(r["run_status"] == IDLE for r in rows)


def test_lot_id_never_leaks_into_rows(facts, busy_window):
    """FDC 는 로트를 모른다. 계획서 '설계 결정' 참조."""
    rows = build_readings(facts, *busy_window)
    assert all("lot_id" not in r for r in rows)
```

파일 상단 import 에 `span` 과 `sensors_for` 가 없으면 더한다.

- [x] **Step 3: 실패를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_fdc_generator.py -q 2>&1 | tail -5
```

Expected: `ImportError: cannot import name 'IDLE' from 'src.fdc_generator'`

- [x] **Step 4: 유휴 센서를 공개한다**

`src/fdc_sensors.py` 의 `sensors_for` 아래에 더한다. `COMMON_SENSORS` 는 이미 공개 상수라 그대로 쓴다.

```python
def idle_sensors() -> tuple[SensorDef, ...]:
    """설비가 멈춰 있을 때도 의미가 있는 센서.

    챔버 압력이나 RF 파워는 멈춘 설비에서 측정 자체가 무의미하다. 그렇다고
    0 을 내보내면 RF_POWER 의 alarm_min 이 1400 이라 유휴 내내 경보가 된다.
    클린룸 주변 온도·습도는 설비 가동과 무관하게 계속 측정된다.
    """
    return COMMON_SENSORS
```

- [x] **Step 5: `fdc_generator.py` 를 고친다**

import 블록을 아래로 바꾼다.

```python
from src.fdc_anomaly import (
    EquipmentProfile,
    build_profiles,
    run_excursion,
    seed,
)
from src.fdc_runs import Run, run_at, runs_by_equipment
from src.fdc_sensors import (
    SAMPLE_INTERVAL_SEC,
    SensorDef,
    idle_sensors,
    sensors_for,
)

NORMAL = "Normal"
WARNING = "Warning"
ALARM = "Alarm"

RUNNING = "Run"
IDLE = "Idle"

# 유휴 샘플링 간격. 30초의 배수여야 한다. align_to_grid 가 epoch 기준이라
# 배수이기만 하면 유휴 격자가 런 격자의 부분집합이 되어 중복이 없다.
IDLE_INTERVAL_SEC = 300
```

`excursion_offset` 을 아래로 바꾼다.

```python
def excursion_offset(
    sensor: SensorDef, run: Run | None, moment: datetime, profile: EquipmentProfile
) -> float:
    """이상 구간의 이탈량. 런 밖이면 0 이다.

    진폭도 대상 센서도 시점도 전부 MES 불량 실적에서 온다. 그래야
    Eventhouse 에서 찾은 이상이 MES 의 실제 공정이력과 맞아떨어진다.
    """
    if run is None:
        return 0.0
    scale = run_excursion(run, sensor.sensor_code, moment, profile)
    if scale == 0.0:
        return 0.0
    half = (sensor.normal_max - sensor.normal_min) / 2
    return scale * half
```

`reading_value` 를 아래로 바꾼다.

```python
def reading_value(
    sensor: SensorDef,
    eqp_id: str,
    moment: datetime,
    profile: EquipmentProfile,
    run: Run | None = None,
) -> float:
    value = (
        sensor.base
        + diurnal(sensor, eqp_id, moment)
        + noise(sensor, eqp_id, moment)
        + excursion_offset(sensor, run, moment, profile)
    )
    return round(value, 4)
```

`build_readings` 를 통째로 아래로 바꾼다.

```python
def build_readings(facts, start: datetime, end: datetime) -> list[dict]:
    """구간 안의 모든 판독값.

    30초 격자를 하나만 깔고 각 시각을 런/유휴로 나눈다. 격자를 둘 만들지
    않는 이유는 한 시각이 양쪽에 속해 중복 행이 생기는 것을 막기 위해서다.
    IDLE_INTERVAL_SEC 가 30초의 배수이고 격자가 epoch 기준이라, 유휴 격자는
    런 격자의 부분집합이다.

    런 중에는 센서 6종을 30초마다, 유휴에는 공통 2종을 5분마다 낸다. 멈춘
    설비의 챔버 압력을 30초마다 적는 FDC 는 없다.

    로트 번호는 이상 배치 계산에만 쓰고 행에는 넣지 않는다. 어느 로트였는지는
    MES 에 물어야 한다.
    """
    profiles = build_profiles(facts)
    all_runs = runs_by_equipment(facts)
    moments = grid_timestamps(start, end)
    idle = idle_sensors()
    rows: list[dict] = []

    for profile in profiles.values():
        runs = all_runs.get(profile.eqp_id, [])
        running_sensors = sensors_for(profile.eqp_type)
        for moment in moments:
            run = run_at(runs, moment)
            if run is not None:
                active, run_status = running_sensors, RUNNING
            elif int(moment.timestamp()) % IDLE_INTERVAL_SEC == 0:
                active, run_status = idle, IDLE
            else:
                continue
            for sensor in active:
                value = reading_value(sensor, profile.eqp_id, moment, profile, run)
                rows.append(
                    {
                        "reading_ts": moment,
                        "eqp_id": profile.eqp_id,
                        "eqp_type": profile.eqp_type,
                        "step_code": profile.step_code,
                        "run_status": run_status,
                        "sensor_code": sensor.sensor_code,
                        "value": value,
                        "unit": sensor.unit,
                        "status": classify(sensor, value),
                    }
                )
    return rows
```

- [x] **Step 6: 전체 테스트를 돌린다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests -q 2>&1 | tail -15
```

기존 테스트 중 "모든 설비 × 6센서 × 전체 격자" 를 전제한 행 수 계산이 있으면 지금 깨진다. 새 모델에 맞게 고친다. `reading_value` 를 4인자로 부르던 테스트는 `run` 이 기본값 `None` 이라 그대로 동작한다.

- [x] **Step 7: 볼륨과 경보 비율을 눈으로 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 - <<'PY'
import json
from src.fdc_generator import build_readings
from src.fdc_runs import span
from src.mes_probe import MesFacts

facts = MesFacts.from_dict(json.load(open("tests/fixtures/mes_facts.json")))
lo, hi = span(facts)
rows = build_readings(facts, lo, hi)
run = sum(1 for r in rows if r["run_status"] == "Run")
st = {}
for r in rows:
    st[r["status"]] = st.get(r["status"], 0) + 1
print(f"구간 {(hi - lo).total_seconds() / 3600:.1f}h")
print(f"총 {len(rows):,}행 (가동 {run:,} · 유휴 {len(rows) - run:,})")
for k in ("Normal", "Warning", "Alarm"):
    n = st.get(k, 0)
    print(f"  {k:8s} {n:8,d}  {n / len(rows):6.2%}")
PY
```

Expected — 프로토타입 실측과 같아야 한다:

```
구간 64.8h
총 102,552행 (가동 93,210 · 유휴 9,342)
  Normal    100,990  98.48%
  Warning     1,305   1.27%
  Alarm         257   0.25%
```

MES 픽스처가 이 계획의 프로토타입과 정확히 같으면 숫자도 정확히 같다. 다르면 구현이 아니라 **픽스처가 다른 것이므로** Task 0 으로 돌아가 시드가 맞는지 본다.

`Alarm` 이 `MAX_ALARM_RATIO`(5%) 를 넘으면 `EXCURSION_MAX` 를 낮추고, 0 이면 올린다. 단 **`EXCURSION_MIN` 은 1.5 미만을 유지해야 한다**(Task 3 참고). **테스트 기대값을 고쳐 맞추지 마라.**

- [x] **Step 7b: 신호가 실습에 쓸 만한지 확인한다**

행 수만으로는 부족하다. 경보가 불량 런에만 뜨는지, 유휴에 안 뜨는지를 본다. 이 셋이 README 데모의 전제다.

```bash
cd customizing/fabric/fdc-eventhouse && python3 - <<'PY'
import json
from src.fdc_generator import build_readings
from src.fdc_runs import span, runs_by_equipment
from src.mes_probe import MesFacts

facts = MesFacts.from_dict(json.load(open("tests/fixtures/mes_facts.json")))
lo, hi = span(facts)
rows = build_readings(facts, lo, hi)
runs = runs_by_equipment(facts)

def owner(eqp, ts):
    for r in runs.get(eqp, ()):
        if r.start <= ts < r.end:
            return r
    return None

alarmed, orphan = set(), 0
for row in rows:
    if row["status"] != "Alarm":
        continue
    r = owner(row["eqp_id"], row["reading_ts"])
    if r is None:
        orphan += 1
    else:
        alarmed.add((r.eqp_id, r.lot_id, r.step_code))

everything = [r for v in runs.values() for r in v]
defect = {(r.eqp_id, r.lot_id, r.step_code) for r in everything if r.defect_code}
clean = {(r.eqp_id, r.lot_id, r.step_code) for r in everything if not r.defect_code}
print(f"런 {len(everything)} (불량 {len(defect)} · 정상 {len(clean)})")
print(f"경보 난 런 {len(alarmed)}")
print(f"  불량 런 검출 {len(alarmed & defect)}/{len(defect)}")
print(f"  정상 런 오탐 {len(alarmed & clean)}   <- 0 이어야 한다")
print(f"  유휴 중 경보 {orphan}행               <- 0 이어야 한다")
PY
```

Expected:

```
런 84 (불량 31 · 정상 53)
경보 난 런 22
  불량 런 검출 22/31
  정상 런 오탐 0   <- 0 이어야 한다
  유휴 중 경보 0행               <- 0 이어야 한다
```

**오탐 0 이 중요한 이유:** README 데모가 "경보 시각을 MES 에 물으면 불량이 나온다"로 성립하려면 `Alarm` 이 거짓말을 하면 안 된다. `Warning` 은 자연 잡음으로 정상 런에도 3건 뜬다 — 이건 현실적이라 그대로 두고, README 쿼리는 `status == "Alarm"` 만 쓴다.

**검출률 71% 가 낮지 않은 이유:** FDC 가 불량을 전부 잡으면 MES 와 QMS 에 물을 이유가 없어진다. 9건은 센서에 안 잡히고 검사에서만 드러난다 — 세 시스템을 다 봐야 하는 실습 목적에 맞다.

- [x] **Step 8: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/src/ customizing/fabric/fdc-eventhouse/tests/
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "가동과 유휴를 나눠 판독값을 만든다

모든 설비가 24시간 내내 6종을 30초마다 내보내는 건 FDC 가 아니라 난수
발생기였다. 이제 MES 가 언제 무엇이 돌았는지 알려주므로, 가동 중에만 공정
센서를 내고 멈춰 있을 땐 주변 온도·습도만 5분 간격으로 남긴다.

멈춘 설비에 RF 파워 0 을 내보내면 alarm_min 이 1400 이라 유휴 내내 경보가
된다. 유휴에 공정 센서를 내지 않는 이유다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### ✅ Task 5: 검증기가 `run_status` 를 본다

**Files:**
- Modify: `src/fdc_validate.py`
- Modify: `tests/test_fdc_validate.py`

**Interfaces:**
- Consumes: Task 4 의 `RUNNING` / `IDLE`
- Produces: `validate()` 가 돌려주는 `Check` 목록에 `run_status` 검사가 하나 추가된다. 번호는 기존 마지막 검사 다음이다.

- [x] **Step 1: 실패하는 테스트를 쓴다**

`readings` / `checks` 픽스처는 Task 4 Step 1 에서 이미 `busy_window` 기반으로 옮겼다. 여기서는 테스트만 더한다. `tests/test_fdc_validate.py` 끝에 붙인다.

```python
def test_unknown_run_status_is_fatal(readings, facts):
    rows = [dict(r) for r in readings]
    rows[0]["run_status"] = "Maintenance"
    assert any(c.fatal and not c.passed for c in validate(rows, facts))


def test_missing_run_status_is_fatal(readings, facts):
    rows = [dict(r) for r in readings]
    del rows[0]["run_status"]
    assert any(c.fatal and not c.passed for c in validate(rows, facts))


def test_idle_row_with_process_sensor_is_fatal(readings, facts):
    """유휴에 공정 센서가 섞이면 0 값이 그대로 경보가 된다."""
    rows = [dict(r) for r in readings]
    victim = next(r for r in rows if r["run_status"] == "Idle")
    victim["sensor_code"] = "RF_POWER"
    assert any(c.fatal and not c.passed for c in validate(rows, facts))


def test_clean_readings_have_no_fatal_check(readings, facts):
    assert not any(c.fatal and not c.passed for c in validate(readings, facts))


def test_lot_columns_still_forbidden(readings, facts):
    """무중복 원칙은 그대로다. run_status 는 설비 상태이지 로트가 아니다."""
    rows = [dict(r) for r in readings]
    rows[0]["lot_id"] = "LOT0010"
    assert any(c.fatal and not c.passed for c in validate(rows, facts))
```

- [x] **Step 2: 실패를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_fdc_validate.py -q 2>&1 | tail -8
```

Expected: `run_status` 검사 3건 FAIL. `test_clean_readings_have_no_fatal_check` 와 `test_lot_columns_still_forbidden` 은 PASS.

- [x] **Step 3: 검사를 더한다**

`src/fdc_validate.py` 상단 import 에 상태 상수를 더한다.

```python
from src.fdc_generator import ALARM, IDLE, NORMAL, RUNNING, classify
```

`validate()` 안의 마지막 `checks.append(...)` 뒤, `return checks` 앞에 아래를 넣는다. `_next` 는 쓰지 말고 그 자리의 실제 번호를 넣는다. 기존 검사가 N개면 번호는 N+1 이다.

```python
    bad_status = sorted({r.get("run_status") for r in readings} - {RUNNING, IDLE})
    idle_common = {s.sensor_code for s in idle_sensors()}
    idle_leak = sorted(
        {
            r["sensor_code"]
            for r in readings
            if r.get("run_status") == IDLE and r["sensor_code"] not in idle_common
        }
    )
    checks.append(
        Check(
            <다음 번호>,
            "run_status 가 Run/Idle 뿐이고 유휴에 공정 센서가 없다",
            True,
            not bad_status and not idle_leak,
            (
                f"알 수 없는 상태: {bad_status}" if bad_status
                else f"유휴에 섞인 공정 센서: {idle_leak}" if idle_leak
                else f"가동 {sum(1 for r in readings if r['run_status'] == RUNNING):,}행"
            ),
        )
    )
```

`<다음 번호>` 는 실제 값으로 바꾼다. 확인 방법:

```bash
cd customizing/fabric/fdc-eventhouse && grep -c "        Check(" src/fdc_validate.py
```

`idle_sensors` import 를 더한다.

```python
from src.fdc_sensors import (
    SAMPLE_INTERVAL_SEC,
    build_sensor_spec_rows,
    idle_sensors,
    sensor_by_code,
)
```

> `r.get("run_status")` 로 읽는 이유: 컬럼이 아예 없는 행이면 `None` 이 되어 `bad_status` 에 걸린다. `r["run_status"]` 로 읽으면 `KeyError` 로 죽어서 검증 보고서가 안 나온다.

- [x] **Step 3b: 검사 개수를 세던 기존 테스트 4건을 고친다**

검사가 8개에서 9개가 되므로 개수를 박아 둔 테스트가 깨진다. **구현이 아니라 기대값이 낡은 것이므로 기대값을 고치는 게 맞다.**

```python
def test_all_nine_checks_run(checks):          # 이름도 바꾼다
    assert [c.number for c in checks] == list(range(1, 10))


def test_fatal_flags_match_spec(checks):
    ...
    assert fatal == {1, 2, 3, 4, 5, 9}
```

`test_unknown_sensor_does_not_crash_status_check` 와 `test_validate_handles_empty_readings` 의 `assert len(result) == 8` 을 `== 9` 로 바꾼다. 두 곳이다.

- [x] **Step 4: 샘플링 간격 검사를 확인한다**

기존 검사 중 판독 간격이 `SAMPLE_INTERVAL_SEC` 라고 단정하는 것이 있으면 유휴 행 때문에 깨진다. 그런 검사는 **가동 행에만** 적용하도록 좁힌다.

```bash
cd customizing/fabric/fdc-eventhouse && grep -n "SAMPLE_INTERVAL_SEC" src/fdc_validate.py
```

걸리는 곳이 있으면 대상 목록을 `[r for r in readings if r.get("run_status") == RUNNING]` 으로 바꾸고, 검사 이름에 "(가동 구간)" 을 덧붙인다.

- [x] **Step 5: 통과를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests -q 2>&1 | tail -8
```

Expected: 전부 통과.

- [x] **Step 6: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/src/fdc_validate.py \
        customizing/fabric/fdc-eventhouse/tests/test_fdc_validate.py
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "적재 전에 가동 상태를 검증한다

Eventhouse 는 append-only 라 잘못 쓴 행을 지우려면 익스텐트째 지워야 하고
같은 익스텐트의 정상 행까지 날아간다. 유휴에 공정 센서가 섞이면 유휴 내내
경보가 되므로 쓰기 전에 막는다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### ✅ Task 6: 노트북 생성 구간을 MES 에 맞춘다

24시간 캡이 남아 있으면 첫 백필이 최근 24시간만 남기고 워터마크가 `NOW` 로 간다. 건너뛴 구간은 **다시 채워지지 않는다.** MES 공정이력 대부분의 시간대가 영구히 비어서 교차 질의가 성립하지 않는다.

**Files:**
- Modify: `build_notebook.py` (`_INTRO`, `_PARAMETERS`, `_WATERMARK`)
- Modify: `tests/test_build_notebook.py`

**Interfaces:**
- Produces: 첫 실행 범위가 `[MES min(in_time), NOW]`. 캡 없음.

- [x] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_build_notebook.py` 끝에 붙인다. 파일 상단에 `import build_notebook as bn` 이 없으면 더한다(모듈 자체를 참조해야 상수를 볼 수 있다).

```python
def test_module_order_includes_fdc_runs():
    """등록하지 않으면 노트북에서 span 이 정의되지 않는다."""
    assert "fdc_runs" in bn.MODULE_ORDER


def test_fdc_runs_comes_before_generator():
    order = list(bn.MODULE_ORDER)
    assert order.index("fdc_runs") < order.index("fdc_generator")


def test_no_wall_clock_backfill_constant():
    assert "BACKFILL_HOURS" not in bn._PARAMETERS
    assert "BACKFILL_HOURS" not in bn._WATERMARK


def test_no_span_cap():
    assert "MAX_SPAN_HOURS" not in bn._PARAMETERS
    assert "MAX_SPAN_HOURS" not in bn._WATERMARK


def test_backfill_starts_from_the_mes_span():
    assert "span(FACTS)" in bn._WATERMARK


def test_notebook_defines_span_before_it_is_used(notebook):
    """인라인된 소스에서 span 정의가 호출보다 앞에 있어야 한다."""
    source = "\n".join(c.source for c in notebook.cells if c.cell_type == "code")
    assert source.index("def span(") < source.index("span(FACTS)")
```

- [x] **Step 2: 실패를 확인한다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 -m pytest tests/test_build_notebook.py -q 2>&1 | tail -8
```

Expected: 캡 관련 3건과 `span(FACTS)` 관련 2건 FAIL. `MODULE_ORDER` 2건은 Task 1 에서 이미 통과.

- [x] **Step 3: 파라미터에서 벽시계 값을 뺀다**

`build_notebook.py` 의 `_PARAMETERS` 에서 아래 두 블록(주석 포함)을 지운다.

```python
# 첫 실행에서 거슬러 올라가 채울 시간. 차트에 하루 주기가 보이려면 24시간이 필요합니다.
BACKFILL_HOURS = 24
```

```python
# 한 번에 만들 수 있는 최대 구간. 잡이 며칠 멈췄다 살아날 때 수백만 행을
# 한 번에 쓰려다 세션이 죽는 것을 막습니다.
MAX_SPAN_HOURS = 24
```

- [x] **Step 4: 범위를 MES 에서 가져온다**

`_WATERMARK` 의 아래 부분(`if WATERMARK is None:` 부터 마지막 `print` 까지)을 바꾼다.

기존:

```python
if WATERMARK is None:
    MODE = "backfill"
    START = NOW - timedelta(hours=BACKFILL_HOURS)
else:
    MODE = "live"
    START = WATERMARK

# 잡이 오래 멈췄다 살아나면 구간이 며칠로 벌어집니다. 한 번에 다 쓰려다
# 세션이 죽는 대신 최근 구간만 채우고, 다음 실행이 이어받게 합니다.
_span = NOW - START
if _span > timedelta(hours=MAX_SPAN_HOURS):
    print(f"구간이 {_span} 로 너무 깁니다. 최근 {MAX_SPAN_HOURS}시간만 채웁니다.")
    START = NOW - timedelta(hours=MAX_SPAN_HOURS)

print(f"모드={MODE} · watermark={WATERMARK} · 생성 구간 {START} ~ {NOW}")
```

새로:

```python
# 첫 실행은 MES 공정이력이 시작하는 시각부터 채웁니다. 벽시계 기준으로 최근
# 몇 시간만 채우면 MES 가 아는 구간과 겹치지 않아서, 센서에서 찾은 이상을
# 공정이력에서 확인할 수 없습니다. 이 노트북의 존재 이유가 사라집니다.
MES_FROM, MES_TO = span(FACTS)

if WATERMARK is None:
    MODE = "backfill"
    START = MES_FROM
else:
    MODE = "live"
    START = WATERMARK

# 구간을 잘라내지 않습니다. 잘라내면 watermark 가 잘린 지점이 아니라 NOW 로
# 가버려서 건너뛴 구간을 다시는 채우지 않습니다. 유휴 구간은 5분 간격 2종이라
# 며칠이 밀려도 수만 행에 그칩니다.
print(f"모드={MODE} · watermark={WATERMARK}")
print(f"MES 공정이력 {MES_FROM} ~ {MES_TO}")
print(f"생성 구간 {START} ~ {NOW}")
if NOW > MES_TO:
    _stale = NOW - MES_TO
    print(f"  MES 배포 후 {_stale.days}일 {_stale.seconds // 3600}시간 지났습니다."
          f" 그 이후 구간은 전부 유휴로 채웁니다.")
```

`timedelta` 는 셀 첫 줄 import 에 이미 있으므로 그대로 둔다. `span` 은 `fdc_runs` 가 인라인되면서 정의되므로 별도 import 가 필요 없다.

- [x] **Step 4b: 캡을 강제하던 기존 테스트를 뒤집는다**

`tests/test_build_notebook.py::test_watermark_cell_caps_span` 이 캡의 존재를 강제한다. 캡을 없앴으니 이 테스트의 전제가 틀렸다. **지우지 말고 반대 불변식으로 바꾼다** — 다음 사람이 "구간이 길어지면 위험하다"며 캡을 되살리는 것을 막는 장치가 필요하다.

```python
def test_watermark_cell_does_not_cap_span(notebook):
    """구간을 잘라내면 워터마크가 NOW 로 가서 건너뛴 구간이 영영 안 채워진다."""
    cell = next(c.source for c in notebook.cells if "WATERMARK is None" in c.source)
    assert "MAX_SPAN_HOURS" not in cell
    assert "MES_FROM" in cell
```

- [x] **Step 5: 설명 문구를 고친다**

`_INTRO` 에서 백필을 설명하는 문장을 찾는다.

```bash
cd customizing/fabric/fdc-eventhouse && grep -n "24시간\|백필" build_notebook.py
```

"첫 실행은 지난 24시간을 백필합니다" 취지의 문장을 아래로 바꾼다.

```
첫 실행은 MES 공정이력이 걸쳐 있는 구간 전체를 백필합니다. 약 65시간이고
10만 행 안팎입니다. 이후 실행은 이미 적재된 마지막 시각(watermark)부터
지금까지만 채웁니다. 값이 (설비, 센서, 타임스탬프)만으로 정해지므로 몇 번을
다시 돌려도 같은 시각에는 같은 값이 들어갑니다.

설비가 돌고 있을 때만 공정 센서 6종을 30초 간격으로 내보냅니다. 멈춰 있는
동안에는 주변 온도·습도 2종만 5분 간격으로 남습니다. `run_status` 컬럼으로
구분할 수 있습니다.
```

- [x] **Step 6: 노트북을 다시 만들고 전체를 돌린다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 build_notebook.py && python3 -m pytest tests -q 2>&1 | tail -8
```

Expected: 노트북 생성 성공, 전부 통과.

- [x] **Step 7: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "생성 구간을 벽시계에서 MES 공정이력으로 옮긴다

24시간 캡이 남아 있으면 첫 백필이 최근 24시간만 남기고 워터마크가 NOW 로
간다. 건너뛴 구간은 다시 채워지지 않으므로 MES 가 아는 시간대 대부분이
영구히 빈다. 센서에서 찾은 이상을 공정이력에서 확인할 수 없게 된다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### ✅ Task 7: 문서와 데모 시나리오를 고정한다

**Files:**
- Modify: `customizing/fabric/fdc-eventhouse/README.md`

- [x] **Step 1: 대표 시나리오를 실제 데이터에서 뽑는다**

```bash
cd customizing/fabric/fdc-eventhouse && python3 - <<'PY'
import json
from src.fdc_anomaly import build_profiles, run_sensor
from src.fdc_generator import build_readings
from src.fdc_runs import runs_by_equipment, span
from src.mes_probe import MesFacts

facts = MesFacts.from_dict(json.load(open("tests/fixtures/mes_facts.json")))
lo, hi = span(facts)
alarms = [r for r in build_readings(facts, lo, hi) if r["status"] == "Alarm"]
profiles = build_profiles(facts)
for runs in runs_by_equipment(facts).values():
    for r in runs:
        if not r.defect_code:
            continue
        row = next(p for p in facts.process_results
                   if p["lot_id"] == r.lot_id and p["step_code"] == r.step_code)
        sensor = run_sensor(r, profiles[r.eqp_id].eqp_type)
        hits = [a for a in alarms if a["eqp_id"] == r.eqp_id
                and a["sensor_code"] == sensor and r.start <= a["reading_ts"] < r.end]
        if not hits:
            continue
        print(f"{r.lot_id} {r.step_code:6s} {r.eqp_id:11s} {row.get('result','?'):7s} "
              f"{r.defect_code:14s} {r.start:%m-%d %H:%M}~{r.end:%H:%M} "
              f"→ {sensor} 경보 {len(hits)}건 (첫 {hits[0]['reading_ts']:%H:%M})")
PY
```

프로토타입에서 EQP-ETCH01 은 이렇게 나왔다. **README 에는 반드시 직접 돌려 나온 값을 적어라** — 픽스처를 다시 뜨면 로트 번호가 바뀐다.

```
LOT0002 ETCH   EQP-ETCH01  Fail    Particle       09-01 15:13~17:01 → CHAMBER_TEMP 경보 5건 (첫 16:47)
LOT0006 ETCH   EQP-ETCH01  Fail    Particle       09-02 07:26~09:14 → AMBIENT_HUMIDITY 경보 2건 (첫 09:04)
```

같은 설비·같은 불량인데 흐른 센서가 다르다. `run_sensor` 가 런 단위로 고르기 때문이고, 이게 "설비가 아니라 그 런에서 무슨 일이 있었나"를 보게 만드는 지점이다. 대표 예로는 공정 센서가 걸린 첫 줄을 쓴다 — `AMBIENT_*` 는 클린룸 환경이라 설명이 한 단계 더 필요하다.

- [x] **Step 2: README 를 갱신한다**

아래 내용을 담는다.

- 데이터가 MES 공정이력 구간(약 65시간)을 덮는다는 것. 벽시계 24시간이 아니다
- 가동 중에는 6종 30초, 유휴에는 2종 5분. `fdc_sensor_spec.sample_interval_sec` 은 **가동 중 간격**이다
- `run_status` 가 `Run` / `Idle` 이라는 것
- **`lot_id` 가 없는 것은 의도다.** 어느 로트였는지는 MES 에 물어야 한다. 그것이 이 실습의 핵심이다
- METRO(계측)는 설비를 배정받지 않아 FDC 데이터가 없다. 모든 공정에 FDC 가 붙어 있지는 않다
- 3단 교차 질의 시나리오. Step 1 의 실제 값으로 채운다

```kusto
// 1단계 — FDC: 경보가 몰린 설비와 시각을 찾는다
fdc_sensor_reading
| where status == "Alarm" and run_status == "Run"
| summarize alarms = count(), from = min(reading_ts), to = max(reading_ts)
        by eqp_id, sensor_code
| order by alarms desc
```

```
2단계 — MES: 그 설비가 그 시각에 무엇을 돌렸는지 묻는다
  "EQP-ETCH01 이 <from> ~ <to> 에 처리한 로트와 불량코드를 알려줘"

3단계 — QMS: 그 로트의 검사 결과를 확인한다
  "<로트> 의 검사 이력과 NCR 을 보여줘"
```

- [x] **Step 3: 커밋한다**

```bash
git add customizing/fabric/fdc-eventhouse/README.md
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "시간축과 가동·유휴 구분을 문서에 적는다

lot_id 가 없는 것이 의도라는 점과, 3단 교차 질의 시나리오를 적었다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

## 완료 조건

- [x] `python3 -m pytest tests -q` 전부 통과 (기준선 252건 → **310건**)
- [x] 픽스처의 고유 `out_time` 이 91개
- [x] 모든 판독 행에 `run_status` 가 있고 값이 `Run` / `Idle` 뿐
- [x] 유휴 행의 센서가 공통 2종뿐이고 5분 격자 위에 있음
- [x] **어떤 행에도 `lot_id` 가 없고, `fdc_validate` 의 무중복 검사가 그대로 살아 있음**
- [x] 한 `(eqp_id, sensor_code, reading_ts)` 가 중복되지 않음
- [x] 구간을 반으로 나눠 생성해도 통째로 생성한 것과 같음
- [x] `Alarm` 비율이 `MAX_ALARM_RATIO`(5%) 미만이고 0 이 아님
- [x] **`Alarm` 이 뜬 런이 전부 불량 런임 (정상 런 오탐 0)**
- [x] **유휴 구간에 `Alarm` 이 0 건**
- [x] `EXCURSION_MIN` 이 1.5 미만 (`test_lowest_severity_stays_below_min_alarm_ratio`)
- [x] `build_notebook.py` 에 `MAX_SPAN_HOURS` / `BACKFILL_HOURS` 가 없음
- [x] `MODULE_ORDER` 에 `fdc_runs` 가 `fdc_generator` 보다 앞에 있음
- [x] `python3 build_notebook.py` 가 노트북을 다시 만듦 (셀 16개)
- [x] README 의 대표 시나리오가 실제 데이터에서 뽑은 값

## 이 계획은 프로토타입으로 실증되었다

계획을 쓴 뒤 `/tmp/fdcproto` 에 저장소 사본을 만들어 Task 0~4 의 모든 변경을 실제로 적용하고 돌렸다. 아래는 추정이 아니라 **실행 결과**다.

| 항목 | 값 |
|---|---|
| 구간 | 64.8h (2026-09-01 07:13 ~ 09-04 00:00) |
| 총 행 | 102,552 (가동 93,210 · 유휴 9,342) |
| Normal / Warning / Alarm | 100,990 / 1,305 / 257 (98.48% / 1.27% / 0.25%) |
| 런 | 84 (불량 31 · 정상 53) |
| 경보 난 런 | 22 — 전부 불량 런, **오탐 0** |
| 유휴 중 경보 | 0행 |
| 검증기 | 치명 0 · 경고 0 / 8건 전부 OK (구현 후 9건) |
| 테스트 | **252 passed** |

프로토타입이 계획의 결함 네 가지를 잡았고 전부 위 본문에 반영했다.

1. **`EXCURSION_MIN = 1.5` 는 기존 불변식을 깬다.** `test_lowest_severity_stays_below_min_alarm_ratio` 가 "가장 깨끗한 설비는 반폭 1.5배에 못 닿는다"를 강제하는데, `excursion_amplitude` 는 severity 0 설비에 정확히 `EXCURSION_MIN` 을 준다. 1.4 로 확정했다.
2. **센서를 나눠 실으면 데모가 죽는다.** 분산 방식은 불량 런 31건 중 12건만 경보를 냈고 그 90%가 `AMBIENT_*` 폴백이라 README 가 지목한 EQP-ETCH01 이 조용했다. 런당 한 센서로 바꿔 22건·7개 설비로 늘었다.
3. **노트북 셀 수 테스트가 깨진다.** `MODULE_ORDER` 에 `fdc_runs` 를 넣으면 `== 15` 리터럴이 틀린다. Task 1 Step 5 로 넣었다.
4. **`test_row_count_matches_grid` 는 다시 써야 한다.** "설비 전부 × 센서 전부 × 격자 전부" 가정이 가동/유휴 분리와 양립하지 않는다. Task 4 Step 1 에 대체 테스트를 넣었다.


## 범위 밖 (의도적)

- **확산로 배치 공정.** 실제 확산로는 여러 로트를 함께 넣는다. 지금 모델은 한 시각에 한 런만 본다.
- **설비 예방정비(PM) 상태.** SEMI E10 은 여섯 가지 상태를 정의하지만 여기선 Run/Idle 둘만 쓴다.
- **QMS 재검사·NCR 의 미래 시각.** 별도 문제이며 이 계획에서 다루지 않는다.
- **노트북의 Fabric 실제 실행 검증.** Kusto 커넥터와 `mssparkutils.credentials.getToken()` 은 아직 한 번도 실행된 적이 없다.

## 남는 한계 (README 에 적는다)

- `MES_API_KEY` 를 노트북 셀에 붙여넣어야 한다. Fabric 노트북은 자동 저장되고 작업 영역은 공유될 수 있다. 실행 후 지우라고 안내한다.
- MES 앵커가 배포 시점에 고정되므로 데이터는 나이를 먹는다. 코호트마다 재배포한다.
- METRO 공정에는 FDC 데이터가 없다.

## 실행 결과 (2026-09-07)

Task 0~7 전부 완료. 완료 조건 16항목 전수 검증 통과.

```
테스트  319 passed
구간    64.8h (2026-09-01 07:13 ~ 09-04 00:00)
총 행   102,552 (가동 93,210 · 유휴 9,342)
        Normal 100,924 (98.41%) · Warning 1,327 (1.29%) · Alarm 301 (0.29%)
런      84 (불량 31 · 정상 53) · 경보 난 런 23 · 정상 런 오탐 0 · 유휴 경보 0
검증    9건 전부 OK (치명 0 · 경고 0)
```

프로토타입 예측과 한 자리도 어긋나지 않았다. 위 분포는 코드 리뷰 뒤 진폭 모델을
바꾼 결과다(아래 "코드 리뷰 대응" 참고). 리뷰 전에는 Alarm 257 · 경보 난 런 22 였다.

### 노트북을 실제로 실행해 봤다

`fdc_eventhouse_stream.ipynb` 의 16셀 중 외부 의존(Kusto 커넥터·`mssparkutils`·
MES HTTP) 4셀만 스텁하고 나머지를 전부 실행했다. 워터마크 셀이 `span(FACTS)` 로
MES 구간을 잡고, 배포 후 경과분을 유휴로 채워 117,032행을 만들었으며, `to_rows`
9열과 검증 9건이 전부 통과했다. Fabric 밖에서 확인할 수 있는 범위는 여기까지다.

### 계획에 없던 발견

| 항목 | 내용 |
|---|---|
| `test_watermark_cell_caps_span` | 캡의 존재를 강제하던 기존 테스트. 캡을 없앴으니 전제가 틀렸다. 지우지 않고 반대 불변식(`test_watermark_cell_does_not_cap_span`)으로 뒤집어 캡 부활을 막았다. Task 6 Step 4b 로 계획서에도 넣었다 |
| `data-agent-schema.md` | 계획이 언급하지 않은 파일. 에이전트에게 **"시간으로 조인하지 마세요"** 라고 지시하고 있었다. 이 문서가 데모 동작을 직접 좌우하므로 함께 뒤집었다 |
| `ago(24h)` 필터 | README·노트북·에이전트 문서의 KQL 예시 전부에 있었다. 가동 구간이 과거라 빈 결과를 낸다. 전부 걷어내고 "구간부터 확인하라"는 쿼리를 앞에 뒀다 |

### 남은 일 (자율 불가)

- `ChangJu-Ahn/mock-mes-kr#3` 리뷰·머지
- 머지 후 `az deployment group create` 재배포
- Fabric 에서 노트북 실제 실행 (Kusto 커넥터 사전 설치 여부, `mssparkutils.credentials.getToken()`)

## 코드 리뷰 대응 (2026-09-07)

`code-review` 에이전트가 5건을 지적했다. 지적을 그대로 믿지 않고 전부 코드로
재현해 실제 문제임을 확인한 뒤 고쳤다.

| # | 심각도 | 문제 | 조치 |
|---|---|---|---|
| 1 | High | `watermark_query` 실패를 "첫 실행"으로 오인해 65시간을 통째로 중복 적재. Eventhouse 는 유니크 제약이 없다 | `union isfuzzy=true` 로 "테이블 없음"을 빈 결과로 만들고, 남은 예외는 `RuntimeError` 로 멈춘다 |
| 2 | High | 스펙 42행을 판독 테이블의 watermark 로 판정. 판독 적재가 실패할 때마다 스펙만 42행씩 쌓여 조인 질의가 팬아웃 | `spec_count_query()` 로 스펙 테이블 자신의 행 수를 본다 |
| 3 | Medium-High | 진폭이 전체 설비의 min/max 정규화라, 한 설비 실적이 하나 바뀌면 4,245행의 값이 흔들림 | `severity` 를 그 설비 자신의 불량률로. 같은 실험에서 4,245행 → 152행(해당 설비만)으로 줄었다. `EXCURSION_MIN` 1.4→1.0, `MAX` 2.9→3.4 |
| 4 | Medium | README 의 `272행`/`6행` 이 `Alarm` 이 아니라 `Warning`+`Alarm` 합계. 참가자 화면과 2.4배 어긋남 | 실측값 `130행`/`0행` 으로 정정 |
| 5 | Medium | 검사 7·8 이 데이터셋 전체 성질인데 배치 단위로 평가. 백필 이후 유휴만 담긴 배치에서 영구 실패 → 20명 × 480회/일이 매번 경고를 봄 | `Check.skipped` 를 도입해 가동 행이 없으면 `SKIP` |

### 리뷰가 "문제 없음"을 확인해 준 것

- **결정론**: `PYTHONHASHSEED=0/1/12345` 3프로세스에서 해시 동일
- **워터마크 증분**: 25회 증분 시뮬레이션 vs 단발 생성 — 중복 0, 누락 0
- **가동/유휴 격자**: 102,552행 키 중복 0

### 리뷰가 짚지 않았는데 같이 고친 것

| 항목 | 내용 |
|---|---|
| `_spec_present` 의 `[0]` | Issue 2 를 고치며 넣은 `.collect()[0]["rows"]` 가 빈 결과에 `IndexError` 로 죽었다. `summarize count()` 는 한 행을 보장하므로 빈 결과는 조회 이상이다. 명시적으로 멈추게 했다 |
| `test_build_notebook.py` 의 유실된 함수 헤더 | `test_spec_table_written_once` 의 `def` 줄이 사라져 본문이 앞 테스트에 흡수돼 있었다. 앞 테스트가 통과하는 한 아무도 모르는 상태였다. 헤더를 복원했다 |
| README 1단계 데모 | `Alarm` 상위 4행이 전부 `AMBIENT_*` 라 플래그십 데모(`PAD_PRESSURE`)가 8위로 밀려 참가자가 찾을 수 없었다. 전체 13행을 보여준 뒤 `AMBIENT_*` 를 걷어내는 단계를 추가했다. 그러면 설비 고유 신호 4개만 남고 각각이 2·3단계로 이어진다 |
| README "알려진 한계" | MES 재배포 시 Eventhouse 를 비우라는 안내가 없었다. 새 가동 구간이 기존 watermark 보다 과거면 그 구간은 영구히 유휴로 남는다 |
| 거짓 문서 2곳 | `fdc_generator.py` docstring 과 README 가 판독값을 "(설비, 센서, 타임스탬프)만의 순수 함수" 라고 주장했다. MES 스냅샷은 설계상 입력이므로 "고정된 MES 스냅샷에 대해" 라는 단서를 붙여 정직화했다 |

### 노트북을 5개 시나리오로 다시 돌렸다

이번에는 셀을 건너뛰지 않고 외부 서비스(`spark`, `mssparkutils`, `kusto_read`,
`kusto_write`, MES HTTP)만 스텁해 **14개 코드 셀 전부**를 실행했다.

| 시나리오 | 기대 | 결과 |
|---|---|---|
| A 첫 실행 (스펙 0행 · watermark 없음) | 스펙 + 판독 둘 다 적재 | `backfill` · 42행 + 117,160행 ✓ |
| B 재실행 (스펙 42행 · watermark 있음) | 스펙 건너뜀, 판독만 증분 | `live` · 46,778행 ✓ |
| C 판독만 실패했던 재시도 | 스펙 건너뜀, 판독 전체 | `backfill` · 스펙 0회 ✓ |
| D watermark 조회 실패 (401) | 백필로 떨어지지 말고 멈춤 | `RuntimeError` ✓ |
| E 스펙 조회 빈 결과 | 모르는 채로 쓰지 말고 멈춤 | `RuntimeError` ✓ |

D·E 가 Issue 1·2 의 회귀 방어선이다. 둘 다 실패 시 조용한 중복 적재로 이어지므로
멈추는 쪽이 옳다.

## 2차 코드 리뷰 대응 (2026-09-07, 커밋 `ace5366`)

1차 대응 커밋이 만든 **회귀**를 포함해 4건을 고쳤다.

### Critical — `union isfuzzy=true` 단일 레그는 빈 결과가 아니라 에러다

`watermark_query()` / `spec_count_query()` 를 `union isfuzzy=true` 로 감싸면
테이블이 없어도 빈 결과가 온다고 가정했는데 틀렸다. 공식 문서 원문:

> If at least one such table was found, any resolution failure yields a warning
> ... **If no resolutions were successful, the query returns an error.**

isfuzzy 는 **여러 레그 중 일부**가 없을 때만 무시한다. 레그가 실제 테이블
하나뿐이라 첫 실행에는 해석 성공이 0건 → 쿼리 실패. 1차 대응에서 조회 실패를
`RuntimeError` 로 바꿔 뒀으므로 **참가자 전원의 첫 실행이 중단됐을 것이다.**
게다가 메시지가 "KUSTO_URI 를 확인하세요" 라 멀쩡한 설정을 의심하게 만든다.

항상 해석되는 빈 `datatable` 레그를 붙여 해결. 이 레그가 두 가지를 동시에 푼다.

1. 해석 성공 최소 1건 보장 → fuzzy union 성립
2. `reading_ts` 타입 제공 → 빈 결과에 컬럼이 없어 `max(reading_ts)` 가
   SEM0100 으로 죽던 두 번째 문제도 제거

**이 버그가 통과한 경로가 교훈이다.** 테스트가 `"union isfuzzy=true" in query`
라는 문자열 포함만 봤다. 이름은 동작을 주장하는데 실제로는 구현 문자열을
고정하고 있었다. 하네스도 Kusto 를 스텁하므로 같은 가정을 되풀이할 뿐이었다.
→ 문자열 포함 검사로 동작을 주장하는 테스트를 경계할 것.

한계를 README "알려진 한계" 에 명시했다. 이 두 쿼리는 실제 빈 데이터베이스에
대고 KQL 쿼리셋에서 실행해 보는 것 외에 검증 방법이 없다.

### 스펙 게이트가 부분 적재를 조용한 누락으로 바꿨다

`== 0` 이 아니면 건너뛰면 스펙 20행 상태가 영구히 방치된다. 빠진 22개 조합의
판독 행이 `join kind=inner` 에서 소리 없이 사라진다. 중복은 행 수가 부풀어
눈에 띄지만 누락은 안 띈다. 0 / 정확히 42 / 그 외 세 갈래로 나누고, 세 번째는
어긋난 방향과 복구용 `.drop table` 을 알려주고 멈춘다.

### 문서가 코드보다 강한 주장을 하던 곳

- `fdc_anomaly.py` `severity` 필드 주석이 이번에 없앤 "상대 불량률" 을 설명
- `test_amplitude_boundary...` docstring 이 코드가 걸지 않는 제약("여유 0.06")을
  단언 → 단언하는 것/하지 않는 것을 명시하도록 좁히고, 상수 근거는 상수 옆으로
- `data-agent-schema.md` 경보 비율이 옛 측정값
- `AMBIENT_*` 제외 단서가 README 에만 있고 에이전트 지식에 없었음 → 대표 질의에
  필터 추가 + 환경 경보 판단 근거 서술

### 계획 밖 발견 — 행 수를 고정값으로 적어 둔 문제

시나리오 검증 중 총 행이 문서의 102,552 가 아니라 117,224 였다. 생성 구간이
`NOW` 까지라 MES 종료 이후가 전부 유휴로 채워진다. 실측:

| 시점 | 총 행 | 증가 | Alarm |
|---|---|---|---|
| MES 구간 | 102,552 | — | 301 |
| +1일 | 107,160 | +4,608 | 301 |
| +7일 | 134,808 | +32,256 | 301 |
| +30일 | 240,792 | +138,240 | 301 |

**유휴는 경보를 내지 않아 Alarm 301 은 고정**이고 총 행·Normal·비율만 자란다.
측정 사흘 만에 14% 어긋나 있었다. 참가자가 문서와 다른 숫자를 보고 잘못됐다고
의심하게 된다. README 표에 단서를 달고, 에이전트에게는 "비율 대신 건수와 설비별
분포를 보라 · 세야 하면 직접 세라" 고 안내했다.

### 검증

- 323 테스트 통과
- 노트북 14셀 × **7개 시나리오**(기존 5 + 스펙 부분적재 20행 + 중복 84행) 전부 통과
- 워터마크 재실행이 정확히 여집합만 씀: 102,552 + 14,672 = 117,224 (중복·누락 0)

### 남은 것 (자율 불가)

- `datatable` 레그가 Spark Kusto 커넥터 read 경로에서 동작하는지 **실제 Fabric 검증**
- 커넥터 기본 `writeMode`(트랜잭션)에 암묵적으로 기대는 점을 명시할지 판단

---

## 3차 코드 리뷰 대응 (2026-09-07, 커밋 `58f1ab7`)

### High — watermark 읽기가 드라이버 타임존을 UTC 로 단정했다

3라운드 내내 막아 온 "조용한 중복 적재" 의 마지막 구멍이었다. 앞의 두 라운드에서 다른 경로를 다 막았기 때문에 이것만 남았다.

리뷰 지적을 믿지 않고 PySpark `branch-3.5` 의 `python/pyspark/sql/types.py` 를 직접 받아 확인했다. `TimestampType` 은 쓰기와 읽기가 **비대칭**이다.

```python
def toInternal(self, dt):    # tz-aware 를 넘기므로 timegm = UTC 기준
    seconds = calendar.timegm(dt.utctimetuple()) if dt.tzinfo else time.mktime(dt.timetuple())

def fromInternal(self, ts):  # tz 인자가 없다 = 드라이버 OS 로컬 시각, naive
    return datetime.datetime.fromtimestamp(ts // 1000000).replace(microsecond=ts % 1000000)
```

쓰기는 `to_rows()` 가 tz-aware 를 넘기므로 안전하다. **읽기만** 로컬 시각으로 떨어진다. 그런데 코드는 그 naive 값에 `.replace(tzinfo=utc)` 로 라벨만 붙였다. `replace` 는 값을 그대로 두므로 드라이버가 UTC 가 아니면 watermark 가 오프셋만큼 통째로 어긋난다.

하네스로 실제 재현했다 (2회차 적재 행 수).

| 드라이버 TZ | 2회차 행 수 | 결과 |
|---|---|---|
| UTC | 14,752 | 정상 |
| Asia/Seoul (+9) | 13,024 | **1,728행 영구 누락** |
| America/Los_Angeles (−7) | 21,538 | **6,786행 중복** |

양수 오프셋은 watermark 를 미래로 보내 `START > NOW` 를 만들고, 그 구간을 영구히 잃는다. 음수 오프셋은 과거로 보내 매 실행이 수천 행을 중복 적재한다. **Fabric 기본 세션은 UTC 라 대부분 안 터지지만, 작업 영역 설정 하나로 조용히 깨진다.**

특히 나쁜 점은 **기존 방어가 전부 무력하다**는 것이다. `datatable` 레그도, `except` 의 `RuntimeError` 도, 3갈래 스펙 게이트도 전부 "쿼리가 실패했을 때" 를 막는다. 여기서는 쿼리가 *성공*하고 값만 틀린다. 검증기 5번(중복 없음)도 어긋난 watermark 를 기준으로 판정하므로 통과한다.

`.astimezone(timezone.utc)` 로 고쳤다. `astimezone` 은 naive 를 시스템 로컬로 해석해 변환하므로 `fromtimestamp` 가 한 일을 정확히 되돌린다. 수정 후 **다섯 타임존 모두 14,752행으로 일치**한다.

`spark.sql.session.timeZone` 설정은 해결책이 **아니다.** `fromtimestamp` 는 그 설정이 아니라 OS TZ 를 본다.

### 미래 watermark 가드

watermark 는 우리가 과거에 쓴 행에서 나오므로 미래일 수 없다. 미래면 `START > NOW` 가 되어 매 실행이 0행을 쓰고 "새로 만들 구간이 없습니다" 만 반복하다 그 구간을 영구히 잃는다. 시계 오차를 감안해 5분을 허용하고 그 밖은 멈춘다.

경계를 확인했다: `NOW+2일` → RAISE, `NOW+10분` → RAISE, `NOW+1분` → OK.

`astimezone` 이 타임존 문제를 중화하므로 이 가드가 실제로 잡는 것은 **시계 어긋남**이다. 두 겹으로 두는 이유는 위 표에서 보듯 이 실패가 조용하기 때문이다.

### 테이블 사전 생성 (리뷰어의 더 강한 제안을 채택)

2차 리뷰에서 남긴 열린 질문 — "`datatable` 레그가 Kusto 의 해석 성공 카운트에 들어가는가" — 은 실제 Eventhouse 없이 확정할 수 없다. 리뷰어가 **질문 자체를 없애는** 쪽을 제안했고 그게 옳다.

README 1단계에 붙여넣기 두 줄을 넣었다.

```kusto
.create-merge table fdc_sensor_reading (...)
.create-merge table fdc_sensor_spec (...)
```

그러면 "테이블 없음" 상태 자체가 사라져 검증 불가능한 가정을 타지 않는다. `.create-merge` 는 멱등이라 여러 번 실행해도 안전하다. `isfuzzy` 방어는 그대로 둔다 — 안내를 건너뛴 사람에게는 여전히 필요하다.

README 의 DDL 이 스키마와 어긋나면 사전 생성이 오히려 해로우므로 `test_readme_ddl_matches_the_schema` 로 두 정의를 묶었다.

### 함께 고친 것

- README 238행의 첫 백필 설명이 "약 65시간, 10만 행" 으로 남아 있었다. 생성 구간은 `NOW` 까지이므로 배포 후 며칠이 지나면 그만큼 유휴가 더 붙는다. 같은 README 116행의 "일주일 뒤 134,808행" 과 어긋나 있었다.
- `except` 주석이 "union isfuzzy 라서 예외를 안 낸다" 고 옛 근거를 설명하고 있어 `datatable` 레그로 정정했다.

### 테스트는 문자열이 아니라 의미를 검사한다

2차 리뷰의 Issue A 가 문자열 포함 검사의 틈으로 통과한 전례가 있어, 이번 테스트는 PySpark 의 `toInternal`/`fromInternal` 을 재현해 **다섯 타임존에서 왕복**시키고 원래 값으로 돌아오는지 본다.

**새 테스트가 실제로 버그를 잡는지 확인했다.** `astimezone` 을 옛 `replace` 로 되돌리니 3건이 실패하고, 복원하니 통과한다.

### 검증

- **332 테스트 통과** (323 + 타임존 3 + 노트북 셀 2 + DDL 1 + 기타 3)
- 노트북 14셀 × 7시나리오 전부 통과
- 타임존 5종(UTC/서울/LA/베를린/콜카타) 왕복 — 전부 14,752행 동일

### MES 시간축 작업과의 정합 확인

`ChangJu-Ahn/mock-mes-kr#3` 이 올라왔고, 그 결과를 픽스처와 대조해 **전 항목 일치**를 확인했다.

```
                런 91 · 고유 out_time 91 · 구간 64.8h · 끝 2026-09-04T00:00:00+00:00
                불량코드 있는 런 35
설비별 런        DIFF01 16 · ETCH01 15 · IMPL01 12 · CVD01 11 · PHOT01 11
                CMP01 8 · METRO(None) 7 · TEST01 6 · PHOT02 5
LOT0010/ETCH    ETCH01 / Particle / Rework · 22:32 → 00:12 (100분)
```

파생값도 맞는다. 설비가 배정된 런 84건(91 − METRO 7), 그중 불량 31건(35 − METRO 4) — FDC 가 "런" 으로 세는 단위와 정확히 같다. **즉 픽스처는 이미 PR #3 의 출력이며, 재배포를 기다리지 않고 FDC 를 완성할 수 있었다.**

### 남은 것 (자율 불가)

- `mock-mes-kr#3` 머지 → `az deployment group create` 재배포. 재배포 시 `utcNow()` 가 새 앵커를 잡으므로 **FDC Eventhouse 도 함께 비워야 한다** (새 구간이 기존 watermark 보다 과거면 영구히 빈 채로 남는다). README "알려진 한계" 에 `.drop table` 로 적어 뒀다.
- 실제 Fabric 에서 노트북 첫 실행 — 사전 생성 안내를 따르면 `isfuzzy` 경로를 아예 타지 않는다.

---

## 4차 코드 리뷰 대응 (2026-09-07, 커밋 `9f7fb42`·`a671f7d`)

### 예측이 맞았다 — 네 번째가 있었다

3차 대응을 넘기면서 리뷰어에게 이렇게 물었다.

> 1차 watermark 재백필, 2차 `union isfuzzy`, 3차 타임존 — 전부 "조용한 중복/누락 적재" 였습니다. 같은 뿌리에서 나온 네 번째가 남아 있을 가능성이 높다고 봅니다.

있었다. 그리고 이번 것은 앞의 셋과 성질이 다르다. **앞의 셋은 코드 안의 버그였고, 이번 것은 코드가 기대는 전제 자체다.**

### Issue G (High) — 실행이 겹치면 방어가 전부 무력해진다

방어 일곱 줄이 전부 **단일 실행 내부**를 지킨다. 그런데 구조는 read-modify-write 다.

```
max(reading_ts) 읽기  ─┐
                       │  이 사이에 다른 실행이 못 끼어든다는 보장이 없다
readings 쓰기         ─┘
```

Kusto 에는 유니크 제약도, 우리가 쓸 CAS 나 리스도 없다. `writeMode=Transactional` 은 **각각의** 쓰기를 원자적으로 만들 뿐 두 쓰기가 모두 안착하는 것을 막지 않는다.

리뷰어가 재현한 결과.

```
A 가 쓴 행    107,160
B 가 쓴 행    107,160
최종 테이블   214,320행 (고유 107,160)
```

**검증기 9개가 전부 통과한다.** `fdc_validate.py:111` 의 `stale = [r for r in readings if watermark and r["reading_ts"] <= watermark]` 에서 B 의 `watermark` 는 `None` 이다(A 의 쓰기가 아직 안 보임). 각 실행이 **자기 자신과는 일관되기** 때문에 아무 검사도 못 잡는다.

### 스펙 게이트가 일시적 경합을 영구 장애로 바꾼다 (2·3차 수정이 만든 회귀)

```
A: _spec_present == 0 → 42행 write
B: _spec_present == 0 → 42행 write   (A 의 write 미가시)
   → 84행
C 이후: else 의 raise → 모든 실행이 판독을 한 행도 못 쓰고 죽는다
```

`else` 의 raise 가 판독 write **앞**에 있다. 시끄럽게 만든 방향은 옳지만, 경합의 결과로 파이프라인이 멈춰 선다는 성질은 3갈래 게이트가 새로 만든 것이다.

**복구 안내가 더 나빴다.** `.drop table fdc_sensor_spec` 만 안내하고 있었다. 판독 테이블의 107,160 중복행은 그대로 남는다. 참가자는 시끄러운 에러를 고쳤다고 믿는데 판독은 계속 2배다.

### Fabric 이 대신 막아 주는지 1차 자료로 확인했다

리뷰어도 "공식 문서에서 확인하지 못했다" 고 남겼기에 직접 조사했다. 결론은 **문서에 보장이 없다.**

| 확인 대상 | 결과 |
|---|---|
| Job Scheduler 의 `Deduped` 상태 | 공통 모델에 존재("A job instance of the same job type is already running and this job instance is skipped"). 그러나 **노트북 job 타입에 적용된다는 서술이 없다.** Lakehouse table maintenance 는 명시하는데 Notebook API 문서에는 같은 문장이 없다 |
| Create Item Schedule REST API | request body 에 `concurrency` / `skipIfRunning` / `overlapPolicy` 없음. Data Factory 파이프라인에는 있다 |
| Spark job queueing | "Job admission is based on available Spark VCores" — **용량 대기열**이다. 중복 실행 방지가 아니다. 용량이 넉넉하면 두 job 이 함께 admission 된다 |
| 최소 스케줄 주기 | 1분. 3분은 허용 범위 |
| starter pool 세션 기동 | 기본 5~10초지만 best-effort. 조건에 따라 **"2 to 5 minutes"** 라고 문서가 명시 |

마지막 줄이 결정적이다. **README 가 권하던 3분 주기는 세션 기동만으로 주기를 넘길 수 있다.**

### 고친 방향 — 막을 수 없으면 좁히고, 빠져나올 길을 정확히 한다

겹침을 코드로 완전히 막을 방법이 없다. CAS 도 잠금도 없다. 그래서 경로 셋을 각각 좁혔다.

| 경로 | 조치 |
|---|---|
| 백필 중에 스케줄이 켜져 있다 | README 4단계 맨 앞에 3단계 완료 확인을 못 박음 |
| 실행이 주기보다 길다 | 권장 주기 3분 → **15분**. 근거를 비용에서 겹침으로 바꾸고 Learn 의 세션 기동 시간을 인용 |
| 멈춘 줄 알고 "Run all" 을 다시 누른다 | `kusto_write` 가 쓰기 전에 몇 분 걸린다고, 다시 실행하지 말라고 먼저 출력 |

세 번째가 가장 흔할 것이다. Transactional 쓰기는 임시 테이블 → 폴링 → extent 이동이라 몇 분간 아무 출력이 없다.

복구 안내에는 중복 확인 쿼리와 두 테이블을 모두 지우는 경로를 넣었다.

```kusto
fdc_sensor_reading
| summarize n = count() by reading_ts, eqp_id, sensor_code
| where n > 1 | count
```

### Issue F (Medium) — 3차에 지적한 테스트가 그대로였다

`test_watermark_query_tolerates_a_missing_table` 이 `e2abbe1`(datatable 레그가 없어 첫 실행에 반드시 실패하던 버전)과 **바이트 단위로 같았다.**

```python
"""첫 실행에는 테이블이 없다. 거기서 예외가 나면 안 된다."""
assert "union isfuzzy=true" in cell
```

docstring 은 **Kusto 런타임 동작**을 단언하고 어서션은 **토큰 존재**만 본다. 2차 리뷰의 Critical 이 정확히 이 틈으로 통과했는데, 고친 뒤에도 그 틈이 열려 있었다. 쌍둥이인 `test_fdc_schema.py:178` 은 제대로 올렸는데 이쪽만 빠졌다.

**교훈: 같은 성질의 테스트가 두 파일에 있으면 둘 다 고쳤는지 확인해야 한다.** 한쪽만 고치면 남은 쪽이 옛 가정을 계속 통과시킨다.

### 타임존 테스트가 PySpark 를 복제하는 문제

리뷰어가 Q1 답변에서 짚었다. `test_watermark_round_trip_survives_any_driver_timezone` 은 PySpark 의 `fromInternal` 을 **테스트가 복제**한다. PySpark 가 동작을 바꾸면 코드와 테스트가 **같은 방향으로 함께** 틀린다 — 테스트는 계속 초록이고 프로덕션만 깨진다. Issue F 와 같은 종류다.

docstring 에 "PySpark 3.5 `types.py` 기준이며 런타임 버전을 올리면 원문을 다시 대조하라" 를 원문과 함께 못 박았다. 남는 방어는 미래 watermark 가드다.

`format_datetime()` 문자열 왕복은 채택하지 않았다. 이론적으로 더 견고하지만 **검증 불가능한 Kusto 쿼리 표면이 하나 더 는다.** 이미 두 쿼리를 실제 Kusto 없이 검증 못 하는 상태다.

### 부수 — README 의 설비 단위 재백필은 없는 기능이었다

> 실습 중 MES 에 쓰기를 했고 시계열에 계단이 보인다면 **그 설비의 데이터를 다시 백필하세요.**

이 조작은 노트북이 제공하지 않는다. `START` 는 전역 watermark 하나에서 나오고 `build_readings` 는 모든 설비를 한꺼번에 돈다.

그리고 진폭 계단보다 조용한 결과가 하나 더 있다. `register_process_result` 로 **이미 적재한 구간 안에** 실적을 등록하면 그 런의 `Run` 판독값이 영영 생성되지 않는다. MES 는 "그 로트가 그 설비에서 돌았다" 는데 FDC 에는 `Idle` 뿐이라 교차 질의가 빈 결과를 낸다. 에러 없이. 둘 다 적었다.

### 리뷰어가 "문제 없음" 을 증거로 확인해 준 것

단일 writer 전제 하에서는 증분 경로에 빠진 것이 없다. 격자에 안 맞는 `NOW`, 1초~3시간 지터, MES 종료 시각을 가로지르는 구간, write 실패 25% 주입으로 8회 무작위 시행.

```
trial0 +87.3h 실패write=79  정답119,304 적재119,304 중복0 누락0 잉여0 값불일치0
...
trial6 +17.7h 실패write=47  정답105,928 적재105,928 중복0 누락0 잉여0 값불일치0
```

**빠진 건 방어가 아니라 그 전제 자체였다.**

### 계획 밖 발견 — 노트북 재빌드가 항상 dirty 로 보이던 문제

`nbformat` 은 셀마다 임의의 id 를 새로 뽑는다. 내용이 하나도 안 바뀌어도 재빌드가 매번 16줄 diff 를 만들었다. 그러면 `git status` 로 "내용이 진짜 바뀌었나" 를 구분할 수 없고, **"src/ 를 고치고 재빌드를 안 해서 옛 코드가 배포되는" 함정을 테스트로 막을 수도 없다.** 이 함정에는 이 작업 중에 실제로 한 번 걸렸다.

셀 id 를 역할에서 뽑도록 바꿨다(`fdc-watermark`, `fdc-module-fdc-runs` …). 내용 해시가 아니라 역할인 이유는, 해시로 하면 한 셀을 고칠 때 그 셀의 id 까지 흔들려 리뷰가 어려워지기 때문이다. 세 번 연속 빌드해 바이트 해시가 같은 것을 확인했다(`201d55f9ecb482cd`).

그 위에 `test_committed_notebook_matches_a_fresh_build` 를 얹었다. 비교는 `nbformat.writes` 가 아니라 `write` 로 한다 — 둘은 끝 개행 하나가 다르고 실제로 커밋되는 것은 `write` 쪽이다. `writes` 로 비교하면서 그 차이를 `rstrip` 으로 덮으면 나중에 nbformat 이 직렬화를 바꿨을 때 테스트가 조용히 거짓을 말한다.

### 검증

- **338 테스트 통과** · 노트북 7시나리오
- 새 테스트 4개가 실제로 회귀를 잡는지 되돌려서 확인

```
복구 안내에서 판독 테이블 제거      → FAILED
쓰기 전 안내 문구 제거              → FAILED
datatable 레그 제거 (옛 상태)       → FAILED   ← 옛 테스트는 통과하던 상태
README 권장 주기 되돌리기            → FAILED
```

### 남은 한계 (의도적)

겹침을 코드로 막지 못했다. 리뷰어가 제안한 "쓰기 직전 watermark 재확인" 은 창을 초 단위로 줄일 뿐 닫지 못하고, 백필 경합(둘 다 `None` 을 봄)에는 아예 듣지 않는다. 그러면서 검증 불가능한 Kusto 쿼리 표면을 하나 더 만든다. 교육 자료에 그 값을 치를 만하지 않다고 판단했고, 대신 한계를 README 에 적었다.

### 이 작업에서 배운 것

네 라운드가 전부 같은 종류였다.

| 라운드 | 지적 | 공통점 |
|---|---|---|
| 1차 | watermark 재백필 | 쿼리는 성공하고 값만 틀림 |
| 2차 | `union isfuzzy` 단일 레그 | 문자열 검사가 동작을 단언 |
| 3차 | 드라이버 타임존 | 쿼리는 성공하고 값만 틀림 |
| 4차 | 실행 겹침 | 검사가 전부 통과하고 값만 2배 |

**전부 "에러가 안 나는 실패" 다.** 이런 코드에서는 "테스트가 통과한다" 가 근거가 되지 못한다. 새 테스트가 실제로 그 버그를 잡는지 옛 코드로 되돌려 확인하는 습관이 네 라운드 내내 유일하게 믿을 만한 도구였다.
