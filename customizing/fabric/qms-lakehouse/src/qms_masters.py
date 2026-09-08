"""QMS 마스터 3종. 불량코드 24 / 검사기준 108 / 검사원 15."""

from __future__ import annotations

import datetime as dt
import random

from src.mes_client import MesSnapshot, anchor_date
from src.qms_reference import (
    CHARACTERISTIC_BASE,
    CONTROL_METHODS,
    DEFECT_DETAILS,
    DEFECT_TAXONOMY,
    INSPECTION_FREQUENCIES,
    INSPECTOR_NAMES,
    INSPECTOR_TEAMS,
    PRODUCT_CD_SCALE,
    QUALIFICATION_LEVELS,
    SAMPLING_METHODS,
    SEED_MASTERS,
    SEVERITY_SCORE,
    STEP_CHARACTERISTICS,
)

_SHIFTS = ("A", "B", "C")

# 검사원 자격 갱신 주기. 3년마다 재인증한다.
_CERTIFICATION_PERIOD_DAYS = 365 * 3


def build_defect_codes() -> list[dict]:
    """MES 상위 불량코드 6종을 QMS 세부코드 24종으로 전개한다."""
    meta = {code: (prefix, category, steps) for code, prefix, category, steps in DEFECT_TAXONOMY}
    rows = []
    for mes_code, seq, name_ko, name_en, severity, cause, action in DEFECT_DETAILS:
        prefix, category, steps = meta[mes_code]
        rows.append(
            {
                "defect_code": f"DEF-{prefix}-{seq:03d}",
                "defect_name_ko": name_ko,
                "defect_name_en": name_en,
                "mes_defect_code": mes_code,
                "defect_category": category,
                "severity": severity,
                "severity_score": SEVERITY_SCORE[severity],
                "typical_step_codes": steps,
                "standard_cause_ko": cause,
                "standard_action_ko": action,
                "is_active": True,
            }
        )
    return rows


def defect_codes_by_mes(defect_codes: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in defect_codes:
        grouped.setdefault(row["mes_defect_code"], []).append(row)
    return grouped


def build_inspection_specs(snapshot: MesSnapshot) -> list[dict]:
    """제품 4 × 공정 9 × 특성 3 = 108행. 규격은 제품 노드에 따라 조정된다."""
    base_date = anchor_date(snapshot)
    product_names = {p["product_code"]: p["product_name"] for p in snapshot.products}
    step_names = {s["step_code"]: s["step_name"] for s in snapshot.route}
    rows = []
    for product_code in sorted(product_names):
        scale = PRODUCT_CD_SCALE[product_code]
        for step in sorted(snapshot.route, key=lambda s: s["seq"]):
            step_code = step["step_code"]
            for char_code in STEP_CHARACTERISTICS[step_code]:
                name_ko, meas_type, unit, target, lsl, usl = CHARACTERISTIC_BASE[char_code]
                if char_code == "CD":
                    target, lsl, usl = target * scale, lsl * scale, usl * scale
                sampling, sample_size = SAMPLING_METHODS[meas_type]
                rows.append(
                    {
                        "spec_id": f"SPEC-{product_code}-{step_code}-{char_code}",
                        "product_code": product_code,
                        "product_name": product_names[product_code],
                        "step_code": step_code,
                        "step_name": step_names[step_code],
                        "characteristic_code": char_code,
                        "characteristic_name_ko": name_ko,
                        "measurement_type": meas_type,
                        "unit": unit,
                        "target_value": round(target, 3),
                        "lsl": round(lsl, 3),
                        "usl": round(usl, 3),
                        "cpk_target": 1.33,
                        "sampling_method": sampling,
                        "sample_size": sample_size,
                        "inspection_frequency": INSPECTION_FREQUENCIES[char_code],
                        "control_method_ko": CONTROL_METHODS[meas_type],
                        "spec_version": "v1.2",
                        "effective_from": base_date - dt.timedelta(days=180),
                        "is_active": True,
                    }
                )
    return rows


def spec_index(specs: list[dict]) -> dict[tuple[str, str, str], dict]:
    return {(s["product_code"], s["step_code"], s["characteristic_code"]): s for s in specs}


def _certified_until(certified_from: dt.date, base_date: dt.date) -> dt.date:
    """검사원 자격 만료일. 활동 중이면 항상 현재보다 뒤에 있다.

    자격은 3년마다 갱신한다. 취득일에 3년을 한 번만 더하면 6년 전에 자격을 딴
    사람은 3년 전에 만료된 상태가 된다. 그 상태로 검사 기록을 만들면 자격 없는
    검사원이 수행한 검사가 대량으로 생긴다. 실제 QMS 에서 이는 그 자체로 중대
    부적합이라 데이터가 앞뒤로 맞지 않는다.

    그래서 취득일부터 3년 주기를 반복해 현재를 지나는 첫 만료일을 쓴다. 앵커가
    어디로 이동해도 활동 중인 검사원의 자격은 유효하다.
    """
    elapsed = (base_date - certified_from).days
    periods = elapsed // _CERTIFICATION_PERIOD_DAYS + 1
    return certified_from + dt.timedelta(days=_CERTIFICATION_PERIOD_DAYS * periods)


def build_inspectors(snapshot: MesSnapshot) -> list[dict]:
    """팀 5 × 교대 3 = 15명."""
    base_date = anchor_date(snapshot)
    rng = random.Random(SEED_MASTERS)
    rows = []
    for team_no, (team_ko, certified) in enumerate(INSPECTOR_TEAMS):
        for shift_no, shift in enumerate(_SHIFTS):
            index = team_no * len(_SHIFTS) + shift_no
            years = rng.randint(1, 6)
            certified_from = base_date - dt.timedelta(days=365 * years + rng.randint(0, 300))
            rows.append(
                {
                    "inspector_id": f"QI-{index + 1:03d}",
                    "inspector_name": INSPECTOR_NAMES[index],
                    "team_ko": team_ko,
                    "shift_code": shift,
                    "qualification_level": QUALIFICATION_LEVELS[index % len(QUALIFICATION_LEVELS)],
                    "certified_characteristics": certified,
                    "certified_from": certified_from,
                    "certified_until": _certified_until(certified_from, base_date),
                    "is_active": True,
                }
            )
    return rows
