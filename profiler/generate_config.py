#!/usr/bin/env python3
"""
Generate a model config YAML by profiling a model against test cases.
Sends each test case prompt to the model, analyzes the raw response,
detects which normalizers are needed, and writes the config file.

Modes:
  --model MODEL                  Live profiling (calls Ollama)
  --model MODEL --capture FILE   Capture raw responses to FILE (no analysis)
  --model MODEL --from-capture FILE  Replay analysis from captured FILE (no Ollama calls)
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
    if any(r.get("is_quote_wrapped") for r in all_results):
        needed.append("unwrap_json_string")

    # Check for top-level tool keys
    if any(r.get("top_level_tools") for r in all_results):
        needed.append("merge_top_level_keys")

    # Check for trailing garbage (valid JSON prefix with extra content after)
    for r in all_results:
        raw = r.get("_raw", "")
        if not raw.strip():
            continue
        stripped = raw.strip()
        for end in range(len(stripped), 0, -1):
            candidate = stripped[:end]
            try:
                json.loads(candidate)
                if end < len(stripped):
                    needed.append("trim_trailing_garbage")
                break
            except json.JSONDecodeError:
                continue

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


def gather_raw_responses(model_name, max_time=300, temperature=0.0):
    """Send each test prompt to the model, return {test_name: raw_content}."""
    responses = {}
    for tc in TEST_CASES:
        print(f"\n  Test: {tc['name']}...", end=" ", flush=True)

        last_error = None
        raw_content = None
        for attempt in range(3):
            resp = call_ollama(model_name, tc["prompt"], max_time, temperature=temperature)
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
            print(f"--- {tc['name']} (0 bytes) ---")
            print("raw = ''")
            print()
            print("jq: echo '' | jq '.tool_calls'")
            print(f"Error: {last_error}")
            print("HINT: model returned empty response — too small or doesn't understand tool calling")
            print("STATUS: FAIL")
            print()
            responses[tc["name"]] = ""
            continue

        responses[tc["name"]] = raw_content

    return responses


def analyze_responses(model_name, responses, output_dir=None, temperature=0.0):
    """Analyze captured raw responses and generate config."""
    config_dir = pathlib.Path(output_dir) if output_dir else CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Analyzing captured responses for: {model_name}")
    print(f"{'='*60}")

    results = []

    for tc in TEST_CASES:
        raw_content = responses.get(tc["name"], "")

        if not raw_content:
            print(f"--- {tc['name']} (0 bytes) ---")
            print("raw = ''")
            print()
            print("jq: echo '' | jq '.tool_calls'")
            print("Error: empty captured response")
            print("HINT: model returned empty response — too small or doesn't understand tool calling")
            print("STATUS: FAIL")
            print()
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
                "errors": ["empty captured response"],
            })
            continue

        analysis = run_test_case(tc, raw_content)
        analysis["_raw"] = raw_content
        results.append(analysis)

        raw_repr = repr(raw_content[:300])
        is_valid = analysis["valid_json"]
        has_tc = analysis["has_tool_calls_key"]
        tc_count = analysis["tool_call_count"]
        tc_names = analysis["tool_call_names"]
        errors = analysis["errors"]
        has_fences = analysis["has_json_fences"]
        quote_wrap = analysis.get("is_quote_wrapped")
        top_tools = analysis.get("top_level_tools")

        print(f"--- {tc['name']} ({len(raw_content)} bytes) ---")
        print(f"raw = {raw_repr}")
        print()

        # Compiler-style parse attempt
        if not raw_content.strip():
            print("jq: echo '' | jq '.tool_calls'")
            print("Error: empty response from model")
            print("HINT: model returned nothing — too small or doesn't support tools")
        elif not is_valid:
            print(f"jq: echo {raw_repr} | jq '.tool_calls'")
            print("Error: not valid JSON")
            if has_fences:
                print("HINT: model wrapped output in markdown fences — add 'strip_markdown_fences'")
            if quote_wrap:
                print("HINT: model wrapped output in quotes — add 'unwrap_json_string'")
        elif tc_count == 0 and not has_tc:
            print(f"jq: echo {raw_repr} | jq '.tool_calls'")
            print("Error: JSON has no 'tool_calls' key")
            if top_tools:
                print(f"HINT: found tools at root level ({top_tools}) — add 'merge_top_level_keys'")
        elif tc_count == 0:
            print("tool_calls = []")
            print("Error: empty tool_calls array")
        else:
            print(f"tool_calls = {tc_count} calls: {tc_names}")
            for c in (analysis.get("tool_calls") or []):
                if isinstance(c, dict) and isinstance(c.get("args"), list):
                    print(f"  WARN: {c.get('name', '?')} args is array, not object: {c['args']}")

        if errors:
            print(f"errors: {'; '.join(errors)}")
        if not errors and tc_count > 0:
            print("STATUS: PASS")
        else:
            print("STATUS: FAIL")
        print()

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
    config_path = config_dir / f"{sanitize_model_name(model_name)}.yaml"
    if output_dir is None:
        remove_duplicate_configs(model_name)
        config_path = get_config_path(model_name)[0]
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    print(f"\n  Config written: {config_path}  (ID: {model_id or 'unknown'})")
    print(f"{'='*60}\n")

    return config, results


def profile_model(model_name, max_time=300, output_dir=None, temperature=0.0, capture=None, replay=None):
    """Profile a model against all test cases and generate its config.

    Args:
        capture: If set, path to write captured raw responses (no analysis).
        replay: If set, path to read captured raw responses and analyze.
    """
    if capture:
        print(f"\n{'='*60}")
        print(f"Capturing raw responses for: {model_name}")
        print(f"{'='*60}")
        responses = gather_raw_responses(model_name, max_time, temperature)
        with open(capture, "w") as f:
            json.dump(responses, f, indent=2)
        print(f"\n  Captured {len(responses)} responses to {capture}")
        print(f"{'='*60}\n")
        return None, None

    if replay:
        with open(replay) as f:
            responses = json.load(f)
        return analyze_responses(model_name, responses, output_dir, temperature)

    # Default: live profiling
    responses = gather_raw_responses(model_name, max_time, temperature)
    return analyze_responses(model_name, responses, output_dir, temperature)


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
    parser.add_argument("--output-dir", help="Directory to write config (default: models/)")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature for model calls (default: 0.0 for deterministic profiling)")
    parser.add_argument("--capture", help="Capture raw responses to FILE (queries model once, no analysis)")
    parser.add_argument("--from-capture", help="Replay analysis from captured FILE (no Ollama calls)")
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
        profile_model(args.model, output_dir=args.output_dir, temperature=args.temperature,
                      capture=args.capture, replay=args.from_capture)
    elif args.all:
        models = list_available_models()
        for model in models:
            profile_model(model)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()