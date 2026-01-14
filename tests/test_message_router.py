"""
Router contract tests (no network).

These tests validate:
- the test case fixture shape (tests/message_types_test.json)
- the output validation helpers used by manual/integration runners

LLM calls are intentionally not part of the default test suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = Path(__file__).parent / "message_types_test.json"


def load_test_cases() -> list[dict[str, Any]]:
    with TEST_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("test_cases")
    if not isinstance(cases, list):
        raise ValueError("message_types_test.json: missing/invalid test_cases list")
    return cases


def check_contains(actual: str | None, expected_pattern: str) -> bool:
    """
    Check if actual string contains expected pattern.
    Pattern format: "contains: X AND Y" or "contains: X OR Y"
    """
    if actual is None:
        return False

    actual_lower = actual.lower()

    # Remove "contains:" prefix if present
    pattern = expected_pattern
    if pattern.startswith("contains:"):
        pattern = pattern[9:].strip()

    # Handle AND
    if " AND " in pattern:
        parts = pattern.split(" AND ")
        return all(p.strip().lower() in actual_lower for p in parts)

    # Handle OR
    if " OR " in pattern:
        parts = pattern.split(" OR ")
        return any(p.strip().lower() in actual_lower for p in parts)

    # Simple contains
    return pattern.lower() in actual_lower


def check_context_files(actual_files: list[str], expected_patterns: list[str]) -> tuple[bool, str]:
    """Check if actual context files match expected patterns."""
    if not expected_patterns:
        return True, ""

    errors = []
    actual_joined = " ".join(str(x) for x in (actual_files or [])).lower()

    for pattern in expected_patterns:
        if pattern.lower() not in actual_joined:
            errors.append(f"Missing context file matching: {pattern}")

    if errors:
        return False, "; ".join(errors)
    return True, ""


def validate_output(actual: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate router output against expected values.
    Returns (passed, list of errors).
    """
    errors = []

    # Check message_en
    if "message_en" in expected:
        exp = expected["message_en"]
        act = actual.get("message_en", "")
        if isinstance(exp, str) and exp.startswith("contains:"):
            if not check_contains(act, exp):
                errors.append(f"message_en: expected {exp}, got '{act}'")
        elif act != exp:
            errors.append(f"message_en: expected '{exp}', got '{act}'")

    # Check needs_context
    if "needs_context" in expected:
        if actual.get("needs_context") != expected["needs_context"]:
            errors.append(f"needs_context: expected {expected['needs_context']}, got {actual.get('needs_context')}")

    # Check response
    if "response" in expected:
        exp = expected["response"]
        act = actual.get("response")
        if exp is None:
            if act is not None:
                errors.append(f"response: expected None, got '{act}'")
        elif isinstance(exp, str) and exp.startswith("contains:"):
            if not check_contains(act, exp):
                errors.append(f"response: expected {exp}, got '{act}'")

    if expected.get("response_not_null") is True:
        act = actual.get("response")
        if not isinstance(act, str) or not act.strip():
            errors.append(f"response: expected non-empty string, got '{act}'")
    
    # Check context_files
    if "context_files_must_include" in expected:
        passed, err = check_context_files(
            actual.get("context_files", []),
            expected["context_files_must_include"]
        )
        if not passed:
            errors.append(f"context_files: {err}")

    # Check question_for_next_llm
    if "question_for_next_llm" in expected:
        exp = expected["question_for_next_llm"]
        act = actual.get("question_for_next_llm", "")
        if isinstance(exp, str) and exp.startswith("contains:"):
            if not check_contains(act, exp):
                errors.append(f"question_for_next_llm: expected {exp}, got '{act}'")

    return len(errors) == 0, errors


def _app_pages_filenames() -> set[str]:
    app_pages = REPO_ROOT / "data" / "app_pages"
    if not app_pages.exists():
        return set()
    return {p.name for p in app_pages.glob("*.md")}


def test_message_types_test_cases_are_well_formed():
    cases = load_test_cases()
    assert cases, "message_types_test.json has no test_cases"

    app_pages_names = _app_pages_filenames()
    for tc in cases:
        assert isinstance(tc, dict)
        assert isinstance(tc.get("id"), str) and tc["id"].strip()
        assert isinstance(tc.get("name"), str) and tc["name"].strip()
        assert isinstance(tc.get("input"), dict)
        assert isinstance(tc.get("expected"), dict)

        expected = tc["expected"]
        assert "needs_context" in expected
        assert isinstance(expected["needs_context"], bool)

        # Ensure context patterns reference real files (exact path or filename).
        patterns = expected.get("context_files_must_include") or []
        assert isinstance(patterns, list)
        for pat in patterns:
            assert isinstance(pat, str) and pat.strip()
            if "/" in pat:
                assert (REPO_ROOT / pat).exists(), f"Missing file referenced by test: {pat}"
            elif pat.endswith(".md"):
                assert pat in app_pages_names, f"Unknown app_pages markdown referenced by test: {pat}"

        # Ensure response expectations aren't contradictory.
        if "response" in expected and expected.get("response") is None and expected.get("response_not_null") is True:
            raise AssertionError(f"{tc['id']}: expected response both null and not null")


async def run_single_test(test_case: dict, route_fn) -> dict:
    """
    Run a single test case.
    
    Args:
        test_case: Test case from JSON
        route_fn: Async function that takes input dict and returns router output
    
    Returns:
        Dict with test_id, passed, errors, actual_output
    """
    try:
        actual = await route_fn(test_case["input"])
        passed, errors = validate_output(actual, test_case["expected"])
        return {
            "test_id": test_case["id"],
            "name": test_case["name"],
            "passed": passed,
            "errors": errors,
            "actual": actual,
        }
    except Exception as e:
        return {
            "test_id": test_case["id"],
            "name": test_case["name"],
            "passed": False,
            "errors": [f"Exception: {e}"],
            "actual": None,
        }


async def run_all_tests(route_fn) -> list[dict]:
    """
    Run all test cases.
    
    Args:
        route_fn: Async function that takes input dict and returns router output
    
    Returns:
        List of test results
    """
    test_cases = load_test_cases()
    results = []
    
    for tc in test_cases:
        result = await run_single_test(tc, route_fn)
        results.append(result)
        
        # Print progress
        status = "✓" if result["passed"] else "✗"
        print(f"{status} {result['test_id']}: {result['name']}")
        if not result["passed"]:
            for err in result["errors"]:
                print(f"    - {err}")
    
    # Summary
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed ({100*passed/total:.1f}%)")
    
    return results


def print_test_case(test_id: str):
    """Print a single test case for manual inspection."""
    cases = load_test_cases()
    for tc in cases:
        if tc["id"] == test_id:
            print(f"Test: {tc['id']} - {tc['name']}")
            print(f"\nInput:")
            print(json.dumps(tc["input"], indent=2, ensure_ascii=False))
            print(f"\nExpected:")
            print(json.dumps(tc["expected"], indent=2, ensure_ascii=False))
            return
    print(f"Test {test_id} not found")


if __name__ == "__main__":
    # Manual test runner - print all test cases
    cases = load_test_cases()
    print(f"Loaded {len(cases)} test cases:\n")
    for tc in cases:
        print(f"  {tc['id']}: {tc['name']}")
    
    print("\n" + "="*50)
    print("To run tests with pytest: pytest tests/test_message_router.py -v")
    print("To inspect a test: python -c \"from tests.test_message_router import print_test_case; print_test_case('T01')\"")
