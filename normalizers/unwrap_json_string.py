"""
Unwrap content that is a JSON-encoded string.
Some models wrap their output as a JSON string literal:
  '"{\\"tool_calls\\": [...]}"'  →  '{"tool_calls": [...]}'
"""
import json

NAME = "unwrap_json_string"
DESCRIPTION = "Decode JSON-encoded string wrapping (double-quoted content)"


def normalize(text):
    """If text is a JSON-encoded string, decode it once."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped.startswith('"') and stripped.endswith('"'):
        try:
            decoded = json.loads(stripped)
            if isinstance(decoded, str):
                return decoded
        except (json.JSONDecodeError, TypeError):
            pass
    return text
