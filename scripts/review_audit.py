"""Review a completed real-agent audit for context and causal invariants.

This is intentionally separate from the live evaluator.  ``audit_real_world``
records every prompt and transcript; this command checks the complete artifact
after the world stops, including prompt ordering, ReAct feedback continuity,
role-scoped quality, and persisted behavior/proposal causality.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def _issue(kind: str, detail: str, session: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": kind, "detail": detail}
    if session is not None:
        result["session"] = session
    return result


def _provider_error(session: dict[str, Any]) -> bool:
    result = session.get("result") if isinstance(session.get("result"), dict) else {}
    quality = session.get("quality") if isinstance(session.get("quality"), dict) else {}
    return result.get("failed_check") == "llm_response" or "provider_error" in str(quality.get("grade") or "")


def _unresolved_provider_errors(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return provider failures without a same-boundary successful retry."""

    successful = {
        (
            str(item.get("role") or ""),
            str(item.get("actor_id") or ""),
            str(item.get("task_type") or ""),
            int((item.get("world_time") or {}).get("minute", -1)),
        )
        for item in sessions
        if not _provider_error(item) and (item.get("result") or {}).get("status") in {"accepted", "wait"}
    }
    return [
        item for item in sessions
        if _provider_error(item)
        and (
            str(item.get("role") or ""),
            str(item.get("actor_id") or ""),
            str(item.get("task_type") or ""),
            int((item.get("world_time") or {}).get("minute", -1)),
        ) not in successful
    ]


def _prompt_checks(session: dict[str, Any], index: int) -> list[dict[str, Any]]:
    prompts = list(session.get("prompt_history") or [])
    if not prompts:
        return [_issue("missing_prompt_history", "session has no rendered prompt", index)]
    issues: list[dict[str, Any]] = []
    prefixes: list[str] = []
    for prompt_index, prompt in enumerate(prompts):
        if not isinstance(prompt, str):
            issues.append(_issue("invalid_prompt", f"prompt {prompt_index} is not text", index))
            continue
        required = ("<protocol>", "<world_state>", "<interaction_history>", "<decision>")
        missing = [tag for tag in required if tag not in prompt]
        if missing:
            issues.append(_issue("prompt_missing_section", f"prompt {prompt_index}: {missing}", index))
            continue
        positions = {tag: prompt.find(tag) for tag in required}
        if not positions["<protocol>"] < positions["<world_state>"] < positions["<interaction_history>"] < positions["<decision>"]:
            issues.append(_issue("prompt_section_order", f"prompt {prompt_index}: {positions}", index))
        prefixes.append(prompt[: positions["<world_state>"]])
        if len(prompt) > 16000:
            issues.append(_issue("prompt_over_budget", f"prompt {prompt_index}: {len(prompt)} chars", index))
        if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", prompt, re.IGNORECASE):
            issues.append(_issue("internal_uuid_in_prompt", f"prompt {prompt_index} exposes a UUID", index))
    if prefixes and any(prefix != prefixes[0] for prefix in prefixes[1:]):
        issues.append(_issue("unstable_static_prefix", "protocol/tool prefix changed inside one ReAct session", index))
    llm_steps = [step for step in session.get("transcript", []) if step.get("step") == "llm"]
    if len(prompts) != len(llm_steps):
        issues.append(_issue("prompt_response_count_mismatch", f"prompts={len(prompts)} llm_steps={len(llm_steps)}", index))
    return issues


def _feedback_checks(session: dict[str, Any], index: int) -> list[dict[str, Any]]:
    prompts = list(session.get("prompt_history") or [])
    transcript = list(session.get("transcript") or [])
    issues: list[dict[str, Any]] = []
    llm_position = -1
    for step in transcript:
        if step.get("step") == "llm":
            llm_position += 1
            continue
        if step.get("step") not in {"tool", "intent"}:
            continue
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        if result.get("status") != "rejected" or llm_position + 1 >= len(prompts):
            continue
        next_prompt = prompts[llm_position + 1]
        message = str(result.get("message") or result.get("failed_check") or "").strip()
        if message and message[:18] not in next_prompt and str(result.get("failed_check") or "") not in next_prompt:
            issues.append(_issue("platform_feedback_not_rendered", f"rejection after LLM step {llm_position} is absent from next prompt", index))
    return issues


def _tool_view_checks(session: dict[str, Any], index: int) -> list[dict[str, Any]]:
    """Check that the model saw a bounded, provenance-preserving tool view."""

    issues: list[dict[str, Any]] = []
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.IGNORECASE)
    for step_index, step in enumerate(session.get("transcript", []) or []):
        if step.get("step") != "tool":
            continue
        model_result = step.get("model_result")
        raw_result = step.get("raw_result", step.get("result"))
        # Older audit artifacts predate the dual-track field; do not claim a
        # model-view defect when reviewing those historical files.
        if model_result is None:
            continue
        try:
            encoded = json.dumps(model_result, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            issues.append(_issue("invalid_model_tool_view", f"tool {step_index}: {exc}", index))
            continue
        if len(encoded) > 6000:
            issues.append(_issue("model_tool_view_over_budget", f"tool {step_index}: {len(encoded)} chars", index))
        if uuid_pattern.search(encoded):
            issues.append(_issue("internal_uuid_in_model_tool_view", f"tool {step_index} exposes an internal UUID", index))
        if isinstance(raw_result, dict) and isinstance(model_result, dict):
            if raw_result.get("status") != model_result.get("status"):
                issues.append(_issue("tool_status_changed_in_view", f"tool {step_index}: raw={raw_result.get('status')}; model={model_result.get('status')}", index))
            if raw_result.get("source") and model_result.get("source") != raw_result.get("source"):
                issues.append(_issue("tool_source_lost_in_view", f"tool {step_index}: source={raw_result.get('source')}", index))
            if raw_result.get("failed_check") and model_result.get("failed_check") != raw_result.get("failed_check"):
                issues.append(_issue("tool_feedback_lost_in_view", f"tool {step_index}: failed_check={raw_result.get('failed_check')}", index))
    return issues


def _database_checks(data: dict[str, Any]) -> dict[str, Any]:
    """Verify persisted execution and evolution closure for the full run.

    Prompt checks can prove what the model saw, but only the instance database
    can prove that Platform subsequently completed or retained every long
    action.  This pass deliberately checks all rows, not a recent-event
    window.
    """

    db_path = data.get("db_path")
    if not db_path or not Path(str(db_path)).is_file():
        return {"passed": False, "issues": [_issue("missing_audit_database", str(db_path))]}
    issues: list[dict[str, Any]] = []
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        def rows(query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return [dict(row) for row in connection.execute(query, args).fetchall()]

        def payload(row: dict[str, Any], key: str = "data_json") -> dict[str, Any]:
            try:
                value = json.loads(row.get(key) or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return value if isinstance(value, dict) else {}

        requested = int(data.get("target_minute", data.get("minutes_requested", 0)) or 0)
        runtime_status = str((data.get("world") or {}).get("status", {}).get("status", ""))
        if runtime_status and runtime_status != "running":
            issues.append(_issue("world_runtime_not_running", runtime_status))
        minute_row = connection.execute("SELECT value FROM instance_meta WHERE key='world_minute'").fetchone()
        final_minute = int(json.loads(minute_row["value"])) if minute_row else None
        if requested and final_minute != requested:
            issues.append(_issue("world_run_incomplete", f"requested={requested}; persisted={final_minute}"))

        bt_rows = rows("SELECT execution_id,status,data_json FROM bt_instance")
        bt_status = {str(row["execution_id"]): str(row["status"]) for row in bt_rows}
        event_rows = rows("SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log ORDER BY event_id")
        started = {}
        outcomes = {}
        for row in event_rows:
            item = payload(row)
            execution_id = str(item.get("execution_id") or item.get("intent_id") or "")
            if row["event_type"] == "behavior.started" and execution_id:
                started[execution_id] = row
            elif row["event_type"] in {"behavior.completed", "behavior.failed", "behavior.interrupted"} and execution_id:
                outcomes[execution_id] = row
                outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
                required = {"expected", "actual", "delta", "result", "status"}
                missing = sorted(key for key in required if key not in outcome)
                if missing:
                    issues.append(_issue("behavior_outcome_incomplete", f"execution={execution_id}; missing={missing}"))
        for execution_id, row in started.items():
            if execution_id not in outcomes and bt_status.get(execution_id) not in {"executing", "paused"}:
                issues.append(_issue("behavior_execution_not_closed", f"execution={execution_id}; status={bt_status.get(execution_id)}"))

        action_started: set[str] = set()
        action_finished: set[str] = set()
        action_started_events = 0
        action_finished_events = 0
        for row in event_rows:
            item = payload(row)
            execution_id = str(item.get("execution_id") or "")
            if not execution_id:
                continue
            if row["event_type"] == "action.started":
                action_started_events += 1
                action_started.add(execution_id)
            elif row["event_type"] in {"action.completed", "action.failed", "action.interrupted"}:
                action_finished_events += 1
                action_finished.add(execution_id)
        running_actions = {str(row["execution_id"]) for row in rows("SELECT execution_id FROM action_instance WHERE status IN ('running','paused')")}
        for execution_id in sorted(action_started - action_finished - running_actions):
            issues.append(_issue("action_execution_not_closed", f"action_instance={execution_id}"))

        # A behavior may fail legitimately, but repeatedly offering the same
        # unchanged choice with the same failure means AgentView validation and
        # execution disagree.  Structural closure alone would otherwise mark
        # every failed instance as healthy.
        repeated_failures: dict[tuple[str, str, str], list[int]] = {}
        for row in event_rows:
            if row["event_type"] != "behavior.failed":
                continue
            item = payload(row)
            key = (
                str(row["actor_id"] or ""),
                str(item.get("behavior_id") or ""),
                str(item.get("result") or (item.get("outcome") or {}).get("result") or ""),
            )
            repeated_failures.setdefault(key, []).append(int(row["world_minute"]))
        for (actor_id, behavior_id, result), minutes in repeated_failures.items():
            if len(minutes) >= 3:
                issues.append(_issue(
                    "repeated_behavior_failure",
                    f"actor={actor_id}; behavior={behavior_id}; result={result}; minutes={minutes[:12]}",
                ))

        event_by_id = {int(row["event_id"]): {**row, **payload(row)} for row in event_rows}
        completed_locations: dict[tuple[str, int], str] = {}
        for row in event_rows:
            if row["event_type"] != "behavior.completed":
                continue
            item = payload(row)
            outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
            actual = outcome.get("actual") if isinstance(outcome.get("actual"), dict) else {}
            if actual.get("location"):
                completed_locations[(str(row["actor_id"] or ""), int(row["world_minute"]))] = str(actual["location"])

        spatial_observations = 0
        for row in event_rows:
            item = payload(row)
            observed = item.get("observed") if isinstance(item.get("observed"), dict) else {}
            parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
            if not parameters.get("object_id") or not observed.get("location"):
                continue
            spatial_observations += 1
            actor_location = completed_locations.get((str(row["actor_id"] or ""), int(row["world_minute"])))
            if actor_location and actor_location != str(observed["location"]):
                issues.append(_issue(
                    "remote_object_observation",
                    f"event={row['event_id']}; actor_location={actor_location}; object_location={observed['location']}",
                ))

        rules_row = connection.execute("SELECT value FROM instance_meta WHERE key='rules'").fetchone()
        rules = json.loads(rules_row["value"]) if rules_row else {}
        progress_definitions = rules.get("civilization_progress", {}) if isinstance(rules, dict) else {}
        progress_sources: set[tuple[str, int]] = set()
        progress_evidence_rows = 0

        def progress_source_matches(definition: dict[str, Any], source: dict[str, Any]) -> bool:
            clauses = [item for item in (definition.get("evidence_sources") or []) if isinstance(item, dict)]
            if clauses:
                identifiers = {
                    str(source.get(key)) for key in ("entity_id", "object_id", "source_id", "target_id", "resource_id")
                    if source.get(key) is not None
                }
                for clause in clauses:
                    event_types = {str(value) for value in (clause.get("event_types") or ([clause["event_type"]] if clause.get("event_type") else []))}
                    behavior_ids = {str(value) for value in (clause.get("behavior_ids") or ([clause["behavior_id"]] if clause.get("behavior_id") else []))}
                    action_ids = {str(value) for value in (clause.get("action_ids") or ([clause["action_id"]] if clause.get("action_id") else []))}
                    entity_ids = {str(value) for value in (clause.get("entity_ids") or ([clause["entity_id"]] if clause.get("entity_id") else []))}
                    if event_types and str(source.get("event_type") or "") not in event_types:
                        continue
                    if behavior_ids and str(source.get("behavior_id") or "") not in behavior_ids:
                        continue
                    if action_ids and str(source.get("action_id") or "") not in action_ids:
                        continue
                    if entity_ids and not identifiers & entity_ids:
                        continue
                    return True
                return False
            events = {str(value) for value in (definition.get("evidence_event_types") or [])}
            behaviors = {str(value) for value in (definition.get("evidence_behaviors") or [])}
            return (not events and not behaviors) or str(source.get("event_type") or "") in events or str(source.get("behavior_id") or "") in behaviors

        for row in event_rows:
            if row["event_type"] not in {"civilization.progressed", "civilization.milestone"}:
                continue
            progress_evidence_rows += 1
            item = payload(row)
            key = str(item.get("progress_key") or (item.get("progress") or {}).get("progress_key") or "")
            source_id = int(item.get("source_event_id", 0) or 0)
            definition = progress_definitions.get(key) if isinstance(progress_definitions, dict) else None
            source = event_by_id.get(source_id)
            if not key or source is None:
                issues.append(_issue("progress_source_missing", f"event={row['event_id']}; progress={key}; source={source_id}"))
                continue
            source_key = (key, source_id)
            if source_key in progress_sources:
                issues.append(_issue("duplicate_progress_evidence", f"progress={key}; source={source_id}"))
            progress_sources.add(source_key)
            if isinstance(definition, dict) and not progress_source_matches(definition, source):
                issues.append(_issue("progress_source_mismatch", f"progress={key}; source={source_id}; type={source.get('event_type')}"))
            progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
            actual_delta = float(progress.get("after", 0) or 0) - float(progress.get("before", 0) or 0)
            expected_delta = float((definition or {}).get("evidence_value", 1) or 1) if isinstance(definition, dict) else actual_delta
            if abs(actual_delta - expected_delta) > 1e-9:
                issues.append(_issue("progress_delta_mismatch", f"progress={key}; source={source_id}; expected={expected_delta}; actual={actual_delta}"))

        capability_source_uses: set[tuple[str, int]] = set()
        capability_updates = 0
        for row in event_rows:
            if row["event_type"] != "capability.updated":
                continue
            capability_updates += 1
            item = payload(row)
            capability = str(item.get("capability") or "")
            source_ids = [int(value) for value in (item.get("source_event_ids") or []) if int(value) > 0]
            if not capability or not source_ids:
                issues.append(_issue("capability_evidence_missing", f"event={row['event_id']}; capability={capability}"))
                continue
            for source_id in source_ids:
                source = event_by_id.get(source_id)
                source_key = (capability, source_id)
                if source_key in capability_source_uses:
                    issues.append(_issue("duplicate_capability_evidence", f"capability={capability}; source={source_id}"))
                capability_source_uses.add(source_key)
                if (
                    source is None
                    or str(source.get("event_type") or "") != "capability.evidence"
                    or str(source.get("actor_id") or "") != str(row["actor_id"] or "")
                    or str(source.get("capability") or "") != capability
                ):
                    issues.append(_issue(
                        "capability_evidence_mismatch",
                        f"update={row['event_id']}; capability={capability}; source={source_id}",
                    ))

        scheduled_due = rows("SELECT task_id,status,due_minute FROM evolution_task WHERE due_minute<=?", (final_minute if final_minute is not None else (requested or 10**9),))
        open_statuses = {"ready", "claimed", "running", "scheduled"}
        open_tasks = [row for row in scheduled_due if str(row["status"]) in open_statuses]
        if open_tasks:
            issues.append(_issue("evolution_tasks_unresolved", str([{key: row[key] for key in ("task_id", "status", "due_minute")} for row in open_tasks])))

        accepted_behavior_intents = rows("SELECT intent_id,result_json FROM intent WHERE status IN ('accepted','completed') AND intent_type IN ('select_behavior','request_behavior')")
        for row in accepted_behavior_intents:
            result = payload(row, "result_json")
            execution_id = str(result.get("execution_id") or "")
            if execution_id and execution_id not in outcomes and bt_status.get(execution_id) not in {"executing", "paused"}:
                issues.append(_issue("accepted_behavior_without_execution", f"intent={row['intent_id']}; execution={execution_id}"))

        god_property = [
            row for row in event_rows
            if row["actor_id"] == "god" and row["event_type"] in {"world.change.world_property", "world.property_changed"}
        ]
        if god_property:
            issues.append(_issue("god_direct_property_change", str([row["event_id"] for row in god_property])))

        exchange_rows = rows("SELECT id,speaker_id,listener_id,topic,world_minute,data_json FROM dialogue_exchange ORDER BY id")
        entity_ids = {str(row["id"]) for row in rows("SELECT id FROM entity")}
        empty_messages = 0
        placeholder_topics = []
        oversized_messages = []
        exact_repeats = 0
        prior: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
        for row in exchange_rows:
            data_json = payload(row)
            message = str(data_json.get("message") or "").strip()
            if not message:
                empty_messages += 1
            if str(row["topic"]).strip().startswith("context.") or "{context." in str(row["topic"]):
                placeholder_topics.append({"id": row["id"], "topic": row["topic"]})
            if len(message) > 1000:
                oversized_messages.append(row["id"])
            if row["speaker_id"] not in entity_ids or (row["listener_id"] and row["listener_id"] not in entity_ids):
                issues.append(_issue("dialogue_unknown_participant", f"exchange={row['id']}"))
            key = (str(row["speaker_id"]), str(row["listener_id"] or ""), str(row["topic"]))
            if message:
                for previous_minute, previous in prior.get(key, []):
                    if int(row["world_minute"]) - previous_minute <= 360 and message.casefold() == previous.casefold():
                        exact_repeats += 1
                        break
                prior.setdefault(key, []).append((int(row["world_minute"]), message))
                prior[key] = prior[key][-8:]
        if placeholder_topics:
            issues.append(_issue("dialogue_placeholder_topic", str(placeholder_topics[:8])))
        if oversized_messages:
            issues.append(_issue("dialogue_message_over_limit", str(oversized_messages[:8])))

        return {
            "passed": not issues,
            "world_minute": final_minute,
            "behavior_started": len(started),
            "behavior_closed": len(outcomes),
            "action_started": action_started_events,
            "action_closed": action_finished_events,
            "action_running": len(running_actions),
            "evolution_due": len(scheduled_due),
            "dialogue_exchanges": len(exchange_rows),
            "dialogue_empty_messages": empty_messages,
            "dialogue_exact_repeats_within_window": exact_repeats,
            "spatial_observations": spatial_observations,
            "progress_evidence_rows": progress_evidence_rows,
            "capability_updates": capability_updates,
            "issues": issues,
        }
    finally:
        connection.close()


def review(data: dict[str, Any]) -> dict[str, Any]:
    sessions = list(data.get("sessions") or [])
    issues: list[dict[str, Any]] = []
    role_stats: dict[str, dict[str, int]] = {}
    for index, session in enumerate(sessions):
        role = str(session.get("role") or "unknown")
        stats = role_stats.setdefault(role, {"sessions": 0, "quality_failed": 0, "prompt_issues": 0, "feedback_issues": 0})
        stats["sessions"] += 1
        quality = session.get("quality") or {}
        provider_error = _provider_error(session)
        if not provider_error and not quality.get("passed") and not quality.get("pending") and session.get("result", {}).get("status") != "skipped":
            stats["quality_failed"] += 1
        prompt_issues = _prompt_checks(session, index) if not provider_error and session.get("result", {}).get("status") != "skipped" else []
        feedback_issues = _feedback_checks(session, index)
        tool_view_issues = _tool_view_checks(session, index)
        stats["prompt_issues"] += len(prompt_issues)
        stats["feedback_issues"] += len(feedback_issues) + len(tool_view_issues)
        issues.extend(prompt_issues)
        issues.extend(feedback_issues)
        issues.extend(tool_view_issues)
    unresolved_provider_errors = _unresolved_provider_errors(sessions)
    if unresolved_provider_errors:
        issues.append(_issue("unresolved_provider_errors", str([
            {
                "role": item.get("role"),
                "actor": item.get("actor_id"),
                "task": item.get("task_type"),
                "minute": (item.get("world_time") or {}).get("minute"),
            }
            for item in unresolved_provider_errors[:12]
        ])))
    world = data.get("world") or {}
    causality = world.get("causality") or {}
    if causality.get("orphan_behavior_intents"):
        issues.append(_issue("orphan_behavior_intents", str(causality["orphan_behavior_intents"])))
    if causality.get("god_direct_property_changes"):
        issues.append(_issue("god_direct_property_change", str(causality["god_direct_property_changes"])))
    for change in causality.get("world_rule_changes") or []:
        if change.get("status") not in {"pending", "verified"}:
            issues.append(_issue("unknown_rule_followup_status", str(change)))
    quality_counts = world.get("quality_counts") or {}
    database_checks = _database_checks(data)
    issues.extend(database_checks.get("issues") or [])
    if int(quality_counts.get("pending", 0) or 0) > 0:
        issues.append(_issue("unresolved_quality_checks", str(quality_counts.get("pending"))))
    target_minute = int(data.get("target_minute", data.get("minutes_requested", 0)) or 0)
    start_minute = int(data.get("start_minute", 0) or 0)
    if target_minute - start_minute >= 1440:
        role_counts = {role: sum(1 for item in sessions if item.get("role") == role) for role in ("agent_decision", "agent_reflection", "story", "god")}
        missing_roles = [role for role, count in role_counts.items() if count == 0]
        if missing_roles:
            issues.append(_issue("missing_agent_phase_coverage", str(missing_roles)))
    return {
        "passed": not issues and int(quality_counts.get("failed", 0) or 0) == 0 and int(quality_counts.get("pending", 0) or 0) == 0,
        "sessions": len(sessions),
        "quality_counts": quality_counts,
        "role_stats": role_stats,
        "rule_count": len(causality.get("world_rule_changes") or []),
        "rule_followup_status": {status: sum(1 for item in causality.get("world_rule_changes") or [] if item.get("status") == status) for status in ("verified", "pending")},
        "database_checks": database_checks,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = review(json.loads(args.audit.read_text(encoding="utf-8")))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
