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
                return FakeResult([FakeRow(last_ts=None)])
            return FakeResult([FakeRow(last_ts=max(r["reading_ts"] for r in rows))])

        if "rows" in kql or "count()" in kql:
            return FakeResult([FakeRow(rows=len(self.tables.get("fdc_sensor_spec", [])))])

        raise AssertionError(f"대역이 모르는 쿼리입니다: {kql[:120]}")

    def write(self, table: str, rows: list, options: dict, mode: str | None) -> None:
        self.writes.append((table, len(rows), dict(options), mode))
        self.tables.setdefault(table, [])
        if table == "fdc_sensor_reading":
            # reading_ts 는 READING_COLUMNS 의 첫 컬럼이다.
            self.tables[table].extend({"reading_ts": row[0]} for row in rows)
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
            kusto = FakeKusto(tables={"fdc_sensor_reading": [{"reading_ts": naive}]})

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
        tables={"fdc_sensor_reading": [{"reading_ts": mes_to + timedelta(days=1)}]}
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
