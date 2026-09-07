import ast
import re
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
    """첫 실행에는 테이블이 없다. 거기서 예외가 나면 안 된다."""
    cell = next(c.source for c in notebook.cells if "def watermark_query" in c.source)
    assert "union isfuzzy=true" in cell


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
