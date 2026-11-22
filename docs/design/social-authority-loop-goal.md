# Social Authority Loop — Current Goal and Open Issues

Status: **ACTIVE**
Last updated: 2026-11-22

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
| Capability model (2026-11-19) | One `transfer_resource` family + target bindings. Remote: legal 1/1 request, causal reference, approval and withdrawal; directed take 1/1 witnessed and sanctioned; dynamic God **15 accepted / 0 rejected**, complete core 1/1, legal 1/1, violation 1/1; growth 240 minutes **passed, 0 rejected sessions**, 12 plans opened / 11 completed, 17 structural units, 4 new behaviors executed, 8 Story beats, all five cells grew. Every God-created stock Action used `verb: transfer_resource` with `resource_id` and `context.resource_id`; a remote withdrawal/take is now rejected unless a Travel node reaches the bound stock. Full suite **1466 passed**. |
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

| G-11 | A withdrawal/take could execute without the actor at the stock, and a generated Behavior could put the stock-location binding on the Action | Previous growth `log_approved_rationed_draw` bound `Public Energy Reserve` with no Travel; the residual replay completed remotely before the fix. The first post-fix growth run accepted `withdraw_reserve_energy` with `parameter_bindings` on the Action and only a decision-view-inferred Travel target; a later run still produced a local-only `meter_storm_cellar_charge_into_ledger` Behavior | `resource_location` binding, gates in admission/start/parallel/completion/view, reject Action-level `parameter_bindings`/`authorization_operation`, require the binding when a node uses `context.resource_location_id`, and require Travel or `resource.here` for a God-proposed withdrawal/take | Remote growth after the fix: two no-Travel `Log Power Draw` attempts rejected with `resource_location`; accepted `log_an_approved_power_draw` declares the Behavior binding and Travel, executes with intent `resource_location_id=relay_lab`, stock 1→0; a later 240-minute run rejects the no-Travel `Draw Reserve Power` Behavior and all accepted stock behaviors travel to Relay Lab; free loop flags []; focused tests | FIXED / VERIFIED |

### 5.1 Status summary

| Status | Count | IDs |
| --- | --- | --- |
| Fixed / verified | 11 | G-01, G-02, G-03, G-04, G-05, G-06, G-07, G-08, G-09, G-10, G-11 |
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
- Stock location binding fixed (2026-11-19): `resource_withdrawal` and `resource_take` now share the located-stock contract at admission, Action start, parallel start, completion and decision view. `world/bindings.py` resolves nested `context.*` chains (a Travel node's `context.resource_location_id` resolves through the node binding), and `parameter_bindings: {resource_location_id: resource_location}` derives the chosen stock's location from the same resolved target used by authorization. `dynamic_social_probe`'s generic `withdraw_resource`/`take_resource` now declare that binding and travel to `context.resource_location_id`; `docs/world-authoring.md` documents the pattern. Focused regressions cover remote registration rejection, remote admission rejection for withdrawal and take, parameterized travel-and-withdraw, the decision-view diagnostic, and a mid-action move that prevents completion.
- First post-fix growth audit (2026-11-19, remote, location gate only): passed with `flags: []`, 9 plans / 8 completed, 24 structural units, 3 rejected sessions, 9 new behaviors executed, 5 Story beats. God first tried `Log Power Draw` twice without Travel and both were rejected with `resource_location`; it then created `log_an_approved_power_draw` with the binding and Travel. The run also accepted `withdraw_reserve_energy` with `parameter_bindings` on the **Action** and a Travel node that only the decision view inferred. That exposed a second content-shape defect: the binding must live on the Behavior, and the view must not mask a missing binding. Validation now rejects `parameter_bindings`/`authorization_operation` on Actions and requires the `resource_location` declaration when any node uses `context.resource_location_id`.
- Growth rerun after the content-shape validation (2026-11-19, remote, 240 minutes): passed with `flags: []`, 8 Director calls, 8 plans opened / 7 completed, 22 structural units, 3 rejected sessions (model iteration/budget, not accepted remote withdrawals), 6 new behaviors executed and 5 Story beats. `log_power_draw` is a generic `resource_withdrawal` with `context.resource_id`, no Behavior fields; `log_an_approved_power_draw` declares `parameter_bindings.resource_location_id: resource_location` and `Travel To context.resource_location_id`; its accepted intents record `resource_location_id=relay_lab`, and the first execution completed a withdrawal (stock 1→0) while the second failed only because the stock was empty. The decision view rendered `Withdraw from Public Energy Reserve at Relay Lab` for the bound behavior.
- Final growth rerun after the Travel content rule (2026-11-19, remote, 240 minutes): passed with `flags: []`, 7 Director calls, 7 plans opened / 6 completed, 31 structural units, 6 rejected sessions (model iteration/budget), 7 new behaviors executed and 3 Story beats. God's first `Draw Reserve Power` proposal was rejected because its withdrawal Behavior had no Travel; the accepted stock behaviors `draw_from_the_storm_cell_bank`, `draw_measured_charge_from_the_storm_cell_bank` and `spend_storm_cell_charge_on_relay` each include `Travel To Relay Lab` before the withdrawal. The later stock draws failed only because the `Storm Cell Bank` stayed at zero, not because of location. Every God-created stock Action uses `verb: transfer_resource` with `resource_id` and `context.resource_id`, and none carries Behavior-only fields.
- Residual audit replay: the previous growth instance's `log_approved_rationed_draw` (literal `Public Energy Reserve`, no Travel) is now unavailable in the decision view when Mira is moved away, and a direct submission returns `failed_check: resource_location` without changing stock. At `relay_lab` the same Behavior completes and reduces stock 5→4, so the gate is location-sensitive rather than a blanket ban.
- Free LLM rerun after the location fix (`--rounds 5`, remote): `audit_dynamic_evolution_loop.py` passed with `flags: []`. God created `Deposit Energy Cell` (generic `resource_transfer`, `context.resource_id`, no Behavior-only fields) and `Deliver Energy Cell To Reserve` with `Travel To Relay Lab` before the deposit. In round 4 God created the `Relay Checksum Fragment` research source with two readable `{id, fact}` discoveries, so the research-fact validation is exercised by a fresh remote source; Story produced four accepted beats from the deposit, archive finding, fragment recovery and storm-era claim. The only pending task is the same `research_synthesis` closure at the round boundary.
- Location-fix focused suites: `tests/test_action_binding.py` (20), `test_capability_bindings.py` (8), `test_catalog_scaling.py`, `test_dynamic_social_content.py`, `test_social_authority_loop.py`, `test_contested_resource_actions.py` all pass. Full release gate after the location fix: **1466 passed** across four isolated workers; compileall and `git diff --check` are clean.
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

### 2026-11-21 潮汐公社首小时闭环

- 首场风暴改为明确的两次修复路径：`tide_gate_integrity` 初始 28%，每次 `repair_gate` +28%，闸室公共工具架 `commons_tool_rack` 开局两只备用工具包；玩家与玛拉各修一次可到 84%–89%。`gate_repairs` 记录修复次数，上手提示在第一次修复后切换为“还差一次修复”，并明确写出玛拉通常会补第二道缺口。
- 公共工具架使用通用世界资源，不新增平台特例：`resource_withdrawal` 支持 `output_item` 与 `only_if_missing`，`resource_transfer` 支持参数化 item/quantity；`resource_property_projections` 把库存投影为可见的“公共工具包”。玛拉和伊沃能在远程运行中完成 `inspect_gate` → `repair_gate` → `stock_repair_kit` 的真实流转。
- `repair_tide_gate` 与 `repair_gate` 现在都要求 `maintenance_evidence >= 1`，“先测量再修复”从提示变成机制。
- `public_confidence` 进入规则：低于 20% 禁止“优先抢修”，低于 35% 禁止校准信号，低于 25% 触发 `confidence.low`；高于 65% 触发 `confidence.solidarity`，居民每 12 小时自发加固潮门 1%。
- 风暴压力改为每小时 +0.004、警戒线 0.45，`storm.warning` 在首场风暴前可触发；`storm_cycle` 记录轮次，第一轮后出现“撑过接下来的风暴季”目标。
- 新增可修复集雨器（每 6 小时 +6 淡水）、码头口粮换芦苇绳、潮门支撑件配方与 `brace_gate`、季末结局投影；`tune_signal` 门槛从 0.56 提高到 0.80。
- 表现层：出身专属 onboarding 覆盖、行为冷却显示为剩余时间、按属性合并 per-minute 成本行。
- 条件引擎接受 `world["property"]` 与 `world.property` 两种写法；God 规则不再出现“叙事生效、机制不生效”。
- 聚焦回归：`tests/test_tidebound_commons.py` 30 项、`tests/test_condition_engine.py` 3 项全部通过。全量门禁 **1479 passed**（4 个隔离 worker）；`compileall`、`git diff --check` 干净；平台基准 p95 **197.447 ms**（1000 agents / 5 runs）。
- 远程 360 分钟最终代码审计（`deepseek-v4.1-flash`）：玛拉测量并修复，`gate.repair_needed` 规则触发，伊沃制作并放回工具包，市场交换 2 次、Story 2 条，无空轮次。第一次 720 分钟审计还记录了 5 条 Story 和 1 个 God 定义。
- 确定性 3 天 smoke：潮门完整性 0.96、公共信心 0.77、风暴轮次 2、公共工具架 3（上限生效），无退化标记。
- 确定性首场风暴回归：沼泽采集者玩家与玛拉各修一次，潮门 0.61 → 0.89，`storm.held`；同一角色仍受 720 分钟冷却限制。
- 季末结局不再吞掉后续章节：`signal.route_open` 之后由 Story 生成的 `story.beat.activated` 会替换结局卡；没有后续 Story 时结局卡继续作为兜底。
- 玩家界面完成一轮信息设计：中栏新增可配置的公共防线速览（潮门/风暴/信心/工具包），右栏公共账目按防线/储备/进展分组，章节卡显示 `goal_hint` 并有季末结局强调，行动筛选增加“交易”，新事件类型和设施状态改为可读文案。
- 真实浏览器走查（1366×900 与 390×844）：公共防线速览、分组账目、章节 `goal_hint`、季末结局强调和移动端行动栏均正常；工具架行动曾把 `item_quantity_parameter` 原样显示给玩家，现在投影为“预留 1 修理工具包（开始时扣除）”。桌面端开始行动后自动把任务进度滚入视野；移动端防线速览改为四列紧凑布局，长提示保留在 `title` 中。全量门禁 **1479 passed**。
- 公共设施卡：世界包可在实体或共享资源上声明 `facility.order` 与 `facility.properties`；`/player/view` 新增通用 `facilities` 投影，客户端在左栏显示潮门、集雨器、信号透镜和公共工具架的状态、位置和关联数值，移动端改为横向滚动卡片。没有 `facility` 声明的世界不显示该面板。
- 季末收束层：`story.endings[].stats` 声明的数值进入半屏结局面板；面板首次出现时聚焦主按钮，关闭后按玩家和结局 ID 记住，章节卡仍保留结局作为兜底。结局数值使用 `world_property_labels` 的百分比、布尔标签和上限格式化。
- 移动端顺序：中栏和左栏在窄屏使用 `display: contents`，场景、防线、设施、提示、角色、目标、行动和地图按阅读顺序重排；角色卡不再沉到完整行动列表之后。
- 场景美术与音效：场景远景升级为按 `tags` 主题组合的分层 SVG 插画（码头、闸门、工坊、观测塔、沼泽、住屋），带雨、雾、星光、炉火和信号光束动画；HUD 新增环境音开关，使用 Web Audio 合成风声、浪声和低频持续音，默认关闭并记住开关状态。外部图片生成服务本次凭据失败，因此没有引入位图资产或新的世界专用分支。
- 最终真实浏览器复核（新建实例、1366×900 与 390×844）：设施卡显示潮门完整性/修复次数、集雨器淡水储备、信号覆盖和工具架库存；移动端角色卡位于目标与行动之前；结局遮罩显示四项赛季数值并在关闭后写入本地记忆；环境音按钮可切换；浏览器 console 无报错。全量门禁 **1479 passed**，`compileall`、`node --check`、`git diff --check` 干净，平台基准 p95 **204.786 ms**（1000 agents / 5 runs）。

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
- Web/API v1 重构已实现：`src/sim2/api/v1/` 提供声明式路由、Pydantic 契约、Auth/幂等/错误中间件、统一 SSE 和可恢复手动任务；`/api/v1` 硬切完成，旧路径删除。游戏端与运营台已同步切换，`api/http.py` 只保留传输和服务器状态。
- 手动推进改为 202 + 后台任务：`POST /api/v1/player/{id}/wait` 立即返回 `task_id`，`ManualTaskRunner` 每分钟一块推进并发布 `task.progress` / `task.completed`；控制锁不再覆盖整段等待。浏览器走查中 24 分钟移动任务完成后视图 1 秒内刷新，精力从 88 降到 85。
- 前端已拆为原生 ES modules：游戏端 `js/core|api|audio|art|render_*|actions|app.js`，运营台 `js/admin_core|api|render|editor|actions|app.js`；入口 `app.js` / `admin.js` 只做模块加载，1366×900 与 390×844 浏览器走查无 console 错误。
- 属性系统修正：`stamina_recovery` 只在 `action.none` 时生效并降为 0.2/分钟，避免 0.45/分钟恢复盖过全部 0.02–0.20/分钟行动成本；新增 `rest_at_hearth` 行为（安全地点休息 60 分钟恢复 30 精力），`belonging` / `purpose` 衰减提升到每 10/15 分钟 -0.05，需求条显示一位小数。聚焦回归 `test_stamina_and_social_needs_change_instead_of_staying_pinned` 通过。
- 最终门禁：全量 **1380 passed**（4 个隔离 worker），`compileall`、`node --check`、`git diff --check` 干净；平台基准 p95 **186.416 ms**（1000 agents / 5 runs）。浏览器复核 1366×900 与 390×844：公共设施卡、移动端顺序（场景→防线→设施→角色→目标→行动）、SSE 在线、行动任务完成刷新均正常，空闲 20 秒内没有重复 `/player/view` 请求，console 无报错。
- Web 通用性审计：网页不含任何具体世界名、角色名、设施名或事件类型前缀。事件分类/标题/世界脉搏优先级、行动结果文案、失败原因文案分别由 `setting.ui.event_kinds`、`event_titles`、`pulse_prefixes`、`result_labels`、`reason_labels` 声明；属性名与成本正负来自 `player.attributes[].label/cost_mode`。`starter_village` 与 `tidebound_commons` 两套世界均通过浏览器走查，未出现对方的词汇。`test_web_assets_world_neutral.py` 已扩展到设施名、角色名和内部事件码。
- 首屏钩子：`/api/v1/world` 现在返回实时 `world_properties`、`world_property_labels` 与 `cast`；入口页用 `setting.ui.tagline`、`ui.crisis_metrics`、`ui.cast_order` 渲染一句钩子、关键指标和在场角色。游戏场景左上角增加“章节 + 最紧急指标 + 倒计时”态势条，`crisisMood()` 按指标阈值切换场景/封面色调和雨效。没有配置的世界自动隐藏这些区域；移动端有玩家时先展示存档入口，否则先展示世界钩子或创建说明。
- Agent 可见性：`/observer/timeline` 增加结构化 `activity`（行为开始/完成/失败、God 规则、Story 章节），右栏“人物”tab 渲染“居民动向”（角色、行为、公开理由、时间）；场景人物 chip 与角色卡显示 `active_behavior_label`、进度和当前打算；世界脉搏在没有更大事件时退回最近一条居民行动。背景 `bt:` 人口不进入居民动向，避免“后台控制器”式噪音。
- 手动时间：世界状态面板增加“等待 1 小时 / 4 小时”，`ManualTaskRunner` 区分“跟随当前长行动”与“空闲等待完整时长”，因此空闲时也能推进 NPC 决策；等待进度显示在按钮旁，完成后提示实际推进分钟与居民决策次数。
- 公共淡水：`fresh_water_reserve` 是共享 `fresh_water` 的只读投影。新增 `draw_water` 行为在安全地点从公共储备领取 1 份随身淡水（10 分钟，储备 -1，随身少于 2 份时出现），沼泽采集仍是补充储备的主要方式；配给、抢修、风暴和集雨器都会改变储备，低于 8 触发 `resource.safety_low`。标签改为“公共淡水”，并带说明 tooltip。
- 潮门改为两段式公共工程：`tide_gate_integrity`（结构，目标 80%）与 `gate_supports`（临时支撑，目标 40%）必须同时达标；`brace_gate` 主要加支撑 15%，`repair_priority_today` 加支撑 10%，风暴后支撑按 30%/50%/60% 衰减。`storm_eta_hours <= 6` 时开启 `storm_final_watch`，玩家在“通宵抢修 / 撤走物资 / 让居民避风”中三选一；`storm_prepared` 让失败分支的物资损失减半。设计、数值与验证见 `docs/design/tidebound-gate-loop.md`。

### 2026-11-22

- Riverbend 采集目录新增显式 `resource_id`；包校验与模板校验会拒绝未知资源、地点/资源地点不一致、`resource_withdrawal` 指向别的资源、缺少产出物品或未在 `item_labels` 声明的产出。
- 采集目录的 `requires` 现在校验条件路径：未知命名空间、未声明状态/需求/世界属性、未声明物品不再静默变成 false。
- 玩家视图按绑定资源的当前库存过滤采集条目；资源不足一次采集量时不再出现在下拉框。聚焦回归 `tests/test_riverbend_market.py` 17 项、`tests/test_world_authoring.py` 12 项通过；相关世界/打包校验共 80 项通过。
- 玩家初始化改为“角色名 + 出身”：新角色的内部 ID 由 Platform 生成（`player_<12 hex>`），网页入口不再要求填写玩家 ID；已有角色仍按 ID 恢复。API 的 `player_id` 变为可选，缺名且缺 ID 时返回 `failed_check: name`。回归：`test_player_entry_templates.py` 18 项、`test_web_client.py`/`test_http_post_routes.py`/`test_play_without_llm.py` 合计 118 项通过。
- 首屏优化（2026-11-22）：入口“进入世界”改为固定底部主按钮；游戏内把行动区移到目标/地图之前，首屏可见“下一步”；推荐行动优先跟随 `onboarding.action_ref`/目标 ready refs，其次生产性行动，需求不低时不再默认“使用随身物品”。聚焦回归 128 项通过。
- 实例管理（2026-11-22）：Web 存档卡片可删除非当前实例；后端只接受 `instance_id`、拒绝当前实例和越界路径，删除整个实例目录（数据库与快照）；实例描述新增 `world_title` 供存档列表展示。
- 首小时体验 P1-P3（2026-11-22）：`onboarding_checklist`/`onboarding_progress` 支持 3 步勾选与 `complete_when` 契约；`setting.ui.celebrations` 驱动金币/盖章/礼花/章节卡；`relationships` 暴露信任条；`recap` 提供最近一天回访摘要；账本汇总设施/配方/目标；`countdown` 场景主视觉和 Web Share 章节分享。所有内容标签和事件映射来自世界配置，平台不写死世界名。
