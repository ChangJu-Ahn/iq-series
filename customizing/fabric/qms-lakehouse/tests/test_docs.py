from pathlib import Path

from src.qms_schema import TABLE_COLUMNS
from src.qms_validate import DEVICE_TARGETS, TABLE_ROW_TARGETS

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
AGENT_DOC = ROOT / "data-agent-schema.md"


def test_both_documents_exist():
    assert README.exists()
    assert AGENT_DOC.exists()


def test_agent_doc_describes_every_table_and_column():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for table, columns in TABLE_COLUMNS.items():
        assert f"`{table}`" in text, table
        for column in columns:
            assert f"`{column}`" in text, f"{table}.{column}"


def test_agent_doc_states_the_correct_row_counts():
    text = AGENT_DOC.read_text(encoding="utf-8")
    for table, count in TABLE_ROW_TARGETS.items():
        assert f"{count}행" in text, f"{table} {count}"
    assert "1,004행" in text


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
