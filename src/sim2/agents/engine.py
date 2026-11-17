from __future__ import annotations

import copy
import json
from typing import Any

from ..agent_view.validator import validate_agent_view
from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from ..prompting.renderer import render_context
from ..world.lifecycle import BUSY_STATUSES, is_inactive
from .runtime_policy import (
    ROLE_INTENTS, ROLE_NAMES, configured_evolution_group, configured_evolution_trigger,
    configured_role_tools, role_policy,
)
from ..agent_view.policies import task_text
from .intent_gateway import AgentIntentGateway
from .audit import AgentAudit
from .protocol import ProviderProtocol, normalize_provider_intent, readable_model_response
from .session import AgentTurn, ReactSession, TaskContext, model_tool_result, trim_conversation_messages
from .tool_guards import read_signature, screen_tool_call
from .tools import AgentToolExecutor, TOOL_DEFINITIONS, parse_tool_call


def _closed_turn(role: str, task_type: str, step: str, result: dict[str, Any]) -> AgentTurn:
    """A turn that ended before any model call, recorded as a single transcript step."""

    return AgentTurn(role, task_type, {}, result, 0, [{"step": step, "result": result}], {}, [])


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
        skipped = self._skip_unavailable_character(role, actor_id, task_type)
        if skipped is not None:
            return skipped
        compiled = self._compile_task_context(role, actor_id, task_type, interaction, source_event_id, source_events)
        unprepared = self._reflection_without_evidence(role, task_type, compiled.context)
        if unprepared is not None:
            return unprepared
        session = self._start_session(role, actor_id, task_type, compiled)
        for iteration in range(1, self.max_iterations + 1):
            turn = self._react_step(session, iteration)
            if turn is not None:
                return turn
        return self._exhausted_turn(session)

    def _skip_unavailable_character(self, role: str, actor_id: str, task_type: str) -> AgentTurn | None:
        """Skip the decision turn of a character that is busy or incapacitated; Platform advances it."""

        if role != "agent_decision" or str(task_type).startswith("instant:"):
            return None
        try:
            state = self.platform.store.repositories.entities.get_agent(actor_id).get("initial_state", {})
        except KeyError:
            state = {}
        if state.get("status") in BUSY_STATUSES:
            result = {"status": "skipped", "reason": "active_behavior", "message": "Platform is already executing a behavior; wait for completion or an interrupt."}
            return _closed_turn(role, task_type, "skipped", result)
        if is_inactive(state):
            result = {"status": "skipped", "reason": "incapacitated", "message": "The character is unconscious or dead and cannot act."}
            return _closed_turn(role, task_type, "skipped", result)
        return None

    def _compile_task_context(
        self,
        role: str,
        actor_id: str,
        task_type: str,
        interaction: dict[str, Any] | None,
        source_event_id: int | None,
        source_events: list[dict[str, Any]] | None,
    ) -> TaskContext:
        """Build the AgentView for one task: trigger contract, validated evidence and tool allow-list."""

        state_attributes = self.platform.attribute_schema.state_ids()
        context = self.view.build_decision_view(actor_id, role=role, source_event_id=source_event_id)
        context["role"] = role
        trigger_contract = self._attach_evolution_trigger(context, task_type)
        event_ids = self._attach_triggering_evidence(context, source_event_id, source_events)
        if interaction:
            context["interaction"] = dict(interaction)
        interaction_tools, session_tools = self._select_session_tools(context, role, task_type, trigger_contract)
        # Event tasks carry the exact validated source. They still may read a
        # small, budgeted amount of related world state: a God reviewer often
        # needs one read to see what the world lacks before choosing a
        # declarative proposal. The read budget below is the only cap.
        view_errors = validate_agent_view(context, role=role)
        if view_errors:
            raise ValueError("invalid AgentView: " + "; ".join(view_errors))
        return TaskContext(context, state_attributes, event_ids, trigger_contract, interaction_tools, session_tools)

    def _attach_evolution_trigger(self, context: dict[str, Any], task_type: str) -> Any:
        """Add the configured trigger or evidence-group contract to the view; return the contract."""

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
        return trigger_contract

    def _attach_triggering_evidence(
        self,
        context: dict[str, Any],
        source_event_id: int | None,
        source_events: list[dict[str, Any]] | None,
    ) -> list[int]:
        """Show the validated source events once as evidence; return their ids."""

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
        return event_ids

    def _select_session_tools(
        self,
        context: dict[str, Any],
        role: str,
        task_type: str,
        trigger_contract: Any,
    ) -> tuple[list[str] | None, list[str] | None]:
        """Pick the tools the session exposes and cut the view's tool list to them.

        Returns ``(interaction_tools, session_tools)``: an instant interaction
        exposes only its direct reply tools, a trigger its configured
        ``allowed_tools``, anything else the role's configured tools.
        """

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
        return interaction_tools, session_tools

    def _reflection_without_evidence(self, role: str, task_type: str, context: dict[str, Any]) -> AgentTurn | None:
        """A scheduled reflection needs a completed outcome or meaningful event; without one it waits."""

        if role == "agent_reflection" and task_type in {"daily_reflection", "integration_verification"} and not self.intent_gateway.reflection_has_evidence(context):
            result = {"status": "wait", "reason": "No completed outcome or meaningful event is available for reflection."}
            return _closed_turn(role, task_type, "guard", result)
        return None

    def _start_session(self, role: str, actor_id: str, task_type: str, compiled: TaskContext) -> ReactSession:
        context = compiled.context
        task = task_text(role, task_type)
        executor = AgentToolExecutor(
            self.platform,
            role=role,
            actor_id=actor_id,
            source_event_ids=compiled.event_ids,
            observed_world_revision=context.get("world_revision"),
            observed_agent_revision=context.get("agent_revision"),
            evolution_contract=compiled.trigger_contract if isinstance(compiled.trigger_contract, dict) else None,
        )
        initial_context = copy.deepcopy(context)
        system_policy = role_policy(self.platform, role)
        return ReactSession(
            role=role,
            actor_id=actor_id,
            task_type=task_type,
            task=task,
            system_policy=system_policy,
            context=context,
            initial_context=initial_context,
            executor=executor,
            event_ids=compiled.event_ids,
            state_attributes=compiled.state_attributes,
            interaction_tools=compiled.interaction_tools,
            session_tools=compiled.session_tools,
            messages=[
                {"role": "system", "content": system_policy},
                {"role": "user", "content": render_context(context, task)},
            ],
            max_tool_calls=self.max_tool_calls,
            max_repeated_tool_calls=self.max_repeated_tool_calls,
            # The read budget is role configuration. A bounded world reviewer
            # may need several focused reads before its final typed proposal;
            # without a cap, remote models can spend all ReAct turns reading
            # the same event from different projections and leave the
            # evolution task unresolved.
            read_budget=self.max_read_tool_calls,
        )

    def _react_step(self, session: ReactSession, iteration: int) -> AgentTurn | None:
        """Run one ReAct iteration; return the finished turn, or ``None`` to continue the session."""

        prompt = render_context(session.context, session.task)
        session.prompt_history.append(prompt)
        try:
            response = self._request_model(session, prompt)
        except Exception as exc:
            return self._llm_error_turn(session, iteration, prompt, exc)
        readable_response = self._readable_model_response(response)
        session.last_response = readable_response
        session.transcript.append({"step": "llm", "response": readable_response})
        self.audit.record(session.actor_id, session.role, session.task_type, iteration, prompt, readable_response, "response", usage=self._last_usage)
        try:
            call = parse_tool_call(response)
        except (TypeError, ValueError) as exc:
            session.last_result = {"status": "rejected", "failed_check": "tool_arguments", "message": str(exc)}
            session.context["last_platform_result"] = session.last_result
            session.transcript.append({"step": "tool_error", "error": str(exc)})
            return None
        if call is None:
            return self._submit_intent(session, iteration, response)
        return self._run_tool_calls(session, iteration, response, call)

    def _request_model(self, session: ReactSession, prompt: str) -> dict[str, Any]:
        """Ask the provider for the next tool request or final Intent."""

        self._last_usage = {}
        call_tools = session.session_tools
        if session.role == "agent_decision" and not str(session.task_type).startswith("instant:") and session.read_tool_calls >= session.read_budget:
            available = session.session_tools if session.session_tools is not None else [str(item.get("name")) for item in (session.context.get("tools") or [])]
            call_tools = [name for name in available if TOOL_DEFINITIONS.get(name, {}).get("kind") != "read"]
        response = self._complete(
            role=session.role,
            system=session.system_policy,
            context=prompt,
            messages=session.messages,
            allowed_tools=call_tools,
        )
        if not isinstance(response, dict):
            raise ValueError("LLM response must be a JSON object")
        return response

    def _llm_error_turn(self, session: ReactSession, iteration: int, prompt: str, exc: Exception) -> AgentTurn:
        """End the session after a provider failure, which the scheduler retries after backoff."""

        self._last_usage = dict(getattr(exc, "usage", {}) or self._last_usage)
        session.last_result = {"status": "rejected", "failed_check": "llm_response", "message": str(exc)}
        session.context["last_platform_result"] = session.last_result
        session.transcript.append({"step": "llm_error", "error": str(exc)})
        session.context.setdefault("interaction_history", []).append({"kind": "llm_error", "error": str(exc)})
        self.audit.record(session.actor_id, session.role, session.task_type, iteration, prompt, {"error": str(exc)}, "llm_error", usage=self._last_usage)
        # A provider/configuration failure is outside the model's
        # ReAct loop. Retrying it immediately six times creates an
        # error storm; the scheduler will retry after backoff.
        return session.finish(session.last_result, iterations=iteration)

    def _run_tool_calls(self, session: ReactSession, iteration: int, response: dict[str, Any], call: tuple[str, dict[str, Any]]) -> AgentTurn | None:
        """Execute the tool call(s) of one model response; finish the turn once an Intent is accepted."""

        native_tool_calls = response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None
        calls = self._collect_calls(session, iteration, call, native_tool_calls)
        batched = bool(native_tool_calls) and len(calls) > 1
        accepted_result, intent_results, model_result = self._execute_calls(session, calls, batched=batched)
        if not native_tool_calls:
            # Native tool messages already contain every result. An extra
            # user copy is redundant and can invalidate strict providers when
            # a response contains multiple calls.
            session.messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
            session.messages.append({"role": "user", "content": f"Tool results: {json.dumps(model_result, ensure_ascii=False)}"})
        # Keep the stable system prompt and initial snapshot, plus a
        # bounded tail of the current ReAct exchange.  Without this
        # cap every read result is resent on every iteration, so a
        # short choice can accumulate a large prompt and latency.
        if len(session.messages) > 8:
            session.messages = trim_conversation_messages(session.messages, max_tail=6)
        if accepted_result is None:
            return None
        if len(intent_results) > 1:
            accepted_result = {
                **intent_results[-1],
                "status": "accepted",
                "submission_results": intent_results,
                "submission_count": len(intent_results),
            }
        return session.finish(accepted_result, iterations=iteration)

    @staticmethod
    def _collect_calls(
        session: ReactSession,
        iteration: int,
        call: tuple[str, dict[str, Any]],
        native_tool_calls: list[dict[str, Any]] | None,
    ) -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
        """List the ``(tool, arguments, native call)`` triples of a response, declaring native calls in the messages."""

        if not native_tool_calls:
            return [(call[0], call[1], None)]
        calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
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
        session.messages.append({"role": "assistant", "tool_calls": normalized_native_calls})
        return calls

    def _execute_calls(
        self,
        session: ReactSession,
        calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]],
        *,
        batched: bool,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
        """Run the calls in order; return the last accepted Intent result, all Intent results and the last model result."""

        accepted_result: dict[str, Any] | None = None
        intent_results: list[dict[str, Any]] = []
        model_result: dict[str, Any] = {}
        for tool_name, arguments, native_call in calls:
            is_read_tool = TOOL_DEFINITIONS.get(tool_name, {}).get("kind") == "read"
            repeats = session.register_call(tool_name, arguments, is_read_tool=is_read_tool)
            tool_result = screen_tool_call(session, tool_name, arguments, is_read_tool=is_read_tool, repeats=repeats)
            if tool_result is None:
                tool_result = session.executor.execute(tool_name, arguments)
            model_result, internal_tool_result = self._record_tool_result(session, tool_name, arguments, native_call, tool_result, is_read_tool)
            if TOOL_DEFINITIONS.get(tool_name, {}).get("kind") == "intent" and model_result.get("status") in {"accepted", "wait"}:
                # A provider may return several deliberately ordered
                # intents in one native response.  Execute each exactly
                # once in that order so a fact proposal precedes the
                # narrative/rule that references it.  The batch remains
                # a single bounded Agent turn.
                accepted_result = internal_tool_result
                intent_results.append(internal_tool_result)
                if not is_read_tool and not batched:
                    break
        return accepted_result, intent_results, model_result

    def _record_tool_result(
        self,
        session: ReactSession,
        tool_name: str,
        arguments: dict[str, Any],
        native_call: dict[str, Any] | None,
        tool_result: dict[str, Any],
        is_read_tool: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Record one tool result in the session; return ``(model_result, internal_tool_result)``."""

        if is_read_tool and tool_result.get("status") == "ok":
            session.read_signatures.add(read_signature(tool_name, arguments))
            if session.role == "agent_decision":
                session.context["decision_guidance"] = f"{session.read_tool_calls} focused read(s) completed. Use those results now; choose one listed behavior or wait."
        model_result = model_tool_result(tool_name, tool_result, state_attributes=session.state_attributes)
        internal_tool_result = self._restore_internal_tool_result(tool_name, arguments, tool_result)
        session.transcript.append({
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
        session.context.setdefault("interaction_history", []).extend([
            {"kind": "assistant_tool_call", "name": tool_name, "arguments": arguments},
            {"kind": "tool_result", "name": tool_name, "result": model_result},
        ])
        session.context.setdefault("tool_results", []).append({"tool": tool_name, "result": model_result})
        session.context["last_platform_result"] = model_result
        session.last_result = internal_tool_result
        if native_call is not None:
            session.messages.append({"role": "tool", "tool_call_id": native_call.get("id", "tool_call"), "name": tool_name, "content": json.dumps(model_result, ensure_ascii=False)})
        return model_result, internal_tool_result

    def _submit_intent(self, session: ReactSession, iteration: int, response: dict[str, Any]) -> AgentTurn | None:
        """Submit a response without a tool call as the final Intent; it ends the session when accepted or a wait."""

        result = self.intent_gateway.submit(
            session.role,
            session.actor_id,
            response,
            context=session.context,
            task_type=session.task_type,
            source_event_ids=session.event_ids,
        )
        session.transcript.append({"step": "intent", "response": response, "result": result})
        session.messages.append({"role": "assistant", "content": json.dumps(response, ensure_ascii=False)})
        session.messages.append({"role": "user", "content": f"Platform result: {json.dumps(result, ensure_ascii=False)}"})
        session.context.setdefault("interaction_history", []).extend([
            {"kind": "assistant_intent", "intent": response},
            {"kind": "platform_result", "result": result},
        ])
        session.context["last_platform_result"] = result
        session.last_result = result
        if result.get("status") in {"accepted", "wait"}:
            return session.finish(result, iterations=iteration, response=response)
        return None

    def _exhausted_turn(self, session: ReactSession) -> AgentTurn:
        """End a session whose iterations ran out before a valid final Intent."""

        last_result = session.last_result
        if last_result.get("status") not in {"accepted", "wait", "skipped"}:
            last_result = {
                **last_result,
                "status": "rejected",
                "failed_check": "budget_exhausted",
                "retryable": True,
                "message": "The ReAct session reached its iteration limit before a valid final Intent.",
            }
        return session.finish(last_result, iterations=self.max_iterations)

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
