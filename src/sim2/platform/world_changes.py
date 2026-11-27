"""World change commit: apply an admitted change, publish its event and store the proposal.

``submit_world_change`` validates through ``WorldChangeValidationMixin`` and
commits the change in one transaction.  World templates live here too: they
are validated and recorded the same way but only seed new instances.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from ..actions.conditions import ConditionEngine
from ..evolution.memory import remember_role
from .world_change_validation import WorldChangeSubmission


class _ChangeRejected(Exception):
    """A change found unusable while it is applied, before anything was written."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result["message"])
        self.result = result


class WorldChangesRuntimeMixin:
    """Commit validated world-level changes and manage world templates."""

    @staticmethod
    def _normalize_rule_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
        """Lift a signal's event/wake into the rule's top-level fields.

        God models often describe a signal as ``effect: {type: signal,
        signal: ..., wake: [...]}``.  Runtime rule dispatch reads the
        top-level fields, so accepting the nested spelling unchanged creates a
        rule that silently never wakes anyone.  Normalize before validation.
        """

        if not isinstance(proposal, dict) or str(proposal.get("change_type") or "") != "rule":
            return proposal
        rule = proposal.get("rule")
        if not isinstance(rule, dict):
            return proposal
        effect = rule.get("effect")
        if not isinstance(effect, dict):
            return proposal
        normalized = dict(rule)
        signal = effect.get("signal") or effect.get("event_type")
        if signal and not str(normalized.get("event_type") or "").strip():
            normalized["event_type"] = str(signal)
        wake = effect.get("wake")
        if wake and not normalized.get("wake"):
            normalized["wake"] = [wake] if isinstance(wake, str) else list(wake)
        return {**proposal, "rule": normalized}

    def submit_world_change(self, actor_id: str, proposal: dict[str, Any], *, origin_role: str | None = None) -> dict[str, Any]:
        proposal = self._normalize_rule_proposal(proposal)
        submission = WorldChangeSubmission(str(uuid4()), actor_id, proposal, origin_role)
        rejection = self._validate_world_change(submission)
        if rejection is not None:
            return rejection
        return self._commit_world_change(submission)

    def _commit_world_change(self, submission: WorldChangeSubmission) -> dict[str, Any]:
        proposal = submission.proposal
        intent_id = submission.intent_id
        world_minute = int(self.store.repositories.metadata.get("world_minute", 0))
        evaluation_window = max(1, min(int(proposal.get("evaluation_window_minutes", 1440) or 1440), 43200))
        evaluation_due_minute = world_minute + evaluation_window
        with self.store.transaction():
            self.store.repositories.evolution.create_intent(intent_id, submission.actor_id, "propose_world_change", proposal, world_minute)
            try:
                effect = self._apply_change(submission)
            except _ChangeRejected as rejected:
                # Nothing was written yet: keep the intent and its rejection together.
                self.store.repositories.evolution.finish_intent(intent_id, "rejected", rejected.result)
                return rejected.result
            event_id, expected_effects = self._record_world_proposal(submission, effect, world_minute, evaluation_due_minute)
            result = {
                "status": "accepted", "intent_id": intent_id, "proposal_id": intent_id, "event_id": event_id,
                "evaluation_status": "pending_evaluation", "evaluation_due_minute": evaluation_due_minute,
                "applied": True, "applied_effects": bool(effect.get("state_changed", True)),
                "change_type": submission.change_type, "effect": effect, "expected_effects": expected_effects,
            }
            self.store.repositories.evolution.finish_intent(intent_id, "accepted", result)
        return result

    def _apply_change(self, submission: WorldChangeSubmission) -> dict[str, Any]:
        """Write the change and return its effect summary."""

        change_type = submission.change_type
        proposal = submission.proposal
        if change_type == "rule":
            return self._apply_rule(proposal)
        if change_type == "route":
            return self._apply_route(proposal)
        if change_type == "relationship":
            return self._apply_relationship(submission)
        if change_type == "faction":
            return self._apply_faction(proposal)
        if self._changes_property(submission):
            return self._context.services["tick"].set_world_property(str(proposal["property"]), proposal.get("value"))
        return {"event_recorded": True, "state_changed": False}

    def _apply_rule(self, proposal: dict[str, Any]) -> dict[str, Any]:
        rules = dict(self.store.repositories.metadata.get("rules", {}) or {})
        world_rules = list(rules.get("world_rules", []))
        normalized = self._normalize_rule_proposal({"change_type": "rule", "rule": proposal["rule"]})
        world_rules.append(dict(normalized["rule"]))
        rules["world_rules"] = world_rules
        self.store.repositories.metadata.set("rules", rules)
        self._context.set_dependency("conditions", ConditionEngine(rules))
        self._context.set_dependency("rule_compiler", self._context.services["api"].new_rule_compiler())
        return {"rule_added": dict(proposal["rule"]), "rule_count": len(world_rules)}

    def _apply_route(self, proposal: dict[str, Any]) -> dict[str, Any]:
        route = dict(proposal["route"])
        source = str(route["from"])
        target = str(route["to"])
        distance = float(route["distance"])
        source_data = self.store.repositories.entities.get_entity(source)
        target_data = self.store.repositories.entities.get_entity(target)
        source_routes = list(source_data.get("routes") or [])
        target_routes = list(target_data.get("routes") or [])
        if target not in source_routes:
            source_routes.append(target)
        if source not in target_routes:
            target_routes.append(source)
        source_distances = dict(source_data.get("route_distances") or {})
        target_distances = dict(target_data.get("route_distances") or {})
        source_distances[target] = distance
        target_distances[source] = distance
        source_data["routes"] = source_routes
        source_data["route_distances"] = source_distances
        target_data["routes"] = target_routes
        target_data["route_distances"] = target_distances
        self.store.repositories.entities.put_entity(source, "location", source_data.get("name", source), {key: value for key, value in source_data.items() if key not in {"id", "entity_type", "name"}})
        self.store.repositories.entities.put_entity(target, "location", target_data.get("name", target), {key: value for key, value in target_data.items() if key not in {"id", "entity_type", "name"}})
        return {"route_added": {"from": source, "to": target, "distance": distance}}

    def _apply_relationship(self, submission: WorldChangeSubmission) -> dict[str, Any]:
        relation = dict(submission.proposal["relationship"])
        source = relation.pop("from", relation.pop("from_id", ""))
        target = relation.pop("to", relation.pop("to_id", ""))
        # God and Story use visible names at the model boundary. Keep
        # the persisted relationship graph canonical so later views
        # and effects can resolve both directions reliably.
        participants = relation.pop("participants", None)
        if (not source or not target) and isinstance(participants, list) and len(participants) >= 2:
            source, target = participants[0], participants[1]
        source = self.targets.canonical_id(str(source)) if source else None
        target = self.targets.canonical_id(str(target)) if target else None
        relation_type = str(relation.pop("type", "related"))
        if not source or not target:
            raise _ChangeRejected({"status": "rejected", "message": "relationship from and to are required.", "failed_check": "relationship", "retryable": True})
        existing = self.store.connection.execute(
            "SELECT 1 FROM relationship WHERE from_id=? AND to_id=? AND relation_type=?",
            (source, target, relation_type),
        ).fetchone()
        submission.relationship_created = existing is None
        self.store.repositories.entities.put_relationship(str(source), str(target), relation_type, relation)
        return {"relationship": {"from": str(source), "to": str(target), "type": relation_type, **relation}}

    def _apply_faction(self, proposal: dict[str, Any]) -> dict[str, Any]:
        faction = dict(proposal["faction"])
        faction_id = str(faction.pop("id", faction.pop("faction_id", "")))
        if not faction_id:
            raise _ChangeRejected({"status": "rejected", "message": "faction id is required.", "failed_check": "faction.id", "retryable": True})
        try:
            current = self.store.repositories.entities.get_entity(faction_id)
        except KeyError:
            raise _ChangeRejected({"status": "rejected", "message": "faction does not exist.", "failed_check": "faction.id", "retryable": True}) from None
        current.update(faction)
        self.store.repositories.entities.put_entity(faction_id, "faction", current.get("name", faction_id), {key: value for key, value in current.items() if key not in {"id", "entity_type", "name"}})
        return {"faction_id": faction_id, "changed_fields": sorted(faction), "after": {key: current.get(key) for key in faction}}

    def _record_world_proposal(self, submission: WorldChangeSubmission, effect: dict[str, Any], world_minute: int, evaluation_due_minute: int) -> tuple[int, list[Any]]:
        """Publish ``world.change.*``, store the pending proposal and, for God, remember it."""

        proposal = submission.proposal
        change_type = submission.change_type
        intent_id = submission.intent_id
        actor_id = submission.actor_id
        event_data = {
            "intent_id": intent_id,
            "change_type": change_type,
            "summary": str(proposal.get("summary", "")),
            "scope": str(proposal.get("scope", "world")),
            "conditions": list(proposal.get("conditions") or []),
            "effects": list(proposal.get("effects") or []),
            "expected_effects": list(proposal.get("expected_effects") or proposal.get("effects") or []),
            "effect": effect,
            "proposal_id": intent_id,
            "evaluation_due_minute": evaluation_due_minute,
        }
        expected = self._world_proposal_expectation(change_type, proposal, effect)
        if change_type == "relationship":
            event_data["relationship_created"] = submission.relationship_created
        event_data["expected_state"] = expected
        event_id = self._context.services["events"].publish(f"world.change.{change_type}", actor_id, world_minute, event_data, wake="configured")
        self.store.repositories.evolution.put_world_proposal(intent_id, intent_id, actor_id, change_type, "pending_evaluation", world_minute, evaluation_due_minute, {"effects": event_data["expected_effects"], "summary": event_data["summary"], "state": expected}, {"event_id": event_id, "effect": effect})
        if actor_id == "god":
            remember_role(self.store, "god", "world_proposal", {
                "summary": str(proposal.get("summary", "")),
                "change_type": change_type,
                "expected_effects": list(event_data["expected_effects"]),
                "status": "pending_evaluation",
                "evaluation_due_minute": evaluation_due_minute,
            }, memory_key=f"god:proposal:{intent_id}", source_event_id=event_id)
        return event_id, event_data["expected_effects"]

    def _world_proposal_expectation(self, change_type: str, proposal: dict[str, Any], effect: dict[str, Any]) -> dict[str, Any]:
        """Compile the small machine-checkable expectation for God review.

        Free-form prose remains an explanation for the model.  Evaluation
        uses typed fields or a configured follow-up event, so unrelated events
        cannot make a proposal look successful merely because something
        happened during its observation window.
        """

        expected: dict[str, Any] = {"change_type": str(change_type), "mode": "signal_only"}
        if change_type in {"world_property", "event"} and proposal.get("property"):
            expected.update({"mode": "property_value", "property": str(proposal["property"]), "target_value": proposal.get("value")})
        elif change_type == "rule":
            rule = proposal.get("rule") if isinstance(proposal.get("rule"), dict) else {}
            expected.update({
                "mode": "followup_event",
                "rule_id": str(rule.get("id") or rule.get("name") or ""),
                "followup_behaviors": list(rule.get("behavior_names") or rule.get("behavior_ids") or rule.get("action_names") or []),
            })
        elif change_type == "route":
            expected.update({"mode": "route_topology", "route": dict(effect.get("route_added") or proposal.get("route") or {})})
        elif change_type == "relationship":
            expected.update({"mode": "relationship_state", "relationship": dict(effect.get("relationship") or proposal.get("relationship") or {})})
        elif change_type == "faction":
            expected.update({"mode": "faction_state", "faction_id": effect.get("faction_id"), "fields": dict(effect.get("after") or {})})
        return expected

    def validate_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        from ..bootstrap.service import validate_world_template

        report = validate_world_template(template)
        errors = list(report["errors"])
        return {
            "status": "ok",
            "valid": not errors,
            "errors": errors,
            "failed_check": "template" if errors else None,
            "message": "Template is valid." if not errors else "Template needs correction.",
            "retryable": bool(errors),
        }

    def submit_world_template(self, actor_id: str, template: dict[str, Any]) -> dict[str, Any]:
        validation = self.validate_world_template(template)
        if not validation["valid"]:
            return {"status": "rejected", "message": "World template failed validation.", "errors": validation["errors"], "retryable": True}
        intent_id = str(uuid4())
        world_minute = int(self.store.repositories.metadata.get("world_minute", 0))
        with self.store.transaction():
            self.store.repositories.evolution.create_intent(intent_id, actor_id, "submit_world_template", {"template": template}, world_minute)
            self._context.services["events"].append("world.template.accepted", actor_id, world_minute, {"template_keys": sorted(template)})
            result = {"status": "accepted", "intent_id": intent_id, "template_valid": True, "message": "Template accepted for new-instance bootstrap."}
            self.store.repositories.evolution.finish_intent(intent_id, "accepted", result)
        return result

    def bootstrap_world_from_template(self, template: dict[str, Any], instance_id: str, runtime_root: Path | None = None) -> dict[str, Any]:
        from ..bootstrap.service import create_instance_from_template

        validation = self.validate_world_template(template)
        if not validation["valid"]:
            return {"status": "rejected", "message": "World template failed validation.", "errors": validation["errors"], "retryable": True}
        db_path = create_instance_from_template(template, instance_id, runtime_root)
        return {"status": "accepted", "instance_id": instance_id, "db_path": str(db_path)}
