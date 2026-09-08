import json
from datetime import timedelta
from pathlib import Path

import pytest

from src.fdc_generator import align_to_grid
from src.fdc_runs import span
from src.mes_probe import MesFacts

FIXTURE = Path(__file__).parent / "fixtures" / "mes_facts.json"


@pytest.fixture(scope="session")
def facts() -> MesFacts:
    """실 MES에서 뜬 고정 스냅샷. 오프라인 테스트 전체가 이 위에서 돈다."""
    with FIXTURE.open(encoding="utf-8") as fh:
        return MesFacts.from_dict(json.load(fh))


@pytest.fixture(scope="session")
def busy_window(facts):
    """공정이력이 실제로 있는 24시간.

    벽시계 상수를 쓰면 픽스처를 다시 뜰 때마다 창이 구간 밖으로 나간다.
    build_readings 를 부르는 테스트는 전부 이 창을 쓴다.
    """
    lo, hi = span(facts)
    start = align_to_grid(lo)
    return start, min(start + timedelta(hours=24), hi)
