"""
Test runner for message router.
Loads test cases from message_types_test.json and validates router output.
"""

import json
import os
import re
from pathlib import Path

import pytest

# Will import once implemented
# from group_chat_telegram_ai.message_router import route_message


TEST_FILE = Path(__file__).parent / "message_types_test.json"


def load_test_cases() -> list[dict]:
    """Load test cases from JSON file."""
    with open(TEST_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data["test_cases"]


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


def check_file_updates(actual_updates: list[dict], expected_updates: list[dict]) -> tuple[bool, str]:
    """
    Check if actual file updates match expected.
    Returns (passed, error_message).
    """
    if not expected_updates:
        return True, ""
    
    errors = []
    
    for expected in expected_updates:
        expected_file = expected.get("file", "")
        found = False
        
        for actual in actual_updates:
            actual_file = actual.get("file", "")
            
            # Check if file path matches (partial match allowed)
            if expected_file in actual_file or actual_file.endswith(expected_file):
                found = True
                
                # Check what_contains if specified
                what_contains = expected.get("what_contains")
                if what_contains:
                    actual_what = str(actual.get("what", ""))
                    if not check_contains(actual_what, f"contains: {what_contains}"):
                        errors.append(f"File {expected_file}: 'what' should contain '{what_contains}', got '{actual_what}'")
                
                # Check action if specified
                expected_action = expected.get("action")
                if expected_action and actual.get("action") != expected_action:
                    errors.append(f"File {expected_file}: action should be '{expected_action}', got '{actual.get('action')}'")
                
                break
        
        if not found:
            errors.append(f"Missing update for file: {expected_file}")
    
    if errors:
        return False, "; ".join(errors)
    return True, ""


def check_context_files(actual_files: list[str], expected_patterns: list[str]) -> tuple[bool, str]:
    """Check if actual context files match expected patterns."""
    if not expected_patterns:
        return True, ""
    
    errors = []
    actual_joined = " ".join(actual_files).lower()
    
    for pattern in expected_patterns:
        if pattern.lower() not in actual_joined:
            errors.append(f"Missing context file matching: {pattern}")
    
    if errors:
        return False, "; ".join(errors)
    return True, ""


def validate_output(actual: dict, expected: dict) -> tuple[bool, list[str]]:
    """
    Validate router output against expected values.
    Returns (passed, list of errors).
    """
    errors = []
    
    # Check message_en
    if "message_en" in expected:
        exp = expected["message_en"]
        act = actual.get("message_en", "")
        if exp.startswith("contains:"):
            if not check_contains(act, exp):
                errors.append(f"message_en: expected {exp}, got '{act}'")
        elif act != exp:
            errors.append(f"message_en: expected '{exp}', got '{act}'")
    
    # Check intent
    if "intent" in expected:
        if actual.get("intent") != expected["intent"]:
            errors.append(f"intent: expected '{expected['intent']}', got '{actual.get('intent')}'")
    
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
        if exp.startswith("contains:"):
            if not check_contains(act, exp):
                errors.append(f"question_for_next_llm: expected {exp}, got '{act}'")
    
    # Check file_updates
    if "file_updates_must_include" in expected:
        passed, err = check_file_updates(
            actual.get("file_updates", []),
            expected["file_updates_must_include"]
        )
        if not passed:
            errors.append(f"file_updates: {err}")
    
    return len(errors) == 0, errors


# Generate test IDs for pytest
def get_test_ids():
    cases = load_test_cases()
    return [f"{c['id']}_{c['name'].replace(' ', '_')}" for c in cases]


@pytest.fixture
def test_cases():
    return load_test_cases()


class TestMessageRouter:
    """Test class for message router."""
    
    @pytest.mark.parametrize("test_case", load_test_cases(), ids=get_test_ids())
    def test_route_message(self, test_case):
        """Test single message routing."""
        # Skip until router is implemented
        pytest.skip("Router not implemented yet - use test_manual_router for manual testing")
        
        # from group_chat_telegram_ai.message_router import route_message
        # 
        # result = route_message(test_case["input"])
        # passed, errors = validate_output(result, test_case["expected"])
        # 
        # assert passed, f"Test {test_case['id']} failed:\n" + "\n".join(f"  - {e}" for e in errors)


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
