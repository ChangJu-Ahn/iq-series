import json
from pathlib import Path

import pytest

from src.mes_probe import MesFacts

FIXTURE = Path(__file__).parent / "fixtures" / "mes_facts.json"


@pytest.fixture(scope="session")
def facts() -> MesFacts:
    """실 MES에서 뜬 고정 스냅샷. 오프라인 테스트 전체가 이 위에서 돈다."""
    with FIXTURE.open(encoding="utf-8") as fh:
        return MesFacts.from_dict(json.load(fh))
