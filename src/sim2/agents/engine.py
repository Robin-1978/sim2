from __future__ import annotations

import copy
import json
from typing import Any

from ..agent_view.validator import validate_agent_view
from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from ..prompting.renderer import render_context
from .runtime_policy import (
    ROLE_INTENTS, ROLE_NAMES, configured_evolution_group, configured_evolution_trigger,
    configured_role_tools, role_policy,
)
from .policies import task_text
from .intent_gateway import AgentIntentGateway
from .audit import AgentAudit
from .protocol import ProviderProtocol, normalize_provider_intent, readable_model_response
from .session import AgentTurn, model_tool_result, trim_conversation_messages
from .tools import AgentToolExecutor, TOOL_DEFINITIONS, parse_tool_call


class AgentEngine:
    """Run one bounded ReAct session for any supported agent role.

    A session is independent of simulation minutes.  Each provider call either
    returns a tool request or a final Intent.  Tool results and Platform errors
    are appended to the compact context, so the model can correct its next
    request.  Once an Intent is accepted, the session ends; a long action is
    subsequently advanced by Platform rather than by another LLM call.
    """

    def __init__(self, platform: Platform, provider: Any, max_iterations: int | None = None, max_repeated_tool_calls: int = 2, max_tool_calls: int = 12, max_read_tool_calls: int | None = None):
        self.platform = platform
        self.provider = provider
        configured_iterations = getattr(self.provider, "max_agent_iterations", 4)
        self.max_iterations = max(1, int(configured_iterations if max_iterations is None else max_iterations))
        self.max_repeated_tool_calls = max(1, max_repeated_tool_calls)
        self.max_tool_calls = max(1, max_tool_calls)
        configured_reads = getattr(self.provider, "max_read_tool_calls", 8)
        self.max_read_tool_calls = max(0, int(configured_reads if max_read_tool_calls is None else max_read_tool_calls))
        # Reuse the Platform-owned compiler and MemoryService. Creating a new
        # compiler for every session discards the in-process query embedding
        # cache and repeats BGE inference for identical goal/location views.
        self.view = platform.view
        self._last_usage: dict[str, Any] = {}
        self.audit = AgentAudit(platform)
        self.protocol = ProviderProtocol(provider)
        self.intent_gateway = AgentIntentGateway(platform)

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

        reply = self.protocol.complete(
            role=role,
            system=system,
            context=context,
            messages=messages,
            allowed_tools=allowed_tools,
        )
        self._last_usage = dict(reply.usage)
        return reply.payload



    @staticmethod
    def _read_signature(tool_name: str, arguments: dict[str, Any]) -> str:
        """Normalize a read request so reworded repeats are visible to the guard."""

        name = str(tool_name)
        if name in {"observe_nearby", "listen"}:
            return name
        if name == "inspect_entity":
            return f"{name}:{str(arguments.get('entity') or '').casefold().strip()}"
        if name == "read_public_record":
            return f"{name}:{str(arguments.get('record') or '').casefold().strip()}"
        if name == "preview_behavior":
            return f"{name}:{str(arguments.get('choice') or '').casefold().strip()}"
        normalized = {
            str(key): " ".join(str(value).casefold().split())
            for key, value in sorted(arguments.items())
            if key != "question"
        }
        return f"{name}:{json.dumps(normalized, ensure_ascii=False, sort_keys=True)}"

    def run(
        self,
        *,
        role: str,
        actor_id: str,
        task_type: str = "decision",
        interaction: dict[str, Any] | None = None,
        source_event_id: int | None = None,
        source_events: list[dict[str, Any]] | None = None,
    ) -> AgentTurn:
        if role not in ROLE_NAMES:
            raise ValueError(f"unknown agent role: {role}")
        if role == "agent_decision" and not str(task_type).startswith("instant:"):
            try:
                state = self.platform.store.repositories.entities.get_agent(actor_id).get("initial_state", {})
            except KeyError:
                state = {}
            if state.get("status") in {"executing", "paused"}:
                result = {"status": "skipped", "reason": "active_behavior", "message": "Platform is already executing a behavior; wait for completion or an interrupt."}
                return AgentTurn(role, task_type, {}, result, 0, [{"step": "skipped", "result": result}], {}, [])
        context = self.view.build_decision_view(actor_id, role=role, source_event_id=source_event_id)
        context["role"] = role
        trigger_config = configured_evolution_trigger(self.platform, task_type)
        group_contract = configured_evolution_group(self.platform, task_type)
        trigger_contract = ((trigger_config or {}).get("agent_contract") if isinstance(trigger_config, dict) else None) or group_contract
        if isinstance(trigger_contract, dict):
            context["evolution_trigger"] = {
                "id": str((trigger_config or {}).get("id") or (task_type if group_contract else "")),
                "condition": str((trigger_config or {}).get("when") or "configured evidence group"),
                **dict(trigger_contract),
            }
            if trigger_contract.get("evidence_sources"):
                context["evolution_trigger"]["observed_evidence"] = self.platform.count_verified_evidence(trigger_contract.get("evidence_sources"))
        event_refs = list(source_events or [])
        if source_event_id is not None and not any(
            int(item.get("event_id", -1) or -1) == int(source_event_id)
            for item in event_refs if isinstance(item, dict)
        ):
            event_refs.append({"event_id": int(source_event_id)})
        event_ids = [
            int(item.get("event_id", 0) or 0)
            for item in event_refs
            if isinstance(item, dict) and int(item.get("event_id", 0) or 0) > 0
        ]
        if event_ids:
            verified_events = self.platform.store.repositories.events.events_by_ids(event_ids)
            context["triggering_evidence"] = []
            if "recent_history" in context:
                # The validated trigger is shown once as evidence; repeating
                # it in the history would spend prompt budget on the same text.
                shown = {int(item.get("event_id", 0) or 0) for item in verified_events[-16:]}
                context["recent_history"] = [
                    item for item in context["recent_history"] if int(item.get("event_id", 0) or 0) not in shown
                ]
            for item in verified_events[-16:]:
                evidence = {
                    "event_type": item.get("event_type"),
                    "world_minute": item.get("world_minute"),
                    "actor": self._visible_actor_name(item.get("actor_id")),
                    "summary": item.get("summary") or item.get("result") or item.get("fact") or "validated event recorded",
                }
                if item.get("source_id"):
                    evidence["research_source"] = self._visible_entity_name(item.get("source_id"))
                if item.get("stage_id"):
                    evidence["research_stage"] = item.get("stage_id")
                context["triggering_evidence"].append(evidence)
            if verified_events:
                context["last_platform_result"] = verified_events[-1]
        if interaction:
            context["interaction"] = dict(interaction)
        interaction_tools = self._interaction_tools(task_type)
        role_tools = configured_role_tools(self.platform, role)
        trigger_tools = None
        if isinstance(trigger_contract, dict) and isinstance(trigger_contract.get("allowed_tools"), list):
            trigger_tools = [str(item) for item in trigger_contract.get("allowed_tools", []) if str(item)]
        if interaction_tools is not None:
            self._compact_interaction_context(context, task_type, interaction_tools)
        session_tools = interaction_tools if interaction_tools is not None else trigger_tools if trigger_tools is not None else role_tools
        if session_tools is not None:
            context["tools"] = [item for item in (context.get("tools") or []) if item.get("name") in set(session_tools)]
        # Event tasks carry the exact validated source. They still may read a
        # small, budgeted amount of related world state: a God reviewer often
        # needs one read to see what the world lacks before choosing a
        # declarative proposal. The read budget below is the only cap.
        view_errors = validate_agent_view(context, role=role)
        if view_errors:
            raise ValueError("invalid AgentView: " + "; ".join(view_errors))
        if role == "agent_reflection" and task_type in {"daily_reflection", "integration_verification"} and not self.intent_gateway.reflection_has_evidence(context):
            result = {"status": "wait", "reason": "No completed outcome or meaningful event is available for reflection."}
            return AgentTurn(role, task_type, {}, result, 0, [{"step": "guard", "result": result}], {}, [])
        task = task_text(role, task_type)
        executor = AgentToolExecutor(
            self.platform,
            role=role,
            actor_id=actor_id,
            source_event_ids=event_ids,
            observed_world_revision=context.get("world_revision"),
            observed_agent_revision=context.get("agent_revision"),
            evolution_contract=trigger_contract if isinstance(trigger_contract, dict) else None,
        )
        last_response: dict[str, Any] = {}
        last_result: dict[str, Any] = {"status": "no_result"}
        transcript: list[dict[str, Any]] = []
        initial_context = copy.deepcopy(context)
        prompt_history: list[str] = []
        repeated: dict[str, int] = {}
        read_signatures: set[str] = set()
        tool_calls_used = 0
        read_tool_calls = 0
        # World-level reviewers must make a bounded decision after a small
        # evidence sample.  Without a role-specific cap, remote models can
        # spend all six ReAct turns reading the same event from different
        # projections and leave the evolution task unresolved.
        # The read budget is role configuration. A bounded world reviewer may
        # need several focused reads before its final typed proposal.
        read_budget = self.max_read_tool_calls
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
                call_tools = session_tools
                if role == "agent_decision" and not str(task_type).startswith("instant:") and read_tool_calls >= read_budget:
                    available = session_tools if session_tools is not None else [str(item.get("name")) for item in (context.get("tools") or [])]
                    call_tools = [name for name in available if TOOL_DEFINITIONS.get(name, {}).get("kind") != "read"]
                response = self._complete(
                    role=role,
                    system=system_policy,
                    context=prompt,
                    messages=conversation_messages,
                    allowed_tools=call_tools,
                )
                if not isinstance(response, dict):
                    raise ValueError("LLM response must be a JSON object")
            except Exception as exc:
                self._last_usage = dict(getattr(exc, "usage", {}) or self._last_usage)
                last_result = {"status": "rejected", "failed_check": "llm_response", "message": str(exc)}
                context["last_platform_result"] = last_result
                transcript.append({"step": "llm_error", "error": str(exc)})
                context.setdefault("interaction_history", []).append({"kind": "llm_error", "error": str(exc)})
                self.audit.record(actor_id, role, task_type, iteration, prompt, {"error": str(exc)}, "llm_error", usage=self._last_usage)
                # A provider/configuration failure is outside the model's
                # ReAct loop. Retrying it immediately six times creates an
                # error storm; the scheduler will retry after backoff.
                return AgentTurn(role, task_type, last_response, last_result, iteration, transcript, initial_context, prompt_history)
            last_response = response
            readable_response = self._readable_model_response(response)
            last_response = readable_response
            transcript.append({"step": "llm", "response": readable_response})
            self.audit.record(actor_id, role, task_type, iteration, prompt, readable_response, "response", usage=self._last_usage)

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
                    normalized_native_calls: list[dict[str, Any]] = []
                    for native_call in native_tool_calls:
                        native_call = dict(native_call)
                        # A malformed compatible endpoint may omit the native
                        # correlation id. Generate a session-local protocol
                        # id so multiple results cannot collide. This is not a
                        # world/entity id and is never used for Platform state.
                        if not str(native_call.get("id") or "").strip():
                            native_call["id"] = f"platform-tool-{iteration}-{len(normalized_native_calls)}"
                        normalized_native_calls.append(native_call)
                        parsed = parse_tool_call({"tool_calls": [native_call]})
                        if parsed is not None:
                            calls.append((parsed[0], parsed[1], native_call))
                    conversation_messages.append({"role": "assistant", "tool_calls": normalized_native_calls})
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
                    elif is_read_tool and read_tool_calls > read_budget:
                        tool_result = {"status": "rejected", "tool": tool_name, "failed_check": "read_budget", "message": "Read budget exhausted; use the facts already collected and submit one final Intent."}
                    elif is_read_tool and self._read_signature(tool_name, arguments) in read_signatures:
                        tool_result = {"status": "rejected", "tool": tool_name, "failed_check": "repeated_read", "retryable": True, "message": "This scene, entity or behavior was already read in this session. Use the existing result and submit a behavior or wait."}
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
                    elif session_tools is not None and tool_name not in session_tools:
                        tool_result = {
                            "status": "rejected",
                            "tool": tool_name,
                            "failed_check": "task_tool",
                            "retryable": True,
                            "message": "This event task already includes its validated evidence. Use one listed submission tool or return wait.",
                        }
                    elif role == "god" and (str(task_type).startswith("trigger:") or str(task_type).startswith("event:")) and tool_name == "propose_world_change" and arguments.get("change_type") == "event" and not arguments.get("property"):
                        tool_result = {"status": "rejected", "tool": tool_name, "failed_check": "event_already_recorded", "retryable": True, "message": "The triggering event is already recorded. Do not add a narrative acknowledgement; return wait or submit one typed rule, progress, entity, relationship, or faction proposal grounded in the observed result."}
                    else:
                        tool_result = executor.execute(tool_name, arguments)
                    if is_read_tool and tool_result.get("status") == "ok":
                        read_signatures.add(self._read_signature(tool_name, arguments))
                        if role == "agent_decision":
                            context["decision_guidance"] = f"{read_tool_calls} focused read(s) completed. Use those results now; choose one listed behavior or wait."
                    model_result = model_tool_result(tool_name, tool_result)
                    internal_tool_result = self._restore_internal_tool_result(tool_name, arguments, tool_result)
                    transcript.append({
                        "step": "tool",
                        "name": tool_name,
                        "arguments": arguments,
                        # ``result`` is private Platform output; the model
                        # receives only ``model_result`` below.  Keep one raw
                        # copy for provenance instead of duplicating it under
                        # two transcript keys in every audit artifact.
                        "model_result": model_result,
                        "result": internal_tool_result,
                    })
                    context.setdefault("interaction_history", []).extend([
                        {"kind": "assistant_tool_call", "name": tool_name, "arguments": arguments},
                        {"kind": "tool_result", "name": tool_name, "result": model_result},
                    ])
                    context.setdefault("tool_results", []).append({"tool": tool_name, "result": model_result})
                    context["last_platform_result"] = model_result
                    last_result = internal_tool_result
                    if native_call is not None:
                        conversation_messages.append({"role": "tool", "tool_call_id": native_call.get("id", "tool_call"), "name": tool_name, "content": json.dumps(model_result, ensure_ascii=False)})
                    if TOOL_DEFINITIONS.get(tool_name, {}).get("kind") == "intent" and model_result.get("status") in {"accepted", "wait"}:
                        accepted_result = internal_tool_result
                        # One session may batch read tools, but a decision or
                        # proposal is a single side effect. Ignore any later
                        # intent calls in the same model response.
                        if not is_read_tool:
                            break
                if native_tool_calls:
                    # Native tool messages already contain every result.  An
                    # extra user copy is redundant and can invalidate strict
                    # providers when a response contains multiple calls.
                    pass
                else:
                    conversation_messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
                    conversation_messages.append({"role": "user", "content": f"Tool results: {json.dumps(model_result, ensure_ascii=False)}"})
                # Keep the stable system prompt and initial snapshot, plus a
                # bounded tail of the current ReAct exchange.  Without this
                # cap every read result is resent on every iteration, so a
                # short choice can accumulate a large prompt and latency.
                if len(conversation_messages) > 8:
                    conversation_messages = trim_conversation_messages(conversation_messages, max_tail=6)
                if accepted_result is not None:
                    return AgentTurn(role, task_type, last_response, accepted_result, iteration, transcript, initial_context, prompt_history)
                continue

            result = self.intent_gateway.submit(
                role,
                actor_id,
                response,
                context=context,
                task_type=task_type,
                source_event_ids=event_ids,
            )
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

    def _visible_actor_name(self, actor_id: Any) -> str:
        if not actor_id:
            return "the world"
        try:
            return str(self.platform.store.repositories.entities.get_entity(str(actor_id)).get("name") or actor_id)
        except KeyError:
            return str(actor_id)

    def _visible_entity_name(self, entity_id: Any) -> str | None:
        if not entity_id:
            return None
        try:
            return str(self.platform.store.repositories.entities.get_entity(str(entity_id)).get("name") or entity_id)
        except KeyError:
            return str(entity_id)

    @staticmethod
    def _trim_conversation_messages(messages: list[dict[str, Any]], *, max_tail: int = 6) -> list[dict[str, Any]]:
        """Keep a bounded tail without orphaning native tool results.

        OpenAI-compatible protocols require each ``tool`` message to follow
        the assistant message that declared its correlation ID.  Cropping a
        raw message slice can remove that declaration and make the next HTTP
        request invalid.  Remove whole assistant/tool groups instead.
        """

        return trim_conversation_messages(messages, max_tail=max_tail)

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
            return ["send_message", "wait"]
        if event_type in {"trade.offer_created", "trade.countered"}:
            return ["accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "wait"]
        return ["send_message", "wait"]

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
                "State concrete facts only when they appear in the incoming message, known facts, or retained memory. A rumor label has no unstated details; ask a question or say the detail is unknown instead of inventing it.",
                "Never invent sensory or operational details about a route, bridge, building, item, weather, material, timing, quantity, or cause. If those details are absent, say they are unknown.",
                "A message may propose or promise terms, but it cannot transfer items or currency, collect payment, sign a contract, or complete a trade. Describe those outcomes as pending until a Platform transaction confirms them.",
                "Use the exact visible participant name; Platform owns every state change and world time.",
            ]
        context["platform_rules"] = [
            "Only the incoming interaction is being handled; do not start an unrelated behavior.",
            "A reply is at most two concise sentences. Repeating known information is a reason to wait.",
            "Do not add a new name, number, appearance, location, cause, possession, or event unless it is visible in this context.",
            "Do not embellish a known fact with invented physical conditions, travel times, materials, quantities, or weather.",
            "send_message records testimony only. Never say payment, delivery, ownership, a signed contract, or a trade has completed without a visible Platform result confirming it.",
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
    def _readable_model_response(response: dict[str, Any]) -> dict[str, Any]:
        """Decode nested native-tool argument JSON for audit and operator views.

        OpenAI-compatible responses carry ``function.arguments`` as a JSON
        string, so non-ASCII names appear as ``\\uXXXX`` in a raw audit row.
        The provider still receives the original wire shape; only the stored
        AgentTurn/audit copy is made human-readable.
        """

        return readable_model_response(response)
