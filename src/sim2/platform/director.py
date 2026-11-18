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
                metadata.set("season_plan", plan)
            elif int(plan.get("expires_minute", 0) or 0) <= minute:
                plan = {**plan, "status": "expired", "completed_minute": int(minute)}
                metadata.set("season_plan", plan)
            else:
                return plan

        targets = [str(item) for item in config.get("targets", [])]
        if not targets:
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
