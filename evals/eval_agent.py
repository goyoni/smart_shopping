"""Agent evaluation runner.

Loads test cases, calls the API, and judges results against expected outcomes.
Generates HTML reports in evals/results/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_test_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def evaluate_result(test_case: dict, result: dict) -> dict:
    """Judge whether a result meets the test case expectations."""
    expected = test_case["expected"]
    passed = True
    reasons = []

    if "min_results" in expected:
        actual_count = len(result.get("results", []))
        if actual_count < expected["min_results"]:
            passed = False
            reasons.append(
                f"Expected >= {expected['min_results']} results, got {actual_count}"
            )

    return {
        "test_id": test_case["id"],
        "test_name": test_case["name"],
        "passed": passed,
        "reasons": reasons,
    }


def main() -> None:
    test_dir = Path(__file__).parent / "test_cases"
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)

    all_results = []
    for test_file in test_dir.glob("*.json"):
        test_cases = load_test_cases(test_file)
        for tc in test_cases:
            # TODO: Call the actual API and get results
            mock_result: dict = {"results": []}
            eval_result = evaluate_result(tc, mock_result)
            all_results.append(eval_result)

    # Write results
    output_path = results_dir / "latest.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)

    passed = sum(1 for r in all_results if r["passed"])
    total = len(all_results)
    print(f"Evaluation complete: {passed}/{total} passed")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
