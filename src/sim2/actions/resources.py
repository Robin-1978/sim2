from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..persistence.store import WorldStore


class ResourceLedger:
    """Reserve configured resources from an Agent or a shared world entity."""

    def __init__(self, store: "WorldStore"):
        self.store = store

    def reserve(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], *, commit: bool = True) -> tuple[bool, str, list[dict[str, Any]]]:
        required: list[tuple[str, float]] = []
        for key, value in (action.get("costs", {}).get("reserve", {}) or {}).items():
            if key == "recipe_inputs":
                recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
                if recipe is None:
                    return False, "recipe is unavailable", []
                limit_error = self._check_recipe_output_limits(recipe)
                if limit_error:
                    return False, limit_error, []
                for requirement in recipe.get("requires", []) or []:
                    capability = str(requirement.get("capability", ""))
                    minimum = float(requirement.get("min_level", 0) or 0)
                    if float((agent.get("capabilities") or {}).get(capability, 0) or 0) < minimum:
                        return False, f"capability {capability} is below recipe requirement", []
                for item in (recipe or {}).get("inputs", []):
                    required.append((str(item.get("item")), float(item.get("quantity", 0))))
            elif key == "item_parameter":
                item_id = parameters.get(str(value))
                if item_id:
                    required.append((str(item_id), 1.0))
            elif key == "resource_parameter":
                resource_id = parameters.get(str(value))
                amount = float((action.get("costs", {}).get("reserve", {}) or {}).get("resource_amount", 1) or 1)
                if resource_id:
                    required.append((str(resource_id), amount))
            elif key == "resource_amount":
                continue
            elif isinstance(value, (int, float)):
                required.append((str(key), float(value)))
        inventory = dict(agent.get("inventory") or {})
        reservation: list[dict[str, Any]] = []
        for key, amount in required:
            if amount <= 0:
                continue
            if float(inventory.get(key, 0)) >= amount:
                reservation.append({"scope": "agent", "key": key, "amount": amount})
                inventory[key] = inventory.get(key, 0) - amount
                continue
            world = self._world_resource(key)
            if world is None or world["amount"] < amount:
                return False, f"required resource {key} is unavailable", []
            reservation.append({"scope": "world", "entity_id": world["entity_id"], "key": key, "amount": amount})
        if commit:
            for item in reservation:
                if item["scope"] == "agent":
                    key = item["key"]
                    value = inventory[key]
                    agent.setdefault("inventory", {})[key] = int(value) if float(value).is_integer() else value
                else:
                    self._change_world_resource(item["entity_id"], -float(item["amount"]), actor_id=agent.get("id"))
        return True, "", reservation

    def _check_recipe_output_limits(self, recipe: dict[str, Any]) -> str | None:
        """Enforce declarative world-wide output caps before reserving inputs."""

        limits = recipe.get("limits", []) or []
        if isinstance(limits, dict):
            limits = [limits]
        if not limits:
            return None
        totals: dict[str, float] = {}
        for agent in self.store.list_agents():
            for item, quantity in (agent.get("inventory", {}) or {}).items():
                totals[str(item)] = totals.get(str(item), 0.0) + float(quantity or 0)
        for limit in limits:
            if not isinstance(limit, dict) or not limit.get("item"):
                continue
            item = str(limit["item"])
            maximum = float(limit.get("max_total", limit.get("max", 0)) or 0)
            if maximum > 0 and totals.get(item, 0.0) >= maximum:
                return f"recipe output limit reached for {item} ({maximum:g} total)"
        return None

    def _world_resource(self, key: str) -> dict[str, Any] | None:
        try:
            entity = self.store.get_entity(key)
        except KeyError:
            return None
        data = entity.get("initial_state", {}) if isinstance(entity.get("initial_state"), dict) else {}
        amount = entity.get("amount")
        if amount is None:
            amount = data.get("quantity", data.get("amount"))
        if not isinstance(amount, (int, float)):
            return None
        return {"entity_id": key, "amount": float(amount)}

    def _change_world_resource(self, entity_id: str, delta: float, *, actor_id: str | None = None) -> None:
        entity = self.store.get_entity(entity_id)
        before = self._world_resource(entity_id)
        if isinstance(entity.get("amount"), (int, float)):
            entity["amount"] = float(entity["amount"]) + delta
            self.store.put_entity(entity_id, entity["entity_type"], entity["name"], {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}})
        else:
            state = dict(entity.get("initial_state") or {})
            key = "quantity" if isinstance(state.get("quantity"), (int, float)) else "amount"
            state[key] = float(state.get(key, 0)) + delta
            entity["initial_state"] = state
            self.store.put_entity(entity_id, entity["entity_type"], entity["name"], {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}})
        after = self._world_resource(entity_id)
        if after is not None:
            self.store.sync_world_property_from_resource(entity_id, after["amount"])
        self.store.append_event(
            "resource.changed", actor_id, int(self.store.get_meta("world_minute", 0)),
            {"resource_id": entity_id, "delta": float(delta), "before": (before or {}).get("amount"), "after": (after or {}).get("amount"), "source": "reservation_or_release"},
        )

    def release(self, agent: dict[str, Any], reservations: list[dict[str, Any]]) -> None:
        """Return resources held by a paused action that is cancelled or fails."""

        for item in reservations:
            amount = float(item.get("amount", 0) or 0)
            if amount <= 0:
                continue
            if item.get("scope") == "agent":
                key = str(item.get("key"))
                value = float((agent.setdefault("inventory", {}).get(key, 0) or 0)) + amount
                agent["inventory"][key] = int(value) if value.is_integer() else value
            elif item.get("scope") == "world" and item.get("entity_id"):
                self._change_world_resource(str(item["entity_id"]), amount, actor_id=agent.get("id"))
