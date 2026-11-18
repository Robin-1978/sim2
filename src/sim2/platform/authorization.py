"""Platform owner for access requests, delegation decisions and revocation."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..world.authorization import authorization_for_behavior, behavior_authorization_spec
from .services import PlatformService


class AuthorizationRejected(Exception):
    """One typed domain refusal raised inside a caller-owned transaction."""

    def __init__(self, failed_check: str, message: str, **extra: Any):
        super().__init__(message)
        self.failed_check = str(failed_check)
        self.message = str(message)
        self.extra = dict(extra)

    def result(self) -> dict[str, Any]:
        return {
            "status": "rejected",
            "failed_check": self.failed_check,
            "message": self.message,
            "retryable": True,
            **self.extra,
        }


class AuthorizationService(PlatformService):
    """Read eligibility and execute one authorization transaction."""

    # -- shared evaluator -------------------------------------------------
    def eligibility(
        self,
        agent: dict[str, Any],
        behavior: dict[str, Any],
        parameters: dict[str, Any] | None = None,
        *,
        decision: str | None = None,
    ) -> dict[str, Any]:
        return authorization_for_behavior(
            self.store,
            agent,
            behavior,
            parameters,
            rule_compiler=self._dependency("rule_compiler"),
            decision=decision,
        )

    def operation_of(self, behavior: dict[str, Any]) -> str | None:
        value = behavior.get("authorization_operation") if isinstance(behavior, dict) else None
        return str(value) if value else None

    # -- request creation -------------------------------------------------
    def create_request(
        self,
        agent: dict[str, Any],
        payload: dict[str, Any],
        *,
        minute: int | None = None,
        validate_causal: bool = True,
    ) -> dict[str, Any]:
        """Create one pending access request and its audit event.

        ``validate_causal`` exists for harness fixtures that must create a
        request without a preceding denial (for example the self-approval
        fixture).  The Agent-facing access-request behavior always uses the
        validating path.
        """

        minute = int(self.store.repositories.metadata.get("world_minute", 0) if minute is None else minute)
        requester = str(agent.get("id") or payload.get("requester") or "")
        request_id = str(payload.get("request_id") or f"req_{uuid4().hex[:12]}")
        existing = self.store.repositories.authorization.get_request(request_id)
        if existing is not None:
            same = (
                str(existing.get("requester")) == requester
                and str(existing.get("target")) == str(payload.get("target") or existing.get("target"))
                and str(existing.get("behavior_id")) == str(payload.get("behavior_id") or existing.get("behavior_id"))
            )
            if not same:
                raise AuthorizationRejected("request_id_conflict", "This request id already belongs to a different request.")
            return {"request": existing, "created": False}

        based_on_event = payload.get("based_on_event")
        event = None
        if based_on_event not in (None, ""):
            try:
                event_id = int(str(based_on_event))
            except (TypeError, ValueError):
                raise AuthorizationRejected("invalid_causal_reference", "based_on_event must identify one recorded denial event.") from None
            events = self.store.repositories.events.events_by_ids([event_id])
            event = events[0] if events else None
        elif validate_causal:
            raise AuthorizationRejected("invalid_causal_reference", "An access request must cite the denial event it is based on.")

        behavior_label = str(payload.get("behavior") or "").strip()
        behavior_id = str(payload.get("behavior_id") or "").strip()
        target = str(payload.get("target") or "").strip()
        scope = str(payload.get("scope") or "").strip()

        if validate_causal:
            if event is None:
                raise AuthorizationRejected("invalid_causal_reference", "The cited denial event does not exist.")
            data = {key: value for key, value in event.items() if key not in {"event_id", "event_type", "actor_id", "world_minute"}}
            if str(event.get("event_type") or "") != "behavior.denied":
                raise AuthorizationRejected("invalid_causal_reference", "The cited event is not a behavior denial.")
            if str(event.get("actor_id") or "") != requester:
                raise AuthorizationRejected("invalid_causal_reference", "The cited denial belongs to a different actor.")
            if int(event.get("world_minute", 0) or 0) > minute:
                raise AuthorizationRejected("invalid_causal_reference", "The cited denial has not happened yet.")
            event_behavior = str(data.get("behavior") or "")
            event_behavior_id = str(data.get("behavior_id") or "")
            event_target = str(data.get("target") or "")
            if behavior_label and behavior_label not in {event_behavior, event_behavior_id}:
                raise AuthorizationRejected("invalid_causal_reference", "The requested behavior does not match the cited denial.")
            if behavior_id and event_behavior_id and behavior_id != event_behavior_id:
                raise AuthorizationRejected("invalid_causal_reference", "The requested behavior does not match the cited denial.")
            if not behavior_id:
                behavior_id = event_behavior_id
            behavior_label = event_behavior or behavior_label
            if target:
                canonical_target = self.targets.canonical_id(target)
                target = str(canonical_target or target)
            if target and target != event_target:
                raise AuthorizationRejected("invalid_causal_reference", "The requested target does not match the cited denial.")
            target = target or event_target
            # Scope is derived from the denied behavior's configured
            # authorization rule.  It is not a free-form model choice: a
            # delegation granted for a different scope would not authorize the
            # behavior the denial blocked.
            denied_behavior = next(
                (item for item in self.store.repositories.entities.get_definitions("bt_def") if str(item.get("id")) == behavior_id),
                {},
            )
            spec = behavior_authorization_spec(self._dependency("rule_compiler"), denied_behavior)
            scope = str((spec or {}).get("scope") or data.get("scope") or "")
            if not behavior_label or not behavior_id or not target or not scope:
                raise AuthorizationRejected("invalid_causal_reference", "The cited denial does not carry a complete behavior, target and scope.")
        else:
            behavior_label = behavior_label or behavior_id
            if not behavior_id or not target or not scope:
                raise AuthorizationRejected("invalid_causal_reference", "A request needs behavior, target and scope.")

        request = {
            "request_id": request_id,
            "requester": requester,
            "behavior": behavior_label,
            "behavior_id": behavior_id,
            "target": target,
            "scope": scope,
            "reason": str(payload.get("reason") or ""),
            "based_on_event": int(based_on_event) if based_on_event not in (None, "") else None,
            "status": "pending",
            "created_minute": minute,
            "resolved_minute": None,
            "resolved_by": None,
        }
        with self.store.transaction():
            self.store.repositories.authorization.put_request(request)
            self._context.services["events"].publish(
                "access.requested",
                requester,
                minute,
                {
                    "request_id": request_id,
                    "requester": requester,
                    "behavior": behavior_label,
                    "behavior_id": behavior_id,
                    "target": target,
                    "scope": scope,
                    "reason": request["reason"],
                    "based_on_event": request["based_on_event"],
                    "minute": minute,
                },
                wake="configured",
            )
        return {"request": request, "created": True}

    # -- decisions --------------------------------------------------------
    def approve(
        self,
        agent: dict[str, Any],
        request: dict[str, Any],
        *,
        minute: int | None = None,
        delegation_id: str | None = None,
    ) -> dict[str, Any]:
        minute = int(self.store.repositories.metadata.get("world_minute", 0) if minute is None else minute)
        delegation_key = str(delegation_id or f"del_{uuid4().hex[:12]}")
        delegation = {
            "delegation_id": delegation_key,
            "source": str(request.get("requester")),
            "target": str(request.get("target")),
            "type": "delegation",
            "granted_by": str(agent.get("id")),
            "scope": str(request.get("scope")),
            "status": "active",
            "granted_at": minute,
            "expires_minute": None,
            "revoked_at": None,
            "revoked_by": None,
            "request_id": str(request.get("request_id")),
        }
        resolved = dict(request)
        resolved.update({"status": "approved", "resolved_minute": minute, "resolved_by": str(agent.get("id"))})
        with self.store.transaction():
            self.store.repositories.authorization.put_delegation(delegation)
            self.store.repositories.authorization.update_request(resolved)
            self._context.services["events"].append(
                "access.approved",
                str(agent.get("id")),
                minute,
                {
                    "request_id": str(request.get("request_id")),
                    "approver": str(agent.get("id")),
                    "subject": str(request.get("requester")),
                    "target": str(request.get("target")),
                    "scope": str(request.get("scope")),
                    "delegation_id": delegation_key,
                    "minute": minute,
                },
            )
        return {"request": resolved, "delegation": delegation}

    def reject(self, agent: dict[str, Any], request: dict[str, Any], *, reason: str = "", minute: int | None = None) -> dict[str, Any]:
        minute = int(self.store.repositories.metadata.get("world_minute", 0) if minute is None else minute)
        resolved = dict(request)
        resolved.update({"status": "rejected", "resolved_minute": minute, "resolved_by": str(agent.get("id"))})
        with self.store.transaction():
            self.store.repositories.authorization.update_request(resolved)
            self._context.services["events"].append(
                "access.rejected",
                str(agent.get("id")),
                minute,
                {
                    "request_id": str(request.get("request_id")),
                    "approver": str(agent.get("id")),
                    "reason": str(reason or "rejected"),
                    "minute": minute,
                },
            )
        return {"request": resolved}

    def revoke(self, agent: dict[str, Any], delegation: dict[str, Any], *, reason: str = "", minute: int | None = None) -> dict[str, Any]:
        minute = int(self.store.repositories.metadata.get("world_minute", 0) if minute is None else minute)
        revoked = dict(delegation)
        revoked.update({"status": "revoked", "revoked_at": minute, "revoked_by": str(agent.get("id"))})
        with self.store.transaction():
            self.store.repositories.authorization.update_delegation(revoked)
            self._context.services["events"].append(
                "delegation.revoked",
                str(agent.get("id")),
                minute,
                {
                    "delegation_id": str(delegation.get("delegation_id")),
                    "revoked_by": str(agent.get("id")),
                    "reason": str(reason or "revoked"),
                    "target": str(delegation.get("target")),
                    "minute": minute,
                },
            )
        return {"delegation": revoked}


def withdrawal_effect(behavior: dict[str, Any], action_defs: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Return the configured two-phase withdrawal effect for one behavior."""

    for node in behavior.get("nodes", []) or []:
        if not isinstance(node, dict) or node.get("type") != "action":
            continue
        action = action_defs.get(str(node.get("action_id"))) or {}
        for effect in action.get("effects", []) or []:
            if isinstance(effect, dict) and str(effect.get("type") or effect.get("op") or "") == "resource_withdrawal":
                return dict(effect)
    return None
