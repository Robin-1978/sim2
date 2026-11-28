from __future__ import annotations

import copy
import json
import re
from typing import Any
from uuid import uuid4

from ..bootstrap.service import world_template_schema
from ..world.definition_schema import propose_definition_schema
from ..world.schema_text import concise_schema, concise_text


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
        "purpose": "Send testimony to a visible participant; no state transfer or physical details.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "The visible participant name."},
                "topic": {"type": "string"},
                "message": {"type": "string", "maxLength": 240, "description": "At most two concise sentences grounded in visible evidence."},
            },
            "required": ["recipient", "topic", "message"],
            "additionalProperties": False,
        },
    },
    "create_trade_offer": {
        "purpose": "Offer item bundles; transfer only on acceptance.",
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
        "purpose": "Accept one open offer.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"offer": {"type": "string", "description": "The exact open offer choice from the current interaction list."}},
            "required": ["offer"],
            "additionalProperties": False,
        },
    },
    "counter_trade_offer": {
        "purpose": "Replace one open offer with a counteroffer.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"offer": {"type": "string", "description": "The exact open offer choice from the current interaction list."}, "give": {"type": "object"}, "want": {"type": "object"}},
            "required": ["offer", "give", "want"],
            "additionalProperties": False,
        },
    },
    "reject_trade_offer": {
        "purpose": "Reject one open offer.",
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
    "query_history": {
        "purpose": "Search world history by event type, actor, keyword or time.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "description": "Exact event type, e.g. research.completed."},
                "actor": {"type": "string", "description": "Visible character name."},
                "contains": {"type": "string", "description": "Optional word or phrase in the validated event details."},
                "since_minute": {"type": "integer", "minimum": 0, "description": "Optional inclusive start of world time."},
                "until_minute": {"type": "integer", "minimum": 0, "description": "Optional inclusive end of world time."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "query_knowledge_gaps": {
        "purpose": "List open or already answered research questions before proposing new research.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "resolved", "all"], "description": "active means still unanswered; resolved means already answered."},
                "contains": {"type": "string", "description": "Optional word or phrase from the question or source name."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "query_world_index": {
        "purpose": "Page the structural index by cell or keyword.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "cell": {"type": "string", "enum": ["place", "verb", "social", "rule", "resource"], "description": "Optional structural cell to list."},
                "query": {"type": "string", "description": "Optional word or phrase from a visible name, summary, effect or operation."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "offset": {"type": "integer", "minimum": 0, "maximum": 500},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "inspect_definition": {
        "purpose": "Read one Action, behavior or rule by visible name.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The exact visible Action, behavior tree or rule name."},
                "kind": {"type": "string", "enum": ["any", "action", "behavior", "rule"]},
            },
            "required": ["name"],
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
                "scope": {"type": "string", "enum": ["all", "short_term", "long_term"], "description": "Use short_term for current working context or long_term for durable lessons."},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "query_behavior_catalog": {
        "purpose": "Find registered behaviors by name or summary, including options not currently offered.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to match in a behavior name or summary; empty lists offered options."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Maximum matches; defaults to 8."},
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
                "target": {"type": "object", "description": "Optional target; use {name: exact visible participant or entity name}.", "properties": {"name": {"type": "string"}}, "additionalProperties": False},
            },
            "required": ["choice"],
            "additionalProperties": False,
        },
    },
    "request_behavior": {
        "purpose": "Start one exact visible behavior.",
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
                "event": {"type": "string", "description": "Cite the visible event:<id> from the outcome review when available."},
                "summary": {"type": "string", "maxLength": 500},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "claim_status": {"type": "string", "enum": ["observed", "confirmed", "reported", "promised", "inferred", "disputed", "false", "failed"], "description": "Evidence provenance. Use reported or promised for unverified testimony, inferred for a conclusion, and disputed or false only with corroborating event evidence."},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
    "update_capability": {
        "purpose": "Propose a bounded capability gain with evidence.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "description": "A configured capability name."},
                "delta": {"type": "number", "minimum": 0, "maximum": 0.2},
                "evidence": {"type": "string", "description": "A concise explanation grounded in recent completed outcomes."},
            },
            "required": ["capability", "delta", "evidence"],
            "additionalProperties": False,
        },
    },
    "record_world_progress": {
        "purpose": "Assign one configured progress key.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "progress_key": {"type": "string", "description": "A configured world progress name."},
                "evidence": {"type": "string", "description": "A concise public result that supports this progress."},
            },
            "required": ["progress_key", "evidence"],
            "additionalProperties": False,
        },
    },
    "propose_institution": {
        "purpose": "Propose a new social institution without activating it or changing world state.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "A short visible institution name."},
                "purpose": {"type": "string", "description": "What public problem the institution is meant to solve."},
                "scope": {"type": "string", "description": "Who or what the institution covers."},
                "evidence": {"type": "string", "description": "The observed problem or event motivating the proposal."},
                "evidence_sources": {"type": "array", "items": {"type": "object"}, "description": "Optional when this is an event-triggered task; Platform supplies the task's verified evidence contract. If supplied, use persisted event selectors only."},
                "minimum_evidence": {"type": "integer", "minimum": 1, "description": "Optional evidence threshold; the triggering task threshold is used when omitted."},
                "ledger": {"type": "object", "description": "Optional future ledger. Use {period:'week', selectors:[{event_types:[...], behavior_ids:[...], action_ids:[...], category:'civic_work', points:1}]}; it is inert until activation."},
                "policy": {"type": "object", "description": "Optional allocation policy. If allocating a shared pool, use {allocation:{minimum_per_subject:1, contribution_share:0.5, category:'civic_work'}}; it changes nothing until a validated settlement behavior runs."},
            },
            "required": ["name", "purpose"],
            "additionalProperties": False,
        },
    },
    "approve_institution": {
        "purpose": "Activate one pending institution after the world-level review accepts it.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "institution": {"type": "string", "description": "The exact visible pending institution name."},
                "reason": {"type": "string", "description": "Short evidence-backed approval reason."},
            },
            "required": ["institution"],
            "additionalProperties": False,
        },
    },
    "revise_institution": {
        "purpose": "Revise one active institution's policy or ledger selectors.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "institution": {"type": "string", "description": "The exact visible active institution name."},
                "policy": {"type": "object", "description": "Replacement allocation policy; omit to keep the current policy."},
                "ledger": {"type": "object", "description": "Replacement ledger selectors; omit to keep the current selectors."},
                "reason": {"type": "string", "description": "The recorded result that justifies the change, 12 to 500 characters."},
            },
            "required": ["institution", "reason"],
            "additionalProperties": False,
        },
    },
    "update_belief": {
        "purpose": "Revise an existing belief by quoting it in previous_statement, or add a belief on a new subject.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "belief": {"type": "string", "description": "The belief statement to create or update."},
                "statement": {"type": "string"},
                "previous_statement": {"type": "string", "description": "Copy the existing belief verbatim for any revision. With confidence alone, keep its wording."},
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
                "entry_conditions": {"type": "array", "items": {"type": "string"}, "description": "Durable predicates only, e.g. actor.alive; [] if none."},
                "success_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable result labels, not sentences that claim unperformed effects."},
                "failure_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable failure labels, not free-form conditions."},
            },
            "required": ["goal", "actions"],
            "additionalProperties": False,
        },
    },
    "propose_entity": {
        "purpose": "Propose a new evidence-backed world entity for Platform validation. God may create people, places, factions, objects, or empty resource nodes needed by the evolving story.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "description": "agent, or configured type: location, faction, object, material, resource."},
                "name": {"type": "string"},
                "location": {"type": "string", "description": "A visible location name."},
                "state": {"type": "object", "description": "Initial state. A location must include non-empty routes naming existing locations and may include positive route_distances; Platform creates reciprocal route links atomically. For an agent, include role, ultimate_goal, initial_bt_ids, capabilities, needs, inventory, and initial_state with a visible location and status. Platform initializes configured defaults and validates every reference."},
                "description": {"type": "string"},
            },
            "required": ["entity_type", "name"],
            "additionalProperties": False,
        },
    },
    "propose_definition": {
        "purpose": "Propose a declarative definition.",
        "kind": "intent",
        "parameters": propose_definition_schema(),
    },
    "propose_world_change": {
        "purpose": "Propose an evidence-backed rule, relationship, route connection, existing-faction change, or narrative event. Behavior outcomes own resources and personal/social value changes.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "change_type": {"type": "string", "enum": ["world_property", "rule", "relationship", "route", "faction", "event"], "description": "Choose one change type."},
                "summary": {"type": "string"},
                "scope": {"type": "string"},
                "conditions": {"type": "array", "items": {"type": "string"}},
                "effects": {"type": "array", "items": {"type": "string"}, "description": "Observable effects; a signal only records or wakes, it does not block or hold."},
                "expected_effects": {"type": "array", "items": {"type": "string"}, "description": "Short effects to compare with the next outcome."},
                "evaluation_window_minutes": {"type": "integer", "minimum": 1, "maximum": 43200, "description": "Minutes before God reviews outcomes."},
                "property": {"type": "string"},
                "value": {},
                "rule": {
                    "type": "object",
                    "description": "Declarative policy: signal, deny or behavior_constraint.",
                    "properties": {
                        "id": {"type": "string", "description": "Short unique rule name."},
                        "label": {"type": "string", "description": "Visible policy name."},
                        "description": {"type": "string", "description": "One sentence."},
                        "condition": {"type": "string", "description": "Machine condition, e.g. world[\"water quality\"] < 0.5 or event.recent.facility.inspected == true."},
                        "effect": {"description": "{type: signal|deny|behavior_constraint, ...}. Only a typed effect enforces; prose does not."},
                        "wake": {"type": "array", "items": {"type": "string"}, "description": "Optional roles to wake: god or story."},
                        "behavior_names": {"type": "array", "items": {"type": "string"}, "description": "Visible behavior names; behavior_constraint blocks every listed behavior."},
                        "action_names": {"type": "array", "items": {"type": "string"}, "description": "Visible Action names blocked by a deny effect."},
                    },
                    "required": ["id", "condition", "effect"],
                    "additionalProperties": False,
                },
                "route": {
                    "type": "object",
                    "description": "Connect two visible locations with a weighted edge; travel still takes time.",
                    "properties": {
                        "from": {"type": "string", "description": "Visible origin location."},
                        "to": {"type": "string", "description": "Visible destination location."},
                        "distance": {"type": "number", "minimum": 0.1, "description": "Positive distance units."},
                    },
                    "required": ["from", "to", "distance"],
                    "additionalProperties": False,
                },
                "relationship": {
                    "type": "object",
                    "description": "Record a relationship between two visible participants; never use internal IDs.",
                    "properties": {
                        "from": {"type": "string", "description": "First visible participant."},
                        "to": {"type": "string", "description": "Second visible participant."},
                        "type": {"type": "string", "description": "Relationship kind, e.g. trust."},
                        "description": {"type": "string"},
                        "delta": {"type": "number"},
                    },
                    "required": ["from", "to", "type"],
                    "additionalProperties": False,
                },
                "faction": {"type": "object", "description": "Use visible member or location names."},
            },
            "required": ["change_type", "summary"],
            "additionalProperties": False,
        },
    },
    "propose_story_beat": {
        "purpose": "Activate or propose one grounded story beat.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "beat": {"type": "string", "description": "Copy one visible configured beat exactly, or give a short new label when no configured beat is listed."},
                "audience": {"type": "array", "items": {"type": "string"}, "description": "For an emergent beat, provide at least one exact character name from audience_candidates. A configured beat may use its configured audience."},
                "summary": {"type": "string", "description": "Optional short summary for an emergent beat; when omitted, fact is used as the summary."},
                "fact": {"type": "string", "description": "A short evidence-grounded fact to deliver to the audience and use as the emergent summary when summary is omitted."},
                "goal_hint": {"type": "string", "description": "A short next objective for the audience."},
                "research_source": {"type": "string", "description": "Optional exact visible research source to assign to the audience. The existing generic research behavior is used."},
                "unlock_choices": {"type": "array", "items": {"type": "string"}, "description": "Visible registered behavior choices that carry this beat forward. Use at least one exact executable follow-up when the audience has a next action; do not invent a new BT for a new research object."},
                "reason": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "validate_world_template": {
        "purpose": "Validate a declarative world template without creating an instance.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {"template": {**world_template_schema(), "description": "Complete declarative template. Use human-readable labels; Platform validates every reference."}},
            "required": ["template"],
            "additionalProperties": False,
        },
    },
    "submit_world_template": {
        "purpose": "Submit a validated world template for a new SQLite instance.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"template": {**world_template_schema(), "description": "The exact template that already passed validate_world_template."}},
            "required": ["template"],
            "additionalProperties": False,
        },
    },
    "wait": {
        "purpose": "End this bounded session without changing the world.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string", "maxLength": 240}},
            "required": [],
            "additionalProperties": False,
        },
    },
}


ROLE_TOOLS: dict[str, list[str]] = {
    "agent_decision": ["observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "recall_memory", "query_behavior_catalog", "preview_behavior", "send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "request_behavior", "wait"],
    "agent_reflection": ["recall_memory", "query_known_fact", "record_experience", "update_capability", "update_belief", "update_goal", "propose_behavior", "wait"],
    # Story receives the validated source and recent history in its world view;
    # query_history is the bounded read path for an older causal thread.
    "story": ["query_history", "query_knowledge_gaps", "record_world_progress", "propose_story_beat", "wait"],
    "god": ["observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "query_history", "query_knowledge_gaps", "query_world_index", "inspect_definition", "recall_memory", "preview_behavior", "record_world_progress", "propose_entity", "propose_definition", "propose_world_change", "propose_institution", "approve_institution", "revise_institution", "wait"],
    "world_builder": ["validate_world_template", "submit_world_template", "wait"],
}


def tools_for_role(role: str, allowed_names: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
    """Return compact, human-readable descriptions used in AgentView.

    A bounded interaction session only needs the tools that can answer that
    interaction.  Filtering here keeps the function schema and the prose
    view aligned, instead of asking the model to ignore unrelated tools.
    """

    names = ROLE_TOOLS.get(role, [])
    if allowed_names is not None:
        allowed = set(allowed_names)
        names = [name for name in names if name in allowed]
    return [_tool_for_role(role, name) for name in names]


def _tool_for_role(role: str, name: str) -> dict[str, Any]:
    """Return a role-scoped tool view instead of exposing invalid choices."""

    definition = copy.deepcopy(TOOL_DEFINITIONS[name])
    if name == "propose_entity" and role == "god":
        definition["purpose"] = "Propose a person, place, faction, object, or empty resource node needed by the verified storyline. Do not create stocked resources as a reward."
        definition["parameters"]["properties"]["state"]["description"] = "Initial structural state. A location must include non-empty routes naming existing locations and may include positive route_distances; Platform creates reciprocal links atomically. For an agent, include role, ultimate_goal, initial_bt_ids, capabilities, needs, inventory, and initial_state with a visible location and status. A positive resource quantity must come from a validated behavior result."
    if name == "propose_world_change" and role == "god":
        definition["purpose"] = "Propose a rule, relationship, route, faction change or event."
        change_type = definition["parameters"]["properties"]["change_type"]
        change_type["enum"] = ["rule", "relationship", "route", "faction", "event"]
        change_type["description"] = "One of rule, relationship, route, faction or event."
        definition["parameters"]["properties"].pop("property", None)
        definition["parameters"]["properties"].pop("value", None)
    definition["purpose"] = concise_text(definition["purpose"])
    definition["parameters"] = concise_schema(definition["parameters"])
    if name == "propose_entity":
        definition["parameters"]["properties"]["state"]["description"] = (
            "Location: routes to existing places, route_distances, public: true for the player map, safe. "
            "Agent: role, ultimate_goal, initial_bt_ids, needs, inventory, initial_state.location_id."
        )
    if name == "propose_story_beat":
        props = definition["parameters"]["properties"]
        props["unlock_choices"]["description"] = "Exact executable follow-ups; do not invent a behavior."
        props["audience"]["description"] = "Exact audience names from audience_candidates."
        props["fact"]["description"] = "Verified fact for the audience."
        props["research_source"]["description"] = "Optional exact research source."
    return {"name": name, **definition, "input": definition["parameters"]}


def tool_schemas_for_role(role: str, allowed_names: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
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
        for tool in tools_for_role(role, allowed_names)
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
