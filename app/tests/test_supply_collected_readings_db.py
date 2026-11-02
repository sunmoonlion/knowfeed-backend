"""私有副本的逐读法回执与补加工：真实作业库，合成的授权/加工服务。"""

from __future__ import annotations

import pytest
from test_durable_delivery_db import db as db
from test_durable_delivery_db import sql
from test_supply_db import KEY, FakeKnowbase, FixtureWords, World
from test_supply_schedule_db import job_of

from app.domain.supply.models import SupplyError


class PerCopyKnowbase(FakeKnowbase):
    def __init__(self, ready=None, *, legacy=False):
        super().__init__()
        self.ready = ready or {}
        self.legacy = legacy
        self.writes = []

    async def deliver_collected(self, **kw):
        self.delivered.append(kw)
        return [
            {
                "subject": b["subject"],
                "item_ref": b["item_ref"],
                "material_id": b["subject"],
                "version_no": 1,
                "created": False,
                "has_readings": True,
                **(
                    {}
                    if self.legacy
                    else {"readings": sorted(self.ready.get(b["subject"], set()))}
                ),
            }
            for b in kw["beneficiaries"]
        ]

    async def processing_grant(
        self, material_id, version_no, *, owner="public:disclosure"
    ):
        assert material_id == owner
        self.calls.append(("grant", owner))
        return owner

    async def write_pages(self, grant_id, processor_version, pages):
        self.writes.append((grant_id, "pages"))
        self.ready.setdefault(grant_id, set()).add("pages")
        return {}

    async def write_facts(self, grant_id, processor_version, raw, standard):
        self.writes.append((grant_id, "table"))
        self.ready.setdefault(grant_id, set()).add("table")
        return {}


async def world(db, ready=None, *, legacy=False):
    w = World(db)
    await sql(db, "UPDATE supply_channel SET zone = 'collected' WHERE id = 'cninfo'")
    w.knowbase = PerCopyKnowbase(ready, legacy=legacy)
    w.demands.knowbase = w.runner.knowbase = w.knowbase
    w.confirm("conf-1", "alice", KEY)
    return w


@pytest.mark.parametrize("legacy", [False, True])
async def test_any_reading_boolean_does_not_skip_missing_tables(db, legacy):
    w = await world(db, {"alice": {"text"}}, legacy=legacy)
    demand, items, _ = await w.submit()
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status == "ready"
    assert w.knowbase.writes == [("alice", "pages"), ("alice", "table")]
    if not legacy:
        assert "text" in got[0].receipt["readings"]


async def test_only_missing_readings_are_processed_and_each_copy_has_own_grant(db):
    w = await world(db, {"alice": {"pages"}, "bob": {"table"}})
    a, ia, _ = await w.submit()
    w.confirm("conf-b", "bob", KEY)
    b, ib, _ = await w.submit(
        owner="bob", confirmation="conf-b", idem="idem-bob-readings"
    )
    job = await job_of(db, ia[0].id)
    assert job == await job_of(db, ib[0].id)
    assert await w.runner.run(job) == "done"
    assert set(w.knowbase.writes) == {("alice", "table"), ("bob", "pages")}
    assert {v for k, v in w.knowbase.calls if k == "grant"} == {"alice", "bob"}
    for demand in (a, b):
        _, got = await w.demands.get("knowbench", demand.id)
        assert got[0].status == "ready"
        assert got[0].receipt["readings"] == ["original", "pages", "table"]


async def test_original_only_needs_no_processing_or_grant(db):
    w = await world(db)
    demand, items, _ = await w.submit(readings=("original",))
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status == "ready" and got[0].receipt["readings"] == ["original"]
    assert not w.knowbase.writes and not w.knowbase.calls


async def test_receipt_for_another_owner_is_not_used(db):
    w = await world(db)
    deliver = w.knowbase.deliver_collected

    async def wrong_owner(**kw):
        return [{**row, "subject": "bob"} for row in await deliver(**kw)]

    w.knowbase.deliver_collected = wrong_owner
    demand, items, _ = await w.submit()
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status == "failed" and got[0].reason == "unexpected_response"
    assert not w.knowbase.writes and not w.knowbase.calls


async def test_authorization_revoked_after_pages_preserves_success_and_other_owner(db):
    w = await world(db)
    write = w.knowbase.write_facts

    async def denied_for_alice(grant_id, processor_version, raw, standard):
        if grant_id == "alice":
            raise SupplyError(403, "grant_revoked", "synthetic revocation")
        return await write(grant_id, processor_version, raw, standard)

    w.knowbase.write_facts = denied_for_alice
    a, items, _ = await w.submit()
    w.confirm("conf-b", "bob", KEY)
    b, _, _ = await w.submit(
        owner="bob", confirmation="conf-b", idem="idem-bob-revocation"
    )
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, alice = await w.demands.get("knowbench", a.id)
    _, bob = await w.demands.get("knowbench", b.id)
    assert alice[0].status == "partial"
    assert alice[0].receipt["readings"] == ["original", "pages"]
    assert bob[0].status == "ready"
    assert ("alice", "table") not in w.knowbase.writes


@pytest.mark.parametrize("readings", ["table", ["made_up"], [1]])
async def test_malformed_ready_list_cannot_produce_a_success_receipt(db, readings):
    w = await world(db)
    deliver = w.knowbase.deliver_collected

    async def malformed(**kw):
        return [{**row, "readings": readings} for row in await deliver(**kw)]

    w.knowbase.deliver_collected = malformed
    demand, items, _ = await w.submit()
    # 上游错误可以进入有界重试，但不能交付一个成功回执。
    await w.runner.run(await job_of(db, items[0].id))
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status != "ready" and not got[0].receipt
    assert not w.knowbase.writes


async def test_partial_processing_retry_preserves_success_and_does_not_rewrite_pages(
    db,
):
    class Unreadable:
        def read_words(self, *args, **kwargs):
            raise ValueError("synthetic table extraction failure")

    w = await world(db)
    w.runner.pdf_words = Unreadable()
    first, items, _ = await w.submit()
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", first.id)
    assert got[0].status == "partial"
    assert got[0].receipt["readings"] == ["original", "pages"]
    w.runner.pdf_words = FixtureWords()
    second, items, _ = await w.submit(idem="idem-retry-readings")
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", second.id)
    assert got[0].status == "ready"
    assert w.knowbase.writes == [("alice", "pages"), ("alice", "table")]
    third, items, _ = await w.submit(idem="idem-ready-readings")
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", third.id)
    assert got[0].status == "ready"
    assert len(w.knowbase.writes) == 2


async def test_private_pdf_text_does_not_rewrite_existing_pages(db):
    w = await world(db, {"alice": {"pages"}})
    demand, items, _ = await w.submit(readings=("text",))
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status == "ready"
    assert "text" in got[0].receipt["readings"]
    assert w.knowbase.writes == []  # already-ready pages were not overwritten
    assert any(kind == "text" for kind, _ in w.knowbase.calls)


async def test_public_pdf_existing_delivery_can_add_a_new_text_reading(db):
    w = World(db)
    w.confirm("conf-1", "alice", KEY)
    _, first, _ = await w.submit()
    assert await w.runner.run(await job_of(db, first[0].id)) == "done"
    original_deliver = w.knowbase.deliver_public

    async def same_version(**kw):
        return {**await original_deliver(**kw), "material_id": "m-1", "created": False}

    w.knowbase.deliver_public = same_version
    before = len(w.knowbase.calls)
    w.confirm("conf-text", "alice", KEY)
    demand, items, _ = await w.submit(
        confirmation="conf-text", idem="new-text-reading", readings=("text",)
    )
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status == "ready" and "text" in got[0].receipt["readings"]
    kinds = [kind for kind, _ in w.knowbase.calls[before:]]
    assert "text" in kinds and "pages" not in kinds and "facts" not in kinds


async def test_scanned_pdf_is_not_reported_as_ready_text(db):
    w = await world(db, {"alice": {"pages"}})

    class EmptyPages:
        def text_pages(self, pdf):
            return [(1, "")]

    w.runner.pdf_pages = EmptyPages()
    demand, items, _ = await w.submit(readings=("text",))
    assert await w.runner.run(await job_of(db, items[0].id)) == "done"
    _, got = await w.demands.get("knowbench", demand.id)
    assert got[0].status != "ready"
    assert "text" not in got[0].receipt["readings"]
    assert ("failed", "text") in w.knowbase.calls
