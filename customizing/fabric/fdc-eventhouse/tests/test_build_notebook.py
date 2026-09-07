import ast
import re
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

import nbformat
import pytest

import build_notebook as bn
from build_notebook import MODULE_ORDER, build_notebook, strip_local_imports

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def notebook():
    return build_notebook(ROOT)


def test_strip_removes_single_line_local_import():
    assert strip_local_imports("from src.fdc_sensors import sensors_for\nx = 1\n") == "x = 1\n"


def test_strip_removes_parenthesised_local_import():
    source = "from src.fdc_anomaly import (\n    build_profiles,\n    seed,\n)\ny = 2\n"
    assert strip_local_imports(source) == "y = 2\n"


def test_strip_keeps_stdlib_imports():
    source = "import math\nfrom datetime import datetime\nfrom src.fdc_sensors import x\n"
    result = strip_local_imports(source)
    assert "import math" in result
    assert "from datetime import datetime" in result
    assert "src.fdc_sensors" not in result


def test_notebook_is_valid(notebook):
    nbformat.validate(notebook)


def test_notebook_uses_synapse_kernel(notebook):
    assert notebook.metadata["kernelspec"]["name"] == "synapse_pyspark"


def test_parameters_cell_is_tagged(notebook):
    """Fabric 스케줄러가 이 태그를 보고 값을 덮어씁니다."""
    tagged = [c for c in notebook.cells if "parameters" in c.metadata.get("tags", [])]
    assert len(tagged) == 1
    assert "KUSTO_URI" in tagged[0].source
    assert "KUSTO_DATABASE" in tagged[0].source


def test_every_module_is_inlined(notebook):
    inlined = [c.metadata.get("fdc_module") for c in notebook.cells if c.metadata.get("fdc_module")]
    assert inlined == list(MODULE_ORDER)


def test_no_local_imports_survive(notebook):
    for cell in notebook.cells:
        if cell.cell_type == "code":
            assert "from src." not in cell.source, cell.metadata


def test_every_code_cell_parses(notebook):
    """Spark 전용 이름이 있어도 구문은 유효해야 합니다."""
    for index, cell in enumerate(notebook.cells):
        if cell.cell_type == "code":
            ast.parse(cell.source)


def test_modules_are_defined_before_use(notebook):
    """의존 모듈이 뒤에 오면 노트북 실행 중 NameError 가 납니다."""
    order = {name: i for i, name in enumerate(MODULE_ORDER)}
    for name in MODULE_ORDER:
        source = (ROOT / "src" / f"{name}.py").read_text(encoding="utf-8")
        for dependency in re.findall(r"from src\.([a-z_]+) import", source):
            assert order[dependency] < order[name], f"{name} 이 {dependency} 보다 먼저 온다"


def test_notebook_never_hardcodes_secrets(notebook):
    """자격증명이 값과 함께 커밋되면 20명이 공유하는 자료에 그대로 퍼집니다."""
    pattern = re.compile(r'(API_KEY|PASSWORD|SECRET|ACCESS_TOKEN)\s*=\s*"[^"]+"')
    for cell in notebook.cells:
        if cell.cell_type == "code":
            found = pattern.search(cell.source)
            assert not found, f"{cell.metadata}: {found.group(0) if found else ''}"


def test_parameters_leave_credentials_empty(notebook):
    params = next(c for c in notebook.cells if "parameters" in c.metadata.get("tags", []))
    assert 'MES_API_KEY = ""' in params.source
    assert 'KUSTO_URI = ""' in params.source


def test_load_cell_pins_spark_schema(notebook):
    load = [c.source for c in notebook.cells if "kusto_write" in c.source and "createDataFrame" in c.source]
    assert load
    assert "spark_schema(READING_TABLE)" in load[0]
    assert "spark_schema(SPEC_TABLE)" in load[0]


def test_load_cell_appends_never_overwrites(notebook):
    connect = next(c.source for c in notebook.cells if "def kusto_write" in c.source)
    assert '.mode("Append")' in connect
    assert "Overwrite" not in connect


def test_ddl_is_surfaced_but_never_executed(notebook):
    """Spark 커넥터는 데이터 평면 전용이라 `.create-merge` 를 보낼 수 없습니다.

    DDL 을 실행하려 들면 런타임에 깨집니다. 출력만 하는지 확인합니다.
    """
    connect = next(c.source for c in notebook.cells if "def kusto_write" in c.source)
    assert "TABLE_DDL" in connect
    assert "RETENTION_DDL" in connect
    for line in connect.splitlines():
        stripped = line.strip()
        if "TABLE_DDL" in stripped or "RETENTION_DDL" in stripped:
            assert stripped.startswith(("print(", "for ", "#")), stripped
    assert "kusto_read(TABLE_DDL" not in connect
    assert "execute_mgmt" not in connect


def test_connect_cell_explains_what_pins_types(notebook):
    connect = next(c.source for c in notebook.cells if "def kusto_write" in c.source)
    assert "spark_schema(" in connect


def test_spec_table_write_is_gated_on_its_own_row_count(notebook):
    """판독 watermark 로 판정하면 판독 적재 실패 때마다 스펙만 42행씩 쌓인다."""
    load = next(c.source for c in notebook.cells if "kusto_write" in c.source and "SPEC_TABLE" in c.source)
    assert "spec_count_query()" in load
    assert "_spec_present == 0" in load
    assert 'MODE == "backfill"' not in load


def test_watermark_is_converted_not_relabelled(notebook):
    """naive 를 UTC 로 '라벨만' 붙이면 드라이버 타임존만큼 어긋난다.

    PySpark TimestampType 은 쓰기와 읽기가 비대칭이다. 쓸 때는 tz-aware 라
    calendar.timegm 을 타서 UTC 로 저장되지만, 읽을 때는
    datetime.fromtimestamp(ts) 를 tz 인자 없이 부르므로 드라이버 OS 로컬
    시각이 naive 로 돌아온다. replace(tzinfo=utc) 는 값을 그대로 두고 라벨만
    바꾸므로 오프셋만큼 통째로 어긋난다.
    """
    cell = next(c.source for c in notebook.cells if "WATERMARK.tzinfo is None" in c.source)
    assert "astimezone(timezone.utc)" in cell
    assert "replace(tzinfo=timezone.utc)" not in cell, (
        "라벨만 바꾸면 드라이버가 UTC 가 아닐 때 중복 적재나 영구 누락이 난다"
    )


def test_future_watermark_is_rejected(notebook):
    """watermark 는 우리가 쓴 행에서 나오므로 미래일 수 없다.

    미래면 START > NOW 가 되어 매 실행이 0행을 쓰고 그 구간을 영구히 잃는다.
    화면에는 "새로 만들 구간이 없습니다" 만 뜨므로 아무도 눈치채지 못한다.
    """
    cell = next(c.source for c in notebook.cells if "WATERMARK.tzinfo is None" in c.source)
    assert "WATERMARK > NOW" in cell
    assert "raise RuntimeError" in cell.split("WATERMARK > NOW")[1][:400]


@pytest.mark.parametrize(
    "tz", ["UTC", "Asia/Seoul", "America/Los_Angeles", "Europe/Berlin", "Asia/Kolkata"]
)
def test_watermark_round_trip_survives_any_driver_timezone(tz):
    """노트북이 쓰는 변환을 실제 타임존에서 왕복시켜 본다.

    문자열 검사가 아니라 의미를 검사한다. PySpark 의 toInternal/fromInternal
    을 그대로 재현해, 우리가 쓴 시각이 어떤 드라이버 타임존에서도 원래 값으로
    돌아오는지 확인한다.

    ⚠️ 이 재현은 **PySpark 3.5 의 `python/pyspark/sql/types.py`** 기준이다.
    테스트가 프로덕션 코드의 상대(fromInternal)를 복제하고 있으므로, PySpark
    가 동작을 바꾸면 코드와 테스트가 **같은 방향으로 함께** 틀린다. 테스트는
    계속 초록인데 프로덕션만 깨지는 종류다. Fabric 런타임의 Spark 버전을
    올릴 때는 원문을 다시 대조하라.

        def fromInternal(self, ts):
            return datetime.datetime.fromtimestamp(ts // 1000000).replace(...)

    tz 인자가 붙거나 utcfromtimestamp 로 바뀌면 노트북의 astimezone 을 함께
    고쳐야 한다. 그때까지 남는 방어는 미래 watermark 가드뿐이다.
    """
    import calendar
    import os
    import time

    written = datetime(2026, 9, 4, 0, 0, tzinfo=dt_timezone.utc)
    # PySpark TimestampType.toInternal — tz-aware 라 timegm 을 탄다
    internal = int(calendar.timegm(written.utctimetuple())) * 1000000 + written.microsecond

    old = os.environ.get("TZ")
    try:
        os.environ["TZ"] = tz
        time.tzset()
        # PySpark TimestampType.fromInternal — tz 인자 없는 fromtimestamp
        naive = datetime.fromtimestamp(internal // 1000000).replace(
            microsecond=internal % 1000000
        )
        assert naive.tzinfo is None
        recovered = naive.astimezone(dt_timezone.utc)
        assert recovered == written, f"{tz} 에서 {recovered} != {written}"
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_write_mode_is_pinned_to_transactional(notebook):
    """중복 방지가 "전부 성공 아니면 전부 실패" 에 기대고 있다.

    Queued 로 두면 워커 일부만 안착할 수 있고, 행이 설비별로 묶여 있어서
    다음 실행의 전역 max(reading_ts) 가 안 써진 설비를 통째로 건너뛴다.
    커넥터 기본값이 Transactional 이지만 기본값에 기대지 않고 못 박는다.
    """
    cell = next(c.source for c in notebook.cells if "def kusto_write" in c.source)
    assert '.option("writeMode", "Transactional")' in cell


def test_spec_gate_rejects_partial_loads(notebook):
    """0 이 아니면 무조건 건너뛰면 부분 적재가 영구히 방치된다.

    스펙이 20행만 남으면 22개 조합이 빠지고, 그 센서의 판독 행은
    `join kind=inner fdc_sensor_spec` 에서 조용히 사라진다. 중복은 행 수가
    부풀어 눈에 띄지만 누락은 안 띈다. 워크숍에서 가장 나쁜 실패 유형이다.
    """
    load = next(c.source for c in notebook.cells if "spec_count_query()" in c.source)
    assert "_spec_expected = len(SPEC_ROWS)" in load
    assert "elif _spec_present == _spec_expected:" in load
    assert load.count("raise RuntimeError") >= 2, "부분 적재에도 멈춰야 한다"
    assert "drop table" in load, "복구 방법을 알려줘야 한다"


def test_spec_count_is_not_indexed_blindly(notebook):
    """빈 결과에 [0] 을 바로 태우면 IndexError 로 죽는다.

    summarize count() 는 한 행을 보장하므로 빈 결과는 조회가 이상하다는
    뜻이다. 모르는 채로 쓰면 스펙이 42행씩 중복된다.
    """
    load = next(c.source for c in notebook.cells if "spec_count_query()" in c.source)
    assert ".collect()[0]" not in load
    assert "if not _spec_rows:" in load
    assert "raise RuntimeError" in load


def test_validation_runs_before_load(notebook):
    sources = [c.source for c in notebook.cells if c.cell_type == "code"]
    validate_at = next(i for i, s in enumerate(sources) if "raise_on_fatal" in s)
    load_at = next(i for i, s in enumerate(sources) if "kusto_write(_reading_frame" in s)
    assert validate_at < load_at


def test_watermark_cell_never_falls_back_to_backfill(notebook):
    """조회 실패를 첫 실행으로 오인하면 65시간이 통째로 중복 적재된다.

    테이블이 없는 경우는 union isfuzzy 가 빈 결과로 처리하므로, 여기 오는
    예외는 토큰 만료·스로틀링 같은 일시적 실패다. 멈춰야 한다.
    """
    cell = next(c.source for c in notebook.cells if "WATERMARK" in c.source and "try:" in c.source)
    assert "WATERMARK = None" not in cell
    assert "raise RuntimeError" in cell


def test_watermark_query_tolerates_a_missing_table(notebook):
    """노트북에 들어간 watermark_query 가 첫 실행에 살아남아야 한다.

    `"union isfuzzy=true" in cell` 만 보면 안 된다. isfuzzy 는 여러 레그 중
    일부가 없을 때만 무시하고, 공식 문서는 "If no resolutions were
    successful, the query returns an error" 라고 명시한다. 레그가 실제
    테이블 하나뿐이면 첫 실행에 쿼리가 그대로 실패한다.

    앞선 구현이 정확히 그 상태였는데, 문자열 검사만 하던 이 테스트가
    통과시켰다. docstring 은 Kusto 런타임 동작을 단언하고 어서션은 토큰
    존재만 보는 틈이었다. 의미를 보도록 tests/test_fdc_schema.py 의
    test_watermark_query_tolerates_missing_table 과 기준을 맞춘다.
    """
    cell = next(c.source for c in notebook.cells if "def watermark_query" in c.source)
    assert "union isfuzzy=true" in cell
    assert "datatable(" in cell, (
        "레그가 실제 테이블 하나뿐이면 첫 실행에 쿼리가 에러를 낸다"
    )
    assert cell.index("union isfuzzy=true") < cell.index("datatable(")


def test_watermark_cell_does_not_cap_span(notebook):
    """구간을 잘라내면 워터마크가 NOW 로 가서 건너뛴 구간이 영영 안 채워진다."""
    cell = next(c.source for c in notebook.cells if "WATERMARK is None" in c.source)
    assert "MAX_SPAN_HOURS" not in cell
    assert "MES_FROM" in cell


def test_intro_states_the_cross_system_boundary(notebook):
    intro = notebook.cells[0].source
    assert "lot_id" in intro
    assert "MES" in intro


def test_outro_has_runnable_kql(notebook):
    outro = notebook.cells[-1].source
    assert "fdc_sensor_reading" in outro
    assert "fdc_sensor_spec" in outro
    assert "```kusto" in outro


def test_notebook_cell_count_is_stable(notebook):
    # intro + parameters + 7 modules + gate + connect + watermark + build + validate + load + outro
    assert len(notebook.cells) == 1 + 1 + len(MODULE_ORDER) + 6 + 1 == 16


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


# --- 커밋된 노트북이 소스와 어긋나지 않게 한다 -----------------------------
#
# .ipynb 는 생성물입니다. 참가자와 하네스는 src/ 가 아니라 .ipynb 를 읽으므로,
# src/ 를 고치고 build_notebook.py 를 안 돌리면 옛 코드가 그대로 배포됩니다.
# 이 함정에 실제로 한 번 걸렸습니다. 테스트는 전부 통과하는데 노트북만 옛
# 코드여서, 고쳤다고 믿은 버그를 하네스가 다시 잡았습니다.
#
# 아래 두 테스트가 그 틈을 닫습니다.


def _serialize(nb) -> str:
    return nbformat.writes(nb)


def test_notebook_build_is_deterministic():
    """같은 소스로 두 번 빌드하면 바이트까지 같아야 한다.

    nbformat 은 기본적으로 셀마다 임의의 id 를 새로 뽑습니다. 그대로 두면
    재빌드가 항상 16줄짜리 diff 를 만들어서, git status 만으로는 "내용이
    진짜 바뀌었나" 를 구분할 수 없습니다. 노이즈가 상시라면 아래
    test_committed_notebook_matches_a_fresh_build 도 무의미해집니다.
    """
    assert _serialize(build_notebook(ROOT)) == _serialize(build_notebook(ROOT))


def test_cell_ids_are_stable_names_not_random():
    """셀 id 가 내용이 아니라 역할에서 나와야 한다.

    역할 기반이면 한 셀을 고쳐도 그 셀의 source 만 diff 에 뜹니다. 내용
    해시로 만들면 고친 셀의 id 까지 같이 흔들려서 리뷰가 어려워집니다.
    """
    ids = [cell.id for cell in build_notebook(ROOT).cells]
    assert ids == [
        "fdc-intro",
        "fdc-parameters",
        *[f"fdc-module-{name.replace('_', '-')}" for name in MODULE_ORDER],
        "fdc-gate",
        "fdc-connect",
        "fdc-watermark",
        "fdc-build",
        "fdc-validate",
        "fdc-load",
        "fdc-outro",
    ]
    assert len(set(ids)) == len(ids), "id 가 겹치면 nbformat 이 거부한다"


def test_committed_notebook_matches_a_fresh_build(tmp_path):
    """커밋된 .ipynb 가 지금 src/ 로 빌드한 것과 같아야 한다.

    비교는 nbformat.writes 가 아니라 nbformat.write 로 합니다. 둘은 끝
    개행 하나가 다르고, 실제로 커밋되는 것은 write 쪽입니다. writes 로
    비교하면서 그 차이를 rstrip 으로 덮으면, 나중에 nbformat 이 직렬화를
    바꿨을 때 테스트가 조용히 거짓을 말하게 됩니다.

    이 테스트가 실패하면 답은 하나입니다: `python3 build_notebook.py`.
    """
    fresh_path = tmp_path / "fresh.ipynb"
    nbformat.write(build_notebook(ROOT), fresh_path)

    committed = (ROOT / "fdc_eventhouse_stream.ipynb").read_bytes()
    assert committed == fresh_path.read_bytes(), (
        "커밋된 노트북이 src/ 와 어긋납니다. `python3 build_notebook.py` 를 실행하세요."
    )


# --- 실행이 겹쳤을 때의 복구 안내 -------------------------------------------
#
# 두 실행이 같은 watermark 를 읽으면 스펙도 판독도 함께 중복됩니다. 스펙만
# 지우라고 안내하면 에러는 사라지지만 판독 테이블은 계속 2배인 채로 남고,
# 참가자는 고쳤다고 믿습니다. 그 조용한 상태로 되돌아가지 않게 묶어 둡니다.


def _load_cell(notebook):
    return next(c.source for c in notebook.cells if "_spec_present" in c.source)


def test_spec_gate_recovery_covers_the_reading_table(notebook):
    """스펙만 지우라고 안내하면 판독 중복이 그대로 남는다."""
    cell = _load_cell(notebook)
    # 스펙 행 수가 어긋났을 때 내는 마지막 raise 의 메시지만 본다
    message = cell.rsplit("raise RuntimeError", 1)[1].split(")\n", 1)[0]
    assert ".drop table {READING_TABLE}" in message, (
        "스펙만 지우면 판독 테이블은 계속 2배인 채로 남는다"
    )
    assert "summarize n = count() by reading_ts, eqp_id, sensor_code" in message, (
        "지우기 전에 판독이 실제로 중복됐는지 확인할 방법을 줘야 한다"
    )


def test_write_warns_before_a_long_silent_write(notebook):
    """Transactional 쓰기는 몇 분간 출력이 없어 멈춘 것처럼 보인다.

    참가자가 "Run all" 을 다시 누르면 두 실행이 같은 watermark 를 읽고 같은
    행을 두 번 씁니다. Kusto 에 유니크 제약이 없어 조용히 2배가 됩니다.
    막을 코드가 없으므로 최소한 기다리라고 말해야 합니다.
    """
    cell = next(c.source for c in notebook.cells if "def kusto_write" in c.source)
    body = cell.split("def kusto_write", 1)[1].split(".save()", 1)[0]
    assert "print(" in body, "쓰기 전에 안내가 나가야 한다"
    assert "다시 실행하지 마세요" in body


def test_readme_recommends_the_longer_schedule_interval():
    """3분 주기는 실행이 주기보다 길어질 수 있어 겹침을 부른다.

    Learn 은 starter pool 세션 기동만으로 2~5분이 걸릴 수 있다고 적는다.
    표에서 권장 표시가 사라지면 안내가 조용히 옛 상태로 돌아간다.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "**15분 (권장)**" in readme
    schedule = readme.split("### 4. 스케줄 걸기", 1)[1].split("####", 1)[0]
    assert schedule.index("15분 (권장)") < schedule.index("| 3분"), (
        "권장 주기가 표에서 먼저 와야 한다"
    )
