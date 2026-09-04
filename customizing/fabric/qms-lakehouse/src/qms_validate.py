"""스펙 8절 검증 8항목.

장치 검출 질의는 데이터 값만으로 성립해야 한다. 전용 플래그 컬럼을 두면
검증은 쉬워지지만 '두 시스템을 함께 봐야 답이 나온다'는 전제가 무너진다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from src.mes_client import MesSnapshot

TABLE_ROW_TARGETS = {
    "qms_defect_code": 24,
    "qms_inspection_spec": 108,
    "qms_inspector": 15,
    "qms_inspection": 207,
    "qms_measurement": 260,
    "qms_incoming_inspection": 200,
    "qms_nonconformance": 95,
    "qms_disposition": 95,
}

FORBIDDEN_COLUMNS = frozenset({"mes_result", "scrap_qty", "operator", "in_qty", "out_qty"})

DEVICE_TARGETS = {
    "① MES Pass ↔ QMS 불합격": 3,
    "② MES Fail ↔ QMS 특채": 2,
    "③ MES 불량코드 없음 ↔ QMS 결함 검출": 4,
    "④ IQC 불합격 자재가 로트에 투입": 2,
    "⑤ MES Rework ↔ 재작업 실패 후 폐기": 2,
}


class ValidationError(RuntimeError):
    """치명 항목이 실패했을 때. 적재를 중단시킨다."""


@dataclass(frozen=True)
class ValidationResult:
    name: str
    passed: bool
    detail: str
    fatal: bool


def validate(snapshot: MesSnapshot, tables: dict[str, list[dict]]) -> list[ValidationResult]:
    return [
        _check_row_counts(tables),
        _check_orphan_keys(snapshot, tables),
        _check_forbidden_columns(tables),
        _check_internal_references(tables),
        _check_time_causality(snapshot, tables),
        _check_quantities(tables),
        _check_measurement_limits(tables),
        _check_devices(snapshot, tables),
    ]


def raise_on_fatal(results: list[ValidationResult]) -> None:
    fatal = [r for r in results if r.fatal and not r.passed]
    if fatal:
        raise ValidationError("치명 검증 실패:\n" + "\n".join(f"- {r.name}: {r.detail}" for r in fatal))


def format_report(results: list[ValidationResult]) -> str:
    width = max(len(r.name) for r in results)
    lines = [f"{'항목'.ljust(width)}  결과  상세", "-" * (width + 40)]
    for result in results:
        lines.append(f"{result.name.ljust(width)}  {'PASS' if result.passed else 'FAIL'}  {result.detail}")
    return "\n".join(lines)


def _result(name: str, problems: list[str], ok_detail: str, fatal: bool = False) -> ValidationResult:
    if problems:
        return ValidationResult(name, False, "; ".join(problems[:5]), fatal)
    return ValidationResult(name, True, ok_detail, fatal)


def _check_row_counts(tables) -> ValidationResult:
    problems = [
        f"{name} {len(tables[name])}행 (목표 {target})"
        for name, target in TABLE_ROW_TARGETS.items()
        if len(tables[name]) != target
    ]
    return _result("행수", problems, f"8개 테이블 총 {sum(len(v) for v in tables.values())}행")


def _check_orphan_keys(snapshot, tables) -> ValidationResult:
    known = {
        "lot_id": {l["lot_id"] for l in snapshot.lots},
        "product_code": {p["product_code"] for p in snapshot.products},
        "step_code": {s["step_code"] for s in snapshot.route},
        "material_code": {m["material_code"] for m in snapshot.materials},
        "eqp_id": {e["eqp_id"] for e in snapshot.equipment},
    }
    problems = []
    for table_name, rows in tables.items():
        for row in rows:
            for column, allowed in known.items():
                value = row.get(column)
                if value is not None and value not in allowed:
                    problems.append(f"{table_name}.{column}={value}")
    return _result("고아 키", problems, "MES 미존재 키 0건", fatal=True)


def _check_forbidden_columns(tables) -> ValidationResult:
    problems = []
    for table_name, rows in tables.items():
        if rows:
            for column in sorted(set(rows[0]) & FORBIDDEN_COLUMNS):
                problems.append(f"{table_name}.{column}")
    return _result("무중복 위반", problems, "MES 중복 컬럼 0건", fatal=True)


def _check_internal_references(tables) -> ValidationResult:
    inspection_ids = {r["inspection_id"] for r in tables["qms_inspection"]}
    spec_ids = {r["spec_id"] for r in tables["qms_inspection_spec"]}
    inspector_ids = {r["inspector_id"] for r in tables["qms_inspector"]}
    defect_ids = {r["defect_code"] for r in tables["qms_defect_code"]}
    ncr_ids = {r["ncr_id"] for r in tables["qms_nonconformance"]}
    iqc_ids = {r["iqc_id"] for r in tables["qms_incoming_inspection"]}

    references = [
        ("qms_inspection.inspector_id", [r["inspector_id"] for r in tables["qms_inspection"]], inspector_ids),
        ("qms_measurement.inspection_id", [r["inspection_id"] for r in tables["qms_measurement"]], inspection_ids),
        ("qms_measurement.spec_id", [r["spec_id"] for r in tables["qms_measurement"]], spec_ids),
        ("qms_measurement.measured_by", [r["measured_by"] for r in tables["qms_measurement"]], inspector_ids),
        ("qms_incoming_inspection.inspector_id", [r["inspector_id"] for r in tables["qms_incoming_inspection"]], inspector_ids),
        ("qms_incoming_inspection.defect_code", [r["defect_code"] for r in tables["qms_incoming_inspection"]], defect_ids),
        ("qms_nonconformance.inspection_id", [r["inspection_id"] for r in tables["qms_nonconformance"]], inspection_ids),
        ("qms_nonconformance.iqc_id", [r["iqc_id"] for r in tables["qms_nonconformance"]], iqc_ids),
        ("qms_nonconformance.defect_code", [r["defect_code"] for r in tables["qms_nonconformance"]], defect_ids),
        ("qms_disposition.ncr_id", [r["ncr_id"] for r in tables["qms_disposition"]], ncr_ids),
    ]
    problems = []
    for label, values, allowed in references:
        missing = {v for v in values if v is not None and v not in allowed}
        if missing:
            problems.append(f"{label} → {sorted(missing)[:3]}")
    return _result("내부 FK", problems, "내부 참조 전건 유효", fatal=True)


def _check_time_causality(snapshot, tables) -> ValidationResult:
    out_time = {r["id"]: dt.datetime.fromisoformat(r["out_time"]) for r in snapshot.process_results}
    inspection_by_id = {r["inspection_id"]: r for r in tables["qms_inspection"]}
    ncr_by_id = {r["ncr_id"]: r for r in tables["qms_nonconformance"]}
    problems = []

    for row in tables["qms_inspection"]:
        mes_id = row["mes_process_result_id"]
        if mes_id is not None and row["inspection_datetime"] < out_time[mes_id]:
            problems.append(f"{row['inspection_id']} 검사시각이 MES 종료시각보다 이르다")
    for row in tables["qms_nonconformance"]:
        inspection = inspection_by_id.get(row["inspection_id"])
        if inspection and row["detected_date"] < inspection["inspection_datetime"].date():
            problems.append(f"{row['ncr_id']} 검출일이 검사일보다 이르다")
    for row in tables["qms_disposition"]:
        if row["decision_date"] <= ncr_by_id[row["ncr_id"]]["detected_date"]:
            problems.append(f"{row['disposition_id']} 결정일이 검출일 이전이다")
    return _result("시간 인과", problems, "검사 → 부적합 → 처리 순서 성립")


def _check_quantities(tables) -> ValidationResult:
    affected = {r["ncr_id"]: r["affected_qty"] for r in tables["qms_nonconformance"]}
    problems = []
    for row in tables["qms_inspection"]:
        if row["defect_found_qty"] > row["sample_size"]:
            problems.append(f"{row['inspection_id']} 검출수 > 샘플수")
    for row in tables["qms_disposition"]:
        if row["disposition_qty"] > affected[row["ncr_id"]]:
            problems.append(f"{row['disposition_id']} 처리수량 > 영향수량")
    return _result("수량 정합", problems, "수량 대소관계 성립")


def _check_measurement_limits(tables) -> ValidationResult:
    problems = []
    for row in tables["qms_measurement"]:
        outside = row["measured_value"] < row["lsl"] or row["measured_value"] > row["usl"]
        if outside != row["is_out_of_spec"]:
            problems.append(f"{row['measurement_id']} 규격이탈 플래그 불일치")
        if row["judgment"] != ("NG" if outside else "OK"):
            problems.append(f"{row['measurement_id']} 판정 불일치")
    return _result("측정치 규격", problems, "규격 이탈 플래그 전건 일치")


def _check_devices(snapshot, tables) -> ValidationResult:
    counts = count_devices(snapshot, tables)
    problems = [
        f"{name} {counts[name]}건 (설계 {target}건)"
        for name, target in DEVICE_TARGETS.items()
        if counts[name] != target
    ]
    return _result("불일치 장치", problems, "5종 13건 전부 검출")


def count_devices(snapshot: MesSnapshot, tables: dict[str, list[dict]]) -> dict[str, int]:
    """전용 표시 컬럼 없이 값 조건만으로 장치를 세어 본다."""
    mes = {r["id"]: r for r in snapshot.process_results}
    inspection_by_id = {r["inspection_id"]: r for r in tables["qms_inspection"]}
    disposition_by_ncr = {r["ncr_id"]: r for r in tables["qms_disposition"]}
    lots = {l["lot_id"] for l in snapshot.lots}

    device1 = device3 = device2 = device4 = device5 = 0

    for row in tables["qms_inspection"]:
        mes_id = row["mes_process_result_id"]
        if mes_id is None:
            continue
        source = mes[mes_id]
        if source.get("defect_code"):
            continue
        if source["result"] == "Pass" and row["judgment"] == "불합격" and row["defect_found_qty"] == 0:
            device1 += 1
        if row["defect_found_qty"] > 0 and row["judgment"] != "불합격":
            device3 += 1

    for ncr in tables["qms_nonconformance"]:
        disposition = disposition_by_ncr[ncr["ncr_id"]]
        inspection = inspection_by_id.get(ncr["inspection_id"])
        if (
            inspection
            and inspection["mes_process_result_id"] is not None
            and mes[inspection["mes_process_result_id"]]["result"] == "Fail"
            and disposition["disposition_type"] == "특채"
        ):
            device2 += 1
        if ncr["ncr_source"] == "입고검사" and ncr["lot_id"] in lots:
            device4 += 1

    for row in tables["qms_disposition"]:
        if row["rework_result"] == "실패" and row["disposition_type"] == "폐기":
            device5 += 1

    return dict(zip(DEVICE_TARGETS, (device1, device2, device3, device4, device5)))
