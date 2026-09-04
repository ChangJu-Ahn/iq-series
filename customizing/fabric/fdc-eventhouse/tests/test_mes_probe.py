import pytest

from src.mes_probe import MES_BASE_URL, MesApiKeyMissing, MesProbe, parse_mcp_body


def test_derive_equipment_excludes_null_eqp(facts):
    eqp_ids = {e["eqp_id"] for e in facts.equipment}
    assert None not in eqp_ids
    assert len(facts.equipment) == 8


def test_derive_equipment_maps_type_from_route(facts):
    by_id = {e["eqp_id"]: e for e in facts.equipment}
    assert by_id["EQP-DIFF01"]["eqp_type"] == "Furnace"
    assert by_id["EQP-DIFF01"]["step_code"] == "DIFF"
    assert by_id["EQP-PHOT01"]["eqp_type"] == "Scanner"
    assert by_id["EQP-CMP01"]["eqp_type"] == "Polisher"


def test_every_equipment_has_a_type(facts):
    for eqp in facts.equipment:
        assert eqp["eqp_type"], eqp["eqp_id"]


def test_parse_mcp_body_handles_plain_json():
    assert parse_mcp_body('{"a": 1}') == {"a": 1}


def test_parse_mcp_body_handles_sse():
    raw = 'event: message\ndata: {"a":\ndata: 1}\n'
    assert parse_mcp_body(raw) == {"a": 1}


def test_parse_mcp_body_empty_returns_none():
    assert parse_mcp_body("   ") is None


def test_parse_mcp_body_rejects_garbage():
    with pytest.raises(ValueError):
        parse_mcp_body("not json at all")


def test_probe_requires_api_key():
    with pytest.raises(MesApiKeyMissing):
        MesProbe(MES_BASE_URL, "")


def test_probe_refuses_write_tools():
    probe = MesProbe(MES_BASE_URL, "dummy-key")
    with pytest.raises(ValueError, match="쓰기 툴"):
        probe.mcp_call("start_lot")


def test_facts_roundtrip(facts):
    again = type(facts).from_dict(facts.to_dict())
    assert again.equipment == facts.equipment
    assert len(again.process_results) == len(facts.process_results)
