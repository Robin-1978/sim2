from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..evolution.memory import remember_role
from ..evolution.story import eligible_story_beats, story_evidence_event_types
from ..world.lifecycle import trim_unlocked_behaviors, unlocked_behavior_limit

_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?")
_NO_FOLLOWUP = ("<no-explicit-followup>",)
_PROPOSAL_TEXT_FIELDS = ("beat", "summary", "fact", "goal_hint", "research_source", "reason")


def _visible_label(value: Any) -> str:
    text = str(value or "").strip()
    # Models sometimes serialize a visible name with one extra pair of quotes
    # inside the JSON string. Remove only wrapping quotes; exact Platform name
    # resolution still decides whether it exists.
    for _ in range(2):
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"\"", "'"}:
            text = text[1:-1].strip()
        else:
            break
    return text


def _fold(value: Any) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())


def _signature(value: Any) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _narrative_signature(value: Any) -> str:
    return "".join(char for char in str(value or "") if not char.isdigit() and char.isalnum()).casefold()


def _beat_key(beat: dict[str, Any]) -> str:
    return _fold(beat.get("agent_label") or beat.get("summary") or beat.get("id", ""))


def _source_identity(event: dict[str, Any]) -> str:
    """Research results use source_id, while generic creation events identify
    their distinct object with entity_id.  Keeping that identity in a story
    signature lets separate creations each carry a grounded continuation
    without allowing a retry to create a duplicate."""

    return str(event.get("source_id") or event.get("entity_id") or event.get("object_id") or event.get("area_id") or "")


@dataclass
class _Submission:
    """One story beat proposal moving through the validation gates."""

    intent_id: str
    actor_id: str
    proposal: dict[str, Any]
    story_root: dict[str, Any]
    beats: list[dict[str, Any]]
    state: dict[str, Any]
    recent: list[dict[str, Any]]
    source_event: dict[str, Any] | None
    observed_events: set[str]
    observed_facts: set[str]
    scoped_beats: list[dict[str, Any]]
    # Filled in by the gates, in order.
    beat: dict[str, Any] = field(default_factory=dict)
    beat_id: str = ""
    emergent: bool = False
    minute: int = 0
    audience: list[str] = field(default_factory=list)
    research_source_id: str = ""
    registered: set[str] = field(default_factory=set)
    requested_unlocks: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)


class StoryRuntimeMixin:
    """Validate and commit Story beats from validated evidence."""

    def submit_story_beat(
        self,
        actor_id: str,
        proposal: dict[str, Any],
        *,
        source_event_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Activate one configured story beat after Platform checks its evidence.

        Each gate returns a rejection or wait result that ends the submission,
        or ``None`` to let the next gate run.  Nothing is written before
        ``_commit_beat``, except the audit record of a rejected intent.
        """

        submission = self._open_submission(actor_id, proposal, source_event_ids)
        if source_event_ids and submission.source_event is None:
            return self._reject(submission, "source_event", "The selected source event does not exist.", True)
        for gate in (
            self._choose_beat,
            self._check_beat_state,
            self._check_emergent_evidence,
            self._check_research_source_evidence,
            self._check_beat_triggers,
            self._resolve_audience,
            self._resolve_research_source,
            self._resolve_followups,
            self._check_followups_executable,
            self._check_duplicate_continuation,
        ):
            verdict = gate(submission)
            if verdict is not None:
                return verdict
        return self._commit_beat(submission)

    # ----------------------------------------------------------------- intake

    def _open_submission(self, actor_id: str, proposal: dict[str, Any], source_event_ids: list[int] | None) -> _Submission:
        intent_id = str(uuid4())
        proposal = dict(proposal or {})
        for key in _PROPOSAL_TEXT_FIELDS:
            if key in proposal and isinstance(proposal[key], str):
                proposal[key] = _visible_label(proposal[key])
        if isinstance(proposal.get("audience"), list):
            proposal["audience"] = [_visible_label(item) for item in proposal["audience"]]
        if isinstance(proposal.get("unlock_choices"), list):
            proposal["unlock_choices"] = [_visible_label(item) for item in proposal["unlock_choices"]]
        story_root = self.store.repositories.metadata.get("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        beats = list(story_root.get("beats", []) or []) if isinstance(story_root, dict) else []
        state = dict(self.store.repositories.metadata.get("story_state", {}) or {})
        self._expire_story_beats(story_root, state, int(self.store.repositories.metadata.get("world_minute", 0) or 0))
        recent = self.store.repositories.events.recent_events(limit=500)
        bound_sources = self.store.repositories.events.events_by_ids([int(item) for item in (source_event_ids or []) if int(item) > 0])
        configured_source_types = {str(item) for item in (story_root.get("emergent_source_event_types") or [])}
        source_event = next(reversed(bound_sources), None) if source_event_ids else next(
            (
                item for item in recent[:30]
                if bool(item.get("story_source"))
                or str(item.get("event_type", "")) in configured_source_types
            ),
            None,
        )
        observed_events = {str(item.get("event_type", "")) for item in recent}
        observed_events.update(story_evidence_event_types(source_event))
        fact_rows = self.store.connection.execute("SELECT fact_key FROM knowledge_fact").fetchall()
        observed_facts = {str(row["fact_key"]) for row in fact_rows}
        scoped_beats = eligible_story_beats(
            beats,
            state,
            source_event_type=story_evidence_event_types(source_event),
            observed_events=observed_events,
            observed_facts=observed_facts,
        )
        return _Submission(
            intent_id, actor_id, proposal, story_root, beats, state, recent,
            source_event, observed_events, observed_facts, scoped_beats,
        )

    def _expire_story_beats(self, story_root: dict[str, Any], state: dict[str, Any], minute: int) -> None:
        """Close or expire a beat whose configured lifetime has elapsed.

        Global beats have no audience follow-up, so without a lifetime they
        stay ``active`` forever and block the same configured beat from being
        used again.  A one-shot beat becomes ``completed``; a repeatable beat
        becomes ``expired`` and can be activated by a later validated event.
        """

        beats = {
            str(item.get("id")): item
            for item in (story_root.get("beats") or [])
            if isinstance(item, dict) and item.get("id")
        }
        changed = False
        for beat_id, entry in list(state.items()):
            if beat_id == "emergent_beats" or not isinstance(entry, dict) or entry.get("status") != "active":
                continue
            definition = beats.get(str(beat_id)) or {}
            ttl = int(definition.get("ttl_minutes", 0) or 0)
            started = int(entry.get("started_minute", 0) or 0)
            if ttl <= 0 or minute < started + ttl:
                continue
            entry = dict(entry)
            entry["status"] = "completed" if definition.get("one_shot") else "expired"
            entry["completed_minute"] = int(minute)
            state[beat_id] = entry
            changed = True
        if changed:
            with self.store.transaction():
                self.store.repositories.metadata.set("story_state", state)

    def _reject(self, submission: _Submission, failed_check: str, message: str, retryable: bool, **extra: Any) -> dict[str, Any]:
        return self._context.services["intent"].reject_intent(
            submission.intent_id,
            submission.actor_id,
            "propose_story_beat",
            submission.proposal,
            {"status": "rejected", "message": message, "failed_check": failed_check, "retryable": retryable, **extra},
        )

    # ------------------------------------------------------------- beat choice

    def _choose_beat(self, submission: _Submission) -> dict[str, Any] | None:
        proposal = submission.proposal
        choice = str(proposal.get("beat", "")).strip()
        key = _fold(choice)
        all_matches = [beat for beat in submission.beats if isinstance(beat, dict) and key == _beat_key(beat)]
        matches = [beat for beat in submission.scoped_beats if isinstance(beat, dict) and key == _beat_key(beat)]
        emergent_summary = str(proposal.get("summary") or proposal.get("fact") or "").strip()
        if not matches and not all_matches and not submission.scoped_beats and emergent_summary:
            if len(emergent_summary) < 8 or len(emergent_summary) > 500:
                return self._reject(submission, "story_summary", "An emergent story beat summary must be 8 to 500 characters.", True)
            proposal.setdefault("summary", emergent_summary)
            matches = [self._emergent_beat(proposal, choice, emergent_summary)]
            submission.emergent = True
        if len(matches) != 1:
            return self._reject(
                submission, "story_beat_choice", "Choose one exact configured story beat from the current story view.", True,
                available_choices=[str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")) for beat in submission.scoped_beats if isinstance(beat, dict)],
            )
        submission.beat = matches[0]
        submission.beat_id = str(submission.beat.get("id", ""))
        return None

    @staticmethod
    def _emergent_beat(proposal: dict[str, Any], choice: str, summary: str) -> dict[str, Any]:
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
        # An emergent chapter must not disappear because its audience did an
        # unrelated maintenance action. If Story names follow-up choices, use
        # those visible choices as the completion boundary; otherwise leave
        # the chapter active until a later Story result explicitly supersedes
        # or closes it.
        if beat["unlock_choices"]:
            beat["followup_behaviors"] = list(beat["unlock_choices"])
        return beat

    def _check_beat_state(self, submission: _Submission) -> dict[str, Any] | None:
        current = submission.state.get(submission.beat_id) if isinstance(submission.state.get(submission.beat_id), dict) else {}
        if current.get("status") in {"active", "completed"}:
            return self._reject(submission, "story_beat_state", "This story beat is already active or completed.", False)
        if submission.emergent and submission.source_event is None:
            return self._reject(submission, "story_evidence", "An emergent beat needs a recent discovery, research, recovery, or relationship result.", True)
        return None

    # ---------------------------------------------------------- evidence gates

    def _check_emergent_evidence(self, submission: _Submission) -> dict[str, Any] | None:
        """An emergent beat may interpret a validated result, never invent one."""

        source_event = submission.source_event
        if not (submission.emergent and source_event):
            return None
        beat = submission.beat
        prior_emergent = list(submission.state.get("emergent_beats", []) or [])
        if any(int(item.get("source_event_id", -1)) == int(source_event.get("event_id", -2)) for item in prior_emergent if isinstance(item, dict)):
            return {"status": "wait", "reason": "This validated result already has an active emergent story beat; wait for a new discovery or relationship result."}
        beat["source_event_id"] = int(source_event.get("event_id"))
        beat["source_event_type"] = str(source_event.get("event_type") or "validated.result")
        beat["source_event_source_id"] = _source_identity(source_event)
        beat["source_event_stage_id"] = str(source_event.get("stage_id") or "")
        # A new event does not automatically make a new story beat useful.
        # Repeated water-safety or pact-stability outcomes often arrive as
        # separate validated events but carry the same fact. Keep one durable
        # narrative thread until the fact itself changes; the source event
        # remains in the audit log for causal analysis.
        candidate_signature = _signature(beat.get("fact") or beat.get("summary"))
        if len(candidate_signature) >= 16 and any(
            _signature(item.get("fact") or item.get("summary")) == candidate_signature
            for item in prior_emergent
            if isinstance(item, dict)
        ):
            return {"status": "wait", "reason": "A materially identical validated result already has a story beat; wait for new evidence or a changed result."}
        return self._check_narrative_claims(submission, source_event)

    def _check_narrative_claims(self, submission: _Submission, source_event: dict[str, Any]) -> dict[str, Any] | None:
        """Keep numbers and later actions out of story state unless the source chain has them."""

        proposal = submission.proposal
        narrative = " ".join(str(proposal.get(key, "")) for key in ("summary", "fact", "goal_hint"))
        source_text = " ".join(
            str(source_event.get(key, ""))
            for key in ("event_type", "summary", "fact", "result", "actor_id", "location")
        )
        if not set(_NUMBER.findall(narrative)) <= set(_NUMBER.findall(source_text)):
            return self._reject(
                submission, "story_unverified_numeric_detail",
                "An emergent beat adds a numeric detail absent from the validated source event. State only the source result or wait for a confirming observation.",
                True,
            )
        # An emergent beat may not silently invent a later delivery, public
        # reading, gathering, or movement: those claims need a matching event
        # in the same chain.
        claim_guard = submission.story_root.get("narrative_claim_guard", {}) or {}
        action_claims = tuple(str(item).casefold() for item in (claim_guard.get("terms") or []))
        if any(term in narrative.casefold() for term in action_claims) and self._claim_followup(submission, source_event, claim_guard) is None:
            return self._reject(
                submission, "story_unverified_action_claim",
                "This emergent beat claims a later delivery, meeting, reading, or movement that is not present in a validated follow-up event. State only the source fact or wait until the action occurs.",
                True,
            )
        return None

    def _claim_followup(self, submission: _Submission, source_event: dict[str, Any], claim_guard: dict[str, Any]) -> dict[str, Any] | None:
        source_actor = str(source_event.get("actor_id") or "")
        source_event_id = int(source_event.get("event_id", 0) or 0)
        followup_event_types = {str(item) for item in (claim_guard.get("followup_event_types") or [])}
        followup_behavior_tags = {str(item) for item in (claim_guard.get("followup_behavior_tags") or [])}
        behaviors = {str(item.get("id")): item for item in self.store.repositories.entities.get_definitions("bt_def")}

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

        return next(
            (
                item for item in submission.recent
                if int(item.get("event_id", 0) or 0) > source_event_id
                and str(item.get("actor_id") or "") == source_actor
                and supports_claim(item)
            ),
            None,
        )

    def _check_research_source_evidence(self, submission: _Submission) -> dict[str, Any] | None:
        requested_research_source = str(submission.proposal.get("research_source") or "").strip()
        source_event = submission.source_event
        if not (requested_research_source and source_event):
            return None
        requested_source_id = str(self.targets.canonical_id(requested_research_source) or "")
        if not requested_source_id or not self.store.repositories.memory.research_discoveries(requested_source_id):
            return self._reject_research_source(submission)
        source_event_id = str(source_event.get("source_id") or "")
        source_event_type = str(source_event.get("event_type") or "")
        # A research result already names the source that produced it. Story
        # may assign a source for a non-research event, but it may not retell
        # one research result as if it came from another object. This is a
        # causal check, not a vocabulary check.
        if source_event_type == "research.completed" and (not source_event_id or requested_source_id != source_event_id):
            return self._reject(
                submission, "story_research_source_evidence",
                "The assigned research source must match the source named by the validated research result.",
                True,
            )
        return None

    def _reject_research_source(self, submission: _Submission) -> dict[str, Any]:
        return self._reject(
            submission, "story_research_source",
            "research_source must be an exact visible configured source with research stages.",
            True,
        )

    def _check_beat_triggers(self, submission: _Submission) -> dict[str, Any] | None:
        beat = submission.beat
        trigger_events = {str(item) for item in (beat.get("trigger_events", []) or [])}
        if trigger_events and not trigger_events & submission.observed_events:
            return self._reject(submission, "story_trigger", "The configured story beat has no observed trigger event yet.", True)
        required_facts = {str(item) for item in (beat.get("trigger_facts", []) or [])}
        if required_facts and not required_facts <= submission.observed_facts:
            return self._reject(
                submission, "story_facts", "The configured story beat is missing one or more required facts.", True,
                missing_facts=sorted(required_facts - submission.observed_facts),
            )
        return None

    # ----------------------------------------------------- audience and unlocks

    def _resolve_audience(self, submission: _Submission) -> dict[str, Any] | None:
        beat = submission.beat
        submission.minute = int(self.store.repositories.metadata.get("world_minute", 0))
        raw_audience = [str(item) for item in (beat.get("audience", []) or [])]
        resolved_audience: list[str] = []
        for value in raw_audience[:8]:
            canonical = self.targets.canonical_id(value)
            if canonical and self.store.connection.execute("SELECT 1 FROM agent_state WHERE id=?", (canonical,)).fetchone():
                resolved_audience.append(canonical)
        if raw_audience and not resolved_audience:
            return self._reject(submission, "story_audience", "An emergent beat needs at least one exact visible character audience.", True)
        submission.audience = resolved_audience
        if submission.emergent:
            beat["audience"] = list(submission.audience)
        return None

    def _resolve_research_source(self, submission: _Submission) -> dict[str, Any] | None:
        beat = submission.beat
        requested_source = str(submission.proposal.get("research_source") or beat.get("research_source") or "").strip()
        if not requested_source:
            return None
        research_source_id = str(self.targets.canonical_id(requested_source) or "")
        if not research_source_id or not self.store.repositories.memory.research_discoveries(research_source_id):
            return self._reject_research_source(submission)
        submission.research_source_id = research_source_id
        beat["research_source_id"] = research_source_id
        return None

    def _resolve_followups(self, submission: _Submission) -> dict[str, Any] | None:
        beat = submission.beat
        definitions = self.store.repositories.entities.get_definitions("bt_def")
        submission.registered = {item["id"] for item in definitions}
        submission.labels = {_fold(item.get("agent_label") or item.get("summary") or item.get("id", "")): str(item.get("id")) for item in definitions}
        labels = submission.labels
        submission.requested_unlocks = [str(value) for value in (beat.get("unlock_choices", []) or [])]
        unresolved_unlocks = [value for value in submission.requested_unlocks if _fold(value) not in labels]
        if unresolved_unlocks:
            return self._reject(
                submission, "story_followup_choice",
                "Every follow-up must be an exact visible behavior already registered in the audience catalog.",
                True,
                invalid_choices=unresolved_unlocks,
            )
        beat["unlock_behavior_ids"] = [labels[_fold(value)] for value in beat.get("unlock_choices", []) if _fold(value) in labels]
        return None

    def _check_followups_executable(self, submission: _Submission) -> dict[str, Any] | None:
        """A continuation is useful only when a named audience member can start it now.

        Checking the same executable catalog exposed to NPC decisions prevents
        a Story beat from assigning research, crafting, or institution work to
        a character who has no such behavior or lacks its prerequisites.
        """

        beat, audience = submission.beat, submission.audience
        if not (audience and beat.get("unlock_behavior_ids")):
            return None
        unavailable_followups: list[dict[str, Any]] = []
        for behavior_id in beat["unlock_behavior_ids"]:
            executable_for = []
            for audience_id in audience:
                candidate = self.view.build_decision_view(audience_id, role="agent_decision", include_diagnostics=True)
                if any(str(item.get("ref")) == f"behavior:{behavior_id}" for item in candidate.get("executable_behaviors", [])):
                    executable_for.append(audience_id)
            if not executable_for:
                unavailable_followups.append({
                    "behavior": next((str(value) for value in submission.requested_unlocks if submission.labels.get(_fold(value)) == behavior_id), behavior_id),
                    "audience": list(audience),
                })
        if unavailable_followups:
            return self._reject(
                submission, "story_followup_executable",
                "Every story follow-up must be executable by at least one audience member now.",
                True,
                unavailable_followups=unavailable_followups,
            )
        return None

    def _check_duplicate_continuation(self, submission: _Submission) -> dict[str, Any] | None:
        source_event, beat = submission.source_event, submission.beat
        if not (submission.emergent and source_event):
            return None
        candidate_followup_values = tuple(sorted(str(item) for item in (beat.get("unlock_behavior_ids") or [])))
        candidate_followups = candidate_followup_values or _NO_FOLLOWUP
        candidate_content = "" if candidate_followup_values else _narrative_signature(
            " ".join(str(beat.get(key) or "") for key in ("summary", "fact", "goal_hint"))
        )
        source_type = str(source_event.get("event_type") or "")
        source_id = _source_identity(source_event)
        stage_id = str(source_event.get("stage_id") or "")
        audience_key = tuple(sorted(str(item) for item in submission.audience))
        summary_signature = _signature(beat.get("summary"))
        for prior in submission.state.get("emergent_beats", []) or []:
            if not isinstance(prior, dict):
                continue
            prior_audience = tuple(sorted(str(item) for item in (prior.get("audience") or [])))
            if prior_audience != audience_key:
                continue
            if len(summary_signature) >= 16 and _signature(prior.get("summary")) == summary_signature:
                return {"status": "wait", "reason": "A materially identical chapter already reached this audience; wait for new evidence."}
            if (
                str(prior.get("source_event_type") or "") == source_type
                and str(prior.get("source_event_source_id") or "") == source_id
                and str(prior.get("source_event_stage_id") or "") == stage_id
                and (tuple(sorted(str(item) for item in (prior.get("unlock_behavior_ids") or []))) or _NO_FOLLOWUP) == candidate_followups
                and (candidate_followup_values or _narrative_signature(" ".join(str(prior.get(key) or "") for key in ("summary", "fact", "goal_hint"))) == candidate_content)
            ):
                return {"status": "wait", "reason": "This validated result already has the same audience and continuation; wait for a different outcome, stage, or executable follow-up."}
        return None

    # ------------------------------------------------------------------ commit

    def _commit_beat(self, submission: _Submission) -> dict[str, Any]:
        beat, beat_id, proposal, state = submission.beat, submission.beat_id, submission.proposal, submission.state
        actor_id, minute, audience = submission.actor_id, submission.minute, submission.audience
        with self.store.transaction():
            self.store.repositories.evolution.create_intent(submission.intent_id, actor_id, "propose_story_beat", proposal, minute)
            state[beat_id] = {
                "status": "active",
                "started_minute": minute,
                "actor_id": actor_id,
                "audience": list(audience),
                # Persist the Story Agent's public continuation so a later
                # God review can consume the same grounded thread without
                # reading private memory or reconstructing lost prose.
                "summary": str(beat.get("summary") or ""),
                "fact": str(beat.get("fact") or proposal.get("fact") or ""),
                "goal_hint": str(beat.get("goal_hint") or proposal.get("goal_hint") or ""),
            }
            if submission.research_source_id:
                state[beat_id]["research_source_id"] = submission.research_source_id
            if submission.emergent:
                emergent_beats = list(state.get("emergent_beats", []) or [])
                emergent_beats.append({key: value for key, value in beat.items() if key != "unlock_choices"})
                state["emergent_beats"] = emergent_beats[-64:]
            self.store.repositories.metadata.set("story_state", state)
            for audience_id in audience:
                self._grant_to_audience_member(submission, audience_id)
            source_event = submission.source_event
            event_id = self._context.services["events"].append("story.beat.activated", actor_id, minute, {
                "beat_id": beat_id,
                **({"source_event_id": source_event["event_id"], "source_event_type": source_event["event_type"]}
                   if source_event else {}),
                "title": str(beat.get("agent_label") or beat.get("summary") or beat_id),
                "summary": beat.get("summary", ""),
                "fact": beat.get("fact") or proposal.get("fact") or "",
                "goal_hint": beat.get("goal_hint") or proposal.get("goal_hint") or "",
                "audience": audience,
                "research_source_id": submission.research_source_id,
                "public_event": beat.get("public_event"),
                "reason": proposal.get("reason", ""),
            })
            remember_role(self.store, "story", "story_thread", {
                "summary": str(beat.get("summary") or beat.get("fact") or ""),
                "beat_id": beat_id,
                "goal_hint": str(beat.get("goal_hint") or proposal.get("goal_hint") or ""),
                "audience": list(audience),
                "source_event_type": str((source_event or {}).get("event_type") or ""),
                "status": "active",
            }, memory_key=f"story:beat:{beat_id}:{source_event.get('event_id') if isinstance(source_event, dict) else event_id}", source_event_id=int((source_event or {}).get("event_id") or event_id))
            self.store.repositories.evolution.finish_intent(submission.intent_id, "accepted", {"status": "accepted", "intent_id": submission.intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True})
        return {"status": "accepted", "intent_id": submission.intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True}

    def _grant_to_audience_member(self, submission: _Submission, audience_id: str) -> None:
        """Give one audience member the goal, unlocked behaviors, research target and story fact."""

        beat, beat_id, proposal = submission.beat, submission.beat_id, submission.proposal
        try:
            target = self.store.repositories.entities.get_agent(audience_id)
        except KeyError:
            return
        goal_hint = str(beat.get("goal_hint") or proposal.get("goal_hint") or "").strip()
        if goal_hint:
            goals = list(target.get("story_goals", []) or [])
            if goal_hint not in goals:
                goals.append(goal_hint)
            target["story_goals"] = goals[-8:]
        for behavior_id in beat.get("unlock_behavior_ids", []) or []:
            behavior_id = str(behavior_id)
            if behavior_id in submission.registered:
                unlocked = list(target.get("unlocked_bt_ids", []) or [])
                if behavior_id not in unlocked and behavior_id not in (target.get("initial_bt_ids", []) or []):
                    unlocked.append(behavior_id)
                target["unlocked_bt_ids"] = trim_unlocked_behaviors(
                    {**target, "unlocked_bt_ids": unlocked},
                    unlocked_behavior_limit(self.store.repositories.metadata.get("rules", {}) or {}),
                )
        if submission.research_source_id:
            targets = list(target.get("research_targets", []) or [])
            if submission.research_source_id not in targets:
                targets.append(submission.research_source_id)
            target["research_targets"] = targets[-16:]
        fact = str(beat.get("fact") or proposal.get("fact") or "").strip()
        if not fact and not submission.emergent:
            # Behavior entry conditions may depend on the public
            # ``story:<beat_id>`` key.  A configured beat must grant it even
            # when the model supplies no separate fact text.
            fact = str(beat.get("summary") or beat.get("agent_label") or "").strip()
        if fact:
            self.store.repositories.memory.merge_knowledge_fact(target["id"], f"story:{beat_id}", {"fact": fact, "source_type": "story", "source_id": beat_id, "confidence": 0.9, "observed_at": submission.minute})
        self.store.repositories.entities.update_agent(target["id"], target)
