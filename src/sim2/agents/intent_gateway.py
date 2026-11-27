from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from .protocol import normalize_provider_intent
from .reflection_policy import MEANINGFUL_REFLECTION_EVENTS
from .runtime_policy import ROLE_INTENTS

# Clear statements that no durable lesson is new; see ``reflection_reason_is_decline``.
REFLECTION_DECLINE_MARKERS = (
    "no new lesson",
    "no new durable",
    "no durable lesson",
    "nothing new",
    "no useful lesson",
    "already recorded",
    "already known",
    "no change to record",
    "没有新的经验",
    "没有新的教训",
    "无需记录",
    "没有值得保留",
)
# Providers sometimes emit the visible action as ``intent``.  The alias is
# accepted only at this protocol boundary; Platform still validates the
# resulting Intent and all side effects.
PROVIDER_COMMAND_ALIASES = {
    "request_behavior": "select_behavior",
    "select_behavior": "select_behavior",
    "send_message": "send_message",
    "create_trade_offer": "create_trade_offer",
    "accept_trade_offer": "accept_trade_offer",
    "counter_trade_offer": "counter_trade_offer",
    "reject_trade_offer": "reject_trade_offer",
    "record_experience": "record_experience",
    "update_capability": "update_capability",
    "record_world_progress": "record_world_progress",
    "update_belief": "update_belief",
    "update_goal": "update_goal",
    "propose_behavior": "propose_behavior",
    "propose_entity": "propose_entity",
    "propose_definition": "propose_definition",
    "propose_world_change": "propose_world_change",
    "propose_story_beat": "propose_story_beat",
    "propose_institution": "propose_institution",
    "approve_institution": "approve_institution",
    "revise_institution": "revise_institution",
    "wait": "wait",
}
# Fields that make an otherwise typeless answer a real proposal, so a bare
# ``reason`` next to them is not an implicit wait.
_INSTANT_REPLY_FIELDS = ("message", "recipient", "offer", "give", "want")
_DECISION_FIELDS = ("choice", "behavior", "behavior_ref", *_INSTANT_REPLY_FIELDS)
_REFLECTION_FIELDS = ("summary", "statement", "belief", "goal", "capability", "definition")


def _fold(value: Any) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())


@dataclass(frozen=True)
class IntentCall:
    """One normalized final intent, ready for a Platform submission."""

    role: str
    actor_id: str
    data: dict[str, Any]
    context: dict[str, Any] | None
    task_type: str
    source_event_ids: list[int] | None


class AgentIntentGateway:
    """Translate one final model intent into a validated Platform operation.

    Session scheduling and tool calls remain in AgentEngine/AgentToolExecutor;
    this gateway owns only final Intent normalization, role permissions and
    Platform submission.
    """

    def __init__(self, platform: Platform):
        self.platform = platform
        self.view = platform.view
        self._handlers: dict[str, Callable[[IntentCall], dict[str, Any]]] = {
            "select_behavior": self._select_behavior,
            "send_message": self._send_message,
            "create_trade_offer": self._create_trade_offer,
            "accept_trade_offer": self._accept_trade_offer,
            "counter_trade_offer": self._counter_trade_offer,
            "reject_trade_offer": self._reject_trade_offer,
            "record_experience": self._record_experience,
            "update_capability": self._update_capability,
            "record_world_progress": self._record_world_progress,
            "propose_institution": self._propose_institution,
            "approve_institution": self._approve_institution,
            "revise_institution": self._revise_institution,
            "update_belief": self._update_belief,
            "update_goal": self._update_goal,
            "propose_behavior": self._propose_behavior,
            "propose_entity": self._propose_entity,
            "propose_definition": self._propose_definition,
            "propose_world_change": self._propose_world_change,
            "propose_story_beat": self._propose_story_beat,
            "submit_world_template": self._submit_world_template,
        }

    # ------------------------------------------------------------ reflection

    @staticmethod
    def reflection_has_evidence(context: dict[str, Any]) -> bool:
        """Allow reflection only when the view contains something observable."""

        if context.get("recent_outcomes") or context.get("memories"):
            return True
        return any(
            isinstance(item, dict) and str(item.get("event_type", "")) in MEANINGFUL_REFLECTION_EVENTS
            for item in (context.get("recent_actions") or [])
        )

    @staticmethod
    def reflection_reason_is_decline(reason: Any) -> bool:
        """Recognize an explicit low-value reflection decline.

        A bare explanation is not automatically a successful reflection: it
        may be an unfinished model response.  Only clear statements that no
        durable lesson is new are normalized to the intentional ``wait``
        outcome; all other reason-only responses remain protocol errors and
        receive the normal ReAct feedback.
        """

        text = str(reason or "").casefold()
        if not text.strip():
            return False
        return any(marker in text for marker in REFLECTION_DECLINE_MARKERS)

    # ---------------------------------------------------------------- submit

    def submit(
        self,
        role: str,
        actor_id: str,
        response: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        task_type: str = "",
        source_event_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        data = response.get("intent") if isinstance(response.get("intent"), dict) else dict(response)
        if not isinstance(data, dict):
            return {"status": "rejected", "failed_check": "intent_schema", "retryable": True, "message": "Intent must be an object."}
        data = self.normalize_provider_intent_data(role, data)
        if data.get("type") == "intent":
            data.pop("type", None)
        intent_type = self._resolve_intent_type(role, data, context, task_type)
        rejection = self._reject_disallowed(role, intent_type, task_type)
        if rejection is not None:
            return rejection
        contract = (context or {}).get("evolution_trigger") or {}
        allowed = contract.get("allowed_tools", contract.get("tools"))
        tool_name = "request_behavior" if intent_type == "select_behavior" else intent_type
        if isinstance(allowed, list) and intent_type != "wait" and tool_name not in allowed:
            return {"status": "rejected", "failed_check": "task_tool", "retryable": True, "message": "This task accepts only its configured submission tools."}
        if intent_type == "wait":
            return {"status": "wait", "reason": str(data.get("reason") or data.get("message") or "No suitable behavior now.")}
        handler = self._handlers.get(intent_type)
        try:
            if handler is not None:
                return handler(IntentCall(role, actor_id, data, context, task_type, source_event_ids))
        except (TypeError, ValueError, KeyError) as exc:
            return {"status": "rejected", "failed_check": "intent_schema", "message": str(exc)}
        return {"status": "rejected", "failed_check": "intent_type", "message": "Intent was not handled."}

    # ------------------------------------------------------ intent resolution

    def _resolve_intent_type(self, role: str, data: dict[str, Any], context: dict[str, Any] | None, task_type: str) -> str:
        command = next((data.get(key) for key in ("intent", "decision", "action") if isinstance(data.get(key), str)), "")
        declared = data.get("intent_type") or data.get("kind") or self._shape_intent_type(role, data)
        if not declared and isinstance(command, str) and command:
            declared = PROVIDER_COMMAND_ALIASES.get(str(command).casefold())
        if role == "agent_reflection" and not declared and data.get("summary"):
            # A reflection response containing only a concise lesson is an
            # unambiguous record-experience proposal.
            declared = "record_experience"
            self._default_event_ref(data, context)
        intent_type = str(declared or ("select_behavior" if self._names_behavior(data) else ""))
        if data.get("wait") is True or str(command).casefold() == "wait":
            return "wait"
        return "wait" if self._implies_wait(role, data, intent_type, context, task_type) else intent_type

    @staticmethod
    def _shape_intent_type(role: str, data: dict[str, Any]) -> str | None:
        """Infer a missing intent name from the role-specific required shape.

        Native tool-capable models sometimes return the proposal fields
        directly instead of repeating the tool name; Platform still validates
        the proposal.
        """

        if role == "god":
            if data.get("change_type"):
                return "propose_world_change"
            if data.get("entity_type") and data.get("name"):
                return "propose_entity"
            if data.get("definition_type") and data.get("definition") and data.get("name"):
                return "propose_definition"
        elif role == "story" and (data.get("beat") or data.get("summary")):
            return "propose_story_beat"
        return None

    @staticmethod
    def _default_event_ref(data: dict[str, Any], context: dict[str, Any] | None) -> None:
        """Use the latest visible outcome as the audit reference of a bare lesson."""

        recent_outcomes = (context or {}).get("recent_outcomes") or []
        if recent_outcomes:
            latest = recent_outcomes[0]
            data.setdefault(
                "event_ref",
                f"{latest.get('event_type', 'behavior.outcome')} at world minute {latest.get('world_minute', 'unknown')}",
            )
        else:
            data.setdefault("event_ref", "reflection")

    @staticmethod
    def _names_behavior(data: dict[str, Any]) -> bool:
        return any(data.get(key) for key in ("choice", "behavior_ref", "behavior_id", "behavior"))

    def _implies_wait(
        self, role: str, data: dict[str, Any], intent_type: str, context: dict[str, Any] | None, task_type: str
    ) -> bool:
        """Recognize reason-only answers that explicitly decline to act.

        Reasoning models sometimes finish with only a concise ``{"reason": ...}``
        object after deciding that nothing is needed; retrying such an answer
        through rejected planning iterations would only burn the ReAct budget.
        """

        if intent_type or not str(data.get("reason") or "").strip():
            return False
        if str(task_type).startswith("instant:") and not any(data.get(key) for key in _INSTANT_REPLY_FIELDS):
            return True
        if (
            role == "agent_decision"
            and not (context or {}).get("executable_behaviors")
            and not any(data.get(key) for key in _DECISION_FIELDS)
        ):
            # Platform already projected no executable behavior, so the
            # explanation is an unambiguous no-op.
            return True
        return (
            role == "agent_reflection"
            and not any(data.get(key) for key in _REFLECTION_FIELDS)
            and self.reflection_reason_is_decline(data.get("reason"))
        )

    def _reject_disallowed(self, role: str, intent_type: str, task_type: str) -> dict[str, Any] | None:
        if intent_type not in ROLE_INTENTS.get(role, set()):
            return {
                "status": "rejected",
                "failed_check": "intent_type",
                "retryable": True,
                "message": "This intent type is not allowed for the current role.",
            }
        interaction_allowed = self.interaction_intents(task_type)
        if interaction_allowed is not None and intent_type not in interaction_allowed:
            return {
                "status": "rejected",
                "failed_check": "interaction_intent_type",
                "retryable": True,
                "message": "This interaction accepts only a direct reply or the listed trade decision; choose wait when no response is needed.",
            }
        return None

    @staticmethod
    def normalize_provider_intent_data(role: str, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize harmless provider wrappers without changing world semantics.

        Some JSON-mode models put the intent name in ``choice`` and the real
        visible behavior in ``behavior`` or a nested ``parameters.choice``.
        The model-facing contract remains the visible choice; this boundary
        repair is accepted only for the decision role and still passes through
        exact Platform choice resolution.
        """

        return normalize_provider_intent(role, data)

    @staticmethod
    def interaction_intents(task_type: str) -> set[str] | None:
        task = str(task_type or "")
        if not task.startswith("instant:"):
            return None
        event_type = task.removeprefix("instant:")
        if event_type == "dialogue.completed":
            return {"send_message", "wait"}
        if event_type in {"trade.offer_created", "trade.countered"}:
            return {"accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "wait"}
        return {"send_message", "wait"}

    def resolve_action_names(self, actions: Any) -> list[str]:
        definitions = self.platform.store.repositories.entities.get_definitions("action_def")
        resolved: list[str] = []
        for value in list(actions or []):
            key = _fold(value)
            matches = [
                str(item["id"])
                for item in definitions
                if key in {_fold(item.get("id", "")), _fold(item.get("description", ""))}
            ]
            if len(matches) == 1:
                resolved.append(matches[0])
        return resolved

    # ------------------------------------------------- Platform submissions

    @staticmethod
    def _proposal(call: IntentCall) -> dict[str, Any]:
        proposal = dict(call.data)
        proposal.pop("intent_type", None)
        return proposal

    def _select_behavior(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        if data.get("choice") and not data.get("behavior_ref"):
            # The model-facing contract uses a visible choice name.  The
            # canonical ref is resolved from the current Platform view
            # immediately before submission and never guessed by the LLM.
            behavior, error = self.view.resolve_behavior_choice(call.actor_id, data.get("choice"))
            if error:
                return error
            data = dict(data)
            data["behavior_ref"] = behavior["ref"]
        data = dict(data)
        if isinstance(call.context, dict):
            data.setdefault("observed_world_revision", call.context.get("world_revision"))
            data.setdefault("observed_agent_revision", call.context.get("agent_revision"))
        return self.platform.submit_behavior(BehaviorIntent.from_json(data, actor_id=call.actor_id))

    def _send_message(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.submit_dialogue(
            call.actor_id,
            str(data.get("recipient") or data.get("target_id") or ""),
            str(data.get("topic") or "general"),
            str(data.get("message") or ""),
        )

    def _create_trade_offer(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.create_trade_offer(
            call.actor_id,
            str(data.get("recipient") or data.get("recipient_id") or ""),
            dict(data.get("give") or {}),
            dict(data.get("want") or {}),
        )

    def _resolve_offer(self, call: IntentCall) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        return self.view.resolve_trade_offer_choice(call.actor_id, call.data.get("offer") or call.data.get("offer_id"))

    def _accept_trade_offer(self, call: IntentCall) -> dict[str, Any]:
        offer, error = self._resolve_offer(call)
        if error:
            return error
        return self.platform.accept_trade_offer(call.actor_id, str(offer["offer_id"]))

    def _counter_trade_offer(self, call: IntentCall) -> dict[str, Any]:
        offer, error = self._resolve_offer(call)
        if error:
            return error
        return self.platform.counter_trade_offer(
            call.actor_id,
            str(offer["offer_id"]),
            dict(call.data.get("give") or {}),
            dict(call.data.get("want") or {}),
        )

    def _reject_trade_offer(self, call: IntentCall) -> dict[str, Any]:
        offer, error = self._resolve_offer(call)
        if error:
            return error
        return self.platform.reject_trade_offer(call.actor_id, str(offer["offer_id"]))

    def _record_experience(self, call: IntentCall) -> dict[str, Any]:
        if call.role == "agent_reflection" and not self.reflection_has_evidence(call.context or {}):
            return {
                "status": "rejected",
                "failed_check": "reflection_no_observed_result",
                "retryable": True,
                "message": "No completed outcome, meaningful event, or prior memory is available for a durable experience.",
            }
        data = call.data
        return self.platform.submit_experience(
            call.actor_id,
            str(data.get("event") or data.get("event_ref") or ""),
            str(data.get("summary") or ""),
            float(data.get("confidence", 1.0)),
            str(data.get("claim_status") or "inferred"),
        )

    def _update_capability(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.submit_capability_update(
            call.actor_id,
            str(data.get("capability") or ""),
            float(data.get("delta", 0)),
            str(data.get("evidence") or ""),
        )

    def _record_world_progress(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.submit_world_progress(
            call.actor_id,
            str(data.get("progress_key") or ""),
            None,
            str(data.get("evidence") or ""),
            source_event_ids=call.source_event_ids,
        )

    def _propose_institution(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.submit_institution_proposal(
            call.actor_id,
            {
                "name": data.get("name"),
                "purpose": data.get("purpose"),
                "scope": data.get("scope"),
                "evidence": data.get("evidence"),
                "evidence_sources": data.get("evidence_sources"),
                "minimum_evidence": data.get("minimum_evidence", 1),
                "ledger": data.get("ledger"),
                "policy": data.get("policy"),
            },
        )

    def _approve_institution(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.activate_institution(
            call.actor_id,
            str(data.get("institution") or ""),
            str(data.get("reason") or ""),
        )

    def _revise_institution(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.revise_institution(
            call.actor_id,
            str(data.get("institution") or ""),
            policy=data.get("policy"),
            ledger=data.get("ledger"),
            reason=str(data.get("reason") or ""),
        )

    def _update_belief(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.submit_belief_update(
            call.actor_id,
            "",
            str(data.get("statement") or data.get("belief") or ""),
            float(data.get("confidence", 0.0)),
            str(data.get("reason") or ""),
            previous_statement=str(data.get("previous_statement") or ""),
        )

    def _update_goal(self, call: IntentCall) -> dict[str, Any]:
        data = call.data
        return self.platform.submit_goal_update(
            call.actor_id,
            "ultimate_goal",
            str(data.get("statement") or data.get("goal") or ""),
            str(data.get("status") or "active"),
            float(data.get("priority", 1.0)),
        )

    def _propose_behavior(self, call: IntentCall) -> dict[str, Any]:
        proposal = self._proposal(call)
        if not proposal.get("action_ids") and proposal.get("actions"):
            proposal["action_ids"] = self.resolve_action_names(proposal.pop("actions"))
        proposal["behavior_id"] = ""
        proposal["agent_label"] = str(proposal.pop("name", "") or "")
        return self.platform.submit_behavior_proposal(call.actor_id, proposal)

    def _propose_entity(self, call: IntentCall) -> dict[str, Any]:
        proposal = self._proposal(call)
        proposal["entity_id"] = ""
        if proposal.get("location") and not proposal.get("location_id"):
            location = proposal.pop("location")
            proposal["location_id"] = self.platform.targets.canonical_id(location)
            if not proposal["location_id"]:
                return {
                    "status": "rejected",
                    "failed_check": "location_name",
                    "retryable": True,
                    "message": "Choose an exact visible location name.",
                }
        return self.platform.submit_entity_proposal(call.actor_id, proposal)

    def _propose_definition(self, call: IntentCall) -> dict[str, Any]:
        return self.platform.submit_definition_proposal(
            call.actor_id, self._proposal(call), contract=(call.context or {}).get("evolution_trigger"),
        )

    def _propose_world_change(self, call: IntentCall) -> dict[str, Any]:
        proposal = self._proposal(call)
        if (
            (str(call.task_type).startswith("trigger:") or str(call.task_type).startswith("event:"))
            and proposal.get("change_type") == "event"
            and not proposal.get("property")
        ):
            return {
                "status": "rejected",
                "failed_check": "event_already_recorded",
                "retryable": True,
                "message": "The triggering event is already recorded. Return wait or submit one typed rule, progress, entity, relationship, or faction proposal grounded in the observed result.",
            }
        return self.platform.submit_world_change(call.actor_id, proposal, origin_role=call.role)

    def _propose_story_beat(self, call: IntentCall) -> dict[str, Any]:
        return self.platform.submit_story_beat(
            call.actor_id, self._proposal(call), source_event_ids=call.source_event_ids
        )

    def _submit_world_template(self, call: IntentCall) -> dict[str, Any]:
        return self.platform.submit_world_template(call.actor_id, call.data.get("template") or {})
