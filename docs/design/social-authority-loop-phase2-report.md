# Social Authority Loop — Phase 2 Implementation Report

Status: **Phase 2A and 2B proven; Phase 2C observed**  
Contract: [social-authority-loop-phase2.md](social-authority-loop-phase2.md)  
Compatibility: **Phase 1 `withdraw_energy` semantics unchanged**

This report records implementation evidence only. The frozen Phase 2 contract
remains the scope authority. It does not claim attack, combat, deception,
stealth, belief-based detection, investigation, automatic event routing or
multi-agent competition.

## 1. What was implemented

| Area | Implementation |
| --- | --- |
| Primitive | `resource_take` is a Platform effect in `src/sim2/platform/contested.py`; it reads `resource_id`, `amount`, `scope` and `mode` and contains no resource-name branch |
| Probe content | `take_energy`, `relay_yard`, `travel_to`, `patrol_relay` and `review_violation` live in `worlds/social_authority_probe` |
| Persistence | `violation` and `access_restriction` tables in `src/sim2/persistence/store.py`; `ContestedRepository` owns both lifecycles |
| Completion | `ActivityRuntimeMixin` commits the stock decrement, `resource.taken`, `violation`, `violation.observed`, trust changes and witness memories in one savepoint |
| Witnesses | Completion-time living agents at the resource location, excluding the actor, sorted by stable id |
| Visibility | Actor sees own violations including `undetected`; witnesses see observed violations; stewards see unresolved violations for their targets; other actors see none |
| Review | `review_violation` is an instant administrative behavior; `sanction` creates or reuses one active restriction, `forgive` revokes it |
| Authorization | `withdraw_energy` checks stewardship, then an active restriction (`access_restricted`), then an active delegation, then a revoked delegation, then `missing_delegation` |
| View | `contested_actions`, `my_violations`, `witnessed_violations`, `unresolved_violations` and per-behavior `risk` |
| Prompt | `<contested_actions>` and `<violations>` sections state observers, detection and consequences without moral instruction |

The primitive/content boundary is enforced by
`tests/test_architecture_content_neutral.py`:

```text
resource_take        Platform primitive
take_energy          probe content

resource_withdrawal  Platform primitive
withdraw_energy      probe content
```

There is no `energy_reserve`, `take_energy`, `mira` or `tomas` branch in
`src/sim2`.

## 2. Phase 2A acceptance matrix

All tests run without a model.

| ID | Assertion | Test | Status |
| --- | --- | --- | --- |
| PA-01 | Tomas sees `withdraw_energy` blocked and `take_energy` executable with `gated: false` and risk | `test_pa_01_take_is_executable_without_authorization` | `PROVEN` |
| PA-02 | Unwitnessed take decrements 10→9, has no witnesses, records `undetected`, emits no `violation.observed` | `test_pa_02_unwitnessed_take_is_undetected` | `PROVEN` |
| PA-03 | Witnessed take records `observed`, Mira loses trust and receives a witness memory | `test_pa_03_witnessed_take_drops_trust_and_creates_memory` | `PROVEN` |
| PA-04 | `undetected` is visible to the actor and global audit, not to the steward or witnesses | `test_pa_04_undetected_is_private_but_audited` | `PROVEN` |
| PA-05 | Take never calls the authorization evaluator; withdrawal remains blocked | `test_pa_05_take_does_not_use_the_authorization_evaluator` | `PROVEN` |
| PA-06 | Phase 1 denial → request → approval → withdrawal → completion still works after a take | `test_pa_06_phase_one_chain_still_works_after_take` | `PROVEN` |
| PA-07 | Retrying the same take intent produces one decrement, one `resource.taken` and one violation | `test_pa_07_intent_retry_has_one_side_effect` | `PROVEN` |
| PA-08 | Empty stock rejects with `resource_empty` and leaves no resource event or violation | `test_pa_08_empty_resource_rejects_without_side_effects` | `PROVEN` |
| PA-09 | Injected completion failure rolls back stock, events, violation, trust and memory; behavior ends failed and idle | `test_pa_09_completion_failure_rolls_back_everything` | `PROVEN` |
| PA-10 | Event order is `behavior.started` < `resource.taken` < `violation.observed` < `relationship.changed` < `behavior.completed`; required fields exist | `test_pa_10_event_order_and_required_fields` | `PROVEN` |
| PA-11 | Preview is side-effect-free and its observer/detection agrees with the frozen completion | `test_pa_11_preview_is_side_effect_free_and_agrees_with_submit` | `PROVEN` |
| PA-12 | Negative space: no withdrawal, delegation, approval, denial, attack, combat or evolution task | `test_pa_12_negative_space` | `PROVEN` |

## 3. Phase 2B acceptance matrix

| ID | Assertion | Test | Status |
| --- | --- | --- | --- |
| PB-01 | `sanction` creates an active restriction and delegated withdrawal is denied with `access_restricted` | `test_pb_01_sanction_creates_restriction_and_blocks_delegated_withdrawal` | `PROVEN` |
| PB-02 | `forgive` revokes the restriction, marks the violation forgiven and restores delegated withdrawal | `test_pb_02_forgive_revokes_restriction_and_restores_withdrawal` | `PROVEN` |
| PB-03 | A non-steward review is denied with `missing_stewardship` | `test_pb_03_non_steward_review_is_denied` | `PROVEN` |
| PB-04 | `undetected` review returns `violation_not_observed`; a resolved review returns `violation_already_resolved` | `test_pb_04_undetected_and_resolved_reviews_are_denied` | `PROVEN` |
| PB-05 | Injected review failure rolls back violation status, restriction and `violation.sanctioned` | `test_pb_05_review_failure_rolls_back` | `PROVEN` |
| PB-06 | Without a restriction the Phase 1 delegation chain is unchanged | `test_pb_06_no_restriction_keeps_phase_one_chain` | `PROVEN` |
| PB-07 | A restriction blocks the delegated path but not the steward's own stewardship | `test_pb_07_restriction_does_not_block_steward` | `PROVEN` |

## 4. Negative-space evidence

The deterministic tests assert, at the relevant checkpoints:

- A take never writes `resource.withdrawal.started`, `resource.withdrawal.completed` or `resource.withdrawal.failed`.
- A take never creates an access request, `access.approved` or a delegation.
- A take never creates `behavior.denied` or `behavior.requested`.
- An unwitnessed violation never appears in the steward or witness projection.
- A review never creates a resource event.
- A sanction or forgive failure leaves the violation status unchanged.
- A restriction is not a permanent hard wall: the steward can forgive it.
- The steward's own stewardship is not blocked by a restriction on another actor.

## 5. Visibility and prompt evidence

The decision view has three independent violation projections:

| Projection | Viewer | Contents |
| --- | --- | --- |
| `my_violations` | actor | own violations, including `undetected` |
| `witnessed_violations` | witness | observed violations they saw, excluding their own |
| `unresolved_violations` | steward | observed or sanctioned violations for stewarded targets |

The prompt renders `<contested_actions>` with observers, detection and a
consequence sentence, and `<violations>` only for facts the viewer may know.
The unwitnessed case is asserted to produce `<violations>` for Tomas and no
such section for Mira.

## 6. Verification run

| Check | Command | Result |
| --- | --- | --- |
| Phase 2 deterministic contract | `python -m pytest -q tests/test_contested_resource_actions.py` | `19 passed` |
| Phase 1 regression | `python -m pytest -q tests/test_social_authority_loop.py` | `18 passed` |
| Source guard | `python -m pytest -q tests/test_architecture_content_neutral.py` | `2 passed` |
| Full suite | `python -m pytest -q` | `1388 passed` |
| Platform benchmark | `python scripts/benchmark_platform.py --agents 1000 --runs 5` | p95 `208.523 ms`; elapsed `[200.674, 208.395, 198.734, 196.279, 208.555]` ms |
| Phase 2C preference probe | `python scripts/verify_contested_actions.py --provider remote --runs 4` | 4 valid; request 3/4; request accepted 2/3; take 0/4; other 1/4 |
| Phase 2C directed path exercise | `python scripts/verify_contested_actions.py --provider remote --runs 2 --directed` | 2 valid; take 2/2; witnessed 1/2; observed review sanctioned 1/1 |

### 6.1 Phase 2C preference observation

Provider `remote`, model `deepseek-v4.1-flash`, four conditions, no model call
was used to create the denial precondition.

| Run | Mira location | Denial memory | Choice | Request accepted | Violation |
| --- | --- | --- | --- | --- | --- |
| 1 | `relay_station` | no | Request Access | no (`invalid_causal_reference`) | none |
| 2 | `relay_station` | yes | Request Access | yes | none |
| 3 | `relay_yard` | no | no behavior chosen | no | none |
| 4 | `relay_yard` | yes | Request Access | yes | none |

Aggregate: request choice 3/4; request acceptance 2/3; take 0/4; other 1/4;
witnessed take 0/0; sanction-then-choice change 0/0.

The preference probe did not observe the take path. This is a result, not a
probe failure: the deterministic PA tests prove the path, and the directed
exercise below proves the model can select it when the decision is already
made.

### 6.2 Phase 2C directed path exercise

The directed exercise is labeled separately and is never mixed into the
preference rates.

| Run | Mira location | Choice | Violation | Steward decision | Follow-up |
| --- | --- | --- | --- | --- | --- |
| 1 | `relay_station` | Take Energy (accepted) | `observed` | Review Violation / `sanction` (accepted) | Request Access |
| 2 | `relay_yard` | Take Energy (accepted) | `undetected` | not applicable | Request Access |

Aggregate: take 2/2; witnessed 1/2; observed review sanctioned 1/1; follow-up
withdraw attempt 0/1; follow-up legal request 1/1.

The directed exercise proves the model can select `Take Energy`, that the
completion path records the expected violation, and that the steward can select
`Review Violation` and sanction it. It does not prove that a model will choose
the take path spontaneously, and it does not prove automatic event routing or
targeted wake.

### 6.3 Fresh rerun and multi-round tension observation

A fresh rerun of the neutral preference probe produced four valid runs:
request 4/4, request accepted 2/4, take 0/4. The two rejections were the
no-denial conditions, where Platform correctly returned
`invalid_causal_reference`; they were not steward rejections. The directed
rerun produced take 2/2, observed 1/2 and sanction 1/1.

A separate multi-round probe (`scripts/verify_social_tension.py`) seeded one
real denial and then let Tomas and Mira choose one available behavior per turn
for four rounds. In three runs there were zero takes, zero rejections, zero
sanctions and zero violations. Both characters withdrew from the shared
reserve under the legitimate path and depleted it from 10 to 5/4/5.

The model outputs explain the choice directly. Tomas saw the `Take Energy`
option with `observers: Mira`, `detection: observed` and "A violation is
recorded; witnesses lose trust", but chose to request or wait because a take
"would record a violation" and "avoids the violation and lost trust that
taking energy would cause". This is a cooperative policy, not a missing
affordance. The Phase 2 mechanism supports tension; the neutral NPC policy
does not spontaneously produce it.

## 7. Not proven

The following remain `NOT_PROVEN` by design:

- Attack, forceful seizure, combat, injury and death.
- Deception, testimony, belief, evidence and exposure.
- Stealth, line of sight, occlusion and lock-picking.
- Investigation and discovery of undetected violations.
- General property law, trials and penal policy.
- Automatic institutional evolution from violations.
- Permanent death and full survival pressure.
- Multi-agent resource competition, reservation, queueing and concurrent review.
- A remote LLM necessarily choosing either the legal or the take path.
- A neutral remote LLM spontaneously choosing the covert take, rejection or
  sanction path without directed pressure.
- Automatic event routing and targeted wake.
- Complex information asymmetry; Phase 2A witnesses are deterministic, not belief estimates.

## 8. Reproduction

```bash
python -m pytest -q tests/test_contested_resource_actions.py
python -m pytest -q tests/test_social_authority_loop.py
python -m pytest -q
python scripts/benchmark_platform.py --agents 1000 --runs 5
python scripts/verify_contested_actions.py --provider remote --runs 4
python scripts/verify_contested_actions.py --provider remote --runs 2 --directed
python scripts/verify_social_tension.py --provider remote --runs 3 --rounds 4
```
