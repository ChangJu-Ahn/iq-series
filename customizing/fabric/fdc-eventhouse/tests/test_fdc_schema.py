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


def test_watermark_query_datatable_leg_declares_the_column_it_aggregates():
    """빈 결과에는 컬럼이 없어 max(reading_ts) 가 SEM0100 으로 죽는다.

    스텁 레그가 집계 대상 컬럼을 실제 테이블과 같은 타입으로 선언해야 한다.
    타입이 어긋나면 outer union 이 접미사 붙은 컬럼 두 개를 만들어
    `reading_ts` 라는 이름 자체가 사라진다.
    """
    query = watermark_query()
    declared = dict(READING_SCHEMA)["reading_ts"]
    assert declared == "datetime", "스키마가 바뀌었으면 스텁 레그도 함께 바꿔야 한다"
    assert f"datatable(reading_ts:{declared})[]" in query
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
