"""KQL 테이블 스키마와 Spark 적재용 행 변환.

Spark 커넥터의 `tableCreateOptions=CreateIfNotExist` 가 테이블을 자동
생성하지만, 여기에 DDL 을 명시적으로 둔다. 이유는 두 가지다.

- 자동 생성은 컬럼 타입을 데이터프레임에서 추론한다. 판독값이 우연히 모두
  정수면 `long` 으로 잡혀 이후 실수 적재가 깨진다. DDL 을 먼저 실행하면
  타입이 고정된다.
- 실습자가 Eventhouse 를 직접 열어 스키마를 확인하고 KQL 을 배우는 자료가
  된다.
"""

from __future__ import annotations

from datetime import datetime, timezone

SPEC_TABLE = "fdc_sensor_spec"
READING_TABLE = "fdc_sensor_reading"

SPEC_COLUMNS: tuple[str, ...] = (
    "sensor_code",
    "sensor_name_ko",
    "eqp_type",
    "unit",
    "normal_min",
    "normal_max",
    "alarm_min",
    "alarm_max",
    "sample_interval_sec",
    "is_active",
)

READING_COLUMNS: tuple[str, ...] = (
    "reading_ts",
    "eqp_id",
    "eqp_type",
    "step_code",
    "sensor_code",
    "value",
    "unit",
    "status",
)

# KQL 타입. Spark 데이터프레임 스키마와 이 표가 어긋나면 적재가 조용히 실패한다.
SPEC_SCHEMA: tuple[tuple[str, str], ...] = (
    ("sensor_code", "string"),
    ("sensor_name_ko", "string"),
    ("eqp_type", "string"),
    ("unit", "string"),
    ("normal_min", "real"),
    ("normal_max", "real"),
    ("alarm_min", "real"),
    ("alarm_max", "real"),
    ("sample_interval_sec", "int"),
    ("is_active", "bool"),
)

READING_SCHEMA: tuple[tuple[str, str], ...] = (
    ("reading_ts", "datetime"),
    ("eqp_id", "string"),
    ("eqp_type", "string"),
    ("step_code", "string"),
    ("sensor_code", "string"),
    ("value", "real"),
    ("unit", "string"),
    ("status", "string"),
)


def _create_command(table: str, schema: tuple[tuple[str, str], ...]) -> str:
    """`.create-merge` 를 쓴다. 이미 있으면 컬럼을 합치고 없으면 만든다.

    `.create` 는 기존 테이블을 덮어써 적재한 데이터를 날린다. 실습자가
    노트북을 두 번 돌리는 것만으로 데이터가 사라지면 안 된다.
    """
    columns = ", ".join(f"{name}:{kql_type}" for name, kql_type in schema)
    return f".create-merge table {table} ({columns})"


TABLE_DDL: dict[str, str] = {
    SPEC_TABLE: _create_command(SPEC_TABLE, SPEC_SCHEMA),
    READING_TABLE: _create_command(READING_TABLE, READING_SCHEMA),
}

# 판독 테이블만 보존 기간을 둔다. 3분마다 쌓이므로 방치하면 용량을 먹는다.
# 스펙 테이블은 42행 정적이라 보존 정책이 필요 없다.
RETENTION_DDL = (
    f".alter-merge table {READING_TABLE} policy retention "
    'softdelete = 30d recoverability = disabled'
)


def to_iso(moment: datetime) -> str:
    """KQL datetime 리터럴로 안전한 ISO 8601 UTC 문자열."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def to_rows(records: list[dict], columns: tuple[str, ...]) -> list[tuple]:
    """딕셔너리를 컬럼 순서에 맞춘 튜플로. Spark 데이터프레임 입력이 된다.

    `datetime` 은 그대로 넘긴다. Spark 가 TimestampType 으로 받아 커넥터가
    KQL datetime 으로 변환한다. 문자열로 바꾸면 타입이 어긋난다.
    """
    missing = set(columns) - set(records[0]) if records else set()
    if missing:
        raise KeyError(f"행에 없는 컬럼: {sorted(missing)}")
    return [tuple(record[column] for column in columns) for record in records]


def watermark_query(table: str = READING_TABLE) -> str:
    """마지막으로 적재한 시각. 없으면 빈 결과가 아니라 null 한 행이 온다."""
    return f"{table} | summarize last_ts = max(reading_ts)"
