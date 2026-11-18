# Social Authority Loop — Phase 1 Implementation Report

Status: **Phase 1A proven; Phase 1B observed**  
Contract: [social-authority-loop-phase1.md](social-authority-loop-phase1.md)  
Compatibility: **none required; the frozen contract replaced earlier conflicting behavior**

This report records implementation evidence only. The external contract remains
the scope authority. It does not claim Story, God, Director, legitimacy,
visibility asymmetry, automatic event routing or multi-agent competition.

## 1. What was implemented

| Area | Implementation |
| --- | --- |
| Shared decision | `src/sim2/world/authorization.py` contains the pure `authorization_decision(agent, action, action_context, world)` plus the store-backed projection used by both view and admission |
| Platform owner | `src/sim2/platform/authorization.py` owns request creation, approval, rejection, revocation and visible authority projections |
| Persistence | `access_request` and `delegation` tables in `src/sim2/persistence/store.py`; delegation history is retained and a partial unique index enforces one active tuple |
| View | `blocked_behaviors`, `denial_memories`, `pending_access_requests` and `active_delegations` are projected into the decision view |
| Admission | `behavior.requested` / `behavior.denied`, typed `failed_check`, denial memory, and instant administrative decisions commit with the intent record |
| Withdrawal | `resource_withdrawal` is a two-phase effect: admission commits `resource.withdrawal.started`; completion commits the single decrement and `resource.withdrawal.completed`; failure commits `resource.withdrawal.failed` while retaining the started fact |
| Prompt | `<blocked_behaviors>`, `<access_authority>` and `<denial_memory>` are rendered and pinned through compaction |
| Probe world | `worlds/social_authority_probe` contains only `mira`, `tomas` and `energy_reserve`; Story/God/Director consumers are absent |

The authorization rule is declarative. The probe world uses three rules:

```yaml
- id: energy_reserve_stewardship
  effect: {type: authorization, mode: resource_access, target: energy_reserve, scope: energy_withdrawal, behavior_ids: [withdraw_energy]}
- id: approval_authority
  effect: {type: authorization, mode: request_decision, behavior_ids: [approve_withdrawal, reject_withdrawal]}
- id: revocation_authority
  effect: {type: authorization, mode: delegation_revocation, behavior_ids: [revoke_delegation]}
```

There is no world-specific branch in Platform code and no hardcoded `mira` or
`tomas`. Stewardship is configured on the agent and stored as a relationship;
delegation is a first-class lifecycle record.

## 2. Acceptance matrix

Phase 1A is deterministic and runs without a model.

| ID | Assertion | Test | Status |
| --- | --- | --- | --- |
| SA-01 | Steward preview allowed; non-steward blocked with `missing_delegation`; no state change | `test_sa_01_preview_discriminates_steward` | `PROVEN` |
| SA-02 | Non-steward submit denied; inventory unchanged; requested/denied facts and denial memory exist | `test_sa_02_non_steward_withdraw_denied` | `PROVEN` |
| SA-03 | `request_access` cites the denial through `based_on_event` | `test_sa_03_request_cites_denial` | `PROVEN` |
| SA-04 | Approval creates exactly one active delegation and resolves the request | `test_sa_04_approval_creates_delegation` | `PROVEN` |
| SA-05 | Withdrawal starts, then completes; inventory decrements once | `test_sa_05_withdrawal_decrements_once` | `PROVEN` |
| SA-06 | Revocation invalidates future withdrawals with `delegation_revoked`; history retained | `test_sa_06_revocation_denies_withdrawal` | `PROVEN` |
| SA-07 | Self-approval denied with `self_approval_forbidden`; no delegation; request pending | `test_sa_07_self_approval_denied` | `PROVEN` |
| SA-08 | Non-steward approver denied with `missing_stewardship`; no delegation | `test_sa_08_non_steward_approver_denied` | `PROVEN` |
| SA-09 | Reject branch records `access.rejected`; no delegation and no resource event | `test_sa_09_reject_branch` | `PROVEN` |
| SA-10 | Subject/target/scope mismatch and resolved request return canonical codes | `test_sa_10_approval_mismatch_codes` | `PROVEN` |
| SA-11 | Duplicate active tuple returns `duplicate_delegation`; request stays pending | `test_sa_11_duplicate_delegation_rejected` | `PROVEN` |
| SA-12 | A wrong-scope delegation does not authorize withdrawal | `test_sa_12_wrong_scope_delegation_denied` | `PROVEN` |
| SA-13 | Same intent, request, approval, withdrawal and revoke retries produce one side effect | `test_sa_13_idempotent_retries` | `PROVEN` |
| SA-14 | Approval, admission, completion and revocation failure injection leaves no partial state | `test_sa_14_transaction_failure_rollbacks` | `PROVEN` |
| SA-15 | Preview is side-effect-free; preview and submit agree on the same snapshot | `test_sa_15_preview_submit_equivalence` | `PROVEN` |
| SA-16 | Required event fields and causal order are present | `test_sa_16_event_schema_and_order` | `PROVEN` |
| SA-17 | Denial memory is private and usable; all invalid causal references are rejected | `test_sa_17_denial_memory_and_causal_reference` | `PROVEN` |
| SA-18 | Negative space is clean after denials, rejection, self-approval and non-steward approval | `test_sa_18_negative_space` | `PROVEN` |

Command:

```bash
python -m pytest -q tests/test_social_authority_loop.py
```

Result: `18 passed`.

## 3. Phase 1B remote probe

Command:

```bash
python scripts/verify_social_authority.py --provider remote --runs 5
```

| Field | Value |
| --- | --- |
| Provider / model | `remote` / `deepseek-v4.1-flash` |
| Runs | 5 |
| Valid probes | 5 |
| Invalid probes | 0 |
| Request rate | 5/5 |
| Causal-reference rate | 5/5 |
| Approval rate | 5/5 |
| Withdrawal-after-approval rate | 5/5 |
| Notes | Observational only; the approval task is harness-injected; automatic targeted wake is not proven |

Each valid run observed: the denial in Tomas's view, Tomas choosing
`Request Access` with a valid `based_on_event`, Mira choosing
`Approve Withdrawal` on the pending request, and Tomas choosing
`Withdraw Energy` after the delegation became active.

The probe start is deterministic: Platform first records a real
`behavior.denied` for Tomas. It does not use a model to create the denial.

## 4. Negative-space evidence

The deterministic tests assert, at the relevant checkpoints:

- No Story, God or Director task is created after five world minutes.
- No resource event exists for the initial denial, rejection, self-approval or non-steward approval.
- No delegation and no `access.approved` appear on rejection, self-approval or non-steward approval.
- No `resource.withdrawal.completed` appears before approval.
- A retry of the same withdrawal intent does not decrement twice.
- No second `access.approved`, second active delegation or second `delegation.revoked` appears on retry.
- After revocation, the resource amount is unchanged.

## 5. Fixture notes

1. **Self-approval and non-steward fixtures inject a pending request.** A
   steward cannot produce a legitimate `behavior.denied` for a reserve they
   already steward, so a causal `request_access` cannot create these fixtures.
   The harness calls the same request service with `validate_causal=False`.
   The Agent-facing `request_access` behavior always validates the reference;
   no acceptance assertion depends on the injected request's causal reference.

2. **The probe world is excluded from the general liveness contract.** It
   declares `kind: probe`. Phase 1A requires no Story, God or Director
   consumers, while the general liveness contract requires every playable
   shipped world to wake God on stagnation. The probe is a deterministic
   fixture, not a playable liveness world.

3. **Approval task text is harness-injected.** The probe passes an explicit
   approval task to Mira. Phase 1 does not prove automatic event routing or
   targeted wake.

## 6. Not proven

The following remain `NOT_PROVEN` by design:

- Request expiry.
- Delegation expiry.
- General multi-scope isolation.
- Concurrent approvals.
- Mid-flight revocation.
- Automatic targeted wake / event routing.
- Visibility and information asymmetry.
- Legitimacy evaluation.
- Story, God and Director institutional evolution.
- Multi-agent competition and resource reservation.
- A real LLM necessarily choosing the intended behavior in production.

## 7. Verification run

| Check | Command | Result |
| --- | --- | --- |
| Deterministic contract | `python -m pytest -q tests/test_social_authority_loop.py` | `18 passed` |
| Full suite | `python -m pytest -q` | `1367 passed` |
| Platform benchmark | `python scripts/benchmark_platform.py --agents 1000 --runs 5` | p95 `182.03 ms`; elapsed `[164.279, 178.723, 163.391, 182.856, 166.447]` ms |
| Remote probe | `python scripts/verify_social_authority.py --provider remote --runs 5` | 5 valid, 5/5 request, 5/5 causal reference, 5/5 approval, 5/5 withdrawal |

The benchmark p95 remains below the frozen `250 ms` bound. The full-suite
result includes the 18 deterministic contract tests and the existing test
suite.

## 8. Reproduction

```bash
python -m pytest -q tests/test_social_authority_loop.py
python -m pytest -q
python scripts/benchmark_platform.py --agents 1000 --runs 5
python scripts/verify_social_authority.py --provider remote --runs 5
```
