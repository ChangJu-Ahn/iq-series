from datetime import datetime, timedelta, timezone

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
    watermark_query,
)
from src.fdc_generator import build_readings
from src.fdc_sensors import build_sensor_spec_rows

T0 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def readings(facts):
    return build_readings(facts, T0, T0 + timedelta(minutes=3))


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
