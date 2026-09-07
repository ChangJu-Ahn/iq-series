from datetime import datetime, timedelta, timezone

import pytest

from src.fdc_generator import build_readings
from src.fdc_validate import (
    Check,
    ValidationFailed,
    format_report,
    raise_on_fatal,
    validate,
)

T0 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def readings(facts, busy_window):
    """24시간 백필. 경보 비율 검사가 의미를 가지려면 이 규모가 필요하다."""
    return build_readings(facts, *busy_window)


@pytest.fixture(scope="module")
def checks(readings, facts, busy_window):
    return validate(readings, facts, watermark=busy_window[0])


def test_all_nine_checks_run(checks):
    assert [c.number for c in checks] == list(range(1, 10))


def test_healthy_data_passes_everything(checks):
    failed = [(c.number, c.name, c.detail) for c in checks if not c.passed]
    assert not failed, failed


def test_fatal_flags_match_spec(checks):
    fatal = {c.number for c in checks if c.fatal}
    assert fatal == {1, 2, 3, 4, 5, 9}


def test_lot_column_leak_is_fatal(readings, facts):
    tainted = [dict(r, lot_id="LOT-0001") for r in readings]
    check = next(c for c in validate(tainted, facts) if c.number == 1)
    assert not check.passed and check.fatal
    assert "lot_id" in check.detail


def test_unknown_equipment_is_fatal(readings, facts):
    tainted = [dict(readings[0], eqp_id="EQP-GHOST")] + readings[1:]
    check = next(c for c in validate(tainted, facts) if c.number == 2)
    assert not check.passed and check.fatal
    assert "EQP-GHOST" in check.detail


def test_unknown_sensor_is_fatal(readings, facts):
    tainted = [dict(readings[0], sensor_code="NO_SUCH_SENSOR")] + readings[1:]
    check = next(c for c in validate(tainted, facts) if c.number == 3)
    assert not check.passed and check.fatal


def test_misaligned_timestamp_is_fatal(readings, facts):
    off = dict(readings[0], reading_ts=readings[0]["reading_ts"] + timedelta(seconds=7))
    check = next(c for c in validate([off] + readings[1:], facts) if c.number == 4)
    assert not check.passed and check.fatal


def test_rows_at_or_before_watermark_are_fatal(readings, facts):
    """watermark 이하를 다시 만들면 중복 행이 쌓인다."""
    watermark = readings[0]["reading_ts"]
    check = next(c for c in validate(readings, facts, watermark=watermark) if c.number == 5)
    assert not check.passed and check.fatal


def test_watermark_none_passes(readings, facts):
    check = next(c for c in validate(readings, facts, watermark=None) if c.number == 5)
    assert check.passed


def test_status_mismatch_is_warning_not_fatal(readings, facts):
    index = next(i for i, r in enumerate(readings) if r["status"] == "Normal")
    tainted = list(readings)
    tainted[index] = dict(readings[index], status="Alarm")
    check = next(c for c in validate(tainted, facts) if c.number == 6)
    assert not check.passed
    assert not check.fatal


def test_unknown_sensor_does_not_crash_status_check(readings, facts):
    """3번이 치명으로 잡은 뒤에도 6번이 죽지 않아야 검증 리포트가 나온다."""
    tainted = [dict(readings[0], sensor_code="NO_SUCH_SENSOR")] + readings[1:]
    result = validate(tainted, facts)
    assert len(result) == 9
    assert next(c for c in result if c.number == 6).passed


def test_zero_alarms_is_warning(facts, busy_window):
    """경보가 없으면 데이터는 멀쩡해도 실습 소재가 못 된다."""
    short = build_readings(facts, busy_window[0], busy_window[0] + timedelta(seconds=30))
    flat = [dict(r, status="Normal") for r in short]
    check = next(c for c in validate(flat, facts) if c.number == 7)
    assert not check.passed
    assert not check.fatal


def test_too_many_alarms_is_warning(readings, facts):
    noisy = [dict(r, status="Alarm") for r in readings]
    check = next(c for c in validate(noisy, facts) if c.number == 7)
    assert not check.passed
    assert not check.fatal


def test_raise_on_fatal_blocks_ingestion(readings, facts):
    tainted = [dict(r, lot_id="LOT-0001") for r in readings]
    with pytest.raises(ValidationFailed, match="적재를 중단"):
        raise_on_fatal(validate(tainted, facts))


def test_raise_on_fatal_allows_warnings_only(readings, facts):
    tainted = [dict(readings[0], status="Alarm")] + readings[1:]
    raise_on_fatal(validate(tainted, facts))


def test_raise_on_fatal_silent_when_clean(checks):
    raise_on_fatal(checks)


def test_report_marks_each_outcome():
    report = format_report(
        [
            Check(1, "통과 항목", True, True, "상세"),
            Check(2, "치명 실패", True, False, "이유"),
            Check(3, "경고", False, False, "이유"),
        ]
    )
    assert "[OK  ] 1." in report
    assert "[FAIL] 2." in report
    assert "[WARN] 3." in report
    assert "치명 1건, 경고 1건 / 전체 3건" in report


def test_report_shows_fatal_tag():
    report = format_report([Check(1, "항목", True, True)])
    assert "(치명)" in report


def test_validate_handles_empty_readings(facts):
    result = validate([], facts)
    assert len(result) == 9


def test_unknown_run_status_is_fatal(readings, facts):
    rows = [dict(r) for r in readings]
    rows[0]["run_status"] = "Maintenance"
    assert any(c.fatal and not c.passed for c in validate(rows, facts))


def test_missing_run_status_is_fatal(readings, facts):
    rows = [dict(r) for r in readings]
    del rows[0]["run_status"]
    assert any(c.fatal and not c.passed for c in validate(rows, facts))


def test_idle_row_with_process_sensor_is_fatal(readings, facts):
    """유휴에 공정 센서가 섞이면 0 값이 그대로 경보가 된다."""
    rows = [dict(r) for r in readings]
    victim = next(r for r in rows if r["run_status"] == "Idle")
    victim["sensor_code"] = "RF_POWER"
    assert any(c.fatal and not c.passed for c in validate(rows, facts))


def test_clean_readings_have_no_fatal_check(readings, facts):
    assert not any(c.fatal and not c.passed for c in validate(readings, facts))


def test_lot_columns_still_forbidden(readings, facts):
    """무중복 원칙은 그대로다. run_status 는 설비 상태이지 로트가 아니다."""
    rows = [dict(r) for r in readings]
    rows[0]["lot_id"] = "LOT0010"
    assert any(c.fatal and not c.passed for c in validate(rows, facts))


def test_idle_only_batch_skips_dataset_wide_checks(readings, facts):
    """유휴만 담긴 증분 배치가 매번 경고를 띄우면 안 된다.

    검사 7·8 은 데이터셋 전체의 성질인데 배치 단위로 평가된다. 백필 이후의
    3분 배치는 대개 유휴만 담고, 유휴에는 경보가 구조적으로 0 이라 이 두
    검사는 영구히 실패한다. 20명 × 480회/일이 전부 이 경고를 보게 된다.
    """
    idle_only = [r for r in readings if r["run_status"] == "Idle"]
    assert idle_only, "픽스처에 유휴 행이 있어야 이 테스트가 의미를 갖는다"

    checks = {c.number: c for c in validate(idle_only, facts)}
    for number in (7, 8):
        assert checks[number].skipped, f"검사 {number}가 유휴 배치에서 건너뛰지 않았다"
        assert checks[number].passed
        assert checks[number].mark == "SKIP"


def test_running_batch_still_evaluates_dataset_wide_checks(readings, facts):
    """가동 행이 있으면 건너뛰지 않는다. skip 이 검사를 무력화하면 안 된다."""
    checks = {c.number: c for c in validate(readings, facts)}
    for number in (7, 8):
        assert not checks[number].skipped
        assert checks[number].mark != "SKIP"


def test_report_reports_skipped_count(readings, facts):
    idle_only = [r for r in readings if r["run_status"] == "Idle"]
    assert "건너뜀 2건" in format_report(validate(idle_only, facts))
    assert "건너뜀" not in format_report(validate(readings, facts))
