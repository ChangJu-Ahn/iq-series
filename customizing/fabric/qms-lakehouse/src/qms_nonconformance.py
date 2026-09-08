"""qms_nonconformance 95행과 qms_disposition 95행.

둘은 1:1이고 날짜가 사슬로 이어지므로 한 함수에서 함께 만든다.
불일치 장치 ②(Fail인데 특채) ④(불합격 자재가 로트에 투입) ⑤(재작업 실패 후 폐기)가
여기서 심긴다.
"""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import MesSnapshot, anchor_date
from src.qms_inspection import mes_result_index
from src.qms_reference import (
    APPROVER_NAMES,
    OWNER_DEPTS,
    OWNER_NAMES,
    SEED_NCR,
)

NCR_SOURCE_COUNTS = {"공정검사": 43, "출하검사": 6, "입고검사": 40, "고객제기": 6}
CUSTOMER_COMPLAINT_COUNT = 6

_SOURCE_BY_INSPECTION_TYPE = {
    "IPQC": "공정검사",
    "IPQC-RT": "공정검사",
    "PCS": "공정검사",
    "EQV": "공정검사",
    "OQC": "출하검사",
}

STEP_DEFECT_MAP = {
    "DIFF": ("Contamination", "Particle"),
    "PHOTO": ("CD-OOS", "Overlay"),
    "ETCH": ("Etch-Residue", "CD-OOS"),
    "IMPL": ("Contamination",),
    "CVD": ("Particle", "Etch-Residue"),
    "CMP": ("Scratch", "Particle"),
    "METRO": ("CD-OOS", "Overlay"),
    "TEST": ("Contamination",),
    "PKG": ("Scratch",),
}

# '측정'은 장치① 전용으로 예약한다. 다른 NCR이 같은 원인을 쓰면 이야기가 흐려진다.
_GENERAL_CAUSES = ("설비", "자재", "작업방법", "환경")

_ROOT_CAUSE_TEXT = {
    "설비": "설비 파라미터가 점진적으로 드리프트해 관리한계를 벗어남",
    "자재": "입고 자재 로트 간 편차가 공정 결과로 전이됨",
    "작업방법": "개정된 작업표준이 현장 레시피에 반영되지 않음",
    "환경": "클린룸 온습도 변동이 공정 안정성에 영향을 줌",
    "측정": "계측 재현성 저하로 실제 품질과 판정이 어긋남",
}

_IMMEDIATE_ACTION = {
    "설비": "해당 설비 가동 중지 후 파라미터 재설정 및 검증 런 수행",
    "자재": "동일 공급 로트 전량 격리 및 대체 로트 투입",
    "작업방법": "작업표준 최신본 재배포 및 교대조 교육 실시",
    "환경": "공조 설정 재조정 및 파티클 모니터링 강화",
    "측정": "계측기 재교정 및 Gage R&R 재평가",
}

_ESTIMATED_COST = {"Critical": (20_000_000, 80_000_000), "Major": (5_000_000, 20_000_000), "Minor": (500_000, 5_000_000)}
_SCRAP_UNIT_COST = (1_200_000, 3_500_000)

_COMPLAINT_TEXT = "고객 현장에서 반환된 제품의 분석 결과 품질 이슈 확인"


def build_nonconformances(
    snapshot: MesSnapshot,
    inspections: list[dict],
    incoming: list[dict],
    defect_codes: list[dict],
) -> tuple[list[dict], list[dict]]:
    base_date = anchor_date(snapshot)
    rng = random.Random(SEED_NCR)
    mes = mes_result_index(snapshot)
    defect_by_code = {d["defect_code"]: d for d in defect_codes}
    by_mes_defect: dict[str, list[dict]] = {}
    for row in defect_codes:
        by_mes_defect.setdefault(row["mes_defect_code"], []).append(row)
    lots = {l["lot_id"]: l for l in snapshot.lots}
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    products = sorted(snapshot.products, key=lambda p: p["product_code"])
    iqc_by_id = {r["iqc_id"]: r for r in incoming}

    seeds = _collect_seeds(inspections, incoming)
    devices = _pick_devices(rng, seeds, inspections, mes, snapshot, iqc_by_id)

    ncrs: list[dict] = []
    dispositions: list[dict] = []
    inspection_by_id = {i["inspection_id"]: i for i in inspections}

    for position, seed in enumerate(seeds):
        ncr_id = f"NCR-2026-{position + 1:04d}"
        if seed[0] == "inspection":
            ncr = _ncr_from_inspection(
                rng, ncr_id, inspection_by_id[seed[1]], mes, lots, by_mes_defect, devices
            )
        elif seed[0] == "iqc":
            ncr = _ncr_from_iqc(
                rng, ncr_id, iqc_by_id[seed[1]], defect_by_code, lots, step_names, devices
            )
        else:
            ncr = _ncr_from_complaint(
                rng, ncr_id, products[position % len(products)], defect_codes, base_date
            )
        disposition = _disposition_for(
            rng, f"DSP-2026-{position + 1:04d}", ncr, devices, step_names
        )
        if ncr["status"] == "완료":
            ncr["closed_date"] = disposition["decision_date"] + dt.timedelta(days=rng.randint(0, 3))
        ncrs.append(ncr)
        dispositions.append(disposition)
    return ncrs, dispositions


def _collect_seeds(inspections: list[dict], incoming: list[dict]) -> list[tuple[str, object]]:
    seeds: list[tuple[str, object]] = [
        ("inspection", i["inspection_id"]) for i in inspections if i["has_nonconformance"]
    ]
    seeds += [("iqc", r["iqc_id"]) for r in incoming if r["judgment"] != "합격"]
    seeds += [("complaint", n) for n in range(CUSTOMER_COMPLAINT_COUNT)]
    return seeds


def _pick_devices(rng, seeds, inspections, mes, snapshot, iqc_by_id) -> dict:
    """장치 ②④⑤가 붙을 대상을 미리 정한다. 건수를 확정적으로 고정하기 위함이다."""
    inspection_by_id = {i["inspection_id"]: i for i in inspections}
    fails, reworks = [], []
    for kind, key in seeds:
        if kind != "inspection":
            continue
        inspection = inspection_by_id[key]
        if inspection["inspection_type"] != "IPQC":
            continue
        result = mes[inspection["mes_process_result_id"]]["result"]
        if result == "Fail":
            fails.append(key)
        elif result == "Rework":
            reworks.append(key)

    rng.shuffle(fails)
    device2 = set(fails[:2])
    device5 = set(reworks)
    assert len(device2) == 2 and len(device5) == 2

    # 장치④: 불합격/특채 자재 중 BOM으로 실제 로트까지 이어지는 것만 후보다.
    # RETICLE-5NM 은 BOM에 없고, 로트가 도달하지 못한 공정의 자재도 이어지지 않는다.
    reachable = _reachable_bom(snapshot)
    candidates = [
        key for kind, key in seeds
        if kind == "iqc" and reachable.get(iqc_by_id[key]["material_code"])
    ]
    assert len(candidates) >= 2, "장치④ 후보 자재가 부족합니다."
    rng.shuffle(candidates)
    device4 = {}
    for key in candidates[:2]:
        material = iqc_by_id[key]["material_code"]
        product_code, step_code, lot_ids = rng.choice(reachable[material])
        device4[key] = (product_code, step_code, rng.choice(lot_ids))
    return {"device2": device2, "device4": device4, "device5": device5}


def _reachable_bom(snapshot: MesSnapshot) -> dict[str, list[tuple[str, str, list[str]]]]:
    """자재 → [(제품, 공정, 그 공정을 실제로 지난 로트들)]."""
    product_of = {l["lot_id"]: l["product_code"] for l in snapshot.lots}
    lots_at: dict[tuple[str, str], list[str]] = {}
    for row in snapshot.process_results:
        key = (product_of[row["lot_id"]], row["step_code"])
        bucket = lots_at.setdefault(key, [])
        if row["lot_id"] not in bucket:
            bucket.append(row["lot_id"])
    reachable: dict[str, list[tuple[str, str, list[str]]]] = {}
    for entry in snapshot.bom:
        key = (entry["product_code"], entry["step_code"])
        if key in lots_at:
            reachable.setdefault(entry["material_code"], []).append(
                (entry["product_code"], entry["step_code"], sorted(lots_at[key]))
            )
    return reachable


def _base_ncr(rng, ncr_id: str, source: str, defect: dict, detected: dt.date) -> dict:
    cause = rng.choice(_GENERAL_CAUSES)
    low, high = _ESTIMATED_COST[defect["severity"]]
    status = rng.choices(
        ("완료", "조사중", "처리대기", "접수", "보류"), weights=(45, 20, 15, 15, 5)
    )[0]
    return {
        "ncr_id": ncr_id,
        "ncr_source": source,
        "inspection_id": None,
        "iqc_id": None,
        "lot_id": None,
        "product_code": None,
        "product_name": None,
        "step_code": None,
        "step_name": None,
        "material_code": None,
        "eqp_id": None,
        "defect_code": defect["defect_code"],
        "mes_defect_code": defect["mes_defect_code"],
        "severity": defect["severity"],
        "affected_qty": 1,
        "detected_date": detected,
        "root_cause_category": cause,
        "root_cause_ko": _ROOT_CAUSE_TEXT[cause],
        "immediate_action_ko": _IMMEDIATE_ACTION[cause],
        "owner_dept_ko": rng.choice(OWNER_DEPTS),
        "owner_name": rng.choice(OWNER_NAMES),
        "status": status,
        "due_date": detected + dt.timedelta(days=rng.randint(7, 14)),
        "closed_date": None,
        "estimated_cost_krw": rng.randrange(low, high, 100_000),
    }


def _ncr_from_inspection(rng, ncr_id, inspection, mes, lots, by_mes_defect, devices) -> dict:
    mes_row = mes.get(inspection["mes_process_result_id"]) if inspection["mes_process_result_id"] else None
    if mes_row and mes_row.get("defect_code"):
        parent = mes_row["defect_code"]
    else:
        parent = rng.choice(STEP_DEFECT_MAP[inspection["step_code"]])
    defect = rng.choice(by_mes_defect[parent])
    detected = inspection["inspection_datetime"].date() + dt.timedelta(days=rng.randint(0, 2))
    ncr = _base_ncr(
        rng, ncr_id, _SOURCE_BY_INSPECTION_TYPE[inspection["inspection_type"]], defect, detected
    )
    ncr["inspection_id"] = inspection["inspection_id"]
    ncr["lot_id"] = inspection["lot_id"]
    ncr["product_code"] = inspection["product_code"]
    ncr["product_name"] = inspection["product_name"]
    ncr["step_code"] = inspection["step_code"]
    ncr["step_name"] = inspection["step_name"]
    ncr["eqp_id"] = inspection["eqp_id"]

    if mes_row is not None:
        ncr["affected_qty"] = max(1, min(rng.randint(1, 12), mes_row["out_qty"]))
        is_quality_hold = (
            mes_row["result"] == "Pass"
            and not mes_row.get("defect_code")
            and inspection["judgment"] == "불합격"
            and inspection["defect_found_qty"] == 0
        )
        if is_quality_hold:
            ncr["root_cause_category"] = "측정"
            ncr["root_cause_ko"] = _ROOT_CAUSE_TEXT["측정"]
            ncr["immediate_action_ko"] = _IMMEDIATE_ACTION["측정"]
    elif inspection["lot_id"]:
        ncr["affected_qty"] = max(1, min(rng.randint(1, 12), lots[inspection["lot_id"]]["wafer_qty"]))
    else:
        ncr["affected_qty"] = rng.randint(1, 8)
    return ncr


def _ncr_from_iqc(rng, ncr_id, iqc, defect_by_code, lots, step_names, devices) -> dict:
    defect = defect_by_code[iqc["defect_code"]]
    detected = iqc["inspection_date"] + dt.timedelta(days=rng.randint(0, 2))
    ncr = _base_ncr(rng, ncr_id, "입고검사", defect, detected)
    ncr["iqc_id"] = iqc["iqc_id"]
    ncr["material_code"] = iqc["material_code"]
    ncr["affected_qty"] = max(1, min(rng.randint(1, 50), int(iqc["received_qty"])))
    ncr["root_cause_category"] = "자재"
    ncr["root_cause_ko"] = _ROOT_CAUSE_TEXT["자재"]
    ncr["immediate_action_ko"] = _IMMEDIATE_ACTION["자재"]

    traced = devices["device4"].get(iqc["iqc_id"])
    if traced:
        product_code, step_code, lot_id = traced
        ncr["product_code"] = product_code
        ncr["product_name"] = lots[lot_id]["product_name"]
        ncr["step_code"] = step_code
        ncr["step_name"] = step_names[step_code]
        ncr["lot_id"] = lot_id
        ncr["immediate_action_ko"] = "해당 자재가 투입된 로트를 역추적해 후속 공정 홀드"
    return ncr


def _ncr_from_complaint(rng, ncr_id, product, defect_codes, base_date) -> dict:
    defect = rng.choice(defect_codes)
    detected = base_date + dt.timedelta(days=rng.randint(3, 10))
    ncr = _base_ncr(rng, ncr_id, "고객제기", defect, detected)
    ncr["product_code"] = product["product_code"]
    ncr["product_name"] = product["product_name"]
    ncr["affected_qty"] = rng.randint(1, 10)
    ncr["root_cause_ko"] = _COMPLAINT_TEXT
    return ncr


def _disposition_for(rng, disposition_id: str, ncr: dict, devices: dict, step_names: dict) -> dict:
    inspection_id = ncr["inspection_id"]
    rework_step, rework_result, scrap_cost = None, None, 0

    if inspection_id in devices["device2"]:
        disposition_type, decision_body = "특채", "MRB"
    elif inspection_id in devices["device5"]:
        # 재작업을 시도했으나 실패해 결국 폐기된 건. 두 사실이 한 행에 함께 남는다.
        disposition_type, decision_body = "폐기", "MRB"
        rework_step, rework_result = ncr["step_code"], "실패"
    elif ncr["ncr_source"] == "입고검사":
        disposition_type = "반품" if ncr["defect_code"] and ncr["severity"] != "Minor" else "특채"
        decision_body = "품질책임자"
    elif ncr["ncr_source"] == "고객제기":
        disposition_type = rng.choice(("선별", "폐기", "반품"))
        decision_body = "품질책임자"
    else:
        # 특채는 장치②와 입고검사에만 허용한다. 여기서 새면 장치② 건수가 흔들린다.
        disposition_type = rng.choice(("재작업", "선별", "폐기"))
        decision_body = rng.choice(("MRB", "품질책임자", "생산책임자"))
        if disposition_type == "재작업":
            rework_step = ncr["step_code"]
            rework_result = rng.choice(("성공", "성공", "진행중"))

    disposition_qty = rng.randint(1, ncr["affected_qty"])
    if disposition_type == "폐기":
        scrap_cost = disposition_qty * rng.randrange(*_SCRAP_UNIT_COST, 100_000)
    decision_date = ncr["detected_date"] + dt.timedelta(days=rng.randint(1, 5))
    return {
        "disposition_id": disposition_id,
        "ncr_id": ncr["ncr_id"],
        "lot_id": ncr["lot_id"],
        "product_code": ncr["product_code"],
        "step_code": ncr["step_code"],
        "disposition_type": disposition_type,
        "disposition_qty": disposition_qty,
        "decision_date": decision_date,
        "decision_body_ko": decision_body,
        "approver_name": rng.choice(APPROVER_NAMES),
        "approval_status": rng.choices(("승인", "대기", "반려"), weights=(85, 10, 5))[0],
        "rework_step_code": rework_step,
        "rework_result": rework_result,
        "scrap_cost_krw": scrap_cost,
        "effectiveness_check_date": decision_date + dt.timedelta(days=rng.randint(7, 21)),
        "effectiveness_result": rng.choices(("유효", "재발", "확인중"), weights=(70, 10, 20))[0],
        "reason_ko": f"{ncr['severity']} 등급 부적합에 대한 {disposition_type} 결정",
    }

