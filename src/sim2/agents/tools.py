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


class AgentToolExecutor:
    """Execute role-allowed tools and route every mutation to Platform."""

    def __init__(
        self,
        platform: "Platform",
        *,
        role: str,
        actor_id: str,
        source_event_ids: list[int] | None = None,
        observed_world_revision: int | None = None,
        observed_agent_revision: int | None = None,
        evolution_contract: dict[str, Any] | None = None,
    ):
        self.platform = platform
        self.role = role
        self.actor_id = actor_id
        self.source_event_ids = [int(value) for value in (source_event_ids or []) if int(value) > 0]
        self.observed_world_revision = observed_world_revision
        self.observed_agent_revision = observed_agent_revision
        self.evolution_contract = dict(evolution_contract or {})
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
        public = self._publicize(result)
        # The model must not see implementation identifiers, but the
        # scheduler still needs a stable signal that a zero-minute interaction
        # committed successfully so it can apply the configured cooldown.
        if public.get("status") == "accepted" and name == "send_message":
            public["interaction_committed"] = True
        if public.get("status") == "accepted" and name in {
            "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer",
        }:
            public["trade_committed"] = True
        return public

    @classmethod
    def _publicize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            internal_fields = {
                "active_bt_instance", "active_behavior_id", "active_action_instance",
                "active_action_parameters", "active_action_reservation", "active_parameters",
                "active_parallel_actions", "execution_id", "action_instance_id",
            }
            return {
                key: cls._publicize(item)
                for key, item in value.items()
                if key not in {"id", "ref", "behavior_ref", "intent_id", "event_id"} and key not in internal_fields
                and key != "tags"
                and not key.endswith("_id")
            }
        if isinstance(value, list):
            return [cls._publicize(item) for item in value]
        return value

    def _agent(self) -> dict[str, Any]:
        return self.platform.store.get_agent(self.actor_id)

    def _contract_visible_name(self, value: Any) -> str:
        canonical = self.platform.targets.canonical_id(str(value))
        if canonical:
            try:
                return str(self.platform.store.get_agent(str(canonical)).get("name") or canonical)
            except KeyError:
                pass
        return str(value)

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
        # Keep instant testimony compact enough that the model does not turn a
        # short reply into a second narrative scene. The prompt still carries
        # the evidence-grounding rule; this boundary only enforces the length
        # contract advertised to every instant interaction.
        text = str(message or "")
        sentences = [part for part in re.split(r"(?<=[.!?。！？])", text) if part.strip()]
        if len(sentences) > 2 or len(text) > 240:
            return {
                "status": "rejected",
                "failed_check": "message_too_long",
                "retryable": True,
                "message": "Send at most two sentences and 240 characters. Keep only facts visible in this turn and say when a detail is unknown.",
            }
        return self.platform.submit_dialogue(self.actor_id, recipient, topic, message)

    def _wait(self, reason: str = "") -> dict[str, Any]:
        """End the bounded session without creating a world-side effect."""

        return {
            "status": "wait",
            "reason": str(reason or "No response or world change is needed now."),
        }

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

    def _query_history(
        self,
        event_type: str = "",
        actor: str = "",
        contains: str = "",
        since_minute: int | None = None,
        until_minute: int | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        return self.platform.perception.query_history(
            self.actor_id,
            event_type=event_type,
            actor=actor,
            contains=contains,
            since_minute=since_minute,
            until_minute=until_minute,
            limit=limit,
            global_scope=self.role in {"god", "story"},
        )

    def _query_knowledge_gaps(self, status: str = "active", contains: str = "", limit: int = 8) -> dict[str, Any]:
        return self.platform.perception.query_knowledge_gaps(
            self.actor_id,
            status=status,
            contains=contains,
            limit=limit,
            global_scope=self.role in {"god", "story"},
        )

    def _recall_memory(self, question: str = "", limit: int = 8, scope: str = "all") -> dict[str, Any]:
        return self.platform.memory.recall(self.actor_id, question, limit, scope=scope)

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
            observed_world_revision=self.observed_world_revision,
            observed_agent_revision=self.observed_agent_revision,
        )
        result = self.platform.submit_behavior(intent)
        if isinstance(result, dict) and result.get("behavior_ref"):
            # Keep canonical refs in Platform/audit storage, but return the
            # visible choice to the model so it never has to learn an ID.
            result = dict(result)
            result["choice"] = (behavior or {}).get("choice") or (behavior or {}).get("name")
            result.pop("behavior_ref", None)
        return result

    def _record_experience(self, summary: str, event: str = "", confidence: float = 1.0, claim_status: str = "inferred") -> dict[str, Any]:
        if self.role == "agent_reflection":
            meaningful_events = {
                "behavior.completed", "behavior.failed", "behavior.interrupted",
                "dialogue.completed", "trade.completed", "trade.offer_created",
                "trade.countered", "exploration.completed", "research.completed",
                "object.recovered", "relationship.changed", "world.change.world_property",
            }
            has_outcome = bool(self.platform.store.recent_outcomes_for_actor(self.actor_id, limit=1))
            has_memory = bool(self.platform.store.list_memories(self.actor_id, limit=1))
            has_event = any(
                str(item.get("event_type", "")) in meaningful_events
                for item in self.platform.store.recent_events_for_actor(self.actor_id, limit=20)
            )
            if not (has_outcome or has_memory or has_event):
                return {
                    "status": "rejected",
                    "failed_check": "reflection_no_observed_result",
                    "retryable": True,
                    "message": "No completed outcome, meaningful event, or prior memory is available for a durable experience.",
                }
        return self.platform.submit_experience(self.actor_id, event, summary, float(confidence), str(claim_status or "inferred"))

    def _update_capability(self, capability: str, delta: float, evidence: str) -> dict[str, Any]:
        return self.platform.submit_capability_update(self.actor_id, capability, float(delta), evidence)

    def _record_world_progress(self, progress_key: str, evidence: str) -> dict[str, Any]:
        return self.platform.submit_world_progress(
            self.actor_id,
            progress_key,
            None,
            evidence,
            source_event_ids=self.source_event_ids,
        )

    def _propose_institution(
        self,
        name: str,
        purpose: str,
        scope: str = "world",
        evidence: str = "",
        evidence_sources: list[dict[str, Any]] | None = None,
        minimum_evidence: int | None = None,
        ledger: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Event-triggered institution tasks already carry the exact persisted
        # evidence contract.  Authors and models should describe the public
        # institution, not copy internal selectors into every proposal.
        contract = self.evolution_contract
        if not evidence_sources and isinstance(contract.get("evidence_sources"), list):
            evidence_sources = list(contract.get("evidence_sources") or [])
        if minimum_evidence is None and contract.get("minimum_evidence") is not None:
            minimum_evidence = max(1, int(contract.get("minimum_evidence") or 1))
        return self.platform.submit_institution_proposal(
            self.actor_id,
            {"name": name, "purpose": purpose, "scope": scope, "evidence": evidence, "evidence_sources": list(evidence_sources or []), "minimum_evidence": int(minimum_evidence or 1), "ledger": dict(ledger or {}), "policy": dict(policy or {})},
        )

    def _approve_institution(self, institution: str, reason: str = "") -> dict[str, Any]:
        return self.platform.activate_institution(self.actor_id, institution, reason)

    def _revise_institution(
        self,
        institution: str,
        reason: str,
        policy: dict[str, Any] | None = None,
        ledger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.platform.revise_institution(self.actor_id, institution, policy=policy, ledger=ledger, reason=reason)

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

    def _propose_definition(
        self,
        definition_type: str,
        name: str,
        definition: dict[str, Any],
        assign_to: list[str] | None = None,
        followup_behavior: dict[str, Any] | None = None,
        followup_recipe: dict[str, Any] | None = None,
        evidence: str = "",
    ) -> dict[str, Any]:
        required_type = str(self.evolution_contract.get("required_definition_type") or "").strip()
        if required_type and str(definition_type).strip().casefold() != required_type.casefold():
            return {
                "status": "rejected",
                "failed_check": "evolution_contract",
                "retryable": True,
                "message": f"This evolution task requires a {required_type} definition; choose that definition_type or wait.",
            }
        required_assignees = self.evolution_contract.get("assign_to")
        if isinstance(required_assignees, str):
            required_assignees = [required_assignees]
        if required_assignees:
            supplied = {
                str(self.platform.targets.canonical_id(str(item)) or item)
                for item in (assign_to or [])
            }
            missing = [
                self._contract_visible_name(item)
                for item in required_assignees
                if str(self.platform.targets.canonical_id(str(item)) or item) not in supplied
            ]
            if missing:
                return {
                    "status": "rejected",
                    "failed_check": "evolution_contract",
                    "retryable": True,
                    "message": "Assign the behavior to the configured audience so it enters the next NPC catalog.",
                    "required_assign_to": [self._contract_visible_name(item) for item in required_assignees],
                }
        if self.evolution_contract.get("require_story_outcome") and str(definition_type).strip().casefold() == "action":
            followup_definition = (followup_behavior or {}).get("definition") if isinstance(followup_behavior, dict) else None
            outcome_events = followup_definition.get("outcome_events") if isinstance(followup_definition, dict) else None
            if not any(
                isinstance(event, dict) and "story" in {str(role) for role in (event.get("wake") or [])}
                for event in (outcome_events or [])
            ):
                return {
                    "status": "rejected",
                    "failed_check": "evolution_contract",
                    "retryable": True,
                    "message": "This evolution task requires the bound behavior to declare one outcome_events entry with wake ['story'] so Story receives the executed result.",
                }
        return self.platform.submit_definition_proposal(
            self.actor_id,
            {
                "definition_type": definition_type,
                "name": name,
                "definition": dict(definition or {}),
                "assign_to": list(assign_to or []),
                "followup_behavior": dict(followup_behavior or {}) if followup_behavior else None,
                "followup_recipe": dict(followup_recipe or {}) if followup_recipe else None,
                "evidence": evidence,
            },
        )

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
        evaluation_window_minutes: int = 1440,
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
            "evaluation_window_minutes": int(evaluation_window_minutes),
            "property": property,
            "value": value,
            "rule": dict(rule or {}),
            "relationship": dict(relationship or {}),
            "faction": dict(faction or {}),
        }
        return self.platform.submit_world_change(self.actor_id, proposal, origin_role=self.role)

    def _propose_story_beat(self, beat: str = "", audience: list[str] | None = None, summary: str = "", fact: str = "", goal_hint: str = "", research_source: str = "", unlock_choices: list[str] | None = None, reason: str = "") -> dict[str, Any]:
        return self.platform.submit_story_beat(
            self.actor_id,
            {"beat": beat, "audience": list(audience or []), "summary": summary, "fact": fact, "goal_hint": goal_hint, "research_source": research_source, "unlock_choices": list(unlock_choices or []), "reason": reason},
            source_event_ids=self.source_event_ids,
        )

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
    # Several OpenAI-compatible free models use the Anthropic/SDK-style
    # ``tool_uses`` envelope in JSON mode even when native tools were not
    # negotiated.  Treat it as a prompt-level call; the Engine will append
    # the result as ordinary ReAct context and will not fabricate a native
    # provider correlation id.
    if candidate is None and response.get("tool_uses"):
        candidate = response["tool_uses"][0]
    if candidate is None and response.get("type") == "tool_call":
        candidate = response
    # Longcat and a few other compatible endpoints flatten a call as
    # ``{"tool": "name", "parameters": {...}}``.  This is still a tool
    # request, not a world Intent; normalize it before the role and Platform
    # validation layers see it.
    if candidate is None and isinstance(response.get("tool"), str):
        candidate = response
    # Some OpenAI-compatible gateways flatten one function call to
    # ``{"name": ..., "arguments": {...}}``.  Accept that wire shape at the
    # protocol boundary; the role and schema checks still happen below.
    if candidate is None and isinstance(response.get("name"), str) and "arguments" in response:
        candidate = response
    if isinstance(candidate, list):
        candidate = candidate[0] if candidate else None
    if not isinstance(candidate, dict):
        return None
    function = candidate.get("function") if isinstance(candidate.get("function"), dict) else candidate
    name = (
        function.get("name")
        or candidate.get("name")
        or candidate.get("tool")
        or (candidate.get("function") if isinstance(candidate.get("function"), str) else None)
    )
    arguments = function.get(
        "arguments",
        function.get(
            "args",
            candidate.get(
                "arguments",
                candidate.get("parameters", candidate.get("params", candidate.get("input", {}))),
            ),
        ),
    )
    # Some JSON-mode adapters put the arguments beside the tool name rather
    # than under ``arguments``/``params``.  Keep only fields that are not
    # protocol envelope metadata; the normal tool-schema validator still
    # rejects anything unknown for the selected tool.
    if not arguments and isinstance(candidate, dict):
        envelope_fields = {
            "tool", "name", "type", "id", "function", "arguments", "args",
            "parameters", "params", "input",
        }
        arguments = {key: value for key, value in candidate.items() if key not in envelope_fields}
    if not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return name, arguments
