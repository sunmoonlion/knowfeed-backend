"""A registered supplier query uses the existing confirmed collection workflow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

from app.application.ports.supply import SupplyRepository
from app.domain.supply.models import Channel, DescriptionKey, Entry, SupplyError


class QueryClient(Protocol):
    async def call(
        self, service: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def tools(self, service: str) -> list[dict[str, Any]]: ...


class ProviderCatalog:
    """The original is the supplier response, never a pretend issuer PDF."""

    def __init__(
        self,
        client: QueryClient,
        channel: Channel,
        repo: SupplyRepository,
        *,
        trial: bool = False,
    ) -> None:
        self.client = client
        self.channel = channel
        self.repo = repo
        self.trial = trial

    async def list(self, key: DescriptionKey) -> list[Entry]:
        current = await self.current_channel()
        candidate = replace(current, review="approved") if self.trial else current
        if not key.source_query or not candidate.covers(key):
            return []
        if key.doc_type != "other_text" or key.period_start or key.period_end:
            raise SupplyError(
                422, "query_invalid", "query results are supplier response evidence"
            )
        query = json.loads(key.source_query)
        digest = hashlib.sha256(key.source_query.encode()).hexdigest()
        return [
            Entry(
                channel_id=self.channel.id,
                channel_doc_id=digest,
                title=f"Wind 查询回执 {query['tool']}",
                url="https://mcp.wind.com.cn/",
                disclosed_on=None,
                doc_type="other_text",
                subject=key.subject,
                source_query=key.source_query,
            )
        ]

    async def current_channel(self) -> Channel:
        current = await self.repo.get_channel(self.channel.id)
        if (
            current is None
            or current.status != "active"
            or current.review
            not in (("approved", "pending") if self.trial else ("approved",))
            or current.zone != "collected"
            or current.license != "self"
            or current.config.get("audience", "owner")
            != self.channel.config.get("audience", "owner")
            or current.config.get("owner_subject")
            != self.channel.config.get("owner_subject")
        ):
            raise SupplyError(
                409, "provider_suspended", "supplier channel is suspended or changed"
            )
        return current

    async def tools(self, service: str) -> list[dict[str, Any]]:
        current = await self.current_channel()
        try:
            return await self.client.tools(service)
        except SupplyError as exc:
            if exc.code in ("provider_quota_exhausted", "provider_auth_error"):
                await self.repo.channel_failed(
                    current.id, datetime.now(UTC), pause_after=1
                )
            raise

    async def fetch(self, entry: Entry) -> tuple[bytes, str]:
        if not entry.source_query or entry.channel_id != self.channel.id:
            raise SupplyError(422, "query_invalid", "missing confirmed provider query")
        current = await self.current_channel()
        query = json.loads(entry.source_query)
        if (
            query["channel"] != current.id
            or hashlib.sha256(entry.source_query.encode()).hexdigest()
            != entry.channel_doc_id
        ):
            raise SupplyError(422, "query_invalid", "provider query identity mismatch")
        try:
            result = await self.client.call(
                query["service"], query["tool"], query["arguments"]
            )
        except SupplyError as exc:
            if exc.code in ("provider_quota_exhausted", "provider_auth_error"):
                # One registered channel owns this account; pause is durable across
                # workers/restarts and existing admin resume is explicit.
                await self.repo.channel_failed(
                    current.id, datetime.now(UTC), pause_after=1
                )
            raise
        envelope = {
            "kind": "supplier_response",
            "source": "Wind",
            "query": query,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "result": result,
            "limitations": [
                "原始供应商查询响应；不是发行人年报 PDF。",
                "单位、期间、币种和数据完整性以响应为准；未提供的元数据未知。",
            ],
        }
        return json.dumps(
            envelope, ensure_ascii=False, indent=2
        ).encode(), "text/plain; charset=utf-8"


class SourceDiscovery:
    """Owner-scoped capabilities; discovery never calls a data tool."""

    def __init__(
        self, repo: SupplyRepository, plugins: Callable, services: list[str]
    ) -> None:
        self.repo, self.plugins, self.services = repo, plugins, services

    async def discover(self, owner: str, service: str | None = None) -> dict[str, Any]:
        if service is not None and service not in self.services:
            raise SupplyError(422, "provider_route_invalid", "unknown supplier service")
        sources = []
        for channel in await self.repo.channels():
            if (
                channel.plugin != "wind-mcp"
                or channel.review != "approved"
                or channel.status != "active"
                or not channel.allows_owner(owner)
            ):
                continue
            adapter = self.plugins(channel)
            if not isinstance(adapter, ProviderCatalog):
                continue
            await adapter.current_channel()
            sources.append(
                {
                    "channel": channel.id,
                    "name": channel.name,
                    "services": self.services,
                    "tools": await adapter.tools(service) if service else [],
                    "service": service,
                    "license": channel.license,
                    "kind": "supplier_response",
                    "readings": ["original", "text"],
                }
            )
        return {"sources": sources}
