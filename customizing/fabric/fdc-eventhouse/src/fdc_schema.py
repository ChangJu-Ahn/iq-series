"""KQL 테이블 스키마와 Spark 적재용 행 변환.

테이블은 Spark 커넥터의 `tableCreateOptions=CreateIfNotExist` 가 만들고,
컬럼 타입은 `spark_schema()` 가 데이터프레임에 명시해 고정한다. 타입 추론에
맡기면 판독값이 우연히 모두 정수인 배치에서 `long` 컬럼이 만들어지고 이후
실수 적재가 조용히 잘린다.

`TABLE_DDL` 과 `RETENTION_DDL` 은 노트북이 **실행하지 않고 출력만** 한다.
Spark 커넥터는 데이터 평면 전용이라 `.create-merge` 같은 제어 명령을 보낼 수
없고, 그걸 보내려면 `azure-kusto-data` 를 따로 설치해야 해서 실습자마다
설치 단계가 하나 늘기 때문이다. 대신 실습자가 KQL 쿼리셋에 붙여넣어 스키마를
확인하거나 다른 작업 영역으로 옮길 때 쓰는 자료로 둔다.
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
    "run_status",
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
    ("run_status", "string"),
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


# KQL 타입에 대응하는 Spark 타입. 데이터프레임 스키마를 명시하는 데 쓴다.
_SPARK_TYPE = {
    "string": "STRING",
    "real": "DOUBLE",
    "int": "INT",
    "bool": "BOOLEAN",
    "datetime": "TIMESTAMP",
}


def spark_schema(table: str) -> str:
    """`spark.createDataFrame(rows, schema=...)` 에 넣을 DDL 문자열.

    스키마를 주지 않으면 Spark 가 값에서 타입을 추론한다. 한 배치의 판독값이
    우연히 모두 정수면 LongType 으로 잡히고, 커넥터가 그대로 KQL `long`
    컬럼을 만들어 이후 실수 적재가 조용히 잘린다. 문자열로 두는 이유는 이
    모듈이 pyspark 없이도 import 되어야 오프라인 테스트가 돌기 때문이다.
    """
    schema = {SPEC_TABLE: SPEC_SCHEMA, READING_TABLE: READING_SCHEMA}[table]
    return ", ".join(f"{name} {_SPARK_TYPE[kql_type]}" for name, kql_type in schema)
