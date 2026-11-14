from __future__ import annotations

from typing import Any

from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from .protocol import normalize_provider_intent
from .runtime_policy import ROLE_INTENTS


class AgentIntentGateway:
    """Translate one final model intent into a validated Platform operation.

    Session scheduling and tool calls remain in AgentEngine/AgentToolExecutor;
    this gateway owns only final Intent normalization, role permissions and
    Platform submission.
    """

    def __init__(self, platform: Platform):
        self.platform = platform
        self.view = platform.view

    @staticmethod
    def reflection_has_evidence(context: dict[str, Any]) -> bool:
        """Allow reflection only when the view contains something observable."""

        if context.get("recent_outcomes") or context.get("memories"):
            return True

        meaningful_events = {
            "behavior.completed",
            "behavior.failed",
            "behavior.interrupted",
            "dialogue.completed",
            "trade.completed",
            "trade.offer_created",
            "trade.countered",
            "exploration.completed",
            "research.completed",
            "object.recovered",
            "relationship.changed",
            "world.change.world_property",
        }

        return any(
            isinstance(item, dict)
            and str(item.get("event_type", "")) in meaningful_events
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

        markers = (
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

        return any(marker in text for marker in markers)

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

        data = (
            response.get("intent")
            if isinstance(response.get("intent"), dict)
            else dict(response)
        )

        if not isinstance(data, dict):
            return {
                "status": "rejected",
                "failed_check": "intent_schema",
                "retryable": True,
                "message": "Intent must be an object.",
            }

        data = self.normalize_provider_intent_data(role, data)

        if data.get("type") == "intent":
            data.pop("type", None)

        intent_type_value = data.get("intent_type") or data.get("kind")

        # Native tool-capable models sometimes return the proposal fields

        # directly instead of repeating the tool name. Infer only from the

        # role-specific required shape; Platform still validates the proposal.

        if not intent_type_value and role == "god":
            if data.get("change_type"):
                intent_type_value = "propose_world_change"

            elif data.get("entity_type") and data.get("name"):
                intent_type_value = "propose_entity"

            elif (
                data.get("definition_type")
                and data.get("definition")
                and data.get("name")
            ):
                intent_type_value = "propose_definition"

        if (
            not intent_type_value
            and role == "story"
            and (data.get("beat") or data.get("summary"))
        ):
            intent_type_value = "propose_story_beat"

        command_value = next(
            (
                data.get(key)
                for key in ("intent", "decision", "action")
                if isinstance(data.get(key), str)
            ),
            "",
        )

        if not intent_type_value and isinstance(command_value, str) and command_value:
            # Providers sometimes emit the visible action as ``intent``.  The

            # alias is accepted only at this protocol boundary; Platform still

            # validates the resulting Intent and all side effects.

            aliases = {
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

            intent_type_value = aliases.get(str(command_value).casefold())

        # A reflection response containing only a concise lesson is an

        # unambiguous record-experience proposal.  Use the latest visible

        # outcome as its audit reference; the Platform still performs all

        # duplicate and length checks.

        if role == "agent_reflection" and not intent_type_value and data.get("summary"):
            intent_type_value = "record_experience"

            recent_outcomes = (context or {}).get("recent_outcomes") or []

            if recent_outcomes:
                latest = recent_outcomes[0]

                data.setdefault(
                    "event_ref",
                    f"{latest.get('event_type', 'behavior.outcome')} at world minute {latest.get('world_minute', 'unknown')}",
                )

            else:
                data.setdefault("event_ref", "reflection")

        intent_type = str(
            intent_type_value
            or (
                "select_behavior"
                if data.get("choice")
                or data.get("behavior_ref")
                or data.get("behavior_id")
                or data.get("behavior")
                else ""
            )
        )

        if data.get("wait") is True or str(command_value).casefold() == "wait":
            intent_type = "wait"

        # Reasoning models sometimes finish an instant reply with only a

        # concise ``{"reason": ...}`` object after deciding that no answer

        # is needed.  In an interaction, that shape is an explicit wait; do

        # not send it through six rejected planning iterations.

        if (
            not intent_type
            and str(task_type).startswith("instant:")
            and str(data.get("reason") or "").strip()
            and not any(
                data.get(key)
                for key in ("message", "recipient", "offer", "give", "want")
            )
        ):
            intent_type = "wait"

        if (
            not intent_type
            and role == "agent_decision"
            and not (context or {}).get("executable_behaviors")
            and str(data.get("reason") or "").strip()
            and not any(
                data.get(key)
                for key in (
                    "choice",
                    "behavior",
                    "behavior_ref",
                    "message",
                    "recipient",
                    "offer",
                    "give",
                    "want",
                )
            )
        ):
            # A decision model may finish with a concise explanation instead

            # of selecting the exposed wait tool. When Platform has already

            # projected no executable behavior, that explanation is an

            # unambiguous no-op and should close the session cleanly.

            intent_type = "wait"

        if (
            not intent_type
            and role == "agent_reflection"
            and str(data.get("reason") or "").strip()
            and not any(
                data.get(key)
                for key in (
                    "summary",
                    "statement",
                    "belief",
                    "goal",
                    "capability",
                    "definition",
                )
            )
            and self.reflection_reason_is_decline(data.get("reason"))
        ):
            # A reflection model may answer with only a short explanation

            # that no durable lesson is new. Treat that as the explicit

            # low-value ``wait`` outcome instead of retrying the same empty

            # response until the ReAct budget is exhausted.

            intent_type = "wait"

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

        if intent_type == "wait":
            return {
                "status": "wait",
                "reason": str(
                    data.get("reason")
                    or data.get("message")
                    or "No suitable behavior now."
                ),
            }

        try:
            if intent_type == "select_behavior":
                # The model-facing contract uses a visible choice name.  The

                # canonical ref is resolved from the current Platform view

                # immediately before submission and never guessed by the LLM.

                if data.get("choice") and not data.get("behavior_ref"):
                    behavior, error = self.view.resolve_behavior_choice(
                        actor_id, data.get("choice")
                    )

                    if error:
                        return error

                    data = dict(data)

                    data["behavior_ref"] = behavior["ref"]

                data = dict(data)

                if isinstance(context, dict):
                    data.setdefault(
                        "observed_world_revision", context.get("world_revision")
                    )

                    data.setdefault(
                        "observed_agent_revision", context.get("agent_revision")
                    )

                return self.platform.submit_behavior(
                    BehaviorIntent.from_json(data, actor_id=actor_id)
                )

            if intent_type == "send_message":
                return self.platform.submit_dialogue(
                    actor_id,
                    str(data.get("recipient") or data.get("target_id") or ""),
                    str(data.get("topic") or "general"),
                    str(data.get("message") or ""),
                )

            if intent_type == "create_trade_offer":
                return self.platform.create_trade_offer(
                    actor_id,
                    str(data.get("recipient") or data.get("recipient_id") or ""),
                    dict(data.get("give") or {}),
                    dict(data.get("want") or {}),
                )

            if intent_type == "accept_trade_offer":
                offer, error = self.view.resolve_trade_offer_choice(
                    actor_id, data.get("offer") or data.get("offer_id")
                )

                if error:
                    return error

                return self.platform.accept_trade_offer(
                    actor_id, str(offer["offer_id"])
                )

            if intent_type == "counter_trade_offer":
                offer, error = self.view.resolve_trade_offer_choice(
                    actor_id, data.get("offer") or data.get("offer_id")
                )

                if error:
                    return error

                return self.platform.counter_trade_offer(
                    actor_id,
                    str(offer["offer_id"]),
                    dict(data.get("give") or {}),
                    dict(data.get("want") or {}),
                )

            if intent_type == "reject_trade_offer":
                offer, error = self.view.resolve_trade_offer_choice(
                    actor_id, data.get("offer") or data.get("offer_id")
                )

                if error:
                    return error

                return self.platform.reject_trade_offer(
                    actor_id, str(offer["offer_id"])
                )

            if intent_type == "record_experience":
                if role == "agent_reflection":
                    if not self.reflection_has_evidence(context or {}):
                        return {
                            "status": "rejected",
                            "failed_check": "reflection_no_observed_result",
                            "retryable": True,
                            "message": "No completed outcome, meaningful event, or prior memory is available for a durable experience.",
                        }

                return self.platform.submit_experience(
                    actor_id,
                    str(data.get("event") or data.get("event_ref") or ""),
                    str(data.get("summary") or ""),
                    float(data.get("confidence", 1.0)),
                    str(data.get("claim_status") or "inferred"),
                )

            if intent_type == "update_capability":
                return self.platform.submit_capability_update(
                    actor_id,
                    str(data.get("capability") or ""),
                    float(data.get("delta", 0)),
                    str(data.get("evidence") or ""),
                )

            if intent_type == "record_world_progress":
                return self.platform.submit_world_progress(
                    actor_id,
                    str(data.get("progress_key") or ""),
                    None,
                    str(data.get("evidence") or ""),
                    source_event_ids=source_event_ids,
                )

            if intent_type == "propose_institution":
                return self.platform.submit_institution_proposal(
                    actor_id,
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

            if intent_type == "approve_institution":
                return self.platform.activate_institution(
                    actor_id,
                    str(data.get("institution") or ""),
                    str(data.get("reason") or ""),
                )

            if intent_type == "revise_institution":
                return self.platform.revise_institution(
                    actor_id,
                    str(data.get("institution") or ""),
                    policy=data.get("policy"),
                    ledger=data.get("ledger"),
                    reason=str(data.get("reason") or ""),
                )

            if intent_type == "update_belief":
                return self.platform.submit_belief_update(
                    actor_id,
                    "",
                    str(data.get("statement") or data.get("belief") or ""),
                    float(data.get("confidence", 0.0)),
                    str(data.get("reason") or ""),
                )

            if intent_type == "update_goal":
                return self.platform.submit_goal_update(
                    actor_id,
                    "ultimate_goal",
                    str(data.get("statement") or data.get("goal") or ""),
                    str(data.get("status") or "active"),
                    float(data.get("priority", 1.0)),
                )

            if intent_type == "propose_behavior":
                proposal = dict(data)

                proposal.pop("intent_type", None)

                if not proposal.get("action_ids") and proposal.get("actions"):
                    proposal["action_ids"] = self.resolve_action_names(
                        proposal.pop("actions")
                    )

                proposal["behavior_id"] = ""

                proposal["agent_label"] = str(proposal.pop("name", "") or "")

                return self.platform.submit_behavior_proposal(actor_id, proposal)

            if intent_type == "propose_entity":
                proposal = dict(data)

                proposal.pop("intent_type", None)

                proposal["entity_id"] = ""

                if proposal.get("location") and not proposal.get("location_id"):
                    location = proposal.pop("location")

                    proposal["location_id"] = self.platform.targets.canonical_id(
                        location
                    )

                    if not proposal["location_id"]:
                        return {
                            "status": "rejected",
                            "failed_check": "location_name",
                            "retryable": True,
                            "message": "Choose an exact visible location name.",
                        }

                return self.platform.submit_entity_proposal(actor_id, proposal)

            if intent_type == "propose_definition":
                proposal = dict(data)

                proposal.pop("intent_type", None)

                return self.platform.submit_definition_proposal(actor_id, proposal)

            if intent_type == "propose_world_change":
                proposal = dict(data)

                proposal.pop("intent_type", None)

                if (
                    (
                        str(task_type).startswith("trigger:")
                        or str(task_type).startswith("event:")
                    )
                    and proposal.get("change_type") == "event"
                    and not proposal.get("property")
                ):
                    return {
                        "status": "rejected",
                        "failed_check": "event_already_recorded",
                        "retryable": True,
                        "message": "The triggering event is already recorded. Return wait or submit one typed rule, progress, entity, relationship, or faction proposal grounded in the observed result.",
                    }

                result = self.platform.submit_world_change(
                    actor_id, proposal, origin_role=role
                )

                return result

            if intent_type == "propose_story_beat":
                proposal = dict(data)

                proposal.pop("intent_type", None)

                return self.platform.submit_story_beat(
                    actor_id, proposal, source_event_ids=source_event_ids
                )

            if intent_type == "submit_world_template":
                return self.platform.submit_world_template(
                    actor_id, data.get("template") or {}
                )

        except (TypeError, ValueError, KeyError) as exc:
            return {
                "status": "rejected",
                "failed_check": "intent_schema",
                "message": str(exc),
            }

        return {
            "status": "rejected",
            "failed_check": "intent_type",
            "message": "Intent was not handled.",
        }

    @staticmethod
    def normalize_provider_intent_data(
        role: str, data: dict[str, Any]
    ) -> dict[str, Any]:
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
            return {
                "accept_trade_offer",
                "counter_trade_offer",
                "reject_trade_offer",
                "wait",
            }

        return {"send_message", "wait"}

    def resolve_action_names(self, actions: Any) -> list[str]:

        definitions = self.platform.store.repositories.entities.get_definitions(
            "action_def"
        )

        resolved: list[str] = []

        for value in list(actions or []):
            key = "".join(char for char in str(value).casefold() if char.isalnum())

            matches = []

            for item in definitions:
                candidates = {
                    "".join(
                        char
                        for char in str(item.get("id", "")).casefold()
                        if char.isalnum()
                    ),
                    "".join(
                        char
                        for char in str(item.get("description", "")).casefold()
                        if char.isalnum()
                    ),
                }

                if key in candidates:
                    matches.append(str(item["id"]))

            if len(matches) == 1:
                resolved.append(matches[0])

        return resolved
