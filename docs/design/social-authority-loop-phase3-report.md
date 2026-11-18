# Social Authority Loop — Phase 3 Implementation Report

Status: **Phase 3A proven; Phase 3B observed; Phase 3C decision: no Phase 4 trigger**  
Contract: [social-authority-loop-phase3.md](social-authority-loop-phase3.md)  
Compatibility: **Phase 1 SA-01..SA-18 and Phase 2 PA/PB unchanged**

This report records implementation evidence only. The frozen Phase 3 contract
remains the scope authority. It does not claim that God can generate arbitrary
new mechanisms, and it does not start the Phase 4 Mechanism DSL.

## 1. What was implemented

| Area | Implementation |
| --- | --- |
| Empty world | `worlds/dynamic_social_probe` starts with Mira, Tomas, `relay_station`, `relay_yard`, `travel_to` and `travel` only |
| God content fixtures | `worlds/dynamic_social_probe/proposals.py` contains the proposal payloads; tests submit them through Platform services |
| Resource | `water_plant` location, empty `water_reserve`, and `mira -> water_reserve` stewardship |
| Controlled path | `withdraw_water` action/BT, `water_reserve_stewardship` rule, request/approve/reject/revoke administrative BTs and rules |
| Contested path | `take_water` action/BT, `review_water_violation` action/BT, `water_violation_authority` rule |
| Fill behavior | `fill_water_reserve` uses `world_resource_delta` with `maximum: 10`; this respects the existing guard that God cannot create a stocked resource directly |
| Semantics | resource target/type, positive amount, non-empty scope, covert take mode, authorization operation vocabulary, review parameters, resource authorization target, review behavior operation |
| Director | `targets: [resource]`; the plan is created before the resource and completion is retained in `last_completed_season_plan` |
| Tests | DA-01..DA-10 in `tests/test_dynamic_social_content.py` |
| Probe | `scripts/verify_dynamic_social_content.py` |

All social content is written through `propose_entity`, `propose_definition`
and `propose_world_change`. There is no test-only definition table write and
no new social primitive.

## 2. Actual God content sequence

The frozen design's `water_reserve` initial quantity of 10 was rejected by the
existing `god_resource_creation` guard. The sequence therefore creates an empty
resource and a God-proposed fill behavior:

| Step | Proposal | Result |
| --- | --- | --- |
| 1 | `water_plant` location connected to `relay_station` | accepted |
| 2 | `water_reserve` empty resource at `water_plant` | accepted |
| 3 | `mira -> water_reserve` stewardship | accepted |
| 4 | Fill Water Reserve action + behavior | accepted |
| 5 | Withdraw Water action + behavior | accepted |
| 6 | `water_reserve_stewardship` authorization rule | accepted |
| 7 | Take Water action + behavior | accepted |
| 8 | Request Water Access action + behavior | accepted |
| 9 | Approve Withdrawal action + behavior | accepted |
| 10 | Reject Withdrawal action + behavior | accepted |
| 11 | Revoke Delegation action + behavior | accepted |
| 12 | Review Water Violation action + behavior | accepted |
| 13 | `water_approval_authority` rule | accepted |
| 14 | `water_revocation_authority` rule | accepted |
| 15 | `water_violation_authority` rule | accepted |
| 16 | Mira fills the reserve through the generated behavior | stock 0 → 10 |

Every Action proposal includes `followup_behavior` because the existing
definition contract requires an executable BT in the same transaction.

## 3. Phase 3A acceptance matrix

| ID | Assertion | Test | Status |
| --- | --- | --- | --- |
| DA-01 | Empty world has no water entity, definition, stewardship, authorization rule, violation or restriction | `test_da_01_empty_world_has_no_social_content` | `PROVEN` |
| DA-02 | All God proposals accepted; definitions, rules, assignments and views exist | `test_da_02_god_creates_resource_rules_and_behaviors` | `PROVEN` |
| DA-03 | Generated legal chain: denial → request → approve → withdraw → complete → revoke → `delegation_revoked` | `test_da_03_controlled_withdrawal_chain_on_generated_content` | `PROVEN` |
| DA-04 | Generated violation chain: observed/undetected, sanction, `access_restricted`, forgive, restore | `test_da_04_violation_sanction_and_forgiveness_on_generated_content` | `PROVEN` |
| DA-05 | Director resource plan: baseline 0, resource created, plan completed, next baseline 1 | `test_da_05_director_demand_and_completion` | `PROVEN` |
| DA-06 | Invalid proposals rejected with `failed_check` and zero partial content | `test_da_06_invalid_proposals_leave_no_partial_content` | `PROVEN` |
| DA-07 | Platform source contains no water content identifiers | `test_da_07_platform_source_has_no_water_content` | `PROVEN` |
| DA-08 | Phase 1 denial/authorization and Phase 2 take/witness codes preserved | `test_da_08_phase_one_and_two_codes_are_preserved` | `PROVEN` |
| DA-09 | No withdrawal from take, no delegation, no attack/combat/deception, no Story/God task | `test_da_09_negative_space` | `PROVEN` |
| DA-10 | Duplicate and failed proposals leave content counts unchanged | `test_da_10_duplicate_and_failed_proposals_are_idempotent` | `PROVEN` |

## 4. Chain evidence

Legal chain:

```text
Tomas withdraw_water -> behavior.denied missing_delegation
Tomas request_access -> access.requested
Mira approve_withdrawal -> access.approved + delegation
Tomas withdraw_water -> resource.withdrawal.completed (10 -> 9)
Mira revoke_delegation -> delegation.revoked
Tomas withdraw_water -> behavior.denied delegation_revoked
```

Violation chain:

```text
Tomas take_water -> resource.taken + violation.observed (Mira witness)
Mira review_water_violation sanction -> violation.sanctioned + access_restriction
Tomas withdraw_water -> behavior.denied access_restricted
Mira review_water_violation forgive -> violation.forgiven + restriction revoked
Tomas withdraw_water -> accepted
Mira leaves; Tomas take_water -> violation.undetected, absent from steward view
```

The generated content uses the same Phase 1 and Phase 2 event families and
error codes as the hand-written probe worlds.

## 5. Director evidence

At minute 5 the Director creates an active `resource` plan with baseline 0 and
a `god_structural` task. After God creates `water_reserve`, the minute-10 tick
records `last_completed_season_plan` with `status: completed`, baseline 0, and
creates the next active plan with baseline 1. The Director did not write any
water definition.

## 6. Phase 3B LLM God observation

Provider `remote`, model `deepseek-v4.1-flash`. This is observational, not a
gate.

Main probe: 3 runs, six staged God turns per run.

| Metric | Result |
| --- | --- |
| Valid complete probes | 0/3 |
| God turn statuses | accepted in all three runs |
| Accepted tool calls | 17 |
| Rejected tool calls | 23 |
| Failed checks | `definition_schema`, `entity_schema`, `read_budget` |
| Resources created | 3/3 runs |
| Complete core content | 0/3 runs |
| Legal chain runnable | 0/3 |
| Violation chain runnable | 0/3 |

Every run produced a new resource and several accepted action/behavior
definitions, but none produced the complete authorization-rule and review
surface needed for the two chains. The model repeatedly omitted a positive
withdrawal amount, a scope, or the covert take mode on its first attempt; it
also spent reads on inspection and did not reliably submit
`propose_world_change` rules.

Focused diagnostic run: seven stages, explicitly asking for the two
authorization rules at the end.

| Metric | Result |
| --- | --- |
| Accepted tool calls | 7 |
| Rejected tool calls | 6 |
| Failed checks | `definition_schema`, `read_budget` |
| Generated take behavior | yes (`covertly_draw_cistern_water`) |
| Generated withdraw behavior | yes (`draw_water_from_cistern`) |
| Generated review authorization operation | no |
| Generated resource/review rules | no |
| Chains runnable | no |

The accepted content used the current primitives: `resource_take` with
`mode: covert`, `resource_withdrawal` with a scope, `travel_to`, and visible
resource names that Platform canonicalized. The failures were proposal schema
and task completeness, not a missing lifecycle, transaction or event
primitive.

Phase 3B therefore provides **no evidence that a remote God reliably
instantiates the full social chain in one run**. It does provide evidence that
God can create new entities and several action/behavior definitions, and that
Platform validates and registers them. Phase 3A remains the deterministic gate.

## 7. Phase 3C DSL gap ledger

| Gap | Requested content | Observed constraint | Workaround | Category | Phase 4 candidate |
| --- | --- | --- | --- | --- | --- |
| gap_001 | Stocked resource at creation | Existing `god_resource_creation` rejects direct stock | Empty resource + `world_resource_delta` fill behavior | content assignment | no |
| gap_002 | Separate Action and behavior proposals | Action proposals require `followup_behavior` | Submit Action and BT together | existing contract | no |
| gap_003 | Legal request/approval in an empty world | Phase 1 administrative behaviors are content, not primitives | God proposes request/approve/reject/revoke | content assignment | no |
| gap_004 | Director resource target | `director.targets` accepted place/verb/social/rule only | Added generic `resource` cell to the target vocabulary | small config fix | no |
| gap_005 | Review Action with duration 0 | God-proposed Action duration must be positive | Duration 1; administrative path completes instantly | content workaround | no |
| gap_006 | Administrative Action with no effects | Generic Action validation requires an observable channel | Authorization operation now counts as the observable channel | validator fix | no |
| gap_007 | Rule naming a new BT | Rule validation used the bootstrap compiler snapshot | Refresh the rule compiler before validating a rule | validator fix | no |
| gap_008 | Dynamic resource projection | `world_properties` only projects configured keys | Harness reads the resource entity directly | small projection fix | no |
| gap_009 | Standalone travel choice | A parameterized `travel` BT has no visible destination option without a concrete value | Harness positions actors; travel remains a base definition | content/harness | no |
| gap_010 | Completed Director plan audit | `season_plan` was overwritten by the next active plan | Added `last_completed_season_plan` | small audit fix | no |
| gap_011 | Review visibility | View authorization had no concrete violation binding | Resolve the first reviewable violation for the steward | validator/view fix | no |

No gap requires a new lifecycle, transaction stage, event contract or
visibility rule. Every workaround uses an existing primitive or a small
validator/content fix.

## 8. Phase 4 decision

The frozen Phase 4 triggers are:

1. At least three different new lifecycle/transaction/event needs cannot be
   expressed. **Not met: zero.**
2. The same new mechanism recurs in three or more world content requests.
   **Not met.**
3. God's valid proposal rate is below 50% because of expressiveness rather than
   validation. **The observed accepted tool rate is 17/40 (42.5%), but every
   failure is a schema, completeness or read-budget failure; none requires a
   new lifecycle, transaction or event primitive. This trigger is not met.**

Decision: **do not start Phase 4.** Fix the validator, content and probe
scope. Revisit only if a later content request needs a new lifecycle or event
contract that the current DSL cannot express.

## 9. Verification run

| Check | Command | Result |
| --- | --- | --- |
| Phase 3 deterministic contract | `python -m pytest -q tests/test_dynamic_social_content.py` | `10 passed` |
| Phase 1 and Phase 2 regression | `python -m pytest -q tests/test_social_authority_loop.py tests/test_contested_resource_actions.py` | `37 passed` |
| Full suite | `python -m pytest -q` | `1398 passed` |
| Platform benchmark | `python scripts/benchmark_platform.py --agents 1000 --runs 5` | p95 `203.148 ms`; elapsed `[194.330, 204.594, 195.125, 197.362, 196.627]` ms |
| Phase 3B God probe | `python scripts/verify_dynamic_social_content.py --provider remote --runs 3` | 0/3 complete cores; 17 accepted / 23 rejected tool calls; failed checks `definition_schema`, `entity_schema`, `read_budget` |

## 10. Not proven

- God can generate arbitrary new lifecycle, transaction or event semantics.
- God reliably generates a complete social chain in one turn.
- Automatic event routing and targeted wake.
- Attack, combat, death, deception and stealth.
- Investigation and discovery of undetected violations.
- Multi-agent competition, reservation and concurrent review.
- Story/Director institutional evolution from generated content.
- A remote LLM necessarily producing accepted content.

## 11. Reproduction

```bash
python -m pytest -q tests/test_dynamic_social_content.py
python -m pytest -q tests/test_social_authority_loop.py tests/test_contested_resource_actions.py
python -m pytest -q
python scripts/benchmark_platform.py --agents 1000 --runs 5
python scripts/verify_dynamic_social_content.py --provider remote --runs 3
```
