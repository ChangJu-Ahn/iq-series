import collections

from src.mes_client import mes_anchor
from src.qms_inspection import build_inspections
from src.qms_masters import build_inspection_specs, build_inspectors, spec_index
from src.qms_measurement import (
    EQV_SPEC_PRODUCT,
    build_measurements,
    characteristics_for,
    nominal_sigma,
)


def measurements(snapshot):
    inspections = build_inspections(snapshot, build_inspectors(snapshot))
    return inspections, build_measurements(
        inspections, build_inspection_specs(snapshot), mes_anchor(snapshot)
    )


def test_total_is_260_and_matches_declared_measurement_counts(snapshot):
    inspections, rows = measurements(snapshot)
    assert len(rows) == 260
    assert len({r["measurement_id"] for r in rows}) == 260
    declared = {i["inspection_id"]: i["measurement_count"] for i in inspections}
    actual = collections.Counter(r["inspection_id"] for r in rows)
    for inspection_id, count in declared.items():
        assert actual.get(inspection_id, 0) == count


def test_measurement_split_by_inspection_type(snapshot):
    inspections, rows = measurements(snapshot)
    type_by_id = {i["inspection_id"]: i["inspection_type"] for i in inspections}
    assert collections.Counter(type_by_id[r["inspection_id"]] for r in rows) == {
        "IPQC-RT": 120,
        "PCS": 108,
        "EQV": 32,
    }


def test_spec_id_is_always_consistent_with_its_join_keys(snapshot):
    _, rows = measurements(snapshot)
    for row in rows:
        assert row["spec_id"] == (
            f"SPEC-{row['product_code']}-{row['step_code']}-{row['characteristic_code']}"
        )


def test_equipment_verification_measures_against_reference_product(snapshot):
    inspections, rows = measurements(snapshot)
    eqv_ids = {i["inspection_id"] for i in inspections if i["inspection_type"] == "EQV"}
    eqv_rows = [r for r in rows if r["inspection_id"] in eqv_ids]
    assert len(eqv_rows) == 32
    assert {r["product_code"] for r in eqv_rows} == {EQV_SPEC_PRODUCT}
    assert {r["lot_id"] for r in eqv_rows} == {None}


def test_spec_snapshot_matches_master_spec(snapshot):
    index = spec_index(build_inspection_specs(snapshot))
    _, rows = measurements(snapshot)
    for row in rows:
        spec = index[(row["product_code"], row["step_code"], row["characteristic_code"])]
        assert row["target_value"] == spec["target_value"]
        assert row["lsl"] == spec["lsl"]
        assert row["usl"] == spec["usl"]
        assert row["unit"] == spec["unit"]


def test_out_of_spec_flag_agrees_with_limits(snapshot):
    _, rows = measurements(snapshot)
    for row in rows:
        outside = row["measured_value"] < row["lsl"] or row["measured_value"] > row["usl"]
        assert row["is_out_of_spec"] is outside
        assert row["judgment"] == ("NG" if outside else "OK")


def test_failed_inspections_contain_at_least_one_out_of_spec_point(snapshot):
    inspections, rows = measurements(snapshot)
    failed = {
        i["inspection_id"]
        for i in inspections
        if i["judgment"] == "불합격" and i["measurement_count"] > 0
    }
    assert failed
    by_inspection = collections.defaultdict(list)
    for row in rows:
        by_inspection[row["inspection_id"]].append(row)
    for inspection_id in failed:
        assert any(r["is_out_of_spec"] for r in by_inspection[inspection_id])


def test_nominal_sigma_targets_cpk_1_33(snapshot):
    spec = build_inspection_specs(snapshot)[0]
    sigma = nominal_sigma(spec)
    assert sigma == (spec["usl"] - spec["lsl"]) / 8
    cpk = (spec["usl"] - spec["lsl"]) / 2 / (3 * sigma)
    assert round(cpk, 2) == 1.33


def test_characteristics_per_inspection_type(snapshot):
    inspections, _ = measurements(snapshot)
    for inspection in inspections:
        chars = characteristics_for(inspection)
        assert len(chars) == inspection["measurement_count"]


def test_measurements_reference_the_inspection_inspector(snapshot):
    inspections, rows = measurements(snapshot)
    inspector_by_id = {i["inspection_id"]: i["inspector_id"] for i in inspections}
    for row in rows:
        assert row["measured_by"] == inspector_by_id[row["inspection_id"]]


def test_metrology_equipment_is_separate_from_mes_equipment(snapshot):
    _, rows = measurements(snapshot)
    assert all(r["metrology_eqp_id"].startswith("MET-") for r in rows)


def test_measurements_carry_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    _, rows = measurements(snapshot)
    assert not (set(rows[0]) & forbidden)


def test_measurements_are_deterministic(snapshot):
    assert measurements(snapshot)[1] == measurements(snapshot)[1]


# --- 열화 산포 -------------------------------------------------------------
#
# "조건부합격이고 부적합이 있는 검사" 는 설비가 열화된 상태를 뜻하고, 그 로트의
# 계측은 산포가 커야 한다. 이게 데이터의 의미인데 3라운드 동안 아무 테스트도
# 보고 있지 않았다. _DEGRADED_SIGMA_FACTOR 를 2.2 에서 1.0 으로 바꾸면 열화군
# 산포가 정상군보다 오히려 작아지고(0.78배), 12.0 으로 키우면 이탈률이 5.0%
# 에서 9.2% 로 두 배가 되는데, 둘 다 185개 테스트를 전부 통과했다.
#
# 적재도 성공하고 값의 범위도 맞다. 규격 안에 있는 정상적인 숫자들이다.
# 다만 "열화 설비의 계측이 왜 흔들리나" 라는 질문의 근거가 사라진다.


def _normalised_spread(rows, *, continuous_only: bool) -> float:
    """목표 대비 편차를 규격폭으로 나눈 값의 표준편차.

    특성마다 단위와 스케일이 달라(nm, %, ea) 그냥 섞으면 큰 특성이 지배한다.
    """
    import statistics

    scaled = [
        abs(r["measured_value"] - r["target_value"]) / ((r["usl"] - r["lsl"]) or 1.0)
        for r in rows
        if not (continuous_only and r["unit"] == "ea")
    ]
    return statistics.pstdev(scaled)


def test_degraded_inspections_scatter_more_than_healthy_ones(snapshot):
    """실제 생성된 데이터에서 열화군이 정상군보다 흔들리는가.

    표본이 21행뿐이라 배수는 못 박고 방향만 본다. 배수 자체는 아래 테스트가
    본다. 여기는 "생성 파이프라인 끝까지 통과한 데이터에도 그 차이가 남아
    있는가" 를 묻는다.
    """
    inspections, rows = measurements(snapshot)
    by_id = {i["inspection_id"]: i for i in inspections}

    def degraded(row) -> bool:
        i = by_id[row["inspection_id"]]
        return i["judgment"] == "조건부합격" and i["has_nonconformance"]

    hot = [r for r in rows if degraded(r)]
    cold = [r for r in rows if not degraded(r)]
    assert hot, "열화 검사가 하나도 없으면 이 개념이 데이터에 없는 것입니다"

    assert _normalised_spread(hot, continuous_only=False) > _normalised_spread(
        cold, continuous_only=False
    ), "열화 검사의 계측이 정상 검사보다 안정적이면 도메인 의미가 뒤집힌 것입니다"


def test_degradation_widens_scatter_by_the_documented_multiple(snapshot):
    """열화 배수가 문서에 적힌 2.2배인가.

    같은 검사 집합을 '전부 열화' / '전부 정상' 두 벌로 만들어 비교한다.
    _DEGRADED_SIGMA_FACTOR 를 읽어 기대값을 만들면 돌연변이가 생성과 검증
    양쪽에 걸려 상쇄된다. FDC 가 정확히 그 함정에 빠져 돌연변이 7개가 전부
    통과했다고 알려왔다. 그래서 배수를 숫자로 박는다.

    계수형(ea)은 정수로 반올림돼 산포가 이산화되므로 뺀다. 넣으면 2.12 로
    희석돼 경계가 흐려진다.
    """
    inspections, _ = measurements(snapshot)
    specs = build_inspection_specs(snapshot)
    as_of = mes_anchor(snapshot)

    def variant(degraded: bool):
        # 불합격은 규격 이탈점을 강제하는 별도 경로라 산포 측정을 오염시킨다.
        # 조건부합격/합격만 쓰면 값이 전부 가우시안에서 나온다.
        flavoured = [
            dict(i, judgment="조건부합격" if degraded else "합격", has_nonconformance=degraded)
            for i in inspections
        ]
        return build_measurements(flavoured, specs, as_of)

    hot = _normalised_spread(variant(True), continuous_only=True)
    cold = _normalised_spread(variant(False), continuous_only=True)
    assert cold > 0
    multiple = hot / cold
    assert 1.95 < multiple < 2.45, (
        f"열화 배수가 {multiple:.2f} 입니다. 2.2 를 의도했다면 "
        "_DEGRADED_SIGMA_FACTOR 를, 다른 값을 의도했다면 이 테스트와 "
        "data-agent-schema.md 를 함께 고치세요"
    )

    # 문서가 말하는 배수와 실측이 어긋나면 안내가 거짓이 된다. 문서에 숫자를
    # 적어 두기만 하고 대조하지 않으면 영원히 모른다. `2.2배` 를 부분 문자열로
    # 찾으면 `12.2배` 도 통과하므로 정규식으로 뽑아 실수로 비교한다.
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[1] / "data-agent-schema.md").read_text(
        encoding="utf-8"
    )
    stated = re.search(r"정상 로트보다 \*\*([\d.]+)배\*\* 흩어지", doc)
    assert stated, "문서에 열화 배수 문장이 없습니다"
    assert abs(float(stated.group(1)) - multiple) < 0.25, (
        f"문서는 {stated.group(1)}배라는데 실측은 {multiple:.2f}배입니다"
    )
