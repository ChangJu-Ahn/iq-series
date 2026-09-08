"""Spark 테이블 스키마와 전체 조립.

pyspark 를 로컬에서 import 할 수 없으므로 스키마를 DDL 문자열로 표현한다.
문자열은 pytest 에서 검증할 수 있고, Fabric 에서는
spark.createDataFrame(rows, schema=TABLE_DDL[name]) 로 그대로 쓰인다.
전부 None 인 컬럼이 있어 타입 추론에 맡길 수 없다.
"""

from __future__ import annotations

from src.mes_client import MesSnapshot, mes_anchor
from src.qms_incoming import build_incoming_inspections
from src.qms_inspection import build_inspections
from src.qms_masters import build_defect_codes, build_inspection_specs, build_inspectors
from src.qms_measurement import build_measurements
from src.qms_nonconformance import build_nonconformances

TABLE_DDL = {
    "qms_defect_code": (
        "defect_code STRING, defect_name_ko STRING, defect_name_en STRING, "
        "mes_defect_code STRING, defect_category STRING, severity STRING, "
        "severity_score INT, typical_step_codes STRING, standard_cause_ko STRING, "
        "standard_action_ko STRING, is_active BOOLEAN"
    ),
    "qms_inspection_spec": (
        "spec_id STRING, product_code STRING, product_name STRING, step_code STRING, "
        "step_name STRING, characteristic_code STRING, characteristic_name_ko STRING, "
        "measurement_type STRING, unit STRING, target_value DOUBLE, lsl DOUBLE, "
        "usl DOUBLE, cpk_target DOUBLE, sampling_method STRING, sample_size INT, "
        "inspection_frequency STRING, control_method_ko STRING, spec_version STRING, "
        "effective_from DATE, is_active BOOLEAN"
    ),
    "qms_inspector": (
        "inspector_id STRING, inspector_name STRING, team_ko STRING, shift_code STRING, "
        "qualification_level STRING, certified_characteristics STRING, "
        "certified_from DATE, certified_until DATE, is_active BOOLEAN"
    ),
    "qms_inspection": (
        "inspection_id STRING, inspection_type STRING, lot_id STRING, product_code STRING, "
        "product_name STRING, step_code STRING, step_name STRING, eqp_id STRING, "
        "mes_process_result_id INT, inspector_id STRING, inspection_datetime TIMESTAMP, "
        "sample_size INT, inspected_wafer_qty INT, judgment STRING, judgment_basis_ko STRING, "
        "defect_found_qty INT, measurement_count INT, has_nonconformance BOOLEAN, remark_ko STRING"
    ),
    "qms_measurement": (
        "measurement_id STRING, inspection_id STRING, spec_id STRING, lot_id STRING, "
        "product_code STRING, step_code STRING, characteristic_code STRING, "
        "characteristic_name_ko STRING, sample_no INT, site_no INT, measured_value DOUBLE, "
        "unit STRING, target_value DOUBLE, lsl DOUBLE, usl DOUBLE, deviation_pct DOUBLE, "
        "is_out_of_spec BOOLEAN, judgment STRING, metrology_eqp_id STRING, "
        "measured_by STRING, measured_at TIMESTAMP"
    ),
    "qms_incoming_inspection": (
        "iqc_id STRING, material_code STRING, material_name STRING, supplier_code STRING, "
        "supplier_name_ko STRING, supplier_lot_no STRING, receipt_date DATE, "
        "received_qty DOUBLE, uom STRING, sample_size INT, inspection_items_ko STRING, "
        "judgment STRING, defect_code STRING, coa_received BOOLEAN, coa_conformance STRING, "
        "inspector_id STRING, inspection_date DATE, remark_ko STRING"
    ),
    "qms_nonconformance": (
        "ncr_id STRING, ncr_source STRING, inspection_id STRING, iqc_id STRING, "
        "lot_id STRING, product_code STRING, product_name STRING, step_code STRING, "
        "step_name STRING, material_code STRING, eqp_id STRING, defect_code STRING, "
        "mes_defect_code STRING, severity STRING, affected_qty INT, detected_date DATE, "
        "root_cause_category STRING, root_cause_ko STRING, immediate_action_ko STRING, "
        "owner_dept_ko STRING, owner_name STRING, status STRING, due_date DATE, "
        "closed_date DATE, estimated_cost_krw BIGINT"
    ),
    "qms_disposition": (
        "disposition_id STRING, ncr_id STRING, lot_id STRING, product_code STRING, "
        "step_code STRING, disposition_type STRING, disposition_qty INT, decision_date DATE, "
        "decision_body_ko STRING, approver_name STRING, approval_status STRING, "
        "rework_step_code STRING, rework_result STRING, scrap_cost_krw BIGINT, "
        "effectiveness_check_date DATE, effectiveness_result STRING, reason_ko STRING"
    ),
}


def ddl_columns(ddl: str) -> tuple[str, ...]:
    return tuple(part.strip().split()[0] for part in ddl.split(","))


TABLE_COLUMNS = {name: ddl_columns(ddl) for name, ddl in TABLE_DDL.items()}


def build_all_tables(snapshot: MesSnapshot) -> dict[str, list[dict]]:
    """8개 테이블 전체를 만든다. 노트북과 테스트가 공유하는 단일 진입점이다."""
    inspectors = build_inspectors(snapshot)
    defect_codes = build_defect_codes()
    specs = build_inspection_specs(snapshot)
    inspections = build_inspections(snapshot, inspectors)
    measurements = build_measurements(inspections, specs, mes_anchor(snapshot))
    incoming = build_incoming_inspections(snapshot, inspectors, defect_codes)
    ncrs, dispositions = build_nonconformances(snapshot, inspections, incoming, defect_codes)
    return {
        "qms_defect_code": defect_codes,
        "qms_inspection_spec": specs,
        "qms_inspector": inspectors,
        "qms_inspection": inspections,
        "qms_measurement": measurements,
        "qms_incoming_inspection": incoming,
        "qms_nonconformance": ncrs,
        "qms_disposition": dispositions,
    }


def to_rows(table_name: str, rows: list[dict]) -> list[tuple]:
    """dict 를 컬럼 순서 tuple 로 바꾼다.

    spark.createDataFrame 에 dict 를 넘기면 키 순서 경고와 함께 스키마가
    알파벳순으로 재정렬된다. tuple 로 넘기면 DDL 순서가 그대로 지켜진다.
    """
    columns = TABLE_COLUMNS[table_name]
    return [tuple(row[column] for column in columns) for row in rows]
