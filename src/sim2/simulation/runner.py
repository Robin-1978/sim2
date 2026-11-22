from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import unicodedata
from typing import Any, Callable

from ..agents.engine import AgentEngine, AgentTurn
from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from ..world.lifecycle import BUSY_STATUSES, is_inactive
from ..world.time import minutes_until_next_day_phase


@dataclass
class SimulationReport:
    start_minute: int
    end_minute: int
    decisions: int = 0
    reflections: int = 0
    god_sessions: int = 0
    story_sessions: int = 0
    waits: int = 0
    llm_errors: int = 0
    rejected_turns: int = 0
    interaction_replies_suppressed: int = 0
    interaction_threads_closed: int = 0
    event_types: dict[str, int] = field(default_factory=dict)
    selected_behaviors: dict[str, int] = field(default_factory=dict)
    degeneration_flags: list[str] = field(default_factory=list)
    diversity: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None
    bt_decisions: int = 0
    director_plans: int = 0
    director_errors: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
            "decisions": self.decisions,
            "reflections": self.reflections,
            "god_sessions": self.god_sessions,
            "story_sessions": self.story_sessions,
            "waits": self.waits,
            "llm_errors": self.llm_errors,
            "rejected_turns": self.rejected_turns,
            "interaction_replies_suppressed": self.interaction_replies_suppressed,
            "interaction_threads_closed": self.interaction_threads_closed,
            "event_types": self.event_types,
            "selected_behaviors": self.selected_behaviors,
            "degeneration_flags": self.degeneration_flags,
            "diversity": self.diversity,
            "stop_reason": self.stop_reason,
            "bt_decisions": self.bt_decisions,
            "director_plans": self.director_plans,
            "director_errors": self.director_errors,
        }


class SimulationRunner:
    """Drive Platform minutes and invoke Agent phases only at boundaries."""

    def __init__(self, platform: Platform, providers: dict[str, Any], *, wait_cooldown_minutes: int = 30, llm_error_cooldown_minutes: int = 60, max_interaction_rounds: int | None = None, interaction_cooldown_minutes: int | None = None, interaction_thread_window_minutes: int | None = None, turn_observer: Callable[[AgentTurn, str], None] | None = None, resolve_interactions: bool = True):
        self.platform = platform
        self.providers = providers
        self.resolve_interactions = bool(resolve_interactions)
        interaction_policy = (self.platform.store.repositories.metadata.get("rules", {}) or {}).get("interaction_policy", {}) or {}
        self.wait_cooldown_minutes = max(1, int(wait_cooldown_minutes))
        self.llm_error_cooldown_minutes = max(1, int(llm_error_cooldown_minutes))
        evolution = (self.platform.store.repositories.metadata.get("evolution", {}) or {})
        evolution = evolution.get("evolution", evolution) if isinstance(evolution, dict) else {}
        limits = evolution.get("limits") if isinstance(evolution, dict) else {}
        self.max_ready_god_tasks = max(1, int((limits or {}).get("max_ready_god_tasks", 4) or 4))
        director = evolution.get("director") if isinstance(evolution, dict) else {}
        self.director_policy = str((director or {}).get("policy") or "deterministic")
        self.director_cadence_minutes = max(1, int((director or {}).get("cadence_minutes", 60) or 60))
        self._last_director_plan_id: str | None = None
        self.max_interaction_rounds = max(1, int(interaction_policy.get("max_rounds", 3) if max_interaction_rounds is None else max_interaction_rounds))
        self.interaction_cooldown_minutes = max(1, int(interaction_policy.get("reply_cooldown_minutes", 120) if interaction_cooldown_minutes is None else interaction_cooldown_minutes))
        self.interaction_thread_window_minutes = max(self.interaction_cooldown_minutes, int(interaction_policy.get("thread_window_minutes", 360) if interaction_thread_window_minutes is None else interaction_thread_window_minutes))
        self._decision_not_before: dict[str, int] = {}
        self._llm_error_count: dict[str, int] = {}
        self._llm_retry_until: dict[str, int] = {}
        self._failure_seen: dict[str, int] = {}
        self._interaction_events_seen: set[int] = set()
        self._interaction_rounds: dict[tuple[str, ...], tuple[int, int]] = {}
        self._interaction_messages: dict[tuple[str, str], list[tuple[int, str]]] = {}
        self._interaction_not_before: dict[str, int] = {}
        self._provider_retry_pending = False
        self._wait_streak: dict[str, int] = {}
        self._decision_event_cursor = 0
        self.turn_observer = turn_observer
        self._load_scheduler_state()
        # Claims are transient.  Restore due work before this runner starts
        # scheduling new minutes so a crash cannot strand an evolution task.
        self.platform.recover_claimed_evolution_tasks()

    def _load_scheduler_state(self) -> None:
        state = self.platform.store.repositories.metadata.get("runtime_scheduler_state", {}) or {}
        self._decision_not_before = {str(key): int(value) for key, value in (state.get("decision_not_before") or {}).items()}
        self._llm_error_count = {str(key): int(value) for key, value in (state.get("llm_error_count") or {}).items()}
        self._llm_retry_until = {str(key): int(value) for key, value in (state.get("llm_retry_until") or {}).items()}
        self._failure_seen = {str(key): int(value) for key, value in (state.get("failure_seen") or {}).items()}
        self._interaction_not_before = {str(key): int(value) for key, value in (state.get("interaction_not_before") or {}).items()}
        self._interaction_events_seen = {int(value) for value in (state.get("interaction_events_seen") or [])}
        self._provider_retry_pending = bool(state.get("provider_retry_pending", False))
        self._wait_streak = {str(key): int(value) for key, value in (state.get("wait_streak") or {}).items()}
        self._decision_event_cursor = int(state.get("decision_event_cursor", 0) or 0)
        self._interaction_rounds = {
            tuple(str(value) for value in item.get("key", [])): (int(item.get("last_minute", 0)), int(item.get("rounds", 0)))
            for item in (state.get("interaction_rounds") or []) if isinstance(item, dict) and item.get("key")
        }
        self._interaction_messages = {
            tuple(str(value) for value in item.get("key", [])): [(int(row[0]), str(row[1])) for row in (item.get("messages") or []) if isinstance(row, list) and len(row) == 2]
            for item in (state.get("interaction_messages") or []) if isinstance(item, dict) and item.get("key")
        }

    def _persist_scheduler_state(self) -> None:
        state = {
            "decision_not_before": self._decision_not_before,
            "llm_error_count": self._llm_error_count,
            "llm_retry_until": self._llm_retry_until,
            "failure_seen": self._failure_seen,
            "interaction_not_before": self._interaction_not_before,
            "interaction_events_seen": sorted(self._interaction_events_seen)[-2000:],
            "provider_retry_pending": self._provider_retry_pending,
            "wait_streak": self._wait_streak,
            "decision_event_cursor": self._decision_event_cursor,
            "interaction_rounds": [
                {"key": list(key), "last_minute": value[0], "rounds": value[1]}
                for key, value in sorted(self._interaction_rounds.items())
            ],
            "interaction_messages": [
                {"key": list(key), "messages": [[minute, message] for minute, message in values[-8:]]}
                for key, values in sorted(self._interaction_messages.items())
            ],
        }
        with self.platform.store.transaction():
            self.platform.store.repositories.metadata.set("runtime_scheduler_state", state)

    def _observe_turn(self, turn: AgentTurn, actor_id: str) -> None:
        if self.turn_observer is not None:
            self.turn_observer(turn, actor_id)

    def run(self, minutes: int, *, require_running: bool = True) -> SimulationReport:
        minutes = int(minutes)
        if minutes < 1:
            raise ValueError("minutes must be at least 1")
        start = int(self.platform.store.repositories.metadata.get("world_minute", 0))
        report = SimulationReport(start, start + minutes)
        for _ in range(minutes):
            if require_running and self.platform.status().get("status") != "running":
                report.stop_reason = "runtime_not_running"
                break
            cooling_roles = self._cooling_provider_roles()
            if cooling_roles:
                report.stop_reason = "provider_cooldown:" + ",".join(cooling_roles)
                break
            # Resume and fully resolve any persisted zero-minute interaction
            # before advancing world time. This is the recovery path when a
            # provider cooldown paused a dialogue or trade halfway through.
            if not self._run_instant_interactions(report):
                cooling_roles = self._cooling_provider_roles()
                report.stop_reason = (
                    "provider_cooldown:" + ",".join(cooling_roles)
                    if cooling_roles else "interaction_pending"
                )
                break
            cooling_roles = self._cooling_provider_roles()
            if cooling_roles:
                report.stop_reason = "provider_cooldown:" + ",".join(cooling_roles)
                break
            if self._provider_retry_pending:
                self._provider_retry_pending = False
                self._run_due_evolution(report)
                self._run_idle_decisions(report)
                cooling_roles = self._cooling_provider_roles()
                if cooling_roles:
                    report.stop_reason = "provider_cooldown:" + ",".join(cooling_roles)
                    break
                self._persist_scheduler_state()
            # A completed discovery creates a Story task at the current
            # boundary. Resolve that feedback before the next world minute so
            # the story cannot lag one tick behind the fact it is meant to
            # interpret. Rejected or unavailable Story work pauses the world
            # without advancing time again.
            if any(task.get("role") == "story" for task in self.platform.ready_evolution_tasks()):
                self._run_due_evolution(report)
                if require_running and self.platform.status().get("status") != "running":
                    break
            self._plan_next_structure(report)
            self.platform.step()
            if not self._run_instant_interactions(report):
                cooling_roles = self._cooling_provider_roles()
                report.stop_reason = (
                    "provider_cooldown:" + ",".join(cooling_roles)
                    if cooling_roles else "interaction_pending"
                )
                break
            self._run_due_evolution(report)
            self._run_idle_decisions(report)
        report.end_minute = int(self.platform.store.repositories.metadata.get("world_minute", 0))
        report.event_types = self.platform.store.repositories.events.event_type_counts()
        report.selected_behaviors = self.platform.store.repositories.events.behavior_start_counts()
        report.diversity = self._diversity(report)
        report.degeneration_flags = self._degeneration_flags(report)
        self._persist_scheduler_state()
        return report

    def resolve_pending_interactions(self) -> SimulationReport:
        """Drain pending dialogue/trade micro-turns without advancing time."""

        minute = int(self.platform.store.repositories.metadata.get("world_minute", 0))
        report = SimulationReport(minute, minute)
        resolved = self._run_instant_interactions(report)
        if not resolved:
            cooling_roles = self._cooling_provider_roles()
            report.stop_reason = (
                "provider_cooldown:" + ",".join(cooling_roles)
                if cooling_roles
                else "interaction_pending"
            )
        self._persist_scheduler_state()
        return report

    def _cooling_provider_roles(self) -> list[str]:
        return sorted(role for role, provider in self.providers.items() if self._provider_cooling_down(provider))

    def provider_cooldown_seconds(self) -> float:
        """Longest cooldown left on any provider, for callers that wait it out."""

        return max((self._cooldown_seconds(provider) for provider in self.providers.values()), default=0.0)

    def _run_instant_interactions(self, report: SimulationReport) -> bool:
        """Dispatch zero-minute dialogue/trade events as bounded micro-turns."""

        provider = self.providers.get("agent_decision")
        pending = [
            event for event in reversed(self.platform.store.repositories.events.recent_events(limit=100))
            if int(event.get("event_id", 0)) not in self._interaction_events_seen
            and event.get("event_type") in {"dialogue.completed", "trade.offer_created", "trade.countered"}
        ]
        if not pending:
            return True
        if not self.resolve_interactions:
            # No model provider can answer these interactions.  Keep the
            # authored world playable instead of freezing the clock on an
            # unanswerable dialogue; the events remain in the public log.
            self._interaction_events_seen.update(int(event.get("event_id", 0)) for event in pending)
            self._persist_scheduler_state()
            return True
        if provider is None or self._provider_cooling_down(provider):
            return False
        for event in pending:
            if self._provider_cooling_down(provider):
                return False
            event_id = int(event.get("event_id", 0))
            if event_id in self._interaction_events_seen:
                continue
            if event.get("event_type") not in {"dialogue.completed", "trade.offer_created", "trade.countered"}:
                continue
            minute = int(self.platform.store.repositories.metadata.get("world_minute", 0))
            actor_id = str(event.get("actor_id") or "")
            recipient_id = str(event.get("target_id") or event.get("recipient_id") or "")
            if not actor_id or not recipient_id or actor_id == recipient_id:
                self._interaction_events_seen.add(event_id)
                self._persist_scheduler_state()
                continue
            if not self.platform.store.repositories.events.claim_interaction(event_id, minute):
                # A concurrent runner (dialogue reply thread or manual
                # advancement) already owns this interaction.  Never answer
                # the same message twice.
                self._interaction_events_seen.add(event_id)
                continue
            self._interaction_events_seen.add(event_id)
            self._persist_scheduler_state()
            if self._is_player_controlled(recipient_id):
                # A human-controlled character answers through player intents.
                # Record the incoming message, but never speak for the player.
                continue
            pair = tuple(sorted((actor_id, recipient_id)))
            dialogue = None
            event_data = event.get("data") if isinstance(event.get("data"), dict) else event
            dialogue_id = event_data.get("dialogue_id") if isinstance(event_data, dict) else None
            if dialogue_id is not None:
                dialogue = self.platform.store.repositories.social.get_dialogue_exchange(dialogue_id)
            topic = str((dialogue or {}).get("topic") or event_data.get("topic") or "general")
            message = str((dialogue or {}).get("message") or "").strip()
            # The model may rephrase a topic on every reply.  A dialogue
            # thread is therefore the participant pair within the configured
            # window, not model-controlled topic text. Trade exchanges use a
            # separate pair thread so conversation and offer rounds do not
            # consume each other's budget.
            interaction_kind = "dialogue" if event.get("event_type") == "dialogue.completed" else "trade"
            thread_key = (interaction_kind, *pair)
            last_minute, rounds = self._interaction_rounds.get(thread_key, (minute, 0))
            if minute - last_minute >= self.interaction_thread_window_minutes:
                rounds = 0
            explicit_player_message = dialogue is not None and dialogue.get("origin") == "player"
            if (
                event.get("event_type") == "dialogue.completed"
                and not explicit_player_message
                and self._is_repeated_message(pair, minute, message)
            ):
                report.interaction_replies_suppressed += 1
                continue
            if rounds >= self.max_interaction_rounds:
                report.interaction_replies_suppressed += 1
                report.interaction_threads_closed += 1
                continue
            self._interaction_rounds[thread_key] = (minute, rounds + 1)
            if message:
                self._interaction_messages.setdefault(pair, []).append((minute, message))
                self._interaction_messages[pair] = self._interaction_messages[pair][-8:]
            self._persist_scheduler_state()
            interaction = {
                "from": self._visible_name(actor_id),
                "topic": topic,
                "message": message or "The other participant opened this topic without a written message.",
                "origin": "player" if explicit_player_message else "agent",
            }
            interaction_task = "instant:player_dialogue.completed" if explicit_player_message and interaction_kind == "dialogue" else f"instant:{event['event_type']}"
            turn = AgentEngine(self.platform, provider).run(
                role="agent_decision",
                actor_id=recipient_id,
                task_type=interaction_task,
                interaction=interaction,
            )
            self._observe_turn(turn, recipient_id)
            report.decisions += 1
            self._record_turn(report, turn.result, recipient_id)
            if turn.result.get("failed_check") == "llm_response":
                # A provider outage must not consume the interaction event;
                # it remains eligible after the scheduler backoff.
                self._interaction_events_seen.discard(event_id)
                self.platform.store.repositories.events.release_interaction(event_id)
                self._persist_scheduler_state()
                return False
            if turn.result.get("status") == "accepted" and (
                turn.result.get("interaction_committed")
                or turn.result.get("trade_committed")
                or any(key in turn.result for key in ("dialogue_id", "offer_id", "trade_status"))
            ):
                self._interaction_not_before[recipient_id] = minute + self.interaction_cooldown_minutes
                self._interaction_not_before[actor_id] = minute + self.interaction_cooldown_minutes
            if turn.result.get("status") == "wait":
                report.waits += 1

        # Replies append new zero-minute events after the snapshot above was
        # read. Drain them now at the same world minute. The configured round
        # limit guarantees termination, while a provider error returns above
        # with the triggering event still persisted for the next run.
        if not self._provider_cooling_down(provider) and any(
            int(event.get("event_id", 0)) not in self._interaction_events_seen
            and event.get("event_type") in {"dialogue.completed", "trade.offer_created", "trade.countered"}
            for event in self.platform.store.repositories.events.recent_events(limit=100)
        ):
            return self._run_instant_interactions(report)
        return not self._provider_cooling_down(provider)

    @staticmethod
    def _message_similarity(left: str, right: str) -> float:
        """Cheap language-independent overlap check for repeated replies."""

        def units(value: str) -> set[str]:
            text = unicodedata.normalize("NFKC", str(value or "")).casefold()
            text = re.sub(r"\s+", "", text)
            words = set(re.findall(r"[a-z0-9]+", text))
            cjk = "".join(re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", text))
            words.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
            return words

        if not left or not right:
            return 0.0
        if unicodedata.normalize("NFKC", left).casefold() == unicodedata.normalize("NFKC", right).casefold():
            return 1.0
        a, b = units(left), units(right)
        return len(a & b) / max(1, len(a | b))

    def _is_repeated_message(self, pair: tuple[str, ...], minute: int, message: str) -> bool:
        if not message:
            return False
        recent = self._interaction_messages.get(tuple(pair), [])
        for previous_minute, previous in recent:
            if minute - previous_minute <= self.interaction_thread_window_minutes and self._message_similarity(message, previous) >= 0.72:
                return True
        return False

    def _is_player_controlled(self, agent_id: str) -> bool:
        """Return True when a human player owns this character's decisions."""

        try:
            agent = self.platform.store.repositories.entities.get_agent(str(agent_id))
        except (KeyError, TypeError):
            return False
        state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
        role = str(agent.get("role") or state.get("role") or "").casefold()
        controller = str(agent.get("controller") or state.get("controller") or "").casefold()
        return role == "player" or controller in {"manual", "player"}

    def _visible_name(self, actor_id: str) -> str:
        try:
            return str(self.platform.store.repositories.entities.get_entity(actor_id).get("name", actor_id))
        except KeyError:
            return actor_id

    def _run_idle_decisions(self, report: SimulationReport) -> None:
        provider = self.providers.get("agent_decision")
        minute = int(self.platform.store.repositories.metadata.get("world_minute", 0))
        self._refresh_decision_wakes(minute)
        for agent in self.platform.store.repositories.entities.list_agents():
            controller = str(agent.get("controller") or "llm").casefold()
            # Background population is still executed through the same
            # validated BT/Action runtime, but it does not consume an LLM
            # decision or reflection budget.  Important characters keep the
            # default LLM controller and their full memory/learning loop.
            if controller in {"bt", "behavior_tree", "bt_only"}:
                if self._run_bt_only_decision(agent, report, minute):
                    continue
                continue
            if controller in {"manual", "player"}:
                # Human-controlled characters act only through player intents;
                # the autonomous runtime must not spend an LLM turn or overrule them.
                continue
            if provider is None or self._provider_cooling_down(provider):
                return
            agent_id = agent["id"]
            state = agent.get("initial_state", {}) or {}
            # An unconscious or dead character cannot act, so it is not asked for a turn.
            if state.get("status") in BUSY_STATUSES or is_inactive(state) or minute < max(self._decision_not_before.get(agent_id, 0), self._llm_not_before(agent_id), self._interaction_not_before.get(agent_id, 0)):
                continue
            outcomes = self.platform.store.repositories.events.recent_outcomes_for_actor(agent_id, limit=1)
            if outcomes and outcomes[0].get("event_type") == "behavior.failed":
                event_id = int(outcomes[0].get("event_id", 0))
                if self._failure_seen.get(agent_id) != event_id:
                    self._failure_seen[agent_id] = event_id
                    self._decision_not_before[agent_id] = minute + 60
                    continue
            turn = AgentEngine(self.platform, provider).run(role="agent_decision", actor_id=agent_id, task_type="agent_plan_selection")
            self._observe_turn(turn, agent_id)
            report.decisions += 1
            self._record_turn(report, turn.result, agent_id)
            if self._provider_cooling_down(provider):
                return
            if turn.result.get("status") == "wait":
                report.waits += 1
                streak = self._wait_streak.get(agent_id, 0) + 1
                self._wait_streak[agent_id] = streak
                empty_catalog = not (turn.initial_context.get("executable_behaviors") or turn.initial_context.get("open_trade_offers"))
                delay = self._empty_catalog_delay(minute) if empty_catalog else self._wait_delay(streak)
                self._decision_not_before[agent_id] = minute + delay
            elif turn.result.get("status") == "accepted":
                self._llm_error_count.pop(agent_id, None)
                self._wait_streak.pop(agent_id, None)
                if turn.result.get("interaction_committed") or turn.result.get("trade_committed") or any(key in turn.result for key in ("dialogue_id", "offer_id", "trade_status")):
                    self._interaction_not_before[agent_id] = minute + self.interaction_cooldown_minutes
                # If a selected BT fails at its first boundary, avoid an
                # unbounded same-minute retry loop while preserving prompt
                # driven replanning at the next safe boundary.
                self._decision_not_before[agent_id] = minute + 1
            else:
                # A rejected turn has already consumed its bounded ReAct
                # budget. Let the world advance before trying again.
                self._decision_not_before[agent_id] = minute + self.wait_cooldown_minutes
            self._persist_scheduler_state()

    def _run_bt_only_decision(self, agent: dict[str, Any], report: SimulationReport, minute: int) -> bool:
        """Run one configured full-BT choice for a non-LLM background actor.

        The controller only chooses among the current executable catalog.  It
        never bypasses AgentIntentGateway, so resource reservations, travel,
        outcomes, memories and world events remain identical to an important
        NPC's selected behavior.  ``bt_priority`` is optional declarative
        ordering; without it the platform's visible catalog order is used.
        """
        agent_id = str(agent.get("id") or "")
        state = agent.get("initial_state", {}) or {}
        if not agent_id or state.get("status") in BUSY_STATUSES or is_inactive(state):
            return True
        if minute < int(self._decision_not_before.get(agent_id, 0) or 0):
            return True
        view = self.platform.decision_view(agent_id)
        options = [item for item in (view.get("executable_behaviors") or []) if item.get("ref")]
        # A generic catalog behavior is executable only when its required
        # visible choice exists.  Background controllers cannot invent a
        # recipe, offer or carried consumable, so skip empty option sets
        # instead of submitting a guaranteed parameter rejection.
        def has_required_choices(item: dict[str, Any]) -> bool:
            options_by_key = item.get("parameter_options") or {}
            for spec in item.get("parameters") or []:
                match = re.match(r"(\w+)\s*\(required", str(spec))
                if match and not options_by_key.get(match.group(1)):
                    return False
            return True
        options = [item for item in options if has_required_choices(item)]
        if not options:
            self._decision_not_before[agent_id] = minute + self._empty_catalog_delay(minute)
            return True
        priority = [str(item) for item in (agent.get("bt_priority") or [])]
        ranked = {str(item.get("ref", "")).removeprefix("behavior:"): item for item in options}
        urgency = {
            str(item.get("id")): float(item.get("urgency", 0) or 0)
            for item in (view.get("drives") or [])
            if isinstance(item, dict) and item.get("id")
        }
        scored = []
        for index, item in enumerate(options):
            tags = [str(tag) for tag in (item.get("drive_tags") or [])]
            score = max((urgency.get(tag, 0.0) for tag in tags), default=0.0)
            behavior_id = str(item.get("ref", "")).removeprefix("behavior:")
            priority_index = priority.index(behavior_id) if behavior_id in priority else len(priority)
            scored.append((score, -priority_index, -index, item))
        # A critical need is a hard tie breaker for background actors: their
        # selection remains declarative, but they will eat/drink before doing
        # another optional trip when a survival action is executable.
        selected = max(scored, key=lambda value: value[:3])[3]
        if not any(score > 0.02 for score, *_ in scored):
            configured = next((ranked[item] for item in priority if item in ranked), None)
            if configured is not None:
                selected = configured
            else:
                # With no urgent drive and no configured priority, the first
                # catalog entry is usually travel.  Pacing to the same place
                # every minute starves configured work such as gathering.
                # Prefer a stationary behavior when one is executable.
                stationary = [
                    item for score, _priority, _index, item in scored
                    if "movement" not in {str(tag) for tag in (item.get("goal_tags") or [])}
                ]
                if stationary:
                    selected = stationary[0]
        parameters = dict(selected.get("parameter_values") or {})
        for key, choices in (selected.get("parameter_options") or {}).items():
            if key in parameters or not isinstance(choices, list) or not choices:
                continue
            first = choices[0] if isinstance(choices[0], dict) else {}
            value = first.get("value") or first.get("name")
            if value:
                parameters[str(key)] = str(value)
        result = self.platform.submit_behavior(BehaviorIntent(
            actor_id=agent_id,
            behavior_ref=str(selected["ref"]),
            target={},
            parameters=parameters,
            reason="Configured background controller selected the highest-priority executable behavior.",
            confidence=1.0,
            intent_id=f"bt:{agent_id}:{minute}:{selected['ref']}",
        ))
        report.bt_decisions += 1
        self._record_turn(report, result, agent_id)
        if result.get("status") == "accepted":
            self._decision_not_before[agent_id] = minute + 1
        else:
            self._decision_not_before[agent_id] = minute + max(1, self.wait_cooldown_minutes)
        self._persist_scheduler_state()
        return True

    def _wait_delay(self, streak: int) -> int:
        policy = (self.platform.store.repositories.metadata.get("rules", {}) or {}).get("decision_policy", {}) or {}
        values = policy.get("wait_backoff_minutes") or [self.wait_cooldown_minutes]
        delays = [max(1, int(value)) for value in values if isinstance(value, (int, float)) and int(value) > 0]
        if not delays:
            delays = [self.wait_cooldown_minutes]
        return delays[min(max(1, int(streak)) - 1, len(delays) - 1)]

    def _empty_catalog_delay(self, minute: int) -> int:
        """Retry at the next likely catalog change without another empty LLM call."""

        policy = (self.platform.store.repositories.metadata.get("rules", {}) or {}).get("decision_policy", {}) or {}
        maximum = max(1, int(policy.get("empty_catalog_retry_minutes", 360) or 360))
        phase_change = minutes_until_next_day_phase(self.platform.store, minute)
        return min(maximum, phase_change) if phase_change is not None else maximum

    def _refresh_decision_wakes(self, minute: int) -> None:
        """Clear wait backoff for configured events without semantic branches."""

        policy = (self.platform.store.repositories.metadata.get("rules", {}) or {}).get("decision_policy", {}) or {}
        wake_events = {str(value) for value in (policy.get("wake_events") or []) if str(value)}
        rows = self.platform.store.connection.execute(
            "SELECT event_id,event_type,actor_id,data_json FROM event_log WHERE event_id>? ORDER BY event_id LIMIT 2000",
            (int(self._decision_event_cursor),),
        ).fetchall()
        if not rows:
            return
        known_agents = {str(item["id"]) for item in self.platform.store.repositories.entities.list_agents()}
        for row in rows:
            self._decision_event_cursor = max(self._decision_event_cursor, int(row["event_id"]))
            if str(row["event_type"]) not in wake_events:
                continue
            try:
                data = json.loads(row["data_json"] or "{}")
            except (TypeError, ValueError):
                data = {}
            candidates = [row["actor_id"]]
            for key in ("target_id", "recipient_id", "listener_id", "patient_id", "counterparty_id"):
                candidates.append(data.get(key))
            audience = data.get("audience")
            if isinstance(audience, list):
                candidates.extend(audience)
            assigned_to = data.get("assigned_to")
            if isinstance(assigned_to, list):
                candidates.extend(assigned_to)
            resolved_agents: set[str] = set()
            for value in candidates:
                if value is None:
                    continue
                visible_or_id = str(value)
                canonical = self.platform.targets.canonical_id(visible_or_id) or visible_or_id
                if canonical in known_agents:
                    resolved_agents.add(canonical)
            for actor_id in resolved_agents:
                self._decision_not_before[actor_id] = min(minute, self._decision_not_before.get(actor_id, minute))
                self._wait_streak.pop(actor_id, None)

    def _plan_next_structure(self, report: SimulationReport) -> None:
        """Ask the LLM Director for the next target before the tick writes one.

        The deterministic Director still runs inside ``step``.  A provider
        failure or an invalid target therefore falls back to the configured
        policy instead of stopping the world.
        """

        if self.director_policy != "llm":
            return
        provider = self.providers.get("director")
        if provider is None or self._provider_cooling_down(provider):
            return
        minute = int(self.platform.store.repositories.metadata.get("world_minute", 0)) + 1
        try:
            plan = self.platform.plan_structure_with_provider(provider, minute)
        except Exception:
            report.director_errors += 1
            return
        if not isinstance(plan, dict):
            return
        plan_id = str(plan.get("plan_id") or "")
        if plan_id and plan_id != self._last_director_plan_id:
            self._last_director_plan_id = plan_id
            report.director_plans += 1

    def _run_due_evolution(self, report: SimulationReport) -> None:
        god_runs = 0
        for task in list(self.platform.ready_evolution_tasks()):
            role = task.get("role")
            if role == "god" and god_runs >= self.max_ready_god_tasks:
                # Bound one scheduler pass.  The remaining ready tasks stay
                # queued for the next minute instead of fanning out an
                # unbounded number of provider calls at once.
                continue
            provider = self.providers.get(role)
            actor_id = task.get("actor_id") or (
                "god" if role == "god" else "story" if role == "story" else None
            )
            if provider is None or actor_id is None:
                if role == "story":
                    # Story feedback is asynchronous. A missing provider must
                    # leave the task pending without freezing the world.
                    continue
                continue
            if self._provider_cooling_down(provider):
                return
            claimed = self.platform.claim_evolution_task(task["task_id"])
            if claimed is None:
                continue
            if role == "god":
                god_runs += 1
            task_type = str(task.get("task_type", "evolution"))
            if task.get("trigger_id"):
                task_type = f"trigger:{task.get('trigger_id')}"
            # WorldStore returns evolution data flattened into the task view.
            # Some API callers may wrap it under ``data``; accept both without
            # dropping the causal source event at the Agent boundary.
            task_data = task.get("data") if isinstance(task.get("data"), dict) else task
            source_events = [item for item in (task_data.get("source_events") or []) if isinstance(item, dict)]
            source_event_id = task_data.get("source_event_id")
            if source_event_id is None and source_events:
                source_event_id = source_events[-1].get("event_id")
            turn = AgentEngine(self.platform, provider).run(
                role=role,
                actor_id=actor_id,
                task_type=task_type,
                source_event_id=int(source_event_id) if source_event_id is not None else None,
                source_events=source_events,
            )
            self._observe_turn(turn, actor_id)
            if role == "god":
                report.god_sessions += 1
            elif role == "story":
                report.story_sessions += 1
            elif role == "agent_decision":
                report.decisions += 1
            else:
                report.reflections += 1
            self._record_turn(report, turn.result, actor_id)
            if turn.result.get("status") in {"accepted", "wait"}:
                self.platform.complete_evolution_task(task["task_id"])
            else:
                provider_cooldown = self._provider_cooling_down(provider)
                if provider_cooldown:
                    retry_delay = 0
                else:
                    retry_delay = self._evolution_retry_delay(task, turn.result)
                world_minute = int(self.platform.store.repositories.metadata.get("world_minute", 0))
                self.platform.retry_evolution_task(
                    task["task_id"],
                    world_minute + retry_delay,
                    str(turn.result.get("message") or turn.result.get("failed_check") or "evolution session rejected"),
                )
            if self._provider_cooling_down(provider):
                return

    def _evolution_retry_delay(self, task: dict[str, Any], result: dict[str, Any]) -> int:
        """Retry a failed evolution task without losing its Director window.

        A budget-exhausted structural session is retried at the Director
        cadence, not after a full day, because the season plan may expire much
        sooner.  The delay is also capped at half the remaining plan window so
        one retry still fits before expiry.  Other rejections keep the existing
        one-hour exponential backoff.
        """

        failed_check = str(result.get("failed_check") or "")
        if failed_check == "llm_response":
            return self.llm_error_cooldown_minutes
        retry_count = int(task.get("retry_count", 0) or 0)
        task_data = task.get("data") if isinstance(task.get("data"), dict) else task
        plan = task_data.get("season_plan") if isinstance(task_data, dict) else None
        plan = plan if isinstance(plan, dict) else None
        if failed_check == "budget_exhausted":
            base_delay = max(1, self.director_cadence_minutes) if plan else 1440
        else:
            base_delay = 60
        delay = min(10080, base_delay * (2 ** min(retry_count, 6)))
        if plan:
            minute = int(self.platform.store.repositories.metadata.get("world_minute", 0))
            remaining = int(plan.get("expires_minute", 0) or 0) - minute
            if remaining > 0:
                delay = min(delay, max(1, remaining // 2))
        return delay

    @staticmethod
    def _cooldown_seconds(provider: Any) -> float:
        value = getattr(provider, "cooldown_remaining", 0.0)
        try:
            remaining = value() if callable(value) else value
            return max(0.0, float(remaining or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _provider_cooling_down(cls, provider: Any) -> bool:
        """Avoid issuing more calls while a provider circuit breaker is open."""

        return cls._cooldown_seconds(provider) > 0.0

    def _llm_not_before(self, agent_id: str) -> int:
        return self._llm_retry_until.get(agent_id, 0)

    def _record_turn(self, report: SimulationReport, result: dict[str, Any], actor_id: str) -> None:
        if result.get("status") != "rejected":
            return
        report.rejected_turns += 1
        if result.get("failed_check") != "llm_response":
            return
        report.llm_errors += 1
        if self._cooling_provider_roles():
            self._provider_retry_pending = True
            return
        self._record_llm_failure(actor_id, int(self.platform.store.repositories.metadata.get("world_minute", 0)))

    def _record_llm_failure(self, actor_id: str, minute: int) -> None:
        count = self._llm_error_count.get(actor_id, 0) + 1
        self._llm_error_count[actor_id] = count
        delay = min(1440, self.llm_error_cooldown_minutes * (2 ** min(count - 1, 4)))
        self._llm_retry_until[actor_id] = minute + delay

    def _diversity(self, report: SimulationReport) -> dict[str, Any]:
        behavior_count = len(report.selected_behaviors)
        event_count = len(report.event_types)
        agents = self.platform.store.repositories.entities.list_agents()
        locations = {str((agent.get("initial_state") or {}).get("location_id")) for agent in agents}
        return {
            "unique_behaviors": behavior_count,
            "unique_event_types": event_count,
            "agents_with_distinct_locations": len(locations - {"None"}),
            "memories": int(self.platform.store.connection.execute("SELECT COUNT(*) AS n FROM memory_item").fetchone()["n"]),
            "relationships": int(self.platform.store.connection.execute("SELECT COUNT(*) AS n FROM relationship").fetchone()["n"]),
        }

    def _degeneration_flags(self, report: SimulationReport) -> list[str]:
        flags: list[str] = []
        if report.decisions and not report.selected_behaviors:
            flags.append("decisions_without_any_behavior_execution")
        if report.decisions >= 10 and len(report.selected_behaviors) == 1:
            flags.append("single_behavior_dominance")
        if report.decisions > max(20, len(self.platform.store.repositories.entities.list_agents()) * 20) and len(report.selected_behaviors) < max(2, len(self.platform.store.repositories.entities.list_agents()) // 2):
            flags.append("excessive_replanning_or_low_execution")
        if report.event_types.get("trade.completed", 0) < 0:
            flags.append("invalid_trade_count")
        vital = (self.platform.store.repositories.metadata.get("rules", {}) or {}).get("vital_attribute")
        if vital:
            for agent in self.platform.store.repositories.entities.list_agents():
                state = agent.get("initial_state", {}) or {}
                if float(state.get(str(vital), 0) or 0) <= 0:
                    flags.append(f"vital_depleted:{agent['id']}")
        return flags
