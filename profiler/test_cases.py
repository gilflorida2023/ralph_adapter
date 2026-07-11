"""
Standard test cases for profiling model output.
Each test case sends a prompt and validates the expected tool_calls shape.
"""
import json

# The tool definitions the model needs to know about
TOOL_DEFINITIONS = """Available tools:
- read_file: Read a file from disk. Args: path (string)
- write_file: Write content to a file. Args: path (string), content (string)
- run_command: Execute a shell command. Args: cmd (string)
- debrief_task: Report what was confusing and suggest improvements. Args: what_was_confusing (string), suggested_rule_for_prompt (string), suggested_spec_clarification (string)
- mark_task: Mark a task as done or blocked. Args: num (int), state (string)
- get_next_task: Get the next available task. No args needed.

Respond with ONLY a single valid JSON object containing exactly one key, tool_calls, whose value is an array of objects each with name and args."""

TEST_CASES = [
    {
        "name": "read_file_single",
        "prompt": TOOL_DEFINITIONS + "\n\nRead workspace/tasks.py.",
        "min_calls": 1,
        "validate": lambda calls: (
            any(c.get("name") == "read_file" for c in calls),
            f"expected read_file tool call, got {[c.get('name') for c in calls]}",
        ),
    },
    {
        "name": "write_python_code",
        "prompt": TOOL_DEFINITIONS + (
            "\n\nWrite a function is_even(n) to workspace/tasks.py "
            "that returns True if n is even. Include a doctest."
        ),
        "min_calls": 1,
        "validate": lambda calls: (
            any(c.get("name") in ("read_file", "write_file") for c in calls),
            f"expected read_file or write_file, got {[c.get('name') for c in calls]}",
        ),
    },
    {
        "name": "run_pytest",
        "prompt": TOOL_DEFINITIONS + (
            "\n\nRun pytest on workspace/tasks.py with verbose output."
        ),
        "min_calls": 1,
        "validate": lambda calls: (
            any(c.get("name") == "run_command" for c in calls),
            f"expected run_command, got {[c.get('name') for c in calls]}",
        ),
    },
    {
        "name": "multi_step_flow",
        "prompt": TOOL_DEFINITIONS + (
            "\n\nRead workspace/tasks.py, then add a docstring to it, "
            "then run doctests."
        ),
        "min_calls": 3,
        "validate": lambda calls: (
            len(calls) >= 3,
            f"expected at least 3 tool calls, got {len(calls)} ({[c.get('name') for c in calls]})",
        ),
    },
    {
        "name": "debrief_task",
        "prompt": TOOL_DEFINITIONS + (
            "\n\nTask 1 is done. Reflect on what was difficult "
            "and suggest improvements."
        ),
        "min_calls": 1,
        "validate": lambda calls: (
            any(c.get("name") == "debrief_task" for c in calls),
            f"expected debrief_task, got {[c.get('name') for c in calls]}",
        ),
    },
]


def run_test_case(test_case, raw_content):
    """Analyze a raw model response against a test case."""
    # Normalize: strip markdown, unwrap quotes
    content = raw_content.strip()
    for fence in ("```json", "```"):
        content = content.replace(fence, "")
    content = content.strip()

    result = {
        "name": test_case["name"],
        "raw_length": len(raw_content),
        "raw_preview": raw_content[:300],
        "has_json_fences": "```" in raw_content,
        "is_quote_wrapped": (
            content.startswith('"') and content.endswith('"')
        ),
        "errors": [],
    }

    # Check for quote wrapping and unwrap
    if result["is_quote_wrapped"]:
        try:
            inner = json.loads(content)
            if isinstance(inner, str):
                content = inner
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        result["valid_json"] = False
        result["has_tool_calls_key"] = False
        result["tool_calls"] = []
        result["errors"].append("invalid JSON")
        return result

    result["valid_json"] = True
    result["has_tool_calls_key"] = "tool_calls" in data

    # Find top-level tool-like keys
    tool_keys = {"read_file", "write_file", "run_command", "debrief_task", "mark_task", "get_next_task"}
    top_tools = [k for k in data if k in tool_keys]
    result["top_level_tools"] = top_tools

    calls = data.get("tool_calls") or []
    if not isinstance(calls, list):
        calls = []
        result["errors"].append("tool_calls is not a list")

    result["tool_call_count"] = len(calls)
    result["tool_call_names"] = [
        c.get("name") or c.get("function") or c.get("tool", "?")
        for c in calls if isinstance(c, dict)
    ]

    # Validation
    ok, msg = test_case["validate"](calls)
    if not ok:
        result["errors"].append(msg)

    return result
