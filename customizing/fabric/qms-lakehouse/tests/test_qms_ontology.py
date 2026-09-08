"""온톨로지 패키지 검증.

Fabric 에 올리기 전에 오프라인에서 잡을 수 있는 것은 전부 여기서 잡는다.
업로드 실패는 원인 메시지가 빈약해서 디버깅 비용이 크다.
"""

from __future__ import annotations

import base64
import json

import pytest

from src.qms_ontology import (
    DDL_TO_VALUE_TYPE,
    ENTITY_CONFIG,
    NAME_PATTERN,
    RELATIONSHIPS,
    build_ontology_parts,
    decode_part,
    parse_ddl,
    stable_id,
)
from src.qms_schema import TABLE_COLUMNS, TABLE_DDL

WORKSPACE = "11111111-1111-1111-1111-111111111111"
LAKEHOUSE = "22222222-2222-2222-2222-222222222222"

ALLOWED_VALUE_TYPES = {"String", "Boolean", "DateTime", "Object", "BigInt", "Double"}


@pytest.fixture
def parts():
    return build_ontology_parts(WORKSPACE, LAKEHOUSE, "QMS_Ontology")


def by_path(parts, suffix):
    return [p for p in parts if p["path"].endswith(suffix)]


def test_every_qms_table_becomes_an_entity():
    assert set(ENTITY_CONFIG) == set(TABLE_DDL)


def test_required_parts_are_present(parts):
    paths = {p["path"] for p in parts}
    assert "definition.json" in paths
    assert ".platform" in paths
    assert decode_part(next(p for p in parts if p["path"] == "definition.json")) == {}


def test_part_counts_match_the_design(parts):
    # 필수 2 + 엔티티당 (정의 + 바인딩) + 관계당 (정의 + 컨텍스트화)
    assert len(parts) == 2 + len(ENTITY_CONFIG) * 2 + len(RELATIONSHIPS) * 2


def test_every_payload_is_valid_base64_json(parts):
    for part in parts:
        assert part["payloadType"] == "InlineBase64"
        json.loads(base64.b64decode(part["payload"]).decode("utf-8"))


def test_entity_definitions_carry_every_column_as_a_property(parts):
    for table_name, cfg in ENTITY_CONFIG.items():
        entity_id = stable_id("entity", cfg["entity"])
        body = decode_part(
            next(p for p in parts if p["path"] == f"EntityTypes/{entity_id}/definition.json")
        )
        assert body["name"] == cfg["entity"]
        assert body["namespace"] == "usertypes"
        assert body["namespaceType"] == "Custom"
        names = [prop["name"] for prop in body["properties"]]
        assert names == list(TABLE_COLUMNS[table_name])


def test_key_and_display_properties_point_at_real_properties(parts):
    for cfg in ENTITY_CONFIG.values():
        entity_id = stable_id("entity", cfg["entity"])
        body = decode_part(
            next(p for p in parts if p["path"] == f"EntityTypes/{entity_id}/definition.json")
        )
        ids = {prop["id"]: prop["name"] for prop in body["properties"]}
        assert len(body["entityIdParts"]) == 1
        assert ids[body["entityIdParts"][0]] == cfg["key"]
        assert ids[body["displayNamePropertyId"]] == cfg["display"]


def test_all_value_types_are_allowed(parts):
    for part in by_path(parts, "/definition.json"):
        body = decode_part(part)
        for prop in body.get("properties", []):
            assert prop["valueType"] in ALLOWED_VALUE_TYPES


def test_ddl_type_map_covers_every_type_actually_used():
    used = {
        chunk.strip().partition(" ")[2].strip().upper()
        for ddl in TABLE_DDL.values()
        for chunk in ddl.split(",")
    }
    assert used <= set(DDL_TO_VALUE_TYPE)


def test_names_follow_the_ontology_regex():
    for cfg in ENTITY_CONFIG.values():
        assert NAME_PATTERN.match(cfg["entity"])
    for rel in RELATIONSHIPS:
        assert NAME_PATTERN.match(rel["name"])
    for columns in TABLE_COLUMNS.values():
        for column in columns:
            assert NAME_PATTERN.match(column)


def test_all_ids_are_unique_and_positive(parts):
    seen = set()
    for part in parts:
        body = decode_part(part)
        for prop in body.get("properties", []):
            assert prop["id"] not in seen
            seen.add(prop["id"])
        if "id" in body and isinstance(body["id"], str) and body["id"].isdigit():
            assert 0 < int(body["id"]) < 2**63


def test_data_bindings_map_every_column_and_target_the_lakehouse(parts):
    bindings = [p for p in parts if "/DataBindings/" in p["path"]]
    assert len(bindings) == len(ENTITY_CONFIG)
    bound_tables = set()
    for part in bindings:
        cfg = decode_part(part)["dataBindingConfiguration"]
        assert cfg["dataBindingType"] == "NonTimeSeries"
        source = cfg["sourceTableProperties"]
        assert source["sourceType"] == "LakehouseTable"
        assert source["workspaceId"] == WORKSPACE
        assert source["itemId"] == LAKEHOUSE
        table = source["sourceTableName"]
        bound_tables.add(table)
        columns = [b["sourceColumnName"] for b in cfg["propertyBindings"]]
        assert columns == list(TABLE_COLUMNS[table])
    assert bound_tables == set(TABLE_DDL)


def test_source_schema_is_omitted_unless_requested():
    without = build_ontology_parts(WORKSPACE, LAKEHOUSE, "QMS_Ontology")
    binding = decode_part(next(p for p in without if "/DataBindings/" in p["path"]))
    assert "sourceSchema" not in binding["dataBindingConfiguration"]["sourceTableProperties"]

    with_schema = build_ontology_parts(WORKSPACE, LAKEHOUSE, "QMS_Ontology", source_schema="dbo")
    binding = decode_part(next(p for p in with_schema if "/DataBindings/" in p["path"]))
    assert binding["dataBindingConfiguration"]["sourceTableProperties"]["sourceSchema"] == "dbo"


def test_relationships_connect_declared_entities(parts):
    entity_ids = {
        stable_id("entity", cfg["entity"]): cfg["entity"] for cfg in ENTITY_CONFIG.values()
    }
    rel_parts = [
        p for p in parts if p["path"].startswith("RelationshipTypes/") and p["path"].endswith("/definition.json")
    ]
    assert len(rel_parts) == len(RELATIONSHIPS)
    for part in rel_parts:
        body = decode_part(part)
        assert entity_ids[body["source"]["entityTypeId"]]
        assert entity_ids[body["target"]["entityTypeId"]]


def test_contextualization_columns_exist_in_the_binding_table(parts):
    """관계를 만드는 두 컬럼이 실제로 그 테이블에 있어야 한다."""
    for part in [p for p in parts if "/Contextualizations/" in p["path"]]:
        body = decode_part(part)
        table = body["dataBindingTable"]["sourceTableName"]
        columns = set(TABLE_COLUMNS[table])
        for binding in body["sourceKeyRefBindings"] + body["targetKeyRefBindings"]:
            assert binding["sourceColumnName"] in columns


def test_contextualization_binds_keys_to_the_right_entity_keys(parts):
    """소스 키는 그 테이블 엔티티의 키여야 하고, 타깃 키는 상대 엔티티의 키여야 한다."""
    key_prop = {
        cfg["entity"]: stable_id("property", cfg["entity"], cfg["key"])
        for cfg in ENTITY_CONFIG.values()
    }
    for rel in RELATIONSHIPS:
        prefix = f"RelationshipTypes/{stable_id('relationship', rel['name'])}/Contextualizations/"
        ctx = decode_part(next(p for p in parts if p["path"].startswith(prefix)))
        assert ctx["dataBindingTable"]["sourceTableName"] == rel["table"]
        assert ctx["targetKeyRefBindings"][0]["sourceColumnName"] == rel["target_column"]
        assert ctx["sourceKeyRefBindings"][0]["sourceColumnName"] == ENTITY_CONFIG[rel["table"]]["key"]
        assert ctx["sourceKeyRefBindings"][0]["targetPropertyId"] == key_prop[rel["source"]]
        assert ctx["targetKeyRefBindings"][0]["targetPropertyId"] == key_prop[rel["target"]]


def test_relationship_source_entity_owns_the_binding_table():
    for rel in RELATIONSHIPS:
        assert ENTITY_CONFIG[rel["table"]]["entity"] == rel["source"]


def test_package_is_deterministic():
    first = build_ontology_parts(WORKSPACE, LAKEHOUSE, "QMS_Ontology")
    second = build_ontology_parts(WORKSPACE, LAKEHOUSE, "QMS_Ontology")
    assert first == second


def test_relationships_materialize_against_the_generated_data(tables):
    """관계마다 타깃 값이 실제로 타깃 엔티티에 존재해야 한다.

    고아가 있으면 온톨로지는 조용히 관계가 비어 있는 채로 만들어진다.
    업로드는 성공하는데 그래프가 끊겨 있는 상태가 가장 찾기 어렵다.
    """
    key_values = {
        cfg["entity"]: {row[cfg["key"]] for row in tables[table]}
        for table, cfg in ENTITY_CONFIG.items()
    }
    for rel in RELATIONSHIPS:
        values = {
            row[rel["target_column"]]
            for row in tables[rel["table"]]
            if row[rel["target_column"]] is not None
        }
        assert values, f"{rel['name']} 은 연결되는 행이 하나도 없습니다"
        missing = values - key_values[rel["target"]]
        assert not missing, f"{rel['name']} 고아: {sorted(missing)[:3]}"


def test_parse_ddl_rejects_a_type_the_ontology_cannot_express():
    with pytest.raises(ValueError, match="모르는 타입"):
        parse_ddl("col_a STRING, col_b BINARY")
