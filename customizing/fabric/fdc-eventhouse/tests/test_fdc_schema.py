from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.fdc_schema import (
    READING_COLUMNS,
    READING_SCHEMA,
    READING_TABLE,
    RETENTION_DDL,
    SPEC_COLUMNS,
    SPEC_SCHEMA,
    SPEC_TABLE,
    TABLE_DDL,
    spark_schema,
    to_iso,
    to_rows,
    spec_count_query,
    watermark_query,
)
from src.fdc_generator import build_readings
from src.fdc_sensors import build_sensor_spec_rows

T0 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def readings(facts, busy_window):
    start = busy_window[0]
    return build_readings(facts, start, start + timedelta(minutes=3))


def test_ddl_uses_create_merge_not_create():
    """`.create` 는 기존 테이블을 덮어써 적재한 데이터를 날린다."""
    for command in TABLE_DDL.values():
        assert command.startswith(".create-merge table ")


def test_ddl_covers_both_tables():
    assert set(TABLE_DDL) == {SPEC_TABLE, READING_TABLE}


def test_reading_ddl_declares_datetime_and_real():
    ddl = TABLE_DDL[READING_TABLE]
    assert "reading_ts:datetime" in ddl
    assert "value:real" in ddl


def test_spec_ddl_declares_bool_and_int():
    ddl = TABLE_DDL[SPEC_TABLE]
    assert "is_active:bool" in ddl
    assert "sample_interval_sec:int" in ddl


def test_schema_order_matches_column_order():
    """데이터프레임 컬럼 순서와 KQL 스키마가 어긋나면 값이 밀려 적재된다."""
    assert tuple(name for name, _ in SPEC_SCHEMA) == SPEC_COLUMNS
    assert tuple(name for name, _ in READING_SCHEMA) == READING_COLUMNS


def test_schema_types_are_valid_kql():
    valid = {"string", "real", "int", "long", "bool", "datetime", "timespan", "dynamic"}
    for schema in (SPEC_SCHEMA, READING_SCHEMA):
        for _, kql_type in schema:
            assert kql_type in valid


def test_retention_targets_reading_table_only():
    assert READING_TABLE in RETENTION_DDL
    assert SPEC_TABLE not in RETENTION_DDL
    assert "softdelete = 30d" in RETENTION_DDL


def test_to_rows_preserves_column_order(readings):
    rows = to_rows(readings, READING_COLUMNS)
    assert len(rows) == len(readings)
    first = readings[0]
    assert rows[0] == tuple(first[c] for c in READING_COLUMNS)
    assert len(rows[0]) == len(READING_COLUMNS)


def test_to_rows_keeps_datetime_objects(readings):
    """문자열로 바꾸면 Spark 가 StringType 으로 잡아 KQL datetime 적재가 깨진다."""
    rows = to_rows(readings, READING_COLUMNS)
    assert isinstance(rows[0][0], datetime)


def test_to_rows_handles_spec_rows():
    rows = to_rows(build_sensor_spec_rows(), SPEC_COLUMNS)
    assert len(rows) == 42
    assert all(len(r) == len(SPEC_COLUMNS) for r in rows)


def test_to_rows_rejects_missing_column(readings):
    with pytest.raises(KeyError):
        to_rows(readings, READING_COLUMNS + ("lot_id",))


def test_to_rows_empty_input():
    assert to_rows([], READING_COLUMNS) == []


def test_to_iso_is_utc_with_z():
    assert to_iso(T0) == "2026-09-04T12:00:00.000000Z"


def test_to_iso_converts_other_zones():
    kst = timezone(timedelta(hours=9))
    assert to_iso(datetime(2026, 9, 4, 21, 0, tzinfo=kst)) == "2026-09-04T12:00:00.000000Z"


def test_to_iso_assumes_utc_for_naive():
    assert to_iso(datetime(2026, 9, 4, 12, 0)) == "2026-09-04T12:00:00.000000Z"


def test_watermark_query_targets_reading_table():
    query = watermark_query()
    assert READING_TABLE in query
    assert "max(reading_ts)" in query


def test_reading_schema_has_no_lot_columns():
    forbidden = {"lot_id", "product_code", "wafer_qty", "defect_code", "judgment", "result", "operator"}
    assert not (forbidden & {name for name, _ in READING_SCHEMA})


def test_spark_schema_pins_types():
    """타입을 추론에 맡기면 정수만 든 배치가 LongType 으로 잡혀 KQL real 이 깨진다."""
    reading = spark_schema(READING_TABLE)
    assert "reading_ts TIMESTAMP" in reading
    assert "value DOUBLE" in reading
    spec = spark_schema(SPEC_TABLE)
    assert "is_active BOOLEAN" in spec
    assert "sample_interval_sec INT" in spec


def test_spark_schema_field_count_matches_columns():
    assert len(spark_schema(READING_TABLE).split(", ")) == len(READING_COLUMNS)
    assert len(spark_schema(SPEC_TABLE).split(", ")) == len(SPEC_COLUMNS)


def test_spark_schema_order_matches_to_rows(readings):
    names = [f.split(" ")[0] for f in spark_schema(READING_TABLE).split(", ")]
    assert names == list(READING_COLUMNS)


def test_spark_schema_rejects_unknown_table():
    with pytest.raises(KeyError):
        spark_schema("no_such_table")

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


def test_watermark_query_tolerates_missing_table():
    """테이블이 없을 때 예외가 나면 '첫 실행' 과 '조회 실패' 를 못 가른다.

    `union isfuzzy=true` 문자열만 검사하면 안 된다. isfuzzy 는 여러 레그 중
    일부가 없을 때만 무시하고, 공식 문서가 "If no resolutions were
    successful, the query returns an error" 라고 명시한다. 레그가 실제
    테이블 하나뿐이면 첫 실행에 쿼리가 그대로 실패한다.

    앞선 구현이 정확히 그 상태였는데 문자열 검사만 하던 테스트가 통과시켰다.
    항상 해석되는 datatable 레그가 있는지를 본다.
    """
    query = watermark_query()
    assert "union isfuzzy=true" in query
    assert "datatable(" in query, (
        "레그가 실제 테이블 하나뿐이면 첫 실행에 쿼리가 에러를 낸다"
    )
    # datatable 레그가 union 안에, 테이블보다 앞에 와야 한다
    assert query.index("datatable(") < query.index(READING_TABLE)
    assert query.index("union") < query.index("datatable(")


def test_watermark_query_datatable_leg_declares_every_column_it_touches():
    """빈 결과에는 컬럼이 없어 max(reading_ts) 가 SEM0100 으로 죽는다.

    스텁 레그가 쿼리에서 건드리는 컬럼을 **전부** 실제 테이블과 같은 타입으로
    선언해야 한다. 타입이 어긋나면 outer union 이 접미사 붙은 컬럼 두 개를
    만들어 그 이름 자체가 사라진다.

    컬럼 이름을 여기 박아 두지 않는다. 쿼리가 참조하는 컬럼을 스키마와
    대조해 찾아낸다. 앵커 드리프트를 잡으려고 run_status 를 더할 때 스텁
    레그를 같이 고치지 않아 첫 실행이 깨졌었다 -- 이름을 박아 둔 테스트는
    새 컬럼이 늘어난 것을 모른다.
    """
    import re

    query = watermark_query()
    schema = dict(READING_SCHEMA)

    leg_start = query.index("datatable(")
    leg = query[leg_start:query.index("]", leg_start) + 1]

    referenced = {c for c in schema if re.search(rf"\b{c}\b", query)}
    assert referenced, "쿼리가 스키마 컬럼을 하나도 안 쓴다면 검사가 무의미하다"

    for col in sorted(referenced):
        assert f"{col}:{schema[col]}" in leg, (
            f"쿼리가 {col} 을 쓰는데 스텁 레그가 선언하지 않았다."
            f" 첫 실행에서 그 컬럼이 없어 쿼리가 죽거나 null 이 된다. 레그: {leg}"
        )

    assert "max(reading_ts)" in query


def test_spec_count_query_counts_the_spec_table():
    """스펙 적재 여부는 스펙 테이블 자신의 행 수로 판정해야 한다."""
    query = spec_count_query()
    assert SPEC_TABLE in query
    assert READING_TABLE not in query
    assert "union isfuzzy=true" in query
    assert "datatable(" in query
    assert "count()" in query


def test_readme_ddl_matches_the_schema():
    """README 가 참가자에게 붙여넣게 하는 DDL 이 실제 스키마와 같아야 한다.

    README 는 1단계에서 테이블을 미리 만들라고 안내한다. 그 DDL 이 코드와
    어긋나면 참가자가 틀린 타입의 테이블을 만들고, 노트북 적재가 실패하거나
    더 나쁘게는 조용히 캐스팅된다. 두 정의를 여기서 묶어 둔다.
    """
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    for name, ddl in TABLE_DDL.items():
        assert ddl in readme, f"README 의 {name} DDL 이 스키마와 다릅니다"


def test_readme_recovery_drops_every_table_the_notebook_writes():
    """앵커가 움직였을 때 README 는 어긋난 테이블을 비우라고 안내한다.

    노트북이 쓰는 테이블 중 하나라도 빠뜨리면 참가자가 절반만 지운다. 그러면
    남은 테이블에 옛 시간축이 그대로 있고, 다시 적재해도 두 시간축이 섞인
    상태가 유지된다. 증상이 사라지지 않으니 재배포가 안 먹혔다고 여긴다.

    README **전체**에서 문자열을 찾으면 안 된다. `.drop table` 은 개발 절과
    한계 절에도 나와서 네 번 등장한다. 앵커 절에서 통째로 지워도 다른
    세 곳이 통과시킨다 -- 처음 쓴 판이 정확히 그랬고, 돌연변이 둘이 모두
    빠져나갔다. 해당 절만 잘라 검사한다.
    """
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")

    marker = "### 앵커가 움직였을 때 복구"
    assert marker in readme, "앵커 드리프트 절이 README 에서 사라졌습니다"
    start = readme.index(marker)
    end = readme.index("### ", start + len(marker))
    section = readme[start:end]

    for name in TABLE_DDL:
        assert f".drop table {name}\n" in section, (
            f"앵커 절이 {name} 을 비우라고 안내하지 않습니다."
            " 절반만 지우면 옛 시간축이 남아 증상이 그대로입니다."
        )

    assert "mesAnchor" in section, (
        "앵커를 명시해 재배포하는 방법이 없으면 참가자가 같은 자리를 다시 밟습니다"
    )
