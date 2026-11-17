"""The Story role's view: the validated source event, audience candidates and active story threads."""

from __future__ import annotations

import json
from typing import Any

from ..evolution.story import eligible_story_beats, story_evidence_event_types
from ..evolution.memory import role_memory_query


class StoryViewMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    def _active_story_threads(self, limit: int = 6) -> list[dict[str, Any]]:
        """Project active Story threads for world-level consumers.

        Character views receive only beats addressed to that character. God
        needs the bounded global thread list as causal input so it can extend
        an unresolved consequence instead of treating the Story output as
        unrelated prose.
        """

        story_root = self.store.repositories.metadata.get("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        story_state = self.store.repositories.metadata.get("story_state", {}) or {}
        story_defs = {
            str(item.get("id")): item
            for item in list(story_root.get("beats", []) or []) + list(story_state.get("emergent_beats", []) or [])
            if isinstance(item, dict) and item.get("id")
        }
        threads: list[dict[str, Any]] = []
        for beat_id, entry in story_state.items():
            if beat_id == "emergent_beats" or not isinstance(entry, dict) or entry.get("status") != "active":
                continue
            definition = story_defs.get(str(beat_id), {})
            audience: list[str] = []
            for value in entry.get("audience", []) or []:
                try:
                    audience.append(str(self.entities_repo.get_entity(str(value)).get("name") or value))
                except KeyError:
                    audience.append(str(value))
            threads.append({
                "summary": str(definition.get("summary") or definition.get("fact") or entry.get("summary") or "validated story thread"),
                "goal_hint": str(definition.get("goal_hint") or entry.get("goal_hint") or ""),
                "audience": audience,
                "research_source": str(definition.get("research_source") or entry.get("research_source_id") or ""),
                "world_minute": int(entry.get("started_minute", 0) or 0),
            })
        threads.sort(key=lambda item: item["world_minute"], reverse=True)
        return threads[: max(1, int(limit))]

    def _build_story_view(self, actor_id: str, role: str, *, source_event_id: int | None = None) -> dict[str, Any]:
        """Build the Story view from the recent validated events, the source event and the eligible beats."""

        minute = int(self.store.repositories.metadata.get("world_minute", 0))
        story_root = self.store.repositories.metadata.get("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        story_state = self.store.repositories.metadata.get("story_state", {}) or {}
        recent_events = self.events_repo.recent_events(limit=80)
        source_event = self._story_source_event(recent_events, source_event_id)
        story_memory_query = self._story_memory_query(source_event, story_state)
        story_memories = [self._humanize_record(item) for item in role_memory_query(self.store, "story", story_memory_query, limit=6)]
        validated_source: dict[str, Any] = {}
        if source_event:
            validated_source = self._validated_story_source(source_event)
            # The view reports the source event's actor as ``self.id``; with no source event it keeps the requested id.
            actor_id = source_event.get("actor_id")
        audience_candidates = self._story_audience_candidates(recent_events)
        observed_events = {str(item.get("event_type", "")) for item in recent_events}
        fact_rows = self.store.connection.execute("SELECT fact_key,data_json FROM knowledge_fact ORDER BY id DESC").fetchall()
        observed_facts = {str(row["fact_key"]) for row in fact_rows}
        choices = self._story_beat_choices(story_root, story_state, source_event, observed_events, observed_facts)
        prompt_protocol, platform_rules = self._prompt_sections(role)
        known_facts = self._story_known_facts(fact_rows)
        return {
            "context_version": "1", "role": role,
            "prompt_protocol": prompt_protocol, "platform_rules": platform_rules,
            "world_time": self._world_time(minute),
            "self": {"id": actor_id, "name": "Story Agent", "role": role, "status": "ready", "location": "world", "goal": "Turn validated discoveries into coherent continuing story beats."},
            "identity": {"traits": {}, "beliefs": [], "capabilities": {"story_scope": True}},
            "needs": {}, "inventory": {}, "injuries": [], "drives": [],
            "observation": {"facts": [self._visible_text(str(item.get("event_type", "event")) + ": " + str(item.get("summary") or item.get("fact") or item.get("result") or "validated world result")) for item in recent_events[:10]], "source": "validated_world_events"},
            "validated_source": validated_source,
            "nearby_entities": [], "known_facts": [{"fact": fact, "source_type": "world_knowledge", "confidence": 1.0} for fact in known_facts],
            "audience_candidates": audience_candidates,
            "recent_actions": recent_events[:8], "recent_story_events": [item for item in recent_events if str(item.get("event_type", "")).startswith("story.")][:8],
            "recent_history": self._recent_history_view(16),
            "knowledge_gaps": self._knowledge_gap_view(status="active", limit=8),
            "research_sources": self._research_source_view(limit=12),
            "memories": story_memories,
            "long_term_memories": story_memories,
            "short_term_memories": [],
            "memory_query": story_memory_query,
            "recent_world_changes": [], "recent_agent_outcomes": [], "direct_relationships": [],
            "active_task": {"bt": None, "action": None, "remaining_minutes": 0},
            "executable_behaviors": [], "story_beats": choices[:12],
            "world_progress_candidates": self._world_progress_candidates(),
            "active_world_rules": self.rule_compiler.model_view(
                self._world_rule_labels(),
                context={"recent_event_types": self._recent_event_types()},
                world=self.store.repositories.metadata.get("world_properties", {}) or {},
            ),
            "last_platform_result": recent_events[0] if recent_events else None,
            "tools": self._role_tools(role),
        }

    def _story_source_event(self, recent_events: list[dict[str, Any]], source_event_id: int | None) -> dict[str, Any] | None:
        """Find the event the Story session is about: the one named by ``source_event_id`` or the latest validated one."""

        # Prefer the validated information event over the later wrapper
        # ``behavior.completed`` row.  The wrapper often says only
        # ``research_progressed``; using it as the source hides the completed
        # fact and makes Story search for evidence it already received.
        source_event = None
        if source_event_id is not None:
            source_event = next((item for item in recent_events if int(item.get("event_id", -1) or -1) == int(source_event_id)), None)
        if source_event is None and source_event_id is None:
            source_event = next(
                (
                    item for item in recent_events
                    if str(item.get("event_type", "")) in {
                        "exploration.completed", "research.completed", "object.recovered",
                        "relationship.changed", "negotiation.result_recorded",
                    }
                    and item.get("actor_id") not in {"story", "god"}
                ),
                None,
            )
        if source_event is None and source_event_id is None:
            source_event = next(
                (
                    item for item in recent_events
                    if str(item.get("event_type", "")) == "behavior.completed"
                    and item.get("actor_id") not in {"story", "god"}
                ),
                None,
            )
        return source_event

    def _story_memory_query(self, source_event: dict[str, Any] | None, story_state: dict[str, Any]) -> str:
        """Build the query that recalls Story memories from the thread keywords, the source event and the latest beats."""

        return " ".join(
            str(item) for item in (
                "story narrative thread",
                (source_event or {}).get("event_type") if isinstance(source_event, dict) else "",
                (source_event or {}).get("result") if isinstance(source_event, dict) else "",
                " ".join(str(item.get("summary") or "") for item in (story_state.get("emergent_beats", []) or [])[-3:] if isinstance(item, dict)),
            ) if item
        )

    def _validated_story_source(self, source_event: dict[str, Any]) -> dict[str, Any]:
        """Describe the source event for Story: who did what where, its result and the matching public fact."""

        outcome = source_event.get("outcome") if isinstance(source_event.get("outcome"), dict) else {}
        actual = outcome.get("actual") if isinstance(outcome.get("actual"), dict) else {}
        actor_id = source_event.get("actor_id")
        try:
            actor_name = self.entities_repo.get_entity(str(actor_id)).get("name", actor_id) if actor_id else "The world"
        except KeyError:
            actor_name = actor_id or "The world"
        location_id = actual.get("location") or source_event.get("location_id")
        try:
            location_name = self.entities_repo.get_entity(str(location_id)).get("name", location_id) if location_id else "unknown"
        except KeyError:
            location_name = location_id or "unknown"
        validated_source = {
            "event_type": source_event.get("event_type"),
            "actor": actor_name,
            "location": location_name,
            "result": self._visible_text(outcome.get("result") or source_event.get("result") or source_event.get("fact") or "recorded"),
            "fact": self._humanize_fact(source_event.get("fact") or (outcome.get("expected") or {}).get("summary") or ""),
            "world_minute": source_event.get("world_minute"),
        }
        source_id = source_event.get("source_id")
        if source_id:
            try:
                validated_source["research_source"] = self.entities_repo.get_entity(str(source_id)).get("name", source_id)
            except KeyError:
                validated_source["research_source"] = str(source_id)
        if str(source_event.get("event_type")) == "institution.allocation_completed":
            self._describe_allocation(validated_source, source_event)
        self._attach_source_fact(validated_source, source_event, actor_id)
        return validated_source

    def _describe_allocation(self, validated_source: dict[str, Any], source_event: dict[str, Any]) -> None:
        """Replace the generic result with what an institution allocation handed out."""

        consumed = source_event.get("consumed")
        remaining = source_event.get("remaining")
        allocations = source_event.get("allocations") or []
        item = str(source_event.get("item") or "resource")
        validated_source["result"] = (
            f"Allocated {consumed} {item}; {remaining} remains in the shared pool."
        )
        validated_source["allocation_summary"] = [
            {
                "subject": self._visible_text(str(row.get("subject", "a character"))),
                "amount": row.get("total"),
            }
            for row in allocations[:12]
            if isinstance(row, dict)
        ]

    def _attach_source_fact(self, validated_source: dict[str, Any], source_event: dict[str, Any], actor_id: Any) -> None:
        """Add the public fact the source event's actor recorded at the same minute, matched by source type."""

        # Information facts are persisted separately from the event
        # envelope.  Include the matching public fact in the source view
        # so Story never has to guess a key or query a private record.
        source_minute = int(source_event.get("world_minute", -1) or -1)
        expected_source_types = {
            "research.completed": {"research"},
            "exploration.completed": {"exploration"},
            "object.recovered": {"recovery"},
            "entity.installed": {"deployment"},
        }.get(str(source_event.get("event_type") or ""), set())
        fact_rows = self.store.connection.execute("SELECT agent_id,fact_key,data_json FROM knowledge_fact ORDER BY id DESC LIMIT 64").fetchall()
        for row in fact_rows:
            try:
                fact_data = json.loads(row["data_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            fact_source_type = str(fact_data.get("source_type") or "")
            if expected_source_types and fact_source_type not in expected_source_types:
                continue
            if not expected_source_types and fact_source_type not in {"research", "exploration", "recovery", "deployment"}:
                continue
            if str(row["agent_id"] or "") != str(actor_id) or fact_data.get("observed_at") not in {source_minute, str(source_minute)}:
                continue
            if fact_data.get("fact"):
                validated_source["fact"] = self._humanize_fact(str(fact_data["fact"]))
                validated_source["fact_key"] = str(row["fact_key"] or fact_data.get("source_id") or "")
                break

    def _story_beat_choices(
        self,
        story_root: dict[str, Any],
        story_state: dict[str, Any],
        source_event: dict[str, Any] | None,
        observed_events: set[str],
        observed_facts: set[str],
    ) -> list[dict[str, Any]]:
        """List the configured and emergent beats the evidence supports, as choices the Story model can name."""

        choices: list[dict[str, Any]] = []
        scoped_beats = eligible_story_beats(
            list(story_root.get("beats", []) or []) + list(story_state.get("emergent_beats", []) or []),
            story_state,
            source_event_type=story_evidence_event_types(source_event),
            observed_events=observed_events,
            observed_facts=observed_facts,
        )
        for beat in scoped_beats:
            beat_id = str(beat.get("id", ""))
            trigger_events = {str(item) for item in (beat.get("trigger_events", []) or [])}
            trigger_facts = {str(item) for item in (beat.get("trigger_facts", []) or [])}
            choices.append({
                "ref": f"story:{beat_id}",
                "choice": str(beat.get("agent_label") or beat.get("summary") or beat_id),
                "summary": self._visible_text(str(beat.get("summary", ""))),
                "when": self._visible_text("; ".join(sorted(trigger_events))) or "The latest evidence supports a new story direction.",
                "requires": "; ".join(self._humanize_fact(item) for item in sorted(trigger_facts)) or "A recent validated discovery is required.",
                "audience": [self._visible_text(str(item)) for item in (beat.get("audience", []) or [])],
                "followup_behaviors": [self._visible_text(str(item)) for item in (beat.get("followup_behaviors", []) or [])],
                "impact": ["Platform records the beat, informs its configured audience, and unlocks only configured behaviors."],
            })
        return choices

    def _story_known_facts(self, fact_rows: list[Any]) -> list[str]:
        """Turn the newest knowledge facts into at most twelve distinct sentences."""

        known_facts: list[str] = []
        seen_fact_keys: set[str] = set()
        seen_fact_text: set[str] = set()
        for row in fact_rows:
            try:
                data = json.loads(row["data_json"])
            except (TypeError, json.JSONDecodeError):
                data = {}
            fact = data.get("fact") or data.get("summary")
            text = str(self._humanize_fact(str(fact or str(row["fact_key"]).replace(":", " — ").replace("_", " ")))).strip()
            fact_key = str(row["fact_key"] or "").strip()
            normalized_text = text.casefold()
            if (fact_key and fact_key in seen_fact_keys) or normalized_text in seen_fact_text:
                continue
            if fact_key:
                seen_fact_keys.add(fact_key)
            seen_fact_text.add(normalized_text)
            known_facts.append(text)
            if len(known_facts) >= 12:
                break
        return known_facts

    def _world_progress_candidates(self) -> list[dict[str, Any]]:
        """List the configured civilization progress tracks and the evidence that advances them."""

        return [
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
        ]

    def _story_audience_candidates(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expose exact character names found in recent validated events.

        Story delivery is global, so the audience need not be co-located with
        the Story Agent.  The candidate list is derived from event participants
        and contains names only; internal identifiers remain Platform-owned.
        """

        candidate_ids: list[str] = []
        participant_keys = {"target_id", "recipient_id", "speaker_id", "listener_id", "counterparty_id", "participant_id", "actor_id", "from_id", "to_id"}

        def collect(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    collect(child_value, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    collect(child, key)
            elif isinstance(value, str) and (key in participant_keys or key in {"from", "to", "source", "target", "participant", "recipient"}):
                candidate_ids.append(value)

        for event in events[:40]:
            actor_id = event.get("actor_id")
            if actor_id:
                candidate_ids.append(str(actor_id))
            collect(event)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate_id in candidate_ids:
            try:
                agent = self.entities_repo.get_agent(str(candidate_id))
            except KeyError:
                continue
            name = str(agent.get("name") or candidate_id).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            candidate_view = self.build_decision_view(str(candidate_id), role="agent_decision")
            executable = [
                {
                    "choice": str(item.get("choice") or item.get("name") or ""),
                    "purpose": str(item.get("purpose") or item.get("summary") or ""),
                    "parameters": list(item.get("parameters") or []),
                    "parameter_options": dict(item.get("parameter_options") or {}),
                }
                for item in (candidate_view.get("executable_behaviors") or [])[:8]
            ]
            result.append({
                "name": name,
                "reason": "participant in a recent validated event",
                "executable_behaviors": executable,
                "research_tracks": list(candidate_view.get("research_tracks") or [])[:6],
            })
            if len(result) >= 12:
                break
        return result
