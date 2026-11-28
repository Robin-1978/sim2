"""Why a configured Action is, or is not, available to a character right now."""

from __future__ import annotations

from typing import Any

from ..actions.capacity import check_action_capacity
from ..actions.institutions import allocation_preflight
from ..actions.pacing import generated_action_unavailable
from ..world.context import action_condition_context


class ActionAvailabilityMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    @staticmethod
    def _view_action_parameters(parameters: dict[str, Any], state: dict[str, Any], context_values: dict[str, Any], *, safe_location_id: str | None = None, resource_node: dict[str, str] | None = None, health_location_id: str | None = None, default_target_id: str | None = None) -> dict[str, Any]:
        resolved = {}
        for key, value in parameters.items():
            if value == "context.location_id" or value == "context.station_id":
                resolved[key] = state.get("location_id")
            elif value == "context.safe_location_id":
                resolved[key] = safe_location_id
            elif value == "context.resource_location_id":
                resolved[key] = context_values.get("resource_location_id") or (resource_node or {}).get("location_id")
            elif value == "context.resource_tag":
                resolved[key] = (resource_node or {}).get("resource_tag")
            elif value == "context.health_location_id":
                resolved[key] = health_location_id
            elif value == "context.target_id":
                resolved[key] = default_target_id
            elif isinstance(value, str) and value.startswith("context."):
                resolved[key] = context_values.get(value.removeprefix("context."))
            else:
                resolved[key] = value
        return resolved

    def _action_available(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], location: dict[str, Any], duration_minutes: int | None = None) -> bool:
        return self._action_unavailable_reason(agent, action, parameters, location, duration_minutes) is None

    def _action_unavailable_reason(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], location: dict[str, Any], duration_minutes: int | None = None, *, reservations: list[dict[str, Any]] | None = None) -> str | None:
        """Return why one Action cannot start for this actor, or ``None`` when it can.

        The reason names the failing check in the Action's own terms so a
        reviewer who sees a blocked behavior learns what to change, instead of
        a generic refusal.
        """

        if reason := generated_action_unavailable(self.store, action, agent):
            return reason
        resource_parameters = (action.get("parameter_defaults", {}) or {})
        resource_key = next((key for key, value in resource_parameters.items() if value == "first_available_resource"), None)
        if resource_key and parameters.get(resource_key) in {None, "", "context.resource_tag"}:
            rules = self.store.repositories.metadata.get("rules", {}) or {}
            entity_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
            protected_key = str(rules.get("resource_protected_state", "protected"))
            source = next((item for item in self.entities_repo.entities_at_location((agent.get("initial_state") or {}).get("location_id")) if item.get("entity_type") in entity_types and not (item.get("initial_state") or {}).get(protected_key) and self._entity_quantity(item) > 0), None)
            parameters["resource_tag"] = source.get("id") if source else parameters.get("resource_tag")
        if action.get("requires_destination") and parameters.get("location_id") == (agent.get("initial_state") or {}).get("location_id"):
            return "the actor is already at the destination"
        resolved_target, target_error = self.targets.resolve(agent, parameters)
        if target_error:
            return "no permitted target can be resolved"
        target = {**parameters, **(resolved_target or {})}
        context = action_condition_context(
            self.store, agent, parameters, resolved_target, canonical_id=self.targets.canonical_id,
            recent_event_types=self._recent_event_types(), location=location,
        )
        for effect in action.get("effects", []) or []:
            if not isinstance(effect, dict) or str(effect.get("type") or effect.get("op") or "") != "institution_allocation":
                continue
            def resolve(value: Any) -> Any:
                if isinstance(value, str) and value.startswith("context."):
                    return parameters.get(value.removeprefix("context."), value)
                return value
            subjects = resolve(effect.get("subjects"))
            if isinstance(subjects, str):
                subjects = [subjects]
            # A reusable definition may intentionally bind these values from
            # the caller at execution time.  Without a concrete institution
            # and pool there is no safe preflight to perform here.
            if not resolve(effect.get("institution")) or not resolve(effect.get("pool_entity")) or str(resolve(effect.get("institution")) or "").startswith("context.") or str(resolve(effect.get("pool_entity")) or "").startswith("context."):
                continue
            check = allocation_preflight(
                self.store,
                resolve(effect.get("institution")),
                resolve(effect.get("pool_entity")),
                subjects if isinstance(subjects, list) else None,
            )
            context.update({
                "institution.allocation_possible": bool(check.get("possible")),
                "institution.allocation_available": check.get("available", 0.0),
                "institution.allocation_required": check.get("required", 0.0),
            })
            if not check.get("possible"):
                return (
                    f"the pool holds {float(check.get('available', 0) or 0):g} but the policy minimum needs {float(check.get('required', 0) or 0):g}"
                    if check.get("reason") == "minimum_allocation"
                    else f"the institution allocation is not possible ({check.get('reason') or 'unavailable'})"
                )
        if parameters.get("object_id") and not context["current_object"]:
            return "the referenced object does not exist"
        if parameters.get("source_id") and not context["source"]:
            return "the referenced source does not exist"
        if (
            action.get("duration", {}).get("mode") == "recipe_duration"
            or "recipe_inputs" in (action.get("costs", {}).get("reserve", {}) or {})
        ):
            recipe = next((item for item in self._definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
            if recipe is None:
                return "the recipe is not registered"
            context["station_tags"] = list(recipe.get("station_tags", []) or [])
        actor_location = str((agent.get("initial_state") or {}).get("location_id") or "")
        stock_effect_types = {"resource_transfer", "resource_withdrawal", "resource_take"}
        for effect in action.get("effects", []) or []:
            if not isinstance(effect, dict):
                continue
            if str(effect.get("type") or effect.get("op") or "") not in stock_effect_types:
                continue
            reference = effect.get("resource_id") or effect.get("resource")
            if isinstance(reference, str) and reference.startswith("context."):
                reference = parameters.get(reference.removeprefix("context."))
            if not reference:
                continue
            resource_id = str(self.targets.canonical_id(str(reference)) or reference)
            try:
                resource = self.entities_repo.get_entity(resource_id)
            except KeyError:
                continue
            state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
            resource_location = str(state.get("location_id") or resource.get("location_id") or "")
            if resource_location and resource_location != actor_location:
                return "the actor is not at the resource location"
        if self.rule_compiler.denial(
            action=action,
            agent=agent,
            target=target,
            context=context,
            world=self.store.repositories.metadata.get("world_properties", {}) or {},
        ):
            return "an active rule denies it"
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=target, context=context, world=self.store.repositories.metadata.get("world_properties", {}) or {}):
                return f"precondition not met: {predicate}"
        capacity_ok, capacity_detail = check_action_capacity(
            agent,
            action,
            max(1, int(duration_minutes or 1)),
            self.store.repositories.metadata.get("attributes", []) or [],
        )
        if not capacity_ok:
            return str(capacity_detail or "the actor lacks the capacity for its duration")
        allowed, reserve_detail, reserved = self.resources.reserve(agent, action, parameters, commit=False)
        if allowed and reservations is not None:
            reservations.extend(reserved)
        return None if allowed else str(reserve_detail or "required resources are unavailable")

    @staticmethod
    def _entity_quantity(entity: dict[str, Any]) -> float:
        state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
        value = entity.get("amount")
        if value is None:
            value = state.get("quantity", state.get("amount", 0))
        return float(value or 0)
