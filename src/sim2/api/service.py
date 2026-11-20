from __future__ import annotations

from pathlib import Path
from typing import Any

from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from ..world.player_template import PlayerTemplateError, parse_player_template
from ..world.ui_text import UiText
from .authoring import AuthoringService


def _player_chapter(platform: Platform, player_id: str) -> dict[str, Any] | None:
    """Prefer a configured ending, then an emergent Story beat, then an anchor.

    A situation anchor is not a plot branch.  It is a fixed, read-only
    narration of a validated public fact used when no model-driven Story beat
    exists.  It never mutates world state or gates behavior.  A configured
    season ending stays visible after the final route opens; Story beats take
    precedence over opening anchors for the audience they were addressed to.
    """

    ending = _configured_ending(platform, player_id)
    if ending is not None:
        return ending

    events_repo = platform.store.repositories.events
    for event in events_repo.recent_events_by_types(["story.beat.activated"], limit=24):
        audience = [str(item) for item in (event.get("audience") or []) if str(item)]
        if audience and player_id not in audience and "player" not in audience:
            continue
        title = str(event.get("title") or event.get("summary") or event.get("beat_id") or "当前篇章")
        return {
            "id": str(event.get("beat_id") or title),
            "title": title,
            "summary": str(event.get("summary") or event.get("fact") or ""),
            "goal_hint": str(event.get("goal_hint") or ""),
            "event_type": "story.beat.activated",
            "event_id": int(event.get("event_id", 0) or 0),
            "world_minute": int(event.get("world_minute", 0) or 0),
            "kind": "story",
        }

    story = platform.store.repositories.metadata.get("story", {}) or {}
    root = story.get("story", story) if isinstance(story, dict) else {}
    window = root.get("situation_anchor_window_minutes")
    if isinstance(window, (int, float)) and not isinstance(window, bool):
        current_minute = int(platform.store.repositories.metadata.get("world_minute", 0) or 0)
        if current_minute > int(window):
            return None
    anchors = root.get("situation_anchors") or root.get("fallback_chapters") or []
    anchors = [
        item for item in anchors
        if isinstance(item, dict) and item.get("id")
    ]
    if not anchors:
        return None
    triggers: dict[str, list[dict[str, Any]]] = {}
    for anchor in anchors:
        event_types = anchor.get("trigger_events") or []
        if isinstance(event_types, str):
            event_types = [event_types]
        if anchor.get("trigger_event"):
            event_types = list(event_types) + [anchor["trigger_event"]]
        for event_type in event_types:
            triggers.setdefault(str(event_type), []).append(anchor)
    if not triggers:
        return None
    for event in events_repo.recent_events_by_types(list(triggers), limit=40):
        for anchor in triggers.get(str(event.get("event_type") or ""), []):
            return {
                "id": str(anchor["id"]),
                "title": str(anchor.get("title") or anchor["id"]),
                "summary": str(anchor.get("summary") or ""),
                "goal_hint": str(anchor.get("goal_hint") or ""),
                "event_type": str(event.get("event_type") or ""),
                "event_id": int(event.get("event_id", 0) or 0),
                "world_minute": int(event.get("world_minute", 0) or 0),
                "kind": "situation",
            }
    return None


def _configured_ending(platform: Platform, player_id: str) -> dict[str, Any] | None:
    """Return the first configured season ending whose condition is true."""

    story = platform.store.repositories.metadata.get("story", {}) or {}
    root = story.get("story", story) if isinstance(story, dict) else {}
    endings = root.get("endings") or []
    if not isinstance(endings, list) or not endings:
        return None
    try:
        agent = platform.store.repositories.entities.get_agent(player_id)
    except KeyError:
        agent = {}
    world = platform.store.repositories.metadata.get("world_properties", {}) or {}
    for ending in endings:
        if not isinstance(ending, dict) or not ending.get("id"):
            continue
        condition = str(ending.get("condition") or "").strip()
        try:
            visible = not condition or platform.view.conditions.evaluate(
                condition, agent=agent, target={}, world=world, context={},
            )
        except Exception:
            visible = False
        if not visible:
            continue
        return {
            "id": str(ending["id"]),
            "title": str(ending.get("title") or ending["id"]),
            "summary": str(ending.get("summary") or ""),
            "goal_hint": str(ending.get("goal_hint") or ""),
            "event_type": "story.ending",
            "event_id": 0,
            "world_minute": int(platform.store.repositories.metadata.get("world_minute", 0) or 0),
            "kind": str(ending.get("kind") or "ending"),
            "ended": True,
        }
    return None


def _onboarding_step(platform: Platform, agent: dict[str, Any]) -> dict[str, Any] | None:
    """Project the first matching configurable onboarding step for one player."""

    rules = platform.store.repositories.metadata.get("rules", {}) or {}
    steps = rules.get("onboarding") if isinstance(rules, dict) else []
    if not isinstance(steps, list) or not steps:
        return None
    window = rules.get("onboarding_window_minutes") if isinstance(rules, dict) else None
    if isinstance(window, (int, float)) and not isinstance(window, bool):
        current_minute = int(platform.store.repositories.metadata.get("world_minute", 0) or 0)
        if current_minute > int(window):
            return None
    world = platform.store.repositories.metadata.get("world_properties", {}) or {}
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        condition = str(raw.get("when") or "").strip()
        try:
            visible = not condition or platform.view.conditions.evaluate(
                condition, agent=agent, target={}, world=world, context={},
            )
        except Exception:
            visible = False
        if not visible:
            continue
        override = raw.get("origin_overrides") if isinstance(raw.get("origin_overrides"), dict) else {}
        origin_id = str(agent.get("origin_id") or "")
        chosen = override.get(origin_id) if isinstance(override.get(origin_id), dict) else {}
        payload = {**raw, **chosen}
        return {
            "id": str(payload["id"]),
            "title": str(payload.get("title") or payload["id"]),
            "body": str(payload.get("body") or ""),
            "action_ref": str(payload.get("action_ref") or ""),
            "goal_id": str(payload.get("goal_id") or ""),
            "step": index + 1,
            "total": len([item for item in steps if isinstance(item, dict) and item.get("id")]),
        }
    return None


def build_player_view(platform: Platform, player_id: str) -> dict[str, Any]:
    """Build a compact, playable projection for one player.

    The projection is shared by every client that needs the scene, exits and
    executable actions.  It is read-only and never bypasses a Platform
    visibility boundary.
    """

    player_id = str(player_id or "").strip()
    if not player_id:
        return {"status": "rejected", "message": "player_id is required"}
    try:
        decision = platform.view.build_decision_view(player_id, role="agent_decision", include_diagnostics=True)
    except KeyError:
        return {"status": "rejected", "message": "player does not exist", "failed_check": "player_id"}
    agent = platform.store.repositories.entities.get_agent(player_id)
    map_view = platform.map_view(viewer_id=player_id, observer=False)
    current_id = str((agent.get("initial_state") or {}).get("location_id") or "")
    nodes = {str(item.get("id")): item for item in map_view.get("nodes", []) if isinstance(item, dict)}
    current = nodes.get(current_id, {})
    try:
        location_entity = platform.store.repositories.entities.get_entity(current_id) if current_id else {}
    except KeyError:
        location_entity = {}
    routes = []
    for destination in current.get("routes", []) or []:
        target = nodes.get(str(destination))
        if not target:
            continue
        if not target.get("open", True):
            continue
        edge = next((item for item in map_view.get("edges", []) if {str(item.get("from")), str(item.get("to"))} == {current_id, str(destination)}), {})
        routes.append({
            "id": str(destination),
            "name": str(target.get("name") or destination),
            "distance": edge.get("distance", 1),
            "minutes": edge.get("minutes", 1),
            "safe": bool(target.get("safe", False)),
        })
    active_task = decision.get("active_task", {}) or {}
    actions = list(decision.get("executable_behaviors", []) or [])
    # Travel is a generic player affordance. The decision projection hides
    # a travel BT when its destination is not selected yet; clients supply
    # each known route as an explicit, validated intent instead.
    ui = UiText.from_setting(platform.store.repositories.metadata.get("setting", {}))
    for route in routes:
        if active_task.get("action") or active_task.get("bt"):
            break
        actions.insert(0, {
            "ref": "behavior:travel",
            "choice": ui.text("travel_choice", name=route["name"]),
            "name": ui.text("travel_choice", name=route["name"]),
            "summary": ui.text("travel_summary", name=route["name"]),
            "purpose": ui.text("travel_purpose"),
            "parameters": {"location_id": route["id"]},
            "time": ui.text("travel_time", minutes=route["minutes"]),
            "cost": [],
            "impact": [ui.text("travel_impact")],
            "target_required": False,
            "kind": "travel",
        })
    player_projection = dict(decision.get("self", {}) or {})
    player_projection["location_id"] = current_id
    player_projection["location_safe"] = bool(current.get("safe", False))
    player_projection["location_tags"] = list(current.get("tags") or [])
    # Dialogue and trade target living participants only.  The raw nearby
    # projection also contains resources and objects for scene context; those
    # ids are not valid listeners and used to surface as "participant does not
    # exist" when a player picked them from the target dropdown.
    nearby = list(decision.get("nearby_entities", []) or [])
    participants = [
        item for item in nearby
        if str(item.get("type") or item.get("entity_type") or "") in {"agent", "player"}
    ]
    bt_defs = {str(item.get("id")): item for item in platform.store.repositories.entities.get_definitions("bt_def")}
    unavailable_actions = []
    for item in (decision.get("unavailable_behavior_diagnostics") or []):
        behavior_id = str(item.get("behavior_id") or "")
        if not behavior_id or behavior_id == "travel":
            continue
        behavior = bt_defs.get(behavior_id, {})
        unavailable_actions.append({
            "ref": f"behavior:{behavior_id}",
            "label": behavior.get("agent_label") or behavior.get("summary") or behavior_id,
            "reasons": [str(reason) for reason in (item.get("failed") or [])][:4],
        })
    rules = platform.store.repositories.metadata.get("rules", {}) or {}
    item_labels = {
        str(key): str(value)
        for key, value in (rules.get("item_labels") or {}).items()
        if str(key) and str(value)
    }
    world_property_labels = {
        str(key): dict(value)
        for key, value in (rules.get("world_property_labels") or {}).items()
        if str(key) and isinstance(value, dict)
    }
    alert_thresholds = {
        str(key): float(value)
        for key, value in (rules.get("context_values") or {}).items()
        if str(key).endswith("_threshold") and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    capability_names = {
        str(item.get("id")): str(item.get("name") or item.get("label") or item.get("id"))
        for item in platform.store.repositories.entities.get_definitions("capability_def")
    }
    capabilities = [
        {
            "id": str(key),
            "label": capability_names.get(str(key), str(key).replace("_", " ")),
            "value": float(value),
        }
        for key, value in (agent.get("capabilities") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    capabilities.sort(key=lambda item: (-item["value"], item["id"]))
    location = {
        "id": current_id,
        "name": str(location_entity.get("name") or current.get("name") or current_id),
        "description": str(location_entity.get("description") or ""),
        "tags": [str(item) for item in (location_entity.get("tags") or current.get("tags") or [])],
        "safe": bool(location_entity.get("safe", current.get("safe", False))),
    }
    return {
        "status": "ok",
        "world_id": map_view.get("world_id"),
        "world_time": decision.get("world_time"),
        "player": player_projection,
        "inventory": decision.get("inventory", {}),
        "item_labels": item_labels,
        "needs": decision.get("needs", {}),
        "capabilities": capabilities,
        "goals": decision.get("progression_goals", [])[:8],
        "chapter": _player_chapter(platform, player_id),
        "onboarding": _onboarding_step(platform, agent),
        "active_task": active_task,
        "actions": actions[:24],
        "unavailable_actions": unavailable_actions[:12],
        "location": location,
        "world_properties": platform.store.repositories.metadata.get("world_properties", {}),
        "world_property_labels": world_property_labels,
        "alert_thresholds": alert_thresholds,
        "routes": routes,
        "map": map_view,
        "recent_outcomes": decision.get("recent_outcomes", [])[:6],
        "nearby_entities": nearby[:12],
        "nearby_participants": participants[:12],
        "recent_dialogue": platform.store.repositories.social.list_dialogue_exchanges(player_id, limit=4),
    }


class ControlService:
    """Small transport-neutral control API for CLI, HTTP or a future UI."""

    def __init__(self, db_path: Path, *, authoring_root: Path | None = None):
        self.platform = Platform(db_path)
        resolved = Path(db_path).resolve()
        container = resolved.parent.parent
        runtime_root = container.parent if container.name == "instances" else container
        if authoring_root is None:
            report = self.platform.store.repositories.metadata.get("bootstrap_report", {}) or {}
            source = report.get("config_dir") if isinstance(report, dict) else None
            if source:
                candidate = Path(str(source)).resolve().parent
                if candidate.is_dir():
                    authoring_root = candidate
        self.authoring = AuthoringService(authoring_root, runtime_root=runtime_root)

    def close(self) -> None:
        self.platform.close()

    def status(self) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.status()

    def health(self) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.health()

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        return self.platform.pause(reason)

    def resume(self, reason: str = "operator") -> dict[str, Any]:
        return self.platform.resume(reason)

    def step(self) -> dict[str, Any]:
        return self.platform.step()

    def inject(self, event_type: str, payload: dict[str, Any], actor_id: str | None = None) -> dict[str, Any]:
        return self.platform.inject_event(event_type, payload, actor_id)

    def enter_player(self, player_id: str, origin_id: str, name: str | None = None) -> dict[str, Any]:
        return self.platform.enter_player(player_id, origin_id, name=name)

    def world_info(self) -> dict[str, Any]:
        """Return the presentation facts a game client needs before entry."""

        with self.platform.store.read_transaction():
            metadata = self.platform.store.repositories.metadata
            setting = metadata.get("setting", {}) or {}
            world_id = str(metadata.get("world_id") or "")
            return {
                "status": "ok",
                "world_id": world_id,
                "instance_id": str(metadata.get("instance_id") or ""),
                "title": str(setting.get("name") or world_id),
                "premise": str(setting.get("premise") or ""),
                "genre": str(setting.get("genre") or ""),
                "locale": str(setting.get("locale") or "en"),
                "season": str((setting.get("world_clock") or {}).get("season") or ""),
                "calendar": dict(setting.get("calendar") or {}),
                "world_clock": dict(setting.get("world_clock") or {}),
                "world_minute": int(metadata.get("world_minute", 0) or 0),
            }

    def player_entry(self) -> dict[str, Any]:
        """Return generic player creation choices from the loaded world pack."""

        try:
            player = parse_player_template(self.platform.store.repositories.metadata.get("player_template", {}))
        except PlayerTemplateError as exc:
            return {"status": "rejected", "message": str(exc), "failed_check": "player_template"}
        entry_id = player["entry_location_id"]
        try:
            entry = self.platform.store.repositories.entities.get_entity(entry_id)
        except KeyError:
            entry = {}
        origins = [
            {
                "id": str(item["id"]),
                "name": str(item.get("name") or item["id"]),
                "description": str(item.get("description") or ""),
                "goal": str(item.get("starting_goal") or ""),
                "first_task": str((item.get("opening") or {}).get("summary") or ""),
                "traits": dict(item.get("traits") or {}),
                "capabilities": dict(item.get("capabilities") or {}),
                "inventory": dict(item.get("inventory") or {}),
            }
            for item in player.get("origins", [])
            if item.get("id")
        ]
        players = []
        for agent in self.platform.store.repositories.entities.list_agents():
            if str(agent.get("role") or "").casefold() != "player":
                continue
            state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
            location_id = str(state.get("location_id") or "")
            try:
                location_name = str(
                    self.platform.store.repositories.entities.get_entity(location_id).get("name") or location_id
                ) if location_id else ""
            except KeyError:
                location_name = location_id
            players.append(
                {
                    "id": str(agent.get("id") or ""),
                    "name": str(agent.get("name") or agent.get("id") or ""),
                    "location_id": location_id,
                    "location": location_name,
                    "status": str(state.get("status") or "idle"),
                }
            )
        return {
            "status": "ok",
            "world_id": self.platform.store.repositories.metadata.get("world_id"),
            "entry_location": {"id": entry_id, "name": str(entry.get("name") or entry_id)},
            "origins": origins,
            "players": players,
            "can_create_player": not players,
        }

    def player_view(self, player_id: str) -> dict[str, Any]:
        """Build a compact, playable projection for one player."""

        return build_player_view(self.platform, player_id)

    def player_act(self, body: dict[str, Any]) -> dict[str, Any]:
        player_id = str(body.get("player_id") or "").strip()
        if not player_id:
            return {"status": "rejected", "message": "player_id is required"}
        intent = BehaviorIntent.from_json(dict(body), actor_id=player_id)
        return self.platform.submit_behavior(intent)

    def player_wait(self, player_id: str, *, minutes: int = 1) -> dict[str, Any]:
        """Advance manual time for a player, stopping when their task ends."""

        try:
            self.platform.store.repositories.entities.get_agent(str(player_id))
        except KeyError:
            return {"status": "rejected", "message": "player does not exist", "failed_check": "player_id"}
        steps = max(1, min(int(minutes), 480))
        result: dict[str, Any] = {}
        for _ in range(steps):
            result = self.platform.step()
            agent = self.platform.store.repositories.entities.get_agent(str(player_id))
            state = agent.get("initial_state", {}) or {}
            if state.get("status") not in {"executing", "paused"} and not state.get("active_action") and not state.get("active_bt_instance"):
                break
        return result

    def metrics(self) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.metrics()

    def observer(self, event_limit: int = 20) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.observer_view(event_limit=event_limit)

    def map(self, *, viewer_id: str | None = None, observer: bool = False) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.map_view(viewer_id=viewer_id, observer=observer)

    def timeline(self, event_limit: int = 100, after_event_id: int = 0, include_ticks: bool = False, latest: bool = False) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.timeline(limit=event_limit, after_event_id=after_event_id, include_ticks=include_ticks, latest=latest)

    @staticmethod
    def _clip(text: Any, size: int = 140) -> str:
        words = " ".join(str(text or "").split())
        return words if len(words) <= size else words[: max(0, size - 1)] + "…"

    def _world_label(self) -> str:
        """What the world calls itself as the actor of an event nobody caused."""

        setting = self.platform.store.repositories.metadata.get("setting", {})
        return UiText.from_setting(setting).text("world_label")

    def _public_name(self, agent_id: str | None, world_label: str) -> str:
        if not agent_id:
            return world_label
        try:
            return str(self.platform.store.repositories.entities.get_entity(str(agent_id)).get("name") or agent_id)
        except KeyError:
            return str(agent_id)

    def observer_dialogue(self, limit: int = 20) -> dict[str, Any]:
        """Return the newest world-wide dialogue for the observer NPC feed.

        Only the public transcript fields are projected: speaker/listener
        names, topic, message and minute. Testimony metadata, memories and
        credibility internals stay out, matching the public timeline which
        already renders dialogue messages.
        """

        with self.platform.store.read_transaction():
            world_label = self._world_label()
            exchanges = self.platform.store.repositories.social.list_recent_dialogue_exchanges(limit=max(1, min(int(limit), 100)))
            items = [
                {
                    "dialogue_id": item.get("dialogue_id"),
                    "world_minute": item.get("world_minute"),
                    "topic": item.get("topic") or "general",
                    "message": item.get("message") or "",
                    "speaker_id": item.get("speaker_id"),
                    "speaker": self._public_name(item.get("speaker_id"), world_label),
                    "listener_id": item.get("listener_id"),
                    "listener": self._public_name(item.get("listener_id"), world_label),
                }
                for item in exchanges
            ]
        return {"status": "ok", "exchanges": items}

    def observer_decisions(self, limit: int = 20) -> dict[str, Any]:
        """Return recent NPC behavior decisions for the observer feed.

        Sourced from the same public behavior events the timeline renders
        (started with reason, completed/failed with result). Prompts, full
        decision payloads and private memories are never included.
        """

        wanted = ("behavior.started", "behavior.completed", "behavior.failed", "behavior.interrupted")
        with self.platform.store.read_transaction():
            world_label = self._world_label()
            events = self.platform.store.repositories.events.recent_events_by_types(wanted, limit=max(1, min(int(limit), 100)))
            labels = {
                str(item.get("id")): str(item.get("agent_label") or item.get("summary") or item.get("description") or item.get("id"))
                for item in self.platform.store.repositories.entities.get_definitions("bt_def")
            }
            items = []
            for event in events:
                behavior_id = str(event.get("behavior_id") or "")
                detail: str | None = None
                if event.get("event_type") == "behavior.started":
                    decision = event.get("decision") if isinstance(event.get("decision"), dict) else {}
                    detail = str(decision.get("reason") or "") or None
                else:
                    detail = str(event.get("result") or "") or None
                items.append({
                    "event_id": event.get("event_id"),
                    "world_minute": event.get("world_minute"),
                    "event_type": event.get("event_type"),
                    "actor_id": event.get("actor_id"),
                    "actor": self._public_name(event.get("actor_id"), world_label),
                    "behavior_id": behavior_id or None,
                    "behavior": labels.get(behavior_id, behavior_id or None),
                    "detail": self._clip(detail, 140) if detail else None,
                })
        return {"status": "ok", "decisions": items}

    def _public_event(self, event: dict[str, Any], world_label: str) -> dict[str, Any]:
        """Project one event for observer drill-down without private payloads."""

        decision = event.get("decision") if isinstance(event.get("decision"), dict) else {}
        reason = str(decision.get("reason") or "") or None
        summary = event.get("summary") or event.get("result") or reason
        return {
            "event_id": event.get("event_id"),
            "world_minute": event.get("world_minute"),
            "event_type": event.get("event_type"),
            "actor_id": event.get("actor_id"),
            "actor": self._public_name(event.get("actor_id"), world_label),
            "topic": event.get("topic"),
            "summary": self._clip(summary, 160) if summary else None,
        }

    def event_related(self, event_id: int) -> dict[str, Any]:
        """Return one public event plus the events sharing its causal links.

        Links are the intent/correlation/causation identifiers the Platform
        already stamps on events, so a decision can be traced back to its
        trigger and forward to its committed outcome without exposing
        prompts, full decision payloads or private memories.
        """

        try:
            target_id = int(event_id)
        except (TypeError, ValueError):
            return {"status": "rejected", "message": "event_id must be an integer", "failed_check": "event_id"}
        with self.platform.store.read_transaction():
            found = self.platform.store.repositories.events.events_by_ids([target_id])
            if not found:
                return {"status": "rejected", "message": f"event does not exist: {target_id}", "failed_check": "event_id"}
            event = found[0]
            world_label = self._world_label()
            links = {str(event.get(key)) for key in ("causation_id", "correlation_id", "intent_id") if event.get(key)}
            related = []
            if links:
                for item in self.platform.store.repositories.events.recent_events(limit=500):
                    if int(item.get("event_id", -1)) == target_id:
                        continue
                    item_links = {str(item.get(key)) for key in ("causation_id", "correlation_id", "intent_id") if item.get(key)}
                    if links & item_links:
                        related.append(self._public_event(item, world_label))
                        if len(related) >= 50:
                            break
            return {"status": "ok", "event": self._public_event(event, world_label), "related": related}

    def dialogue(self, speaker_id: str, listener_id: str, topic: str, message: str) -> dict[str, Any]:
        return self.platform.submit_dialogue(speaker_id, listener_id, topic, message)

    def trade_offer(self, offerer_id: str, recipient_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        return self.platform.create_trade_offer(offerer_id, recipient_id, give, want)

    def trade_accept(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        return self.platform.accept_trade_offer(recipient_id, offer_id)

    def trade_counter(self, counterparty_id: str, offer_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        return self.platform.counter_trade_offer(counterparty_id, offer_id, give, want)

    def trade_reject(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        return self.platform.reject_trade_offer(recipient_id, offer_id)

    def trade_offers(self, agent_id: str, *, include_closed: bool = False) -> list[dict[str, Any]]:
        with self.platform.store.read_transaction():
            return self.platform.store.repositories.social.list_trade_offers(str(agent_id), include_closed=bool(include_closed))

    def decide(self, agent_id: str, role: str = "agent_decision", provider: Any | None = None) -> dict[str, Any]:
        """Run one Agent turn for any CLI `decide` equivalent."""

        from ..agents.engine import AgentEngine
        from ..llm.gateway import LLMRouter

        actor = str(agent_id or "").strip()
        if not actor:
            return {"status": "rejected", "message": "agent_id is required", "failed_check": "agent_id"}
        chosen = str(role or "agent_decision").strip() or "agent_decision"
        try:
            active = provider if provider is not None else LLMRouter.from_default_config().provider_for_role(chosen)
        except Exception as exc:
            return {"status": "rejected", "message": str(exc), "failed_check": "provider"}
        try:
            turn = AgentEngine(self.platform, active).run(role=chosen, actor_id=actor)
        except (KeyError, ValueError) as exc:
            return {"status": "rejected", "message": str(exc), "failed_check": "agent_id"}
        return {"response": turn.response, "result": turn.result, "iterations": turn.iterations}

    def simulate(
        self,
        minutes: int,
        *,
        wait_cooldown_minutes: int = 30,
        llm_error_cooldown_minutes: int = 60,
        provider_name: str | None = None,
        providers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the synchronous CLI `simulate` loop without the background supervisor."""

        total = int(minutes)
        if total < 1:
            return {"status": "rejected", "message": "minutes must be at least 1", "failed_check": "minutes"}
        try:
            report = self.platform.run(
                total,
                providers=providers,
                provider_name=provider_name,
                wait_cooldown_minutes=int(wait_cooldown_minutes),
                llm_error_cooldown_minutes=int(llm_error_cooldown_minutes),
            )
        except Exception as exc:
            return {"status": "rejected", "message": str(exc), "failed_check": "simulate"}
        with self.platform.store.read_transaction():
            metrics = self.platform.metrics()
        return {"status": "ok", "report": report.as_dict(), "metrics": metrics}

    def audit(self) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.export_audit()

    def evolution_tasks(self) -> list[dict[str, Any]]:
        with self.platform.store.read_transaction():
            return self.platform.ready_evolution_tasks()

    def snapshot(self, destination: Path) -> str:
        return str(self.platform.snapshot(destination))

    def restore(self, source: Path) -> dict[str, Any]:
        return self.platform.restore_snapshot(source)

    def authoring_schema(self) -> dict[str, Any]:
        return self.authoring.schema()

    def authoring_catalog(self) -> dict[str, Any]:
        return self.authoring.list_packages()

    def authoring_create(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.create_instance(
            str(body.get("package_id") or ""),
            str(body.get("instance_id") or ""),
        )

    def authoring_compile(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.compile(body.get("template"), source=body.get("source"), source_path=body.get("source_path"), draft_revision=body.get("draft_revision", 0))

    def authoring_smoke(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.smoke(
            body.get("template"),
            source=body.get("source"),
            source_path=body.get("source_path"),
            draft_revision=body.get("draft_revision", 0),
            minutes=int(body.get("minutes", 1)),
        )

    def authoring_export(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.export(body.get("template"), source=body.get("source"), source_path=body.get("source_path"), draft_revision=body.get("draft_revision", 0))

    def authoring_open(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        requested = body.get("path")
        if requested:
            try:
                return self.authoring.open_package(Path(str(requested)))
            except (TypeError, ValueError, OSError) as exc:
                return {"status": "rejected", "errors": [str(exc)], "diagnostics": self.authoring.diagnostics([str(exc)])}
        report = self.platform.store.repositories.metadata.get("bootstrap_report", {}) or {}
        source = report.get("config_dir") if isinstance(report, dict) else None
        if not source:
            return {"status": "rejected", "errors": ["this instance has no readable source package"]}
        try:
            return self.authoring.open_package(Path(str(source)), trusted=True)
        except (TypeError, ValueError, OSError) as exc:
            return {"status": "rejected", "errors": [str(exc)], "diagnostics": self.authoring.diagnostics([str(exc)])}

    def authoring_validate(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = dict(body or {})
        if not body.get("package_id") and not body.get("path"):
            report = self.platform.store.repositories.metadata.get("bootstrap_report", {}) or {}
            source = report.get("config_dir") if isinstance(report, dict) else None
            if source:
                try:
                    return self.authoring.describe_package(None, str(Path(str(source)).parent))
                except (TypeError, ValueError, OSError):
                    pass
        return self.authoring.validate_package(body.get("package_id"), body.get("path"))

    def authoring_describe(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = dict(body or {})
        if not body.get("package_id") and not body.get("path"):
            report = self.platform.store.repositories.metadata.get("bootstrap_report", {}) or {}
            source = report.get("config_dir") if isinstance(report, dict) else None
            if source:
                try:
                    return self.authoring.describe_package(None, str(Path(str(source)).parent))
                except (TypeError, ValueError, OSError):
                    pass
        return self.authoring.describe_package(body.get("package_id"), body.get("path"))

    def authoring_build_world(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = dict(body or {})
        return self.authoring.build_world_package(
            body.get("template"),
            output=body.get("output"),
            overwrite=bool(body.get("overwrite", False)),
            instance_id=body.get("instance_id"),
            source_path=body.get("source_path"),
        )

    def authoring_build(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.build_from_brief(str(body.get("brief") or ""), provider_name=body.get("provider"))
