"""Pull the JSON object out of an LLM reply that may wrap it in prose or code fences."""

import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """The outermost {...} in `text`, parsed. Raises ValueError when there is none or it isn't an object."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("JSON block is not an object")
    return parsed
