"""Research tracks, pending research sources and configured progression stages offered to a character."""

from __future__ import annotations

from typing import Any

from ..world.navigation import reachable_locations
from .action_text import label, predicate_text
from .decision_frame import DecisionFrame


class ProgressViewMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    def _research_tracks(self, frame: DecisionFrame) -> list[dict[str, Any]]:
        research_sources, known_fact_keys, catalog_ids = frame.research_sources, frame.known_fact_keys, frame.catalog_ids
        research_tracks: list[dict[str, Any]] = []
        known_fact_set = set(known_fact_keys)
        seen_sources: set[str] = set()
        for source in research_sources:
            source_id = str(source.get("id"))
            seen_sources.add(source_id)
            research_tracks.append({
                "source": str(source.get("name") or source_id),
                "location": str(source.get("location_name") or source.get("location_id") or "configured source"),
                "stages": [
                    {
                        "fact_key": str(item.get("id")),
                        "status": str(item.get("status") or "active"),
                        "available": str(item.get("id")) not in known_fact_set and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"},
                        "required": item.get("required") or source.get("research_required") or (self.store.repositories.metadata.get("rules", {}) or {}).get("research_default_required", 1),
                    }
                    for item in (source.get("discoveries") or [])
                ],
            })
        for behavior_id in catalog_ids:
            for node in self._iter_behavior_actions(str(behavior_id)):
                params = node.get("parameters") or {}
                source_id = params.get("source_id")
                if not source_id or str(source_id).startswith("context.") or str(source_id) in seen_sources:
                    continue
                discoveries = self.memory_repo.research_discoveries(str(source_id))
                if not discoveries:
                    continue
                seen_sources.add(str(source_id))
                try:
                    source_entity = self.entities_repo.get_entity(str(source_id))
                    source_name = source_entity.get("name", source_id)
                    source_location_id = (source_entity.get("initial_state") or {}).get("location_id") or source_entity.get("location_id")
                    source_location = self.entities_repo.get_entity(str(source_location_id)).get("name", source_location_id) if source_location_id else "configured source"
                except KeyError:
                    source_entity = {}
                    source_name = source_id
                    source_location = "configured source"
                research_tracks.append({
                    "source": str(source_name),
                    "location": str(source_location),
                    "stages": [
                        {
                            "fact_key": str(item.get("id")),
                            "status": str(item.get("status") or "active"),
                            "available": str(item.get("id")) not in known_fact_set and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"},
                            "required": item.get("required") or source_entity.get("research_required") or ((source_entity.get("initial_state") or {}).get("research_required") if isinstance(source_entity.get("initial_state"), dict) else None) or (self.store.repositories.metadata.get("rules", {}) or {}).get("research_default_required", 1),
                        }
                        for item in discoveries
                    ],
                })
        return research_tracks

    def _pending_research_sources(
        self,
        agent: dict[str, Any],
        known_fact_keys: list[Any] | set[Any],
        location_id: str | None,
        configured_source_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return reachable source entities with at least one open stage.

        Research is a generic capability.  The catalog is assembled from
        persisted entity data, so a source registered later by God is visible
        to the same BT as an opening source.  Only the source's configured
        stages are exposed; object-specific BT definitions are never needed.
        """

        known = {str(item) for item in (known_fact_keys or []) if item}
        reachable = reachable_locations(self.store, location_id)
        if location_id:
            reachable.add(str(location_id))
        result: list[dict[str, Any]] = []
        configured_source_ids = {str(item) for item in (configured_source_ids or set())}
        agent_name = str(agent.get("name") or agent.get("id") or "")
        for source in self.memory_repo.research_source_catalog():
            source_id = str(source.get("id") or "")
            if not source_id:
                continue
            source_state = {}
            try:
                source_entity = self.entities_repo.get_entity(source_id)
                source_state = source_entity.get("initial_state") if isinstance(source_entity.get("initial_state"), dict) else {}
            except KeyError:
                source_entity = {}
            assignees = source_entity.get("research_assignees", source_state.get("research_assignees", []))
            if isinstance(assignees, str):
                assignees = [assignees]
            assigned = {str(item) for item in (assignees or [])}
            assigned_ids = {
                str(self.targets.canonical_id(item) or item)
                for item in assigned
            }
            if source_id not in configured_source_ids and str(agent.get("id")) not in assigned_ids and agent_name not in assigned:
                continue
            source_location = source.get("location_id")
            if source_location and reachable and str(source_location) not in reachable:
                continue
            discoveries = list(source.get("discoveries") or [])
            stage_status = self.memory_repo.research_stage_status(str(agent.get("id")), source_id)
            pending_ids = {str(item.get("id")) for item in stage_status if item.get("available")}
            pending = [
                item for item in discoveries
                if isinstance(item, dict)
                and (str(item.get("id") or "") in pending_ids or (not stage_status and str(item.get("id") or "") not in known))
                and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"}
            ]
            if not pending and discoveries:
                continue
            try:
                location_name = self.entities_repo.get_entity(str(source_location)).get("name", source_location) if source_location else "configured source"
            except KeyError:
                location_name = source_location or "configured source"
            result.append({
                **source,
                "location_name": str(location_name),
                "remaining_stages": len(pending) if discoveries else 1,
            })
        result.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))
        return result

    def _progression_goals(
        self,
        agent: dict[str, Any],
        *,
        available: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        bt_defs: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project configurable world-stage goals and their current blockers.

        A progression entry is content, not a world branch.  It names visible
        target/support behaviors and the validated event contract that closes
        the stage.  Platform still decides whether a behavior is executable.
        """
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        definitions = rules.get("progression", []) if isinstance(rules, dict) else []
        if not isinstance(definitions, list):
            return []
        agent_name = str(agent.get("name") or agent.get("id") or "")
        origin_id = str(agent.get("origin_id") or "")
        world_properties = self.store.repositories.metadata.get("world_properties", {}) or {}
        available_ids = {
            str(item.get("ref") or "").removeprefix("behavior:")
            for item in available
            if isinstance(item, dict)
        }
        diagnostics_by_id = {
            str(item.get("behavior_id") or ""): [str(value) for value in (item.get("failed") or [])]
            for item in diagnostics
            if isinstance(item, dict)
        }

        def visible_behavior(behavior_id: str) -> str:
            definition = bt_defs.get(str(behavior_id), {})
            return str(definition.get("agent_label") or definition.get("summary") or label(behavior_id))

        def completion(contract: Any) -> dict[str, Any]:
            contract = contract if isinstance(contract, dict) else {}
            required = max(1, int(contract.get("count", 1) or 1))
            event_type = str(contract.get("event_type") or "")
            if not event_type:
                return {"completed": False, "current": 0, "required": required}
            match = contract.get("match") if isinstance(contract.get("match"), dict) else {}
            distinct_by = [str(value) for value in (contract.get("distinct_by") or []) if str(value)]
            current = self.events_repo.matching_event_count(
                event_type=event_type,
                match=match,
                distinct_by=distinct_by,
                actor_id=str(agent.get("id") or "") if contract.get("self_only") else "",
            )
            return {
                "completed": current >= required,
                "current": min(current, required),
                "required": required,
                "event_type": event_type,
            }

        goals: list[dict[str, Any]] = []
        for raw in definitions:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            audience = {str(value) for value in (raw.get("audience") or [])}
            player_audience = "player" in audience and str(agent.get("role") or "") == "player"
            if audience and not player_audience and agent_name not in audience and str(agent.get("id")) not in audience:
                continue
            goal_origins = {str(value) for value in (raw.get("origins") or [])}
            if goal_origins and origin_id not in goal_origins:
                continue
            visible_when = str(raw.get("visible_when") or "").strip()
            if visible_when and not self.conditions.evaluate(
                visible_when,
                agent=agent,
                target={},
                world=world_properties,
                context={},
            ):
                continue
            target_ids = [str(value) for value in (raw.get("target_behaviors") or [])]
            support_ids = [str(value) for value in (raw.get("support_behaviors") or [])]
            target_choices = [visible_behavior(value) for value in target_ids]
            support_choices = [visible_behavior(value) for value in support_ids if value in available_ids]
            event_progress = completion(raw.get("complete_when"))
            ready_targets = [visible_behavior(value) for value in target_ids if value in available_ids]
            ready_refs = [f"behavior:{value}" for value in target_ids if value in available_ids]
            support_refs = [f"behavior:{value}" for value in support_ids if value in available_ids]
            blockers: list[str] = []
            for behavior_id in target_ids + support_ids:
                if behavior_id in available_ids:
                    continue
                blockers.extend(predicate_text(value) for value in diagnostics_by_id.get(behavior_id, []))
            if event_progress["completed"]:
                status = "completed"
            elif ready_targets:
                status = "ready"
            elif support_choices or blockers:
                status = "needs_support"
            else:
                status = "blocked"
            if status == "completed":
                continue
            goal = {
                "id": str(raw["id"]),
                "label": str(raw.get("label") or label(str(raw["id"]))),
                "summary": str(raw.get("summary") or raw.get("label") or label(str(raw["id"]))),
                "priority": float(raw.get("priority", 0.5) or 0.5),
                "status": status,
                "target_behaviors": target_choices,
                "ready_behaviors": ready_targets,
                "ready_behavior_refs": ready_refs,
                "support_behaviors": support_choices,
                "support_behavior_refs": support_refs,
                "missing_requirements": list(dict.fromkeys(blockers))[:6],
                "progress": (
                    {key: event_progress[key] for key in ("current", "required")}
                    if raw.get("complete_when") else {"current": 0, "required": 0}
                ),
            }
            goals.append(goal)
        goals.sort(key=lambda item: (-float(item.get("priority", 0)), str(item.get("label", ""))))
        return goals[:8]
