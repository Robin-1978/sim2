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
                "entity": {"type": "string", "description": "The visible or known entity name."},
                "question": {"type": "string", "description": "The detail you need."},
            },
            "required": ["entity"],
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
    "send_message": {
        "purpose": "Send a short message to a visible participant at your location; the Platform records testimony for the recipient.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "The visible participant name."},
                "topic": {"type": "string"},
                "message": {"type": "string", "maxLength": 1000},
            },
            "required": ["recipient", "topic", "message"],
            "additionalProperties": False,
        },
    },
    "create_trade_offer": {
        "purpose": "Offer an item bundle for another participant's item bundle; no inventory changes until acceptance.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "The visible participant name."},
                "give": {"type": "object", "description": "item to positive quantity"},
                "want": {"type": "object", "description": "item to positive quantity"},
            },
            "required": ["recipient", "give", "want"],
            "additionalProperties": False,
        },
    },
    "accept_trade_offer": {
        "purpose": "Accept one open trade offer; Platform atomically transfers both item bundles.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"offer": {"type": "string", "description": "The exact open offer choice from the current interaction list."}},
            "required": ["offer"],
            "additionalProperties": False,
        },
    },
    "counter_trade_offer": {
        "purpose": "Replace an open offer with a counteroffer; no inventory changes until a later acceptance.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"offer": {"type": "string", "description": "The exact open offer choice from the current interaction list."}, "give": {"type": "object"}, "want": {"type": "object"}},
            "required": ["offer", "give", "want"],
            "additionalProperties": False,
        },
    },
    "reject_trade_offer": {
        "purpose": "Reject one open trade offer without changing inventory.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"offer": {"type": "string", "description": "The exact open offer choice from the current interaction list."}},
            "required": ["offer"],
            "additionalProperties": False,
        },
    },
    "read_public_record": {
        "purpose": "Read a public document, location or faction record.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {"record": {"type": "string", "description": "The visible public record name."}, "question": {"type": "string"}},
            "required": ["record"],
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
                "choice": {"type": "string", "description": "The exact visible behavior name from the current executable list."},
                "target": {"type": "object", "description": "Optional target details using visible names, never database identifiers."},
            },
            "required": ["choice"],
            "additionalProperties": False,
        },
    },
    "request_behavior": {
        "purpose": "Ask Platform to start one offered behavior; Platform validates and executes it.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "choice": {"type": "string", "description": "The exact visible behavior name from the current executable list."},
                "target": {"type": "object", "description": "Optional target details using visible names, never database identifiers."},
                "parameters": {"type": "object", "description": "Only configured user-facing parameters; use visible names."},
                "reason": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["choice"],
            "additionalProperties": False,
        },
    },
    "record_experience": {
        "purpose": "Submit a short subjective summary of a completed experience.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "event": {"type": "string", "description": "A short visible description of the experience being reviewed."},
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
                "belief": {"type": "string", "description": "The belief statement to create or update."},
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
                "goal": {"type": "string", "description": "The goal statement to create or update."},
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
                "name": {"type": "string", "description": "A short human-readable name for the proposed behavior."},
                "summary": {"type": "string"},
                "goal": {"type": "string"},
                "actions": {"type": "array", "items": {"type": "string"}, "description": "Visible action names in execution order."},
                "entry_conditions": {"type": "array", "items": {"type": "string"}},
                "success_results": {"type": "array", "items": {"type": "string"}},
                "failure_results": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["goal", "actions"],
            "additionalProperties": False,
        },
    },
    "propose_entity": {
        "purpose": "Propose a new world entity for Platform validation and creation.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string"},
                "name": {"type": "string"},
                "location": {"type": "string", "description": "A visible location name."},
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
                "change_type": {"type": "string", "enum": ["world_property", "rule", "relationship", "faction", "event"], "description": "Choose exactly one: world_property, rule, relationship, faction, or event."},
                "summary": {"type": "string"},
                "scope": {"type": "string"},
                "conditions": {"type": "array", "items": {"type": "string"}},
                "effects": {"type": "array", "items": {"type": "string"}},
                "expected_effects": {"type": "array", "items": {"type": "string"}, "description": "Short observable effects to compare with the next world outcome."},
                "property": {"type": "string"},
                "value": {},
                "rule": {"type": "object", "description": "Use visible names and configured natural-language conditions."},
                "relationship": {"type": "object", "description": "Use visible participant names, never database identifiers."},
                "faction": {"type": "object", "description": "Use visible member or location names, never database identifiers."},
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
            "properties": {"template": {"type": "object", "description": "Use human-readable names; Platform assigns internal identifiers."}},
            "required": ["template"],
            "additionalProperties": False,
        },
    },
    "submit_world_template": {
        "purpose": "Submit a validated world template for a new SQLite instance.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"template": {"type": "object", "description": "Use human-readable names; Platform assigns internal identifiers."}},
            "required": ["template"],
            "additionalProperties": False,
        },
    },
}


ROLE_TOOLS: dict[str, list[str]] = {
    "agent_decision": ["observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "recall_memory", "preview_behavior", "send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "request_behavior"],
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
        if "enum" in properties[key] and value not in properties[key]["enum"]:
            errors.append(f"{key} must be one of: {', '.join(str(item) for item in properties[key]['enum'])}")
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
        # Compatibility for older callers.  These aliases are intentionally
        # absent from the model-facing JSON Schemas.
        legacy_aliases = {
            "inspect_entity": {"entity_id": "entity"},
            "read_public_record": {"entity_id": "record"},
            "send_message": {"target_id": "recipient"},
            "create_trade_offer": {"recipient_id": "recipient"},
            "accept_trade_offer": {"offer_id": "offer"},
            "counter_trade_offer": {"offer_id": "offer"},
            "reject_trade_offer": {"offer_id": "offer"},
            "record_experience": {"event_ref": "event"},
            "update_belief": {"belief_id": "belief"},
            "update_goal": {"goal_id": "goal"},
            "propose_behavior": {"behavior_id": "name", "action_ids": "actions"},
            "propose_entity": {"entity_id": "name", "location_id": "location"},
        }
        for old, current in legacy_aliases.get(name, {}).items():
            if old in args:
                if current not in args:
                    args[current] = args[old]
                args.pop(old)
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
        return self._publicize(result)

    @classmethod
    def _publicize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._publicize(item)
                for key, item in value.items()
                if key not in {"id", "ref", "behavior_ref", "intent_id", "event_id"}
                and not key.endswith("_id")
            }
        if isinstance(value, list):
            return [cls._publicize(item) for item in value]
        return value

    def _agent(self) -> dict[str, Any]:
        return self.platform.store.get_agent(self.actor_id)

    def _observe_nearby(self, question: str = "") -> dict[str, Any]:
        return self.platform.perception.observe_nearby(self.actor_id, question)

    def _resolve_entity(self, value: str) -> tuple[str | None, dict[str, Any] | None]:
        resolved = self.platform.targets.canonical_id(value)
        if resolved:
            return resolved, None
        return None, {"status": "rejected", "failed_check": "entity_name", "retryable": True, "message": "Choose an exact visible or known entity name."}

    def _inspect_entity(self, entity: str, question: str = "") -> dict[str, Any]:
        entity_id, error = self._resolve_entity(entity)
        return error or self.platform.perception.inspect_entity(self.actor_id, str(entity_id), question, global_scope=self.role in {"god", "world_builder"})

    def _listen(self, question: str = "", limit: int = 5) -> dict[str, Any]:
        return self.platform.perception.listen(self.actor_id, question, limit)

    def _send_message(self, recipient: str, topic: str, message: str) -> dict[str, Any]:
        return self.platform.submit_dialogue(self.actor_id, recipient, topic, message)

    def _create_trade_offer(self, recipient: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        result = self.platform.create_trade_offer(self.actor_id, recipient, give, want)
        return self._trade_result_with_choice(result)

    def _resolve_offer(self, offer: str) -> tuple[str | None, dict[str, Any] | None]:
        resolved, error = self.platform.view.resolve_trade_offer_choice(self.actor_id, offer)
        return (str(resolved["offer_id"]), None) if resolved else (None, error)

    def _accept_trade_offer(self, offer: str) -> dict[str, Any]:
        offer_id, error = self._resolve_offer(offer)
        return error or self.platform.accept_trade_offer(self.actor_id, str(offer_id))

    def _counter_trade_offer(self, offer: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        offer_id, error = self._resolve_offer(offer)
        if error:
            return error
        return self._trade_result_with_choice(self.platform.counter_trade_offer(self.actor_id, str(offer_id), give, want))

    def _reject_trade_offer(self, offer: str) -> dict[str, Any]:
        offer_id, error = self._resolve_offer(offer)
        return error or self.platform.reject_trade_offer(self.actor_id, str(offer_id))

    def _trade_result_with_choice(self, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict) or not result.get("offer_id"):
            return result
        offer_view = next((item for item in self.platform.view._trade_offer_view(self.actor_id) if item.get("offer_id") == result.get("offer_id")), None)
        public = dict(result)
        if offer_view:
            public["offer"] = offer_view.get("choice")
        public.pop("offer_id", None)
        return public

    def _read_public_record(self, record: str, question: str = "") -> dict[str, Any]:
        entity_id, error = self._resolve_entity(record)
        return error or self.platform.perception.read_public_record(self.actor_id, str(entity_id), question, global_scope=self.role == "god")

    def _query_known_fact(self, query: str = "", limit: int = 8) -> dict[str, Any]:
        return self.platform.perception.query_known_fact(self.actor_id, query, limit)

    def _recall_memory(self, question: str = "", limit: int = 8) -> dict[str, Any]:
        return self.platform.memory.recall(self.actor_id, question, limit)

    def _preview_behavior(self, choice: str, target: dict[str, Any] | None = None) -> dict[str, Any]:
        behavior, error = self.platform.view.resolve_behavior_choice(self.actor_id, choice)
        if error:
            return error
        # Do not echo the internal reference in a model-facing tool result.
        public = {key: value for key, value in (behavior or {}).items() if key != "ref"}
        return {"status": "ok", "behavior": public, "target": target or {}}

    def _request_behavior(
        self,
        choice: str,
        target: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        reason: str = "",
        confidence: float = 0.0,
    ) -> dict[str, Any]:
        behavior, error = self.platform.view.resolve_behavior_choice(self.actor_id, choice)
        if error:
            return error
        intent = BehaviorIntent(
            actor_id=self.actor_id,
            behavior_ref=str((behavior or {}).get("ref")),
            target=dict(target or {}),
            parameters=dict(parameters or {}),
            reason=reason,
            confidence=float(confidence),
            intent_id=str(uuid4()),
        )
        result = self.platform.submit_behavior(intent)
        if isinstance(result, dict) and result.get("behavior_ref"):
            # Keep canonical refs in Platform/audit storage, but return the
            # visible choice to the model so it never has to learn an ID.
            result = dict(result)
            result["choice"] = (behavior or {}).get("choice") or (behavior or {}).get("name")
            result.pop("behavior_ref", None)
        return result

    def _record_experience(self, summary: str, event: str = "", confidence: float = 1.0) -> dict[str, Any]:
        return self.platform.submit_experience(self.actor_id, event, summary, float(confidence))

    def _update_belief(self, confidence: float, belief: str = "", statement: str = "", reason: str = "") -> dict[str, Any]:
        return self.platform.submit_belief_update(self.actor_id, "", statement or belief, float(confidence), reason)

    def _update_goal(self, statement: str, goal: str = "", status: str = "active", priority: float = 1.0) -> dict[str, Any]:
        return self.platform.submit_goal_update(self.actor_id, "ultimate_goal", statement or goal, status, float(priority))

    def _propose_behavior(
        self,
        goal: str,
        actions: list[str],
        name: str = "",
        summary: str = "",
        entry_conditions: list[str] | None = None,
        success_results: list[str] | None = None,
        failure_results: list[str] | None = None,
    ) -> dict[str, Any]:
        action_defs = self.platform.store.get_definitions("action_def")
        resolved_actions: list[str] = []
        unknown_actions: list[str] = []
        for action in actions:
            key = "".join(char for char in str(action).casefold() if char.isalnum())
            matches = [item["id"] for item in action_defs if key in {"".join(char for char in str(item.get("id", "")).casefold() if char.isalnum()), "".join(char for char in str(item.get("description", "")).casefold() if char.isalnum())}]
            if len(matches) == 1:
                resolved_actions.append(matches[0])
            else:
                unknown_actions.append(str(action))
        if unknown_actions:
            return {
                "status": "rejected",
                "failed_check": "action_name",
                "retryable": True,
                "message": "Use exact names from the registered action list.",
                "unknown_actions": unknown_actions,
                "available_actions": [str(item.get("id", "action")).replace("_", " ").title() for item in action_defs],
            }
        proposal = {
            "behavior_id": "",
            "agent_label": name,
            "summary": summary or goal,
            "goal": goal,
            "action_ids": resolved_actions,
            "entry_conditions": list(entry_conditions or []),
            "success_results": list(success_results or []),
            "failure_results": list(failure_results or []),
        }
        return self.platform.submit_behavior_proposal(self.actor_id, proposal)

    def _propose_entity(
        self,
        entity_type: str,
        name: str,
        location: str = "",
        state: dict[str, Any] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        location_id = self.platform.targets.canonical_id(location) if location else ""
        if location and not location_id:
            return {"status": "rejected", "failed_check": "location_name", "retryable": True, "message": "Choose an exact visible location name."}
        proposal = {
            "entity_id": "",
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
        expected_effects: list[str] | None = None,
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
            "expected_effects": list(expected_effects or effects or []),
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
