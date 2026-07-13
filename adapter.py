#!/usr/bin/env python3
"""
Model adapter — thin layer between Ralph and Ollama models.

Reads a per-model YAML config from models/<model_name>.yaml that lists
which normalizer plugins to apply. Ralph sends a prompt, gets clean JSON back.
No Ralph code changes needed for new models.

Usage:
  python3 adapter.py --model MODEL_NAME [--prompt FILE] [--max-time SECONDS]

Outputs JSON with tool_calls array, prompt_tokens, completion_tokens.
"""
import json
import os
import pathlib
import subprocess
import sys
import time
import yaml

# Ensure normalizers/ package is importable regardless of cwd
_self_dir = pathlib.Path(__file__).parent.resolve()
if str(_self_dir) not in sys.path:
    sys.path.insert(0, str(_self_dir))

from normalizers import apply_all


def get_model_id(model_name):
    """Look up the current model ID from ollama list."""
    try:
        ol = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
        for line in ol.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if parts and parts[0] == model_name and len(parts) >= 2:
                return parts[1]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def load_model_config(model_name):
    """Load YAML config for a model. Returns dict or None.
    Writes a warning to stderr if the stored model_id doesn't match."""
    config_dir = _self_dir / "models"
    # Try exact name, then sanitized (replace / and : with _)
    candidates = [
        config_dir / f"{model_name}.yaml",
        config_dir / f"{model_name.replace('/', '_').replace(':', '_')}.yaml",
    ]
    for path in candidates:
        if path.exists():
            with open(path) as f:
                cfg = yaml.safe_load(f)
            # Check model ID
            stored_id = cfg.get("model_id") if cfg else None
            if stored_id:
                current_id = get_model_id(model_name)
                if current_id and current_id != stored_id:
                    print(f"WARNING: Model {model_name} ID changed "
                          f"({stored_id[:12]}... → {current_id[:12]}...). "
                          "Re-run profiler to validate config.",
                          file=sys.stderr)
            return cfg
    return None


def call_ollama(model, prompt, max_time=300, temperature=0.7):
    """Call Ollama API and return the response dict, or None on failure."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        "options": {"temperature": temperature},
    }
    try:
        proc = subprocess.run(
            ["curl", "-s", "--max-time", str(max_time),
             "http://localhost:11434/api/chat", "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=max_time + 10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def extract_tool_calls(raw_content, normalizers):
    """Apply normalizer pipeline, then parse tool_calls from JSON."""
    raw = normalizers(raw_content) if normalizers else raw_content

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, dict):
        return []

    calls = []
    for c in data.get("tool_calls") or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or c.get("function") or c.get("tool")
        if not name:
            continue
        if name in ("run_shell", "shell"):
            name = "run_command"
        args = c.get("args") or c.get("parameters") or {}
        calls.append({"name": name, "args": args})

    # Fallback: single tool at top level
    if not calls:
        tool = data.get("tool") or data.get("tool_to_use") or data.get("action")
        if tool and tool not in ("done", "write_function", "write_test", "run_pytest"):
            calls.append({
                "name": tool,
                "args": {k: v for k, v in data.items()
                         if k not in ("tool", "tool_to_use", "action", "reasoning")},
            })

    return calls


def main():
    model = os.environ.get("RALPH_MODEL")
    prompt_file = "/tmp/ralph_prompt.txt"
    max_time = 300
    retries = 5

    args = iter(sys.argv[1:])
    for arg in args:
        if arg == "--model":
            model = next(args)
        elif arg == "--prompt":
            prompt_file = next(args)
        elif arg == "--max-time":
            max_time = int(next(args))
        elif arg == "--retries":
            retries = int(next(args))

    if not model:
        model = "qwen2.5-coder:7b"

    # Load model config
    config = load_model_config(model)
    if config is None:
        normalizer_names = []
    else:
        normalizer_names = config.get("normalizers") or []

    # Reject blocked / unsupported models with a clear error
    if config and (config.get("blocked") or config.get("unsupported")):
        reason = "unsupported" if config.get("unsupported") else "blocked"
        print(json.dumps({
            "tool_calls": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "error": f"model {model} is {reason}: no working adapter config",
        }), file=sys.stderr)
        sys.exit(2)

    if not os.path.exists(prompt_file):
        print(json.dumps({"tool_calls": [], "prompt_tokens": 0, "completion_tokens": 0}))
        return

    prompt = open(prompt_file).read()

    # Call Ollama with retries
    last_error = None
    for attempt in range(retries):
        resp = call_ollama(model, prompt, max_time)
        if resp is None:
            last_error = "curl failed or empty response"
            if attempt < retries - 1:
                time.sleep(5)
            continue

        msg = resp.get("message") or {}
        content = (msg.get("content") or "").strip()
        if not content:
            last_error = "empty content"
            if attempt < retries - 1:
                time.sleep(5)
            continue

        tool_calls = extract_tool_calls(content, lambda s: apply_all(s, normalizer_names))

        result = {
            "tool_calls": tool_calls,
            "prompt_tokens": resp.get("prompt_eval_count", 0),
            "completion_tokens": resp.get("eval_count", 0),
        }
        json.dump(resp, open("/tmp/ralph_last_response.json", "w"))
        print(json.dumps(result))
        return

    print(json.dumps({
        "tool_calls": [],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "error": last_error or "unknown",
    }), file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
