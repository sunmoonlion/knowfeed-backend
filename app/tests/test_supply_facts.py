"""要哪些数的需求项（SDD 0051 第 2.3 节）。

照用户确认的原样留住，knowfeed 自己推「落在哪份定期报告里」去找渠道；
Wind 只认配置里有对照的科目。
"""

from __future__ import annotations

from datetime import date

import pytest
from test_durable_delivery_db import db as db  # noqa: F401
from test_durable_delivery_db import sql
from test_supply_db import World
from test_supply_web import channel

from app.domain.supply.models import DescriptionKey, SupplyError

FACTS = {
    "kind": "facts",
    "subject": "300750.SZ",
    "period_end": "2025-12-31",
    "codes": ["IS.REVENUE", "IS.NET_PROFIT_PARENT"],
    "consolidation": "consolidated",
    "duration": "year",
    "disclosure": "first",
}


def test_a_facts_key_round_trips_exactly_and_knows_its_report():
    key = DescriptionKey.from_dict(FACTS)
    assert key.as_dict() == FACTS  # 对得上用户的确认
    assert (key.doc_type, key.period_start, key.period_end) == (
        "annual_report",
        date(2025, 1, 1),
        date(2025, 12, 31),
    )
    assert "facts:IS.REVENUE,IS.NET_PROFIT_PARENT" in key.text


@pytest.mark.parametrize(
    "end,doc_type",
    [
        ("2025-03-31", "q1_report"),
        ("2025-06-30", "semiannual_report"),
        ("2025-09-30", "q3_report"),
    ],
)
def test_other_period_ends_map_to_their_report(end, doc_type):
    assert DescriptionKey.from_dict({**FACTS, "period_end": end}).doc_type == doc_type


@pytest.mark.parametrize(
    "bad",
    [{"period_end": "2025-05-31"}, {"codes": []}, {"subject": ""}],
)
def test_a_facts_key_without_a_report_period_or_codes_is_refused(bad):
    with pytest.raises(ValueError):
        DescriptionKey.from_dict({**FACTS, **bad})


def wind(**config):
    return channel(
        id="wind",
        name="Wind",
        kind="provider",
        plugin="wind-mcp",
        doc_types=("other_text",),
        config=config,
    )


def test_wind_takes_facts_only_for_mapped_codes():
    key = DescriptionKey.from_dict(FACTS)
    assert not wind().covers(key)  # 还没有对照：不认，落到下一个渠道
    partial = wind(fact_fields={"IS.REVENUE": "oper_rev"})
    assert not partial.covers(key)  # 少一个科目也不认
    full = wind(fact_fields={"IS.REVENUE": "oper_rev", "IS.NET_PROFIT_PARENT": "np"})
    assert full.covers(key)
    # 要资料的 Wind 一律不认
    report = DescriptionKey(
        "300750.SZ", "annual_report", date(2025, 1, 1), date(2025, 12, 31)
    )
    assert not full.covers(report)


def test_a_report_channel_takes_facts_through_their_report():
    cninfo = channel(id="cninfo", plugin="cninfo", doc_types=("annual_report",))
    assert cninfo.covers(DescriptionKey.from_dict(FACTS))
    quarterly = DescriptionKey.from_dict({**FACTS, "period_end": "2025-03-31"})
    assert not cninfo.covers(quarterly)  # 这条渠道没登记季报


async def test_a_facts_demand_fetches_the_report_and_writes_the_facts(db):
    """端到端：要五粮液 2025 年的营业收入 → 巨潮那份年报 → 交 knowbase、
    抽表写事实 → 就绪。"""
    w = World(db)
    facts = DescriptionKey.from_dict({**FACTS, "subject": "000858.SZ"})
    w.confirm("conf-f", "alice", facts)
    with pytest.raises(SupplyError):  # 要数却不要表格读法：数在表格读法里，不收
        await w.submit(confirmation="conf-f", key=facts, readings=("original",))
    demand, items, _ = await w.submit(
        confirmation="conf-f", key=facts, readings=("table",), idem="idem-facts-01"
    )
    assert [i.status for i in items] == ["fetching"]
    assert items[0].key is not None and items[0].key.as_dict()["kind"] == "facts"
    job_id = str(await sql(db, "SELECT id FROM supply_job"))
    assert await w.runner.run(job_id) == "done"
    kinds = [c[0] for c in w.knowbase.calls]
    assert "deliver" in kinds and "facts" in kinds
    _, after = await w.demands.get("knowbench", demand.id)
    assert after[0].status == "ready"
    assert after[0].key is not None and after[0].key.as_dict() == facts.as_dict()
