"""qms_incoming_inspection 200행.

MES는 자재를 알지만 그 자재의 입고 품질은 남기지 않는다. 공급업체, 성적서,
샘플링 판정은 전부 QMS 고유 사실이다.
"""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import MesSnapshot, anchor_date
from src.qms_masters import defect_codes_by_mes
from src.qms_reference import INSPECTION_ITEMS, SEED_INCOMING, SUPPLIERS

IQC_JUDGMENT_COUNTS = {"불합격": 24, "특채": 16, "합격": 160}

SUPPLIERS_BY_CATEGORY = {
    "Raw Wafer": ("SUP-A01", "SUP-A02"),
    "Chemical": ("SUP-B01", "SUP-B02"),
    "Gas": ("SUP-C01", "SUP-C02"),
    "Metal": ("SUP-D01", "SUP-D02"),
    "Mask": ("SUP-A01", "SUP-D02"),
    "Package": ("SUP-B02", "SUP-D01"),
}

MATERIAL_DEFECT_MAP = {
    "Raw Wafer": ("Scratch", "Particle"),
    "Chemical": ("Contamination", "Particle"),
    "Gas": ("Particle", "Contamination"),
    "Metal": ("Contamination",),
    "Mask": ("CD-OOS", "Overlay"),
    "Package": ("Scratch", "Contamination"),
}

# uom 별 1회 입고 수량 범위. 병 단위 가스와 미터 단위 와이어는 자릿수가 다르다.
_RECEIPT_QTY = {
    "EA": (200, 2000),
    "L": (20, 200),
    "BTL": (10, 60),
    "SET": (1, 6),
    "M": (1000, 8000),
    "KG": (50, 400),
}

_REMARKS = {
    "합격": "규격 이내. 정상 입고 처리",
    "특채": "경미한 규격 이탈. 사용처 한정 조건으로 특채 승인",
    "불합격": "규격 이탈 확인. 격리 후 부적합 보고서 발행",
}


def build_incoming_inspections(
    snapshot: MesSnapshot, inspectors: list[dict], defect_codes: list[dict]
) -> list[dict]:
    base_date = anchor_date(snapshot)
    rng = random.Random(SEED_INCOMING)
    suppliers = dict(SUPPLIERS)
    receiving = [i for i in inspectors if i["team_ko"] == "입고검사팀"]
    by_mes_defect = defect_codes_by_mes(defect_codes)
    materials = sorted(snapshot.materials, key=lambda m: m["material_code"])

    total = sum(IQC_JUDGMENT_COUNTS.values())
    slots = list(range(total))
    rng.shuffle(slots)
    judgments: dict[int, str] = {}
    cursor = 0
    for judgment in ("불합격", "특채", "합격"):
        count = IQC_JUDGMENT_COUNTS[judgment]
        for slot in slots[cursor : cursor + count]:
            judgments[slot] = judgment
        cursor += count

    rows = []
    for slot in range(total):
        material = materials[slot % len(materials)]
        category = material["category"]
        judgment = judgments[slot]
        supplier_code = SUPPLIERS_BY_CATEGORY[category][slot % 2]
        low, high = _RECEIPT_QTY[material["uom"]]
        received_qty = float(rng.randint(low, high))
        sample_size = min(rng.randint(3, 20), int(received_qty))
        # 입고일은 앵커에서 2~32일 전. 최소 2일을 띄우는 이유는 입고검사가
        # 입고 후 0~2일에 이뤄지기 때문이다. 0일부터 잡으면 검사일이 앵커를
        # 넘어 아직 오지 않은 날짜에 판정이 끝난 입고검사가 생긴다.
        receipt_date = base_date - dt.timedelta(days=rng.randint(2, 32))
        coa_received = rng.random() >= 0.10
        if not coa_received:
            coa_conformance = "미제출"
        elif judgment != "합격" and rng.random() < 0.7:
            coa_conformance = "불일치"
        else:
            coa_conformance = "일치"
        if judgment == "합격":
            defect_code = None
        else:
            mes_defect = rng.choice(MATERIAL_DEFECT_MAP[category])
            defect_code = rng.choice(by_mes_defect[mes_defect])["defect_code"]
        rows.append(
            {
                "iqc_id": f"IQC-2026-{slot + 1:04d}",
                "material_code": material["material_code"],
                "material_name": material["material_name"],
                "supplier_code": supplier_code,
                "supplier_name_ko": suppliers[supplier_code],
                "supplier_lot_no": f"{supplier_code[-3:]}-{receipt_date:%y%m}-{slot + 1:04d}",
                "receipt_date": receipt_date,
                "received_qty": received_qty,
                "uom": material["uom"],
                "sample_size": sample_size,
                "inspection_items_ko": INSPECTION_ITEMS[category],
                "judgment": judgment,
                "defect_code": defect_code,
                "coa_received": coa_received,
                "coa_conformance": coa_conformance,
                "inspector_id": rng.choice(receiving)["inspector_id"],
                "inspection_date": receipt_date + dt.timedelta(days=rng.randint(0, 2)),
                "remark_ko": _REMARKS[judgment],
            }
        )
    return rows
