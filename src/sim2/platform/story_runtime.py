from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from ..evolution.memory import remember_role
from ..evolution.story import eligible_story_beats, story_evidence_event_types


class StoryRuntimeMixin:
    """Validate and commit Story beats from validated evidence."""

    def submit_story_beat(
        self,
        actor_id: str,
        proposal: dict[str, Any],
        *,
        source_event_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Activate one configured story beat after Platform checks its evidence."""

        intent_id = str(uuid4())
        proposal = dict(proposal or {})

        def visible_label(value: Any) -> str:
            text = str(value or "").strip()
            # Models sometimes serialize a visible name with one extra pair
            # of quotes inside the JSON string. Remove only wrapping quotes;
            # exact Platform name resolution still decides whether it exists.
            for _ in range(2):
                if len(text) >= 2 and text[0] == text[-1] and text[0] in {"\"", "'"}:
                    text = text[1:-1].strip()
                else:
                    break
            return text

        for key in ("beat", "summary", "fact", "goal_hint", "research_source", "reason"):
            if key in proposal and isinstance(proposal[key], str):
                proposal[key] = visible_label(proposal[key])
        if isinstance(proposal.get("audience"), list):
            proposal["audience"] = [visible_label(item) for item in proposal["audience"]]
        if isinstance(proposal.get("unlock_choices"), list):
            proposal["unlock_choices"] = [visible_label(item) for item in proposal["unlock_choices"]]
        story_root = self.store.get_meta("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        beats = list(story_root.get("beats", []) or []) if isinstance(story_root, dict) else []
        state = dict(self.store.get_meta("story_state", {}) or {})
        recent = self.store.recent_events(limit=500)
        bound_sources = self.store.events_by_ids([int(item) for item in (source_event_ids or []) if int(item) > 0])
        configured_source_types = {str(item) for item in (story_root.get("emergent_source_event_types") or [])}
        source_event = bound_sources[-1] if bound_sources else next(
            (
                item for item in recent[:30]
                if bool(item.get("story_source"))
                or str(item.get("event_type", "")) in configured_source_types
            ),
            None,
        )
        observed_events = {str(item.get("event_type", "")) for item in recent}
        fact_rows = self.store.connection.execute("SELECT fact_key FROM knowledge_fact").fetchall()
        observed_facts = {str(row["fact_key"]) for row in fact_rows}
        scoped_beats = eligible_story_beats(
            beats,
            state,
            source_event_type=story_evidence_event_types(source_event),
            observed_events=observed_events,
            observed_facts=observed_facts,
        )
        choice = str(proposal.get("beat", "")).strip()
        key = "".join(char for char in choice.casefold() if char.isalnum())
        all_matches = [
            beat for beat in beats if isinstance(beat, dict)
            and key == "".join(char for char in str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")).casefold() if char.isalnum())
        ]
        matches = [
            beat for beat in scoped_beats if isinstance(beat, dict)
            and key == "".join(char for char in str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")).casefold() if char.isalnum())
        ]
        emergent = False
        emergent_summary = str(proposal.get("summary") or proposal.get("fact") or "").strip()
        if not matches and not all_matches and not scoped_beats and emergent_summary:
            summary = emergent_summary
            if len(summary) < 8 or len(summary) > 500:
                return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent story beat summary must be 8 to 500 characters.", "failed_check": "story_summary", "retryable": True})
            proposal.setdefault("summary", summary)
            beat = {
                "id": f"emergent:{uuid4().hex[:12]}",
                "agent_label": choice or summary[:80],
                "summary": summary,
                "audience": list(proposal.get("audience", []) or []),
                "fact": str(proposal.get("fact", "")).strip(),
                "goal_hint": str(proposal.get("goal_hint", "")).strip(),
                "research_source": str(proposal.get("research_source", "")).strip(),
                "unlock_choices": list(proposal.get("unlock_choices", []) or []),
                "public_event": "story.emergent_beat",
            }
            # An emergent chapter must not disappear because its audience did
            # an unrelated maintenance action. If Story names follow-up
            # choices, use those visible choices as the completion boundary;
            # otherwise leave the chapter active until a later Story result
            # explicitly supersedes or closes it.
            if beat["unlock_choices"]:
                beat["followup_behaviors"] = list(beat["unlock_choices"])
            matches = [beat]
            emergent = True
        if len(matches) != 1:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {
                "status": "rejected", "message": "Choose one exact configured story beat from the current story view.", "failed_check": "story_beat_choice", "retryable": True,
                "available_choices": [str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")) for beat in scoped_beats if isinstance(beat, dict)],
            })
        beat = matches[0]
        beat_id = str(beat.get("id", ""))
        current = state.get(beat_id) if isinstance(state.get(beat_id), dict) else {}
        if current.get("status") in {"active", "completed"}:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "This story beat is already active or completed.", "failed_check": "story_beat_state", "retryable": False})
        if emergent and source_event is None:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent beat needs a recent discovery, research, recovery, or relationship result.", "failed_check": "story_evidence", "retryable": True})
        if emergent and source_event:
            prior_emergent = list(state.get("emergent_beats", []) or [])
            if any(int(item.get("source_event_id", -1)) == int(source_event.get("event_id", -2)) for item in prior_emergent if isinstance(item, dict)):
                return {"status": "wait", "reason": "This validated result already has an active emergent story beat; wait for a new discovery or relationship result."}
            beat["source_event_id"] = int(source_event.get("event_id"))
            beat["source_event_type"] = str(source_event.get("event_type") or "validated.result")
            beat["source_event_source_id"] = str(source_event.get("source_id") or "")
            beat["source_event_stage_id"] = str(source_event.get("stage_id") or "")
            # A new event does not automatically make a new story beat useful.
            # Repeated water-safety or pact-stability outcomes often arrive as
            # separate validated events but carry the same fact. Keep one
            # durable narrative thread until the fact itself changes; the
            # source event remains in the audit log for causal analysis.
            def story_signature(value: Any) -> str:
                return "".join(char for char in str(value or "").casefold() if char.isalnum())

            candidate_signature = story_signature(beat.get("fact") or beat.get("summary"))
            if len(candidate_signature) >= 16 and any(
                story_signature(item.get("fact") or item.get("summary")) == candidate_signature
                for item in prior_emergent
                if isinstance(item, dict)
            ):
                return {"status": "wait", "reason": "A materially identical validated result already has a story beat; wait for new evidence or a changed result."}
            # An emergent beat may interpret a validated result, but it cannot
            # silently invent a later delivery, public reading, gathering, or
            # movement. Require a matching event in the same chain before
            # allowing those claims into persistent story state.
            narrative = " ".join(str(proposal.get(key, "")) for key in ("summary", "fact", "goal_hint"))
            source_text = " ".join(
                str(source_event.get(key, ""))
                for key in ("event_type", "summary", "fact", "result", "actor_id", "location")
            )
            narrative_numbers = set(re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?", narrative))
            source_numbers = set(re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?", source_text))
            if not narrative_numbers <= source_numbers:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "An emergent beat adds a numeric detail absent from the validated source event. State only the source result or wait for a confirming observation.",
                        "failed_check": "story_unverified_numeric_detail",
                        "retryable": True,
                    },
                )
            claim_guard = story_root.get("narrative_claim_guard", {}) or {}
            action_claims = tuple(str(item).casefold() for item in (claim_guard.get("terms") or []))
            if any(term in narrative.casefold() for term in action_claims):
                source_actor = str(source_event.get("actor_id") or "")
                source_event_id = int(source_event.get("event_id", 0) or 0)
                followup_event_types = {str(item) for item in (claim_guard.get("followup_event_types") or [])}
                followup_behavior_tags = {str(item) for item in (claim_guard.get("followup_behavior_tags") or [])}
                behaviors = {str(item.get("id")): item for item in self.store.get_definitions("bt_def")}

                def supports_claim(item: dict[str, Any]) -> bool:
                    if str(item.get("event_type", "")) in followup_event_types:
                        return True
                    if str(item.get("event_type", "")) != "behavior.completed":
                        return False
                    behavior = behaviors.get(str(item.get("behavior_id", "")), {})
                    tags = {
                        str(value)
                        for key in ("tags", "goal_tags", "drive_tags")
                        for value in (behavior.get(key) or [])
                    }
                    return bool(tags & followup_behavior_tags)

                followup = next(
                    (
                        item for item in recent
                        if int(item.get("event_id", 0) or 0) > source_event_id
                        and str(item.get("actor_id") or "") == source_actor
                        and supports_claim(item)
                    ),
                    None,
                )
                if followup is None:
                    return self._reject_intent(
                        intent_id,
                        actor_id,
                        "propose_story_beat",
                        proposal,
                        {
                            "status": "rejected",
                            "message": "This emergent beat claims a later delivery, meeting, reading, or movement that is not present in a validated follow-up event. State only the source fact or wait until the action occurs.",
                            "failed_check": "story_unverified_action_claim",
                            "retryable": True,
                        },
                    )
        requested_research_source = str(proposal.get("research_source") or "").strip()
        if requested_research_source and source_event:
            requested_source_id = str(self.targets.canonical_id(requested_research_source) or "")
            if not requested_source_id or not self.store.research_discoveries(requested_source_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "research_source must be an exact visible configured source with research stages.",
                        "failed_check": "story_research_source",
                        "retryable": True,
                    },
                )
            source_event_id = str(source_event.get("source_id") or "")
            source_event_type = str(source_event.get("event_type") or "")
            # A research result already names the source that produced it.
            # Story may assign a source for a non-research event, but it may
            # not retell one research result as if it came from another
            # object. This is a causal check, not a vocabulary check.
            if source_event_type == "research.completed" and (not source_event_id or requested_source_id != source_event_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "The assigned research source must match the source named by the validated research result.",
                        "failed_check": "story_research_source_evidence",
                        "retryable": True,
                    },
                )
        trigger_events = {str(item) for item in (beat.get("trigger_events", []) or [])}
        if trigger_events and not trigger_events & observed_events:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "The configured story beat has no observed trigger event yet.", "failed_check": "story_trigger", "retryable": True})
        required_facts = {str(item) for item in (beat.get("trigger_facts", []) or [])}
        if required_facts:
            if not required_facts <= observed_facts:
                return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "The configured story beat is missing one or more required facts.", "failed_check": "story_facts", "retryable": True, "missing_facts": sorted(required_facts - observed_facts)})
        minute = int(self.store.get_meta("world_minute", 0))
        raw_audience = [str(item) for item in (beat.get("audience", []) or [])]
        resolved_audience: list[str] = []
        for value in raw_audience[:8]:
            canonical = self.targets.canonical_id(value)
            if canonical and self.store.connection.execute("SELECT 1 FROM agent_state WHERE id=?", (canonical,)).fetchone():
                resolved_audience.append(canonical)
        if raw_audience and not resolved_audience:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent beat needs at least one exact visible character audience.", "failed_check": "story_audience", "retryable": True})
        audience = resolved_audience
        if emergent:
            beat["audience"] = list(audience)
        research_source_id = ""
        requested_source = str(proposal.get("research_source") or beat.get("research_source") or "").strip()
        if requested_source:
            research_source_id = str(self.targets.canonical_id(requested_source) or "")
            if not research_source_id or not self.store.research_discoveries(research_source_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "research_source must be an exact visible configured source with research stages.",
                        "failed_check": "story_research_source",
                        "retryable": True,
                    },
                )
            beat["research_source_id"] = research_source_id
        registered = {item["id"] for item in self.store.get_definitions("bt_def")}
        labels = {
            "".join(char for char in str(item.get("agent_label") or item.get("summary") or item.get("id", "")).casefold() if char.isalnum()): str(item.get("id"))
            for item in self.store.get_definitions("bt_def")
        }
        requested_unlocks = [str(value) for value in (beat.get("unlock_choices", []) or [])]
        unresolved_unlocks = [
            value for value in requested_unlocks
            if "".join(char for char in value.casefold() if char.isalnum()) not in labels
        ]
        if unresolved_unlocks:
            return self._reject_intent(
                intent_id,
                actor_id,
                "propose_story_beat",
                proposal,
                {
                    "status": "rejected",
                    "message": "Every follow-up must be an exact visible behavior already registered in the audience catalog.",
                    "failed_check": "story_followup_choice",
                    "retryable": True,
                    "invalid_choices": unresolved_unlocks,
                },
            )
        beat["unlock_behavior_ids"] = [
            labels["".join(char for char in str(value).casefold() if char.isalnum())]
            for value in beat.get("unlock_choices", [])
            if "".join(char for char in str(value).casefold() if char.isalnum()) in labels
        ]
        if audience and beat.get("unlock_behavior_ids"):
            # A narrative continuation is useful only when at least one named
            # audience member can actually start that behavior now.  Checking
            # the same executable catalog exposed to NPC decisions prevents a
            # Story beat from assigning research, crafting, or institution
            # work to a character who has no such behavior or lacks its
            # prerequisites.
            unavailable_followups: list[dict[str, Any]] = []
            for behavior_id in beat["unlock_behavior_ids"]:
                executable_for = []
                for audience_id in audience:
                    candidate = self.view.build_decision_view(audience_id, role="agent_decision", include_diagnostics=True)
                    if any(str(item.get("ref")) == f"behavior:{behavior_id}" for item in candidate.get("executable_behaviors", [])):
                        executable_for.append(audience_id)
                if not executable_for:
                    unavailable_followups.append({
                        "behavior": next((str(value) for value in requested_unlocks if labels.get("".join(char for char in value.casefold() if char.isalnum())) == behavior_id), behavior_id),
                        "audience": list(audience),
                    })
            if unavailable_followups:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "Every story follow-up must be executable by at least one audience member now.",
                        "failed_check": "story_followup_executable",
                        "retryable": True,
                        "unavailable_followups": unavailable_followups,
                    },
                )
        if emergent and source_event:
            candidate_followup_values = tuple(sorted(str(item) for item in (beat.get("unlock_behavior_ids") or [])))
            candidate_followups = candidate_followup_values or ("<no-explicit-followup>",)
            def narrative_signature(value: Any) -> str:
                return "".join(char for char in str(value or "") if not char.isdigit() and char.isalnum()).casefold()
            candidate_content = "" if candidate_followup_values else narrative_signature(
                " ".join(str(beat.get(key) or "") for key in ("summary", "fact", "goal_hint"))
            )
            source_type = str(source_event.get("event_type") or "")
            source_id = str(source_event.get("source_id") or "")
            stage_id = str(source_event.get("stage_id") or "")
            audience_key = tuple(sorted(str(item) for item in audience))
            for prior in state.get("emergent_beats", []) or []:
                if not isinstance(prior, dict):
                    continue
                if (
                    str(prior.get("source_event_type") or "") == source_type
                    and str(prior.get("source_event_source_id") or "") == source_id
                    and str(prior.get("source_event_stage_id") or "") == stage_id
                    and tuple(sorted(str(item) for item in (prior.get("audience") or []))) == audience_key
                    and (tuple(sorted(str(item) for item in (prior.get("unlock_behavior_ids") or []))) or ("<no-explicit-followup>",)) == candidate_followups
                    and (candidate_followup_values or narrative_signature(" ".join(str(prior.get(key) or "") for key in ("summary", "fact", "goal_hint"))) == candidate_content)
                ):
                    return {"status": "wait", "reason": "This validated result already has the same audience and continuation; wait for a different outcome, stage, or executable follow-up."}
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_story_beat", proposal, minute)
            state[beat_id] = {"status": "active", "started_minute": minute, "actor_id": actor_id, "audience": list(audience)}
            if research_source_id:
                state[beat_id]["research_source_id"] = research_source_id
            if emergent:
                emergent_beats = list(state.get("emergent_beats", []) or [])
                emergent_beats.append({key: value for key, value in beat.items() if key != "unlock_choices"})
                state["emergent_beats"] = emergent_beats[-64:]
            self.store.set_meta("story_state", state)
            for audience_id in audience:
                try:
                    target = self.store.get_agent(audience_id)
                except KeyError:
                    continue
                goal_hint = str(beat.get("goal_hint") or proposal.get("goal_hint") or "").strip()
                if goal_hint:
                    goals = list(target.get("story_goals", []) or [])
                    if goal_hint not in goals:
                        goals.append(goal_hint)
                    target["story_goals"] = goals[-8:]
                for behavior_id in beat.get("unlock_behavior_ids", []) or []:
                    behavior_id = str(behavior_id)
                    if behavior_id in registered:
                        unlocked = list(target.get("unlocked_bt_ids", []) or [])
                        if behavior_id not in unlocked and behavior_id not in (target.get("initial_bt_ids", []) or []):
                            unlocked.append(behavior_id)
                        target["unlocked_bt_ids"] = unlocked
                if research_source_id:
                    targets = list(target.get("research_targets", []) or [])
                    if research_source_id not in targets:
                        targets.append(research_source_id)
                    target["research_targets"] = targets[-16:]
                fact = str(beat.get("fact") or proposal.get("fact") or "").strip()
                if not fact and not emergent:
                    # Behavior entry conditions may depend on the public
                    # ``story:<beat_id>`` key.  A configured beat must grant it
                    # even when the model supplies no separate fact text.
                    fact = str(beat.get("summary") or beat.get("agent_label") or "").strip()
                if fact:
                    self.store.merge_knowledge_fact(target["id"], f"story:{beat_id}", {"fact": fact, "source_type": "story", "source_id": beat_id, "confidence": 0.9, "observed_at": minute})
                self.store.update_agent(target["id"], target)
            event_id = self.store.append_event("story.beat.activated", actor_id, minute, {"beat_id": beat_id, "summary": beat.get("summary", ""), "audience": audience, "research_source_id": research_source_id, "public_event": beat.get("public_event"), "reason": proposal.get("reason", "")})
            remember_role(self.store, "story", "story_thread", {
                "summary": str(beat.get("summary") or beat.get("fact") or ""),
                "beat_id": beat_id,
                "goal_hint": str(beat.get("goal_hint") or proposal.get("goal_hint") or ""),
                "audience": list(audience),
                "source_event_type": str((source_event or {}).get("event_type") or ""),
                "status": "active",
            }, memory_key=f"story:beat:{beat_id}:{source_event.get('event_id') if isinstance(source_event, dict) else event_id}", source_event_id=int((source_event or {}).get("event_id") or event_id))
            self.store.finish_intent(intent_id, "accepted", {"status": "accepted", "intent_id": intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True})
        return {"status": "accepted", "intent_id": intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True}
