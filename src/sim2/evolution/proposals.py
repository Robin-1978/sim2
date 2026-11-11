from __future__ import annotations

import json
from typing import Any


class WorldProposalEvaluator:
    """Evaluate typed world proposals against observable SQLite projections."""

    def __init__(self, store: Any, targets: Any):
        self.store = store
        self.targets = targets

    def evaluate_due(self, minute: int) -> None:
        for proposal in self.store.list_world_proposals(status="pending_evaluation", limit=100):
            if int(proposal.get("evaluation_due_minute", 0) or 0) > int(minute):
                continue
            created = int(proposal.get("created_minute", 0) or 0)
            rows = self.store.connection.execute(
                "SELECT event_id,event_type,world_minute,data_json FROM event_log WHERE world_minute>? AND world_minute<=? AND event_type NOT IN ('world_minute.advanced','evolution.task_due') ORDER BY event_id LIMIT 200",
                (created, int(minute)),
            ).fetchall()
            expected = proposal.get("expected", {}).get("state", {}) if isinstance(proposal.get("expected"), dict) else {}
            mode = str(expected.get("mode") or "signal_only")
            matched = False
            match_reason = ""
            current_value: Any = None
            if mode == "property_value":
                properties = self.store.get_meta("world_properties", {}) or {}
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
                    entity = self.store.get_entity(faction_id)
                except KeyError:
                    entity = {}
                current_value = {key: entity.get(key) for key in (expected.get("fields") or {})}
                matched = bool(entity) and all(current_value.get(key) == value for key, value in (expected.get("fields") or {}).items())
                match_reason = "faction_reached" if matched else "faction_not_at_expected_value"
            elif mode == "followup_event":
                expected_rule = str(expected.get("rule_id") or "")
                followup_behaviors = {str(item) for item in (expected.get("followup_behaviors") or [])}
                for row in rows:
                    payload = json.loads(row["data_json"] or "{}")
                    if row["event_type"] != "world.rule.followed":
                        continue
                    if expected_rule and str(payload.get("rule") or "") != expected_rule:
                        continue
                    if followup_behaviors and not ({str(payload.get("behavior_id") or ""), str(payload.get("action_id") or "")} & followup_behaviors):
                        continue
                    matched = True
                    break
                match_reason = "configured_followup_observed" if matched else "configured_followup_not_observed"
            else:
                match_reason = "signal_only_no_state_claim"
            observed = {
                "event_count": len(rows),
                "event_types": sorted({str(row["event_type"]) for row in rows}),
                "window": {"from_minute": created, "to_minute": int(minute)},
                "match": matched,
                "match_reason": match_reason,
                "mode": mode,
            }
            if current_value is not None:
                observed["current_value"] = current_value
            status = "observed" if matched else "no_observed_effect"
            result = {"status": status, "proposal_id": proposal["proposal_id"], "expected": proposal.get("expected", {}), "observed": observed}
            self.store.update_world_proposal(proposal["proposal_id"], status, observed, result, int(minute))
            self.store.append_event("world.proposal.evaluated", proposal.get("actor_id"), int(minute), result)
