from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from ..intents.models import BehaviorIntent

if TYPE_CHECKING:  # pragma: no cover - imports are only for type checkers
    from ..platform.runtime import Platform


# Tools are model-facing requests. Collecting, crafting, travelling and
# fighting remain Platform actions executed by a selected behavior tree.
TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "observe_nearby": {
        "purpose": "Read visible facts and entities at the current location.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "What you need to know."}},
            "required": [],
            "additionalProperties": False,
        },
    },
    "inspect_entity": {
        "purpose": "Read permitted public details about a known or visible entity.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "question": {"type": "string", "description": "The detail you need."},
            },
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    },
    "listen": {
        "purpose": "Read recent public events that can be heard at the current location.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 8}},
            "required": [],
            "additionalProperties": False,
        },
    },
    "read_public_record": {
        "purpose": "Read a public document, location or faction record.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}, "question": {"type": "string"}},
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    },
    "query_known_fact": {
        "purpose": "Search facts already known by this agent.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 8}},
            "required": [],
            "additionalProperties": False,
        },
    },
    "recall_memory": {
        "purpose": "Recall the agent's own retained experiences and knowledge.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "preview_behavior": {
        "purpose": "Show an offered behavior's requirements, time, costs and outcomes.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "behavior_ref": {"type": "string"},
                "target": {"type": "object"},
            },
            "required": ["behavior_ref"],
            "additionalProperties": False,
        },
    },
    "request_behavior": {
        "purpose": "Ask Platform to start one offered behavior; Platform validates and executes it.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "behavior_ref": {"type": "string"},
                "target": {"type": "object"},
                "parameters": {"type": "object"},
                "reason": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["behavior_ref"],
            "additionalProperties": False,
        },
    },
    "record_experience": {
        "purpose": "Submit a short subjective summary of a completed experience.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "event_ref": {"type": "string"},
                "summary": {"type": "string", "maxLength": 500},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
    "update_belief": {
        "purpose": "Submit a confidence update for a known fact or belief.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "belief_id": {"type": "string"},
                "statement": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
            "required": ["confidence"],
            "additionalProperties": False,
        },
    },
    "update_goal": {
        "purpose": "Submit a change to the agent's goal or goal progress.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "statement": {"type": "string"},
                "status": {"type": "string"},
                "priority": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["statement"],
            "additionalProperties": False,
        },
    },
    "propose_behavior": {
        "purpose": "Propose a small behavior made only from registered actions for validation.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "behavior_id": {"type": "string"},
                "summary": {"type": "string"},
                "goal": {"type": "string"},
                "action_ids": {"type": "array", "items": {"type": "string"}},
                "entry_conditions": {"type": "array", "items": {"type": "string"}},
                "success_results": {"type": "array", "items": {"type": "string"}},
                "failure_results": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal", "action_ids"],
            "additionalProperties": False,
        },
    },
    "propose_entity": {
        "purpose": "Propose a new world entity for Platform validation and creation.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "entity_type": {"type": "string"},
                "name": {"type": "string"},
                "location_id": {"type": "string"},
                "state": {"type": "object"},
                "description": {"type": "string"},
            },
            "required": ["entity_type", "name"],
            "additionalProperties": False,
        },
    },
    "propose_world_change": {
        "purpose": "Propose a world event or rule change for Platform validation.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "change_type": {"type": "string"},
                "summary": {"type": "string"},
                "scope": {"type": "string"},
                "conditions": {"type": "array", "items": {"type": "string"}},
                "effects": {"type": "array", "items": {"type": "string"}},
                "property": {"type": "string"},
                "value": {},
                "rule": {"type": "object"},
                "relationship": {"type": "object"},
                "faction": {"type": "object"},
            },
            "required": ["change_type", "summary"],
            "additionalProperties": False,
        },
    },
    "validate_world_template": {
        "purpose": "Validate a declarative world template without creating an instance.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {"template": {"type": "object"}},
            "required": ["template"],
            "additionalProperties": False,
        },
    },
    "submit_world_template": {
        "purpose": "Submit a validated world template for a new SQLite instance.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"template": {"type": "object"}},
            "required": ["template"],
            "additionalProperties": False,
        },
    },
}


ROLE_TOOLS: dict[str, list[str]] = {
    "agent_decision": ["observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "recall_memory", "preview_behavior", "request_behavior"],
    "agent_reflection": ["recall_memory", "query_known_fact", "record_experience", "update_belief", "update_goal", "propose_behavior"],
    "god": ["observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "recall_memory", "preview_behavior", "propose_entity", "propose_world_change"],
    "world_builder": ["validate_world_template", "submit_world_template"],
}


def tools_for_role(role: str) -> list[dict[str, Any]]:
    """Return compact, human-readable descriptions used in AgentView."""

    return [{"name": name, **TOOL_DEFINITIONS[name], "input": TOOL_DEFINITIONS[name]["parameters"]} for name in ROLE_TOOLS.get(role, [])]


def tool_schemas_for_role(role: str) -> list[dict[str, Any]]:
    """Return OpenAI-compatible function schemas for providers that support them."""

    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["purpose"],
                "parameters": tool["parameters"],
            },
        }
        for tool in tools_for_role(role)
    ]


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> list[str]:
    """Validate the small function schemas before invoking a handler.

    This intentionally covers the JSON Schema subset used by the built-in
    tools.  Validation happens before Python dispatch so malformed model
    output becomes a retryable tool error rather than an exception or side
    effect.
    """

    definition = TOOL_DEFINITIONS.get(name)
    if definition is None:
        return ["unknown tool"]
    schema = definition["parameters"]
    errors: list[str] = []
    if not isinstance(arguments, dict):
        return ["arguments must be an object"]
    for required in schema.get("required", []):
        if required not in arguments:
            errors.append(f"missing required field: {required}")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        errors.extend(f"unknown field: {key}" for key in arguments if key not in properties)
    for key, value in arguments.items():
        if key not in properties:
            continue
        expected = properties[key].get("type")
        type_ok = {
            "string": isinstance(value, str),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
        }.get(expected, True)
        if not type_ok:
            errors.append(f"{key} must be {expected}")
            continue
        if isinstance(value, (int, float)) and "minimum" in properties[key] and value < properties[key]["minimum"]:
            errors.append(f"{key} is below the minimum")
        if isinstance(value, (int, float)) and "maximum" in properties[key] and value > properties[key]["maximum"]:
            errors.append(f"{key} is above the maximum")
        if isinstance(value, str) and "maxLength" in properties[key] and len(value) > properties[key]["maxLength"]:
            errors.append(f"{key} is too long")
    return errors


class AgentToolExecutor:
    """Execute role-allowed tools and route every mutation to Platform."""

    def __init__(self, platform: "Platform", *, role: str, actor_id: str):
        self.platform = platform
        self.role = role
        self.actor_id = actor_id
        self.allowed = set(ROLE_TOOLS.get(role, []))

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in self.allowed:
            return {"status": "rejected", "tool": name, "message": "This tool is not allowed for this role."}
        if name not in TOOL_DEFINITIONS:
            return {"status": "rejected", "tool": name, "message": "Unknown tool."}
        args = dict(arguments or {})
        validation_errors = validate_tool_arguments(name, args)
        if validation_errors:
            return {
                "status": "rejected",
                "tool": name,
                "failed_check": "tool_arguments",
                "retryable": True,
                "message": "; ".join(validation_errors),
            }
        try:
            result = getattr(self, f"_{name}")(**args)
        except (KeyError, TypeError, ValueError) as exc:
            return {"status": "rejected", "tool": name, "message": str(exc)}
        if not isinstance(result, dict):
            return {"status": "ok", "tool": name, "result": result}
        result.setdefault("tool", name)
        return result

    def _agent(self) -> dict[str, Any]:
        return self.platform.store.get_agent(self.actor_id)

    def _observe_nearby(self, question: str = "") -> dict[str, Any]:
        return self.platform.perception.observe_nearby(self.actor_id, question)

    def _inspect_entity(self, entity_id: str, question: str = "") -> dict[str, Any]:
        return self.platform.perception.inspect_entity(self.actor_id, entity_id, question, global_scope=self.role in {"god", "world_builder"})

    def _listen(self, question: str = "", limit: int = 5) -> dict[str, Any]:
        return self.platform.perception.listen(self.actor_id, question, limit)

    def _read_public_record(self, entity_id: str, question: str = "") -> dict[str, Any]:
        return self.platform.perception.read_public_record(self.actor_id, entity_id, question)

    def _query_known_fact(self, query: str = "", limit: int = 8) -> dict[str, Any]:
        return self.platform.perception.query_known_fact(self.actor_id, query, limit)

    def _recall_memory(self, question: str = "", limit: int = 8) -> dict[str, Any]:
        return self.platform.memory.recall(self.actor_id, question, limit)

    def _preview_behavior(self, behavior_ref: str, target: dict[str, Any] | None = None) -> dict[str, Any]:
        view = self.platform.view.build_decision_view(self.actor_id, role=self.role)
        for behavior in view.get("executable_behaviors", []):
            if behavior.get("ref") == behavior_ref:
                return {"status": "ok", "behavior": behavior, "target": target or {}}
        return {"status": "rejected", "message": "The behavior is not offered to this agent."}

    def _request_behavior(
        self,
        behavior_ref: str,
        target: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        reason: str = "",
        confidence: float = 0.0,
    ) -> dict[str, Any]:
        intent = BehaviorIntent(
            actor_id=self.actor_id,
            behavior_ref=behavior_ref,
            target=dict(target or {}),
            parameters=dict(parameters or {}),
            reason=reason,
            confidence=float(confidence),
            intent_id=str(uuid4()),
        )
        return self.platform.submit_behavior(intent)

    def _record_experience(self, summary: str, event_ref: str = "", confidence: float = 1.0) -> dict[str, Any]:
        return self.platform.submit_experience(self.actor_id, event_ref, summary, float(confidence))

    def _update_belief(self, confidence: float, belief_id: str = "", statement: str = "", reason: str = "") -> dict[str, Any]:
        return self.platform.submit_belief_update(self.actor_id, belief_id, statement, float(confidence), reason)

    def _update_goal(self, statement: str, goal_id: str = "ultimate_goal", status: str = "active", priority: float = 1.0) -> dict[str, Any]:
        return self.platform.submit_goal_update(self.actor_id, goal_id, statement, status, float(priority))

    def _propose_behavior(
        self,
        goal: str,
        action_ids: list[str],
        behavior_id: str = "",
        summary: str = "",
        entry_conditions: list[str] | None = None,
        success_results: list[str] | None = None,
        failure_results: list[str] | None = None,
    ) -> dict[str, Any]:
        proposal = {
            "behavior_id": behavior_id,
            "summary": summary or goal,
            "goal": goal,
            "action_ids": list(action_ids),
            "entry_conditions": list(entry_conditions or []),
            "success_results": list(success_results or []),
            "failure_results": list(failure_results or []),
        }
        return self.platform.submit_behavior_proposal(self.actor_id, proposal)

    def _propose_entity(
        self,
        entity_type: str,
        name: str,
        entity_id: str = "",
        location_id: str = "",
        state: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        proposal = {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "name": name,
            "location_id": location_id,
            "state": dict(state or {}),
            "description": description,
        }
        return self.platform.submit_entity_proposal(self.actor_id, proposal)

    def _propose_world_change(
        self,
        change_type: str,
        summary: str,
        scope: str = "world",
        conditions: list[str] | None = None,
        effects: list[str] | None = None,
        property: str = "",
        value: Any = None,
        rule: dict[str, Any] | None = None,
        relationship: dict[str, Any] | None = None,
        faction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal = {
            "change_type": change_type,
            "summary": summary,
            "scope": scope,
            "conditions": list(conditions or []),
            "effects": list(effects or []),
            "property": property,
            "value": value,
            "rule": dict(rule or {}),
            "relationship": dict(relationship or {}),
            "faction": dict(faction or {}),
        }
        return self.platform.submit_world_change(self.actor_id, proposal)

    def _validate_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        return self.platform.validate_world_template(template)

    def _submit_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        return self.platform.submit_world_template(self.actor_id, template)


def parse_tool_call(response: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Normalize JSON and OpenAI tool-call shapes into one call."""

    candidate: Any = response.get("tool_call")
    if candidate is None:
        candidate = response.get("function_call")
    if candidate is None and response.get("tool_calls"):
        candidate = response["tool_calls"][0]
    if candidate is None and response.get("type") == "tool_call":
        candidate = response
    if isinstance(candidate, list):
        candidate = candidate[0] if candidate else None
    if not isinstance(candidate, dict):
        return None
    function = candidate.get("function") if isinstance(candidate.get("function"), dict) else candidate
    name = function.get("name") or candidate.get("name")
    arguments = function.get("arguments", function.get("args", candidate.get("arguments", {})))
    if not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return name, arguments
