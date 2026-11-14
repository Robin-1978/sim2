from __future__ import annotations

import copy
from typing import Any
from uuid import uuid4

from ..evolution.memory import remember_role
from ..world.schema import AttributeSchema
from .proposal_errors import _UnreachableProposal


class EntityProposalRuntimeMixin:
    """Validate and commit declarative entity proposals."""

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
                location = self.store.get_entity(location_id)
                if str(location.get("entity_type")) != "location":
                    errors.append(f"{raw_location} is not a location")
            except KeyError:
                errors.append(f"agent location does not exist: {raw_location}")
        if location_id:
            initial_state["location_id"] = location_id

        rules = self.store.get_meta("rules", {}) or {}
        schema = AttributeSchema(self.store.get_meta("attributes", []) or [], rules)
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

        capability_defs = {str(item["id"]): item for item in self.store.get_definitions("capability_def")}
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
            behavior_id, matches = self._context.services["definitions"]._resolve_registered_definition("bt_def", value)
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
                faction = self.store.get_entity(faction_id)
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

    def submit_entity_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        entity_id = str(proposal.get("entity_id") or f"entity_{uuid4().hex[:10]}")
        data = {**proposal, "entity_id": entity_id, "actor_id": actor_id}
        errors = []
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
        if actor_id == "god":
            rules = self.store.get_meta("rules", {}) or {}
            resource_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
            entity_type = str(proposal.get("entity_type", ""))
            state = proposal.get("state") if isinstance(proposal.get("state"), dict) else {}
            quantity = state.get("quantity", state.get("amount"))
            if entity_type in resource_types and isinstance(quantity, (int, float)) and float(quantity) > 0:
                errors.append("God cannot create a stocked resource directly; a behavior result must create or recover the supply")
        if proposal.get("location_id"):
            try:
                self.store.get_entity(str(proposal["location_id"]))
            except KeyError:
                errors.append("location_id does not exist")
        proposed_state = proposal.get("state") if isinstance(proposal.get("state"), dict) else {}
        research_values = proposed_state.get("research_discoveries")
        if research_values is None:
            research_values = proposed_state.get("research_discovery")
        if str(proposal.get("entity_type") or "") == "research_source" and research_values in (None, [], {}):
            errors.append("a research_source must declare at least one research_discovery")
        if research_values is not None:
            stages = [research_values] if isinstance(research_values, dict) else research_values if isinstance(research_values, list) else []
            if not stages:
                errors.append("research_discoveries must be a non-empty list or object")
            for index, stage in enumerate(stages):
                if isinstance(stage, str):
                    continue
                if not isinstance(stage, dict) or not str(stage.get("id") or stage.get("fact_key") or stage.get("fact") or "").strip():
                    errors.append(f"research_discoveries[{index}] must declare an id or fact")
        if "research_required" in proposed_state and (
            not isinstance(proposed_state.get("research_required"), (int, float))
            or isinstance(proposed_state.get("research_required"), bool)
            or float(proposed_state.get("research_required", 0) or 0) < 1
        ):
            errors.append("research_required must be a positive number")
        research_values_present = proposed_state.get("research_discoveries") is not None or proposed_state.get("research_discovery") is not None
        if research_values_present and not proposed_state.get("research_assignees"):
            errors.append("a dynamic research source must declare at least one research_assignee")
        if research_values_present:
            duplicate_research = self._context.services["definitions"]._research_duplicate(proposed_state, proposal.get("description"), proposal.get("location_id") or proposed_state.get("location_id"))
            if duplicate_research:
                errors.append(
                    f"research source repeats existing evidence ({duplicate_research['name']}): {duplicate_research['reason']}; "
                    "submit a genuinely different research question or wait"
                )
        research_assignees, assignee_errors = self._context.services["definitions"]._resolve_research_assignees(proposed_state)
        errors.extend(assignee_errors)
        try:
            self.store.get_entity(entity_id)
            errors.append("entity_id already exists")
        except KeyError:
            pass
        if errors:
            if any("visible name" in error and "duplicate" in error for error in errors):
                failed_check = "entity_name_duplicate"
            elif any("research source repeats existing evidence" in error for error in errors):
                failed_check = "research_novelty_duplicate"
            else:
                failed_check = "god_resource_creation" if any("stocked resource" in error for error in errors) else "entity_schema"
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_entity", data, {"status": "rejected", "message": "Entity proposal failed validation.", "failed_check": failed_check, "errors": errors, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        # Entity proposals use one compact ``state`` object at the Agent
        # boundary.  Location topology is the one structural exception: the
        # navigation engine reads routes/tags/public/safe from the location
        # definition itself, while ``initial_state`` is reserved for mutable
        # state such as a location condition.  Promote only the configured
        # structural fields so a God-created settlement is immediately
        # navigable without adding a world-specific branch.
        proposed_state = dict(proposal.get("state") or {})
        entity_type = str(proposal.get("entity_type") or "")
        if proposed_state.get("research_discoveries") is not None or proposed_state.get("research_discovery") is not None:
            focus, _ = self._context.services["definitions"]._research_focus(proposed_state, proposal.get("description"))
            if focus:
                proposed_state.setdefault("research_focus", focus)
        entity_data = {"description": proposal.get("description", ""), "initial_state": proposed_state}
        agent_state: dict[str, Any] | None = None
        if entity_type == "agent":
            agent_state, agent_errors = self._normalize_god_agent_state(entity_id, proposal)
            if agent_errors:
                return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_entity", data, {"status": "rejected", "message": "Agent proposal failed validation.", "failed_check": "agent_schema", "errors": agent_errors, "retryable": True})
        elif entity_type == "location":
            for field in ("routes", "tags", "public", "safe", "discoveries", "map_position", "region_id"):
                if field in proposed_state:
                    entity_data[field] = proposed_state.pop(field)
            entity_data["initial_state"] = proposed_state
        if proposal.get("location_id"):
            entity_data["initial_state"]["location_id"] = proposal["location_id"]
        try:
            with self.store.transaction():
                self.store.create_intent(intent_id, actor_id, "propose_entity", data, world_minute)
                if agent_state is not None:
                    self.store.put_agent(entity_id, agent_state, str(proposal["name"]))
                    for knowledge in agent_state.get("knowledge_seed", []):
                        if not isinstance(knowledge, dict):
                            continue
                        fact_key = str(knowledge.get("id") or knowledge.get("fact_key") or "").strip()
                        if fact_key:
                            self.store.put_knowledge_fact(entity_id, fact_key, {
                                "fact": str(knowledge.get("fact") or knowledge.get("summary") or fact_key),
                                "source_type": str(knowledge.get("source_type") or "god_created"),
                                "confidence": float(knowledge.get("confidence", 1.0)),
                                "claim_status": str(knowledge.get("claim_status") or "observed"),
                            })
                else:
                    self.store.put_entity(entity_id, entity_type, str(proposal["name"]), entity_data)
                    if research_values_present:
                        focus, _ = self._context.services["definitions"]._research_focus(proposed_state, proposal.get("description"))
                        gap_key = str(proposed_state.get("research_novelty_key") or proposed_state.get("research_gap_key") or f"source:{entity_id}")
                        question = str(proposed_state.get("research_question") or proposed_state.get("open_question") or focus or proposal.get("description") or proposal.get("name"))
                        self.store.upsert_knowledge_gap(
                            gap_key,
                            "active",
                            gap_key,
                            question,
                            source_id=entity_id,
                            data={"source_name": str(proposal.get("name") or entity_id), "actor_id": str(actor_id)},
                            world_minute=world_minute,
                        )
                    if research_assignees:
                        # Judge reachability with the assignees' own views of
                        # the world that already contains the new source.
                        problems = self._context.services["definitions"]._unreachable_research_assignees(proposed_name, research_assignees)
                        if problems:
                            raise _UnreachableProposal(problems)
                event_id = self.uow.events.append("entity.created", actor_id, world_minute, {"intent_id": intent_id, "entity_id": entity_id, "entity_type": entity_type, "summary": proposal.get("description", "")})
                self.wakes.configured(event_id, "entity.created", actor_id, world_minute)
                if actor_id == "god":
                    remember_role(self.store, "god", "world_entity", {
                        "summary": f"Created {entity_type}: {proposal.get('name', entity_id)}.",
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "status": "accepted",
                    }, memory_key=f"god:entity:{intent_id}", source_event_id=event_id)
                result = {"status": "accepted", "intent_id": intent_id, "entity_id": entity_id, "event_id": event_id, "applied": True}
                self.store.finish_intent(intent_id, "accepted", result)
        except _UnreachableProposal as exc:
            # The transaction has rolled back: nothing of the proposal exists.
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_entity", data, {
                "status": "rejected",
                "message": "The proposed research source would be unreachable for its assignees; nothing was created.",
                "failed_check": "entity_unreachable",
                "errors": exc.errors,
                "retryable": True,
            })
        return result
