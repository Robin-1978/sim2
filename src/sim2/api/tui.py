from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static

from ..platform.runtime import Platform
from ..llm.gateway import LLMRouter
from ..simulation.runner import SimulationRunner
from .map_view import render_topology_map
from .service import ControlService
from .text import TextWorldSession


class AutonomousWorldWorker:
    """Run the world on a database connection separate from the UI thread."""

    def __init__(self, db_path: Path, provider_name: str | None = None, chunk_minutes: int = 60):
        self.db_path = Path(db_path)
        self.provider_name = provider_name
        self.chunk_minutes = max(1, int(chunk_minutes))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_error = ""

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="sim2-world-runtime", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        platform = Platform(self.db_path)
        try:
            router = LLMRouter.from_default_config()
            if self.provider_name:
                providers = {
                    role: router.new_provider_for_name(self.provider_name, role=role)
                    for role in ("agent_decision", "agent_reflection", "god", "story")
                }
            else:
                providers = {
                    role: router.provider_for_role(role)
                    for role in ("agent_decision", "agent_reflection", "god", "story")
                }
            runner = SimulationRunner(platform, providers)
            while not self.stop_event.is_set():
                if platform.status().get("status") != "running":
                    time.sleep(0.2)
                    continue
                try:
                    report = runner.run(self.chunk_minutes)
                    if str(report.stop_reason or "").startswith("provider_cooldown"):
                        remaining = max(
                            (float(getattr(provider, "cooldown_remaining", 0.0) or 0.0) for provider in providers.values()),
                            default=1.0,
                        )
                        self.last_error = f"Provider cooling down for {remaining:.1f}s; world time is held."
                        self.stop_event.wait(max(0.25, min(30.0, remaining)))
                    else:
                        self.last_error = ""
                except Exception as exc:  # pragma: no cover - provider failures are integration concerns
                    self.last_error = str(exc)
                    self.stop_event.wait(1.0)
        finally:
            platform.close()


class WorldTUI(App[None]):
    """Live observer/player interface over the public Platform API."""

    TITLE = "sim2 · Living World"
    SUB_TITLE = "SQLite world observer"

    CSS = """
    Screen { layout: vertical; background: #0e141b; color: #d9e2ec; }
    #status_bar { height: 3; padding: 1 2; background: #172231; color: #b8e1ff; text-style: bold; }
    #body { height: 1fr; }
    .panel { border: round #45627f; margin: 0 1 1 1; background: #111b26; }
    .panel_title { height: 2; padding: 0 1; color: #7dd3fc; text-style: bold; }
    #left { width: 2fr; min-width: 34; }
    #center { width: 3fr; min-width: 42; }
    #right { width: 2fr; min-width: 34; }
    #map { height: 1fr; padding: 0 1 1 1; overflow: auto auto; }
    #timeline { height: 1fr; padding: 0 1; scrollbar-size: 1 1; }
    #agents { height: 3fr; }
    #details { height: 2fr; padding: 1; overflow: auto auto; }
    #controls { height: 3; margin: 0 1; }
    #controls Button { min-width: 12; margin-right: 1; }
    #command { width: 1fr; }
    #message { height: 3; padding: 1 2; color: #a7f3d0; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("p", "toggle_pause", "Pause / resume"),
        Binding("s", "step_world", "Step one minute"),
        Binding("r", "refresh_world", "Refresh"),
    ]

    def __init__(
        self,
        db_path: Path,
        *,
        observer: bool = True,
        player_id: str | None = None,
        run_world: bool = False,
        provider_name: str | None = None,
        refresh_seconds: float = 1.0,
    ):
        super().__init__()
        self.db_path = Path(db_path)
        self.observer = bool(observer)
        self.player_id = player_id
        self.service = ControlService(self.db_path)
        self.session = TextWorldSession(self.service.platform, player_id=player_id, observer=observer)
        self.worker = AutonomousWorldWorker(self.db_path, provider_name) if run_world else None
        self.refresh_seconds = max(0.25, float(refresh_seconds))
        self.selected_agent_id: str | None = None
        self._last_timeline_event = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Loading world…", id="status_bar")
        with Horizontal(id="body"):
            with Vertical(id="left", classes="panel"):
                yield Static("WORLD MAP", classes="panel_title")
                yield Static("", id="map")
            with Vertical(id="center", classes="panel"):
                yield Static("STORY TIMELINE", classes="panel_title")
                yield RichLog(id="timeline", wrap=True, markup=False, auto_scroll=True)
            with Vertical(id="right", classes="panel"):
                yield Static("CHARACTERS", classes="panel_title")
                yield DataTable(id="agents", cursor_type="row", zebra_stripes=True)
                yield Static("Select a character to inspect public state.", id="details")
        with Horizontal(id="controls"):
            yield Button("Pause", id="pause", variant="warning")
            yield Button("Step +1m", id="step")
            yield Button("Refresh", id="refresh", variant="primary")
            yield Input(placeholder="Command: map, look, move…, say…, offer…", id="command")
        yield Static("Ready.", id="message")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#agents", DataTable)
        table.add_columns("Character", "Location", "State", "Activity", "Left")
        if self.worker:
            self.worker.start()
        self.refresh_world()
        self.set_interval(self.refresh_seconds, self.refresh_world)

    def on_unmount(self) -> None:
        if self.worker:
            self.worker.stop()
        self.service.close()

    def refresh_world(self) -> None:
        status = self.service.status()
        view = self.service.observer(100)
        map_view = self.service.map(viewer_id=self.player_id, observer=self.observer)
        minute = int(status.get("world_minute", 0))
        clock = view.get("world_clock") or {}
        time_label = str(clock.get("label") or f"minute {minute}")
        runtime = str(status.get("status", "unknown"))
        worker_note = ""
        if self.worker:
            worker_note = " · auto running" if self.worker.running else " · auto stopped"
            if self.worker.last_error:
                worker_note += f" · provider error: {self.worker.last_error[:100]}"
        self.query_one("#status_bar", Static).update(
            f"{view.get('world_id')}  ·  {time_label}  ·  minute {minute}  ·  {runtime}{worker_note}"
        )
        self.query_one("#map", Static).update(render_topology_map(map_view))

        table = self.query_one("#agents", DataTable)
        table.clear(columns=False)
        for agent in view.get("agents") or []:
            remaining = int(agent.get("remaining_minutes", 0) or 0)
            table.add_row(
                str(agent.get("name") or agent.get("id")),
                str(agent.get("location") or "unknown"),
                str(agent.get("status") or "idle"),
                str(agent.get("active_behavior") or "—"),
                f"{remaining}m" if remaining else "—",
                key=str(agent.get("id")),
            )
        self._update_selected_agent(view)

        timeline = self.service.timeline(200, self._last_timeline_event, False)
        log = self.query_one("#timeline", RichLog)
        for event in timeline.get("events") or []:
            event_id = int(event.get("event_id", 0))
            if event_id <= self._last_timeline_event:
                continue
            log.write(Text(f"[{event.get('world_minute')}] {event.get('narrative')}", style="white"))
            self._last_timeline_event = max(self._last_timeline_event, event_id)

        pause = self.query_one("#pause", Button)
        pause.label = "Pause" if runtime == "running" else "Resume"

    def _update_selected_agent(self, view: dict[str, Any]) -> None:
        agents = list(view.get("agents") or [])
        selected = next((item for item in agents if str(item.get("id")) == self.selected_agent_id), None)
        if selected is None and agents:
            selected = agents[0]
            self.selected_agent_id = str(selected.get("id"))
        if selected is None:
            self.query_one("#details", Static).update("No visible characters.")
            return
        attributes = selected.get("attributes") or []
        values = "\n".join(
            f"  {item.get('label')}: {item.get('value')} {item.get('unit', '')}" for item in attributes
        ) or "  none"
        self.query_one("#details", Static).update(
            f"{selected.get('name')}\n"
            f"Role: {selected.get('role')}\n"
            f"Location: {selected.get('location')}\n"
            f"State: {selected.get('status')}\n"
            f"Activity: {selected.get('active_behavior') or 'none'}\n"
            f"Remaining: {selected.get('remaining_minutes', 0)} minutes\n\n"
            f"Public attributes\n{values}"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_agent_id = str(event.row_key.value)
        self.refresh_world()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pause":
            self.action_toggle_pause()
        elif event.button.id == "step":
            self.action_step_world()
        else:
            self.refresh_world()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        if not command:
            return
        event.input.value = ""
        try:
            result = self.session.execute(command)
        except (TypeError, ValueError) as exc:
            result = f"Error: {exc}"
        self.query_one("#message", Static).update(result[:1000] or "Command completed.")
        self.refresh_world()

    def action_toggle_pause(self) -> None:
        if self.service.status().get("status") == "running":
            result = self.service.pause("tui")
        else:
            result = self.service.resume("tui")
            if self.worker:
                self.worker.start()
        self.query_one("#message", Static).update(f"Runtime is {result.get('status')}.")
        self.refresh_world()

    def action_step_world(self) -> None:
        if self.service.status().get("status") == "running" and self.worker:
            self.query_one("#message", Static).update("Pause the automatic runtime before stepping one minute.")
            return
        result = self.service.step()
        self.query_one("#message", Static).update(
            f"Advanced from minute {result.get('from_minute')} to {result.get('to_minute')}."
        )
        self.refresh_world()

    def action_refresh_world(self) -> None:
        self.refresh_world()


def run_tui(
    db_path: Path,
    *,
    observer: bool = True,
    player_id: str | None = None,
    run_world: bool = False,
    provider_name: str | None = None,
) -> None:
    WorldTUI(
        db_path,
        observer=observer,
        player_id=player_id,
        run_world=run_world,
        provider_name=provider_name,
    ).run()
