from __future__ import annotations

import copy
import json
from typing import Any

from ..actions.conditions import ConditionEngine
from ..actions.capacity import check_action_capacity
from ..actions.resources import ResourceLedger
from ..actions.targets import TargetResolver
from ..agents.tools import tools_for_role
from ..behavior.estimator import BehaviorDurationEstimator
from ..memory.service import MemoryService
from ..persistence.store import WorldStore
from ..world.drives import DriveCalculator
from ..world.navigation import configured_location_tags, nearest_location_with_tag, nearest_resource_node, nearest_safe_location, reachable_locations
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..world.time import world_time_view


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _choice_key(value: Any) -> str:
    """Normalize a model-facing label for deterministic exact matching."""

    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _predicate_text(predicate: str) -> str:
    """Turn a configured predicate into one short sentence for the model."""

    text = str(predicate).replace("_", " ").replace(".", " ").strip()
    return f"Configured condition: {text[:1].upper() + text[1:]}."


def _display_item(value: Any) -> str:
    return str(value).removeprefix("context.").replace("_", " ")


def _display_number(value: Any) -> str:
    """Keep preformatted context strings free of binary float tails."""

    if isinstance(value, float):
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _cost_text(action: dict[str, Any], recipe: dict[str, Any] | None = None) -> list[str]:
    costs = action.get("costs", {})
    out: list[str] = []
    for phase, values in costs.items():
        if not values:
            continue
        if isinstance(values, dict):
            for key, value in values.items():
                if key == "recipe_inputs" and recipe:
                    inputs = ", ".join(f"{item.get('quantity', 1)} {_display_item(item.get('item', 'input'))}" for item in recipe.get("inputs", []) or [])
                    out.append(f"Recipe inputs: {inputs or 'configured inputs'} (reserved before start)")
                    continue
                if key == "item_parameter":
                    out.append(f"One selected item (reserved before start)")
                    continue
                if key == "resource_parameter":
                    amount = values.get("resource_amount", 1)
                    out.append(f"{amount} units of the selected resource (reserved before start)")
                    continue
                if key == "resource_amount":
                    continue
                shown_value = "the selected target" if str(value).endswith("_id") else _display_item(value)
                out.append(f"{_display_item(key).title()} {shown_value} ({phase.replace('_', ' ')})")
        else:
            out.append(f"{phase.replace('_', ' ')}: {values}")
    return out or ["No direct resource cost."]


def _effect_text(action: dict[str, Any], recipe: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> list[str]:
    impacts: list[str] = []
    parameters = parameters or {}

    def resolved(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("context."):
            return parameters.get(value.removeprefix("context."), value)
        return value

    for effect in action.get("effects", []) or []:
        effect_type = str(effect.get("type") or effect.get("op") or "")
        if effect_type == "need_delta":
            impacts.append(f"{_display_item(effect.get('need', 'need')).title()} {float(effect.get('delta', 0)):g}")
        elif effect_type == "state_delta":
            impacts.append(f"{_display_item(effect.get('state', 'state')).title()} {float(effect.get('delta', 0)):g}")
        elif effect_type == "inventory_add":
            impacts.append(f"Inventory +{effect.get('quantity', 1)} {_display_item(resolved(effect.get('item', 'item')))}")
        elif effect_type == "inventory_remove":
            impacts.append(f"Inventory -{effect.get('quantity', 1)} {_display_item(resolved(effect.get('item', 'item')))}")
        elif effect_type in {"attribute_delta", "attribute_set"}:
            attribute = _display_item(effect.get("attribute", "attribute"))
            if effect_type == "attribute_set":
                impacts.append(f"{attribute.title()} becomes {effect.get('value')}")
            elif effect.get("formula"):
                direction = "decreases" if str(effect.get("direction", "add")) == "subtract" else "increases"
                impacts.append(f"{attribute.title()} {direction} by the configured formula")
            else:
                impacts.append(f"{attribute.title()} changes by {effect.get('delta', 0)}")
        elif effect_type == "location_set":
            impacts.append("Arrive at the selected location")
        elif effect_type == "world_property_delta":
            impacts.append(f"World {_display_item(effect.get('property', 'property'))} changes by {effect.get('delta', effect.get('value', 0))}")
        elif effect_type:
            impacts.append(f"Platform applies configured {effect_type.replace('_', ' ')} effect")
    if recipe:
        outputs = ", ".join(f"{item.get('quantity', 1)} {_display_item(item.get('item', 'output'))}" for item in recipe.get("outputs", []) or [])
        if outputs:
            impacts.append(f"Produces {outputs}")
    progress = action.get("progress") or {}
    if isinstance(progress, dict) and progress.get("key"):
        impacts.append(f"Adds {progress.get('amount', 1)} progress; {progress.get('required', 1)} total progress completes the discovery")
    if action.get("information"):
        impacts.append("Records the configured information result")
    return list(dict.fromkeys(impacts)) or ["Platform applies configured state effects and records the result."]


def _action_view(action: dict[str, Any], recipe: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    success = action.get("success", {})
    failure = action.get("failure", {})
    return {
        "name": _label(action.get("id", "action")),
        "summary": action.get("summary") or action.get("description") or _label(action.get("id", "action")),
        "when": action.get("when") or action.get("precondition_text") or [_predicate_text(item) for item in action.get("preconditions", [])] or ["Platform confirms the configured requirements."],
        "requires": action.get("requirements") or action.get("requirement_text") or [_predicate_text(item) for item in action.get("preconditions", [])] or ["Platform confirms the configured requirements."],
        "target": "Platform selects a permitted target from the current view.",
        "time": f"{action.get('duration', {}).get('minutes', 'variable')} minutes",
        "cost": _cost_text(action, recipe),
        "impact": _effect_text(action, recipe, parameters),
        "success": [str(success.get("result", "configured success")).replace("_", " ")],
        "failure": [str(failure.get("result", "configured failure")).replace("_", " ")],
        "interrupt": action.get("interrupt_policy", "cancel"),
    }


class AgentViewCompiler:
    """Build a small, role-scoped context instead of exposing database rows."""

    def __init__(self, store: WorldStore):
        self.store = store
        self.conditions = ConditionEngine((self.store.get_meta("rules", {}) or {}))
        self.rule_compiler = self._new_rule_compiler()
        self.resources = ResourceLedger(store)
        self.targets = TargetResolver(store)
        self.memory = MemoryService(store)

    def _new_rule_compiler(self) -> RuleCompiler:
        rules = self.store.get_meta("rules", {}) or {}
        aliases = {}
        for key in (self.store.get_meta("world_properties", {}) or {}):
            aliases[str(key)] = str(key)
            aliases[str(key).replace("_", " ")] = str(key)
        for entity_type in ("resource", "material", "location", "object", "faction", "agent"):
            for item in self.store.entities_of_type(entity_type):
                identifier, visible = str(item.get("id") or ""), str(item.get("name") or "")
                if identifier and visible:
                    aliases[visible] = identifier
        aliases.update({str(key): str(value) for key, value in (rules.get("condition_aliases", {}) or {}).items()})
        return RuleCompiler(rules, self.conditions, aliases)

    def _interaction_context(self, agent: dict[str, Any], participants: list[dict[str, Any]]) -> dict[str, Any]:
        """Expose configured relationship signals without naming story entities."""

        rules = self.store.get_meta("rules", {}) or {}
        conflict_types = {str(item) for item in (rules.get("conflict_relation_types", ["conflict"]) or [])}
        participant_ids = {str(item.get("id")) for item in participants}
        available = False
        for relation in self.store.get_related_relationships(agent["id"], limit=50):
            if str(relation.get("person")) not in participant_ids or str(relation.get("relation")) not in conflict_types:
                continue
            value = relation.get("relation_strength", relation.get("value", 0))
            try:
                available = float(value) < 0
            except (TypeError, ValueError):
                available = False
            if available:
                break
        return {"conflict": {"available": available}}

    @staticmethod
    def _target_parameter_keys(behavior: dict[str, Any], behavior_defs: dict[str, dict[str, Any]] | None = None) -> set[str]:
        """Return target fields from every configured BT control-flow node."""

        keys: set[str] = set()
        target_context_names = {"target_id", "patient_id", "protected_target_id", "counterparty_id", "listener_id"}
        visited: set[str] = set()

        def visit(nodes: list[Any], owner_id: str | None = None) -> None:
            if owner_id:
                if owner_id in visited:
                    return
                visited.add(owner_id)
            for raw in nodes or []:
                node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
                node_type = node.get("type")
                if node_type == "action":
                    for key, value in (node.get("parameters") or {}).items():
                        if isinstance(value, str) and value.startswith("context.") and value.removeprefix("context.") in target_context_names:
                            keys.add(str(key))
                elif node_type in {"sequence", "parallel", "root", "selector"}:
                    visit(node.get("children", []) or [])
                elif node_type in {"behavior", "subtree"} and behavior_defs and node.get("behavior_id"):
                    nested_id = str(node["behavior_id"])
                    nested = behavior_defs.get(nested_id) or {}
                    visit(nested.get("nodes", []) or [], nested_id)

        visit(behavior.get("nodes", []) or [], str(behavior.get("id", "")) or None)
        return keys

    def _iter_behavior_actions(self, behavior_id: str, *, seen: set[str] | None = None):
        """Yield configured Action nodes from any BT control-flow shape."""

        seen = set(seen or set())
        if behavior_id in seen:
            return
        seen.add(behavior_id)
        definitions = {item["id"]: item for item in self.store.get_definitions("bt_def")}
        behavior = definitions.get(behavior_id) or {}

        def visit(nodes: list[Any], branch_seen: set[str]):
            for raw in nodes or []:
                node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
                node_type = node.get("type")
                if node_type == "action" and node.get("action_id"):
                    yield node
                elif node_type in {"behavior", "subtree"} and node.get("behavior_id"):
                    nested_id = str(node["behavior_id"])
                    if nested_id not in branch_seen:
                        nested = definitions.get(nested_id) or {}
                        yield from visit(nested.get("nodes", []) or [], branch_seen | {nested_id})
                elif node_type in {"sequence", "parallel", "root"}:
                    yield from visit(node.get("children", []) or [], branch_seen)
                elif node_type == "selector":
                    # Match Platform's deterministic first-branch behavior
                    # until conditional selector evaluation is configured.
                    children = node.get("children", []) or []
                    if children:
                        yield from visit([children[0]], branch_seen)

        yield from visit(behavior.get("nodes", []) or [], seen)

    def _world_time(self, minute: int | None = None) -> dict[str, Any]:
        """Expose configured calendar meaning without leaking scheduler state."""

        return world_time_view(self.store, minute)

    def _prompt_sections(self, role: str) -> tuple[list[str], list[str]]:
        prompt = self.store.get_prompt("agent", {}) or {}
        prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
        protocol = prompt.get("protocol", {}) if isinstance(prompt, dict) else {}
        protocol_lines = list(protocol.get(role, []) or []) if isinstance(protocol, dict) else []
        role_rules = prompt.get("role_platform_rules", {}) if isinstance(prompt, dict) else {}
        rules = list((role_rules.get(role) if isinstance(role_rules, dict) else None) or prompt.get("platform_rules", []) or []) if isinstance(prompt, dict) else []
        attributes = self.store.get_meta("attributes", []) or []
        attribute_rules = [
            f"{item.get('label', item.get('id'))}: {item.get('prompt')}"
            for item in attributes
            if isinstance(item, dict) and item.get("prompt")
        ]
        if role in {"agent_decision", "agent_reflection"}:
            rules = rules + attribute_rules
        return protocol_lines, rules

    def _role_tools(self, role: str) -> list[dict[str, Any]]:
        prompt = self.store.get_prompt("agent", {}) or {}
        prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
        policy = prompt.get("tool_policy", {}) if isinstance(prompt, dict) else {}
        names = policy.get(role) if isinstance(policy, dict) else None
        return tools_for_role(role, names if isinstance(names, list) else None)

    def _world_rule_labels(self) -> dict[str, str]:
        """Map package storage keys to visible names for model-facing policy text."""

        labels: dict[str, str] = {}
        for key in (self.store.get_meta("world_properties", {}) or {}):
            labels[str(key)] = str(key).replace("_", " ")
        for entity_type in ("resource", "material", "location", "object", "faction", "agent"):
            for item in self.store.entities_of_type(entity_type):
                if item.get("id") and item.get("name"):
                    identifier = str(item["id"])
                    name = str(item["name"])
                    labels[identifier] = name if name != identifier else identifier.replace("_", " ")
        return labels

    def _attribute_values(self, agent: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        inventory = agent.get("inventory", {}) or {}
        for definition in self.store.get_meta("attributes", []) or []:
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
        return AttributeSchema(self.store.get_meta("attributes", []) or [], self.store.get_meta("rules", {}) or {})

    @staticmethod
    def _assign_choices(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give the current view short, human-readable, collision-free choices.

        ``ref`` remains an internal Platform value.  The choice is the only
        behavior selector rendered to the model.  A suffix is used only when
        two configured behaviors have the same visible name.
        """

        counts: dict[str, int] = {}
        for behavior in behaviors:
            base = str(behavior.get("choice") or behavior.get("name") or "Choose")
            key = _choice_key(base) or "choice"
            counts[key] = counts.get(key, 0) + 1
        seen: dict[str, int] = {}
        result: list[dict[str, Any]] = []
        for behavior in behaviors:
            item = dict(behavior)
            base = str(item.get("choice") or item.get("name") or "Choose")
            key = _choice_key(base) or "choice"
            seen[key] = seen.get(key, 0) + 1
            item["choice"] = base if counts[key] == 1 else f"{base} (option {seen[key]})"
            result.append(item)
        return result

    def resolve_behavior_choice(self, agent_id: str, choice: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve a visible choice to an internal behavior only in Platform.

        Matching is exact after case/spacing/punctuation normalization.  No
        fuzzy matching is used because a wrong behavior can have side effects.
        ``behavior:<id>`` is accepted only as a private compatibility path for
        old integrations; it is never advertised in the model context.
        """

        value = str(choice or "").strip()
        view = self.build_decision_view(agent_id, role="agent_decision")
        behaviors = view.get("executable_behaviors", [])
        if value.startswith("behavior:"):
            matches = [item for item in behaviors if item.get("ref") == value]
        else:
            key = _choice_key(value)
            matches = [item for item in behaviors if _choice_key(item.get("choice") or item.get("name")) == key]
        if len(matches) == 1:
            return matches[0], None
        available = [str(item.get("choice") or item.get("name")) for item in behaviors]
        if not matches:
            return None, {
                "status": "rejected",
                "failed_check": "behavior_choice",
                "retryable": True,
                "message": "Choose one behavior by its exact visible name.",
                "available_choices": available,
            }
        return None, {
            "status": "rejected",
            "failed_check": "ambiguous_behavior_choice",
            "retryable": True,
            "message": "That behavior name is ambiguous; choose the displayed option.",
            "available_choices": available,
        }

    def _trade_offer_view(self, agent_id: str) -> list[dict[str, Any]]:
        offers: list[dict[str, Any]] = []
        for offer in self.store.list_trade_offers(agent_id, include_closed=False, limit=8):
            other_id = offer["recipient_id"] if offer["offerer_id"] == agent_id else offer["offerer_id"]
            try:
                other_name = self.store.get_agent(other_id).get("name", other_id)
            except KeyError:
                other_name = other_id
            direction = "to" if offer["offerer_id"] == agent_id else "from"
            give = ", ".join(f"{amount} {item}" for item, amount in offer.get("give", {}).items())
            want = ", ".join(f"{amount} {item}" for item, amount in offer.get("want", {}).items())
            offers.append({
                "offer_id": offer["offer_id"],
                "choice": f"Offer {direction} {other_name}: give {give}; want {want}",
                "summary": f"Trade {give} for {want}.",
                "round": offer.get("round", 1),
                "status": offer.get("status", "open"),
            })
        return offers

    def resolve_trade_offer_choice(self, agent_id: str, choice: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        value = str(choice or "").strip()
        offers = self._trade_offer_view(agent_id)
        if value in {str(item.get("offer_id")) for item in offers}:
            matches = [item for item in offers if str(item.get("offer_id")) == value]
        else:
            key = _choice_key(value)
            matches = [item for item in offers if _choice_key(item.get("choice")) == key]
        available = [str(item.get("choice")) for item in offers]
        if len(matches) == 1:
            return matches[0], None
        return None, {
            "status": "rejected",
            "failed_check": "trade_offer_choice",
            "retryable": True,
            "message": "Choose one exact open trade offer from the current interaction list.",
            "available_choices": available,
        }

    def _humanize_fact(self, fact: Any) -> Any:
        """Replace compact config references in facts with visible names."""

        if isinstance(fact, str) and ":" in fact:
            prefix, value = fact.split(":", 1)
            try:
                visible_value = self.store.get_entity(value).get("name", value)
            except KeyError:
                visible_value = value.replace("_", " ")
            return f"{prefix.replace('_', ' ').title()}: {visible_value}"
        if isinstance(fact, dict) and "fact" in fact:
            item = dict(fact)
            item["fact"] = self._humanize_fact(item["fact"])
            return item
        return fact

    def build_decision_view(self, agent_id: str, role: str = "agent_decision", *, source_event_id: int | None = None) -> dict[str, Any]:
        rules = self.store.get_meta("rules", {}) or {}
        self.conditions = ConditionEngine(rules)
        self.rule_compiler = self._new_rule_compiler()
        try:
            agent = self.store.get_agent(agent_id)
        except KeyError:
            if role in {"god", "story", "world_builder"}:
                if role == "story":
                    return self._build_story_view(agent_id, role, source_event_id=source_event_id)
                return self._build_world_view(agent_id, role)
            raise
        bt_defs = {x["id"]: x for x in self.store.get_definitions("bt_def")}
        action_defs = {x["id"]: x for x in self.store.get_definitions("action_def")}
        recipe_defs = {x["id"]: x for x in self.store.get_definitions("recipe_def")}
        estimator = BehaviorDurationEstimator(
            action_defs,
            bt_defs,
            recipe_defs,
            self._route_distance,
            capability_modifiers=(self.store.get_meta("rules", {}) or {}).get("capability_modifiers", {}),
        )
        available = []
        fallback_ids = (self.store.get_meta("rules", {}) or {}).get("fallback_behavior_ids", [])
        catalog_ids = list(dict.fromkeys(
            list(agent.get("initial_bt_ids", []))
            + list(agent.get("unlocked_bt_ids", []))
            + list(fallback_ids)
        ))
        state = agent.get("initial_state", {})
        location_id = state.get("location_id")
        safe_location_id = nearest_safe_location(self.store, location_id)
        resource_node = nearest_resource_node(self.store, location_id)
        health_location_id = nearest_location_with_tag(self.store, location_id, configured_location_tags(self.store, "health_service", {"health", "clinic"}))
        known_route_destinations = reachable_locations(self.store, location_id)
        mapped_areas = self.store.explored_areas(agent_id)
        researched_sources = self.store.researched_sources(agent_id)
        known_fact_keys = [item.get("fact_key") for item in self.store.list_knowledge_facts(agent_id, limit=1000) if item.get("fact_key")]
        current_location = {}
        if location_id:
            try:
                current_location = self.store.get_entity(location_id)
            except KeyError:
                pass
        participants = self.targets.visible_agents(agent)
        # A single visible participant is safe to infer. With multiple
        # participants, one candidate is used only to validate the BT; the
        # Agent must submit an explicit visible target.
        default_target = participants[0] if len(participants) == 1 else None
        validation_target = default_target or (participants[0] if participants else None)
        for bt_id in catalog_ids:
            bt = bt_defs.get(bt_id)
            if not bt:
                continue
            rules = self.store.get_meta("rules", {}) or {}
            condition_context = {
                "current_location": current_location,
                "location_id": location_id,
                "safe_location_id": safe_location_id,
                "resource_location_id": (resource_node or {}).get("location_id"),
                "resource_tag": (resource_node or {}).get("resource_tag"),
                "health_location_id": health_location_id,
                "known_route_destinations": known_route_destinations,
                "mapped_areas": mapped_areas,
                "researched_sources": researched_sources,
                "known_fact_keys": known_fact_keys,
                "inventory": agent.get("inventory", {}) or {},
                "food_items": rules.get("food_items", []) or [],
                "satiety_alert_threshold": float(rules.get("satiety_alert_threshold", 40) or 40),
                "recent_event_types": self._recent_event_types(),
                "day_phase": self._world_time().get("day_phase"),
                "target": {"id": validation_target.get("id"), "available": True} if validation_target else {"available": False},
            }
            condition_context.update(self._interaction_context(agent, participants))
            if any(not self.conditions.evaluate(str(predicate), agent=agent, target=condition_context["target"], context=condition_context, world=self.store.get_meta("world_properties", {}) or {}) for predicate in bt.get("entry_conditions", []) or []):
                continue
            if self.rule_compiler.behavior_denial(
                behavior=bt,
                agent=agent,
                context=condition_context,
                world=self.store.get_meta("world_properties", {}) or {},
            ):
                continue
            cooldown_minutes = max(0, int(bt.get("cooldown_minutes", 0) or 0))
            if cooldown_minutes:
                last_minute = self.store.last_behavior_minute(agent_id, bt_id)
                current_minute = int(self.store.get_meta("world_minute", 0))
                if last_minute is not None and current_minute - last_minute < cooldown_minutes:
                    continue
            steps = []
            costs = []
            impacts = []
            action_successes = []
            action_failures = []
            action_unavailable = False
            simulated_agent = copy.deepcopy(agent)
            estimator.capabilities = simulated_agent.get("capabilities", {}) or {}
            simulated_location = dict(current_location)
            for node in self._iter_behavior_actions(bt_id):
                action = action_defs.get(node.get("action_id"))
                if not action:
                    continue
                simulated_state = simulated_agent.setdefault("initial_state", {})
                parameters = self._view_action_parameters(node.get("parameters") or {}, simulated_state, simulated_agent.get("inventory", {}) or {}, rules.get("food_items", []) or [], safe_location_id=safe_location_id, resource_node=resource_node, health_location_id=health_location_id, default_target_id=(validation_target or {}).get("id"))
                # A configured destination Action is complete when the actor
                # is already there. It contributes no visible step to the
                # surrounding BT, while the remaining steps stay executable.
                if (
                    action.get("requires_destination")
                    and action.get("completion", {}).get("if_already_at_destination")
                    and parameters.get("location_id") == simulated_state.get("location_id")
                ):
                    continue
                action_minutes = estimator._action_minutes(action, parameters, simulated_state.get("location_id"))
                if not self._action_available(simulated_agent, action, parameters, simulated_location, rules.get("food_items", []) or [], action_minutes):
                    action_unavailable = True
                    break
                recipe = recipe_defs.get(str(parameters.get("recipe_id"))) if action.get("id") == "craft_recipe" else None
                view = _action_view(action, recipe, parameters)
                steps.append(view["name"])
                costs.extend(view["cost"])
                impacts.extend(view["impact"][:3])
                action_successes.extend(view["success"])
                action_failures.extend(view["failure"])
                schema = self._attribute_schema()
                for cost_key, rate in (action.get("costs", {}).get("per_minute", {}) or {}).items():
                    if cost_key == "time" or not isinstance(rate, (int, float)) or rate <= 0:
                        continue
                    target_state = simulated_state if cost_key in simulated_state else simulated_agent.get("needs", {})
                    if cost_key not in target_state:
                        continue
                    current = float(target_state.get(cost_key, schema.default(str(cost_key), 0)) or 0)
                    delta = current + float(rate) * action_minutes if schema.cost_mode(str(cost_key)) == "increase" else current - float(rate) * action_minutes
                    target_state[cost_key] = schema.clamp(str(cost_key), delta)
                # Validate a sequential BT against the state produced by its
                # earlier configured steps. This keeps a travel-then-service
                # behavior executable without adding action-specific code.
                if action.get("id") == "travel_to" and parameters.get("location_id"):
                    simulated_state["location_id"] = str(parameters["location_id"])
                    try:
                        simulated_location = self.store.get_entity(str(parameters["location_id"]))
                    except KeyError:
                        simulated_location = {}
            if action_unavailable:
                continue
            if not steps:
                continue
            target_text = "Platform resolves permitted targets from the current view."
            target_keys = self._target_parameter_keys(bt, bt_defs)
            target_required = bool(target_keys) and len(participants) > 1
            target_options = [{"name": str(item.get("name", item.get("id", "participant")))} for item in participants[:8]]
            bindings = bt.get("parameter_bindings", {}) or {}
            binding_values = {
                "nearest_safe_location": safe_location_id,
                "nearest_health_service": health_location_id,
                "nearest_resource_node": (resource_node or {}).get("location_id"),
                "nearest_resource_tag": (resource_node or {}).get("resource_tag"),
            }
            bound_value = next((binding_values.get(str(resolver)) for resolver in bindings.values() if binding_values.get(str(resolver))), None)
            if bound_value and any(str(resolver) == "nearest_safe_location" for resolver in bindings.values()):
                try:
                    target_text = f"Configured safe location: {self.store.get_entity(bound_value).get('name', bound_value)}."
                except KeyError:
                    target_text = "Configured safe location is available."
            elif bound_value and any(str(resolver) == "nearest_health_service" for resolver in bindings.values()):
                try:
                    target_text = f"Health service: {self.store.get_entity(bound_value).get('name', bound_value)}."
                except KeyError:
                    target_text = "Configured health service is available."
            elif bound_value and any(str(resolver) == "nearest_resource_node" for resolver in bindings.values()):
                try:
                    target_text = f"Resource node: {self.store.get_entity(bound_value).get('name', bound_value)}."
                except KeyError:
                    target_text = "Configured resource node is available."
            elif resource_node and any(
                node.get("type") == "action"
                and any(value == "context.resource_tag" for value in (node.get("parameters") or {}).values())
                for node in bt.get("nodes", []) or []
            ):
                try:
                    resource = self.store.get_entity(resource_node["resource_tag"])
                    location = self.store.get_entity(resource_node["location_id"])
                    target_text = f"Resource: {resource.get('name', resource_node['resource_tag'])} at {location.get('name', resource_node['location_id'])}. Platform reserves the configured amount."
                except KeyError:
                    target_text = "A configured resource is available at the nearest known node."
            elif target_keys and participants:
                names = ", ".join(str(item.get("name", item.get("id"))) for item in participants[:8])
                if target_required:
                    target_text = f"Choose one participant explicitly: {names}. Platform will reject a missing or ambiguous target."
                else:
                    target_text = f"Participant: {names}. Platform uses this participant unless another permitted target is supplied."
            available.append({
                "ref": f"behavior:{bt_id}",
                "registration": "platform_validated",
                "catalog_source": bt.get("kind", "configured"),
                "name": bt.get("summary") or _label(bt_id),
                "choice": bt.get("agent_label") or bt.get("summary") or _label(bt_id),
                "summary": bt.get("description") or bt.get("summary") or _label(bt_id),
                "purpose": bt.get("purpose") or bt.get("description") or bt.get("summary") or _label(bt_id),
                "when": bt.get("when") or bt.get("entry_descriptions") or [_predicate_text(item) for item in bt.get("entry_conditions", [])] or ["The current goal makes this useful."],
                "requires": bt.get("requirements") or bt.get("requirement_descriptions") or [_predicate_text(item) for item in bt.get("entry_conditions", [])] or ["Platform confirms all step requirements."],
                "target": target_text,
                "target_required": target_required,
                "target_options": target_options if target_required else [],
                "parameters": [
                    f"{name} ({'required' if spec.get('required', False) else 'optional'} {spec.get('type', 'value')}): {spec.get('description', 'configured input')}"
                    for name, raw_spec in (bt.get("agent_parameters", {}) or {}).items()
                    for spec in [raw_spec if isinstance(raw_spec, dict) else {"type": str(raw_spec)}]
                ],
                "steps": steps[:5],
                "time": f"{estimator.behavior_minutes(bt_id, {'safe_location_id': safe_location_id} if safe_location_id else {}, location_id)} minutes",
                "cost": list(dict.fromkeys(costs))[:6] or ["Costs are defined by the selected steps."],
                "impact": list(dict.fromkeys(impacts))[:3] or ["Platform applies configured effects and records events."],
                "success": [str(item).replace("_", " ") for item in bt.get("success_results", [])] or list(dict.fromkeys(action_successes))[:4] or ["Platform reports the configured success result."],
                "failure": [str(item).replace("_", " ") for item in bt.get("failure_results", [])] or list(dict.fromkeys(action_failures))[:4] or ["Platform reports a failed or interrupted step."],
                "interrupt": bt.get("interrupt_policy", "cancel"),
                "cooldown_minutes": cooldown_minutes,
                "tags": bt.get("tags", []),
                "goal_tags": bt.get("goal_tags", []),
                "drive_tags": bt.get("drive_tags", []),
            })
        # Long-term goal tags are context only.  They must not promote a
        # behavior whose current drive, need, or resource conditions are weak.
        nearby = [item for item in (self.store.entities_at_location(location_id) if location_id else []) if item.get("id") != agent_id]
        known_routes = []
        for route_id in (current_location.get("routes", []) if isinstance(current_location, dict) else []):
            try:
                known_routes.append(self.store.get_entity(str(route_id)).get("name", str(route_id)))
            except KeyError:
                known_routes.append(str(route_id))
        active_behavior_id = state.get("active_behavior_id")
        active_action_id = state.get("active_action")
        active_behavior = next((item for item in bt_defs.values() if item.get("id") == active_behavior_id), {})
        active_action = next((item for item in action_defs.values() if item.get("id") == active_action_id), {})
        available_actions = [
            {"choice": action.get("agent_label") or _label(str(action.get("id", "action"))), "name": action.get("agent_label") or _label(str(action.get("id", "action"))), "summary": action.get("agent_summary") or _label(str(action.get("id", "action")))}
            for action in action_defs.values()
        ]
        needs = agent.get("needs", {})
        drives = DriveCalculator(self.store.get_meta("rules", {})).calculate(agent)
        drive_model = (self.store.get_meta("rules", {}) or {}).get("motivation_model", {})
        memory_query_parts = [str(agent.get("ultimate_goal", ""))]
        if current_location:
            memory_query_parts.append(str(current_location.get("name", location_id)))
        for drive in drives[:3]:
            memory_query_parts.append(str(drive.get("id", "")))
        for behavior in available[:6]:
            memory_query_parts.append(str(behavior.get("ref", "")).removeprefix("behavior:"))
            choice_words = str(behavior.get("choice", "")).split()
            if choice_words:
                memory_query_parts.append(" ".join(choice_words[:4]))
            memory_query_parts.extend(str(item) for item in (behavior.get("goal_tags") or []))
        memory_query = " ".join(part for part in memory_query_parts if part).strip()
        memory_dimensions = {
            "location": [str(location_id), str(current_location.get("name", ""))] if location_id else [],
            "behavior": [str(item.get("ref", "")).removeprefix("behavior:") for item in available[:8]],
            "goal": list(agent.get("goal_tags", []) or []),
        }
        # Compiling a snapshot is read-only. Explicit ``recall_memory`` calls
        # touch rows so opening a new decision view cannot reorder a just-written
        # experience by incrementing its predecessor's access count.
        memories = self.memory.retrieve(agent_id, query=memory_query, limit=6, touch=False, dimensions=memory_dimensions)
        short_term_memories = [item for item in memories if item.get("tier") == "short_term"]
        long_term_memories = [item for item in memories if item.get("tier") == "long_term"]
        known_facts = [self._humanize_fact(item) for item in self.memory.relevant_facts(agent_id, query=memory_query, limit=8, dimensions=memory_dimensions)]
        if not known_facts:
            known_facts = [{"fact": self._humanize_fact(fact), "source_type": "config_seed", "confidence": 1.0} for fact in agent.get("knowledge_seed", [])[:8]]
        relationships = self.store.get_related_relationships(agent_id, limit=5)
        for relationship in relationships:
            person_id = relationship.get("person")
            try:
                relationship["person"] = self.store.get_agent(person_id).get("name", person_id)
            except KeyError:
                try:
                    relationship["person"] = self.store.get_entity(person_id).get("name", person_id)
                except KeyError:
                    pass
        actor_events = self.store.recent_events_for_actor(agent_id, limit=8)
        recent_actions = actor_events[:2]
        recent_outcomes = self.store.recent_outcomes_for_actor(agent_id, limit=3)
        for outcome in recent_outcomes:
            behavior = bt_defs.get(str(outcome.get("behavior_id", "")), {})
            outcome["behavior_choice"] = behavior.get("agent_label") or behavior.get("summary") or behavior.get("description") or "a behavior"
        observation_facts = []
        if current_location:
            observation_facts.append(f"You are at {current_location.get('name', location_id)}.")
        observation_facts.extend(
            f"{item.get('name', item.get('id', 'Someone'))} is nearby."
            for item in nearby[:4]
        )
        prompt_protocol, platform_rules = self._prompt_sections(role)
        decision_constraints: list[str] = []
        labels = {str(item.get("id")): str(item.get("label") or item.get("id")) for item in (self.store.get_meta("attributes", []) or []) if isinstance(item, dict) and item.get("id")}
        for signal in rules.get("decision_signals", []) or []:
            if not isinstance(signal, dict) or not signal.get("need"):
                continue
            need_id = str(signal["need"])
            if need_id not in needs:
                continue
            value = float(needs.get(need_id, 0) or 0)
            threshold = float(signal.get("alert_threshold", 0) or 0)
            message = signal.get("below_message") if value <= threshold else signal.get("above_message")
            if message:
                decision_constraints.append(f"{labels.get(need_id, need_id)} is {value:g}/100; {str(message).rstrip('.') }.")
        schema = self._attribute_schema()
        capacity_id = schema.capacity_attribute()
        if capacity_id:
            capacity_value = float(state.get(capacity_id, 0) or 0)
            capacity_label = labels.get(capacity_id, capacity_id)
            capacity_max = schema.maximum(capacity_id)
            decision_constraints.append(f"{capacity_label} is {capacity_value:g}/{capacity_max:g}; Platform hides any BT whose configured cost exceeds current {capacity_label}.")
        if location_id and safe_location_id and safe_location_id != location_id:
            decision_constraints.append("The current location is not safe for rest; the recovery BT moves to a configured safe location first.")
        elif location_id and not safe_location_id:
            decision_constraints.append("No configured safe location is reachable from the current location.")
        if len(available) == 1:
            decision_constraints.append("Exactly one BT is executable in this state; choose it only if its stated purpose fits the current goal.")
        elif not available:
            decision_constraints.append("No BT is executable in this state; return wait.")
        capability_update_candidates = []
        progression_rules = rules.get("capability_progression", {}) or {}
        for capability, definition in progression_rules.items():
            last = self.store.connection.execute("SELECT MAX(world_minute) AS minute FROM capability_progress WHERE agent_id=? AND capability=?", (agent_id, str(capability))).fetchone()["minute"]
            completed = self.store.connection.execute("SELECT COUNT(*) AS n FROM event_log WHERE actor_id=? AND event_type='behavior.completed' AND world_minute>?", (agent_id, int(last or -1))).fetchone()["n"]
            required = int((definition or {}).get("minimum_completed_outcomes", 3) or 3)
            if int(completed or 0) >= required:
                capability_update_candidates.append({"capability": str(capability), "completed_outcomes": int(completed), "required_outcomes": required, "max_delta": float((definition or {}).get("max_delta", 0.1) or 0.1)})
        pending_institutions = [
            {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "proposed"}
            for item in self.store.list_institutions(status="proposed", limit=12)
        ]
        active_institutions = [
            {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "active"}
            for item in self.store.list_institutions(status="active", limit=12)
        ]
        institution_ledgers = []
        for item in self.store.list_institutions(status="active", limit=12):
            institution_ledgers.append({
                "name": str(item.get("name", "")),
                "entries": self.store.institution_ledger_totals(str(item.get("institution_id")), None)[:12],
            })
        return {
            "context_version": "1", "role": role,
            "world_revision": int(self.store.get_meta("world_revision", 0) or 0),
            "agent_revision": int(agent.get("agent_revision", 0) or 0),
            "prompt_protocol": prompt_protocol,
            "platform_rules": platform_rules,
            "world_time": self._world_time(),
            "self": {"id": agent_id, "name": agent.get("name", agent_id), "role": agent.get("role", "agent"), "status": state.get("status", "idle"), "location": current_location.get("name", location_id) if isinstance(current_location, dict) else location_id, "known_routes": known_routes, "attributes": self._attribute_values(agent, state), "goal": agent.get("ultimate_goal")},
            "identity": {"traits": agent.get("traits", {}), "beliefs": agent.get("beliefs", []), "capabilities": agent.get("capabilities", {})},
            "goal_tags": agent.get("goal_tags", []),
            "capability_history": self.store.list_capability_progress(agent_id, limit=5),
            "capability_update_candidates": capability_update_candidates,
            "needs": needs, "inventory": agent.get("inventory", {}), "injuries": state.get("injuries", []),
            "drives": drives[:5], "drive_model": drive_model, "observation": {"facts": observation_facts[:6], "source": "current_state"}, "nearby_entities": nearby[:8], "known_facts": known_facts, "memories": memories, "short_term_memories": short_term_memories, "long_term_memories": long_term_memories, "memory_query": memory_query, "recent_actions": recent_actions, "recent_outcomes": recent_outcomes, "direct_relationships": relationships,
            "active_task": {"bt": active_behavior.get("summary") or ("in progress" if active_behavior_id else None), "action": active_action.get("description") or (_label(str(active_action_id)) if active_action_id else None), "remaining_minutes": state.get("remaining_minutes", 0)},
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
                world=self.store.get_meta("world_properties", {}) or {},
            ),
            "pending_institutions": pending_institutions,
            "active_institutions": active_institutions,
            "institution_ledgers": institution_ledgers,
            "executable_behaviors": self._assign_choices(available), "decision_constraints": decision_constraints, "last_platform_result": state.get("last_platform_result"), "tools": self._role_tools(role),
        }

    @staticmethod
    def _view_action_parameters(parameters: dict[str, Any], state: dict[str, Any], inventory: dict[str, Any], food_items: list[str], *, safe_location_id: str | None = None, resource_node: dict[str, str] | None = None, health_location_id: str | None = None, default_target_id: str | None = None) -> dict[str, Any]:
        resolved = {}
        first_food = next((item for item in food_items if float(inventory.get(item, 0) or 0) >= 1), None)
        for key, value in parameters.items():
            if value == "context.location_id" or value == "context.station_id":
                resolved[key] = state.get("location_id")
            elif value == "context.safe_location_id":
                resolved[key] = safe_location_id
            elif value == "context.resource_location_id":
                resolved[key] = (resource_node or {}).get("location_id")
            elif value == "context.resource_tag":
                resolved[key] = (resource_node or {}).get("resource_tag")
            elif value == "context.health_location_id":
                resolved[key] = health_location_id
            elif value == "context.item_id":
                resolved[key] = first_food
            elif value == "context.target_id":
                resolved[key] = default_target_id
            else:
                resolved[key] = value
        return resolved

    def _route_distance(self, origin: str | None, destination: str | None) -> int:
        if not origin or not destination or origin == destination:
            return 0
        hop_minutes = max(1, int((self.store.get_meta("rules", {}) or {}).get("route_minutes_per_hop", 1)))
        queue: list[tuple[str, int]] = [(str(origin), 0)]
        visited = {str(origin)}
        while queue:
            current, distance = queue.pop(0)
            try:
                location = self.store.get_entity(current)
            except KeyError:
                continue
            for neighbor in location.get("routes", []) or []:
                neighbor = str(neighbor)
                if neighbor in visited:
                    continue
                if neighbor == str(destination):
                    return (distance + 1) * hop_minutes
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
        return hop_minutes

    def _recent_event_types(self, limit: int = 120) -> list[str]:
        rows = self.store.connection.execute(
            "SELECT DISTINCT event_type FROM event_log WHERE world_minute<=? ORDER BY event_id DESC LIMIT ?",
            (int(self.store.get_meta("world_minute", 0)), max(1, min(int(limit), 500))),
        ).fetchall()
        return [str(row["event_type"]) for row in rows]

    def _action_available(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], location: dict[str, Any], food_items: list[str], duration_minutes: int | None = None) -> bool:
        resource_parameters = (action.get("parameter_defaults", {}) or {})
        resource_key = next((key for key, value in resource_parameters.items() if value == "first_available_resource"), None)
        if resource_key and parameters.get(resource_key) in {None, "", "context.resource_tag"}:
            rules = self.store.get_meta("rules", {}) or {}
            entity_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
            protected_key = str(rules.get("resource_protected_state", "protected"))
            source = next((item for item in self.store.entities_at_location((agent.get("initial_state") or {}).get("location_id")) if item.get("entity_type") in entity_types and not (item.get("initial_state") or {}).get(protected_key) and self._entity_quantity(item) > 0), None)
            parameters["resource_tag"] = source.get("id") if source else parameters.get("resource_tag")
        if action.get("requires_destination") and parameters.get("location_id") == (agent.get("initial_state") or {}).get("location_id"):
            return False
        context = {
            "current_location": location,
            "location_id": (agent.get("initial_state") or {}).get("location_id"),
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "area_id": parameters.get("area_id"),
            "item_id": parameters.get("item_id"),
            "object_id": parameters.get("object_id"),
            "current_object": {},
            "inventory": agent.get("inventory", {}) or {},
            "food_items": food_items,
            "station_tags": [],
            "known_route_destinations": reachable_locations(self.store, (agent.get("initial_state") or {}).get("location_id")),
            "mapped_areas": self.store.explored_areas(str(agent.get("id", ""))),
            "researched_sources": self.store.researched_sources(str(agent.get("id", ""))),
            "recent_event_types": self._recent_event_types(),
        }
        if parameters.get("object_id"):
            try:
                context["current_object"] = self.store.get_entity(str(parameters["object_id"]))
            except KeyError:
                return False
        if action.get("id") == "craft_recipe":
            recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
            if recipe is None:
                return False
            context["station_tags"] = list(recipe.get("station_tags", []) or [])
        resolved_target, target_error = self.targets.resolve(agent, parameters)
        if target_error:
            return False
        target = {**parameters, **(resolved_target or {})}
        context["target"] = resolved_target or {}
        context["target.visible"] = bool((resolved_target or {}).get("visible", False))
        context["target.available"] = bool((resolved_target or {}).get("available", False))
        if self.rule_compiler.denial(
            action=action,
            agent=agent,
            target={**parameters, **(resolved_target or {})},
            context=context,
            world=self.store.get_meta("world_properties", {}) or {},
        ):
            return False
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=target, context=context, world=self.store.get_meta("world_properties", {}) or {}):
                return False
        capacity_ok, _ = check_action_capacity(
            agent,
            action,
            max(1, int(duration_minutes or 1)),
            self.store.get_meta("attributes", []) or [],
        )
        if not capacity_ok:
            return False
        allowed, _, _ = self.resources.reserve(agent, action, parameters, commit=False)
        return allowed

    @staticmethod
    def _entity_quantity(entity: dict[str, Any]) -> float:
        state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
        value = entity.get("amount")
        if value is None:
            value = state.get("quantity", state.get("amount", 0))
        return float(value or 0)

    def _build_story_view(self, actor_id: str, role: str, *, source_event_id: int | None = None) -> dict[str, Any]:
        minute = int(self.store.get_meta("world_minute", 0))
        story_root = self.store.get_meta("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        story_state = self.store.get_meta("story_state", {}) or {}
        recent_events = self.store.recent_events(limit=80)
        # Prefer the validated information event over the later wrapper
        # ``behavior.completed`` row.  The wrapper often says only
        # ``research_progressed``; using it as the source hides the completed
        # fact and makes Story search for evidence it already received.
        source_event = None
        if source_event_id is not None:
            source_event = next((item for item in recent_events if int(item.get("event_id", -1) or -1) == int(source_event_id)), None)
        if source_event is None and source_event_id is None:
            source_event = next(
                (
                    item for item in recent_events
                    if str(item.get("event_type", "")) in {
                        "exploration.completed", "research.completed", "object.recovered",
                        "relationship.changed", "negotiation.result_recorded",
                    }
                    and item.get("actor_id") not in {"story", "god"}
                ),
                None,
            )
        if source_event is None and source_event_id is None:
            source_event = next(
                (
                    item for item in recent_events
                    if str(item.get("event_type", "")) == "behavior.completed"
                    and item.get("actor_id") not in {"story", "god"}
                ),
                None,
            )
        validated_source: dict[str, Any] = {}
        if source_event:
            outcome = source_event.get("outcome") if isinstance(source_event.get("outcome"), dict) else {}
            actual = outcome.get("actual") if isinstance(outcome.get("actual"), dict) else {}
            actor_id = source_event.get("actor_id")
            try:
                actor_name = self.store.get_entity(str(actor_id)).get("name", actor_id) if actor_id else "The world"
            except KeyError:
                actor_name = actor_id or "The world"
            location_id = actual.get("location") or source_event.get("location_id")
            try:
                location_name = self.store.get_entity(str(location_id)).get("name", location_id) if location_id else "unknown"
            except KeyError:
                location_name = location_id or "unknown"
            validated_source = {
                "event_type": source_event.get("event_type"),
                "actor": actor_name,
                "location": location_name,
                "result": outcome.get("result") or source_event.get("result") or source_event.get("fact") or "recorded",
                "fact": source_event.get("fact") or (outcome.get("expected") or {}).get("summary") or "",
                "world_minute": source_event.get("world_minute"),
            }
            # Information facts are persisted separately from the event
            # envelope.  Include the matching public fact in the source view
            # so Story never has to guess a key or query a private record.
            source_minute = int(source_event.get("world_minute", -1) or -1)
            expected_source_types = {
                "research.completed": {"research"},
                "exploration.completed": {"exploration"},
                "object.recovered": {"recovery"},
                "entity.installed": {"deployment"},
            }.get(str(source_event.get("event_type") or ""), set())
            fact_rows = self.store.connection.execute("SELECT agent_id,fact_key,data_json FROM knowledge_fact ORDER BY id DESC LIMIT 64").fetchall()
            for row in fact_rows:
                try:
                    fact_data = json.loads(row["data_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                fact_source_type = str(fact_data.get("source_type") or "")
                if expected_source_types and fact_source_type not in expected_source_types:
                    continue
                if not expected_source_types and fact_source_type not in {"research", "exploration", "recovery", "deployment"}:
                    continue
                if str(row["agent_id"] or "") != str(actor_id) or fact_data.get("observed_at") not in {source_minute, str(source_minute)}:
                    continue
                if fact_data.get("fact"):
                    validated_source["fact"] = str(fact_data["fact"])
                    validated_source["fact_key"] = str(row["fact_key"] or fact_data.get("source_id") or "")
                    break
        audience_candidates = self._story_audience_candidates(recent_events)
        observed_events = {str(item.get("event_type", "")) for item in recent_events}
        fact_rows = self.store.connection.execute("SELECT fact_key,data_json FROM knowledge_fact ORDER BY id DESC").fetchall()
        observed_facts = {str(row["fact_key"]) for row in fact_rows}
        choices: list[dict[str, Any]] = []
        for beat in list(story_root.get("beats", []) or []) + list(story_state.get("emergent_beats", []) or []):
            if not isinstance(beat, dict):
                continue
            beat_id = str(beat.get("id", ""))
            if isinstance(story_state.get(beat_id), dict) and story_state[beat_id].get("status") in {"active", "completed"}:
                continue
            trigger_events = {str(item) for item in (beat.get("trigger_events", []) or [])}
            trigger_facts = {str(item) for item in (beat.get("trigger_facts", []) or [])}
            source_event_type = str((source_event or {}).get("event_type") or "")
            if trigger_events and source_event_type and source_event_type not in trigger_events:
                continue
            if trigger_events and not source_event_type and not trigger_events & observed_events:
                continue
            if trigger_facts and not trigger_facts <= observed_facts:
                continue
            choices.append({
                "ref": f"story:{beat_id}",
                "choice": str(beat.get("agent_label") or beat.get("summary") or beat_id),
                "summary": str(beat.get("summary", "")),
                "when": "; ".join(sorted(trigger_events)) or "The latest evidence supports a new story direction.",
                "requires": "; ".join(sorted(trigger_facts)) or "A recent validated discovery is required.",
                "audience": [str(item) for item in (beat.get("audience", []) or [])],
                "impact": ["Platform records the beat, informs its configured audience, and unlocks only configured behaviors."],
            })
        prompt_protocol, platform_rules = self._prompt_sections(role)
        known_facts: list[str] = []
        for row in fact_rows[:12]:
            try:
                data = json.loads(row["data_json"])
            except (TypeError, json.JSONDecodeError):
                data = {}
            fact = data.get("fact") or data.get("summary")
            if fact:
                known_facts.append(str(fact))
            else:
                known_facts.append(str(row["fact_key"]).replace(":", " — ").replace("_", " "))
        return {
            "context_version": "1", "role": role,
            "prompt_protocol": prompt_protocol, "platform_rules": platform_rules,
            "world_time": self._world_time(minute),
            "self": {"id": actor_id, "name": "Story Agent", "role": role, "status": "ready", "location": "world", "goal": "Turn validated discoveries into coherent continuing story beats."},
            "identity": {"traits": {}, "beliefs": [], "capabilities": {"story_scope": True}},
            "needs": {}, "inventory": {}, "injuries": [], "drives": [],
            "observation": {"facts": [str(item.get("event_type", "event")) + ": " + str(item.get("summary") or item.get("fact") or item.get("result") or "validated world result") for item in recent_events[:10]], "source": "validated_world_events"},
            "validated_source": validated_source,
            "nearby_entities": [], "known_facts": [{"fact": fact, "source_type": "world_knowledge", "confidence": 1.0} for fact in known_facts],
            "audience_candidates": audience_candidates,
            "recent_actions": recent_events[:8], "recent_story_events": [item for item in recent_events if str(item.get("event_type", "")).startswith("story.")][:8],
            "recent_world_changes": [], "recent_agent_outcomes": [], "direct_relationships": [],
            "active_task": {"bt": None, "action": None, "remaining_minutes": 0},
            "executable_behaviors": [], "story_beats": choices[:12],
            "world_progress_candidates": [
                {
                    "progress_key": str(key),
                    "required": float((value or {}).get("required", 1) or 1),
                    "description": str((value or {}).get("description") or ""),
                    "evidence_event_types": [str(item) for item in ((value or {}).get("evidence_event_types") or [])],
                    "evidence_behaviors": [str(item) for item in ((value or {}).get("evidence_behaviors") or [])],
                }
                for key, value in ((self.store.get_meta("rules", {}) or {}).get("civilization_progress", {}) or {}).items()
                if isinstance(value, dict)
            ],
            "active_world_rules": self.rule_compiler.model_view(
                self._world_rule_labels(),
                context={"recent_event_types": self._recent_event_types()},
                world=self.store.get_meta("world_properties", {}) or {},
            ),
            "last_platform_result": recent_events[0] if recent_events else None,
            "tools": self._role_tools(role),
        }

    def _story_audience_candidates(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expose exact character names found in recent validated events.

        Story delivery is global, so the audience need not be co-located with
        the Story Agent.  The candidate list is derived from event participants
        and contains names only; internal identifiers remain Platform-owned.
        """

        candidate_ids: list[str] = []
        participant_keys = {"target_id", "recipient_id", "speaker_id", "listener_id", "counterparty_id", "participant_id", "actor_id", "from_id", "to_id"}

        def collect(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    collect(child_value, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    collect(child, key)
            elif isinstance(value, str) and (key in participant_keys or key in {"from", "to", "source", "target", "participant", "recipient"}):
                candidate_ids.append(value)

        for event in events[:40]:
            actor_id = event.get("actor_id")
            if actor_id:
                candidate_ids.append(str(actor_id))
            collect(event)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate_id in candidate_ids:
            try:
                agent = self.store.get_agent(str(candidate_id))
            except KeyError:
                continue
            name = str(agent.get("name") or candidate_id).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append({"name": name, "reason": "participant in a recent validated event"})
            if len(result) >= 12:
                break
        return result

    def _build_world_view(self, actor_id: str, role: str) -> dict[str, Any]:
        minute = self.store.get_meta("world_minute", 0)
        all_events = self.store.recent_events(limit=20)
        recent_world_changes = self.store.recent_world_change_events(limit=6)
        world_proposals = self.store.list_world_proposals(limit=8)
        recent_agent_outcomes = self.store.recent_behavior_outcomes(limit=6)
        world_trends = self.store.world_trends(current_minute=int(minute))
        bt_defs = {item["id"]: item for item in self.store.get_definitions("bt_def")}
        for outcome in recent_agent_outcomes:
            behavior = bt_defs.get(str(outcome.get("behavior_id", "")), {})
            outcome["behavior_choice"] = behavior.get("agent_label") or behavior.get("summary") or behavior.get("description") or "a behavior"
            outcome_actor_id = outcome.get("actor_id")
            if outcome_actor_id:
                try:
                    outcome["actor_name"] = self.store.get_entity(str(outcome_actor_id)).get("name", "a character")
                except KeyError:
                    outcome["actor_name"] = "a character"
        recent_world_events = (recent_world_changes + [item for item in all_events if item.get("actor_id") == actor_id])[:6]
        active_count = sum(
            1 for agent in self.store.list_agents()
            if (agent.get("initial_state") or {}).get("status") in {"executing", "paused"}
        )
        world_properties = self.store.get_meta("world_properties", {}) or {}
        world_summary = "; ".join(
            f"{str(key).replace('_', ' ')}={_display_number(value)}"
            for key, value in world_properties.items()
        ) or "none"
        world_id = str(self.store.get_meta("world_id", "world"))
        prompt_protocol, platform_rules = self._prompt_sections(role)
        behavior_catalog = []
        for behavior in bt_defs.values():
            behavior_catalog.append({
                "name": behavior.get("agent_label") or behavior.get("summary") or _label(str(behavior.get("id", "behavior"))),
                "purpose": behavior.get("description") or behavior.get("summary") or "configured behavior",
                "success": [str(item).replace("_", " ") for item in (behavior.get("success_results") or [])[:3]],
            })
        action_catalog = []
        for action in self.store.get_definitions("action_def"):
            action_catalog.append({
                "name": action.get("agent_label") or _label(str(action.get("id", "action"))),
                "purpose": action.get("agent_summary") or action.get("description") or "configured action",
            })
        condition_fields = [
            {"condition": f'world["{str(key).replace("_", " ")}"]', "meaning": str(key).replace("_", " ")}
            for key in (self.store.get_meta("world_properties", {}) or {})
        ]
        condition_fields.extend(
            {"condition": f"event.recent.{event_type}", "meaning": "a recent validated public event"}
            for event_type in self._recent_event_types()[:16]
            if event_type not in {"world_minute.advanced", "world.created"}
        )
        return {
            "context_version": "1",
            "role": role,
            "prompt_protocol": prompt_protocol,
            "platform_rules": platform_rules,
            "world_time": self._world_time(int(minute)),
            "self": {"id": actor_id, "name": actor_id, "role": role, "status": "ready", "location": "world", "goal": "preserve a coherent, evolving world"},
            "identity": {"traits": {}, "beliefs": [], "capabilities": {"world_scope": True}},
            "needs": {},
            "inventory": {},
            "injuries": [],
            "drives": [],
            "observation": {"facts": [f"World properties: {world_summary}", f"Active behaviors: {active_count}"], "source": "platform_world_summary"},
            "nearby_entities": [],
            "known_facts": [],
            "recent_actions": recent_world_events,
            "recent_world_changes": recent_world_changes,
            "world_proposals": world_proposals,
            "recent_agent_outcomes": recent_agent_outcomes,
            "world_trends": world_trends,
            "civilization_progress": self.store.list_progress(world_id, limit=20),
            "world_progress_candidates": [
                {
                    "progress_key": str(key),
                    "required": float((value or {}).get("required", 1) or 1),
                    "description": str((value or {}).get("description") or ""),
                    "evidence_event_types": [str(item) for item in ((value or {}).get("evidence_event_types") or [])],
                    "evidence_behaviors": [str(item) for item in ((value or {}).get("evidence_behaviors") or [])],
                }
                for key, value in ((self.store.get_meta("rules", {}) or {}).get("civilization_progress", {}) or {}).items()
                if isinstance(value, dict)
            ],
            "pending_institutions": [
                {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "proposed"}
                for item in self.store.list_institutions(status="proposed", limit=20)
            ],
            "active_institutions": [
                {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "active"}
                for item in self.store.list_institutions(status="active", limit=20)
            ],
            "institution_ledgers": [
                {"name": str(item.get("name", "")), "entries": self.store.institution_ledger_totals(str(item.get("institution_id")), None)[:20]}
                for item in self.store.list_institutions(status="active", limit=20)
            ],
            "active_world_rules": self.rule_compiler.model_view(
                self._world_rule_labels(),
                context={"recent_event_types": self._recent_event_types()},
                world=world_properties,
            ),
            "registered_behaviors": behavior_catalog[:32],
            "registered_actions": action_catalog[:32],
            "rule_condition_fields": condition_fields[:24],
            "direct_relationships": [],
            "active_task": {"bt": None, "action": None, "remaining_minutes": 0},
            "executable_behaviors": [],
            "last_platform_result": recent_world_events[0] if recent_world_events else None,
            "tools": self._role_tools(role),
        }
