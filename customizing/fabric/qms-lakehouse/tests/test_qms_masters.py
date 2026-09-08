from src.qms_masters import (
    build_defect_codes,
    build_inspection_specs,
    build_inspectors,
    defect_codes_by_mes,
    spec_index,
)
from src.qms_reference import MES_DEFECT_CODES, STEP_CHARACTERISTICS

FORBIDDEN = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}


def test_defect_codes_expand_six_mes_codes_into_24():
    rows = build_defect_codes()
    assert len(rows) == 24
    assert len({r["defect_code"] for r in rows}) == 24
    assert {r["mes_defect_code"] for r in rows} == set(MES_DEFECT_CODES)
    by_mes = defect_codes_by_mes(rows)
    assert all(len(v) == 4 for v in by_mes.values())


def test_defect_code_severity_score_matches_severity():
    expected = {"Critical": 9, "Major": 6, "Minor": 3}
    for row in build_defect_codes():
        assert row["severity_score"] == expected[row["severity"]]


def test_defect_codes_carry_no_forbidden_columns():
    for row in build_defect_codes():
        assert not (set(row) & FORBIDDEN)


def test_inspection_specs_are_product_by_step_by_characteristic(snapshot):
    rows = build_inspection_specs(snapshot)
    assert len(rows) == 108
    assert len({r["spec_id"] for r in rows}) == 108
    assert {r["product_code"] for r in rows} == {"DDR5", "LX9", "NAND", "PMIC"}
    for row in rows:
        assert row["characteristic_code"] in STEP_CHARACTERISTICS[row["step_code"]]
        assert row["lsl"] < row["target_value"] < row["usl"]
        assert row["cpk_target"] == 1.33


def test_finer_node_product_has_tighter_cd_spec(snapshot):
    index = spec_index(build_inspection_specs(snapshot))
    # LX9 는 5nm, PMIC 는 28nm. 선폭 목표치가 노드에 비례해야 한다.
    assert index[("LX9", "PHOTO", "CD")]["target_value"] < index[("PMIC", "PHOTO", "CD")]["target_value"]


def test_specs_carry_mes_labels(snapshot):
    index = spec_index(build_inspection_specs(snapshot))
    assert index[("DDR5", "PHOTO", "CD")]["step_name"] == "Photolithography"
    assert index[("DDR5", "PHOTO", "CD")]["product_name"] == "DDR5-16G"


def test_inspectors_are_five_teams_by_three_shifts(snapshot):
    rows = build_inspectors(snapshot)
    assert len(rows) == 15
    assert len({r["inspector_id"] for r in rows}) == 15
    assert len({r["team_ko"] for r in rows}) == 5
    assert {r["shift_code"] for r in rows} == {"A", "B", "C"}
    assert all(r["is_active"] for r in rows)


def test_inspector_names_do_not_collide_with_mes_operators(snapshot):
    mes_operators = {r.get("operator") for r in snapshot.process_results}
    names = {r["inspector_name"] for r in build_inspectors(snapshot)}
    assert not (names & mes_operators)
    assert len(names) == 15


def test_masters_are_deterministic(snapshot):
    assert build_defect_codes() == build_defect_codes()
    assert build_inspectors(snapshot) == build_inspectors(snapshot)
    assert build_inspection_specs(snapshot) == build_inspection_specs(snapshot)
