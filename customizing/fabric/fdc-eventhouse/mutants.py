"""돌연변이를 걸어 테스트가 실제로 잡는지 확인한다.

돌연변이 테스트는 세 가지로 조용히 실패한다. 전부 겪었다.

1. **치환이 안 걸린다.** 원본 문자열이 안 맞으면 파일은 그대로인데 테스트는
   통과한다. 이걸 "테스트가 견뎠다"로 읽으면 없는 그물을 있다고 믿는다.
2. **걸렸는데 값이 안 변한다.** `old == new` 인 실수. 1번과 결과가 같다.
3. **코드가 깨진다.** `NameError` 로 45개가 실패하면 "잡혔다"로 보이지만
   불변식이 잡은 게 아니다. 크래시는 미적용과 같이 다뤄야 한다.

3번은 QMS 세션이 알려 줬다. 앞의 둘은 문자열만 봐도 막지만 3번은 결과를
읽어야 안다. 그래서 실패 이유가 `AssertionError` 인지 확인하고, 관심 지표를
함께 출력해 값이 실제로 움직였는지 눈으로 본다.

    python3 mutants.py            # 전부
    python3 mutants.py anchor     # 그룹 하나
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass
class Mutant:
    name: str
    path: str
    old: str
    new: str
    tests: str
    probe: str = ""
    """돌연변이가 걸린 상태에서 출력할 지표. 값이 움직였는지 눈으로 본다."""
    rebuild: bool = False
    """산출물을 다시 만들어야 하는지. build_notebook.py 를 고쳐도 노트북을
    재생성하지 않으면 테스트가 옛 .ipynb 를 읽어 조용히 통과한다. 이것이
    네 번째 실패 방식이다 — 적용됐고 크래시도 아닌데 안 잡힌다."""


@dataclass
class Result:
    name: str
    verdict: str
    detail: str = ""
    probe: str = ""
    reasons: list[str] = field(default_factory=list)


def _run(mutant: Mutant) -> Result:
    target = ROOT / mutant.path
    original = target.read_text(encoding="utf-8")

    if mutant.old not in original:
        return Result(mutant.name, "미적용", "치환문자열이 파일에 없다")
    mutated = original.replace(mutant.old, mutant.new, 1)
    if mutated == original:
        return Result(mutant.name, "미적용", "치환은 됐는데 값이 그대로다")

    target.write_text(mutated, encoding="utf-8")
    try:
        if mutant.rebuild:
            built = subprocess.run(
                [sys.executable, "build_notebook.py"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            if built.returncode != 0:
                return Result(
                    mutant.name, "크래시", "재생성이 실패했다 — 불변식이 아니라 빌드가 막았다"
                )
        probe = ""
        if mutant.probe:
            got = subprocess.run(
                [sys.executable, "-c", mutant.probe],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            probe = (got.stdout or got.stderr).strip().splitlines()
            probe = probe[-1] if probe else ""

        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-x", mutant.tests],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
    finally:
        target.write_text(original, encoding="utf-8")
        if mutant.rebuild:
            # 원본만 되돌리면 돌연변이가 박힌 산출물이 남는다. 다음 테스트가
            # 그걸 읽고 엉뚱하게 실패한다. 되돌린 원본으로 다시 만든다.
            subprocess.run(
                [sys.executable, "build_notebook.py"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )

    summary = run.stdout.strip().splitlines()
    summary = summary[-1] if summary else ""
    reasons = sorted(
        {
            line.split(":")[0].removeprefix("E ").strip()
            for line in run.stdout.splitlines()
            if line.startswith("E ") and ":" in line
        }
    )

    if run.returncode == 0:
        return Result(mutant.name, "안 잡힘", summary, probe, reasons)

    # 크래시로 죽은 것을 잡힌 것으로 읽으면 안 된다. 다만 "예외가 났다"는
    # 것만으로는 못 가른다. 이 모듈의 가드는 RuntimeError 를 던지는 것이
    # 정상 동작이고, pytest.raises 가 그걸 못 받으면 Failed 로 뜬다. 둘 다
    # 불변식이 일한 결과다.
    #
    # 갈라야 하는 것은 코드가 깨져 테스트가 도달하지 못한 경우다. 이름이
    # 없거나 임포트가 실패하면 무엇을 검사하려 했든 상관없이 죽는다.
    CRASH = ("NameError", "ImportError", "ModuleNotFound", "SyntaxError", "AttributeError", "TypeError")
    if " error" in summary or "errors" in summary:
        return Result(mutant.name, "크래시", summary, probe, reasons)
    if any(r.startswith(CRASH) for r in reasons):
        return Result(mutant.name, "크래시", summary, probe, reasons)

    return Result(mutant.name, "잡힘", summary, probe, reasons)


SCHEMA = "src/fdc_schema.py"
DOC = "data-agent-schema.md"
BUILD = "build_notebook.py"
README = "README.md"

DOC_TESTS = "tests/test_data_agent_schema.py"
NB_TESTS = "tests/test_notebook_execution.py"
SCHEMA_TESTS = "tests/test_fdc_schema.py"

_BLAME = """
import sys; sys.path.insert(0, '.')
from collections import Counter, defaultdict
import json
from pathlib import Path
from src.fdc_generator import build_readings
from src.fdc_runs import span
from src.fdc_sensors import build_sensor_spec_rows
from src.mes_probe import MesFacts
facts = MesFacts.from_dict(json.loads(Path('tests/fixtures/mes_facts.json').read_text()))
rows = build_readings(facts, *span(facts))
by = defaultdict(list)
for s in build_sensor_spec_rows(): by[s['sensor_code']].append(s)
def judge(v, s):
    if v < s['alarm_min'] or v > s['alarm_max']: return 'Alarm'
    if v < s['normal_min'] or v > s['normal_max']: return 'Warning'
    return 'Normal'
inflate, flip = Counter(), Counter()
for r in rows:
    n = len(by[r['sensor_code']])
    if n > 1: inflate[r['sensor_code']] += n - 1
    for s in by[r['sensor_code']]:
        if s['eqp_type'] != r['eqp_type'] and judge(r['value'], s) != r['status']:
            flip[r['sensor_code']] += 1
silent = sum(v for k, v in inflate.items() if not flip[k])
tot = sum(inflate.values()) or 1
print(f"조용한 부풀림 {silent/tot:.0%} · 뒤집는 센서 {len(flip)}종")
"""

_JOIN_KEYS = """
import sys; sys.path.insert(0, '.')
from src.fdc_schema import READING_SCHEMA, SPEC_SCHEMA
r = {c for c, _ in READING_SCHEMA}
print('조인 필요 컬럼', len([c for c, _ in SPEC_SCHEMA if c not in r]), '개')
"""

GROUPS: dict[str, list[Mutant]] = {
    "join": [
        Mutant(
            "조인필요 절 삭제",
            DOC,
            "### 조인이 필요한 질문도 있습니다",
            "### 참고",
            DOC_TESTS,
        ),
        Mutant(
            "목록에서 normal_max 만 빼기",
            DOC,
            "`normal_min` · `normal_max` ·",
            "`normal_min` ·",
            DOC_TESTS,
        ),
        Mutant(
            "normal_max 를 판독 행에 복제",
            SCHEMA,
            '    ("run_status", "string"),',
            '    ("run_status", "string"),\n    ("normal_max", "real"),',
            DOC_TESTS,
            _JOIN_KEYS,
        ),
        Mutant(
            "기여도 표에서 CHAMBER_TEMP 줄 삭제",
            DOC,
            "| `CHAMBER_TEMP` | 3 | 19,098 | **19,088** | ❌ 셋 다 `degC` |\n",
            "",
            DOC_TESTS,
        ),
        Mutant(
            "눈먼 비중을 절반으로",
            DOC,
            "`CHAMBER_TEMP` 가 78.2%인데",
            "`CHAMBER_TEMP` 가 38.0%인데",
            DOC_TESTS,
        ),
        Mutant(
            "AMBIENT 뒤집힘을 0 아닌 값으로",
            DOC,
            "| `AMBIENT_TEMP` | 7 | 121,236 | 0 | — (범위가 같음) |",
            "| `AMBIENT_TEMP` | 7 | 121,236 | 999 | — (범위가 같음) |",
            DOC_TESTS,
        ),
        Mutant(
            "판정에 조인 불필요 안내 삭제",
            DOC,
            "### 판정에는 조인이 필요 없습니다",
            "### 그 밖에",
            DOC_TESTS,
        ),
        Mutant(
            "CHAMBER_TEMP 를 유형마다 다른 단위로",
            "src/fdc_sensors.py",
            'SensorDef("CHAMBER_TEMP", "챔버 온도", "degC", 420.0,',
            'SensorDef("CHAMBER_TEMP", "챔버 온도", "K", 420.0,',
            DOC_TESTS,
            _BLAME,
        ),
    ],
    "anchor": [
        Mutant(
            "드리프트 가드 제거",
            BUILD,
            "if abs(ANCHOR_DRIFT) > timedelta(minutes=5):",
            "if False:",
            NB_TESTS,
            rebuild=True,
        ),
        Mutant(
            "naive watermark 를 UTC 로 단정",
            BUILD,
            "LOADED_RUN_TS = LOADED_RUN_TS.astimezone(timezone.utc)",
            "LOADED_RUN_TS = LOADED_RUN_TS.replace(tzinfo=timezone.utc)",
            NB_TESTS,
            rebuild=True,
        ),
        Mutant(
            "드리프트 임계값을 하루로",
            BUILD,
            "> timedelta(minutes=5):",
            "> timedelta(days=1):",
            NB_TESTS,
            rebuild=True,
        ),
        Mutant(
            "절댓값을 떼어 뒤로 간 앵커를 통과",
            BUILD,
            "if abs(ANCHOR_DRIFT) >",
            "if (ANCHOR_DRIFT) >",
            NB_TESTS,
            rebuild=True,
        ),
    ],
    "readme": [
        Mutant(
            "복구 절에서 drop table 삭제",
            README,
            ".drop table fdc_sensor_reading",
            "(테이블 정리)",
            SCHEMA_TESTS,
        ),
        Mutant(
            "재배포 명령에서 mesAnchor 제거",
            README,
            "-p mesAnchor=",
            "-p other=",
            SCHEMA_TESTS,
        ),
    ],
}


def main() -> int:
    wanted = sys.argv[1:] or list(GROUPS)
    bad = 0
    for group in wanted:
        if group not in GROUPS:
            print(f"모르는 그룹: {group}. 있는 것: {', '.join(GROUPS)}")
            return 2
        print(f"\n=== {group} ===")
        for mutant in GROUPS[group]:
            result = _run(mutant)
            mark = {"잡힘": "  ", "안 잡힘": "★ ", "미적용": "! ", "크래시": "! "}[
                result.verdict
            ]
            print(f"{mark}{result.name:34} {result.verdict}")
            if result.probe:
                print(f"     지표 {result.probe}")
            if result.verdict != "잡힘":
                bad += 1
                if result.detail:
                    print(f"     {result.detail}")
                for reason in result.reasons[:3]:
                    print(f"     {reason}")
    print()
    print("전부 불변식이 잡았다" if not bad else f"확인 필요 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
