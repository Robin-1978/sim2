"""The state one decision view derives, replays and fills while it decides which behaviors to offer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..behavior.estimator import BehaviorDurationEstimator


@dataclass
class DecisionFrame:
    """What one decision view derives from the agent and the current world."""

    agent_id: str
    agent: dict[str, Any]
    rules: dict[str, Any]
    configured_context: dict[str, Any]
    bt_defs: dict[str, dict[str, Any]]
    action_defs: dict[str, dict[str, Any]]
    recipe_defs: dict[str, dict[str, Any]]
    estimator: BehaviorDurationEstimator
    catalog_ids: list[Any]
    state: dict[str, Any]
    location_id: Any
    safe_location_id: Any
    resource_node: dict[str, str] | None
    health_location_id: Any
    known_route_destinations: Any
    mapped_areas: Any
    researched_sources: Any
    known_fact_keys: list[Any]
    research_sources: list[dict[str, Any]]
    current_location: dict[str, Any]
    participants: list[dict[str, Any]]
    validation_target: dict[str, Any] | None
    available: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Simulation:
    """A behavior replayed step by step against a copy of the agent."""

    agent: dict[str, Any]
    location: dict[str, Any]
    steps: list[str] = field(default_factory=list)
    costs: list[str] = field(default_factory=list)
    impacts: list[str] = field(default_factory=list)
    successes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    unavailable: bool = False
    unavailable_action: str = ""
    unavailable_reason: str | None = None


class Guidance(NamedTuple):
    """How the model is told to choose targets and parameters for one behavior."""

    text: str
    required: bool
    options: list[dict[str, Any]]
    parameter_options: dict[str, list[dict[str, Any]]]
    source_parameter: Any
