import collections
import datetime as dt

from src.qms_inspection import INSPECTION_COUNTS, build_inspections, mes_result_index
from src.qms_masters import build_inspectors


def inspections(snapshot):
    return build_inspections(snapshot, build_inspectors())


def test_total_is_207_with_the_designed_type_mix(snapshot):
    rows = inspections(snapshot)
    assert len(rows) == 207
    assert collections.Counter(r["inspection_type"] for r in rows) == INSPECTION_COUNTS
    assert len({r["inspection_id"] for r in rows}) == 207


def test_ipqc_maps_one_to_one_onto_mes_process_results(snapshot):
    rows = [r for r in inspections(snapshot) if r["inspection_type"] == "IPQC"]
    assert {r["mes_process_result_id"] for r in rows} == {p["id"] for p in snapshot.process_results}


def test_retest_covers_union_of_defective_and_non_pass(snapshot):
    expected = {p["id"] for p in snapshot.process_results if p.get("defect_code") or p["result"] != "Pass"}
    assert len(expected) == 40
    rows = [r for r in inspections(snapshot) if r["inspection_type"] == "IPQC-RT"]
    assert {r["mes_process_result_id"] for r in rows} == expected


def test_lot_free_inspections_have_null_keys(snapshot):
    for row in inspections(snapshot):
        if row["inspection_type"] in {"PCS", "EQV"}:
            assert row["lot_id"] is None
            assert row["mes_process_result_id"] is None
        if row["inspection_type"] == "EQV":
            assert row["product_code"] is None
            assert row["eqp_id"] is not None
        if row["inspection_type"] in {"IPQC", "IPQC-RT"}:
            assert row["mes_process_result_id"] is not None


def test_measurement_counts_total_260(snapshot):
    rows = inspections(snapshot)
    assert sum(r["measurement_count"] for r in rows) == 260
    per_type = collections.defaultdict(set)
    for row in rows:
        per_type[row["inspection_type"]].add(row["measurement_count"])
    assert per_type["IPQC"] == {0}
    assert per_type["OQC"] == {0}
    assert per_type["IPQC-RT"] == {3}
    assert per_type["PCS"] == {3}
    assert per_type["EQV"] == {2}


def test_ipqc_judgment_follows_mes_state(snapshot):
    index = mes_result_index(snapshot)
    rows = [r for r in inspections(snapshot) if r["inspection_type"] == "IPQC"]
    by_state = collections.Counter()
    for row in rows:
        mes = index[row["mes_process_result_id"]]
        if mes["result"] == "Fail":
            assert row["judgment"] == "불합격"
        by_state[(mes["result"], bool(mes.get("defect_code")), row["judgment"])] += 1
    assert by_state[("Pass", True, "합격")] == 23
    assert by_state[("Pass", True, "조건부합격")] == 10
    assert by_state[("Pass", False, "불합격")] == 3
    assert by_state[("Pass", False, "조건부합격")] == 4
    assert by_state[("Pass", False, "합격")] == 44


def test_nonconformance_flag_count_matches_ncr_budget(snapshot):
    rows = inspections(snapshot)
    flagged = collections.Counter(r["inspection_type"] for r in rows if r["has_nonconformance"])
    # 스펙 6.4의 검사 출처 NCR 배분: 24 + 10 + 6 + 7 + 2 = 49
    assert flagged == {"IPQC": 24, "IPQC-RT": 10, "OQC": 6, "PCS": 7, "EQV": 2}


def test_device_1_and_3_are_disjoint_and_correctly_sized(snapshot):
    index = mes_result_index(snapshot)
    rows = inspections(snapshot)
    device1 = [
        r for r in rows
        if r["mes_process_result_id"] is not None
        and index[r["mes_process_result_id"]]["result"] == "Pass"
        and not index[r["mes_process_result_id"]].get("defect_code")
        and r["judgment"] == "불합격"
        and r["defect_found_qty"] == 0
    ]
    device3 = [
        r for r in rows
        if r["mes_process_result_id"] is not None
        and not index[r["mes_process_result_id"]].get("defect_code")
        and r["defect_found_qty"] > 0
        and r["judgment"] != "불합격"
    ]
    assert len(device1) == 3
    assert len(device3) == 4
    assert not ({r["inspection_id"] for r in device1} & {r["inspection_id"] for r in device3})


def test_quantities_and_causality_hold(snapshot):
    index = mes_result_index(snapshot)
    for row in inspections(snapshot):
        assert row["defect_found_qty"] <= row["sample_size"]
        assert row["inspected_wafer_qty"] <= row["sample_size"]
        if row["mes_process_result_id"] is not None:
            mes = index[row["mes_process_result_id"]]
            assert row["sample_size"] <= mes["in_qty"]
            # MES out_time 은 JSON 문자열이라 파싱해서 비교한다.
            assert row["inspection_datetime"] >= dt.datetime.fromisoformat(mes["out_time"])


def test_inspections_carry_no_forbidden_columns(snapshot):
    forbidden = {"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"}
    assert not (set(inspections(snapshot)[0]) & forbidden)


def test_inspections_are_deterministic(snapshot):
    assert inspections(snapshot) == inspections(snapshot)
