"""One pure authorization decision shared by view, preview and admission.

The evaluator is a decision function over plain data.  It never writes state,
emits events or reads a clock.  Platform services own the transaction and the
store-backed projector below owns reading the current snapshot.
"""

from __future__ import annotations

from typing import Any


RESOURCE_ACCESS = "resource_access"
REQUEST_DECISION = "request_decision"
DELEGATION_REVOCATION = "delegation_revocation"
VIOLATION_REVIEW = "violation_review"

APPROVAL_CHECK_ORDER = (
    "self_approval_forbidden",
    "missing_stewardship",
    "request_not_pending",
    "request_subject_mismatch",
    "request_target_mismatch",
    "request_scope_mismatch",
    "duplicate_delegation",
)


def _reason(code: str, *, attemptable: bool, rule_id: str, authority: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "type": str(code),
        "attemptable": bool(attemptable),
        "rule_id": str(rule_id),
        "authority": dict(authority),
        "message": str(message),
    }


def behavior_authorization_spec(rule_compiler: Any, behavior: dict[str, Any], *, target: str | None = None) -> dict[str, Any] | None:
    """Return the configured authorization rule that governs one behavior.

    A capability may bind different resources, each with its own stewardship
    rule.  When ``target`` is supplied, prefer the matching rule instead of
    returning the first rule for the behavior id.
    """

    if rule_compiler is None or not isinstance(behavior, dict):
        return None
    fallback: dict[str, Any] | None = None
    for raw in rule_compiler.all_rules():
        compiled = rule_compiler.compile(raw)
        effect = compiled.get("effect") or {}
        if str(effect.get("type") or "").casefold() != "authorization":
            continue
        if not rule_compiler.behavior_matches(raw, behavior):
            continue
        mode = str(effect.get("mode") or RESOURCE_ACCESS)
        rule_id = str(raw.get("id") or compiled.get("name") or "authorization")
        configured_authority = effect.get("authority") if isinstance(effect.get("authority"), dict) else {}
        default_type = "rule" if mode == RESOURCE_ACCESS else "institution"
        authority = {
            "type": str(configured_authority.get("type") or default_type),
            "id": str(configured_authority.get("id") or (rule_id if mode == RESOURCE_ACCESS else f"{mode}_authority")),
        }
        spec = {
            "rule_id": rule_id,
            "mode": mode,
            "target": str(effect.get("target") or ""),
            "scope": str(effect.get("scope") or ""),
            "authority": authority,
        }
        if target and spec["target"] and spec["target"] == target:
            return spec
        if fallback is None:
            fallback = spec
    return fallback


def _allow(spec: dict[str, Any], authorization: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "available": True,
        "reasons": [],
        "mode": spec.get("mode"),
        "rule_id": spec.get("rule_id"),
        "authorization": authorization,
        **extra,
    }


def _deny(spec: dict[str, Any], code: str, *, attemptable: bool = True, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "available": False,
        "reasons": [
            _reason(
                code,
                attemptable=attemptable,
                rule_id=str(spec.get("rule_id") or "authorization"),
                authority=dict(spec.get("authority") or {"type": "rule", "id": spec.get("rule_id")}),
                message=message,
            )
        ],
        "mode": spec.get("mode"),
        "rule_id": spec.get("rule_id"),
        "authorization": None,
        **extra,
    }


def authorization_decision(agent: dict[str, Any], action: dict[str, Any], action_context: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    """Return the current eligibility for one authorization-gated action.

    ``action`` carries the configured spec.  ``action_context`` carries the
    concrete request/delegation/proposed values.  ``world`` carries the current
    stewardship and delegation projection plus ``minute``.
    """

    spec = dict(action.get("spec") or {})
    if not spec:
        return {"available": True, "reasons": [], "mode": None, "rule_id": None, "authorization": None}
    mode = str(spec.get("mode") or RESOURCE_ACCESS)
    actor_id = str(agent.get("id") or "")
    stewardships = world.get("stewardships") or {}
    stewarded_targets = {str(item) for item in (stewardships.get(actor_id) or set())}
    delegations = list(world.get("delegations") or [])

    if mode == RESOURCE_ACCESS:
        target = str(action_context.get("target") or spec.get("target") or "")
        scope = str(action_context.get("scope") or spec.get("scope") or "")
        if not target:
            return _deny(spec, "missing_delegation", attemptable=False, message="No configured resource target was supplied.")
        if target in stewarded_targets:
            return _allow(spec, {"type": "stewardship", "id": target})
        restrictions = list(world.get("restrictions") or [])
        restriction = next(
            (
                item for item in restrictions
                if str(item.get("actor") or "") == actor_id
                and str(item.get("target") or "") == target
                and (not scope or str(item.get("scope") or "") == scope)
                and str(item.get("status") or "active") == "active"
            ),
            None,
        )
        if restriction is not None:
            return _deny(
                spec,
                "access_restricted",
                attemptable=False,
                message="A steward restriction blocks delegated access until it is forgiven.",
                restriction=restriction,
            )
        matching = [
            item for item in delegations
            if str(item.get("source")) == actor_id
            and str(item.get("target")) == target
            and (not scope or str(item.get("scope")) == scope)
        ]
        active = [item for item in matching if str(item.get("status") or "active") == "active"]
        if active:
            chosen = sorted(active, key=lambda item: (int(item.get("granted_at", 0) or 0), str(item.get("delegation_id"))))[0]
            return _allow(spec, {"type": "delegation", "id": str(chosen.get("delegation_id"))})
        if any(str(item.get("status") or "") == "revoked" for item in matching):
            return _deny(spec, "delegation_revoked", message="The delegation for this resource was revoked.")
        return _deny(spec, "missing_delegation", message="This character has no stewardship or active delegation for the resource.")

    if mode == REQUEST_DECISION:
        request = action_context.get("request")
        if not request:
            return _deny(spec, "request_not_found", attemptable=False, message="No pending access request is available to decide.")
        decision = str(action_context.get("decision") or "approve")
        if str(request.get("requester") or "") == actor_id:
            return _deny(spec, "self_approval_forbidden", message="The requester cannot decide their own request.")
        if str(request.get("target") or "") not in stewarded_targets:
            return _deny(spec, "missing_stewardship", message="Only a steward of the request target can decide this request.")
        if str(request.get("status") or "pending") != "pending":
            return _deny(spec, "request_not_pending", message="This access request is already resolved.")
        if decision == "approve":
            proposed = dict(action_context.get("proposed") or {})
            if str(proposed.get("subject") or request.get("requester")) != str(request.get("requester")):
                return _deny(spec, "request_subject_mismatch", message="The approval subject does not match the request requester.")
            if str(proposed.get("target") or request.get("target")) != str(request.get("target")):
                return _deny(spec, "request_target_mismatch", message="The approval target does not match the request target.")
            if str(proposed.get("scope") or request.get("scope")) != str(request.get("scope")):
                return _deny(spec, "request_scope_mismatch", message="The approval scope does not match the request scope.")
            duplicate = next(
                (
                    item for item in delegations
                    if str(item.get("source")) == str(request.get("requester"))
                    and str(item.get("target")) == str(request.get("target"))
                    and str(item.get("scope")) == str(request.get("scope"))
                    and str(item.get("status") or "active") == "active"
                ),
                None,
            )
            if duplicate is not None:
                return _deny(spec, "duplicate_delegation", message="An active delegation already covers this request tuple.")
        return _allow(spec, request=request, proposed=dict(action_context.get("proposed") or {}))

    if mode == DELEGATION_REVOCATION:
        delegation = action_context.get("delegation")
        if not delegation:
            return _deny(spec, "delegation_not_found", attemptable=False, message="No active delegation is available to revoke.")
        if str(delegation.get("status") or "active") != "active":
            return _deny(spec, "delegation_not_active", message="This delegation is already inactive.")
        if str(delegation.get("target") or "") not in stewarded_targets:
            return _deny(spec, "missing_stewardship", message="Only a steward of the target can revoke this delegation.")
        return _allow(spec, delegation=delegation)

    if mode == VIOLATION_REVIEW:
        violation = action_context.get("violation")
        if not violation:
            return _deny(spec, "violation_not_found", attemptable=False, message="No violation is available to review.")
        status = str(violation.get("status") or "undetected")
        if status == "undetected":
            return _deny(spec, "violation_not_observed", message="An undetected violation is not available to the steward.")
        if status not in {"observed", "sanctioned"}:
            return _deny(spec, "violation_already_resolved", message="This violation is already resolved.")
        if str(violation.get("target") or "") not in stewarded_targets:
            return _deny(spec, "missing_stewardship", message="Only a steward of the target can review this violation.")
        return _allow(spec, violation=violation)

    return _deny(spec, "unsupported_authorization_mode", attemptable=False, message="The configured authorization mode is not supported.")


def stewardship_targets(store: Any, actor_id: str) -> list[str]:
    return store.repositories.entities.stewardship_targets(actor_id)


def _stewardship_map(store: Any) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for item in store.repositories.entities.list_relationships():
        if str(item.get("type") or "") != "stewardship":
            continue
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        if source and target:
            result.setdefault(source, set()).add(target)
    return result


def _pending_request_for(store: Any, actor_id: str, stewarded: set[str], request_id: str | None) -> dict[str, Any] | None:
    repo = store.repositories.authorization
    if request_id:
        return repo.get_request(str(request_id))
    candidates = [
        item for item in repo.list_requests(status="pending", limit=200)
        if str(item.get("requester") or "") == actor_id or str(item.get("target") or "") in stewarded
    ]
    return candidates[0] if candidates else None


def _delegation_for(store: Any, actor_id: str, stewarded: set[str], delegation_id: str | None) -> dict[str, Any] | None:
    repo = store.repositories.authorization
    if delegation_id:
        return repo.get_delegation(str(delegation_id))
    active = [item for item in repo.list_delegations(status="active", limit=200) if str(item.get("target") or "") in stewarded]
    if active:
        return active[0]
    owned = repo.list_delegations(status="active", source_id=actor_id, limit=1)
    return owned[0] if owned else None


def authorization_for_behavior(
    store: Any,
    agent: dict[str, Any],
    behavior: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    *,
    rule_compiler: Any,
    request_id: str | None = None,
    delegation_id: str | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    """Project the current world into the pure evaluator for one behavior."""

    spec = behavior_authorization_spec(rule_compiler, behavior)
    actor_id = str(agent.get("id") or "")
    values = dict(parameters or {})
    if spec is not None and str(spec.get("mode") or RESOURCE_ACCESS) == RESOURCE_ACCESS:
        from ..actions.targets import TargetResolver

        resolver = TargetResolver(store)
        bound = values.get("resource_id") or values.get("resource") or values.get("target") or ""
        bound_id = (resolver.canonical_id(bound) if bound else None) or str(bound or "")
        if bound_id:
            spec = behavior_authorization_spec(rule_compiler, behavior, target=bound_id) or spec
    if spec is None:
        return {"available": True, "reasons": [], "mode": None, "rule_id": None, "authorization": None, "gated": False}
    stewardships = _stewardship_map(store)
    stewarded = stewardships.get(actor_id, set())
    mode = str(spec.get("mode") or RESOURCE_ACCESS)
    action_context: dict[str, Any] = {"target": spec.get("target"), "scope": spec.get("scope"), "decision": decision}

    if mode == REQUEST_DECISION:
        if request_id is None:
            request_id = values.get("request_id")
        request = _pending_request_for(store, actor_id, stewarded, request_id)
        action_context["request"] = request
        # A model may name the requester or target with its visible name.
        # Compare canonical identifiers so a valid approval is not rejected
        # merely because the prompt shows a visible name instead of a stable id.
        from ..actions.targets import TargetResolver

        resolver = TargetResolver(store)
        proposed_subject = values.get("subject")
        proposed_target = values.get("target")
        action_context["proposed"] = {
            "subject": (resolver.canonical_id(proposed_subject) if proposed_subject else None) or proposed_subject,
            "target": (resolver.canonical_id(proposed_target) if proposed_target else None) or proposed_target,
            "scope": values.get("scope"),
        }
        if not decision:
            # The behavior's configured operation supplies the decision; when a
            # caller omits it an approval is assumed because that is the only
            # decision that creates a delegation.
            decision = "approve"
            action_context["decision"] = decision
    elif mode == DELEGATION_REVOCATION:
        if delegation_id is None:
            delegation_id = values.get("delegation_id")
        action_context["delegation"] = _delegation_for(store, actor_id, stewarded, delegation_id)
    elif mode == VIOLATION_REVIEW:
        violation_id = str(values.get("violation_id") or "").strip()
        if violation_id:
            violation = store.repositories.contested.get_violation(violation_id)
        else:
            # A decision view has no concrete violation parameter yet.  Resolve
            # the first reviewable violation for this steward so the behavior
            # is visible as executable; submission still binds and checks the
            # explicit id.
            violation = next(
                (
                    item for item in store.repositories.contested.list_violations(
                        statuses=("observed", "sanctioned"), limit=200,
                    )
                    if str(item.get("target") or "") in stewarded
                ),
                None,
            )
        action_context["violation"] = violation
        action_context["decision"] = str(values.get("decision") or "sanction")

    world = {
        "stewardships": stewardships,
        "delegations": store.repositories.authorization.list_delegations(limit=500),
        "restrictions": store.repositories.contested.list_restrictions(status="active", limit=500),
        "minute": int(store.repositories.metadata.get("world_minute", 0) or 0),
    }
    result = authorization_decision(agent, {"behavior_id": str(behavior.get("id") or ""), "spec": spec}, action_context, world)
    result["gated"] = True
    return result
