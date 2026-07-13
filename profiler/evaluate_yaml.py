#!/usr/bin/env python3
"""
Evaluate a submitted YAML adapter config against captured model responses.

This is the per-iteration feedback tool for the Ralph profiler:

  1. Load the YAML ralph just wrote (its `normalizers` list).
  2. Run every captured raw model response through the adapter
     (apply_all normalizers + extract_tool_calls) to produce tool-calls JSON.
  3. Pipe that `{"tool_calls": [...]}` stream through `jq '.tool_calls'`
     and RECORD any jq errors (parse failures / type mismatches).
  4. Print the submitted YAML source alongside the per-test jq results so
     ralph can see exactly what its YAML produced and where jq broke.

Usage:
  python3 evaluate_yaml.py --yaml workspace/<sanitized>.yaml \
      --capture workspace/captured_<sanitized>.json
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

import yaml


def _find_repo_root(start):
    """Walk up from `start` until a dir containing the normalizers/ package."""
    d = pathlib.Path(start).resolve()
    for _ in range(6):
        if (d / "normalizers" / "__init__.py").exists():
            return d
        if d.parent == d:
            break
        d = d.parent
    return None


ROOT = _find_repo_root(pathlib.Path(__file__)) or _find_repo_root(os.getcwd())
for _p in (str(ROOT) if ROOT else None, os.getcwd(), str(pathlib.Path(__file__).parent)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from normalizers import apply_all  # noqa: E402
from adapter import extract_tool_calls  # noqa: E402


def run_jq_tool_calls(payload_json):
    """Pipe a JSON string through `jq '.tool_calls'`; return (ok, text)."""
    try:
        proc = subprocess.run(
            ["jq", ".tool_calls"],
            input=payload_json,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return True, proc.stdout.rstrip()
        return False, (proc.stderr.strip() or "jq failed with no message")
    except FileNotFoundError:
        return False, "jq: command not found"
    except subprocess.TimeoutExpired:
        return False, "jq: timed out"


def load_capture(capture_path):
    """Load captured responses. Returns dict {test_name: raw_string}."""
    with open(capture_path) as f:
        data = json.load(f)
    if isinstance(data, list):
        out = {}
        for i, item in enumerate(data):
            if isinstance(item, dict) and "name" in item and "raw" in item:
                out[item["name"]] = item["raw"]
            else:
                out[f"test_{i}"] = item if isinstance(item, str) else json.dumps(item)
        return out
    if isinstance(data, dict):
        return {k: (v if isinstance(v, str) else json.dumps(v))
                for k, v in data.items()}
    return {"raw": str(data)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", required=True, help="Submitted YAML config to evaluate")
    ap.add_argument("--capture", required=True, help="Captured raw responses JSON")
    ap.add_argument("--model", default="", help="Target model name (for display)")
    args = ap.parse_args()

    yaml_path = pathlib.Path(args.yaml)
    capture_path = pathlib.Path(args.capture)

    print("=" * 60)
    print(f"Evaluating submitted YAML: {yaml_path}")
    if args.model:
        print(f"Target model: {args.model}")
    print("=" * 60)

    if not yaml_path.exists():
        print(f"ERROR: YAML not found: {yaml_path}")
        print("STATUS: NO_YAML")
        return
    if not capture_path.exists():
        print(f"ERROR: capture not found: {capture_path}")
        print("STATUS: NO_CAPTURE")
        return

    with open(yaml_path) as f:
        config = yaml.safe_load(f) or {}
    normalizer_names = config.get("normalizers") or []
    yaml_source = yaml_path.read_text()

    print("\n--- SUBMITTED YAML SOURCE ---")
    print(yaml_source.rstrip())
    print("--- END YAML SOURCE ---\n")

    print(f"Normalizers applied: {normalizer_names or '(none)'}")

    captured = load_capture(capture_path)
    normalizers_fn = lambda s: apply_all(s, normalizer_names)

    total = len(captured)
    jq_ok = 0
    jq_errors = 0
    calls_found = 0

    print(f"\n{'=' * 60}")
    print(f"Per-test jq evaluation ({total} tests)")
    print(f"{'=' * 60}")

    for name, raw in captured.items():
        # The adapter runs the raw model output through the YAML's normalizers.
        normalized = normalizers_fn(raw)
        # Pipe THAT stream of (hopefully) JSON through jq and record errors.
        ok, text = run_jq_tool_calls(normalized)
        # Also run the production extract path for the summary counts.
        calls = extract_tool_calls(raw, normalizers_fn)
        print(f"\n--- {name} ---")
        print(f"normalized (adapter stream): {normalized[:400]}")
        if ok:
            jq_ok += 1
            if calls:
                calls_found += 1
            print(f"jq '.tool_calls' => {text}")
        else:
            jq_errors += 1
            if calls:
                calls_found += 1
            print(f"jq '.tool_calls' ERROR: {text}")

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"  tests:            {total}")
    print(f"  jq_ok:            {jq_ok}")
    print(f"  jq_errors:        {jq_errors}")
    print(f"  tests_with_calls: {calls_found}/{total}")
    if jq_errors == 0 and calls_found == total:
        print("  STATUS: ALL_PASS")
    elif jq_errors == 0:
        print("  STATUS: JSON_OK (some tests produced no tool calls)")
    else:
        print("  STATUS: JQ_ERRORS")
    print("=" * 60)


if __name__ == "__main__":
    main()
