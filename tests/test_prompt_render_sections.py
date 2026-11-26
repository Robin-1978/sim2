"""Section layout and size limits of the rendered Agent prompt.

The renderer emits the stable protocol first so a provider can cache the prefix,
then the role's evidence, and always ends with the task.  Each role gets only
the sections that belong to it, and an over-long prompt gives up background
sections before it ever touches the task or the executable choices.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context

WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"
UNLIMITED = 10**7
TASK = "Choose one behavior."
TAG = re.compile(r"^\s*<([a-z_]+)>$", re.MULTILINE)

_CHARACTER_HEAD = [
    "protocol", "platform_rules", "allowed_intents_and_tools", "world_state",
    "self", "state_attributes", "identity", "needs_and_state", "drives",
    "scene", "nearby_entities", "relationships", "knowledge_and_memory",
    "recent_actions", "open_trade_offers",
]
_WORLD_HEAD = [
    "protocol", "platform_rules", "allowed_intents_and_tools", "world_state",
    "world_history", "active_story_threads", "knowledge_gaps", "research_sources", "scene",
]
# Sections that always exist for the role, in the order they must appear.  Sections whose
# presence depends on world data (rules, research tracks, constraints) are not listed.
SECTION_ORDER = {
    "agent_decision": [*_CHARACTER_HEAD, "active_execution", "executable_behaviors", "interaction_history", "decision"],
    "agent_reflection": [*_CHARACTER_HEAD, "capability_updates", "outcome_review", "registered_actions", "interaction_history", "decision"],
    "god": [
        *_WORLD_HEAD, "world_proposal_contract", "configured_public_progress", "rule_condition_fields",
        "world_behavior_catalog", "world_action_catalog", "world_trends", "world_outcomes",
        "interaction_history", "decision",
    ],
    "story": [*_WORLD_HEAD, "knowledge_and_memory", "validated_source", "story_state", "audience_candidates", "interaction_history", "decision"],
}
ACTORS = {"agent_decision": "su_wu", "agent_reflection": "su_wu", "god": "god", "story": "story"}
CHARACTER_ONLY = {
    "self", "state_attributes", "identity", "needs_and_state", "drives", "nearby_entities",
    "relationships", "recent_actions", "open_trade_offers", "active_execution", "executable_behaviors",
}


def _platform(tmp_path: Path) -> Platform:
    return Platform(create_instance(WORLD, "render-sections-world", tmp_path / "runtime"))


def _tags(prompt: str) -> list[str]:
    return TAG.findall(prompt)


@pytest.mark.parametrize("role", sorted(SECTION_ORDER))
def test_each_role_renders_its_sections_in_order_and_ends_with_the_task(tmp_path: Path, role: str):
    platform = _platform(tmp_path)
    try:
        view = platform.view.build_decision_view(ACTORS[role], role)
    finally:
        platform.close()

    prompt = render_context(view, TASK, max_chars=UNLIMITED)

    expected = SECTION_ORDER[role]
    assert [tag for tag in _tags(prompt) if tag in set(expected)] == expected
    assert prompt.endswith(f"<decision>\n  {TASK}\n</decision>")
    # The stable protocol and tool names come before any dynamic state.
    assert prompt.index("<protocol>") < prompt.index("<allowed_intents_and_tools>") < prompt.index("<world_state>")


def test_world_level_prompts_omit_character_scaffolding_that_characters_receive(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        tags = {
            role: set(_tags(render_context(platform.view.build_decision_view(ACTORS[role], role), TASK, max_chars=UNLIMITED)))
            for role in SECTION_ORDER
        }
    finally:
        platform.close()

    assert CHARACTER_ONLY <= tags["agent_decision"] | tags["agent_reflection"]
    assert not CHARACTER_ONLY & tags["god"]
    assert not CHARACTER_ONLY & tags["story"]
    assert "knowledge_and_memory" not in tags["god"] and "knowledge_and_memory" in tags["story"]
    # A reflection turn reviews outcomes instead of choosing a behavior.
    assert not {"executable_behaviors", "active_execution"} & tags["agent_reflection"]
    assert not {"capability_updates", "outcome_review", "registered_actions"} & tags["agent_decision"]


def test_session_section_reports_the_react_history_and_a_rejection_correction():
    context = {
        "role": "agent_decision",
        "decision_guidance": "Prefer food.",
        "interaction_history": [
            {"kind": "assistant_tool_call", "name": "query_history", "arguments": {"limit": 3}},
            {"kind": "tool_result", "name": "query_history", "result": {"status": "ok", "message": "two events"}},
            {"kind": "assistant_intent", "intent": {"status": "pending", "message": "wait"}},
            {"kind": "platform_result", "result": {"status": "rejected", "message": "No target"}},
            {"kind": "llm_error", "error": "timeout"},
            {"note": "free form"},
        ],
        "last_platform_result": {"status": "rejected", "message": "No target", "failed_check": "target"},
    }

    prompt = render_context(context, TASK)

    assert prompt[prompt.index("  <interaction_history>"):].split("\n<platform_correction>")[0].split("\n") == [
        "  <interaction_history>",
        "    - tool request query_history: limit=3",
        "    - tool result query_history: status=ok; message=two events",
        "    - intent request: status=pending; message=wait",
        "    - Platform result: status=rejected; message=No target",
        "    - LLM error: timeout",
        "    - event: note=free form",
        "    latest_platform_result: status=rejected; failed_check=target; message=No target",
        "</interaction_history>",
    ]
    assert "<platform_correction>  The previous request was rejected." in prompt
    assert prompt.endswith(f"<decision>\n  {TASK}\n  Decision guidance: Prefer food.\n</decision>")


def test_accepted_result_adds_no_correction_and_guidance_is_only_for_decisions():
    context = {"role": "agent_reflection", "decision_guidance": "ignored here", "last_platform_result": {"status": "accepted"}}

    prompt = render_context(context, "Reflect.")

    assert "<platform_correction>" not in prompt
    assert "Decision guidance" not in prompt
    assert prompt.endswith("<decision>\n  Reflect.\n</decision>")


def test_empty_context_renders_the_defaults_of_a_decision_turn():
    prompt = render_context({}, "wait")

    assert "    - none; return wait" in prompt
    assert "    - none in this session" in prompt
    assert prompt.endswith("<decision>\n  wait\n</decision>")


def test_small_overflow_drops_only_the_first_background_section_and_says_so(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        view = platform.view.build_decision_view("su_wu", "agent_decision")
    finally:
        platform.close()
    full = render_context(view, TASK, max_chars=UNLIMITED)
    limit = len(full) - 10

    prompt = render_context(view, TASK, max_chars=limit)

    assert len(prompt) <= limit
    assert prompt.endswith("\n<context_limit>Omitted knowledge_and_memory; use a read tool if needed.</context_limit>")
    assert "<knowledge_and_memory>" not in prompt and "<nearby_entities>" in prompt
    assert "<executable_behaviors>" in prompt and prompt.count("<decision>") == 1


def test_tight_limit_keeps_the_executable_choices_and_the_task_verbatim(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        view = platform.view.build_decision_view("su_wu", "agent_decision")
    finally:
        platform.close()
    full = render_context(view, TASK, max_chars=UNLIMITED)
    choices = full[full.index("  <executable_behaviors>"): full.index("  </executable_behaviors>") + len("  </executable_behaviors>")]
    tail = full[full.index("<decision>"):]
    limit = 5000
    assert len(choices) + len(tail) + 120 < limit < len(full)

    prompt = render_context(view, TASK, max_chars=limit)

    assert len(prompt) <= limit
    assert "<context_limit>Earlier context was compacted; use a read tool if needed.</context_limit>" in prompt
    assert choices in prompt
    assert prompt.endswith(tail)


def test_oversized_behavior_catalog_keeps_complete_blocks_and_decision():
    """A catalog that cannot fit at all must degrade to complete blocks, not a broken prompt."""

    from sim2.prompting.compaction import fit_to_limit

    blocks = [
        f"    <behavior>\n      name: Choice {index}\n      choice: Choice {index}\n    </behavior>"
        for index in range(100)
    ]
    rendered = (
        "<world_state>small</world_state>\n"
        "  <executable_behaviors>\n"
        + "\n".join(blocks)
        + "\n  </executable_behaviors>\n"
        "<decision>\n  Choose one behavior.\n</decision>"
    )
    fitted = fit_to_limit(rendered, "agent_decision", 4000)

    assert len(fitted) <= 4000
    assert fitted.count("<behavior>") == fitted.count("</behavior>")
    assert fitted.count("<decision>") == 1
    assert "more behaviors omitted by the prompt budget" in fitted
    assert fitted.rstrip().endswith("</decision>")


def test_oversized_compact_catalog_keeps_complete_lines():
    """A large compact list is trimmed by whole lines, never mid-line."""

    from sim2.prompting.compaction import fit_to_limit

    blocks = [
        f"    <behavior>\n      name: Full {index}\n      choice: Full {index}\n    </behavior>"
        for index in range(5)
    ]
    compact = [
        f"      - Compact Choice {index} | 5 minutes | actor.alive | drives: purpose"
        for index in range(100)
    ]
    rendered = (
        "<world_state>small</world_state>\n"
        "  <executable_behaviors>\n"
        + "\n".join(blocks)
        + "\n    <more_behaviors>\n      Use query_behavior_catalog for full details.\n"
        + "\n".join(compact)
        + "\n    </more_behaviors>\n  </executable_behaviors>\n"
        "<decision>\n  Choose one behavior.\n</decision>"
    )
    fitted = fit_to_limit(rendered, "agent_decision", 4000)

    assert len(fitted) <= 4000
    assert fitted.count("<behavior>") == fitted.count("</behavior>")
    assert "<more_behaviors>" in fitted and "</more_behaviors>" in fitted
    assert "more behaviors omitted by the prompt budget" in fitted
    assert fitted.count("<decision>") == 1
    for line in fitted.splitlines():
        if "Compact Choice" in line:
            assert line.strip().startswith("- Compact Choice") and "drives: purpose" in line


def test_god_compaction_keeps_rule_condition_fields_and_world_trends(tmp_path: Path):
    """Exact machine fields and current trends are decision evidence, not background."""

    platform = _platform(tmp_path)
    try:
        with platform.store.transaction():
            platform.store.repositories.events.append_event(
                "resource.changed", "fei_lan", 100, {"resource_id": "village_food", "delta": -12},
            )
        view = platform.view.build_decision_view("god", "god")
    finally:
        platform.close()
    full = render_context(view, TASK, max_chars=UNLIMITED)
    compacted = render_context(view, TASK, max_chars=len(full) - 10)

    assert "<rule_condition_fields>" in compacted
    assert 'world["village_food"]' in compacted
    assert "<world_trends>" in compacted
    assert "<decision>" in compacted


def test_token_budget_is_enforced_without_dropping_the_decision(tmp_path: Path):
    from sim2.prompting.compaction import estimate_tokens

    platform = _platform(tmp_path)
    try:
        view = platform.view.build_decision_view("su_wu", "agent_decision")
    finally:
        platform.close()
    prompt = render_context(view, TASK, max_chars=UNLIMITED, max_tokens=2500)

    assert estimate_tokens(prompt) <= 2500
    assert "<decision>" in prompt
    assert "<executable_behaviors>" in prompt


def test_agent_turn_records_context_budget_metrics(tmp_path: Path):
    from sim2.agents.engine import AgentEngine

    class WaitProvider:
        max_agent_iterations = 2

        def complete_json(self, *, system, context, tools=None, messages=None):
            return {"intent_type": "wait", "reason": "No world change is needed."}

    platform = _platform(tmp_path)
    try:
        turn = AgentEngine(platform, WaitProvider()).run(role="agent_decision", actor_id="su_wu")
        metrics = turn.context_metrics[0]
        assert metrics["role"] == "agent_decision"
        assert metrics["estimated_tokens"] > 0
        assert metrics["max_chars"] > 0
        assert "<decision>" in turn.prompt_history[0]
        rows = platform.store.connection.execute(
            "SELECT prompt_tokens_est, context_metrics_json FROM llm_call ORDER BY call_id DESC LIMIT 1"
        ).fetchall()
        assert rows and rows[0]["prompt_tokens_est"] == metrics["request_input_tokens_est"]
        assert rows[0]["context_metrics_json"]
    finally:
        platform.close()
