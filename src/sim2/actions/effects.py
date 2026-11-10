from __future__ import annotations

import ast
import operator
from typing import Any

from ..world.schema import AttributeSchema


class EffectPipeline:
    """Apply declarative effects to an in-memory projection.

    The pipeline returns event-shaped descriptions; Platform decides when to
    persist the mutated projection and event in its transaction.  No package
    can execute arbitrary code through an effect definition.
    """

    def __init__(self, attributes: list[dict[str, Any]] | None = None):
        self.schema = AttributeSchema(attributes or [])

    def apply(self, agent: dict[str, Any], effect: dict[str, Any], *, target: dict[str, Any] | None = None, formula_agent: dict[str, Any] | None = None) -> dict[str, Any]:
        target = target or {}
        effect_type = effect.get("type") or effect.get("op")
        state = dict(agent.get("initial_state") or {})
        needs = dict(agent.get("needs") or {})
        inventory = dict(agent.get("inventory") or {})
        result: dict[str, Any] = {"type": effect_type, "applied": False}
        if effect_type in {"need_delta", "need.delta"}:
            key = str(effect.get("need"))
            value = float(needs.get(key, state.get(key, 0))) + float(effect.get("delta", effect.get("value", 0)))
            value = self.schema.clamp(key, value)
            if key in state:
                state[key] = value
            else:
                needs[key] = value
            result.update({"applied": True, "need": key, "value": value})
        elif effect_type in {"state_delta", "state.delta", "threat_delta"}:
            key = "threat_level" if effect_type == "threat_delta" else str(effect.get("state"))
            value = self.schema.clamp(key, float(state.get(key, self.schema.default(key, 0)) or 0) + float(effect.get("delta", effect.get("value", 0))))
            state[key] = value
            result.update({"applied": True, "state": key, "value": value})
        elif effect_type in {"attribute_delta", "attribute.delta", "attribute_set", "attribute.set"}:
            key = str(effect.get("attribute") or effect.get("state") or "")
            if not key:
                result["error"] = "attribute is required"
            else:
                before = state.get(key, self.schema.default(key, 0))
                value = effect.get("value") if effect_type in {"attribute_set", "attribute.set"} else float(before or 0) + float(effect.get("delta", 0) or 0)
                state[key] = self.schema.clamp(key, value)
                result.update({"applied": True, "attribute": key, "before": before, "value": state[key]})
        elif effect_type in {"public_event", "event.public"}:
            result.update({"applied": True, "event": str(effect.get("event") or "public.event")})
        elif effect_type in {"relationship_delta", "relationship.delta"}:
            result.update({"applied": True, "relation": str(effect.get("relation") or "related"), "delta": float(effect.get("delta", effect.get("value", 0)) or 0)})
        elif effect_type in {"inventory_add", "inventory.add", "add_item"}:
            item = str(effect.get("item"))
            amount = float(effect.get("quantity", effect.get("amount", 1)))
            inventory[item] = inventory.get(item, 0) + amount
            result.update({"applied": True, "item": item, "quantity": amount})
        elif effect_type in {"inventory_remove", "inventory.consume", "consume_item"}:
            item = str(effect.get("item"))
            amount = float(effect.get("quantity", effect.get("amount", 1)))
            if float(inventory.get(item, 0)) < amount:
                result.update({"error": f"insufficient inventory: {item}"})
            else:
                inventory[item] = inventory.get(item, 0) - amount
                result.update({"applied": True, "item": item, "quantity": amount})
        elif effect_type in {"status_add", "condition.add"}:
            conditions = list(state.get("conditions") or [])
            status = str(effect.get("status") or effect.get("condition"))
            if status not in conditions:
                conditions.append(status)
            state["conditions"] = conditions
            result.update({"applied": True, "status": status})
        elif effect_type in {"status_remove", "condition.remove"}:
            status = str(effect.get("status") or effect.get("condition"))
            state["conditions"] = [item for item in state.get("conditions", []) if item != status]
            result.update({"applied": True, "status": status})
        elif effect_type in {"damage", "health_delta"}:
            amount = self._formula(effect.get("formula"), formula_agent or agent, target) if effect.get("formula") else float(effect.get("amount", effect.get("delta", 0)))
            key = str(effect.get("attribute") or "health")
            state[key] = self.schema.clamp(key, float(state.get(key, self.schema.default(key, 100)) or 0) - amount if effect_type == "damage" else float(state.get(key, self.schema.default(key, 100)) or 0) + amount)
            result.update({"applied": True, "attribute": key, "value": state[key]})
        elif effect_type in {"heal", "health_restore"}:
            amount = float(effect.get("amount", effect.get("value", 0)))
            key = str(effect.get("attribute") or "health")
            state[key] = self.schema.clamp(key, float(state.get(key, self.schema.default(key, 100)) or 0) + amount)
            result.update({"applied": True, "attribute": key, "value": state[key]})
        elif effect_type in {"location_set", "move_to"}:
            location_id = effect.get("location_id") or target.get("location_id") or target.get("destination_id")
            if location_id:
                state["location_id"] = str(location_id)
                result.update({"applied": True, "location_id": state["location_id"]})
            else:
                result["error"] = "location_id is required"
        else:
            result["error"] = f"unsupported effect type: {effect_type}"
        agent["initial_state"] = state
        agent["needs"] = needs
        agent["inventory"] = inventory
        return result

    @staticmethod
    def _formula(formula: str, agent: dict[str, Any], target: dict[str, Any]) -> float:
        values = {
            "actor": {**agent.get("combat_stats", {}), **agent.get("initial_state", {})},
            "target": target,
            "weapon": target.get("weapon", {}) if isinstance(target, dict) else {},
        }

        def evaluate(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                return float(values.get(node.value.id, {}).get(node.attr, 0))
            if isinstance(node, ast.BinOp) and type(node.op) in {ast.Add, ast.Sub, ast.Mult, ast.Div}:
                left = evaluate(node.left)
                right = evaluate(node.right)
                return {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}[type(node.op)](left, right) if not (isinstance(node.op, ast.Div) and right == 0) else 0.0
            if isinstance(node, ast.UnaryOp) and type(node.op) in {ast.UAdd, ast.USub}:
                value = evaluate(node.operand)
                return value if isinstance(node.op, ast.UAdd) else -value
            raise ValueError("unsupported effect formula")

        try:
            return max(0.0, evaluate(ast.parse(str(formula), mode="eval")))
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError):
            return 0.0
