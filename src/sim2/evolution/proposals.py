from __future__ import annotations

import json
from typing import Any

from .memory import remember_role


class WorldProposalEvaluator:
    """Evaluate typed world proposals against observable SQLite projections."""

    def __init__(self, store: Any, targets: Any):
        self.store = store
        self.targets = targets

    def evaluate_due(self, minute: int) -> None:
        due = self.store.repositories.evolution.list_world_proposals(status="pending_evaluation", due_by=minute, limit=100)
        for proposal in due:
            created = int(proposal.get("created_minute", 0) or 0)
            end = int(proposal["evaluation_due_minute"])
            cursor = int(proposal["result"].get("event_id") or 0)
            window = (created if cursor else created + 1, end, cursor)
            counts = self.store.connection.execute(
                """SELECT event_type, COUNT(*) AS total FROM event_log
                   WHERE world_minute BETWEEN ? AND ? AND event_id>?
                     AND event_type NOT IN ('world_minute.advanced','evolution.task_due')
                   GROUP BY event_type ORDER BY event_type""", window,
            ).fetchall()
            expected = proposal.get("expected", {}).get("state", {}) if isinstance(proposal.get("expected"), dict) else {}
            mode = str(expected.get("mode") or "signal_only")
            matched = False
            match_reason = ""
            current_value: Any = None
            outcome_event_id = None
            if mode == "property_value":
                properties = self.store.repositories.metadata.get("world_properties", {}) or {}
                property_name = str(expected.get("property") or "")
                current_value = properties.get(property_name)
                matched = current_value == expected.get("target_value")
                match_reason = "property_reached" if matched else "property_not_at_expected_value"
            elif mode == "relationship_state":
                relation = expected.get("relationship") if isinstance(expected.get("relationship"), dict) else {}
                source = self.targets.canonical_id(str(relation.get("from") or relation.get("from_id") or ""))
                target = self.targets.canonical_id(str(relation.get("to") or relation.get("to_id") or ""))
                relation_type = str(relation.get("type") or "related")
                if source and target:
                    row = self.store.connection.execute(
                        "SELECT data_json FROM relationship WHERE from_id=? AND to_id=? AND relation_type=?",
                        (str(source), str(target), relation_type),
                    ).fetchone()
                    current_value = json.loads(row["data_json"]) if row else None
                    matched = row is not None and all(current_value.get(key) == value for key, value in relation.items() if key not in {"from", "from_id", "to", "to_id", "type"})
                match_reason = "relationship_reached" if matched else "relationship_not_at_expected_value"
            elif mode == "faction_state":
                faction_id = str(expected.get("faction_id") or "")
                try:
                    entity = self.store.repositories.entities.get_entity(faction_id)
                except KeyError:
                    entity = {}
                current_value = {key: entity.get(key) for key in (expected.get("fields") or {})}
                matched = bool(entity) and all(current_value.get(key) == value for key, value in (expected.get("fields") or {}).items())
                match_reason = "faction_reached" if matched else "faction_not_at_expected_value"
            elif mode == "followup_event":
                expected_rule = str(expected.get("rule_id") or "")
                rule = proposal["result"].get("effect", {}).get("rule_added", {})
                legacy_name = rule.get("name") or rule.get("label") or expected_rule
                # A completed execution carries the rules its dispatcher actually matched.
                row = self.store.connection.execute(
                    """SELECT done.event_id FROM event_log AS done
                       WHERE done.event_type='behavior.completed'
                         AND done.world_minute BETWEEN ? AND ? AND done.event_id>?
                         AND EXISTS (
                           SELECT 1 FROM json_each(done.data_json,'$.outcome.rule_followups') AS followup
                           WHERE followup.type='object' AND (
                             json_extract(followup.value,'$.rule_id')=? OR
                             (json_extract(followup.value,'$.rule_id') IS NULL AND json_extract(followup.value,'$.rule')=?))
                         )
                       ORDER BY done.event_id LIMIT 1""", (*window, expected_rule, legacy_name),
                ).fetchone()
                matched = row is not None
                if row:
                    outcome_event_id = row["event_id"]
                match_reason = "configured_followup_observed" if matched else "configured_followup_not_observed"
            else:
                match_reason = "signal_only_no_state_claim"
            observed = {
                "event_count": sum(row["total"] for row in counts),
                "event_types": [row["event_type"] for row in counts],
                "window": {"from_minute": created, "to_minute": end},
                "match": matched,
                "match_reason": match_reason,
                "mode": mode,
            }
            if current_value is not None:
                observed["current_value"] = current_value
            if outcome_event_id is not None:
                observed["outcome_event_id"] = outcome_event_id
            status = "observed" if matched else "no_observed_effect"
            result = {"status": status, "proposal_id": proposal["proposal_id"], "expected": proposal.get("expected", {}), "observed": observed}
            self.store.repositories.evolution.update_world_proposal(proposal["proposal_id"], status, observed, result, int(minute))
            event_id = self.store.repositories.events.append_event("world.proposal.evaluated", proposal.get("actor_id"), int(minute), result)
            if str(proposal.get("actor_id") or "") == "god":
                remember_role(self.store, "god", "proposal_review", {
                    "summary": f"Proposal {proposal['proposal_id']} evaluated as {status}: {match_reason}.",
                    "proposal_id": proposal["proposal_id"],
                    "proposal_status": status,
                    "match_reason": match_reason,
                    "observed": observed,
                    "status": "confirmed",
                }, memory_key=f"god:proposal-evaluation:{proposal['proposal_id']}:{status}", source_event_id=event_id)
