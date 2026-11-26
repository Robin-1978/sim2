"""Authoring-time validation of the single-object world template.

``validate_world_template`` reports every problem it finds, in section order,
so an author (or WorldBuilder) can fix a template in one pass.  Each ``check_*``
method owns one section of the template and appends to ``errors``; the checks
share the identifiers declared by earlier sections through the instance.
"""

from __future__ import annotations

import re
from typing import Any

from ..actions.conditions import ConditionEngine
from ..behavior.plan import compile_behavior_plan, wait_minutes
from ..world.lifecycle import validate_incapacitation_rule
from ..dsl.expressions import parse_expression, reference_paths
from ..world.definition_compiler import EFFECT_TYPES, action_conditions, condition_syntax_error
from ..evolution.config import normalize_evolution_config
from ..world.time import validate_day_phase_duration
from ..world.ui_text import validate_ui_text
from .artwork import decode_template_artwork
from .catalog_validation import validate_action_catalogs

COLLECTION_NAMES = (
    "attributes", "locations", "resources", "objects", "factions", "agents",
    "capabilities", "actions", "recipes", "behavior_trees", "relationships",
    "initial_events",
)
BEHAVIOR_NODE_TYPES = frozenset({"action", "subtree", "behavior", "sequence", "parallel", "selector", "condition", "wait"})
# State an agent carries without the world declaring it as an attribute.
STRUCTURAL_AGENT_STATE = frozenset({"location_id", "status", "injuries", "conditions", "active_bt_instance", "active_action", "remaining_minutes", "action_progress", "current_goal_id", "last_platform_result"})


def validate_world_template(template: dict[str, Any]) -> dict[str, Any]:
    """Validate the single-object authoring format used by WorldBuilder."""

    if not isinstance(template, dict):
        return {"valid": False, "errors": ["template must be an object"]}
    return _TemplateChecks(template).run()


class _TemplateChecks:
    def __init__(self, template: dict[str, Any]) -> None:
        self.template = template
        self.errors: list[str] = []
        self.world = template.get("world")
        self.rules: dict[str, Any] = template.get("rules") if isinstance(template.get("rules"), dict) else {}
        self.condition_engine = ConditionEngine(self.rules)
        self.collections: dict[str, list[dict[str, Any]]] = {}
        self.world_properties: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        self.check_header()
        self.check_collections()
        self.derive_identifiers()
        self.check_required_collections()
        self.check_world_properties()
        self.check_progression()
        self.check_attributes()
        self.check_locations()
        self.check_resources_and_objects()
        self.check_location_connectivity()
        self.check_actions()
        self.check_action_catalogs()
        self.check_behaviors()
        self.check_agents()
        self.check_recipes()
        self.check_context_rules()
        self.check_vital_and_needs()
        self.check_minute_effects()
        self.check_relationships()
        self.check_initial_events()
        try:
            normalize_evolution_config(self.template.get("evolution"))
        except (TypeError, ValueError) as exc:
            self.errors.append(f"evolution: {exc}")
        return {"valid": not self.errors, "errors": list(dict.fromkeys(self.errors))}

    # ------------------------------------------------------------------ shape

    def check_header(self) -> None:
        world = self.world
        if not isinstance(world, dict) or not str(world.get("id") or "").strip():
            self.errors.append("world.id is required")
        if isinstance(world, dict):
            self.errors.extend(error.replace("ui_text", "world.ui_text", 1) for error in validate_ui_text(world.get("ui_text")))
            try:
                decode_template_artwork(world)
            except ValueError as exc:
                self.errors.append(str(exc))
        if self.template.get("template_version") is not None and not isinstance(self.template.get("template_version"), str):
            self.errors.append("template_version must be a string")

    def check_collections(self) -> None:
        for key in COLLECTION_NAMES:
            value = self.template.get(key, [])
            if not isinstance(value, list):
                self.errors.append(f"{key} must be a list")
                value = []
            self.collections[key] = [item for item in value if isinstance(item, dict)]
            if len(self.collections[key]) != len(value):
                self.errors.append(f"{key} entries must be objects")
            ids = [str(item.get("id") or "") for item in self.collections[key] if key != "relationships"]
            if any(not value for value in ids):
                self.errors.append(f"{key} entries require non-empty ids")
            if len(ids) != len(set(ids)):
                self.errors.append(f"duplicate id in {key}")

    def derive_identifiers(self) -> None:
        def ids_of(collection: str) -> set[str]:
            return {str(item.get("id")) for item in self.collections[collection]}

        def attribute_ids_of(kind: str) -> set[str]:
            return {str(item.get("id")) for item in self.collections["attributes"] if item.get("kind") == kind}

        self.attribute_ids = ids_of("attributes")
        self.location_ids = ids_of("locations")
        self.faction_ids = ids_of("factions")
        self.agent_ids = ids_of("agents")
        self.action_ids = ids_of("actions")
        self.behavior_ids = ids_of("behavior_trees")
        self.capability_ids = ids_of("capabilities")
        self.resources = ids_of("resources") | {str(item.get("name")) for item in self.collections["resources"] if item.get("name")}
        self.state_ids = attribute_ids_of("state")
        self.need_ids = attribute_ids_of("need")

    def check_required_collections(self) -> None:
        for required_collection in ("attributes", "locations", "agents", "actions", "behavior_trees"):
            if not self.collections[required_collection]:
                self.errors.append(f"{required_collection} must contain at least one entry")

    def check_world_properties(self) -> None:
        world = self.world
        world_properties = (world or {}).get("properties", {}) if isinstance(world, dict) else {}
        if not isinstance(world_properties, dict):
            self.errors.append("world.properties must be an object")
            world_properties = {}
        self.world_properties = world_properties
        duplicate_scopes = self.attribute_ids & {str(key) for key in world_properties}
        if duplicate_scopes:
            self.errors.append("character attributes and world properties must use distinct ids: " + ", ".join(sorted(duplicate_scopes)))

    # --------------------------------------------------------------- progression

    def check_progression(self) -> None:
        errors = self.errors
        progression = self.rules.get("progression", [])
        if progression is None:
            progression = []
        if not isinstance(progression, list):
            errors.append("rules.progression must be a list")
            progression = []
        known_audience_ids = self.agent_ids | {"player"} | {
            str(item.get("name")) for item in self.collections["agents"] if item.get("name")
        }
        progression_ids: set[str] = set()
        for index, stage in enumerate(progression):
            if not isinstance(stage, dict):
                errors.append(f"rules.progression[{index}] must be an object")
                continue
            stage_id = str(stage.get("id") or "").strip()
            owner = f"progression stage {stage_id or index}"
            if not stage_id:
                errors.append(f"rules.progression[{index}] requires a non-empty id")
            elif stage_id in progression_ids:
                errors.append(f"duplicate progression stage id: {stage_id}")
            progression_ids.add(stage_id)
            audience = stage.get("audience", [])
            if audience is None:
                audience = []
            if not isinstance(audience, list) or any(not isinstance(item, str) or not item for item in audience):
                errors.append(f"{owner}.audience must be a list of agent ids or names")
            else:
                for recipient in audience:
                    if recipient not in known_audience_ids:
                        errors.append(f"{owner} references unknown audience member {recipient}")
            referenced_behaviors: list[str] = []
            for field in ("target_behaviors", "support_behaviors"):
                values = stage.get(field, [])
                if values is None:
                    values = []
                if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
                    errors.append(f"{owner}.{field} must be a list of behavior ids")
                    continue
                referenced_behaviors.extend(values)
                for behavior_id in values:
                    if behavior_id not in self.behavior_ids:
                        errors.append(f"{owner} references unknown behavior {behavior_id}")
            if not referenced_behaviors:
                errors.append(f"{owner} must name at least one target or support behavior")
            contract = stage.get("complete_when")
            if contract is None and stage.get("visible_when"):
                self.check_expression(stage["visible_when"], f"{owner} visibility")
                continue
            if not isinstance(contract, dict):
                errors.append(f"{owner}.complete_when must be an object")
                continue
            self._check_completion_contract(contract, owner)

    def _check_completion_contract(self, contract: dict[str, Any], owner: str) -> None:
        errors = self.errors
        event_type = str(contract.get("event_type") or "").strip()
        if not event_type:
            errors.append(f"{owner}.complete_when.event_type is required")
        count = contract.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            errors.append(f"{owner}.complete_when.count must be a positive integer")
        match = contract.get("match", {})
        if not isinstance(match, dict) or any(
            not re.fullmatch(r"[A-Za-z0-9_]+", str(key)) for key in match
        ):
            errors.append(f"{owner}.complete_when.match must use flat identifier keys")
        distinct_by = contract.get("distinct_by", [])
        if distinct_by is None:
            distinct_by = []
        if not isinstance(distinct_by, list) or any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_]+", item)
            for item in distinct_by
        ):
            errors.append(f"{owner}.complete_when.distinct_by must be a list of flat identifier fields")

    # ------------------------------------------------------- attributes, places

    def check_attributes(self) -> None:
        errors = self.errors
        for attribute in self.collections["attributes"]:
            for field in ("kind", "label", "min", "max", "default", "prompt"):
                if field not in attribute:
                    errors.append(f"attribute {attribute.get('id')} is missing {field}")
            if attribute.get("kind") == "state" and attribute.get("cost_mode") not in {"increase", "decrease"}:
                errors.append(f"state attribute {attribute.get('id')} must declare cost_mode")
            minimum, maximum, default = attribute.get("min"), attribute.get("max"), attribute.get("default")
            if all(isinstance(value, (int, float)) for value in (minimum, maximum, default)):
                if float(minimum) >= float(maximum):
                    errors.append(f"attribute {attribute.get('id')} min must be lower than max")
                if not float(minimum) <= float(default) <= float(maximum):
                    errors.append(f"attribute {attribute.get('id')} default must be within its range")

    def check_locations(self) -> None:
        errors = self.errors
        ui = self.world.get("ui") if isinstance(self.world, dict) else None
        scene_art = ui.get("scene_art") if isinstance(ui, dict) else None
        if isinstance(scene_art, dict):
            for location_id in scene_art:
                if location_id not in self.location_ids:
                    errors.append(f"world.ui.scene_art references unknown location {location_id}")
        for location in self.collections["locations"]:
            if not isinstance(location.get("routes"), list):
                errors.append(f"location {location.get('id')} must declare routes as a list")
            for route in location.get("routes", []) or []:
                if str(route) not in self.location_ids:
                    errors.append(f"location {location.get('id')} references unknown route {route}")
            route_distances = location.get("route_distances", {})
            if route_distances is not None and not isinstance(route_distances, dict):
                errors.append(f"location {location.get('id')} route_distances must be an object")
            elif isinstance(route_distances, dict):
                for route, distance in route_distances.items():
                    if str(route) not in {str(item) for item in (location.get("routes", []) or [])}:
                        errors.append(f"location {location.get('id')} route_distances references non-route {route}")
                    if isinstance(distance, bool) or not isinstance(distance, (int, float)) or float(distance) <= 0:
                        errors.append(f"location {location.get('id')} route distance for {route} must be positive")

    def check_resources_and_objects(self) -> None:
        errors = self.errors
        for item in self.collections["objects"]:
            if not str(item.get("type") or "").strip():
                errors.append(f"{item.get('id')} requires a non-empty type")
        for item in self.collections["resources"] + self.collections["objects"]:
            location_id = item.get("location_id") or (item.get("initial_state") or {}).get("location_id")
            if location_id and str(location_id) not in self.location_ids:
                errors.append(f"{item.get('id')} references unknown location {location_id}")

    def check_location_connectivity(self) -> None:
        visible_locations = [item for item in self.collections["locations"] if item.get("public") != "hidden"]
        if len(visible_locations) > 1:
            for location in visible_locations:
                if not location.get("routes"):
                    self.errors.append(f"visible location {location.get('id')} must connect to at least one route")

    # ------------------------------------------------- expressions and effects

    def check_expression(self, expression: Any, owner: str, *, result_contract: bool = False) -> None:
        errors = self.errors
        if expression is None or (isinstance(expression, str) and not expression.strip()):
            return
        if error := condition_syntax_error(expression):
            errors.append(f"{owner} has invalid expression: {error}")
            return
        # Legacy result contracts may name domain signals such as resource.collected.
        if not result_contract:
            for path in self.condition_engine.unknown_paths(expression):
                errors.append(f"{owner} uses an unsupported condition path: {path}")
        for path in dict.fromkeys(reference_paths(parse_expression(expression))):
            for prefix, declared, kind in (
                ("actor.state.", self.state_ids | STRUCTURAL_AGENT_STATE, "state attribute"),
                ("target.state.", self.state_ids | STRUCTURAL_AGENT_STATE, "state attribute"),
                ("actor.need.", self.need_ids, "need"),
                ("actor.needs.", self.need_ids, "need"),
                ("world.", self.world_properties, "world property"),
            ):
                if path.startswith(prefix):
                    key = path.removeprefix(prefix)
                    if key not in declared and key.partition(".")[0] not in declared:
                        errors.append(f"{owner} references undeclared {kind} {key}")
            if path in self.attribute_ids:
                errors.append(f"{owner} must reference {path} through actor.state, actor.need or target.state")

    def check_effect(self, effect: dict[str, Any], owner: str) -> None:
        errors = self.errors
        effect_type = str(effect.get("type") or effect.get("op") or "")
        if effect_type in {"need_delta", "state_delta", "world_property_delta", "relationship_delta"}:
            if not isinstance(effect.get("delta"), (int, float)):
                errors.append(f"{owner} {effect_type} requires numeric delta")
        elif effect_type == "attribute_delta":
            if not isinstance(effect.get("delta"), (int, float)) and not str(effect.get("formula") or "").strip():
                errors.append(f"{owner} attribute_delta requires numeric delta or formula")
        elif effect_type == "attribute_set" and "value" not in effect:
            errors.append(f"{owner} attribute_set requires value")
        elif effect_type in {"inventory_add", "inventory_remove", "inventory.consume_item"}:
            quantity = effect.get("quantity", 1)
            if not str(effect.get("item") or "").strip() or not (isinstance(quantity, (int, float)) or isinstance(quantity, str) and quantity.startswith("context.")):
                errors.append(f"{owner} {effect_type} requires item and numeric quantity")
        elif effect_type == "location_set" and not str(effect.get("location_id") or "").strip():
            errors.append(f"{owner} location_set requires location_id")
        elif effect_type == "entity_state_set":
            if not str(effect.get("entity_id") or "").strip() or not str(effect.get("state") or "").strip() or "value" not in effect:
                errors.append(f"{owner} entity_state_set requires entity_id, state and value")
        elif effect_type == "institution_allocation":
            required = ("institution", "pool_entity", "item")
            if any(not str(effect.get(key) or "").strip() for key in required):
                errors.append(f"{owner} institution_allocation requires institution, pool_entity and item")
        elif effect_type == "public_event" and not str(effect.get("event") or "").strip():
            errors.append(f"{owner} public_event requires event")
        elif effect_type == "catalog_action" and (not str(effect.get("catalog") or "").strip() or not str(effect.get("entry") or "").strip()):
            errors.append(f"{owner} catalog_action requires catalog and entry")
        elif effect_type in {"status_add", "status_remove"} and not str(effect.get("status") or "").strip():
            errors.append(f"{owner} {effect_type} requires status")
        if effect_type == "world_property_delta" and not str(effect.get("property") or "").strip():
            errors.append(f"{owner} world_property_delta requires property")
        if effect_type == "relationship_delta" and not str(effect.get("relation") or "").strip():
            errors.append(f"{owner} relationship_delta requires relation")

    def check_effect_targets(self, effect: dict[str, Any], subject: str) -> None:
        """An effect may only touch state, needs and world properties the world declares."""

        errors = self.errors
        effect_type = str(effect.get("type") or effect.get("op") or "")
        if effect_type in {"state_delta", "attribute_delta", "attribute_set"}:
            key = str(effect.get("attribute") or effect.get("state") or "")
            if key not in self.state_ids:
                errors.append(f"{subject} references undeclared state attribute {key}")
        if effect_type == "need_delta" and str(effect.get("need") or "") not in (self.need_ids | self.state_ids):
            errors.append(f"{subject} references undeclared need {effect.get('need')}")
        if effect_type in {"world_property_delta", "world_property_set"} and not str(effect.get("property") or "").startswith("context.") and str(effect.get("property") or "") not in self.world_properties:
            errors.append(f"{subject} references undeclared world property {effect.get('property')}")
        if effect_type in {"world_resource_delta", "resource_transfer", "resource_withdrawal", "resource_take"}:
            resource = str(effect.get("resource_id") or effect.get("resource") or "")
            if not resource.startswith("context.") and resource not in self.resources:
                errors.append(f"{subject} references unknown world resource {resource or '<missing>'}")

    def check_effect_list(self, effects: Any, owner: str) -> None:
        if not isinstance(effects, list):
            self.errors.append(f"{owner} must be a list")
            return
        for effect in effects:
            if not isinstance(effect, dict) or str(effect.get("type") or effect.get("op") or "") not in EFFECT_TYPES:
                self.errors.append(f"{owner} has unsupported or missing effect type")
                continue
            self.check_effect(effect, owner)
            self.check_effect_targets(effect, owner)

    # ------------------------------------------------------------------ actions

    def check_actions(self) -> None:
        errors = self.errors
        for action in self.collections["actions"]:
            duration = action.get("duration")
            if "preconditions" in action and not isinstance(action["preconditions"], list):
                errors.append(f"action {action.get('id')}.preconditions must be a list")
            if "_recipe_snapshot" in action:
                errors.append(f"action {action.get('id')} must not declare execution snapshots")
            if not isinstance(duration, dict):
                errors.append(f"action {action.get('id')} requires a duration object")
            elif not (
                isinstance(duration.get("minutes"), (int, float)) and float(duration.get("minutes")) >= 0
            ) and str(duration.get("mode") or "") not in {"route_distance", "recipe", "recipe_duration", "until_day_phase"}:
                errors.append(f"action {action.get('id')} duration needs non-negative minutes or a supported mode")
            if error := validate_day_phase_duration(action, self.world or {}):
                errors.append(error)
            for field, condition in action_conditions(action):
                self.check_expression(condition, f"action {action.get('id')}.{field}", result_contract=field.endswith(".condition"))
            self._check_action_costs(action)
            effects = action.get("effects", [])
            if not effects and not str(action.get("executor") or "").strip():
                errors.append(f"action {action.get('id')} requires at least one typed effect")
            self.check_effect_list(effects, f"action {action.get('id')}.effects")
            self.check_effect_list(action.get("progress_effects", []), f"action {action.get('id')}.progress_effects")
            if not isinstance(action.get("success"), dict) or not isinstance(action.get("success", {}).get("condition"), str) or not isinstance(action.get("success", {}).get("result"), str):
                errors.append(f"action {action.get('id')} requires a success condition and result")
            if not isinstance(action.get("failure"), dict) or not isinstance(action.get("failure", {}).get("condition"), str) or not isinstance(action.get("failure", {}).get("result"), str):
                errors.append(f"action {action.get('id')} requires a failure condition and result")

    def check_action_catalogs(self) -> None:
        self.errors.extend(validate_action_catalogs(
            self.rules,
            resources=self.collections["resources"],
            location_ids=self.location_ids,
            condition_engine=self.condition_engine,
            state_ids=self.state_ids,
            need_ids=self.need_ids,
            world_properties=set(self.world_properties),
            item_labels=self.rules.get("item_labels") if isinstance(self.rules.get("item_labels"), dict) else {},
        ))

    def _check_action_costs(self, action: dict[str, Any]) -> None:
        errors = self.errors
        costs = action.get("costs")
        required_cost_sections = {"reserve", "per_minute", "on_success", "on_failure", "refund"}
        if not isinstance(costs, dict) or not required_cost_sections <= set(costs):
            errors.append(f"action {action.get('id')} costs must declare reserve, per_minute, on_success, on_failure and refund")
        elif any(not isinstance(costs.get(key), dict) for key in required_cost_sections):
            errors.append(f"action {action.get('id')} cost sections must be objects")
        else:
            for attribute_id in costs.get("per_minute", {}):
                if attribute_id != "time" and str(attribute_id) not in self.attribute_ids:
                    errors.append(f"action {action.get('id')} cost references undeclared attribute {attribute_id}")
                value = costs.get("per_minute", {}).get(attribute_id)
                if isinstance(value, (int, float)) and float(value) < 0:
                    errors.append(f"action {action.get('id')} per-minute cost {attribute_id} must be non-negative; cost_mode controls direction")
            for resource_id in costs.get("reserve", {}):
                if str(resource_id) in self.attribute_ids:
                    errors.append(f"action {action.get('id')} must put state cost {resource_id} in costs.per_minute, not resource reserve")

    # ---------------------------------------------------------------- behaviors

    def check_behaviors(self) -> None:
        errors = self.errors
        definitions = {str(item.get("id")): item for item in self.collections["behavior_trees"]}
        for behavior in self.collections["behavior_trees"]:
            self.check_effect_list(behavior.get("outcome_effects", []), f"behavior {behavior.get('id')}.outcome_effects")
            nodes = behavior.get("nodes", [])
            if not isinstance(nodes, list) or not nodes:
                errors.append(f"behavior {behavior.get('id')} requires at least one node")
                continue
            used_action_ids: set[str] = set()
            previous_errors = len(errors)
            for index, node in enumerate(nodes):
                self._check_behavior_node(behavior, node, f"nodes[{index}]", used_action_ids)
            if len(errors) == previous_errors:
                try:
                    compile_behavior_plan(str(behavior.get("id")), definitions, {})
                except ValueError as error:
                    errors.append(f"behavior {behavior.get('id')}: {error}")
            for condition in behavior.get("entry_conditions", []) or []:
                self.check_expression(condition, f"behavior {behavior.get('id')} entry condition")
            self._check_outcome_events(behavior, used_action_ids)

    def _check_behavior_node(self, behavior: dict[str, Any], node: Any, path: str, used_action_ids: set[str]) -> None:
        errors = self.errors
        if not isinstance(node, dict):
            errors.append(f"behavior {behavior.get('id')} contains a non-object node at {path}")
            return
        node_type = str(node.get("type") or "")
        if node_type not in BEHAVIOR_NODE_TYPES:
            errors.append(f"behavior {behavior.get('id')} has unsupported node type {node_type or 'missing'} at {path}")
            return
        if node_type == "action" and str(node.get("action_id")) not in self.action_ids:
            errors.append(f"behavior {behavior.get('id')} references unknown action {node.get('action_id')}")
        elif node_type == "action":
            used_action_ids.add(str(node.get("action_id")))
        elif node_type in {"subtree", "behavior"} and str(node.get("behavior_id")) not in self.behavior_ids:
            errors.append(f"behavior {behavior.get('id')} references unknown behavior {node.get('behavior_id')}")
        elif node_type in {"sequence", "parallel", "selector"}:
            children = node.get("children")
            if not isinstance(children, list) or not children:
                errors.append(f"behavior {behavior.get('id')} {node_type} at {path} requires children")
            else:
                for index, child in enumerate(children):
                    self._check_behavior_node(behavior, child, f"{path}.children[{index}]", used_action_ids)
        elif node_type == "condition":
            expression = node.get("expression")
            if not isinstance(expression, str) or not expression.strip():
                errors.append(f"behavior {behavior.get('id')} condition at {path} requires expression")
            else:
                self.check_expression(expression, f"behavior {behavior.get('id')} {path}.expression")
        elif node_type == "wait":
            try:
                wait_minutes(node)
            except ValueError as error:
                errors.append(f"behavior {behavior.get('id')} {path}: {error}")
        if node_type not in {"sequence", "parallel", "selector"} and "children" in node:
            errors.append(f"behavior {behavior.get('id')} {path} does not accept children")

    def _check_outcome_events(self, behavior: dict[str, Any], used_action_ids: set[str]) -> None:
        """A behavior outcome must not repeat an event its actions already record."""

        errors = self.errors
        used_actions = [action for action in self.collections["actions"] if str(action.get("id")) in used_action_ids]
        action_information_events = {
            str((action.get("information") or {}).get("event_type") or "")
            for action in used_actions
            if isinstance(action.get("information"), dict)
        }
        action_public_events = {
            str(effect.get("event") or "")
            for action in used_actions
            for effect in (action.get("effects") or [])
            if isinstance(effect, dict) and str(effect.get("type") or "") == "public_event"
        }
        for outcome in behavior.get("outcome_events", []) or []:
            if not isinstance(outcome, dict):
                errors.append(f"behavior {behavior.get('id')} outcome_events entries must be objects")
                continue
            event_type = str(outcome.get("event_type") or "")
            if not event_type:
                errors.append(f"behavior {behavior.get('id')} outcome event requires event_type")
            elif event_type in action_information_events:
                errors.append(
                    f"behavior {behavior.get('id')} duplicates action information event {event_type}"
                )
            elif event_type in action_public_events:
                errors.append(
                    f"behavior {behavior.get('id')} duplicates action public_event {event_type}"
                )

    # ------------------------------------------------------------------- agents

    def check_agents(self) -> None:
        errors = self.errors
        for agent in self.collections["agents"]:
            state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
            for field in ("name", "role", "ultimate_goal", "initial_state", "initial_bt_ids", "needs", "inventory"):
                if field not in agent:
                    errors.append(f"agent {agent.get('id')} is missing {field}")
            if not state.get("location_id"):
                errors.append(f"agent {agent.get('id')} requires initial_state.location_id")
            elif str(state["location_id"]) not in self.location_ids:
                errors.append(f"agent {agent.get('id')} references unknown location {state['location_id']}")
            if not state.get("status"):
                errors.append(f"agent {agent.get('id')} requires initial_state.status")
            needs = agent.get("needs") if isinstance(agent.get("needs"), dict) else {}
            for need_id in needs:
                if str(need_id) not in self.need_ids:
                    errors.append(f"agent {agent.get('id')} references undeclared need {need_id}")
            controller = str(agent.get("controller") or "llm").casefold()
            if controller not in {"llm", "bt", "bt_only", "behavior_tree"}:
                errors.append(f"agent {agent.get('id')} controller must be llm or bt")
            if agent.get("controller_fallback") is not None and (agent.get("controller_fallback") != "bt" or controller != "llm"):
                errors.append(f"agent {agent.get('id')} controller_fallback must be bt on an llm controller")
            for priority_id in agent.get("bt_priority", []) or []:
                if str(priority_id) not in self.behavior_ids:
                    errors.append(f"agent {agent.get('id')} bt_priority references unknown behavior {priority_id}")
            for behavior_id in agent.get("initial_bt_ids", []) or []:
                if str(behavior_id) not in self.behavior_ids:
                    errors.append(f"agent {agent.get('id')} references unknown behavior {behavior_id}")
            for key in state:
                if key in STRUCTURAL_AGENT_STATE:
                    continue
                if self.attribute_ids and key not in self.attribute_ids:
                    errors.append(f"agent {agent.get('id')} has undeclared state attribute {key}")

    # ------------------------------------------------------------------ recipes

    def check_recipes(self) -> None:
        errors = self.errors
        for recipe in self.collections["recipes"]:
            if not isinstance(recipe.get("inputs"), list):
                errors.append(f"recipe {recipe.get('id')} inputs must be a list")
            if not isinstance(recipe.get("outputs"), list) or not recipe.get("outputs"):
                errors.append(f"recipe {recipe.get('id')} requires at least one output")
            if not isinstance(recipe.get("duration_minutes"), int) or int(recipe.get("duration_minutes", 0)) < 1:
                errors.append(f"recipe {recipe.get('id')} requires positive integer duration_minutes")
            for item in list(recipe.get("inputs") or []) + list(recipe.get("outputs") or []):
                if not isinstance(item, dict) or not str(item.get("item") or "").strip():
                    errors.append(f"recipe {recipe.get('id')} contains an item without a name")
                    continue
                if not isinstance(item.get("quantity"), (int, float)) or float(item.get("quantity", 0)) <= 0:
                    errors.append(f"recipe {recipe.get('id')} item {item.get('item')} requires positive quantity")
                if item.get("scope") is not None and str(item.get("scope")) not in {"agent", "world", "auto"}:
                    errors.append(f"recipe {recipe.get('id')} item {item.get('item')} has unsupported scope {item.get('scope')}")

    # -------------------------------------------------------------------- rules

    def check_context_rules(self) -> None:
        errors = self.errors
        rules = self.rules
        context_values = rules.get("context_values", {}) or {}
        item_groups = rules.get("item_groups", {}) or {}
        context_resolvers = rules.get("context_resolvers", {}) or {}
        if not isinstance(context_values, dict):
            errors.append("rules.context_values must be an object")
            context_values = {}
        if not isinstance(item_groups, dict):
            errors.append("rules.item_groups must be an object of string lists")
            item_groups = {}
        else:
            for group, items in item_groups.items():
                if not isinstance(items, list) or not items or any(not isinstance(item, str) or not item for item in items):
                    errors.append(f"rules.item_groups.{group} must be a non-empty list of item names")
        if not isinstance(context_resolvers, dict):
            errors.append("rules.context_resolvers must be an object")
            context_resolvers = {}
        else:
            for name, resolver in context_resolvers.items():
                if not isinstance(resolver, dict) or str(resolver.get("type") or "") != "first_inventory_item":
                    errors.append(f"rules.context_resolvers.{name} has an unsupported resolver")
                    continue
                group = str(resolver.get("group") or "")
                if group not in item_groups:
                    errors.append(f"rules.context_resolvers.{name} references unknown item group {group or 'missing'}")
        available_context_values = {str(key) for key in context_values} | {str(key) for key in context_resolvers}
        for action in self.collections["actions"]:
            for parameter, default in (action.get("parameter_defaults", {}) or {}).items():
                if not isinstance(default, dict):
                    continue
                if str(default.get("resolver") or "") != "context_value":
                    errors.append(f"action {action.get('id')} parameter default {parameter} has an unsupported resolver")
                    continue
                key = str(default.get("key") or parameter)
                if key not in available_context_values:
                    errors.append(f"action {action.get('id')} parameter default {parameter} references unknown context value {key}")

    def check_vital_and_needs(self) -> None:
        errors = self.errors
        rules = self.rules
        vital = str(rules.get("vital_attribute") or "")
        capacity = str(rules.get("capacity_attribute") or "")
        if vital not in self.state_ids:
            errors.append("rules.vital_attribute must name a declared state attribute")
        if capacity and capacity not in self.state_ids:
            errors.append("rules.capacity_attribute must name a declared state attribute")
        errors.extend(validate_incapacitation_rule(rules))
        raw_need_rules = rules.get("needs", [])
        if not isinstance(raw_need_rules, list):
            errors.append("rules.needs must be a list of objects with id, target, minimum, gamma, critical_threshold and critical_gain")
            raw_need_rules = []
        need_rules = {str(item.get("id")): item for item in raw_need_rules if isinstance(item, dict) and item.get("id")}
        for need_id, entry in need_rules.items():
            missing = [field for field in ("target", "minimum", "gamma", "critical_threshold", "critical_gain") if field not in entry]
            if missing:
                errors.append(f"rules.needs entry {need_id} is missing {', '.join(missing)}")

    def check_minute_effects(self) -> None:
        errors = self.errors
        for minute_effect in self.rules.get("minute_effects", []) or []:
            if not isinstance(minute_effect, dict):
                errors.append("rules.minute_effects entries must be objects")
                continue
            missing = [field for field in ("id", "scope", "every_minutes") if field not in minute_effect]
            if missing:
                errors.append(f"minute effect is missing {', '.join(missing)}")
                continue
            if minute_effect.get("scope") not in {"agent", "world", "relationship"}:
                errors.append(f"minute effect {minute_effect.get('id')} has unsupported scope")
            if not isinstance(minute_effect.get("every_minutes"), int) or int(minute_effect["every_minutes"]) < 1:
                errors.append(f"minute effect {minute_effect.get('id')} needs positive integer every_minutes")
            self.check_expression(minute_effect.get("condition"), f"minute effect {minute_effect.get('id')} condition")
            scope = minute_effect.get("scope")
            owner = f"minute effect {minute_effect.get('id')}"
            if scope == "relationship":
                effect = minute_effect.get("effect") or {}
                if not isinstance(effect, dict) or not isinstance(effect.get("relation_strength", effect.get("delta")), (int, float)):
                    errors.append(f"{owner} requires a numeric relationship delta")
                continue
            effects = minute_effect.get("effects") if scope == "world" and "effects" in minute_effect else [minute_effect.get("effect")]
            if not isinstance(effects, list) or not effects:
                errors.append(f"{owner} needs at least one supported typed effect")
                continue
            self.check_effect_list(effects, owner)

    # -------------------------------------------------- relationships, history

    def check_relationships(self) -> None:
        errors = self.errors
        known_relation_entities = self.agent_ids | self.faction_ids
        for relation in self.collections["relationships"]:
            if str(relation.get("from") or "") not in known_relation_entities:
                errors.append(f"relationship references unknown source {relation.get('from')}")
            if str(relation.get("to") or "") not in known_relation_entities | {"player"}:
                errors.append(f"relationship references unknown target {relation.get('to')}")

    def check_initial_events(self) -> None:
        errors = self.errors
        for event in self.collections["initial_events"]:
            if not str(event.get("type") or "").strip():
                errors.append(f"initial event {event.get('id')} requires type")
            if not isinstance(event.get("world_minute"), int) or int(event.get("world_minute", -1)) < 0:
                errors.append(f"initial event {event.get('id')} requires non-negative integer world_minute")
            if not isinstance(event.get("payload"), dict):
                errors.append(f"initial event {event.get('id')} requires payload object")
