from __future__ import annotations

import math
from typing import Any


def sigmoid(value: float) -> float:
    value = max(-60.0, min(60.0, value))
    return 1.0 / (1.0 + math.exp(-value))


class DriveCalculator:
    """Deterministic internal/external drive model from the design contract."""

    def __init__(self, rules: dict[str, Any] | None = None):
        self.rules = rules or {}
        self.need_defs = {x["id"]: x for x in self.rules.get("needs", [])}
        self.gates = self.rules.get("need_gates", [])

    def calculate(self, agent: dict[str, Any]) -> list[dict[str, Any]]:
        needs = agent.get("needs", {})
        external = agent.get("external_pressure", {})
        relations = agent.get("relation_pressure", {})
        novelty = agent.get("novelty_bonus", {})
        inhibition = agent.get("inhibition", {})
        values: dict[str, float] = {}
        rows: dict[str, dict[str, Any]] = {}
        for need_id, definition in self.need_defs.items():
            value = float(needs.get(need_id, definition.get("target", 100)))
            target = float(definition.get("target", 100))
            minimum = float(definition.get("minimum", 0))
            denominator = max(1.0, target - minimum)
            deficit = max(0.0, min(1.0, (target - value) / denominator))
            gamma = float(definition.get("gamma", 1.0))
            gap = deficit ** gamma
            threshold = float(definition.get("critical_threshold", 0.8))
            critical = sigmoid(8.0 * (deficit - threshold))
            raw = (
                float(definition.get("bias", 0.0))
                + float(definition.get("weight", 1.0)) * gap
                + float(definition.get("critical_gain", 1.0)) * critical
                + float(external.get(need_id, 0.0))
                + float(relations.get(need_id, 0.0))
                + float(novelty.get(need_id, 0.0))
                - float(inhibition.get(need_id, 0.0))
            )
            urgency = sigmoid(raw)
            values[need_id] = deficit
            rows[need_id] = {"id": need_id, "urgency": urgency, "deficit": deficit, "reason": []}
            if deficit > 0:
                rows[need_id]["reason"].append(f"{need_id} satisfaction is {value:g}/{target:g}")
            if critical > 0.5:
                rows[need_id]["reason"].append("near its critical threshold")
            if external.get(need_id):
                rows[need_id]["reason"].append("external pressure is active")
        for gate in self.gates:
            source = gate.get("from")
            target = gate.get("to")
            if source not in values or target not in rows:
                continue
            threshold = float(gate.get("threshold", 0.8))
            if values[source] >= threshold:
                multiplier = float(gate.get("multiplier", 1.0))
                rows[target]["urgency"] *= multiplier
                rows[target]["reason"].append(f"gated by critical {source}")
        result = []
        for row in rows.values():
            row["urgency"] = round(max(0.0, min(1.0, row["urgency"])), 4)
            row["reason"] = "; ".join(row["reason"]) or "no immediate pressure"
            result.append(row)
        return sorted(result, key=lambda x: x["urgency"], reverse=True)
