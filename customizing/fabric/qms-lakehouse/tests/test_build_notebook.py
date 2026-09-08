from pathlib import Path

import pytest

import build_notebook as builder

ROOT = Path(__file__).resolve().parents[1]


def test_strip_removes_single_and_multiline_local_imports():
    source = (
        "from __future__ import annotations\n"
        "import json\n"
        "from src.mes_client import MesSnapshot\n"
        "from src.qms_reference import (\n"
        "    SEED_INSPECTION,\n"
        "    SEED_MASTERS,\n"
        ")\n"
        "VALUE = 1\n"
    )
    stripped = builder.strip_local_imports(source)
    assert "src." not in stripped
    assert "import json" in stripped
    assert "VALUE = 1" in stripped


def test_strip_keeps_standard_library_imports():
    source = "import datetime as dt\nimport random\nfrom dataclasses import dataclass\n"
    assert builder.strip_local_imports(source) == source


def test_notebook_has_no_local_imports_left():
    notebook = builder.build_notebook(ROOT)
    for cell in notebook.cells:
        assert "from src." not in cell.source
        assert "import src" not in cell.source


def test_notebook_starts_with_markdown_and_has_a_parameters_cell():
    notebook = builder.build_notebook(ROOT)
    assert notebook.cells[0].cell_type == "markdown"
    assert notebook.cells[-1].cell_type == "markdown"
    tagged = [c for c in notebook.cells if "parameters" in c.metadata.get("tags", [])]
    assert len(tagged) == 1
    assert "TARGET_SCHEMA" in tagged[0].source
    assert "LAKEHOUSE_NAME" not in tagged[0].source


def test_load_cell_writes_a_bare_table_name_by_default():
    """2단 이름 '레이크하우스.테이블'은 Spark가 '스키마.테이블'로 읽어 실패한다.

    대상 레이크하우스는 노트북 Attach 로 정해지므로 이름을 붙이면 안 된다.
    """
    notebook = builder.build_notebook(ROOT)
    load = next(c.source for c in notebook.cells if "saveAsTable" in c.source)
    params = next(
        c.source for c in notebook.cells if "parameters" in c.metadata.get("tags", [])
    )
    assert 'target = f"{TARGET_SCHEMA}.{name}" if TARGET_SCHEMA else name' in load
    assert 'TARGET_SCHEMA = ""' in params


def test_notebook_contains_no_api_key_literal():
    notebook = builder.build_notebook(ROOT)
    joined = "\n".join(c.source for c in notebook.cells)
    assert "changjuahn" not in joined
    assert 'MES_API_KEY = ""' in joined


def test_inlined_module_cells_execute_and_expose_the_entry_points():
    notebook = builder.build_notebook(ROOT)
    module_cells = [
        c.source for c in notebook.cells if c.metadata.get("qms_cell") == "module"
    ]
    assert len(module_cells) == len(builder.MODULE_ORDER)
    namespace: dict = {}
    # 셀 단위로 실행한다. 각 모듈이 'from __future__ import annotations' 로 시작하는데,
    # 이어 붙여 한 번에 컴파일하면 future 문이 파일 첫머리가 아니라며 SyntaxError 가 난다.
    # Jupyter 도 셀을 각각 컴파일하므로 이쪽이 실제 실행과 같다.
    for index, source in enumerate(module_cells):
        exec(compile(source, f"<cell {index}>", "exec"), namespace)
    for symbol in ("MesClient", "build_all_tables", "validate", "TABLE_DDL", "to_rows"):
        assert symbol in namespace


def test_notebook_uses_overwrite_mode_and_the_qms_prefix():
    notebook = builder.build_notebook(ROOT)
    joined = "\n".join(c.source for c in notebook.cells)
    assert 'WRITE_MODE = "overwrite"' in joined
    assert 'TABLE_PREFIX = "qms_"' in joined


@pytest.mark.stale_artifact
def test_written_notebook_is_valid_and_current():
    """디스크의 노트북이 지금 src 로 만든 것과 같은가.

    이 테스트는 src 를 한 글자만 고쳐도 실패한다. 노트북이 생성물이기 때문이고,
    그건 의도한 동작이다. 다만 돌연변이 검증에서는 함정이 된다. 무해한 주석 한
    줄을 넣어도 "1 failed" 가 나오므로, 실패 건수만 보면 불변식이 잡은 것과
    구별되지 않는다. 실제로 열화 시그마를 12배로 키운 돌연변이가 이 테스트
    하나만 실패시켰고, 하마터면 "잡혔다" 로 읽을 뻔했다.

    그래서 stale_artifact 마커를 달았다. 돌연변이를 돌릴 때는 src 를 고친 뒤
    build_notebook.py 를 다시 돌려 이 테스트를 정상 통과시키고, 그러고도 남는
    실패만 진짜 검출로 센다.
    """
    import nbformat

    path = ROOT / "qms_lakehouse_seed.ipynb"
    assert path.exists(), "build_notebook.py 를 실행해 노트북을 생성하세요."
    on_disk = nbformat.read(path, as_version=4)
    nbformat.validate(on_disk)
    fresh = builder.build_notebook(ROOT)
    assert [c.source for c in on_disk.cells] == [c.source for c in fresh.cells], (
        "노트북이 src 와 어긋났습니다. python3 build_notebook.py 를 다시 도세요. "
        "이 실패는 코드의 결함이 아니라 산출물 미갱신입니다."
    )
