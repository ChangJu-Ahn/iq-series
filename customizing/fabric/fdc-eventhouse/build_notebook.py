"""src/ 모듈을 인라인 전개해 Fabric 노트북을 조립한다.

Fabric 노트북은 파일 업로드나 pip install 없이 단독 실행되어야 한다. 20명이
각자 자기 작업 영역에서 돌리는 자료라 준비 단계가 하나 늘 때마다 20번씩
막힌다. 그래서 모듈을 그대로 셀에 붙이고 로컬 import 행만 지운다. 지운
뒤에도 모든 이름이 한 네임스페이스에 모이므로 참조는 그대로 성립한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import nbformat

MODULE_ORDER = (
    "mes_probe",
    "fdc_runs",
    "fdc_sensors",
    "fdc_anomaly",
    "fdc_generator",
    "fdc_schema",
    "fdc_validate",
)

# 괄호 묶음 여러 줄 import 도 통째로 잡는다. import 목록에 중첩 괄호가 없어
# [^)]* 로 충분하다.
_LOCAL_IMPORT = re.compile(
    r"^from\s+src\.[a-z_]+\s+import\s+(?:\([^)]*\)|[^\n]*)\n",
    re.MULTILINE,
)

_INTRO = """# FDC 설비 텔레메트리 스트림 노트북

설비 센서 판독값을 만들어 이 작업 영역의 **Eventhouse(KQL DB)** 에 적재합니다.
Fabric 스케줄러로 몇 분마다 돌리면 실제 팹처럼 시계열이 계속 쌓입니다.

## 세 번째 시스템

MES는 무엇을 만들었는지, QMS는 그것이 합격인지 압니다. FDC는 **설비가 그때
어떤 상태였는지**를 압니다. 셋은 서로 다른 시스템이고 공유하는 키는
`eqp_id` 와 `step_code` 뿐입니다.

FDC 판독값에는 `lot_id`, `product_code`, `defect_code`, `judgment` 가 **없습니다**.
센서는 지금 어떤 로트가 올라와 있는지 모르기 때문입니다. 그래서
"챔버 온도가 튄 그 시각에 어떤 로트가 그 설비에 있었나"를 알려면 MES에 물어야
합니다. 이것이 이 실습의 목표입니다.

## 이상은 MES에서 유도합니다

"고장 설비 목록"을 하드코딩하지 않습니다. MES `process_results` 의 설비별
불량률을 집계해, 불량률이 높은 설비일수록 크게 이탈시키고 불량코드가
지목하는 센서에만 이탈을 싣습니다. Eventhouse에서 찾아낸 이상 설비가 MES
실적과 맞아떨어지는 이유입니다.

## 실행 순서

1. 작업 영역에 **Eventhouse** 를 만들고 KQL 데이터베이스를 하나 둡니다.
2. KQL 데이터베이스 페이지 오른쪽 위 **Query URI** 를 복사합니다.
3. 아래 파라미터 셀에 그 URI와 데이터베이스 이름, MES API 키를 넣습니다.
4. 전체 실행합니다.

Query URI는 비밀값이 아닙니다. 인증은 `mssparkutils` 가 실행자 신원으로
토큰을 발급해 처리하므로 복사해 둘 키가 없습니다.

## 두 번째 실행부터

첫 실행은 MES 공정이력이 걸쳐 있는 구간 전체를 백필합니다. 약 65시간이고
10만 행 안팎입니다. 이후 실행은 이미 적재된 마지막 시각(watermark)부터
지금까지만 채웁니다. 값이 (설비, 센서, 타임스탬프)만으로 정해지므로 몇 번을
다시 돌려도 같은 시각에는 같은 값이 들어갑니다.

설비가 돌고 있을 때만 공정 센서 6종을 30초 간격으로 내보냅니다. 멈춰 있는
동안에는 주변 온도·습도 2종만 5분 간격으로 남습니다. `run_status` 컬럼으로
구분할 수 있습니다.
"""

_PARAMETERS = '''# Fabric 파이프라인이나 스케줄러에서 이 셀의 값을 덮어쓸 수 있습니다.

# KQL 데이터베이스 페이지 오른쪽 위의 Query URI 입니다. 비밀값이 아닙니다.
# 예: "https://trd-abcdefg.z9.kusto.fabric.microsoft.com"
KUSTO_URI = ""
KUSTO_DATABASE = ""

# MES 인증 키. 이 작업 영역은 공유될 수 있고 노트북은 자동 저장되니 실행 후 지우세요.
MES_BASE_URL = "https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io"
MES_API_KEY = ""


'''

_GATE = '''# MES 연결 게이트. 여기서 실패하면 이상 주입의 근거가 없으므로 진행하지 않습니다.
if not MES_API_KEY:
    raise ValueError("MES_API_KEY 를 파라미터 셀에 넣으세요.")
if not KUSTO_URI or not KUSTO_DATABASE:
    raise ValueError("KUSTO_URI 와 KUSTO_DATABASE 를 파라미터 셀에 넣으세요.")

PROBE = MesProbe(MES_BASE_URL, MES_API_KEY)
try:
    FACTS = PROBE.fetch_facts()
except Exception as exc:
    raise RuntimeError(
        "MES 연결에 실패했습니다. 확인할 것: "
        "(1) API 키가 올바른가 (2) Fabric Spark 풀에서 외부 인터넷 아웃바운드가 허용되는가 "
        f"(3) {MES_BASE_URL} 가 응답하는가. 원인: {exc}"
    ) from exc

PROFILES = build_profiles(FACTS)
print(f"공정이력 {len(FACTS.process_results)}건 · 설비 {len(FACTS.equipment)}대")
for _p in sorted(PROFILES.values(), key=lambda p: -p.defect_rate):
    print(
        f"  {_p.eqp_id:12s} {_p.eqp_type:10s} 불량 {_p.defect_runs:2d}/{_p.total_runs:2d}"
        f" = {_p.defect_rate:5.1%}  이상센서: {', '.join(hinted_sensors(_p))}"
    )
'''

_CONNECT = '''# Kusto 커넥터는 Fabric 런타임에 내장돼 있어 설치가 필요 없습니다.
# 토큰은 이 노트북을 실행하는 신원으로 발급됩니다. 복사해 둘 비밀값이 없습니다.
KUSTO_FORMAT = "com.microsoft.kusto.spark.synapse.datasource"


def kusto_token():
    return mssparkutils.credentials.getToken(KUSTO_URI)


def kusto_read(query):
    return (
        spark.read.format(KUSTO_FORMAT)
        .option("kustoCluster", KUSTO_URI)
        .option("kustoDatabase", KUSTO_DATABASE)
        .option("kustoQuery", query)
        .option("accessToken", kusto_token())
        .load()
    )


def kusto_write(frame, table):
    (
        frame.write.format(KUSTO_FORMAT)
        .option("kustoCluster", KUSTO_URI)
        .option("kustoDatabase", KUSTO_DATABASE)
        .option("kustoTable", table)
        .option("accessToken", kusto_token())
        .option("tableCreateOptions", "CreateIfNotExist")
        .mode("Append")
        .save()
    )


print("테이블은 Spark 커넥터가 만듭니다. 컬럼 타입은 spark_schema() 가 고정합니다.")
for _name, _ddl in TABLE_DDL.items():
    print(f"  {_name:20s} {spark_schema(_name)}")

print()
print("같은 스키마의 KQL DDL 입니다. 실행할 필요는 없고, KQL 쿼리셋에 붙여넣어")
print("스키마를 직접 확인하거나 다른 작업 영역에 옮길 때 씁니다.")
for _ddl in TABLE_DDL.values():
    print(f"  {_ddl}")
print(f"  {RETENTION_DDL}")
'''

_WATERMARK = '''# 어디부터 채울지 정합니다. 테이블이 없으면 첫 실행으로 봅니다.
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)

try:
    _row = kusto_read(watermark_query()).collect()
    WATERMARK = _row[0]["last_ts"] if _row else None
except Exception as exc:
    # 여기서 첫 실행으로 간주하고 넘어가면 안 됩니다. watermark_query 는
    # 테이블이 없어도 예외를 내지 않으므로(union isfuzzy) 여기 온 예외는
    # 토큰 만료·스로틀링·네트워크 같은 일시적 실패입니다. 백필로 떨어지면
    # 이미 적재된 10만 행을 통째로 다시 씁니다. 멈추는 편이 낫습니다.
    raise RuntimeError(
        "watermark 조회에 실패해 중단합니다. 첫 실행으로 간주하면 이미 적재된"
        " 구간을 다시 써서 중복이 쌓입니다(Eventhouse 는 유니크 제약이 없습니다)."
        " KUSTO_URI 와 KUSTO_DATABASE 를 확인하고 다시 실행하세요."
        f" 원인: {exc}"
    ) from exc

if WATERMARK is not None and WATERMARK.tzinfo is None:
    WATERMARK = WATERMARK.replace(tzinfo=timezone.utc)

# 첫 실행은 MES 공정이력이 시작하는 시각부터 채웁니다. 벽시계 기준으로 최근
# 몇 시간만 채우면 MES 가 아는 구간과 겹치지 않아서, 센서에서 찾은 이상을
# 공정이력에서 확인할 수 없습니다. 이 노트북의 존재 이유가 사라집니다.
MES_FROM, MES_TO = span(FACTS)

if WATERMARK is None:
    MODE = "backfill"
    START = MES_FROM
else:
    MODE = "live"
    START = WATERMARK

# 구간을 잘라내지 않습니다. 잘라내면 watermark 가 잘린 지점이 아니라 NOW 로
# 가버려서 건너뛴 구간을 다시는 채우지 않습니다. 유휴 구간은 5분 간격 2종이라
# 며칠이 밀려도 수만 행에 그칩니다.
print(f"모드={MODE} · watermark={WATERMARK}")
print(f"MES 공정이력 {MES_FROM} ~ {MES_TO}")
print(f"생성 구간 {START} ~ {NOW}")
if NOW > MES_TO:
    _stale = NOW - MES_TO
    print(f"  MES 배포 후 {_stale.days}일 {_stale.seconds // 3600}시간 지났습니다."
          f" 그 이후 구간은 전부 유휴로 채웁니다.")
'''

_BUILD = '''SPEC_ROWS = build_sensor_spec_rows()
READINGS = build_readings(FACTS, START, NOW)

print(f"센서 스펙 {len(SPEC_ROWS)}행")
print(f"판독값 {len(READINGS):,}행")
if READINGS:
    _counts = {}
    for _r in READINGS:
        _counts[_r["status"]] = _counts.get(_r["status"], 0) + 1
    for _k in ("Normal", "Warning", "Alarm"):
        _n = _counts.get(_k, 0)
        print(f"  {_k:8s} {_n:7,d}  {_n / len(READINGS):6.2%}")
else:
    print("새로 만들 구간이 없습니다. 30초 격자가 아직 차지 않았습니다.")
'''

_VALIDATE = '''# 적재 전에 검증합니다. Eventhouse 는 append-only 라 잘못 쓴 행을 지우려면
# 익스텐트 단위로 지워야 하고 같은 익스텐트의 정상 행까지 날아갑니다.
if READINGS:
    RESULTS = validate(READINGS, FACTS, watermark=WATERMARK)
    print(format_report(RESULTS))
    raise_on_fatal(RESULTS)
else:
    print("적재할 행이 없어 검증을 건너뜁니다.")
'''

_LOAD = '''# 센서 스펙은 42행 정적입니다. 적재 여부를 판독 테이블의 watermark 로
# 판정하면 안 됩니다. 스펙 쓰기가 판독 쓰기보다 먼저라, 판독 적재가 실패해
# 재실행될 때마다 스펙만 42행씩 쌓입니다. 그러면 스펙과 조인하는 질의가
# 전부 중복 수만큼 팬아웃됩니다. 스펙 테이블 자신의 행 수로 판정합니다.
_spec_rows = kusto_read(spec_count_query()).collect()

# summarize count() 는 테이블이 비어도, 없어도 반드시 한 행을 냅니다. 빈
# 결과가 왔다면 조회 자체가 이상한 것이므로 여기서 멈춥니다. 모르는 채로
# 쓰면 스펙이 42행씩 중복되고, 중복은 조인하는 모든 질의를 조용히 부풀립니다.
if not _spec_rows:
    raise RuntimeError(
        f"{SPEC_TABLE} 행 수를 확인하지 못했습니다. 중복 적재를 피하려고 멈춥니다. "
        "KUSTO_URI 와 KUSTO_DATABASE 를 확인하고 다시 실행하세요."
    )

_spec_present = _spec_rows[0]["rows"]
_spec_expected = len(SPEC_ROWS)

# 0 / 정확히 기대값 / 그 외 를 가릅니다. "0 이 아니면 건너뛴다" 로 뭉뚱그리면
# 부분 적재(20행만 남음)가 영구히 방치됩니다. 그러면 스펙에 없는 센서의 판독
# 행이 inner join 에서 조용히 사라집니다. 중복은 눈에 띄지만 누락은 안 띕니다.
if _spec_present == 0:
    _spec_frame = spark.createDataFrame(
        to_rows(SPEC_ROWS, SPEC_COLUMNS), schema=spark_schema(SPEC_TABLE)
    )
    kusto_write(_spec_frame, SPEC_TABLE)
    print(f"{SPEC_TABLE:20} {_spec_frame.count():7,d}행 적재")
elif _spec_present == _spec_expected:
    print(f"{SPEC_TABLE:20} 건너뜀 (이미 {_spec_present:,}행)")
else:
    raise RuntimeError(
        f"{SPEC_TABLE} 이 {_spec_present:,}행입니다. {_spec_expected}행이어야 합니다.\\n"
        f"  {_spec_present:,} < {_spec_expected}  이전 적재가 중간에 끊겼습니다. "
        f"스펙에 없는 센서의 판독 행이 조인에서 조용히 사라집니다.\\n"
        f"  {_spec_present:,} > {_spec_expected}  중복 적재됐습니다. "
        f"스펙과 조인하는 질의가 중복 수만큼 부풀어 오릅니다.\\n"
        f"둘 다 KQL 쿼리셋에서 `.drop table {SPEC_TABLE}` 로 지운 뒤 "
        "이 노트북을 다시 실행하면 됩니다."
    )

if READINGS:
    _reading_frame = spark.createDataFrame(
        to_rows(READINGS, READING_COLUMNS), schema=spark_schema(READING_TABLE)
    )
    kusto_write(_reading_frame, READING_TABLE)
    print(f"{READING_TABLE:20} {_reading_frame.count():7,d}행 적재")
'''

_OUTRO = """## 다음 단계

### 1. 스케줄 걸기

노트북 오른쪽 위 **Run > Schedule** 에서 분 단위 반복을 켭니다. 3분으로 두면
실제 팹에 가깝지만 하루 480번 Spark 세션이 뜹니다. 용량이 걱정되면 15분으로
두세요. **해상도는 그대로입니다**(가동 30초 · 유휴 5분). 잡 주기와 데이터 해상도는
별개이고, 매 실행이 watermark부터 지금까지의 격자를 통째로 채우기 때문입니다.

### 2. Eventhouse에서 확인하기

데이터는 MES 공정이력 구간(약 65시간)을 덮습니다. 그 이후 시각은 전부 유휴라
`ago(24h)` 로 거르면 가동 구간을 통째로 놓칠 수 있습니다. 먼저 구간을 봅니다.

```kusto
// 데이터가 어느 구간을 덮고 있나?
fdc_sensor_reading
| summarize 시작 = min(reading_ts), 종료 = max(reading_ts), 행 = count() by run_status
```

```kusto
// 경보가 몰린 설비와 시각. 교차 질의의 출발점입니다.
fdc_sensor_reading
| where status == "Alarm" and run_status == "Run"
| summarize 건수 = count(), 시작 = min(reading_ts), 종료 = max(reading_ts)
    by eqp_id, sensor_code
| order by 건수 desc
```

```kusto
// 설비별 주변 온도 추이. 공통 센서라 전 설비를 한 차트에서 비교합니다.
fdc_sensor_reading
| where sensor_code == "AMBIENT_TEMP"
| make-series avg(value) default=0 on reading_ts step 10m by eqp_id
| render timechart
```

```kusto
// 한계치는 판독값에 복사하지 않고 스펙 테이블과 조인해 얻습니다.
fdc_sensor_reading
| where run_status == "Run"
| join kind=inner fdc_sensor_spec on eqp_type, sensor_code
| extend 여유 = normal_max - value
| project reading_ts, eqp_id, sensor_code, value, normal_min, normal_max, 여유
```

### 3. 세 시스템을 함께 봐야 답이 나오는 질문

FDC만으로는 절반까지밖에 못 갑니다. 나머지는 MES와 QMS에 물어야 합니다.

- 경보가 가장 많았던 설비는 어디이고, **그 시각에 그 설비에서
  어떤 로트를 처리하고 있었나요?** (FDC → MES)
- 그 로트들은 품질 검사를 통과했나요? (MES → QMS)
- QMS에서 `Overlay` 결함이 나온 로트를 처리한 설비의 **온도 추이**는 어땠나요?
  (QMS → MES → FDC)
- 센서는 정상 범위였는데 결함이 난 건이 있나요? 설비 문제가 아니라면
  무엇을 봐야 할까요? (FDC + QMS)
- 불량률이 가장 높은 설비의 센서 중 실제로 이탈한 것은 무엇인가요?
  MES 불량코드와 물리적으로 맞아떨어지나요? (MES → FDC)
"""


def strip_local_imports(source: str) -> str:
    """src. 로 시작하는 import 행만 제거한다. 표준 라이브러리 import 는 남긴다."""
    return _LOCAL_IMPORT.sub("", source)


def _code(source: str, **metadata) -> nbformat.NotebookNode:
    cell = nbformat.v4.new_code_cell(source.rstrip() + "\n")
    cell.metadata.update(metadata)
    return cell


def build_notebook(root: Path) -> nbformat.NotebookNode:
    notebook = nbformat.v4.new_notebook()
    notebook.metadata.update(
        {
            "kernelspec": {
                "display_name": "Synapse PySpark",
                "language": "Python",
                "name": "synapse_pyspark",
            },
            "language_info": {"name": "python"},
        }
    )
    cells = [nbformat.v4.new_markdown_cell(_INTRO)]
    cells.append(_code(_PARAMETERS, tags=["parameters"]))
    for module in MODULE_ORDER:
        source = (root / "src" / f"{module}.py").read_text(encoding="utf-8")
        cells.append(_code(strip_local_imports(source), fdc_cell="module", fdc_module=module))
    cells.append(_code(_GATE))
    cells.append(_code(_CONNECT))
    cells.append(_code(_WATERMARK))
    cells.append(_code(_BUILD))
    cells.append(_code(_VALIDATE))
    cells.append(_code(_LOAD))
    cells.append(nbformat.v4.new_markdown_cell(_OUTRO))
    notebook.cells = cells
    return notebook


def main() -> None:
    root = Path(__file__).resolve().parent
    target = root / "fdc_eventhouse_stream.ipynb"
    notebook = build_notebook(root)
    nbformat.validate(notebook)
    nbformat.write(notebook, target)
    print(f"{target} 생성됨 · {len(notebook.cells)}셀")


if __name__ == "__main__":
    sys.exit(main())
