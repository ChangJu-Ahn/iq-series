import os

import pytest

from src.mes_client import (
    MES_BASE_URL,
    MesApiKeyMissing,
    MesClient,
    MesSnapshot,
    derive_equipment,
    parse_mcp_body,
)


def test_parse_plain_json_body():
    assert parse_mcp_body('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}') == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }


def test_parse_sse_body_starting_with_event_line():
    # 실제 mock-mes 응답 형태. 'event: message'가 먼저 오고 CRLF로 끝난다.
    raw = 'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\r\n\r\n'
    assert parse_mcp_body(raw) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_parse_multiline_sse_data_is_concatenated():
    raw = 'event: message\r\ndata: {"a":\r\ndata: 1}\r\n\r\n'
    assert parse_mcp_body(raw) == {"a": 1}


def test_parse_empty_body_returns_none():
    # notifications/initialized 는 본문 없는 202를 돌려준다.
    assert parse_mcp_body("") is None
    assert parse_mcp_body("   \r\n") is None




def test_derive_equipment_from_distinct_eqp_ids():
    process_results = [
        {"eqp_id": "EQP-DIFF01", "step_code": "DIFF"},
        {"eqp_id": "EQP-DIFF01", "step_code": "DIFF"},
        {"eqp_id": "EQP-PHOT02", "step_code": "PHOTO"},
    ]
    route = [
        {"step_code": "DIFF", "eqp_type": "Furnace"},
        {"step_code": "PHOTO", "eqp_type": "Scanner"},
        {"step_code": "PKG", "eqp_type": "Bonder"},
    ]
    assert derive_equipment(process_results, route) == [
        {"eqp_id": "EQP-DIFF01", "eqp_type": "Furnace", "step_code": "DIFF"},
        {"eqp_id": "EQP-PHOT02", "eqp_type": "Scanner", "step_code": "PHOTO"},
    ]


def test_derive_equipment_skips_rows_without_eqp_id():
    process_results = [{"eqp_id": None, "step_code": "DIFF"}, {"step_code": "ETCH"}]
    route = [{"step_code": "DIFF", "eqp_type": "Furnace"}]
    assert derive_equipment(process_results, route) == []


def test_derive_equipment_is_sorted_by_eqp_id():
    process_results = [
        {"eqp_id": "EQP-TEST01", "step_code": "TEST"},
        {"eqp_id": "EQP-CMP01", "step_code": "CMP"},
    ]
    route = [
        {"step_code": "TEST", "eqp_type": "Prober"},
        {"step_code": "CMP", "eqp_type": "Polisher"},
    ]
    assert [e["eqp_id"] for e in derive_equipment(process_results, route)] == [
        "EQP-CMP01",
        "EQP-TEST01",
    ]




def test_client_requires_api_key():
    with pytest.raises(MesApiKeyMissing):
        MesClient(api_key="")


@pytest.mark.parametrize("tool", ["start_lot", "register_process_result"])
def test_write_tools_are_rejected(tool):
    client = MesClient(api_key="dummy")
    with pytest.raises(ValueError, match="쓰기 툴 호출 금지"):
        client.mcp_call(tool, {})


def test_snapshot_roundtrips_through_dict():
    snapshot = MesSnapshot(products=[{"product_code": "DDR5"}])
    assert MesSnapshot.from_dict(snapshot.to_dict()) == snapshot





@pytest.mark.live
def test_live_snapshot_matches_known_mes_state():
    api_key = os.environ.get("MES_API_KEY")
    assert api_key, "MES_API_KEY 환경변수가 필요합니다."
    snapshot = MesClient(MES_BASE_URL, api_key).fetch_snapshot()
    assert len(snapshot.products) == 4
    assert len(snapshot.materials) == 12
    assert len(snapshot.bom) == 48
    assert len(snapshot.lots) == 16
    assert len(snapshot.process_results) == 91
    assert len(snapshot.route) == 9
    assert len(snapshot.equipment) == 8
    assert sum(1 for r in snapshot.process_results if r["result"] == "Fail") == 5
    assert sum(1 for r in snapshot.process_results if r["result"] == "Rework") == 2


def test_fixture_matches_live_mes_state(snapshot):
    assert len(snapshot.products) == 4
    assert len(snapshot.lots) == 16
    assert len(snapshot.process_results) == 91
    assert len(snapshot.equipment) == 8
    assert [e["eqp_id"] for e in snapshot.equipment] == [
        "EQP-CMP01",
        "EQP-CVD01",
        "EQP-DIFF01",
        "EQP-ETCH01",
        "EQP-IMPL01",
        "EQP-PHOT01",
        "EQP-PHOT02",
        "EQP-TEST01",
    ]
