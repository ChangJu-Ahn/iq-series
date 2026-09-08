import json
from pathlib import Path

import pytest

from src.mes_client import MesSnapshot
from src.qms_schema import build_all_tables

FIXTURE = Path(__file__).parent / "fixtures" / "mes_snapshot.json"


@pytest.fixture(scope="session")
def snapshot() -> MesSnapshot:
    """실 MES에서 뜬 고정 스냅샷. 태스크 2~8의 모든 테스트가 이 위에서 돈다."""
    with FIXTURE.open(encoding="utf-8") as fh:
        return MesSnapshot.from_dict(json.load(fh))


@pytest.fixture(scope="session")
def tables(snapshot) -> dict[str, list[dict]]:
    """8개 테이블 전체. 노트북이 적재하는 것과 같은 함수를 거친다."""
    return build_all_tables(snapshot)
