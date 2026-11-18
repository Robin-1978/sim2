"""Entity proposal validation: every reason a ``propose_entity`` proposal cannot be created.

``EntityProposalValidationMixin._collect_entity_errors`` runs the steps in a
fixed order and appends each reason to ``submission.errors``; the proposal is
rejected once, with all reasons, when the list is not empty.  The steps only
read Platform state; ``_normalize_location_routes`` replaces the ``state`` of a
location proposal with canonical route ids.  ``_normalize_god_agent_state``
builds and validates the state of a new character from the configured world
defaults; the commit module calls it after the steps above passed.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..world.schema import AttributeSchema


@dataclass
class EntitySubmission:
    """One entity proposal moving through validation and commit.

    ``data`` is what the stored intent shows (the proposal as received);
    ``proposal`` is the same proposal after the location routes were
    normalized.
    """

    intent_id: str
    actor_id: str
    entity_id: str
    data: dict[str, Any]
    proposal: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    has_research: bool = False
    research_assignees: list[tuple[str, str]] = field(default_factory=list)


def entity_failed_check(errors: list[str]) -> str:
    """Name the rejection after the most specific reason in ``errors``."""

    if any("visible name" in error and "duplicate" in error for error in errors):
        return "entity_name_duplicate"
    if any("research source repeats existing evidence" in error for error in errors):
        return "research_novelty_duplicate"
    return "god_resource_creation" if any("stocked resource" in error for error in errors) else "entity_schema"


def _research_discovery_errors(research_values: Any) -> list[str]:
    errors: list[str] = []
    stages = [research_values] if isinstance(research_values, dict) else research_values if isinstance(research_values, list) else []
    if not stages:
        errors.append("research_discoveries must be a non-empty list or object")
    for index, stage in enumerate(stages):
        if isinstance(stage, str):
            continue
        if not isinstance(stage, dict) or not str(stage.get("id") or stage.get("fact_key") or stage.get("fact") or "").strip():
            errors.append(f"research_discoveries[{index}] must declare an id or fact")
    return errors


class EntityProposalValidationMixin:
    """Validation steps for entity proposals."""

    def _normalize_god_agent_state(self, entity_id: str, proposal: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Initialize a new runtime character from configured world defaults."""

        errors: list[str] = []
        state = copy.deepcopy(proposal.get("state") if isinstance(proposal.get("state"), dict) else {})
        name = str(proposal.get("name") or "").strip()
        role = str(state.get("role") or "").strip()
        goal = str(state.get("ultimate_goal") or "").strip()
        if not role:
            errors.append("agent.state.role is required")
        if not goal:
            errors.append("agent.state.ultimate_goal is required")

        initial_state = dict(state.get("initial_state") or {})
        raw_location = proposal.get("location_id") or initial_state.get("location_id")
        location_id = self.targets.canonical_id(str(raw_location or ""))
        if not location_id:
            errors.append("agent.state.initial_state.location_id must name an existing location")
        else:
            try:
                location = self.store.repositories.entities.get_entity(location_id)
                if str(location.get("entity_type")) != "location":
                    errors.append(f"{raw_location} is not a location")
            except KeyError:
                errors.append(f"agent location does not exist: {raw_location}")
        if location_id:
            initial_state["location_id"] = location_id

        rules = self.store.repositories.metadata.get("rules", {}) or {}
        schema = AttributeSchema(self.store.repositories.metadata.get("attributes", []) or [], rules)
        for attribute_id, value in schema.state_defaults().items():
            initial_state.setdefault(attribute_id, value)
        initial_state.setdefault("status", "idle")
        initial_state.setdefault("current_goal_id", "ultimate_goal")
        initial_state.setdefault("injuries", [])
        initial_state.setdefault("conditions", [])
        initial_state.setdefault("active_bt_instance", None)
        initial_state.setdefault("active_action", None)
        initial_state.setdefault("action_progress", 0.0)
        initial_state.setdefault("remaining_minutes", 0)
        initial_state.setdefault("last_platform_result", None)
        for attribute_id in schema.state_defaults():
            if attribute_id in initial_state:
                initial_state[attribute_id] = schema.clamp(attribute_id, initial_state[attribute_id])

        needs = dict(state.get("needs") or {})
        for need_id, value in schema.need_defaults().items():
            needs.setdefault(need_id, value)
        for item in rules.get("needs", []) or []:
            if isinstance(item, dict) and item.get("id"):
                needs.setdefault(str(item["id"]), float(item.get("target", 100)))
        for need_id, value in list(needs.items()):
            needs[need_id] = schema.clamp(str(need_id), value)

        capability_defs = {str(item["id"]): item for item in self.store.repositories.entities.get_definitions("capability_def")}
        capabilities = {
            capability_id: float(definition.get("default", 0) or 0)
            if isinstance(definition.get("default", 0), (int, float)) and not isinstance(definition.get("default", 0), bool)
            else 0.0
            for capability_id, definition in capability_defs.items()
        }
        provided_capabilities = state.get("capabilities") if isinstance(state.get("capabilities"), dict) else {}
        unknown_capabilities = sorted(set(provided_capabilities) - set(capability_defs)) if capability_defs else []
        if unknown_capabilities:
            errors.append("agent.state.capabilities references unknown capabilities: " + ", ".join(unknown_capabilities))
        for capability_id in set(provided_capabilities) & set(capability_defs):
            value = provided_capabilities[capability_id]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1:
                errors.append(f"agent capability {capability_id} must be between 0 and 1")
            else:
                capabilities[capability_id] = float(value)

        initial_bt_ids: list[str] = []
        for value in state.get("initial_bt_ids") or []:
            behavior_id, matches = self._context.services["definitions"].resolve_registered_definition("bt_def", value)
            if not behavior_id:
                errors.append(f"unknown or ambiguous initial behavior: {value}; matches={matches}")
            elif behavior_id not in initial_bt_ids:
                initial_bt_ids.append(behavior_id)
        if not initial_bt_ids:
            errors.append("agent.state.initial_bt_ids must include at least one existing behavior")

        faction_ids: list[str] = []
        for value in state.get("faction_ids") or []:
            faction_id = self.targets.canonical_id(str(value))
            if not faction_id:
                errors.append(f"unknown faction: {value}")
                continue
            try:
                faction = self.store.repositories.entities.get_entity(faction_id)
                if str(faction.get("entity_type")) != "faction":
                    errors.append(f"{value} is not a faction")
                else:
                    faction_ids.append(faction_id)
            except KeyError:
                errors.append(f"unknown faction: {value}")

        normalized = {
            **state,
            "id": entity_id,
            "name": name,
            "role": role,
            "ultimate_goal": goal,
            "traits": dict(state.get("traits") or {}),
            "beliefs": list(state.get("beliefs") or []),
            "needs": needs,
            "capabilities": capabilities,
            "initial_bt_ids": initial_bt_ids,
            "unlocked_bt_ids": [],
            "inventory": dict(state.get("inventory") or {}),
            "permissions": dict(state.get("permissions") or {}),
            "faction_ids": faction_ids,
            "combat_stats": dict(state.get("combat_stats") or {}),
            "initial_state": initial_state,
            "story_goals": list(state.get("story_goals") or []),
            "knowledge_seed": list(state.get("knowledge_seed") or []),
        }
        return normalized, errors

    @staticmethod
    def _proposed_state(proposal: dict[str, Any]) -> dict[str, Any]:
        return proposal.get("state") if isinstance(proposal.get("state"), dict) else {}

    def _collect_entity_errors(self, submission: EntitySubmission) -> None:
        """Append every validation error, in the order the steps run."""

        for step in (
            self._check_entity_label,
            self._normalize_location_routes,
            self._check_god_limits,
            self._check_location_reference,
            self._check_research_state,
            self._check_research_assignees,
            self._check_entity_id_free,
        ):
            step(submission)

    def _check_entity_label(self, submission: EntitySubmission) -> None:
        """The type and a short visible name are required; the name must not duplicate another entity's."""

        proposal = submission.proposal
        errors = submission.errors
        if not proposal.get("entity_type"):
            errors.append("entity_type is required")
        if not proposal.get("name"):
            errors.append("name is required")
        proposed_name = str(proposal.get("name") or "").strip()
        if proposed_name:
            if len(proposed_name) > 120 or "\n" in proposed_name or "\r" in proposed_name or "<" in proposed_name or ">" in proposed_name:
                errors.append("name must be a short visible label without markup or embedded parameter text")
            name_key = "".join(proposed_name.casefold().split())
            duplicate = self.store.connection.execute(
                "SELECT id,entity_type,name FROM entity WHERE name IS NOT NULL"
            ).fetchall()
            existing = next(
                (row for row in duplicate if "".join(str(row["name"]).casefold().split()) == name_key),
                None,
            )
            if existing is not None:
                errors.append(
                    f"an entity with visible name {existing['name']} already exists "
                    f"({existing['entity_type']}:{existing['id']}); use it instead of creating a duplicate"
                )

    def _normalize_location_routes(self, submission: EntitySubmission) -> None:
        """Store the route neighbors and distances of a location under their canonical ids."""

        proposal = submission.proposal
        if str(proposal.get("entity_type") or "") != "location" or not isinstance(proposal.get("state"), dict):
            return
        state = dict(proposal["state"])
        raw_routes = state.get("routes", [])
        if not isinstance(raw_routes, list):
            return
        neighbors: list[str] = []
        distances: dict[str, Any] = {}
        for route in raw_routes:
            # Models often send ``[{to: name, distance: n}]`` because a route
            # has two facts.  Normalize that clear shorthand to the canonical
            # ``routes`` list plus ``route_distances`` map before validation.
            if isinstance(route, dict):
                neighbor = route.get("to") or route.get("location") or route.get("name")
                distance = route.get("distance", route.get("minutes"))
            else:
                neighbor, distance = route, None
            name = str(neighbor or "").strip()
            if not name:
                continue
            neighbors.append(name)
            if isinstance(distance, (int, float)) and not isinstance(distance, bool) and float(distance) > 0:
                distances.setdefault(name, distance)
        if isinstance(state.get("route_distances"), dict):
            distances = {**distances, **state["route_distances"]}
        canonical = lambda value: str(self.targets.canonical_id(str(value)) or value)
        state["routes"] = list(dict.fromkeys(canonical(name) for name in neighbors))
        if distances:
            state["route_distances"] = {canonical(name): distance for name, distance in distances.items()}
        submission.proposal = {**proposal, "state": state}

    def _check_god_limits(self, submission: EntitySubmission) -> None:
        """God cannot stock a resource directly, and a location it creates must join existing locations."""

        if submission.actor_id != "god":
            return
        proposal = submission.proposal
        entity_type = str(proposal.get("entity_type", ""))
        state = proposal.get("state") if isinstance(proposal.get("state"), dict) else {}
        self._check_god_resource(submission, entity_type, state)
        if entity_type == "location":
            self._check_god_location(submission, state)

    def _check_god_resource(self, submission: EntitySubmission, entity_type: str, state: dict[str, Any]) -> None:
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        resource_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
        quantity = state.get("quantity", state.get("amount"))
        if entity_type in resource_types and isinstance(quantity, (int, float)) and float(quantity) > 0:
            submission.errors.append("God cannot create a stocked resource directly; a behavior result must create or recover the supply")

    def _check_god_location(self, submission: EntitySubmission, state: dict[str, Any]) -> None:
        errors = submission.errors
        routes = state.get("routes")
        if not isinstance(routes, list) or not routes or any(not isinstance(item, str) or not item for item in routes):
            errors.append("a dynamic location requires at least one existing route neighbor")
        else:
            for neighbor_id in routes:
                try:
                    neighbor = self.store.repositories.entities.get_entity(str(neighbor_id))
                except KeyError:
                    errors.append(f"location routes reference unknown location {neighbor_id}")
                    continue
                if str(neighbor.get("entity_type") or "") != "location":
                    errors.append(f"location route {neighbor_id} is not a location")
        route_distances = state.get("route_distances", {})
        if route_distances is not None and not isinstance(route_distances, dict):
            errors.append("location route_distances must be an object")
        elif isinstance(route_distances, dict):
            route_ids = {str(item) for item in (routes or [])}
            for neighbor_id, distance in route_distances.items():
                if str(neighbor_id) not in route_ids:
                    errors.append(f"location route distance references non-route {neighbor_id}")
                if isinstance(distance, bool) or not isinstance(distance, (int, float)) or float(distance) <= 0:
                    errors.append(f"location route distance for {neighbor_id} must be positive")

    def _check_location_reference(self, submission: EntitySubmission) -> None:
        proposal = submission.proposal
        if proposal.get("location_id"):
            try:
                self.store.repositories.entities.get_entity(str(proposal["location_id"]))
            except KeyError:
                submission.errors.append("location_id does not exist")

    def _check_research_state(self, submission: EntitySubmission) -> None:
        """A research source declares what it can discover, who researches it, and a question that is new."""

        proposal = submission.proposal
        errors = submission.errors
        proposed_state = self._proposed_state(proposal)
        research_values = proposed_state.get("research_discoveries")
        if str(proposal.get("entity_type") or "") == "research_source" and research_values in (None, [], {}):
            errors.append("a research_source must declare at least one research_discoveries entry")
        if research_values is not None:
            errors.extend(_research_discovery_errors(research_values))
        if "research_required" in proposed_state and (
            not isinstance(proposed_state.get("research_required"), (int, float))
            or isinstance(proposed_state.get("research_required"), bool)
            or float(proposed_state.get("research_required", 0) or 0) < 1
        ):
            errors.append("research_required must be a positive number")
        submission.has_research = proposed_state.get("research_discoveries") is not None
        if submission.has_research and not proposed_state.get("research_assignees"):
            errors.append("a dynamic research source must declare at least one research_assignee")
        if submission.has_research:
            duplicate_research = self._context.services["definitions"].research_duplicate(proposed_state, proposal.get("description"), proposal.get("location_id") or proposed_state.get("location_id"))
            if duplicate_research:
                errors.append(
                    f"research source repeats existing evidence ({duplicate_research['name']}): {duplicate_research['reason']}; "
                    "submit a genuinely different research question or wait"
                )

    def _check_research_assignees(self, submission: EntitySubmission) -> None:
        research_assignees, assignee_errors = self._context.services["definitions"].resolve_research_assignees(self._proposed_state(submission.proposal))
        submission.research_assignees = research_assignees
        submission.errors.extend(assignee_errors)

    def _check_entity_id_free(self, submission: EntitySubmission) -> None:
        try:
            self.store.repositories.entities.get_entity(submission.entity_id)
            submission.errors.append("entity_id already exists")
        except KeyError:
            pass
