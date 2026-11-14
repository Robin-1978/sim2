from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..actions.conditions import ConditionEngine
from ..evolution.memory import remember_role


class WorldChangesRuntimeMixin:
    """Validate and commit generic world-level changes and templates."""

    def submit_world_change(self, actor_id: str, proposal: dict[str, Any], *, origin_role: str | None = None) -> dict[str, Any]:
        intent_id = str(uuid4())
        if not proposal.get("change_type") or not proposal.get("summary"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "change_type and summary are required.", "retryable": True})
        change_type = str(proposal["change_type"])
        if change_type not in {"world_property", "rule", "relationship", "faction", "event"}:
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "Unsupported world change type.", "failed_check": "change_type", "retryable": True})
        if change_type in {"world_property", "event"} and proposal.get("property"):
            current_properties = self.store.get_meta("world_properties", {}) or {}
            proposal = {**proposal, "property": self._canonical_world_property(str(proposal["property"]), current_properties)}
            property_id = str(proposal["property"])
            if actor_id == "god" and not self._god_property_change_allowed(property_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "God cannot directly change a world property. Submit a rule, entity, faction, or narrative proposal; world behavior must produce the state change.",
                        "failed_check": "god_direct_property_change",
                        "retryable": True,
                    },
                )
        if change_type == "world_property" and not proposal.get("property"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "property is required for a world_property change.", "failed_check": "property", "retryable": True})
        if change_type == "rule" and not isinstance(proposal.get("rule"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "rule object is required for a rule change.", "failed_check": "rule", "retryable": True})
        if change_type == "rule" and isinstance(proposal.get("rule"), dict):
            valid, message = self.rule_compiler.validate(dict(proposal["rule"]))
            if not valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": message, "failed_check": "rule_schema", "retryable": True})
            conditions_valid, conditions_message = self.rule_compiler.validate_declared_conditions(
                dict(proposal["rule"]), list(proposal.get("conditions") or [])
            )
            if not conditions_valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": conditions_message, "failed_check": "rule_condition_semantics", "retryable": True})
            declared_valid, declared_message = self.rule_compiler.validate_declared_effects(
                dict(proposal["rule"]),
                list(proposal.get("effects") or []) + list(proposal.get("expected_effects") or []),
            )
            if not declared_valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": declared_message, "failed_check": "rule_effect_semantics", "retryable": True})
            proposed_id = str(proposal["rule"].get("id") or proposal["rule"].get("name") or "").strip()
            compiled_rule = self.rule_compiler.compile(dict(proposal["rule"]))
            wake_roles = {str(item).strip() for item in (compiled_rule.get("wake") or []) if str(item).strip()}
            if origin_role and str(origin_role) in wake_roles:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "A dynamic signal cannot wake the same role that proposed it. Use a different consumer, an executable follow-up, or wait for new evidence.",
                        "failed_check": "rule_self_wake",
                        "retryable": True,
                    },
                )
            existing_rules = (self.store.get_meta("rules", {}) or {}).get("world_rules", []) or []
            signal_conflict = self.rule_compiler.signal_conflict(dict(proposal["rule"]), existing_rules)
            if signal_conflict:
                conflict_type, conflict_name = signal_conflict
                if conflict_type == "cycle":
                    message = "A signal rule cannot wake the same consumer from another signal rule. Use the behavior result as the next state change, or wait for a new world event."
                    failed_check = "rule_signal_cycle"
                else:
                    message = f"A signal for this event and follow-up already exists ({conflict_name}); review its outcome instead of adding another signal."
                    failed_check = "rule_duplicate"
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": message, "failed_check": failed_check, "retryable": True})
            if proposed_id and any(str(item.get("id") or item.get("name") or "").strip() == proposed_id for item in existing_rules if isinstance(item, dict)):
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "A world rule with this name is already registered; review its existing outcome instead of adding a duplicate.", "failed_check": "rule_duplicate", "retryable": True})
        if change_type == "relationship" and not isinstance(proposal.get("relationship"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "relationship object is required for a relationship change.", "failed_check": "relationship", "retryable": True})
        if change_type == "faction" and not isinstance(proposal.get("faction"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "faction object is required for a faction change.", "failed_check": "faction", "retryable": True})
        if change_type == "event" and not proposal.get("property"):
            structured_mutation_types = {"state_delta", "need_delta", "attribute_delta", "attribute_set", "inventory_add", "inventory_remove", "world_property_delta", "relationship_delta", "entity_state_set"}
            structured_mutation = [item.get("type") or item.get("op") for item in (proposal.get("effects") or []) if isinstance(item, dict) and str(item.get("type") or item.get("op") or "") in structured_mutation_types]
            if structured_mutation:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "Event effects must be narrative only: remove typed state-mutating effects. Use a world_property, relationship, faction, or rule proposal for structured change, or let a behavior outcome change state.",
                        "failed_check": "event_effects",
                        "retryable": True,
                    },
                )
        if change_type in {"world_property", "event"} and proposal.get("property"):
            property_name = str(proposal["property"])
            current_properties = self.store.get_meta("world_properties", {}) or {}
            before_value = current_properties.get(property_name)
            value = proposal.get("value")
            if isinstance(before_value, bool):
                if not isinstance(value, bool):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is boolean; value must be true or false.", "failed_check": "property_type", "retryable": True,
                    })
            elif isinstance(before_value, (int, float)):
                if isinstance(value, bool):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                    })
                if isinstance(value, str):
                    try:
                        value = float(value.strip())
                    except ValueError:
                        return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                            "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                        })
                elif not isinstance(value, (int, float)):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                    })
                if isinstance(before_value, float):
                    value = float(value)
                elif isinstance(value, float) and value.is_integer():
                    value = int(value)
            proposal = {**proposal, "value": value}
            if change_type in {"world_property", "event"} and property_name in current_properties and current_properties.get(property_name) == value:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "The proposed world property already has this value; submit a real state change or a narrative event without a property.",
                        "failed_check": "no_state_change",
                        "retryable": True,
                    },
                )
        world_minute = int(self.store.get_meta("world_minute", 0))
        evaluation_window = max(1, min(int(proposal.get("evaluation_window_minutes", 1440) or 1440), 43200))
        evaluation_due_minute = world_minute + evaluation_window
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_world_change", proposal, world_minute)
            effect: dict[str, Any] = {}
            if change_type == "world_property":
                effect = self._set_world_property(str(proposal["property"]), proposal.get("value"))
            elif change_type == "rule":
                rules = dict(self.store.get_meta("rules", {}) or {})
                world_rules = list(rules.get("world_rules", []))
                world_rules.append(dict(proposal["rule"]))
                rules["world_rules"] = world_rules
                self.store.set_meta("rules", rules)
                self.conditions = ConditionEngine(rules)
                self.rule_compiler = self._new_rule_compiler()
                effect = {"rule_added": dict(proposal["rule"]), "rule_count": len(world_rules)}
            elif change_type == "relationship":
                relation = dict(proposal["relationship"])
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
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "relationship from and to are required.", "failed_check": "relationship", "retryable": True})
                    return {"status": "rejected", "message": "relationship from and to are required.", "failed_check": "relationship", "retryable": True}
                self.store.put_relationship(str(source), str(target), relation_type, relation)
                effect = {"relationship": {"from": str(source), "to": str(target), "type": relation_type, **relation}}
            elif change_type == "faction":
                faction = dict(proposal["faction"])
                faction_id = str(faction.pop("id", faction.pop("faction_id", "")))
                if not faction_id:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "faction id is required.", "failed_check": "faction.id", "retryable": True})
                    return {"status": "rejected", "message": "faction id is required.", "failed_check": "faction.id", "retryable": True}
                try:
                    current = self.store.get_entity(faction_id)
                except KeyError:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "faction does not exist.", "failed_check": "faction.id", "retryable": True})
                    return {"status": "rejected", "message": "faction does not exist.", "failed_check": "faction.id", "retryable": True}
                current.update(faction)
                self.store.put_entity(faction_id, "faction", current.get("name", faction_id), {key: value for key, value in current.items() if key not in {"id", "entity_type", "name"}})
                effect = {"faction_id": faction_id, "changed_fields": sorted(faction), "after": {key: current.get(key) for key in faction}}
            elif change_type == "event" and proposal.get("property"):
                effect = self._set_world_property(str(proposal["property"]), proposal.get("value"))
            else:
                effect = {"event_recorded": True, "state_changed": False}
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
            event_data["expected_state"] = expected
            event_id = self.uow.events.append(f"world.change.{change_type}", actor_id, world_minute, event_data)
            self.store.put_world_proposal(intent_id, intent_id, actor_id, change_type, "pending_evaluation", world_minute, evaluation_due_minute, {"effects": event_data["expected_effects"], "summary": event_data["summary"], "state": expected}, {"event_id": event_id, "effect": effect})
            if actor_id == "god":
                remember_role(self.store, "god", "world_proposal", {
                    "summary": str(proposal.get("summary", "")),
                    "change_type": change_type,
                    "expected_effects": list(event_data["expected_effects"]),
                    "status": "pending_evaluation",
                    "evaluation_due_minute": evaluation_due_minute,
                }, memory_key=f"god:proposal:{intent_id}", source_event_id=event_id)
            result = {"status": "accepted", "intent_id": intent_id, "proposal_id": intent_id, "event_id": event_id, "evaluation_status": "pending_evaluation", "evaluation_due_minute": evaluation_due_minute, "applied": True, "applied_effects": bool(effect.get("state_changed", True)), "change_type": change_type, "effect": effect, "expected_effects": event_data["expected_effects"]}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

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
        elif change_type == "relationship":
            expected.update({"mode": "relationship_state", "relationship": dict(effect.get("relationship") or proposal.get("relationship") or {})})
        elif change_type == "faction":
            expected.update({"mode": "faction_state", "faction_id": effect.get("faction_id"), "fields": dict(effect.get("after") or {})})
        return expected

    @staticmethod
    def _canonical_world_property(value: str, properties: dict[str, Any]) -> str:
        """Map a readable property label to its configured storage key."""

        def normalized(text: str) -> str:
            return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(text).casefold())).strip("_")

        candidate = normalized(value)
        for key in properties:
            if normalized(str(key)) == candidate:
                return str(key)
        return candidate or str(value).strip()

    def _god_property_change_allowed(self, property_id: str) -> bool:
        """Return whether the package explicitly permits God to set a property.

        The default is deny. Shared resources and personal/social outcomes must
        be changed by configured behaviors; a package may opt in to a narrow
        world property only when it has a documented reason.
        """

        try:
            entity = self.store.get_entity(property_id)
        except KeyError:
            entity = None
        if entity and entity.get("entity_type") == "resource":
            return False
        policy = self.store.get_meta("rules", {}).get("world_change_policy", {}) or {}
        allowed = policy.get("god_direct_properties", []) if isinstance(policy, dict) else []
        return property_id in {str(item) for item in (allowed or [])}

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
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "submit_world_template", {"template": template}, world_minute)
            self.uow.events.append("world.template.accepted", actor_id, world_minute, {"template_keys": sorted(template)})
            result = {"status": "accepted", "intent_id": intent_id, "template_valid": True, "message": "Template accepted for new-instance bootstrap."}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def bootstrap_world_from_template(self, template: dict[str, Any], instance_id: str, runtime_root: Path | None = None) -> dict[str, Any]:
        from ..bootstrap.service import create_instance_from_template

        validation = self.validate_world_template(template)
        if not validation["valid"]:
            return {"status": "rejected", "message": "World template failed validation.", "errors": validation["errors"], "retryable": True}
        db_path = create_instance_from_template(template, instance_id, runtime_root)
        return {"status": "accepted", "instance_id": instance_id, "db_path": str(db_path)}
