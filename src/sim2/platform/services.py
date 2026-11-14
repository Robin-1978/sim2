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

    def __init__(self, host: Any) -> None:
        self.host = host
        self.services: dict[str, PlatformService] = {}

    def resolve(self, name: str, requester: PlatformService | None = None) -> Any:
        """Resolve a shared capability without recursing through a proxy."""

        try:
            return object.__getattribute__(self.host, name)
        except AttributeError:
            pass
        for service in self.services.values():
            if service is requester:
                continue
            try:
                return object.__getattribute__(service, name)
            except AttributeError:
                continue
        raise AttributeError(name)


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
        setattr(self._context.host, name, value)


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
