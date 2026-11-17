"""The world-level view of the God and World Builder roles: history, knowledge gaps, research sources and rules."""

from __future__ import annotations

from typing import Any

from ..actions.institutions import describe_institution_terms
from ..evolution.memory import role_memory_query
from .action_text import _display_number, label


# Bookkeeping events that fill the newest-first history window without telling
# a reviewer anything (one per world minute).
_TICK_EVENT_TYPES = ("world_minute.advanced",)


class WorldViewMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    def _recent_history_view(self, limit: int = 16) -> list[dict[str, Any]]:
        """Return a compact, visible-name projection of recent world facts."""

        events = self.events_repo.recent_events(limit=max(1, min(int(limit), 32)), exclude_types=_TICK_EVENT_TYPES)
        bt_labels = {
            str(item.get("id")): str(item.get("agent_label") or item.get("summary") or item.get("id"))
            for item in self._definitions("bt_def")
        }
        result: list[dict[str, Any]] = []
        for event in events:
            actor_id = str(event.get("actor_id") or "")
            try:
                actor_name = str(self.entities_repo.get_entity(actor_id).get("name") or actor_id or "world")
            except KeyError:
                actor_name = actor_id or "world"
            details: dict[str, Any] = {}
            for key in (
                "summary", "fact", "result", "status", "behavior_id", "definition_type",
                "name", "institution", "property", "delta", "allocations", "consumed", "remaining",
            ):
                value = event.get(key)
                if value not in (None, "", [], {}):
                    if key == "behavior_id":
                        value = bt_labels.get(str(value), value)
                    details[key] = value
            result.append({
                "event_id": int(event.get("event_id", 0) or 0),
                "event_type": str(event.get("event_type") or "event"),
                "world_minute": int(event.get("world_minute", 0) or 0),
                "actor": actor_name,
                "details": details,
            })
        return result

    def _knowledge_gap_view(self, *, status: str = "active", limit: int = 8) -> list[dict[str, Any]]:
        """Expose current research questions without internal identifiers."""

        result: list[dict[str, Any]] = []
        for row in self.memory_repo.list_knowledge_gaps(status=status, limit=limit):
            item: dict[str, Any] = {
                "question": str(row.get("question") or ""),
                "status": str(row.get("status") or status),
                "updated_minute": int(row.get("updated_minute") or 0),
            }
            if row.get("last_fact"):
                item["last_finding"] = str(row["last_fact"])[:240]
            total = int(row.get("total_stages") or 0)
            if total:
                item["stages"] = f"{len(row.get('completed_stages') or [])}/{total} completed"
            try:
                item["source"] = str(self.entities_repo.get_entity(str(row.get("current_source_id"))).get("name") or "")
            except (KeyError, TypeError):
                pass
            result.append(item)
        return result

    def _research_source_view(self, *, limit: int = 16) -> list[dict[str, Any]]:
        """Expose the current status of every declarative research source.

        Event history may contain an old sentence such as "the second checksum
        is unresolved" even after a later stage has verified it.  World agents
        need a small current-state projection beside that history so they do
        not reopen a resolved question.  The projection is derived from the
        generic source catalog and the knowledge-gap table; it does not know
        any world-specific source names or research meanings.
        """

        gaps = {
            str(row.get("current_source_id")): row
            for row in self.memory_repo.list_knowledge_gaps(status=None, limit=200)
            if row.get("current_source_id")
        }
        result: list[dict[str, Any]] = []
        for source in self.memory_repo.research_source_catalog():
            source_id = str(source.get("id") or "")
            if not source_id:
                continue
            discoveries = [
                item for item in (source.get("discoveries") or [])
                if isinstance(item, dict)
                and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"}
            ]
            gap = gaps.get(source_id) or {}
            completed = {str(item) for item in (gap.get("completed_stages") or [])}
            remaining = [
                item for item in discoveries
                if str(item.get("id") or "") not in completed
            ]
            status = str(gap.get("status") or ("resolved" if not remaining else "active"))
            row: dict[str, Any] = {
                "name": str(source.get("name") or source_id),
                "status": status,
                "stages": f"{len(discoveries) - len(remaining)}/{len(discoveries)} completed",
                "remaining_stages": len(remaining),
            }
            if remaining:
                question = remaining[0].get("question") or remaining[0].get("fact")
                if question:
                    row["next_question"] = str(question)[:300]
            elif gap.get("last_fact"):
                row["last_finding"] = str(gap["last_fact"])[:300]
            result.append(row)
            if len(result) >= max(1, int(limit)):
                break
        return result

    def _build_world_view(self, actor_id: str, role: str) -> dict[str, Any]:
        minute = self.store.repositories.metadata.get("world_minute", 0)
        all_events = self.events_repo.recent_events(limit=20)
        recent_world_changes = self.events_repo.recent_world_change_events(limit=6)
        world_proposals = self.evolution_repo.list_world_proposals(limit=8)
        recent_agent_outcomes = self.events_repo.recent_behavior_outcomes(limit=6)
        world_trends = self.events_repo.world_trends(current_minute=int(minute))
        god_memory_query = "world diagnosis proposal outcome trend " + " ".join(
            str(item.get("event_type") or "") for item in recent_world_changes[:4]
        )
        god_memories = [self._humanize_record(item) for item in role_memory_query(self.store, "god", god_memory_query, limit=6)]
        bt_defs = {item["id"]: item for item in self._definitions("bt_def")}
        for outcome in recent_agent_outcomes:
            behavior = bt_defs.get(str(outcome.get("behavior_id", "")), {})
            outcome["behavior_choice"] = behavior.get("agent_label") or behavior.get("summary") or behavior.get("description") or "a behavior"
            outcome_actor_id = outcome.get("actor_id")
            if outcome_actor_id:
                try:
                    outcome["actor_name"] = self.entities_repo.get_entity(str(outcome_actor_id)).get("name", "a character")
                except KeyError:
                    outcome["actor_name"] = "a character"
        recent_world_events = (recent_world_changes + [item for item in all_events if item.get("actor_id") == actor_id])[:6]
        active_count = sum(
            1 for agent in self.entities_repo.list_agents()
            if (agent.get("initial_state") or {}).get("status") in {"executing", "paused"}
        )
        world_properties = self.store.repositories.metadata.get("world_properties", {}) or {}
        world_summary = "; ".join(
            f"{str(key).replace('_', ' ')}={_display_number(value)}"
            for key, value in world_properties.items()
        ) or "none"
        world_id = str(self.store.repositories.metadata.get("world_id", "world"))
        configured_regeneration = []
        for raw in (self.store.repositories.metadata.get("rules", {}) or {}).get("resource_regeneration", []) or []:
            if not isinstance(raw, dict) or not raw.get("resource_id"):
                continue
            resource_id = str(raw.get("resource_id"))
            try:
                resource_name = str(self.entities_repo.get_entity(resource_id).get("name") or resource_id)
            except KeyError:
                resource_name = resource_id.replace("_", " ")
            configured_regeneration.append({
                "resource": resource_name,
                "every_minutes": int(raw.get("every_minutes", 0) or 0),
                "amount": raw.get("amount", 0),
                "cap": raw.get("cap"),
            })
        active_story_threads = self._active_story_threads()
        prompt_protocol, platform_rules = self._prompt_sections(role)
        behavior_catalog = []
        for behavior in bt_defs.values():
            behavior_catalog.append({
                "name": behavior.get("agent_label") or behavior.get("summary") or label(str(behavior.get("id", "behavior"))),
                "purpose": behavior.get("description") or behavior.get("summary") or "configured behavior",
                "success": [str(item).replace("_", " ") for item in (behavior.get("success_results") or [])[:3]],
            })
        action_catalog = []
        for action in self._definitions("action_def"):
            action_catalog.append({
                "name": action.get("agent_label") or label(str(action.get("id", "action"))),
                "purpose": action.get("agent_summary") or action.get("description") or "configured action",
                "parameters": [str(key) for key in (action.get("parameters") or {}).keys()],
                "parameter_schema": {
                    str(key): (value if isinstance(value, dict) else {"type": str(value)})
                    for key, value in (action.get("parameters") or {}).items()
                },
                "parameter_defaults": dict(action.get("parameter_defaults") or {}),
                "success": [str(item).replace("_", " ") for item in ([action.get("success", {}).get("result")] if isinstance(action.get("success"), dict) else []) if item],
            })
        condition_fields = [
            {"condition": f'world["{str(key)}"]', "meaning": str(key).replace("_", " ")}
            for key in (self.store.repositories.metadata.get("world_properties", {}) or {})
        ]
        condition_fields.extend(
            {"condition": f"event.recent.{event_type}", "meaning": "a recent validated public event"}
            for event_type in self._recent_event_types()[:16]
            if event_type not in {"world_minute.advanced", "world.created"}
        )
        visible_entities = []
        for entity_type in ("location", "agent", "faction", "object", "resource", "material"):
            for item in self.entities_repo.entities_of_type(entity_type):
                name = str(item.get("name") or "").strip()
                if name:
                    visible_entities.append({"name": name, "type": entity_type})
        return {
            "context_version": "1",
            "role": role,
            "prompt_protocol": prompt_protocol,
            "platform_rules": platform_rules,
            "world_time": self._world_time(int(minute)),
            "self": {"id": actor_id, "name": actor_id, "role": role, "status": "ready", "location": "world", "goal": "preserve a coherent, evolving world"},
            "identity": {"traits": {}, "beliefs": [], "capabilities": {"world_scope": True}},
            "needs": {},
            "inventory": {},
            "injuries": [],
            "drives": [],
            "observation": {"facts": [f"World properties: {world_summary}", f"Active behaviors: {active_count}"], "source": "platform_world_summary"},
            "nearby_entities": [],
            "known_facts": [],
            "recent_actions": recent_world_events,
            "recent_history": self._recent_history_view(20),
            "active_story_threads": active_story_threads,
            "knowledge_gaps": self._knowledge_gap_view(status="active", limit=12),
            "research_sources": self._research_source_view(limit=16),
            "memories": god_memories,
            "long_term_memories": god_memories,
            "short_term_memories": [],
            "memory_query": god_memory_query,
            "recent_world_changes": recent_world_changes,
            "world_proposals": world_proposals,
            "recent_agent_outcomes": recent_agent_outcomes,
            "world_trends": world_trends,
            "configured_resource_regeneration": configured_regeneration,
            "civilization_progress": self.activity_repo.list_progress(world_id, limit=20),
            "world_progress_candidates": [
                {
                    "progress_key": str(key),
                    "required": float((value or {}).get("required", 1) or 1),
                    "description": str((value or {}).get("description") or ""),
                    "evidence_event_types": [str(item) for item in ((value or {}).get("evidence_event_types") or [])],
                    "evidence_behaviors": [str(item) for item in ((value or {}).get("evidence_behaviors") or [])],
                    "evidence_sources": list((value or {}).get("evidence_sources") or []),
                }
                for key, value in ((self.store.repositories.metadata.get("rules", {}) or {}).get("civilization_progress", {}) or {}).items()
                if isinstance(value, dict)
            ],
            "pending_institutions": [
                {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "proposed"}
                for item in self.social_repo.list_institutions(status="proposed", limit=20)
            ],
            "active_institutions": [
                {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "active", "terms": describe_institution_terms(item)}
                for item in self.social_repo.list_institutions(status="active", limit=20)
            ],
            "institution_ledgers": [
                {"name": str(item.get("name", "")), "entries": self.social_repo.institution_ledger_totals(str(item.get("institution_id")), None)[:20]}
                for item in self.social_repo.list_institutions(status="active", limit=20)
            ],
            "active_world_rules": self.rule_compiler.model_view(
                self._world_rule_labels(),
                context={"recent_event_types": self._recent_event_types()},
                world=world_properties,
            ),
            "registered_behaviors": behavior_catalog[:32],
            "registered_actions": action_catalog[:32],
            "visible_entities": visible_entities[:64],
            "condition_examples": {
                "capability": "actor.can_<capability>",
                "location": "actor.state.location_id == <visible location name>",
                "world_property": 'world["<configured property>"]',
                "recent_event": "event.recent.<event type>",
            },
            "rule_condition_fields": condition_fields[:24],
            "direct_relationships": [],
            "active_task": {"bt": None, "action": None, "remaining_minutes": 0},
            "executable_behaviors": [],
            "last_platform_result": recent_world_events[0] if recent_world_events else None,
            "tools": self._role_tools(role),
        }
