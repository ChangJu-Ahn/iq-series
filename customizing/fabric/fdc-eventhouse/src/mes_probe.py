"""Mock MES 접속 프로브. MCP 채널만 쓴다.

FDC 텔레메트리가 MES에서 필요로 하는 사실은 두 가지뿐이다.

- 설비 목록: 어떤 설비에서 센서를 읽을 것인가
- 설비별 불량 실적: 어느 설비에 이상을 주입할 것인가

둘 다 `list_process_results` 와 `get_process_route` 로 얻는다. REST 채널
(`/api/products` 등)과 제품·자재·BOM·로트는 쓰지 않으므로 가져오지 않는다.

표준 라이브러리만 사용한다. Fabric 노트북에 그대로 인라인되기 때문에
추가 패키지 설치가 있어선 안 된다.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any

MES_BASE_URL = "https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io"

# 쓰기 툴은 호출하지 않는다. 이 노트북은 MES를 읽기만 한다.
_WRITE_TOOLS = frozenset({"start_lot", "register_process_result"})


class MesApiKeyMissing(ValueError):
    """MES_API_KEY 가 비어 있을 때."""


def parse_mcp_body(raw: str) -> dict | None:
    """MCP 응답 본문을 파싱한다.

    이 서버는 Accept 헤더에 text/event-stream 이 있으면 SSE 로 답한다.
    본문이 'event: message' 로 시작하므로 'data:' 접두 검사만으로는 SSE를
    인식할 수 없다. 순수 JSON을 먼저 시도하고 실패하면 data 행을 모은다.
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
        if line.strip().startswith("data:")
    ]
    if not chunks:
        raise ValueError(f"MCP 응답을 해석할 수 없습니다: {body[:200]!r}")
    return json.loads("".join(chunks))


def derive_equipment(process_results: list[dict], route: list[dict]) -> list[dict]:
    """공정이력의 eqp_id 고유값에서 설비 목록을 만든다.

    MES는 설비 마스터를 REST에도 MCP에도 노출하지 않는다. eqp_id 가 비어 있는
    공정이력이 존재하므로(실측 91건 중 7건) 그 행은 건너뛴다.
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


@dataclass(frozen=True)
class MesFacts:
    """FDC 생성기의 유일한 MES 입력. 네트워크 계층과 생성 계층의 경계다."""

    process_results: list[dict]
    route: list[dict]
    equipment: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.equipment:
            object.__setattr__(
                self, "equipment", derive_equipment(self.process_results, self.route)
            )

    @classmethod
    def from_dict(cls, payload: dict) -> MesFacts:
        return cls(
            process_results=payload["process_results"],
            route=payload["route"],
            equipment=payload.get("equipment") or [],
        )

    def to_dict(self) -> dict:
        return {
            "process_results": self.process_results,
            "route": self.route,
            "equipment": self.equipment,
        }


class MesProbe:
    def __init__(self, base_url: str = MES_BASE_URL, api_key: str = "", timeout: int = 60):
        if not api_key:
            raise MesApiKeyMissing(
                "MES_API_KEY가 비어 있습니다. 노트북 파라미터 셀에 키를 넣으세요."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._rpc_id = 0
        self._initialized = False

    def _post(self, payload: dict) -> str:
        request = urllib.request.Request(
            self.base_url + "/mcp",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-API-Key": self.api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def _rpc(self, method: str, params: dict, notify: bool = False) -> dict | None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            self._rpc_id += 1
            payload["id"] = self._rpc_id
        return parse_mcp_body(self._post(payload))

    def _handshake(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fdc-telemetry", "version": "1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True

    def mcp_call(self, tool: str, args: dict | None = None) -> Any:
        """읽기 전용 MCP 툴 호출. 결과를 그대로 돌려준다."""
        if tool in _WRITE_TOOLS:
            raise ValueError(f"쓰기 툴 호출 금지: {tool}")
        self._handshake()
        response = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        if response is None:
            raise RuntimeError(f"MCP 툴 {tool} 응답이 비어 있습니다.")
        if "error" in response:
            raise RuntimeError(f"MCP 툴 {tool} 오류: {response['error']}")
        if "result" not in response:
            raise RuntimeError(f"MCP 툴 {tool} 응답에 result가 없습니다: {response}")
        result = response["result"]
        if result.get("isError"):
            raise RuntimeError(f"MCP 툴 {tool} 실패: {result.get('content')}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict) or "result" not in structured:
            raise RuntimeError(f"MCP 툴 {tool} 응답 형식이 예상과 다릅니다: {result}")
        return structured["result"]

    def fetch_facts(self) -> MesFacts:
        process_results = self.mcp_call("list_process_results", {"limit": 500})
        route = self.mcp_call("get_process_route")
        return MesFacts(process_results=process_results, route=route)
