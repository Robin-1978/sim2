from __future__ import annotations

from typing import Any
from uuid import uuid4


class LearningRuntimeMixin:
    """Validated intent handlers separated from the Platform coordinator."""

    def submit_experience(self, actor_id: str, event_ref: str, summary: str, confidence: float, claim_status: str = "inferred") -> dict[str, Any]:
        summary = str(summary).strip()
        claim_status = str(claim_status or "inferred").strip().casefold()
        data = {"event_ref": event_ref, "summary": summary, "confidence": confidence, "claim_status": claim_status}
        intent_id = str(uuid4())
        if not summary.strip():
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Experience summary is required.", "failed_check": "summary", "retryable": True})
        if len(summary) > 500:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Experience summary must be at most 500 characters.", "failed_check": "summary_length", "retryable": True})
        if not 0 <= confidence <= 1:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Confidence must be between 0 and 1.", "failed_check": "confidence", "retryable": True})
        allowed_claims = {"observed", "confirmed", "reported", "promised", "inferred", "disputed", "false", "failed"}
        if claim_status not in allowed_claims:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "claim_status must be observed, confirmed, reported, promised, inferred, disputed, false, or failed.", "failed_check": "claim_status", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            state = agent.get("initial_state", {}) or {}
            location_name = ""
            try:
                location_name = str(self.store.get_entity(str(state.get("location_id"))).get("name", "")) if state.get("location_id") else ""
            except KeyError:
                location_name = ""
            data.update({
                "location": location_name,
                "goal": str(agent.get("ultimate_goal", "")),
                "goal_tags": list(agent.get("goal_tags", []) or []),
                "behavior": str(event_ref or ""),
            })
            source_event = self._resolve_experience_source(actor_id, event_ref)
            if source_event:
                data.update({
                    "source_event_id": int(source_event["event_id"]),
                    "source_event_type": str(source_event["event_type"]),
                    "source_world_minute": int(source_event["world_minute"]),
                })
            elif claim_status in {"observed", "confirmed", "disputed", "false", "failed"}:
                result = {
                    "status": "rejected",
                    "message": "Observed, confirmed, and failed lessons must cite a recorded event or completed outcome. Use inferred for a conclusion or promised for an unverified commitment.",
                    "failed_check": "claim_evidence",
                    "retryable": True,
                }
                self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
                self.store.finish_intent(intent_id, "rejected", result)
                return result
            similarity = self.memory.experience_match(actor_id, summary)
            if similarity["duplicate"]:
                result = {
                    "status": "rejected",
                    "message": "This experience is too similar to a retained lesson; return wait or submit a genuinely new lesson.",
                    "failed_check": "similar_experience",
                    "similarity": similarity,
                    "retryable": True,
                }
                self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
                self.store.finish_intent(intent_id, "rejected", result)
                return result
            duplicate = self.store.connection.execute(
                "SELECT 1 FROM memory_item WHERE agent_id=? AND memory_type='experience' AND json_extract(data_json,'$.summary')=? LIMIT 1",
                (actor_id, summary.strip()),
            ).fetchone()
            if duplicate is not None:
                result = {"status": "rejected", "message": "This experience is already retained; return wait or submit a genuinely new lesson.", "failed_check": "duplicate_experience", "retryable": True}
                self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
                self.store.finish_intent(intent_id, "rejected", result)
                return result
            self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
            memory_id = self.store.put_memory(actor_id, "experience", {**data, "world_minute": world_minute})
            self._context.services["events"].append("memory.experience_recorded", actor_id, world_minute, {"memory_id": memory_id, **data})
            result = {"status": "accepted", "intent_id": intent_id, "memory_id": memory_id}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def _resolve_experience_source(self, actor_id: str, event_ref: str) -> dict[str, Any] | None:
        """Resolve a short model-facing event reference to one real event."""

        reference = str(event_ref or "").strip().casefold()
        if not reference:
            return None
        rows = self.store.recent_events(limit=500)
        for event in rows:
            if str(event.get("actor_id") or "") != str(actor_id) and str(event.get("target_id") or "") != str(actor_id):
                # Public world events may have no actor; keep them eligible.
                if event.get("actor_id") not in {None, ""}:
                    continue
            event_id = str(event.get("event_id") or "")
            event_type = str(event.get("event_type") or "").casefold()
            behavior_id = str(event.get("behavior_id") or "").casefold()
            if reference in {event_id, f"event:{event_id}"} or reference == event_type or event_type in reference or (behavior_id and behavior_id in reference):
                return event
        return None

    def submit_capability_update(self, actor_id: str, capability: str, delta: float, evidence: str) -> dict[str, Any]:
        """Apply a personal capability change only after Agent evidence review."""

        intent_id = str(uuid4())
        capability = str(capability).strip()
        evidence = str(evidence).strip()
        data = {"capability": capability, "delta": delta, "evidence": evidence}
        definitions = {str(item.get("id")): item for item in self.store.get_definitions("capability_def")}
        if capability not in definitions:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability is not configured.", "failed_check": "capability", "retryable": True})
        if not 0 < float(delta) <= 0.2 or len(evidence) < 12:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability delta or evidence is invalid.", "failed_check": "evidence", "retryable": True})
        rules = self.store.get_meta("rules", {}) or {}
        progression = (rules.get("capability_progression", {}) or {}).get(capability)
        if not isinstance(progression, dict):
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "This capability has no configured progression rule.", "failed_check": "capability_progression", "retryable": False})
        minimum = max(1, int(progression.get("minimum_completed_outcomes", 3) or 3))
        maximum_delta = min(0.2, float(progression.get("max_delta", 0.1) or 0.1))
        if float(delta) > maximum_delta:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability delta exceeds the configured review bound.", "failed_check": "delta_limit", "retryable": True})
        try:
            agent = self.store.get_agent(actor_id)
        except KeyError:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability updates require an individual Agent.", "failed_check": "actor", "retryable": False})
        if capability not in (agent.get("capabilities") or {}) and not bool(definitions[capability].get("acquirable", False)):
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "The character has no configured path to this capability.", "failed_check": "capability_ownership", "retryable": False})
        last = self.store.connection.execute("SELECT MAX(world_minute) AS minute FROM capability_progress WHERE agent_id=? AND capability=?", (actor_id, capability)).fetchone()["minute"]
        start = int(last) if last is not None else -1
        evidence_events = self.store.capability_evidence(actor_id, capability, after_minute=start)
        completed = len(evidence_events)
        if completed < minimum:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": f"More matching capability evidence is required before updating {capability}.", "failed_check": "insufficient_evidence", "completed_outcomes": completed, "required_outcomes": minimum, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        current = float((agent.get("capabilities") or {}).get(capability, 0) or 0)
        maximum = float(progression.get("max_value", 1.0) or 1.0)
        after = min(maximum, current + float(delta))
        applied_delta = after - current
        if applied_delta <= 0:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability is already at its configured maximum.", "failed_check": "capability_max", "retryable": False})
        with self.store.transaction():
            agent.setdefault("capabilities", {})[capability] = after
            self.store.create_intent(intent_id, actor_id, "update_capability", data, world_minute)
            self.store.update_agent(actor_id, agent)
            source_event_ids = [int(item["event_id"]) for item in evidence_events]
            self.store.put_capability_progress(actor_id, capability, applied_delta, "agent_review", world_minute, {"evidence": evidence, "completed_outcomes": completed, "source_event_ids": source_event_ids})
            event_id = self._context.services["events"].append("capability.updated", actor_id, world_minute, {"capability": capability, "before": current, "after": after, "delta": applied_delta, "evidence": evidence, "completed_outcomes": completed, "source_event_ids": source_event_ids})
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "capability": capability, "before": current, "after": after, "delta": applied_delta}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_world_progress(
        self,
        actor_id: str,
        progress_key: str,
        delta: float | None,
        evidence: str,
        *,
        source_event_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Record public progress proposed by Story or God from real outcomes."""

        intent_id = str(uuid4())
        progress_key = str(progress_key).strip()
        evidence = str(evidence).strip()
        data = {"progress_key": progress_key, "evidence": evidence}
        rules = self.store.get_meta("rules", {}) or {}
        definitions = rules.get("civilization_progress", {}) or {}
        definition = definitions.get(progress_key) if isinstance(definitions, dict) else None
        if not isinstance(definition, dict):
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_world_progress", data, {"status": "rejected", "message": "World progress key is not configured.", "failed_check": "progress_key", "retryable": True})
        contribution = float(definition.get("evidence_value", 1) or 1)
        if contribution <= 0 or contribution > 5 or len(evidence) < 12:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_world_progress", data, {"status": "rejected", "message": "The configured progress contribution or submitted evidence is invalid.", "failed_check": "evidence", "retryable": True})
        if delta is not None and abs(float(delta) - contribution) > 1e-9:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_world_progress", {**data, "requested_delta": delta}, {"status": "rejected", "message": "Platform derives progress from the configured evidence value; do not choose a delta.", "failed_check": "progress_delta", "retryable": True})
        source_events = (
            self.store.events_by_ids(list(source_event_ids or []))
            if source_event_ids
            else self.store.recent_events(limit=200)
        )
        if not source_events:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_world_progress", data, {"status": "rejected", "message": "A public progress update needs a recent behavior outcome.", "failed_check": "insufficient_evidence", "retryable": True})
        allowed_events = {str(item) for item in (definition.get("evidence_event_types") or [])}
        allowed_behaviors = {str(item) for item in (definition.get("evidence_behaviors") or [])}
        source_clauses = [item for item in (definition.get("evidence_sources") or []) if isinstance(item, dict)]

        def source_matches(item: dict[str, Any]) -> bool:
            if source_clauses:
                identifiers = {
                    str(item.get(key)) for key in ("entity_id", "object_id", "source_id", "target_id", "resource_id")
                    if item.get(key) is not None
                }
                for clause in source_clauses:
                    event_types = {str(value) for value in (clause.get("event_types") or ([clause["event_type"]] if clause.get("event_type") else []))}
                    behavior_ids = {str(value) for value in (clause.get("behavior_ids") or ([clause["behavior_id"]] if clause.get("behavior_id") else []))}
                    action_ids = {str(value) for value in (clause.get("action_ids") or ([clause["action_id"]] if clause.get("action_id") else []))}
                    entity_ids = {str(value) for value in (clause.get("entity_ids") or ([clause["entity_id"]] if clause.get("entity_id") else []))}
                    if event_types and str(item.get("event_type") or "") not in event_types:
                        continue
                    if behavior_ids and str(item.get("behavior_id") or "") not in behavior_ids:
                        continue
                    if action_ids and str(item.get("action_id") or "") not in action_ids:
                        continue
                    if entity_ids and not identifiers & entity_ids:
                        continue
                    return True
                return False
            return (
                not allowed_events and not allowed_behaviors
            ) or (
                str(item.get("event_type") or "") in allowed_events
                or str(item.get("behavior_id") or "") in allowed_behaviors
            )

        matching_sources = [
            item for item in source_events
            if source_matches(item)
        ]
        if not matching_sources:
            description = str(definition.get("description") or progress_key.replace("_", " "))
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_world_progress", data, {
                "status": "rejected",
                "message": f"The triggering result is not configured evidence for {description}. Choose a matching progress category or wait.",
                "failed_check": "progress_evidence_mismatch",
                "retryable": True,
            })
        source_event = matching_sources[-1] if source_event_ids else matching_sources[0]
        data.update({
            "source_event_id": int(source_event.get("event_id", 0) or 0),
            "source_event_type": str(source_event.get("event_type") or ""),
            "source_behavior_id": str(source_event.get("behavior_id") or ""),
            "delta": contribution,
        })
        duplicate = self.store.connection.execute(
            "SELECT 1 FROM event_log WHERE event_type IN ('civilization.progressed','civilization.milestone') "
            "AND json_extract(data_json,'$.progress_key')=? "
            "AND json_extract(data_json,'$.source_event_id')=? LIMIT 1",
            (progress_key, data["source_event_id"]),
        ).fetchone()
        if duplicate is not None:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "record_world_progress", data, {"status": "rejected", "message": "This source event has already contributed to that progress category.", "failed_check": "duplicate_progress_evidence", "retryable": False})
        world_minute = int(self.store.get_meta("world_minute", 0))
        world_id = str(self.store.get_meta("world_id", "world"))
        required = float(definition.get("required", 1) or 1)
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "record_world_progress", data, world_minute)
            progress = self.store.advance_progress(world_id, progress_key, contribution, required, world_minute, {
                "evidence": evidence,
                "source_agent": actor_id,
                "source_event_id": data["source_event_id"],
                "source_event_type": data["source_event_type"],
                "source_behavior_id": data["source_behavior_id"],
            })
            event_type = "civilization.milestone" if progress["completed"] else "civilization.progressed"
            event_id = self._context.services["events"].append(event_type, actor_id, world_minute, {"progress": progress, **data, "source_agent": actor_id})
            result = {
                "status": "accepted",
                "intent_id": intent_id,
                "event_id": event_id,
                "progress": progress,
                "milestone": bool(progress["completed"]),
                "source_event_id": data["source_event_id"],
                "source_event_type": data["source_event_type"],
                "source_behavior_id": data["source_behavior_id"],
            }
            if progress["completed"]:
                wake = list(definition.get("wake") or [])
                self.wakes.followups({"wake": wake}, event_id, event_type, actor_id, world_minute)
                for role in wake:
                    self.store.mark_evolution_task_ready(f"{role}:event:{event_id}")
            self.store.finish_intent(intent_id, "accepted", result)
        return result


    def submit_belief_update(self, actor_id: str, belief_id: str, statement: str, confidence: float, reason: str) -> dict[str, Any]:
        data = {"belief_id": belief_id, "statement": statement, "confidence": confidence, "reason": reason}
        intent_id = str(uuid4())
        if not statement and not belief_id:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_belief", data, {"status": "rejected", "message": "A belief id or statement is required.", "failed_check": "belief", "retryable": True})
        if not 0 <= confidence <= 1:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_belief", data, {"status": "rejected", "message": "Confidence must be between 0 and 1.", "failed_check": "confidence", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            beliefs = list(agent.get("beliefs", []))
            match = next((belief for belief in beliefs if belief.get("id") == belief_id), None)
            if match is None:
                match = {"id": belief_id or f"belief_{uuid4().hex[:8]}", "statement": statement}
                beliefs.append(match)
            elif statement:
                match["statement"] = statement
            match["confidence"] = confidence
            match["updated_world_minute"] = world_minute
            agent["beliefs"] = beliefs
            self.store.create_intent(intent_id, actor_id, "update_belief", data, world_minute)
            self.store.update_agent(actor_id, agent)
            self._context.services["events"].append("belief.updated", actor_id, world_minute, data)
            result = {"status": "accepted", "intent_id": intent_id, "belief_id": match["id"], "confidence": confidence}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_goal_update(self, actor_id: str, goal_id: str, statement: str, status: str, priority: float) -> dict[str, Any]:
        intent_id = str(uuid4())
        data = {"goal_id": goal_id, "statement": statement, "status": status, "priority": priority}
        if not statement.strip():
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_goal", data, {"status": "rejected", "message": "Goal statement is required.", "failed_check": "statement", "retryable": True})
        if not 0 <= priority <= 1:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "update_goal", data, {"status": "rejected", "message": "Goal priority must be between 0 and 1.", "failed_check": "priority", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            if goal_id in {"", "ultimate_goal"}:
                agent["ultimate_goal"] = statement
            else:
                goals = list(agent.get("goals", []))
                match = next((goal for goal in goals if goal.get("id") == goal_id), None)
                if match is None:
                    match = {"id": goal_id}
                    goals.append(match)
                match.update({"statement": statement, "status": status, "priority": priority, "updated_world_minute": world_minute})
                agent["goals"] = goals
            self.store.create_intent(intent_id, actor_id, "update_goal", data, world_minute)
            self.store.update_agent(actor_id, agent)
            self._context.services["events"].append("goal.updated", actor_id, world_minute, data)
            result = {"status": "accepted", "intent_id": intent_id, "goal_id": goal_id or "ultimate_goal"}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_behavior_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        action_ids = list(proposal.get("action_ids") or [])
        action_defs = {x["id"]: x for x in self.store.get_definitions("action_def")}
        errors: list[str] = []
        if not proposal.get("goal"):
            errors.append("goal is required")
        if not action_ids:
            errors.append("at least one action_id is required")
        if len(action_ids) > 16:
            errors.append("a behavior may contain at most 16 actions")
        errors.extend(f"unknown action: {action_id}" for action_id in action_ids if action_id not in action_defs)
        behavior_id = str(proposal.get("behavior_id") or f"proposed_{uuid4().hex[:10]}")
        if any(x["id"] == behavior_id for x in self.store.get_definitions("bt_def")):
            errors.append(f"behavior already exists: {behavior_id}")
        proposed_label = str(proposal.get("agent_label") or proposal.get("summary") or proposal.get("goal") or "").casefold()
        proposed_actions = list(action_ids)
        for existing in self.store.get_definitions("bt_def"):
            existing_label = str(existing.get("agent_label") or existing.get("summary") or existing.get("id", "").replace("_", " ")).casefold()
            existing_actions = [node.get("action_id") for node in existing.get("nodes", []) if node.get("type") == "action"]
            if proposed_label and existing_label == proposed_label and existing_actions == proposed_actions:
                errors.append("an equivalent behavior is already registered")
                break
        data = {**proposal, "behavior_id": behavior_id, "actor_id": actor_id}
        if errors:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_behavior", data, {"status": "rejected", "message": "Behavior proposal failed validation.", "errors": errors, "retryable": True})
        estimated = 0
        nodes = []
        for action_id in action_ids:
            action = action_defs[action_id]
            duration = action.get("duration", {}).get("minutes", 0)
            try:
                estimated += int(duration)
            except (TypeError, ValueError):
                pass
            nodes.append({"type": "action", "action_id": action_id})
        definition = {
            "id": behavior_id, "version": 1, "definition_version": 1, "scope": "personal", "summary": proposal.get("summary") or proposal["goal"],
            "agent_label": proposal.get("agent_label") or proposal.get("summary") or proposal["goal"],
            "entry_conditions": proposal.get("entry_conditions", []), "estimated_minutes": estimated,
            "success_results": proposal.get("success_results", []), "failure_results": proposal.get("failure_results", []),
            "nodes": nodes, "definition_source": "agent_proposal", "kind": "proposed", "validation_status": "validated",
        }
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            self.store.create_intent(intent_id, actor_id, "propose_behavior", data, world_minute)
            self.store.put_definition("bt_def", behavior_id, definition)
            ids = list(agent.get("initial_bt_ids", []))
            if behavior_id not in ids:
                ids.append(behavior_id)
            agent["initial_bt_ids"] = ids
            self.store.update_agent(actor_id, agent)
            self._context.services["events"].append("behavior.registered", actor_id, world_minute, {"behavior_id": behavior_id, "source": "agent_proposal"})
            result = {"status": "accepted", "intent_id": intent_id, "behavior_ref": f"behavior:{behavior_id}", "registered": True}
            self.store.finish_intent(intent_id, "accepted", result)
        return result
