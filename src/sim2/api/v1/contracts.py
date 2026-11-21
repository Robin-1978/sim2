"""Versioned HTTP contracts for the sim2 web API.

The platform and its world packages stay the source of truth.  These models
define the *wire* shape only: request validation, stable response envelopes and
the documentation surface exposed by ``/api/v1/meta/contracts``.

World-driven sections (inventory, actions, facilities, map nodes) keep
``extra="allow"`` so a world can add fields without a server release.  The
top-level envelope is strict: adding or removing a stable field fails the
contract tests.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = "v1"


class WireModel(BaseModel):
    """Base model for stable wire envelopes."""

    model_config = ConfigDict(extra="forbid")


class ProjectionModel(BaseModel):
    """Base model for world-driven projections.

    A world package may add properties, actions or facility fields at any time;
    the projection model therefore validates the fields it declares and keeps
    the rest intact.
    """

    model_config = ConfigDict(extra="allow")


class SessionRequestV1(WireModel):
    role: str = "observer"
    operator_token: str | None = None
    idempotency_key: str | None = None

    @field_validator("role")
    @classmethod
    def _default_role(cls, value: str) -> str:
        return str(value or "observer").strip() or "observer"


class SessionV1(WireModel):
    status: str
    role: str
    session_token: str


class WorldClockV1(ProjectionModel):
    start_day: int | None = None
    start_minute: int | None = None
    season: str | None = None


class WorldInfoV1(ProjectionModel):
    status: str
    world_id: str = ""
    instance_id: str = ""
    title: str = ""
    premise: str = ""
    genre: str = ""
    locale: str = ""
    season: str = ""
    calendar: dict[str, Any] = Field(default_factory=dict)
    world_clock: dict[str, Any] = Field(default_factory=dict)
    world_minute: int = 0
    ui: dict[str, Any] = Field(default_factory=dict)


class InstanceV1(ProjectionModel):
    instance_id: str = ""
    db_path: str = ""
    active: bool = False


class InstanceListV1(ProjectionModel):
    status: str
    active_db: str = ""
    instance_id: str = ""
    instances: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeV1(ProjectionModel):
    state: str
    running: bool = False
    thread_alive: bool = False
    run_id: str | None = None
    provider: str | None = None
    world_minute: int = 0


class PlayerEntryV1(ProjectionModel):
    status: str
    world_id: str = ""
    entry_location: dict[str, Any] | None = None
    origins: list[dict[str, Any]] = Field(default_factory=list)
    players: list[dict[str, Any]] = Field(default_factory=list)
    can_create_player: bool = False


class PlayerCharacterV1(ProjectionModel):
    id: str = ""
    name: str = ""
    location: str = ""
    status: str = ""


class PlayerLocationV1(ProjectionModel):
    id: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    safe: bool = False


class FacilityV1(ProjectionModel):
    id: str = ""
    name: str = ""
    type: str = ""
    description: str = ""
    location_id: str = ""
    location: str = ""
    status: str = ""
    stock: float | int | None = None
    properties: list[dict[str, Any]] = Field(default_factory=list)
    order: int = 999


class ActionV1(ProjectionModel):
    ref: str = ""
    name: str = ""
    kind: str = ""
    time: str = ""


class GoalV1(ProjectionModel):
    id: str = ""
    title: str = ""
    status: str = ""


class ChapterV1(ProjectionModel):
    id: str = ""
    title: str = ""
    summary: str = ""
    goal_hint: str = ""
    kind: str = ""


class PlayerViewV1(ProjectionModel):
    status: str
    revision: int = 0
    last_event_id: int = 0
    server_minute: int = 0
    world_id: str = ""
    world_time: dict[str, Any] = Field(default_factory=dict)
    player: dict[str, Any] = Field(default_factory=dict)
    inventory: dict[str, Any] = Field(default_factory=dict)
    item_labels: dict[str, str] = Field(default_factory=dict)
    needs: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    goals: list[dict[str, Any]] = Field(default_factory=list)
    chapter: dict[str, Any] | None = None
    onboarding: dict[str, Any] | None = None
    active_task: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    unavailable_actions: list[dict[str, Any]] = Field(default_factory=list)
    location: dict[str, Any] = Field(default_factory=dict)
    world_properties: dict[str, Any] = Field(default_factory=dict)
    world_property_labels: dict[str, Any] = Field(default_factory=dict)
    facilities: list[dict[str, Any]] = Field(default_factory=list)
    ui: dict[str, Any] = Field(default_factory=dict)
    alert_thresholds: dict[str, Any] = Field(default_factory=dict)
    routes: list[dict[str, Any]] = Field(default_factory=list)
    map: dict[str, Any] = Field(default_factory=dict)
    recent_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    nearby_entities: list[dict[str, Any]] = Field(default_factory=list)
    nearby_participants: list[dict[str, Any]] = Field(default_factory=list)
    recent_dialogue: list[dict[str, Any]] = Field(default_factory=list)


class TaskV1(ProjectionModel):
    task_id: str
    player_id: str
    state: str
    requested_minutes: int
    completed_minutes: int = 0
    world_minute: int = 0
    decisions: int = 0
    llm_errors: int = 0
    stop_reason: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class TaskAcceptedV1(ProjectionModel):
    status: str = "accepted"
    task: TaskV1


class TaskListV1(ProjectionModel):
    status: str = "ok"
    task: TaskV1 | None = None


class StreamEventV1(WireModel):
    cursor: int
    event_id: int = 0
    revision: int = 0
    type: str
    world_minute: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class ErrorV1(WireModel):
    status: str = "error"
    code: str = "internal_error"
    message: str
    failed_check: str | None = None
    request_id: str = ""
    traceback: None = None


class SessionRequest(SessionRequestV1):
    pass


class EnterPlayerRequestV1(WireModel):
    player_id: str
    origin_id: str = ""
    name: str | None = None
    idempotency_key: str | None = None


class ActionRequestV1(BaseModel):
    model_config = ConfigDict(extra="allow")

    behavior_ref: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    confidence: float = 1.0
    idempotency_key: str | None = None


class WaitRequestV1(WireModel):
    minutes: int = 1
    provider: str | None = None
    idempotency_key: str | None = None

    @field_validator("minutes")
    @classmethod
    def _bounded_minutes(cls, value: int) -> int:
        return max(1, min(int(value), 480))


class DialogueRequestV1(WireModel):
    listener_id: str
    topic: str = "general"
    message: str
    provider: str | None = None
    idempotency_key: str | None = None


class OperatorDialogueRequestV1(WireModel):
    speaker_id: str
    listener_id: str
    topic: str = "general"
    message: str
    provider: str | None = None
    idempotency_key: str | None = None


class TradeOfferRequestV1(WireModel):
    recipient_id: str
    give: dict[str, Any] = Field(default_factory=dict)
    want: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class TradeCounterRequestV1(WireModel):
    give: dict[str, Any] = Field(default_factory=dict)
    want: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class RuntimeStartRequestV1(WireModel):
    provider: str | None = None
    max_world_minutes: int = 1440
    max_llm_calls: int = 120
    idempotency_key: str | None = None


class ReasonRequestV1(WireModel):
    reason: str = "web"
    idempotency_key: str | None = None


class InstanceRequestV1(WireModel):
    package_id: str | None = None
    instance_id: str | None = None
    db_path: str | None = None
    activate: bool = False
    idempotency_key: str | None = None


class DecideRequestV1(WireModel):
    agent_id: str
    role: str = "agent_decision"
    idempotency_key: str | None = None


class SimulateRequestV1(WireModel):
    minutes: int = 1
    provider: str | None = None
    wait_cooldown_minutes: int = 30
    llm_error_cooldown_minutes: int = 60
    idempotency_key: str | None = None


class InjectRequestV1(WireModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_id: str | None = None
    idempotency_key: str | None = None


class SnapshotRequestV1(WireModel):
    label: str = "manual"
    idempotency_key: str | None = None


class SnapshotRestoreRequestV1(WireModel):
    snapshot_id: str
    idempotency_key: str | None = None


class GenericCommandV1(BaseModel):
    """Command envelope for authoring calls whose payload is world-authored."""

    model_config = ConfigDict(extra="allow")

    idempotency_key: str | None = None


REQUEST_MODELS: dict[str, type[BaseModel]] = {
    "session": SessionRequestV1,
    "enter_player": EnterPlayerRequestV1,
    "action": ActionRequestV1,
    "wait": WaitRequestV1,
    "dialogue": DialogueRequestV1,
    "operator_dialogue": OperatorDialogueRequestV1,
    "trade_offer": TradeOfferRequestV1,
    "trade_counter": TradeCounterRequestV1,
    "runtime_start": RuntimeStartRequestV1,
    "reason": ReasonRequestV1,
    "instance": InstanceRequestV1,
    "decide": DecideRequestV1,
    "simulate": SimulateRequestV1,
    "inject": InjectRequestV1,
    "snapshot": SnapshotRequestV1,
    "snapshot_restore": SnapshotRestoreRequestV1,
    "command": GenericCommandV1,
}

RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "session": SessionV1,
    "world": WorldInfoV1,
    "instances": InstanceListV1,
    "runtime": RuntimeV1,
    "player_entry": PlayerEntryV1,
    "player_view": PlayerViewV1,
    "task": TaskListV1,
    "task_accepted": TaskAcceptedV1,
    "stream": StreamEventV1,
}


def json_schema_map() -> dict[str, Any]:
    """Return every public request/response schema keyed by its role."""

    schemas: dict[str, Any] = {"version": SCHEMA_VERSION, "requests": {}, "responses": {}}
    for name, model in REQUEST_MODELS.items():
        schemas["requests"][name] = model.model_json_schema()
    for name, model in RESPONSE_MODELS.items():
        schemas["responses"][name] = model.model_json_schema()
    return schemas
