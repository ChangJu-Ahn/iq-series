from src.qms_schema import (
    TABLE_COLUMNS,
    TABLE_DDL,
    build_all_tables,
    ddl_columns,
    to_rows,
)
from src.qms_validate import TABLE_ROW_TARGETS


def test_every_target_table_has_columns_and_ddl():
    assert set(TABLE_COLUMNS) == set(TABLE_DDL) == set(TABLE_ROW_TARGETS)


def test_ddl_column_names_match_declared_column_order():
    for name, ddl in TABLE_DDL.items():
        assert ddl_columns(ddl) == TABLE_COLUMNS[name]


def test_generated_rows_match_declared_columns_including_order(tables):
    for name, rows in tables.items():
        assert tuple(rows[0]) == TABLE_COLUMNS[name]
        for row in rows:
            assert set(row) == set(TABLE_COLUMNS[name])


def test_to_rows_produces_tuples_in_column_order(tables):
    for name, rows in tables.items():
        tuples = to_rows(name, rows)
        assert len(tuples) == len(rows)
        assert all(len(t) == len(TABLE_COLUMNS[name]) for t in tuples)
        first_column = TABLE_COLUMNS[name][0]
        assert tuples[0][0] == rows[0][first_column]


def test_build_all_tables_matches_the_fixture(snapshot, tables):
    assert build_all_tables(snapshot) == tables


def test_no_ddl_declares_a_forbidden_column():
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    for name, ddl in TABLE_DDL.items():
        assert not (set(ddl_columns(ddl)) & forbidden), name
