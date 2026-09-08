from pathlib import Path
import re

from src.qms_schema import TABLE_COLUMNS
from src.qms_validate import DEVICE_TARGETS, TABLE_ROW_TARGETS

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
AGENT_DOC = ROOT / "data-agent-schema.md"

_HEADING = re.compile(r"^### `(qms_\w+)` — ([\d,]+)행\s*$", re.M)


def _table_sections(text):
    """테이블 절을 이름 → 본문으로 자른다.

    문서 전체에서 부분 문자열을 찾으면 어느 절에 있든 통과한다. judgment 는
    세 테이블에 있어, 한 절에서 통째로 지워도 다른 절이 통과시킨다. FDC 쪽에서
    같은 구조의 어서션이 두 번 연속 뚫린 것을 보고 절 단위로 바꿨다.
    """
    marks = list(_HEADING.finditer(text))
    return {
        m.group(1): text[m.end() : (marks[i + 1].start() if i + 1 < len(marks) else len(text))]
        for i, m in enumerate(marks)
    }


def test_both_documents_exist():
    assert README.exists()
    assert AGENT_DOC.exists()


def test_agent_doc_describes_every_column_inside_its_own_table_section():
    sections = _table_sections(AGENT_DOC.read_text(encoding="utf-8"))
    assert set(sections) == set(TABLE_COLUMNS), (
        f"절 누락 {set(TABLE_COLUMNS) - set(sections)} · 여분 {set(sections) - set(TABLE_COLUMNS)}"
    )
    for table, columns in TABLE_COLUMNS.items():
        for column in columns:
            assert f"`{column}`" in sections[table], f"{table} 절에 {column} 설명 없음"


def test_agent_doc_states_the_correct_row_counts():
    """행수를 헤딩에서 정확히 뽑아 대조한다.

    `f"{count}행" in text` 는 207 을 1207 로 바꿔도 통과한다. 부분 문자열이기
    때문이다. 돌연변이로 실제 확인했다.
    """
    text = AGENT_DOC.read_text(encoding="utf-8")
    stated = {m.group(1): int(m.group(2).replace(",", "")) for m in _HEADING.finditer(text)}
    assert stated == TABLE_ROW_TARGETS

    total = re.search(r"8개 테이블 ([\d,]+)행", text)
    assert total, "총 행수 문장이 없습니다"
    assert int(total.group(1).replace(",", "")) == sum(TABLE_ROW_TARGETS.values())


def test_agent_doc_lists_all_five_mismatch_devices():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for marker in DEVICE_TARGETS:
        assert marker[0] in text, marker


def test_agent_doc_states_the_forbidden_columns():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for column in ("mes_result", "scrap_qty", "operator", "in_qty", "out_qty"):
        assert f"`{column}`" in text


def test_readme_documents_the_build_and_test_commands():
    text = README.read_text(encoding="utf-8")
    assert "build_notebook.py" in text
    assert "-m pytest" in text
    assert "-m live" in text
    assert "qms_lakehouse_seed.ipynb" in text


def test_documents_contain_no_api_key():
    for path in (README, AGENT_DOC):
        assert "changjuahn" not in path.read_text(encoding="utf-8")
