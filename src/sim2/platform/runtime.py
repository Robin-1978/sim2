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
        self.rule_compiler = self._services["api"]._new_rule_compiler()
        self._service_context.set_dependency("rule_compiler", self.rule_compiler)
        self.wakes = WakeService(self.uow, self._services["api"]._resolve_evolution_consumer)
        self._service_context.set_dependency("wakes", self.wakes)
        self.snapshots = SnapshotService(self.store)
        self._service_context.set_dependency("snapshots", self.snapshots)
        self.proposal_evaluator = WorldProposalEvaluator(self.store, self.targets)
        self._service_context.set_dependency("proposal_evaluator", self.proposal_evaluator)

    # Public operations are explicit facade methods. Each method has one
    # concrete service owner; there is no runtime method-name scan or dynamic
    # fallback that can silently route a new operation to the wrong service.
    def run(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].run(*args, **kwargs)

    def status(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].status(*args, **kwargs)

    def health(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].health(*args, **kwargs)

    def pause(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].pause(*args, **kwargs)

    def resume(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].resume(*args, **kwargs)

    def inject_event(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].inject_event(*args, **kwargs)

    def enter_player(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].enter_player(*args, **kwargs)

    def decision_view(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].decision_view(*args, **kwargs)

    def observer_view(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].observer_view(*args, **kwargs)

    def map_view(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].map_view(*args, **kwargs)

    def timeline(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].timeline(*args, **kwargs)

    def tick_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].tick_snapshot(*args, **kwargs)

    def metrics(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].metrics(*args, **kwargs)

    def export_audit(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].export_audit(*args, **kwargs)

    def snapshot(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].snapshot(*args, **kwargs)

    def restore_snapshot(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["api"].restore_snapshot(*args, **kwargs)

    def submit_behavior(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["intent"].submit_behavior(*args, **kwargs)

    def interrupt_behavior(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["execution"].interrupt_behavior(*args, **kwargs)

    def resume_behavior(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["execution"].resume_behavior(*args, **kwargs)

    def step(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["tick"].step(*args, **kwargs)

    def ready_evolution_tasks(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["evolution"].ready_evolution_tasks(*args, **kwargs)

    def claim_evolution_task(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["evolution"].claim_evolution_task(*args, **kwargs)

    def recover_claimed_evolution_tasks(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["evolution"].recover_claimed_evolution_tasks(*args, **kwargs)

    def complete_evolution_task(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["evolution"].complete_evolution_task(*args, **kwargs)

    def retry_evolution_task(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["evolution"].retry_evolution_task(*args, **kwargs)

    def submit_experience(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["learning"].submit_experience(*args, **kwargs)

    def submit_capability_update(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["learning"].submit_capability_update(*args, **kwargs)

    def submit_world_progress(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["learning"].submit_world_progress(*args, **kwargs)

    def submit_belief_update(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["learning"].submit_belief_update(*args, **kwargs)

    def submit_goal_update(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["learning"].submit_goal_update(*args, **kwargs)

    def submit_behavior_proposal(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["learning"].submit_behavior_proposal(*args, **kwargs)

    def submit_institution_proposal(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].submit_institution_proposal(*args, **kwargs)

    def activate_institution(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].activate_institution(*args, **kwargs)

    def revise_institution(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].revise_institution(*args, **kwargs)

    def count_verified_evidence(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].count_verified_evidence(*args, **kwargs)

    def institution_ledger(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].institution_ledger(*args, **kwargs)

    def calculate_institution_allocation(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].calculate_institution_allocation(*args, **kwargs)

    def submit_dialogue(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].submit_dialogue(*args, **kwargs)

    def create_trade_offer(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].create_trade_offer(*args, **kwargs)

    def accept_trade_offer(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].accept_trade_offer(*args, **kwargs)

    def counter_trade_offer(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].counter_trade_offer(*args, **kwargs)

    def reject_trade_offer(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["social"].reject_trade_offer(*args, **kwargs)

    def submit_entity_proposal(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["entities"].submit_entity_proposal(*args, **kwargs)

    def submit_definition_proposal(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["definitions"].submit_definition_proposal(*args, **kwargs)

    def submit_story_beat(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["story"].submit_story_beat(*args, **kwargs)

    def submit_world_change(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["world_changes"].submit_world_change(*args, **kwargs)

    def validate_world_template(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["world_changes"].validate_world_template(*args, **kwargs)

    def submit_world_template(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["world_changes"].submit_world_template(*args, **kwargs)

    def bootstrap_world_from_template(self, *args: Any, **kwargs: Any) -> Any:
        return self._services["world_changes"].bootstrap_world_from_template(*args, **kwargs)

    @staticmethod
    def _resolve_parameters(parameters: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resolve declarative ``context.*`` values without requiring a Platform instance."""

        return BehaviorExecutionService._resolve_parameters(parameters, state)

    def close(self) -> None:
        self.store.close()
