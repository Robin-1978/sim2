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
    context is assembled once by Platform and exposes only declared shared
    dependencies plus an explicit service registry. Cross-service calls must
    name their owning service (for example ``context.services["events"]``);
    there is no method-name lookup or fallback dispatch.
    """

    _DEPENDENCY_NAMES = frozenset({
        "store",
        "uow",
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

    def register_services(self, services: dict[str, PlatformService]) -> None:
        """Register the named services used for explicit cross-service calls."""

        self.services = dict(services)

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
    """Base for services extracted from the former Platform mixins."""

    def __init__(self, context: PlatformContext) -> None:
        object.__setattr__(self, "_context", context)

    def _dependency(self, name: str) -> Any:
        """Read one declared dependency without arbitrary attribute lookup."""

        return self._context.dependencies[name]

    @property
    def store(self) -> Any:
        return self._dependency("store")

    @property
    def uow(self) -> Any:
        return self._dependency("uow")

    @property
    def view(self) -> Any:
        return self._dependency("view")

    @property
    def perception(self) -> Any:
        return self._dependency("perception")

    @property
    def memory(self) -> Any:
        return self._dependency("memory")

    @property
    def attribute_schema(self) -> Any:
        return self._dependency("attribute_schema")

    @property
    def conditions(self) -> Any:
        return self._dependency("conditions")

    @property
    def effects(self) -> Any:
        return self._dependency("effects")

    @property
    def targets(self) -> Any:
        return self._dependency("targets")

    @property
    def resources(self) -> Any:
        return self._dependency("resources")

    @property
    def rule_compiler(self) -> Any:
        return self._dependency("rule_compiler")

    @property
    def wakes(self) -> Any:
        return self._dependency("wakes")

    @property
    def snapshots(self) -> Any:
        return self._dependency("snapshots")

    @property
    def proposal_evaluator(self) -> Any:
        return self._dependency("proposal_evaluator")


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

    # Imported lazily because the concrete service modules use
    # PlatformService as their base class. Keeping the factory import local
    # avoids a module cycle while preserving one construction point.
    from .api_service import RuntimeApiService
    from .behavior_service import BehaviorExecutionService, BehaviorIntentService
    from .events import EventService
    from .evolution_service import EvolutionService
    from .world_tick_service import WorldTickService

    services: dict[str, PlatformService] = {
        "events": EventService(context),
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
    context.register_services(services)
    return services
