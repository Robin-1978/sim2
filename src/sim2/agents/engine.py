from __future__ import annotations

import copy
import json
from typing import Any

from ..agent_view.validator import validate_agent_view
from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from ..prompting.renderer import render_context_with_metrics
from ..world.lifecycle import BUSY_STATUSES, is_inactive
from ..world.growth import build_growth_ledger
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
        self._explicit_max_iterations = max_iterations is not None
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
        iteration_limit = self.max_iterations
        if not self._explicit_max_iterations and role == "god" and str(task_type) == "god_structural":
            # Structural work may contain many independent proposals; the
            # role's ordinary decision budget is too small for the pack.  An
            # explicit caller limit still wins for tests and bounded probes.
            iteration_limit = max(iteration_limit, 20)
        for iteration in range(1, iteration_limit + 1):
            turn = self._react_step(session, iteration)
            if turn is not None:
                return turn
        return self._exhausted_turn(session, iteration_limit)

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
                "condition": str(
                    (trigger_config or {}).get("when")
                    or ("configured cadence contract" if (trigger_config or {}).get("contract") else "configured evidence group")
                ),
                **dict(trigger_contract),
            }
            if trigger_contract.get("evidence_sources"):
                context["evolution_trigger"]["observed_evidence"] = self.platform.count_verified_evidence(trigger_contract.get("evidence_sources"))
            if isinstance(trigger_contract.get("stages"), list) and trigger_contract.get("stages"):
                # The stage text already carries the exact proposal shape and
                # the role policy is in the system message.  A ten-bullet
                # free-form protocol would repeat it on every stage.
                context["prompt_protocol"] = [
                    "Follow the current stage instruction and submit exactly one proposal for it.",
                    "Use exact visible names; a new Action needs followup_behavior in the same call.",
                    "Use at most four focused reads; after the budget warning, submit with the facts already collected.",
                ]
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
            evidence_limit = 8 if context.get("role") == "god" else 16
            for item in verified_events[-evidence_limit:]:
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
        if isinstance(trigger_contract, dict):
            # World YAML uses the author-facing ``tools`` key; compiled
            # contracts use ``allowed_tools``.  Accept both so a raw contract
            # cannot silently expose the role's entire tool catalog.
            allowed = trigger_contract.get("allowed_tools")
            if not isinstance(allowed, list):
                allowed = trigger_contract.get("tools")
            if isinstance(allowed, list):
                trigger_tools = [str(item) for item in allowed if str(item)]
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

    def _context_limits(self, role: str) -> tuple[int, int | None]:
        """Resolve a role's character and token budget from world configuration."""

        rules = self.platform.store.repositories.metadata.get("rules", {}) or {}
        limits = rules.get("context_limits") if isinstance(rules, dict) else {}
        limits = limits if isinstance(limits, dict) else {}
        default_chars = int(limits.get("default_chars", 16000) or 16000)
        default_tokens = limits.get("default_tokens")
        roles = limits.get("roles") if isinstance(limits.get("roles"), dict) else {}
        role_limits = roles.get(role) if isinstance(roles.get(role), dict) else {}
        max_chars = int(role_limits.get("max_chars", default_chars) or default_chars)
        max_tokens_value = role_limits.get("max_tokens", default_tokens)
        max_tokens = int(max_tokens_value) if isinstance(max_tokens_value, (int, float)) and not isinstance(max_tokens_value, bool) and max_tokens_value > 0 else None
        return max_chars, max_tokens

    def _render_prompt(self, context: dict[str, Any], task: str, role: str) -> tuple[str, dict[str, Any]]:
        max_chars, max_tokens = self._context_limits(role)
        return render_context_with_metrics(context, task, max_chars=max_chars, max_tokens=max_tokens)

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
        prompt, metrics = self._render_prompt(context, task, role)
        messages = [
            {"role": "system", "content": system_policy},
            {"role": "user", "content": prompt},
        ]
        stages = compiled.trigger_contract.get("stages") if isinstance(compiled.trigger_contract, dict) else None
        if isinstance(stages, list) and stages:
            instruction = str((stages[0] or {}).get("instruction") or "") if isinstance(stages[0], dict) else ""
            messages.append({
                "role": "user",
                "content": f"Structural stage 1/{len(stages)}: {instruction}",
            })
        session = ReactSession(
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
            messages=messages,
            context_metrics=[metrics],
            max_tool_calls=self.max_tool_calls,
            max_repeated_tool_calls=self.max_repeated_tool_calls,
            # The read budget is role configuration. A bounded world reviewer
            # may need several focused reads before its final typed proposal;
            # without a cap, remote models can spend all ReAct turns reading
            # the same event from different projections and leave the
            # evolution task unresolved.
            read_budget=self.max_read_tool_calls,
        )
        self._restore_structural_progress(session)
        return session

    def _structural_progress_key(self, session: ReactSession) -> str:
        """Identify one structural plan across retries of the same God task."""

        if session.role != "god" or str(session.task_type) != "god_structural":
            return ""
        plan = session.context.get("season_plan") if isinstance(session.context.get("season_plan"), dict) else {}
        plan_id = str(plan.get("plan_id") or plan.get("target_cell") or "no-plan")
        return f"{session.actor_id}:{session.task_type}:{plan_id}"

    def _restore_structural_progress(self, session: ReactSession) -> None:
        """Resume the persisted stage boundary after an interrupted session.

        Stage instructions tell the model what to propose next; accepted facts
        remain in SQLite and are re-checked semantically.  Persisting only the
        stage index and a short accepted summary therefore keeps the prompt
        bounded while avoiding a full replay of old proposals.
        """

        key = self._structural_progress_key(session)
        stages = self._structural_stages(session)
        if not key or not stages:
            return
        progress = (self.platform.store.repositories.metadata.get("structural_progress", {}) or {}).get(key)
        if not isinstance(progress, dict):
            return
        stage_index = max(0, int(progress.get("stage_index", 0) or 0))
        if stage_index >= len(stages):
            return
        session.stage_index = stage_index
        if stage_index > 0:
            stage = stages[stage_index] if isinstance(stages[stage_index], dict) else {}
            if session.messages:
                session.messages[-1] = {
                    "role": "user",
                    "content": f"Structural stage {stage_index + 1}/{len(stages)}: {stage.get('instruction') or ''}",
                }
        accepted = [str(item) for item in (progress.get("accepted") or []) if str(item)]
        if accepted:
            session.messages.append({
                "role": "user",
                "content": "Structural progress already accepted: " + ", ".join(accepted[-12:]) + ". Continue from the current stage.",
            })

    def _save_structural_progress(self, session: ReactSession, result: dict[str, Any] | None = None, *, status: str = "") -> None:
        """Persist the stage boundary and a bounded accepted-proposal summary."""

        key = self._structural_progress_key(session)
        if not key:
            return
        with self.platform.store.transaction():
            progress = dict(self.platform.store.repositories.metadata.get("structural_progress", {}) or {})
            entry = dict(progress.get(key) or {})
            entry["stage_index"] = int(session.stage_index)
            entry["updated_minute"] = int(self.platform.store.repositories.metadata.get("world_minute", 0) or 0)
            if status:
                entry["status"] = status
            else:
                entry.setdefault("status", "in_progress")
            if isinstance(result, dict) and str(result.get("status")) == "accepted":
                label = (
                    result.get("name")
                    or result.get("choice")
                    or result.get("definition_type")
                    or result.get("kind")
                    or result.get("summary")
                    or "accepted proposal"
                )
                accepted = [str(item) for item in (entry.get("accepted") or [])]
                accepted.append(str(label)[:120])
                entry["accepted"] = accepted[-30:]
            progress[key] = entry
            self.platform.store.repositories.metadata.set("structural_progress", progress)

    def _react_step(self, session: ReactSession, iteration: int) -> AgentTurn | None:
        """Run one ReAct iteration; return the finished turn, or ``None`` to continue the session."""

        prompt, metrics = self._render_prompt(session.context, session.task, session.role)
        session.prompt_history.append(prompt)
        session.context_metrics.append(metrics)
        try:
            response = self._request_model(session, prompt)
        except Exception as exc:
            return self._llm_error_turn(session, iteration, prompt, exc)
        readable_response = self._readable_model_response(response)
        session.last_response = readable_response
        session.transcript.append({"step": "llm", "response": readable_response})
        self.audit.record(session.actor_id, session.role, session.task_type, iteration, prompt, readable_response, "response", usage=self._last_usage, context_metrics=metrics)
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

    @staticmethod
    def _messages_for_prompt(messages: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
        """Return a copy whose first user snapshot is the fresh dynamic prompt."""

        updated = [dict(item) for item in messages]
        for index, item in enumerate(updated):
            if item.get("role") == "user":
                updated[index] = {**item, "content": prompt}
                break
        else:
            updated.append({"role": "user", "content": prompt})
        return updated

    def _request_model(self, session: ReactSession, prompt: str) -> dict[str, Any]:
        """Ask the provider for the next tool request or final Intent."""

        self._last_usage = {}
        call_tools = session.session_tools
        if session.role in {"agent_decision", "god"} and not str(session.task_type).startswith("instant:") and session.read_tool_calls >= session.read_budget:
            available = session.session_tools if session.session_tools is not None else [str(item.get("name")) for item in (session.context.get("tools") or [])]
            call_tools = [name for name in available if TOOL_DEFINITIONS.get(name, {}).get("kind") != "read"]
        response = self._complete(
            role=session.role,
            system=session.system_policy,
            context=prompt,
            messages=self._messages_for_prompt(session.messages, prompt),
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
        self.audit.record(session.actor_id, session.role, session.task_type, iteration, prompt, {"error": str(exc)}, "llm_error", usage=self._last_usage, context_metrics=session.context_metrics[-1] if session.context_metrics else None)
        # A provider/configuration failure is outside the model's
        # ReAct loop. Retrying it immediately six times creates an
        # error storm; the scheduler will retry after backoff.
        return session.finish(session.last_result, iterations=iteration)

    def _multi_proposal(self, session: ReactSession, result: dict[str, Any] | None) -> bool:
        """Whether an accepted structural God proposal should continue the same turn.

        The broad structural contract allows several proposals in one turn.  A
        God turn therefore keeps the ReAct session open after an accepted
        proposal so the model can submit the next one, instead of stopping at
        the first Intent.  Other roles and tasks retain the one-final-Intent
        contract.
        """

        return bool(
            result
            and str(result.get("status")) == "accepted"
            and session.role == "god"
            and str(session.task_type) == "god_structural"
        )

    def _required_content_missing(self, session: ReactSession, required: dict[str, Any] | None = None) -> list[str]:
        """Return contract-required content that does not exist yet.

        Exact ids are supported, but the preferred contract uses semantic
        requirements: an effect family, an authorization operation, or an
        authorization rule mode.  Content may then choose its own visible
        names without failing the structural gate.
        """

        if required is None:
            contract = session.context.get("evolution_trigger")
            required = contract.get("required_content") if isinstance(contract, dict) else None
        if not isinstance(required, dict):
            return []
        entities = self.platform.store.repositories.entities
        missing: list[str] = []
        if required.get("growth_cell"):
            plan = session.context.get("season_plan") if isinstance(session.context.get("season_plan"), dict) else {}
            raw_cell = required.get("growth_cell")
            cell = str(plan.get("target_cell") if raw_cell is True else raw_cell or "").strip()
            if cell:
                ledger = build_growth_ledger(self.platform.store)
                count = int(((ledger.get("cells") or {}).get(cell) or {}).get("count", 0) or 0)
                baseline = int(plan.get("baseline", 0) or 0)
                if count <= baseline:
                    missing.append(f"growth: {cell} count must exceed {baseline}")
        for raw in required.get("entities") or []:
            name = str(raw)
            canonical = self.platform.targets.canonical_id(name)
            found = False
            if canonical:
                try:
                    entities.get_entity(str(canonical))
                    found = True
                except KeyError:
                    found = False
            if not found:
                missing.append(f"entity: {name}")
        behavior_values: set[str] = set()
        behaviors = list(entities.get_definitions("bt_def"))
        for item in behaviors:
            behavior_values.update({
                str(item.get("id") or "").casefold(),
                str(item.get("agent_label") or "").casefold(),
                str(item.get("name") or "").casefold(),
            })
        for raw in required.get("behaviors") or []:
            if str(raw).casefold() not in behavior_values:
                missing.append(f"behavior: {raw}")
        for raw in required.get("behaviors_using_action") or []:
            wanted = str(raw).casefold()
            if not any(
                str(node.get("action_id") or "").casefold() == wanted
                for item in behaviors
                for node in (item.get("nodes") or [])
                if isinstance(node, dict)
            ):
                missing.append(f"behavior using action: {raw}")
        action_defs = list(entities.get_definitions("action_def"))
        effect_types: set[str] = set()
        for action in action_defs:
            for effect in action.get("effects") or []:
                if isinstance(effect, dict):
                    effect_types.add(str(effect.get("type") or effect.get("op") or "").casefold())
        for raw in required.get("behavior_effects") or []:
            if str(raw).casefold() not in effect_types:
                missing.append(f"effect: {raw}")
        for raw in required.get("generic_parameters") or []:
            parameter = str(raw)
            found = False
            for action in action_defs:
                if parameter not in (action.get("parameters") or {}):
                    continue
                if any(
                    isinstance(effect, dict)
                    and any(str(value) == f"context.{parameter}" for value in effect.values())
                    for field in ("effects", "progress_effects")
                    for effect in (action.get(field) or [])
                ):
                    found = True
                    break
            if not found:
                missing.append(f"generic parameter: {parameter}")
        operations: set[tuple[str, str]] = set()
        for behavior in behaviors:
            operation = str(behavior.get("authorization_operation") or "").casefold()
            decision = str(behavior.get("authorization_decision") or "").casefold()
            operations.add((operation, decision))
            operations.add((operation, ""))
        for raw in required.get("behavior_operations") or []:
            value = str(raw)
            if ":" in value:
                operation, decision = value.split(":", 1)
                found = (operation.casefold(), decision.casefold()) in operations
            else:
                found = (value.casefold(), "") in operations
            if not found:
                missing.append(f"operation: {value}")
        relationships = list(entities.list_relationships())
        for raw in required.get("relationships") or []:
            if not isinstance(raw, dict):
                continue
            source = str(self.platform.targets.canonical_id(str(raw.get("from") or "")) or raw.get("from") or "")
            target = str(self.platform.targets.canonical_id(str(raw.get("to") or "")) or raw.get("to") or "")
            kind = str(raw.get("type") or "")
            found = any(
                str(item.get("from") or "") == source
                and str(item.get("to") or "") == target
                and str(item.get("type") or "") == kind
                for item in relationships
            )
            if not found:
                missing.append(f"relationship: {raw.get('from')} -> {raw.get('to')} ({kind})")
        rules = (self.platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) or []
        rule_values: set[str] = set()
        authorization_modes: set[str] = set()
        for item in rules:
            rule_values.update({str(item.get("id") or "").casefold(), str(item.get("name") or "").casefold()})
            effect = item.get("effect") if isinstance(item.get("effect"), dict) else {}
            if str(effect.get("type") or "") == "authorization":
                authorization_modes.add(str(effect.get("mode") or "resource_access").casefold())
        for raw in required.get("rules") or []:
            if str(raw).casefold() not in rule_values:
                missing.append(f"rule: {raw}")
        for raw in required.get("authorization_modes") or []:
            if str(raw).casefold() not in authorization_modes:
                missing.append(f"authorization mode: {raw}")
        return missing

    def _contract_correction(self, session: ReactSession) -> dict[str, Any] | None:
        if session.role != "god" or str(session.task_type) != "god_structural":
            return None
        missing = self._required_content_missing(session)
        if not missing:
            return None
        return {
            "status": "incomplete",
            "failed_check": "required_content",
            "missing": missing,
            "message": (
                "The structural contract is incomplete. Missing: "
                + ", ".join(missing)
                + ". Submit the missing proposals now; do not wait until every required item exists."
            ),
        }

    def _apply_contract_correction(self, session: ReactSession, correction: dict[str, Any]) -> None:
        session.last_result = correction
        session.context["last_platform_result"] = correction
        session.context["contract_missing"] = list(correction.get("missing") or [])
        session.messages.append({"role": "user", "content": str(correction["message"])})

    def _structural_required(self, session: ReactSession) -> bool:
        contract = session.context.get("evolution_trigger")
        return bool(isinstance(contract, dict) and isinstance(contract.get("required_content"), dict) and contract.get("required_content"))

    def _structural_stages(self, session: ReactSession) -> list[dict[str, Any]] | None:
        contract = session.context.get("evolution_trigger")
        stages = contract.get("stages") if isinstance(contract, dict) else None
        return stages if isinstance(stages, list) and stages else None

    def _advance_structural_stage(self, session: ReactSession) -> str:
        """Check the current atomic stage, advance when complete, and tell the caller whether to finish."""

        stages = self._structural_stages(session)
        if not stages:
            return "none"
        if session.stage_index >= len(stages):
            return "finish"
        stage = stages[session.stage_index] if isinstance(stages[session.stage_index], dict) else {}
        required = stage.get("required_content") if isinstance(stage.get("required_content"), dict) else {}
        missing = self._required_content_missing(session, required)
        if missing:
            self._apply_contract_correction(session, {
                "status": "incomplete",
                "failed_check": "required_content",
                "missing": missing,
                "message": (
                    f"Structural stage {session.stage_index + 1}/{len(stages)}: "
                    + str(stage.get("instruction") or "")
                    + " Missing: " + ", ".join(missing)
                    + ". Submit the next proposal for this stage."
                ),
            })
            return "continue"
        session.stage_index += 1
        self._save_structural_progress(session)
        if session.stage_index >= len(stages):
            return "finish"
        next_stage = stages[session.stage_index] if isinstance(stages[session.stage_index], dict) else {}
        session.messages.append({
            "role": "user",
            "content": f"Structural stage {session.stage_index + 1}/{len(stages)}: {next_stage.get('instruction') or ''}",
        })
        return "continue"

    def _append_structural_progress(self, session: ReactSession, missing: list[str]) -> None:
        session.context["contract_missing"] = list(missing)
        session.messages.append({
            "role": "user",
            "content": "Structural progress: still missing " + ", ".join(missing) + ". Submit the next proposal.",
        })

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
            max_tail = 4 if session.role == "god" and str(session.task_type) == "god_structural" else 6
            session.messages = trim_conversation_messages(session.messages, max_tail=max_tail)
        if accepted_result is None and str(model_result.get("failed_check")) == "tool_budget":
            # No further proposal can be submitted in this session.  Returning
            # to the model only produces more rejected calls, so close the
            # session and let the scheduler retry with a fresh budget.
            result = {
                "status": "rejected",
                "failed_check": "budget_exhausted",
                "retryable": True,
                "message": "Tool-call budget exhausted before a valid proposal.",
            }
            session.last_result = result
            session.context["last_platform_result"] = result
            return session.finish(result, iterations=iteration)
        if accepted_result is None:
            return None
        session.accepted_proposals += max(1, len(intent_results))
        if len(intent_results) > 1:
            accepted_result = {
                **intent_results[-1],
                "status": "accepted",
                "submission_results": intent_results,
                "submission_count": len(intent_results),
            }
        if self._structural_stages(session):
            self._save_structural_progress(session, accepted_result)
            transition = self._advance_structural_stage(session)
            if transition == "finish":
                self._save_structural_progress(session, status="completed")
                return session.finish(accepted_result, iterations=iteration)
            return None
        if not batched and self._multi_proposal(session, accepted_result):
            if self._structural_required(session):
                missing = self._required_content_missing(session)
                if not missing:
                    return session.finish(accepted_result, iterations=iteration)
                self._append_structural_progress(session, missing)
                return None
            contract = session.context.get("evolution_trigger")
            limit = int(contract.get("max_proposals") or 0) if isinstance(contract, dict) else 0
            if limit and session.accepted_proposals >= limit:
                return session.finish(accepted_result, iterations=iteration)
            return None
        correction = self._contract_correction(session) if str(accepted_result.get("status")) == "wait" or batched else None
        if correction is not None:
            self._apply_contract_correction(session, correction)
            return None
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
        structural = session.role == "god" and str(session.task_type) == "god_structural"
        history_result = self._structural_history_result(tool_name, model_result) if structural else model_result
        history_arguments = self._structural_history_arguments(tool_name, arguments) if structural else arguments
        history = session.context.setdefault("interaction_history", [])
        history.extend([
            {"kind": "assistant_tool_call", "name": tool_name, "arguments": history_arguments},
            {"kind": "tool_result", "name": tool_name, "result": history_result},
        ])
        tool_results = session.context.setdefault("tool_results", [])
        tool_results.append({"tool": tool_name, "result": history_result})
        if structural:
            # A structural session may submit many proposals; keep only the
            # recent exchange plus the compact progress message.  The latest
            # tool result stays in the native message tail, so old index pages
            # and definition trees need only a name-level summary here.
            session.context["interaction_history"] = history[-8:]
            session.context["tool_results"] = tool_results[-3:]
        session.context["last_platform_result"] = model_result
        session.last_result = internal_tool_result
        if native_call is not None:
            session.messages.append({"role": "tool", "tool_call_id": native_call.get("id", "tool_call"), "name": tool_name, "content": json.dumps(model_result, ensure_ascii=False)})
        return model_result, internal_tool_result

    @staticmethod
    def _structural_history_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Summarize a submitted proposal instead of resending its full definition.

        The accepted content already lives in SQLite and the immediate tool
        message carries the exact platform result.  Re-rendering every earlier
        Action tree on each later stage is the largest avoidable source of
        structural prompt growth.
        """

        if tool_name not in {
            "propose_entity", "propose_definition", "propose_world_change",
            "propose_institution", "approve_institution", "revise_institution",
        }:
            return arguments
        summary: dict[str, Any] = {}
        for key in ("entity_type", "name", "definition_type", "change_type", "summary", "property", "reason"):
            value = arguments.get(key)
            if value not in (None, "", [], {}):
                summary[key] = value if not isinstance(value, str) else value[:160]
        for key in ("definition", "followup_behavior", "followup_recipe", "rule", "route", "relationship", "faction", "event"):
            value = arguments.get(key)
            if not isinstance(value, dict):
                continue
            label = value.get("name") or value.get("id") or value.get("label") or value.get("summary")
            summary[key] = {"name": str(label)[:160]} if label else {"submitted": True}
        return summary or {"submitted": tool_name}

    @staticmethod
    def _structural_history_result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        """Keep structural read history small; the full result is in the tool message."""

        if tool_name == "query_world_index":
            items = result.get("items") if isinstance(result.get("items"), list) else []
            return {
                "status": result.get("status"),
                "cell": result.get("cell"),
                "total": result.get("total"),
                "names": [str(item.get("name") or "") for item in items[:6] if isinstance(item, dict)],
                "next_offset": result.get("next_offset"),
            }
        if tool_name == "inspect_definition":
            definition = result.get("definition") if isinstance(result.get("definition"), dict) else {}
            return {
                "status": result.get("status"),
                "found": result.get("found"),
                "kind": definition.get("kind"),
                "name": definition.get("name") or result.get("name"),
            }
        return result

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
            if result.get("status") == "accepted":
                session.accepted_proposals += 1
            if self._structural_stages(session):
                self._save_structural_progress(session, result)
                transition = self._advance_structural_stage(session)
                if transition == "finish":
                    self._save_structural_progress(session, status="completed")
                    return session.finish(result, iterations=iteration, response=response)
                return None
            if self._multi_proposal(session, result):
                if self._structural_required(session):
                    missing = self._required_content_missing(session)
                    if not missing:
                        return session.finish(result, iterations=iteration, response=response)
                    self._append_structural_progress(session, missing)
                    return None
                contract = session.context.get("evolution_trigger")
                limit = int(contract.get("max_proposals") or 0) if isinstance(contract, dict) else 0
                if limit and session.accepted_proposals >= limit:
                    return session.finish(result, iterations=iteration, response=response)
                return None
            correction = self._contract_correction(session) if result.get("status") == "wait" else None
            if correction is not None:
                self._apply_contract_correction(session, correction)
                return None
            return session.finish(result, iterations=iteration, response=response)
        return None

    def _exhausted_turn(self, session: ReactSession, iteration_limit: int | None = None) -> AgentTurn:
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
        return session.finish(last_result, iterations=int(iteration_limit or self.max_iterations))

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
            player_message = str((context.get("interaction") or {}).get("origin") or "") == "player"
            context["prompt_protocol"] = [
                "This is one bounded instant message response.",
                "This is a direct player message; always send one short reply." if player_message else "Send one short reply only when the incoming message adds information or needs a decision; otherwise wait.",
                "Reply in the character's own first-person voice; use your role, traits, current work, or a memory when relevant.",
                "You may state feelings, preferences, suspicions, plans, and what you want from the player. Never invent hard facts (names, quantities, prices, routes, ownership) that are absent from this context.",
                "Never invent sensory or operational details about a route, bridge, building, item, weather, material, timing, quantity, or cause. If those details are absent, say so briefly and add a reaction or question.",
                "If a hard detail is unknown, say so briefly and add a reaction, question, or next step. Never reply with only that you do not know.",
                "A message cannot transfer items or currency, collect payment, sign a contract, or complete a trade; describe those outcomes as pending until a Platform transaction confirms them.",
                "Use the exact visible participant name; Platform owns every state change and world time.",
            ]
        context["platform_rules"] = [
            "Only the incoming interaction is being handled; do not start an unrelated behavior.",
            "A reply is at most two concise sentences and stays in first person.",
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
