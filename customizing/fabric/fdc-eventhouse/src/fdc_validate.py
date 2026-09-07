"""적재 전 검증.

Eventhouse 는 append-only 다. 잘못 쓴 행을 지우려면 `.drop extents` 로
익스텐트 단위로 지워야 하고, 같은 익스텐트에 섞인 정상 행까지 날아간다.
그래서 쓰기 전에 검사하고, 치명 항목이 걸리면 아무것도 쓰지 않고 멈춘다.

치명이 아닌 항목은 경고만 낸다. 데이터가 틀린 건 아니지만 실습 소재로서
쓸모가 떨어지는 경우다. 예를 들어 경보가 하나도 없으면 "이상 설비를
찾아라"는 실습을 할 수 없다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from src.fdc_anomaly import build_profiles
from src.fdc_generator import ALARM, IDLE, NORMAL, RUNNING, classify
from src.fdc_sensors import (
    SAMPLE_INTERVAL_SEC,
    build_sensor_spec_rows,
    idle_sensors,
    sensor_by_code,
)
from src.fdc_schema import READING_SCHEMA

FORBIDDEN_COLUMNS = frozenset(
    {"lot_id", "product_code", "wafer_qty", "defect_code", "judgment", "result", "operator"}
)

MAX_ALARM_RATIO = 0.05


@dataclass(frozen=True)
class Check:
    number: int
    name: str
    fatal: bool
    passed: bool
    detail: str = ""
    skipped: bool = False

    @property
    def mark(self) -> str:
        if self.skipped:
            return "SKIP"
        if self.passed:
            return "OK"
        return "FAIL" if self.fatal else "WARN"


class ValidationFailed(RuntimeError):
    """치명 항목이 걸렸다. 적재하면 안 된다."""


def validate(readings: list[dict], facts, watermark: datetime | None = None) -> list[Check]:
    checks: list[Check] = []
    known_eqp = {e["eqp_id"] for e in facts.equipment}
    spec_keys = {(r["eqp_type"], r["sensor_code"]) for r in build_sensor_spec_rows()}
    columns = set(readings[0]) if readings else set()

    leaked = sorted(FORBIDDEN_COLUMNS & (columns | {n for n, _ in READING_SCHEMA}))
    checks.append(
        Check(
            1,
            "무중복 원칙: 로트 관련 컬럼이 없다",
            True,
            not leaked,
            f"유출된 컬럼: {leaked}" if leaked else "FDC 는 로트를 모른다",
        )
    )

    unknown_eqp = sorted({r["eqp_id"] for r in readings} - known_eqp)
    checks.append(
        Check(
            2,
            "모든 eqp_id 가 MES 설비 목록에 있다",
            True,
            not unknown_eqp,
            f"MES 에 없는 설비: {unknown_eqp}" if unknown_eqp else f"설비 {len(known_eqp)}대",
        )
    )

    unknown_sensor = sorted(
        {(r["eqp_type"], r["sensor_code"]) for r in readings} - spec_keys
    )
    checks.append(
        Check(
            3,
            "모든 sensor_code 가 센서 스펙에 있다",
            True,
            not unknown_sensor,
            f"스펙에 없는 센서: {unknown_sensor}" if unknown_sensor else f"센서 스펙 {len(spec_keys)}종",
        )
    )

    misaligned = [
        r for r in readings if int(r["reading_ts"].timestamp()) % SAMPLE_INTERVAL_SEC
    ]
    checks.append(
        Check(
            4,
            f"reading_ts 가 {SAMPLE_INTERVAL_SEC}초 격자에 정렬돼 있다",
            True,
            not misaligned,
            f"어긋난 행 {len(misaligned)}개" if misaligned else "전부 정렬",
        )
    )

    stale = [r for r in readings if watermark and r["reading_ts"] <= watermark]
    checks.append(
        Check(
            5,
            "생성 구간이 watermark 보다 뒤에 있다",
            True,
            not stale,
            f"이미 적재된 시각의 행 {len(stale)}개" if stale else f"watermark={watermark}",
        )
    )

    # 스펙에 없는 센서는 건너뛴다. 3번 검사가 이미 치명으로 잡았고, 여기서
    # 조회를 시도하면 KeyError 로 죽어 검증 리포트 자체가 나오지 않는다.
    spec_lookup = {
        (r["eqp_type"], r["sensor_code"]): sensor_by_code(r["eqp_type"], r["sensor_code"])
        for r in build_sensor_spec_rows()
    }
    mismatched = [
        r
        for r in readings
        if (r["eqp_type"], r["sensor_code"]) in spec_lookup
        and r["status"] != classify(spec_lookup[(r["eqp_type"], r["sensor_code"])], r["value"])
    ]
    checks.append(
        Check(
            6,
            "status 가 센서 한계와 일치한다",
            False,
            not mismatched,
            f"불일치 {len(mismatched)}행" if mismatched else "전부 일치",
        )
    )

    # 7·8 번은 데이터셋 전체의 성질이지 배치 하나의 성질이 아니다. 첫 백필
    # 이후의 배치는 MES 구간을 지난 유휴만 담고, 유휴에는 이상을 싣지 않으므로
    # 경보가 구조적으로 0 이다. 그대로 평가하면 정상 운영 중인 모든 실행이
    # 경고 2건을 뱉어 진짜 경고가 묻힌다.
    running = [r for r in readings if r.get("run_status") == RUNNING]

    counts = Counter(r["status"] for r in readings)
    ratio = counts[ALARM] / len(readings) if readings else 0.0
    checks.append(
        Check(
            7,
            f"Alarm 비율이 0 초과 {MAX_ALARM_RATIO:.0%} 미만이다",
            False,
            (0 < ratio < MAX_ALARM_RATIO) if running else True,
            (
                f"Alarm {counts[ALARM]}행 / 전체 {len(readings)}행 = {ratio:.2%}"
                if running
                else "가동 행이 없는 배치라 평가하지 않습니다"
            ),
            skipped=not running,
        )
    )

    profiles = build_profiles(facts)
    abnormal = Counter(r["eqp_id"] for r in readings if r["status"] != NORMAL)
    ranked = sorted(profiles.values(), key=lambda p: p.defect_rate)
    worst, best = (ranked[-1].eqp_id, ranked[0].eqp_id) if ranked else ("", "")
    checks.append(
        Check(
            8,
            "불량률 상위 설비의 이상이 하위 설비보다 많다",
            False,
            (abnormal[worst] > abnormal[best]) if running else True,
            (
                f"{worst}={abnormal[worst]}행, {best}={abnormal[best]}행"
                if running
                else "가동 행이 없는 배치라 평가하지 않습니다"
            ),
            skipped=not running,
        )
    )

    bad_status = sorted({r.get("run_status") for r in readings} - {RUNNING, IDLE})
    idle_common = {s.sensor_code for s in idle_sensors()}
    idle_leak = sorted(
        {
            r["sensor_code"]
            for r in readings
            if r.get("run_status") == IDLE and r["sensor_code"] not in idle_common
        }
    )
    checks.append(
        Check(
            9,
            "run_status 가 Run/Idle 뿐이고 유휴에 공정 센서가 없다",
            True,
            not bad_status and not idle_leak,
            (
                f"알 수 없는 상태: {bad_status}" if bad_status
                else f"유휴에 섞인 공정 센서: {idle_leak}" if idle_leak
                else f"가동 {sum(1 for r in readings if r['run_status'] == RUNNING):,}행"
            ),
        )
    )

    return checks


def format_report(checks: list[Check]) -> str:
    lines = ["적재 전 검증", "=" * 60]
    for check in checks:
        flag = " (치명)" if check.fatal else ""
        lines.append(f"[{check.mark:4s}] {check.number}. {check.name}{flag}")
        if check.detail:
            lines.append(f"        {check.detail}")
    failed = [c for c in checks if not c.passed and c.fatal]
    warned = [c for c in checks if not c.passed and not c.fatal]
    skipped = [c for c in checks if c.skipped]
    lines.append("=" * 60)
    tail = f", 건너뜀 {len(skipped)}건" if skipped else ""
    lines.append(f"치명 {len(failed)}건, 경고 {len(warned)}건{tail} / 전체 {len(checks)}건")
    return "\n".join(lines)


def raise_on_fatal(checks: list[Check]) -> None:
    failed = [c for c in checks if not c.passed and c.fatal]
    if failed:
        detail = "; ".join(f"{c.number}. {c.name} — {c.detail}" for c in failed)
        raise ValidationFailed(f"치명 검증 실패로 적재를 중단합니다: {detail}")
