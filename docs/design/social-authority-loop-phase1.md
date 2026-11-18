# Social Authority Loop — Phase 1 Freeze and Implementation Plan

Status: **frozen external contract; implementation evidence recorded in [the Phase 1 report](social-authority-loop-phase1-report.md)**  
Scope: **Phase 1A deterministic contract + Phase 1B remote-LLM probe**  
Compatibility: **none required; this contract replaces earlier conflicting behavior**

This document is the single source of truth for Phase 1. Earlier discussion notes,
prototype probes and conflicting clauses are superseded. Internal implementation
may change; the external behavior, event schema and transaction boundaries below
are frozen.

## 1. Thesis

Phase 1 proves one thing:

> A social relationship can change an Agent's legal behavior set through an
> authorization transaction, and that change can move a world resource.

It does not prove that Story can narrate it, that God can maintain it, or that an
Agent accepts it as legitimate. Those are later phases.

The minimum causal chain is:

```text
Tomas sees a blocked behavior
  -> Tomas submits the forbidden intent
  -> Platform re-evaluates and denies it
  -> the denial becomes a structured fact and a memory
  -> Tomas chooses a different social action, citing the denial
  -> Mira approves through a real transaction
  -> a scoped delegation becomes active
  -> Tomas withdraws successfully; inventory decreases once
  -> Mira revokes the delegation
  -> Tomas's next withdrawal is denied; inventory does not change again
```

## 2. Phase 1 scope

### 2.1 P0 capabilities

| Capability | P0 requirement |
| --- | --- |
| Legible eligibility | Blocked behavior is visible with reasons and `attemptable` |
| Authoritative enforcement | Submit re-evaluates current state; denial has real consequences |
| Structured memory | The denying actor receives a structured denial memory for the next view |
| Social intent | `request_access` creates a traceable access request |
| Delegation | Approval creates a scoped, sourced authorization |
| Resource transaction | Authorization affects the actual withdrawal transaction |
| Revocation | Revocation invalidates future withdrawals while preserving history |
| Idempotency | Retrying the same intent, request, approval or revoke produces no duplicate side effect |

### 2.2 Explicitly outside P0

- Story, God and Director participation.
- Legitimacy evaluation.
- Automatic targeted wake / event routing.
- Generic two-hop relationship queries.
- Multi-agent resource competition and resource reservation.
- Request expiry and delegation expiry.
- General multi-scope isolation.
- Concurrent approvals.
- Mid-flight revocation safety.
- Full visibility and information asymmetry.

These are P1 or later. They must not be claimed as proven because Phase 1 passes.

### 2.3 Breaking changes allowed

Because this is a development contract, the implementation may replace current
behavior. In particular:

- Rule-denied behaviors are no longer hidden from the Agent's decision view.
  They are visible as blocked options with reasons.
- A blocked choice is a real, recorded intent attempt, not a generic
  `behavior_unavailable` response.
- Denied behavior attempts create `behavior.requested` / `behavior.denied`
  facts even though no world state changes.
- Delegation is a first-class authorization record, not only a relationship
  strength adjustment.

## 3. Roles and authority model

The Phase 1 probe world has two Agents:

| Agent | Role | Authority |
| --- | --- | --- |
| `mira` | Steward of `energy_reserve` | `stewardship` over the reserve |
| `tomas` | Requester / delegate | No stewardship; may receive a scoped delegation |

The Platform is the only component allowed to evaluate and execute authorization.
No world-specific branch may be added to Platform code.

Authority sources are relationship-derived:

- `stewardship(mira, energy_reserve)` grants implicit authority for the reserve.
- `delegation(tomas, energy_reserve, scope=energy_withdrawal)` grants scoped authority.
- A rule or action must not hardcode `mira` or `tomas`; it checks the relationship.

## 4. Shared authorization evaluator

### 4.1 One decision entry point

All of these must call the same evaluator:

- Agent decision view (blocked/available projection)
- `preview_behavior`
- Behavior admission
- Action execution re-check
- Approval/reject/revoke admission

Signature:

```text
authorization_decision(agent, action, action_context, world) -> Eligibility
```

`action_context` mapping:

| Action | Context |
| --- | --- |
| `withdraw_energy` | `energy_reserve` |
| `approve_withdrawal` | access request record |
| `reject_withdrawal` | access request record |
| `revoke_delegation` | delegation record |

The evaluator must be a single shared function/service. Duplicating rule checks
in `behavior_view`, `activity`, `choices` and `world_tick_service` is a contract
violation even if the outputs happen to agree.

### 4.2 Eligibility output

```yaml
eligibility:
  available: false
  reasons:
    - type: missing_delegation
      attemptable: true
      rule_id: energy_reserve_stewardship
```

Rules:

- `available` is the final executability decision.
- `reasons` lists all relevant blockers and their nature.
- `attemptable` belongs to each reason, not to the behavior as a whole.
- A policy/authority denial is attemptable; a missing objective precondition is
  normally not attemptable.
- `reasons[].type` and `action_result.failed_check` use the same canonical code.
- `behavior.denied.reason` uses the same canonical code.
- Preview and submit must agree for the same frozen world snapshot.
- Submit must re-evaluate current state; it must not trust a stale preview result.

### 4.3 Preview contract

Preview:

```text
state_mutation: false
business_event: false
intent_record: false
```

Preview may write an LLM tool-call or diagnostic log, but it must not enter a
world transaction or change the world snapshot. The equivalence test freezes the
world minute and authorization state between preview and submit.

### 4.4 Withdraw authorization

```text
if actor has stewardship over energy_reserve:
    allow
elif actor has an active delegation for energy_reserve with scope energy_withdrawal:
    allow
elif the actor's delegation for this tuple exists but was revoked:
    deny with delegation_revoked
else:
    deny with missing_delegation
```

P0 does not support `delegation_expired`; expiry is P1.

### 4.5 Approval / rejection authorization

The checks are short-circuited in this order. The first failure is the only
returned reason.

| Order | Condition | Canonical code |
| --- | --- | --- |
| 1 | approver == requester | `self_approval_forbidden` |
| 2 | approver has no stewardship over the request target | `missing_stewardship` |
| 3 | request is not pending | `request_not_pending` |
| 4 | proposed subject != request subject | `request_subject_mismatch` |
| 5 | proposed target != request target | `request_target_mismatch` |
| 6 | proposed scope != request scope | `request_scope_mismatch` |
| 7 | an active delegation already exists for the tuple | `duplicate_delegation` |

Notes:

- Rows 1–3 apply to both approve and reject.
- Rows 4–7 apply to approve, because reject does not create a delegation.
- Self-approval is an eligibility reason, not a post-submit-only check.
- `self_approval_forbidden` is the canonical code for "the requester cannot decide
  their own request", including reject decisions.
- `not_authorized_approver` is retired and must not appear in code or tests.

### 4.6 Revocation authorization

- Any current steward over the request target may revoke an active delegation.
- `granted_by` is provenance, not a permanent revocation right.
- A non-steward receives `missing_stewardship`.
- Revocation changes the delegation status; it never deletes the record.
- Retrying the same revoke returns the original result and does not emit a
  second `delegation.revoked`.

Revoking an already-revoked delegation with a new intent is outside P0. It must
not be used as a P0 acceptance claim.

## 5. Request and delegation lifecycle

### 5.1 Access request

```yaml
access_request:
  request_id: req_001
  requester: tomas
  behavior: Withdraw Energy
  target: energy_reserve
  scope: energy_withdrawal
  reason: Need energy for relay maintenance
  based_on_event: evt_denial_t5
  status: pending
  created_minute: 10
```

Status transitions:

```text
pending -> approved
pending -> rejected
```

`approved` and `rejected` are terminal. A failed approval does not change the
request state. `duplicate_delegation` leaves the request pending.

### 5.2 Delegation

```yaml
delegation:
  delegation_id: del_001
  source: tomas
  target: energy_reserve
  type: delegation
  granted_by: mira
  scope: energy_withdrawal
  status: active
  granted_at: 12
  expires_minute: null
  revoked_at: null
  revoked_by: null
```

Rules:

- At most one active delegation per `(source, target, scope)`.
- `status` is `active` or `revoked`.
- Revocation keeps the record with `revoked_at` and `revoked_by`.
- Delegation history is retained. Replacing an active record with a new one
  without preserving the old record is a contract violation.
- `expires_minute` is reserved for P1; P0 always uses `null`.

### 5.3 Invariants

```text
I1: UNIQUE ACTIVE (source, target, scope)
I2: Each request has exactly one terminal state: approved OR rejected
I3: Approval must match request.subject, request.target and request.scope
I4: A successful withdrawal must reference the authorization used at execution
I5: Revocation and expiry affect future withdrawals only; past resource changes
    are immutable
```

## 6. State machine

### 6.1 Main timeline

Initial inventory: `energy_reserve = 10`. The withdrawal amount is `1`.

| World minute | Operation | Expected |
| --- | --- | --- |
| 0 | S0 preview: Mira `withdraw_energy` | Allow; no execution, inventory 10 |
| 0 | S0 preview: Tomas `withdraw_energy` | Deny / `missing_delegation`; inventory 10 |
| 5 | Tomas submits `withdraw_energy` | `behavior.denied` / `missing_delegation`; inventory 10 |
| 10 | Tomas `request_access` | `access.requested`; `based_on_event=evt_denial_t5`; request pending |
| 12 | Mira `approve_withdrawal` | `access.approved`; `del_001` active |
| 13 | Tomas `withdraw_energy` admitted | `resource.withdrawal.started`; inventory still 10 |
| 13+d | withdraw completes | `resource.withdrawal.completed`; inventory 9 |
| 20 | Mira `revoke_delegation` | `del_001` status revoked; `delegation.revoked` |
| 21 | Tomas `withdraw_energy` | Deny / `delegation_revoked`; inventory 9 |

`d` is the configured withdrawal duration.

### 6.2 Self-approval fixture

Independent world fixture. Initial inventory 10.

| World minute | Operation | Expected |
| --- | --- | --- |
| 0 | Mira `request_access` | `access.requested`; `req_002` pending |
| 1 | Mira `approve_withdrawal` on `req_002` | `behavior.denied` / `self_approval_forbidden` |

Assertions:

- No `access.approved`.
- No delegation.
- `req_002` remains pending.
- No resource event or inventory change.

### 6.3 Non-steward approver fixture

Independent world fixture.

| World minute | Operation | Expected |
| --- | --- | --- |
| 0 | Mira `request_access` | `access.requested`; `req_003` pending |
| 1 | Tomas `approve_withdrawal` on `req_003` | `behavior.denied` / `missing_stewardship` |

Assertions: no delegation, no `access.approved`, request remains pending.

### 6.4 Reject fixture

Independent world fixture. Initial inventory 10.

| World minute | Operation | Expected |
| --- | --- | --- |
| 0 | Tomas `request_access` | `access.requested`; `req_004` pending |
| 1 | Mira `reject_withdrawal` | `access.rejected`; request rejected |

Assertions:

- No delegation.
- No `access.approved`.
- No resource events; inventory 10.
- A later Tomas withdrawal is denied with `missing_delegation`.

### 6.5 Scope mismatch fixture

Independent world fixture. Initial inventory 10.

Setup: `delegation(tomas, energy_reserve, scope=reserve_management)` is active.

| Operation | Expected |
| --- | --- |
| Tomas `withdraw_energy` | Deny / `missing_delegation`; inventory 10 |

This proves the evaluator checks `scope=energy_withdrawal`, not merely the
existence of a delegation. General multi-scope isolation remains P1.

### 6.6 Approval mismatch fixtures

These are injected at the Platform boundary, not generated by an Agent tool call.
They test admission invariants.

| Case | Expected |
| --- | --- |
| Subject mismatch | `request_subject_mismatch`; no delegation |
| Target mismatch | `request_target_mismatch`; no delegation |
| Scope mismatch | `request_scope_mismatch`; no delegation |
| Request already resolved | `request_not_pending`; no delegation |
| Duplicate active tuple | `duplicate_delegation`; no second delegation; request pending |

### 6.7 Atomicity failure fixtures

| Transaction | Injected failure | Required final state |
| --- | --- | --- |
| Approval | `access.approved` write fails after delegation write | Request still pending; no delegation; no `access.approved` |
| Withdrawal admission | `resource.withdrawal.started` write fails after behavior start | No behavior start; no started event; actor free |
| Withdrawal completion | `resource.withdrawal.completed` write fails after inventory decrement | Inventory restored; `started` retained; no `completed`; `resource.withdrawal.failed` exists; behavior failed/interrupted; actor free |
| Revocation | `delegation.revoked` write fails after status change | Delegation still active; no revoked event |

P0 requires same-transaction commits. Outbox or eventual-consistency schemes are
not accepted as P0 implementations unless they prove equivalent recovery
semantics in a separate contract.

## 7. Event contract

Required events and fields:

| Event | Required fields |
| --- | --- |
| `behavior.requested` | `intent_id`, `actor`, `behavior`, `target`, `minute` |
| `behavior.denied` | `intent_id`, `actor`, `behavior`, `target`, `rule_id` (rule denials), `reason`, `authority`, `minute` |
| `access.requested` | `request_id`, `requester`, `behavior`, `target`, `scope`, `reason`, `based_on_event`, `minute` |
| `access.approved` | `request_id`, `approver`, `subject`, `scope`, `delegation_id`, `minute` |
| `access.rejected` | `request_id`, `approver`, `reason`, `minute` |
| `delegation.revoked` | `delegation_id`, `revoked_by`, `reason`, `minute` |
| `resource.withdrawal.started` | `intent_id`, `actor`, `target`, `authorization`, `minute` |
| `resource.withdrawal.completed` | `intent_id`, `actor`, `target`, `authorization`, `amount`, `before`, `after`, `minute` |
| `resource.withdrawal.failed` | `intent_id`, `actor`, `target`, `reason`, `minute` |

Semantics:

- `access.approved` is the approval success event and the delegation creation
  fact. There is no separate `delegation.created` event.
- `delegation active` is a state, not an event.
- `access.rejected` is a successful institutional decision by an authorized
  approver. It is not a `behavior.denied`.
- `behavior.denied` is a Platform refusal. It does not create `access.approved`
  or `access.rejected`.
- `authority` is structured:

```yaml
authority:
  type: rule | stewardship | institution
  id: energy_reserve_stewardship
```

- Withdrawal authorization reference is structured:

```yaml
authorization:
  type: stewardship | delegation
  id: del_001
```

- The withdrawal records the authorization actually used at execution, not every
  authorization that might exist.
- `resource.withdrawal.failed` records a real failure. A failed completion does
  not erase the already committed `started` fact.

Causal chain:

```text
behavior.denied
    | based_on_event
access.requested
    | request_id
access.approved
    | delegation_id
delegation active (state)
    | authorization
resource.withdrawal.started
    | intent_id
resource.withdrawal.completed
```

## 8. Transaction boundaries

```text
Approval:
  request terminal-state transition
  + delegation creation
  + access.approved
  MUST commit atomically.

Withdrawal admission:
  behavior start
  + resource.withdrawal.started
  MUST commit atomically.

Withdrawal completion:
  inventory decrement
  + resource.withdrawal.completed
  MUST commit atomically.

Withdrawal failure:
  behavior failed/interrupted
  + resource.withdrawal.failed
  MUST commit atomically.

Revocation:
  delegation status transition
  + delegation.revoked
  MUST commit atomically.
```

If any write fails, no partial state may be committed. Events and world state
must share the same transaction boundary or an equivalent recovery mechanism
proven by a separate contract.

## 9. Idempotency

| Operation | Retry requirement |
| --- | --- |
| Same withdraw `intent_id` | Return original result; inventory decremented once |
| Same `request_id` | One request record |
| Same approval | One delegation and one `access.approved` |
| Same revoke | One status transition and one `delegation.revoked` |
| Different withdraw `intent_id` | A separate intent; it may withdraw again if authorized and stock exists |

A different request for the same active `(source, target, scope)` returns
`duplicate_delegation` and does not create a second active delegation. The
request remains pending.

## 10. Causal reference validation

`request_access` must carry `based_on_event`.

Validation:

```text
event exists
AND event.actor == requester
AND event.type == behavior.denied
AND event.behavior == request.behavior
AND event.target == request.target
AND event.minute <= request.minute
```

Failure returns `invalid_causal_reference` and does not create a pending request.

The `based_on_event` value must be persisted in the `access.requested` event, not
only in the intent record or transient context.

## 11. Memory contract

On a denial, the actor receives a structured memory entry in the next decision
view. The memory includes:

```yaml
denial:
  event_id: evt_denial_t5
  behavior: Withdraw Energy
  rule: energy_reserve_stewardship
  reason: missing_delegation
  authority:
    type: rule
    id: energy_reserve_stewardship
  target: energy_reserve
  minute: 5
```

The entry must be private to the actor. It must not be inferred from Story text.
The next decision view must expose it where the Agent can use it, and the
`request_access` intent must be able to cite its `event_id`.

## 12. Acceptance matrix

Phase 1A is deterministic. Phase 1B is a separate observational probe.

| ID | Assertion | Evidence |
| --- | --- | --- |
| SA-01 | Steward preview is allowed; non-steward preview is blocked with `missing_delegation` | Eligibility result; no state change |
| SA-02 | Non-steward submit is denied; inventory unchanged | `behavior.requested`, `behavior.denied`, state snapshot |
| SA-03 | `request_access` cites the denial via `based_on_event` | `access.requested`; validation success |
| SA-04 | Mira approval creates one active delegation | `access.approved`; delegation row; request approved |
| SA-05 | Tomas withdrawal succeeds after approval; inventory decrements once | Started/completed events; inventory 9 |
| SA-06 | Revocation invalidates the delegation; next withdrawal fails with `delegation_revoked` | Delegation status; denial event; inventory 9 |
| SA-07 | Steward self-approval is denied with `self_approval_forbidden` | `behavior.denied`; no delegation; request pending |
| SA-08 | Non-requester non-steward approval is denied with `missing_stewardship` | `behavior.denied`; no delegation |
| SA-09 | Reject branch records `access.rejected`; no delegation; no resource event | Event log; request rejected |
| SA-10 | Approval subject/target/scope mismatch and resolved request are rejected | Canonical codes; no delegation |
| SA-11 | Duplicate delegation is rejected; only one active delegation | `duplicate_delegation`; tuple count 1 |
| SA-12 | Wrong-scope delegation does not authorize withdrawal | `missing_delegation`; inventory unchanged |
| SA-13 | Idempotent retries produce one side effect and one event | Event counts; inventory |
| SA-14 | Injected transaction failures leave zero partial state | State snapshots after each failure |
| SA-15 | Preview and submit agree on the same frozen snapshot; preview is side-effect-free | Eligibility comparison; state hash before/after |
| SA-16 | Required event fields and causal order are present | Event log assertions |
| SA-17 | Denial memory is present in the next view; `based_on_event` validation works | Agent view; invalid reference rejection |
| SA-18 | Negative space is clean | No forbidden events or tasks |
| SA-19 | Remote LLM probe: Agent-side behavior | Separate report; not a 1A gate |

### 12.1 Negative space

| Checkpoint | Must not exist |
| --- | --- |
| Initial Tomas denial | `resource.withdrawal.completed`, new delegation, `access.approved`, relationship change |
| Self-approval failure | `access.approved`, delegation, resource event |
| Non-steward approver failure | `access.approved`, delegation, resource event |
| Reject branch | delegation, `access.approved`, resource event |
| Duplicate approval retry | second `access.approved`, second active delegation |
| Same withdrawal retry | second inventory decrement, second completed event |
| After revocation | inventory change |
| Entire Phase 1A | Story, God, Director tasks |

### 12.2 Preview / submit equivalence

For each main state:

- Call preview on a frozen snapshot.
- Assert no state mutation and no business event.
- Call submit on the same frozen snapshot.
- Assert the `available` result and canonical reason match.
- For a denied submit, assert `behavior.denied.reason` equals the preview reason.
- For an allowed submit, assert the expected started event.

## 13. Phase 1A test environment

- World entities: only `mira` and `tomas`, or no other `energy_reserve` consumers.
- Initial inventory: `energy_reserve = 10`.
- Withdrawal amount: `1`.
- `withdraw_energy` is visible to Mira and Tomas.
- `request_access` is visible to Tomas and to Mira for the self-approval fixture.
- `approve_withdrawal` and `reject_withdrawal` are visible to all Agents; the
  authority gate must not be hidden by capability visibility.
- All actions and behavior trees are pre-authored. Phase 1 does not test God's
  ability to create them.
- `event_wake_policy` has no Story, God or Director consumers.
- Periodic evolution, director and story tasks are disabled.
- Mira's approval/rejection tasks are injected explicitly by the harness. The
  report must mark automatic event routing as not proven.
- World minutes follow the frozen timeline. Preview/submit equivalence tests do
  not advance the world between calls.
- No other Agent consumes the reserve.

## 14. Phase 1B remote LLM probe

Phase 1B is observational, not a Runtime gate.

- Run 3–5 times with the same probe world and provider configuration.
- Report behavior-selection rate, `based_on_event` rate and approval-selection rate.
- Do not require 100%.
- A failed precondition marks the run `invalid_probe`, not an Agent failure.

Per-run preconditions:

1. The denial is present in Tomas's actual Agent view.
2. Mira's approval task is injected.
3. `request_access` is in Tomas's available options.
4. `approve_withdrawal` is in Mira's available options.

Observed behaviors:

- Does Tomas understand the blocked behavior and its reason?
- Does Tomas choose `request_access` after the denial?
- Does Tomas supply a valid `based_on_event`?
- Does Mira choose to approve?
- After approval, does Tomas choose to withdraw?

Report format:

| Field | Value |
| --- | --- |
| Provider / model | concrete provider and model id |
| Runs | 3–5 |
| Valid probes | count |
| Invalid probes | count and reason |
| Request rate | count / valid probes |
| Causal-reference rate | count / valid probes |
| Approval rate | count / valid probes |
| Withdrawal-after-approval rate | count / valid probes |
| Notes | observed failures and preconditions |

## 15. Acceptance status template

Every acceptance item must be reported as exactly one of:

| Status | Meaning |
| --- | --- |
| `PROVEN` | Deterministic test and state evidence exist |
| `NOT_PROVEN` | No test, or coverage is insufficient |
| `FAILED` | Test executed and violates the contract |

The Phase 1 report must include a Proven / Not Proven table. At minimum, these
remain `NOT_PROVEN` after Phase 1A:

- Request expiry.
- Delegation expiry.
- General multi-scope isolation.
- Concurrent approvals.
- Mid-flight revocation.
- Automatic targeted wake.
- Visibility and information asymmetry.
- Legitimacy.
- Story, God and Director institutional evolution.
- Multi-agent competition and resource reservation.
- Real LLM necessarily choosing the intended behavior.

## 16. Implementation plan

### 16.1 Current code gaps

These are the concrete gaps between the current runtime and the frozen contract:

1. `behavior_constraint` removes denied behaviors from the Agent's executable
   list; there is no blocked-option projection.
2. A direct submission of a denied behavior returns a generic
   `behavior_unavailable`, not a typed rule denial.
3. Denied behavior attempts do not emit `behavior.requested` / `behavior.denied`.
4. Rule evaluation is split across `agent_view/behavior_view.py`,
   `platform/activity.py`, and admission code; there is no shared evaluator.
5. Rule conditions support `context.*` projection paths, but relationships are
   not projected into the authorization context.
6. There is no access-request or delegation persistence model.
7. `relationship_delta` only adjusts strength and cannot write delegation scope,
   status or revocation fields.
8. The relationship table has one row per `(from, to, type)`, so it cannot retain
   multiple historical delegation records by itself.
9. There is no `based_on_event` field or validation.
10. Denial memory is not injected into the next decision view.

### 16.2 Milestones

| Milestone | Work | Exit criteria |
| --- | --- | --- |
| M0 | Freeze document, create `worlds/social_authority_probe`, create test fixtures | World validates; both Agents and all actions exist |
| M1 | Add authorization domain model: access request, delegation, status transitions, invariants | Repository tests for request/delegation lifecycle |
| M2 | Add pure shared evaluator and eligibility projection | Unit tests for withdraw/approve/reject/revoke decisions |
| M3 | Expose blocked behaviors and reasons in the decision view; implement preview/submit equivalence | SA-01, SA-15 |
| M4 | Implement request/approve/reject/revoke actions and atomic transactions | SA-03, SA-04, SA-07..SA-11 |
| M5 | Implement withdrawal authorization, started/completed/failed transactions | SA-02, SA-05, SA-06, SA-12, SA-14 |
| M6 | Implement denial memory and `based_on_event` validation | SA-16, SA-17 |
| M7 | Implement deterministic contract tests and negative-space checks | SA-01..SA-18 pass |
| M8 | Implement remote LLM probe and report | SA-19 report with valid-probe counts |
| M9 | Full suite, benchmark, docs, commit | Full tests green; benchmark recorded; worktree clean |

Dependency order: `M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> M8 -> M9`.

### 16.3 Likely module changes

| Area | Likely files |
| --- | --- |
| Pure authorization domain | new `src/sim2/world/authorization.py` |
| Persistence model | `src/sim2/persistence/store.py`, new delegation/request repository module |
| Shared evaluator service | new `src/sim2/platform/authorization.py`, registered in `platform/services.py` |
| Blocked eligibility view | `src/sim2/agent_view/behavior_view.py`, `compiler.py`, `choices.py`, `validator.py` |
| Prompt projection | `src/sim2/prompting/sections_character.py`, `compaction.py` |
| Typed denial events | `src/sim2/platform/behavior_intent.py`, `platform/events.py` |
| Withdrawal transactions | `src/sim2/platform/activity.py`, `platform/world_tick_service.py` |
| Relationship/delegation mutation | `src/sim2/platform/activity.py`, `persistence/repositories/entities.py` or new repository |
| Probe world | `worlds/social_authority_probe/` |
| Deterministic tests | `tests/test_social_authority_loop.py` |
| Remote probe | `scripts/verify_social_authority.py` |

Exact file placement may change, but the shared evaluator must live below the
Platform layer and must not import world packages or contain world-specific
branches.

### 16.4 Delegation persistence decision

P0 uses a first-class delegation record, not only a relationship row:

- `delegation_id` is stable and auditable.
- `status`, `scope`, `granted_by`, `revoked_at`, `revoked_by` are explicit.
- A partial unique index enforces one active delegation per
  `(source, target, scope)`.
- Revocation updates the record and preserves it.
- A new delegation after revocation creates a new record with a new id.

This avoids overwriting revoked history in the relationship table. The
relationship table remains the social graph; the delegation table is the
authorization lifecycle.

### 16.5 Shared evaluator implementation rule

The evaluator must be a pure decision function over plain data:

```text
authorization_decision(agent, action, action_context, world) -> Eligibility
```

It must not write state, emit events, read the current wall clock outside the
provided world snapshot, or import Agent/Platform services. Platform services
call it and own the transaction.

### 16.6 Withdrawal transaction implementation rule

Admission and completion are separate transactions:

- Admission: behavior start + `resource.withdrawal.started`.
- Completion: inventory decrement + `resource.withdrawal.completed`.
- Completion failure: record `resource.withdrawal.failed` and move the behavior
  to failed/interrupted; do not delete the started event.
- P0 withdrawal has no admission cost and no resource reservation. The only
  inventory change is the completion decrement.

### 16.7 Test names

Deterministic tests should map one-to-one to the acceptance matrix:

```text
tests/test_social_authority_loop.py::test_sa_01_preview_discriminates_steward
tests/test_social_authority_loop.py::test_sa_02_non_steward_withdraw_denied
tests/test_social_authority_loop.py::test_sa_03_request_cites_denial
tests/test_social_authority_loop.py::test_sa_04_approval_creates_delegation
tests/test_social_authority_loop.py::test_sa_05_withdrawal_decrements_once
tests/test_social_authority_loop.py::test_sa_06_revocation_denies_withdrawal
tests/test_social_authority_loop.py::test_sa_07_self_approval_denied
tests/test_social_authority_loop.py::test_sa_08_non_steward_approver_denied
tests/test_social_authority_loop.py::test_sa_09_reject_branch
tests/test_social_authority_loop.py::test_sa_10_approval_mismatch_codes
tests/test_social_authority_loop.py::test_sa_11_duplicate_delegation_rejected
tests/test_social_authority_loop.py::test_sa_12_wrong_scope_delegation_denied
tests/test_social_authority_loop.py::test_sa_13_idempotent_retries
tests/test_social_authority_loop.py::test_sa_14_transaction_failure_rollbacks
tests/test_social_authority_loop.py::test_sa_15_preview_submit_equivalence
tests/test_social_authority_loop.py::test_sa_16_event_schema_and_order
tests/test_social_authority_loop.py::test_sa_17_denial_memory_and_causal_reference
tests/test_social_authority_loop.py::test_sa_18_negative_space
```

`SA-19` is separate:

```text
scripts/verify_social_authority.py --provider remote --runs 5
```

### 16.8 Definition of done

Phase 1 is complete only when:

1. SA-01 through SA-18 pass deterministically.
2. Negative space is clean in every contract test.
3. SA-19 is reported separately with valid-probe counts and rates.
4. Full test suite passes.
5. Benchmark is recorded. The 1,000-Agent non-LLM p95 remains at or below
   250 ms; any larger regression is investigated before sign-off.
6. No Story, God or Director task is created in Phase 1A.
7. The report clearly lists Proven, Not Proven and Failed items.

## 17. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Four copies of rule logic drift | One pure evaluator; architecture test forbids duplicate authorization paths |
| Blocked options create model loops | Attemptable reasons only; per-intent idempotency; no automatic retry loop in P0 |
| Delegation history overwritten | First-class delegation table with stable ids and status history |
| Event/state partial commit | Same-transaction commits plus failure-injection tests |
| Denial memory not in next view | Explicit view field and SA-17 assertion |
| `based_on_event` becomes a decorative string | Structured validation against actor, type, behavior, target and time |
| LLM probe flakiness | Separate observational report; invalid-probe preconditions |
| Relationship projection cost at 1000 Agents | Measure with `scripts/benchmark_platform.py`; cache only if measured necessary |
| P0 scope creep | This document is the only scope authority; new work requires a failed P0 assertion |

## 18. Change control

After sign-off, every question is answered in this order:

1. Which frozen assertion failed?
2. Is the implementation violating the contract, or is the contract itself
   contradictory?
3. What repeatable evidence supports the fix?

No new abstraction and no Phase 1 scope expansion without a failed frozen
assertion. Story, God, Director, legitimacy and visibility remain P1 until Phase 1
has repeatable evidence.

## 19. Freeze declaration

The Phase 1 external contract is frozen at this document. It covers:

- Authorization behavior and canonical error codes.
- Access request and delegation lifecycle.
- State machine and independent negative fixtures.
- Event schema and causal chain.
- Transaction boundaries and idempotency.
- Deterministic acceptance matrix and negative space.
- Test environment and remote-probe reporting.

Internal implementation remains open, provided it satisfies:

- one shared authorization evaluator,
- explicit delegation lifecycle and history,
- atomic state/event commits,
- no world-specific branches in Platform,
- no hidden capability filtering of authority checks.

Design sign-off does not mean implementation is proven. Phase 1 is proven only
when SA-01 through SA-18 pass with repeatable state evidence and SA-19 is reported
separately.
