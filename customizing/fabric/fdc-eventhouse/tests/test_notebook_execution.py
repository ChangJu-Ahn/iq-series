"""노트북 셀을 실제로 실행해 본다.

`test_build_notebook.py` 51개는 셀 **소스**를 검사한다. 문자열이 들어 있는지,
AST 로 파싱되는지, 모듈이 쓰이기 전에 정의됐는지. 전부 필요하지만 셀을 한 번도
실행하지 않는다. 그래서 구문은 맞고 실행이 깨지는 종류를 놓친다 — 셀 사이로
넘어가는 이름이 어긋났다든지, 예외 처리가 의도한 갈래로 안 간다든지.

리뷰 다섯 라운드가 전부 "에러가 안 나는 실패" 였다. 이건 그 반대다. 에러는
나는데 **참가자 20명 앞에서** 난다. 이 모듈의 존재 이유가 "각자 자기 Fabric 에서
이 노트북을 돌린다" 인 만큼, 안 돌아가는 노트북이 최악의 실패 모드다.

Fabric 런타임이 주는 것(`spark`, `mssparkutils`)과 바깥 세계(MES HTTP, Kusto)를
대역으로 세우고 코드 셀을 순서대로 exec 한다. 대역은 **얇게** 만든다. 두꺼워지면
대역의 동작을 검사하게 되고, 그건 3차 리뷰에서 PySpark 를 복제하다 걸린 함정과
같은 것이다. 여기서 검사하는 것은 오직 "셀이 서로 맞물려 끝까지 도는가" 다.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.fdc_schema import READING_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "fdc_eventhouse_stream.ipynb"
FIXTURE = Path(__file__).parent / "fixtures" / "mes_facts.json"


# --------------------------------------------------------------------------
# Fabric 런타임 대역
# --------------------------------------------------------------------------


class FakeRow(dict):
    """Kusto 결과 한 행. 셀은 `row["last_ts"]` 로만 읽는다."""


class FakeResult:
    def __init__(self, rows: list[FakeRow]):
        self._rows = rows

    def collect(self) -> list[FakeRow]:
        return self._rows


class FakeReader:
    def __init__(self, kusto: "FakeKusto"):
        self._kusto = kusto
        self._options: dict[str, str] = {}

    def format(self, _fmt):
        return self

    def option(self, key, value):
        self._options[key] = value
        return self

    def load(self):
        return self._kusto.query(self._options["kustoQuery"])


class FakeWriter:
    def __init__(self, kusto: "FakeKusto", rows: list):
        self._kusto = kusto
        self._rows = rows
        self._options: dict[str, str] = {}
        self._mode: str | None = None

    def format(self, _fmt):
        return self

    def option(self, key, value):
        self._options[key] = value
        return self

    def mode(self, mode):
        self._mode = mode
        return self

    def save(self):
        self._kusto.write(self._options["kustoTable"], self._rows, self._options, self._mode)


class FakeFrame:
    def __init__(self, kusto: "FakeKusto", rows: list, schema):
        self._kusto = kusto
        self._rows = rows
        self.schema = schema

    def count(self) -> int:
        return len(self._rows)

    @property
    def write(self) -> FakeWriter:
        return FakeWriter(self._kusto, self._rows)


class FakeSpark:
    def __init__(self, kusto: "FakeKusto"):
        self._kusto = kusto

    @property
    def read(self) -> FakeReader:
        return FakeReader(self._kusto)

    def createDataFrame(self, rows, schema=None):  # noqa: N802 - PySpark 이름
        return FakeFrame(self._kusto, list(rows), schema)


class FakeKusto:
    """테이블 두 개짜리 인메모리 Eventhouse.

    쿼리는 파싱하지 않는다. 노트북이 보내는 것은 `watermark_query()` 와
    `spec_count_query()` 둘뿐이라 어느 테이블을 겨냥했는지만 본다. KQL 문법을
    흉내 내기 시작하면 대역이 곧 두 번째 구현이 되고, 그때부터는 대역의 버그와
    노트북의 버그를 구별할 수 없다.
    """

    def __init__(self, *, tables: dict[str, list] | None = None, read_fails: bool = False):
        self.tables: dict[str, list] = tables if tables is not None else {}
        self.read_fails = read_fails
        self.writes: list[tuple[str, int, dict, str | None]] = []

    def query(self, kql: str) -> FakeResult:
        if self.read_fails:
            raise RuntimeError("kusto 가 응답하지 않습니다 (토큰 만료를 흉내 냅니다)")

        if "last_ts" in kql:
            rows = self.tables.get("fdc_sensor_reading", [])
            if not rows:
                # datatable 레그가 null 한 행을 낸다.
                return FakeResult([FakeRow(last_ts=None, last_run_ts=None)])
            # `maxif(reading_ts, run_status == "Run")` 에 대응한다. KQL 은
            # 파싱하지 않는다 -- 대역이 두 번째 구현이 되면 대역의 버그와
            # 노트북의 버그를 구별할 수 없다.
            running = [r["reading_ts"] for r in rows if r["run_status"] == "Run"]
            return FakeResult([FakeRow(
                last_ts=max(r["reading_ts"] for r in rows),
                last_run_ts=max(running) if running else None,
            )])

        if "rows" in kql or "count()" in kql:
            return FakeResult([FakeRow(rows=len(self.tables.get("fdc_sensor_spec", [])))])

        raise AssertionError(f"대역이 모르는 쿼리입니다: {kql[:120]}")

    def write(self, table: str, rows: list, options: dict, mode: str | None) -> None:
        self.writes.append((table, len(rows), dict(options), mode))
        self.tables.setdefault(table, [])
        if table == "fdc_sensor_reading":
            # 인덱스를 박지 않는다. READING_COLUMNS 순서가 바뀌면 대역이
            # 조용히 엉뚱한 컬럼을 읽는다.
            self.tables[table].extend(dict(zip(READING_COLUMNS, row)) for row in rows)
        else:
            self.tables[table].extend({} for _ in rows)


class FakeCredentials:
    def getToken(self, _uri):  # noqa: N802 - mssparkutils 이름
        return "fake-token"


class FakeMsSparkUtils:
    credentials = FakeCredentials()


# --------------------------------------------------------------------------
# 노트북 실행
# --------------------------------------------------------------------------


def _code_cells() -> list[tuple[str, str]]:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return [
        (cell.get("id", "?"), "".join(cell["source"]))
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    ]


def run_notebook(kusto: FakeKusto, *, now: datetime | None = None) -> tuple[dict, str]:
    """코드 셀을 순서대로 exec 하고 (네임스페이스, 표준출력) 을 준다.

    파라미터 셀 다음에 자격 증명을 채우고, 모듈 셀 다음에 MES 프로브를 픽스처로
    바꾼다. 그 두 지점 말고는 노트북 그대로다.
    """
    facts_json = json.loads(FIXTURE.read_text(encoding="utf-8"))
    namespace: dict = {"spark": FakeSpark(kusto), "mssparkutils": FakeMsSparkUtils()}
    out = io.StringIO()

    with redirect_stdout(out):
        for cell_id, source in _code_cells():
            if cell_id == "fdc-watermark" and now is not None:
                # NOW 를 고정한다. 픽스처 구간이 과거로 흘러가도 테스트가 흔들리지
                # 않게 하려는 것이고, 셀의 다른 줄은 건드리지 않는다.
                source = re.sub(
                    r"^NOW = datetime\.now\(timezone\.utc\)$",
                    f"NOW = datetime.fromisoformat({now.isoformat()!r})",
                    source,
                    count=1,
                    flags=re.MULTILINE,
                )
                assert "datetime.fromisoformat" in source, "NOW 주입 지점이 사라졌습니다"

            exec(compile(source, f"<{cell_id}>", "exec"), namespace)

            if cell_id == "fdc-parameters":
                namespace["KUSTO_URI"] = "https://fake.kusto.fabric.microsoft.com"
                namespace["KUSTO_DATABASE"] = "fdc"
                namespace["MES_API_KEY"] = "fake-key"

            if cell_id == "fdc-module-mes-probe":
                mes_facts = namespace["MesFacts"]

                class FixtureProbe:
                    def __init__(self, *_args, **_kwargs):
                        pass

                    def fetch_facts(self):
                        return mes_facts.from_dict(facts_json)

                namespace["MesProbe"] = FixtureProbe

    return namespace, out.getvalue()


@pytest.fixture(scope="module")
def mes_span() -> tuple[datetime, datetime]:
    """픽스처가 덮는 구간. 시나리오의 NOW 를 여기서 잡는다."""
    import sys

    sys.path.insert(0, str(ROOT))
    from src.fdc_runs import span
    from src.mes_probe import MesFacts

    with FIXTURE.open(encoding="utf-8") as fh:
        return span(MesFacts.from_dict(json.load(fh)))


# --------------------------------------------------------------------------
# 시나리오
# --------------------------------------------------------------------------


def test_first_run_backfills_from_the_mes_span(mes_span):
    """1. 첫 실행 — 테이블이 없으면 MES 구간 시작부터 채운다."""
    _, mes_to = mes_span
    kusto = FakeKusto()

    namespace, stdout = run_notebook(kusto, now=mes_to)

    assert namespace["MODE"] == "backfill"
    assert namespace["WATERMARK"] is None
    assert namespace["START"] == mes_span[0], "첫 실행은 MES 구간 시작부터여야 한다"
    assert namespace["READINGS"], "백필이 한 행도 안 만들었다"

    written = {table for table, _, _, _ in kusto.writes}
    assert written == {"fdc_sensor_spec", "fdc_sensor_reading"}
    assert "적재" in stdout


def test_second_run_is_incremental_and_writes_no_duplicate_readings(mes_span):
    """2. 두 번째 실행 — watermark 뒤만 채우고 스펙은 건너뛴다.

    이 노트북의 중복 방지 전체가 여기 걸려 있다. 스펙이 42행씩 다시 쌓이면
    조인하는 모든 질의가 팬아웃되고, 판독이 겹치면 조용히 2배가 된다.
    """
    _, mes_to = mes_span
    kusto = FakeKusto()
    run_notebook(kusto, now=mes_to)

    after_first = {table: len(rows) for table, rows in kusto.tables.items()}
    kusto.writes.clear()

    later = mes_to + timedelta(hours=1)
    namespace, stdout = run_notebook(kusto, now=later)

    assert namespace["MODE"] == "live"
    assert namespace["WATERMARK"] is not None
    assert namespace["START"] == namespace["WATERMARK"]

    written = {table for table, _, _, _ in kusto.writes}
    assert "fdc_sensor_spec" not in written, "스펙을 두 번 썼다 — 조인이 2배로 팬아웃된다"
    assert "건너뜀" in stdout

    assert len(kusto.tables["fdc_sensor_spec"]) == after_first["fdc_sensor_spec"]
    assert len(kusto.tables["fdc_sensor_reading"]) > after_first["fdc_sensor_reading"], (
        "한 시간을 더 줬는데 판독이 안 늘었다"
    )

    timestamps = [row["reading_ts"] for row in kusto.tables["fdc_sensor_reading"]]
    assert all(ts <= later for ts in timestamps), "NOW 이후를 만들었다"


def test_a_naive_watermark_from_a_non_utc_driver_does_not_shift_the_window(mes_span):
    """3. 드라이버가 UTC 가 아닐 때 — naive watermark 를 라벨이 아니라 변환으로 받는다.

    PySpark 는 tz 인자 없이 `datetime.fromtimestamp` 를 부르므로 드라이버 OS 의
    로컬 시각이 naive 로 돌아온다. 셀이 `replace()` 로 라벨만 붙이면 서울에서는
    watermark 가 9시간 미래로 가서 그 구간이 영영 비고, LA 에서는 7시간 과거로
    가서 매 실행이 수천 행을 중복 적재한다. 쿼리는 성공하고 값만 틀린다.
    """
    import os
    import time

    _, mes_to = mes_span
    truth = mes_to - timedelta(hours=2)

    windows = {}
    old_tz = os.environ.get("TZ")
    try:
        for tz in ("UTC", "Asia/Seoul", "America/Los_Angeles"):
            os.environ["TZ"] = tz
            time.tzset()

            # PySpark 가 돌려주는 모양: 드라이버 로컬 벽시계의 naive datetime
            naive = datetime.fromtimestamp(truth.timestamp())
            # run_status 는 Idle -- 이 검사의 관심사는 watermark 의 타임존이지
            # 앵커 드리프트가 아니다. 가동 표본이 없으면 드리프트 가드가
            # 건너뛰므로 두 관심사가 섞이지 않는다.
            kusto = FakeKusto(
                tables={
                    "fdc_sensor_reading": [
                        {"reading_ts": naive, "run_status": "Idle"}
                    ]
                }
            )

            namespace, _ = run_notebook(kusto, now=mes_to)
            windows[tz] = namespace["START"]
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    assert len(set(windows.values())) == 1, (
        f"드라이버 타임존에 따라 생성 구간이 달라졌다: {windows}"
    )
    assert windows["UTC"] == truth


def test_a_future_watermark_stops_the_run(mes_span):
    """4. 미래 watermark — 조용히 0행을 반복하지 말고 멈춘다."""
    _, mes_to = mes_span
    kusto = FakeKusto(
        tables={
            "fdc_sensor_reading": [
                {"reading_ts": mes_to + timedelta(days=1), "run_status": "Idle"}
            ]
        }
    )

    with pytest.raises(RuntimeError, match="미래"):
        run_notebook(kusto, now=mes_to)


def test_a_failed_watermark_read_stops_instead_of_backfilling(mes_span):
    """5. watermark 조회 실패 — 첫 실행으로 간주하면 10만 행이 중복된다.

    1차 리뷰의 지적이다. `watermark_query` 는 테이블이 없어도 예외를 내지
    않으므로, 여기 오는 예외는 토큰 만료·스로틀링 같은 일시적 실패다.
    """
    _, mes_to = mes_span
    kusto = FakeKusto(read_fails=True)

    with pytest.raises(RuntimeError, match="watermark 조회에 실패"):
        run_notebook(kusto, now=mes_to)


def test_a_partial_spec_load_stops_the_run(mes_span):
    """6. 스펙 부분 적재 — 누락은 중복과 달리 눈에 안 띈다.

    "0 이 아니면 건너뛴다" 로 뭉뚱그리면 20행만 남은 스펙이 영구히 방치되고,
    스펙에 없는 센서의 판독 행이 inner join 에서 조용히 사라진다.
    """
    _, mes_to = mes_span
    kusto = FakeKusto(tables={"fdc_sensor_spec": [{} for _ in range(20)]})

    with pytest.raises(RuntimeError, match="이전 적재가 중간에 끊겼습니다"):
        run_notebook(kusto, now=mes_to)


def test_a_duplicated_spec_load_stops_and_points_at_the_readings(mes_span):
    """7. 스펙 중복 — 스펙만 지우면 판독은 2배인 채로 남는다.

    4차 리뷰의 실행 겹침이 남기는 흔적이 이것이다. 안내가 판독 확인까지
    데려가지 않으면 참가자는 에러만 없애고 2배 데이터로 실습하게 된다.
    """
    _, mes_to = mes_span
    kusto = FakeKusto(tables={"fdc_sensor_spec": [{} for _ in range(84)]})

    with pytest.raises(RuntimeError) as caught:
        run_notebook(kusto, now=mes_to)

    message = str(caught.value)
    assert "중복 적재됐습니다" in message
    assert "fdc_sensor_reading" in message, "판독 확인으로 안내해야 한다"
    assert ".drop table" in message


def test_every_write_carries_the_options_we_pinned(mes_span):
    """실행 경로에서 쓰기 옵션이 실제로 붙는지 본다.

    `test_build_notebook.py` 는 셀 소스에 문자열이 있는지만 본다. 그 검사는
    옵션이 `if` 안에 들어가 안 불리게 돼도 통과한다. 여기서는 대역이 받은
    것을 본다.
    """
    _, mes_to = mes_span
    kusto = FakeKusto()
    run_notebook(kusto, now=mes_to)

    assert kusto.writes, "아무것도 안 썼다"
    for table, _rows, options, mode in kusto.writes:
        assert options["adjustSchema"] == "GenerateDynamicCsvMapping", table
        assert options["timeZone"] == "UTC", table
        assert options["writeMode"] == "Transactional", table
        assert options["tableCreateOptions"] == "CreateIfNotExist", table
        assert mode == "Append", f"{table} 을 Append 가 아닌 {mode} 로 썼다"


# --------------------------------------------------------------------------
# 앵커 드리프트
#
# MES 는 EmptyDir 볼륨에 0개까지 축소되므로 콜드스타트마다 시드를 다시 돌린다.
# 배포에 MES_ANCHOR 가 박혀 있으면 몇 번을 재시드해도 같은 데이터가 나오지만
# (9개 테이블 해시로 실증했다), 그 값이 없으면 앵커가 재시작 시각으로 잡혀
# 공정이력 전체가 통째로 평행이동한다. 실제 배포에서 20분 만에 움직였다.
#
# 이게 조용한 실패다. 노트북은 watermark 이후만 만들므로 행이 겹치지 않고
# 검증도 통과한다. 한 테이블 안에 서로 다른 시간축의 조각이 쌓일 뿐이다.
# 앵커가 네 시간 움직인 뒤 한 번 더 적재하면 테이블의 40% 가 라이브 MES 와
# 모순됐다(측정값). 그 상태에서 시각으로 조인하면 38% 가 엉뚱한 로트를 답한다.
# --------------------------------------------------------------------------

GRID = timedelta(seconds=30)


def _loaded(run_ts: datetime | None, *, idle_ts: datetime | None = None) -> FakeKusto:
    """가동 표본의 최대 시각이 `run_ts` 인 적재 상태를 만든다."""
    rows = []
    if run_ts is not None:
        rows.append({"reading_ts": run_ts, "run_status": "Run"})
    if idle_ts is not None:
        rows.append({"reading_ts": idle_ts, "run_status": "Idle"})
    return FakeKusto(tables={"fdc_sensor_reading": rows})


def test_a_matching_anchor_lets_the_run_continue(mes_span):
    """적재된 시간축이 지금 조회한 MES 와 같으면 그냥 이어붙인다.

    가동 표본의 최댓값은 앵커보다 정확히 한 격자 이르다. 실제 스냅샷 네 개
    (앵커 다른 것 셋 + 픽스처) 전부에서 30초였다.
    """
    _, mes_to = mes_span
    kusto = _loaded(mes_to - GRID)

    namespace, _ = run_notebook(kusto, now=mes_to)

    assert namespace["ANCHOR_DRIFT"] == timedelta(0)


def test_a_drifted_anchor_stops_instead_of_stitching_two_timelines(mes_span):
    """앵커가 움직였는데 이어붙이면 한 테이블에 두 시간축이 섞인다.

    행이 겹치지 않으니 중복 검사에 안 걸리고, 각 조각은 자기 앵커 기준으로
    내부 정합이 맞으니 검증도 통과한다. 조인할 때만 드러난다.
    """
    _, mes_to = mes_span
    kusto = _loaded(mes_to - GRID - timedelta(hours=4))

    with pytest.raises(RuntimeError, match="앵커") as caught:
        run_notebook(kusto, now=mes_to)

    message = str(caught.value)
    assert "mesAnchor" in message, "재배포 방법을 알려주지 않으면 멈추기만 한다"
    assert ".drop table" in message, "이미 적재된 어긋난 데이터를 지우라고 해야 한다"


def test_a_backward_drift_stops_too(mes_span):
    """앵커는 뒤로도 간다 -- 과거 시각을 명시해 재배포하면 그렇다.

    한 방향만 보면(abs 를 빼면) 이 경우를 통과시킨다. 어긋남의 부호는
    문제가 아니다. 어긋났다는 사실이 문제다.

    NOW 를 MES 구간보다 뒤에 둔다. 앵커가 2026 년으로 잡혀 있어 현실의
    벽시계는 MES_TO 보다 한참 과거이고, 그러면 미래 watermark 가드가
    걸리지 않아 이 가드가 유일한 그물이 된다.
    """
    _, mes_to = mes_span
    kusto = _loaded(mes_to - GRID + timedelta(hours=4))

    with pytest.raises(RuntimeError, match="앵커"):
        run_notebook(kusto, now=mes_to + timedelta(hours=5))


def test_the_first_run_has_nothing_to_compare_against(mes_span):
    """빈 테이블에는 적재된 시간축이 없다. 여기서 멈추면 아무도 시작 못 한다."""
    _, mes_to = mes_span

    namespace, _ = run_notebook(_loaded(None), now=mes_to)

    assert namespace["MODE"] == "backfill"
    assert namespace["LOADED_RUN_TS"] is None


def test_a_table_holding_only_idle_samples_does_not_stop_the_run(mes_span):
    """가동 표본이 없으면 대조할 근거가 없다. 근거 없이 멈추면 안 된다.

    `maxif` 가 조건에 맞는 행이 없을 때 null 을 준다. 그 null 을 0 이나
    최소 시각으로 잘못 읽으면 모든 실행이 드리프트로 죽는다.
    """
    _, mes_to = mes_span
    kusto = _loaded(None, idle_ts=mes_to - timedelta(hours=1))

    namespace, _ = run_notebook(kusto, now=mes_to)

    assert namespace["LOADED_RUN_TS"] is None
    assert namespace["MODE"] == "live", "watermark 는 있으므로 이어붙여야 한다"


@pytest.mark.parametrize(
    "drift, stops",
    [
        (timedelta(minutes=4), False),
        (timedelta(minutes=6), True),
    ],
)
def test_the_threshold_tolerates_grid_jitter_but_not_a_moved_anchor(
    mes_span, drift, stops
):
    """임계값은 격자 흔들림을 견디되 실제 이동은 잡아야 한다.

    격자가 30초라 정상 오차는 1분을 넘지 않는다. 반면 관측된 최소 이동은
    20분이었다. 5분은 그 사이에 있다. 임계값을 무한대로 열면 가드가
    이름만 남는다.
    """
    _, mes_to = mes_span
    kusto = _loaded(mes_to - GRID - drift)

    if stops:
        with pytest.raises(RuntimeError, match="앵커"):
            run_notebook(kusto, now=mes_to)
    else:
        namespace, _ = run_notebook(kusto, now=mes_to)
        assert namespace["ANCHOR_DRIFT"] == drift


def test_a_naive_run_timestamp_is_compared_in_utc_not_rejected(mes_span):
    """PySpark 는 드라이버 로컬 벽시계의 naive datetime 을 돌려준다.

    watermark 와 같은 경로로 오므로 last_run_ts 도 naive 다. tz 를 붙이지
    않고 aware 인 MES_TO 와 빼면 TypeError 로 매 실행이 죽는다. 시끄럽긴
    해도 노트북이 아예 못 돈다.

    붙이는 방법도 중요하다. replace 로 UTC 라고 우기면 서울(+9) 드라이버가
    아홉 시간 어긋난 것으로 읽어 정상 실행을 드리프트로 막는다. 로컬로
    해석해 변환하는 astimezone 이어야 한다.
    """
    import os
    import time

    _, mes_to = mes_span
    truth = mes_to - GRID

    drifts = {}
    old_tz = os.environ.get("TZ")
    try:
        for tz in ("UTC", "Asia/Seoul", "America/Los_Angeles"):
            os.environ["TZ"] = tz
            time.tzset()

            naive = datetime.fromtimestamp(truth.timestamp())
            namespace, _ = run_notebook(_loaded(naive), now=mes_to)
            drifts[tz] = namespace["ANCHOR_DRIFT"]
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    assert set(drifts.values()) == {timedelta(0)}, (
        f"드라이버 타임존이 드리프트 판정을 바꿨다: {drifts}"
    )
