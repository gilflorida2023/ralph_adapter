"""
Strip non-JSON content after the first valid JSON value ends.
Some models append trailing whitespace, null bytes, or stray characters
after a valid JSON object.
"""
import json

NAME = "trim_trailing_garbage"
DESCRIPTION = "Remove non-JSON content trailing after valid JSON"


def normalize(text):
    """Find the first valid JSON value and discard everything after."""
    stripped = text.strip()
    if not stripped:
        return text

    # Try to parse incrementally — find the longest valid prefix
    for end in range(len(stripped), 0, -1):
        candidate = stripped[:end]
        try:
            json.loads(candidate)
            if end < len(stripped):
                # There was trailing garbage
                return candidate
            return text  # No garbage to trim
        except json.JSONDecodeError:
            continue

    return text
