from __future__ import annotations

import json
import math
from typing import Any
from uuid import uuid4

from ..actions.institutions import describe_institution_terms, entity_quantity
from ..evolution.memory import remember_role

_ALLOCATION_FIELDS = frozenset({"minimum_per_subject", "contribution_share", "category"})
_LEDGER_FIELDS = frozenset({"period", "selectors", "evidence"})
_SELECTOR_SOURCE_FIELDS = ("event_types", "events", "behavior_ids", "behaviors", "action_ids", "actions")
_SELECTOR_FIELDS = frozenset({*_SELECTOR_SOURCE_FIELDS, "category", "points", "amount"})
_SELECTOR_LIMIT = 16


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _short_label(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 60


def institution_term_errors(policy: Any = None, ledger: Any = None) -> list[str]:
    """Return why an institution's allocation policy or ledger would be unusable.

    The same contract guards a new proposal and a later revision, so terms
    that cannot be settled (a negative minimum, a share above one) or that
    would credit every event (a selector without a source) never enter the
    world.  ``None`` and ``{}`` mean "nothing to validate".
    """

    errors: list[str] = []
    if policy not in (None, {}):
        if not isinstance(policy, dict):
            errors.append("policy must be an object")
        else:
            unknown = sorted(str(key) for key in policy if key != "allocation")
            if unknown:
                errors.append(f"policy has unknown field(s): {', '.join(unknown)}; the supported field is allocation")
            allocation = policy.get("allocation")
            if allocation is not None and not isinstance(allocation, dict):
                errors.append("policy.allocation must be an object")
            elif isinstance(allocation, dict):
                unknown = sorted(str(key) for key in allocation if key not in _ALLOCATION_FIELDS)
                if unknown:
                    errors.append(f"policy.allocation has unknown field(s): {', '.join(unknown)}")
                if not _finite_number(allocation.get("minimum_per_subject", 0)) or float(allocation.get("minimum_per_subject", 0)) < 0:
                    errors.append("policy.allocation.minimum_per_subject must be a number of at least 0")
                if not _finite_number(allocation.get("contribution_share", 0)) or not 0 <= float(allocation.get("contribution_share", 0)) <= 1:
                    errors.append("policy.allocation.contribution_share must be a number from 0 to 1")
                if allocation.get("category") is not None and not _short_label(allocation.get("category")):
                    errors.append("policy.allocation.category must be a short non-empty string")
    if ledger not in (None, {}):
        if not isinstance(ledger, dict):
            errors.append("ledger must be an object")
            return errors
        unknown = sorted(str(key) for key in ledger if key not in _LEDGER_FIELDS)
        if unknown:
            errors.append(f"ledger has unknown field(s): {', '.join(unknown)}; use period and selectors")
        period = ledger.get("period")
        if period is not None and not (_short_label(period) or (_finite_number(period) and float(period) > 0)):
            errors.append("ledger.period must be a calendar name such as day or week, or a positive number of minutes")
        selectors = ledger.get("selectors", ledger.get("evidence"))
        if selectors is not None:
            if not isinstance(selectors, list) or not selectors:
                errors.append("ledger.selectors must be a non-empty list")
            elif len(selectors) > _SELECTOR_LIMIT:
                errors.append(f"ledger.selectors may contain at most {_SELECTOR_LIMIT} entries")
            else:
                for index, selector in enumerate(selectors):
                    label = f"ledger.selectors[{index}]"
                    if not isinstance(selector, dict):
                        errors.append(f"{label} must be an object")
                        continue
                    unknown = sorted(str(key) for key in selector if key not in _SELECTOR_FIELDS)
                    if unknown:
                        errors.append(f"{label} has unknown field(s): {', '.join(unknown)}")
                    named = [selector.get(key) for key in _SELECTOR_SOURCE_FIELDS if selector.get(key)]
                    if not named or not all(
                        (isinstance(value, str) and value.strip())
                        or (isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value))
                        for value in named
                    ):
                        errors.append(f"{label} must name event_types, behavior_ids or action_ids; a selector without a source would credit every event")
                    if not _finite_number(selector.get("points", selector.get("amount", 1))) or float(selector.get("points", selector.get("amount", 1))) <= 0:
                        errors.append(f"{label}.points must be a positive number")
                    if selector.get("category") is not None and not _short_label(selector.get("category")):
                        errors.append(f"{label}.category must be a short non-empty string")
    return errors


class SocialRuntimeMixin:
    """Institution, dialogue and atomic trade transactions."""

    def count_verified_evidence(self, selectors: list[dict[str, Any]] | None) -> int:
        """Count unique persisted events matching declarative evidence selectors."""

        matched: set[int] = set()
        for clause in selectors or []:
            if not isinstance(clause, dict):
                continue
            event_types = clause.get("event_types", clause.get("events", []))
            if isinstance(event_types, str):
                event_types = [event_types]
            behavior_ids = clause.get("behavior_ids", clause.get("behaviors", []))
            if isinstance(behavior_ids, str):
                behavior_ids = [behavior_ids]
            action_ids = clause.get("action_ids", clause.get("actions", []))
            if isinstance(action_ids, str):
                action_ids = [action_ids]
            event_types = [str(item) for item in (event_types or [])]
            behavior_ids = {str(item) for item in (behavior_ids or [])}
            action_ids = {str(item) for item in (action_ids or [])}
            if not event_types and not behavior_ids and not action_ids:
                continue
            if event_types:
                placeholders = ",".join("?" for _ in event_types)
                rows = self.store.connection.execute(
                    f"SELECT event_id,data_json FROM event_log WHERE event_type IN ({placeholders})",
                    tuple(event_types),
                ).fetchall()
            else:
                rows = self.store.connection.execute("SELECT event_id,data_json FROM event_log").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["data_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if behavior_ids and str(payload.get("behavior_id") or "") not in behavior_ids:
                    continue
                if action_ids and str(payload.get("action_id") or "") not in action_ids:
                    continue
                matched.add(int(row["event_id"]))
        return len(matched)

    def submit_institution_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Record a proposed social institution without activating it.

        A proposal is world data, not a state mutation.  It creates no metric
        values and no ledger rows until an authorized world decision activates
        it later.
        """

        intent_id = str(uuid4())
        if str(actor_id) != "god":
            return self._context.services["intent"]._reject_intent(
                intent_id,
                actor_id,
                "propose_institution",
                proposal,
                {
                    "status": "rejected",
                    "message": "Only God may create a world institution. Submit the observed problem or let the configured evolution trigger wake God.",
                    "failed_check": "institution_authority",
                    "retryable": False,
                },
            )
        name = str(proposal.get("name") or "").strip()
        purpose = str(proposal.get("purpose") or "").strip()
        if len(name) < 2 or len(name) > 120:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_institution", proposal, {"status": "rejected", "message": "Institution name must be 2 to 120 characters.", "failed_check": "name", "retryable": True})
        if len(purpose) < 12 or len(purpose) > 500:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_institution", proposal, {"status": "rejected", "message": "Institution purpose must be 12 to 500 characters.", "failed_check": "purpose", "retryable": True})
        term_errors = institution_term_errors(proposal.get("policy") or None, proposal.get("ledger") or None)
        if term_errors:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_institution", proposal, {
                "status": "rejected",
                "message": "The institution's policy or ledger could not be settled as written.",
                "failed_check": "institution_terms",
                "errors": term_errors,
                "retryable": True,
            })
        evidence_count = 0
        evidence_sources = [item for item in (proposal.get("evidence_sources") or []) if isinstance(item, dict)]
        minimum_evidence = max(1, int(proposal.get("minimum_evidence", 1) or 1))
        matched_evidence_ids: set[int] = set()
        if evidence_sources:
            for clause in evidence_sources:
                event_types = [str(item) for item in (clause.get("event_types") or ([clause["event_type"]] if clause.get("event_type") else []))]
                behavior_ids = [str(item) for item in (clause.get("behavior_ids") or ([clause["behavior_id"]] if clause.get("behavior_id") else []))]
                if not event_types and not behavior_ids:
                    continue
                if event_types:
                    placeholders = ",".join("?" for _ in event_types)
                    rows = self.store.connection.execute(
                        f"SELECT event_id,data_json FROM event_log WHERE event_type IN ({placeholders})",
                        tuple(event_types),
                    ).fetchall()
                else:
                    # A behavior-only selector is useful for generic evidence
                    # such as any completed run of one BT.  Do not require an
                    # event type merely because the first implementation did.
                    rows = self.store.connection.execute("SELECT event_id,data_json FROM event_log").fetchall()
                for row in rows:
                    try:
                        payload = json.loads(row["data_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if behavior_ids and str(payload.get("behavior_id") or "") not in behavior_ids:
                        continue
                    matched_evidence_ids.add(int(row["event_id"]))
            evidence_count = len(matched_evidence_ids)
        if not evidence_sources or evidence_count < minimum_evidence:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_institution", proposal, {
                "status": "rejected",
                "message": "An institution needs configured evidence sources and the required number of verified events.",
                "failed_check": "institution_evidence",
                "retryable": True,
                "required_evidence": minimum_evidence,
                "observed_evidence": evidence_count,
            })
        existing = self.store.list_institutions(limit=200)
        if any(str(item.get("name", "")).casefold() == name.casefold() and item.get("status") in {"proposed", "active"} for item in existing):
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_institution", proposal, {"status": "rejected", "message": "An institution with this name is already proposed or active.", "failed_check": "duplicate_institution", "retryable": False})
        institution_id = f"institution:{uuid4().hex[:16]}"
        minute = int(self.store.get_meta("world_minute", 0))
        data = {
            "name": name,
            "purpose": purpose,
            "scope": str(proposal.get("scope") or "world"),
            "evidence": str(proposal.get("evidence") or ""),
            "evidence_sources": evidence_sources,
            "minimum_evidence": minimum_evidence,
            "evidence_count_at_creation": evidence_count,
            "ledger": dict(proposal.get("ledger") or {}) if isinstance(proposal.get("ledger"), dict) else {},
            "policy": dict(proposal.get("policy") or {}) if isinstance(proposal.get("policy"), dict) else {},
            "proposed_by": actor_id,
        }
        with self.store.transaction():
            self.store.put_institution(institution_id, "proposed", data, minute)
            event_id = self._context.services["events"].append("institution.proposed", actor_id, minute, {"institution": {"name": name, "purpose": purpose, "scope": data["scope"]}, "status": "proposed"})
            self.wakes.configured(event_id, "institution.proposed", actor_id, minute)
            if actor_id == "god":
                remember_role(self.store, "god", "institution_decision", {
                    "summary": f"Proposed institution {name}: {purpose}.",
                    "institution": name,
                    "status": "proposed",
                }, memory_key=f"god:institution-proposed:{institution_id}", source_event_id=event_id)
            self.store.create_intent(intent_id, actor_id, "propose_institution", {**data, "institution_name": name}, minute)
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "institution": {"name": name, "status": "proposed"}}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def activate_institution(self, actor_id: str, institution: str, reason: str = "") -> dict[str, Any]:
        """Activate one pending institution after a world-level decision."""

        intent_id = str(uuid4())
        if str(actor_id) != "god":
            return self._context.services["intent"]._reject_intent(
                intent_id,
                actor_id,
                "approve_institution",
                {"institution": institution, "reason": reason},
                {
                    "status": "rejected",
                    "message": "Only God may activate a world institution.",
                    "failed_check": "institution_authority",
                    "retryable": False,
                },
            )
        choice = str(institution or "").strip()
        candidates = [item for item in self.store.list_institutions(status="proposed", limit=200) if str(item.get("name", "")).casefold() == choice.casefold() or str(item.get("institution_id", "")) == choice]
        if len(candidates) != 1:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "activate_institution", {"institution": choice, "reason": reason}, {"status": "rejected", "message": "Choose one exact pending institution name.", "failed_check": "institution_choice", "retryable": True, "available_choices": [str(item.get("name", "")) for item in self.store.list_institutions(status="proposed", limit=20)]})
        selected = candidates[0]
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            institution_data = {key: value for key, value in selected.items() if key not in {"institution_id", "status", "created_minute", "activated_minute"}}
            self.store.put_institution(str(selected["institution_id"]), "active", institution_data, int(selected.get("created_minute", minute)), minute)
            event_id = self._context.services["events"].append("institution.activated", actor_id, minute, {"institution": {"name": selected.get("name"), "purpose": selected.get("purpose")}, "reason": reason, "status": "active"})
            self.wakes.configured(event_id, "institution.activated", actor_id, minute)
            if actor_id == "god":
                remember_role(self.store, "god", "institution_decision", {
                    "summary": f"Activated institution {selected.get('name')}: {selected.get('purpose') or ''}.",
                    "institution": selected.get("name"),
                    "status": "active",
                    "reason": reason,
                }, memory_key=f"god:institution-activated:{selected.get('institution_id')}", source_event_id=event_id)
            self.store.create_intent(intent_id, actor_id, "activate_institution", {"institution": choice, "reason": reason}, minute)
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "institution": {"name": selected.get("name"), "status": "active"}}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def _institution_operations_since_revision(self, selected: dict[str, Any]) -> int:
        """Count settled operations (ledger entries, applied allocations) after the latest revision.

        A revision is judged against recorded results, so each one needs fresh
        operating evidence from the terms it replaces.  Event ids are strictly
        increasing, which keeps two revisions in one minute from sharing the
        same evidence.
        """

        baseline = int(selected.get("last_revision_event_id", 0) or 0)
        since = int(selected.get("activated_minute") or 0)
        identities = {str(selected.get("name") or "").casefold(), str(selected.get("institution_id") or "").casefold()} - {""}
        rows = self.store.connection.execute(
            "SELECT data_json FROM event_log WHERE event_id>? AND world_minute>=? "
            "AND event_type IN ('institution.allocation_applied','institution.ledger_entry')",
            (baseline, since),
        ).fetchall()
        count = 0
        for row in rows:
            try:
                payload = json.loads(row["data_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            reference = payload.get("institution")
            label = reference.get("name") if isinstance(reference, dict) else reference
            if str(label or "").casefold() in identities:
                count += 1
        return count

    def revise_institution(
        self,
        actor_id: str,
        institution: str,
        *,
        policy: Any = None,
        ledger: Any = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Revise the allocation policy or ledger selectors of one active institution.

        An institution is world data that must be able to follow its own
        results.  The revision is declarative, validated like a proposal, and
        allowed only after the institution has operated since its latest
        terms.  The ledger period is fixed once active because settlements
        and verified totals are keyed by it.
        """

        intent_id = str(uuid4())
        choice = str(institution or "").strip()
        data = {"institution": choice, "policy": policy, "ledger": ledger, "reason": reason}
        if str(actor_id) != "god":
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "revise_institution", data, {
                "status": "rejected",
                "message": "Only God may revise a world institution.",
                "failed_check": "institution_authority",
                "retryable": False,
            })
        active = self.store.list_institutions(status="active", limit=200)
        candidates = [
            item for item in active
            if str(item.get("name", "")).casefold() == choice.casefold() or str(item.get("institution_id", "")) == choice
        ]
        if len(candidates) != 1:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "revise_institution", data, {
                "status": "rejected",
                "message": "Choose one exact active institution name.",
                "failed_check": "institution_choice",
                "retryable": True,
                "available_choices": [str(item.get("name", "")) for item in active[:20]],
            })
        selected = candidates[0]
        reason_text = str(reason or "").strip()
        if len(reason_text) < 12 or len(reason_text) > 500:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "revise_institution", data, {
                "status": "rejected",
                "message": "Revision reason must cite the recorded result in 12 to 500 characters.",
                "failed_check": "reason",
                "retryable": True,
            })
        if not policy and not ledger:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "revise_institution", data, {
                "status": "rejected",
                "message": "Provide a replacement policy, replacement ledger selectors, or both.",
                "failed_check": "institution_terms",
                "retryable": True,
            })
        errors = institution_term_errors(policy or None, ledger or None)
        old_policy = dict(selected.get("policy") or {}) if isinstance(selected.get("policy"), dict) else {}
        old_ledger = dict(selected.get("ledger") or {}) if isinstance(selected.get("ledger"), dict) else {}
        new_policy = old_policy
        new_ledger = old_ledger
        if policy:
            if isinstance(policy, dict) and not policy.get("allocation"):
                errors.append("policy.allocation is required when replacing the policy")
            new_policy = dict(policy) if isinstance(policy, dict) else old_policy
        if ledger and isinstance(ledger, dict):
            if not ledger.get("selectors"):
                errors.append("ledger.selectors is required when replacing the ledger")
            if "period" in ledger and old_ledger.get("period") not in (None, ledger.get("period")):
                errors.append(f"the ledger period cannot change after activation (it is {old_ledger.get('period')}); omit period")
            new_ledger = {**old_ledger, "selectors": list(ledger.get("selectors") or [])}
            if "period" in ledger:
                new_ledger["period"] = ledger["period"]
            new_ledger.pop("evidence", None)
        if errors:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "revise_institution", data, {
                "status": "rejected",
                "message": "The revised terms could not be settled as written.",
                "failed_check": "institution_terms",
                "errors": errors,
                "retryable": True,
            })
        if new_policy == old_policy and new_ledger == old_ledger:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "revise_institution", data, {
                "status": "rejected",
                "message": "The revision does not change the institution's current terms.",
                "failed_check": "institution_unchanged",
                "retryable": True,
            })
        operations = self._institution_operations_since_revision(selected)
        if operations < 1:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "revise_institution", data, {
                "status": "rejected",
                "message": "An institution can be revised only after it has settled or recorded something under its current terms.",
                "failed_check": "institution_evidence",
                "retryable": True,
                "observed_operations": operations,
            })
        minute = int(self.store.get_meta("world_minute", 0))
        revision = int(selected.get("revision", 0) or 0) + 1
        changed_before: dict[str, Any] = {}
        changed_after: dict[str, Any] = {}
        if new_policy != old_policy:
            changed_before["policy"], changed_after["policy"] = old_policy, new_policy
        if new_ledger != old_ledger:
            changed_before["ledger"] = {"selectors": old_ledger.get("selectors", old_ledger.get("evidence", []))}
            changed_after["ledger"] = {"selectors": new_ledger.get("selectors", [])}
        with self.store.transaction():
            terms = {key: value for key, value in selected.items() if key not in {"institution_id", "status", "created_minute", "activated_minute"}}
            terms.update({"policy": new_policy, "ledger": new_ledger, "revision": revision, "last_revised_minute": minute})
            event_id = self._context.services["events"].append("institution.revised", actor_id, minute, {
                "institution": {"name": selected.get("name"), "purpose": selected.get("purpose")},
                "before": changed_before,
                "after": changed_after,
                "reason": reason_text,
                "revision": revision,
                "summary": f"{selected.get('name')} was revised: {describe_institution_terms({**terms})}.",
            })
            terms["last_revision_event_id"] = event_id
            self.store.put_institution(
                str(selected["institution_id"]), "active", terms,
                int(selected.get("created_minute", minute)), int(selected["activated_minute"]) if selected.get("activated_minute") is not None else minute,
            )
            self.wakes.configured(event_id, "institution.revised", actor_id, minute)
            remember_role(self.store, "god", "institution_decision", {
                "summary": f"Revised institution {selected.get('name')}: {reason_text}",
                "institution": selected.get("name"),
                "status": "active",
                "reason": reason_text,
            }, memory_key=f"god:institution-revised:{selected.get('institution_id')}:{revision}", source_event_id=event_id)
            self.store.create_intent(intent_id, actor_id, "revise_institution", data, minute)
            result = {
                "status": "accepted",
                "intent_id": intent_id,
                "event_id": event_id,
                "institution": {"name": selected.get("name"), "status": "active", "revision": revision},
                "terms": describe_institution_terms(terms),
            }
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def institution_ledger(self, institution: str, period: str | None = None) -> dict[str, Any]:
        """Return a public aggregate for an active institution."""

        candidates = [item for item in self.store.list_institutions(status="active", limit=200) if str(item.get("name", "")).casefold() == str(institution).casefold() or str(item.get("institution_id", "")) == str(institution)]
        if len(candidates) != 1:
            return {"status": "rejected", "message": "Choose one exact active institution name.", "failed_check": "institution_choice", "retryable": True}
        selected = candidates[0]
        return {"status": "ok", "institution": {"name": selected.get("name"), "status": selected.get("status")}, "entries": self.store.institution_ledger_totals(str(selected["institution_id"]), period)}

    def calculate_institution_allocation(
        self,
        institution: str,
        subjects: list[str],
        available_pool: float,
        period: str | None = None,
    ) -> dict[str, Any]:
        """Calculate, but do not apply, a declarative resource allocation.

        The policy must explicitly define a minimum allocation and the share of
        the remaining pool that may use verified ledger totals.  This keeps an
        emergent contribution institution safe before a world-specific
        settlement Action is configured.
        """

        candidates = [item for item in self.store.list_institutions(status="active", limit=200) if str(item.get("name", "")).casefold() == str(institution).casefold() or str(item.get("institution_id", "")) == str(institution)]
        if len(candidates) != 1:
            return {"status": "rejected", "message": "Choose one exact active institution name.", "failed_check": "institution_choice", "retryable": True}
        selected = candidates[0]
        policy = selected.get("policy") if isinstance(selected.get("policy"), dict) else {}
        allocation = policy.get("allocation") if isinstance(policy.get("allocation"), dict) else {}
        minimum = float(allocation.get("minimum_per_subject", 0) or 0)
        contribution_share = float(allocation.get("contribution_share", 0) or 0)
        if minimum < 0 or not 0 <= contribution_share <= 1:
            return {"status": "rejected", "message": "The active institution has an invalid allocation policy.", "failed_check": "allocation_policy", "retryable": False}
        subject_ids = [str(item) for item in subjects if str(item)]
        if not subject_ids:
            return {"status": "accepted", "institution": {"name": selected.get("name")}, "period": period or "world", "allocations": [], "unallocated": float(available_pool)}
        pool = max(0.0, float(available_pool))
        base_total = minimum * len(subject_ids)
        if base_total > pool + 1e-9:
            return {"status": "rejected", "message": "The available pool cannot satisfy the configured minimum allocation.", "failed_check": "minimum_allocation", "retryable": True, "required": base_total, "available": pool}
        remaining = pool - base_total
        bonus_pool = remaining * contribution_share
        totals = self.store.institution_ledger_totals(str(selected["institution_id"]), period)
        category = allocation.get("category")
        weights = {
            subject: sum(
                float(row.get("total", 0) or 0)
                for row in totals
                if str(row.get("subject_id")) == subject and (not category or str(row.get("category")) == str(category))
            )
            for subject in subject_ids
        }
        weight_total = sum(weights.values())
        rows = []
        for subject in subject_ids:
            bonus = bonus_pool * weights[subject] / weight_total if weight_total > 0 else 0.0
            rows.append({"subject": subject, "minimum": minimum, "bonus": bonus, "total": minimum + bonus, "verified_weight": weights[subject]})
        return {"status": "accepted", "institution": {"name": selected.get("name")}, "period": period or "world", "allocations": rows, "unallocated": remaining - bonus_pool}

    def apply_institution_allocation(
        self,
        institution: str,
        pool_entity: str,
        item: str,
        period: str | None,
        actor_id: str,
        minute: int,
        subjects: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply a declarative institution allocation through one transaction.

        The institution supplies the policy and ledger selectors. The Action
        supplies only the resource pool and output item, so this primitive is
        reusable for food, water, fuel, wages, or any other configured stock.
        """

        try:
            pool = self.store.get_entity(str(pool_entity))
        except KeyError:
            return {"status": "rejected", "failed_check": "pool_entity", "message": "The configured allocation pool does not exist."}
        state = dict(pool.get("initial_state") or {})
        available = entity_quantity(pool)
        subject_ids = [str(value) for value in (subjects or []) if str(value)]
        if not subject_ids or subject_ids == ["all_agents"]:
            subject_ids = [str(agent["id"]) for agent in self.store.list_agents()]
        result = self.calculate_institution_allocation(institution, subject_ids, available, period)
        if result.get("status") != "accepted":
            return result
        allocations = list(result.get("allocations") or [])
        consumed = sum(float(row.get("total", 0) or 0) for row in allocations)
        if consumed > available + 1e-9:
            return {"status": "rejected", "failed_check": "pool_capacity", "message": "The allocation exceeds the current pool."}
        with self.store.transaction():
            new_amount = available - consumed
            if "amount" in pool:
                pool["amount"] = new_amount
            else:
                state["quantity"] = new_amount
                pool["initial_state"] = state
            self.store.put_entity(
                pool["id"], pool["entity_type"], pool["name"],
                {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}},
            )
            self._context.services["events"].append(
                "resource.changed", actor_id, minute,
                {"resource_id": pool["id"], "before": available, "after": new_amount, "delta": -consumed, "source": "institution_allocation"},
            )
            for row in allocations:
                subject_id = str(row.get("subject"))
                try:
                    recipient = self.store.get_agent(subject_id)
                except KeyError:
                    continue
                inventory = dict(recipient.get("inventory") or {})
                quantity = float(row.get("total", 0) or 0)
                inventory[str(item)] = float(inventory.get(str(item), 0) or 0) + quantity
                recipient["inventory"] = inventory
                self.store.update_agent(subject_id, recipient)
                self._context.services["events"].append(
                    "institution.allocation_applied", actor_id, minute,
                    {"institution": institution, "subject_id": subject_id, "item": str(item), "amount": quantity, "period": period or "world", "verified_weight": row.get("verified_weight", 0)},
                )
        return {"status": "accepted", "institution": institution, "pool_entity": pool["id"], "item": str(item), "period": period or "world", "allocations": allocations, "consumed": consumed, "remaining": available - consumed}

    @staticmethod
    def _valid_transfer(bundle: dict[str, Any]) -> bool:
        return bool(bundle) and all(isinstance(item, str) and item and isinstance(quantity, (int, float)) and not isinstance(quantity, bool) and quantity > 0 for item, quantity in bundle.items())

    @staticmethod
    def _has_inventory(agent: dict[str, Any], bundle: dict[str, Any]) -> bool:
        inventory = agent.get("inventory", {}) or {}
        return all(float(inventory.get(item, 0)) >= float(quantity) for item, quantity in bundle.items())

    @staticmethod
    def _same_public_location(first: dict[str, Any], second: dict[str, Any]) -> bool:
        first_state = first.get("initial_state", {}) or {}
        second_state = second.get("initial_state", {}) or {}
        return bool(first_state.get("location_id")) and first_state.get("location_id") == second_state.get("location_id")

    def _available_for_instant_interaction(self, agent: dict[str, Any]) -> bool:
        """Check interaction eligibility without interrupting an active BT."""

        state = agent.get("initial_state", {}) or {}
        if state.get("status", "idle") in {"unconscious", "dead"}:
            return False
        if state.get("status") != "executing":
            return True
        action_id = state.get("active_action")
        if action_id == "parallel":
            action_ids = [item.get("action_id") for item in (state.get("active_parallel_actions") or {}).values()]
            definitions = {item["id"]: item for item in self.store.get_definitions("action_def")}
            return bool(action_ids) and all(definitions.get(item, {}).get("interaction_policy") == "allow" for item in action_ids)
        if not action_id:
            return False
        action = next((item for item in self.store.get_definitions("action_def") if item.get("id") == action_id), None)
        return bool(action and action.get("interaction_policy") == "allow")

    def _testimony_metadata(self, speaker: dict[str, Any], listener: dict[str, Any]) -> dict[str, Any]:
        """Attach a configured credibility prior without deciding truth."""

        rules = self.store.get_meta("rules", {}) or {}
        policy = rules.get("social_credibility", {}) or {}
        base = float(policy.get("base", 0.5) or 0.5)
        trust_weight = float(policy.get("trust_weight", 0.35) or 0.0)
        reputation_weight = float(policy.get("reputation_weight", 0.15) or 0.0)
        trust_relations = {str(item) for item in (policy.get("trust_relations", ["trust"]) or [])}
        trust_values = [
            float(item.get("relation_strength", item.get("value", 0)) or 0)
            for item in self.store.get_relationships(str(listener.get("id")))
            if str(item.get("relation")) in trust_relations
        ]
        trust = max(-1.0, min(1.0, trust_values[0] if trust_values else 0.0))
        state = speaker.get("initial_state", {}) or {}
        reputation_key = str(policy.get("reputation_attribute", "reputation") or "reputation")
        reputation = float(state.get(reputation_key, 0) or 0)
        rep_min = float(policy.get("reputation_min", -100) or -100)
        rep_max = float(policy.get("reputation_max", 100) or 100)
        midpoint = (rep_min + rep_max) / 2.0
        half_range = max(1e-9, (rep_max - rep_min) / 2.0)
        reputation_signal = max(-1.0, min(1.0, (reputation - midpoint) / half_range))
        trait_signals: dict[str, float] = {}
        for config_key, weight_key, sign in (
            ("honesty_trait", "honesty_weight", 1.0),
            ("deception_trait", "deception_weight", -1.0),
        ):
            trait_key = policy.get(config_key)
            if not trait_key:
                continue
            value = float((speaker.get("traits", {}) or {}).get(str(trait_key), 0) or 0)
            bounded = max(0.0, min(1.0, value))
            trait_signals[str(trait_key)] = bounded
            base += sign * float(policy.get(weight_key, 0.0) or 0.0) * bounded
        credibility = max(0.0, min(1.0, base + trust_weight * trust + reputation_weight * reputation_signal))
        return {
            "statement_kind": "reported",
            "verification_status": "unverified",
            "credibility": round(credibility, 3),
            "credibility_basis": {
                "listener_trust": round(trust, 3),
                "speaker_reputation": round(reputation, 3),
                "trait_signals": trait_signals,
            },
        }

    def submit_dialogue(self, speaker_id: str, listener_id: str, topic: str, message: str) -> dict[str, Any]:
        """Record a direct, permission-checked text exchange."""

        intent_id = str(uuid4())
        listener_id = self.targets.canonical_id(listener_id) or str(listener_id)
        topic = str(topic).strip()
        message = str(message).strip()
        if not listener_id or listener_id == speaker_id or not topic or not message:
            return {"status": "rejected", "message": "speaker, listener, topic and message are required.", "failed_check": "dialogue", "retryable": True}
        if len(message) > 1000:
            return {"status": "rejected", "message": "message must be at most 1000 characters.", "failed_check": "message_length", "retryable": True}
        try:
            speaker = self.store.get_agent(speaker_id)
            listener = self.store.get_agent(listener_id)
        except KeyError:
            return {"status": "rejected", "message": "Both participants must exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(speaker, listener):
            return {"status": "rejected", "message": "Both participants must be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(speaker) or not self._available_for_instant_interaction(listener):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant interaction.", "failed_check": "participant_busy", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        testimony = self._testimony_metadata(speaker, listener)
        with self.store.transaction():
            self.store.create_intent(intent_id, speaker_id, "send_message", {"target_id": listener_id, "topic": topic, "message": message}, minute)
            exchange_id = self.store.put_dialogue_exchange(speaker_id, listener_id, topic, minute, {"message": message, "source": "direct_text"})
            self.store.put_memory(speaker_id, "dialogue", {"topic": topic, "message": message, "target_id": listener_id, "world_minute": minute, "dialogue_id": exchange_id, "importance": 0.35, "claim_status": "observed", "source_type": "own_statement"})
            self.store.put_memory(listener_id, "testimony", {"topic": topic, "message": message, "speaker_id": speaker_id, "world_minute": minute, "dialogue_id": exchange_id, "importance": 0.45, "pending_decision": True, "claim_status": "reported", "source_type": "testimony", **testimony})
            self._apply_configured_need_effect(speaker, "dialogue.completed")
            self._apply_configured_need_effect(listener, "dialogue.completed")
            self._apply_configured_relationship_effect(speaker, listener, "dialogue.completed", minute)
            self.store.update_agent(speaker_id, speaker)
            self.store.update_agent(listener_id, listener)
            event_id = self._context.services["events"].append("dialogue.completed", speaker_id, minute, {"dialogue_id": exchange_id, "target_id": listener_id, "topic": topic, "summary": "A direct exchange was recorded as unverified testimony.", "activation": "recipient_next_decision_or_reflection", "duration_minutes": 0, "priority": "normal", **testimony})
            result = {"status": "accepted", "intent_id": intent_id, "dialogue_id": exchange_id, "event_id": event_id, "topic": topic}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def _apply_configured_need_effect(self, agent: dict[str, Any], event_type: str) -> None:
        """Apply package-declared social/interaction need outcomes."""

        rules = self.store.get_meta("rules", {}) or {}
        effects = (rules.get("need_effects", {}) or {}).get(event_type, [])
        if isinstance(effects, dict):
            effects = [{"type": "need_delta", "need": key, "delta": value} for key, value in effects.items()]
        for effect in effects or []:
            self.effects.apply(agent, dict(effect))

    def _apply_configured_relationship_effect(self, source: dict[str, Any], target: dict[str, Any], event_type: str, minute: int) -> None:
        """Apply package-declared relationship changes for completed interactions."""

        rules = self.store.get_meta("rules", {}) or {}
        effects = (rules.get("relationship_effects", {}) or {}).get(event_type, [])
        if isinstance(effects, dict):
            effects = [effects]
        for effect in effects or []:
            self._context.services["activity"]._apply_relationship_effect(source, target, dict(effect), minute)

    def create_trade_offer(self, offerer_id: str, recipient_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        """Create a two-phase item offer; no inventory moves until acceptance."""

        give = dict(give or {})
        want = dict(want or {})
        recipient_id = self.targets.canonical_id(recipient_id) or str(recipient_id)
        if not recipient_id or recipient_id == offerer_id or not self._valid_transfer(give) or not self._valid_transfer(want):
            return {"status": "rejected", "message": "A trade needs two participants and positive give/want quantities.", "failed_check": "trade_schema", "retryable": True}
        try:
            offerer = self.store.get_agent(offerer_id)
            recipient = self.store.get_agent(recipient_id)
        except KeyError:
            return {"status": "rejected", "message": "Both traders must exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(offerer, recipient):
            return {"status": "rejected", "message": "Both traders must be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(offerer) or not self._available_for_instant_interaction(recipient):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant trade.", "failed_check": "participant_busy", "retryable": True}
        if not self._has_inventory(offerer, give):
            return {"status": "rejected", "message": "The offerer lacks an offered item.", "failed_check": "offer_inventory", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        offer_id = str(uuid4())
        intent_id = str(uuid4())
        with self.store.transaction():
            self.store.create_intent(intent_id, offerer_id, "create_trade_offer", {"recipient_id": recipient_id, "give": give, "want": want}, minute)
            self.store.put_trade_offer(offer_id, offerer_id, recipient_id, give, want, minute)
            self.store.put_memory(recipient_id, "trade_offer", {"offer_id": offer_id, "offerer_id": offerer_id, "give": give, "want": want, "world_minute": minute, "pending_decision": True, "importance": 0.6})
            self.store.put_memory(offerer_id, "trade_offer", {"offer_id": offer_id, "recipient_id": recipient_id, "give": give, "want": want, "world_minute": minute, "pending_decision": True, "importance": 0.45})
            event_id = self._context.services["events"].append("trade.offer_created", offerer_id, minute, {"offer_id": offer_id, "recipient_id": recipient_id, "give": give, "want": want, "activation": "recipient_next_decision", "duration_minutes": 0, "priority": "normal"})
            result = {"status": "accepted", "intent_id": intent_id, "offer_id": offer_id, "event_id": event_id, "trade_status": "open"}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def accept_trade_offer(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        recipient_id = self.targets.canonical_id(recipient_id) or str(recipient_id)
        offer = self.store.get_trade_offer(offer_id)
        if offer is None:
            return {"status": "rejected", "message": "Trade offer does not exist.", "failed_check": "offer_id", "retryable": False}
        if offer["status"] != "open":
            return {"status": "rejected", "message": "Trade offer is no longer open.", "failed_check": "offer_status", "retryable": False}
        if offer["recipient_id"] != recipient_id:
            return {"status": "rejected", "message": "Only the named recipient can accept this offer.", "failed_check": "recipient", "retryable": False}
        try:
            offerer = self.store.get_agent(offer["offerer_id"])
            recipient = self.store.get_agent(recipient_id)
        except KeyError:
            return {"status": "rejected", "message": "Both traders must still exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(offerer, recipient):
            return {"status": "rejected", "message": "Both traders must still be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(offerer) or not self._available_for_instant_interaction(recipient):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant trade.", "failed_check": "participant_busy", "retryable": True}
        if not self._has_inventory(offerer, offer["give"]):
            return {"status": "rejected", "message": "The offered items are no longer available.", "failed_check": "offer_inventory", "retryable": False}
        if not self._has_inventory(recipient, offer["want"]):
            return {"status": "rejected", "message": "The requested items are no longer available.", "failed_check": "request_inventory", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        intent_id = str(uuid4())
        with self.store.transaction():
            self.store.create_intent(intent_id, recipient_id, "accept_trade_offer", {"offer_id": offer_id}, minute)
            for item, quantity in offer["give"].items():
                offerer.setdefault("inventory", {})[item] = offerer["inventory"].get(item, 0) - quantity
                recipient.setdefault("inventory", {})[item] = recipient["inventory"].get(item, 0) + quantity
            for item, quantity in offer["want"].items():
                recipient.setdefault("inventory", {})[item] = recipient["inventory"].get(item, 0) - quantity
                offerer.setdefault("inventory", {})[item] = offerer["inventory"].get(item, 0) + quantity
            self._apply_configured_need_effect(offerer, "trade.completed")
            self._apply_configured_need_effect(recipient, "trade.completed")
            self._apply_configured_relationship_effect(offerer, recipient, "trade.completed", minute)
            self.store.update_agent(offerer_id := offer["offerer_id"], offerer)
            self.store.update_agent(recipient_id, recipient)
            result = {"status": "accepted", "offer_id": offer_id, "trade_status": "completed", "give": offer["give"], "want": offer["want"]}
            self.store.finish_trade_offer(offer_id, "completed", minute, result)
            self.store.put_memory(offerer_id, "trade_result", {"offer_id": offer_id, "trade_status": "completed", "world_minute": minute, "importance": 0.55})
            self.store.put_memory(recipient_id, "trade_result", {"offer_id": offer_id, "trade_status": "completed", "world_minute": minute, "importance": 0.55})
            event_id = self._context.services["events"].append("trade.completed", recipient_id, minute, {"offer_id": offer_id, "offerer_id": offerer_id, "recipient_id": recipient_id, "give": offer["give"], "want": offer["want"], "activation": "both_next_reflection", "duration_minutes": 0, "priority": "normal"})
            result["intent_id"] = intent_id
            result["event_id"] = event_id
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def counter_trade_offer(self, counterparty_id: str, offer_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        """Close an offer and create the counterparty's next-round offer."""

        counterparty_id = self.targets.canonical_id(counterparty_id) or str(counterparty_id)
        offer = self.store.get_trade_offer(offer_id)
        if offer is None or offer["status"] != "open" or offer["recipient_id"] != counterparty_id:
            return {"status": "rejected", "message": "Only the open offer recipient can counter it.", "failed_check": "offer", "retryable": False}
        if int(offer.get("round", 1)) >= 8:
            return {"status": "rejected", "message": "The negotiation reached its round limit.", "failed_check": "round_limit", "retryable": False}
        give = dict(give or {})
        want = dict(want or {})
        if not self._valid_transfer(give) or not self._valid_transfer(want):
            return {"status": "rejected", "message": "A counteroffer needs positive give/want quantities.", "failed_check": "trade_schema", "retryable": True}
        try:
            counterparty = self.store.get_agent(counterparty_id)
            original_offerer = self.store.get_agent(offer["offerer_id"])
        except KeyError:
            return {"status": "rejected", "message": "Both traders must still exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(counterparty, original_offerer):
            return {"status": "rejected", "message": "Both traders must still be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(counterparty) or not self._available_for_instant_interaction(original_offerer):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant trade.", "failed_check": "participant_busy", "retryable": True}
        if not self._has_inventory(counterparty, give):
            return {"status": "rejected", "message": "The counterparty lacks the counteroffer items.", "failed_check": "offer_inventory", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        counter_id = str(uuid4())
        with self.store.transaction():
            self.store.finish_trade_offer(offer_id, "countered", minute, {"status": "accepted", "trade_status": "countered", "counter_offer_id": counter_id})
            self.store.put_trade_offer(counter_id, counterparty_id, offer["offerer_id"], give, want, minute, parent_offer_id=offer_id, round_number=int(offer.get("round", 1)) + 1)
            self.store.put_memory(offer["offerer_id"], "trade_offer", {"offer_id": counter_id, "parent_offer_id": offer_id, "offerer_id": counterparty_id, "give": give, "want": want, "world_minute": minute, "pending_decision": True, "importance": 0.6})
            event_id = self._context.services["events"].append("trade.countered", counterparty_id, minute, {"offer_id": counter_id, "parent_offer_id": offer_id, "recipient_id": offer["offerer_id"], "give": give, "want": want, "activation": "recipient_next_decision", "duration_minutes": 0, "priority": "normal"})
        return {"status": "accepted", "offer_id": counter_id, "parent_offer_id": offer_id, "trade_status": "open", "event_id": event_id}

    def reject_trade_offer(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        recipient_id = self.targets.canonical_id(recipient_id) or str(recipient_id)
        offer = self.store.get_trade_offer(offer_id)
        if offer is None or offer["recipient_id"] != recipient_id or offer["status"] != "open":
            return {"status": "rejected", "message": "The trade offer cannot be rejected by this participant.", "failed_check": "offer", "retryable": False}
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            result = {"status": "accepted", "offer_id": offer_id, "trade_status": "rejected"}
            self.store.finish_trade_offer(offer_id, "rejected", minute, result)
            self._context.services["events"].append("trade.rejected", recipient_id, minute, {"offer_id": offer_id, "offerer_id": offer["offerer_id"], "recipient_id": recipient_id, "activation": "offerer_next_decision_or_reflection", "duration_minutes": 0, "priority": "normal"})
        return result
