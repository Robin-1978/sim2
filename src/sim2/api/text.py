"""Readable MUD-style text client over Platform.

``execute`` returns plain text so the same commands work in a terminal, a test
or a script. The client reads projected views and submits intents to Platform;
it never edits SQLite directly.
"""

from __future__ import annotations

import json
import shlex
from typing import Any, Callable

from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from .map_view import render_topology_map
from .service import build_player_view


_COMMAND_HELP = {
    "look": "Show the world clock, your location, goal, needs, routes and actions.",
    "status": "Show the current world, runtime and character status.",
    "actions": "List the numbered actions available right now.",
    "do": "do <n|behavior_ref> [key=value ...] - submit a listed action through Platform.",
    "go": "go <location_id|name|n> - travel to a connected known location.",
    "say": "say <target> <message...> - speak to a nearby character by name or ID; topic defaults to general.",
    "offer": "offer <target_id> <give:item=qty> <want:item=qty> - propose a trade.",
    "offers": "List your open trade offers.",
    "accept": "accept <offer_id> - accept a trade offer addressed to you.",
    "counter": "counter <offer_id> <give:item=qty> <want:item=qty> - counter an offer.",
    "reject": "reject <offer_id> - reject a trade offer.",
    "wait": "wait [minutes] - advance the world while no runtime owns the clock.",
    "map": "Render the known location topology and current occupants.",
    "timeline": "timeline [limit] - read the public third-person story timeline.",
    "events": "events [limit] - observer mode; show compact public events.",
    "world": "world - observer mode; show the whole-world dashboard.",
    "pause": "pause - observer mode; stop world advancement.",
    "resume": "resume - observer mode; resume world advancement.",
    "step": "step - observer mode; advance exactly one world minute.",
    "evolution": "evolution - observer mode; list ready evolution tasks.",
    "metrics": "metrics - observer mode; show runtime counters.",
    "help": "help [command] - show the command list or one command.",
    "quit": "quit - leave the session.",
}


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


def _assignments(args: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for token in args:
        key, separator, raw = token.partition("=")
        if not separator or not key:
            raise ValueError("parameters must use key=value")
        lowered = raw.lower()
        if lowered in {"true", "false"}:
            values[key] = lowered == "true"
        elif lowered in {"null", "none"}:
            values[key] = None
        else:
            try:
                values[key] = int(raw)
            except ValueError:
                try:
                    values[key] = float(raw)
                except ValueError:
                    values[key] = raw
    return values


def _scalar(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _meter(value: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(float(value) / 100 * width))))
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def _quantity(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


class TextWorldSession:
    """Execute short text commands for a player or a whole-world observer."""

    def __init__(self, platform: Platform, *, player_id: str | None = None, observer: bool = False):
        if not observer and not player_id:
            raise ValueError("player_id is required unless observer mode is enabled")
        self.platform = platform
        self.player_id = player_id
        self.observer = observer
        self._command_seq = 0

    def execute(self, line: str) -> str:
        parts = shlex.split(line.strip())
        if not parts:
            return ""
        command = parts[0].lower()
        args = parts[1:]
        if command in {"quit", "exit"}:
            return "Goodbye."
        if command == "help":
            return self._help(args[0] if args else None)
        if command == "look":
            return self._scene()
        if command == "status":
            return self._observer_status() if self.observer else self._player_status()
        if command in {"world", "observe"}:
            self._require_observer()
            return self._world_scene()
        if command == "events":
            self._require_observer()
            limit = self._limit(args, 20, maximum=100)
            return self._format_events(self.platform.observer_view(event_limit=limit)["recent_events"])
        if command == "timeline":
            limit = self._limit(args, 50, maximum=200)
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
        if command == "step":
            self._require_observer()
            return self._format_result(self.platform.step())
        if command == "evolution":
            self._require_observer()
            return self._format_evolution()
        if command == "metrics":
            self._require_observer()
            return self._format_mapping("Metrics", self.platform.metrics())
        if command == "actions":
            self._require_player()
            return self._format_actions(self._player_view())
        if command in {"do", "act"}:
            self._require_player()
            if not args:
                raise ValueError("do <number|behavior_ref> [key=value ...]")
            view = self._player_view()
            action = self._select_action(view.get("actions") or [], args[0])
            parameters = dict(action.get("parameters") or {})
            parameters.update(_assignments(args[1:]))
            title = action.get("choice") or action.get("name") or action.get("ref")
            return self._submit(action.get("ref") or "", action.get("target") or {}, parameters, f"Player selected {title} from the text client.")
        if command == "go":
            self._require_player()
            if not args:
                raise ValueError("go <location_id|name|n>")
            return self._travel(args[0])
        if command == "wait":
            return self._wait(args)
        if command == "say":
            self._require_player()
            if len(args) < 2:
                raise ValueError("say <target> <message>")
            message = " ".join(args[1:]).strip()
            if not message:
                raise ValueError("say <target> <message>")
            return self._format_result(self.platform.submit_dialogue(self.player_id or "", args[0], "general", message))
        if command == "offer":
            self._require_player()
            if len(args) != 3:
                raise ValueError("offer <target_id> <give:item=qty> <want:item=qty>")
            return self._format_result(self.platform.create_trade_offer(self.player_id or "", args[0], _bundle(args[1]), _bundle(args[2])))
        if command == "offers":
            self._require_player()
            offers = self.platform.store.repositories.social.list_trade_offers(self.player_id or "")
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
        output_fn(self._world_scene() if self.observer else self._player_scene())
        if self.observer:
            output_fn("Type help for commands.")
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

    def _scene(self) -> str:
        return self._world_scene() if self.observer else self._player_scene()

    def _world_scene(self) -> str:
        return self._format_world(
            self.platform.observer_view(),
            self.platform.status(),
            self.platform.ready_evolution_tasks(),
        )

    def _player_scene(self) -> str:
        return self._format_player(self._player_view())

    def _player_status(self) -> str:
        view = self._player_view()
        player = view.get("player") or {}
        world_time = view.get("world_time") if isinstance(view.get("world_time"), dict) else {}
        lines = [
            self._clock_line(world_time) + f" · {player.get('location') or 'unknown'}",
            f"{player.get('name') or self.player_id} · {player.get('status') or 'idle'} · goal: {player.get('goal') or 'none'}",
        ]
        needs = [item for item in player.get("attributes") or [] if item.get("kind") == "need"]
        if needs:
            lines.append("Needs: " + " | ".join(
                f"{item.get('label') or item.get('id')} {_quantity(float(item.get('value') or 0))}"
                for item in needs[:5]
            ))
        task = view.get("active_task") or {}
        label = task.get("action") or task.get("bt")
        lines.append(f"Busy: {label} · {task.get('remaining_minutes', 0)} minute(s) remaining" if label else "Busy: idle")
        inventory = view.get("inventory") or {}
        lines.append("Inventory: " + (", ".join(f"{name} x{_quantity(float(value))}" for name, value in sorted(inventory.items())) or "empty"))
        return "\n".join(lines)

    def _observer_status(self) -> str:
        view = self.platform.observer_view(event_limit=1)
        runtime = self.platform.status()
        agents = view.get("agents") or []
        occupied = {str(agent.get("location_id") or agent.get("location") or "") for agent in agents}
        clock = self._clock_line(view.get("world_clock") or {}, view.get("world_time"))
        tasks = self.platform.ready_evolution_tasks()
        instance = f" · instance={runtime.get('instance_id')}" if runtime.get("instance_id") else ""
        occupied_count = len({item for item in occupied if item})
        entity_counts = self._format_counts(view.get("entity_counts") or {})
        return "\n".join([
            clock,
            f"Runtime: {runtime.get('status')}{instance}",
            f"Characters: {len(agents)} · occupied locations: {occupied_count} · entities: {entity_counts}",
            f"Evolution: {len(tasks)} ready task(s)",
        ])

    def _wait(self, args: list[str]) -> str:
        if self.observer:
            raise ValueError("Observer mode is read-only; run the Platform runtime to advance the world.")
        count = 1
        if args:
            try:
                count = int(args[0])
            except ValueError as exc:
                raise ValueError("wait [minutes]") from exc
        if count < 1 or count > 240:
            raise ValueError("wait accepts between 1 and 240 minutes")
        before = int(self.platform.status().get("world_minute", 0))
        for _ in range(count):
            self.platform.step()
        after = int(self.platform.status().get("world_minute", 0))
        return f"Advanced {after - before} world minute(s) to minute {after}."

    def _submit(self, behavior_ref: str, target: dict[str, Any], parameters: dict[str, Any], reason: str) -> str:
        minute = int(self.platform.store.repositories.metadata.get("world_minute", 0) or 0)
        self._command_seq += 1
        intent = BehaviorIntent(
            actor_id=self.player_id or "",
            behavior_ref=behavior_ref,
            target=target,
            parameters=parameters,
            reason=reason,
            confidence=1.0,
            intent_id=f"text-{self.player_id}-{minute}-{self._command_seq}",
        )
        return self._format_action_result(self.platform.submit_behavior(intent))

    def _travel(self, selector: str) -> str:
        view = self._player_view()
        routes = view.get("routes") or []
        route = None
        if selector.isdigit():
            index = int(selector) - 1
            if 0 <= index < len(routes):
                route = routes[index]
        else:
            route = next(
                (item for item in routes if selector in {str(item.get("id")), str(item.get("name"))}),
                None,
            )
        if route is None:
            raise ValueError(f"unknown route: {selector}; type look to list routes")
        return self._submit(
            "behavior:travel",
            {},
            {"location_id": route["id"]},
            f"Travel to {route.get('name') or route['id']} through the text client.",
        )

    @staticmethod
    def _select_action(actions: list[dict[str, Any]], selector: str) -> dict[str, Any]:
        if selector.isdigit():
            index = int(selector) - 1
            if 0 <= index < len(actions):
                return actions[index]
            raise ValueError(f"action {selector} is out of range; type actions to list choices")
        for action in actions:
            names = {str(action.get("ref") or ""), str(action.get("choice") or ""), str(action.get("name") or "")}
            if selector in names:
                return action
        raise ValueError(f"unknown action: {selector}; type actions to list choices")

    def _player_view(self) -> dict[str, Any]:
        view = build_player_view(self.platform, self.player_id or "")
        if view.get("status") != "ok":
            raise ValueError(str(view.get("message") or "Player view is unavailable."))
        return view

    def _require_player(self) -> None:
        if self.observer or not self.player_id:
            raise ValueError("This command is available only in player mode.")

    def _require_observer(self) -> None:
        if not self.observer:
            raise ValueError("Whole-world telemetry requires observer mode.")

    def _help(self, name: str | None = None) -> str:
        if name:
            text = _COMMAND_HELP.get(name.lower())
            if not text:
                raise ValueError(f"Unknown command: {name}. Type help.")
            return f"{name.lower()}: {text}"
        order = (
            ["status", "world", "events", "timeline", "map", "pause", "resume", "step", "evolution", "metrics", "help", "quit"]
            if self.observer
            else ["look", "status", "actions", "do", "go", "say", "offer", "offers", "accept", "counter", "reject", "wait", "map", "timeline", "help", "quit"]
        )
        mode = "observer" if self.observer else "player"
        lines = [f"Commands: {mode} mode"]
        lines.extend(f"  {item:<10} {_COMMAND_HELP[item]}" for item in order)
        return "\n".join(lines)

    @staticmethod
    def _limit(args: list[str], default: int, *, maximum: int) -> int:
        if not args:
            return default
        try:
            value = int(args[0])
        except ValueError as exc:
            raise ValueError("limit must be an integer") from exc
        return max(1, min(maximum, value))

    @staticmethod
    def _clock_line(world_time: dict[str, Any], minute: Any = None) -> str:
        current = world_time.get("minute", minute)
        label = world_time.get("label")
        return f"World minute {current}" + (f" · {label}" if label else "")

    @classmethod
    def _format_player(cls, view: dict[str, Any]) -> str:
        player = view.get("player") or {}
        world_time = view.get("world_time") if isinstance(view.get("world_time"), dict) else {}
        name = player.get("name") or player.get("id") or "player"
        location = player.get("location") or player.get("location_id") or "unknown"
        safety = "safe" if player.get("location_safe") else "unsafe"
        tags = [str(item) for item in player.get("location_tags") or []]
        lines = [
            "== World ==",
            cls._clock_line(world_time) + f" · world={view.get('world_id') or 'unknown'}",
            "",
            "== You ==",
            f"{name} · {player.get('role') or 'player'} · {player.get('status') or 'idle'} · {location} ({safety})",
        ]
        if tags:
            lines.append("Tags: " + ", ".join(tags))
        lines.append(f"Goal: {player.get('goal') or 'none'}")
        needs = [item for item in player.get("attributes") or [] if item.get("kind") == "need"]
        if needs:
            lines.append("Needs: " + " | ".join(
                f"{item.get('label') or item.get('id')} {_meter(float(item.get('value') or 0))} {_quantity(float(item.get('value') or 0))}"
                for item in needs
            ))
        else:
            needs_map = view.get("needs") or {}
            lines.append("Needs: " + (" | ".join(f"{key} {_quantity(float(value))}" for key, value in needs_map.items()) or "none"))
        inventory = view.get("inventory") or {}
        lines.append("Inventory: " + (", ".join(f"{key} x{_quantity(float(value))}" for key, value in sorted(inventory.items())) or "empty"))
        routes = view.get("routes") or []
        route_text = "; ".join(
            f"{route.get('name')} ({route.get('minutes')} min, {'safe' if route.get('safe') else 'unsafe'})"
            for route in routes
        )
        lines.append("Routes: " + (route_text or "none known"))
        task = view.get("active_task") or {}
        label = task.get("action") or task.get("bt")
        if label:
            lines.extend(["", f"Busy: {label} · {task.get('remaining_minutes', 0)} minute(s) remaining · type wait to advance."])
        lines.extend(["", "== Actions =="])
        lines.extend(cls._action_lines(view.get("actions") or []))
        nearby = view.get("nearby_entities") or []
        if nearby:
            lines.extend(["", "== Nearby =="])
            for item in nearby[:12]:
                name_or_id = item.get("name") or item.get("id") or "entity"
                kind = item.get("role") or item.get("type") or item.get("entity_type")
                identifier = str(item.get("id") or "")
                label = f"- {name_or_id}"
                if kind:
                    label += f" · {kind}"
                if identifier and identifier != str(name_or_id):
                    label += f" · id={identifier}"
                lines.append(label)
        outcomes = view.get("recent_outcomes") or []
        if outcomes:
            lines.extend(["", "== Recent =="])
            for item in outcomes[:6]:
                text = item.get("result") or item.get("summary") or item.get("behavior_choice") or item.get("event_type") or "outcome"
                lines.append(f"[minute {item.get('world_minute', '?')}] {_scalar(text)}")
        lines.extend(["", "Type actions, do <n>, go <place>, map or help."])
        return "\n".join(lines)

    @staticmethod
    def _action_lines(actions: list[dict[str, Any]]) -> list[str]:
        if not actions:
            return ["  None available right now."]
        lines: list[str] = []
        for index, action in enumerate(actions, start=1):
            title = action.get("choice") or action.get("name") or action.get("ref") or "action"
            detail = [str(action.get("time"))] if action.get("time") else []
            summary = action.get("summary") or action.get("purpose")
            if summary:
                detail.append(str(summary))
            suffix = f" · {' · '.join(detail)}" if detail else ""
            lines.append(f" {index}. {title}{suffix}  [do {index}]")
            extras = [str(item) for item in action.get("cost") or []]
            extras.extend(str(item) for item in (action.get("impact") or [])[:2])
            if extras:
                lines.append(f"    -> {'; '.join(extras)}")
        return lines

    def _format_actions(self, view: dict[str, Any]) -> str:
        return "\n".join(["Actions:", *self._action_lines(view.get("actions") or [])])

    @classmethod
    def _format_world(
        cls,
        view: dict[str, Any],
        runtime: dict[str, Any] | None = None,
        evolution: list[dict[str, Any]] | None = None,
    ) -> str:
        clock = cls._clock_line(view.get("world_clock") or {}, view.get("world_time"))
        lines = ["== World ==", clock + f" · world={view.get('world_id') or 'unknown'}"]
        if runtime:
            instance = f" · instance={runtime.get('instance_id')}" if runtime.get("instance_id") else ""
            lines.append(f"Runtime: {runtime.get('status')}{instance}")
        properties = view.get("world_properties") or {}
        if properties:
            lines.append("Properties: " + ", ".join(f"{key}={_scalar(value)}" for key, value in properties.items()))
        agents = view.get("agents") or []
        lines.extend(["", f"Characters: {len(agents)}"])
        for agent in agents:
            lines.append(
                f"- {agent.get('name')} ({agent.get('role')}): {agent.get('status')} at {agent.get('location')}; "
                f"behavior={agent.get('active_behavior') or 'none'}; remaining={agent.get('remaining_minutes', 0)}"
            )
        lines.append("Entities: " + cls._format_counts(view.get("entity_counts") or {}))
        lines.append(f"Evolution: {len(evolution or [])} ready task(s)")
        events = view.get("recent_events") or []
        if events:
            lines.extend(["", "Recent events:"])
            for event in events[:8]:
                detail = event.get("result") or event.get("summary") or event.get("effect")
                lines.append(
                    f"[{event.get('world_minute')}] {event.get('event_type')}"
                    + (f" · {_scalar(detail)}" if detail else "")
                )
        return "\n".join(lines)

    @staticmethod
    def _format_counts(counts: dict[str, Any]) -> str:
        return ", ".join(f"{key}={value}" for key, value in counts.items()) or "none"

    def _format_evolution(self) -> str:
        tasks = self.platform.ready_evolution_tasks()
        if not tasks:
            return "No evolution tasks are ready."
        lines = [f"Ready evolution tasks: {len(tasks)}"]
        for task in tasks:
            label = task.get("task_type") or task.get("type") or task.get("role") or "task"
            parts = [str(label)]
            who = task.get("agent_id") or task.get("actor_id")
            if who:
                parts.append(str(who))
            due = task.get("due_minute", task.get("run_at", task.get("world_minute")))
            if due is not None:
                parts.append(f"at minute {due}")
            lines.append("- " + " · ".join(parts))
        return "\n".join(lines)

    @staticmethod
    def _format_mapping(title: str, values: dict[str, Any]) -> str:
        lines = [f"{title}:"]
        lines.extend(f"  {key}: {_scalar(value)}" for key, value in values.items())
        return "\n".join(lines)

    @staticmethod
    def _format_result(result: dict[str, Any]) -> str:
        lines = [f"status={result.get('status')}"]
        for key, value in result.items():
            if key == "status" or value in (None, "", [], {}):
                continue
            lines.append(f"  {key}: {_scalar(value)}")
        return "\n".join(lines)

    @classmethod
    def _format_action_result(cls, result: dict[str, Any]) -> str:
        text = cls._format_result(result)
        try:
            remaining = int(result.get("remaining_minutes") or 0)
        except (TypeError, ValueError):
            remaining = 0
        if result.get("status") == "accepted" and remaining > 0:
            text += f"\nNext: type wait {remaining} to let Platform finish this action."
        return text

    @staticmethod
    def _format_offer(offer: dict[str, Any]) -> str:
        return (
            f"{offer.get('offer_id')}: from={offer.get('offerer_id')} give={_scalar(offer.get('give'))} "
            f"want={_scalar(offer.get('want'))} status={offer.get('status')}"
        )

    @staticmethod
    def _format_events(events: list[dict[str, Any]]) -> str:
        if not events:
            return "No public events."
        lines = []
        for event in events:
            detail = event.get("result") or event.get("summary") or event.get("effect") or ""
            lines.append(
                f"[{event.get('world_minute')}] {event.get('event_type')} actor={event.get('actor_id')}"
                + (f" · {_scalar(detail)}" if detail else "")
            )
        return "\n".join(lines)
