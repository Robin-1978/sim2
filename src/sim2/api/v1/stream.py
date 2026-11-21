"""Unified server-sent event stream with role filtering and resume support."""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from typing import Any


_OBSERVER_TYPES = {"ready", "timeline", "runtime", "dialogue", "decisions"}
_PLAYER_TYPES = {
    "ready",
    "timeline",
    "runtime",
    "player.view.changed",
    "feed.append",
    "dialogue.append",
    "trade.changed",
    "chapter.changed",
    "task.progress",
    "task.completed",
}


class Subscription:
    def __init__(self, role: str, player_id: str, ticks: bool):
        self.role = role
        self.player_id = player_id
        self.ticks = ticks
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1000)
        self.closed = False

    def put(self, envelope: dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(envelope)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(envelope)
            except queue.Full:
                pass

    def accepts(self, envelope: dict[str, Any]) -> bool:
        kind = str(envelope.get("type") or "")
        if self.role == "operator":
            return True
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        owner = str(data.get("player_id") or "")
        if self.role == "player":
            if owner and owner != self.player_id:
                return False
            return kind in _PLAYER_TYPES
        if owner:
            return False
        return kind in _OBSERVER_TYPES


class StreamHub:
    """Poll world state once and fan deltas out to every subscription."""

    POLL_SECONDS = 0.45
    HEARTBEAT_SECONDS = 15.0

    def __init__(self, server: Any):
        self.server = server
        self._lock = threading.RLock()
        self._subs: set[Subscription] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=1200)
        self._seq = 0
        self._after_event = 0
        self._primed = False
        self._last_runtime = ""
        self._last_dialogue = 0
        self._last_decision = 0
        self._stop = threading.Event()
        try:
            self._prime()
        except Exception:
            self._primed = False
        self._thread = threading.Thread(target=self._run, name="sim2-stream-hub", daemon=True)
        self._thread.start()

    # ---- publishing ---------------------------------------------------------

    def publish(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        event_id: int = 0,
        revision: int = 0,
        world_minute: int = 0,
    ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            envelope = {
                "cursor": self._seq,
                "event_id": int(event_id or 0),
                "revision": int(revision or 0),
                "type": str(kind),
                "world_minute": int(world_minute or 0),
                "data": dict(data or {}),
            }
            self._history.append(envelope)
            targets = [sub for sub in self._subs if sub.accepts(envelope)]
        for sub in targets:
            sub.put(envelope)
        return envelope

    def subscribe(self, role: str, player_id: str, after: int, ticks: bool = False) -> Subscription:
        role = role if role in {"operator", "player"} else "observer"
        sub = Subscription(role, player_id, ticks)
        with self._lock:
            self._subs.add(sub)
            latest = self._seq
            replay = [item for item in self._history if int(item["cursor"]) > max(0, int(after))]
            seq = latest
        sub.put({
            "cursor": seq,
            "event_id": 0,
            "revision": 0,
            "type": "ready",
            "world_minute": 0,
            "data": {"after": max(0, int(after)), "cursor": seq},
        })
        for envelope in replay:
            if sub.accepts(envelope):
                sub.put(envelope)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.closed = True
        with self._lock:
            self._subs.discard(sub)

    # ---- SSE transport ------------------------------------------------------

    def serve(self, handler: Any, sub: Subscription) -> None:
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream; charset=utf-8")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "keep-alive")
        handler.send_header("x-accel-buffering", "no")
        handler.end_headers()
        last_beat = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    envelope = sub.queue.get(timeout=1.0)
                except queue.Empty:
                    envelope = None
                if envelope is not None:
                    if not _emit(handler, envelope):
                        return
                if time.monotonic() - last_beat >= self.HEARTBEAT_SECONDS:
                    last_beat = time.monotonic()
                    try:
                        handler.wfile.write(b": ping\n\n")
                        handler.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ValueError):
                        return
        finally:
            self.unsubscribe(sub)

    # ---- poll loop ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(self.POLL_SECONDS):
            try:
                self._poll_once()
            except Exception:
                # A transient DB or instance switch error must not kill the hub.
                continue

    def _poll_once(self) -> None:
        if not self.server.has_world:
            self._runtime_changed()
            return
        if not self._primed:
            self._prime()
        timeline = self.server.service_call("timeline", 200, self._after_event, False, False)
        events = timeline.get("events", []) if isinstance(timeline, dict) else []
        if events:
            self._after_event = max(self._after_event, int(timeline.get("next_after_event_id", self._after_event) or self._after_event))
            max_event = max(int(item.get("event_id", 0) or 0) for item in events)
            revision = self.server.world_revision()
            minute = max(int(item.get("world_minute", 0) or 0) for item in events)
            self.publish(
                "timeline",
                {"events": events, "next_after_event_id": self._after_event},
                event_id=max_event,
                revision=revision,
                world_minute=minute,
            )
            kinds = {str(item.get("event_type") or "") for item in events}
            if kinds & {"dialogue.completed"}:
                self._publish_dialogue()
            if any(kind.startswith("behavior.") for kind in kinds):
                self._publish_decisions()
            if any(kind.startswith("trade.") for kind in kinds):
                self.publish("trade.changed", {}, event_id=max_event, revision=revision, world_minute=minute)
            if kinds & {"story.beat.activated", "story.ending"}:
                self.publish("chapter.changed", {}, event_id=max_event, revision=revision, world_minute=minute)
            # Any committed event can change a player's scene, needs or goals.
            self.publish(
                "player.view.changed",
                {"reason": "world.changed"},
                event_id=max_event,
                revision=revision,
                world_minute=minute,
            )
            self.publish(
                "feed.append",
                {"events": events},
                event_id=max_event,
                revision=revision,
                world_minute=minute,
            )
        self._runtime_changed()

    def _prime(self) -> None:
        latest = self.server.service_call("timeline", 1, 0, False, True)
        events = latest.get("events", []) if isinstance(latest, dict) else []
        self._after_event = max([int(item.get("event_id", 0) or 0) for item in events] + [0])
        self._primed = True

    def _runtime_changed(self) -> None:
        try:
            runtime = self.server.runtime_status()
        except Exception:
            return
        signature = json.dumps(runtime, ensure_ascii=False, sort_keys=True, default=str)
        if signature == self._last_runtime:
            return
        self._last_runtime = signature
        self.publish(
            "runtime",
            runtime,
            revision=int(runtime.get("world_revision", 0) or 0),
            world_minute=int(runtime.get("world_minute", 0) or 0),
        )

    def _publish_dialogue(self) -> None:
        try:
            dialogue = self.server.service_call("observer_dialogue", 20)
        except Exception:
            return
        exchanges = dialogue.get("exchanges", []) if isinstance(dialogue, dict) else []
        fresh = [item for item in exchanges if int(item.get("dialogue_id", -1) or -1) > self._last_dialogue]
        if not fresh:
            return
        self._last_dialogue = max([self._last_dialogue] + [int(item.get("dialogue_id", -1) or -1) for item in exchanges])
        self.publish("dialogue", {"exchanges": fresh})

    def _publish_decisions(self) -> None:
        try:
            decisions = self.server.service_call("observer_decisions", 20)
        except Exception:
            return
        items = decisions.get("decisions", []) if isinstance(decisions, dict) else []
        fresh = [item for item in items if int(item.get("event_id", -1) or -1) > self._last_decision]
        if not fresh:
            return
        self._last_decision = max([self._last_decision] + [int(item.get("event_id", -1) or -1) for item in items])
        self.publish("decisions", {"decisions": fresh})

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            subs = list(self._subs)
            self._subs.clear()
        for sub in subs:
            sub.closed = True
        self._thread.join(timeout=2.0)


def _emit(handler: Any, envelope: dict[str, Any]) -> bool:
    try:
        body = json.dumps(envelope, ensure_ascii=False)
        handler.wfile.write(
            f"id: {envelope['cursor']}\nevent: {envelope['type']}\ndata: {body}\n\n".encode("utf-8")
        )
        handler.wfile.flush()
        return True
    except (BrokenPipeError, ConnectionResetError, ValueError):
        return False
