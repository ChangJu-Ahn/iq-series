import json
from pathlib import Path

import pytest

from src.mes_client import MesSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "mes_snapshot.json"


@pytest.fixture(scope="session")
def snapshot() -> MesSnapshot:
    """실 MES에서 뜬 고정 스냅샷. 태스크 2~8의 모든 테스트가 이 위에서 돈다."""
    with FIXTURE.open(encoding="utf-8") as fh:
        return MesSnapshot.from_dict(json.load(fh))
