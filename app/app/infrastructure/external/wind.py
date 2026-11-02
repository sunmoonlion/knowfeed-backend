"""Wind's read-only MCP services, using the skill's official wire contract.

No CLI subprocess, user-home credential lookup, redirects or automatic retries.
The channel layer supplies rate limits and persists account suspension.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import SecretStr
from referencing.exceptions import Unresolvable

from app.domain.supply.models import SupplyError

TOOLS = json.loads(Path(__file__).with_name("wind-tools.json").read_text())
MAX_BYTES = 8 * 1024 * 1024
_EMPTY = {"没找到数据", "没有找到数据", "暂无数据", "无数据", "no data", "no results"}
_QUOTA = re.compile(
    r"(?:额度|积分|余额).{0,30}(?:不足|耗尽|用完|超限)|(?:insufficient|exhausted|exceeded).{0,30}(?:quota|credit|balance)|(?:quota|credit|balance).{0,30}(?:insufficient|exhausted|exceeded)",
    re.I,
)


def endpoint(service: str) -> str:
    if service not in TOOLS:
        raise SupplyError(422, "provider_route_invalid", "unknown Wind service")
    return f"https://mcp.wind.com.cn/vserver_{service}/mcp/"


def _business_error(result: dict[str, Any]) -> None:
    bad = bool(result.get("isError"))
    texts = [
        c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
    ]
    payloads = []
    for value in texts:
        if not isinstance(value, str):
            continue
        try:
            inner = json.loads(value)
        except ValueError:
            continue
        if not isinstance(inner, dict):
            continue
        payloads.append(inner)
        if inner.get("mcp_tool_error_code") not in (None, 0) or inner.get("error"):
            bad = True
        data = inner.get("data")
        if isinstance(data, dict) and "code" in data:
            try:
                code = int(data["code"])
                bad |= code != 0 and not 200 <= code < 300
            except (TypeError, ValueError):
                pass
    if bad:
        if any(isinstance(t, str) and _QUOTA.search(t) for t in texts):
            raise SupplyError(402, "provider_quota_exhausted", "Wind quota exhausted")
        raise SupplyError(422, "provider_backend_error", "Wind rejected the query")
    if texts and all(
        isinstance(t, str) and t.strip().lower().rstrip("。.!！") in _EMPTY
        for t in texts
    ):
        raise SupplyError(404, "provider_no_results", "Wind returned no results")
    if isinstance(result.get("structuredContent"), dict):
        payloads.append(result["structuredContent"])
    if payloads and all(_empty_payload(p) for p in payloads):
        raise SupplyError(404, "provider_no_results", "Wind returned an empty dataset")
    if not result.get("content") and not result.get("structuredContent"):
        raise SupplyError(404, "provider_no_results", "Wind returned no content")


def _empty_payload(value: dict[str, Any]) -> bool:
    data = value.get("data", value)
    if not isinstance(data, dict):
        return data == []
    if "data" in data and not any(k in data for k in ("items", "rows")):
        return _empty_payload(data)
    return any(k in data for k in ("items", "rows")) and all(
        data.get(k, []) == [] for k in ("items", "rows")
    )


class HttpWind:
    def __init__(
        self,
        key: SecretStr,
        *,
        transport=None,
        timeout: float = 180,
        before_request=None,
    ) -> None:
        self.key = key
        self.transport = transport
        self.timeout = timeout
        self.before_request = before_request

    async def _rpc(self, client, url, method, params, number, headers):
        if self.before_request is not None:
            await self.before_request(method)
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": number,
                    "method": method,
                    "params": params,
                },
            ) as response:
                if response.status_code == 402:
                    raise SupplyError(
                        402, "provider_quota_exhausted", "Wind quota exhausted"
                    )
                if response.status_code in (401, 403):
                    raise SupplyError(
                        403, "provider_auth_error", "Wind authentication rejected"
                    )
                if response.status_code == 429:
                    raise SupplyError(429, "provider_rate_limited", "Wind rate limited")
                if response.status_code != 200:
                    raise SupplyError(
                        409,
                        "provider_outcome_unknown",
                        "Wind HTTP failure; automatic replay disabled",
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise SupplyError(
                            422,
                            "provider_response_too_large",
                            "Wind response exceeds 8 MiB",
                        )
                session = response.headers.get("mcp-session-id")
                if session:
                    headers["Mcp-Session-Id"] = session
        except httpx.HTTPError:
            raise SupplyError(
                409,
                "provider_outcome_unknown",
                "Wind network outcome unknown; automatic replay disabled",
            ) from None
        try:
            raw = data.decode("utf-8")
            if raw.lstrip().startswith("{"):
                reply = json.loads(raw)
            else:
                events = []
                for block in raw.replace("\r\n", "\n").split("\n\n"):
                    lines = [
                        line[5:].lstrip()
                        for line in block.splitlines()
                        if line.startswith("data:")
                    ]
                    if lines:
                        event = json.loads("\n".join(lines))
                        if event.get("id") == number:
                            events.append(event)
                [reply] = events
            if not isinstance(reply, dict) or reply.get("id") != number:
                raise ValueError("wrong response identity")
        except (ValueError, TypeError, AttributeError):
            raise SupplyError(
                422, "provider_response_invalid", "invalid Wind MCP response"
            ) from None
        if reply.get("error"):
            message = json.dumps(reply["error"], ensure_ascii=False)
            if _QUOTA.search(message):
                raise SupplyError(
                    402, "provider_quota_exhausted", "Wind quota exhausted"
                )
            raise SupplyError(
                422, "provider_backend_error", "Wind MCP rejected the request"
            )
        result = reply.get("result")
        if not isinstance(result, dict):
            raise SupplyError(
                422, "provider_response_invalid", "Wind result must be an object"
            )
        return result

    async def _catalog(self, client, service, headers):
        url = endpoint(service)
        await self._rpc(
            client,
            url,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "sunmoon-knowfeed", "version": "1"},
            },
            1,
            headers,
        )
        result = await self._rpc(client, url, "tools/list", {}, 2, headers)
        return [
            t
            for t in result.get("tools", [])
            if isinstance(t, dict) and t.get("name") in TOOLS[service]
        ]

    async def tools(self, service: str) -> list[dict[str, Any]]:
        endpoint(service)
        async with self._client() as client:
            return await self._catalog(client, service, self._headers())

    async def call(
        self, service: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        url = endpoint(service)
        if tool not in TOOLS[service]:
            raise SupplyError(
                422, "provider_route_invalid", "tool is not in the audited Wind service"
            )
        async with self._client() as client:
            headers = self._headers()
            catalog = await self._catalog(client, service, headers)
            schema = next(
                (t.get("inputSchema") for t in catalog if t.get("name") == tool), None
            )
            if not isinstance(schema, dict):
                raise SupplyError(
                    422, "provider_route_invalid", "Wind did not advertise this tool"
                )
            # Remote references are not allowed to cause additional network requests.
            encoded = json.dumps(schema)
            if re.search(r'"\$(?:ref|dynamicRef)"\s*:\s*"(?!#)', encoded):
                raise SupplyError(
                    422,
                    "provider_schema_invalid",
                    "external schema references are unsupported",
                )
            try:
                Draft202012Validator.check_schema(schema)
                invalid = next(
                    Draft202012Validator(schema).iter_errors(arguments), None
                )
            except (SchemaError, Unresolvable, RecursionError):
                raise SupplyError(
                    422, "provider_schema_invalid", "invalid Wind tool schema"
                ) from None
            if invalid:
                raise SupplyError(
                    422,
                    "provider_parameters_invalid",
                    "arguments do not match Wind tool schema; inspect source capabilities",
                )
            result = await self._rpc(
                client,
                url,
                "tools/call",
                {"name": tool, "arguments": arguments},
                3,
                headers,
            )
            _business_error(result)
            return result

    def _headers(self):
        key = self.key.get_secret_value()
        if not key:
            raise SupplyError(
                403, "provider_auth_error", "Wind service credential is not configured"
            )
        return {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

    def _client(self):
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        )
