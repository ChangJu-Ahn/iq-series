"""Fabric 온톨로지 정의 패키지 생성기.

Fabric 온톨로지는 하나의 JSON 덩어리가 아니라 base64 로 인코딩된 파일 여러 개를
parts 배열로 올린다. 경로가 곧 구조다.

    definition.json                                        (빈 객체, 필수)
    .platform                                              (아이템 메타데이터, 필수)
    EntityTypes/{엔티티ID}/definition.json                  엔티티 타입과 속성
    EntityTypes/{엔티티ID}/DataBindings/{GUID}.json         레이크하우스 테이블 연결
    RelationshipTypes/{관계ID}/definition.json              관계 타입
    RelationshipTypes/{관계ID}/Contextualizations/{GUID}.json  관계를 만드는 컬럼 쌍

컬럼 목록은 qms_schema.TABLE_DDL 에서 얻는다. 레이크하우스를 읽지 않으므로
테이블이 아직 없어도 패키지를 만들 수 있고, 오프라인 테스트가 가능하다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

from src.qms_schema import TABLE_DDL

# 온톨로지가 허용하는 이름 규칙. 엔티티·속성 공통이다.
NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")

# Spark DDL 타입 → 온톨로지 valueType.
# 온톨로지가 아는 값은 String, Boolean, DateTime, Object, BigInt, Double 뿐이다.
DDL_TO_VALUE_TYPE = {
    "STRING": "String",
    "INT": "BigInt",
    "BIGINT": "BigInt",
    "DOUBLE": "Double",
    "BOOLEAN": "Boolean",
    "DATE": "DateTime",
    "TIMESTAMP": "DateTime",
}

# 테이블 하나가 엔티티 하나가 된다. key 는 엔티티 식별자, display 는 UI 표시 이름이다.
ENTITY_CONFIG = {
    "qms_defect_code": {"entity": "DefectCode", "key": "defect_code", "display": "defect_name_ko"},
    "qms_inspection_spec": {"entity": "InspectionSpec", "key": "spec_id", "display": "characteristic_name_ko"},
    "qms_inspector": {"entity": "Inspector", "key": "inspector_id", "display": "inspector_name"},
    "qms_inspection": {"entity": "Inspection", "key": "inspection_id", "display": "inspection_id"},
    "qms_measurement": {"entity": "Measurement", "key": "measurement_id", "display": "measurement_id"},
    "qms_incoming_inspection": {"entity": "IncomingInspection", "key": "iqc_id", "display": "material_name"},
    "qms_nonconformance": {"entity": "NonConformance", "key": "ncr_id", "display": "ncr_id"},
    "qms_disposition": {"entity": "Disposition", "key": "disposition_id", "display": "disposition_type"},
}

# QMS 내부 참조 10건. qms_validate._check_internal_references 가 검증하는 것과 같은 목록이므로
# 여기 있는 관계는 데이터에 고아가 없음이 보장된다.
# table 은 두 키가 같은 행에 함께 있는 테이블이다. 관계는 그 행에서 생긴다.
RELATIONSHIPS = [
    {"name": "inspectionPerformedBy", "table": "qms_inspection",
     "source": "Inspection", "target": "Inspector", "target_column": "inspector_id"},
    {"name": "measurementTakenDuring", "table": "qms_measurement",
     "source": "Measurement", "target": "Inspection", "target_column": "inspection_id"},
    {"name": "measurementEvaluatedAgainst", "table": "qms_measurement",
     "source": "Measurement", "target": "InspectionSpec", "target_column": "spec_id"},
    {"name": "measurementTakenBy", "table": "qms_measurement",
     "source": "Measurement", "target": "Inspector", "target_column": "measured_by"},
    {"name": "incomingInspectionPerformedBy", "table": "qms_incoming_inspection",
     "source": "IncomingInspection", "target": "Inspector", "target_column": "inspector_id"},
    {"name": "incomingInspectionFoundDefect", "table": "qms_incoming_inspection",
     "source": "IncomingInspection", "target": "DefectCode", "target_column": "defect_code"},
    {"name": "nonConformanceRaisedFromInspection", "table": "qms_nonconformance",
     "source": "NonConformance", "target": "Inspection", "target_column": "inspection_id"},
    {"name": "nonConformanceRaisedFromIncoming", "table": "qms_nonconformance",
     "source": "NonConformance", "target": "IncomingInspection", "target_column": "iqc_id"},
    {"name": "nonConformanceClassifiedAs", "table": "qms_nonconformance",
     "source": "NonConformance", "target": "DefectCode", "target_column": "defect_code"},
    {"name": "dispositionResolves", "table": "qms_disposition",
     "source": "Disposition", "target": "NonConformance", "target_column": "ncr_id"},
]


def parse_ddl(ddl: str) -> list[tuple[str, str]]:
    """DDL 문자열에서 (컬럼명, valueType) 목록을 뽑는다."""
    columns = []
    for chunk in ddl.split(","):
        name, _, ddl_type = chunk.strip().partition(" ")
        value_type = DDL_TO_VALUE_TYPE.get(ddl_type.strip().upper())
        if value_type is None:
            raise ValueError(f"온톨로지가 모르는 타입입니다: {name} {ddl_type}")
        columns.append((name, value_type))
    return columns


def stable_id(*parts: str) -> str:
    """이름에서 결정론적 64비트 양의 정수 ID를 만든다.

    난수를 쓰면 다시 만들 때마다 ID가 바뀌어 이전 패키지와 대조할 수 없다.
    최상위 비트를 세워 항상 큰 양수가 되게 한다.
    """
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return str(int(digest[:15], 16) | (1 << 59))


def _uuid_from(*parts: str) -> str:
    """GUID 자리에 넣을 결정론적 UUID. 데이터바인딩·컨텍스트화 식별자로 쓴다."""
    h = hashlib.sha256(("uuid:" + ":".join(parts)).encode("utf-8")).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _part(path: str, obj: dict) -> dict:
    payload = base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return {"path": path, "payload": payload, "payloadType": "InlineBase64"}


def build_ontology_parts(
    workspace_id: str,
    lakehouse_id: str,
    display_name: str,
    table_ddl: dict[str, str] | None = None,
    source_schema: str = "",
) -> list[dict]:
    """온톨로지 정의 parts 배열을 만든다.

    workspace_id 와 lakehouse_id 는 데이터바인딩이 가리킬 레이크하우스다.
    source_schema 는 스키마 사용 레이크하우스일 때만 채운다.
    """
    table_ddl = TABLE_DDL if table_ddl is None else table_ddl

    entity_ids: dict[str, str] = {}
    key_property_ids: dict[str, str] = {}
    property_ids: dict[tuple[str, str], str] = {}

    parts = [
        _part("definition.json", {}),
        _part(".platform", {"metadata": {"type": "Ontology", "displayName": display_name}}),
    ]

    def source_table(name: str) -> dict:
        table = {
            "sourceType": "LakehouseTable",
            "workspaceId": workspace_id,
            "itemId": lakehouse_id,
            "sourceTableName": name,
        }
        if source_schema:
            table["sourceSchema"] = source_schema
        return table

    for table_name, cfg in ENTITY_CONFIG.items():
        entity = cfg["entity"]
        if not NAME_PATTERN.match(entity):
            raise ValueError(f"엔티티 이름 규칙 위반: {entity}")

        entity_id = stable_id("entity", entity)
        entity_ids[entity] = entity_id

        properties = []
        for column, value_type in parse_ddl(table_ddl[table_name]):
            if not NAME_PATTERN.match(column):
                raise ValueError(f"속성 이름 규칙 위반: {table_name}.{column}")
            prop_id = stable_id("property", entity, column)
            property_ids[(entity, column)] = prop_id
            properties.append({
                "id": prop_id,
                "name": column,
                "redefines": None,
                "baseTypeNamespaceType": None,
                "valueType": value_type,
            })

        key_property_ids[entity] = property_ids[(entity, cfg["key"])]

        parts.append(_part(f"EntityTypes/{entity_id}/definition.json", {
            "id": entity_id,
            "namespace": "usertypes",
            "baseEntityTypeId": None,
            "name": entity,
            "entityIdParts": [key_property_ids[entity]],
            "displayNamePropertyId": property_ids[(entity, cfg["display"])],
            "namespaceType": "Custom",
            "visibility": "Visible",
            "properties": properties,
            "timeseriesProperties": [],
        }))

        binding_id = _uuid_from("binding", entity, table_name)
        parts.append(_part(f"EntityTypes/{entity_id}/DataBindings/{binding_id}.json", {
            "id": binding_id,
            "dataBindingConfiguration": {
                "dataBindingType": "NonTimeSeries",
                "propertyBindings": [
                    {"sourceColumnName": name, "targetPropertyId": property_ids[(entity, name)]}
                    for name, _ in parse_ddl(table_ddl[table_name])
                ],
                "sourceTableProperties": source_table(table_name),
            },
        }))

    for rel in RELATIONSHIPS:
        name = rel["name"]
        if not NAME_PATTERN.match(name):
            raise ValueError(f"관계 이름 규칙 위반: {name}")
        source, target = rel["source"], rel["target"]
        rel_id = stable_id("relationship", name)

        parts.append(_part(f"RelationshipTypes/{rel_id}/definition.json", {
            "namespace": "usertypes",
            "id": rel_id,
            "name": name,
            "namespaceType": "Custom",
            "source": {"entityTypeId": entity_ids[source]},
            "target": {"entityTypeId": entity_ids[target]},
        }))

        ctx_id = _uuid_from("contextualization", name)
        source_key_column = ENTITY_CONFIG[rel["table"]]["key"]
        parts.append(_part(f"RelationshipTypes/{rel_id}/Contextualizations/{ctx_id}.json", {
            "id": ctx_id,
            "dataBindingTable": source_table(rel["table"]),
            "sourceKeyRefBindings": [
                {"sourceColumnName": source_key_column, "targetPropertyId": key_property_ids[source]}
            ],
            "targetKeyRefBindings": [
                {"sourceColumnName": rel["target_column"], "targetPropertyId": key_property_ids[target]}
            ],
        }))

    ids = [p["path"] for p in parts]
    if len(set(ids)) != len(ids):
        raise ValueError("정의 파트 경로가 중복됩니다.")
    return parts


def decode_part(part: dict) -> dict:
    """테스트와 진단용. base64 파트를 다시 dict 로 되돌린다."""
    return json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
