"""Compact model-facing schema text without changing structure or validation."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_MAX = 120
# Do not treat "e.g." or "i.e." as a sentence boundary.  A real boundary is
# followed by whitespace and a capital letter/quote, or ends the string.
_SENTENCE = re.compile(r"^(.+?[.!?])(?=\s+[A-Z\"'(]|$)")


def concise_text(value: Any, max_length: int = DEFAULT_MAX) -> str:
    """Collapse whitespace and keep the first useful sentence."""

    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    match = _SENTENCE.match(text)
    if match:
        sentence = match.group(1)
        if len(sentence) <= max_length:
            return sentence
    return text[: max(8, max_length - 3)].rstrip(" ,;:") + "..."


def concise_schema(value: Any, max_length: int = DEFAULT_MAX) -> Any:
    """Walk a JSON Schema and shorten description/purpose prose only."""

    if isinstance(value, dict):
        return {
            key: concise_text(item, max_length) if key in {"description", "purpose"} and isinstance(item, str)
            else concise_schema(item, max_length)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [concise_schema(item, max_length) for item in value]
    return value
