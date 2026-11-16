from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..actions.capacity import check_action_capacity
from ..behavior.estimator import BehaviorDurationEstimator
from ..behavior.recovery import failure_block_recovered
from ..intents.models import BehaviorIntent
from ..world.context import configured_context_values
from ..world.player_template import PlayerTemplateError, parse_player_template
from ..world.navigation import (
    configured_location_tags,
    nearest_location_with_tag,
    nearest_resource_node,
    nearest_safe_location,
    reachable_locations,
)
from ..world.time import world_time_view
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..actions.conditions import ConditionEngine
from .services import PlatformService


class RuntimeApiService(PlatformService):
    """Public runtime API and shared rule/consumer resolution."""

    def _new_rule_compiler(self) -> RuleCompiler:
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        aliases = {}
        for key in (self.store.repositories.metadata.get("world_properties", {}) or {}):
            aliases[str(key)] = str(key)
            aliases[str(key).replace("_", " ")] = str(key)
        for entity_type in ("resource", "material", "location", "object", "faction", "agent"):
            for item in self.store.repositories.entities.entities_of_type(entity_type):
                identifier, visible = str(item.get("id") or ""), str(item.get("name") or "")
                if identifier and visible:
                    aliases[visible] = identifier
        aliases.update({str(key): str(value) for key, value in (rules.get("condition_aliases", {}) or {}).items()})
        return RuleCompiler(
            rules,
            self.conditions,
            aliases,
            known_behaviors=list(self.store.repositories.entities.get_definitions("bt_def")),
            known_actions=list(self.store.repositories.entities.get_definitions("action_def")),
            world_keys=set((self.store.repositories.metadata.get("world_properties", {}) or {}).keys()),
        )

    def _resolve_evolution_consumer(self, consumer: str, actor_id: str | None = None) -> tuple[str, str | None]:
        """Resolve a configured wake target into a runtime role and actor.

        World packages use visible names at the model/configuration boundary.
        Evolution tasks use runtime roles and canonical entity IDs.  Keeping
        this conversion here prevents a rule such as ``wake: [梅岚]`` from
        becoming an unknown task role that the runner can never execute.
        """

        value = str(consumer or "").strip()
        if value in {"god", "story", "agent_decision", "agent_reflection", "world_builder"}:
            return value, actor_id
        if value.startswith("god_"):
            return "god", actor_id
        if value.startswith("story_"):
            return "story", actor_id
        canonical = self.targets.canonical_id(value)
        if canonical:
            try:
                entity = self.store.repositories.entities.get_entity(canonical)
            except KeyError:
                entity = None
            if entity and str(entity.get("entity_type")) == "agent":
                return "agent_decision", canonical
        return value, actor_id

    def run(
        self,
        minutes: int,
        *,
        providers: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_model: str | None = None,
        provider_max_completion_tokens: int | None = None,
        wait_cooldown_minutes: int = 30,
        llm_error_cooldown_minutes: int = 60,
        turn_observer: Callable[[Any, str], None] | None = None,
    ) -> Any:
        """Run the autonomous world loop owned by Platform.

        ``step`` remains the single-tick manual/test primitive. Normal CLI,
        service, and daemon callers use this method so scheduling, Agent
        wakeups, interactions, and evolution stay inside the runtime.
        """

        from ..llm.gateway import LLMRouter
        from ..simulation.runner import SimulationRunner

        if providers is None:
            router = LLMRouter.from_default_config()
            if provider_name:
                providers = {
                    role: router.new_provider_for_name(
                        provider_name,
                        provider_model,
                        max_completion_tokens=provider_max_completion_tokens,
                        role=role,
                    )
                    for role in ("agent_decision", "agent_reflection", "god", "story")
                }
            else:
                providers = {
                    "agent_decision": router.provider_for_role("agent_decision"),
                    "agent_reflection": router.provider_for_role("agent_reflection"),
                    "god": router.provider_for_role("god"),
                    "story": router.provider_for_role("story"),
                }
        return SimulationRunner(
            # ``run`` is exposed through the Platform facade but is owned by
            # this service.  The runner needs the facade so it can resolve
            # all public operations, not the service instance itself.
            self._context.host,
            providers,
            wait_cooldown_minutes=wait_cooldown_minutes,
            llm_error_cooldown_minutes=llm_error_cooldown_minutes,
            turn_observer=turn_observer,
        ).run(minutes)

    def status(self) -> dict[str, Any]:
        return {
            "status": self.store.repositories.metadata.get("runtime_status", "running"),
            "world_minute": int(self.store.repositories.metadata.get("world_minute", 0)),
            "instance_id": self.store.repositories.metadata.get("instance_id"),
        }

    def health(self) -> dict[str, Any]:
        """Return a read-only liveness check for external process probes."""

        database = str(self.store.connection.execute("PRAGMA quick_check").fetchone()[0])
        return {
            "status": "ok" if database == "ok" else "degraded",
            "database": database,
            "runtime": self.store.repositories.metadata.get("runtime_status", "running"),
            "world_minute": int(self.store.repositories.metadata.get("world_minute", 0)),
            "instance_id": self.store.repositories.metadata.get("instance_id"),
            "memory_retrieval": self.memory.status(),
        }

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        with self.store.transaction():
            self.store.repositories.metadata.set("runtime_status", "paused")
            minute = int(self.store.repositories.metadata.get("world_minute", 0))
            self._context.services["events"].append("runtime.paused", None, minute, {"reason": reason})
        return self.status()

    def resume(self, reason: str = "operator") -> dict[str, Any]:
        with self.store.transaction():
            self.store.repositories.metadata.set("runtime_status", "running")
            minute = int(self.store.repositories.metadata.get("world_minute", 0))
            self._context.services["events"].append("runtime.resumed", None, minute, {"reason": reason})
        return self.status()

    def inject_event(self, event_type: str, payload: dict[str, Any], actor_id: str | None = None) -> dict[str, Any]:
        if not event_type.strip():
            raise ValueError("event_type is required")
        minute = int(self.store.repositories.metadata.get("world_minute", 0))
        with self.store.transaction():
            event_id = self._context.services["events"].append(event_type, actor_id, minute, payload)
        return {"status": "accepted", "event_id": event_id, "world_minute": minute}

    def enter_player(self, player_id: str, origin_id: str, *, name: str | None = None) -> dict[str, Any]:
        """Instantiate a player or resume an existing one from the template.

        A player session is local and browser-held.  Re-entering an existing
        player id used to be impossible after the browser lost its session
        token; this method now treats an existing player as a resume and
        backfills any template capabilities, inventory or behaviors that were
        introduced after the character was created.
        """

        player_id = str(player_id or "").strip()
        if not player_id:
            raise ValueError("player_id is required")
        try:
            existing = self.store.repositories.entities.get_agent(player_id)
        except KeyError:
            existing = None
        if existing is not None and str(existing.get("role") or "") != "player":
            return {"status": "rejected", "message": "That id belongs to a world character, not a player.", "failed_check": "player_id"}
        try:
            template = parse_player_template(
                self.store.repositories.metadata.get("player_template", {}),
                require_entry=True,
            )
        except PlayerTemplateError as exc:
            return {
                "status": "rejected",
                "message": str(exc),
                "failed_check": "player_template",
                "retryable": False,
            }
        origins = template["origins"]
        origin = next((item for item in origins if item.get("id") == str(origin_id or "").strip()), None)
        if existing is None and origin is None:
            return {
                "status": "rejected",
                "message": "The selected origin is not configured.",
                "failed_check": "origin_id",
                "retryable": True,
                "allowed_values": [str(item["id"]) for item in origins],
            }
        if existing is not None:
            return self._resume_player(existing, template, origin, name=name)
        location_id = template["entry_location_id"]
        if not location_id:
            return {"status": "rejected", "message": "The player template does not configure an entry location.", "failed_check": "entry_location_id", "retryable": False}
        try:
            self.store.repositories.entities.get_entity(location_id)
        except KeyError:
            return {"status": "rejected", "message": "The configured player entry location does not exist.", "failed_check": "entry_location_id", "retryable": False}
        state_defaults = self.attribute_schema.state_defaults()
        state_defaults.update({"location_id": location_id, "status": "idle", "injuries": [], "conditions": [], "active_bt_instance": None, "active_action": None, "remaining_minutes": 0})
        needs = self.attribute_schema.need_defaults()
        for item in ((self.store.repositories.metadata.get("rules", {}) or {}).get("needs", []) or []):
            if isinstance(item, dict) and item.get("id"):
                needs.setdefault(str(item["id"]), float(item.get("target", 100)))
        state_defaults["reputation"] = template["initial_reputation"]
        agent = {
            "id": player_id,
            "name": name or player_id,
            "role": "player",
            "controller": template["controller"],
            "faction_ids": list(template["initial_faction_ids"]),
            "traits": dict(origin.get("traits") or {}),
            "ultimate_goal": origin.get("starting_goal") or "Find a meaningful place in this world.",
            "beliefs": [],
            "needs": needs,
            "knowledge_seed": list(template.get("knowledge_seed") or []),
            "initial_bt_ids": list(template.get("initial_bt_ids") or []),
            "capabilities": self._player_capabilities(template, origin),
            "initial_state": state_defaults,
            "inventory": self._player_inventory(template, origin),
        }
        minute = int(self.store.repositories.metadata.get("world_minute", 0))
        with self.store.transaction():
            self.store.repositories.entities.put_agent(player_id, agent, agent["name"])
            for fact in agent["knowledge_seed"]:
                self.store.repositories.memory.put_knowledge_fact(player_id, str(fact), {"fact": str(fact), "source_type": "player_template", "confidence": 1.0})
            self._context.services["events"].append("player.entered", player_id, minute, {"origin_id": origin_id, "location_id": location_id})
        return {"status": "accepted", "player_id": player_id, "origin_id": origin_id, "location_id": location_id, "resumed": False}

    @staticmethod
    def _player_capabilities(template: dict[str, Any], origin: dict[str, Any] | None) -> dict[str, float]:
        values = {str(key): float(value) for key, value in (template.get("capabilities") or {}).items()}
        values.update({str(key): float(value) for key, value in ((origin or {}).get("capabilities") or {}).items()})
        return values

    @staticmethod
    def _player_inventory(template: dict[str, Any], origin: dict[str, Any] | None) -> dict[str, float]:
        values = {str(key): float(value) for key, value in (template.get("inventory") or {}).items()}
        values.update({str(key): float(value) for key, value in ((origin or {}).get("inventory") or {}).items()})
        return values

    def sync_player_template(self, raw_template: dict[str, Any]) -> dict[str, Any]:
        """Reload a player entry template and backfill existing players.

        World packages evolve after an instance is created.  This explicit
        migration keeps the instance snapshot authoritative until the operator
        asks for the package's current player contract to be applied.
        """

        template = parse_player_template(raw_template, require_entry=True)
        updated: list[str] = []
        with self.store.transaction():
            self.store.repositories.metadata.set("player_template", dict(raw_template))
            for agent in self.store.repositories.entities.list_agents():
                if str(agent.get("role") or "") != "player":
                    continue
                before = (dict(agent.get("capabilities") or {}), dict(agent.get("inventory") or {}), list(agent.get("initial_bt_ids") or []), agent.get("controller"))
                capabilities = dict(agent.get("capabilities") or {})
                for key, value in template["capabilities"].items():
                    capabilities.setdefault(key, value)
                inventory = dict(agent.get("inventory") or {})
                for key, value in template["inventory"].items():
                    inventory.setdefault(key, value)
                behavior_ids = list(agent.get("initial_bt_ids") or [])
                for behavior_id in template["initial_bt_ids"]:
                    if behavior_id not in behavior_ids:
                        behavior_ids.append(behavior_id)
                agent["capabilities"] = capabilities
                agent["inventory"] = inventory
                agent["initial_bt_ids"] = behavior_ids
                agent["controller"] = template["controller"]
                after = (capabilities, inventory, behavior_ids, agent.get("controller"))
                if after != before:
                    self.store.repositories.entities.put_agent(str(agent["id"]), agent, str(agent.get("name") or agent["id"]))
                    updated.append(str(agent["id"]))
        return {
            "status": "ok",
            "updated_players": updated,
            "capabilities": list(template["capabilities"]),
            "inventory": list(template["inventory"]),
            "initial_bt_ids": list(template["initial_bt_ids"]),
            "controller": template["controller"],
        }

    def _resume_player(
        self,
        existing: dict[str, Any],
        template: dict[str, Any],
        origin: dict[str, Any] | None,
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        agent = dict(existing)
        if name and not str(agent.get("name") or "").strip():
            agent["name"] = name
        capabilities = dict(agent.get("capabilities") or {})
        for key, value in self._player_capabilities(template, origin).items():
            capabilities.setdefault(key, value)
        inventory = dict(agent.get("inventory") or {})
        for key, value in self._player_inventory(template, origin).items():
            inventory.setdefault(key, value)
        behavior_ids = list(agent.get("initial_bt_ids") or [])
        for behavior_id in template.get("initial_bt_ids") or []:
            if behavior_id not in behavior_ids:
                behavior_ids.append(str(behavior_id))
        changed = (
            capabilities != (agent.get("capabilities") or {})
            or inventory != (agent.get("inventory") or {})
            or behavior_ids != (agent.get("initial_bt_ids") or [])
            or agent.get("controller") != template["controller"]
        )
        agent["capabilities"] = capabilities
        agent["inventory"] = inventory
        agent["initial_bt_ids"] = behavior_ids
        agent["controller"] = template["controller"]
        if changed:
            minute = int(self.store.repositories.metadata.get("world_minute", 0))
            with self.store.transaction():
                self.store.repositories.entities.put_agent(agent["id"], agent, str(agent.get("name") or agent["id"]))
                self._context.services["events"].append("player.resumed", str(agent["id"]), minute, {
                    "origin_id": str((origin or {}).get("id") or ""),
                    "migrated": True,
                })
        location_id = str((agent.get("initial_state") or {}).get("location_id") or "")
        return {
            "status": "accepted",
            "player_id": str(agent["id"]),
            "origin_id": str((origin or {}).get("id") or ""),
            "location_id": location_id,
            "name": str(agent.get("name") or agent["id"]),
            "resumed": True,
            "migrated": changed,
        }

    def decision_view(self, agent_id: str, role: str = "agent_decision") -> dict[str, Any]:
        return self.view.build_decision_view(agent_id, role=role)

    def observer_view(self, *, event_limit: int = 20) -> dict[str, Any]:
        """Return public whole-world telemetry without private memories."""

        behavior_defs = {
            str(item.get("id")): item for item in self.store.repositories.entities.get_definitions("bt_def")
        }
        agents = []
        for agent in self.store.repositories.entities.list_agents():
            state = agent.get("initial_state", {}) or {}
            location_id = str(state.get("location_id") or "")
            try:
                location_name = str(self.store.repositories.entities.get_entity(location_id).get("name") or location_id)
            except KeyError:
                location_name = location_id or "unknown"
            behavior_id = str(state.get("active_behavior_id") or "")
            behavior = behavior_defs.get(behavior_id, {})
            agents.append({
                "id": agent["id"],
                "name": agent.get("name", agent["id"]),
                "role": agent.get("role", "agent"),
                "location_id": location_id,
                "location": location_name,
                "status": state.get("status", "idle"),
                "attributes": self.attribute_schema.values(state, agent.get("needs", {}) or {}),
                "active_behavior_id": behavior_id or None,
                "active_behavior": (
                    behavior.get("agent_label")
                    or behavior.get("summary")
                    or behavior.get("description")
                    or behavior_id
                    or None
                ),
                "remaining_minutes": state.get("remaining_minutes", 0),
            })
        public_events = []
        for event in self.store.repositories.events.recent_events(limit=max(1, min(int(event_limit), 100))):
            public = {key: event.get(key) for key in ("event_id", "event_type", "actor_id", "world_minute")}
            for key in ("summary", "behavior_id", "action_id", "result", "change_type", "effect", "activation"):
                if event.get(key) is not None:
                    public[key] = event[key]
            public_events.append(public)
        return {
            "status": "ok",
            "world_time": int(self.store.repositories.metadata.get("world_minute", 0)),
            "world_clock": world_time_view(self.store),
            "world_id": self.store.repositories.metadata.get("world_id"),
            "world_properties": self.store.repositories.metadata.get("world_properties", {}) or {},
            "agents": agents,
            "entity_counts": {entity_type: len(self.store.repositories.entities.entities_of_type(entity_type)) for entity_type in ("location", "agent", "faction", "resource", "object")},
            "recent_events": public_events,
            "private_data": "excluded",
        }

    def map_view(self, *, viewer_id: str | None = None, observer: bool = False) -> dict[str, Any]:
        """Return the visible location topology for text, TUI or HTTP clients."""

        from ..api.map_view import build_map_view

        return build_map_view(self.store, viewer_id=viewer_id, observer=observer)

    def timeline(self, *, limit: int = 100, after_event_id: int = 0, include_ticks: bool = False) -> dict[str, Any]:
        from ..api.timeline import build_timeline

        return build_timeline(self.store, limit=limit, after_event_id=after_event_id, include_ticks=include_ticks)

    def tick_snapshot(self) -> dict[str, Any]:
        """Expose the current read-only boundary for schedulers and audits."""

        return self.store.world_snapshot()

    def metrics(self) -> dict[str, Any]:
        return self.snapshots.metrics()

    def export_audit(self) -> dict[str, Any]:
        return self.snapshots.export_audit()

    def snapshot(self, destination: Path) -> Path:
        return self.snapshots.snapshot(destination)

    def restore_snapshot(self, source: Path) -> dict[str, Any]:
        self.snapshots.restore(source)
        return self.status()
