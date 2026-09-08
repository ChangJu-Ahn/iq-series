"""에이전트 설정 문서(`data-agent-instructions.md`)를 데이터로 검증한다.

이 문서는 사람이 Fabric 칸에 복사해 넣는 것이라 코드가 읽지 않는다. 그래서 컬럼
이름 하나가 틀려도, 수치가 낡아도 아무도 모른다. 실제로 `data-agent-schema.md`가
그렇게 세 커밋 동안 낡은 수치를 싣고 있었다.

기대값은 전부 원시 스냅샷과 생성된 테이블에서 직접 계산한다. 검증 대상이 쓰는
헬퍼(`mes_anchor` 등)로 기대값을 만들면 양쪽에 같은 결함이 걸려 상쇄된다.
"""

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import sqlite3

import pytest

from src.mes_client import parse_mes_time
from src.qms_schema import TABLE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "data-agent-instructions.md"

_EXAMPLE = re.compile(r"^### (.+?)\n\n```sql\n(.*?)```", re.M | re.S)
_ALIAS = re.compile(r"\bAS\s+([a-z][a-z0-9_]*)", re.I)
_LITERAL = re.compile(r"(\w+)\s*=\s*'([^']+)'")


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def examples(doc) -> list[tuple[str, str]]:
    found = _EXAMPLE.findall(doc)
    assert found, "예시 질의를 하나도 못 찾았다 — 문서 구조가 바뀌었다"
    return found


def _strip_cast_to_date(sql: str) -> str:
    """`CAST(<식> AS DATE)` 를 `substr(<식>, 1, 10)` 으로 바꾼다.

    sqlite 에는 DATE 형이 없어 `CAST(x AS DATE)` 가 숫자 변환이 된다('2026' → 2026).
    ISO 문자열에서 앞 10글자를 자르는 것이 Spark 의 날짜 캐스팅과 같은 결과를 낸다.
    괄호가 중첩되므로 정규식 대신 짝을 세어 자른다.
    """
    while (start := sql.upper().find("CAST(")) != -1:
        depth, i = 0, start + 4
        while i < len(sql):
            depth += (sql[i] == "(") - (sql[i] == ")")
            if depth == 0:
                break
            i += 1
        inner = sql[start + 5 : i]
        assert inner.upper().rstrip().endswith("AS DATE"), f"DATE 아닌 CAST: {inner}"
        inner = inner.rstrip()[: -len("AS DATE")].rstrip()
        sql = f"{sql[:start]}substr({inner}, 1, 10){sql[i + 1 :]}"
    return sql


@pytest.fixture(scope="module")
def db(tables) -> sqlite3.Connection:
    """8개 테이블을 sqlite 에 실어 예시 SQL 을 실제로 돌린다."""
    conn = sqlite3.connect(":memory:")
    for name, rows in tables.items():
        columns = list(rows[0])
        quoted = ", ".join(f'"{c}"' for c in columns)
        conn.execute(f"CREATE TABLE {name} ({quoted})")
        conn.executemany(
            f"INSERT INTO {name} VALUES ({', '.join('?' * len(columns))})",
            [
                [
                    value.isoformat() if isinstance(value, (datetime, date)) else value
                    for value in (row[c] for c in columns)
                ]
                for row in rows
            ],
        )
    return conn


def _anchor(snapshot) -> datetime:
    """원시 공정이력에서 직접 구한 앵커. 헬퍼를 거치지 않는다."""
    return max(parse_mes_time(r["out_time"]) for r in snapshot.process_results)


def test_the_document_exists_and_names_every_fabric_field(doc):
    for field in (
        "데이터 원본 설명",
        "데이터 원본 지시문",
        "에이전트 지시문",
        "예시 질의",
    ):
        assert field in doc, f"Fabric 칸 안내 누락: {field}"
    assert "data-agent-schema.md" in doc, "데이터 원본 지시문으로 넣을 파일을 안 가리킨다"


def _expected_rows(tables, snapshot, examples) -> dict[str, int]:
    """예시 질의마다 나와야 할 행 수를 데이터에서 직접 센다.

    SQL 을 돌려 기대값을 만들면 SQL 이 틀려도 양쪽이 똑같이 틀려 상쇄된다. 실제로
    `MAX(inspection_datetime)` 을 `MIN` 으로 바꾸는 돌연변이가 "행이 있으면 통과"
    어서션을 그대로 빠져나갔다. 파이썬으로 따로 세야 그물이 생긴다.

    로트 번호와 공정 이름은 문서의 SQL 에서 뽑는다. 하드코딩하면 문서를 고쳤을 때
    기대값이 따라오지 않아, 문서와 테스트가 서로 다른 것을 말하게 된다.
    """
    literals = dict(_LITERAL.findall(" ".join(sql for _, sql in examples)))
    history_lot = literals["lot_id"]
    photo_step = literals["step_code"]

    inspections = tables["qms_inspection"]
    measurements = tables["qms_measurement"]
    ncrs = tables["qms_nonconformance"]
    dispositions = tables["qms_disposition"]

    specs = {s["spec_id"]: s for s in tables["qms_inspection_spec"]}
    photo = {
        (
            m["characteristic_code"],
            specs[m["spec_id"]]["cpk_target"],
            specs[m["spec_id"]]["sampling_method"],
            specs[m["spec_id"]]["inspection_frequency"],
            specs[m["spec_id"]]["control_method_ko"],
        )
        for m in measurements
        if m["step_code"] == photo_step
    }

    by_inspection: dict[str, list[dict]] = {}
    for row in ncrs:
        by_inspection.setdefault(row["inspection_id"], []).append(row)
    by_ncr: dict[str, list[dict]] = {}
    for row in dispositions:
        by_ncr.setdefault(row["ncr_id"], []).append(row)

    history = 0
    for row in inspections:
        if row["lot_id"] != history_lot:
            continue
        matched = by_inspection.get(row["inspection_id"], [])
        history += sum(max(len(by_ncr.get(n["ncr_id"], [])), 1) for n in matched) or 1

    lots = {i["lot_id"] for i in inspections if i["lot_id"]}
    shipped = {i["lot_id"] for i in inspections if i["inspection_type"] == "OQC"}

    return {
        "기한이 지났는데 아직 종결되지 않은 부적합은?": _overdue(
            tables, _anchor(snapshot).date()
        ),
        "공정과 특성별 규격 이탈률은?": len(
            {(m["step_code"], m["characteristic_code"]) for m in measurements}
        ),
        "이 특성의 Cpk 목표와 샘플링 방법은?": len(photo),
        "로트 하나의 품질 이력 전체는?": history,
        "출하검사를 아직 안 받은 로트는?": len(lots - shipped),
        "MES 상위 불량코드별로 부적합이 몇 건인가?": len(
            {n["mes_defect_code"] for n in ncrs if n["mes_defect_code"]}
        ),
        "규격을 벗어났는데 출하가 승인된 건과 그 근거는?": sum(
            1 for d in dispositions if d["disposition_type"] == "특채"
        ),
        "공급업체별 입고 불합격률은?": len(
            {r["supplier_code"] for r in tables["qms_incoming_inspection"]}
        ),
    }


def test_every_example_query_returns_the_rows_the_data_says_it_should(
    examples, db, tables, snapshot
):
    """행이 있는지가 아니라 **몇 행인지**를 본다.

    `MAX` 를 `MIN` 으로 바꿔도 행은 나온다. 개수를 세야 그런 것이 걸린다.
    """
    expected = _expected_rows(tables, snapshot, examples)
    asked = {q for q, _ in examples}
    assert asked == set(expected), (
        f"예시 누락 {set(expected) - asked} · 기대값 없는 예시 {asked - set(expected)}"
    )
    for question, sql in examples:
        rows = db.execute(_strip_cast_to_date(sql)).fetchall()
        assert rows, f"행이 0개인 예시: {question}"
        assert len(rows) == expected[question], (
            f"{question} → SQL {len(rows)}행 · 데이터 {expected[question]}행"
        )


def test_every_column_in_the_examples_exists_in_the_schema(examples):
    """컬럼 이름 오타를 잡는다. sqlite 실행으로도 잡히지만 어느 이름인지 알려준다."""
    known = {c for columns in TABLE_COLUMNS.values() for c in columns}
    known |= set(TABLE_COLUMNS)
    reserved = {
        "select", "from", "where", "group", "order", "by", "and", "or", "not",
        "null", "is", "join", "left", "inner", "on", "as", "count", "sum",
        "case", "when", "then", "else", "end", "distinct", "desc", "asc",
        "max", "min", "round", "cast", "date", "in",
    }
    for question, sql in examples:
        aliases = {a.lower() for a in _ALIAS.findall(sql)}
        body = re.sub(r"--[^\n]*", "", sql)
        for word in set(re.findall(r"[a-z][a-z0-9_]{2,}", body)):
            if word in known or word in reserved or word in aliases:
                continue
            pytest.fail(f"스키마에 없는 이름 {word!r} — 예시: {question}")


def test_the_now_of_this_data_is_exactly_the_mes_anchor(snapshot, tables):
    """문서가 `MAX(inspection_datetime)` 을 '지금'으로 쓰라고 한다.

    그 값이 MES 마지막 종료 시각과 어긋나면 안내가 거짓이 된다. 앵커는 원시
    `process_results` 에서 직접 구한다.
    """
    latest = max(row["inspection_datetime"] for row in tables["qms_inspection"])
    assert latest == _anchor(snapshot), (
        f"MAX(검사시각) {latest} 와 MES 앵커 {_anchor(snapshot)} 가 다르다"
    )


def _overdue(tables, today: date) -> int:
    return sum(
        1
        for row in tables["qms_nonconformance"]
        if row["closed_date"] is None and row["due_date"] < today
    )


def test_the_documented_overdue_count_matches_the_data(doc, snapshot, tables):
    measured = _overdue(tables, _anchor(snapshot).date())
    for section in ("에이전트 지시문", "확인해 볼 질문"):
        assert f"{measured}건" in doc, f"기한 초과 {measured}건이 문서에 없다 ({section})"


def test_using_the_wall_clock_really_does_break_the_deadline_scenario(doc, snapshot, tables):
    """`current_date()` 금지 경고의 근거를 실제로 잰다.

    경고가 과장이면 지워야 한다. 과잉 경고는 다른 경고까지 무디게 만든다.

    기준일을 `date.today()` 로 잡으면 이 테스트가 날짜와 함께 썩는다. 대신 벽시계가
    앵커보다 충분히 뒤일 때 나타나는 **포화**를 잰다 — 미결 부적합이 하나도 남지
    않고 전부 초과가 되는 것. 최대 기한이 앵커+14일이므로 그 뒤로는 늘 같다.
    """
    anchor = _anchor(snapshot).date()
    far_future = max(row["due_date"] for row in tables["qms_nonconformance"])
    assert far_future > anchor, "미래 기한이 없으면 이 경고의 전제가 깨진다"

    correct = _overdue(tables, anchor)
    saturated = _overdue(tables, far_future + timedelta(days=1))
    unclosed = sum(1 for r in tables["qms_nonconformance"] if r["closed_date"] is None)
    assert saturated == unclosed, "포화 상태에서는 미종결 전부가 초과여야 한다"
    assert saturated > correct, "벽시계를 써도 결과가 같다면 경고할 이유가 없다"

    future_due = sum(
        1 for row in tables["qms_nonconformance"] if row["due_date"] > anchor
    )
    future_check = sum(
        1
        for row in tables["qms_disposition"]
        if row["effectiveness_check_date"] and row["effectiveness_check_date"] > anchor
    )
    assert future_due and future_check, "미래 예정일이 없으면 기한 시나리오가 죽은 것"

    evidence = (ROOT / "data-agent-schema.md").read_text(encoding="utf-8")
    for number in (correct, saturated, future_due, future_check):
        assert str(number) in evidence, f"근거 수치 {number} 가 데이터 원본 지시문에 없다"
    for number in (correct, saturated):
        assert str(number) in doc, f"근거 수치 {number} 가 에이전트 지시문에 없다"


def test_joining_through_inspections_really_does_lose_half_the_ncrs(doc, tables):
    """"조인이 행을 줄인다"는 경고의 근거를 잰다.

    다른 경고는 전부 부풀림이라 합계가 이상해져 알아챌 여지가 있는데, 이것만
    방향이 반대다. 경고가 과장이면 지워야 하므로 실제 규모를 확인한다.
    """
    ncrs = tables["qms_nonconformance"]
    ids = {row["inspection_id"] for row in tables["qms_inspection"]}
    joined = [n for n in ncrs if n["inspection_id"] in ids]

    assert len(joined) < len(ncrs), "전부 이어지면 경고할 이유가 없다"
    assert len(joined) / len(ncrs) < 0.75, "일부만 빠지는 정도면 경고가 과하다"

    lost_cost = sum(n["estimated_cost_krw"] for n in ncrs if n["inspection_id"] not in ids)
    total_cost = sum(n["estimated_cost_krw"] for n in ncrs)
    assert lost_cost / total_cost > 0.25, "비용까지 크게 빠져야 이 경고가 값을 한다"

    evidence = (ROOT / "data-agent-schema.md").read_text(encoding="utf-8")
    percent = round(100 * len(joined) / len(ncrs))
    for text in (evidence, doc):
        assert f"{len(ncrs)}건 중 {len(joined)}건({percent}%)" in text or (
            f"{len(joined)}건({percent}%)" in text
        ), f"과소 계수 규모 {len(joined)}/{len(ncrs)} 가 문서에 없다"

    counts = Counter(n["ncr_source"] for n in ncrs)
    table = {
        line.split("|")[1].strip().strip("`"): line.split("|")[2].strip()
        for line in evidence.splitlines()
        if line.count("|") >= 4
    }
    for source, count in counts.items():
        assert table.get(source) == str(count), (
            f"출처 {source} → 문서 {table.get(source)!r} · 실측 {count}"
        )


def test_the_inspection_to_disposition_chain_never_amplifies(doc, tables):
    """문서가 이 사슬을 안전하다고 말한다. 실제로 그런지 본다.

    검사당 부적합이 2건이 되거나 부적합당 처리가 2건이 되는 순간 안내가 거짓이 된다.
    """
    ids = {row["inspection_id"] for row in tables["qms_inspection"]}
    per_inspection = Counter(
        n["inspection_id"] for n in tables["qms_nonconformance"] if n["inspection_id"] in ids
    )
    per_ncr = Counter(d["ncr_id"] for d in tables["qms_disposition"])

    assert per_inspection and max(per_inspection.values()) == 1, "검사당 부적합이 1건을 넘는다"
    assert per_ncr and max(per_ncr.values()) == 1, "부적합당 처리 결정이 1건을 넘는다"
    assert set(per_ncr) == {n["ncr_id"] for n in tables["qms_nonconformance"]}, (
        "처리 결정이 없는 부적합이 있다 — '정확히 1건'이 거짓이 된다"
    )


def test_the_checklist_numbers_match_the_data(doc, snapshot, tables):
    """마지막 점검표의 네 수치를 데이터에서 직접 세어 대조한다.

    표를 통째로 읽지 않고 줄 단위로 자른다. 문서 어딘가에 같은 숫자가 있으면
    통과하는 어서션은 그 숫자가 엉뚱한 줄에 있어도 못 잡는다.
    """
    inspections = tables["qms_inspection"]
    lots = {i["lot_id"] for i in inspections if i["lot_id"]}
    shipped = {i["lot_id"] for i in inspections if i["inspection_type"] == "OQC"}

    expected = {
        "기한이 지났는데 종결 안 된 부적합은?": _overdue(tables, _anchor(snapshot).date()),
        "출하검사를 아직 안 받은 로트는?": len(lots - shipped),
        "특채로 승인된 건은?": sum(
            1 for d in tables["qms_disposition"] if d["disposition_type"] == "특채"
        ),
        "규격을 벗어난 계측은?": sum(
            1 for m in tables["qms_measurement"] if m["is_out_of_spec"]
        ),
    }
    rows = {
        line.split("|")[1].strip(): line.split("|")[2].strip()
        for line in doc.splitlines()
        if line.count("|") >= 3
    }
    for question, count in expected.items():
        assert question in rows, f"점검표에 없는 질문: {question}"
        assert rows[question] == f"{count}건" or rows[question] == f"{count}개", (
            f"{question} → 문서 {rows[question]!r} · 실측 {count}"
        )


def test_the_document_forbids_the_three_uncomputable_answers(doc):
    """이 데이터로 못 내는 셋을 에이전트가 지어내지 않도록 못 박았는지 본다."""
    section = doc.split("## Response guidelines")[1].split("## Handling")[0]
    for term in ("Cpk", "sample_no", "site_no", "PCS"):
        assert term in section, f"못 내는 값 안내에 {term} 없음"


def test_the_document_names_all_three_dangerous_joins(doc):
    section = doc.split("**조인은 아래 세 가지만")[1].split("**null")[0]
    for term in ("qms_inspection_spec", "characteristic_code", "qms_defect_code", "spec_id"):
        assert term in section, f"조인 경고에 {term} 없음"


def test_the_document_never_contains_the_api_key(doc):
    assert "changjuahn" not in doc.lower(), "문서에 API 키가 박혔다"
