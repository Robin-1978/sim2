"""Character-facing projections: interaction, memory, relationships, recent activity, constraints, institutions and story directives."""

from __future__ import annotations

from typing import Any

from ..actions.institutions import describe_institution_terms
from .decision_frame import DecisionFrame


class CharacterViewMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    def _interaction_context(self, agent: dict[str, Any], participants: list[dict[str, Any]]) -> dict[str, Any]:
        """Expose configured relationship signals without naming story entities."""

        rules = self.store.repositories.metadata.get("rules", {}) or {}
        conflict_types = {str(item) for item in (rules.get("conflict_relation_types", ["conflict"]) or [])}
        participant_ids = {str(item.get("id")) for item in participants}
        available = False
        for relation in self.entities_repo.get_related_relationships(agent["id"], limit=50):
            if str(relation.get("person")) not in participant_ids or str(relation.get("relation")) not in conflict_types:
                continue
            value = relation.get("relation_strength", relation.get("value", 0))
            try:
                available = float(value) < 0
            except (TypeError, ValueError):
                available = False
            if available:
                break
        return {"conflict": {"available": available}}

    def _memory_context(self, frame: DecisionFrame, available: list[dict[str, Any]], drives: list[dict[str, Any]]) -> tuple[str, list[Any], list[Any], list[Any], list[Any]]:
        agent_id, agent = frame.agent_id, frame.agent
        current_location, location_id = frame.current_location, frame.location_id
        memory_query_parts = [str(agent.get("ultimate_goal", ""))]
        if current_location:
            memory_query_parts.append(str(current_location.get("name", location_id)))
        for drive in drives[:3]:
            memory_query_parts.append(str(drive.get("id", "")))
        for behavior in available[:6]:
            memory_query_parts.append(str(behavior.get("ref", "")).removeprefix("behavior:"))
            choice_words = str(behavior.get("choice", "")).split()
            if choice_words:
                memory_query_parts.append(" ".join(choice_words[:4]))
            memory_query_parts.extend(str(item) for item in (behavior.get("goal_tags") or []))
        memory_query = " ".join(part for part in memory_query_parts if part).strip()
        memory_dimensions = {
            "location": [str(location_id), str(current_location.get("name", ""))] if location_id else [],
            "behavior": [str(item.get("ref", "")).removeprefix("behavior:") for item in available[:8]],
            "goal": list(agent.get("goal_tags", []) or []),
        }
        # Compiling a snapshot is read-only. Explicit ``recall_memory`` calls
        # touch rows so opening a new decision view cannot reorder a just-written
        # experience by incrementing its predecessor's access count.
        memories = [self._humanize_record(item) for item in self.memory.retrieve(agent_id, query=memory_query, limit=6, touch=False, dimensions=memory_dimensions)]
        short_term_memories = [item for item in memories if item.get("tier") == "short_term"]
        long_term_memories = [item for item in memories if item.get("tier") == "long_term"]
        known_facts = self._merge_visible_facts([
            self._humanize_fact(item)
            for item in self.memory.relevant_facts(agent_id, query=memory_query, limit=12, dimensions=memory_dimensions)
        ])[:8]
        if not known_facts:
            known_facts = [{"fact": self._humanize_fact(fact), "source_type": "config_seed", "confidence": 1.0} for fact in agent.get("knowledge_seed", [])[:8]]
        return memory_query, memories, short_term_memories, long_term_memories, known_facts

    def _relationship_view(self, agent_id: str) -> list[dict[str, Any]]:
        relationships = self.entities_repo.get_related_relationships(agent_id, limit=5)
        for relationship in relationships:
            person_id = relationship.get("person")
            try:
                relationship["person"] = self.entities_repo.get_agent(person_id).get("name", person_id)
            except KeyError:
                try:
                    relationship["person"] = self.entities_repo.get_entity(person_id).get("name", person_id)
                except KeyError:
                    pass
        return relationships

    def _violation_view(self, agent_id: str) -> dict[str, list[dict[str, Any]]]:
        """Project only the violation facts this character may know."""

        repo = self.store.repositories.contested
        my = [
            {
                "violation_id": item.get("violation_id"),
                "target": self._entity_name(item.get("target")),
                "amount": item.get("amount"),
                "status": item.get("status"),
                "minute": item.get("created_minute"),
            }
            for item in repo.list_violations(actor_id=agent_id, limit=8)
        ]
        witnessed = [
            {
                "violation_id": item.get("violation_id"),
                "actor": self._entity_name(item.get("actor")),
                "target": self._entity_name(item.get("target")),
                "amount": item.get("amount"),
                "status": item.get("status"),
                "minute": item.get("created_minute"),
            }
            for item in repo.list_violations(witness_id=agent_id, limit=8)
        ]
        stewarded = set(self._authorization_stewardships(agent_id))
        unresolved = [
            {
                "violation_id": item.get("violation_id"),
                "actor": self._entity_name(item.get("actor")),
                "target": self._entity_name(item.get("target")),
                "amount": item.get("amount"),
                "witnesses": [self._entity_name(value) for value in (item.get("witnesses") or [])],
                "status": item.get("status"),
                "minute": item.get("created_minute"),
            }
            for item in repo.list_violations(statuses=("observed", "sanctioned"), limit=200)
            if str(item.get("target") or "") in stewarded
        ][:8]
        return {"my": my, "witnessed": witnessed, "unresolved": unresolved}

    def _recent_activity(self, agent_id: str, bt_defs: dict[str, dict[str, Any]]) -> tuple[list[Any], list[Any]]:
        actor_events = self.events_repo.recent_events_for_actor(agent_id, limit=8)
        recent_actions = [
            self._with_behavior_choice(self._humanize_record(item), bt_defs)
            for item in actor_events[:2]
        ]
        recent_outcomes = [
            self._with_behavior_choice(self._humanize_record(item), bt_defs)
            for item in self.events_repo.recent_outcomes_for_actor(agent_id, limit=3)
        ]
        for outcome in recent_outcomes:
            if outcome.get("behavior_choice"):
                continue
            behavior = bt_defs.get(str(outcome.get("behavior_id", "")), {})
            outcome["behavior_choice"] = behavior.get("agent_label") or behavior.get("summary") or behavior.get("description") or "a behavior"
        return recent_actions, recent_outcomes

    def _decision_constraints(self, frame: DecisionFrame, needs: dict[str, Any]) -> list[str]:
        rules, state, available = frame.rules, frame.state, frame.available
        location_id, safe_location_id = frame.location_id, frame.safe_location_id
        decision_constraints: list[str] = []
        labels = {str(item.get("id")): str(item.get("label") or item.get("id")) for item in (self.store.repositories.metadata.get("attributes", []) or []) if isinstance(item, dict) and item.get("id")}
        for signal in rules.get("decision_signals", []) or []:
            if not isinstance(signal, dict) or not signal.get("need"):
                continue
            need_id = str(signal["need"])
            if need_id not in needs:
                continue
            value = float(needs.get(need_id, 0) or 0)
            threshold = float(signal.get("alert_threshold", 0) or 0)
            message = signal.get("below_message") if value <= threshold else signal.get("above_message")
            if message:
                decision_constraints.append(f"{labels.get(need_id, need_id)} is {value:g}/100; {str(message).rstrip('.') }.")
        schema = self._attribute_schema()
        capacity_id = schema.capacity_attribute()
        if capacity_id:
            capacity_value = float(state.get(capacity_id, 0) or 0)
            capacity_label = labels.get(capacity_id, capacity_id)
            capacity_max = schema.maximum(capacity_id)
            decision_constraints.append(f"{capacity_label} is {capacity_value:g}/{capacity_max:g}; Platform hides any BT whose configured cost exceeds current {capacity_label}.")
        if location_id and safe_location_id and safe_location_id != location_id:
            decision_constraints.append("The current location is not safe for rest; the recovery BT moves to a configured safe location first.")
        elif location_id and not safe_location_id:
            decision_constraints.append("No configured safe location is reachable from the current location.")
        if len(available) == 1:
            decision_constraints.append("Exactly one BT is executable in this state; choose it only if its stated purpose fits the current goal.")
        elif not available:
            decision_constraints.append("No BT is executable in this state; return wait.")
        return decision_constraints

    def _capability_update_candidates(self, agent_id: str, agent: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
        capability_update_candidates = []
        progression_rules = rules.get("capability_progression", {}) or {}
        capability_defs = {str(item.get("id")): item for item in self._definitions("capability_def")}
        owned_capabilities = agent.get("capabilities") or {}
        for capability, definition in progression_rules.items():
            capability = str(capability)
            catalog_entry = capability_defs.get(capability)
            if not catalog_entry or (capability not in owned_capabilities and not bool(catalog_entry.get("acquirable", False))):
                continue
            last = self.store.connection.execute("SELECT MAX(world_minute) AS minute FROM capability_progress WHERE agent_id=? AND capability=?", (agent_id, str(capability))).fetchone()["minute"]
            evidence_events = self.activity_repo.capability_evidence(agent_id, capability, after_minute=int(last) if last is not None else -1)
            completed = len(evidence_events)
            required = int((definition or {}).get("minimum_completed_outcomes", 3) or 3)
            if int(completed or 0) >= required:
                capability_update_candidates.append({"capability": capability, "completed_outcomes": int(completed), "required_outcomes": required, "max_delta": float((definition or {}).get("max_delta", 0.1) or 0.1), "evidence_sources": list(dict.fromkeys(str(item.get("source") or "configured result") for item in evidence_events))})
        return capability_update_candidates

    def _institution_view(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        pending_institutions = [
            {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "proposed"}
            for item in self.social_repo.list_institutions(status="proposed", limit=12)
        ]
        active_institutions = [
            {"name": str(item.get("name", "")), "purpose": str(item.get("purpose", "")), "scope": str(item.get("scope", "world")), "status": "active", "terms": describe_institution_terms(item)}
            for item in self.social_repo.list_institutions(status="active", limit=12)
        ]
        institution_ledgers = []
        for item in self.social_repo.list_institutions(status="active", limit=12):
            institution_ledgers.append({
                "name": str(item.get("name", "")),
                "entries": self.social_repo.institution_ledger_totals(str(item.get("institution_id")), None)[:12],
            })
        return pending_institutions, active_institutions, institution_ledgers

    def _agent_story_view(self, agent_id: str, agent: dict[str, Any]) -> tuple[list[Any], list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
        story_goals = list(agent.get("story_goals", []) or [])[-3:]
        behavior_assignments = [
            item for item in (agent.get("behavior_assignments", []) or [])
            if isinstance(item, dict) and str(item.get("status") or "assigned") == "assigned"
        ][-4:]
        agent_name = str(agent.get("name", agent_id))
        story_root = self.store.repositories.metadata.get("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        story_defs = {
            str(item.get("id")): item
            for item in list(story_root.get("beats", []) or []) + list((self.store.repositories.metadata.get("story_state", {}) or {}).get("emergent_beats", []) or [])
            if isinstance(item, dict) and item.get("id")
        }
        active_story_beats: list[dict[str, Any]] = []
        story_state = self.store.repositories.metadata.get("story_state", {}) or {}
        for beat_id, entry in story_state.items():
            if beat_id == "emergent_beats" or not isinstance(entry, dict) or entry.get("status") != "active":
                continue
            audience = [str(value) for value in (entry.get("audience", []) or [])]
            if agent_id not in audience and agent_name not in audience:
                continue
            definition = story_defs.get(str(beat_id), {})
            active_story_beats.append({
                "summary": str(definition.get("summary", definition.get("goal_hint", beat_id))),
                "beat_id": str(beat_id),
                "world_minute": int(entry.get("started_minute", 0) or 0),
            })
        active_story_beats.sort(key=lambda item: item["world_minute"], reverse=True)
        active_story_beats = active_story_beats[:3]
        active_story_threads = self._active_story_threads()
        return story_goals, behavior_assignments, active_story_beats, active_story_threads
