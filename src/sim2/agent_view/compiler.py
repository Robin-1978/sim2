from __future__ import annotations

import copy
import json
import re
from typing import Any

from ..actions.conditions import ConditionEngine
from ..actions.capacity import check_action_capacity
from ..actions.resources import ResourceLedger
from ..actions.institutions import allocation_preflight, describe_institution_terms
from ..actions.targets import TargetResolver
from ..agents.tools import tools_for_role
from ..agents.policies import default_platform_rules, default_protocol
from ..behavior.estimator import BehaviorDurationEstimator
from ..behavior.recovery import failure_block_recovered
from ..evolution.story import eligible_story_beats, story_evidence_event_types
from ..evolution.memory import role_memory_query
from ..memory.service import MemoryService
from ..persistence.store import WorldStore
from ..world.drives import DriveCalculator
from ..world.navigation import configured_location_tags, nearest_location_with_tag, nearest_resource_node, nearest_safe_location, reachable_locations
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..world.context import configured_context_values
from ..world.time import world_time_view


# Bookkeeping events that fill the newest-first history window without telling
# a reviewer anything (one per world minute).
_TICK_EVENT_TYPES = ("world_minute.advanced",)


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _choice_key(value: Any) -> str:
    """Normalize a model-facing label for deterministic exact matching."""

    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _predicate_text(predicate: str) -> str:
    """Turn a configured predicate into one short sentence for the model."""

    text = str(predicate).replace("_", " ")
    # Dots in configured paths are separators (``actor.state.energy``), but
    # dots between digits are numeric decimals.  Replacing every dot made
    # thresholds such as ``0.9`` appear as ``0 9`` to the Agent.
    text = re.sub(r"(?<=[A-Za-z_])\.(?=[A-Za-z_])", " ", text).strip()
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
                    scope_labels = {
                        "agent": "from carried inventory",
                        "world": "from world stock",
                        "auto": "from carried or shared stock",
                    }
                    inputs = ", ".join(
                        f"{item.get('quantity', 1)} {_display_item(item.get('item', 'input'))} "
                        f"{scope_labels.get(str(item.get('scope') or 'agent'), 'from configured stock')}"
                        for item in recipe.get("inputs", []) or []
                    )
                    out.append(f"Recipe inputs: {inputs or 'configured inputs'} (reserved before start)")
                    continue
                if key == "item_parameter":
                    out.append("Carried stock -1 selected item on success (reserved before start)")
                    continue
                if key == "resource_parameter":
                    amount = values.get("resource_amount", 1)
                    out.append(
                        f"Source stock -{amount} selected resource on success "
                        "(reserved before start)"
                    )
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

    def _recent_history_view(self, limit: int = 16) -> list[dict[str, Any]]:
        """Return a compact, visible-name projection of recent world facts."""

        events = self.store.recent_events(limit=max(1, min(int(limit), 32)), exclude_types=_TICK_EVENT_TYPES)
        bt_labels = {
            str(item.get("id")): str(item.get("agent_label") or item.get("summary") or item.get("id"))
            for item in self.store.get_definitions("bt_def")
        }
        result: list[dict[str, Any]] = []
        for event in events:
            actor_id = str(event.get("actor_id") or "")
            try:
                actor_name = str(self.store.get_entity(actor_id).get("name") or actor_id or "world")
            except KeyError:
                actor_name = actor_id or "world"
            details: dict[str, Any] = {}
            for key in (
                "summary", "fact", "result", "status", "behavior_id", "definition_type",
                "name", "institution", "property", "delta", "allocations", "consumed", "remaining",
            ):
                value = event.get(key)
                if value not in (None, "", [], {}):
                    if key == "behavior_id":
                        value = bt_labels.get(str(value), value)
                    details[key] = value
            result.append({
                "event_id": int(event.get("event_id", 0) or 0),
                "event_type": str(event.get("event_type") or "event"),
                "world_minute": int(event.get("world_minute", 0) or 0),
                "actor": actor_name,
                "details": details,
            })
        return result

    def _knowledge_gap_view(self, *, status: str = "active", limit: int = 8) -> list[dict[str, Any]]:
        """Expose current research questions without internal identifiers."""

        result: list[dict[str, Any]] = []
        for row in self.store.list_knowledge_gaps(status=status, limit=limit):
            item: dict[str, Any] = {
                "question": str(row.get("question") or ""),
                "status": str(row.get("status") or status),
                "updated_minute": int(row.get("updated_minute") or 0),
            }
            if row.get("last_fact"):
                item["last_finding"] = str(row["last_fact"])[:240]
            total = int(row.get("total_stages") or 0)
            if total:
                item["stages"] = f"{len(row.get('completed_stages') or [])}/{total} completed"
            try:
                item["source"] = str(self.store.get_entity(str(row.get("current_source_id"))).get("name") or "")
            except (KeyError, TypeError):
                pass
            result.append(item)
        return result

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
        if not protocol_lines:
            protocol_lines = default_protocol(role)
        role_rules = prompt.get("role_platform_rules", {}) if isinstance(prompt, dict) else {}
        rules = list((role_rules.get(role) if isinstance(role_rules, dict) else None) or prompt.get("platform_rules", []) or []) if isinstance(prompt, dict) else []
        if not rules:
            rules = default_platform_rules(role)
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

    def option_snapshot(self, agent_id: str, *, bt_defs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Project one character's executable options for stagnation audits.

        The character's own decision view is the only authority on what it can
        do now, so the snapshot is derived from it instead of re-implementing
        executability.  ``options`` are stable identifiers: a behavior, or a
        behavior plus one parameter option (for example a configured research
        source), so new content that only adds a parameter option still counts
        as growth.  ``choices`` and ``blocked`` use the visible wording the
        character sees; blocked entries carry the failing conditions.
        """

        view = self.build_decision_view(agent_id, role="agent_decision", include_diagnostics=True)
        definitions = bt_defs if bt_defs is not None else {str(item["id"]): item for item in self.store.get_definitions("bt_def")}
        options: set[str] = set()
        choices: list[str] = []
        for item in view.get("executable_behaviors", []) or []:
            ref = str(item.get("ref") or "")
            if not ref:
                continue
            options.add(ref)
            choices.append(str(item.get("choice") or ref))
            for parameter, entries in (item.get("parameter_options") or {}).items():
                for entry in entries or []:
                    name = str((entry or {}).get("name") or "").strip()
                    if name:
                        options.add(f"{ref}|{parameter}={name}")
        blocked: list[dict[str, Any]] = []
        for diagnostic in view.get("unavailable_behavior_diagnostics", []) or []:
            reasons = [str(text) for text in (diagnostic.get("failed") or [])]
            # A cooldown is a temporary pause, not a structural obstacle.
            if reasons and all(text.startswith("cooldown until") for text in reasons):
                continue
            behavior_id = str(diagnostic.get("behavior_id") or "")
            definition = definitions.get(behavior_id, {})
            blocked.append({
                "choice": str(definition.get("agent_label") or definition.get("summary") or _label(behavior_id)),
                "needs": [str(text)[:120] for text in (diagnostic.get("failed") or [])][:3],
            })
        return {"options": sorted(options), "choices": choices, "blocked": blocked}

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
            prefix, raw_value = fact.split(":", 1)
            visible_parts: list[str] = []
            for value in raw_value.split(":"):
                value = value.strip()
                try:
                    entity_name = self.store.get_entity(value).get("name")
                except KeyError:
                    entity_name = None
                visible_parts.append(str(entity_name if entity_name and entity_name != value else value.replace("_", " ")))
            return self._visible_text(f"{prefix.replace('_', ' ').title()}: {' — '.join(visible_parts)}")
        if isinstance(fact, dict) and "fact" in fact:
            item = dict(fact)
            item["fact"] = self._humanize_fact(item["fact"])
            return item
        return self._visible_text(fact) if isinstance(fact, str) else fact

    @staticmethod
    def _merge_visible_facts(facts: list[Any]) -> list[Any]:
        """Merge duplicate evidence while preserving every source in the prompt."""

        merged: list[Any] = []
        positions: dict[str, int] = {}
        sources: dict[str, list[str]] = {}
        for fact in facts:
            if isinstance(fact, dict):
                text = str(fact.get("fact") or fact.get("statement") or "")
                visible = dict(fact)
            else:
                text = str(fact)
                visible = fact
            key = " ".join(text.split()).casefold()
            if not key:
                continue
            source = ""
            if isinstance(fact, dict):
                source = str(fact.get("source_type") or fact.get("source") or "").strip()
            if key not in positions:
                positions[key] = len(merged)
                merged.append(visible)
                sources[key] = [source] if source else []
                continue
            if source and source not in sources[key]:
                sources[key].append(source)
            current = merged[positions[key]]
            if isinstance(current, dict) and isinstance(fact, dict):
                current["confidence"] = max(
                    float(current.get("confidence", 0) or 0),
                    float(fact.get("confidence", 0) or 0),
                )
                if len(sources[key]) > 1:
                    current["source_type"] = ", ".join(sorted(sources[key]))
        return merged

    @staticmethod
    def _with_behavior_choice(record: Any, bt_defs: dict[str, dict[str, Any]]) -> Any:
        """Attach the readable chosen behavior to outcome feedback."""

        if not isinstance(record, dict):
            return record
        item = dict(record)
        reference = str(item.get("behavior_ref") or item.get("behavior_id") or "").removeprefix("behavior:")
        behavior = bt_defs.get(reference) or {}
        if behavior:
            summary = str(behavior.get("summary") or "")
            item["behavior_choice"] = behavior.get("agent_label") or (
                summary if summary and summary != reference else _label(reference)
            )
        return item

    def _visible_text(self, value: Any) -> str:
        """Replace configured entity keys in prose shown to an Agent.

        Event and discovery facts are authored as ordinary text, so a package
        can mention a location or object by its stable key.  Those keys are
        useful to SQLite but are not readable Agent choices.  Resolve exact
        entity tokens here without changing event types, fact keys, or
        free-form text that merely contains a similar word.
        """

        text = str(value)
        entities: list[tuple[str, str]] = []
        for entity in self.store.entities_of_type("location") + self.store.entities_of_type("agent") + self.store.entities_of_type("object"):
            key = str(entity.get("id") or "")
            name = str(entity.get("name") or key)
            if key and name:
                entities.append((key, name if name != key else key.replace("_", " ")))
        # Include resource/material/artifact/document keys without assuming a
        # particular world vocabulary; ``entities_of_type`` is cheap for the
        # bounded Agent projection and keeps new worlds generic.
        for entity_type in ("resource", "material", "artifact", "document", "tool", "consumable", "shared_stock", "market_stock"):
            for entity in self.store.entities_of_type(entity_type):
                key = str(entity.get("id") or "")
                name = str(entity.get("name") or key)
                if key and name:
                    entities.append((key, name if name != key else key.replace("_", " ")))
        for key, name in sorted(set(entities), key=lambda item: len(item[0]), reverse=True):
            # Entity identifiers are snake_case tokens.  Treat underscore as
            # part of a token so ``village_food`` cannot rewrite the prefix
            # of a different configured identifier such as
            # ``village_food_store``.  This keeps humanization exact without
            # adding world-specific names to the compiler.
            text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", name, text)
        return text

    def _humanize_record(self, value: Any, *, field: str = "") -> Any:
        """Apply visible entity names to nested outcome/event projections."""

        if isinstance(value, dict):
            return {str(key): self._humanize_record(item, field=str(key)) for key, item in value.items()}
        if isinstance(value, list):
            return [self._humanize_record(item, field=field) for item in value]
        if isinstance(value, str):
            return self._humanize_fact(value) if field in {"fact", "summary", "message", "reason"} else self._visible_text(value)
        return value

    def build_decision_view(
        self,
        agent_id: str,
        role: str = "agent_decision",
        *,
        source_event_id: int | None = None,
        include_diagnostics: bool = False,
    ) -> dict[str, Any]:
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
        configured_context = configured_context_values(rules, agent)
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
        diagnostics: list[dict[str, Any]] = []
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
        configured_research_sources: set[str] = {
            str(value)
            for bt_id in catalog_ids
            for value in [((bt_defs.get(str(bt_id), {}).get("parameter_defaults", {}) or {}).get("source_id"))]
            if value
        }
        configured_research_sources = {
            str(self.targets.canonical_id(value) or value)
            for value in configured_research_sources
        }
        configured_research_sources.update(
            str(self.targets.canonical_id(value) or value)
            for value in (agent.get("research_targets", []) or [])
            if value
        )
        research_sources = self._pending_research_sources(agent, known_fact_keys, location_id, configured_research_sources)
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
                diagnostics.append({"behavior_id": bt_id, "failed": ["behavior definition is not registered"]})
                continue
            blocked = (state.get("blocked_behaviors") or {}).get(str(bt_id)) if isinstance(state, dict) else None
            if isinstance(blocked, dict) and not failure_block_recovered(self.store, blocked):
                diagnostics.append({
                    "behavior_id": bt_id,
                    "failed": [f"blocked after the last failed result ({blocked.get('result', 'failed')}); wait for a configured recovery event"],
                })
                continue
            rules = self.store.get_meta("rules", {}) or {}
            behavior_context = dict(configured_context)
            uses_source_parameter = any(
                value == "context.source_id"
                for node in self._iter_behavior_actions(bt_id)
                for value in (node.get("parameters") or {}).values()
            )
            if uses_source_parameter and research_sources:
                selected_source = research_sources[0]
                behavior_context.update({
                    "source_id": selected_source["id"],
                    "source_location_id": selected_source.get("location_id"),
                })
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
                "recent_event_types": self._recent_event_types(),
                "day_phase": self._world_time().get("day_phase"),
                "target": {"id": validation_target.get("id"), "available": True} if validation_target else {"available": False},
            }
            condition_context.update(behavior_context)
            condition_context.update(self._interaction_context(agent, participants))
            failed_conditions = [
                str(predicate)
                for predicate in bt.get("entry_conditions", []) or []
                if not self.conditions.evaluate(
                    str(predicate), agent=agent, target=condition_context["target"],
                    context=condition_context,
                    world=self.store.get_meta("world_properties", {}) or {},
                )
            ]
            if failed_conditions:
                diagnostics.append({"behavior_id": bt_id, "failed": failed_conditions})
                continue
            denial = self.rule_compiler.behavior_denial(
                behavior=bt,
                agent=agent,
                context=condition_context,
                world=self.store.get_meta("world_properties", {}) or {},
            )
            if denial:
                diagnostics.append({"behavior_id": bt_id, "failed": [str(denial.get("message") or "denied by an active rule")]})
                continue
            cooldown_minutes = max(0, int(bt.get("cooldown_minutes", 0) or 0))
            if cooldown_minutes:
                last_minute = self.store.last_behavior_minute(agent_id, bt_id)
                current_minute = int(self.store.get_meta("world_minute", 0))
                if last_minute is not None and current_minute - last_minute < cooldown_minutes:
                    diagnostics.append({"behavior_id": bt_id, "failed": [f"cooldown until world minute {last_minute + cooldown_minutes}"]})
                    continue
            steps = []
            costs = []
            impacts = []
            action_successes = []
            action_failures = []
            action_unavailable = False
            unavailable_action = ""
            unavailable_reason: str | None = None
            simulated_agent = copy.deepcopy(agent)
            estimator.capabilities = simulated_agent.get("capabilities", {}) or {}
            simulated_location = dict(current_location)
            for node in self._iter_behavior_actions(bt_id):
                action = action_defs.get(node.get("action_id"))
                if not action:
                    continue
                simulated_state = simulated_agent.setdefault("initial_state", {})
                parameters = self._view_action_parameters(node.get("parameters") or {}, simulated_state, behavior_context, safe_location_id=safe_location_id, resource_node=resource_node, health_location_id=health_location_id, default_target_id=(validation_target or {}).get("id"))
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
                unavailable_reason = self._action_unavailable_reason(simulated_agent, action, parameters, simulated_location, action_minutes)
                if unavailable_reason is not None:
                    action_unavailable = True
                    unavailable_action = str(action.get("id") or "unknown action")
                    break
                recipe_action = (
                    action.get("duration", {}).get("mode") == "recipe_duration"
                    or "recipe_inputs" in (action.get("costs", {}).get("reserve", {}) or {})
                )
                recipe = recipe_defs.get(str(parameters.get("recipe_id"))) if recipe_action else None
                action_for_view = action
                progress_definition = action.get("progress") if isinstance(action.get("progress"), dict) else {}
                if progress_definition.get("key") and "{source_id}" in str(progress_definition.get("key")) and parameters.get("source_id"):
                    try:
                        research_source = self.store.get_entity(str(parameters["source_id"]))
                    except KeyError:
                        research_source = {}
                    source_state = research_source.get("initial_state") if isinstance(research_source.get("initial_state"), dict) else {}
                    discoveries = self.store.research_discoveries(str(parameters["source_id"]))
                    known = set(known_fact_keys)
                    current_stage = next((item for item in discoveries if str(item.get("id")) not in known and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"}), None)
                    required = (current_stage or {}).get("required")
                    if required is None:
                        required = research_source.get("research_required", source_state.get("research_required", source_state.get("required_research")))
                    if required is None:
                        required = (self.store.get_meta("rules", {}) or {}).get("research_default_required")
                    if required is not None:
                        action_for_view = copy.deepcopy(action)
                        action_for_view.setdefault("progress", {})["required"] = required
                view = _action_view(action_for_view, recipe, parameters)
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
                moves_actor = any(
                    str(effect.get("type") or effect.get("op") or "") == "location_set"
                    for effect in (action.get("effects") or [])
                    if isinstance(effect, dict)
                )
                if moves_actor and parameters.get("location_id"):
                    simulated_state["location_id"] = str(parameters["location_id"])
                    try:
                        simulated_location = self.store.get_entity(str(parameters["location_id"]))
                    except KeyError:
                        simulated_location = {}
            if action_unavailable:
                diagnostics.append({"behavior_id": bt_id, "failed": [f"Action {unavailable_action} cannot start: {unavailable_reason}"]})
                continue
            if not steps:
                diagnostics.append({"behavior_id": bt_id, "failed": ["behavior has no executable Action steps"]})
                continue
            target_text = "Platform resolves permitted targets from the current view."
            target_keys = self._target_parameter_keys(bt, bt_defs)
            target_required = bool(target_keys) and len(participants) > 1
            target_options = [{"name": str(item.get("name", item.get("id", "participant")))} for item in participants[:8]]
            parameter_options: dict[str, list[dict[str, Any]]] = {}
            source_parameter = (bt.get("agent_parameters", {}) or {}).get("source_id")
            if source_parameter is not None and research_sources:
                parameter_options["source_id"] = [
                    {
                        "name": str(item.get("name") or item.get("id")),
                        "location": str(item.get("location_name") or item.get("location_id") or "configured source"),
                        "remaining_stages": int(item.get("remaining_stages", 0) or 0),
                    }
                    for item in research_sources[:12]
                ]
                target_text = "Choose one configured research source: " + "; ".join(
                    f"{item['name']} ({item['remaining_stages']} stage(s) remaining at {item['location']})"
                    for item in parameter_options["source_id"]
                )
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
            elif resource_node and any(str(resolver) == "nearest_resource_node" for resolver in bindings.values()):
                try:
                    resource = self.store.get_entity(resource_node["resource_tag"])
                    location = self.store.get_entity(resource_node["location_id"])
                    target_text = (
                        f"Collect {resource.get('name', resource_node['resource_tag'])} from "
                        f"{location.get('name', resource_node['location_id'])}. "
                        "Source stock decreases and carried inventory increases."
                    )
                except KeyError:
                    target_text = "Collect the configured resource from the nearest known node; source stock decreases."
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
                "time": f"{estimator.behavior_minutes(bt_id, ({'safe_location_id': safe_location_id} if safe_location_id else {}) | ({'source_id': research_sources[0]['id'], 'source_location_id': research_sources[0].get('location_id')} if source_parameter is not None and research_sources else {}), location_id)} minutes",
                "parameter_options": parameter_options,
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
        memories = [self._humanize_record(item) for item in self.memory.retrieve(agent_id, query=memory_query, limit=6, touch=False, dimensions=memory_dimensions)]
        short_term_memories = [item for item in memories if item.get("tier") == "short_term"]
        long_term_memories = [item for item in memories if item.get("tier") == "long_term"]
        known_facts = self._merge_visible_facts([
            self._humanize_fact(item)
            for item in self.memory.relevant_facts(agent_id, query=memory_query, limit=12, dimensions=memory_dimensions)
        ])[:8]
        if not known_facts:
            known_facts = [{"fact": self._humanize_fact(fact), "source_type": "config_seed", "confidence": 1.0} for fact in agent.get("knowledge_seed", [])[:8]]
        research_tracks: list[dict[str, Any]] = []
        known_fact_set = set(known_fact_keys)
        seen_sources: set[str] = set()
        for source in research_sources:
            source_id = str(source.get("id"))
            seen_sources.add(source_id)
            research_tracks.append({
                "source": str(source.get("name") or source_id),
                "location": str(source.get("location_name") or source.get("location_id") or "configured source"),
                "stages": [
                    {
                        "fact_key": str(item.get("id")),
                        "status": str(item.get("status") or "active"),
                        "available": str(item.get("id")) not in known_fact_set and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"},
                        "required": item.get("required") or source.get("research_required") or (self.store.get_meta("rules", {}) or {}).get("research_default_required", 1),
                    }
                    for item in (source.get("discoveries") or [])
                ],
            })
        for behavior_id in catalog_ids:
            for node in self._iter_behavior_actions(str(behavior_id)):
                params = node.get("parameters") or {}
                source_id = params.get("source_id")
                if not source_id or str(source_id).startswith("context.") or str(source_id) in seen_sources:
                    continue
                discoveries = self.store.research_discoveries(str(source_id))
                if not discoveries:
                    continue
                seen_sources.add(str(source_id))
                try:
                    source_entity = self.store.get_entity(str(source_id))
                    source_name = source_entity.get("name", source_id)
                    source_location_id = (source_entity.get("initial_state") or {}).get("location_id") or source_entity.get("location_id")
                    source_location = self.store.get_entity(str(source_location_id)).get("name", source_location_id) if source_location_id else "configured source"
                except KeyError:
                    source_entity = {}
                    source_name = source_id
                    source_location = "configured source"
                research_tracks.append({
                    "source": str(source_name),
                    "location": str(source_location),
                    "stages": [
                        {
                            "fact_key": str(item.get("id")),
                            "status": str(item.get("status") or "active"),
                            "available": str(item.get("id")) not in known_fact_set and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"},
                            "required": item.get("required") or source_entity.get("research_required") or ((source_entity.get("initial_state") or {}).get("research_required") if isinstance(source_entity.get("initial_state"), dict) else None) or (self.store.get_meta("rules", {}) or {}).get("research_default_required", 1),
                        }
                        for item in discoveries
                    ],
                })
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
        recent_actions = [
            self._with_behavior_choice(self._humanize_record(item), bt_defs)
            for item in actor_events[:2]
        ]
        recent_outcomes = [
            self._with_behavior_choice(self._humanize_record(item), bt_defs)
            for item in self.store.recent_outcomes_for_actor(agent_id, limit=3)
        ]
        for outcome in recent_outcomes:
            if outcome.get("behavior_choice"):
                continue
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
        capability_defs = {str(item.get("id")): item for item in self.store.get_definitions("capability_def")}
        owned_capabilities = agent.get("capabilities") or {}
        for capability, definition in progression_rules.items():
            capability = str(capability)
            catalog_entry = capability_defs.get(capability)
            if not catalog_entry or (capability not in owned_capabilities and not bool(catalog_entry.get("acquirable", False))):
                continue
            last = self.store.connection.execute("SELECT MAX(world_minute) AS minute FROM capability_progress WHERE agent_id=? AND capability=?", (agent_id, str(capability))).fetchone()["minute"]
            evidence_events = self.store.capability_evidence(agent_id, capability, after_minute=int(last) if last is not None else -1)
            completed = len(evidence_events)
            required = int((definition or {}).get("minimum_completed_outcomes", 3) or 3)
            if int(completed or 0) >= required:
                capability_update_candidates.append({"capability": capability, "completed_outcomes": int(completed), "required_outcomes": required, "max_delta": float((definition or {}).get("max_delta", 0.1) or 0.1), "evidence_sources": list(dict.fromkeys(str(item.get("source") or "configured result") for item in evidence_events))})
        pending_institutions = [
            {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "proposed"}
            for item in self.store.list_institutions(status="proposed", limit=12)
        ]
        active_institutions = [
            {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "active", "terms": describe_institution_terms(item)}
            for item in self.store.list_institutions(status="active", limit=12)
        ]
        institution_ledgers = []
        for item in self.store.list_institutions(status="active", limit=12):
            institution_ledgers.append({
                "name": str(item.get("name", "")),
                "entries": self.store.institution_ledger_totals(str(item.get("institution_id")), None)[:12],
            })
        story_goals = list(agent.get("story_goals", []) or [])[-3:]
        behavior_assignments = [
            item for item in (agent.get("behavior_assignments", []) or [])
            if isinstance(item, dict) and str(item.get("status") or "assigned") == "assigned"
        ][-4:]
        agent_name = str(agent.get("name", agent_id))
        story_root = self.store.get_meta("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        story_defs = {
            str(item.get("id")): item
            for item in list(story_root.get("beats", []) or []) + list((self.store.get_meta("story_state", {}) or {}).get("emergent_beats", []) or [])
            if isinstance(item, dict) and item.get("id")
        }
        active_story_beats: list[dict[str, Any]] = []
        story_state = self.store.get_meta("story_state", {}) or {}
        for beat_id, entry in story_state.items():
            if beat_id == "emergent_beats" or not isinstance(entry, dict) or entry.get("status") != "active":
                continue
            audience = [str(value) for value in (entry.get("audience", []) or [])]
            if agent_id not in audience and agent_name not in audience:
                continue
            definition = story_defs.get(str(beat_id), {})
            active_story_beats.append({
                "summary": str(definition.get("summary", definition.get("goal_hint", beat_id))),
                "beat_id": str(beat_id),
                "world_minute": int(entry.get("started_minute", 0) or 0),
            })
        active_story_beats.sort(key=lambda item: item["world_minute"], reverse=True)
        active_story_beats = active_story_beats[:3]
        result = {
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
            "drives": drives[:5], "drive_model": drive_model, "observation": {"facts": observation_facts[:6], "source": "current_state"}, "nearby_entities": nearby[:8], "known_facts": known_facts, "research_tracks": research_tracks[:8], "memories": memories, "short_term_memories": short_term_memories, "long_term_memories": long_term_memories, "memory_query": memory_query, "recent_actions": recent_actions, "recent_outcomes": recent_outcomes, "direct_relationships": relationships,
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
            "story_goals": story_goals,
            "behavior_assignments": behavior_assignments,
            "active_story_beats": active_story_beats,
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

    @staticmethod
    def _view_action_parameters(parameters: dict[str, Any], state: dict[str, Any], context_values: dict[str, Any], *, safe_location_id: str | None = None, resource_node: dict[str, str] | None = None, health_location_id: str | None = None, default_target_id: str | None = None) -> dict[str, Any]:
        resolved = {}
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
            elif value == "context.target_id":
                resolved[key] = default_target_id
            elif isinstance(value, str) and value.startswith("context."):
                resolved[key] = context_values.get(value.removeprefix("context."))
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

    def _action_available(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], location: dict[str, Any], duration_minutes: int | None = None) -> bool:
        return self._action_unavailable_reason(agent, action, parameters, location, duration_minutes) is None

    def _action_unavailable_reason(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], location: dict[str, Any], duration_minutes: int | None = None) -> str | None:
        """Return why one Action cannot start for this actor, or ``None`` when it can.

        The reason names the failing check in the Action's own terms so a
        reviewer who sees a blocked behavior learns what to change, instead of
        a generic refusal.
        """

        resource_parameters = (action.get("parameter_defaults", {}) or {})
        resource_key = next((key for key, value in resource_parameters.items() if value == "first_available_resource"), None)
        if resource_key and parameters.get(resource_key) in {None, "", "context.resource_tag"}:
            rules = self.store.get_meta("rules", {}) or {}
            entity_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
            protected_key = str(rules.get("resource_protected_state", "protected"))
            source = next((item for item in self.store.entities_at_location((agent.get("initial_state") or {}).get("location_id")) if item.get("entity_type") in entity_types and not (item.get("initial_state") or {}).get(protected_key) and self._entity_quantity(item) > 0), None)
            parameters["resource_tag"] = source.get("id") if source else parameters.get("resource_tag")
        if action.get("requires_destination") and parameters.get("location_id") == (agent.get("initial_state") or {}).get("location_id"):
            return "the actor is already at the destination"
        context = {
            "current_location": location,
            "location_id": (agent.get("initial_state") or {}).get("location_id"),
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "area_id": parameters.get("area_id"),
            "source_id": parameters.get("source_id"),
            "item_id": parameters.get("item_id"),
            "object_id": parameters.get("object_id"),
            "current_object": {},
            "inventory": agent.get("inventory", {}) or {},
            "station_tags": [],
            "known_route_destinations": reachable_locations(self.store, (agent.get("initial_state") or {}).get("location_id")),
            "mapped_areas": self.store.explored_areas(str(agent.get("id", ""))),
            "researched_sources": self.store.researched_sources(str(agent.get("id", ""))),
            "known_fact_keys": [
                item.get("fact_key")
                for item in self.store.list_knowledge_facts(str(agent.get("id", "")), limit=1000)
                if item.get("fact_key")
            ],
            "recent_event_types": self._recent_event_types(),
        }
        context.update(configured_context_values(self.store.get_meta("rules", {}) or {}, agent))
        for effect in action.get("effects", []) or []:
            if not isinstance(effect, dict) or str(effect.get("type") or effect.get("op") or "") != "institution_allocation":
                continue
            def resolve(value: Any) -> Any:
                if isinstance(value, str) and value.startswith("context."):
                    return parameters.get(value.removeprefix("context."), value)
                return value
            subjects = resolve(effect.get("subjects"))
            if isinstance(subjects, str):
                subjects = [subjects]
            # A reusable definition may intentionally bind these values from
            # the caller at execution time.  Without a concrete institution
            # and pool there is no safe preflight to perform here.
            if not resolve(effect.get("institution")) or not resolve(effect.get("pool_entity")) or str(resolve(effect.get("institution")) or "").startswith("context.") or str(resolve(effect.get("pool_entity")) or "").startswith("context."):
                continue
            check = allocation_preflight(
                self.store,
                resolve(effect.get("institution")),
                resolve(effect.get("pool_entity")),
                subjects if isinstance(subjects, list) else None,
            )
            context.update({
                "institution.allocation_possible": bool(check.get("possible")),
                "institution.allocation_available": check.get("available", 0.0),
                "institution.allocation_required": check.get("required", 0.0),
            })
            if not check.get("possible"):
                return (
                    f"the pool holds {float(check.get('available', 0) or 0):g} but the policy minimum needs {float(check.get('required', 0) or 0):g}"
                    if check.get("reason") == "minimum_allocation"
                    else f"the institution allocation is not possible ({check.get('reason') or 'unavailable'})"
                )
        if parameters.get("object_id"):
            try:
                context["current_object"] = self.store.get_entity(str(parameters["object_id"]))
            except KeyError:
                return "the referenced object does not exist"
        if parameters.get("source_id"):
            try:
                context["source"] = self.store.get_entity(str(parameters["source_id"]))
            except KeyError:
                return "the referenced source does not exist"
            context["research_discoveries"] = self.store.research_discoveries(str(parameters["source_id"]))
        if (
            action.get("duration", {}).get("mode") == "recipe_duration"
            or "recipe_inputs" in (action.get("costs", {}).get("reserve", {}) or {})
        ):
            recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
            if recipe is None:
                return "the recipe is not registered"
            context["station_tags"] = list(recipe.get("station_tags", []) or [])
        resolved_target, target_error = self.targets.resolve(agent, parameters)
        if target_error:
            return "no permitted target can be resolved"
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
            return "an active rule denies it"
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=target, context=context, world=self.store.get_meta("world_properties", {}) or {}):
                return f"precondition not met: {predicate}"
        capacity_ok, capacity_detail = check_action_capacity(
            agent,
            action,
            max(1, int(duration_minutes or 1)),
            self.store.get_meta("attributes", []) or [],
        )
        if not capacity_ok:
            return str(capacity_detail or "the actor lacks the capacity for its duration")
        allowed, reserve_detail, _ = self.resources.reserve(agent, action, parameters, commit=False)
        return None if allowed else str(reserve_detail or "required resources are unavailable")

    @staticmethod
    def _entity_quantity(entity: dict[str, Any]) -> float:
        state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
        value = entity.get("amount")
        if value is None:
            value = state.get("quantity", state.get("amount", 0))
        return float(value or 0)

    def _pending_research_sources(
        self,
        agent: dict[str, Any],
        known_fact_keys: list[Any] | set[Any],
        location_id: str | None,
        configured_source_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return reachable source entities with at least one open stage.

        Research is a generic capability.  The catalog is assembled from
        persisted entity data, so a source registered later by God is visible
        to the same BT as an opening source.  Only the source's configured
        stages are exposed; object-specific BT definitions are never needed.
        """

        known = {str(item) for item in (known_fact_keys or []) if item}
        reachable = reachable_locations(self.store, location_id)
        if location_id:
            reachable.add(str(location_id))
        result: list[dict[str, Any]] = []
        configured_source_ids = {str(item) for item in (configured_source_ids or set())}
        agent_name = str(agent.get("name") or agent.get("id") or "")
        for source in self.store.research_source_catalog():
            source_id = str(source.get("id") or "")
            if not source_id:
                continue
            source_state = {}
            try:
                source_entity = self.store.get_entity(source_id)
                source_state = source_entity.get("initial_state") if isinstance(source_entity.get("initial_state"), dict) else {}
            except KeyError:
                source_entity = {}
            assignees = source_entity.get("research_assignees", source_state.get("research_assignees", []))
            if isinstance(assignees, str):
                assignees = [assignees]
            assigned = {str(item) for item in (assignees or [])}
            assigned_ids = {
                str(self.targets.canonical_id(item) or item)
                for item in assigned
            }
            if source_id not in configured_source_ids and str(agent.get("id")) not in assigned_ids and agent_name not in assigned:
                continue
            source_location = source.get("location_id")
            if source_location and reachable and str(source_location) not in reachable:
                continue
            discoveries = list(source.get("discoveries") or [])
            pending = [
                item for item in discoveries
                if isinstance(item, dict)
                and str(item.get("id") or "") not in known
                and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"}
            ]
            if not pending and discoveries:
                continue
            try:
                location_name = self.store.get_entity(str(source_location)).get("name", source_location) if source_location else "configured source"
            except KeyError:
                location_name = source_location or "configured source"
            result.append({
                **source,
                "location_name": str(location_name),
                "remaining_stages": len(pending) if discoveries else 1,
            })
        result.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))
        return result

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
        story_memory_query = " ".join(
            str(item) for item in (
                "story narrative thread",
                (source_event or {}).get("event_type") if isinstance(source_event, dict) else "",
                (source_event or {}).get("result") if isinstance(source_event, dict) else "",
                " ".join(str(item.get("summary") or "") for item in (story_state.get("emergent_beats", []) or [])[-3:] if isinstance(item, dict)),
            ) if item
        )
        story_memories = [self._humanize_record(item) for item in role_memory_query(self.store, "story", story_memory_query, limit=6)]
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
                "result": self._visible_text(outcome.get("result") or source_event.get("result") or source_event.get("fact") or "recorded"),
                "fact": self._humanize_fact(source_event.get("fact") or (outcome.get("expected") or {}).get("summary") or ""),
                "world_minute": source_event.get("world_minute"),
            }
            source_id = source_event.get("source_id")
            if source_id:
                try:
                    validated_source["research_source"] = self.store.get_entity(str(source_id)).get("name", source_id)
                except KeyError:
                    validated_source["research_source"] = str(source_id)
            if str(source_event.get("event_type")) == "institution.allocation_completed":
                consumed = source_event.get("consumed")
                remaining = source_event.get("remaining")
                allocations = source_event.get("allocations") or []
                item = str(source_event.get("item") or "resource")
                validated_source["result"] = (
                    f"Allocated {consumed} {item}; {remaining} remains in the shared pool."
                )
                validated_source["allocation_summary"] = [
                    {
                        "subject": self._visible_text(str(row.get("subject", "a character"))),
                        "amount": row.get("total"),
                    }
                    for row in allocations[:12]
                    if isinstance(row, dict)
                ]
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
                    validated_source["fact"] = self._humanize_fact(str(fact_data["fact"]))
                    validated_source["fact_key"] = str(row["fact_key"] or fact_data.get("source_id") or "")
                    break
        audience_candidates = self._story_audience_candidates(recent_events)
        observed_events = {str(item.get("event_type", "")) for item in recent_events}
        fact_rows = self.store.connection.execute("SELECT fact_key,data_json FROM knowledge_fact ORDER BY id DESC").fetchall()
        observed_facts = {str(row["fact_key"]) for row in fact_rows}
        choices: list[dict[str, Any]] = []
        scoped_beats = eligible_story_beats(
            list(story_root.get("beats", []) or []) + list(story_state.get("emergent_beats", []) or []),
            story_state,
            source_event_type=story_evidence_event_types(source_event),
            observed_events=observed_events,
            observed_facts=observed_facts,
        )
        for beat in scoped_beats:
            beat_id = str(beat.get("id", ""))
            trigger_events = {str(item) for item in (beat.get("trigger_events", []) or [])}
            trigger_facts = {str(item) for item in (beat.get("trigger_facts", []) or [])}
            choices.append({
                "ref": f"story:{beat_id}",
                "choice": str(beat.get("agent_label") or beat.get("summary") or beat_id),
                "summary": self._visible_text(str(beat.get("summary", ""))),
                "when": self._visible_text("; ".join(sorted(trigger_events))) or "The latest evidence supports a new story direction.",
                "requires": "; ".join(self._humanize_fact(item) for item in sorted(trigger_facts)) or "A recent validated discovery is required.",
                "audience": [self._visible_text(str(item)) for item in (beat.get("audience", []) or [])],
                "followup_behaviors": [self._visible_text(str(item)) for item in (beat.get("followup_behaviors", []) or [])],
                "impact": ["Platform records the beat, informs its configured audience, and unlocks only configured behaviors."],
            })
        prompt_protocol, platform_rules = self._prompt_sections(role)
        known_facts: list[str] = []
        seen_fact_keys: set[str] = set()
        seen_fact_text: set[str] = set()
        for row in fact_rows:
            try:
                data = json.loads(row["data_json"])
            except (TypeError, json.JSONDecodeError):
                data = {}
            fact = data.get("fact") or data.get("summary")
            text = str(self._humanize_fact(str(fact or str(row["fact_key"]).replace(":", " — ").replace("_", " ")))).strip()
            fact_key = str(row["fact_key"] or "").strip()
            normalized_text = text.casefold()
            if (fact_key and fact_key in seen_fact_keys) or normalized_text in seen_fact_text:
                continue
            if fact_key:
                seen_fact_keys.add(fact_key)
            seen_fact_text.add(normalized_text)
            known_facts.append(text)
            if len(known_facts) >= 12:
                break
        return {
            "context_version": "1", "role": role,
            "prompt_protocol": prompt_protocol, "platform_rules": platform_rules,
            "world_time": self._world_time(minute),
            "self": {"id": actor_id, "name": "Story Agent", "role": role, "status": "ready", "location": "world", "goal": "Turn validated discoveries into coherent continuing story beats."},
            "identity": {"traits": {}, "beliefs": [], "capabilities": {"story_scope": True}},
            "needs": {}, "inventory": {}, "injuries": [], "drives": [],
            "observation": {"facts": [self._visible_text(str(item.get("event_type", "event")) + ": " + str(item.get("summary") or item.get("fact") or item.get("result") or "validated world result")) for item in recent_events[:10]], "source": "validated_world_events"},
            "validated_source": validated_source,
            "nearby_entities": [], "known_facts": [{"fact": fact, "source_type": "world_knowledge", "confidence": 1.0} for fact in known_facts],
            "audience_candidates": audience_candidates,
            "recent_actions": recent_events[:8], "recent_story_events": [item for item in recent_events if str(item.get("event_type", "")).startswith("story.")][:8],
            "recent_history": self._recent_history_view(16),
            "knowledge_gaps": self._knowledge_gap_view(status="active", limit=8),
            "memories": story_memories,
            "long_term_memories": story_memories,
            "short_term_memories": [],
            "memory_query": story_memory_query,
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
                    "evidence_sources": list((value or {}).get("evidence_sources") or []),
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
            candidate_view = self.build_decision_view(str(candidate_id), role="agent_decision")
            executable = [
                {
                    "choice": str(item.get("choice") or item.get("name") or ""),
                    "purpose": str(item.get("purpose") or item.get("summary") or ""),
                    "parameters": list(item.get("parameters") or []),
                    "parameter_options": dict(item.get("parameter_options") or {}),
                }
                for item in (candidate_view.get("executable_behaviors") or [])[:8]
            ]
            result.append({
                "name": name,
                "reason": "participant in a recent validated event",
                "executable_behaviors": executable,
                "research_tracks": list(candidate_view.get("research_tracks") or [])[:6],
            })
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
        god_memory_query = "world diagnosis proposal outcome trend " + " ".join(
            str(item.get("event_type") or "") for item in recent_world_changes[:4]
        )
        god_memories = [self._humanize_record(item) for item in role_memory_query(self.store, "god", god_memory_query, limit=6)]
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
        configured_regeneration = []
        for raw in (self.store.get_meta("rules", {}) or {}).get("resource_regeneration", []) or []:
            if not isinstance(raw, dict) or not raw.get("resource_id"):
                continue
            resource_id = str(raw.get("resource_id"))
            try:
                resource_name = str(self.store.get_entity(resource_id).get("name") or resource_id)
            except KeyError:
                resource_name = resource_id.replace("_", " ")
            configured_regeneration.append({
                "resource": resource_name,
                "every_minutes": int(raw.get("every_minutes", 0) or 0),
                "amount": raw.get("amount", 0),
                "cap": raw.get("cap"),
            })
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
                "parameters": [str(key) for key in (action.get("parameters") or {}).keys()],
                "parameter_schema": {
                    str(key): (value if isinstance(value, dict) else {"type": str(value)})
                    for key, value in (action.get("parameters") or {}).items()
                },
                "parameter_defaults": dict(action.get("parameter_defaults") or {}),
                "success": [str(item).replace("_", " ") for item in ([action.get("success", {}).get("result")] if isinstance(action.get("success"), dict) else []) if item],
            })
        condition_fields = [
            {"condition": f'world["{str(key)}"]', "meaning": str(key).replace("_", " ")}
            for key in (self.store.get_meta("world_properties", {}) or {})
        ]
        condition_fields.extend(
            {"condition": f"event.recent.{event_type}", "meaning": "a recent validated public event"}
            for event_type in self._recent_event_types()[:16]
            if event_type not in {"world_minute.advanced", "world.created"}
        )
        visible_entities = []
        for entity_type in ("location", "agent", "faction", "object", "resource", "material"):
            for item in self.store.entities_of_type(entity_type):
                name = str(item.get("name") or "").strip()
                if name:
                    visible_entities.append({"name": name, "type": entity_type})
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
            "recent_history": self._recent_history_view(20),
            "knowledge_gaps": self._knowledge_gap_view(status="active", limit=12),
            "memories": god_memories,
            "long_term_memories": god_memories,
            "short_term_memories": [],
            "memory_query": god_memory_query,
            "recent_world_changes": recent_world_changes,
            "world_proposals": world_proposals,
            "recent_agent_outcomes": recent_agent_outcomes,
            "world_trends": world_trends,
            "configured_resource_regeneration": configured_regeneration,
            "civilization_progress": self.store.list_progress(world_id, limit=20),
            "world_progress_candidates": [
                {
                    "progress_key": str(key),
                    "required": float((value or {}).get("required", 1) or 1),
                    "description": str((value or {}).get("description") or ""),
                    "evidence_event_types": [str(item) for item in ((value or {}).get("evidence_event_types") or [])],
                    "evidence_behaviors": [str(item) for item in ((value or {}).get("evidence_behaviors") or [])],
                    "evidence_sources": list((value or {}).get("evidence_sources") or []),
                }
                for key, value in ((self.store.get_meta("rules", {}) or {}).get("civilization_progress", {}) or {}).items()
                if isinstance(value, dict)
            ],
            "pending_institutions": [
                {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "proposed"}
                for item in self.store.list_institutions(status="proposed", limit=20)
            ],
            "active_institutions": [
                {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "active", "terms": describe_institution_terms(item)}
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
            "visible_entities": visible_entities[:64],
            "condition_examples": {
                "capability": "actor.can_<capability>",
                "location": "actor.state.location_id == <visible location name>",
                "world_property": 'world["<configured property>"]',
                "recent_event": "event.recent.<event type>",
            },
            "rule_condition_fields": condition_fields[:24],
            "direct_relationships": [],
            "active_task": {"bt": None, "action": None, "remaining_minutes": 0},
            "executable_behaviors": [],
            "last_platform_result": recent_world_events[0] if recent_world_events else None,
            "tools": self._role_tools(role),
        }
