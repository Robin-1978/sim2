"""Derived stagnation signal for configuration-driven world evolution.

Every other evolution wake is endogenous: Story and God are woken by events the
characters produce.  A world that has run out of productive behaviors therefore
stops producing the very events that would wake its evolution roles.  The
signal in this module is derived from state instead of events: once per
configured window Platform compares each character's cumulative set of
executable options with the previous window, and when no character gained a
new option it publishes ``world.stagnation`` with the visible reasons.  Worlds
decide who reacts through an ordinary ``wakes`` entry on that event.

Building a decision view costs more as the population grows, so one window
evaluates at most ``max_characters`` characters.  A larger roster is covered
by a deterministic rotating cohort; every character keeps its own cumulative
frontier between windows.
"""

from __future__ import annotations

from typing import Any

STAGNATION_STATE_KEY = "stagnation_state"
EVENT_CHARACTER_LIMIT = 12
# The event lists at most EVENT_CHARACTER_LIMIT characters, so evaluating that
# many per window is the default: what the reviewer reads is what was checked.
DEFAULT_COHORT_SIZE = EVENT_CHARACTER_LIMIT
EVENT_OPTION_LIMIT = 8
EVENT_BLOCKED_LIMIT = 4
SUMMARY_LIMIT = 1100
INACTIVE_STATUSES = {"dead", "unconscious"}
DEFAULT_PROGRESS_EVENTS = (
    "research.progressed",
    "research.completed",
    "exploration.completed",
    "capability.updated",
    "institution.revised",
    "world.change.world_property",
    "world.change.rule",
    "world.change.relationship",
    "world.change.faction",
)


def _duration_label(minutes: int) -> str:
    for unit, size in (("day", 1440), ("hour", 60)):
        if minutes >= size and minutes % size == 0:
            count = minutes // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{minutes} minutes"


class StagnationRuntimeMixin:
    """Publish ``world.stagnation`` when the world stops creating options."""

    def _stagnation_config(self) -> dict[str, Any] | None:
        root = self.store.get_meta("evolution", {}) or {}
        config = root.get("evolution", root) if isinstance(root, dict) else {}
        raw = config.get("stagnation") if isinstance(config, dict) else None
        if not isinstance(raw, dict):
            return None
        window = int(raw.get("window_minutes", 0) or 0)
        if window <= 0:
            return None
        progress_events = raw.get("progress_event_types") or list(DEFAULT_PROGRESS_EVENTS)
        if isinstance(progress_events, str):
            progress_events = [progress_events]
        return {
            "window": window,
            "max_interval": max(window, int(raw.get("max_interval_minutes", window) or window)),
            "max_characters": max(1, int(raw.get("max_characters", DEFAULT_COHORT_SIZE) or DEFAULT_COHORT_SIZE)),
            "progress_event_types": [str(item) for item in progress_events if str(item)],
        }

    @staticmethod
    def _stagnation_cohort(active: list[dict[str, Any]], window_index: int, size: int) -> list[dict[str, Any]]:
        """Pick the characters evaluated in this window.

        A roster within ``size`` is evaluated completely.  A larger one is
        walked in id order, ``size`` characters per window, wrapping around, so
        the choice is reproducible and every character is revisited.
        """

        if len(active) <= size:
            return active
        start = (window_index * size) % len(active)
        return [active[(start + offset) % len(active)] for offset in range(size)]

    def _emit_stagnation_signal(self, minute: int, *, agents: list[dict[str, Any]] | None = None) -> int | None:
        """Evaluate one window boundary and return the published event id, if any.

        Growth means a character gained an executable option it never had
        before (a behavior, or a parameter option such as a new research
        source) or a new character appeared.  The first evaluation only
        records the baseline.  While stagnation persists the event is repeated
        at doubling spacing (1, 2, 4, ... windows) up to ``max_interval``, so a
        consumer that waited is asked again without a wake on every window.
        When the roster is larger than ``max_characters`` only a rotating
        cohort is evaluated per window, and a character met for the first time
        records its baseline instead of counting as growth.
        """

        config = self._stagnation_config()
        if config is None or minute <= 0 or minute % config["window"]:
            return None
        window = config["window"]
        cap = max(1, config["max_interval"] // window)
        roster = agents if agents is not None else self.store.repositories.entities.list_agents()
        active = sorted(
            (
                agent for agent in roster
                if str((agent.get("initial_state") or {}).get("status", "idle")) not in INACTIVE_STATUSES
            ),
            key=lambda agent: str(agent["id"]),
        )
        sampled = len(active) > config["max_characters"]
        cohort = self._stagnation_cohort(active, minute // window, config["max_characters"])
        state = dict(self.store.get_meta(STAGNATION_STATE_KEY, {}) or {})
        baseline = "frontier" not in state
        progress_cursor = int(state.get("progress_event_id", 0) or 0)
        progress_types = list(config.get("progress_event_types") or DEFAULT_PROGRESS_EVENTS)
        placeholders = ",".join("?" for _ in progress_types)
        progress_rows = self.store.connection.execute(
            f"SELECT event_id FROM event_log WHERE event_id>? AND event_type IN ({placeholders}) ORDER BY event_id",
            (progress_cursor, *progress_types),
        ).fetchall()
        progressed = bool(progress_rows)
        if progress_rows:
            progress_cursor = int(progress_rows[-1]["event_id"])
        frontier = {str(key): set(value) for key, value in (state.get("frontier") or {}).items()}
        bt_defs = {str(item["id"]): item for item in self.store.repositories.entities.get_definitions("bt_def")}
        snapshots = {str(agent["id"]): self.view.option_snapshot(str(agent["id"]), bt_defs=bt_defs) for agent in cohort}
        grew = False
        for agent_id, snapshot in snapshots.items():
            options = set(snapshot["options"])
            known = frontier.get(agent_id)
            if known is None:
                # Sampling meets most characters long after the baseline, so
                # the first sight only records them; a fully evaluated roster
                # treats a newcomer as growth.
                grew = grew or not sampled
            elif options - known:
                grew = True
            frontier[agent_id] = (known or set()) | options

        windows = int(state.get("stagnant_windows", 0) or 0)
        next_emit_at = int(state.get("next_emit_at", 1) or 1)
        gap = int(state.get("gap", 1) or 1)
        last_novel = int(state.get("last_novel_minute", minute) or minute)
        event_id: int | None = None
        if baseline or grew or progressed:
            windows, next_emit_at, gap, last_novel = 0, 1, 1, minute
        else:
            windows += 1
            if windows >= next_emit_at:
                event_id = self._publish_stagnation(minute, windows, window, last_novel, active, snapshots)
                next_emit_at = windows + gap
                gap = min(gap * 2, cap)
        self.store.set_meta(STAGNATION_STATE_KEY, {
            "frontier": {key: sorted(value) for key, value in frontier.items()},
            "stagnant_windows": windows,
            "next_emit_at": next_emit_at,
            "gap": gap,
            "last_novel_minute": last_novel,
            "last_evaluated_minute": int(minute),
            "progress_event_id": progress_cursor,
            "progressed_last_window": progressed,
        })
        return event_id

    def _publish_stagnation(
        self,
        minute: int,
        windows: int,
        window: int,
        last_novel: int,
        active: list[dict[str, Any]],
        snapshots: dict[str, dict[str, Any]],
    ) -> int:
        names = {str(agent["id"]): str(agent.get("name") or agent["id"]) for agent in active}
        characters: list[dict[str, Any]] = []
        sentences = [f"No character gained a new executable option for {_duration_label(windows * window)} (last growth at minute {last_novel})."]
        for agent_id in sorted(snapshots)[:EVENT_CHARACTER_LIMIT]:
            snapshot = snapshots[agent_id]
            options = list(snapshot["choices"])[:EVENT_OPTION_LIMIT]
            blocked = list(snapshot["blocked"])[:EVENT_BLOCKED_LIMIT]
            characters.append({"name": names[agent_id], "options": options, "blocked": blocked})
            sentence = f"{names[agent_id]} can: {'; '.join(options) or 'nothing'}."
            if blocked:
                sentence += " Blocked: " + "; ".join(
                    f"{item['choice']} (needs {', '.join(item['needs'])})" if item.get("needs") else str(item["choice"])
                    for item in blocked
                ) + "."
            sentences.append(sentence)
        # Characters outside the evaluated cohort and those beyond the event
        # listing limit are both left out of the evidence.
        omitted = max(0, len(active) - min(len(snapshots), EVENT_CHARACTER_LIMIT))
        if omitted:
            sentences.append(f"{omitted} more character(s) omitted.")
        summary = " ".join(sentences)
        if len(summary) > SUMMARY_LIMIT:
            summary = summary[: SUMMARY_LIMIT - 3] + "..."
        payload = {
            "summary": summary,
            "window_minutes": int(window),
            "stagnant_windows": int(windows),
            "stagnant_minutes": int(windows * window),
            "last_novel_minute": int(last_novel),
            "progress_event_types": list(self._stagnation_config().get("progress_event_types") or DEFAULT_PROGRESS_EVENTS) if self._stagnation_config() else [],
            "characters": characters,
            "omitted_characters": omitted,
        }
        return self._context.services["events"].publish("world.stagnation", None, int(minute), payload, wake="configured")
