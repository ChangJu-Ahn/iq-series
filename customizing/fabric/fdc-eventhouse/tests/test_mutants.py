"""돌연변이 도구 자체의 그물.

`mutants.py` 는 테스트를 검사하는 도구인데, 도구가 조용히 망가지면 없는
그물을 있다고 믿게 된다. 여섯 번째 실패 방식(적용됐는데 지표가 안 움직임)을
찾은 것이 실제로 그런 경우였다 — 다섯 가드를 전부 통과하는데 돌연변이가
아무 일도 안 했다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutants import GROUPS, _probe_moved  # noqa: E402


def test_probe_movement_is_detected_per_metric():
    """지표 하나만 움직여도 유효, 전부 그대로면 무효."""
    assert _probe_moved("Alarm 1855 (원래 0) · Warning 2 (원래 2)") is True
    assert _probe_moved("Alarm 0 (원래 0) · Warning 2 (원래 2)") is False


def test_probe_movement_ignores_thousands_separator():
    """`102,540` 과 `102540` 은 같은 값이다. 쉼표 때문에 움직였다고 읽으면
    안 움직인 돌연변이를 유효로 통과시킨다."""
    assert _probe_moved("값차이 102,540 (원래 102540)") is False
    assert _probe_moved("값차이 102,541 (원래 102,540)") is True


def test_probe_without_baseline_is_not_judged():
    """기준값을 안 적은 probe 까지 무효로 몰면 그물이 성겨진다.

    `조인 필요 컬럼 11 개` 처럼 기준값이 없는 probe 가 실재한다. 판정을
    거부해야지 무효로 만들면 안 된다.
    """
    assert _probe_moved("조인 필요 컬럼 11 개") is None
    assert _probe_moved("") is None


def test_baseline_probes_carry_a_reference_value():
    """기준값을 적은 probe 가 최소 하나는 있어야 이 가드가 살아 있다.

    전부 기준값을 지우면 `_probe_moved` 가 언제나 None 을 돌려주고 여섯
    번째 실패 방식이 다시 열린다. 검사는 늘 통과하는데 대상이 없는 상태다.
    """
    with_baseline = [
        mutant
        for group in GROUPS.values()
        for mutant in group
        if "(원래" in mutant.probe
    ]
    assert with_baseline, "기준값을 적은 probe 가 하나도 없다"


def test_every_mutant_replaces_something_different():
    """`old == new` 인 돌연변이는 파일을 안 바꾸고도 통과한다."""
    for name, group in GROUPS.items():
        for mutant in group:
            assert mutant.old != mutant.new, f"{name}/{mutant.name}"
            assert mutant.old, f"{name}/{mutant.name} 의 치환문자열이 비었다"


def test_every_mutant_targets_a_file_that_exists():
    root = Path(__file__).resolve().parent.parent
    for name, group in GROUPS.items():
        for mutant in group:
            assert (root / mutant.path).is_file(), f"{name}/{mutant.name} → {mutant.path}"
            assert (root / mutant.tests).is_file(), f"{name}/{mutant.name} → {mutant.tests}"


def test_every_mutant_string_is_present_in_its_target():
    """치환문자열이 파일에 없으면 돌연변이가 통째로 무의미해진다.

    소스를 고치다 보면 문자열이 조용히 어긋난다. 그 상태로 돌리면 "미적용"
    으로 뜨지만, 돌연변이 실행은 오래 걸려 매번 돌리지 않는다. 여기서 싸게
    잡는다.
    """
    root = Path(__file__).resolve().parent.parent
    for name, group in GROUPS.items():
        for mutant in group:
            text = (root / mutant.path).read_text(encoding="utf-8")
            assert mutant.old in text, f"{name}/{mutant.name} 의 치환문자열이 사라졌다"
