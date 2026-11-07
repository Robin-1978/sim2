from __future__ import annotations

import math
from typing import Any


def sigmoid(value: float) -> float:
    value = max(-60.0, min(60.0, value))
    return 1.0 / (1.0 + math.exp(-value))


class DriveCalculator:
    """Calculate explainable internal and external motivation components.

    Needs are not a hard-coded Maslow ladder.  Each configured need has a
    nonlinear deficit curve and a critical sigmoid.  External, relationship,
    novelty and inhibition signals are kept as separate components before a
    bounded urgency is produced, so an agent can distinguish ``need pressure``
    from ``social pressure`` in its context.
    """

    def __init__(self, rules: dict[str, Any] | None = None):
        self.rules = rules or {}
        self.need_defs = {item["id"]: item for item in self.rules.get("needs", []) if isinstance(item, dict) and item.get("id")}
        self.gates = self.rules.get("need_gates", []) or []

    def calculate(self, agent: dict[str, Any]) -> list[dict[str, Any]]:
        needs = agent.get("needs", {}) or {}
        external = agent.get("external_pressure", {}) or {}
        relations = agent.get("relation_pressure", {}) or {}
        novelty = agent.get("novelty_bonus", {}) or {}
        inhibition = agent.get("inhibition", {}) or {}
        deficits: dict[str, float] = {}
        rows: dict[str, dict[str, Any]] = {}
        for need_id, definition in self.need_defs.items():
            current = float(needs.get(need_id, definition.get("target", 100)))
            target = float(definition.get("target", 100))
            minimum = float(definition.get("minimum", 0))
            denominator = max(1.0, target - minimum)
            deficit = max(0.0, min(1.0, (target - current) / denominator))
            gamma = max(0.01, float(definition.get("gamma", 1.0)))
            gap_pressure = float(definition.get("weight", 1.0)) * (deficit ** gamma)
            threshold = float(definition.get("critical_threshold", 0.8))
            sharpness = float(definition.get("critical_sharpness", 8.0))
            critical_pressure = float(definition.get("critical_gain", 1.0)) * sigmoid(sharpness * (deficit - threshold))
            internal = max(0.0, float(definition.get("bias", 0.0))) + gap_pressure + critical_pressure
            external_value = float(external.get(need_id, 0.0))
            relation_value = float(relations.get(need_id, 0.0))
            novelty_value = float(novelty.get(need_id, 0.0))
            inhibition_value = max(0.0, float(inhibition.get(need_id, 0.0)))
            net_pressure = max(0.0, internal + external_value + relation_value + novelty_value - inhibition_value)
            # 0 pressure means 0 urgency; pressure saturates smoothly at 1.
            urgency = 1.0 - math.exp(-net_pressure)
            reasons: list[str] = []
            if deficit > 0:
                reasons.append(f"{need_id} satisfaction is {current:g}/{target:g}")
            if critical_pressure > float(definition.get("critical_gain", 1.0)) * 0.5:
                reasons.append("near its critical threshold")
            if external_value:
                reasons.append("external pressure is active")
            if relation_value:
                reasons.append("relationship pressure is active")
            if novelty_value:
                reasons.append("novelty is rewarding")
            if inhibition_value:
                reasons.append("inhibition is reducing action pressure")
            deficits[need_id] = deficit
            rows[need_id] = {
                "id": need_id,
                "urgency": round(max(0.0, min(1.0, urgency)), 4),
                "deficit": round(deficit, 4),
                "layer": definition.get("layer", "configured_need"),
                "components": {
                    "internal": round(internal, 4),
                    "external": round(external_value, 4),
                    "social": round(relation_value, 4),
                    "novelty": round(novelty_value, 4),
                    "inhibition": round(inhibition_value, 4),
                    "net_pressure": round(net_pressure, 4),
                },
                "reason": "; ".join(reasons) or "no immediate pressure",
            }
        for gate in self.gates:
            source = gate.get("from")
            target = gate.get("to")
            if source not in deficits or target not in rows:
                continue
            threshold = float(gate.get("threshold", 0.8))
            if deficits[source] >= threshold:
                multiplier = float(gate.get("multiplier", 1.0))
                rows[target]["urgency"] = round(max(0.0, min(1.0, rows[target]["urgency"] * multiplier)), 4)
                rows[target]["reason"] += f"; gated by critical {source}"
                rows[target]["components"]["gate_multiplier"] = multiplier
        return sorted(rows.values(), key=lambda row: row["urgency"], reverse=True)
