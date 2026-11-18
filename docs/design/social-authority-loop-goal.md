# Social Authority Loop — Current Goal and Open Issues

Status: **ACTIVE**
Last updated: 2026-11-18

## 1. User goal (verbatim)

> 把 Phase 1~3 真正跑通并修复所有问题：世界自动演化、自己成长、NPC 张力/对抗、
> 合法链可靠性、自动唤醒与闭环。

Every work session must read this file first. Do not mark an issue fixed without a
focused test or a recorded remote-provider probe. Do not delete history; append a
new status instead.

## 2. Definition of done

| Area | Done means |
| --- | --- |
| Phase 1 | Denial → causal request → steward decision → authorized withdrawal remains 5/5 in fresh remote runs |
| Phase 2 | Legal, take, witness, sanction and forgive paths remain deterministic; directed conflict probe still passes |
| Phase 3 | Decomposed God creates a complete pack with legal chain 3/3, not 2/3 |
| World evolution | A Director gap produces complete content without harness-authored stage instructions, or an automatic orchestrator performs the decomposition |
| NPC tension | Under a pressure scenario, neutral NPCs can choose a coercive path at least once without a directed “you have decided to take” prompt; or the limitation is documented with evidence |
| Automatic closure | A witnessed violation wakes the steward and creates a review task without harness injection |
| Long growth | A free multi-season run creates, executes and re-enters the next gap without harness stage injection |
| Tests | Each fix has a focused regression test; iteration uses `scripts/test_parallel.py --changed`; the full suite is only a release gate |
| Phase 4 | Stays closed unless the frozen Phase 4 triggers are met |

## 3. Non-negotiables

- Use `--provider remote` for LLM verification; never report local-Qwen results as remote results.
- Do not force NPCs to take resources in neutral probes. Separate **capability** from **spontaneous policy**.
- Do not add Phase 4 primitives for prompt, schema, orchestration or content-pressure problems.
- Inspect the actual `decision_view`, prompt and model output; aggregate counters alone are not evidence.
- Never run the full 10-minute suite for ordinary iteration.

## 4. Verified baseline (2026-11-18)

| Item | Evidence |
| --- | --- |
| Phase 1 remote | 5/5 request, causal reference, approval, withdrawal |
| Phase 2 neutral remote | 4/4 request, 2/4 accepted (two no-denial `invalid_causal_reference`), 0/4 take |
| Phase 2 directed remote | 2/2 take, 1/2 witnessed, 1/1 observed sanction |
| Tension multi-round | 3 runs × 4 rounds: 0 take, 0 sanction, 0 violation; cooperative equilibrium |
| Phase 3 decomposed remote | 3/3 complete cores, 3/3 violation chains, 2/3 legal chains; 45 accepted / 6 rejected |
| Phase 3 broad remote | 0/3 complete cores; improved prompt still 0/1 |
| Deterministic | Phase 1 `18 passed`, Phase 2 `19 passed`, Phase 3 `13 passed`, source guard `2 passed` |

## 5. Open issues

| ID | Problem | Evidence | Required fix | Acceptance | Status |
| --- | --- | --- | --- | --- | --- |
| G-01 | An administrative behavior can be accepted without `authorization_operation`, producing an inert legal path | Fresh Phase 3: one `request_access` behavior had `authorization_operation: null`; legal chain `missing_admin_content` | Add a generic semantic validator for social-operation parameters without an operation; make the Phase 3 stage readiness require `open_access_request` so the stage retries | A proposal with `based_on_event`/`violation_id` params and no operation is rejected with a typed check; fresh decomposed legal chain 3/3 | OPEN |
| G-02 | One broad God task cannot finish the pack; model submits one proposal per turn and exhausts reads | Broad 0/3; improved prompt 0/1; `read_budget`, `entity_name_duplicate`, schema omissions | Add an automatic decomposition/continuation path (Director gap → atomic stages) or a bounded multi-proposal plan; do not loosen Platform safety | `god_structural` completes one full pack from one task without harness-authored stage instructions, or an orchestrator does the decomposition automatically; broad complete core 1/1 | OPEN |
| G-03 | A witnessed violation does not automatically wake the steward; review is harness-injected | Phase 2C and tension probes inject Mira’s review task | Add/configure an event wake on `violation.observed` for the steward and create a review task; cover with a deterministic test | After a witnessed take, the steward receives a review task and can sanction without harness injection | OPEN |
| G-04 | Neutral NPCs do not spontaneously choose the coercive path | 0/4 neutral take; 0/3 multi-round take; model sees `observers/detection/consequence` and still requests/waits | Add a pressure scenario (scarcity, deadline, third claimant, persona) and measure; do not hard-code take | In N neutral pressure runs, at least one take/rejection/sanction occurs without a directed “you have decided” prompt; report the rate honestly | OPEN |
| G-05 | Long-term autonomous growth is unproven | No free multi-season run; Phase 3B is 15 harness-driven stages | Run a multi-season free loop after G-02/G-03; record gap → creation → execution → next gap | A free run creates and executes a new pack, then a new Director gap appears and is handled without stage injection | OPEN |
| G-06 | Read budget and schema adherence reduce God reliability | 6 rejected Phase 3 tool calls; repeated read-budget stops | Tune the role read budget/contract and add schema repair prompts; keep the safety cap | Fewer than 2 rejected calls per decomposed run; no read-budget stop before the final proposal | OPEN |
| G-07 | The first tension aggregate undercounted direct intents | Mira’s direct `behavior_ref: behavior:withdraw_energy` was not a `request_behavior` tool call | Script fixed to read `turn.response`; rerun needed to publish corrected counts | Corrected probe rerun completed; docs updated | IN_PROGRESS |

## 6. Work order

1. **G-01** semantic validator + Phase 3 stage readiness; add regression tests.
2. **G-07** rerun the corrected tension probe and publish accurate counts.
3. **G-03** event-driven violation wake; deterministic test.
4. **G-02** automatic decomposition/continuation.
5. **G-04** pressure scenario; measure without directing the choice.
6. **G-05** free multi-season run.
7. **G-06** reliability tuning after the structural fixes.

## 7. Progress log

### 2026-11-18

- Phase 1-3 fresh remote verification completed and recorded in
  `social-authority-loop-phase3-report.md` §9.2.
- Tension probe added at `scripts/verify_social_tension.py`.
- Counting bug for direct intents found and fixed; rerun still pending (G-07).
- Phase 3 legal chain fresh result is 2/3 because of G-01.
