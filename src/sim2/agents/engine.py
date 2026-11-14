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

    @staticmethod
    def _reflection_reason_is_decline(reason: Any) -> bool:
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
            "no new lesson", "no new durable", "no durable lesson", "nothing new", "no useful lesson",
            "already recorded", "already known", "no change to record",
            "没有新的经验", "没有新的教训", "无需记录", "没有值得保留",
        )
        return any(marker in text for marker in markers)

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
        if role == "agent_reflection" and task_type in {"daily_reflection", "integration_verification"} and not self._reflection_has_evidence(context):
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

            result = self._submit_response(
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

    def _submit_response(
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
        data = self._normalize_provider_intent(role, data)
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
            elif data.get("definition_type") and data.get("definition") and data.get("name"):
                intent_type_value = "propose_definition"
        if not intent_type_value and role == "story" and (data.get("beat") or data.get("summary")):
            intent_type_value = "propose_story_beat"
        command_value = next(
            (data.get(key) for key in ("intent", "decision", "action") if isinstance(data.get(key), str)),
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
                data.setdefault("event_ref", f"{latest.get('event_type', 'behavior.outcome')} at world minute {latest.get('world_minute', 'unknown')}")
            else:
                data.setdefault("event_ref", "reflection")
        intent_type = str(intent_type_value or ("select_behavior" if data.get("choice") or data.get("behavior_ref") or data.get("behavior_id") or data.get("behavior") else ""))
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
            and not any(data.get(key) for key in ("message", "recipient", "offer", "give", "want"))
        ):
            intent_type = "wait"
        if (
            not intent_type
            and role == "agent_decision"
            and not (context or {}).get("executable_behaviors")
            and str(data.get("reason") or "").strip()
            and not any(data.get(key) for key in ("choice", "behavior", "behavior_ref", "message", "recipient", "offer", "give", "want"))
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
            and not any(data.get(key) for key in ("summary", "statement", "belief", "goal", "capability", "definition"))
            and self._reflection_reason_is_decline(data.get("reason"))
        ):
            # A reflection model may answer with only a short explanation
            # that no durable lesson is new. Treat that as the explicit
            # low-value ``wait`` outcome instead of retrying the same empty
            # response until the ReAct budget is exhausted.
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
                data = dict(data)
                if isinstance(context, dict):
                    data.setdefault("observed_world_revision", context.get("world_revision"))
                    data.setdefault("observed_agent_revision", context.get("agent_revision"))
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
                return self.platform.submit_world_progress(
                    actor_id,
                    str(data.get("progress_key") or ""),
                    None,
                    str(data.get("evidence") or ""),
                    source_event_ids=source_event_ids,
                )
            if intent_type == "propose_institution":
                return self.platform.submit_institution_proposal(actor_id, {
                    "name": data.get("name"), "purpose": data.get("purpose"), "scope": data.get("scope"),
                    "evidence": data.get("evidence"), "evidence_sources": data.get("evidence_sources"),
                    "minimum_evidence": data.get("minimum_evidence", 1), "ledger": data.get("ledger"), "policy": data.get("policy"),
                })
            if intent_type == "approve_institution":
                return self.platform.activate_institution(actor_id, str(data.get("institution") or ""), str(data.get("reason") or ""))
            if intent_type == "revise_institution":
                return self.platform.revise_institution(
                    actor_id, str(data.get("institution") or ""),
                    policy=data.get("policy"), ledger=data.get("ledger"), reason=str(data.get("reason") or ""),
                )
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
            if intent_type == "propose_definition":
                proposal = dict(data)
                proposal.pop("intent_type", None)
                return self.platform.submit_definition_proposal(actor_id, proposal)
            if intent_type == "propose_world_change":
                proposal = dict(data)
                proposal.pop("intent_type", None)
                if (str(task_type).startswith("trigger:") or str(task_type).startswith("event:")) and proposal.get("change_type") == "event" and not proposal.get("property"):
                    return {
                        "status": "rejected",
                        "failed_check": "event_already_recorded",
                        "retryable": True,
                        "message": "The triggering event is already recorded. Return wait or submit one typed rule, progress, entity, relationship, or faction proposal grounded in the observed result.",
                    }
                result = self.platform.submit_world_change(actor_id, proposal, origin_role=role)
                return result
            if intent_type == "propose_story_beat":
                proposal = dict(data)
                proposal.pop("intent_type", None)
                return self.platform.submit_story_beat(actor_id, proposal, source_event_ids=source_event_ids)
            if intent_type == "submit_world_template":
                return self.platform.submit_world_template(actor_id, data.get("template") or {})
        except (TypeError, ValueError, KeyError) as exc:
            return {"status": "rejected", "failed_check": "intent_schema", "message": str(exc)}
        return {"status": "rejected", "failed_check": "intent_type", "message": "Intent was not handled."}

    @staticmethod
    def _normalize_provider_intent(role: str, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize harmless provider wrappers without changing world semantics.

        Some JSON-mode models put the intent name in ``choice`` and the real
        visible behavior in ``behavior`` or a nested ``parameters.choice``.
        The model-facing contract remains the visible choice; this boundary
        repair is accepted only for the decision role and still passes through
        exact Platform choice resolution.
        """

        return normalize_provider_intent(role, data)

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
        definitions = self.platform.store.repositories.entities.get_definitions("action_def")
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
