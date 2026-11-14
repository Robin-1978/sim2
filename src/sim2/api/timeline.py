from __future__ import annotations

from typing import Any


def build_timeline(store: Any, *, limit: int = 100, after_event_id: int = 0, include_ticks: bool = False) -> dict[str, Any]:
    """Return a public, chronological third-person event projection."""

    def clip(text: Any, size: int = 140) -> str:
        words = " ".join(str(text or "").split())
        return words if len(words) <= size else words[: max(0, size - 1)] + "…"

    def public_reason(text: Any) -> str:
        """Keep framework labels out of the third-person world timeline."""

        value = clip(text, 120)
        replacements = (
            ("故事目标", "当前目标"),
            ("故事节拍", "当前进展"),
            ("story goal", "current goal"),
            ("story beat", "current development"),
            ("Story Agent", "the town's account"),
            ("NPC", "the character"),
            ("Agent", "the character"),
            ("行为树", "行动方案"),
            ("BT", "行动方案"),
        )
        for source, target in replacements:
            value = value.replace(source, target)
        return value

    def bundle_text(bundle: Any) -> str:
        if not isinstance(bundle, dict) or not bundle:
            return "nothing"
        return ", ".join(f"{key}×{value:g}" if isinstance(value, (int, float)) else str(key) for key, value in bundle.items())

    events = store.timeline_events(after_event_id=after_event_id, limit=limit, include_ticks=include_ticks)
    behavior_labels = {
        str(item.get("id")): str(item.get("agent_label") or item.get("summary") or item.get("description") or item.get("id"))
        for item in store.get_definitions("bt_def")
    }
    names: dict[str, str] = {}
    for event in events:
        candidates = [event.get(key) for key in ("actor_id", "target_id", "recipient_id", "offerer_id", "speaker_id", "listener_id", "location_id", "area_id", "object_id", "source_id")]
        candidates.extend(event.get("audience", []) or [])
        for value in candidates:
            if value and str(value) not in names:
                try:
                    names[str(value)] = str(store.get_entity(str(value)).get("name", value))
                except KeyError:
                    names[str(value)] = str(value)
    rendered = []
    for event in events:
        actor = names.get(str(event.get("actor_id")), "The world") if event.get("actor_id") else "The world"
        event_type = str(event.get("event_type", "world.event"))
        target = next((names.get(str(event.get(key))) for key in ("target_id", "recipient_id", "listener_id") if event.get(key)), None)
        if event_type == "behavior.started":
            label = behavior_labels.get(str(event.get("behavior_id", "")), str((event.get("expected", {}) or {}).get("summary") or "a behavior"))
            reason = public_reason((event.get("decision", {}) or {}).get("reason"))
            sentence = f"{actor} began {label}." if not reason else f"{actor} began {label}: {reason}."
        elif event_type == "behavior.completed":
            label = behavior_labels.get(str(event.get("behavior_id", "")), "a behavior")
            sentence = f"{actor} completed {label}: {clip(event.get('result') or event.get('outcome', {}).get('result') or 'the expected result')}."
        elif event_type in {"behavior.failed", "behavior.interrupted"}:
            label = behavior_labels.get(str(event.get("behavior_id", "")), "a behavior")
            sentence = f"{actor}'s {label} {event_type.removeprefix('behavior.')} ({clip(event.get('result') or 'without a stated result')})."
        elif event_type == "action.started":
            sentence = f"{actor} started the action {str(event.get('action_id', 'an action')).replace('_', ' ')}."
        elif event_type == "action.completed":
            sentence = f"{actor} completed the action {str(event.get('action_id', 'an action')).replace('_', ' ')}."
        elif event_type == "dialogue.completed":
            topic = clip(event.get("topic"), 60)
            message = event.get("message")
            if not message and event.get("dialogue_id") is not None:
                try:
                    exchange = store.get_dialogue_exchange(event["dialogue_id"])
                except (TypeError, ValueError):
                    exchange = None
                if exchange:
                    message = exchange.get("message")
                    topic = topic if topic and topic != "unknown" else clip(exchange.get("topic"), 60)
            message = clip(message, 140)
            if message:
                sentence = f"{actor} told {target or 'the town'} ({topic}): {message}"
            else:
                sentence = f"{actor} exchanged information{f' with {target}' if target else ''}."
        elif event_type in {"trade.offer_created", "trade.countered"}:
            verb = "offered a trade" if event_type == "trade.offer_created" else "countered a trade"
            sentence = f"{actor} {verb}{f' to {target}' if target else ''}: gives {bundle_text(event.get('give'))}, wants {bundle_text(event.get('want'))}."
        elif event_type == "trade.completed":
            sentence = f"{actor} completed a trade{f' with {target}' if target else ''}: {bundle_text(event.get('give'))} for {bundle_text(event.get('want'))}."
        elif event_type == "trade.rejected":
            sentence = f"{actor} rejected a trade{f' with {target}' if target else ''}."
        elif event_type == "exploration.completed":
            area = names.get(str(event.get("area_id", "")), str(event.get("area_id", "an area")))
            discoveries = event.get("discoveries", []) or []
            sentence = f"{actor} surveyed {area} and reported {len(discoveries)} finding(s)."
        elif event_type in {"research.completed", "research.progressed"}:
            source = names.get(str(event.get("source_id") or (event.get("parameters", {}) or {}).get("source_id", "")), "a source")
            progress = event.get("progress", {}) or {}
            if event_type == "research.completed":
                sentence = f"{actor} researched {source}: {clip(event.get('fact'), 160)}"
            else:
                sentence = f"{actor} advanced research on {source} ({progress.get('after', '?')}/{progress.get('required', '?')})."
        elif event_type == "object.recovered":
            recovered = names.get(str(event.get("object_id", "")), str(event.get("object_id", "an object")))
            sentence = f"{actor} recovered {recovered}: {clip(event.get('fact'), 160)}"
        elif event_type == "story.beat.activated":
            audience = ", ".join(names.get(str(member), str(member)) for member in (event.get("audience", []) or []))
            sentence = f"A new development emerged: '{clip(event.get('summary'), 160)}'{f' for {audience}' if audience else ''}."
        elif event_type == "story.beat.completed":
            label = behavior_labels.get(str(event.get("behavior_id", "")), str(event.get("behavior_id", "a behavior")))
            sentence = f"{actor} followed through on a development with {label}."
        elif event_type == "evolution.task_due":
            sentence = "A scheduled world review became due."
        elif event_type in {"civilization.milestone", "civilization.progressed"}:
            sentence = f"{'Civilization milestone' if event_type == 'civilization.milestone' else 'Civilization progress'}: {clip(event.get('progress_key'), 60)} — {clip(event.get('evidence'), 160)}"
        elif event_type == "world.rule.followed":
            label = behavior_labels.get(str(event.get("behavior_id", "")), str(event.get("behavior_id", "a behavior")))
            sentence = f"{actor} followed rule '{clip(event.get('rule'), 80)}' with {label}: {clip(event.get('result'), 120)}"
        elif event_type == "memory.experience_recorded":
            sentence = f"{actor} kept a lesson: {clip(event.get('summary'), 160)}"
        elif event_type == "capability.updated":
            sentence = f"{actor}'s capability {event.get('capability')} changed {event.get('before')} → {event.get('after')}."
        elif event_type in {"definition.registered", "behavior.registered"}:
            sentence = f"{actor} registered {clip(event.get('definition_type') or 'a behavior')}: {clip(event.get('name') or event.get('definition_id') or event.get('behavior_id'), 80)}."
        elif event_type.startswith("world.change."):
            summary = str(event.get("summary") or event_type.removeprefix("world.change.").replace("_", " ")).rstrip(".")
            sentence = f"The world changed: {summary}."
        elif event_type == "world.created":
            sentence = "The world was created from its configured opening state."
        elif event_type == "player.entered":
            sentence = f"{actor} entered the world."
        else:
            summary = str(event.get("summary") or event.get("result") or event_type.replace(".", " ").replace("_", " ")).rstrip(".")
            sentence = f"{actor}: {summary}."
        rendered.append({"event_id": event["event_id"], "world_minute": event["world_minute"], "event_type": event_type, "narrative": sentence, "public": {key: value for key, value in event.items() if key not in {"actor_id", "target_id", "recipient_id", "offerer_id", "speaker_id", "listener_id", "location_id", "decision"}}})
    return {"status": "ok", "world_id": store.get_meta("world_id"), "world_minute": int(store.get_meta("world_minute", 0)), "events": rendered, "next_after_event_id": events[-1]["event_id"] if events else after_event_id}
