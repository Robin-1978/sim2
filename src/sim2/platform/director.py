"""Deterministic Director layer: choose the next structural growth target.

The Director owns intent, not content.  It reads the shared growth ledger and
writes one renewable ``season_plan`` (target cell, baseline, expiry).  Story
uses the plan for narrative direction; God receives the same target in its
context and a ``god_structural`` task.  Neither role is allowed to substitute a
research source for the requested structural cell.

This service deliberately uses no provider.  A future LLM showrunner can
replace the target-selection policy while keeping the same persisted plan
contract.
"""

from __future__ import annotations

from typing import Any

from ..world.growth import build_growth_ledger

class DirectorRuntimeMixin:
    """Select and advance the world's next structural growth target."""

    def _counts(self, config: dict[str, Any]) -> dict[str, int]:
        ledger = build_growth_ledger(self._dependency("store"))
        return {
            str(cell): int((ledger.get("cells", {}).get(str(cell), {}) or {}).get("count", 0))
            for cell in config.get("targets", [])
        }

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

    def _write_plan(self, minute: int, target: str, counts: dict[str, int], reason: str | None = None) -> dict[str, Any]:
        config = self._config() or {}
        store = self._dependency("store")
        plan = {
            "plan_id": f"plan:{int(minute)}:{target}",
            "target_cell": target,
            "baseline": int(counts.get(target, 0)),
            "created_minute": int(minute),
            "expires_minute": int(minute) + int(config.get("window_minutes", 4320)),
            "reason": reason or ", ".join(f"{cell}={counts.get(cell, 0)}" for cell in counts),
            "status": "active",
        }
        # The LLM Director calls its provider outside the transaction; the
        # write itself stays atomic with the queued God task.
        with store.transaction():
            metadata = store.repositories.metadata
            metadata.set("season_plan", plan)
            metadata.set("director_rotation", int(metadata.get("director_rotation", 0) or 0) + 1)
            self._remember_plan(plan)
            ready = store.repositories.evolution.ready_evolution_tasks()
            if not any(item.get("task_type") == "god_structural" for item in ready):
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
        if config is None or minute <= 0 or minute % int(config["cadence_minutes"]):
            return None
        store = self._dependency("store")
        metadata = store.repositories.metadata
        plan = dict(metadata.get("season_plan", {}) or {})
        counts = self._counts(config)
        if plan.get("status") == "active":
            target = str(plan.get("target_cell") or "")
            if target in counts and counts[target] > int(plan.get("baseline", 0) or 0):
                plan = {**plan, "status": "completed", "completed_minute": int(minute)}
                with store.transaction():
                    metadata.set("last_completed_season_plan", plan)
                    metadata.set("season_plan", plan)
                    self._remember_plan(plan)
            elif int(plan.get("expires_minute", 0) or 0) <= minute:
                plan = {**plan, "status": "expired", "completed_minute": int(minute)}
                with store.transaction():
                    metadata.set("season_plan", plan)
                    self._remember_plan(plan)
            else:
                return plan
        targets = self._eligible_targets(config, counts)
        if not targets:
            self._mark_saturated(config, minute)
            return None
        last = metadata.get("last_completed_season_plan", {}) or {}
        last_target = str(last.get("target_cell") or "")
        instruction = (
            "You are the Director. Choose the least-covered structural target cell from: "
            + ", ".join(targets)
            + ". Current counts: "
            + ", ".join(f"{cell}={counts.get(cell, 0)}" for cell in targets)
            + '. Return JSON {"target_cell": "<one of the listed cells>", "reason": "<short reason>"}.'
        )
        if last_target in targets:
            instruction += f" Last completed: {last_target}."
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
        return self._write_plan(minute, target, counts, str(response.get("reason") or ""))

    def _config(self) -> dict[str, Any] | None:
        root = self._dependency("store").repositories.metadata.get("evolution", {}) or {}
        root = root.get("evolution", root) if isinstance(root, dict) else {}
        config = root.get("director") if isinstance(root, dict) else None
        if not isinstance(config, dict) or int(config.get("cadence_minutes", 0) or 0) <= 0:
            return None
        return config

    def tick(self, minute: int) -> dict[str, Any] | None:
        """Evaluate the plan at a cadence boundary; return the active plan."""

        config = self._config()
        if config is None or minute <= 0 or minute % int(config["cadence_minutes"]):
            return None
        store = self._dependency("store")
        metadata = store.repositories.metadata
        plan = dict(metadata.get("season_plan", {}) or {})
        ledger = build_growth_ledger(store)
        counts = {
            str(cell): int((ledger.get("cells", {}).get(str(cell), {}) or {}).get("count", 0))
            for cell in config.get("targets", [])
        }

        if plan.get("status") == "active":
            target = str(plan.get("target_cell") or "")
            if target in counts and counts[target] > int(plan.get("baseline", 0) or 0):
                plan = {**plan, "status": "completed", "completed_minute": int(minute)}
                metadata.set("last_completed_season_plan", plan)
                metadata.set("season_plan", plan)
                self._remember_plan(plan)
            elif int(plan.get("expires_minute", 0) or 0) <= minute:
                plan = {**plan, "status": "expired", "completed_minute": int(minute)}
                metadata.set("season_plan", plan)
                self._remember_plan(plan)
            else:
                return plan

        targets = self._eligible_targets(config, counts)
        if not targets:
            self._mark_saturated(config, minute)
            return None
        rotation = int(metadata.get("director_rotation", 0) or 0)
        if len(set(counts.values())) <= 1:
            target = targets[rotation % len(targets)]
            rotation += 1
        else:
            target = min(targets, key=lambda cell: (counts.get(cell, 0), targets.index(cell)))
        plan = {
            "plan_id": f"plan:{int(minute)}:{target}",
            "target_cell": target,
            "baseline": int(counts.get(target, 0)),
            "created_minute": int(minute),
            "expires_minute": int(minute) + int(config.get("window_minutes", 4320)),
            "reason": ", ".join(f"{cell}={counts.get(cell, 0)}" for cell in targets),
            "status": "active",
        }
        metadata.set("season_plan", plan)
        metadata.set("director_rotation", rotation)
        self._remember_plan(plan)
        ready = store.repositories.evolution.ready_evolution_tasks()
        if not any(item.get("task_type") == "god_structural" for item in ready):
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
