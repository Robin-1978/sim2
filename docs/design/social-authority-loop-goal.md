# Social Authority Loop — Current Goal and Open Issues

Status: **ACTIVE**
Last updated: 2026-11-19

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

## 4. Verified baseline (updated 2026-11-19)

| Item | Evidence |
| --- | --- |
| Capability model (2026-11-19) | One `transfer_resource` family + target bindings. Remote: legal 1/1 request, causal reference, approval and withdrawal; directed take 1/1 witnessed and sanctioned; dynamic God **15 accepted / 0 rejected**, complete core 1/1, legal 1/1, violation 1/1; growth 240 minutes **passed, 0 rejected sessions**, 12 plans opened / 11 completed, 17 structural units, 4 new behaviors executed, 8 Story beats, all five cells grew. Every God-created stock Action used `verb: transfer_resource` with `resource_id` and `context.resource_id`. Full suite **1459 passed**. |
| Phase 1 remote | 5/5 request, causal reference, approval, withdrawal |
| Phase 2 neutral remote | 4/4 request, 2/4 accepted (two no-denial `invalid_causal_reference`), 0/4 take |
| Phase 2 directed remote | 2/2 take, 1/2 witnessed, 1/1 observed sanction |
| Tension multi-round | Corrected neutral 3 runs × 4 rounds: 0 take, 0 sanction, 0 violation, 4 requests, 4 approvals, **1 spontaneous revocation (1/3 realized)**. Pressure 3 runs × 4 rounds: **2/3 realized**, 2 rejections, 1 revocation, 0 takes |
| Phase 3 decomposed remote | Fresh 3-run sample: **3/3 complete core, 3/3 legal, 3/3 violation**; 45 accepted / 28 rejected (mostly probe read/tool budget). Original sample was 2/3 legal |
| Phase 3 broad remote | Free-form one-shot remains 0/3; **contract-driven staged broad** now 1/1 complete core, 1/1 legal and 1/1 violation, with 15 accepted / 0 rejected and no failed checks. With the relevance-scoped index the prompt stayed at 14,423 to 15,396 chars and omitted no required section |
| Minimal evolution loop | `audit_dynamic_evolution_loop.py --provider remote --rounds 3`: passed, `flags: []`; round 1 God created a Deposit Energy Cell Action/BT, Mira executed it and the reserve moved 0→1, Story created an emergent beat; rounds 2-3 completed research and another emergent beat |
| Free 8-round evolution | `audit_dynamic_evolution_loop.py --provider remote --rounds 8`: passed, `flags: []`; God created `Deposit Energy Cell` in round 1 and new research sources `Relay Checksum Fragment` (round 4) and `Relay Second Checksum Record` (round 6); Mira executed each next behavior and Story produced 5 beats. One research-synthesis God task was queued when the round window ended |
| LLM Director | Separate `roles.director` remote role (`1024` tokens, 4 iterations, 2 reads); actual call chose `resource` and wrote `plan:5:resource` plus a `god_structural` task |
| Full one-cycle loop | LLM Director → 15-stage God → LLM NPCs → Story: plan completed, next plan opened at `baseline: 1`; Story produced two accepted beats and one wait; God 18 calls / 17 iterations |
| Automatic LLM Director | `director.policy: llm` now runs inside `SimulationRunner`: Aster probe recorded **18 Director calls** in 360 minutes (`social`, `verb`, `rule`, `resource`, `place`) with no harness ordering; tidebound 2-day run completed `plan:1440:social` at minute 2880 and opened `plan:2880:social` |
| Automatic multi-cell growth | Aster probe 360 minutes: **22 structural units** (12 entities, 5 Actions, 5 behaviors), all five cells grew to `place=5, verb=7, social=5, rule=5, resource=5`, **5 new behaviors executed** by Mira or a God-created agent, **13 Story beats**, **0 rejected sessions** |
| Structural contract guard | `max_proposals` bounds a stageless contract; `required_content.growth_cell` keeps the session open until the Director's cell count exceeds its baseline; routine place/verb sessions finish in 2–5 iterations instead of exhausting 24 |
| Deterministic | Phase 1 `18 passed`, Phase 2 `19 passed`, Phase 3 `13 passed`, source guard `2 passed` |

## 5. Open issues

| ID | Problem | Evidence | Required fix | Acceptance | Status |
| --- | --- | --- | --- | --- | --- |
| G-01 | An administrative behavior can be accepted without `authorization_operation`, producing an inert legal path | Original fresh Phase 3: one `request_access` behavior had `authorization_operation: null`; legal chain `missing_admin_content`. After the validator and stage gate: fresh 3-run sample is **3/3 complete core, 3/3 legal, 3/3 violation**, with 15 accepted proposals per run | Generic validation rejects an Action with `based_on_event`/`violation_id` parameters without `authorization_operation`; Phase 3 stage readiness requires the exact operation and retries | A proposal with social operation parameters and no operation is rejected with a typed check; fresh decomposed legal chain 3/3 | FIXED / VERIFIED (3/3 legal chains) |
| G-02 | One broad God task cannot finish the pack; model submits one proposal per turn and exhausts reads | Free-form broad 0/3; contract-driven staged broad 1/1 complete core, 1/1 legal, 1/1 violation, 15 accepted / 0 rejected | Engine keeps a structural session open across proposals, and contracts declare ordered stages. The engine injects the current atomic instruction, checks semantic completion, advances automatically and finishes on the last stage | `god_structural` completes one full pack from one task without harness-authored stage instructions; broad complete core 1/1 | FIXED / VERIFIED for contract-driven stages; free-form one-shot remains a documented limitation |
| G-03 | A witnessed violation does not automatically wake the steward; review is harness-injected | `violation.observed` wakes Mira; `access.requested` publishes through the configured wake path; the full loop ran three Story tasks, with two accepted beats and one wait | Keep the wake path on every relevant event append; the full-loop probe queues configured Story events whose append path predates the wake | After a witnessed take or request, the relevant consumer receives a task without harness injection and the role completes it | FIXED / VERIFIED (wake tests + full-loop Story beats) |
| G-04 | Neutral NPCs do not spontaneously choose the coercive path | Neutral: 0/4 take, 0/12 multi-round take. Pressure (2 units, 30-minute failure, third claimant Ilya, exhausted persona): 3 runs × 4 rounds, **2/3 runs realized tension**, 2 rejections, 1 revocation, 1 approval, 0 takes | `scripts/verify_social_tension.py --pressure` changes scarcity and claimants only; it never says “take” or “you have decided”. The reserve is a real world resource and the third claimant is a real agent | In N neutral pressure runs, at least one take/rejection/sanction occurs without a directed “you have decided” prompt; report the rate honestly | FIXED / VERIFIED for pressure rejection/revocation; spontaneous covert take still not observed |
| G-05 | Long-term autonomous growth is unproven | Free 8-round event-driven run passed with `flags: []`: round 1 God created `Deposit Energy Cell` and Mira executed it; rounds 4 and 6 God created `Relay Checksum Fragment` and `Relay Second Checksum Record`; Mira studied each and Story produced five beats. The full one-cycle Director→God→NPC→Story loop also completed and opened `baseline: 1`. A second Director-driven pack is still untested | Keep the free 8-round run as the long-growth evidence; optionally add a second Director contract target in a future probe | A free run creates and executes new content, then the next gap is created and handled without harness stage injection across seasons | FIXED / VERIFIED for the free event-driven loop; second Director pack remains a documented limitation |
| G-06 | Read budget and schema adherence reduce God reliability | Baseline decomposed 3-run sample: 45 accepted / 28 rejected, mostly probe-imposed `read_budget` / `tool_budget` (probe used 3 iterations, 1 read). Contract-driven broad 3-run: 45 accepted / 2 rejected, both `tool_arguments`; final optimized broad run: **15 accepted / 0 rejected**, prompt 4,731–7,161 tokens, 71.5% cached | Align the probe with the real God budget (24 iterations / 40 tool calls / 4 reads); compact the staged contract and schemas; fix the raw contract `tools` alias so only the six contract tools are exposed | Fewer than 2 rejected calls per structural run; no read-budget or budget-exhausted stop before the final proposal | FIXED / VERIFIED (broad 3-run ≤2 rejects; final optimized run 0) |
| G-07 | The first tension aggregate undercounted direct intents | Mira’s direct `behavior_ref: behavior:withdraw_energy` was not a `request_behavior` tool call. Corrected remote 3×4 run: 4 requests, 4 approvals, 0 takes, 0 sanctions, 1 revocation, 12 turns per run | Script reads `turn.response` as well as `request_behavior` tool calls | Corrected probe rerun completed; docs updated | FIXED / VERIFIED (corrected neutral counts published) |
| G-08 | Context grows with the world and the 16k-char compaction omits action/behavior catalogs | Before: ~15.9k chars, `compacted: true`, catalogs omitted. After the relevance index: broad staged run 1/1 complete core, legal and violation chains, 15 accepted / 0 rejected, prompt 14,423 to 15,396 chars, `omitted_required: []`, `omitted: []` | Implemented a relevance-scoped `world_index`, paged `query_world_index`, one-definition `inspect_definition`, persisted stage progress and structural history summaries | A larger world keeps the structural prompt bounded while the model can still see the current target's relevant content and does not duplicate existing definitions | FIXED / VERIFIED (broad remote 1/1; 100-entry prompt test) |
| G-09 | Automatic growth still depended on a probe calling the Director, and a generic structural contract could accept content that did not increase the target cell | Before: `SimulationRunner` never called `plan_with_provider`; a place/verb session reached 24 iterations with `budget_exhausted`; an agent proposal left `social` at baseline; a location with `routes:[{to,distance}]` failed validation | `director.policy: llm` calls the one-call Director before the tick with deterministic fallback; `max_proposals`; `required_content.growth_cell`; cell-count definitions in the God ledger; route-object normalization; immediate `tool_budget` stop; Director-cadence retries capped to the plan window; Director calls and plan history are audited | A continuous `Platform.run` reaches multiple Director plans, completes plans, creates structural units, and an NPC executes a new behavior | FIXED / VERIFIED (Aster 360 min: 18 Director calls, 22 units, 5 new behaviors executed, 13 beats, 0 rejected sessions; tidebound 2-day social plan completed and the next plan opened) |
| G-10 | Add-only behavior growth can exhaust the NPC prompt and reward reskinned verbs | Before: 20 unlocked behaviors produced 14.4k chars, 40 reached the 16k cap, and 160 cut a block and dropped `<decision>`; no catalog cap and no Director cell cap | Catalog rotation `max_unlocked_behaviors` (24), tiered rendering (five full + compact rest), `query_behavior_catalog`, Action shape dedup, `director.max_cell_units` saturation, compaction that keeps complete blocks and the decision | A large catalog stays inside the prompt with a decision, and saturated cells stop opening plans | FIXED / VERIFIED (160 behaviors: 15,983 chars, 5 full + 102 compact + 58 omitted, decision present; focused suite 40 + changed workers) |

### 5.1 Status summary

| Status | Count | IDs |
| --- | --- | --- |
| Fixed / verified | 10 | G-01, G-02, G-03, G-04, G-05, G-06, G-07, G-08, G-09, G-10 |
| Open / partial / in progress | 0 | none |

All tracked issues are closed. The evidence is:

- **G-01** 3/3 fresh legal chains.
- **G-06** broad runs with at most two rejected calls, and a final optimized
  run with 15 accepted / 0 rejected.
- **G-04** pressure tension realized through rejection and revocation.
- **G-05** 8-round free event-driven growth with `flags: []`.
- **G-07** corrected neutral counts published.
- **G-08** relevance-scoped context, schema compression and omission telemetry
  are recorded in [context-scaling-plan.md](context-scaling-plan.md).
- **G-09** the ordinary runner now grows the world: an Aster 360-minute run
  recorded 18 LLM Director decisions, 22 structural units, five new behaviors
  executed by characters, 13 Story beats and no rejected sessions; a two-day
  tidebound run completed its social plan and opened the next one.

The local role values and the separate Director configuration are recorded in
[llm-role-configuration.md](llm-role-configuration.md).

## 6. Work order

All tracked issues are fixed or verified. Remaining documented limitations:

1. Free-form one-shot broad prompts are still not reliable; the supported paths
   are the contract-driven stage sequence and a bounded generic contract with
   `max_proposals` and `required_content.growth_cell`.
2. Spontaneous covert take was not observed; pressure produced rejection and
   revocation instead.
3. A structural session still depends on the model producing a valid proposal
   shape. Bad output is corrected, bounded and retried at the Director cadence,
   but a plan can expire before a later attempt succeeds.

Completed: **G-01** 3/3 legal chains, **G-02** contract-driven broad,
**G-03** event-driven wakes, **G-04** pressure tension, **G-05** 8-round free
growth, **G-06** reliable broad run, **G-07** corrected counts, **G-08**
relevance-scoped context, **G-09** automatic multi-cell growth through the
ordinary runner.

Completed architectural prerequisite: **G-08** relevance-scoped context, bounded
index reads, persisted stage progress and omission telemetry. The next structural
run should use the new index instead of expanding the old catalogs.

Completed capability migration (2026-11-19): all shipped world Actions declare a
`verb`; the transfer/consume/move/gather/inventory families bind their target as a
parameter; `dynamic_social_probe`, `social_authority_probe` and
`starter_village` no longer register per-resource copies. Definition validation
rejects a target-specific known-verb Action even when no generic exists, and a
world-pack lint covers every `worlds/*/actions.yaml`.

## 7. Progress log

### 2026-11-19

- Capability migration (engine + content): Actions now carry an explicit `verb` family and every shipped world binds targets through parameters instead of registering `take_water`/`withdraw_fuel`-style copies. `dynamic_social_probe` uses one `fill_resource`/`withdraw_resource`/`take_resource` family and one `review_violation`; adding a second or third resource now adds only an entity, a stewardship and two rules. `social_authority_probe` uses `withdraw_resource`/`take_resource` with a literal `resource_id` binding in the Behavior node; the admission path resolves that binding before authorization and contested feasibility. `starter_village` uses `withdraw_stock` and binds `village_food`/`rations` in `get_food`. Validation rejects the first target-specific Action (no bootstrap exception), and `tests/test_world_pack_capabilities.py` lints every world Action. Focused suites and changed workers passed; remote re-verification is recorded separately after it runs.
- Target binding resolver: `world/bindings.py` resolves Action-node literals, nested Behavior bindings and Agent parameters for admission and completion; a literal node binding cannot be overridden by an Agent parameter. The decision view marks each visible resource option `available`/`blocked_reason` when a generic capability has per-resource authority.
- Capability migration verification (remote `deepseek-v4.1-flash`):
  - `verify_social_authority.py --runs 1`: 1/1 request, 1/1 causal reference, 1/1 approval, 1/1 withdrawal accepted. The behavior is now `withdraw_resource` with a literal `energy_reserve` node binding; no per-resource Action exists.
  - `verify_contested_actions.py --runs 1 --directed`: 1/1 valid probe, take 1/1, witnessed take 1/1, review decision `sanction`. The follow-up request was rejected with `invalid_causal_reference` because a restriction is not a denial event; this is the expected separation, not a regression.
  - `verify_dynamic_social_content.py --runs 1`: 1/1 complete core, **15 accepted / 0 rejected**, legal chain `completed`, violation chain `sanctioned`. The probe now reads the world's 15 stages instead of duplicating them.
  - `verify_automatic_growth.py --minutes 240`: passed, `flags: []`, 8 Director plans opened / 7 completed, 8 structural units, 1 new behavior executed (`settle_archive_checksum`), 2 Story beats, 3 budget-exhausted God sessions.
  - `verify_automatic_growth.py --minutes 360`: passed, `flags: []`, 8 plans opened / 5 completed, 10 structural units, 4 new behaviors executed, 2 Story beats, 21 budget-exhausted God sessions. The wider reject count comes from model iteration on malformed proposals, not from accepting target-specific Actions; the accepted Actions (`draw_relay_power`, `ration_relay_power`) are generic `transfer_resource` with `resource_id` parameters.
  - Final `verify_automatic_growth.py --minutes 240` after the literal-reserve lint: passed, `flags: []`, 12 Director plans opened / 11 completed, 17 structural units, **0 rejected sessions**, 3 new behaviors executed (`crank_the_storm_cellar_dynamo`, `log_circle_agreement_in_the_ledger`, `route_reserve_charge_to_relay`), 7 Story beats, and ledger `place=3, verb=3, social=4, rule=4, resource=3`. Every God-created stock Action is generic: `crank_dynamo_charge`, `draw_reserve_charge`, `tap_capacitor_bank` and `stock_salvage_rack` use `verb: transfer_resource` with `resource_id` and `context.resource_id`.
- Content audit of the free LLM loop found five defects: `resource_transfer` could deposit from another location; God-created research discoveries were bare identifiers; Action definitions carried Behavior-only fields; the generated Behavior was a concrete binding without Travel; and the decision view did not show bound stock/research facts. Fixes: `resource_transfer` now requires the actor at the stock's location in start, parallel-start, completion and decision view; `research_discoveries` must be `{id?, fact: readable sentence}`; Action definitions reject Behavior-only fields; the resource-safety contract requires Travel or Agent parameters; bound stock and research facts are rendered in the decision view; the world index exposes entity locations.
- Free LLM rerun after the content fixes (`--rounds 5`, remote): passed, `flags: []`; God's accepted Action no longer contains Behavior fields and its Behavior includes `Travel To Relay Lab` before `Deposit Energy Cell`. God waited on the later research-synthesis wake instead of creating a source, so the new research-fact validation is covered deterministic tests, not by a new remote source.
- Growth rerun after the content fixes (`--minutes 240`, remote): passed, `flags: []`, 12 plans opened / 11 completed, 17 structural units, **0 rejected sessions**, 4 new behaviors executed, 8 Story beats.
- Residual audit finding: `resource_withdrawal` and `resource_take` start paths still do not share the `resource_transfer` location gate. The growth run generated `log_approved_rationed_draw` without a Travel node, so a remote withdrawal remains possible. A future fix needs a generic location binding for the dynamic probe's parameterized withdrawal behavior; it is documented rather than silently forced.
- Focused suites (185 passed) and changed workers (237 + 596 + 250 + 376 passed) are green. Full release gate after the content-audit fixes: **1459 passed** across four isolated workers; compileall is clean.
- G-10 catalog bounds: measured the NPC decision prompt against duplicated behaviors: 20 unlocked behaviors = 14,410 chars, 40 = 15,960 (cap), 160 = the old fallback cut a block and dropped `<decision>`. Now the prompt keeps five full behaviors, lists the rest as one-line entries, preserves complete lines and the decision, and omits 58 of 162 with an explicit note at 15,983 chars. Added `max_unlocked_behaviors` (24) rotation, `query_behavior_catalog`, Action shape dedup, and `director.max_cell_units` saturation. This is a bound, not the final capability model: the next design collapses target-specific trees into one parameterized verb.
- G-09 automatic growth verified through the production runner. Aster probe: 360 simulated minutes, **18 LLM Director calls**, **22 structural units** (12 entities, 5 Actions, 5 behaviors), all five cells grew from `place=2, verb=2, social=0, rule=1, resource=1` to `place=5, verb=7, social=5, rule=5, resource=5`, **5 new behaviors executed** (`cross_check_archive_checksum`, `meter_catchment_charge_at_gantry`, `meter_the_catchment_tray`, `record_draw_authorization_at_bench`, `trace_encoded_relay_route`), **13 Story beats**, **0 rejected sessions**. Tidebound 2-day run: `plan:1440:social` completed at 2880 and `plan:2880:social` opened; God created a faction and a relationship; 12 Story beats.
- G-09 contract fixes: `director.policy: llm` runs the one-call Director before the tick (deterministic fallback on error); `max_proposals` bounds stageless contracts; `required_content.growth_cell` compares the target cell with the plan baseline and returns a correction; the God ledger now states what each cell counts; `routes:[{to,distance}]` is normalized; a `tool_budget` rejection ends the session; budget-exhausted structural retries use the Director cadence capped to the plan window; Director calls and plan history are persisted for audit.
- Final optimized contract-driven broad run: 1/1 complete core, 1/1 legal, 1/1 violation, **15 accepted / 0 rejected**, no failed checks. Prompt 4,731–7,161 tokens; 62,848 cached tokens of 87,948 total (71.5%). The 3-run sample before the final projection was 3/3 with 45 accepted / 2 rejected (`tool_arguments` only).
- Context/schema optimization after inspecting real prompts: staged God prompt 14,769→7,748 chars (contract 5,403→641, outcomes 3,778→358, protocol 2,120→297, rule fields 1,091→139); NPC prompt 10,020→8,216 chars (behavior blocks 3,097→1,750, duplicate attribute rules removed, unconditional rules compacted); Story 7,003→6,815. A raw contract `tools` alias bug exposed all 19 God tools; accepting `tools`/`allowed_tools` cut the actual God schema 20,321→10,755 chars. The decomposed probe now uses the real 24-iteration / 40-tool-call / 4-read God envelope. Provider cache was already effective: 79.5% cached tokens for God, 55.1% for NPC decisions and 47.7% for Story.
- G-01 fresh 3-run decomposed sample: 3/3 complete cores, 3/3 legal chains, 3/3 violation chains; 15 accepted proposals in each run. The legal-chain reliability caveat is closed.
- G-05 free growth run: `audit_dynamic_evolution_loop.py --provider remote --rounds 8` passed with `flags: []`. Round 1 God created `Deposit Energy Cell`; rounds 4 and 6 God created `Relay Checksum Fragment` and `Relay Second Checksum Record`; Mira executed each next behavior; Story produced five beats. The run ended with one research-synthesis God task queued for the next round.
- G-07 corrected neutral tension probe: 3 runs × 4 rounds, 3/3 valid, 4 requests, 4 approvals, 0 takes, 0 sanctions, 1 spontaneous delegation revocation; 1/3 runs realized tension. This count includes direct `behavior_ref` responses, not only `request_behavior` calls.
- G-04 pressure probe: `--pressure` with a two-unit reserve, 30-minute relay failure, real third claimant Ilya and an exhausted persona. Remote 3 runs × 4 rounds: 2/3 runs realized tension (2 rejections, 1 revocation), 0 takes, 0 sanctions. The model still preferred the legal request path under scarcity; the pressure path is documented as rejection/revocation, not forced coercion.
- G-08 implemented and verified: relevance-scoped `world_index`, bounded `query_world_index` and `inspect_definition`, persisted stage progress, structural history summaries and omission telemetry. Broad remote: 1/1 complete core, 1/1 legal, 1/1 violation, 15 accepted / 0 rejected, prompt 14,423 to 15,396 chars, no omitted section. A 100-Action/100-behavior render test stays under 16k and omits no required section.
- Contract-driven broad: 1/1 complete core, legal and violation chains; 15 accepted / 0 rejected.
- Decomposed fresh: 1/1 complete core, legal and violation chains; 15 accepted / 6 rejected.
- Full loop: LLM Director + 15-stage God + LLM NPCs; Director completed the plan and opened `baseline: 1`; three Story tasks were queued.
- Full one-cycle loop completed: Director plan `resource`; God 18 calls / 17 iterations; Mira filled twice; Tomas requested access; Story produced two accepted beats; Director plan completed and next plan opened at `baseline: 1`.
- G-03 marked FIXED / VERIFIED for the wake and full-loop Story path.
- New scalability issue G-08 recorded: the 16k-char cap hides catalogs as the world grows.

### 2026-11-18

- Phase 1-3 fresh remote verification completed and recorded in
  `social-authority-loop-phase3-report.md` §9.2.
- Tension probe added at `scripts/verify_social_tension.py`.
- Counting bug for direct intents found and fixed; rerun still pending (G-07).
- Phase 3 legal chain fresh result is 2/3 because of G-01.
- G-01 validator added in `definition_validation.py`; `tests/test_dynamic_social_content.py` now has a regression case. Focused suite: 124 passed.
- G-02 multi-proposal continuation added to `AgentEngine`; `test_god_structural_accepts_multiple_proposals_in_one_turn` passes. Broad remote rerun pending.
- Phase 3 probe now requires the exact authorization operation at the request/approve/reject/revoke/review stage, so a missing operation triggers a retry instead of silently passing.
- G-03 wake added: `violation.observed` uses the configured wake path; `test_pa_13_observed_violation_wakes_the_steward` passes. `test_pa_12_negative_space` now expects exactly that one review task.
- G-06 config optimized: `roles.god` 24 iterations / 4096 tokens / 4 reads, `agent_decision` 8 / 768 / 3, `story` 8 / 2048 / 4, `agent_reflection` 4 / 1024 / 2, `world_builder` 8 / 4096 / 4.
- Agent context optimized: God read tools are hidden after the read budget; a structural turn finishes as soon as its semantic pack is complete; accepted proposals append a compact missing-content progress line; structural interaction history is capped at 12 entries / 6 tool results / 4 message tail.
- Structural contract stages: 15 atomic stages in `worlds/dynamic_social_probe/evolution.yaml`; engine stage driver added with a two-stage regression test.
- Broad staged probe now passes: `complete_core 1/1`, `legal_chain 1/1`, `violation_chain 1/1`, **15 accepted / 0 rejected**, no failed checks. The model followed the atomic stage instructions and finished the pack in one attempt.
- Decomposed fresh run after the fixes: 1/1 complete core, 1/1 legal, 1/1 violation, 15 accepted / 6 rejected. The legal chain no longer has `missing_admin_content`.
- First full Director->God->NPC->Story run: LLM Director chose `resource`, God completed 15 social behaviors in 16 iterations / 17 calls, Mira filled the reserve, Tomas requested access, and the Director completed the plan and opened the next plan (`baseline: 1`). Story was empty because the first wake list did not include `access.requested` or `resource.changed`; those wakes were added and the full loop is rerunning.
- Separate `roles.director` added to the example and the local `llm.json`: remote, 1024 tokens, 4 iterations, 2 reads. `DirectorRuntimeMixin.plan_with_provider` calls it; actual run chose `resource` and created `plan:5:resource` + `god_structural`.
- Focused verification after the engine, validator, wake and config changes: 138 passed, then 14 focused config/director tests passed.
- Minimal evolution loop passed 3/3 rounds with `flags: []`:
  `python scripts/audit_dynamic_evolution_loop.py --provider remote --rounds 3`.
  Round 1: God proposed and assigned `Deposit Carried Energy Cell`; Mira chose it; the public reserve rose 0→1; Story created an emergent beat.
  Round 2-3: Mira completed Signal Archive research; Story created another emergent beat.
  A `research_synthesis` God task remained pending only because the run was cut at three requested rounds.
