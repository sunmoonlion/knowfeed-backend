"""Validate actual outgoing HTTP bodies against knowbase's pinned public contract."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from app.application.supply.processing import FACTS_VERSION, StatementFacts, _one
from app.domain.securities.report_statements import ExtractedRow, ExtractedStatement
from app.domain.supply.models import Reference
from app.infrastructure.external.knowbase_supply import HttpKnowbase

PARENT = Path(__file__).resolve().parents[3]


def validator(model: str) -> Draft202012Validator:
    lock_path = PARENT / "contracts/knowbase-user-data-lock.json"
    if not lock_path.exists():
        pytest.skip("parent contract lock is not checked out")
    lock = json.loads(lock_path.read_text())
    # CI may use the release provider artifact while sibling worktrees advance.
    # The pinned digest is still mandatory for either source.
    path = Path(
        os.environ.get("KNOWBASE_USER_DATA_CONTRACT_PATH")
        or (PARENT.parent / "knowbase-app" / lock["schema"])
    )
    if not path.exists():
        pytest.skip("knowbase provider contract is not checked out")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == lock["sha256"]
    schema = json.loads(path.read_text())
    return Draft202012Validator({**schema, "$ref": f"#/$defs/{model}"})


class Token:
    async def get_token(self) -> str:
        return "synthetic-contract-token"


async def test_collected_receipt_keeps_partial_readings_and_version_references():
    receipt = {
        "subject": "alice",
        "item_ref": "confirmed:item-1",
        "material_id": "material-1",
        "version_no": 2,
        "created": False,
        "has_readings": True,
        "readings": ["text"],
        "renditions": [
            {
                "kind": "text",
                "rendition_no": 3,
                "sha256": "a" * 64,
                "processor": "knowfeed",
                "processor_version": "text-1",
            }
        ],
    }
    validator("CollectedOriginalRead").validate(receipt)

    async def receive(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/service/collected-originals")
        return httpx.Response(201, json=[receipt])

    client = HttpKnowbase(
        "https://knowbase.internal",
        Token(),
        5.0,
        transport=httpx.MockTransport(receive),
    )
    got = await client.deliver_collected(
        channel="synthetic",
        channel_doc_id="document-1",
        name="sample.txt",
        content=b"synthetic evidence",
        license="self",
        source_url="https://example.org/sample",
        beneficiaries=[{"subject": "alice", "item_ref": "confirmed:item-1"}],
    )
    assert got == [receipt]


async def test_empty_pdf_labels_do_not_reject_valid_facts_or_shift_provenance():
    facts = StatementFacts()
    statement = ExtractedStatement(
        statement="income_statement",
        start_page=4,
        unit="元",
        unit_source="statement_page",
        side_by_side=False,
        rows=(
            ExtractedRow(4, "", Decimal("17"), Decimal("16")),
            ExtractedRow(4, "营业收入", Decimal("100"), Decimal("90")),
            ExtractedRow(4, "  ", Decimal("7"), None),
            ExtractedRow(4, "营业成本", Decimal("60"), Decimal("50")),
        ),
        columns={},
    )
    _one(
        facts,
        "income_statement",
        statement,
        date(2026, 3, 31),
        date(2025, 3, 31),
        "quarter",
    )
    assert facts.problems == [
        "empty_label:income_statement:4:1",
        "empty_label:income_statement:4:3",
    ]
    assert len(facts.raw) == len(facts.standard) == 4
    for fact in facts.standard:
        raw = facts.raw[fact["derived_from"][0]]
        assert (raw["page"], raw["period_end"], raw["value"]) == (
            fact["page"],
            fact["period_end"],
            fact["value"],
        )
        assert (
            raw["label"]
            == {"IS.REVENUE": "营业收入", "IS.COST": "营业成本"}[fact["code"]]
        )
    check = validator("FactsIn")
    received = []

    async def receive(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        check.validate(body)
        received.append(body)
        return httpx.Response(201, json={"status": "ready"})

    client = HttpKnowbase(
        "https://knowbase.internal",
        Token(),
        5.0,
        transport=httpx.MockTransport(receive),
    )
    await client.write_facts("grant", FACTS_VERSION, facts.raw, facts.standard)
    assert len(received) == 1


async def test_public_reference_body_conforms_without_losing_demand_identity():
    check = validator("ReferencesIn")
    received = []

    async def receive(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        check.validate(body)
        received.append(body)
        return httpx.Response(200, json={"recorded": 1})

    client = HttpKnowbase(
        "https://knowbase.internal",
        Token(),
        5.0,
        transport=httpx.MockTransport(receive),
    )
    reference = Reference(
        "alice", "demand:demand-1:c1", {"confirmation_id": "confirmed", "item_id": "c1"}
    )
    assert await client.add_references("material", 1, [reference]) == 1
    assert received == [
        {
            "version_no": 1,
            "references": [{"subject": "alice", "ref": "demand:demand-1:c1"}],
        }
    ]
    assert reference.basis == {"confirmation_id": "confirmed", "item_id": "c1"}


@pytest.mark.parametrize(
    "name",
    [
        "000858-q3_report-2019-09-30",
        "300750-semiannual_report-2025-06-30",
        "600009-q1_report-2025-03-31",
        "600519-q3_report-2025-09-30",
        "600519-semiannual_report-2025-06-30",
    ],
)
def test_periodic_fixture_payloads_match_actual_provider_contract(name):
    from test_periodic_statements import fixture, key_of

    from app.application.supply.processing import statements_from_pages

    source, pages = fixture(name)
    facts = statements_from_pages(pages, key_of(source))
    validator("FactsIn").validate(
        {
            "processor_version": FACTS_VERSION,
            "raw": facts.raw,
            "standard": facts.standard,
        }
    )


def test_numeric_line_uses_uniquely_matched_adjacent_source_label():
    facts = StatementFacts()
    statement = ExtractedStatement(
        statement="income_statement",
        start_page=4,
        unit="元",
        unit_source="statement_page",
        side_by_side=False,
        rows=(
            ExtractedRow(4, "归属于母公司股东的净利润", None, None),
            ExtractedRow(4, "", Decimal("42"), Decimal("40")),
        ),
        columns={},
    )
    _one(
        facts,
        "income_statement",
        statement,
        date(2026, 3, 31),
        date(2025, 3, 31),
        "quarter",
    )
    assert facts.problems == []
    assert {r["label"] for r in facts.raw} == {"归属于母公司股东的净利润"}
    assert {r["code"] for r in facts.standard} == {"IS.NET_PROFIT_PARENT"}
    assert [r["value"] for r in facts.standard] == ["42", "40"]
    for standard in facts.standard:
        raw = facts.raw[standard["derived_from"][0]]
        assert raw["row_no"] == 2 and raw["page"] == 4
    validator("FactsIn").validate(
        {
            "processor_version": FACTS_VERSION,
            "raw": facts.raw,
            "standard": facts.standard,
        }
    )


@pytest.mark.parametrize(
    ("year", "page", "parent", "minority", "equity"),
    [
        (
            2023,
            7,
            ("218275691302.62", "197506672396.00"),
            ("8187882900.01", "7458015858.08"),
            ("226463574202.63", "204964688254.08"),
        ),
        (
            2024,
            8,
            ("239731785910.55", "215668571607.43"),
            ("8804982639.98", "7987897687.39"),
            ("248536768550.53", "223656469294.82"),
        ),
        (
            2026,
            8,
            ("270894035676.27", "244637811032.18"),
            ("10241850759.42", "9321442876.89"),
            ("281135886435.69", "253959253909.07"),
        ),
    ],
)
def test_q1_wrapped_parent_equity_does_not_consume_total_equity(
    year, page, parent, minority, equity
):
    """Replay the printed rows from the three affected reports, not inferred totals."""
    rows = (
        ExtractedRow(page, "归属于母公司所有者权益（或", None, None),
        ExtractedRow(page, "", *map(Decimal, parent)),
        ExtractedRow(page, "股东权益）合计", None, None),
        ExtractedRow(page, "少数股东权益", *map(Decimal, minority)),
        ExtractedRow(page, "所有者权益（或股东权益）", None, None),
        ExtractedRow(page, "", *map(Decimal, equity)),
        ExtractedRow(page, "合计", None, None),
    )
    statement = ExtractedStatement(
        "balance_sheet", page, "元", "statement_page", False, rows, {}
    )
    facts = StatementFacts()
    _one(
        facts,
        "balance_sheet",
        statement,
        date(year, 3, 31),
        date(year - 1, 12, 31),
        "instant",
    )
    assert not facts.problems
    assert len(facts.standard) == 6
    for code, values, label, row_no in (
        ("BS.EQUITY_PARENT", parent, "归属于母公司所有者权益（或股东权益）合计", 2),
        ("BS.MINORITY_EQUITY", minority, "少数股东权益", 4),
        ("BS.TOTAL_EQUITY", equity, "所有者权益（或股东权益）合计", 6),
    ):
        found = [r for r in facts.standard if r["code"] == code]
        assert [r["value"] for r in found] == list(values)
        assert [r["period_end"] for r in found] == [
            f"{year}-03-31",
            f"{year - 1}-12-31",
        ]
        for r in found:
            raw = facts.raw[r["derived_from"][0]]
            assert raw["label"] == label
            assert raw["page"] == page and raw["row_no"] == row_no
            assert raw["value"] == r["value"]
    validator("FactsIn").validate(
        {
            "processor_version": FACTS_VERSION,
            "raw": facts.raw,
            "standard": facts.standard,
        }
    )
