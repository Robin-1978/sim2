"""Bounded player projections of visible residents and public developments."""

from __future__ import annotations

from typing import Any

from ..behavior.controllers import controller_mode
from ..platform.resident_conversations import resident_conversation
from ..world.lifecycle import is_inactive

_LIVING_TYPES = {"agent", "player"}


def scene_people(platform: Any, player: dict[str, Any], entities: list[dict[str, Any]],
                 nodes: dict[str, Any], rules: dict[str, Any], ui: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    repo = platform.store.repositories
    social = platform._services["social"]
    actions = {item["id"]: item for item in repo.entities.get_definitions("action_def")}
    labels = ui.get("resident_actions") or {}
    residents = [
        item for item in entities
        if item.get("type") in _LIVING_TYPES and item["id"] != player["id"]
        and not is_inactive(item.get("initial_state"))
    ]
    profiles = (rules.get("resident_conversations") or {}).get("residents") or {}
    residents.sort(key=lambda item: (
        item["id"] not in profiles,
        (item.get("initial_state") or {}).get("active_action") in {None, "wait"},
        str(item.get("name") or item["id"]),
    ))
    player_available = social._available_for_instant_interaction(player, actions)
    people = []
    for item in residents[:12]:
        actor_id = item["id"]
        actor = repo.entities.get_agent(actor_id)
        state = actor.get("initial_state") or {}
        status = str(state.get("status") or "")
        action = actions.get(state.get("active_action"), {})
        active = status in {"executing", "paused"}
        label = ""
        if active:
            action_id = state.get("active_action")
            label = labels.get(action_id) or action.get("description", "")
            parameters = state.get("active_action_parameters") or {}
            destination = parameters.get("location_id") or parameters.get("destination_id")
            if destination in nodes and action.get("verb") in {"move", "travel"} and labels:
                label = f"正在前往{nodes[destination]['name']}"
        can_talk = player_available and social._available_for_instant_interaction(actor, actions)
        profile = resident_conversation(rules, actor_id) if controller_mode(actor) == "bt" else {}
        portrait = repo.artwork.generated_for(actor_id)
        people.append({
            "id": actor_id,
            "name": item["name"],
            "type": item["type"],
            "role": item.get("role"),
            "status": status,
            "active_behavior": state.get("active_behavior_id") or "",
            "location": nodes.get(state.get("location_id"), {}).get("name"),
            "can_talk": can_talk,
            "active_behavior_label": label or "",
            "initial_state": {key: state[key] for key in ("remaining_minutes", "action_progress") if key in state},
            "portrait": f"/world-art/{portrait}.webp" if portrait else "",
            "profile": profile.get("intro", ""),
            "topics": [
                {"id": key, "question": entry["question"]}
                for key, entry in profile.get("topics", {}).items()
                if entry.get("question")
            ],
            "help_behavior": profile.get("help_behavior", ""),
        })
    return len(residents), people


def city_watch(platform: Any, player: dict[str, Any], world: dict[str, Any], ui: dict[str, Any]) -> dict[str, Any]:
    config = ui.get("city_watch") or {}
    if not config:
        return {}
    signals = [
        {"place": signal["place"], "text": signal["text"]}
        for signal in (config.get("signals") or [])
        if platform.conditions.evaluate(str(signal["when"]), agent=player, world=world)
    ]
    types = config.get("events") or {}
    repo = platform.store.repositories
    milestones = []
    if types:
        for event in repo.events.timeline_events(
            after_event_id=0, limit=5, latest=True, event_types=tuple(types),
        ):
            audience = event.get("audience") or []
            if audience and player["id"] not in audience:
                continue
            label = str(event.get("title") or types[event["event_type"]])
            summary = str(event.get("summary") or label)
            try:
                actor = repo.entities.get_entity(event["actor_id"]) if event.get("actor_id") else {}
            except KeyError:
                actor = {}
            milestones.append({
                "id": event["event_id"], "minute": event["world_minute"], "title": label, "event_type": event["event_type"],
                "summary": summary[:180], "actor": actor.get("name", ""),
            })
    report = None
    presentation = ui.get("decision_report") or {}
    if world.get(presentation.get("show_when")) and not world.get(presentation.get("hide_when")):
        events = presentation.get("event_types") or []
        if events:
            report = next(iter(repo.events.recent_events_by_types(events, limit=1)), None)
    return {
        "signals": signals[:4], "milestones": list(reversed(milestones)),
        "report": {"summary": str(report.get("summary") or "")[:180]} if report else None,
    }
