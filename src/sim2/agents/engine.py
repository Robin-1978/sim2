from __future__ import annotations

import inspect
import hashlib
import json
import copy
from dataclasses import dataclass, field
from typing import Any

from ..agent_view.compiler import AgentViewCompiler
from ..agent_view.validator import validate_agent_view
from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from ..prompting.renderer import render_context
from .tools import (
    AgentToolExecutor,
    TOOL_DEFINITIONS,
    parse_tool_call,
    tool_schemas_for_role,
)


ROLE_NAMES = {"agent_decision", "agent_reflection", "god", "story", "world_builder"}


def role_policy(platform: Platform, role: str) -> str:
    prompt = platform.store.get_prompt("agent", {}) or {}
    prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
    policies = prompt.get("role_policies", {}) if isinstance(prompt, dict) else {}
    return str(policies.get(role) or "")

ROLE_INTENTS = {
    "agent_decision": {"select_behavior", "send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "wait"},
    "agent_reflection": {"record_experience", "update_capability", "update_belief", "update_goal", "propose_behavior", "wait"},
    "god": {"record_world_progress", "propose_entity", "propose_world_change", "wait"},
    "story": {"record_world_progress", "propose_story_beat", "wait"},
    "world_builder": {"submit_world_template", "wait"},
}


@dataclass(frozen=True)
class AgentTurn:
    role: str
    task_type: str
    response: dict[str, Any]
    result: dict[str, Any]
    iterations: int
    transcript: list[dict[str, Any]] = field(default_factory=list)
    initial_context: dict[str, Any] = field(default_factory=dict)
    prompt_history: list[str] = field(default_factory=list)


class AgentEngine:
    """Run one bounded ReAct session for any supported agent role.

    A session is independent of simulation minutes.  Each provider call either
    returns a tool request or a final Intent.  Tool results and Platform errors
    are appended to the compact context, so the model can correct its next
    request.  Once an Intent is accepted, the session ends; a long action is
    subsequently advanced by Platform rather than by another LLM call.
    """

    def __init__(self, platform: Platform, provider: Any, max_iterations: int = 6, max_repeated_tool_calls: int = 2, max_tool_calls: int = 12, max_read_tool_calls: int = 8):
        self.platform = platform
        self.provider = provider
        self.max_iterations = max(1, max_iterations)
        self.max_repeated_tool_calls = max(1, max_repeated_tool_calls)
        self.max_tool_calls = max(1, max_tool_calls)
        self.max_read_tool_calls = max(1, max_read_tool_calls)
        self.view = AgentViewCompiler(platform.store)
        self._last_usage: dict[str, Any] = {}

    def _complete(
        self,
        *,
        role: str,
        system: str,
        context: str,
        messages: list[dict[str, Any]] | None = None,
        allowed_tools: list[str] | set[str] | None = None,
    ) -> dict[str, Any]:
        """Call old test providers and native tool-capable providers alike."""

        method = self.provider.complete_json
        kwargs: dict[str, Any] = {"system": system, "context": context}
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "tools" in parameters:
            kwargs["tools"] = tool_schemas_for_role(role, allowed_tools)
        if "role" in parameters:
            kwargs["role"] = role
        if "messages" in parameters:
            kwargs["messages"] = messages
        response = method(**kwargs)
        self._last_usage = dict(response.pop("_usage", {}) or {}) if isinstance(response, dict) else {}
        return response

    @staticmethod
    def _reflection_has_evidence(context: dict[str, Any]) -> bool:
        """Allow reflection only when the view contains something observable."""

        if context.get("recent_outcomes") or context.get("memories"):
            return True
        meaningful_events = {
            "behavior.completed", "behavior.failed", "behavior.interrupted",
            "dialogue.completed", "trade.completed", "trade.offer_created",
            "trade.countered", "exploration.completed", "research.completed",
            "object.recovered", "relationship.changed", "world.change.world_property",
        }
        return any(
            isinstance(item, dict) and str(item.get("event_type", "")) in meaningful_events
            for item in (context.get("recent_actions") or [])
        )

    def run(
        self,
        *,
        role: str,
        actor_id: str,
        task_type: str = "decision",
        interaction: dict[str, Any] | None = None,
    ) -> AgentTurn:
        if role not in ROLE_NAMES:
            raise ValueError(f"unknown agent role: {role}")
        if role == "agent_decision":
            try:
                state = self.platform.store.get_agent(actor_id).get("initial_state", {})
            except KeyError:
                state = {}
            if state.get("status") in {"executing", "paused"}:
                result = {"status": "skipped", "reason": "active_behavior", "message": "Platform is already executing a behavior; wait for completion or an interrupt."}
                return AgentTurn(role, task_type, {}, result, 0, [{"step": "skipped", "result": result}], {}, [])
        context = self.view.build_decision_view(actor_id, role=role)
        context["role"] = role
        if interaction:
            context["interaction"] = dict(interaction)
        interaction_tools = self._interaction_tools(task_type)
        if interaction_tools is not None:
            self._compact_interaction_context(context, task_type, interaction_tools)
        view_errors = validate_agent_view(context, role=role)
        if view_errors:
            raise ValueError("invalid AgentView: " + "; ".join(view_errors))
        if role == "agent_reflection" and task_type in {"daily_reflection", "integration_verification"} and not self._reflection_has_evidence(context):
            result = {"status": "wait", "reason": "No completed outcome or meaningful event is available for reflection."}
            return AgentTurn(role, task_type, {}, result, 0, [{"step": "guard", "result": result}], {}, [])
        task = self._task_text(role, task_type)
        executor = AgentToolExecutor(self.platform, role=role, actor_id=actor_id)
        last_response: dict[str, Any] = {}
        last_result: dict[str, Any] = {"status": "no_result"}
        transcript: list[dict[str, Any]] = []
        initial_context = copy.deepcopy(context)
        prompt_history: list[str] = []
        repeated: dict[str, int] = {}
        tool_calls_used = 0
        read_tool_calls = 0
        system_policy = role_policy(self.platform, role)
        conversation_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_policy},
            {"role": "user", "content": render_context(context, task)},
        ]

        for iteration in range(1, self.max_iterations + 1):
            prompt = render_context(context, task)
            prompt_history.append(prompt)
            try:
                self._last_usage = {}
                response = self._complete(
                    role=role,
                    system=system_policy,
                    context=prompt,
                    messages=conversation_messages,
                    allowed_tools=interaction_tools,
                )
                if not isinstance(response, dict):
                    raise ValueError("LLM response must be a JSON object")
            except Exception as exc:
                last_result = {"status": "rejected", "failed_check": "llm_response", "message": str(exc)}
                context["last_platform_result"] = last_result
                transcript.append({"step": "llm_error", "error": str(exc)})
                context.setdefault("interaction_history", []).append({"kind": "llm_error", "error": str(exc)})
                self._audit(actor_id, role, task_type, iteration, prompt, {"error": str(exc)}, "llm_error", usage=self._last_usage)
                # A provider/configuration failure is outside the model's
                # ReAct loop. Retrying it immediately six times creates an
                # error storm; the scheduler will retry after backoff.
                return AgentTurn(role, task_type, last_response, last_result, iteration, transcript, initial_context, prompt_history)
            last_response = response
            transcript.append({"step": "llm", "response": response})
            self._audit(actor_id, role, task_type, iteration, prompt, response, "response", usage=self._last_usage)

            try:
                call = parse_tool_call(response)
            except (TypeError, ValueError) as exc:
                call = None
                last_result = {"status": "rejected", "failed_check": "tool_arguments", "message": str(exc)}
                context["last_platform_result"] = last_result
                transcript.append({"step": "tool_error", "error": str(exc)})
                continue

            if call is not None:
                native_tool_calls = response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None
                calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
                if native_tool_calls:
                    for native_call in native_tool_calls:
                        parsed = parse_tool_call({"tool_calls": [native_call]})
                        if parsed is not None:
                            calls.append((parsed[0], parsed[1], native_call))
                    conversation_messages.append({"role": "assistant", "tool_calls": native_tool_calls})
                else:
                    calls.append((call[0], call[1], None))
                accepted_result: dict[str, Any] | None = None
                for tool_name, arguments, native_call in calls:
                    tool_calls_used += 1
                    is_read_tool = TOOL_DEFINITIONS.get(tool_name, {}).get("kind") == "read"
                    if is_read_tool:
                        read_tool_calls += 1
                    signature_key = f"{tool_name}:{repr(sorted(arguments.items()))}"
                    repeated[signature_key] = repeated.get(signature_key, 0) + 1
                    if tool_calls_used > self.max_tool_calls:
                        tool_result = {"status": "rejected", "tool": tool_name, "failed_check": "tool_budget", "message": "Tool-call budget exhausted; submit one final Intent now."}
                    elif is_read_tool and read_tool_calls > self.max_read_tool_calls:
                        tool_result = {"status": "rejected", "tool": tool_name, "failed_check": "read_budget", "message": "Read budget exhausted; use the facts already collected and submit one final Intent."}
                    elif repeated[signature_key] > self.max_repeated_tool_calls:
                        tool_result = {"status": "rejected", "tool": tool_name, "failed_check": "repeated_tool_call", "message": "The same tool request was repeated; use the previous result or choose the previous result."}
                    elif interaction_tools is not None and tool_name not in interaction_tools:
                        tool_result = {
                            "status": "rejected",
                            "tool": tool_name,
                            "failed_check": "interaction_tool",
                            "retryable": True,
                            "message": "This interaction exposes only its direct reply or trade decision tools; return wait or use one listed tool.",
                        }
                    elif role == "god" and str(task_type).startswith("trigger:") and tool_name == "propose_world_change" and arguments.get("change_type") == "event" and not arguments.get("property"):
                        tool_result = {"status": "rejected", "tool": tool_name, "failed_check": "trigger_requires_state_change", "retryable": True, "message": "This trigger requires a direct state-changing proposal; a narrative-only event does not resolve it."}
                    else:
                        tool_result = executor.execute(tool_name, arguments)
                    model_tool_result = tool_result
                    internal_tool_result = self._restore_internal_tool_result(tool_name, arguments, tool_result)
                    transcript.append({"step": "tool", "name": tool_name, "arguments": arguments, "result": internal_tool_result})
                    context.setdefault("interaction_history", []).extend([
                        {"kind": "assistant_tool_call", "name": tool_name, "arguments": arguments},
                        {"kind": "tool_result", "name": tool_name, "result": model_tool_result},
                    ])
                    context.setdefault("tool_results", []).append({"tool": tool_name, "result": model_tool_result})
                    context["last_platform_result"] = model_tool_result
                    last_result = internal_tool_result
                    if native_call is not None:
                        conversation_messages.append({"role": "tool", "tool_call_id": native_call.get("id", "tool_call"), "name": tool_name, "content": json.dumps(model_tool_result, ensure_ascii=False)})
                    if TOOL_DEFINITIONS.get(tool_name, {}).get("kind") == "intent" and model_tool_result.get("status") in {"accepted", "wait"}:
                        accepted_result = internal_tool_result
                        # One session may batch read tools, but a decision or
                        # proposal is a single side effect. Ignore any later
                        # intent calls in the same model response.
                        if not is_read_tool:
                            break
                if native_tool_calls:
                    # The initial snapshot is already in the message list.
                    # Append only the tool result on each ReAct turn; sending
                    # another full snapshot here would duplicate dynamic
                    # context and destroy provider cache efficiency.
                    conversation_messages.append({"role": "user", "content": f"Tool results: {json.dumps(model_tool_result, ensure_ascii=False)}\nContinue the same task and submit the next tool call or final Intent."})
                else:
                    conversation_messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
                    conversation_messages.append({"role": "user", "content": f"Tool results: {json.dumps(model_tool_result, ensure_ascii=False)}"})
                if accepted_result is not None:
                    return AgentTurn(role, task_type, last_response, accepted_result, iteration, transcript, initial_context, prompt_history)
                continue

            result = self._submit_response(role, actor_id, response, context=context, task_type=task_type)
            transcript.append({"step": "intent", "response": response, "result": result})
            conversation_messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
            conversation_messages.append({"role": "user", "content": f"Platform result: {json.dumps(result, ensure_ascii=False)}"})
            context.setdefault("interaction_history", []).extend([
                {"kind": "assistant_intent", "intent": response},
                {"kind": "platform_result", "result": result},
            ])
            context["last_platform_result"] = result
            last_result = result
            if result.get("status") in {"accepted", "wait"}:
                return AgentTurn(role, task_type, response, result, iteration, transcript, initial_context, prompt_history)

        if last_result.get("status") not in {"accepted", "wait", "skipped"}:
            last_result = {
                **last_result,
                "status": "rejected",
                "failed_check": "budget_exhausted",
                "retryable": True,
                "message": "The ReAct session reached its iteration limit before a valid final Intent.",
            }
        return AgentTurn(role, task_type, last_response, last_result, self.max_iterations, transcript, initial_context, prompt_history)

    @staticmethod
    def _interaction_tools(task_type: str) -> list[str] | None:
        """Return the small tool surface for a zero-minute interaction.

        A reply to a message is not a new planning session.  It may answer,
        settle a trade offer, or wait; unrelated BT selection is deferred to
        the next ordinary decision boundary.  Keeping this contract explicit
        prevents the model from spending tokens reconsidering travel, food,
        and other long-running work while replying to one message.
        """

        task = str(task_type or "")
        if not task.startswith("instant:"):
            return None
        event_type = task.removeprefix("instant:")
        if event_type == "dialogue.completed":
            return ["send_message"]
        if event_type in {"trade.offer_created", "trade.countered"}:
            return ["accept_trade_offer", "counter_trade_offer", "reject_trade_offer"]
        return ["send_message"]

    @staticmethod
    def _compact_interaction_context(context: dict[str, Any], task_type: str, tools: list[str]) -> None:
        """Trim unrelated planning state from an instant interaction view.

        The complete decision view remains available for ordinary planning.
        Interaction replies only need the participant, the incoming testimony,
        relevant facts, open offers, and enough state to avoid an impossible
        response.  This keeps the model-facing prompt legible and lowers both
        latency and provider token use without changing Platform validation.
        """

        context["interaction_only"] = True
        context["tools"] = [item for item in (context.get("tools") or []) if item.get("name") in set(tools)]
        if "trade" in str(task_type):
            context["prompt_protocol"] = [
                "This is one bounded instant trade response.",
                "Choose exactly one listed trade decision or wait; Platform owns inventory and time.",
                "Use the exact visible offer choice and participant name; never output internal identifiers.",
            ]
        else:
            context["prompt_protocol"] = [
                "This is one bounded instant message response.",
                "Send one short reply only when the incoming message adds information or needs a decision; otherwise wait.",
                "Use the exact visible participant name; Platform owns every state change and world time.",
            ]
        context["platform_rules"] = [
            "Only the incoming interaction is being handled; do not start an unrelated behavior.",
            "A reply is at most two concise sentences. Repeating known information is a reason to wait.",
            "The Platform validates visibility, availability, inventory, and all state changes.",
        ]
        context["executable_behaviors"] = []
        context["available_behaviors"] = []
        context["recent_outcomes"] = []
        context["capability_history"] = []
        context["capability_update_candidates"] = []
        context["drives"] = []
        context["drive_model"] = {}
        context["identity"] = {"traits": {}, "beliefs": [], "capabilities": {}}
        context["recent_actions"] = (context.get("recent_actions") or [])[:1]
        context["known_facts"] = (context.get("known_facts") or [])[:4]
        context["nearby_entities"] = (context.get("nearby_entities") or [])[:4]
        context["direct_relationships"] = (context.get("direct_relationships") or [])[:3]
        context["short_term_memories"] = (context.get("short_term_memories") or [])[:4]
        context["long_term_memories"] = (context.get("long_term_memories") or [])[:2]
        context["memories"] = (context.get("memories") or [])[:6]
        context["decision_constraints"] = []
        if "trade" not in str(task_type):
            context["open_trade_offers"] = []
        context["interaction_history"] = []
        context.setdefault("interaction", {})

    def _restore_internal_tool_result(self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """Keep internal audit details on AgentTurn without exposing them to the model."""

        if tool_name != "propose_entity" or result.get("status") != "accepted" or result.get("entity_id"):
            return result
        name = str(arguments.get("name") or "").strip()
        if not name:
            return result
        row = self.platform.store.connection.execute(
            "SELECT id FROM entity WHERE name=? ORDER BY rowid DESC LIMIT 1", (name,)
        ).fetchone()
        if row is None:
            return result
        restored = dict(result)
        restored["entity_id"] = row["id"]
        return restored

    @staticmethod
    def _task_text(role: str, task_type: str) -> str:
        if role == "agent_decision":
            if task_type.startswith("instant:"):
                if task_type == "instant:dialogue.completed":
                    return (
                        f"Task type: {task_type}. Reply to the incoming message only when it adds new information, "
                        "asks a clear question, or requires a decision. Send one short reply of at most two sentences; "
                        "if it repeats known information or needs no response, return wait. Do not start a behavior or advance world time."
                    )
                return f"Task type: {task_type}. Resolve the incoming trade interaction with one accept, counter, reject, or wait. Do not start a behavior or advance world time."
            return f"Task type: {task_type}. Select the best available behavior for the current goal, or wait if none is appropriate."
        if role == "agent_reflection":
            return f"Task type: {task_type}. Review the latest behavior outcome and existing memories. Submit one new durable lesson or evolution proposal only if it improves a future decision; otherwise wait."
        if role == "god":
            if str(task_type).startswith("trigger:"):
                return f"Task type: {task_type}. A world trigger is active. Submit one validated direct state-changing proposal that resolves the trigger; a narrative-only event does not resolve it. State the short observable expected_effects."
            return f"Task type: {task_type}. Review the world signal and submit one validated world proposal if needed. State the short observable expected_effects."
        if role == "story":
            if str(task_type).startswith("milestone:"):
                return f"Task type: {task_type}. Review the validated civilization milestone. Record configured public progress only when the evidence supports it, then activate one configured story beat if its evidence is present; otherwise wait. Platform owns every state change."
            return f"Task type: {task_type}. Review newly recorded discoveries and choose one configured story beat that is now unlocked; otherwise wait. Platform applies only the configured beat consequences."
        return f"Task type: {task_type}. Produce or refine one declarative world template."

    def _submit_response(self, role: str, actor_id: str, response: dict[str, Any], *, context: dict[str, Any] | None = None, task_type: str = "") -> dict[str, Any]:
        data = response.get("intent") if isinstance(response.get("intent"), dict) else dict(response)
        if not isinstance(data, dict):
            return {"status": "rejected", "failed_check": "intent_schema", "retryable": True, "message": "Intent must be an object."}
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
        if not intent_type_value and role == "story" and (data.get("beat") or data.get("summary")):
            intent_type_value = "propose_story_beat"
        if not intent_type_value and isinstance(data.get("intent"), str):
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
                "propose_world_change": "propose_world_change",
                "propose_story_beat": "propose_story_beat",
                "wait": "wait",
            }
            intent_type_value = aliases.get(str(data.get("intent")).casefold())
        # A reflection response containing only a concise lesson is an
        # unambiguous record-experience proposal.  Use the latest visible
        # outcome as its audit reference; the Platform still performs all
        # duplicate and length checks.
        if role == "agent_reflection" and not intent_type_value and data.get("summary"):
            intent_type_value = "record_experience"
            recent_outcomes = (context or {}).get("recent_outcomes") or []
            if recent_outcomes:
                latest = recent_outcomes[0]
                data.setdefault("event_ref", f"{latest.get('event_type', 'behavior.outcome')} at world minute {latest.get('world_minute', 'unknown')}")
            else:
                data.setdefault("event_ref", "reflection")
        intent_type = str(intent_type_value or ("select_behavior" if data.get("choice") or data.get("behavior_ref") or data.get("behavior_id") or data.get("behavior") else ""))
        if data.get("wait") is True:
            intent_type = "wait"
        if intent_type not in ROLE_INTENTS.get(role, set()):
            return {"status": "rejected", "failed_check": "intent_type", "retryable": True, "message": "This intent type is not allowed for the current role."}
        interaction_allowed = self._interaction_intents(task_type)
        if interaction_allowed is not None and intent_type not in interaction_allowed:
            return {
                "status": "rejected",
                "failed_check": "interaction_intent_type",
                "retryable": True,
                "message": "This interaction accepts only a direct reply or the listed trade decision; choose wait when no response is needed.",
            }
        if intent_type == "wait":
            return {"status": "wait", "reason": str(data.get("reason") or data.get("message") or "No suitable behavior now.")}
        try:
            if intent_type == "select_behavior":
                # The model-facing contract uses a visible choice name.  The
                # canonical ref is resolved from the current Platform view
                # immediately before submission and never guessed by the LLM.
                if data.get("choice") and not data.get("behavior_ref"):
                    behavior, error = self.view.resolve_behavior_choice(actor_id, data.get("choice"))
                    if error:
                        return error
                    data = dict(data)
                    data["behavior_ref"] = behavior["ref"]
                return self.platform.submit_behavior(BehaviorIntent.from_json(data, actor_id=actor_id))
            if intent_type == "send_message":
                return self.platform.submit_dialogue(actor_id, str(data.get("recipient") or data.get("target_id") or ""), str(data.get("topic") or "general"), str(data.get("message") or ""))
            if intent_type == "create_trade_offer":
                return self.platform.create_trade_offer(actor_id, str(data.get("recipient") or data.get("recipient_id") or ""), dict(data.get("give") or {}), dict(data.get("want") or {}))
            if intent_type == "accept_trade_offer":
                offer, error = self.view.resolve_trade_offer_choice(actor_id, data.get("offer") or data.get("offer_id"))
                if error:
                    return error
                return self.platform.accept_trade_offer(actor_id, str(offer["offer_id"]))
            if intent_type == "counter_trade_offer":
                offer, error = self.view.resolve_trade_offer_choice(actor_id, data.get("offer") or data.get("offer_id"))
                if error:
                    return error
                return self.platform.counter_trade_offer(actor_id, str(offer["offer_id"]), dict(data.get("give") or {}), dict(data.get("want") or {}))
            if intent_type == "reject_trade_offer":
                offer, error = self.view.resolve_trade_offer_choice(actor_id, data.get("offer") or data.get("offer_id"))
                if error:
                    return error
                return self.platform.reject_trade_offer(actor_id, str(offer["offer_id"]))
            if intent_type == "record_experience":
                if role == "agent_reflection":
                    if not self._reflection_has_evidence(context or {}):
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
                return self.platform.submit_capability_update(actor_id, str(data.get("capability") or ""), float(data.get("delta", 0)), str(data.get("evidence") or ""))
            if intent_type == "record_world_progress":
                return self.platform.submit_world_progress(actor_id, str(data.get("progress_key") or ""), float(data.get("delta", 0)), str(data.get("evidence") or ""))
            if intent_type == "update_belief":
                return self.platform.submit_belief_update(actor_id, "", str(data.get("statement") or data.get("belief") or ""), float(data.get("confidence", 0.0)), str(data.get("reason") or ""))
            if intent_type == "update_goal":
                return self.platform.submit_goal_update(actor_id, "ultimate_goal", str(data.get("statement") or data.get("goal") or ""), str(data.get("status") or "active"), float(data.get("priority", 1.0)))
            if intent_type == "propose_behavior":
                proposal = dict(data)
                proposal.pop("intent_type", None)
                if not proposal.get("action_ids") and proposal.get("actions"):
                    proposal["action_ids"] = self._resolve_action_names(proposal.pop("actions"))
                proposal["behavior_id"] = ""
                proposal["agent_label"] = str(proposal.pop("name", "") or "")
                return self.platform.submit_behavior_proposal(actor_id, proposal)
            if intent_type == "propose_entity":
                proposal = dict(data)
                proposal.pop("intent_type", None)
                proposal["entity_id"] = ""
                if proposal.get("location") and not proposal.get("location_id"):
                    location = proposal.pop("location")
                    proposal["location_id"] = self.platform.targets.canonical_id(location)
                    if not proposal["location_id"]:
                        return {"status": "rejected", "failed_check": "location_name", "retryable": True, "message": "Choose an exact visible location name."}
                return self.platform.submit_entity_proposal(actor_id, proposal)
            if intent_type == "propose_world_change":
                proposal = dict(data)
                proposal.pop("intent_type", None)
                if str(task_type).startswith("trigger:") and proposal.get("change_type") == "event" and not proposal.get("property"):
                    return {
                        "status": "rejected",
                        "failed_check": "trigger_requires_state_change",
                        "retryable": True,
                        "message": "This trigger requires a direct state-changing proposal; a narrative-only event does not resolve it.",
                    }
                result = self.platform.submit_world_change(actor_id, proposal)
                return result
            if intent_type == "propose_story_beat":
                proposal = dict(data)
                proposal.pop("intent_type", None)
                return self.platform.submit_story_beat(actor_id, proposal)
            if intent_type == "submit_world_template":
                return self.platform.submit_world_template(actor_id, data.get("template") or {})
        except (TypeError, ValueError, KeyError) as exc:
            return {"status": "rejected", "failed_check": "intent_schema", "message": str(exc)}
        return {"status": "rejected", "failed_check": "intent_type", "message": "Intent was not handled."}

    @staticmethod
    def _interaction_intents(task_type: str) -> set[str] | None:
        task = str(task_type or "")
        if not task.startswith("instant:"):
            return None
        event_type = task.removeprefix("instant:")
        if event_type == "dialogue.completed":
            return {"send_message", "wait"}
        if event_type in {"trade.offer_created", "trade.countered"}:
            return {"accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "wait"}
        return {"send_message", "wait"}

    def _resolve_action_names(self, actions: Any) -> list[str]:
        definitions = self.platform.store.get_definitions("action_def")
        resolved: list[str] = []
        for value in list(actions or []):
            key = "".join(char for char in str(value).casefold() if char.isalnum())
            matches = []
            for item in definitions:
                candidates = {
                    "".join(char for char in str(item.get("id", "")).casefold() if char.isalnum()),
                    "".join(char for char in str(item.get("description", "")).casefold() if char.isalnum()),
                }
                if key in candidates:
                    matches.append(str(item["id"]))
            if len(matches) == 1:
                resolved.append(matches[0])
        return resolved

    def _audit(
        self,
        actor_id: str,
        role: str,
        task_type: str,
        iteration: int,
        context: str,
        response: dict[str, Any],
        status: str,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Persist a redacted-size response and a context digest for replay."""

        try:
            digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
            # Keep audit rows bounded even when a provider accidentally emits a
            # very large object.  The full model context is reconstructed from
            # the instance and the digest still detects replay drift.
            encoded = json.dumps(response, ensure_ascii=False)
            if len(encoded) > 16_000:
                response = {"truncated": True, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
            static_prefix = context.split("<world_state>", 1)[0]
            dynamic_text = context[len(static_prefix):]
            prefix_hash = hashlib.sha256(static_prefix.encode("utf-8")).hexdigest()
            self.platform.store.append_llm_call(
                actor_id,
                role,
                task_type,
                iteration,
                digest,
                response,
                status,
                usage=usage or {},
                static_prefix_hash=prefix_hash,
                static_prefix_chars=len(static_prefix),
                dynamic_chars=len(dynamic_text),
            )
        except Exception:
            # Observability must never turn a valid Intent into a failed turn.
            return
