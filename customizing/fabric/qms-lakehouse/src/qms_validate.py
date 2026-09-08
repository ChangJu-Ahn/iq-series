"""스펙 8절 검증 8항목.

장치 검출 질의는 데이터 값만으로 성립해야 한다. 전용 플래그 컬럼을 두면
검증은 쉬워지지만 '두 시스템을 함께 봐야 답이 나온다'는 전제가 무너진다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from src.mes_client import MesSnapshot, mes_anchor, parse_mes_time
from src.qms_schema import TABLE_DDL

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
        _check_timestamp_timezones(tables),
        _check_time_column_coverage(tables),
        _check_time_causality(snapshot, tables),
        _check_no_future_completions(snapshot, tables),
        _check_inspector_certification(snapshot, tables),
        _check_quantities(tables),
        _check_measurement_limits(tables),
        _check_denormalized_specs(tables),
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
        "mes_process_result_id": {r["id"] for r in snapshot.process_results},
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
        present = set().union(*(row.keys() for row in rows)) if rows else set()
        for column in sorted(present & FORBIDDEN_COLUMNS):
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


def _check_timestamp_timezones(tables) -> ValidationResult:
    """모든 시각 값이 tz-aware 인지 본다.

    PySpark 는 tz-aware 면 calendar.timegm 을, naive 면 time.mktime(로컬 타임존)을
    탄다. 한 컬럼에 둘이 섞이면 드라이버가 UTC 가 아닌 곳에서 일부 행만 밀리는데,
    적재는 성공하고 값만 틀리므로 눈으로는 찾을 수 없다. 그래서 치명 항목이다.
    dt.date 는 dt.datetime 의 인스턴스가 아니므로 날짜 컬럼은 걸리지 않는다.
    """
    naive: dict[str, int] = {}
    for name, rows in tables.items():
        for row in rows:
            for column, value in row.items():
                if isinstance(value, dt.datetime) and value.tzinfo is None:
                    key = f"{name}.{column}"
                    naive[key] = naive.get(key, 0) + 1
    problems = [f"{key} naive {count}건" for key, count in sorted(naive.items())]
    return _result("타임존 통일", problems, "모든 시각이 tz-aware UTC", fatal=True)


def _check_time_causality(snapshot, tables) -> ValidationResult:
    out_time = {r["id"]: parse_mes_time(r["out_time"]) for r in snapshot.process_results}
    inspection_by_id = {r["inspection_id"]: r for r in tables["qms_inspection"]}
    ncr_by_id = {r["ncr_id"]: r for r in tables["qms_nonconformance"]}
    problems = []

    # QMS 시각이 MES 구간에서 떨어져 나가지 않았는지 본다. 벽시계 상수가 다시
    # 들어오면 MES 재배포 때 여기부터 어긋나므로, 창을 앵커 기준으로 잡는다.
    window_start = min(out_time.values())
    window_end = max(out_time.values()) + dt.timedelta(days=4)

    for row in tables["qms_inspection"]:
        mes_id = row["mes_process_result_id"]
        mes_out = out_time.get(mes_id) if mes_id is not None else None
        when = row["inspection_datetime"]
        if mes_out is not None and when < mes_out:
            problems.append(f"{row['inspection_id']} 검사시각이 MES 종료시각보다 이르다")
        if not window_start <= when <= window_end:
            problems.append(f"{row['inspection_id']} 검사시각이 MES 구간 밖이다 ({when})")
    for row in tables["qms_nonconformance"]:
        inspection = inspection_by_id.get(row["inspection_id"])
        if inspection and row["detected_date"] < inspection["inspection_datetime"].date():
            problems.append(f"{row['ncr_id']} 검출일이 검사일보다 이르다")
    for row in tables["qms_disposition"]:
        if row["decision_date"] < ncr_by_id[row["ncr_id"]]["detected_date"]:
            problems.append(f"{row['disposition_id']} 결정일이 검출일 이전이다")
    return _result("시간 인과", problems, "검사 → 부적합 → 처리 순서 성립")


# 완료를 뜻하는 컬럼. 앵커(데이터의 현재)를 넘으면 아직 오지 않은 날짜에
# 끝난 사건이 된다.
_COMPLETED_COLUMNS = [
    ("qms_inspection_spec", "effective_from"),
    ("qms_inspector", "certified_from"),
    ("qms_inspection", "inspection_datetime"),
    ("qms_measurement", "measured_at"),
    ("qms_incoming_inspection", "receipt_date"),
    ("qms_incoming_inspection", "inspection_date"),
    ("qms_nonconformance", "detected_date"),
    ("qms_nonconformance", "closed_date"),
    ("qms_disposition", "decision_date"),
]

# 아직 오지 않은 일. 미래에 있는 것이 정상이라 상한을 걸지 않는다.
# 여기까지 과거로 끌어내리면 "기한이 임박한 미결 부적합" 같은 질문이 죽는다.
_PLANNED_COLUMNS = [
    ("qms_inspector", "certified_until"),
    ("qms_nonconformance", "due_date"),
    ("qms_disposition", "effectiveness_check_date"),
]


def _declared_time_columns() -> set[tuple[str, str]]:
    """DDL 이 선언한 DATE·TIMESTAMP 컬럼 전부.

    분류 목록을 손으로 적으면 새 컬럼이 조용히 빠진다. 컬럼 이름에 date 나
    time 이 들어가는지로 거르는 방식도 같은 함정이다. measured_at 은 둘 다
    없어서 그런 필터에 걸리지 않는다. 그래서 선언된 타입에서 뽑는다.
    """
    columns = set()
    for table, ddl in TABLE_DDL.items():
        for field in ddl.split(","):
            parts = field.strip().rsplit(" ", 1)
            if len(parts) == 2 and parts[1] in ("DATE", "TIMESTAMP"):
                columns.add((table, parts[0]))
    return columns


def _check_time_column_coverage(tables) -> ValidationResult:
    """모든 시각 컬럼이 완료·예정 중 하나로 분류됐는지.

    분류에서 빠진 컬럼은 미래 검사를 그냥 통과한다. 검증이 늘 PASS 라
    안전해 보이지만 실제로는 그 컬럼을 아무도 보고 있지 않다.
    """
    classified = {c for c in _COMPLETED_COLUMNS} | {c for c in _PLANNED_COLUMNS}
    declared = _declared_time_columns()
    problems = [
        f"{table}.{column} 이 완료·예정 어느 쪽으로도 분류되지 않았다"
        for table, column in sorted(declared - classified)
    ]
    problems += [
        f"{table}.{column} 은 DDL 에 없는 컬럼이다"
        for table, column in sorted(classified - declared)
    ]
    return _result(
        "시각 컬럼 분류", problems, f"DATE·TIMESTAMP {len(declared)}개 전부 분류됨", fatal=True
    )


def _check_no_future_completions(snapshot, tables) -> ValidationResult:
    """이미 끝난 사건이 현재를 넘지 않는지.

    앵커는 MES 배포 시각이라 실습 시점의 "지금"과 같다. 판정이 채워진 검사나
    종결된 부적합이 앵커를 넘으면, 참가자가 "최근 검사 결과"를 물었을 때 아직
    오지 않은 날짜의 합격 판정이 돌아온다. 적재는 성공하므로 데이터를 직접
    들여다보기 전에는 드러나지 않는다.
    """
    anchor = mes_anchor(snapshot)
    limit = anchor.date()
    problems = []
    for table, column in _COMPLETED_COLUMNS:
        for row in tables[table]:
            value = row.get(column)
            if value is None:
                continue
            # date 와 datetime 은 서로 비교할 수 없다. 컬럼 타입에 맞춰 자른다.
            ceiling = anchor if isinstance(value, dt.datetime) else limit
            if value > ceiling:
                problems.append(f"{table}.{column} {value} 가 현재({ceiling})를 넘는다")
    return _result(
        "미래 완료 사건",
        problems,
        f"완료 컬럼 {len(_COMPLETED_COLUMNS)}종 전부 앵커({anchor:%Y-%m-%d %H:%M}) 이하",
        fatal=True,
    )


def _check_inspector_certification(snapshot, tables) -> ValidationResult:
    """검사를 수행한 사람의 자격이 그 시점에 유효했는지.

    자격이 만료된 검사원의 기록은 실제 QMS 에서 그 자체로 중대 부적합이다.
    이 데이터에서는 의도한 장치가 아니므로 한 건도 없어야 한다.
    """
    inspectors = {r["inspector_id"]: r for r in tables["qms_inspector"]}
    problems = []
    for row in tables["qms_inspection"]:
        person = inspectors.get(row["inspector_id"])
        if person is None:
            continue
        when = row["inspection_datetime"].date()
        if not (person["certified_from"] <= when <= person["certified_until"]):
            problems.append(
                f"{row['inspection_id']} 를 자격 범위 밖의 {row['inspector_id']} 가 수행했다"
            )
    detail = f"검사 {len(tables['qms_inspection'])}건 전부 유효 자격자가 수행"
    return _result("검사원 자격", problems, detail, fatal=True)


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


_DENORMALIZED_SPEC_FIELDS = ("unit", "target_value", "lsl", "usl")


def _check_denormalized_specs(tables) -> ValidationResult:
    """계측이 들고 있는 규격이 그 spec_id 의 것과 같은지 본다.

    qms_measurement 는 규격 네 값을 자기 행에 복제해 둔다. 덕분에 규격 이탈
    판정에 조인이 필요 없고, 참가자가 characteristic_code 로 잘못 조인할 이유도
    없어진다. 그 복제가 어긋나면 문서가 "조인하지 마세요"라고 안내하는 근거가
    사라지므로 치명으로 둔다.

    characteristic_code 는 여섯 종뿐이라 스펙 108행에서 유일하지 않다. CD 하나에
    32행이 걸리고 제품마다 목표가 4배까지 다르다. 그 조인은 22.65배로 늘어나며
    짝의 95.6%가 다른 제품의 규격이다.
    """
    specs = {s["spec_id"]: s for s in tables["qms_inspection_spec"]}
    drift = []
    for row in tables["qms_measurement"]:
        spec = specs.get(row["spec_id"])
        if spec is None:
            drift.append(f"{row['measurement_id']}: spec_id 없음")
            continue
        for field in _DENORMALIZED_SPEC_FIELDS:
            if row[field] != spec[field]:
                drift.append(f"{row['measurement_id']}.{field} {row[field]} != {spec[field]}")
    return ValidationResult(
        "계측 규격 복제",
        not drift,
        f"규격 4값 복제 {len(tables['qms_measurement'])}건 전부 일치"
        if not drift
        else f"복제 불일치 {len(drift)}건: {drift[:3]}",
        fatal=True,
    )


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
        source = mes.get(mes_id)
        if source is None or source.get("defect_code"):
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
