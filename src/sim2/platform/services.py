from __future__ import annotations

from typing import Any

from .activity import ActivityRuntimeMixin
from .definition_runtime import DefinitionRuntimeMixin
from .entity_proposals import EntityProposalRuntimeMixin
from .learning import LearningRuntimeMixin
from .social import SocialRuntimeMixin
from .stagnation import StagnationRuntimeMixin
from .story_runtime import StoryRuntimeMixin
from .world_changes import WorldChangesRuntimeMixin


class PlatformContext:
    """Shared runtime capabilities exposed to Platform services.

    Services do not receive a SQLite connection directly from callers.  The
    context is assembled once by Platform and resolves shared dependencies or
    a method owned by another service.  This keeps service construction
    explicit while allowing the existing transactional implementations to be
    migrated incrementally.
    """

    _DEPENDENCY_NAMES = frozenset({
        "store",
        "view",
        "perception",
        "memory",
        "attribute_schema",
        "conditions",
        "effects",
        "targets",
        "resources",
        "rule_compiler",
        "wakes",
        "snapshots",
        "proposal_evaluator",
    })

    def __init__(self, host: Any, dependencies: dict[str, Any]) -> None:
        self.host = host
        unknown = set(dependencies) - self._DEPENDENCY_NAMES
        if unknown:
            raise ValueError(f"unknown PlatformContext dependencies: {sorted(unknown)}")
        self.dependencies = dict(dependencies)
        self.services: dict[str, PlatformService] = {}

    def resolve(self, name: str, requester: PlatformService | None = None) -> Any:
        """Resolve a declared dependency or another registered service method."""

        if name in self.dependencies:
            return self.dependencies[name]
        for service in self.services.values():
            if service is requester:
                continue
            try:
                return object.__getattribute__(service, name)
            except AttributeError:
                continue
        raise AttributeError(name)

    def set_dependency(self, name: str, value: Any) -> None:
        if name not in self._DEPENDENCY_NAMES:
            raise ValueError(f"unknown PlatformContext dependency: {name}")
        self.dependencies[name] = value
        # Platform keeps the public read-only fields for API callers.  The
        # service never reads arbitrary host attributes; this assignment only
        # mirrors a declared dependency after a transactional replacement such
        # as rebuilding the rule compiler.
        object.__setattr__(self.host, name, value)


class PlatformService:
    """Base for services extracted from the former Platform mixins.

    The forwarding path is deliberately centralized here.  It is a migration
    seam: new service code should use the context directly, while existing
    methods can be moved one owner at a time without recreating a second
    Platform implementation.
    """

    def __init__(self, context: PlatformContext) -> None:
        object.__setattr__(self, "_context", context)

    def __getattr__(self, name: str) -> Any:
        return self._context.resolve(name, self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_context":
            object.__setattr__(self, name, value)
            return
        if name in self._context._DEPENDENCY_NAMES:
            self._context.set_dependency(name, value)
            return
        object.__setattr__(self, name, value)


class ActivityService(PlatformService, ActivityRuntimeMixin):
    """Long-running BT and Action execution service."""


class DefinitionService(PlatformService, DefinitionRuntimeMixin):
    """Declarative Action, BT, Recipe, Capability and Attribute registry."""


class EntityProposalService(PlatformService, EntityProposalRuntimeMixin):
    """Validated structural entity proposal service."""


class LearningService(PlatformService, LearningRuntimeMixin):
    """Experience, capability, goal and progress service."""


class SocialService(PlatformService, SocialRuntimeMixin):
    """Dialogue, trade and institution service."""


class StagnationService(PlatformService, StagnationRuntimeMixin):
    """Configured liveness and stagnation signal service."""


class StoryService(PlatformService, StoryRuntimeMixin):
    """Evidence-grounded Story beat service."""


class WorldChangesService(PlatformService, WorldChangesRuntimeMixin):
    """Generic world change and template proposal service."""



def build_platform_services(context: PlatformContext) -> dict[str, PlatformService]:
    """Construct all extracted services in one deterministic order."""

    # Imported lazily because runtime_services uses PlatformService as its
    # base class.  Keeping the factory import local avoids a module cycle while
    # preserving one construction point for the Platform facade.
    from .runtime_services import (
        BehaviorExecutionService,
        BehaviorIntentService,
        EvolutionService,
        RuntimeApiService,
        WorldTickService,
    )

    services: dict[str, PlatformService] = {
        "api": RuntimeApiService(context),
        "intent": BehaviorIntentService(context),
        "execution": BehaviorExecutionService(context),
        "tick": WorldTickService(context),
        "evolution": EvolutionService(context),
        "activity": ActivityService(context),
        "definitions": DefinitionService(context),
        "entities": EntityProposalService(context),
        "learning": LearningService(context),
        "social": SocialService(context),
        "stagnation": StagnationService(context),
        "story": StoryService(context),
        "world_changes": WorldChangesService(context),
    }
    context.services = services
    return services
