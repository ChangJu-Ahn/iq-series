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
