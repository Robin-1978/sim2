"""World change validation: the gates one ``propose_world_change`` proposal must pass.

Each gate returns the stored rejection or ``None``; the first rejection ends
the submission.  Gates only read Platform state.  They may normalize
``submission.proposal`` (canonical property key, canonical route endpoints,
coerced property value); the only write is the audit record of a rejected
intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

WORLD_CHANGE_TYPES = frozenset({"world_property", "rule", "relationship", "route", "faction", "event"})
PROPERTY_CHANGE_TYPES = frozenset({"world_property", "event"})
OBJECT_PAYLOAD_TYPES = frozenset({"rule", "relationship", "faction"})
STRUCTURED_MUTATION_TYPES = frozenset({
    "state_delta", "need_delta", "attribute_delta", "attribute_set", "inventory_add",
    "inventory_remove", "world_property_delta", "world_property_set", "relationship_delta", "entity_state_set",
})


@dataclass
class WorldChangeSubmission:
    """One world change proposal moving through the validation gates."""

    intent_id: str
    actor_id: str
    proposal: dict[str, Any]
    origin_role: str | None
    change_type: str = ""


def _coerce_property_value(before_value: Any, value: Any, property_name: str) -> tuple[Any, str | None]:
    """Return ``(value, error)``: the value in the type of the current property, or why it does not fit."""

    if isinstance(before_value, bool):
        if not isinstance(value, bool):
            return value, f"World property {property_name} is boolean; value must be true or false."
    elif isinstance(before_value, (int, float)):
        numeric_error = f"World property {property_name} is numeric; value must be a number."
        if isinstance(value, bool):
            return value, numeric_error
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                return value, numeric_error
        elif not isinstance(value, (int, float)):
            return value, numeric_error
        if isinstance(before_value, float):
            value = float(value)
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
    return value, None


class WorldChangeValidationMixin:
    """Validation gates for world change proposals."""

    def _validate_world_change(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """Run the gates in order; return the first stored rejection, or ``None``."""

        for gate in (
            self._check_required_fields,
            self._check_change_type,
            self._check_property_key,
            self._check_property_required,
            self._check_payload_object,
            self._check_rule,
            self._check_route,
            self._check_event_effects,
            self._check_property_value,
        ):
            rejection = gate(submission)
            if rejection is not None:
                return rejection
        return None

    def _reject(self, submission: WorldChangeSubmission, message: str, failed_check: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "rejected", "message": message}
        if failed_check is not None:
            result["failed_check"] = failed_check
        result["retryable"] = True
        return self._context.services["intent"].reject_intent(submission.intent_id, submission.actor_id, "propose_world_change", submission.proposal, result)

    @staticmethod
    def _changes_property(submission: WorldChangeSubmission) -> bool:
        return submission.change_type in PROPERTY_CHANGE_TYPES and bool(submission.proposal.get("property"))

    def _check_required_fields(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        proposal = submission.proposal
        if not proposal.get("change_type") or not proposal.get("summary"):
            return self._reject(submission, "change_type and summary are required.")
        submission.change_type = str(proposal["change_type"])
        return None

    def _check_change_type(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        if submission.change_type not in WORLD_CHANGE_TYPES:
            return self._reject(submission, "Unsupported world change type.", "change_type")
        return None

    def _check_property_key(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """Store the configured property key; God may set only properties the package opens to it."""

        if not self._changes_property(submission):
            return None
        proposal = submission.proposal
        current_properties = self.store.repositories.metadata.get("world_properties", {}) or {}
        submission.proposal = proposal = {**proposal, "property": self._canonical_world_property(str(proposal["property"]), current_properties)}
        if submission.actor_id == "god" and not self._god_property_change_allowed(str(proposal["property"])):
            return self._reject(
                submission,
                "God cannot directly change a world property. Submit a rule, entity, faction, or narrative proposal; world behavior must produce the state change.",
                "god_direct_property_change",
            )
        return None

    def _check_property_required(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        if submission.change_type == "world_property" and not submission.proposal.get("property"):
            return self._reject(submission, "property is required for a world_property change.", "property")
        return None

    def _check_payload_object(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        change_type = submission.change_type
        if change_type in OBJECT_PAYLOAD_TYPES and not isinstance(submission.proposal.get(change_type), dict):
            return self._reject(submission, f"{change_type} object is required for a {change_type} change.", change_type)
        return None

    def _check_rule(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """Rule changes: compiler schema, declared conditions and effects, then signal safety."""

        if submission.change_type != "rule":
            return None
        # Definitions accepted since the last rule may include new behaviors.
        # Validate against the current registry instead of the bootstrap
        # compiler snapshot, otherwise a valid rule names an unpublished BT.
        self._context.set_dependency("rule_compiler", self._context.services["api"].new_rule_compiler())
        proposal = submission.proposal
        valid, message = self.rule_compiler.validate(dict(proposal["rule"]))
        if not valid:
            return self._reject(submission, message, "rule_schema")
        conditions_valid, conditions_message = self.rule_compiler.validate_declared_conditions(
            dict(proposal["rule"]), list(proposal.get("conditions") or [])
        )
        if not conditions_valid:
            return self._reject(submission, conditions_message, "rule_condition_semantics")
        declared_valid, declared_message = self.rule_compiler.validate_declared_effects(
            dict(proposal["rule"]),
            list(proposal.get("effects") or []) + list(proposal.get("expected_effects") or []),
        )
        if not declared_valid:
            return self._reject(submission, declared_message, "rule_effect_semantics")
        authorization_rejection = self._check_authorization_rule(submission)
        if authorization_rejection is not None:
            return authorization_rejection
        return self._check_rule_signals(submission)

    def _check_authorization_rule(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """An authorization rule must bind an existing resource and the right operation."""

        rule = dict(submission.proposal["rule"])
        compiled = self.rule_compiler.compile(rule)
        effect = compiled.get("effect") or {}
        if str(effect.get("type") or "").casefold() != "authorization":
            return None
        mode = str(effect.get("mode") or "resource_access")
        if mode == "resource_access":
            target_ref = str(effect.get("target") or "").strip()
            target_id = str(self.targets.canonical_id(target_ref) or target_ref)
            try:
                target = self.store.repositories.entities.get_entity(target_id)
            except KeyError:
                return self._reject(submission, "A resource_access authorization rule must target an existing resource.", "rule_authorization_target")
            rules = self.store.repositories.metadata.get("rules", {}) or {}
            resource_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
            if str(target.get("entity_type") or "") not in resource_types:
                return self._reject(submission, "A resource_access authorization rule must target a resource or material.", "rule_authorization_target")
            effect["target"] = target_id
            submission.proposal["rule"]["effect"]["target"] = target_id
            if not str(effect.get("scope") or "").strip():
                return self._reject(submission, "A resource_access authorization rule needs a scope.", "rule_scope")
        if mode == "violation_review":
            selectors = {
                str(item)
                for item in (compiled.get("behavior_ids") or []) + (compiled.get("behavior_names") or [])
                if str(item)
            }
            behaviors = self.store.repositories.entities.get_definitions("bt_def")
            matched = [
                item for item in behaviors
                if str(item.get("id")) in selectors
                or str(item.get("agent_label") or "") in selectors
                or str(item.get("summary") or "") in selectors
            ]
            if not matched:
                return self._reject(submission, "A violation_review authorization rule must name a registered behavior.", "rule_behavior")
            if any(str(item.get("authorization_operation") or "") != "review_violation" for item in matched):
                return self._reject(submission, "A violation_review authorization rule must govern a review_violation behavior.", "rule_behavior")
        return None

    def _check_rule_signals(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """A signal rule must not wake its proposer, chain into another signal or repeat a registered rule."""

        rule = submission.proposal["rule"]
        compiled_rule = self.rule_compiler.compile(dict(rule))
        wake_roles = {str(item).strip() for item in (compiled_rule.get("wake") or []) if str(item).strip()}
        if submission.origin_role and str(submission.origin_role) in wake_roles:
            return self._reject(
                submission,
                "A dynamic signal cannot wake the same role that proposed it. Use a different consumer, an executable follow-up, or wait for new evidence.",
                "rule_self_wake",
            )
        existing_rules = (self.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) or []
        signal_conflict = self.rule_compiler.signal_conflict(dict(rule), existing_rules)
        if signal_conflict:
            conflict_type, conflict_name = signal_conflict
            if conflict_type == "cycle":
                message = "A signal rule cannot wake the same consumer from another signal rule. Use the behavior result as the next state change, or wait for a new world event."
                failed_check = "rule_signal_cycle"
            else:
                message = f"A signal for this event and follow-up already exists ({conflict_name}); review its outcome instead of adding another signal."
                failed_check = "rule_duplicate"
            return self._reject(submission, message, failed_check)
        proposed_id = str(rule.get("id") or rule.get("name") or "").strip()
        if proposed_id and any(str(item.get("id") or item.get("name") or "").strip() == proposed_id for item in existing_rules if isinstance(item, dict)):
            return self._reject(
                submission,
                "A world rule with this name is already registered; review its existing outcome instead of adding a duplicate.",
                "rule_duplicate",
            )
        return None

    def _check_route(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """Route changes: both endpoints are locations, distinct, positively spaced and not yet connected."""

        if submission.change_type != "route":
            return None
        proposal = submission.proposal
        route = proposal.get("route") if isinstance(proposal.get("route"), dict) else {}
        source = self.targets.canonical_id(str(route.get("from") or ""))
        target = self.targets.canonical_id(str(route.get("to") or ""))
        distance = route.get("distance")
        locations = {str(item.get("id")): item for item in self.store.repositories.entities.entities_of_type("location")}
        if not source or source not in locations or not target or target not in locations:
            return self._reject(submission, "route.from and route.to must name existing locations.", "route_endpoint")
        if source == target:
            return self._reject(submission, "A route must connect two different locations.", "route_cycle")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or float(distance) <= 0:
            return self._reject(submission, "route.distance must be positive.", "route_distance")
        source_data = self.store.repositories.entities.get_entity(source)
        target_data = self.store.repositories.entities.get_entity(target)
        if str(target) in {str(item) for item in (source_data.get("routes") or [])} and str(source) in {str(item) for item in (target_data.get("routes") or [])}:
            return self._reject(submission, "That route is already registered.", "route_duplicate")
        submission.proposal = {**proposal, "route": {"from": source, "to": target, "distance": float(distance)}}
        return None

    def _check_event_effects(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """An event without a property is narrative: it may not carry typed state mutations."""

        proposal = submission.proposal
        if submission.change_type != "event" or proposal.get("property"):
            return None
        if any(
            isinstance(item, dict) and str(item.get("type") or item.get("op") or "") in STRUCTURED_MUTATION_TYPES
            for item in (proposal.get("effects") or [])
        ):
            return self._reject(
                submission,
                "Event effects must be narrative only: remove typed state-mutating effects. Use a world_property, relationship, faction, or rule proposal for structured change, or let a behavior outcome change state.",
                "event_effects",
            )
        return None

    def _check_property_value(self, submission: WorldChangeSubmission) -> dict[str, Any] | None:
        """Store the value in the property's type and reject a change that leaves it unchanged."""

        if not self._changes_property(submission):
            return None
        proposal = submission.proposal
        property_name = str(proposal["property"])
        current_properties = self.store.repositories.metadata.get("world_properties", {}) or {}
        value, type_error = _coerce_property_value(current_properties.get(property_name), proposal.get("value"), property_name)
        if type_error is not None:
            return self._reject(submission, type_error, "property_type")
        submission.proposal = {**proposal, "value": value}
        if property_name in current_properties and current_properties.get(property_name) == value:
            return self._reject(
                submission,
                "The proposed world property already has this value; submit a real state change or a narrative event without a property.",
                "no_state_change",
            )
        return None

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
            entity = self.store.repositories.entities.get_entity(property_id)
        except KeyError:
            entity = None
        if entity and entity.get("entity_type") == "resource":
            return False
        policy = self.store.repositories.metadata.get("rules", {}).get("world_change_policy", {}) or {}
        allowed = policy.get("god_direct_properties", []) if isinstance(policy, dict) else []
        return property_id in {str(item) for item in (allowed or [])}
