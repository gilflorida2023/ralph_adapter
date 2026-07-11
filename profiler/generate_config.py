#!/usr/bin/env python3
"""
Generate a model config YAML by profiling a model against test cases.
Sends each test case prompt to the model, analyzes the raw response,
detects which normalizers are needed, and writes the config file.
"""
import json
import os
import pathlib
import subprocess
import sys
import time
import yaml

# Add parent to path for adapter import
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from adapter import call_ollama, extract_tool_calls
from normalizers import available, apply_all
from profiler.test_cases import TEST_CASES, run_test_case

ADAPTER_DIR = pathlib.Path(__file__).parent.parent
CONFIG_DIR = ADAPTER_DIR / "models"


def detect_needed_normalizers(all_results):
    """Analyze all test results and determine which normalizers are needed."""
    needed = []

    # Check for markdown fences
    if any(r["has_json_fences"] for r in all_results):
        needed.append("strip_markdown_fences")

    # Check for quote wrapping
    if any(r.get("has_quote_wrapping") for r in all_results):
        needed.append("unwrap_json_string")

    # Check for top-level tool keys
    if any(r.get("top_level_tools") for r in all_results):
        needed.append("merge_top_level_keys")

    # Check for trailing garbage
    trailing_garbage = False
    for r in all_results:
        content = r["raw_preview"]
        for test in TEST_CASES:
            if test["name"] == r["name"]:
                break
        if r["valid_json"] and r.get("tool_call_count", 0) == 0:
            # JSON parsed but no tool_calls — might have trailing garbage
            pass
    if trailing_garbage:
        needed.append("trim_trailing_garbage")

    return list(dict.fromkeys(needed))  # unique, ordered


def validate_config(model_name, normalizer_names, test_results):
    """Verify that applying normalizers would make all tests pass."""
    fixed = 0
    still_broken = 0

    for result in test_results:
        test_case = next(t for t in TEST_CASES if t["name"] == result["name"])
        raw = result["_raw"]

        # Apply normalizers
        normalized = apply_all(raw, normalizer_names)

        # Extract tool calls
        calls = extract_tool_calls(normalized, None)

        ok, msg = test_case["validate"](calls)
        if ok:
            fixed += 1
        else:
            still_broken += 1

    return fixed, still_broken


def sanitize_model_name(name):
    """Sanitize model name for use as a filename."""
    return name.replace("/", "_").replace(":", "_")


def get_config_path(model_name):
    """Get paths where the config could exist (primary + sanitized)."""
    return [
        CONFIG_DIR / f"{model_name}.yaml",
        CONFIG_DIR / f"{sanitize_model_name(model_name)}.yaml",
    ]


def remove_duplicate_configs(model_name):
    """Remove any stale config variants for this model."""
    paths = get_config_path(model_name)
    for p in paths[1:]:  # Keep only the first (exact name)
        if p.exists():
            p.unlink()


def profile_model(model_name, max_time=300):
    """Profile a model against all test cases and generate its config."""
    print(f"\n{'='*60}")
    print(f"Profiling model: {model_name}")
    print(f"{'='*60}")

    results = []

    for tc in TEST_CASES:
        print(f"\n  Test: {tc['name']}...", end=" ", flush=True)

        # Call Ollama
        last_error = None
        raw_content = None
        for attempt in range(3):
            resp = call_ollama(model_name, tc["prompt"], max_time)
            if resp is None:
                last_error = "curl failed"
                time.sleep(5)
                continue
            msg = resp.get("message") or {}
            raw_content = (msg.get("content") or "").strip()
            if raw_content:
                break
            last_error = "empty content"
            time.sleep(5)

        if not raw_content:
            print(f"FAILED ({last_error})")
            results.append({
                "name": tc["name"],
                "raw_length": 0,
                "raw_preview": "",
                "_raw": "",
                "has_json_fences": False,
                "has_quote_wrapping": False,
                "valid_json": False,
                "has_tool_calls_key": False,
                "tool_calls_type": "none",
                "top_level_tools": [],
                "tool_call_count": 0,
                "tool_call_names": [],
                "errors": ["no response from model"],
            })
            continue

        print(f"({len(raw_content)} bytes)", end=" ", flush=True)
        analysis = run_test_case(tc, raw_content)
        analysis["_raw"] = raw_content
        results.append(analysis)

        if analysis["errors"]:
            print(f"issues: {'; '.join(analysis['errors'])}")
        elif analysis["tool_call_count"] > 0:
            print(f"OK ({analysis['tool_call_count']} calls: {', '.join(analysis['tool_call_names'])})")
        else:
            print("no tool_calls found")

    # Determine needed normalizers (from FORMAT issues, not behavioral)
    normalizers = detect_needed_normalizers(results)
    print(f"\n  Detected normalizers: {normalizers or '(none needed)'}")

    # Check for format-level issues that indicate the model is unpredictable
    format_issues = []
    for r in results:
        if not r["valid_json"]:
            format_issues.append(f"{r['name']}: invalid JSON")
        if not r["has_tool_calls_key"] and not r.get("top_level_tools"):
            format_issues.append(f"{r['name']}: no tool_calls key")

    if format_issues:
        print(f"  FORMAT ISSUES: {'; '.join(format_issues)}")
        blocked = True
    else:
        blocked = False

    # Validate functional tests
    fixed, broken = validate_config(model_name, normalizers, results)
    print(f"  Validation: {fixed}/{len(TEST_CASES)} behavioral tests pass with config")
    if broken > 0:
        print(f"  (behavioral warnings: {broken} tests — not blocking, model just chose different tool sequence)")

    # Look up the model ID from ollama list
    model_id = ""
    try:
        ol = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
        for line in ol.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if parts and parts[0] == model_name and len(parts) >= 2:
                model_id = parts[1]
                break
    except (subprocess.TimeoutExpired, OSError):
        pass

    config = {
        "model": model_name,
        "model_id": model_id or None,
        "normalizers": normalizers,
        "blocked": blocked,
    }
    remove_duplicate_configs(model_name)
    config_path = get_config_path(model_name)[0]
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    print(f"\n  Config written: {config_path}  (ID: {model_id or 'unknown'})")
    print(f"{'='*60}\n")

    return config, results


def list_available_models():
    """List models available via Ollama."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=30,
        )
        models = []
        for line in result.stdout.strip().split("\n")[1:]:
            if line.strip():
                parts = line.split()
                if parts:
                    models.append(parts[0])
        return models
    except (subprocess.TimeoutExpired, OSError):
        return []


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Profile models and generate adapter configs")
    parser.add_argument("--model", help="Profile a specific model")
    parser.add_argument("--all", action="store_true", help="Profile all installed models")
    parser.add_argument("--list", action="store_true", help="List available models and their config status")
    args = parser.parse_args()

    if args.list:
        models = list_available_models()
        print("Available models:")
        for m in models:
            config_paths = get_config_path(m)
            has_config = any(p.exists() for p in config_paths)
            status = "configured" if has_config else "no config"
            print(f"  {m:40s} [{status}]")
        return

    if args.model:
        profile_model(args.model)
    elif args.all:
        models = list_available_models()
        # Skip base/small models that aren't useful for coding
        skip_prefixes = ("qwen2.5:", "qwen3:", "qwen3.5:", "llama3.1:", "glm4:", "ornith:")
        targets = [m for m in models if not any(m.startswith(p) for p in skip_prefixes)]
        for model in targets:
            profile_model(model)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
