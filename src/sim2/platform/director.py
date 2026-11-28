"""Deterministic Director layer: choose the next structural growth target.

The Director owns intent, not content.  It reads the shared growth ledger and
writes one renewable ``season_plan`` (target cell, baseline, expiry).  Story
uses the plan for narrative direction; God receives the same target in its
context and a ``god_structural`` task.  Neither role is allowed to substitute a
research source for the requested structural cell.

The deterministic and model policies share plan advancement and persistence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..actions.pacing import generated_action_pacing
from ..evolution.config import role_enabled

from ..world.growth import build_growth_ledger, growth_count, growth_outcome_event
from ..world.growth_evidence import match_growth_evidence


class DirectorRuntimeMixin:
    """Select and advance the world's next structural growth target."""

    def _counts(self, config: dict[str, Any]) -> dict[str, int]:
        store = self._dependency("store")
        targets = config.get("targets", [])
        behavior_action = config.get("behavior_action")
        ledger = build_growth_ledger(store) if not behavior_action or set(targets) != {"verb"} else None
        return {
            target: growth_count(store, target, behavior_action=behavior_action, ledger=ledger)
            for target in targets
        }

    def _plan_due(self, minute: int, config: dict[str, Any]) -> bool:
        current = self._dependency("store").repositories.metadata.get("season_plan", {}) or {}
        metric_changed = (
            current.get("status") == "active"
            and current.get("target_cell") == "verb"
            and current.get("behavior_action") != config.get("behavior_action")
        )
        return (metric_changed or bool(self._replan_event(config, current))
                or self._should_kickoff(minute, config) or minute % int(config["cadence_minutes"]) == 0)

    def _replan_event(self, config: dict[str, Any], plan: dict[str, Any]) -> int | None:
        event_types = config.get("replan_on")
        if not event_types or plan.get("status") != "active":
            return None
        newest = self._dependency("store").repositories.events.recent_events_by_types(event_types, limit=1)
        if newest and newest[0]["event_id"] > int(plan.get("event_cursor") or 0):
            return newest[0]["event_id"]
        return None

    def _advance_plan(self, minute: int, config: dict[str, Any], counts: dict[str, int]) -> dict[str, Any] | None:
        store = self._dependency("store")
        metadata = store.repositories.metadata
        with store.transaction():
            plan = dict(metadata.get("season_plan", {}) or {})
            if plan.get("status") != "active":
                return None
            target = plan.get("target_cell")
            replan_event = self._replan_event(config, plan)
            metric_changed = target == "verb" and plan.get("behavior_action") != config.get("behavior_action")
            growth = not metric_changed and counts.get(target, 0) > int(plan.get("baseline", 0) or 0)
            evidence = None
            if growth and plan.get("evidence"):
                evidence = match_growth_evidence(store, plan, plan["evidence"])
                outcome_event = evidence.outcome
            else:
                outcome_event = growth_outcome_event(store, plan) if growth else None
            if outcome_event:
                plan.update(status="completed", completed_minute=minute, outcome_event_id=outcome_event)
                metadata.set("last_completed_season_plan", plan)
            elif metric_changed or replan_event or int(plan.get("expires_minute", 0) or 0) <= minute:
                plan.update(status="expired", completed_minute=minute)
                if replan_event:
                    plan.update(closure_reason="world stage changed", replan_event_id=replan_event)
                elif metric_changed:
                    plan["closure_reason"] = "growth metric changed"
                elif growth:
                    plan["closure_reason"] = "registered content has no verified use"
            else:
                review = plan.get("followup")
                if (review and evidence and evidence.registration and "use_review_minute" not in plan
                        and minute >= evidence.registered_minute + review["after_minutes"]):
                    plan["use_review_minute"] = minute
                    plan["use_review_subject"] = evidence.subject
                    metadata.set("season_plan", plan)
                    store.repositories.evolution.put_evolution_task(
                        f"director:use:{plan['plan_id']}", review["role"], review["role"], review["task"],
                        minute, 0, status="ready", data={"season_plan": plan, "source_event_id": evidence.registration, "use_review": True},
                    )
                return plan
            metadata.set("season_plan", plan)
            self._remember_plan(plan)
        return None

    def _context_reason(self, config: dict[str, Any]) -> str:
        """Compact world context the Director uses for its plan brief."""

        world = self._dependency("store").repositories.metadata.get("world_properties", {}) or {}
        parts = [
            f"{key}={world.get(key)}"
            for key in (config.get("context_properties") or [])
            if key in world
        ]
        return ", ".join(parts)

    def _resident_context(self, limit: int, minute: int) -> list[dict[str, str]]:
        # Sample perspectives, never outcomes. Reopening the same save at the
        # same planning minute presents the same witnesses to the Director.
        store = self._dependency("store")
        salt = f"{store.repositories.metadata.get('instance_id')}:{minute}:"
        resident_ids = [row["id"] for row in store.connection.execute(
            "SELECT id FROM agent_state WHERE COALESCE(json_extract(data_json,'$.role'),'') != 'player'"
        )]
        resident_ids.sort(key=lambda actor: hashlib.sha256((salt + actor).encode()).digest())
        result = []
        for actor in resident_ids[:limit]:
            resident = store.repositories.entities.get_agent(actor)
            state = resident.get("initial_state") or {}
            result.append({
                "name": str(resident.get("name") or actor)[:80], "role": str(resident.get("role") or "")[:80],
                "goal": str(resident.get("ultimate_goal") or "")[:180],
                "location": str(state.get("location_id") or "")[:80],
                "activity": str(state.get("active_behavior_id") or "")[:80],
            })
        return result

    def _should_kickoff(self, minute: int, config: dict[str, Any]) -> bool:
        """Create the first plan immediately when a short world needs direction."""

        if not config.get("kickoff") or minute <= 0:
            return False
        metadata = self._dependency("store").repositories.metadata
        if metadata.get("season_plan"):
            return False
        if metadata.get("last_completed_season_plan"):
            return False
        if metadata.get("season_plan_history"):
            return False
        return True

    def _remember_plan(self, plan: dict[str, Any]) -> None:
        """Keep a bounded plan history so a long run can be audited exactly."""

        store = self._dependency("store")
        with store.transaction():
            history = dict(store.repositories.metadata.get("season_plan_history", {}) or {})
            history[str(plan.get("plan_id") or "")] = plan
            if len(history) > 50:
                ordered = sorted(history.items(), key=lambda item: int(item[1].get("created_minute", 0) or 0))
                history = dict(ordered[-50:])
            store.repositories.metadata.set("season_plan_history", history)

    def _eligible_targets(self, config: dict[str, Any], counts: dict[str, int]) -> list[str]:
        """Targets that have not reached the configured per-cell unit cap."""

        targets = [str(item) for item in config.get("targets", [])]
        limit = int(config.get("max_cell_units", 0) or 0)
        if limit <= 0:
            return targets
        return [target for target in targets if int(counts.get(target, 0) or 0) < limit]

    def _mark_saturated(self, config: dict[str, Any], minute: int) -> None:
        store = self._dependency("store")
        limit = int(config.get("max_cell_units", 0) or 0)
        with store.transaction():
            store.repositories.metadata.set("season_plan", {
                "status": "saturated",
                "target_cell": None,
                "saturated_minute": int(minute),
                "max_cell_units": limit,
                "reason": f"all target cells reached max_cell_units={limit}",
            })

    def _write_plan(
        self, minute: int, target: str, counts: dict[str, int], reason: str | None = None,
        *, rotate: bool = True, mainline: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = self._config() or {}
        store = self._dependency("store")
        plan = {
            "plan_id": f"plan:{int(minute)}:{target}",
            "target_cell": target,
            "baseline": int(counts.get(target, 0)),
            "created_minute": int(minute),
            "expires_minute": int(minute) + int(config.get("window_minutes", 4320)),
            "reason": reason or "; ".join(
                item for item in (
                    ", ".join(f"{cell}={counts.get(cell, 0)}" for cell in counts),
                    f"context: {self._context_reason(config)}" if self._context_reason(config) else "",
                ) if item
            ),
            "status": "active",
        }
        if target == "verb" and config.get("behavior_action"):
            plan["behavior_action"] = config["behavior_action"]
        if target in config.get("evidence", {}):
            plan["evidence"] = config["evidence"][target]
        if target in config.get("followups", {}):
            plan["followup"] = config["followups"][target]
        if mainline:
            plan["mainline"] = mainline
        # The LLM Director calls its provider outside the transaction; the
        # write itself stays atomic with the queued God task.
        with store.transaction():
            metadata = store.repositories.metadata
            current = metadata.get("season_plan", {}) or {}
            if current.get("status") == "active":
                return current
            plan["event_cursor"] = store.repositories.events.latest_event_id()
            metadata.set("season_plan", plan)
            if rotate:
                metadata.set("director_rotation", int(metadata.get("director_rotation", 0) or 0) + 1)
            self._remember_plan(plan)
            if mainline:
                self._context.services["events"].publish(
                    "director.plan.started", "director", minute,
                    {"plan_id": plan["plan_id"], "target_cell": target, **mainline},
                    wake="configured",
                )
            ready = store.repositories.evolution.ready_evolution_tasks()
            if plan.get("behavior_action") or not any(item.get("task_type") == "god_structural" for item in ready):
                store.repositories.evolution.put_evolution_task(
                    f"director:growth:{int(minute)}",
                    "god",
                    "god",
                    "god_structural",
                    int(minute),
                    0,
                    status="ready",
                    data={"season_plan": plan},
                )
        return plan

    def plan_with_provider(self, provider: Any, minute: int) -> dict[str, Any] | None:
        """Let a separately configured Director model choose the next target cell.

        The provider is called before the plan is written so the SQLite write
        lock is never held across a network request.  The persisted plan
        contract and the God task are identical to ``tick``.
        """

        config = self._config()
        if config is None or minute <= 0:
            return None
        if not self._plan_due(minute, config):
            return None
        metadata = self._dependency("store").repositories.metadata
        counts = self._counts(config)
        active = self._advance_plan(minute, config, counts)
        if active is not None:
            return active
        targets = self._eligible_targets(config, counts)
        if not targets:
            self._mark_saturated(config, minute)
            return None
        last = metadata.get("last_completed_season_plan", {}) or {}
        last_target = str(last.get("target_cell") or "")
        instruction = (
            ("You are the Director. Choose a structural target cell from: " if config.get("context_events") else
             "You are the Director. Choose the least-covered structural target cell from: ")
            + ", ".join(targets)
            + ". Current counts: "
            + ", ".join(f"{cell}={counts.get(cell, 0)}" for cell in targets)
            + ". World context: "
            + (self._context_reason(config) or "none")
            + '. Return JSON {"target_cell": "<one of the listed cells>", "reason": "<short reason>"}.'
        )
        source_ids = set()
        if config.get("context_events"):
            events = self._dependency("store").repositories.events.recent_events_by_types(config["context_events"], limit=8)
            source_ids = {event["event_id"] for event in events}
            facts = [
                {key: str(event[key])[:180] for key in (
                    "event_id", "event_type", "world_minute", "actor_id", "summary", "fact", "result",
                    "entity_id", "definition_id", "beat_id", "rule_id", "source_event_id", "source_event_type",
                ) if event.get(key) is not None}
                for event in reversed(events)
            ]
            instruction += (
                " Choose the target warranted by this save's history, using coverage as a tie-breaker."
                " Ground the reason in a committed event when available. A story chapter describes a possibility;"
                " only its source event and subsequent outcomes establish physical changes. World events in chronological order: "
                + json.dumps(facts, ensure_ascii=False)
            )
        if config.get("context_residents"):
            residents = self._resident_context(config["context_residents"], minute)
            instruction += (
                " Consider what the committed events mean for these residents, their goals and their activities."
                " Propose a playable change someone here can use. Counts alone do not describe existing options. "
                + json.dumps(residents, ensure_ascii=False)
            )
        if config.get("context_events"):
            history = sorted((metadata.get("season_plan_history") or {}).values(),
                             key=lambda plan: plan.get("created_minute", 0))
            closed = [{key: str(plan[key])[:180] for key in
                       ("target_cell", "reason", "status", "closure_reason", "outcome_event_id") if key in plan}
                      | ({"objective": plan["mainline"]["objective"]} if plan.get("mainline") else {})
                      for plan in history if plan.get("status") in {"completed", "expired"}]
            if closed:
                instruction += (
                    " Recent closed plans: " + json.dumps(closed[-3:], ensure_ascii=False)
                    + ". If residents did not use a proposal, reconsider who can act and what they need."
                )
        if last_target in targets:
            instruction += f" Last completed: {last_target}."
        store = self._dependency("store")
        if generated_action_pacing(store):
            activity = store.repositories.activity.recent_generated_resident_actions(max(0, minute - config["window_minutes"]))
            if activity:
                instruction += (
                    " Recent resident completions of generated Actions: " + json.dumps(activity, ensure_ascii=False)
                    + ". When one Action dominates, move the world to a different decision or stage."
                )
        if config.get("narrative_direction") and source_ids:
            instruction += (
                ' Also return these top-level JSON keys: "objective" (one concrete direction for the current'
                ' arc, in the world language), "player_choice" (two plausible priorities and their tradeoff),'
                ' and "source_event_id" (one integer from the supplied events). For "player_choice", use a short string'
                ' or {"option_a":"...", "option_b":"...", "tradeoff":"..."}. Continue or revise the previous'
                ' objective using actual consequences. Describe which decision becomes possible without deciding'
                ' the player\'s action or announcing an outcome. Choose a target cell that makes the decision playable.'
            )
        from ..agents.audit import AgentAudit

        audit = AgentAudit(self._context.host)
        try:
            response = provider.complete_json(system="Director structural planning", context=instruction) or {}
        except Exception as exc:
            audit.record("director", "director", "director_plan", 1, instruction, {"error": str(exc)}, "llm_error")
            raise
        audit.record("director", "director", "director_plan", 1, instruction, response, "response")
        target = str(response.get("target_cell") or "").strip()
        if target not in targets:
            return None
        brief = None
        if config.get("narrative_direction"):
            source_id = response.get("source_event_id")
            objective = response.get("objective")
            choice = response.get("player_choice")
            if isinstance(choice, dict):
                parts = [choice.get(key) for key in ("option_a", "option_b", "tradeoff")]
                if all(isinstance(part, str) and part.strip() for part in parts):
                    choice = " / ".join(part.strip()[:100] for part in parts)
            if (type(source_id) is int and source_id in source_ids
                    and isinstance(objective, str) and objective.strip()
                    and isinstance(choice, str) and choice.strip()):
                brief = {"source_event_id": source_id, "objective": objective.strip()[:240],
                         "player_choice": choice.strip()[:320]}
        return self._write_plan(minute, target, counts, str(response.get("reason") or ""), mainline=brief)

    def _config(self) -> dict[str, Any] | None:
        root = self._dependency("store").repositories.metadata.get("evolution", {}) or {}
        if not role_enabled(root, "director"):
            return None
        root = root.get("evolution", root) if isinstance(root, dict) else {}
        config = root.get("director") if isinstance(root, dict) else None
        if not isinstance(config, dict) or int(config.get("cadence_minutes", 0) or 0) <= 0:
            return None
        return config

    def tick(self, minute: int) -> dict[str, Any] | None:
        """Evaluate the plan at a cadence boundary; return the active plan."""

        config = self._config()
        if config is None or minute <= 0:
            return None
        if not self._plan_due(minute, config):
            return None
        counts = self._counts(config)
        active = self._advance_plan(minute, config, counts)
        if active is not None:
            return active
        targets = self._eligible_targets(config, counts)
        if not targets:
            self._mark_saturated(config, minute)
            return None
        metadata = self._dependency("store").repositories.metadata
        rotation = int(metadata.get("director_rotation", 0) or 0)
        rotate = len(set(counts.values())) <= 1
        target = targets[rotation % len(targets)] if rotate else min(targets, key=lambda cell: (counts.get(cell, 0), targets.index(cell)))
        return self._write_plan(minute, target, counts, rotate=rotate)
