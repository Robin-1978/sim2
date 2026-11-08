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
                for item in (recipe or {}).get("inputs", []):
                    required.append((str(item.get("item")), float(item.get("quantity", 0))))
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
                    self._change_world_resource(item["entity_id"], -float(item["amount"]))
        return True, "", reservation

    def _world_resource(self, key: str) -> dict[str, Any] | None:
        try:
            entity = self.store.get_entity(key)
        except KeyError:
            return None
        data = entity.get("initial_state", {}) if isinstance(entity.get("initial_state"), dict) else {}
        amount = entity.get("amount", data.get("quantity", data.get("amount")))
        if not isinstance(amount, (int, float)):
            return None
        return {"entity_id": key, "amount": float(amount)}

    def _change_world_resource(self, entity_id: str, delta: float) -> None:
        entity = self.store.get_entity(entity_id)
        if isinstance(entity.get("amount"), (int, float)):
            entity["amount"] = float(entity["amount"]) + delta
            self.store.put_entity(entity_id, entity["entity_type"], entity["name"], {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}})
            return
        state = dict(entity.get("initial_state") or {})
        key = "quantity" if isinstance(state.get("quantity"), (int, float)) else "amount"
        state[key] = float(state.get(key, 0)) + delta
        entity["initial_state"] = state
        self.store.put_entity(entity_id, entity["entity_type"], entity["name"], {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}})
