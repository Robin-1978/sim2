from __future__ import annotations

import copy
import json
import re
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from ..intents.models import BehaviorIntent
from .tool_registry import ROLE_TOOLS, TOOL_DEFINITIONS, validate_tool_arguments

if TYPE_CHECKING:  # pragma: no cover
    from ..platform.runtime import Platform


class AgentToolExecutor:
    """Execute role-allowed tools and route every mutation to Platform."""

    def __init__(
        self,
        platform: "Platform",
        *,
        role: str,
        actor_id: str,
        source_event_ids: list[int] | None = None,
        observed_world_revision: int | None = None,
        observed_agent_revision: int | None = None,
        evolution_contract: dict[str, Any] | None = None,
    ):
        self.platform = platform
        self.role = role
        self.actor_id = actor_id
        self.source_event_ids = [int(value) for value in (source_event_ids or []) if int(value) > 0]
        self.observed_world_revision = observed_world_revision
        self.observed_agent_revision = observed_agent_revision
        self.evolution_contract = dict(evolution_contract or {})
        self.allowed = set(ROLE_TOOLS.get(role, []))

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in self.allowed:
            return {"status": "rejected", "tool": name, "message": "This tool is not allowed for this role."}
        if name not in TOOL_DEFINITIONS:
            return {"status": "rejected", "tool": name, "message": "Unknown tool."}
        args = dict(arguments or {})
        # Compatibility for older callers.  These aliases are intentionally
        # absent from the model-facing JSON Schemas.
        legacy_aliases = {
            "inspect_entity": {"entity_id": "entity"},
            "read_public_record": {"entity_id": "record"},
            "send_message": {"target_id": "recipient"},
            "create_trade_offer": {"recipient_id": "recipient"},
            "accept_trade_offer": {"offer_id": "offer"},
            "counter_trade_offer": {"offer_id": "offer"},
            "reject_trade_offer": {"offer_id": "offer"},
            "record_experience": {"event_ref": "event"},
            "update_belief": {"belief_id": "belief"},
            "update_goal": {"goal_id": "goal"},
            "propose_behavior": {"behavior_id": "name", "action_ids": "actions"},
            "propose_entity": {"entity_id": "name", "location_id": "location"},
        }
        for old, current in legacy_aliases.get(name, {}).items():
            if old in args:
                if current not in args:
                    args[current] = args[old]
                args.pop(old)
        validation_errors = validate_tool_arguments(name, args)
        if validation_errors:
            return {
                "status": "rejected",
                "tool": name,
                "failed_check": "tool_arguments",
                "retryable": True,
                "message": "; ".join(validation_errors),
            }
        try:
            result = getattr(self, f"_{name}")(**args)
        except (KeyError, TypeError, ValueError) as exc:
            return {"status": "rejected", "tool": name, "message": str(exc)}
        if not isinstance(result, dict):
            return {"status": "ok", "tool": name, "result": result}
        result.setdefault("tool", name)
        public = self._publicize(result)
        # The model must not see implementation identifiers, but the
        # scheduler still needs a stable signal that a zero-minute interaction
        # committed successfully so it can apply the configured cooldown.
        if public.get("status") == "accepted" and name == "send_message":
            public["interaction_committed"] = True
        if public.get("status") == "accepted" and name in {
            "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer",
        }:
            public["trade_committed"] = True
        return public

    @classmethod
    def _publicize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            internal_fields = {
                "active_bt_instance", "active_behavior_id", "active_action_instance",
                "active_action_parameters", "active_action_reservation", "active_parameters",
                "active_parallel_actions", "execution_id", "action_instance_id",
            }
            return {
                key: cls._publicize(item)
                for key, item in value.items()
                if key not in {"id", "ref", "behavior_ref", "intent_id", "event_id"} and key not in internal_fields
                and key != "tags"
                and not key.endswith("_id")
            }
        if isinstance(value, list):
            return [cls._publicize(item) for item in value]
        return value

    def _agent(self) -> dict[str, Any]:
        return self.platform.store.repositories.entities.get_agent(self.actor_id)

    def _contract_visible_name(self, value: Any) -> str:
        canonical = self.platform.targets.canonical_id(str(value))
        if canonical:
            try:
                return str(self.platform.store.repositories.entities.get_agent(str(canonical)).get("name") or canonical)
            except KeyError:
                pass
        return str(value)

    def _observe_nearby(self, question: str = "") -> dict[str, Any]:
        return self.platform.perception.observe_nearby(self.actor_id, question)

    def _resolve_entity(self, value: str) -> tuple[str | None, dict[str, Any] | None]:
        resolved = self.platform.targets.canonical_id(value)
        if resolved:
            return resolved, None
        return None, {"status": "rejected", "failed_check": "entity_name", "retryable": True, "message": "Choose an exact visible or known entity name."}

    def _inspect_entity(self, entity: str, question: str = "") -> dict[str, Any]:
        entity_id, error = self._resolve_entity(entity)
        return error or self.platform.perception.inspect_entity(self.actor_id, str(entity_id), question, global_scope=self.role in {"god", "world_builder"})

    def _listen(self, question: str = "", limit: int = 5) -> dict[str, Any]:
        return self.platform.perception.listen(self.actor_id, question, limit)

    def _send_message(self, recipient: str, topic: str, message: str) -> dict[str, Any]:
        # Keep instant testimony compact enough that the model does not turn a
        # short reply into a second narrative scene. The prompt still carries
        # the evidence-grounding rule; this boundary only enforces the length
        # contract advertised to every instant interaction.
        text = str(message or "")
        sentences = [part for part in re.split(r"(?<=[.!?。！？])", text) if part.strip()]
        if len(sentences) > 2 or len(text) > 240:
            return {
                "status": "rejected",
                "failed_check": "message_too_long",
                "retryable": True,
                "message": "Send at most two sentences and 240 characters. Keep only facts visible in this turn and say when a detail is unknown.",
            }
        return self.platform.submit_dialogue(self.actor_id, recipient, topic, message)

    def _wait(self, reason: str = "") -> dict[str, Any]:
        """End the bounded session without creating a world-side effect."""

        return {
            "status": "wait",
            "reason": str(reason or "No response or world change is needed now."),
        }

    def _create_trade_offer(self, recipient: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        result = self.platform.create_trade_offer(self.actor_id, recipient, give, want)
        return self._trade_result_with_choice(result)

    def _resolve_offer(self, offer: str) -> tuple[str | None, dict[str, Any] | None]:
        resolved, error = self.platform.view.resolve_trade_offer_choice(self.actor_id, offer)
        return (str(resolved["offer_id"]), None) if resolved else (None, error)

    def _accept_trade_offer(self, offer: str) -> dict[str, Any]:
        offer_id, error = self._resolve_offer(offer)
        return error or self.platform.accept_trade_offer(self.actor_id, str(offer_id))

    def _counter_trade_offer(self, offer: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        offer_id, error = self._resolve_offer(offer)
        if error:
            return error
        return self._trade_result_with_choice(self.platform.counter_trade_offer(self.actor_id, str(offer_id), give, want))

    def _reject_trade_offer(self, offer: str) -> dict[str, Any]:
        offer_id, error = self._resolve_offer(offer)
        return error or self.platform.reject_trade_offer(self.actor_id, str(offer_id))

    def _trade_result_with_choice(self, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict) or not result.get("offer_id"):
            return result
        offer_view = next((item for item in self.platform.view._trade_offer_view(self.actor_id) if item.get("offer_id") == result.get("offer_id")), None)
        public = dict(result)
        if offer_view:
            public["offer"] = offer_view.get("choice")
        public.pop("offer_id", None)
        return public

    def _read_public_record(self, record: str, question: str = "") -> dict[str, Any]:
        entity_id, error = self._resolve_entity(record)
        return error or self.platform.perception.read_public_record(self.actor_id, str(entity_id), question, global_scope=self.role == "god")

    def _query_known_fact(self, query: str = "", limit: int = 8) -> dict[str, Any]:
        return self.platform.perception.query_known_fact(self.actor_id, query, limit)

    def _query_history(
        self,
        event_type: str = "",
        actor: str = "",
        contains: str = "",
        since_minute: int | None = None,
        until_minute: int | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        return self.platform.perception.query_history(
            self.actor_id,
            event_type=event_type,
            actor=actor,
            contains=contains,
            since_minute=since_minute,
            until_minute=until_minute,
            limit=limit,
            global_scope=self.role in {"god", "story"},
        )

    def _query_knowledge_gaps(self, status: str = "active", contains: str = "", limit: int = 8) -> dict[str, Any]:
        return self.platform.perception.query_knowledge_gaps(
            self.actor_id,
            status=status,
            contains=contains,
            limit=limit,
            global_scope=self.role in {"god", "story"},
        )

    def _recall_memory(self, question: str = "", limit: int = 8, scope: str = "all") -> dict[str, Any]:
        return self.platform.memory.recall(self.actor_id, question, limit, scope=scope)

    def _preview_behavior(self, choice: str, target: dict[str, Any] | None = None) -> dict[str, Any]:
        behavior, error = self.platform.view.resolve_behavior_choice(self.actor_id, choice)
        if error:
            return error
        # Do not echo the internal reference in a model-facing tool result.
        public = {key: value for key, value in (behavior or {}).items() if key != "ref"}
        return {"status": "ok", "behavior": public, "target": target or {}}

    def _request_behavior(
        self,
        choice: str,
        target: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        reason: str = "",
        confidence: float = 0.0,
    ) -> dict[str, Any]:
        behavior, error = self.platform.view.resolve_behavior_choice(self.actor_id, choice)
        if error:
            return error
        intent = BehaviorIntent(
            actor_id=self.actor_id,
            behavior_ref=str((behavior or {}).get("ref")),
            target=dict(target or {}),
            parameters=dict(parameters or {}),
            reason=reason,
            confidence=float(confidence),
            intent_id=str(uuid4()),
            observed_world_revision=self.observed_world_revision,
            observed_agent_revision=self.observed_agent_revision,
        )
        result = self.platform.submit_behavior(intent)
        if isinstance(result, dict) and result.get("behavior_ref"):
            # Keep canonical refs in Platform/audit storage, but return the
            # visible choice to the model so it never has to learn an ID.
            result = dict(result)
            result["choice"] = (behavior or {}).get("choice") or (behavior or {}).get("name")
            result.pop("behavior_ref", None)
        return result

    def _record_experience(self, summary: str, event: str = "", confidence: float = 1.0, claim_status: str = "inferred") -> dict[str, Any]:
        if self.role == "agent_reflection":
            meaningful_events = {
                "behavior.completed", "behavior.failed", "behavior.interrupted",
                "dialogue.completed", "trade.completed", "trade.offer_created",
                "trade.countered", "exploration.completed", "research.completed",
                "object.recovered", "relationship.changed", "world.change.world_property",
            }
            has_outcome = bool(self.platform.store.repositories.events.recent_outcomes_for_actor(self.actor_id, limit=1))
            has_memory = bool(self.platform.store.repositories.memory.list_memories(self.actor_id, limit=1))
            has_event = any(
                str(item.get("event_type", "")) in meaningful_events
                for item in self.platform.store.repositories.events.recent_events_for_actor(self.actor_id, limit=20)
            )
            if not (has_outcome or has_memory or has_event):
                return {
                    "status": "rejected",
                    "failed_check": "reflection_no_observed_result",
                    "retryable": True,
                    "message": "No completed outcome, meaningful event, or prior memory is available for a durable experience.",
                }
        return self.platform.submit_experience(self.actor_id, event, summary, float(confidence), str(claim_status or "inferred"))

    def _update_capability(self, capability: str, delta: float, evidence: str) -> dict[str, Any]:
        return self.platform.submit_capability_update(self.actor_id, capability, float(delta), evidence)

    def _record_world_progress(self, progress_key: str, evidence: str) -> dict[str, Any]:
        return self.platform.submit_world_progress(
            self.actor_id,
            progress_key,
            None,
            evidence,
            source_event_ids=self.source_event_ids,
        )

    def _propose_institution(
        self,
        name: str,
        purpose: str,
        scope: str = "world",
        evidence: str = "",
        evidence_sources: list[dict[str, Any]] | None = None,
        minimum_evidence: int | None = None,
        ledger: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Event-triggered institution tasks already carry the exact persisted
        # evidence contract.  Authors and models should describe the public
        # institution, not copy internal selectors into every proposal.
        contract = self.evolution_contract
        if not evidence_sources and isinstance(contract.get("evidence_sources"), list):
            evidence_sources = list(contract.get("evidence_sources") or [])
        if minimum_evidence is None and contract.get("minimum_evidence") is not None:
            minimum_evidence = max(1, int(contract.get("minimum_evidence") or 1))
        return self.platform.submit_institution_proposal(
            self.actor_id,
            {"name": name, "purpose": purpose, "scope": scope, "evidence": evidence, "evidence_sources": list(evidence_sources or []), "minimum_evidence": int(minimum_evidence or 1), "ledger": dict(ledger or {}), "policy": dict(policy or {})},
        )

    def _approve_institution(self, institution: str, reason: str = "") -> dict[str, Any]:
        return self.platform.activate_institution(self.actor_id, institution, reason)

    def _revise_institution(
        self,
        institution: str,
        reason: str,
        policy: dict[str, Any] | None = None,
        ledger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.platform.revise_institution(self.actor_id, institution, policy=policy, ledger=ledger, reason=reason)

    def _update_belief(self, confidence: float, belief: str = "", statement: str = "", reason: str = "") -> dict[str, Any]:
        return self.platform.submit_belief_update(self.actor_id, "", statement or belief, float(confidence), reason)

    def _update_goal(self, statement: str, goal: str = "", status: str = "active", priority: float = 1.0) -> dict[str, Any]:
        return self.platform.submit_goal_update(self.actor_id, "ultimate_goal", statement or goal, status, float(priority))

    def _propose_behavior(
        self,
        goal: str,
        actions: list[str],
        name: str = "",
        summary: str = "",
        entry_conditions: list[str] | None = None,
        success_results: list[str] | None = None,
        failure_results: list[str] | None = None,
    ) -> dict[str, Any]:
        action_defs = self.platform.store.repositories.entities.get_definitions("action_def")
        resolved_actions: list[str] = []
        unknown_actions: list[str] = []
        for action in actions:
            key = "".join(char for char in str(action).casefold() if char.isalnum())
            matches = [item["id"] for item in action_defs if key in {"".join(char for char in str(item.get("id", "")).casefold() if char.isalnum()), "".join(char for char in str(item.get("description", "")).casefold() if char.isalnum())}]
            if len(matches) == 1:
                resolved_actions.append(matches[0])
            else:
                unknown_actions.append(str(action))
        if unknown_actions:
            return {
                "status": "rejected",
                "failed_check": "action_name",
                "retryable": True,
                "message": "Use exact names from the registered action list.",
                "unknown_actions": unknown_actions,
                "available_actions": [str(item.get("id", "action")).replace("_", " ").title() for item in action_defs],
            }
        proposal = {
            "behavior_id": "",
            "agent_label": name,
            "summary": summary or goal,
            "goal": goal,
            "action_ids": resolved_actions,
            "entry_conditions": list(entry_conditions or []),
            "success_results": list(success_results or []),
            "failure_results": list(failure_results or []),
        }
        return self.platform.submit_behavior_proposal(self.actor_id, proposal)

    def _propose_definition(
        self,
        definition_type: str,
        name: str,
        definition: dict[str, Any],
        assign_to: list[str] | None = None,
        followup_behavior: dict[str, Any] | None = None,
        followup_recipe: dict[str, Any] | None = None,
        evidence: str = "",
    ) -> dict[str, Any]:
        required_type = str(self.evolution_contract.get("required_definition_type") or "").strip()
        if required_type and str(definition_type).strip().casefold() != required_type.casefold():
            return {
                "status": "rejected",
                "failed_check": "evolution_contract",
                "retryable": True,
                "message": f"This evolution task requires a {required_type} definition; choose that definition_type or wait.",
            }
        required_assignees = self.evolution_contract.get("assign_to")
        if isinstance(required_assignees, str):
            required_assignees = [required_assignees]
        if required_assignees:
            supplied = {
                str(self.platform.targets.canonical_id(str(item)) or item)
                for item in (assign_to or [])
            }
            missing = [
                self._contract_visible_name(item)
                for item in required_assignees
                if str(self.platform.targets.canonical_id(str(item)) or item) not in supplied
            ]
            if missing:
                return {
                    "status": "rejected",
                    "failed_check": "evolution_contract",
                    "retryable": True,
                    "message": "Assign the behavior to the configured audience so it enters the next NPC catalog.",
                    "required_assign_to": [self._contract_visible_name(item) for item in required_assignees],
                }
        if self.evolution_contract.get("require_story_outcome") and str(definition_type).strip().casefold() == "action":
            followup_definition = (followup_behavior or {}).get("definition") if isinstance(followup_behavior, dict) else None
            outcome_events = followup_definition.get("outcome_events") if isinstance(followup_definition, dict) else None
            if not any(
                isinstance(event, dict) and "story" in {str(role) for role in (event.get("wake") or [])}
                for event in (outcome_events or [])
            ):
                return {
                    "status": "rejected",
                    "failed_check": "evolution_contract",
                    "retryable": True,
                    "message": "This evolution task requires the bound behavior to declare one outcome_events entry with wake ['story'] so Story receives the executed result.",
                }
        return self.platform.submit_definition_proposal(
            self.actor_id,
            {
                "definition_type": definition_type,
                "name": name,
                "definition": dict(definition or {}),
                "assign_to": list(assign_to or []),
                "followup_behavior": dict(followup_behavior or {}) if followup_behavior else None,
                "followup_recipe": dict(followup_recipe or {}) if followup_recipe else None,
                "evidence": evidence,
            },
        )

    def _propose_entity(
        self,
        entity_type: str,
        name: str,
        location: str = "",
        state: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        location_id = self.platform.targets.canonical_id(location) if location else ""
        if location and not location_id:
            return {"status": "rejected", "failed_check": "location_name", "retryable": True, "message": "Choose an exact visible location name."}
        proposal = {
            "entity_id": "",
            "entity_type": entity_type,
            "name": name,
            "location_id": location_id,
            "state": dict(state or {}),
            "description": description,
        }
        return self.platform.submit_entity_proposal(self.actor_id, proposal)

    def _propose_world_change(
        self,
        change_type: str,
        summary: str,
        scope: str = "world",
        conditions: list[str] | None = None,
        effects: list[str] | None = None,
        expected_effects: list[str] | None = None,
        evaluation_window_minutes: int = 1440,
        property: str = "",
        value: Any = None,
        rule: dict[str, Any] | None = None,
        relationship: dict[str, Any] | None = None,
        faction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal = {
            "change_type": change_type,
            "summary": summary,
            "scope": scope,
            "conditions": list(conditions or []),
            "effects": list(effects or []),
            "expected_effects": list(expected_effects or effects or []),
            "evaluation_window_minutes": int(evaluation_window_minutes),
            "property": property,
            "value": value,
            "rule": dict(rule or {}),
            "relationship": dict(relationship or {}),
            "faction": dict(faction or {}),
        }
        return self.platform.submit_world_change(self.actor_id, proposal, origin_role=self.role)

    def _propose_story_beat(self, beat: str = "", audience: list[str] | None = None, summary: str = "", fact: str = "", goal_hint: str = "", research_source: str = "", unlock_choices: list[str] | None = None, reason: str = "") -> dict[str, Any]:
        return self.platform.submit_story_beat(
            self.actor_id,
            {"beat": beat, "audience": list(audience or []), "summary": summary, "fact": fact, "goal_hint": goal_hint, "research_source": research_source, "unlock_choices": list(unlock_choices or []), "reason": reason},
            source_event_ids=self.source_event_ids,
        )

    def _validate_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        return self.platform.validate_world_template(template)

    def _submit_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        return self.platform.submit_world_template(self.actor_id, template)
