"""
Test runner for message router.
Tests handle_message.py with data from message_types_test.json.

Usage:
    python tests/run_router_tests.py                    # Run all tests with default model
    python tests/run_router_tests.py --model 1         # Run with model #1
    python tests/run_router_tests.py --model 5 T01     # Run T01 with model #5
    python tests/run_router_tests.py --list-models     # Show available models
    python tests/run_router_tests.py --list            # Show available tests
    python tests/run_router_tests.py T01 T05 T10       # Run specific tests
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from group_chat_telegram_ai.handle_message import (
    AVAILABLE_MODELS,
    RouterResult,
    route_message,
)

TEST_FILE = Path(__file__).parent / "message_types_test.json"
LOG_FILE = Path(__file__).parent / "router_test_results.json"


def load_test_cases() -> list[dict]:
    with open(TEST_FILE, encoding="utf-8") as f:
        return json.load(f)["test_cases"]


def print_models():
    """Print available models with prices."""
    print("\nAvailable models:")
    print("-" * 80)
    print(f"{'#':<3} {'Model ID':<45} {'Input':<10} {'Output':<10} {'Context'}")
    print("-" * 80)
    for i, m in enumerate(AVAILABLE_MODELS, 1):
        ctx = f"{m.context_size:,}"
        print(f"{i:<3} {m.id:<45} ${m.input_price:<9.3f} ${m.output_price:<9.2f} {ctx}")
    print("-" * 80)
    print("\nUsage: --model <number>  (e.g., --model 1)")


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
        elif exp and str(exp).startswith("contains:") and not check_contains(act, exp):
            errors.append(f"response: expected {exp}, got '{act}'")
    
    # Check message_en
    if "message_en" in expected:
        exp = expected["message_en"]
        act = actual.get("message_en", "")
        if str(exp).startswith("contains:") and not check_contains(act, exp):
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
        if str(exp).startswith("contains:") and not check_contains(act, exp):
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
                    what_contains = exp_update.get("what_contains")
                    if what_contains:
                        what_str = json.dumps(upd.get("what", ""), ensure_ascii=False)
                        if not check_contains(what_str, what_contains):
                            errors.append(f"file_updates[{exp_file}]: what should contain '{what_contains}'")
                    break
            if not found:
                errors.append(f"file_updates: missing update for '{exp_file}'")
    
    return len(errors) == 0, errors


async def run_test(test_case: dict, model_id: str | None) -> dict:
    """Run single test and return result."""
    test_id = test_case["id"]
    name = test_case["name"]
    
    print(f"\n{'='*70}")
    print(f"TEST {test_id}: {name}")
    print(f"{'='*70}")
    
    print(f"\n📥 INPUT:")
    print(json.dumps(test_case["input"], indent=2, ensure_ascii=False))
    
    start_time = datetime.now()
    
    try:
        result: RouterResult = await route_message(test_case["input"], model=model_id)
        
        duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        
        if result.error:
            print(f"\n💥 ERROR: {result.error}")
            return {
                "test_id": test_id,
                "name": name,
                "status": "error",
                "error": result.error,
                "input": test_case["input"],
                "output": None,
                "expected": test_case["expected"],
                "model": model_id or "default",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0,
                "duration_ms": duration_ms,
                "errors": [result.error],
            }
        
        actual = result.output
        
        print(f"\n📤 OUTPUT:")
        print(json.dumps(actual, indent=2, ensure_ascii=False))
        
        print(f"\n📋 EXPECTED:")
        print(json.dumps(test_case["expected"], indent=2, ensure_ascii=False))
        
        passed, errors = validate_result(actual, test_case["expected"])
        
        print(f"\n📊 STATS: model={result.model}, tokens={result.input_tokens}+{result.output_tokens}, cost=${result.cost:.6f}, time={duration_ms:.0f}ms")
        
        if passed:
            print(f"\n✅ PASSED")
            status = "pass"
        else:
            print(f"\n❌ FAILED:")
            for err in errors:
                print(f"   - {err}")
            status = "fail"
        
        return {
            "test_id": test_id,
            "name": name,
            "status": status,
            "input": test_case["input"],
            "output": actual,
            "expected": test_case["expected"],
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": result.cost,
            "duration_ms": duration_ms,
            "errors": errors if not passed else [],
        }
    
    except Exception as e:
        duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        print(f"\n💥 EXCEPTION: {e}")
        return {
            "test_id": test_id,
            "name": name,
            "status": "error",
            "error": str(e),
            "input": test_case["input"],
            "output": None,
            "expected": test_case["expected"],
            "model": model_id or "default",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0,
            "duration_ms": duration_ms,
            "errors": [str(e)],
        }


async def run_all_tests(test_ids: list[str] | None, model_id: str | None):
    """Run tests and save results to log file."""
    all_tests = load_test_cases()
    
    if test_ids:
        tests = [t for t in all_tests if t["id"] in test_ids]
        if not tests:
            print(f"No tests found matching: {test_ids}")
            print(f"Available: {[t['id'] for t in all_tests]}")
            return
    else:
        tests = all_tests
    
    model_name = "default (with fallback)"
    if model_id:
        for m in AVAILABLE_MODELS:
            if m.id == model_id:
                model_name = f"{m.name} ({m.id})"
                break
    
    print(f"\n🚀 Running {len(tests)} test(s) with model: {model_name}")
    
    results = []
    for tc in tests:
        result = await run_test(tc, model_id)
        results.append(result)
    
    # Calculate totals
    total_cost = sum(r["cost_usd"] for r in results)
    total_input_tokens = sum(r["input_tokens"] for r in results)
    total_output_tokens = sum(r["output_tokens"] for r in results)
    total_duration = sum(r["duration_ms"] for r in results)
    
    passed = [r for r in results if r["status"] == "pass"]
    failed = [r for r in results if r["status"] == "fail"]
    errors = [r for r in results if r["status"] == "error"]
    
    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    
    print(f"\n✅ Passed: {len(passed)}/{len(results)}")
    if passed:
        for r in passed:
            print(f"   {r['test_id']}: {r['name']}")
    
    if failed:
        print(f"\n❌ Failed: {len(failed)}/{len(results)}")
        for r in failed:
            print(f"   {r['test_id']}: {r['name']}")
            for err in r["errors"]:
                print(f"      - {err}")
    
    if errors:
        print(f"\n💥 Errors: {len(errors)}/{len(results)}")
        for r in errors:
            print(f"   {r['test_id']}: {r['name']} - {r.get('error', 'unknown')}")
    
    print(f"\n📊 TOTALS:")
    print(f"   Score: {len(passed)}/{len(results)} ({100*len(passed)/len(results):.0f}%)")
    print(f"   Tokens: {total_input_tokens:,} input + {total_output_tokens:,} output = {total_input_tokens + total_output_tokens:,} total")
    print(f"   Cost: ${total_cost:.6f}")
    print(f"   Time: {total_duration/1000:.1f}s")
    
    # Save log file
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "model": model_id or "default",
        "model_name": model_name,
        "summary": {
            "total": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "errors": len(errors),
            "score_percent": 100 * len(passed) / len(results) if results else 0,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_usd": total_cost,
            "total_duration_ms": total_duration,
        },
        "results": results,
    }
    
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n📁 Results saved to: {LOG_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Test message router")
    parser.add_argument("tests", nargs="*", help="Test IDs to run (e.g., T01 T05)")
    parser.add_argument("--model", "-m", type=int, help="Model number (see --list-models)")
    parser.add_argument("--list-models", action="store_true", help="List available models")
    parser.add_argument("--list", action="store_true", help="List available tests")
    
    args = parser.parse_args()
    
    if args.list_models:
        print_models()
        return
    
    if args.list:
        tests = load_test_cases()
        print("\nAvailable tests:")
        for t in tests:
            print(f"  {t['id']}: {t['name']}")
        return
    
    model_id = None
    if args.model:
        if args.model < 1 or args.model > len(AVAILABLE_MODELS):
            print(f"Invalid model number. Use 1-{len(AVAILABLE_MODELS)}")
            print_models()
            return
        model_id = AVAILABLE_MODELS[args.model - 1].id
    
    test_ids = args.tests if args.tests else None
    
    asyncio.run(run_all_tests(test_ids, model_id))


if __name__ == "__main__":
    main()
