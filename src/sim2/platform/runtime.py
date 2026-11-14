from __future__ import annotations

from pathlib import Path
from typing import Any

from ..actions.conditions import ConditionEngine
from ..actions.effects import EffectPipeline
from ..actions.resources import ResourceLedger
from ..actions.targets import TargetResolver
from ..agent_view.compiler import AgentViewCompiler
from ..evolution import WorldProposalEvaluator
from ..memory.service import MemoryService
from ..perception.service import PerceptionService
from ..persistence.store import WorldStore
from ..world.schema import AttributeSchema
from .runtime_services import (
    BehaviorExecutionService,
    BehaviorIntentService,
    EvolutionService,
    RuntimeApiService,
    WorldTickService,
)
from .services import PlatformContext, build_platform_services
from .snapshot_service import SnapshotService
from .wake_service import WakeService


class Platform:
    """The only component allowed to commit world state changes.

    Public operations are composed from focused services. Agents still submit
    declarative intents; Platform services validate and commit them in SQLite
    transactions.
    """

    def __init__(self, db_path: Path):
        self.store = WorldStore(db_path)
        self.view = AgentViewCompiler(self.store)
        self.perception = PerceptionService(self.store)
        self.memory = MemoryService(self.store)
        rules = self.store.get_meta("rules", {}) or {}
        self.attribute_schema = AttributeSchema(self.store.get_meta("attributes", []) or [], rules)
        self.conditions = ConditionEngine(rules)
        self.effects = EffectPipeline(self.store.get_meta("attributes", []) or [])
        self.targets = TargetResolver(self.store)
        self.resources = ResourceLedger(self.store)
        self._service_context = PlatformContext(self)
        self._services = build_platform_services(self._service_context)
        self.rule_compiler = self._new_rule_compiler()
        self.wakes = WakeService(self.store, self._resolve_evolution_consumer)
        self.snapshots = SnapshotService(self.store)
        self.proposal_evaluator = WorldProposalEvaluator(self.store, self.targets)

    def __getattr__(self, name: str) -> Any:
        """Expose service-owned operations through the stable Platform facade."""

        context = self.__dict__.get("_service_context")
        if context is None:
            raise AttributeError(name)
        return context.resolve(name)

    def close(self) -> None:
        self.store.close()
