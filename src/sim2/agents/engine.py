from __future__ import annotations

import inspect
import hashlib
import json
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


ROLE_NAMES = {"agent_decision", "agent_reflection", "god", "world_builder"}


def role_policy(platform: Platform, role: str) -> str:
    prompt = platform.store.get_prompt("agent", {}) or {}
    prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
    policies = prompt.get("role_policies", {}) if isinstance(prompt, dict) else {}
    return str(policies.get(role) or "")

ROLE_INTENTS = {
    "agent_decision": {"select_behavior", "send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "wait"},
    "agent_reflection": {"record_experience", "update_belief", "update_goal", "propose_behavior", "wait"},
    "god": {"propose_entity", "propose_world_change", "wait"},
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

    def _complete(self, *, role: str, system: str, context: str, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Call old test providers and native tool-capable providers alike."""

        method = self.provider.complete_json
        kwargs: dict[str, Any] = {"system": system, "context": context}
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "tools" in parameters:
            kwargs["tools"] = tool_schemas_for_role(role)
        if "role" in parameters:
            kwargs["role"] = role
        if "messages" in parameters:
            kwargs["messages"] = messages
        response = method(**kwargs)
        self._last_usage = dict(response.pop("_usage", {}) or {}) if isinstance(response, dict) else {}
        return response

    def run(self, *, role: str, actor_id: str, task_type: str = "decision") -> AgentTurn:
        if role not in ROLE_NAMES:
            raise ValueError(f"unknown agent role: {role}")
        if role == "agent_decision":
            try:
                state = self.platform.store.get_agent(actor_id).get("initial_state", {})
            except KeyError:
                state = {}
            if state.get("status") in {"executing", "paused"}:
                result = {"status": "skipped", "reason": "active_behavior", "message": "Platform is already executing a behavior; wait for completion or an interrupt."}
                return AgentTurn(role, task_type, {}, result, 0, [{"step": "skipped", "result": result}])
        context = self.view.build_decision_view(actor_id, role=role)
        context["role"] = role
        view_errors = validate_agent_view(context, role=role)
        if view_errors:
            raise ValueError("invalid AgentView: " + "; ".join(view_errors))
        task = self._task_text(role, task_type)
        executor = AgentToolExecutor(self.platform, role=role, actor_id=actor_id)
        last_response: dict[str, Any] = {}
        last_result: dict[str, Any] = {"status": "no_result"}
        transcript: list[dict[str, Any]] = []
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
            try:
                self._last_usage = {}
                response = self._complete(role=role, system=system_policy, context=prompt, messages=conversation_messages)
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
                return AgentTurn(role, task_type, last_response, last_result, iteration, transcript)
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
                if native_tool_calls:
                    # Native tool results are not enough by themselves: feed
                    # the compact, updated world view back as the next user
                    # message so the model can close the decision loop.
                    conversation_messages.append({"role": "user", "content": render_context(context, task)})
                else:
                    conversation_messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
                    conversation_messages.append({"role": "user", "content": f"Tool results: {json.dumps(model_tool_result, ensure_ascii=False)}"})
                if accepted_result is not None:
                    return AgentTurn(role, task_type, last_response, accepted_result, iteration, transcript)
                continue

            result = self._submit_response(role, actor_id, response)
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
                return AgentTurn(role, task_type, response, result, iteration, transcript)

        return AgentTurn(role, task_type, last_response, last_result, self.max_iterations, transcript)

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
                return f"Task type: {task_type}. Review the incoming instant interaction and choose one response, trade decision, behavior, or wait. Do not advance world time."
            return f"Task type: {task_type}. Select the best available behavior for the current goal, or wait if none is appropriate."
        if role == "agent_reflection":
            return f"Task type: {task_type}. Review the latest behavior outcome and existing memories. Submit one new durable lesson or evolution proposal only if it improves a future decision; otherwise wait."
        if role == "god":
            return f"Task type: {task_type}. Review the world signal and submit one validated world proposal if needed."
        return f"Task type: {task_type}. Produce or refine one declarative world template."

    def _submit_response(self, role: str, actor_id: str, response: dict[str, Any]) -> dict[str, Any]:
        data = response.get("intent") if isinstance(response.get("intent"), dict) else dict(response)
        if not isinstance(data, dict):
            return {"status": "rejected", "failed_check": "intent_schema", "retryable": True, "message": "Intent must be an object."}
        if data.get("type") == "intent":
            data.pop("type", None)
        intent_type_value = data.get("intent_type") or data.get("kind")
        if not intent_type_value and role != "agent_decision" and isinstance(data.get("intent"), str):
            intent_type_value = data.get("intent")
        intent_type = str(intent_type_value or ("select_behavior" if data.get("choice") or data.get("behavior_ref") or data.get("behavior_id") or data.get("behavior") else ""))
        if data.get("wait") is True:
            intent_type = "wait"
        if intent_type not in ROLE_INTENTS.get(role, set()):
            return {"status": "rejected", "failed_check": "intent_type", "retryable": True, "message": "This intent type is not allowed for the current role."}
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
                return self.platform.submit_experience(actor_id, str(data.get("event") or data.get("event_ref") or ""), str(data.get("summary") or ""), float(data.get("confidence", 1.0)))
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
                return self.platform.submit_world_change(actor_id, proposal)
            if intent_type == "submit_world_template":
                return self.platform.submit_world_template(actor_id, data.get("template") or {})
        except (TypeError, ValueError, KeyError) as exc:
            return {"status": "rejected", "failed_check": "intent_schema", "message": str(exc)}
        return {"status": "rejected", "failed_check": "intent_type", "message": "Intent was not handled."}

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
