"""Confirmed supplier queries use real supply jobs; the provider is synthetic."""

import json

import pytest
from test_durable_delivery_db import db as db  # noqa: F401
from test_durable_delivery_db import sql
from test_supply_db import World
from test_supply_schedule_db import job_of

from app.application.supply.provider_query import ProviderCatalog
from app.domain.supply.models import DescriptionKey, SupplyError, query_json


class Provider:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def tools(self, service):
        return [
            {"name": "get_company_announcements", "inputSchema": {"type": "object"}}
        ]

    async def call(self, service, tool, arguments):
        self.calls.append((service, tool, arguments))
        if self.error:
            raise self.error
        return {
            "content": [
                {
                    "type": "text",
                    "text": "synthetic annual report excerpt; unit unknown",
                }
            ]
        }


def key(question="synthetic annual report", *, service="financial_docs"):
    return DescriptionKey(
        None,
        "other_text",
        source_query=query_json(
            {
                "channel": "cninfo",
                "service": service,
                "tool": "get_company_announcements",
                "arguments": {"query": question},
            }
        ),
    )


async def setup(db, error=None):
    w = World(db)
    await sql(
        db,
        "UPDATE supply_channel SET plugin='wind-mcp', zone='collected', license='self', doc_types='[\"other_text\"]', readings='[\"original\",\"text\"]', config='{\"owner_subject\":\"alice\"}' WHERE id='cninfo'",
    )
    channel = await w.repo.get_channel("cninfo")
    provider = Provider(error)
    w.catalogs["wind-mcp"] = ProviderCatalog(provider, channel, w.repo)
    return w, provider


async def test_confirmed_query_delivers_original_response_and_text_without_fake_pdf(db):
    w, provider = await setup(db)
    wanted = key()
    w.confirm("conf-1", "alice", wanted)
    demand, items, _ = await w.submit(key=wanted, readings=("original", "text"))
    job = await job_of(db, items[0].id)
    assert await w.runner.run(job) == "done"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status == "ready"
    assert got[0].receipt["readings"] == ["original", "text"]
    [delivery] = w.knowbase.delivered
    assert delivery["license"] == "self" and delivery["name"].endswith(".txt")
    envelope = json.loads(delivery["content"])
    assert envelope["kind"] == "supplier_response"
    assert envelope["query"] == wanted.as_dict()["source_query"]
    assert len(provider.calls) == 1
    assert await w.runner.run(job) == "skipped"
    assert len(provider.calls) == 1
    assert not any(call[0] in ("pages", "facts") for call in w.knowbase.calls)


async def test_changed_query_cannot_reuse_confirmation_or_merge_job(db):
    w, _ = await setup(db)
    w.confirm("conf-1", "alice", key("first"))
    with pytest.raises(SupplyError) as exc:
        await w.submit(key=key("changed"), readings=("original",))
    assert exc.value.code == "outside_confirmation"
    _, first, _ = await w.submit(key=key("first"), readings=("original",))
    w.confirm("conf-2", "alice", key("second"))
    _, second, _ = await w.submit(
        key=key("second"),
        readings=("original",),
        confirmation="conf-2",
        idem="different-query",
    )
    assert await job_of(db, first[0].id) != await job_of(db, second[0].id)


async def test_other_owner_never_spends_the_configured_accounts_quota(db):
    w, provider = await setup(db)
    w.confirm("conf-bob", "bob", key())
    demand, items, _ = await w.submit(
        owner="bob", confirmation="conf-bob", key=key(), readings=("original",)
    )
    await w.runner.run(await job_of(db, items[0].id))
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status == "failed"
    assert got[0].reason == "provider_owner_forbidden"
    assert not provider.calls and not w.knowbase.delivered


async def test_quota_exhaustion_persists_pause_and_never_retries_or_delivers(db):
    w, provider = await setup(
        db, SupplyError(402, "provider_quota_exhausted", "synthetic")
    )
    w.confirm("conf-1", "alice", key())
    demand, items, _ = await w.submit(key=key(), readings=("original",))
    job = await job_of(db, items[0].id)
    assert await w.runner.run(job) == "failed"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].reason == "provider_quota_exhausted"
    assert (await w.repo.get_channel("cninfo")).status == "paused"
    assert await w.runner.run(job) == "skipped"
    w.confirm("conf-2", "alice", key("next"))
    _, second, _ = await w.submit(
        key=key("next"),
        readings=("original",),
        confirmation="conf-2",
        idem="after-quota-stop",
    )
    assert await w.runner.run(await job_of(db, second[0].id)) == "held"
    assert len(provider.calls) == 1 and not w.knowbase.delivered


async def test_owner_joining_during_catalog_lookup_cannot_receive_private_response(db):
    w, provider = await setup(db)
    wanted = key()
    w.confirm("conf-1", "alice", wanted)
    _, items, _ = await w.submit(key=wanted, readings=("original",))
    adapter = w.catalogs["wind-mcp"]
    original_list = adapter.list
    joined = []

    async def list_and_join(query):
        w.confirm("conf-bob", "bob", wanted)
        demand, _, _ = await w.submit(
            owner="bob",
            confirmation="conf-bob",
            key=wanted,
            readings=("original",),
            idem="late-bob-query",
        )
        joined.append(demand.id)
        return await original_list(query)

    adapter.list = list_and_join
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, forbidden = await w.demands.get("knowbench", joined[0])
    assert forbidden[0].reason == "provider_owner_forbidden"
    assert len(provider.calls) == 1
    assert len(w.knowbase.delivered) == 1
    assert all(
        b["subject"] == "alice" for b in w.knowbase.delivered[0]["beneficiaries"]
    )


async def test_wind_registration_trial_approval_and_updates_preserve_private_scope(db):
    from dataclasses import replace

    from test_supply_catalog_db import REG, Desk

    w = World(db)
    desk = Desk(db, w)
    registration = replace(
        REG,
        plugin="wind-mcp",
        zone="collected",
        license="self",
        doc_types=("other_text",),
        readings=("original", "text"),
        daily_cap=0,
        config={"owner_subject": "alice"},
    )
    channel = await desk.catalog.register("wind", registration, actor="operator")
    wanted = DescriptionKey(
        None,
        "other_text",
        source_query=key().source_query.replace('"cninfo"', '"wind"'),
    )
    provider = Provider()
    normal = ProviderCatalog(provider, channel, w.repo)
    with pytest.raises(SupplyError, match="suspended"):
        await normal.list(wanted)
    trial_adapter = ProviderCatalog(provider, channel, w.repo, trial=True)
    desk.catalogs["wind-mcp"] = trial_adapter
    trial = await desk.catalog.request_trial("wind", wanted, actor="operator")
    assert await desk.trials.run(trial.id) == "ok"
    for change in ({"license": "sale"}, {"zone": "disclosure"}):
        with pytest.raises(SupplyError) as exc:
            await desk.catalog.review(
                "wind", "approve", actor="operator", reason="synthetic", **change
            )
        assert exc.value.code == "provider_scope_invalid"
    await desk.catalog.review(
        "wind", "approve", actor="operator", reason="synthetic authorized account"
    )
    for change in ({"license": "sale"}, {"max_running": 2}, {"readings": ("pages",)}):
        with pytest.raises(SupplyError) as exc:
            await desk.catalog.update(
                "wind",
                replace(registration, **change),
                actor="operator",
                reason="synthetic",
            )
        assert exc.value.code == "provider_scope_invalid"
    assert (await w.repo.get_channel("wind")).daily_cap == 0


async def test_capability_discovery_is_owner_scoped_and_never_fetches_data(db):
    import httpx
    from fastapi import FastAPI

    from app.application.supply.provider_query import SourceDiscovery
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.http.internal import supply as routes

    w, provider = await setup(db)
    discovery = SourceDiscovery(
        w.repo, lambda c: w.catalogs.get(c.plugin), ["stock_data", "financial_docs"]
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[routes.internal_host_only] = lambda: None
    app.dependency_overrides[routes.caller_app] = lambda: "knowbench"
    app.dependency_overrides[routes.get_source_discovery] = lambda: discovery
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://knowfeed"
    ) as client:
        path = "/api/internal/v1/supply/source-tools"
        assert (await client.get(path, params={"owner": "bob"})).json() == {
            "sources": []
        }
        available = await client.get(
            path, params={"owner": "alice", "service": "financial_docs"}
        )
        assert available.status_code == 200
        assert (
            available.json()["sources"][0]["tools"][0]["name"]
            == "get_company_announcements"
        )
        assert not provider.calls
        assert (
            await client.get(
                path, params={"owner": "alice", "service": "https://evil.test"}
            )
        ).status_code == 422
        await sql(db, "UPDATE supply_channel SET status='paused' WHERE id='cninfo'")
        assert (await client.get(path, params={"owner": "alice"})).json() == {
            "sources": []
        }


async def test_delivery_retry_uses_retained_response_without_rebilling(db):
    from datetime import timedelta

    w, provider = await setup(db)
    w.confirm("conf-1", "alice", key())
    demand, items, _ = await w.submit(key=key(), readings=("original",))
    job = await job_of(db, items[0].id)
    deliver = w.knowbase.deliver_collected

    async def unavailable(**kwargs):
        raise SupplyError(503, "temporary_downstream", "synthetic outage")

    w.knowbase.deliver_collected = unavailable
    assert await w.runner.run(job) == "retry"
    assert len(provider.calls) == 1
    w.knowbase.deliver_collected = deliver
    w.clock.now += timedelta(minutes=10)
    assert await w.runner.run(job) == "done"
    assert len(provider.calls) == 1
    assert (await w.demands.get("knowbench", demand.id))[1][0].status == "ready"


async def test_lost_paid_response_is_not_automatically_replayed_after_worker_recovery(
    db,
):
    w, provider = await setup(db)
    w.confirm("conf-1", "alice", key())
    demand, items, _ = await w.submit(key=key(), readings=("original",))
    job = await job_of(db, items[0].id)
    # A prior worker persisted the intent but died before retaining a response.
    await sql(
        db,
        "UPDATE supply_job SET payload=payload || jsonb_build_object('provider_query_started', true) WHERE id=CAST(:job AS uuid)",
        job=job,
    )
    assert await w.runner.run(job) == "failed"
    assert not provider.calls
    assert (await w.demands.get("knowbench", demand.id))[1][
        0
    ].reason == "provider_outcome_unknown"


async def test_shared_wind_discovery_and_confirmed_private_delivery(db):
    from app.application.supply.provider_query import SourceDiscovery

    w, provider = await setup(db)
    await sql(
        db,
        "UPDATE supply_channel SET config=config || '{\"audience\":\"all_users\"}'::jsonb WHERE id='cninfo'",
    )
    channel = await w.repo.get_channel("cninfo")
    w.catalogs["wind-mcp"] = ProviderCatalog(provider, channel, w.repo)
    discovery = SourceDiscovery(
        w.repo, lambda c: w.catalogs.get(c.plugin), ["financial_docs"]
    )
    for owner in ("alice", "bob"):
        assert (await discovery.discover(owner))["sources"]
    assert not (await discovery.discover(""))["sources"]
    assert not provider.calls
    w.confirm("conf-a", "alice", key())
    w.confirm("conf-b", "bob", key())
    da, ia, _ = await w.submit(
        owner="alice",
        confirmation="conf-a",
        key=key(),
        readings=("original", "text"),
        idem="shared-query-alice",
    )
    dbob, ib, _ = await w.submit(
        owner="bob",
        confirmation="conf-b",
        key=key(),
        readings=("original", "text"),
        idem="shared-query-bob",
    )
    assert await job_of(db, ia[0].id) == await job_of(db, ib[0].id)
    assert await w.runner.run(await job_of(db, ia[0].id)) == "done"
    assert len(provider.calls) == 1
    for demand in (da, dbob):
        item = (await w.demands.get("knowbench", demand.id))[1][0]
        assert item.status == "ready"
    assert all(d["license"] == "self" for d in w.knowbase.delivered)
    # Only the two explicitly confirmed beneficiaries get private copies.
    owners = await sql(
        db,
        "SELECT count(DISTINCT owner_subject) FROM supply_original_delivery WHERE zone='collected'",
    )
    assert owners == 2


async def test_shared_wind_revocation_before_delivery_rechecks_owner(db):
    w, provider = await setup(db)
    await sql(
        db,
        "UPDATE supply_channel SET config=config || '{\"audience\":\"all_users\"}'::jsonb WHERE id='cninfo'",
    )
    channel = await w.repo.get_channel("cninfo")
    adapter = ProviderCatalog(provider, channel, w.repo)
    w.catalogs["wind-mcp"] = adapter
    w.confirm("conf-b", "bob", key())
    demand, items, _ = await w.submit(
        owner="bob", confirmation="conf-b", key=key(), readings=("original",)
    )
    fetch = adapter.fetch

    async def fetch_and_restrict(entry):
        response = await fetch(entry)
        await sql(
            db, "UPDATE supply_channel SET config=config - 'audience' WHERE id='cninfo'"
        )
        return response

    adapter.fetch = fetch_and_restrict
    await w.runner.run(await job_of(db, items[0].id))
    assert (await w.demands.get("knowbench", demand.id))[1][
        0
    ].reason == "provider_owner_forbidden"
    assert not w.knowbase.delivered


async def test_shared_account_quota_pause_applies_to_every_user(db):
    w, provider = await setup(
        db, SupplyError(402, "provider_quota_exhausted", "synthetic")
    )
    await sql(
        db,
        "UPDATE supply_channel SET config=config || '{\"audience\":\"all_users\"}'::jsonb WHERE id='cninfo'",
    )
    channel = await w.repo.get_channel("cninfo")
    w.catalogs["wind-mcp"] = ProviderCatalog(provider, channel, w.repo)
    w.confirm("conf-b", "bob", key())
    _, items, _ = await w.submit(
        owner="bob", confirmation="conf-b", key=key(), readings=("original",)
    )
    assert await w.runner.run(await job_of(db, items[0].id)) == "failed"
    assert (await w.repo.get_channel("cninfo")).status == "paused"
    w.confirm("conf-a", "alice", key("another"))
    _, items, _ = await w.submit(
        owner="alice",
        confirmation="conf-a",
        key=key("another"),
        readings=("original",),
        idem="shared-after-quota",
    )
    assert await w.runner.run(await job_of(db, items[0].id)) == "held"
    assert len(provider.calls) == 1
    assert not w.knowbase.delivered
