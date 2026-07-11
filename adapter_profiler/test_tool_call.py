#!/usr/bin/env python3
"""
Test a model's tool-calling ability for ONE prompt.
Outputs raw response + parse errors (like a compiler) to stdout.
"""
import json, subprocess, sys

model = sys.argv[1]
prompt_text = sys.argv[2]

payload = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": prompt_text}],
    "format": "json",
    "stream": False,
    "options": {"temperature": 0},
})

try:
    proc = subprocess.run(
        ["curl", "-s", "--max-time", "120", "http://localhost:11434/api/chat", "-d", payload],
        capture_output=True, text=True, timeout=130,
    )
    raw = proc.stdout
except Exception as e:
    print(f"=== CURL ERROR ===\n{e}")
    sys.exit(1)

# Extract message content from Ollama response
try:
    resp = json.loads(raw)
    content = (resp.get("message") or {}).get("content") or ""
except (json.JSONDecodeError, KeyError) as e:
    # Ollama didn't return valid JSON at all
    print(f"=== OLLAMA RESPONSE (raw) ===\n{raw[:500]}")
    print(f"=== PARSE ERROR ===\necho '{raw[:200]}' | jq .")
    print(f"Error: {e}")
    sys.exit(1)

# Show raw content the model returned
print(f"=== MODEL RAW OUTPUT ({len(content)} bytes) ===")
print(repr(content))
print()

# Try to parse it as JSON and extract tool_calls
try:
    data = json.loads(content)
    calls = data.get("tool_calls", [])
    if calls:
        print(f"=== TOOL CALLS ({len(calls)} found) ===")
        for c in calls:
            print(json.dumps(c))
        print("\nSTATUS: PASS")
    else:
        print("=== NO TOOL CALLS ===")
        # Check for top-level tool keys
        tool_keys = [k for k in data if k in ("read_file", "write_file", "run_command", "mark_task", "debrief_task", "get_next_task")]
        if tool_keys:
            print(f"Found top-level tool keys: {tool_keys}")
            print("HINT: add 'merge_top_level_keys' normalizer")
        else:
            print(f"JSON has keys: {list(data.keys())}")
            print("Model returned valid JSON but no tool_calls")
        print("\nSTATUS: FAIL (no tool_calls)")
except json.JSONDecodeError as e:
    print("=== PARSE ERROR ===")
    print(f"jq: echo {repr(content[:100])} | jq '.tool_calls'")
    print(f"Error: {e}")

    # Check for common patterns
    if "```" in content:
        print("HINT: model wrapped output in markdown fences — add 'strip_markdown_fences' normalizer")
    elif content.startswith('"') or content.startswith("'"):
        print("HINT: model wrapped output in quotes — add 'unwrap_json_string' normalizer")
    elif not content.strip():
        print("HINT: model returned empty response — model too small or doesn't support tools")
    else:
        print(f"First 100 chars: {content[:100]}")
    print("\nSTATUS: FAIL (invalid JSON)")
