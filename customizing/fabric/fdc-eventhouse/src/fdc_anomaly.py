"""MES 불량 실적에서 센서 이상을 유도한다.

이 모듈이 FDC와 MES를 잇는 지점이다. 텔레메트리를 난수로만 만들면 실습자가
"어느 설비가 이상한가"를 Eventhouse 안에서 찾아낸 뒤 MES에 물어봐도 답이
맞아떨어지지 않는다. 그래서 이탈의 크기와 대상 센서를 MES 실적에서 끌어온다.

- 얼마나: 설비의 불량률이 높을수록 이탈 진폭이 크다
- 어디에: 불량코드가 지목하는 센서 중 **런마다 하나**에만 이탈이 실린다
- 언제: 불량이 난 그 런의 `[in_time, out_time)` 구간에만 실린다

결과적으로 "CHAMBER_TEMP 가 튀는 설비"를 Eventhouse에서 찾으면 그 설비가
MES에서 실제로 불량이 많은 설비다. 나아가 **경보가 뜬 시각**을 MES에 물으면
그때 돌던 로트가 나온다. 두 시스템을 교차 질의할 이유가 생긴다.

FDC 는 로트를 모른다 — 판독값에 `lot_id` 를 남기지 않는다. 로트는 이 모듈이
이상을 어디에 실을지 정하는 계산에만 쓴다.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from dataclasses import dataclass

from src.fdc_sensors import sensors_for

# 이탈 진폭. 정상범위 반폭을 1.0 으로 보는 단위다.
# 경보 임계는 정상 반폭의 1.5~3.0 배에 있으므로 1.2 는 경고까지만, 2.8 은
# 경보까지 닿는다. 불량률이 가장 낮은 설비와 높은 설비를 이 두 값에 맞춘다.
EXCURSION_MIN = 1.4
EXCURSION_MAX = 2.9


# 불량코드가 지목하는 센서. 물리적 인과가 성립하는 것만 넣는다(설계 스펙 7.2).
DEFECT_SENSOR_HINT: dict[str, tuple[str, ...]] = {
    # 온도 급변 시 챔버 박리물이 생긴다
    "Particle": ("CHAMBER_TEMP", "AMBIENT_HUMIDITY"),
    # 기계적 접촉 과다
    "Scratch": ("PAD_PRESSURE", "MOTOR_CURRENT", "PROBE_FORCE"),
    # 열팽창에 의한 정렬 오차
    "Overlay": ("AMBIENT_TEMP", "STAGE_TEMP", "RETICLE_TEMP"),
    # 반응 가스 부족
    "Etch-Residue": ("GAS_FLOW", "PRECURSOR_FLOW", "RF_POWER"),
    # 습도 상승과 진공도 저하
    "Contamination": ("AMBIENT_HUMIDITY", "VACUUM"),
    # 노광·식각 조건 이탈
    "CD-OOS": ("FOCUS_OFFSET", "RF_POWER", "ILLUM_DOSE"),
}

# 지목 센서가 그 설비 유형에 하나도 없을 때 대신 쓴다. 모든 유형이 갖는 센서여야 한다.
FALLBACK_SENSOR = "AMBIENT_TEMP"


def seed(*parts: object) -> int:
    """결정적 시드. [0, 2**32) 정수.

    내장 `hash()` 를 쓰면 안 된다. 파이썬은 문자열 해시에 프로세스마다 다른
    난수를 섞으므로(PYTHONHASHSEED) 같은 입력이 실행마다 다른 값을 낸다.
    이 노트북은 3분마다 새 프로세스로 실행되며 백필과 라이브가 같은
    타임스탬프에 같은 값을 내야 한다.

    crc32 도 프로세스 고정이지만 GF(2) 위의 선형 함수라 입력이 몇 비트만
    다르면 출력 비트가 함께 움직인다. 실제로 설비명만 바꾼 48개 조합에서
    최하위 비트가 한쪽으로 몰려 이탈 방향이 거의 같은 쪽으로 쏠렸다.
    sha256 은 비선형이라 이런 뭉침이 없다. 여기서 sha256 은 보안 용도가
    아니라 결정적 혼합기로만 쓴다.
    """
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def unit_from_seed(*parts: object) -> float:
    """시드를 [0, 1) 실수로. 난수 발생기 대신 쓴다."""
    return seed(*parts) / 2**32


@dataclass(frozen=True)
class EquipmentProfile:
    eqp_id: str
    eqp_type: str
    step_code: str
    total_runs: int
    defect_runs: int
    defect_codes: dict[str, int]
    severity: float  # 설비 집합 안에서의 상대 불량률. 0.0~1.0

    @property
    def defect_rate(self) -> float:
        return self.defect_runs / self.total_runs if self.total_runs else 0.0


def build_profiles(facts) -> dict[str, EquipmentProfile]:
    """설비별 불량 프로파일.

    판정은 `defect_code` 유무로 본다. `judgment` 컬럼은 91건 전부 null 이라
    쓸 수 없고, `result` 는 Pass/Fail/Rework 3값이지만 불량코드가 붙은 행이
    Pass 로 남아 있는 경우가 있어 불량코드를 신호로 삼는다.
    """
    eqp_type_by_id = {e["eqp_id"]: e["eqp_type"] for e in facts.equipment}
    step_by_id = {e["eqp_id"]: e["step_code"] for e in facts.equipment}

    totals: dict[str, int] = {}
    defects: dict[str, int] = {}
    codes: dict[str, dict[str, int]] = {}
    for row in facts.process_results:
        eqp_id = row.get("eqp_id")
        if not eqp_id:
            continue
        totals[eqp_id] = totals.get(eqp_id, 0) + 1
        codes.setdefault(eqp_id, {})
        code = row.get("defect_code")
        if code:
            defects[eqp_id] = defects.get(eqp_id, 0) + 1
            codes[eqp_id][code] = codes[eqp_id].get(code, 0) + 1

    rates = {e: defects.get(e, 0) / totals[e] for e in totals}
    lo, hi = (min(rates.values()), max(rates.values())) if rates else (0.0, 0.0)
    span = hi - lo

    return {
        eqp_id: EquipmentProfile(
            eqp_id=eqp_id,
            eqp_type=eqp_type_by_id.get(eqp_id, ""),
            step_code=step_by_id.get(eqp_id, ""),
            total_runs=totals[eqp_id],
            defect_runs=defects.get(eqp_id, 0),
            defect_codes=dict(sorted(codes[eqp_id].items())),
            severity=(rates[eqp_id] - lo) / span if span else 0.0,
        )
        for eqp_id in sorted(totals)
    }


def fallback_sensor(eqp_type: str) -> str | None:
    """지목표가 이 설비에 없는 센서만 가리킬 때 대신 고를 센서.

    Mock MES 는 불량코드를 공정 단계와 무관하게 붙인다. 실제로 EQP-IMPL01
    (Implanter)에 Scratch 불량이 달려 있는데 Scratch 가 가리키는 센서는
    Implanter 에 하나도 없다. 그대로 두면 불량이 신호를 만들지 못한다.

    모든 설비 유형이 갖는 AMBIENT_TEMP 로 넘긴다. 클린룸 열관리 실패는
    실제로 여러 불량의 공통 원인이므로 물리적으로도 말이 된다.
    """
    available = {s.sensor_code for s in sensors_for(eqp_type)}
    return FALLBACK_SENSOR if FALLBACK_SENSOR in available else None


def hinted_sensors(profile: EquipmentProfile) -> dict[str, float]:
    """이탈을 실을 센서와 그 지목 비중. 합은 1.0.

    설비의 불량코드가 가리키는 센서 중 그 설비 유형에 실제로 달려 있는
    것만 남긴다. Furnace 에 RF_POWER 는 없으므로 Etch-Residue 불량이 있어도
    RF_POWER 에는 이탈을 실을 수 없다. 남는 게 없으면 폴백으로 넘긴다.
    """
    available = {s.sensor_code for s in sensors_for(profile.eqp_type)}
    weights: dict[str, float] = {}
    for code, count in profile.defect_codes.items():
        targets = [s for s in DEFECT_SENSOR_HINT.get(code, ()) if s in available]
        if not targets:
            chosen = fallback_sensor(profile.eqp_type)
            if chosen is None:
                continue
            targets = [chosen]
        for sensor_code in targets:
            weights[sensor_code] = weights.get(sensor_code, 0.0) + count / len(targets)
    total = sum(weights.values())
    return {k: v / total for k, v in sorted(weights.items())} if total else {}


def excursion_amplitude(profile: EquipmentProfile) -> float:
    """설비의 이탈 진폭 상한. 불량률이 높을수록 크다."""
    return EXCURSION_MIN + (EXCURSION_MAX - EXCURSION_MIN) * profile.severity


def candidate_sensors(defect_code: str | None, eqp_type: str) -> tuple[str, ...]:
    """이 불량이 지목하는 센서 중 그 설비에 실제로 달린 것.

    Mock MES 는 불량코드를 공정과 무관하게 붙인다. Implanter 에 Scratch 가
    달리면 지목 센서가 하나도 없고, 그때는 모든 유형이 갖는 AMBIENT_TEMP 로
    넘긴다. 클린룸 열관리 실패는 실제로 여러 불량의 공통 원인이다.
    """
    if not defect_code:
        return ()
    available = {s.sensor_code for s in sensors_for(eqp_type)}
    hinted = tuple(
        s for s in DEFECT_SENSOR_HINT.get(defect_code, ()) if s in available
    )
    if hinted:
        return hinted
    chosen = fallback_sensor(eqp_type)
    return (chosen,) if chosen else ()


def run_sensor(run, eqp_type: str) -> str | None:
    """이 런에서 실제로 흐르는 센서 하나. 불량이 없으면 None.

    후보 전부에 이탈을 나눠 실으면 진폭이 희석돼 어느 쪽도 경보에 못 닿는다.
    실제 공정 이탈도 보통 파라미터 하나가 흐르지 여러 개가 동시에 흐르지
    않는다. 런 식별자로 고르므로 같은 설비·같은 불량이라도 런마다 다른
    센서가 걸려 실습 소재가 다양해진다.
    """
    candidates = candidate_sensors(run.defect_code, eqp_type)
    if not candidates:
        return None
    index = seed(run.lot_id, run.step_code, run.eqp_id, run.defect_code)
    return candidates[index % len(candidates)]


def run_excursion(
    run, sensor_code: str, moment: datetime, profile: EquipmentProfile
) -> float:
    """런 구간 안의 이탈량. 정상범위 반폭이 1.0 인 단위.

    런이 진행될수록 커진다(progress 의 제곱). 공정이 서서히 이탈하다 끝에서
    불량으로 잡히는 모습이라 실습자에게 설명하기 쉽고, 런 앞부분이 잠잠해서
    경보가 끊이지 않는 일도 없다.
    """
    if not run.start <= moment < run.end:
        return 0.0
    if sensor_code != run_sensor(run, profile.eqp_type):
        return 0.0
    length = (run.end - run.start).total_seconds()
    if length <= 0:
        return 0.0
    progress = (moment - run.start).total_seconds() / length
    return excursion_sign(run.eqp_id, sensor_code) * excursion_amplitude(profile) * progress**2


def excursion_sign(eqp_id: str, sensor_code: str) -> int:
    """이탈 방향. 설비·센서마다 고정이며 위아래가 섞이게 한다."""
    return 1 if seed(eqp_id, sensor_code, "sign") % 2 == 0 else -1
