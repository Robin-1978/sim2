"""Compose the role-scoped context views a prompt is rendered from."""

from __future__ import annotations

from typing import Any

from ..actions.conditions import ConditionEngine
from ..actions.resources import ResourceLedger
from ..actions.targets import TargetResolver
from ..memory.service import MemoryService
from ..persistence.store import WorldStore
from ..world.drives import DriveCalculator
from ..world.navigation import route_minutes
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..world.time import world_time_view
from .action_availability import ActionAvailabilityMixin
from .action_text import label
from .behavior_view import BehaviorViewMixin
from .character_view import CharacterViewMixin
from .choices import ChoiceMixin
from .policies import default_platform_rules, default_protocol
from .progress_view import ProgressViewMixin
from .story_view import StoryViewMixin
from .tool_registry import tools_for_role
from .visible_text import VisibleTextMixin
from .world_view import WorldViewMixin


class AgentViewCompiler(
    BehaviorViewMixin,
    ActionAvailabilityMixin,
    CharacterViewMixin,
    ProgressViewMixin,
    ChoiceMixin,
    VisibleTextMixin,
    StoryViewMixin,
    WorldViewMixin,
):
    """Build a small, role-scoped context instead of exposing database rows."""

    def __init__(self, store: WorldStore):
        self.store = store
        self.entities_repo = store.repositories.entities
        self.memory_repo = store.repositories.memory
        self.activity_repo = store.repositories.activity
        self.events_repo = store.repositories.events
        self.evolution_repo = store.repositories.evolution
        self.social_repo = store.repositories.social
        self.conditions = ConditionEngine((self.store.repositories.metadata.get("rules", {}) or {}))
        self.rule_compiler = self._new_rule_compiler()
        self._rule_compiler_revision = int(self.store.repositories.metadata.get("world_revision", 0) or 0)
        self._visible_entity_revision: int | None = None
        self._visible_entity_replacements: list[tuple[str, str]] = []
        self._definition_cache: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self.resources = ResourceLedger(store)
        self.targets = TargetResolver(store)
        self.memory = MemoryService(store)

    def _new_rule_compiler(self) -> RuleCompiler:
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        aliases = {}
        for key in (self.store.repositories.metadata.get("world_properties", {}) or {}):
            aliases[str(key)] = str(key)
            aliases[str(key).replace("_", " ")] = str(key)
        for entity_type in ("resource", "material", "location", "object", "faction", "agent"):
            for item in self.entities_repo.entities_of_type(entity_type):
                identifier, visible = str(item.get("id") or ""), str(item.get("name") or "")
                if identifier and visible:
                    aliases[visible] = identifier
        aliases.update({str(key): str(value) for key, value in (rules.get("condition_aliases", {}) or {}).items()})
        return RuleCompiler(rules, self.conditions, aliases)

    def _definitions(self, table: str) -> list[dict[str, Any]]:
        """Read definitions once per committed world revision and table."""

        revision = int(self.store.repositories.metadata.get("world_revision", 0) or 0)
        key = (revision, str(table))
        cached = self._definition_cache.get(key)
        if cached is None:
            cached = self.entities_repo.get_definitions(str(table))
            self._definition_cache = {
                cache_key: value
                for cache_key, value in self._definition_cache.items()
                if cache_key[0] == revision
            }
            self._definition_cache[key] = cached
        return cached

    def _world_time(self, minute: int | None = None) -> dict[str, Any]:
        """Expose configured calendar meaning without leaking scheduler state."""

        return world_time_view(self.store, minute)

    def _prompt_sections(self, role: str) -> tuple[list[str], list[str]]:
        prompt = self.memory_repo.get_prompt("agent", {}) or {}
        prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
        protocol = prompt.get("protocol", {}) if isinstance(prompt, dict) else {}
        protocol_lines = list(protocol.get(role, []) or []) if isinstance(protocol, dict) else []
        if not protocol_lines:
            protocol_lines = default_protocol(role)
        role_rules = prompt.get("role_platform_rules", {}) if isinstance(prompt, dict) else {}
        rules = list((role_rules.get(role) if isinstance(role_rules, dict) else None) or prompt.get("platform_rules", []) or []) if isinstance(prompt, dict) else []
        if not rules:
            rules = default_platform_rules(role)
        attributes = self.store.repositories.metadata.get("attributes", []) or []
        attribute_rules = [
            f"{item.get('label', item.get('id'))}: {item.get('prompt')}"
            for item in attributes
            if isinstance(item, dict) and item.get("prompt")
        ]
        if role in {"agent_decision", "agent_reflection"}:
            rules = rules + attribute_rules
        return protocol_lines, rules

    def _role_tools(self, role: str) -> list[dict[str, Any]]:
        prompt = self.memory_repo.get_prompt("agent", {}) or {}
        prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
        policy = prompt.get("tool_policy", {}) if isinstance(prompt, dict) else {}
        names = policy.get(role) if isinstance(policy, dict) else None
        return tools_for_role(role, names if isinstance(names, list) else None)

    def _world_rule_labels(self) -> dict[str, str]:
        """Map package storage keys to visible names for model-facing policy text."""

        labels: dict[str, str] = {}
        for key in (self.store.repositories.metadata.get("world_properties", {}) or {}):
            labels[str(key)] = str(key).replace("_", " ")
        for entity_type in ("resource", "material", "location", "object", "faction", "agent"):
            for item in self.entities_repo.entities_of_type(entity_type):
                if item.get("id") and item.get("name"):
                    identifier = str(item["id"])
                    name = str(item["name"])
                    labels[identifier] = name if name != identifier else identifier.replace("_", " ")
        return labels

    def _attribute_values(self, agent: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        inventory = agent.get("inventory", {}) or {}
        for definition in self.store.repositories.metadata.get("attributes", []) or []:
            if not isinstance(definition, dict) or not definition.get("id"):
                continue
            kind = definition.get("kind")
            if kind == "state":
                value = state.get(definition["id"])
            elif kind == "need":
                value = (agent.get("needs", {}) or {}).get(definition["id"])
            elif kind == "inventory_group":
                value = sum(float(inventory.get(item, 0) or 0) for item in definition.get("items", []) or [])
            else:
                continue
            values.append({"label": definition.get("label", definition["id"]), "value": value, "unit": definition.get("unit", ""), "kind": kind, "meaning": definition.get("prompt", "")})
        return values

    def _attribute_schema(self) -> AttributeSchema:
        return AttributeSchema(self.store.repositories.metadata.get("attributes", []) or [], self.store.repositories.metadata.get("rules", {}) or {})

    def build_decision_view(
        self,
        agent_id: str,
        role: str = "agent_decision",
        *,
        source_event_id: int | None = None,
        include_diagnostics: bool = False,
    ) -> dict[str, Any]:
        rules = self._refresh_rule_compiler()
        try:
            agent = self.entities_repo.get_agent(agent_id)
        except KeyError:
            if role in {"god", "story", "world_builder"}:
                if role == "story":
                    return self._build_story_view(agent_id, role, source_event_id=source_event_id)
                return self._build_world_view(agent_id, role)
            raise
        frame = self._decision_frame(agent_id, agent, rules)
        for bt_id in frame.catalog_ids:
            entry = self._compile_behavior(frame, bt_id)
            if entry is not None:
                frame.available.append(entry)
        available, diagnostics = frame.available, frame.diagnostics
        state, location_id, current_location = frame.state, frame.location_id, frame.current_location
        bt_defs, action_defs = frame.bt_defs, frame.action_defs
        # Long-term goal tags are context only.  They must not promote a
        # behavior whose current drive, need, or resource conditions are weak.
        nearby = [item for item in (self.entities_repo.entities_at_location(location_id) if location_id else []) if item.get("id") != agent_id]
        known_routes = []
        for route_id in (current_location.get("routes", []) if isinstance(current_location, dict) else []):
            try:
                known_routes.append(self.entities_repo.get_entity(str(route_id)).get("name", str(route_id)))
            except KeyError:
                known_routes.append(str(route_id))
        active_behavior_id = state.get("active_behavior_id")
        active_action_id = state.get("active_action")
        active_behavior = next((item for item in bt_defs.values() if item.get("id") == active_behavior_id), {})
        active_action = next((item for item in action_defs.values() if item.get("id") == active_action_id), {})
        available_actions = [
            {"choice": action.get("agent_label") or label(str(action.get("id", "action"))), "name": action.get("agent_label") or label(str(action.get("id", "action"))), "summary": action.get("agent_summary") or label(str(action.get("id", "action")))}
            for action in action_defs.values()
        ]
        needs = agent.get("needs", {})
        drives = DriveCalculator(self.store.repositories.metadata.get("rules", {})).calculate(agent)
        drive_model = (self.store.repositories.metadata.get("rules", {}) or {}).get("motivation_model", {})
        memory_query, memories, short_term_memories, long_term_memories, known_facts = self._memory_context(frame, available, drives)
        research_tracks = self._research_tracks(frame)
        relationships = self._relationship_view(agent_id)
        recent_actions, recent_outcomes = self._recent_activity(agent_id, bt_defs)
        observation_facts = []
        if current_location:
            observation_facts.append(f"You are at {current_location.get('name', location_id)}.")
        observation_facts.extend(
            f"{item.get('name', item.get('id', 'Someone'))} is nearby."
            for item in nearby[:4]
        )
        prompt_protocol, platform_rules = self._prompt_sections(role)
        decision_constraints = self._decision_constraints(frame, needs)
        capability_update_candidates = self._capability_update_candidates(agent_id, agent, rules)
        pending_institutions, active_institutions, institution_ledgers = self._institution_view()
        story_goals, behavior_assignments, active_story_beats, active_story_threads = self._agent_story_view(agent_id, agent)
        result = {
            "context_version": "1", "role": role,
            "world_revision": int(self.store.repositories.metadata.get("world_revision", 0) or 0),
            "agent_revision": int(agent.get("agent_revision", 0) or 0),
            "prompt_protocol": prompt_protocol,
            "platform_rules": platform_rules,
            "world_time": self._world_time(),
            "self": {"id": agent_id, "name": agent.get("name", agent_id), "role": agent.get("role", "agent"), "status": state.get("status", "idle"), "location": current_location.get("name", location_id) if isinstance(current_location, dict) else location_id, "known_routes": known_routes, "attributes": self._attribute_values(agent, state), "goal": agent.get("ultimate_goal")},
            "identity": {"traits": agent.get("traits", {}), "beliefs": agent.get("beliefs", []), "capabilities": agent.get("capabilities", {})},
            "goal_tags": agent.get("goal_tags", []),
            "capability_history": self.activity_repo.list_capability_progress(agent_id, limit=5),
            "capability_update_candidates": capability_update_candidates,
            "needs": needs, "inventory": agent.get("inventory", {}), "injuries": state.get("injuries", []),
            "drives": drives[:5], "drive_model": drive_model, "observation": {"facts": observation_facts[:6], "source": "current_state"}, "nearby_entities": nearby[:8], "known_facts": known_facts, "research_tracks": research_tracks[:8], "memories": memories, "short_term_memories": short_term_memories, "long_term_memories": long_term_memories, "memory_query": memory_query, "recent_actions": recent_actions, "recent_outcomes": recent_outcomes, "direct_relationships": relationships,
            "active_task": {"bt": active_behavior.get("summary") or ("in progress" if active_behavior_id else None), "action": active_action.get("description") or (label(str(active_action_id)) if active_action_id else None), "remaining_minutes": state.get("remaining_minutes", 0)},
            "open_trade_offers": self._trade_offer_view(agent_id),
            "available_actions": available_actions if role == "agent_reflection" else [],
            "active_world_rules": self.rule_compiler.model_view(
                self._world_rule_labels(),
                agent=agent,
                context={
                    "location_id": location_id,
                    "current_location": current_location,
                    "recent_event_types": self._recent_event_types(),
                },
                world=self.store.repositories.metadata.get("world_properties", {}) or {},
            ),
            "pending_institutions": pending_institutions,
            "active_institutions": active_institutions,
            "institution_ledgers": institution_ledgers,
            "story_goals": story_goals,
            "progression_goals": self._progression_goals(agent, available=available, diagnostics=diagnostics, bt_defs=bt_defs),
            "behavior_assignments": behavior_assignments,
            "active_story_beats": active_story_beats,
            "active_story_threads": active_story_threads,
            "executable_behaviors": self._assign_choices(available) if role == "agent_decision" else [],
            "decision_constraints": decision_constraints if role == "agent_decision" else [],
            "last_platform_result": self._with_behavior_choice(
                self._humanize_record(state.get("last_platform_result")), bt_defs
            ),
            "tools": self._role_tools(role),
        }
        if include_diagnostics:
            result["unavailable_behavior_diagnostics"] = diagnostics
        return result

    def _refresh_rule_compiler(self) -> dict[str, Any]:
        """Rebuild the rule engine when a world change bumped the revision."""

        rules = self.store.repositories.metadata.get("rules", {}) or {}
        revision = int(self.store.repositories.metadata.get("world_revision", 0) or 0)
        if revision != self._rule_compiler_revision:
            self.conditions = ConditionEngine(rules)
            self.rule_compiler = self._new_rule_compiler()
            self._rule_compiler_revision = revision
        return rules

    def _route_distance(self, origin: str | None, destination: str | None) -> int:
        return route_minutes(self.store, origin, destination)

    def _recent_event_types(self, limit: int = 120) -> list[str]:
        rows = self.store.connection.execute(
            "SELECT DISTINCT event_type FROM event_log WHERE world_minute<=? ORDER BY event_id DESC LIMIT ?",
            (int(self.store.repositories.metadata.get("world_minute", 0)), max(1, min(int(limit), 500))),
        ).fetchall()
        return [str(row["event_type"]) for row in rows]
