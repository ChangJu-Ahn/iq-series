"""qms_measurement 260행.

IPQC-RT 40×3 = 120, PCS 36×3 = 108, EQV 16×2 = 32.
IPQC 기본과 OQC는 합부만 남기고 측정치를 기록하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import not_after
from src.qms_masters import spec_index
from src.qms_reference import (
    CHARACTERISTIC_BASE,
    METROLOGY_EQP,
    SEED_MEASUREMENT,
    STEP_CHARACTERISTICS,
)

# 설비 검증은 특정 제품을 위한 검사가 아니다. 규격이 있어야 값을 뽑을 수 있으므로
# 기준 제품 하나를 정해 그 규격으로 측정한다.
EQV_SPEC_PRODUCT = "DDR5"

# 공정능력 미달(PCS 조건부합격)은 산포를 키워 표현한다. Cpk 는 약 0.6이 된다.
_DEGRADED_SIGMA_FACTOR = 2.2


def nominal_sigma(spec: dict) -> float:
    """규격폭의 1/8. 공칭 Cpk 가 1.33이 되는 산포다."""
    return (spec["usl"] - spec["lsl"]) / 8


def characteristics_for(inspection: dict) -> tuple[str, ...]:
    """검사 유형별 측정 특성. 개수가 measurement_count 와 항상 일치한다."""
    count = inspection["measurement_count"]
    if count == 0:
        return ()
    return STEP_CHARACTERISTICS[inspection["step_code"]][:count]


def _spec_product(inspection: dict) -> str:
    return inspection["product_code"] or EQV_SPEC_PRODUCT


def _draw(rng: random.Random, spec: dict, sigma: float, force_out: bool) -> float:
    lsl, usl, target = spec["lsl"], spec["usl"], spec["target_value"]
    if force_out:
        margin = (usl - lsl) * rng.uniform(0.04, 0.12)
        # 계수형(파티클수)은 하한이 0이라 아래로 이탈시키면 물리적으로 말이 안 된다.
        if spec["measurement_type"] == "계수형" or rng.random() < 0.5:
            value = usl + margin
        else:
            value = lsl - margin
    else:
        value = rng.gauss(target, sigma)
    if spec["unit"] == "ea":
        return float(max(0, round(value)))
    return round(value, 4)


def build_measurements(
    inspections: list[dict], specs: list[dict], as_of: dt.datetime
) -> list[dict]:
    rng = random.Random(SEED_MEASUREMENT)
    index = spec_index(specs)
    rows: list[dict] = []
    seq = 0

    for inspection in inspections:
        chars = characteristics_for(inspection)
        if not chars:
            continue
        product_code = _spec_product(inspection)
        degraded = inspection["judgment"] == "조건부합격" and inspection["has_nonconformance"]
        # 불합격 검사는 반드시 규격 이탈점을 하나 이상 남긴다. 판정과 측정이
        # 어긋나면 에이전트가 모순된 답을 하게 된다.
        force_index = 0 if inspection["judgment"] == "불합격" else -1

        for position, char_code in enumerate(chars):
            spec = index[(product_code, inspection["step_code"], char_code)]
            sigma = nominal_sigma(spec) * (_DEGRADED_SIGMA_FACTOR if degraded else 1.0)
            value = _draw(rng, spec, sigma, force_out=position == force_index)
            outside = value < spec["lsl"] or value > spec["usl"]
            seq += 1
            rows.append(
                {
                    "measurement_id": f"MEA-2026-{seq:06d}",
                    "inspection_id": inspection["inspection_id"],
                    "spec_id": spec["spec_id"],
                    "lot_id": inspection["lot_id"],
                    "product_code": product_code,
                    "step_code": inspection["step_code"],
                    "characteristic_code": char_code,
                    "characteristic_name_ko": CHARACTERISTIC_BASE[char_code][0],
                    "sample_no": position % max(inspection["inspected_wafer_qty"], 1) + 1,
                    "site_no": position + 1,
                    "measured_value": value,
                    "unit": spec["unit"],
                    "target_value": spec["target_value"],
                    "lsl": spec["lsl"],
                    "usl": spec["usl"],
                    "deviation_pct": round(
                        (value - spec["target_value"]) / spec["target_value"] * 100, 3
                    ),
                    "is_out_of_spec": outside,
                    "judgment": "NG" if outside else "OK",
                    "metrology_eqp_id": METROLOGY_EQP[char_code],
                    "measured_by": inspection["inspector_id"],
                    # 계측은 검사 중에 이뤄지므로 검사 시각 뒤로 5분씩 밀린다.
                    # 앵커 직전 검사는 그만큼의 시간이 아직 없으므로 자른다.
                    "measured_at": not_after(
                        inspection["inspection_datetime"]
                        + dt.timedelta(minutes=5 * (position + 1)),
                        as_of,
                    ),
                }
            )
    return rows
