import collections

from src.qms_incoming import build_incoming_inspections
from src.qms_inspection import build_inspections, mes_result_index
from src.qms_masters import build_defect_codes, build_inspectors
from src.qms_nonconformance import NCR_SOURCE_COUNTS, build_nonconformances


def build_ncr_bundle(snapshot):
    inspectors = build_inspectors(snapshot)
    defect_codes = build_defect_codes()
    inspections = build_inspections(snapshot, inspectors)
    incoming = build_incoming_inspections(snapshot, inspectors, defect_codes)
    ncrs, dispositions = build_nonconformances(snapshot, inspections, incoming, defect_codes)
    return inspections, incoming, ncrs, dispositions


def test_ncr_and_disposition_are_95_and_one_to_one(snapshot):
    _, _, ncrs, dispositions = build_ncr_bundle(snapshot)
    assert len(ncrs) == 95
    assert len(dispositions) == 95
    assert len({n["ncr_id"] for n in ncrs}) == 95
    assert {d["ncr_id"] for d in dispositions} == {n["ncr_id"] for n in ncrs}


def test_ncr_sources_match_the_designed_split(snapshot):
    _, _, ncrs, _ = build_ncr_bundle(snapshot)
    assert collections.Counter(n["ncr_source"] for n in ncrs) == NCR_SOURCE_COUNTS


def test_every_flagged_inspection_produces_exactly_one_ncr(snapshot):
    inspections, _, ncrs, _ = build_ncr_bundle(snapshot)
    flagged = {i["inspection_id"] for i in inspections if i["has_nonconformance"]}
    linked = [n["inspection_id"] for n in ncrs if n["inspection_id"] is not None]
    assert len(linked) == len(flagged) == 49
    assert set(linked) == flagged


def test_every_rejected_or_concession_material_produces_one_ncr(snapshot):
    _, incoming, ncrs, _ = build_ncr_bundle(snapshot)
    expected = {r["iqc_id"] for r in incoming if r["judgment"] != "합격"}
    linked = [n["iqc_id"] for n in ncrs if n["iqc_id"] is not None]
    assert len(expected) == 40
    assert set(linked) == expected
    assert len(linked) == 40


def test_defect_code_and_parent_code_always_agree(snapshot):
    parent = {d["defect_code"]: d["mes_defect_code"] for d in build_defect_codes()}
    severity = {d["defect_code"]: d["severity"] for d in build_defect_codes()}
    _, _, ncrs, _ = build_ncr_bundle(snapshot)
    for ncr in ncrs:
        assert parent[ncr["defect_code"]] == ncr["mes_defect_code"]
        assert severity[ncr["defect_code"]] == ncr["severity"]


def test_customer_complaints_carry_product_but_no_lot(snapshot):
    _, _, ncrs, _ = build_ncr_bundle(snapshot)
    complaints = [n for n in ncrs if n["ncr_source"] == "고객제기"]
    assert len(complaints) == 6
    for ncr in complaints:
        assert ncr["lot_id"] is None
        assert ncr["inspection_id"] is None
        assert ncr["iqc_id"] is None
        assert ncr["product_code"] in {p["product_code"] for p in snapshot.products}


def test_device_2_two_mes_failures_were_shipped_under_concession(snapshot):
    inspections, _, ncrs, dispositions = build_ncr_bundle(snapshot)
    mes = mes_result_index(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    disposition_by_ncr = {d["ncr_id"]: d for d in dispositions}
    hits = []
    for ncr in ncrs:
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if not inspection or inspection["mes_process_result_id"] is None:
            continue
        if mes[inspection["mes_process_result_id"]]["result"] != "Fail":
            continue
        if disposition_by_ncr[ncr["ncr_id"]]["disposition_type"] == "특채":
            hits.append(ncr["ncr_id"])
    assert len(hits) == 2
    for ncr_id in hits:
        assert disposition_by_ncr[ncr_id]["decision_body_ko"] == "MRB"


def test_device_4_two_rejected_materials_reached_a_lot(snapshot):
    _, incoming, ncrs, _ = build_ncr_bundle(snapshot)
    iqc_by_id = {r["iqc_id"]: r for r in incoming}
    traced = [n for n in ncrs if n["ncr_source"] == "입고검사" and n["lot_id"] is not None]
    assert len(traced) == 2
    lots = {l["lot_id"]: l for l in snapshot.lots}
    bom_pairs = {(b["product_code"], b["material_code"], b["step_code"]) for b in snapshot.bom}
    for ncr in traced:
        material = iqc_by_id[ncr["iqc_id"]]["material_code"]
        assert ncr["material_code"] == material
        assert ncr["lot_id"] in lots
        assert lots[ncr["lot_id"]]["product_code"] == ncr["product_code"]
        assert (ncr["product_code"], material, ncr["step_code"]) in bom_pairs


def test_device_5_two_reworks_failed_and_were_scrapped(snapshot):
    _, _, _, dispositions = build_ncr_bundle(snapshot)
    hits = [
        d for d in dispositions
        if d["rework_result"] == "실패" and d["disposition_type"] == "폐기"
    ]
    assert len(hits) == 2
    for row in hits:
        assert row["rework_step_code"] is not None
        assert row["scrap_cost_krw"] > 0


def test_quality_hold_ncrs_blame_measurement(snapshot):
    inspections, _, ncrs, _ = build_ncr_bundle(snapshot)
    mes = mes_result_index(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    holds = []
    for ncr in ncrs:
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if not inspection or inspection["mes_process_result_id"] is None:
            continue
        source = mes[inspection["mes_process_result_id"]]
        if (
            source["result"] == "Pass"
            and not source.get("defect_code")
            and inspection["judgment"] == "불합격"
            and inspection["defect_found_qty"] == 0
        ):
            holds.append(ncr)
    assert len(holds) == 3
    assert all(n["root_cause_category"] == "측정" for n in holds)


def test_dates_and_quantities_form_a_valid_chain(snapshot):
    inspections, incoming, ncrs, dispositions = build_ncr_bundle(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    iqc_by_id = {r["iqc_id"]: r for r in incoming}
    disposition_by_ncr = {d["ncr_id"]: d for d in dispositions}
    for ncr in ncrs:
        disposition = disposition_by_ncr[ncr["ncr_id"]]
        if ncr["inspection_id"]:
            assert ncr["detected_date"] >= inspection_by_id[ncr["inspection_id"]][
                "inspection_datetime"
            ].date()
        if ncr["iqc_id"]:
            assert ncr["detected_date"] >= iqc_by_id[ncr["iqc_id"]]["inspection_date"]
        assert ncr["due_date"] > ncr["detected_date"]
        # 검출 당일에 결정이 날 수 있다. 앵커에 가까운 부적합은 결정까지의
        # 여유가 없어 같은 날로 잘린다. 결정이 검출보다 앞서지만 않으면 된다.
        assert disposition["decision_date"] >= ncr["detected_date"]
        assert disposition["effectiveness_check_date"] > disposition["decision_date"]
        assert 0 < disposition["disposition_qty"] <= ncr["affected_qty"]
        if ncr["closed_date"] is not None:
            assert ncr["closed_date"] >= disposition["decision_date"]


def test_affected_qty_never_exceeds_mes_output(snapshot):
    inspections, _, ncrs, _ = build_ncr_bundle(snapshot)
    mes = mes_result_index(snapshot)
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    for ncr in ncrs:
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if inspection and inspection["mes_process_result_id"] is not None:
            assert ncr["affected_qty"] <= mes[inspection["mes_process_result_id"]]["out_qty"]


def test_scrap_cost_only_on_scrapped_dispositions(snapshot):
    _, _, _, dispositions = build_ncr_bundle(snapshot)
    for row in dispositions:
        if row["disposition_type"] == "폐기":
            assert row["scrap_cost_krw"] > 0
        else:
            assert row["scrap_cost_krw"] == 0


def test_nonconformance_carries_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    _, _, ncrs, dispositions = build_ncr_bundle(snapshot)
    assert not (set(ncrs[0]) & forbidden)
    assert not (set(dispositions[0]) & forbidden)


def test_nonconformance_is_deterministic(snapshot):
    assert build_ncr_bundle(snapshot)[2:] == build_ncr_bundle(snapshot)[2:]
