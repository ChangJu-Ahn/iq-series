import copy

import pytest

from src.qms_validate import (
    DEVICE_TARGETS,
    TABLE_ROW_TARGETS,
    ValidationError,
    format_report,
    raise_on_fatal,
    validate,
)


def test_every_check_passes_on_generated_data(snapshot, tables):
    """검증 항목이 무엇인지까지 고정한다.

    개수만 세면 하나가 사라지고 다른 하나가 들어와도 통과한다. 검증이 조용히
    빠지는 것을 막으려면 이름을 적어야 한다.
    """
    results = validate(snapshot, tables)
    assert [r.name for r in results] == [
        "행수",
        "고아 키",
        "무중복 위반",
        "내부 FK",
        "타임존 통일",
        "시각 컬럼 분류",
        "시간 인과",
        "미래 완료 사건",
        "검사원 자격",
        "수량 정합",
        "측정치 규격",
        "계측 규격 복제",
        "불일치 장치",
    ]
    failed = [r for r in results if not r.passed]
    assert not failed, format_report(results)


def test_fatal_checks_are_the_ones_that_block_loading(snapshot, tables):
    """치명으로 표시된 항목이 무엇인지 고정한다.

    치명 표시가 빠지면 검증은 실패해도 적재가 그대로 진행된다.
    """
    fatal = {r.name for r in validate(snapshot, tables) if r.fatal}
    assert fatal == {
        "고아 키",
        "무중복 위반",
        "내부 FK",
        "타임존 통일",
        "시각 컬럼 분류",
        "미래 완료 사건",
        "검사원 자격",
        "계측 규격 복제",
    }


def test_row_counts_total_1004(tables):
    assert sum(TABLE_ROW_TARGETS.values()) == 1004
    for name, target in TABLE_ROW_TARGETS.items():
        assert len(tables[name]) == target


def test_device_targets_are_3_2_4_2_2():
    assert list(DEVICE_TARGETS.values()) == [3, 2, 4, 2, 2]


def test_orphan_lot_id_is_detected_and_fatal(snapshot, tables):
    broken = copy.deepcopy(tables)
    broken["qms_inspection"][0]["lot_id"] = "LOT9999"
    result = next(r for r in validate(snapshot, broken) if r.name == "고아 키")
    assert not result.passed
    assert result.fatal
    assert "LOT9999" in result.detail


def test_forbidden_column_is_detected_and_fatal(snapshot, tables):
    broken = copy.deepcopy(tables)
    for row in broken["qms_inspection"]:
        row["scrap_qty"] = 0
    result = next(r for r in validate(snapshot, broken) if r.name == "무중복 위반")
    assert not result.passed
    assert result.fatal
    assert "scrap_qty" in result.detail


def test_forbidden_column_on_a_single_late_row_is_still_detected(snapshot, tables):
    """첫 행만 훑으면 놓치는 경우. 마지막 행 하나에만 심는다."""
    broken = copy.deepcopy(tables)
    broken["qms_measurement"][-1]["operator"] = "OP-999"
    result = next(r for r in validate(snapshot, broken) if r.name == "무중복 위반")
    assert not result.passed
    assert "qms_measurement.operator" in result.detail


def test_orphan_mes_process_result_id_is_detected_and_fatal(snapshot, tables):
    """MES 공정이력 연결점도 고아 검사 대상이어야 한다."""
    broken = copy.deepcopy(tables)
    target = next(r for r in broken["qms_inspection"] if r["mes_process_result_id"] is not None)
    target["mes_process_result_id"] = 999999
    result = next(r for r in validate(snapshot, broken) if r.name == "고아 키")
    assert not result.passed
    assert result.fatal
    assert "999999" in result.detail


def test_broken_internal_reference_is_detected_and_fatal(snapshot, tables):
    broken = copy.deepcopy(tables)
    broken["qms_measurement"][0]["spec_id"] = "SPEC-NOPE-NOPE-NOPE"
    result = next(r for r in validate(snapshot, broken) if r.name == "내부 FK")
    assert not result.passed
    assert result.fatal


def test_mismatched_out_of_spec_flag_is_detected(snapshot, tables):
    broken = copy.deepcopy(tables)
    row = broken["qms_measurement"][0]
    row["is_out_of_spec"] = not row["is_out_of_spec"]
    result = next(r for r in validate(snapshot, broken) if r.name == "측정치 규격")
    assert not result.passed
    assert not result.fatal


def test_removed_device_row_is_detected(snapshot, tables):
    broken = copy.deepcopy(tables)
    for row in broken["qms_disposition"]:
        if row["rework_result"] == "실패":
            row["rework_result"] = "성공"
            break
    result = next(r for r in validate(snapshot, broken) if r.name == "불일치 장치")
    assert not result.passed


def test_raise_on_fatal_only_raises_for_fatal_failures(snapshot, tables):
    raise_on_fatal(validate(snapshot, tables))
    broken = copy.deepcopy(tables)
    broken["qms_inspection"][0]["product_code"] = "NOPE"
    with pytest.raises(ValidationError):
        raise_on_fatal(validate(snapshot, broken))
    tolerable = copy.deepcopy(tables)
    row = tolerable["qms_measurement"][0]
    row["is_out_of_spec"] = not row["is_out_of_spec"]
    raise_on_fatal(validate(snapshot, tolerable))


def test_report_lists_every_check(snapshot, tables):
    report = format_report(validate(snapshot, tables))
    for name in ("행수", "고아 키", "무중복 위반", "내부 FK", "시간 인과", "수량 정합", "측정치 규격", "불일치 장치"):
        assert name in report
