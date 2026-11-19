"""Generic contested-resource effects: take feasibility, witnesses and violations.

``resource_take`` is a Platform primitive.  It knows a resource id, amount,
scope and mode, but never a world-specific resource name.  The completing
transaction owns the stock change, the violation record, the witness set and
the social consequences as one atomic commit.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..world.contested import resource_take_effect
from .services import PlatformService


class ContestedRejected(Exception):
    """One typed refusal raised by the contested-resource service."""

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


class ContestedService(PlatformService):
    """Physical feasibility and the atomic completion of one resource take."""

    def effect_for_behavior(
        self,
        behavior: dict[str, Any],
        action_defs: dict[str, dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        definitions = action_defs or {
            item["id"]: item for item in self.store.repositories.entities.get_definitions("action_def")
        }
        behavior_defs = {
            item["id"]: item for item in self.store.repositories.entities.get_definitions("bt_def")
        }
        return resource_take_effect(behavior, definitions, behavior_defs, parameters)

    def feasibility(self, agent: dict[str, Any], effect: dict[str, Any]) -> dict[str, Any] | None:
        """Return an admission rejection for impossible takes, or ``None``.

        The check deliberately does not require the actor to already be at the
        resource location: a configured behavior may travel first.  Completion
        re-checks location and stock against the committed state.
        """

        resource_id = str(effect.get("resource_id") or effect.get("resource") or "").strip()
        if not resource_id:
            return {"failed_check": "resource_missing", "message": "The configured take has no resource target."}
        current = self.resources._world_resource(resource_id)
        if current is None:
            return {"failed_check": "resource_missing", "message": "The resource does not exist."}
        amount = float(effect.get("amount", effect.get("quantity", 1)) or 0)
        if amount <= 0:
            return {"failed_check": "invalid_amount", "message": "A take amount must be positive."}
        if float(current.get("amount", 0) or 0) < amount:
            return {"failed_check": "resource_empty", "message": "The resource has insufficient stock."}
        return None

    def complete_take(
        self,
        agent: dict[str, Any],
        effect: dict[str, Any],
        minute: int,
    ) -> list[str]:
        """Apply one take and its consequences; return typed local errors.

        Any database error still propagates to the caller's savepoint so the
        whole completion can roll back; these strings cover only expected
        feasibility failures.
        """

        resource_ref = str(effect.get("resource_id") or effect.get("resource") or "").strip()
        resource_id = str(self.targets.canonical_id(resource_ref) or resource_ref) if resource_ref else ""
        amount = float(effect.get("amount", effect.get("quantity", 1)) or 0)
        scope = str(effect.get("scope") or "").strip()
        mode = str(effect.get("mode") or "covert")
        state = agent.get("initial_state") or {}
        actor_id = str(agent.get("id") or "")
        current = self.resources._world_resource(resource_id) if resource_id else None
        if current is None:
            return ["resource_missing"]
        if amount <= 0:
            return ["invalid_amount"]
        location_id = str(state.get("location_id") or "")
        try:
            resource = self.store.repositories.entities.get_entity(resource_id)
        except KeyError:
            return ["resource_missing"]
        resource_state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
        resource_location = str(resource_state.get("location_id") or resource.get("location_id") or "")
        if resource_location and location_id != resource_location:
            return ["actor_not_present"]
        before = float(current.get("amount", 0) or 0)
        if before < amount:
            return ["resource_empty"]
        witnesses = self._witnesses(actor_id, location_id, minute)
        violation_id = f"vio_{uuid4().hex[:12]}"
        intent_id = str(state.get("active_bt_instance") or "")
        self.resources._change_world_resource(resource_id, -amount, actor_id=actor_id)
        after = before - amount
        violation = {
            "violation_id": violation_id,
            "intent_id": intent_id,
            "actor": actor_id,
            "target": resource_id,
            "scope": scope,
            "amount": amount,
            "mode": mode,
            "legitimacy": "unauthorized",
            "status": "observed" if witnesses else "undetected",
            "witnesses": witnesses,
            "created_minute": int(minute),
            "resolved_minute": None,
            "resolved_by": None,
        }
        self.store.repositories.contested.put_violation(violation)
        self._context.services["events"].append(
            "resource.taken",
            actor_id,
            minute,
            {
                "intent_id": intent_id,
                "actor": actor_id,
                "target": resource_id,
                "scope": scope,
                "amount": amount,
                "before": before,
                "after": after,
                "mode": mode,
                "witnesses": witnesses,
                "violation_id": violation_id,
                "minute": int(minute),
            },
        )
        self._remember_take(agent, violation, witnesses, resource)
        if witnesses:
            self._context.services["events"].publish(
                "violation.observed",
                actor_id,
                minute,
                {
                    "violation_id": violation_id,
                    "actor": actor_id,
                    "target": resource_id,
                    "scope": scope,
                    "witnesses": witnesses,
                    "minute": int(minute),
                },
                wake="configured",
            )
            self._apply_witness_consequences(agent, violation, witnesses, minute)
        return []

    def _witnesses(self, actor_id: str, location_id: str, minute: int) -> list[str]:
        if not location_id:
            return []
        world = self.store.repositories.metadata.get("world_properties", {}) or {}
        result = []
        for item in self.store.repositories.entities.entities_at_location(location_id):
            if str(item.get("entity_type") or "") != "agent" or str(item.get("id") or "") == actor_id:
                continue
            if self.conditions.evaluate("actor.alive", agent=item, target={}, context={}, world=world):
                result.append(str(item["id"]))
        return sorted(dict.fromkeys(result))

    def _remember_take(
        self,
        agent: dict[str, Any],
        violation: dict[str, Any],
        witnesses: list[str],
        resource: dict[str, Any],
    ) -> None:
        target_name = str(resource.get("name") or violation.get("target") or "the resource")
        witness_names = ", ".join(self._entity_name(item) for item in witnesses) or "no one"
        summary = (
            f"You took {violation.get('amount')} from {target_name}; witnesses: {witness_names}."
            if witnesses else
            f"You took {violation.get('amount')} from {target_name}; no one saw it."
        )
        self.store.repositories.memory.put_memory(
            str(violation["actor"]),
            "violation",
            {
                "summary": summary,
                "violation_id": str(violation["violation_id"]),
                "actor": str(violation["actor"]),
                "target": str(violation["target"]),
                "scope": str(violation["scope"]),
                "amount": float(violation["amount"]),
                "status": str(violation["status"]),
                "witnesses": list(witnesses),
                "minute": int(violation["created_minute"]),
                "private": True,
            },
            tier="short_term",
        )

    def _apply_witness_consequences(
        self,
        agent: dict[str, Any],
        violation: dict[str, Any],
        witnesses: list[str],
        minute: int,
    ) -> None:
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        config = rules.get("contested_actions", {}) if isinstance(rules.get("contested_actions"), dict) else {}
        trust_delta = float(config.get("witness_trust_delta", 0) or 0)
        confidence = float(config.get("witness_memory_confidence", 0.9) or 0.9)
        actor_name = str(agent.get("name") or agent.get("id") or "someone")
        target_name = self._entity_name(str(violation.get("target") or ""))
        for witness_id in witnesses:
            if trust_delta:
                self._change_trust(witness_id, str(violation["actor"]), trust_delta, minute)
            self.store.repositories.memory.put_memory(
                witness_id,
                "violation",
                {
                    "summary": f"You saw {actor_name} take {violation.get('amount')} from {target_name}.",
                    "violation_id": str(violation["violation_id"]),
                    "actor": str(violation["actor"]),
                    "target": str(violation["target"]),
                    "scope": str(violation["scope"]),
                    "amount": float(violation["amount"]),
                    "status": "observed",
                    "confidence": confidence,
                    "minute": int(minute),
                },
                tier="short_term",
            )

    def _change_trust(self, from_id: str, to_id: str, delta: float, minute: int) -> None:
        current = next(
            (
                item for item in self.store.repositories.entities.list_relationships()
                if str(item.get("from")) == str(from_id)
                and str(item.get("to")) == str(to_id)
                and str(item.get("type")) == "trust"
            ),
            None,
        )
        data = dict(current or {})
        for key in ("from", "to", "type"):
            data.pop(key, None)
        value = max(-1.0, min(1.0, float(data.get("relation_strength", 0) or 0) + float(delta)))
        data["relation_strength"] = value
        data["trust"] = value
        self.store.repositories.entities.put_relationship(from_id, to_id, "trust", data)
        self._context.services["events"].append(
            "relationship.changed",
            from_id,
            minute,
            {"target_id": to_id, "relation": "trust", "delta": float(delta), "relation_strength": value},
        )

    def _entity_name(self, entity_id: str) -> str:
        if not entity_id:
            return ""
        try:
            return str(self.store.repositories.entities.get_entity(entity_id).get("name") or entity_id)
        except KeyError:
            return entity_id.replace("_", " ")

    # -- violation review -------------------------------------------------
    def review_violation(
        self,
        agent: dict[str, Any],
        violation: dict[str, Any],
        decision: str,
        *,
        reason: str = "",
        minute: int | None = None,
    ) -> dict[str, Any]:
        """Sanction or forgive one observed violation in a reversible transaction."""

        minute = int(self.store.repositories.metadata.get("world_minute", 0) if minute is None else minute)
        savepoint = f"violation_review_{uuid4().hex}"
        self.store.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            result = self._apply_review(agent, violation, str(decision).strip().casefold(), reason=reason, minute=minute)
            self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return result
        except ContestedRejected:
            self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        except Exception as exc:
            self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise ContestedRejected("violation_review_failed", str(exc)) from exc

    def _apply_review(
        self,
        agent: dict[str, Any],
        violation: dict[str, Any],
        decision: str,
        *,
        reason: str,
        minute: int,
    ) -> dict[str, Any]:
        repo = self.store.repositories.contested
        steward = str(agent.get("id") or "")
        actor_id = str(violation.get("actor") or "")
        target = str(violation.get("target") or "")
        scope = str(violation.get("scope") or "")
        status = str(violation.get("status") or "undetected")
        if status == "forgiven":
            raise ContestedRejected("violation_already_resolved", "This violation is already forgiven.")
        if decision not in {"sanction", "forgive"}:
            raise ContestedRejected("invalid_decision", "A violation review decision must be sanction or forgive.")
        if decision == "sanction":
            if status != "observed":
                raise ContestedRejected("violation_already_resolved", "This violation is already sanctioned.")
            restriction = repo.active_restriction(actor_id, target, scope)
            if restriction is None:
                restriction = {
                    "restriction_id": f"res_{uuid4().hex[:12]}",
                    "actor": actor_id,
                    "target": target,
                    "scope": scope,
                    "status": "active",
                    "violation_id": str(violation["violation_id"]),
                    "created_by": steward,
                    "created_minute": minute,
                    "revoked_by": None,
                    "revoked_minute": None,
                }
                repo.put_restriction(restriction)
            resolved = dict(violation)
            resolved.update({"status": "sanctioned", "resolved_minute": minute, "resolved_by": steward})
            repo.update_violation(resolved)
            self._context.services["events"].append(
                "violation.sanctioned",
                actor_id,
                minute,
                {
                    "violation_id": str(violation["violation_id"]),
                    "actor": actor_id,
                    "steward": steward,
                    "restriction_id": str(restriction["restriction_id"]),
                    "scope": scope,
                    "reason": str(reason or "sanctioned"),
                    "minute": int(minute),
                },
            )
            return {
                "violation_id": str(violation["violation_id"]),
                "status": "sanctioned",
                "restriction_id": str(restriction["restriction_id"]),
            }

        restriction = repo.active_restriction(actor_id, target, scope)
        restriction_id = ""
        if restriction is not None:
            restriction_id = str(restriction["restriction_id"])
            revoked = dict(restriction)
            revoked.update({"status": "revoked", "revoked_by": steward, "revoked_minute": minute})
            repo.update_restriction(revoked)
        resolved = dict(violation)
        resolved.update({"status": "forgiven", "resolved_minute": minute, "resolved_by": steward})
        repo.update_violation(resolved)
        self._context.services["events"].append(
            "violation.forgiven",
            actor_id,
            minute,
            {
                "violation_id": str(violation["violation_id"]),
                "actor": actor_id,
                "steward": steward,
                "restriction_id": restriction_id,
                "scope": scope,
                "reason": str(reason or "forgiven"),
                "minute": int(minute),
            },
        )
        return {
            "violation_id": str(violation["violation_id"]),
            "status": "forgiven",
            "restriction_id": restriction_id,
        }
