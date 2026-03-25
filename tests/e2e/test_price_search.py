"""End-to-end test for price search flow.

Runs a real multi-model search against live websites.
Marked with ``pytest.mark.e2e`` so it is skipped in normal test runs.

Run explicitly with:
    pytest tests/e2e/test_price_search.py -v --timeout=300
"""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse

import pytest

from src.agents.main_agent import MainAgent
from src.backend.db.engine import init_db

# Skip unless explicitly requested (these hit real sites and take minutes)
pytestmark = pytest.mark.e2e


def _extract_domain(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host


@pytest.fixture(autouse=True)
async def _setup_db():
    await init_db()


@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_bosch_appliance_bundle_il():
    """Sanity test from price_search.md.

    Query: BFL523MB1F, HBG578EB3, PVS631HC1E, SMV4HAX21E
    Expected:
    - At least 2 cross-sellers including soferavi.co.il and superelectric.co.il
    - At least 10 unique non-aggregated results per model
    """
    query = "BFL523MB1F, HBG578EB3, PVS631HC1E, SMV4HAX21E"
    model_ids = ["BFL523MB1F", "HBG578EB3", "PVS631HC1E", "SMV4HAX21E"]

    agent = MainAgent(session_id="e2e-price-search")
    state = await agent.process_query(query, language="he", market="il")

    # --- Basic assertions ---
    assert state.status.value == "completed", (
        f"Search did not complete: {state.status.value}\n"
        f"Messages: {state.status_messages}"
    )
    assert len(state.results) > 0, "No results returned"

    # --- Results per model ---
    counts: dict[str, int] = defaultdict(int)
    for r in state.results:
        ptype = (r.product_type or "").upper()
        if ptype:
            counts[ptype] += 1

    for mid in model_ids:
        actual = counts.get(mid, 0)
        assert actual >= 10, (
            f"Model {mid}: expected >= 10 results, got {actual}. "
            f"All model counts: {dict(counts)}"
        )

    # --- Cross-sellers ---
    assert len(state.cross_sellers) >= 2, (
        f"Expected >= 2 cross-sellers, got {len(state.cross_sellers)}. "
        f"Domains: {[cs.domain for cs in state.cross_sellers]}"
    )

    cs_domains: set[str] = set()
    for cs in state.cross_sellers:
        cs_domains.add(cs.domain)
        if cs.url:
            cs_domains.add(_extract_domain(cs.url))

    assert "soferavi.co.il" in cs_domains, (
        f"soferavi.co.il not found in cross-sellers. "
        f"Found domains: {cs_domains}"
    )
    assert "superelectric.co.il" in cs_domains, (
        f"superelectric.co.il not found in cross-sellers. "
        f"Found domains: {cs_domains}"
    )

    # --- Diagnostic output ---
    print(f"\n{'='*60}")
    print(f"Results: {len(state.results)} total")
    for mid in model_ids:
        print(f"  {mid}: {counts.get(mid, 0)} results")
    print(f"Cross-sellers: {len(state.cross_sellers)}")
    for cs in state.cross_sellers:
        total = f"₪{cs.total_price:.0f}" if cs.total_price else "incomplete"
        print(f"  {cs.domain}: {len(cs.products)} models, {total}")
        for model_key, price in cs.prices.items():
            print(f"    {model_key}: ₪{price:.0f}")
    print(f"{'='*60}")
