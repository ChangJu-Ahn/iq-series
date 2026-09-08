# Mock MES 시간축 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mock MES 시드가 생성하는 91건의 공정이력을 앵커 시각 이전 약 65시간에 걸쳐 현실적인 설비 경합 순서로 흩뿌린다.

**Architecture:** 시드를 2단계로 나눈다. 1단계는 지금과 **완전히 동일한 순서로** `Random(42)` 를 소비해 91건의 공정 계획(설비·불량·판정·작업자)을 메모리에 모으기만 하고 DB 에 쓰지 않는다. 그 사이에 별도 RNG `Random(20260904)` 로 소요시간을 뽑아 설비 경합을 반영한 스케줄을 계산하고, 마지막 공정의 종료가 앵커에 정확히 닿도록 전체를 오프셋으로 민다. 2단계는 계획을 `out_time` 오름차순으로 정렬해 기존 트랜잭션 함수로 등록한다.

**Tech Stack:** Python 3.12+, 표준 라이브러리만(`random`, `datetime`, `os`, `dataclasses`), SQLite, `unittest`, Bicep

## Global Constraints

- **저장소:** `~/Repo/mock-mes-kr` (이 계획의 모든 경로는 이 저장소 기준). `iq-series` 저장소는 건드리지 않는다.
- **RNG 분리는 절대 조건이다.** 기존 `rnd = random.Random(42)` 의 소비 순서와 횟수를 단 한 번도 바꾸지 않는다. 소요시간·이송시간은 오직 새 `sched = random.Random(20260904)` 에서만 뽑는다.
- **회귀 가드:** `mes_core/test_db.py` 의 `SeedTests` 는 `process_result=91`, `lot=16`, Done 로트 6, SEMI 합계 69, FIN 합계 277, WIP 10 을 이미 검증한다. **이 값들이 하나라도 바뀌면 RNG 분리에 실패한 것이다.** 절대 이 기대값을 수정해서 통과시키지 않는다.
- **시각 형식:** `datetime.now(timezone.utc).replace(microsecond=0).isoformat()` 과 동일한 `2026-09-04T12:34:56+00:00` 형식. 기존 `mes_core/db.py:185` `_now_iso()` 와 일치해야 한다.
- **모든 시각은 UTC.** 로컬 시간대로 변환하지 않는다.
- **`hash()` 금지** (프로세스마다 값이 달라짐). 필요하면 `hashlib`.
- **테스트 실행:** `python3 -m unittest mes_core.test_db mes_core.test_schedule api.tests.test_app` (로컬에 `mcp` 패키지가 없으면 `mcp_server.test_server` 는 import 오류가 나므로 제외한다. CI 와 컨테이너에는 설치되어 있다.)
- **공정 소요시간(분):** DIFF 90–180, PHOTO 30–90, ETCH 40–120, IMPL 30–60, CVD 60–180, CMP 30–60, METRO 15–30, TEST 120–240. 이송 10–40. 로트 릴리스 간격 240분.
- **스펙:** `/Users/changjuahn/Repo/copilot-worktrees/iq-series/changju-ahn-friendly-giggle/docs/superpowers/specs/2026-09-04-mes-fdc-time-axis-redesign.md` (§3, §4)

---

## 이 계획은 프로토타입으로 실증되었다

계획을 쓰기 전에 전체 변경을 임시 사본에 적용해 돌려봤다. 아래는 추측이 아니라 실측값이며, **구현이 끝났을 때 나와야 할 목표치**다.

```
기존 66개 테스트 전부 통과 (SeedTests 91/16/6/69/277/10 수정 없이)

런 91  고유 out_time 91  구간 64.8h  끝 2026-09-04T00:00:00+00:00
in<out True · 미래없음 True · id=시간순 True · 로트내순서 True · 설비겹침 0
착수일 3종

설비별 런: DIFF01 16 · ETCH01 15 · IMPL01 12 · CVD01 11 · PHOT01 11
           CMP01 8 · None(METRO) 7 · TEST01 6 · PHOT02 5
불량코드 있는 런: 35
```

설비 분포와 불량 건수가 **변경 전과 완전히 같다.** RNG 분리가 의도대로 동작한다는 증거다.

FDC 데모가 의존하는 로트도 살아남았고, 이제 시각이 붙었다.

```
LOT0010 / ETCH / EQP-ETCH01 / Particle / Rework
  2026-09-02T22:32 → 2026-09-03T00:12   (100분, ETCH 밴드 40–120 안)
```

같은 앵커로 두 번 시드하면 공정이력 해시가 동일하고(`e152a259c416f134a1d1`), 앵커를 바꾸면 `MAX(out_time)` 이 정확히 따라간다.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `mes_core/schedule.py` (신규) | 앵커 해석 + 스케줄 계산. DB 를 모른다. 순수 함수. |
| `mes_core/test_schedule.py` (신규) | 스케줄러 불변식 테스트 |
| `mes_core/seed.py` (수정) | 2단계 구조로 재편. 계획 수집 → 스케줄 → 정렬 등록 |
| `mes_core/db.py` (수정) | `start_lot(start_date=...)` 파라미터, AUTO_FAB `result_date` 를 `out_time` 기준으로 |
| `mes_core/test_db.py` (수정) | 시간축 불변식 검증 추가 |
| `infra/main.bicep` (수정) | `MES_ANCHOR` 파라미터 → 시드 컨테이너 env |
| `README.md` (수정) | 시간축·앵커·재배포 설명 |

---

### Task 1: 스케줄러 모듈

DB 를 전혀 모르는 순수 계산 모듈을 먼저 만든다. 이 모듈만 있으면 시드 없이도 불변식을 검증할 수 있다.

**Files:**
- Create: `mes_core/schedule.py`
- Test: `mes_core/test_schedule.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리만)
- Produces:
  - `SCHEDULE_SEED: int = 20260904`
  - `RELEASE_INTERVAL_MIN: int = 240`
  - `STEP_DURATION_MIN: dict[str, tuple[int, int]]`
  - `TRANSPORT_MIN: tuple[int, int] = (10, 40)`
  - `resolve_anchor() -> datetime` — `MES_ANCHOR` env 를 읽어 UTC `datetime` 반환, 없으면 현재 UTC
  - `iso(dt: datetime) -> str` — `_now_iso()` 와 같은 형식의 문자열
  - `assign_times(runs, anchor, sched) -> dict[Any, datetime]` — `runs` 각 원소의 `in_time`/`out_time` 속성을 문자열로 채우고, 로트키→릴리스 시각 매핑을 반환. `runs` 는 **로트별로 묶이고 로트 안에서는 공정 순서**여야 한다. 각 원소는 `.lot_key`, `.step_code`, `.eqp_id`, `.in_time`, `.out_time` 속성을 갖는 가변 객체.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`mes_core/test_schedule.py` 를 만든다.

```python
"""Unit tests for the seed scheduler (mes_core.schedule)."""

import os
import random
import unittest
from datetime import datetime, timedelta, timezone

from mes_core import schedule


class _Run:
    """Minimal stand-in for a planned process result."""

    def __init__(self, lot_key, step_code, eqp_id):
        self.lot_key = lot_key
        self.step_code = step_code
        self.eqp_id = eqp_id
        self.in_time = ""
        self.out_time = ""


ANCHOR = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)


def _parse(s):
    return datetime.fromisoformat(s)


class ResolveAnchorTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("MES_ANCHOR", None)

    def tearDown(self):
        os.environ.pop("MES_ANCHOR", None)

    def test_absent_env_returns_utc_now(self):
        before = datetime.now(timezone.utc)
        got = schedule.resolve_anchor()
        after = datetime.now(timezone.utc)
        self.assertEqual(got.tzinfo, timezone.utc)
        self.assertEqual(got.microsecond, 0)
        self.assertLessEqual(before.replace(microsecond=0), got)
        self.assertLessEqual(got, after)

    def test_parses_offset_form(self):
        os.environ["MES_ANCHOR"] = "2026-09-04T00:00:00+00:00"
        self.assertEqual(schedule.resolve_anchor(), ANCHOR)

    def test_parses_z_suffix(self):
        os.environ["MES_ANCHOR"] = "2026-09-04T00:00:00Z"
        self.assertEqual(schedule.resolve_anchor(), ANCHOR)

    def test_naive_value_is_treated_as_utc(self):
        os.environ["MES_ANCHOR"] = "2026-09-04T00:00:00"
        self.assertEqual(schedule.resolve_anchor(), ANCHOR)

    def test_non_utc_offset_is_converted(self):
        os.environ["MES_ANCHOR"] = "2026-09-04T09:00:00+09:00"
        self.assertEqual(schedule.resolve_anchor(), ANCHOR)

    def test_blank_env_falls_back_to_now(self):
        os.environ["MES_ANCHOR"] = "   "
        self.assertEqual(schedule.resolve_anchor().tzinfo, timezone.utc)

    def test_garbage_raises(self):
        os.environ["MES_ANCHOR"] = "not-a-timestamp"
        with self.assertRaises(ValueError):
            schedule.resolve_anchor()


class IsoTests(unittest.TestCase):
    def test_matches_db_now_iso_format(self):
        self.assertEqual(schedule.iso(ANCHOR), "2026-09-04T00:00:00+00:00")

    def test_drops_microseconds(self):
        dt = ANCHOR.replace(microsecond=123456)
        self.assertEqual(schedule.iso(dt), "2026-09-04T00:00:00+00:00")


class AssignTimesTests(unittest.TestCase):
    def _runs(self):
        """Two lots, three steps each, contending for one etcher."""
        return [
            _Run(1, "DIFF", "EQP-DIFF01"),
            _Run(1, "ETCH", "EQP-ETCH01"),
            _Run(1, "METRO", None),
            _Run(2, "DIFF", "EQP-DIFF01"),
            _Run(2, "ETCH", "EQP-ETCH01"),
            _Run(2, "METRO", None),
        ]

    def test_last_run_ends_exactly_at_anchor(self):
        runs = self._runs()
        schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        self.assertEqual(max(_parse(r.out_time) for r in runs), ANCHOR)

    def test_every_run_starts_before_it_ends(self):
        runs = self._runs()
        schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        for r in runs:
            self.assertLess(_parse(r.in_time), _parse(r.out_time))

    def test_no_run_is_in_the_future(self):
        runs = self._runs()
        schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        for r in runs:
            self.assertLessEqual(_parse(r.out_time), ANCHOR)

    def test_steps_within_a_lot_do_not_overlap(self):
        runs = self._runs()
        schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        for lot in (1, 2):
            seq = [r for r in runs if r.lot_key == lot]
            for prev, cur in zip(seq, seq[1:]):
                self.assertLessEqual(_parse(prev.out_time), _parse(cur.in_time))

    def test_same_equipment_never_runs_two_lots_at_once(self):
        runs = self._runs()
        schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        by_eqp = {}
        for r in runs:
            if r.eqp_id:
                by_eqp.setdefault(r.eqp_id, []).append(r)
        for eqp, rs in by_eqp.items():
            rs.sort(key=lambda r: _parse(r.in_time))
            for prev, cur in zip(rs, rs[1:]):
                self.assertLessEqual(
                    _parse(prev.out_time), _parse(cur.in_time),
                    f"{eqp} overlaps",
                )

    def test_equipmentless_steps_may_run_concurrently(self):
        """METRO has no equipment, so it is bounded only by its own lot."""
        runs = self._runs()
        schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        metro = [r for r in runs if r.step_code == "METRO"]
        self.assertEqual(len(metro), 2)

    def test_returns_release_time_per_lot(self):
        runs = self._runs()
        rel = schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        self.assertEqual(set(rel), {1, 2})
        for lot in (1, 2):
            first = min(_parse(r.in_time) for r in runs if r.lot_key == lot)
            self.assertLessEqual(rel[lot], first)

    def test_lots_are_released_at_the_configured_interval(self):
        runs = self._runs()
        rel = schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        self.assertEqual(
            rel[2] - rel[1], timedelta(minutes=schedule.RELEASE_INTERVAL_MIN)
        )

    def test_is_deterministic(self):
        a, b = self._runs(), self._runs()
        schedule.assign_times(a, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        schedule.assign_times(b, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        self.assertEqual(
            [(r.in_time, r.out_time) for r in a],
            [(r.in_time, r.out_time) for r in b],
        )

    def test_durations_stay_within_the_configured_band(self):
        runs = self._runs()
        schedule.assign_times(runs, ANCHOR, random.Random(schedule.SCHEDULE_SEED))
        for r in runs:
            lo, hi = schedule.STEP_DURATION_MIN[r.step_code]
            mins = (_parse(r.out_time) - _parse(r.in_time)).total_seconds() / 60
            self.assertGreaterEqual(mins, lo)
            self.assertLessEqual(mins, hi)

    def test_empty_input_returns_empty_mapping(self):
        self.assertEqual(
            schedule.assign_times([], ANCHOR, random.Random(1)), {}
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Repo/mock-mes-kr && python3 -m unittest mes_core.test_schedule -v
```

Expected: `ModuleNotFoundError: No module named 'mes_core.schedule'`

- [ ] **Step 3: 스케줄러를 구현한다**

`mes_core/schedule.py` 를 만든다.

```python
"""Deterministic wall-clock scheduling for the seeded MES dataset.

The seed's business data (which lots exist, which steps they ran, which
equipment and operator, what went wrong) comes from a shared ``Random(42)``.
Timing must NOT be drawn from that generator: inserting draws into it would
shift every subsequent value and change the dataset itself. Timing therefore
uses its own generator, seeded with ``SCHEDULE_SEED``.

This module knows nothing about SQLite. It takes planned runs, hands them
start/end timestamps that respect equipment contention, and shifts the whole
schedule so the very last run ends exactly at the anchor.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

SCHEDULE_SEED = 20260904

# Lots enter the fab this many minutes apart. Tuned so 16 lots / 91 runs span
# roughly 65 hours: shorter intervals saturate the bottleneck furnace and stop
# shortening the makespan, longer ones push it past three days.
RELEASE_INTERVAL_MIN = 240

# Inclusive minute bands per step, loosely following real fab step times.
STEP_DURATION_MIN: dict[str, tuple[int, int]] = {
    "DIFF": (90, 180),
    "PHOTO": (30, 90),
    "ETCH": (40, 120),
    "IMPL": (30, 60),
    "CVD": (60, 180),
    "CMP": (30, 60),
    "METRO": (15, 30),
    "TEST": (120, 240),
}

# Wafer transport + queue time between consecutive steps of one lot.
TRANSPORT_MIN = (10, 40)

ANCHOR_ENV = "MES_ANCHOR"


class PlannedRun(Protocol):
    lot_key: Any
    step_code: str
    eqp_id: str | None
    in_time: str
    out_time: str


def iso(dt: datetime) -> str:
    """Format like mes_core.db._now_iso: UTC, second precision, offset form."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def resolve_anchor() -> datetime:
    """The instant the newest process result finishes.

    Read from ``MES_ANCHOR`` so it is fixed at deployment time rather than
    recomputed on every cold start. A container that restarts mid-workshop
    must not move the dataset out from under a running exercise.
    """
    raw = os.environ.get(ANCHOR_ENV, "").strip()
    if not raw:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def assign_times(
    runs: Iterable[PlannedRun],
    anchor: datetime,
    sched: random.Random,
) -> dict[Any, datetime]:
    """Fill in ``in_time``/``out_time`` and return each lot's release time.

    ``runs`` must be grouped by lot, and ordered by route sequence within each
    lot. Times are computed in minutes from an arbitrary zero, then the whole
    schedule is translated so ``max(out_time) == anchor``. Translating instead
    of clamping is what keeps every run in the past without dropping any.
    """
    runs = list(runs)
    if not runs:
        return {}

    lot_order: list[Any] = []
    for run in runs:
        if run.lot_key not in lot_order:
            lot_order.append(run.lot_key)

    release = {lot: i * RELEASE_INTERVAL_MIN for i, lot in enumerate(lot_order)}
    lot_ready = dict(release)
    eqp_free: dict[str, int] = {}
    spans: list[tuple[int, int]] = []

    for run in runs:
        low, high = STEP_DURATION_MIN[run.step_code]
        duration = sched.randint(low, high)
        start = lot_ready[run.lot_key]
        if run.eqp_id is not None:
            start = max(start, eqp_free.get(run.eqp_id, 0))
            eqp_free[run.eqp_id] = start + duration
        end = start + duration
        spans.append((start, end))
        lot_ready[run.lot_key] = end + sched.randint(*TRANSPORT_MIN)

    makespan = max(end for _start, end in spans)
    for run, (start, end) in zip(runs, spans):
        run.in_time = iso(anchor - timedelta(minutes=makespan - start))
        run.out_time = iso(anchor - timedelta(minutes=makespan - end))

    return {
        lot: anchor - timedelta(minutes=makespan - minute)
        for lot, minute in release.items()
    }
```

- [ ] **Step 4: 테스트 통과를 확인한다**

```bash
cd ~/Repo/mock-mes-kr && python3 -m unittest mes_core.test_schedule -v
```

Expected: `Ran 20 tests ... OK`

- [ ] **Step 5: 커밋한다**

```bash
cd ~/Repo/mock-mes-kr
git add mes_core/schedule.py mes_core/test_schedule.py
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "설비 경합을 반영한 시드 스케줄러를 더한다

공정이력에 시간 차원을 주려면 소요시간을 뽑아야 하는데, 기존 Random(42) 에
끼워 넣으면 이후 난수가 전부 밀려 로트 수와 불량 분포까지 바뀐다. 그래서
스케줄 전용 RNG 를 따로 둔다.

스케줄을 먼저 짜고 통째로 밀어 마지막 공정이 앵커에 닿게 한다. 잘라내는
방식은 미래 시각을 없애는 대신 뒤쪽 공정을 통째로 잃는다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: DB 계층의 시각 구멍 두 개를 막는다

시드가 시각을 넘겨도 두 곳이 여전히 벽시계를 쓴다. 로트 착수일과 SEMI 자동입고일이다. 65시간 전에 끝난 로트의 입고일이 오늘로 찍히면 시간축이 어긋난다.

**Files:**
- Modify: `mes_core/db.py` (`start_lot` 약 471행, `register_process_result` 내 AUTO_FAB 약 668행)
- Test: `mes_core/test_db.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `start_lot(product_code, start_qty, priority="Normal", lot_id=None, start_date=None)` — `start_date` 는 `YYYY-MM-DD` 문자열, `None` 이면 기존대로 오늘
  - AUTO_FAB `product_result.result_date` 가 `out_time[:10]` 을 따른다

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`mes_core/test_db.py` 의 `SeedTests` 클래스 **바로 앞**(`class SeedTests` 줄 위)에 아래 클래스를 넣는다.

```python
class TimeArgumentTests(DbTestBase):
    def test_start_lot_accepts_explicit_start_date(self):
        self._seed_master()
        lot = self.db.start_lot("P1", 25, start_date="2026-08-30")
        self.assertEqual(lot["start_date"], "2026-08-30")

    def test_start_lot_defaults_to_today(self):
        self._seed_master()
        lot = self.db.start_lot("P1", 25)
        self.assertRegex(lot["start_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_process_result_keeps_supplied_times(self):
        self._seed_master()
        lot = self.db.start_lot("P1", 25)
        self.db.register_process_result(
            lot["lot_id"], "PHOTO", in_qty=25,
            in_time="2026-08-30T01:00:00+00:00",
            out_time="2026-08-30T02:00:00+00:00",
        )
        rows = self.db.list_process_results(lot_id=lot["lot_id"])
        self.assertEqual(rows[0]["in_time"], "2026-08-30T01:00:00+00:00")
        self.assertEqual(rows[0]["out_time"], "2026-08-30T02:00:00+00:00")

    def test_auto_fab_receipt_is_dated_from_out_time(self):
        """A lot that finished last week must not book its SEMI receipt today."""
        self._seed_master()
        lot = self.db.start_lot("P1", 25)
        self.db.register_process_result(
            lot["lot_id"], "PHOTO", in_qty=25,
            in_time="2026-08-30T01:00:00+00:00",
            out_time="2026-08-30T02:00:00+00:00",
        )
        self.db.register_process_result(
            lot["lot_id"], "TEST", in_qty=25, result="Pass",
            in_time="2026-08-30T03:00:00+00:00",
            out_time="2026-08-30T05:00:00+00:00",
        )
        auto = self.db.list_product_results(source="AUTO_FAB")
        self.assertEqual(len(auto), 1)
        self.assertEqual(auto[0]["result_date"], "2026-08-30")
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Repo/mock-mes-kr && python3 -m unittest mes_core.test_db.TimeArgumentTests -v
```

Expected: `test_start_lot_accepts_explicit_start_date` 는 `TypeError: start_lot() got an unexpected keyword argument 'start_date'`, `test_auto_fab_receipt_is_dated_from_out_time` 은 오늘 날짜와 `2026-08-30` 불일치로 FAIL. 나머지 둘은 PASS.

- [ ] **Step 3: `start_lot` 에 파라미터를 더한다**

`mes_core/db.py` 의 `start_lot` 시그니처와 INSERT 를 고친다. 기존:

```python
def start_lot(product_code, start_qty, priority="Normal", lot_id=None):
```

이렇게 바꾼다:

```python
def start_lot(product_code, start_qty, priority="Normal", lot_id=None, start_date=None):
```

같은 함수 안의 INSERT 값 튜플에서 마지막 인자를 바꾼다. 기존:

```python
            (lot_id, product_code, prod["tech_node"], start_qty, start_qty,
             priority, first["step_code"], "Running", _now_iso()[:10]),
```

이렇게 바꾼다:

```python
            (lot_id, product_code, prod["tech_node"], start_qty, start_qty,
             priority, first["step_code"], "Running", start_date or _now_iso()[:10]),
```

- [ ] **Step 4: AUTO_FAB 입고일을 `out_time` 에 맞춘다**

> **주의: `result_date=now[:10]` 은 `mes_core/db.py` 에 세 곳 있다.** 447행(`register_product_result`, MANUAL), 675행(`register_process_result`, AUTO_FAB), 733행(`package`, AUTO_PACK). **675행만** 바꾼다. `package` 에는 `out_time` 이라는 이름이 없어서 잘못 바꾸면 `NameError` 가 나고, MANUAL 실적은 의도적으로 벽시계를 쓴다(아래 "범위 밖으로 두는 것" 참고).

아래 네 줄을 통째로 찾는다. 이 조합은 파일에서 유일하다.

```python
                start_qty = lot["start_qty"] or (out_qty + cum_scrap)
                y = round(out_qty / start_qty * 100, 2) if start_qty else 0.0
                pr_id = _insert_product_result(
                    conn, result_date=now[:10], lot_id=lot_id, product_code=product_code,
```

마지막 줄만 바꾼다.

```python
                start_qty = lot["start_qty"] or (out_qty + cum_scrap)
                y = round(out_qty / start_qty * 100, 2) if start_qty else 0.0
                pr_id = _insert_product_result(
                    conn, result_date=out_time[:10], lot_id=lot_id, product_code=product_code,
```

`out_time` 은 함수 앞부분에서 이미 `out_time = out_time or now` 로 정규화되어 있으므로 항상 값이 있다.

고친 뒤 나머지 두 곳이 남아 있는지 확인한다.

```bash
cd ~/Repo/mock-mes-kr && grep -c "result_date=now\[:10\]" mes_core/db.py
```

Expected: `2`

- [ ] **Step 5: 테스트 통과를 확인한다**

```bash
cd ~/Repo/mock-mes-kr && python3 -m unittest mes_core.test_db api.tests.test_app -v 2>&1 | tail -5
```

Expected: `Ran 70 tests ... OK` (기존 66 + 신규 4). `SeedTests` 의 91/16/6/69/277/10 이 모두 그대로 통과해야 한다.

- [ ] **Step 6: 커밋한다**

```bash
cd ~/Repo/mock-mes-kr
git add mes_core/db.py mes_core/test_db.py
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "로트 착수일과 SEMI 입고일이 벽시계를 벗어나게 한다

공정이력에 과거 시각을 넣어도 이 둘은 여전히 오늘로 찍혀서, 지난주에 끝난
로트가 오늘 입고된 것처럼 보였다.

register_process_result 가 in_time/out_time 을 받는 것과 같은 방식으로
start_lot 에 start_date 를 더하고, AUTO_FAB 입고일은 해당 공정의 out_time 을
따르게 한다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: 시드를 2단계로 재편한다

이 계획의 핵심이다. **RNG 소비 순서를 한 톨도 바꾸지 않는 것**이 성공 조건이다.

지금은 로트를 하나 만들고 곧바로 공정을 등록한다. 그래서 설비 경합을 계산할 전역 정보가 그 시점에 없다. 계획 수집과 등록을 분리하면 그 사이에 스케줄을 넣을 수 있다.

**중요한 세부:** 등록을 `out_time` 오름차순으로 해야 한다. `mes_core/db.py:608` `list_process_results` 와 대시보드가 `ORDER BY pr.id DESC` 로 "최근 공정"을 뽑기 때문에, 삽입 순서가 시간 순서와 어긋나면 콘솔의 최근 목록이 타임스탬프와 모순된다.

**Files:**
- Modify: `mes_core/seed.py` (`_advance` 132–152행 부근, `seed` 154–187행 부근)
- Test: `mes_core/test_db.py`

**Interfaces:**
- Consumes: Task 1 의 `mes_core.schedule` (`SCHEDULE_SEED`, `resolve_anchor`, `assign_times`), Task 2 의 `db.start_lot(start_date=...)`
- Produces: `seed()` 시그니처 불변 (`() -> None`)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`mes_core/test_db.py` 의 `SeedTests` 클래스 **안**, 기존 메서드들 뒤에 아래를 붙인다.

```python
    # --- time axis ---------------------------------------------------------

    def _results(self):
        rows = self.db.list_process_results(limit=500)
        self.assertEqual(len(rows), 91)
        return rows

    def test_process_results_have_distinct_in_and_out_times(self):
        for r in self._results():
            self.assertLess(r["in_time"], r["out_time"], r["lot_id"] + r["step_code"])

    def test_process_results_span_more_than_two_days(self):
        rows = self._results()
        lo = min(r["in_time"] for r in rows)
        hi = max(r["out_time"] for r in rows)
        span_h = (
            datetime.fromisoformat(hi) - datetime.fromisoformat(lo)
        ).total_seconds() / 3600
        self.assertGreater(span_h, 48)
        self.assertLess(span_h, 96)

    def test_no_process_result_is_in_the_future(self):
        now = datetime.now(timezone.utc).isoformat()
        for r in self._results():
            self.assertLessEqual(r["out_time"], now)

    def test_ids_are_assigned_in_chronological_order(self):
        """The console lists 'recent' results by id DESC, so id must track time."""
        rows = sorted(self._results(), key=lambda r: r["id"])
        times = [r["out_time"] for r in rows]
        self.assertEqual(times, sorted(times))

    def test_steps_within_a_lot_run_in_route_order(self):
        by_lot = {}
        for r in self._results():
            by_lot.setdefault(r["lot_id"], []).append(r)
        for lot_id, rows in by_lot.items():
            rows.sort(key=lambda r: r["id"])
            for prev, cur in zip(rows, rows[1:]):
                self.assertLessEqual(prev["out_time"], cur["in_time"], lot_id)

    def test_one_equipment_never_runs_two_lots_at_once(self):
        by_eqp = {}
        for r in self._results():
            if r["eqp_id"]:
                by_eqp.setdefault(r["eqp_id"], []).append(r)
        for eqp_id, rows in by_eqp.items():
            rows.sort(key=lambda r: r["in_time"])
            for prev, cur in zip(rows, rows[1:]):
                self.assertLessEqual(prev["out_time"], cur["in_time"], eqp_id)

    def test_lot_start_date_precedes_its_first_step(self):
        by_lot = {}
        for r in self._results():
            by_lot.setdefault(r["lot_id"], []).append(r)
        for lot in self.db.list_lots(limit=100):
            rows = by_lot.get(lot["lot_id"])
            if not rows:
                continue
            first_in = min(r["in_time"] for r in rows)
            self.assertLessEqual(lot["start_date"], first_in[:10], lot["lot_id"])

    def test_lot_start_dates_are_not_all_the_same_day(self):
        days = {l["start_date"] for l in self.db.list_lots(limit=100)}
        self.assertGreater(len(days), 1)

    def test_auto_fab_receipts_follow_their_lots(self):
        finals = {}
        for r in self._results():
            if r["step_code"] == "TEST":
                finals[r["lot_id"]] = r["out_time"][:10]
        auto = self.db.list_product_results(source="AUTO_FAB", limit=100)
        self.assertEqual(len(auto), 6)
        for row in auto:
            self.assertEqual(row["result_date"], finals[row["lot_id"]])
```

`mes_core/test_db.py` 상단 import 에 `datetime` 을 더한다. 기존:

```python
import importlib
import os
import unittest
from pathlib import Path
```

이렇게 바꾼다:

```python
import importlib
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd ~/Repo/mock-mes-kr && python3 -m unittest mes_core.test_db.SeedTests -v 2>&1 | tail -20
```

Expected: `test_process_results_have_distinct_in_and_out_times` 등 시간축 테스트가 FAIL (현재는 `in_time == out_time` 이고 전 구간이 같은 순간). 기존 `test_master_counts` 등은 PASS.

- [ ] **Step 3: `_advance` 를 수집 함수로 바꾼다**

`mes_core/seed.py` 상단 import 를 고친다. 기존:

```python
from __future__ import annotations

import random

from . import db
```

이렇게 바꾼다:

```python
from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import db, schedule
```

`_scrap_for` 함수와 `_insert_master` 사이(즉 `_insert_master` 정의 바로 앞)에 데이터 클래스를 넣는다.

```python
@dataclass
class PlannedRun:
    """One process result, decided but not yet written or timed."""

    lot_key: int
    step_code: str
    eqp_id: str | None
    in_qty: int
    scrap_qty: int
    defect_code: str | None
    operator: str
    result: str
    in_time: str = field(default="")
    out_time: str = field(default="")
```

기존 `_advance` 를 통째로 아래로 바꾼다. 기존:

```python
def _advance(lot_id: str, upto_idx: int, rnd: random.Random) -> None:
    """Move a lot through FAB_STEPS[0..upto_idx] inclusive, with occasional scrap."""
    for idx in range(upto_idx + 1):
        step = FAB_STEPS[idx]
        lot = db.get_lot(lot_id)
        in_qty = lot["wafer_qty"]
        scrap = _scrap_for(step, in_qty, rnd)
        defect = rnd.choice(DEFECTS) if scrap > 0 else None
        result = "Pass" if rnd.random() > 0.05 else rnd.choice(["Rework", "Fail"])
        # ensure the final TEST step passes so Done lots produce SEMI
        if step == "TEST":
            result = "Pass"
        db.register_process_result(
            lot_id, step, eqp_id=_pick_eqp(step, rnd), in_qty=in_qty,
            scrap_qty=scrap, defect_code=defect,
            operator=rnd.choice(OPERATORS), result=result,
        )
```

새 버전:

```python
def _plan_advance(lot_key: int, upto_idx: int, start_qty: int,
                  rnd: random.Random, out: list[PlannedRun]) -> None:
    """Decide FAB_STEPS[0..upto_idx] for one lot without touching the DB.

    Draw order from ``rnd`` must stay byte-identical to the previous
    register-as-you-go version: scrap, defect, pass roll, rework pick,
    equipment, operator. Anything else changes the whole dataset.

    ``wafer_qty`` used to come back from the DB after each write; the DB set it
    to ``in_qty - scrap_qty``, so tracking it locally is exact.
    """
    qty = start_qty
    for idx in range(upto_idx + 1):
        step = FAB_STEPS[idx]
        in_qty = qty
        scrap = _scrap_for(step, in_qty, rnd)
        defect = rnd.choice(DEFECTS) if scrap > 0 else None
        result = "Pass" if rnd.random() > 0.05 else rnd.choice(["Rework", "Fail"])
        # ensure the final TEST step passes so Done lots produce SEMI
        if step == "TEST":
            result = "Pass"
        out.append(PlannedRun(
            lot_key=lot_key, step_code=step, eqp_id=_pick_eqp(step, rnd),
            in_qty=in_qty, scrap_qty=scrap, defect_code=defect,
            operator=rnd.choice(OPERATORS), result=result,
        ))
        qty = in_qty - scrap
```

- [ ] **Step 4: `seed()` 를 2단계로 바꾼다**

기존 `seed()` 를 통째로 아래로 바꾼다. 기존:

```python
def seed() -> None:
    rnd = random.Random(42)
    db.reset_db()
    _insert_master()

    done_products: list[str] = []
    for i in range(1, 17):
        pcode, _pn, _tn = PRODUCTS[(i - 1) % len(PRODUCTS)]
        start_qty = rnd.choice([25, 25, 25, 24, 12])
        priority = rnd.choice(PRIORITIES)
        lot = db.start_lot(pcode, start_qty, priority=priority)
        lot_id = lot["lot_id"]
        if i <= 6:                       # Done: full FAB route (TEST passes -> SEMI)
            _advance(lot_id, len(FAB_STEPS) - 1, rnd)
            done_products.append(pcode)
        elif i <= 8:                     # Hold: partway then held
            _advance(lot_id, rnd.randint(1, 5), rnd)
            with db.get_conn() as conn:
                conn.execute("UPDATE lot SET status='Hold' WHERE lot_id=?", (lot_id,))
        else:                            # Running: partway
            _advance(lot_id, rnd.randint(0, 6), rnd)

    # Package 3 of the completed products (consume SEMI -> FIN)
    for pcode in done_products[:3]:
        semi = db.list_product_inventory(product_code=pcode, item_type="SEMI")
        avail = int(semi[0]["qty"]) if semi else 0
        if avail > 0:
            db.package(pcode, max(1, avail // 2), scrap_qty=rnd.randint(0, 1),
                       eqp_id="EQP-PKG01", operator=rnd.choice(OPERATORS))

    # One manual FIN result against a Done lot
    done_lots = db.list_lots(status="Done")
    if done_lots:
        db.register_product_result(done_lots[0]["lot_id"], "FIN", rnd.randint(100, 400))
```

새 버전:

```python
def seed() -> None:
    """Build the dataset in three passes.

    1. Decide everything, drawing from ``rnd`` in exactly the historical order,
       but write nothing except the fact that a lot exists is deferred too.
    2. Lay the decisions out on a clock using a separate generator, so the
       makespan ends at the anchor and equipment never double-books.
    3. Replay the decisions into the real transaction functions, oldest first,
       so row ids track time the way the console assumes they do.

    Pass 2 draws no numbers from ``rnd``, so the ``rnd`` sequence -- and hence
    the 91 results, 16 lots, defect mix and yields -- is unchanged.
    """
    rnd = random.Random(42)
    sched = random.Random(schedule.SCHEDULE_SEED)
    anchor = schedule.resolve_anchor()

    db.reset_db()
    _insert_master()

    # --- pass 1: decide ----------------------------------------------------
    lot_specs: list[tuple[str, int, str]] = []   # product_code, start_qty, priority
    planned: list[PlannedRun] = []
    hold_keys: set[int] = set()
    done_products: list[str] = []

    for i in range(1, 17):
        pcode, _pn, _tn = PRODUCTS[(i - 1) % len(PRODUCTS)]
        start_qty = rnd.choice([25, 25, 25, 24, 12])
        priority = rnd.choice(PRIORITIES)
        lot_specs.append((pcode, start_qty, priority))
        if i <= 6:                       # Done: full FAB route (TEST passes -> SEMI)
            _plan_advance(i, len(FAB_STEPS) - 1, start_qty, rnd, planned)
            done_products.append(pcode)
        elif i <= 8:                     # Hold: partway then held
            _plan_advance(i, rnd.randint(1, 5), start_qty, rnd, planned)
            hold_keys.add(i)
        else:                            # Running: partway
            _plan_advance(i, rnd.randint(0, 6), start_qty, rnd, planned)

    # --- pass 2: schedule --------------------------------------------------
    released = schedule.assign_times(planned, anchor, sched)

    # --- pass 3: write -----------------------------------------------------
    lot_ids: dict[int, str] = {}
    for i, (pcode, start_qty, priority) in enumerate(lot_specs, start=1):
        start_date = released.get(i, anchor)
        lot = db.start_lot(pcode, start_qty, priority=priority,
                           start_date=schedule.iso(start_date)[:10])
        lot_ids[i] = lot["lot_id"]

    for run in sorted(planned, key=lambda r: r.out_time):
        db.register_process_result(
            lot_ids[run.lot_key], run.step_code, eqp_id=run.eqp_id,
            in_qty=run.in_qty, scrap_qty=run.scrap_qty,
            defect_code=run.defect_code, operator=run.operator,
            result=run.result, in_time=run.in_time, out_time=run.out_time,
        )

    for key in sorted(hold_keys):
        with db.get_conn() as conn:
            conn.execute("UPDATE lot SET status='Hold' WHERE lot_id=?", (lot_ids[key],))

    # Package 3 of the completed products (consume SEMI -> FIN)
    for pcode in done_products[:3]:
        semi = db.list_product_inventory(product_code=pcode, item_type="SEMI")
        avail = int(semi[0]["qty"]) if semi else 0
        if avail > 0:
            db.package(pcode, max(1, avail // 2), scrap_qty=rnd.randint(0, 1),
                       eqp_id="EQP-PKG01", operator=rnd.choice(OPERATORS))

    # One manual FIN result against a Done lot
    done_lots = db.list_lots(status="Done")
    if done_lots:
        db.register_product_result(done_lots[0]["lot_id"], "FIN", rnd.randint(100, 400))
```

> **왜 Hold 를 뒤로 미뤄도 되나.** `register_process_result` 는 마지막 FAB 스텝을 통과할 때만 상태를 바꾸고 그 외에는 `new_status = prev_status` 로 둔다. Hold 로트는 `rnd.randint(1, 5)` 까지만 진행해 TEST(인덱스 7)에 닿지 않으므로, 등록을 모두 마친 뒤 Hold 를 찍어도 최종 상태는 같다.

> **왜 `rnd` 순서가 보존되나.** 2단계는 `sched` 만 쓴다. 3단계는 난수를 전혀 쓰지 않는다. 포장·수동 실적 블록은 예전과 같은 위치에서 같은 순서로 `rnd` 를 소비한다.

- [ ] **Step 5: 테스트 통과를 확인한다**

```bash
cd ~/Repo/mock-mes-kr && python3 -m unittest mes_core.test_db mes_core.test_schedule api.tests.test_app 2>&1 | tail -5
```

Expected: `Ran 99 tests ... OK`

**`SeedTests.test_master_counts` 가 91/16 을 그대로 통과하는지 반드시 확인한다.** 여기서 숫자가 틀리면 RNG 분리가 깨진 것이므로, 기대값을 고치지 말고 draw 순서를 다시 맞춘다.

- [ ] **Step 6: 실제 시드를 돌려 눈으로 확인한다**

```bash
cd ~/Repo/mock-mes-kr && MES_DB_PATH=/tmp/seedcheck.db MES_ANCHOR=2026-09-04T00:00:00Z python3 -m mes_core.seed
```

Expected: `process_result=91, lot=16` 을 포함한 행 수 출력.

```bash
cd ~/Repo/mock-mes-kr && python3 - <<'PY'
import os, sqlite3
os.environ["MES_DB_PATH"] = "/tmp/seedcheck.db"
c = sqlite3.connect("/tmp/seedcheck.db"); c.row_factory = sqlite3.Row
lo, hi, n = c.execute(
    "SELECT MIN(in_time), MAX(out_time), COUNT(*) FROM process_result").fetchone()
print("runs:", n, "\nfrom:", lo, "\nto:  ", hi)
print("distinct out_time:", c.execute(
    "SELECT COUNT(DISTINCT out_time) FROM process_result").fetchone()[0])
print("\n최근 5건 (id DESC == 시간 역순이어야 한다)")
for r in c.execute("SELECT id, lot_id, step_code, eqp_id, out_time"
                   " FROM process_result ORDER BY id DESC LIMIT 5"):
    print(" ", dict(r))
print("\n로트 착수일")
for r in c.execute("SELECT lot_id, status, start_date FROM lot ORDER BY lot_id"):
    print(" ", r["lot_id"], r["status"], r["start_date"])
PY
```

Expected: `to:` 가 정확히 `2026-09-04T00:00:00+00:00`, `runs: 91`, `distinct out_time: 91`, 최근 5건의 `out_time` 이 내림차순, 착수일이 여러 날에 걸침.

```bash
rm -f /tmp/seedcheck.db*
```

- [ ] **Step 7: 커밋한다**

```bash
cd ~/Repo/mock-mes-kr
git add mes_core/seed.py mes_core/test_db.py
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "시드가 공정이력을 65시간에 걸쳐 흩뿌리게 한다

91건이 전부 같은 2초에 몰려 있어서 '어제 오후 에처에서 무슨 일이 있었나'
같은 질문이 성립하지 않았다.

로트를 만들자마자 공정을 등록하면 설비 경합을 계산할 전역 정보가 없다.
그래서 결정·스케줄·기록을 세 단계로 나눈다. 결정 단계는 기존 Random(42) 를
예전과 똑같은 순서로 소비하므로 로트 16개, 공정 91건, 불량 분포가 그대로다.

기록은 out_time 오름차순으로 한다. 콘솔의 '최근 공정'이 id DESC 로 뽑히기
때문에, 삽입 순서가 시간 순서와 어긋나면 목록이 타임스탬프와 모순된다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: 배포 시점에 앵커를 고정한다

컨테이너는 `minReplicas: 0` 이라 유휴 시 0으로 줄고, 저장소는 `EmptyDir` 이라 콜드스타트마다 시드가 다시 돈다. 앵커를 런타임에 계산하면 실습 도중 재시작될 때 데이터가 통째로 움직인다. 배포 시각을 env 로 굳혀서 재시작이 데이터를 바꾸지 못하게 한다.

**Files:**
- Modify: `infra/main.bicep`

**Interfaces:**
- Consumes: Task 1 의 `MES_ANCHOR` 환경변수 규약
- Produces: 시드 init 컨테이너에 `MES_ANCHOR` 주입

시드 init 컨테이너는 `dbEnv` 를 그대로 쓰고, `appEnv = concat(dbEnv, [apiKey])` 이므로 `dbEnv` 한 곳만 고치면 세 컨테이너 모두 받는다. 앵커를 읽는 것은 시드뿐이지만 나머지가 받아도 무해하다.

- [ ] **Step 1: 파라미터와 env 를 한 번에 고친다**

`infra/main.bicep` 에서 `apiKey` 파라미터와 `dbEnv` 변수를 찾는다. 기존:

```bicep
@description('Demo API key required in the X-API-Key header on all REST /api/* and MCP /mcp calls. Web console + /api/docs stay open.')
param apiKey string = 'changjuahn'

var dbPath = '/data/mes.db'
var dbEnv = [
  {
    name: 'MES_DB_PATH'
    value: dbPath
  }
]
```

이렇게 바꾼다:

```bicep
@description('Demo API key required in the X-API-Key header on all REST /api/* and MCP /mcp calls. Web console + /api/docs stay open.')
param apiKey string = 'changjuahn'

@description('Instant the newest seeded process result finishes (UTC, ISO 8601). The /data volume is EmptyDir and the app scales to zero, so the seed re-runs on every cold start; pinning the anchor at deploy time keeps a restart from shifting the dataset out from under a workshop in progress. Redeploy to move the data forward.')
param mesAnchor string = utcNow('yyyy-MM-ddTHH:mm:ssZ')

var dbPath = '/data/mes.db'
var dbEnv = [
  {
    name: 'MES_DB_PATH'
    value: dbPath
  }
  {
    name: 'MES_ANCHOR'
    value: mesAnchor
  }
]
```

> `utcNow()` 는 파라미터 기본값에서만 쓸 수 있고 배포 시 한 번 평가된다. 이 파일의 `revisionSuffix` 가 이미 같은 방식을 쓴다.

- [ ] **Step 2: 빌드를 검증한다**

```bash
cd ~/Repo/mock-mes-kr && az bicep build --file infra/main.bicep --stdout > /dev/null && echo BICEP_OK
```

Expected: `BICEP_OK`. `az` 가 없으면 이 단계를 건너뛴다.

- [ ] **Step 3: 커밋한다**

```bash
cd ~/Repo/mock-mes-kr
git add infra/main.bicep
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "앵커를 배포 시점에 고정한다

컨테이너가 0으로 줄었다가 다시 뜨면 EmptyDir 이라 시드가 다시 돈다. 앵커를
런타임에 계산하면 실습 도중 데이터가 통째로 움직여서, 참가자가 방금 본 로트가
사라진 것처럼 보인다.

utcNow() 는 파라미터 기본값에서 배포 시 한 번만 평가되므로 이 용도에 맞는다.
데이터를 최신으로 밀려면 재배포한다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 5: 문서를 갱신한다

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1–4
- Produces: 없음

- [ ] **Step 1: 테스트 명령을 고친다**

`README.md` 241행 부근의 실행 명령을 찾는다. 기존:

```
python -m unittest mes_core.test_db api.tests.test_app mcp_server.test_server
```

이렇게 바꾼다:

```
python -m unittest mes_core.test_db mes_core.test_schedule api.tests.test_app mcp_server.test_server
```

- [ ] **Step 2: 시간축 절을 더한다**

`README.md` 의 데이터 개요 표(307행 부근 "Process steps | 9 ...") 근처, 시드 데이터를 설명하는 절에 아래를 넣는다.

```markdown
### Time axis

Seeded process results are not stamped with "now". They are laid out over
roughly 65 hours ending at an **anchor**, so the dataset has a usable time
dimension: 16 lots enter the fab 4 hours apart, each step takes a plausible
number of minutes, and a given tool never runs two lots at once.

| | |
|---|---|
| Anchor | `MES_ANCHOR` env var (UTC ISO 8601). Unset → current time. |
| Span | ~65 h before the anchor |
| Release interval | 240 min between lots |
| Ordering | rows are inserted oldest-first, so `id` tracks time |

The anchor is fixed at **deployment** time, not at container start. The
container scales to zero and its storage is ephemeral, so it reseeds on every
cold start — recomputing the anchor there would shift the whole dataset out
from under a workshop in progress.

**The data ages.** A month after deploying, the newest process result is a
month old. Redeploy to move it forward:

```bash
az deployment group create -g <rg> -f infra/main.bicep
```

Pass `mesAnchor` explicitly to pin it for a reproducible demo:

```bash
az deployment group create -g <rg> -f infra/main.bicep -p mesAnchor=2026-09-04T00:00:00Z
```

Timing uses its own random seed, separate from the one that decides lots,
defects and yields. That separation is deliberate: it keeps the dataset
(16 lots, 91 process results, the defect mix) byte-identical to earlier
releases so downstream demos that hardcode those numbers keep working.
```

- [ ] **Step 3: 커밋한다**

```bash
cd ~/Repo/mock-mes-kr
git add README.md
git -c user.name="Copilot App" -c user.email="223556219+Copilot@users.noreply.github.com" \
  commit -m "시간축과 앵커를 문서에 적는다

데이터가 나이를 먹는다는 사실과 재배포로 갱신한다는 점을 명시한다.

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 6: 전체 검증과 PR

**Files:**
- 없음 (검증만)

**Interfaces:**
- Consumes: Task 1–5

- [ ] **Step 1: 전체 테스트를 돌린다**

```bash
cd ~/Repo/mock-mes-kr && python3 -m unittest mes_core.test_db mes_core.test_schedule api.tests.test_app 2>&1 | tail -5
```

Expected: `Ran 99 tests ... OK`

- [ ] **Step 2: 결정성을 확인한다**

같은 앵커로 두 번 시드해 완전히 같은 DB 가 나오는지 본다.

```bash
cd ~/Repo/mock-mes-kr && for i in 1 2; do
  MES_DB_PATH=/tmp/det$i.db MES_ANCHOR=2026-09-04T00:00:00Z python3 -m mes_core.seed > /dev/null
  python3 - <<PY
import sqlite3, hashlib
c = sqlite3.connect("/tmp/det$i.db")
rows = c.execute("SELECT lot_id, step_code, eqp_id, in_qty, out_qty, scrap_qty,"
                 " defect_code, in_time, out_time, operator, result"
                 " FROM process_result ORDER BY id").fetchall()
print("$i", hashlib.sha256(repr(rows).encode()).hexdigest()[:16])
PY
done; rm -f /tmp/det1.db* /tmp/det2.db*
```

Expected: 두 해시가 동일.

- [ ] **Step 3: 앵커가 실제로 먹히는지 확인한다**

```bash
cd ~/Repo/mock-mes-kr && for a in 2026-09-04T00:00:00Z 2026-10-01T12:00:00Z; do
  MES_DB_PATH=/tmp/anc.db MES_ANCHOR=$a python3 -m mes_core.seed > /dev/null
  python3 -c "
import sqlite3
print('$a ->', sqlite3.connect('/tmp/anc.db').execute(
    'SELECT MAX(out_time) FROM process_result').fetchone()[0])"
  rm -f /tmp/anc.db*
done
```

Expected:

```
2026-09-04T00:00:00Z -> 2026-09-04T00:00:00+00:00
2026-10-01T12:00:00Z -> 2026-10-01T12:00:00+00:00
```

- [ ] **Step 4: 임시 파일이 남지 않았는지 본다**

```bash
cd ~/Repo/mock-mes-kr && git status --short && ls /tmp/det*.db /tmp/anc.db /tmp/seedcheck.db 2>&1 | tail -1
```

Expected: `git status` 가 깨끗하고, `/tmp` 파일들은 `No such file or directory`.

- [ ] **Step 5: PR 을 올린다**

```bash
cd ~/Repo/mock-mes-kr && git --no-pager log --oneline origin/main..HEAD
```

Expected: 5개 커밋 (스케줄러 / DB 시각 / 시드 재편 / Bicep 앵커 / 문서).

푸시하고 PR 을 만든다. 제목과 본문:

- 제목: `공정이력에 시간 차원을 준다`
- 본문에 반드시 포함할 것:
  - 문제: 91건이 같은 2초에 몰려 있어 시간 기반 질문이 성립하지 않는다
  - RNG 를 분리한 이유와, `SeedTests` 의 91/16/6/69/277/10 이 그대로 통과한다는 사실
  - 삽입 순서를 시간 순으로 맞춘 이유 (`ORDER BY pr.id DESC`)
  - 앵커가 배포 시점에 고정된다는 점과 재배포로 갱신한다는 점
  - **머지 후 재배포가 필요하다**는 점 (다운스트림이 새 데이터를 기다린다)

- [ ] **Step 6: 재배포하고 결과를 확인한다**

머지 후:

```bash
az deployment group create -g <rg> -f infra/main.bicep
```

배포가 끝나면 API 로 확인한다.

```bash
curl -s -H "X-API-Key: $MES_API_KEY" \
  "$MES_BASE_URL/api/process-results?limit=3" | python3 -m json.tool
```

Expected: 서로 다른 `in_time`/`out_time`, 가장 최근 건이 배포 시각 근처.

---

## 범위 밖으로 두는 것

**포장(`db.package`)과 수동 실적(`register_product_result`)은 벽시계를 그대로 쓴다.** 이것은 누락이 아니라 판단이다. FAB 이 65시간 전에 끝난 SEMI 가 창고에 있다가 오늘 포장되는 것은 현실적이다. 반면 SEMI 자동입고는 TEST 통과와 같은 트랜잭션에서 일어나므로 반드시 같은 시각이어야 한다 — 그래서 그것만 고친다.

`process_step.stage='PACK'` 공정을 시간축에 올리려면 별도의 후공정 스케줄이 필요한데, 이번 목표(설비 센서 텔레메트리와 공정이력을 시간으로 엮기)에 기여하지 않는다.

---

## 완료 조건

- [ ] `python3 -m unittest mes_core.test_db mes_core.test_schedule api.tests.test_app` 100건 통과
- [ ] `SeedTests` 의 91 / 16 / 6 / 69 / 277 / 10 이 **수정 없이** 통과
- [ ] 같은 앵커로 두 번 시드하면 공정이력 해시가 동일
- [ ] `MAX(out_time) == MES_ANCHOR`
- [ ] 91건의 `out_time` 이 모두 고유하고, `id` 순서와 시간 순서가 일치
- [ ] 한 설비가 동시에 두 로트를 돌리지 않음
- [ ] 로트 착수일이 여러 날에 걸침
- [ ] PR 머지 후 재배포 완료, API 응답에 흩뿌려진 시각이 보임

## 다음 단계 (이 계획 밖)

재배포가 끝나면 `iq-series` 쪽에서:

1. `customizing/fabric/fdc-eventhouse/tests/fixtures/mes_facts.json` 재생성
2. FDC 노트북 재설계 (스펙 §5) — 24시간 캡 제거, `lot_id` 컬럼, 유휴 공통센서 2종
3. 별도 계획서로 진행한다