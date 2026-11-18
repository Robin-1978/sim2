"""Entity proposal commit: plan what a validated proposal writes, then write it in one transaction.

``submit_entity_proposal`` validates through ``EntityProposalValidationMixin``,
plans the records (``EntityPlan``) and commits them.  A research source whose
assignees cannot reach it rolls the whole transaction back and is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..evolution.memory import remember_role
from .entity_proposal_validation import EntitySubmission, entity_failed_check
from .proposal_errors import _UnreachableProposal

LOCATION_STRUCTURE_FIELDS = ("routes", "route_distances", "tags", "public", "safe", "discoveries", "map_position", "region_id")


@dataclass
class EntityPlan:
    """The records a validated entity proposal writes."""

    entity_type: str
    proposed_state: dict[str, Any]
    entity_data: dict[str, Any]
    agent_state: dict[str, Any] | None = None
    agent_errors: list[str] = field(default_factory=list)
    location_routes: list[str] = field(default_factory=list)
    location_distances: dict[str, float] = field(default_factory=dict)


class EntityProposalRuntimeMixin:
    """Commit declarative entity proposals."""

    def submit_entity_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        submission = self._open_entity_submission(actor_id, proposal)
        self._collect_entity_errors(submission)
        if submission.errors:
            return self._reject_entity(submission, "Entity proposal failed validation.", entity_failed_check(submission.errors), submission.errors)
        world_minute = int(self.store.repositories.metadata.get("world_minute", 0))
        plan = self._plan_entity(submission)
        if plan.agent_errors:
            return self._reject_entity(submission, "Agent proposal failed validation.", "agent_schema", plan.agent_errors)
        return self._commit_entity(submission, plan, world_minute)

    def _open_entity_submission(self, actor_id: str, proposal: dict[str, Any]) -> EntitySubmission:
        intent_id = str(uuid4())
        entity_id = str(proposal.get("entity_id") or f"entity_{uuid4().hex[:10]}")
        data = {**proposal, "entity_id": entity_id, "actor_id": actor_id}
        return EntitySubmission(intent_id, actor_id, entity_id, data, proposal)

    def _reject_entity(self, submission: EntitySubmission, message: str, failed_check: str, errors: list[str]) -> dict[str, Any]:
        return self._context.services["intent"].reject_intent(
            submission.intent_id,
            submission.actor_id,
            "propose_entity",
            submission.data,
            {"status": "rejected", "message": message, "failed_check": failed_check, "errors": errors, "retryable": True},
        )

    def _plan_entity(self, submission: EntitySubmission) -> EntityPlan:
        """Turn the validated proposal into the records it writes.

        Entity proposals use one compact ``state`` object at the Agent
        boundary.  Location topology is the one structural exception: the
        navigation engine reads routes/tags/public/safe from the location
        definition itself, while ``initial_state`` is reserved for mutable
        state such as a location condition.  Promote only the configured
        structural fields so a God-created settlement is immediately
        navigable without adding a world-specific branch.
        """

        proposal = submission.proposal
        proposed_state = dict(proposal.get("state") or {})
        entity_type = str(proposal.get("entity_type") or "")
        if proposed_state.get("research_discoveries") is not None:
            focus, _ = self._context.services["definitions"].research_focus(proposed_state, proposal.get("description"))
            if focus:
                proposed_state.setdefault("research_focus", focus)
        plan = EntityPlan(entity_type, proposed_state, {"description": proposal.get("description", ""), "initial_state": proposed_state})
        if entity_type == "agent":
            plan.agent_state, plan.agent_errors = self._normalize_god_agent_state(submission.entity_id, proposal)
            if plan.agent_errors:
                return plan
        elif entity_type == "location":
            self._promote_location_structure(plan)
        if proposal.get("location_id"):
            plan.entity_data["initial_state"]["location_id"] = proposal["location_id"]
        return plan

    @staticmethod
    def _promote_location_structure(plan: EntityPlan) -> None:
        proposed_state = plan.proposed_state
        plan.location_routes = [str(item) for item in (proposed_state.get("routes") or [])]
        plan.location_distances = {
            str(key): float(value)
            for key, value in (proposed_state.get("route_distances") or {}).items()
        }
        for key in LOCATION_STRUCTURE_FIELDS:
            if key in proposed_state:
                plan.entity_data[key] = proposed_state.pop(key)
        plan.entity_data["initial_state"] = proposed_state

    def _commit_entity(self, submission: EntitySubmission, plan: EntityPlan, world_minute: int) -> dict[str, Any]:
        intent_id = submission.intent_id
        actor_id = submission.actor_id
        entity_id = submission.entity_id
        proposal = submission.proposal
        try:
            with self.store.transaction():
                self.store.repositories.evolution.create_intent(intent_id, actor_id, "propose_entity", submission.data, world_minute)
                if plan.agent_state is not None:
                    self._write_agent(entity_id, proposal, plan.agent_state)
                else:
                    self._write_structure(submission, plan, world_minute)
                event_id = self._context.services["events"].publish("entity.created", actor_id, world_minute, {"intent_id": intent_id, "entity_id": entity_id, "entity_type": plan.entity_type, "name": proposal.get("name"), "summary": proposal.get("description", "")}, wake="configured")
                if plan.entity_type == "location":
                    self._context.services["events"].publish(
                        "world.location.created", actor_id, world_minute,
                        {"entity_id": entity_id, "name": proposal.get("name"), "location_id": proposal.get("location_id"), "summary": f"New location {proposal.get('name')}: {proposal.get('description', '')}"},
                        wake="configured",
                    )
                elif plan.entity_type in {str(item) for item in ((self.store.repositories.metadata.get("rules", {}) or {}).get("resource_entity_types", ["resource", "material"]) or [])}:
                    self._context.services["events"].publish(
                        "world.resource.created", actor_id, world_minute,
                        {"entity_id": entity_id, "name": proposal.get("name"), "location_id": proposal.get("location_id"), "summary": f"New resource {proposal.get('name')} at {proposal.get('location_id')}: {proposal.get('description', '')}"},
                        wake="configured",
                    )
                if actor_id == "god":
                    remember_role(self.store, "god", "world_entity", {
                        "summary": f"Created {plan.entity_type}: {proposal.get('name', entity_id)}.",
                        "entity_type": plan.entity_type,
                        "entity_id": entity_id,
                        "status": "accepted",
                    }, memory_key=f"god:entity:{intent_id}", source_event_id=event_id)
                result = {"status": "accepted", "intent_id": intent_id, "entity_id": entity_id, "event_id": event_id, "applied": True}
                self.store.repositories.evolution.finish_intent(intent_id, "accepted", result)
        except _UnreachableProposal as exc:
            # The transaction has rolled back: nothing of the proposal exists.
            return self._reject_entity(submission, "The proposed research source would be unreachable for its assignees; nothing was created.", "entity_unreachable", exc.errors)
        return result

    def _write_agent(self, entity_id: str, proposal: dict[str, Any], agent_state: dict[str, Any]) -> None:
        self.store.repositories.entities.put_agent(entity_id, agent_state, str(proposal["name"]))
        for knowledge in agent_state.get("knowledge_seed", []):
            if not isinstance(knowledge, dict):
                continue
            fact_key = str(knowledge.get("id") or knowledge.get("fact_key") or "").strip()
            if fact_key:
                self.store.repositories.memory.put_knowledge_fact(entity_id, fact_key, {
                    "fact": str(knowledge.get("fact") or knowledge.get("summary") or fact_key),
                    "source_type": str(knowledge.get("source_type") or "god_created"),
                    "confidence": float(knowledge.get("confidence", 1.0)),
                    "claim_status": str(knowledge.get("claim_status") or "observed"),
                })

    def _write_structure(self, submission: EntitySubmission, plan: EntityPlan, world_minute: int) -> None:
        """Write a non-character entity, join a new location to its neighbors and register research."""

        proposal = submission.proposal
        self.store.repositories.entities.put_entity(submission.entity_id, plan.entity_type, str(proposal["name"]), plan.entity_data)
        if plan.entity_type == "location":
            self._join_neighbors(submission.entity_id, plan)
        if submission.has_research:
            self._record_research_gap(submission, plan, world_minute)
        if submission.research_assignees:
            # Judge reachability with the assignees' own views of
            # the world that already contains the new source.
            proposed_name = str(proposal.get("name") or "").strip()
            problems = self._context.services["definitions"].unreachable_research_assignees(proposed_name, submission.research_assignees)
            if problems:
                raise _UnreachableProposal(problems)

    def _join_neighbors(self, entity_id: str, plan: EntityPlan) -> None:
        for neighbor_id in plan.location_routes:
            neighbor = self.store.repositories.entities.get_entity(neighbor_id)
            neighbor_routes = list(neighbor.get("routes") or [])
            if entity_id not in neighbor_routes:
                neighbor_routes.append(entity_id)
            neighbor_distances = dict(neighbor.get("route_distances") or {})
            neighbor_distances[entity_id] = plan.location_distances.get(neighbor_id, 1.0)
            neighbor["routes"] = neighbor_routes
            neighbor["route_distances"] = neighbor_distances
            self.store.repositories.entities.put_entity(
                neighbor_id,
                "location",
                neighbor.get("name", neighbor_id),
                {key: value for key, value in neighbor.items() if key not in {"id", "entity_type", "name"}},
            )

    def _record_research_gap(self, submission: EntitySubmission, plan: EntityPlan, world_minute: int) -> None:
        proposal = submission.proposal
        proposed_state = plan.proposed_state
        entity_id = submission.entity_id
        focus, _ = self._context.services["definitions"].research_focus(proposed_state, proposal.get("description"))
        gap_key = str(proposed_state.get("research_novelty_key") or f"source:{entity_id}")
        question = str(proposed_state.get("research_question") or focus or proposal.get("description") or proposal.get("name"))
        self.store.repositories.memory.upsert_knowledge_gap(
            gap_key,
            "active",
            gap_key,
            question,
            source_id=entity_id,
            data={"source_name": str(proposal.get("name") or entity_id), "actor_id": str(submission.actor_id)},
            world_minute=world_minute,
        )
