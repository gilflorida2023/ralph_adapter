"""
Merge top-level tool keys into the tool_calls array.
Some models put tools like run_command at the top level alongside tool_calls:
  {"tool_calls": [...], "run_command": {"cmd": "..."}}
This normalizer promotes those into the array.
"""
import json

NAME = "merge_top_level_keys"
DESCRIPTION = "Promote run_command etc. from top level into tool_calls array"

TOOL_KEYS = {"read_file", "write_file", "run_command", "debrief_task", "mark_task", "get_next_task"}


def normalize(text):
    """Parse JSON, merge top-level tool keys into tool_calls, re-serialize."""
    stripped = text.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return text

    if not isinstance(data, dict):
        return text

    calls = data.get("tool_calls") or []
    if not isinstance(calls, list):
        calls = []

    seen_names = set()
    for c in calls:
        if isinstance(c, dict):
            name = c.get("name") or c.get("function") or c.get("tool")
            if name:
                seen_names.add(name)

    merged = False
    for key in list(data):
        if key in TOOL_KEYS and key not in seen_names and isinstance(data[key], dict):
            calls.append({"name": key, "args": data[key]})
            seen_names.add(key)
            del data[key]
            merged = True

    if not merged:
        return text

    data["tool_calls"] = calls
    return json.dumps(data)
