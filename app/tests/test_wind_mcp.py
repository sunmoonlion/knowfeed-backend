"""Synthetic wire contracts: all audited domains, no credentials or paid calls."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.domain.supply.models import SupplyError
from app.infrastructure.external.wind import TOOLS, HttpWind


class Server:
    def __init__(self, *, result=None, status=200, sse=False, timeout=False):
        self.calls = []
        self.result = (
            result
            if result is not None
            else {
                "content": [
                    {
                        "type": "text",
                        "text": '{"data":{"rows":[{"value":12,"unit":"unknown"}]}}',
                    }
                ]
            }
        )
        self.status, self.sse, self.timeout = status, sse, timeout

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        if body["method"] == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "serverInfo": {"name": "synthetic", "version": "1"},
            }
        elif body["method"] == "tools/list":
            assert request.headers["Mcp-Session-Id"] == "synthetic-session"
            service = request.url.path.split("/")[1].removeprefix("vserver_")
            result = {
                "tools": [
                    {
                        "name": t,
                        "inputSchema": {
                            "type": "object",
                            "properties": {"question": {"type": "string"}},
                            "required": ["question"],
                            "additionalProperties": False,
                        },
                    }
                    for t in TOOLS[service]
                ]
            }
        else:
            if self.timeout:
                raise httpx.ReadTimeout("synthetic timeout")
            if self.status != 200:
                return httpx.Response(self.status)
            result = self.result
        reply = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        if self.sse:
            return httpx.Response(
                200,
                text="event: message\ndata: " + json.dumps(reply) + "\n\n",
                headers={"Mcp-Session-Id": "synthetic-session"},
            )
        return httpx.Response(
            200, json=reply, headers={"Mcp-Session-Id": "synthetic-session"}
        )


@pytest.mark.parametrize("service", list(TOOLS))
@pytest.mark.parametrize("sse", [False, True])
async def test_all_domains_use_advertised_schema_and_preserve_response(service, sse):
    server = Server(sse=sse)
    limited = []

    async def limit(method):
        limited.append(method)

    client = HttpWind(
        SecretStr("synthetic-key"),
        transport=httpx.MockTransport(server),
        before_request=limit,
    )
    result = await client.call(
        service, TOOLS[service][0], {"question": "synthetic query"}
    )
    assert result == server.result
    assert limited == ["initialize", "tools/list", "tools/call"]
    assert len(server.calls) == 3


@pytest.mark.parametrize(
    "status,code",
    [
        (402, "provider_quota_exhausted"),
        (401, "provider_auth_error"),
        (429, "provider_rate_limited"),
        (503, "provider_outcome_unknown"),
    ],
)
async def test_http_failure_is_not_automatically_replayed(status, code):
    server = Server(status=status)
    client = HttpWind(SecretStr("synthetic-key"), transport=httpx.MockTransport(server))
    with pytest.raises(SupplyError) as exc:
        await client.call(
            "stock_data", "get_stock_fundamentals", {"question": "synthetic"}
        )
    assert exc.value.code == code
    assert len(server.calls) == 3


@pytest.mark.parametrize(
    "result,code",
    [
        ({"content": [{"type": "text", "text": "没找到数据"}]}, "provider_no_results"),
        (
            {"isError": True, "content": [{"type": "text", "text": "积分余额不足"}]},
            "provider_quota_exhausted",
        ),
        (
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"mcp_tool_error_code":5,"mcp_tool_error_msg":"积分不足"}',
                    }
                ]
            },
            "provider_quota_exhausted",
        ),
        (
            {
                "isError": True,
                "content": [{"type": "text", "text": "synthetic failure"}],
            },
            "provider_backend_error",
        ),
        ({"content": []}, "provider_no_results"),
        (
            {"content": [{"type": "text", "text": '{"data":{"items":[],"total":0}}'}]},
            "provider_no_results",
        ),
        ({"structuredContent": {"data": {"rows": []}}}, "provider_no_results"),
    ],
)
async def test_business_failure_is_not_successful_data(result, code):
    client = HttpWind(
        SecretStr("synthetic-key"), transport=httpx.MockTransport(Server(result=result))
    )
    with pytest.raises(SupplyError) as exc:
        await client.call(
            "financial_docs", "get_company_announcements", {"question": "synthetic"}
        )
    assert exc.value.code == code


async def test_bad_parameters_rejected_before_paid_call_and_timeout_is_ambiguous():
    server = Server(timeout=True)
    client = HttpWind(SecretStr("synthetic-key"), transport=httpx.MockTransport(server))
    with pytest.raises(SupplyError, match="schema"):
        await client.call("stock_data", "get_stock_fundamentals", {"question": 42})
    assert len(server.calls) == 2
    with pytest.raises(SupplyError) as exc:
        await client.call(
            "stock_data", "get_stock_fundamentals", {"question": "synthetic"}
        )
    assert exc.value.code == "provider_outcome_unknown"
    assert len(server.calls) == 5


async def test_quota_words_inside_an_annual_report_do_not_disable_account():
    server = Server(
        result={"content": [{"type": "text", "text": "年报原文：该公司授信额度不足。"}]}
    )
    client = HttpWind(SecretStr("synthetic-key"), transport=httpx.MockTransport(server))
    assert (
        await client.call(
            "financial_docs", "get_company_announcements", {"question": "synthetic"}
        )
        == server.result
    )
