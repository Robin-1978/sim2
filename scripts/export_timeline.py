"""Export a complete public world timeline without LLM call details."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sim2.platform.runtime import Platform


def _number(value: Any) -> str:
    if isinstance(value, float):
        return format(value, ".6g")
    return str(value)


def _compact(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if key.endswith("_id") or key in {"id", "event_id", "intent_id", "execution_id", "offer_id", "entity_id"}:
                continue
            parts.append(f"{key}={_compact(item)}")
        return "; ".join(parts) or "none"
    if isinstance(value, list):
        return ", ".join(_compact(item) for item in value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _names(platform: Platform) -> dict[str, str]:
    result: dict[str, str] = {}
    for entity_type in ("agent", "location", "resource", "material", "object", "faction"):
        for item in platform.store.entities_of_type(entity_type):
            result[str(item["id"])] = str(item.get("name", item["id"]))
    return result


def _actor(raw: dict[str, Any], names: dict[str, str]) -> str:
    actor_id = raw.get("actor_id")
    return names.get(str(actor_id), str(actor_id)) if actor_id else "The world"


def _event_detail(raw: dict[str, Any], names: dict[str, str], exchanges: dict[int, dict[str, Any]]) -> str:
    event_type = str(raw.get("event_type", "world.event"))
    if event_type == "world_minute.advanced":
        return f"World time advanced to minute {raw.get('world_minute')}."
    if event_type == "dialogue.completed":
        data = exchanges.get(int(raw.get("dialogue_id", -1)), {})
        speaker = names.get(str(data.get("speaker_id")), _actor(raw, names))
        listener = names.get(str(data.get("listener_id")), "the other participant")
        message = str(data.get("message") or "").strip()
        return f"{speaker} exchanged information with {listener}." + (f' Message: “{message}”' if message else "")
    if event_type.startswith("world.change."):
        values = []
        for key in ("summary", "conditions", "expected_effects", "effect"):
            if raw.get(key) not in (None, "", [], {}):
                values.append(f"{key}={_compact(raw[key])}")
        return "World proposal result: " + ("; ".join(values) or "recorded")
    if event_type == "story.beat.activated":
        values = [f"summary={_compact(raw.get('summary', ''))}"]
        for key in ("audience", "reason", "public_event"):
            if raw.get(key) not in (None, "", [], {}):
                values.append(f"{key}={_compact(raw[key])}")
        return "Story beat activated: " + "; ".join(values)
    values = []
    for key in ("summary", "behavior_id", "action_id", "result", "outcome", "progress", "fact", "capability", "delta", "relation", "change_type"):
        if raw.get(key) not in (None, "", [], {}):
            value = raw[key]
            if key == "behavior_id":
                value = str(value).replace("_", " ")
            values.append(f"{key}={_compact(value)}")
    if values:
        return "; ".join(values)
    return event_type.replace(".", " ").replace("_", " ")


def export_timeline(db_path: Path, output: Path, *, include_ticks: bool = True) -> Path:
    platform = Platform(db_path)
    try:
        names = _names(platform)
        # Read the payload directly because SQLite's JSON extension is not
        # required for every supported runtime.
        exchanges: dict[int, dict[str, Any]] = {}
        for row in platform.store.connection.execute("SELECT id,speaker_id,listener_id,topic,world_minute,data_json FROM dialogue_exchange"):
            exchanges[int(row["id"])] = {"speaker_id": row["speaker_id"], "listener_id": row["listener_id"], "topic": row["topic"], "world_minute": row["world_minute"], **json.loads(row["data_json"] or "{}")}

        raw_events = platform.store.timeline_events(after_event_id=0, limit=500, include_ticks=include_ticks)
        all_events = list(raw_events)
        after = int(raw_events[-1]["event_id"]) if raw_events else 0
        while raw_events:
            raw_events = platform.store.timeline_events(after_event_id=after, limit=500, include_ticks=include_ticks)
            if not raw_events:
                break
            all_events.extend(raw_events)
            next_after = int(raw_events[-1]["event_id"])
            if next_after <= after:
                break
            after = next_after
        all_events.sort(key=lambda item: (int(item.get("world_minute", 0)), int(item.get("event_id", 0))))

        lines = [
            "# Full World Timeline",
            "",
            f"World instance: `{platform.store.get_meta('instance_id', 'unknown')}`  ",
            f"World time: minute 0–{platform.store.get_meta('world_minute', 0)}  ",
            "Agent call details, prompts, internal IDs, and private reasoning are omitted. Every recorded world event is included.",
            "",
            "## Timeline",
            "",
        ]
        for raw in all_events:
            actor = _actor(raw, names)
            lines.append(f"- **Minute {raw['world_minute']} · {raw['event_type']} · {actor}** — {_event_detail(raw, names, exchanges)}")
        lines.append("")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8")
        return output
    finally:
        platform.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--without-ticks", action="store_true", help="omit one-minute world time events")
    args = parser.parse_args()
    print(export_timeline(args.db, args.output, include_ticks=not args.without_ticks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
