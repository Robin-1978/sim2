"""Small MUD-style text adapter over Platform.

The adapter is deliberately transport-neutral: tests and a terminal can call
the same ``execute`` method.  It never edits SQLite directly and observer mode
only exposes the public world projection.
"""

from __future__ import annotations

import shlex
from typing import Callable

from ..prompting.renderer import render_context
from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from .map_view import render_topology_map


def _bundle(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    if not text or text == "-":
        return result
    for token in text.split(","):
        item, separator, quantity = token.partition("=")
        if not separator or not item:
            raise ValueError("items must use item=quantity, separated by commas")
        value = float(quantity)
        if value <= 0:
            raise ValueError("trade quantities must be positive")
        result[item] = value
    return result


class TextWorldSession:
    """Execute short text commands for a player or a whole-world observer."""

    def __init__(self, platform: Platform, *, player_id: str | None = None, observer: bool = False):
        if not observer and not player_id:
            raise ValueError("player_id is required unless observer mode is enabled")
        self.platform = platform
        self.player_id = player_id
        self.observer = observer

    def execute(self, line: str) -> str:
        parts = shlex.split(line.strip())
        if not parts:
            return ""
        command = parts[0].lower()
        args = parts[1:]
        if command in {"quit", "exit"}:
            return "Goodbye."
        if command == "help":
            return self._help()
        if command in {"world", "observe"}:
            self._require_observer()
            return self._format_world(self.platform.observer_view())
        if command == "events":
            self._require_observer()
            limit = int(args[0]) if args else 20
            return self._format_events(self.platform.observer_view(event_limit=limit)["recent_events"])
        if command == "timeline":
            self._require_observer()
            limit = int(args[0]) if args else 50
            timeline = self.platform.timeline(limit=limit)
            return "\n".join(f"[{event['world_minute']}] {event['narrative']}" for event in timeline["events"]) or "No public timeline events."
        if command == "map":
            return render_topology_map(self.platform.map_view(viewer_id=self.player_id, observer=self.observer))
        if command == "pause":
            self._require_observer()
            return self._format_result(self.platform.pause("observer"))
        if command == "resume":
            self._require_observer()
            return self._format_result(self.platform.resume("observer"))
        if command in {"look", "status"}:
            return self._look()
        if command == "wait":
            if self.observer:
                raise ValueError("Observer mode is read-only; run the Platform runtime to advance the world.")
            if args:
                raise ValueError("wait takes no duration; issue one command per world minute")
            return self._format_result(self.platform.step())
        if command == "move":
            self._require_player()
            if len(args) != 1:
                raise ValueError("move <known_location_id>")
            return self._format_result(self.platform.submit_behavior(BehaviorIntent(
                actor_id=self.player_id or "",
                behavior_ref="behavior:travel",
                target={},
                parameters={"location_id": args[0]},
                reason="Move through the text interface.",
                confidence=1.0,
                intent_id=f"text-move:{self.player_id}:{self.platform.store.get_meta('world_minute', 0)}:{args[0]}",
            )))
        if command == "say":
            self._require_player()
            if len(args) < 3:
                raise ValueError("say <target_id> <topic> <message>")
            return self._format_result(self.platform.submit_dialogue(self.player_id or "", args[0], args[1], " ".join(args[2:])))
        if command == "offer":
            self._require_player()
            if len(args) != 3:
                raise ValueError("offer <target_id> <give:item=qty> <want:item=qty>")
            return self._format_result(self.platform.create_trade_offer(self.player_id or "", args[0], _bundle(args[1]), _bundle(args[2])))
        if command == "offers":
            self._require_player()
            offers = self.platform.store.list_trade_offers(self.player_id or "")
            return "No open offers." if not offers else "\n".join(self._format_offer(item) for item in offers)
        if command == "accept":
            self._require_player()
            if len(args) != 1:
                raise ValueError("accept <offer_id>")
            return self._format_result(self.platform.accept_trade_offer(self.player_id or "", args[0]))
        if command == "counter":
            self._require_player()
            if len(args) != 3:
                raise ValueError("counter <offer_id> <give:item=qty> <want:item=qty>")
            return self._format_result(self.platform.counter_trade_offer(self.player_id or "", args[0], _bundle(args[1]), _bundle(args[2])))
        if command == "reject":
            self._require_player()
            if len(args) != 1:
                raise ValueError("reject <offer_id>")
            return self._format_result(self.platform.reject_trade_offer(self.player_id or "", args[0]))
        raise ValueError(f"Unknown command: {command}. Type help.")

    def run(self, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print) -> None:
        output_fn(self.execute("help"))
        while True:
            try:
                line = input_fn("> ")
            except (EOFError, KeyboardInterrupt):
                output_fn("Goodbye.")
                return
            try:
                result = self.execute(line)
                output_fn(result)
                if result == "Goodbye.":
                    return
            except (TypeError, ValueError) as exc:
                output_fn(f"Error: {exc}")

    def _look(self) -> str:
        if self.observer:
            return self._format_world(self.platform.observer_view())
        view = self.platform.decision_view(self.player_id or "", role="agent_decision")
        return render_context(view, "Text interface view. Choose an action only through an allowed command.")

    def _require_player(self) -> None:
        if self.observer or not self.player_id:
            raise ValueError("This command is available only in player mode.")

    def _require_observer(self) -> None:
        if not self.observer:
            raise ValueError("Whole-world telemetry requires observer mode.")

    @staticmethod
    def _help() -> str:
        return "Commands: look, map, status, pause, resume, move <known_location_id>, say <target> <topic> <message>, offer <target> <give:item=qty> <want:item=qty>, offers, accept <offer_id>, counter <offer_id> <give:item=qty> <want:item=qty>, reject <offer_id>, wait, world/events/timeline [limit] in observer mode, quit."

    @staticmethod
    def _format_result(result: dict[str, object]) -> str:
        return "; ".join(f"{key}={value}" for key, value in result.items())

    @staticmethod
    def _format_offer(offer: dict[str, object]) -> str:
        return f"{offer.get('offer_id')}: from={offer.get('offerer_id')} give={offer.get('give')} want={offer.get('want')} status={offer.get('status')}"

    @staticmethod
    def _format_world(view: dict[str, object]) -> str:
        lines = [f"World minute {view.get('world_time')} | world={view.get('world_id')}", f"Properties: {view.get('world_properties')}", "Characters:"]
        for agent in view.get("agents", []):
            lines.append(f"- {agent.get('name')} ({agent.get('role')}): {agent.get('status')} at {agent.get('location')}; behavior={agent.get('active_behavior') or 'none'}; remaining={agent.get('remaining_minutes', 0)}")
        lines.append(f"Entities: {view.get('entity_counts')}")
        return "\n".join(lines)

    @staticmethod
    def _format_events(events: list[dict[str, object]]) -> str:
        if not events:
            return "No public events."
        return "\n".join(f"[{event.get('world_minute')}] {event.get('event_type')} actor={event.get('actor_id')} result={event.get('result') or event.get('summary') or ''}" for event in events)
