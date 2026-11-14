from __future__ import annotations

import copy
import json
import re
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from ..intents.models import BehaviorIntent
from ..bootstrap.service import world_template_schema

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
        "purpose": "Send words to a visible participant; this records testimony only, never transfers state, and must not add physical details absent from visible evidence.",
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
    "query_history": {
        "purpose": "Search validated world history by event type, participant, keyword or simulation time.",
        "kind": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "description": "Optional exact event type such as research.completed or institution.allocation_completed."},
                "actor": {"type": "string", "description": "Optional visible character name; omit for every participant."},
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
        "purpose": "Ask Platform to start one offered behavior; set choice to the exact visible behavior name, never to the tool name request_behavior.",
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
                "claim_status": {"type": "string", "enum": ["observed", "confirmed", "reported", "promised", "inferred", "disputed", "false", "failed"], "description": "Evidence provenance. Use reported or promised for unverified testimony, inferred for a conclusion, and disputed or false only with corroborating event evidence."},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
    "update_capability": {
        "purpose": "Propose a bounded personal capability improvement backed by completed outcomes.",
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
        "purpose": "Assign the triggering result to one matching public progress category; Platform derives the configured contribution and wakes milestone consumers at the threshold.",
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
        "purpose": "Revise the allocation policy or the ledger selectors of one active institution after its recorded results show the current terms no longer fit.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "institution": {"type": "string", "description": "The exact visible active institution name."},
                "policy": {"type": "object", "description": "Replacement allocation policy, for example {allocation:{minimum_per_subject:0.5, contribution_share:0.5, category:'civic_work'}}. Omit to keep the current policy."},
                "ledger": {"type": "object", "description": "Replacement ledger selectors, for example {selectors:[{event_types:[...], behavior_ids:[...], category:'civic_work', points:1}]}. Every selector must name event_types, behavior_ids or action_ids; the ledger period cannot change. Omit to keep the current selectors."},
                "reason": {"type": "string", "description": "The recorded result that justifies the change, 12 to 500 characters."},
            },
            "required": ["institution", "reason"],
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
                "entry_conditions": {"type": "array", "items": {"type": "string"}, "description": "Optional durable machine predicates only, such as actor.alive, actor.can_research, actor.inventory.item > 0, actor.state.location_id == <visible location name>, or world[\"<property>\"] >= <number>. A bare world property is not a condition; compare it with a number or true/false. Do not write prose, goals, or claims like 'actor can reach the records'; use [] when Action preconditions and a Travel node are sufficient."},
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
                "entity_type": {"type": "string", "description": "Use agent for a new playable character; otherwise use a configured type such as location, faction, object, material, or resource."},
                "name": {"type": "string"},
                "location": {"type": "string", "description": "A visible location name."},
                "state": {"type": "object", "description": "Initial state. For an agent, include role, ultimate_goal, initial_bt_ids, capabilities, needs, inventory, and initial_state with a visible location and status. Platform initializes configured defaults and validates every reference."},
                "description": {"type": "string"},
            },
            "required": ["entity_type", "name"],
            "additionalProperties": False,
        },
    },
    "propose_definition": {
        "purpose": "Submit declarative world mechanics: an attribute, world property, capability, Action, behavior tree, or recipe. A new Action must include followup_behavior so one assigned NPC can execute it immediately; Platform validates and registers the bound pair atomically.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "definition_type": {"type": "string", "enum": ["attribute", "world_property", "action", "behavior", "recipe", "capability"]},
                "name": {"type": "string", "description": "A short visible name. Platform derives the internal registry key."},
                "definition": {
                    "type": "object",
                    "description": "Declarative fields for the selected type. Use exact visible entity names; no free-form steps, code, handlers, SQL, or internal IDs.",
                    "properties": {
                        "nodes": {
                            "type": "array",
                            "description": "For a behavior, ordered executable nodes. Use an Action node with the exact registered Action name; include Travel before a destination Action when needed.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": ["action", "behavior", "sequence", "selector", "parallel", "condition", "wait"]},
                                    "action_id": {"type": "string", "description": "Exact visible Action name from world_action_catalog."},
                                    "behavior_id": {"type": "string", "description": "Exact visible BT name when using a behavior node."},
                                    "parameters": {"type": "object", "description": "Action parameter names and exact visible entity values."},
                                    "children": {"type": "array", "items": {"type": "object"}},
                                },
                                "additionalProperties": True,
                            },
                        },
                        "parameters": {
                            "type": "object",
                            "description": "For an Action: parameter_name -> one of string, number, integer, boolean, object, array. Do not use human-language placeholder values.",
                        },
                        "duration": {
                            "type": "object",
                            "description": "For an Action, use {minutes: positive number} or {mode: route_distance|recipe_duration}.",
                        },
                        "costs": {
                            "type": "object",
                            "description": "For an Action, reserve carried inputs under costs.reserve. Use item_parameter and optionally item_quantity_parameter for a variable amount.",
                        },
                        "preconditions": {"type": "array", "items": {"type": "string"}, "description": "Optional machine predicates for an Action."},
                        "effects": {"type": "array", "items": {"type": "object"}, "description": "Observable Action effects. resource_transfer must bind its source and reserved quantity."},
                        "success": {"type": "object", "description": "One Action success object: {condition?: machine predicate, result: short label}."},
                        "failure": {"type": "object", "description": "One Action failure object: {condition?: machine predicate, result: short label}."},
                        "entry_conditions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional durable machine predicates only, such as actor.alive, actor.can_research, actor.inventory.item > 0, actor.state.location_id == <visible location name>, or world[\"<property>\"] >= <number>. A bare world property is not a condition; compare it with a number or true/false. Do not write prose, goals, or claims like 'actor can reach the records'; use [] when Action preconditions and a Travel node are sufficient.",
                        },
                        "success_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable result labels."},
                        "failure_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable failure labels."},
                        "summary": {"type": "string"},
                        "goal": {"type": "string"},
                        "outcome_events": {"type": "array", "items": {"type": "object", "description": "Each event must use {event_type: new event name, summary: fact-based text, wake?: [story|god]}."}},
                    },
                    "additionalProperties": True,
                },
                "assign_to": {"type": "array", "items": {"type": "string"}, "description": "For a behavior, required visible character names that can execute it; other definition types must leave this empty."},
                "followup_behavior": {
                    "type": "object",
                    "description": "Required for a new Action. Bind the Action to one immediately executable behavior in the same transaction.",
                    "properties": {
                        "name": {"type": "string", "description": "Visible BT name."},
                        "assign_to": {"type": "array", "items": {"type": "string"}, "description": "Visible character names that can execute the BT now."},
                        "definition": {"type": "object", "description": "Behavior definition whose nodes include the new Action by its exact visible name."},
                    },
                    "required": ["name", "assign_to", "definition"],
                    "additionalProperties": False,
                },
                "followup_recipe": {
                    "type": "object",
                    "description": "Required when the new Action consumes a recipe. Register the recipe atomically with the Action and followup BT.",
                    "properties": {
                        "name": {"type": "string", "description": "Visible recipe name."},
                        "definition": {"type": "object", "description": "Recipe definition with duration_minutes, inputs and outputs."},
                    },
                    "required": ["name", "definition"],
                    "additionalProperties": False,
                },
                "evidence": {"type": "string", "description": "The observed result that justifies this new definition."},
            },
            "required": ["definition_type", "name", "definition"],
            "additionalProperties": False,
        },
    },
    "propose_world_change": {
        "purpose": "Propose an evidence-backed rule, relationship, existing-faction change, or narrative event. Behavior outcomes own resources and personal/social value changes.",
        "kind": "intent",
        "parameters": {
            "type": "object",
            "properties": {
                "change_type": {"type": "string", "enum": ["world_property", "rule", "relationship", "faction", "event"], "description": "Choose exactly one: world_property, rule, relationship, faction, or event."},
                "summary": {"type": "string"},
                "scope": {"type": "string"},
                "conditions": {"type": "array", "items": {"type": "string"}},
                "effects": {"type": "array", "items": {"type": "string"}, "description": "Observable effects. For a signal, say only what is recorded or woken; do not claim priority, blocking, holding, deferral, or an until condition."},
                "expected_effects": {"type": "array", "items": {"type": "string"}, "description": "Short observable effects to compare with the next world outcome; signal-only proposals must not claim enforcement."},
                "evaluation_window_minutes": {"type": "integer", "minimum": 1, "maximum": 43200, "description": "How long Platform should observe outcomes before God reviews this proposal."},
                "property": {"type": "string"},
                "value": {},
                "rule": {
                    "type": "object",
                    "description": "A declarative policy. Use a supported condition expression; a typed signal wakes work, while a typed denial or behavior_constraint blocks a target. A rule never changes resources by itself. Do not chain one same-role signal rule from another world.rule signal.",
                    "properties": {
                        "id": {"type": "string", "description": "A short unique rule name for the world."},
                        "label": {"type": "string", "description": "A human-readable policy name."},
                        "description": {"type": "string", "description": "One short sentence describing the policy."},
                        "condition": {"type": "string", "description": "A declarative condition such as world[\"water quality\"] < 0.5, event.recent.clinic.fever_reported == true, or actor.permission.old_well == false."},
                        "effect": {"description": "Use a typed object when enforcement matters: {type: signal, name: policy.review_signal, message: ...} records or wakes work; {type: deny, ...} or {type: behavior_constraint, ...} blocks one named target. A dot-separated string such as policy.review_signal is also a signal. Descriptions are explanatory text, not enforcement."},
                        "wake": {"type": "array", "items": {"type": "string"}, "description": "Optional visible roles to wake, such as god or story."},
                        "behavior_names": {"type": "array", "items": {"type": "string"}, "description": "Optional visible BT names. For a behavior_constraint, every listed BT is blocked; list only the blocked BT. For a signal, names identify the follow-up outcome."},
                        "action_names": {"type": "array", "items": {"type": "string"}, "description": "Optional visible Action names to block when the typed effect is a denial."},
                    },
                    "required": ["id", "condition", "effect"],
                    "additionalProperties": False,
                },
                "relationship": {
                    "type": "object",
                    "description": "Record one relationship between two visible participants; use names, never database identifiers.",
                    "properties": {
                        "from": {"type": "string", "description": "Exact visible name of the first participant."},
                        "to": {"type": "string", "description": "Exact visible name of the second participant."},
                        "type": {"type": "string", "description": "Short relationship kind, such as trust or information_exchange."},
                        "description": {"type": "string"},
                        "delta": {"type": "number"},
                    },
                    "required": ["from", "to", "type"],
                    "additionalProperties": False,
                },
                "faction": {"type": "object", "description": "Use visible member or location names, never database identifiers."},
            },
            "required": ["change_type", "summary"],
            "additionalProperties": False,
        },
    },
    "propose_story_beat": {
        "purpose": "Activate one listed beat by exact visible choice, or submit one short evidence-grounded emergent beat.",
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
    "agent_decision": ["observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "recall_memory", "preview_behavior", "send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "request_behavior", "wait"],
    "agent_reflection": ["recall_memory", "query_known_fact", "record_experience", "update_capability", "update_belief", "update_goal", "propose_behavior", "wait"],
    # Story receives the validated source and recent history in its world view;
    # query_history is the bounded read path for an older causal thread.
    "story": ["query_history", "query_knowledge_gaps", "record_world_progress", "propose_story_beat", "wait"],
    "god": ["observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "query_history", "query_knowledge_gaps", "recall_memory", "preview_behavior", "record_world_progress", "propose_entity", "propose_definition", "propose_world_change", "propose_institution", "approve_institution", "revise_institution", "wait"],
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
        definition["parameters"]["properties"]["state"]["description"] = "Initial structural state. For an agent, include role, ultimate_goal, initial_bt_ids, capabilities, needs, inventory, and initial_state with a visible location and status. A positive resource quantity must come from a validated behavior result."
    if name == "propose_world_change" and role == "god":
        definition["purpose"] = "Propose an evidence-backed rule, relationship, faction update, or narrative event. Existing world properties must be created through propose_definition, and behavior produces resource and personal/social values."
        change_type = definition["parameters"]["properties"]["change_type"]
        change_type["enum"] = ["rule", "relationship", "faction", "event"]
        change_type["description"] = "Choose exactly one: rule, relationship, faction, or event. Do not directly set a world property."
        definition["parameters"]["properties"].pop("property", None)
        definition["parameters"]["properties"].pop("value", None)
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
