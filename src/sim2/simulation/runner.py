from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Callable

from ..agents.engine import AgentEngine, AgentTurn
from ..platform.runtime import Platform


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
        }


class SimulationRunner:
    """Drive Platform minutes and invoke Agent phases only at boundaries."""

    def __init__(self, platform: Platform, providers: dict[str, Any], *, wait_cooldown_minutes: int = 30, llm_error_cooldown_minutes: int = 60, max_interaction_rounds: int | None = None, interaction_cooldown_minutes: int | None = None, interaction_thread_window_minutes: int | None = None, turn_observer: Callable[[AgentTurn, str], None] | None = None):
        self.platform = platform
        self.providers = providers
        interaction_policy = (self.platform.store.get_meta("rules", {}) or {}).get("interaction_policy", {}) or {}
        self.wait_cooldown_minutes = max(1, int(wait_cooldown_minutes))
        self.llm_error_cooldown_minutes = max(1, int(llm_error_cooldown_minutes))
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
        self._interaction_topic_families: dict[tuple[str, str], list[tuple[str, set[str]]]] = {}
        self._interaction_not_before: dict[str, int] = {}
        self.turn_observer = turn_observer

    def _observe_turn(self, turn: AgentTurn, actor_id: str) -> None:
        if self.turn_observer is not None:
            self.turn_observer(turn, actor_id)

    def run(self, minutes: int) -> SimulationReport:
        minutes = int(minutes)
        if minutes < 1:
            raise ValueError("minutes must be at least 1")
        start = int(self.platform.store.get_meta("world_minute", 0))
        report = SimulationReport(start, start + minutes)
        for _ in range(minutes):
            if self.platform.status().get("status") != "running":
                break
            # A completed discovery creates a Story task at the current
            # boundary. Resolve that feedback before the next world minute so
            # the story cannot lag one tick behind the fact it is meant to
            # interpret. Rejected or unavailable Story work pauses the world
            # without advancing time again.
            if any(task.get("role") == "story" for task in self.platform.ready_evolution_tasks()):
                self._run_due_evolution(report)
                if self.platform.status().get("status") != "running":
                    break
            self.platform.step()
            self._run_instant_interactions(report)
            self._run_due_evolution(report)
            self._run_idle_decisions(report)
        report.end_minute = int(self.platform.store.get_meta("world_minute", 0))
        report.event_types = self.platform.store.event_type_counts()
        report.selected_behaviors = self.platform.store.behavior_start_counts()
        report.diversity = self._diversity(report)
        report.degeneration_flags = self._degeneration_flags(report)
        return report

    def _run_instant_interactions(self, report: SimulationReport) -> None:
        """Dispatch zero-minute dialogue/trade events as bounded micro-turns."""

        provider = self.providers.get("agent_decision")
        if provider is None:
            return
        for event in reversed(self.platform.store.recent_events(limit=100)):
            event_id = int(event.get("event_id", 0))
            if event_id in self._interaction_events_seen:
                continue
            if event.get("event_type") not in {"dialogue.completed", "trade.offer_created", "trade.countered"}:
                continue
            self._interaction_events_seen.add(event_id)
            minute = int(self.platform.store.get_meta("world_minute", 0))
            actor_id = str(event.get("actor_id") or "")
            recipient_id = str(event.get("target_id") or event.get("recipient_id") or "")
            if not actor_id or not recipient_id or actor_id == recipient_id:
                continue
            pair = tuple(sorted((actor_id, recipient_id)))
            dialogue = None
            event_data = event.get("data") if isinstance(event.get("data"), dict) else event
            dialogue_id = event_data.get("dialogue_id") if isinstance(event_data, dict) else None
            if dialogue_id is not None:
                dialogue = self.platform.store.get_dialogue_exchange(dialogue_id)
            topic = str((dialogue or {}).get("topic") or event_data.get("topic") or "general")
            message = str((dialogue or {}).get("message") or "").strip()
            thread_key = (*pair, self._topic_family_key(pair, topic))
            last_minute, rounds = self._interaction_rounds.get(thread_key, (minute, 0))
            if minute - last_minute >= self.interaction_thread_window_minutes:
                rounds = 0
            if event.get("event_type") == "dialogue.completed" and self._is_repeated_message(pair, minute, message):
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
            interaction = {
                "from": self._visible_name(actor_id),
                "topic": topic,
                "message": message or "The other participant opened this topic without a written message.",
            }
            turn = AgentEngine(self.platform, provider).run(
                role="agent_decision",
                actor_id=recipient_id,
                task_type=f"instant:{event['event_type']}",
                interaction=interaction,
            )
            self._observe_turn(turn, recipient_id)
            report.decisions += 1
            self._record_turn(report, turn.result, recipient_id)
            if turn.result.get("status") == "accepted" and (
                turn.result.get("interaction_committed")
                or turn.result.get("trade_committed")
                or any(key in turn.result for key in ("dialogue_id", "offer_id", "trade_status"))
            ):
                self._interaction_not_before[recipient_id] = minute + self.interaction_cooldown_minutes
                self._interaction_not_before[actor_id] = minute + self.interaction_cooldown_minutes
            if turn.result.get("status") == "wait":
                report.waits += 1

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        text = unicodedata.normalize("NFKC", str(topic or "general")).casefold()
        text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
        return text[:120] or "general"

    @classmethod
    def _topic_components(cls, topic: str) -> set[str]:
        """Extract stable subject components without a world-specific list."""

        normalized = unicodedata.normalize("NFKC", str(topic or "general")).casefold()
        parts = [item for item in re.split(r"[与和及/、,:：&+]+", normalized) if item]
        return {re.sub(r"[\W_]+", "", item, flags=re.UNICODE) for item in parts if len(item) >= 2} or {cls._normalize_topic(normalized)}

    def _topic_family_key(self, pair: tuple[str, ...], topic: str) -> str:
        """Merge reordered or shared-subject labels into one conversation thread."""

        components = self._topic_components(topic)
        pair_key = tuple(pair)
        families = self._interaction_topic_families.setdefault(pair_key, [])
        for key, known in families:
            if components & known:
                return key
        key = self._normalize_topic(topic)
        families.append((key, components))
        return key

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

    def _visible_name(self, actor_id: str) -> str:
        try:
            return str(self.platform.store.get_entity(actor_id).get("name", actor_id))
        except KeyError:
            return actor_id

    def _run_idle_decisions(self, report: SimulationReport) -> None:
        provider = self.providers.get("agent_decision")
        if provider is None:
            return
        minute = int(self.platform.store.get_meta("world_minute", 0))
        for agent in self.platform.store.list_agents():
            agent_id = agent["id"]
            state = agent.get("initial_state", {}) or {}
            if state.get("status") in {"executing", "paused"} or minute < max(self._decision_not_before.get(agent_id, 0), self._llm_not_before(agent_id), self._interaction_not_before.get(agent_id, 0)):
                continue
            outcomes = self.platform.store.recent_outcomes_for_actor(agent_id, limit=1)
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
            if turn.result.get("status") == "wait":
                report.waits += 1
                self._decision_not_before[agent_id] = minute + self.wait_cooldown_minutes
            elif turn.result.get("status") == "accepted":
                self._llm_error_count.pop(agent_id, None)
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

    def _run_due_evolution(self, report: SimulationReport) -> None:
        for task in list(self.platform.ready_evolution_tasks()):
            role = task.get("role")
            provider = self.providers.get(role)
            actor_id = task.get("actor_id") or (
                "god" if role == "god" else "story" if role == "story" else None
            )
            if provider is None or actor_id is None:
                if role == "story":
                    self.platform.pause("story_agent_provider_missing")
                    return
                continue
            claimed = self.platform.claim_evolution_task(task["task_id"])
            if claimed is None:
                continue
            task_type = str(task.get("task_type", "evolution"))
            if task.get("trigger_id"):
                task_type = f"trigger:{task.get('trigger_id')}"
            task_data = task.get("data") if isinstance(task.get("data"), dict) else {}
            source_event_id = task_data.get("source_event_id")
            turn = AgentEngine(self.platform, provider).run(
                role=role,
                actor_id=actor_id,
                task_type=task_type,
                source_event_id=int(source_event_id) if source_event_id is not None else None,
            )
            self._observe_turn(turn, actor_id)
            if role == "god":
                report.god_sessions += 1
            elif role == "story":
                report.story_sessions += 1
            else:
                report.reflections += 1
            self._record_turn(report, turn.result, actor_id)
            if turn.result.get("status") in {"accepted", "wait"}:
                self.platform.complete_evolution_task(task["task_id"])
            else:
                retry_delay = self.llm_error_cooldown_minutes if turn.result.get("failed_check") == "llm_response" else 60
                self.platform.retry_evolution_task(
                    task["task_id"],
                    int(self.platform.store.get_meta("world_minute", 0)) + retry_delay,
                    str(turn.result.get("message") or turn.result.get("failed_check") or "evolution session rejected"),
                )
                if role == "story":
                    # A discovery result is a synchronization barrier. Keep
                    # world time frozen until the Story Agent can acknowledge
                    # it or Platform receives an operator resume decision.
                    self.platform.pause("story_agent_feedback_pending")
                    return

    def _llm_not_before(self, agent_id: str) -> int:
        return self._llm_retry_until.get(agent_id, 0)

    def _record_turn(self, report: SimulationReport, result: dict[str, Any], actor_id: str) -> None:
        if result.get("status") != "rejected":
            return
        report.rejected_turns += 1
        if result.get("failed_check") != "llm_response":
            return
        report.llm_errors += 1
        self._record_llm_failure(actor_id, int(self.platform.store.get_meta("world_minute", 0)))

    def _record_llm_failure(self, actor_id: str, minute: int) -> None:
        count = self._llm_error_count.get(actor_id, 0) + 1
        self._llm_error_count[actor_id] = count
        delay = min(1440, self.llm_error_cooldown_minutes * (2 ** min(count - 1, 4)))
        self._llm_retry_until[actor_id] = minute + delay

    def _diversity(self, report: SimulationReport) -> dict[str, Any]:
        behavior_count = len(report.selected_behaviors)
        event_count = len(report.event_types)
        agents = self.platform.store.list_agents()
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
        if report.decisions > max(20, len(self.platform.store.list_agents()) * 20) and len(report.selected_behaviors) < max(2, len(self.platform.store.list_agents()) // 2):
            flags.append("excessive_replanning_or_low_execution")
        if report.event_types.get("trade.completed", 0) < 0:
            flags.append("invalid_trade_count")
        vital = (self.platform.store.get_meta("rules", {}) or {}).get("vital_attribute")
        if vital:
            for agent in self.platform.store.list_agents():
                state = agent.get("initial_state", {}) or {}
                if float(state.get(str(vital), 0) or 0) <= 0:
                    flags.append(f"vital_depleted:{agent['id']}")
        return flags
