"""Compact one-line renderings of dynamic values shared by every prompt section."""

from __future__ import annotations

from typing import Any


def number(value: Any) -> str:
    """Render numeric state without exposing binary floating-point noise."""

    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return "unknown"
        # Six significant digits preserve meaningful world precision while
        # turning values such as 81.200000000004 into ``81.2``.
        return format(value, ".6g")
    return str(value)


def line(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    value = plain(value)
    return value if len(value) <= 1200 else value[:1197] + "..."


def plain(value: Any) -> str:
    """Render dynamic facts as compact prose instead of nested JSON."""

    if value is None or value == "":
        return "unknown"
    if isinstance(value, dict):
        hidden = {"id", "ref", "behavior_ref", "intent_id", "event_id", "entity_id", "actor_id", "target_id", "location_id", "offer_id"}
        visible = ((key, item) for key, item in value.items() if key not in hidden and not key.endswith("_id"))
        return "; ".join(f"{key}={plain(item)}" for key, item in visible) or "none"
    if isinstance(value, (list, tuple, set)):
        separator = " | " if any(isinstance(item, dict) for item in value) else ", "
        return separator.join(plain(item) for item in value) or "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return number(value)
    return str(value)


def result_line(result: Any) -> str:
    if not isinstance(result, dict):
        return plain(result)
    parts: list[str] = []
    # Canonical IDs are Platform/audit data.  The model receives the visible
    # choice or name instead of an implementation reference.
    for key in ("status", "choice", "behavior_choice", "result", "reason", "event_type", "failed_check", "message", "errors", "retryable", "available_choices", "required_assign_to"):
        if result.get(key) not in (None, ""):
            parts.append(f"{key}={plain(result[key])}")
    outcome = result.get("outcome")
    if isinstance(outcome, dict):
        if outcome.get("status"):
            parts.append(f"outcome={outcome['status']}")
        if outcome.get("delta"):
            parts.append(f"changes={plain(outcome['delta'])}")
    return "; ".join(parts) or plain(result)


def history_line(item: dict[str, Any]) -> str:
    kind = item.get("kind", "event")
    if kind == "assistant_tool_call":
        return f"tool request {item.get('name', 'unknown')}: {plain(item.get('arguments'))}"
    if kind == "tool_result":
        return f"tool result {item.get('name', 'unknown')}: {result_line(item.get('result'))}"
    if kind == "assistant_intent":
        return f"intent request: {result_line(item.get('intent'))}"
    if kind == "platform_result":
        return f"Platform result: {result_line(item.get('result'))}"
    if kind == "llm_error":
        return f"LLM error: {plain(item.get('error'))}"
    return f"{kind}: {plain(item)}"


def memory_line(item: dict[str, Any]) -> str:
    """Keep durable memory useful without copying a raw transcript into context."""

    if item.get("type") in {"dialogue", "testimony"} or item.get("source_type") in {"testimony", "own_statement"}:
        # A topic is only a retrieval key. The Agent needs the actual words to
        # distinguish a report, promise, uncertainty, and fabrication.
        summary = item.get("message") or item.get("summary") or item.get("topic") or "unlabeled dialogue"
    else:
        summary = (
            item.get("summary")
            or item.get("lesson")
            or item.get("fact")
            or item.get("topic")
            or item.get("message")
            or "unlabeled experience"
        )
    summary = " ".join(str(summary).split())
    if len(summary) > 360:
        summary = summary[:357] + "..."
    metadata: list[str] = []
    source = item.get("source_type") or item.get("source")
    if source:
        metadata.append(f"source={plain(source)}")
    claim_status = item.get("claim_status")
    if claim_status:
        metadata.append(f"claim={plain(claim_status)}")
    confidence = item.get("confidence")
    if isinstance(confidence, (int, float)):
        metadata.append(f"confidence={float(confidence):.2f}")
    credibility = item.get("credibility")
    if isinstance(credibility, (int, float)):
        metadata.append(f"credibility={float(credibility):.2f}")
    verification = item.get("verification_status")
    if verification:
        metadata.append(f"verification={plain(verification)}")
    tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
    for key, label in (("location", "where"), ("participants", "people"), ("behavior", "behavior"), ("topic", "topic"), ("goal", "goal")):
        value = tags.get(key)
        if value not in (None, "", []):
            metadata.append(f"{label}={plain(value)}")
    suffix = f" [{'; '.join(metadata)}]" if metadata else ""
    return f"memory ({item.get('type', 'memory')}): {summary}{suffix}"


def fact_line(item: Any) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return plain(item)
    fact = item.get("fact") or item.get("statement") or "unknown fact"
    metadata = []
    source = item.get("source_type") or item.get("source")
    confidence = item.get("confidence")
    if source:
        metadata.append(f"source={plain(source)}")
    if isinstance(confidence, (int, float)):
        metadata.append(f"confidence={float(confidence):.2f}")
    status = item.get("status")
    if status and str(status) != "active":
        metadata.append(f"status={plain(status)}")
    if item.get("superseded_by"):
        metadata.append(f"superseded_by={plain(item.get('superseded_by'))}")
    suffix = f" [{'; '.join(metadata)}]" if metadata else ""
    return f"{plain(fact)}{suffix}"
