import collections

from src.qms_incoming import IQC_JUDGMENT_COUNTS, build_incoming_inspections
from src.qms_masters import build_defect_codes, build_inspectors


def incoming(snapshot):
    return build_incoming_inspections(snapshot, build_inspectors(snapshot), build_defect_codes())


def test_total_is_200_with_unique_ids(snapshot):
    rows = incoming(snapshot)
    assert len(rows) == 200
    assert len({r["iqc_id"] for r in rows}) == 200


def test_judgment_distribution_is_12_8_80(snapshot):
    assert collections.Counter(r["judgment"] for r in incoming(snapshot)) == IQC_JUDGMENT_COUNTS


def test_every_material_is_inspected_and_all_codes_exist_in_mes(snapshot):
    rows = incoming(snapshot)
    mes_materials = {m["material_code"] for m in snapshot.materials}
    used = collections.Counter(r["material_code"] for r in rows)
    assert set(used) == mes_materials
    assert min(used.values()) >= 16


def test_defect_code_present_exactly_when_not_accepted(snapshot):
    valid = {d["defect_code"] for d in build_defect_codes()}
    for row in incoming(snapshot):
        if row["judgment"] == "합격":
            assert row["defect_code"] is None
        else:
            assert row["defect_code"] in valid


def test_material_labels_match_mes(snapshot):
    names = {m["material_code"]: m["material_name"] for m in snapshot.materials}
    uoms = {m["material_code"]: m["uom"] for m in snapshot.materials}
    for row in incoming(snapshot):
        assert row["material_name"] == names[row["material_code"]]
        assert row["uom"] == uoms[row["material_code"]]


def test_inspection_never_precedes_receipt(snapshot):
    for row in incoming(snapshot):
        assert row["inspection_date"] >= row["receipt_date"]
        assert row["sample_size"] <= row["received_qty"]


def test_missing_certificate_is_reported_as_not_submitted(snapshot):
    for row in incoming(snapshot):
        if not row["coa_received"]:
            assert row["coa_conformance"] == "미제출"
        else:
            assert row["coa_conformance"] in {"일치", "불일치"}


def test_incoming_inspections_are_handled_by_the_receiving_team(snapshot):
    receiving = {i["inspector_id"] for i in build_inspectors(snapshot) if i["team_ko"] == "입고검사팀"}
    assert {r["inspector_id"] for r in incoming(snapshot)} <= receiving


def test_supplier_lot_numbers_are_unique(snapshot):
    rows = incoming(snapshot)
    assert len({r["supplier_lot_no"] for r in rows}) == 200


def test_incoming_carries_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    assert not (set(incoming(snapshot)[0]) & forbidden)


def test_incoming_is_deterministic(snapshot):
    assert incoming(snapshot) == incoming(snapshot)
