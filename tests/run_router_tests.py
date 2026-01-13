"""
Manual test runner for message router.
Actually calls the LLM and compares results with expected.

Usage:
    python tests/run_router_tests.py              # Run all tests
    python tests/run_router_tests.py T01          # Run single test
    python tests/run_router_tests.py T01 T05 T10  # Run specific tests
"""

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

TEST_FILE = Path(__file__).parent / "message_types_test.json"
PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "message_router.md"

MODELS = [
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-flash",
]


def load_test_cases() -> list[dict]:
    with open(TEST_FILE, encoding="utf-8") as f:
        return json.load(f)["test_cases"]


def load_prompt() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    # Replace date placeholder with today
    today = date.today().isoformat()
    text = text.replace("YYYY-MM-DD", today)
    return text


async def call_router(input_data: dict) -> dict:
    """Call LLM with router prompt and return parsed JSON."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY in environment")
    
    prompt = load_prompt()
    user_content = json.dumps(input_data, ensure_ascii=False)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for model in MODELS:
            try:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0,
                        "max_tokens": 1500,
                    }
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                return json.loads(raw)
            except Exception as e:
                print(f"  Model {model} failed: {e}")
                continue
        
        raise RuntimeError("All models failed")


def check_contains(actual: str | None, pattern: str) -> bool:
    """Check if actual contains pattern (supports AND/OR)."""
    if actual is None:
        return False
    actual_lower = str(actual).lower()
    
    if pattern.startswith("contains:"):
        pattern = pattern[9:].strip()
    
    if " AND " in pattern:
        return all(p.strip().lower() in actual_lower for p in pattern.split(" AND "))
    if " OR " in pattern:
        return any(p.strip().lower() in actual_lower for p in pattern.split(" OR "))
    return pattern.lower() in actual_lower


def validate_result(actual: dict, expected: dict) -> tuple[bool, list[str]]:
    """Validate actual output against expected. Returns (passed, errors)."""
    errors = []
    
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
        if exp is None and act is not None:
            errors.append(f"response: expected None, got '{act}'")
        elif exp and exp.startswith("contains:") and not check_contains(act, exp):
            errors.append(f"response: expected {exp}, got '{act}'")
    
    # Check message_en
    if "message_en" in expected:
        exp = expected["message_en"]
        act = actual.get("message_en", "")
        if exp.startswith("contains:") and not check_contains(act, exp):
            errors.append(f"message_en: expected {exp}, got '{act}'")
    
    # Check context_files
    if "context_files_must_include" in expected:
        actual_files = " ".join(actual.get("context_files", [])).lower()
        for pattern in expected["context_files_must_include"]:
            if pattern.lower() not in actual_files:
                errors.append(f"context_files: missing '{pattern}'")
    
    # Check question_for_next_llm
    if "question_for_next_llm" in expected:
        exp = expected["question_for_next_llm"]
        act = actual.get("question_for_next_llm")
        if exp.startswith("contains:") and not check_contains(act, exp):
            errors.append(f"question_for_next_llm: expected {exp}, got '{act}'")
    
    # Check file_updates
    if "file_updates_must_include" in expected:
        actual_updates = actual.get("file_updates", [])
        for exp_update in expected["file_updates_must_include"]:
            exp_file = exp_update.get("file", "")
            found = False
            for upd in actual_updates:
                if exp_file in upd.get("file", ""):
                    found = True
                    # Check what_contains if specified
                    what_contains = exp_update.get("what_contains")
                    if what_contains:
                        what_str = json.dumps(upd.get("what", ""), ensure_ascii=False)
                        if not check_contains(what_str, what_contains):
                            errors.append(f"file_updates[{exp_file}]: what should contain '{what_contains}'")
                    break
            if not found:
                errors.append(f"file_updates: missing update for '{exp_file}'")
    
    return len(errors) == 0, errors


async def run_test(test_case: dict) -> dict:
    """Run single test and return result."""
    test_id = test_case["id"]
    name = test_case["name"]
    
    print(f"\n{'='*60}")
    print(f"TEST {test_id}: {name}")
    print(f"{'='*60}")
    
    print(f"\n📥 INPUT:")
    print(json.dumps(test_case["input"], indent=2, ensure_ascii=False))
    
    try:
        actual = await call_router(test_case["input"])
        
        print(f"\n📤 OUTPUT:")
        print(json.dumps(actual, indent=2, ensure_ascii=False))
        
        passed, errors = validate_result(actual, test_case["expected"])
        
        print(f"\n📋 EXPECTED:")
        print(json.dumps(test_case["expected"], indent=2, ensure_ascii=False))
        
        if passed:
            print(f"\n✅ PASSED")
        else:
            print(f"\n❌ FAILED:")
            for err in errors:
                print(f"   - {err}")
        
        return {"id": test_id, "name": name, "passed": passed, "errors": errors, "actual": actual}
    
    except Exception as e:
        print(f"\n💥 ERROR: {e}")
        return {"id": test_id, "name": name, "passed": False, "errors": [str(e)], "actual": None}


async def main(test_ids: list[str] | None = None):
    """Run tests. If test_ids provided, run only those. Otherwise run all."""
    all_tests = load_test_cases()
    
    if test_ids:
        tests = [t for t in all_tests if t["id"] in test_ids]
        if not tests:
            print(f"No tests found matching: {test_ids}")
            print(f"Available: {[t['id'] for t in all_tests]}")
            return
    else:
        tests = all_tests
    
    print(f"\n🚀 Running {len(tests)} test(s)...")
    
    results = []
    for tc in tests:
        result = await run_test(tc)
        results.append(result)
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    
    print(f"\n✅ Passed: {len(passed)}/{len(results)}")
    if passed:
        for r in passed:
            print(f"   {r['id']}: {r['name']}")
    
    if failed:
        print(f"\n❌ Failed: {len(failed)}/{len(results)}")
        for r in failed:
            print(f"   {r['id']}: {r['name']}")
            for err in r["errors"]:
                print(f"      - {err}")
    
    print(f"\n📊 Score: {len(passed)}/{len(results)} ({100*len(passed)/len(results):.0f}%)")


if __name__ == "__main__":
    # Get test IDs from command line args
    test_ids = sys.argv[1:] if len(sys.argv) > 1 else None
    
    if test_ids and test_ids[0] == "--list":
        tests = load_test_cases()
        print("Available tests:")
        for t in tests:
            print(f"  {t['id']}: {t['name']}")
    else:
        asyncio.run(main(test_ids))
