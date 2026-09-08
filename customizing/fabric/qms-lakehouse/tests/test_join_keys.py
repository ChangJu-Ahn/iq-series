"""조인 키를 잘못 골랐을 때 무슨 일이 생기는지 고정한다.

FDC 쪽에서 sensor_code 단독 조인이 행을 3.6배로 부풀리는 것을 발견했다. 데이터는
정확한데 문서가 왜 복합키여야 하는지를 말하지 않은 것이 원인이었다. QMS 에도 같은
구조가 있는지 재보니 characteristic_code 가 22.65배로 훨씬 심했다.

행이 늘어나면 COUNT 와 AVG 가 통째로 어긋난다. 값 하나가 틀리는 것보다 나쁘다.
결과가 그럴듯해 보여 아무도 의심하지 않기 때문이다.

기대값은 픽스처에서 직접 계산한다. 검증 대상 함수로 기대값을 만들면 생성과 검증
양쪽에 같은 결함이 걸려 상쇄된다.
"""

from collections import Counter, defaultdict
import re
from pathlib import Path

import pytest

from src.qms_schema import build_all_tables

AGENT_DOC = Path(__file__).resolve().parents[1] / "data-agent-schema.md"


@pytest.fixture(scope="module")
def tables(snapshot):
    return build_all_tables(snapshot)


def _join_size(left, right, keys):
    """left 의 각 행이 keys 로 right 에 붙을 때 나오는 행 수. inner join 과 같다."""
    index = Counter(tuple(r[k] for k in keys) for r in right)
    return sum(index[tuple(r[k] for k in keys)] for r in left)


# --- 계측 → 검사기준 --------------------------------------------------------


def test_measurement_carries_its_own_limits_so_no_join_is_needed(tables):
    """규격 이탈 판정에 조인이 필요 없어야 한다.

    이것이 성립하지 않으면 참가자가 조인할 수밖에 없고, 그러면 잘못된 키를 고를
    기회가 생긴다. 조인을 없애는 것이 잘못된 조인을 경고하는 것보다 낫다.
    """
    specs = {s["spec_id"]: s for s in tables["qms_inspection_spec"]}
    for row in tables["qms_measurement"]:
        spec = specs[row["spec_id"]]
        for field in ("unit", "target_value", "lsl", "usl"):
            assert row[field] == spec[field], f"{row['measurement_id']}.{field}"
        assert row["is_out_of_spec"] == (not row["lsl"] <= row["measured_value"] <= row["usl"])


def test_spec_id_join_does_not_change_the_row_count(tables):
    size = _join_size(tables["qms_measurement"], tables["qms_inspection_spec"], ["spec_id"])
    assert size == len(tables["qms_measurement"])


def test_full_business_key_join_does_not_change_the_row_count(tables):
    """spec_id 를 모르는 참가자가 쓸 수 있는 안전한 대안이 있어야 한다."""
    size = _join_size(
        tables["qms_measurement"],
        tables["qms_inspection_spec"],
        ["product_code", "step_code", "characteristic_code"],
    )
    assert size == len(tables["qms_measurement"])


def test_characteristic_code_alone_explodes_the_row_count(tables):
    """잘못된 조인이 실제로 위험한지 확인한다.

    이 어서션이 실패한다면 위험이 사라진 것이 아니라, 특성 코드가 우연히
    유일해진 것이다. 그때는 문서의 경고도 근거를 잃으므로 같이 손봐야 한다.
    """
    base = len(tables["qms_measurement"])
    size = _join_size(tables["qms_measurement"], tables["qms_inspection_spec"], ["characteristic_code"])
    assert size > base * 5, f"증폭이 {size / base:.2f}배 뿐입니다"


def test_characteristic_code_join_mostly_pairs_the_wrong_product(tables):
    by_char = defaultdict(list)
    for spec in tables["qms_inspection_spec"]:
        by_char[spec["characteristic_code"]].append(spec)

    total = wrong = flipped = 0
    for row in tables["qms_measurement"]:
        for spec in by_char[row["characteristic_code"]]:
            total += 1
            if spec["spec_id"] == row["spec_id"]:
                continue
            wrong += 1
            outside = not spec["lsl"] <= row["measured_value"] <= spec["usl"]
            if outside != row["is_out_of_spec"]:
                flipped += 1

    assert wrong / total > 0.9, f"틀린 짝이 {wrong / total:.1%} 뿐입니다"
    assert flipped > 0, "판정이 뒤집히는 짝이 없으면 경고할 이유가 없습니다"


def test_a_wrong_judgment_carries_no_unit_clue_that_would_reveal_it(tables):
    """뒤집힌 판정 중 얼마가 단위로 알아챌 수 있는지 본다.

    FDC 쪽에서 이 지표를 제시했다. 뒤집힘 비율만으로는 위험도를 못 잰다.
    단위가 다르면 참가자가 이상하다고 느끼지만, 같으면 값도 단위도 그럴듯해
    아무도 의심하지 않는다. FDC 는 78.2% 가 눈먼 상태였고 여기는 100% 다.
    이 데이터셋의 여섯 특성은 각각 단위가 하나뿐이라 단서가 아예 없다.

    이 어서션이 느슨해진다면 위험이 줄어든 것이므로 문서의 경고도 함께
    손봐야 한다. 과잉 경고는 다른 경고까지 무디게 만든다.
    """
    by_char = defaultdict(list)
    for spec in tables["qms_inspection_spec"]:
        by_char[spec["characteristic_code"]].append(spec)

    flipped = blind = 0
    for row in tables["qms_measurement"]:
        for spec in by_char[row["characteristic_code"]]:
            if spec["spec_id"] == row["spec_id"]:
                continue
            outside = not spec["lsl"] <= row["measured_value"] <= spec["usl"]
            if outside != row["is_out_of_spec"]:
                flipped += 1
                if spec["unit"] == row["unit"]:
                    blind += 1

    assert flipped > 0
    assert blind / flipped > 0.5, (
        f"뒤집힘 {flipped} 중 눈먼 것이 {blind / flipped:.1%} 뿐이라면 "
        "단위가 위험을 드러내므로 경고 수위를 낮춰야 합니다"
    )


def test_specs_not_copied_into_measurement_still_need_a_join(tables):
    """문서의 목록이 실제 미복제 컬럼 집합과 정확히 같은지 본다.

    처음에는 cpk_target 과 sampling_method 두 개만 확인했다. 열한 개 중 아홉은
    문서에서 지워도 통과했다. 하필 확인하던 것을 지운 돌연변이만 잡혔던 것이다.

    양방향으로 막는다. 목록에서 빠지면 안내가 불완전해지고, 반대로 그 컬럼을
    계측에 복제해 버리면 "조인해야 한다"가 거짓이 된다. 한쪽만 보면 나머지를
    놓친다.
    """
    text = AGENT_DOC.read_text(encoding="utf-8")
    block = re.search(r"<!-- 조인필요:시작 -->(.*?)<!-- 조인필요:끝 -->", text, re.S)
    assert block, "조인 필요 목록 문단이 없습니다"

    documented = set(re.findall(r"`(\w+)`", block.group(1)))
    actual = set(tables["qms_inspection_spec"][0]) - set(tables["qms_measurement"][0])
    assert documented == actual, (
        f"문서에만 {documented - actual} · 데이터에만 {actual - documented}"
    )
    assert "cpk_target" in actual, "Cpk 목표까지 복제하면 조인할 이유가 사라집니다"


def test_the_copied_columns_are_exactly_what_a_judgment_needs(tables):
    """복제 범위가 판정에 필요한 것에서 늘어나지 않았는지 본다.

    복제가 늘면 계측 테이블만 커지고 참가자가 복합키를 한 번도 안 써보게 된다.
    줄면 판정에 조인이 필요해져 잘못된 키를 고를 기회가 생긴다. 양쪽 다 막는다.
    """
    copied = set(tables["qms_inspection_spec"][0]) & set(tables["qms_measurement"][0])
    assert copied == {
        "spec_id",
        "product_code",
        "step_code",
        "characteristic_code",
        "characteristic_name_ko",
        "unit",
        "target_value",
        "lsl",
        "usl",
    }


def test_most_amplification_never_flips_a_judgment(tables):
    """판정을 안 뒤집는 부풀림이 얼마나 되는지 고정한다.

    FDC 쪽에서 환경 센서가 부풀림의 91% 를 내면서 판정은 하나도 안 뒤집는 것을
    찾았다. 여기도 89% 다. 규격이 같으니 is_out_of_spec 이 그대로고, 불합격
    건수가 정확한 채로 분모만 늘어난다. 판정이 뒤집히면 들여다볼 여지라도
    있지만 이건 아무도 이상하다고 느끼지 않는다.

    CD 가 위험한 게 아니라 잘못된 조인 자체가 위험하다는 근거다. 이 비율이
    낮아지면 문서의 표도 함께 손봐야 한다.
    """
    by_char = defaultdict(list)
    for spec in tables["qms_inspection_spec"]:
        by_char[spec["characteristic_code"]].append(spec)

    amplified = Counter()
    flipped = Counter()
    for row in tables["qms_measurement"]:
        code = row["characteristic_code"]
        for spec in by_char[code]:
            amplified[code] += 1
            if spec["spec_id"] == row["spec_id"]:
                continue
            if (not spec["lsl"] <= row["measured_value"] <= spec["usl"]) != row["is_out_of_spec"]:
                flipped[code] += 1

    silent = sum(n for code, n in amplified.items() if not flipped[code])
    total = sum(amplified.values())
    assert silent / total > 0.5, (
        f"판정을 안 뒤집는 부풀림이 {silent / total:.0%} 뿐이라면 "
        "위험이 판정 왜곡에 몰린 것이므로 문서의 성격 구분을 다시 보세요"
    )
    assert flipped, "뒤집히는 특성이 하나도 없으면 경고 수위를 낮춰야 합니다"


def test_the_same_characteristic_has_different_targets_per_product(tables):
    """22.65배가 단순한 중복이 아니라 규격 혼선인 근거.

    단위까지 다르면 눈에 띈다. 여기서는 단위가 같은 nm 인데 목표만 4배 차이라
    잘못 조인해도 값이 그럴듯해 보인다.
    """
    variants = defaultdict(set)
    for spec in tables["qms_inspection_spec"]:
        variants[spec["characteristic_code"]].add((spec["unit"], spec["target_value"]))

    multi = {c: v for c, v in variants.items() if len(v) > 1}
    assert multi, "특성별 규격이 하나뿐이면 잘못된 조인도 무해합니다"

    for code, seen in multi.items():
        units = {u for u, _ in seen}
        targets = sorted(t for _, t in seen)
        if len(units) == 1 and len(targets) > 1:
            assert max(targets) / min(targets) > 2, f"{code} 목표 격차 {targets}"
            return
    pytest.fail("같은 단위에서 목표만 갈리는 특성이 없습니다")


# --- 불량코드 ---------------------------------------------------------------


def test_mes_defect_code_maps_to_several_qms_codes(tables):
    counts = Counter(
        row["mes_defect_code"] for row in tables["qms_defect_code"] if row["mes_defect_code"]
    )
    assert max(counts.values()) > 1, "1:1 이면 경고가 불필요합니다"


def test_joining_mes_defects_through_the_code_master_amplifies(snapshot, tables):
    """마스터를 거치면 늘어나고, 부적합을 거치면 안 늘어난다."""
    index = Counter(
        row["mes_defect_code"] for row in tables["qms_defect_code"] if row["mes_defect_code"]
    )
    mes_defects = [code for r in snapshot.process_results if (code := r.get("defect_code"))]
    assert mes_defects, "픽스처에 MES 불량이 없습니다"

    through_master = sum(index[code] for code in mes_defects)
    assert through_master > len(mes_defects)

    ncr_codes = Counter(
        row["mes_defect_code"] for row in tables["qms_nonconformance"] if row["mes_defect_code"]
    )
    assert set(ncr_codes) <= set(index), "부적합의 MES 코드가 마스터 밖입니다"


# --- 검사 → 검사기준 --------------------------------------------------------


def test_inspection_needs_product_and_step_together(tables):
    both = _join_size(
        tables["qms_inspection"], tables["qms_inspection_spec"], ["product_code", "step_code"]
    )
    step_only = _join_size(tables["qms_inspection"], tables["qms_inspection_spec"], ["step_code"])
    assert both > len(tables["qms_inspection"]), "1:N 이 정상입니다"
    assert step_only > both * 2, "제품 코드를 빼도 차이가 없으면 경고가 불필요합니다"


# --- 문서 ------------------------------------------------------------------


def test_agent_doc_warns_about_every_dangerous_join():
    """경고가 문서에 실제로 있는지 본다.

    부분 문자열 포함으로 검사하면 CHARACTERISTIC_CODE_X 같은 변형도 통과한다.
    FDC 쪽에서 그 함정을 두 번 연속 밟았다. 여기서는 절을 잘라 그 안에서 찾는다.
    """
    text = AGENT_DOC.read_text(encoding="utf-8")
    match = re.search(r"^## 조인할 때 반드시 지켜야 할 것$(.*?)^## ", text, re.M | re.S)
    assert match, "조인 경고 절이 없습니다"
    section = match.group(1)

    for phrase in ("`spec_id`", "`characteristic_code`", "`mes_defect_code`", "22.65배", "4.00배"):
        assert phrase in section, f"조인 경고 절에 {phrase} 가 없습니다"

    assert "100%" in section, "뒤집힘 중 눈먼 비율이 적혀 있어야 합니다"
    assert "`cpk_target`" in section, "조인이 필요한 질문의 예가 있어야 합니다"
    assert "89%" in section, "판정을 안 뒤집는 부풀림 비율이 적혀 있어야 합니다"


def test_documented_amplification_matches_the_data(tables):
    """문서에 적은 배수가 실제와 맞는지 대조한다.

    숫자를 적어 두기만 하면 데이터가 바뀌어도 아무도 모른다. 다만 재배포 후
    픽스처를 다시 뜨면 제품 구성이 조금 달라지므로, 그 정도에 깨져 숫자만
    맞춰 넣게 되지 않도록 폭을 준다.
    """
    text = AGENT_DOC.read_text(encoding="utf-8")
    line = re.search(r"^.*characteristic_code 단독.*$", text, re.M)
    assert line, "잘못된 조인의 규모를 적은 줄이 없습니다"
    stated = float(re.search(r"\(([\d.]+)배\)", line.group(0)).group(1))
    actual = _join_size(
        tables["qms_measurement"], tables["qms_inspection_spec"], ["characteristic_code"]
    ) / len(tables["qms_measurement"])
    assert abs(stated - actual) < 3.0, f"문서 {stated}배 vs 실제 {actual:.2f}배"
