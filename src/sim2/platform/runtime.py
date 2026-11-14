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
from ..persistence.uow import UnitOfWork
from ..world.schema import AttributeSchema
from .behavior_service import BehaviorExecutionService
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
        self.uow = UnitOfWork(self.store)
        self.view = AgentViewCompiler(self.store)
        self.perception = PerceptionService(self.store)
        self.memory = MemoryService(self.store)
        rules = self.store.get_meta("rules", {}) or {}
        self.attribute_schema = AttributeSchema(self.store.get_meta("attributes", []) or [], rules)
        self.conditions = ConditionEngine(rules)
        self.effects = EffectPipeline(self.store.get_meta("attributes", []) or [])
        self.targets = TargetResolver(self.store)
        self.resources = ResourceLedger(self.store)
        self._service_context = PlatformContext(self, {
            "store": self.store,
            "uow": self.uow,
            "view": self.view,
            "perception": self.perception,
            "memory": self.memory,
            "attribute_schema": self.attribute_schema,
            "conditions": self.conditions,
            "effects": self.effects,
            "targets": self.targets,
            "resources": self.resources,
            "rule_compiler": None,
            "wakes": None,
            "snapshots": None,
            "proposal_evaluator": None,
        })
        self._services = build_platform_services(self._service_context)
        self.rule_compiler = self._new_rule_compiler()
        self._service_context.set_dependency("rule_compiler", self.rule_compiler)
        self.wakes = WakeService(self.uow, self._resolve_evolution_consumer)
        self._service_context.set_dependency("wakes", self.wakes)
        self.snapshots = SnapshotService(self.store)
        self._service_context.set_dependency("snapshots", self.snapshots)
        self.proposal_evaluator = WorldProposalEvaluator(self.store, self.targets)
        self._service_context.set_dependency("proposal_evaluator", self.proposal_evaluator)

    def __getattr__(self, name: str) -> Any:
        """Expose service-owned operations through the stable Platform facade."""

        context = self.__dict__.get("_service_context")
        if context is None:
            raise AttributeError(name)
        return context.resolve(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep explicit test/instrumentation overrides on their owner service."""

        context = self.__dict__.get("_service_context")
        if context is not None and name not in context._DEPENDENCY_NAMES:
            for service in context.services.values():
                try:
                    object.__getattribute__(service, name)
                except AttributeError:
                    continue
                object.__setattr__(service, name, value)
                return
        object.__setattr__(self, name, value)

    @staticmethod
    def _resolve_parameters(parameters: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resolve declarative ``context.*`` values without requiring a Platform instance."""

        return BehaviorExecutionService._resolve_parameters(parameters, state)

    def close(self) -> None:
        self.store.close()
