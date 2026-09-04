"""Mock MES 접속 클라이언트.

REST(/api)와 MCP(/mcp) 두 채널을 하나의 MesSnapshot으로 모은다.
표준 라이브러리만 사용한다. Fabric 노트북에 그대로 인라인되기 때문에
추가 패키지 설치가 있어선 안 된다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

MES_BASE_URL = "https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io"


def parse_mcp_body(raw: str) -> dict | None:
    """MCP 응답 본문을 파싱한다.

    이 서버는 Accept 헤더에 text/event-stream이 있으면 SSE로 답한다.
    본문은 'event: message'로 시작하므로 'data:' 접두 검사만으로는
    SSE를 인식할 수 없다. 순수 JSON을 먼저 시도하고 실패하면 data 행을 모은다.
    """
    body = raw.strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    chunks = [
        line[len("data:"):].strip()
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    if not chunks:
        raise ValueError(f"MCP 응답을 해석할 수 없습니다: {body[:200]!r}")
    return json.loads("".join(chunks))


def derive_equipment(process_results: list[dict], route: list[dict]) -> list[dict]:
    """공정이력의 eqp_id 고유값에서 설비 목록을 만든다.

    MES는 설비 마스터를 REST에도 MCP에도 노출하지 않는다. /equipment 웹 페이지에만
    있지만 HTML 파싱은 페이지 구조 변경에 취약하므로 쓰지 않는다.
    PKG 단계에 도달한 로트가 없어 EQP-PKG01은 여기서 빠진다.
    """
    eqp_type_by_step = {s["step_code"]: s.get("eqp_type") for s in route}
    first_step: dict[str, str] = {}
    for row in process_results:
        eqp_id = row.get("eqp_id")
        if eqp_id and eqp_id not in first_step:
            first_step[eqp_id] = row["step_code"]
    return [
        {
            "eqp_id": eqp_id,
            "eqp_type": eqp_type_by_step.get(first_step[eqp_id]),
            "step_code": first_step[eqp_id],
        }
        for eqp_id in sorted(first_step)
    ]


_SNAPSHOT_FIELDS = (
    "products",
    "materials",
    "bom",
    "lots",
    "process_results",
    "route",
    "equipment",
)


@dataclass(frozen=True)
class MesSnapshot:
    """QMS 생성기 전체의 유일한 입력. 네트워크 계층과 생성 계층의 경계다."""

    products: list[dict] = field(default_factory=list)
    materials: list[dict] = field(default_factory=list)
    bom: list[dict] = field(default_factory=list)
    lots: list[dict] = field(default_factory=list)
    process_results: list[dict] = field(default_factory=list)
    route: list[dict] = field(default_factory=list)
    equipment: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[dict]]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "MesSnapshot":
        return cls(**{name: payload[name] for name in _SNAPSHOT_FIELDS})


class MesApiKeyMissing(RuntimeError):
    """API 키가 조달되지 않았을 때. 노트북 게이트 셀이 이 예외를 잡아 안내한다."""


class MesClient:
    def __init__(self, base_url: str = MES_BASE_URL, api_key: str = "", timeout: int = 60):
        if not api_key:
            raise MesApiKeyMissing(
                "MES_API_KEY가 비어 있습니다. 노트북 파라미터, Key Vault, "
                "환경변수 MES_API_KEY 중 하나로 공급하세요."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._rpc_id = 0
        self._initialized = False

    def _open(self, url: str, data: bytes | None, headers: dict) -> str:
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data else "GET"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def rest(self, path: str) -> Any:
        """REST GET. path 예: '/api/products'"""
        raw = self._open(
            self.base_url + path,
            None,
            {"X-API-Key": self.api_key, "Accept": "application/json"},
        )
        return json.loads(raw)

    def _rpc(self, method: str, params: dict, notify: bool = False) -> dict | None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            self._rpc_id += 1
            payload["id"] = self._rpc_id
        raw = self._open(
            self.base_url + "/mcp",
            json.dumps(payload).encode("utf-8"),
            {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-API-Key": self.api_key,
            },
        )
        return parse_mcp_body(raw)

    def _handshake(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qms-seeder", "version": "1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True

    def mcp_call(self, tool: str, args: dict | None = None) -> Any:
        """읽기 전용 MCP 툴 호출. 결과 리스트를 그대로 돌려준다."""
        if tool in {"start_lot", "register_process_result"}:
            raise ValueError(f"쓰기 툴 호출 금지: {tool}")
        self._handshake()
        response = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        if response is None:
            raise RuntimeError(f"MCP 툴 {tool} 응답이 비어 있습니다.")
        if "error" in response:
            raise RuntimeError(f"MCP 툴 {tool} 오류: {response['error']}")
        result = response["result"]
        if result.get("isError"):
            raise RuntimeError(f"MCP 툴 {tool} 실패: {result.get('content')}")
        return result["structuredContent"]["result"]

    def fetch_snapshot(self) -> MesSnapshot:
        process_results = self.mcp_call("list_process_results", {"limit": 500})
        route = self.mcp_call("get_process_route")
        return MesSnapshot(
            products=self.rest("/api/products"),
            materials=self.rest("/api/materials"),
            bom=self.rest("/api/bom"),
            lots=self.mcp_call("list_lots", {"limit": 500}),
            process_results=process_results,
            route=route,
            equipment=derive_equipment(process_results, route),
        )
