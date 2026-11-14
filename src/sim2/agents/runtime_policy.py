from __future__ import annotations

from typing import Any

from ..platform.runtime import Platform
from .policies import default_role_policy


ROLE_NAMES = {"agent_decision", "agent_reflection", "god", "story", "world_builder"}


def role_policy(platform: Platform, role: str) -> str:
    prompt = platform.store.repositories.memory.get_prompt("agent", {}) or {}
    prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
    policies = prompt.get("role_policies", {}) if isinstance(prompt, dict) else {}
    return str(policies.get(role) or default_role_policy(role))


def configured_role_tools(platform: Platform, role: str) -> list[str] | None:
    prompt = platform.store.repositories.memory.get_prompt("agent", {}) or {}
    prompt = prompt.get("agent_prompt", prompt) if isinstance(prompt, dict) else {}
    policy = prompt.get("tool_policy", {}) if isinstance(prompt, dict) else {}
    names = policy.get(role) if isinstance(policy, dict) else None
    return [str(item) for item in names if str(item)] if isinstance(names, list) else None


def configured_evolution_trigger(platform: Platform, task_type: str) -> dict[str, Any] | None:
    """Return the declarative contract for one world trigger task."""

    prefix = "trigger:"
    if not str(task_type).startswith(prefix):
        return None
    trigger_id = str(task_type)[len(prefix):]
    root = platform.store.get_meta("evolution", {}) or {}
    root = root.get("evolution", root) if isinstance(root, dict) else {}
    for trigger in (root.get("triggers", []) or []) if isinstance(root, dict) else []:
        if isinstance(trigger, dict) and str(trigger.get("id") or "") == trigger_id:
            return dict(trigger)
    return None


def configured_evolution_group(platform: Platform, task_type: str) -> dict[str, Any] | None:
    """Return the declarative contract for one configured evidence group."""

    prefix = "event_group:"
    if not str(task_type).startswith(prefix):
        return None
    group_id = str(task_type)[len(prefix):]
    root = platform.store.get_meta("evolution", {}) or {}
    root = root.get("evolution", root) if isinstance(root, dict) else {}
    consumers = ((root.get("event_wake_policy", {}) or {}).get("consumers", {}) or {}) if isinstance(root, dict) else {}
    for policy in consumers.values() if isinstance(consumers, dict) else []:
        if not isinstance(policy, dict):
            continue
        for group in policy.get("event_groups", policy.get("groups", [])) or []:
            if isinstance(group, dict) and str(group.get("id") or "") == group_id:
                contract = group.get("agent_contract")
                return dict(contract) if isinstance(contract, dict) else None
    return None

ROLE_INTENTS = {
    "agent_decision": {"select_behavior", "send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer", "wait"},
    "agent_reflection": {"record_experience", "update_capability", "update_belief", "update_goal", "propose_behavior", "wait"},
    "god": {"record_world_progress", "propose_entity", "propose_definition", "propose_world_change", "propose_institution", "approve_institution", "revise_institution", "wait"},
    "story": {"record_world_progress", "propose_story_beat", "wait"},
    "world_builder": {"submit_world_template", "wait"},
}
