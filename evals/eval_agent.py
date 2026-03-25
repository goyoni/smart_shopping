"""Agent evaluation runner.

Loads test cases, calls the agent directly, and judges results against
expected outcomes.  Generates JSON reports in evals/results/.

Usage:
    python evals/eval_agent.py                  # run all test cases
    python evals/eval_agent.py price_search     # run only price_search.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def load_test_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def _extract_domain(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host


def evaluate_result(test_case: dict, result: dict) -> dict:
    """Judge whether a result meets the test case expectations."""
    expected = test_case["expected"]
    passed = True
    reasons: list[str] = []

    results_list = result.get("results", [])
    cross_sellers = result.get("cross_sellers", [])

    # --- min total results ---
    if "min_results" in expected:
        actual_count = len(results_list)
        if actual_count < expected["min_results"]:
            passed = False
            reasons.append(
                f"Expected >= {expected['min_results']} results, got {actual_count}"
            )

    # --- min results per model ---
    if "min_results_per_model" in expected and "model_ids" in expected:
        min_per = expected["min_results_per_model"]
        model_ids = [m.lower() for m in expected["model_ids"]]
        counts: dict[str, int] = defaultdict(int)
        for r in results_list:
            ptype = (r.get("product_type") or "").lower()
            if ptype in model_ids:
                counts[ptype] += 1
        for mid in model_ids:
            actual = counts.get(mid, 0)
            if actual < min_per:
                passed = False
                reasons.append(
                    f"Model {mid.upper()}: expected >= {min_per} results, got {actual}"
                )

    # --- min cross-sellers ---
    if "min_cross_sellers" in expected:
        if len(cross_sellers) < expected["min_cross_sellers"]:
            passed = False
            reasons.append(
                f"Expected >= {expected['min_cross_sellers']} cross-sellers, "
                f"got {len(cross_sellers)}"
            )

    # --- required cross-seller domains ---
    if "required_cross_seller_domains" in expected:
        cs_domains = set()
        for cs in cross_sellers:
            cs_domains.add(cs.get("domain", ""))
            # Also check URL-based domain
            url = cs.get("url", "")
            if url:
                cs_domains.add(_extract_domain(url))
        for domain in expected["required_cross_seller_domains"]:
            if domain not in cs_domains:
                passed = False
                reasons.append(f"Required cross-seller domain missing: {domain}")

    # --- required criteria keys ---
    if "required_criteria" in expected:
        for key in expected["required_criteria"]:
            has_key = any(
                key in r.get("criteria", {}) for r in results_list
            )
            if not has_key:
                reasons.append(f"No result has criteria key: {key}")

    return {
        "test_id": test_case["id"],
        "test_name": test_case["name"],
        "passed": passed,
        "reasons": reasons,
        "result_count": len(results_list),
        "cross_seller_count": len(cross_sellers),
    }


async def run_test_case(tc: dict) -> dict:
    """Execute a single test case against the live agent."""
    from src.backend.db.engine import init_db
    from src.agents.main_agent import MainAgent

    await init_db()

    language = tc.get("language", "en")
    market = tc.get("market", "us")

    agent = MainAgent(session_id=f"eval-{tc['id']}")
    state = await agent.process_query(tc["input"], language=language, market=market)

    return {
        "results": [r.model_dump() for r in state.results],
        "cross_sellers": [c.model_dump() for c in state.cross_sellers],
        "status": state.status.value,
        "status_messages": state.status_messages,
    }


async def async_main(filter_name: str | None = None) -> None:
    test_dir = Path(__file__).parent / "test_cases"
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    all_results = []
    for test_file in sorted(test_dir.glob("*.json")):
        if filter_name and filter_name not in test_file.stem:
            continue

        test_cases = load_test_cases(test_file)
        for tc in test_cases:
            print(f"Running: {tc['id']} - {tc['name']}...")
            try:
                result = await run_test_case(tc)
            except Exception as exc:
                result = {"results": [], "cross_sellers": [], "error": str(exc)}
                print(f"  ERROR: {exc}")

            eval_result = evaluate_result(tc, result)
            # Attach raw output for debugging
            eval_result["raw_status_messages"] = result.get("status_messages", [])
            all_results.append(eval_result)

            status = "PASS" if eval_result["passed"] else "FAIL"
            print(f"  {status} — {eval_result['result_count']} results, "
                  f"{eval_result['cross_seller_count']} cross-sellers")
            for reason in eval_result["reasons"]:
                print(f"    - {reason}")

    # Write results
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = results_dir / f"eval_{timestamp}.json"
    latest_path = results_dir / "latest.json"

    for path in (output_path, latest_path):
        with open(path, "w") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)

    passed = sum(1 for r in all_results if r["passed"])
    total = len(all_results)
    print(f"\nEvaluation complete: {passed}/{total} passed")
    print(f"Results written to: {output_path}")

    if passed < total:
        sys.exit(1)


def main() -> None:
    filter_name = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(async_main(filter_name))


if __name__ == "__main__":
    main()
