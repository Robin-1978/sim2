"""Export public, character-centred storylines from one SQLite world instance."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sim2.platform.runtime import Platform


STORY_EVENTS = {
    "behavior.started", "behavior.completed", "behavior.failed", "behavior.interrupted",
    "dialogue.completed", "trade.offer_created", "trade.countered", "trade.completed",
    "exploration.completed", "research.progressed", "research.completed", "object.recovered",
    "negotiation.completed", "negotiation.result_recorded", "story.beat.activated",
    "world.change.event", "world.change.relationship", "world.change.rule", "world.change.faction",
    "relationship.changed", "resource.changed", "capability.evidence", "memory.experience_recorded",
}


def _pages(platform: Platform) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return raw and rendered public events in chronological order."""

    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    after = 0
    while True:
        raw = platform.store.repositories.events.timeline_events(after_event_id=after, limit=500, include_ticks=False)
        if not raw:
            break
        rendered = platform.timeline(limit=500, after_event_id=after, include_ticks=False).get("events", [])
        by_id = {int(item.get("event_id")): item for item in rendered}
        result.extend((item, by_id.get(int(item["event_id"]), {})) for item in raw)
        next_after = int(raw[-1]["event_id"])
        if next_after <= after:
            break
        after = next_after
    return result


def _participants(raw: dict[str, Any]) -> set[str]:
    people: set[str] = set()
    for key in ("actor_id", "target_id", "recipient_id", "offerer_id", "speaker_id", "listener_id"):
        if raw.get(key):
            people.add(str(raw[key]))
    for key in ("audience", "participants"):
        value = raw.get(key)
        if isinstance(value, list):
            people.update(str(item) for item in value)
    return people


def _number(value: Any) -> str:
    if isinstance(value, float):
        return format(value, ".6g")
    return str(value)


def _agent_line(agent: dict[str, Any], platform: Platform) -> str:
    state = agent.get("initial_state", {}) or {}
    needs = agent.get("needs", {}) or {}
    location_id = state.get("location_id", "unknown")
    try:
        location = platform.store.repositories.entities.get_entity(str(location_id)).get("name", location_id)
    except KeyError:
        location = location_id
    satiety = needs.get("satiety")
    satiety_text = _number(satiety) if isinstance(satiety, (int, float)) else "unknown"
    return (
        f"- **Role:** {agent.get('role', 'agent')}  \n"
        f"- **Long-term goal:** {agent.get('ultimate_goal', 'not configured')}  \n"
        f"- **Final location:** {location}; status={state.get('status', 'idle')}  \n"
        f"- **Final condition:** health={_number(state.get('health', 'unknown'))}, energy={_number(state.get('energy', 'unknown'))}, satiety={satiety_text}"
    )


def export_storylines(db_path: Path, output: Path) -> Path:
    platform = Platform(db_path)
    try:
        agents = {str(agent["id"]): agent for agent in platform.store.repositories.entities.list_agents()}
        events = [(raw, rendered) for raw, rendered in _pages(platform) if raw.get("event_type") in STORY_EVENTS]
        grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {agent_id: [] for agent_id in agents}
        world_events: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw, rendered in events:
            participants = _participants(raw)
            matched = participants & set(agents)
            if raw.get("event_type", "").startswith("world.change."):
                world_events.append((raw, rendered))
            for agent_id in matched:
                grouped[agent_id].append((raw, rendered))

        lines = [
            "# Character Storylines",
            "",
            f"World instance: `{platform.store.get_meta('instance_id', 'unknown')}`  ",
            f"World minute: {platform.store.get_meta('world_minute', 0)}  ",
            "Source: public event timeline; private memories and internal identifiers are omitted.",
            "",
            "## World milestones",
            "",
        ]
        milestones = [item for item in events if item[0].get("event_type") in {"story.beat.activated", "exploration.completed", "research.completed", "object.recovered", "world.change.relationship", "world.change.event"}]
        if not milestones:
            lines.append("No recorded milestone.")
        else:
            for raw, rendered in milestones:
                lines.append(f"- Minute {raw['world_minute']}: {rendered.get('narrative', raw.get('event_type', 'world event'))}")
        lines.extend(["", "## Character timelines", ""])

        for agent_id, agent in agents.items():
            name = str(agent.get("name", agent_id))
            lines.extend([f"### {name}", "", _agent_line(agent, platform), ""])
            rows = grouped.get(agent_id, [])
            if not rows:
                lines.append("No public story event recorded.")
            else:
                for raw, rendered in rows:
                    narrative = rendered.get("narrative") or raw.get("event_type", "world event")
                    lines.append(f"- **Minute {raw['world_minute']} · {raw['event_type']}** — {narrative}")
            lines.append("")

        lines.extend(["## World interventions affecting storylines", ""])
        if not world_events:
            lines.append("No God world proposal was recorded.")
        else:
            for raw, rendered in world_events:
                lines.append(f"- **Minute {raw['world_minute']}** — {rendered.get('narrative', raw.get('event_type', 'world change'))}")
        lines.append("")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines), encoding="utf-8")
        return output
    finally:
        platform.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True, help="Path to one world.sqlite3 instance")
    parser.add_argument("--output", type=Path, required=True, help="Markdown output path")
    args = parser.parse_args()
    print(export_storylines(args.db, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
