"""에이전트가 읽는 문서가 실제 시스템과 맞는지 검사한다.

`data-agent-schema.md` 는 Foundry 에이전트에게 주는 지시문이다. 여기 적힌
테이블 이름이 틀리면 에이전트는 없는 테이블을 질의한다. 사람이 읽는 README 와
달리 **아무도 오타를 눈치채지 못한 채** 실습 중에 실패한다.

실제로 `qms_inspection_result` 라고 적혀 있었다. 그런 테이블은 없다. 진짜
이름은 `qms_inspection` 이다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DOC = ROOT / "data-agent-schema.md"

# QMS 레이크하우스가 실제로 만드는 테이블.
#
# ⚠️ 이 목록은 브랜치 `changju-ahn-qms-fabric-lakehouse-design` 의
# `customizing/fabric/qms-lakehouse/src/qms_schema.py` 의 `TABLE_DDL` 키에서
# 떴다. QMS 는 다른 브랜치에 있어 여기서 직접 읽을 수 없으므로 복제해 두었다.
# QMS 가 테이블을 더하거나 이름을 바꾸면 이 목록도 함께 고쳐야 한다.
#
# 복제본이라 QMS 와 이 목록이 **같은 방향으로 함께** 틀릴 수 있다. 그래도
# 오타는 잡는다. 그게 이 테스트의 목적이다.
QMS_TABLES = frozenset(
    {
        "qms_defect_code",
        "qms_disposition",
        "qms_incoming_inspection",
        "qms_inspection",
        "qms_inspection_spec",
        "qms_inspector",
        "qms_measurement",
        "qms_nonconformance",
    }
)


def test_every_qms_table_named_in_the_doc_actually_exists():
    """문서가 부르는 QMS 테이블이 전부 실재해야 한다."""
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    # 컬럼 참조(`qms_inspection.mes_process_result_id`)에서는 점 앞까지만 잡힌다.
    # QMS 컬럼 중 `qms_` 로 시작하는 것은 없으므로 이 패턴은 테이블만 고른다.
    named = set(re.findall(r"qms_[a-z_]+", text))

    unknown = named - QMS_TABLES
    assert not unknown, (
        f"문서가 없는 QMS 테이블을 가리킨다: {sorted(unknown)}\n"
        f"실재하는 것: {sorted(QMS_TABLES)}\n"
        "에이전트는 이 문서를 그대로 믿고 질의한다."
    )
    assert named, "문서에 QMS 테이블 언급이 하나도 없다 — 교차 질의 안내가 사라졌다"


def test_the_doc_points_at_the_direct_mes_to_qms_key():
    """MES→QMS 는 `lot_id` 가 아니라 런 id 로 이어야 한다.

    `lot_id` 로만 이으면 그 로트의 모든 공정 검사가 걸려서 어느 공정의
    검사였는지 흐려진다. 에이전트가 엉뚱한 공정의 판정을 근거로 답하게 된다.
    """
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    assert "mes_process_result_id" in text, (
        "MES→QMS 직접 연결 키가 문서에 없다"
    )
    assert re.search(r"mes_process_result_id.*(null|NULL|None)", text, re.DOTALL), (
        "이 키가 null 일 수 있다는 점(OQC·PCS·EQV)을 알려야 한다. "
        "모르면 에이전트는 조인 결과가 빈 것을 '검사 없음' 으로 오해한다."
    )


def test_the_doc_does_not_promise_columns_fdc_lacks():
    """FDC 에 없는 컬럼을 있는 것처럼 적으면 안 된다.

    이 문서의 핵심은 '답할 수 없는 것' 절이다. 센서는 어떤 로트가 올라와
    있는지 모른다. 그 경계가 흐려지면 에이전트가 추측으로 답한다.
    """
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    marker = "**답할 수 없는**"
    section = text.split(marker)[1] if marker in text else ""
    assert section, "'답할 수 없는 것' 절이 사라졌다"

    for column in ("lot_id", "product_code", "defect_code"):
        assert column in section, (
            f"`{column}` 이 FDC 에 없다는 안내가 사라졌다. "
            "에이전트가 있다고 믿고 추측한다."
        )


def test_the_doc_warns_that_metrology_has_no_sensor_data():
    """METRO 한계가 에이전트 문서에도 있어야 한다.

    README 에는 두 곳에 있었지만 이 문서에는 없었다. README 는 사람이 읽고
    이 문서는 에이전트가 읽는다. 에이전트가 "METRO 공정의 챔버 온도" 를
    받으면 빈 결과를 받고 그것을 "이상 없음" 으로 오해한다.
    """
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "METRO" in text, "계측 공정에 FDC 가 없다는 안내가 없다"


def test_the_doc_tells_the_agent_not_to_memorize_the_anchor():
    """앵커는 배포마다 움직인다. 절대 날짜를 외우면 안 된다.

    MES 는 앵커를 `utcNow()` 로 배포 시점에 고정한다. 20명이 각자 배포하면
    20개의 서로 다른 시간축이 생긴다. 이 문서에 날짜를 박아두면 19명에게
    틀린 답이 나간다.
    """
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    assert "앵커" in text, "시간축 절이 사라졌다"
    assert re.search(r"앵커.*(움직|바뀝|이동)", text), (
        "앵커가 배포마다 움직인다는 경고가 없다"
    )

    # 날짜를 박아두면 이 테스트가 잡는다. 재배포 후 조용히 틀려지는 종류다.
    hardcoded = re.findall(r"20\d\d-\d\d-\d\d", text)
    assert not hardcoded, (
        f"에이전트 문서에 절대 날짜가 박혀 있다: {hardcoded}. "
        "앵커는 배포마다 움직이므로 참가자마다 다른 날짜를 본다."
    )


def test_the_doc_separates_completed_events_from_deadlines():
    """QMS 의 미래 데이터에는 두 종류가 있다.

    완료된 검사·판정이 미래에 있으면 모순이지만, 조치 기한은 미래가 정상이다.
    에이전트가 이걸 구별하지 못하면 "기한이 미래인 부적합" 을 데이터 오류로
    보고하거나, 반대로 미래의 완료된 검사를 그대로 답한다.
    """
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    assert "due_date" in text, "조치 기한이 미래라는 안내가 없다"
    assert "effectiveness_check_date" in text, "예정 점검일 안내가 없다"
    assert re.search(r"(due_date|기한).*(미래|아직)", text, re.DOTALL), (
        "미래가 정상인 컬럼과 아닌 컬럼의 구별이 없다"
    )


# --------------------------------------------------------------------------
# 스펙 조인의 복합키
#
# QMS 가 자기 검증기에서 "손으로 적은 컬럼 목록 밖은 아무도 보지 않는다"를
# 찾아내고, 범위를 넓히자마자 검사원 자격 만료라는 더 큰 결함이 나왔다고
# 알려 왔다. 같은 방법을 여기 적용했다. 검증기가 보는 범위 밖에서
# reading.unit 과 spec.unit 을 대조해 보니 CHAMBER_PRESSURE 가 걸렸다.
#
# 데이터 자체는 정확했다. 위험한 건 조인하는 쪽이다.
# --------------------------------------------------------------------------


def _doc() -> str:
    return SCHEMA_DOC.read_text(encoding="utf-8")


def _spec_rows():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.fdc_sensors import build_sensor_spec_rows

    return build_sensor_spec_rows()


def test_the_same_sensor_code_really_does_carry_different_units():
    """복합키 경고가 실재하는 함정을 가리키는지 확인한다.

    이 성질이 사라지면 문서의 경고가 과잉이 되고, 반대로 이 성질이 있는데
    경고가 없으면 조인을 틀린 사람이 조용히 오답을 얻는다. 둘을 묶어 둔다.

    단위가 갈리는 쪽이 값 범위만 갈리는 쪽보다 위험하다. Furnace 1050°C 대
    CVD 420°C 는 자릿수가 달라 눈에 띄지만, mTorr 대 Torr 는 값이 그럴듯해
    보인다.
    """
    from collections import defaultdict

    units = defaultdict(set)
    for row in _spec_rows():
        units[row["sensor_code"]].add(row["unit"])

    split = {code: u for code, u in units.items() if len(u) > 1}
    assert split, (
        "같은 sensor_code 가 단위까지 갈리는 경우가 없어졌다."
        " 그렇다면 data-agent-schema.md 의 단위 경고를 지워야 한다"
    )

    doc = _doc()
    for code in split:
        # 부분 문자열로 찾으면 안 된다. 이름 뒤에 무엇을 붙여도 통과한다 --
        # 돌연변이로 CHAMBER_PRESSURE 를 CHAMBER_PRESSURE_X 로 바꿨더니
        # 그대로 통과했다. README 의 `.drop table` 검사에서 같은 실수를
        # 한 직후였다. 밑줄은 단어 문자라 \b 가 경계를 잡아 준다.
        assert re.search(rf"\b{re.escape(code)}\b", doc), (
            f"{code} 는 설비 유형마다 단위가 다른데({split[code]})"
            " 에이전트 문서가 그 사실을 알려주지 않는다"
        )


def test_the_schema_doc_joins_the_spec_table_on_the_full_key():
    """스펙 조인 예시가 복합키를 써야 한다.

    `on sensor_code` 로 줄이면 판독값의 40.8% 가 다른 설비 유형의 한계치에
    붙는다. 쿼리는 성공하고 행 수도 그럴듯하다. 값만 틀린다.

    문서가 지금은 맞다. 맞는 것을 고정해 두지 않으면 다음 사람이 예시를
    줄여도 아무도 모른다.
    """
    import re

    doc = _doc()
    joins = re.findall(r"join[^\n]*fdc_sensor_spec[^\n]*", doc)
    assert joins, "스펙 조인 예시가 사라졌다"

    for line in joins:
        assert "eqp_type" in line and "sensor_code" in line, (
            f"스펙 조인이 복합키가 아니다: {line!r}."
            " sensor_code 만으로는 유일하지 않아 40.8% 가 틀린 한계치에 붙는다"
        )


def _joined_against_code_only():
    """`on sensor_code` 로 조인했을 때의 판독·스펙 짝을 만든다."""
    import json
    import sys
    from collections import defaultdict

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from src.fdc_generator import build_readings
    from src.fdc_runs import span
    from src.fdc_sensors import build_sensor_spec_rows
    from src.mes_probe import MesFacts

    fixture = root / "tests" / "fixtures" / "mes_facts.json"
    facts = MesFacts.from_dict(json.loads(fixture.read_text(encoding="utf-8")))
    rows = build_readings(facts, *span(facts))

    by_code = defaultdict(list)
    for spec in build_sensor_spec_rows():
        by_code[spec["sensor_code"]].append(spec)

    return rows, by_code


def _rejudge(value: float, spec: dict) -> str:
    """그 스펙의 한계치로 다시 판정한다. classify 와 같은 규칙이다."""
    if value < spec["alarm_min"] or value > spec["alarm_max"]:
        return "Alarm"
    if value < spec["normal_min"] or value > spec["normal_max"]:
        return "Warning"
    return "Normal"


def test_joining_the_spec_on_code_alone_really_is_dangerous():
    """경고가 실재하는 위험을 가리키는지 불변식으로 확인한다.

    QMS 가 배수 자체보다 불변식을 주로 검사한다고 알려 왔다. 숫자는 픽스처를
    다시 뜨면 흔들리지만, 경고할 이유가 남아 있는지는 그대로 물을 수 있다.

    이 테스트가 실패하면 위험이 사라진 것이다. 그때는 데이터를 고칠 게
    아니라 **문서의 경고를 지워야 한다.** 과잉 경고는 다른 경고까지
    무디게 만든다.
    """
    rows, by_code = _joined_against_code_only()

    joined = sum(len(by_code[r["sensor_code"]]) for r in rows)
    pairs = [
        (r, s)
        for r in rows
        for s in by_code[r["sensor_code"]]
        if s["eqp_type"] != r["eqp_type"]
    ]

    assert joined > len(rows) * 2, (
        "코드만으로 조인해도 행이 거의 안 늘어난다."
        " 부풀림 경고가 근거를 잃었으니 문서에서 지워야 한다"
    )
    assert len(pairs) / joined > 0.5, (
        "틀린 짝이 절반도 안 된다. 경고 수위를 낮춰야 한다"
    )

    flipped = [(r, s) for r, s in pairs if _rejudge(r["value"], s) != r["status"]]
    assert flipped, (
        "틀린 짝으로 다시 판정해도 결과가 안 바뀐다면 한계치가 사실상 같다는"
        " 뜻이다. 그렇다면 복합키를 고집할 이유가 없다"
    )

    # 단위가 다르면 알아챌 단서가 있다. 같으면 없다.
    blind = [(r, s) for r, s in flipped if s["unit"] == r["unit"]]
    assert len(blind) / len(flipped) > 0.5, (
        "뒤집힌 판정 대부분이 단위 차이를 동반한다면 참가자가 알아챌 수 있다."
        " 문서에서 '단위가 같아 더 안 보인다'는 서술을 조정해야 한다"
    )


def test_the_documented_mis_join_damage_is_actually_measured():
    """문서가 인용한 숫자가 실제 데이터와 맞아야 한다.

    QMS 가 `assert len(results) == 10` 처럼 개수만 세는 검사의 함정을 알려
    왔다. 문자열이 문서에 있는지만 보면 숫자가 낡아도 통과한다. 참가자는
    낡은 숫자로 위험을 잰다.

    이 검사를 만들자마자 문서가 틀린 것이 드러났다. 처음엔 "판독값의 40.8%
    가 다른 유형의 한계치에 붙는다"고 적었는데, 그건 스펙에서 코드마다 한
    행만 고른다고 가정한 값이었다. `inner join` 은 고르지 않고 **모든 짝을
    만든다.** 실제로는 결과가 3.6배로 부풀고 그중 72%가 쓰레기다.

    정확한 값을 못 박지는 않는다. 앵커를 고정해 재배포하면 픽스처를 다시
    뜨고 그때 설비 구성이 조금 달라진다. 크게 어긋날 때만 잡는다. 위험이
    실재하는지 자체는 위 불변식 테스트가 본다.
    """
    doc = _doc()
    inflation = re.search(r"결과가 (\d+(?:\.\d+)?)배로 부풀", doc)
    garbage = re.search(r"그중 (\d+(?:\.\d+)?)%\s*가 엉뚱한", doc)
    flip = re.search(r"(\d+(?:\.\d+)?)%\s*에서 판정이\s*\n?뒤집힙니다", doc)
    blind = re.search(r"뒤집힌 판정의\s*\n?(\d+(?:\.\d+)?)%\s*가 이렇게 단위가 같은", doc)

    assert inflation, "조인을 틀렸을 때 행이 부푼다는 사실이 문서에서 사라졌다"
    assert garbage, "부푼 행의 몇 %가 쓰레기인지가 문서에서 사라졌다"
    assert flip, "판정이 뒤집힌다는 사실이 문서에서 사라졌다"
    assert blind, "뒤집힘 대부분이 단위가 같아 안 보인다는 안내가 사라졌다"

    rows, by_code = _joined_against_code_only()
    joined = sum(len(by_code[r["sensor_code"]]) for r in rows)
    pairs = [
        (r, s)
        for r in rows
        for s in by_code[r["sensor_code"]]
        if s["eqp_type"] != r["eqp_type"]
    ]
    flipped = [(r, s) for r, s in pairs if _rejudge(r["value"], s) != r["status"]]
    same_unit = [(r, s) for r, s in flipped if s["unit"] == r["unit"]]

    for label, actual, quoted, tol in (
        ("부풀림 배수", joined / len(rows), float(inflation.group(1)), 0.5),
        ("오짝 비율", len(pairs) / joined * 100, float(garbage.group(1)), 5.0),
        ("판정 뒤집힘", len(flipped) / len(pairs) * 100, float(flip.group(1)), 5.0),
        ("단위 같은 비율", len(same_unit) / len(flipped) * 100, float(blind.group(1)), 5.0),
    ):
        assert abs(actual - quoted) <= tol, (
            f"{label}: 문서는 {quoted} 인데 실제로는 {actual:.1f} 다."
            " 참가자가 낡은 숫자로 위험을 잰다."
        )


def test_the_doc_tells_the_agent_the_verdict_is_already_in_the_row():
    """판정에 조인이 필요 없다는 사실을 알려야 한다.

    QMS 가 같은 자리에서 경고 대신 조인을 없애는 쪽을 골랐다고 알려 왔다.
    계측 행이 규격을 복제해 두고 있어 애초에 조인할 이유가 없었다는
    것이다. 여기도 같다. `status` 가 그 설비 유형의 한계치로 이미 계산돼
    있고 `unit` 도 판독 행에 있다.

    경고만 적어 두면 "조심해서 조인"하게 된다. 조인할 이유가 없다는 걸
    알려주는 편이 낫다. 밟지 않은 지뢰는 터지지 않는다.

    복제한 값이 스펙과 어긋나면 이 안내가 근거를 잃으므로 함께 확인한다.
    """
    doc = _doc()

    assert re.search(r"판정에는 조인이 필요 없습니다", doc), (
        "status 가 이미 계산돼 있다는 안내가 사라졌다."
        " 그러면 참가자가 굳이 스펙과 조인해 직접 판정하고, 복합키를 놓치면"
        " 9% 에서 틀린 답을 얻는다"
    )

    rows, by_code = _joined_against_code_only()

    # 판독 행의 status 와 unit 이 자기 설비 유형 스펙과 일치해야 안내가 성립한다.
    mismatched = 0
    for row in rows:
        own = next(
            s for s in by_code[row["sensor_code"]] if s["eqp_type"] == row["eqp_type"]
        )
        if own["unit"] != row["unit"] or _rejudge(row["value"], own) != row["status"]:
            mismatched += 1

    assert mismatched == 0, (
        f"판독 {mismatched} 행의 status/unit 이 자기 스펙과 다르다."
        " 그렇다면 '조인이 필요 없다'는 안내가 거짓이 된다"
    )
