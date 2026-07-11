"""
Fix models that emit literal \\n instead of actual newlines in content fields.
"""
import json

NAME = "fix_newline_encoding"
DESCRIPTION = "Replace literal \\\\n strings with real newlines in content args"


def _fix_value(val):
    if isinstance(val, str):
        return val.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    if isinstance(val, dict):
        return {k: _fix_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_fix_value(v) for v in val]
    return val


def normalize(text):
    """Parse JSON, fix escaped newlines in args, re-serialize."""
    stripped = text.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return text

    if not isinstance(data, dict):
        return text

    fixed = _fix_value(data)
    return json.dumps(fixed)
