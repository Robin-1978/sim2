"""Prompt sections only the world reviewer receives: trigger, proposal contract, catalogs, trends and outcomes."""

from __future__ import annotations

from typing import Any

from .formatting import number, plain


def _evolution_trigger_lines(context: dict[str, Any]) -> list[str]:
    """The configured trigger that woke the world reviewer."""

    lines: list[str] = []
    trigger = context.get("evolution_trigger") or {}
    if trigger:
        lines.append("<evolution_trigger>")
        lines.append(f"  id: {trigger.get('id', 'configured trigger')}")
        lines.append(f"  condition: {trigger.get('condition', 'configured condition')}")
        if trigger.get("purpose"):
            lines.append(f"  purpose: {trigger.get('purpose')}")
        if trigger.get("required_result"):
            lines.append(f"  required result: {trigger.get('required_result')}")
        if trigger.get("required_definition_type"):
            lines.append(f"  required definition type: {trigger.get('required_definition_type')}")
        if trigger.get("assign_to"):
            lines.append(f"  assign the new behavior to: {plain(trigger.get('assign_to'))}")
        if trigger.get("evidence_sources"):
            lines.append(f"  evidence selectors: {plain(trigger.get('evidence_sources'))}")
        if trigger.get("minimum_evidence") is not None:
            lines.append(f"  minimum matching events: {trigger.get('minimum_evidence')}; observed: {trigger.get('observed_evidence', 'unknown')}")
        if trigger.get("require_story_outcome"):
            lines.append("  required handoff: an Action followup_behavior must include one outcome_events entry with wake ['story'] so Story receives the real completed result.")
        # The submission format of a research source is shown to wakes that
        # review research evidence. Matching the wording of the required
        # result instead pulled every wake that merely listed a research
        # source among its options toward proposing one.
        evidence_types = {
            str(event_type)
            for source in (trigger.get("evidence_sources") or []) if isinstance(source, dict)
            for event_type in (source.get("event_types") or [])
        }
        if evidence_types & {"research.completed", "research.progressed"}:
            lines.append("  Research continuation: propose one reachable source with a new novelty key, question and discovery. When sources are exhausted, shift to a distinct causal dimension (effect, risk, intervention, provenance or application) and key; never rename a duplicate or wait only on word overlap.")
        lines.append("  Use only the configured trigger tools. A rule signal or narrative acknowledgement is not a substitute for the required result.")
        lines.append("</evolution_trigger>")
    return lines


def _proposal_contract_lines(context: dict[str, Any]) -> list[str]:
    """What a world proposal may contain and how Platform validates it."""

    lines: list[str] = []
    lines.append("<world_proposal_contract>")
    lines.append("  - Create any missing world content the validated situation needs: agent, location, faction, object, empty resource node, institution, relationship, rule, attribute, world property, capability, Action, behavior, recipe, or narrative event.")
    lines.append("  - For a research source, put research_discoveries, research_required/required, and any research_assignees in the proposed entity data. Reuse the registered generic research behavior; do not create one BT per source.")
    lines.append("  - Use the smallest supported content type that makes the next character decision possible. Platform still validates names, references, conservation, executable behavior, and consequences.")
    lines.append("  - rule.behavior_names must use exact BT names from world_behavior_catalog.")
    lines.append("  - In a behavior_constraint, every listed BT is blocked; list only the blocked BT, never the desired follow-up BT.")
    lines.append("  - rule.action_names must use exact Action names from world_action_catalog; a BT name is not an Action name.")
    lines.append("  - A typed signal records or wakes work; prose descriptions never enforce policy. To block behavior, use effect type behavior_constraint or a denial effect.")
    lines.append("  - Use the exact machine field in rule_condition_fields, including underscores inside world[\"...\"]. A God proposal must not wake God itself; use a character/follow-up behavior or wait.")
    lines.append("  - A behavior proposal must use definition.nodes: [{type: action, action_id: exact registered Action name, parameters: {...}}], plus entry_conditions, success_results and failure_results. Do not invent steps, trigger, outcomes, action_names or free-form action objects.")
    lines.append("  - A new Action cannot be submitted alone: include followup_behavior {name, assign_to, definition} in the same propose_definition call. If its duration is recipe_duration or its reserve uses recipe_inputs, include followup_recipe {name, definition} in that same call. The BT must reference the new Action by visible name and be executable for the assigned NPC immediately.")
    lines.append("  - Recipe Action shape: parameters:{recipe_id:'string'}, duration:{mode:'recipe_duration'}, costs:{reserve:{recipe_inputs:1}}; put recipe_inputs under costs.reserve. Include followup_recipe with positive duration_minutes, inputs and outputs; existing resource names are resolved to registry keys.")
    lines.append("  - Action success/failure each need {condition?, result}; Travel To uses location_id. Copy parameter names from world_action_catalog. A dynamic parameter such as item_id is not an inventory key; omit the condition or use the literal visible item name.")
    lines.append("  - A new Action must have a real observable result: declare effects, information or capability/progress effects, or bind a Recipe with outputs. A success label or outcome_events field alone never changes resources or state; do not call a resource refill successful without a configured effect that performs it.")
    lines.append("  - To move carried material into an existing world resource, use costs.reserve:{item_parameter:<item parameter>, item_quantity_parameter:<quantity parameter>} and effect:{type:'resource_transfer', resource_id:<existing visible resource>, source_item_parameter:<same item parameter>, quantity_parameter:<same quantity parameter>}; omit item_quantity_parameter only for one item with quantity:1. The transfer must equal the reserved amount and cannot mint stock or target a missing resource.")
    lines.append("  - A resource_transfer reserves the carried item before the success condition is checked. Its success condition must not inspect actor.inventory.<item>; use actor.alive or an observable destination/world result instead.")
    lines.append("  - Action parameters whose names end in _entity or _id must use exact visible entity names from the world view and parameter names from the Action catalog. A behavior is rejected unless its assigned character can execute the whole sequence now.")
    visible_names = ", ".join(str(item.get("name")) for item in (context.get("visible_entities") or [])[:48] if item.get("name"))
    lines.append(f"  - Exact visible entities (use these names): {visible_names or 'none'}.")
    examples = context.get("condition_examples") or {}
    if examples:
        lines.append("  - Machine condition examples: " + "; ".join(f"{label}={example}" for label, example in examples.items()) + ". A bare world property is not a condition; compare it with a number or true/false, or use [] when no durable condition is needed.")
    lines.append("  - Reusable behavior entry_conditions must use durable actor, capability, inventory, location, relationship or world state. Do not use event.recent.*, which would make the behavior disappear immediately after the current event.")
    lines.append("  - Research is open-ended: when validated results leave a concrete unanswered question, you may propose one new declarative research source or behavior. Do not invent a result; let a character perform the next research.")
    lines.append("  - A newer verified research fact may supersede an older one. Preserve the old fact for audit and use the configured lifecycle status instead of silently deleting it.")
    lines.append("  - If the assigned actor is not already at the required place, include a Travel node before the destination Action; Platform must be able to execute the complete sequence from current state. If no durable condition is needed, use entry_conditions: [] rather than prose.")
    lines.append("  - After a read-budget warning, use the facts already collected. Do not re-read the same source and exhaust the session.")
    lines.append("</world_proposal_contract>")
    return lines


def _public_progress_lines(context: dict[str, Any]) -> list[str]:
    """Configured public progress definitions."""

    lines: list[str] = []
    progress_candidates = context.get("world_progress_candidates") or []
    lines.append("<configured_public_progress>")
    if progress_candidates:
        for item in progress_candidates[:12]:
            evidence = list(item.get("evidence_sources") or []) or (list(item.get("evidence_event_types") or []) + list(item.get("evidence_behaviors") or []))
            lines.append(
                f"  - {item.get('progress_key')} target={number(item.get('required', 1))}: "
                f"{item.get('description') or 'configured progress'}; accepted evidence={plain(evidence) or 'any validated outcome'}"
            )
        lines.append("  Submit progress only when the triggering evidence matches the named progress definition; otherwise wait.")
    else:
        lines.append("  none configured; do not invent a progress key")
    lines.append("</configured_public_progress>")
    return lines


def _catalog_lines(context: dict[str, Any]) -> list[str]:
    """Rule condition fields and the registered behavior and action catalogs."""

    lines: list[str] = []
    lines.append("<rule_condition_fields>")
    fields = context.get("rule_condition_fields") or []
    for field in fields[:20]:
        lines.append(f"  - {field.get('condition')}: {field.get('meaning', 'configured world value')}")
    if not fields:
        lines.append("  - none")
    lines.append("</rule_condition_fields>")
    lines.append("<world_behavior_catalog>")
    catalog = context.get("registered_behaviors") or []
    for behavior in catalog[:24]:
        success = f"; success={plain(behavior.get('success'))}" if behavior.get("success") else ""
        lines.append(f"  - BT: {behavior.get('name', 'behavior')}: {behavior.get('purpose', 'configured behavior')}{success}")
    if not catalog:
        lines.append("  - none")
    lines.append("</world_behavior_catalog>")
    lines.append("<world_action_catalog>")
    actions = context.get("registered_actions") or []
    # God needs the registered action vocabulary and outcome, but not the
    # full parameter schema for every action during a world review. The
    # detailed schema remains in character/BT views where it is used to
    # execute a behavior.
    for action in actions[:16]:
        parameters = "; ".join(str(item) for item in (action.get("parameters") or []))
        defaults = plain(action.get("parameter_defaults") or {})
        success = plain(action.get("success") or [])
        costs = action.get("costs") if isinstance(action.get("costs"), dict) else {}
        reserve = plain((costs or {}).get("reserve") or {})
        duration_value = action.get("duration") if isinstance(action.get("duration"), dict) else {}
        recipe_like = duration_value.get("mode") == "recipe_duration" or "recipe_inputs" in ((costs or {}).get("reserve") or {})
        detail = f"parameters={parameters or 'none'}"
        if recipe_like:
            detail += f"; defaults={defaults or 'none'}; duration={plain(duration_value) or 'not stated'}; reserve={reserve or 'none'}"
        lines.append(
            f"  - Action: {action.get('name', 'action')}: {action.get('purpose', 'configured action')}; "
            f"{detail}; result={success or 'not stated'}"
        )
    if not actions:
        lines.append("  - none")
    lines.append("</world_action_catalog>")
    return lines


def _world_trend_lines(context: dict[str, Any]) -> list[str]:
    """Resource, relationship and goal trends over the review windows."""

    lines: list[str] = []
    lines.append("<world_trends>")
    trends = context.get("world_trends") or {}
    resource_labels = trends.get("resource_labels") or {}

    def resource_changes(values: Any) -> str:
        if not isinstance(values, dict) or not values:
            return "none"
        return "; ".join(f"{resource_labels.get(str(key), str(key))}={plain(value)}" for key, value in values.items())

    current_resources = trends.get("current_resources") or []
    lines.append("  Current resources: " + ("; ".join(f"{item.get('name', 'resource')}={plain(item.get('current', 'unknown'))}" for item in current_resources[:8]) if current_resources else "none") + ".")
    configured_regeneration = context.get("configured_resource_regeneration") or []
    if configured_regeneration:
        lines.append(
            "  Configured regeneration (future world rule, do not confuse with the current observation window): "
            + "; ".join(
                f"{item.get('resource')} +{plain(item.get('amount'))} every {item.get('every_minutes')} minutes"
                + (f" up to {plain(item.get('cap'))}" if item.get('cap') is not None else "")
                for item in configured_regeneration[:8]
            )
            + "."
        )
    else:
        lines.append("  Configured regeneration: none declared.")
    for period in ("weekly", "monthly"):
        item = trends.get(period) or {}
        goal = item.get("goal_trends") or {}
        relationship = item.get("relationship_trends") or []
        lines.append(
            f"  {period}: resource regeneration={resource_changes(item.get('resources_regenerated') or {})}; "
            f"resource net change={resource_changes(item.get('resource_deltas') or {})}; "
            f"relationship change={plain(relationship) if relationship else 'none'}; "
            f"goal progress={plain(goal.get('progress_by_tag') or {})}; "
            f"completed={goal.get('completed_behaviors', 0)}, failed_or_interrupted={goal.get('failed_or_interrupted_behaviors', 0)}."
        )
    progress = context.get("civilization_progress") or []
    lines.append("  Civilization progress: " + ("; ".join(f"{item.get('progress_key')}={plain(item.get('current', 0))}/{plain(item.get('required', 0))}" for item in progress[:8]) if progress else "none") + ".")
    lines.append("</world_trends>")
    return lines


def _world_outcome_lines(context: dict[str, Any]) -> list[str]:
    """Recent world changes, pending evaluations and character outcomes."""

    lines: list[str] = []
    lines.append("<world_outcomes>")
    changes = context.get("recent_world_changes") or []
    if changes:
        for item in changes[:6]:
            effect = item.get("effect") or {}
            expected = item.get("expected_effects") or item.get("effects") or []
            lines.append(
                f"  - {item.get('event_type', 'world.change')}: {item.get('summary', 'proposal applied')}; "
                f"expected={plain(expected) if expected else 'not stated'}; "
                f"observed={plain(effect) if effect else 'event recorded'} at minute {item.get('world_minute', 'unknown')}"
            )
    else:
        lines.append("  - no recent world proposal result")
    pending = [item for item in (context.get("world_proposals") or []) if item.get("status") == "pending_evaluation"]
    if pending:
        lines.append("  Pending proposal evaluations:")
        for item in pending[:6]:
            # Proposal/intent IDs are audit-only identifiers.  They add no
            # meaning to a model decision and make prompt cache/content
            # checks noisy, so expose the typed change and due time only.
            expected = item.get("expected") if isinstance(item.get("expected"), dict) else {}
            state = expected.get("state") if isinstance(expected, dict) else {}
            label = expected.get("summary") or item.get("change_type") or "world proposal"
            lines.append(f"  - {label}; due={item.get('evaluation_due_minute')}; expected={plain(state or expected)}")
    else:
        lines.append("  Pending proposal evaluations: none.")
    outcomes = context.get("recent_agent_outcomes") or []
    if outcomes:
        lines.append("  Recent character outcomes:")
        for item in outcomes[:6]:
            outcome = item.get("outcome") or {}
            lines.append(f"  - {item.get('actor_name') or 'a character'}: {item.get('behavior_choice') or item.get('behavior_name') or 'a behavior'} -> {outcome.get('status', item.get('event_type', 'unknown'))}; {outcome.get('result', 'no result')}")
    lines.append("  Compare expected and observed effects before proposing one next world change.")
    lines.append("</world_outcomes>")
    return lines


def god_lines(context: dict[str, Any]) -> list[str]:
    """Sections only the world reviewer receives."""

    lines: list[str] = []
    lines.extend(_evolution_trigger_lines(context))
    lines.extend(_proposal_contract_lines(context))
    lines.extend(_public_progress_lines(context))
    lines.extend(_catalog_lines(context))
    lines.extend(_world_trend_lines(context))
    lines.extend(_world_outcome_lines(context))
    return lines
