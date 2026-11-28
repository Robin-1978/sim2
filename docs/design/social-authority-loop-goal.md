# Social Authority Loop — Current Goal and Open Issues

Status: **ACTIVE**
Last updated: 2026-11-28

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
- 对话去重与玩家主权（2026-11-22）：`interaction_claim` 原子领取零分钟交互事件，后台回复线程与手动推进并发时同一句只回答一次；`SimulationRunner` 跳过 `role=player` / `controller=manual` 的收件人，自动运行不再替玩家角色回复。复现实例 `riverbend_market_p6wh` 中每条玩家消息出现两条 NPC 回复及玩家自动回复；聚焦回归覆盖双 runner 竞争与玩家收件人跳过。
- 背景 NPC 移动空转（2026-11-22）：`riverbend_market_p6wh` 的阿石每分钟执行 `travel → north_mine` 并原地 `arrived`，因为 `travel_to` 未声明目的地的自检、移动候选包含当前地点、且背景控制器在没有 `bt_priority` 时默认选目录第一项。`parameter_options.location_id` 现在排除当前地点并提供显式 `value`；无紧迫需求时控制器不再默认纯移动；河湾镇三名背景 NPC 补上 `bt_priority`（采集优先，移动最后）。聚焦回归覆盖候选过滤、优先级选择和无优先级旧存档回退。
- 物品闭环与 LLM 可见性（2026-11-22）：香料新增“香炖菜”配方、食用入口和阿芹收购报价，工具购买报价拒绝重复购买；`rules.item_uses` 在角色物品栏显示用途，闭包回归覆盖每件非金币物品都有来源和去向。首页新增“镇上的打算”卡片，展示 LLM 居民的 `decision.reason`；贸易/采集/进食事件改为中文短句，NPC 失败和内部事件不再进入玩家动态。远程对话探针（`remote`）验证 NPC 会表达判断并追问，而不是只回答“不知道”。
- 旧存档配置升级（2026-11-22）：新增 `upgrade_instance()` 与 `scripts/upgrade_instance.py`，只 upsert `setting`、`rules` 和 recipe/action/BT 定义，保留实体、角色、库存、世界属性、记忆、关系、剧情状态和运行时 `world_rules`。在 `riverbend_market_p6wh` 副本上验证：世界分钟/倒计时/玩家库存不变，配方 8→9，`item_uses.spice` 生效，移动候选排除当前地点。

### 2026-11-22 长夜当铺：从写死内容到活世界

- 新增 `worlds/night_pawnshop`：7 位客人、21 件种子物品、3 个阵营、21 夜第一季、6 个章节结局。21 个案子只是**种子**，不是游戏全部。
- 活世界循环：remote Director 每 2 天选择 growth cell；remote God 按 `dyn_case_1..5` 槽位创建新物品实体、新客人、收当/归还/卖出行为；remote Story 监听 `case.resolved` 并提交下一拍。玩家收当/卖出后，`dyn_case_N` 和阵营声望改变，下一次 Director 计划读取这些事实。
- 章节继续：`begin_next_season` 保留当铺、库存、升级、关系、线索和已生成案子，只重置夜晚、债务和行动；第 21 夜审计不再是世界终点。
- DSL 升级（详见 `docs/design/living-world-dsl.md`）：
  - 条件支持 `world[context.case_slot]`、`actor.inventory[context.item_id]`，Action/Behavior 标量参数进入 `context.*`；
  - 新增 `world_property_set`，支持 `context.value`；
  - 新增 `deferred: true`，允许登记未来才满足条件的行为，条件满足后进入玩家行动列表；
  - 契约新增 `max_tool_calls` 与 `required_content.behaviors_using_action`；
  - God 创建的 `entity_type=item` 实体把 name/uses 投影到物品栏；
  - `public_event` 与目录事件改为 `publish(wake="configured")`，`case.pawned_dynamic` 能唤醒后续 God/Story；
  - `propose_entity` 接受 `location`/`behaviors`/list traits/beliefs/inventory 等 LLM 常见写法；
  - 可见物品名在 Behavior 注册和 Action 执行时规范化到实体 id，支持唯一模糊匹配。
- 远程证据：`python scripts/verify_night_pawnshop_growth.py --provider remote` 完成 Director 选 `social` → God 创建物品/客人/3 个行为 → 玩家收当（金币 -20、`dyn_case_1=1`）→ 玩家卖出（金币 +40、`dyn_case_1=3`、阵营声望上升）→ Story `propose_story_beat` accepted。探针没有注入案子或注入行为；God/Story 均由真实 remote provider 调用。
- 聚焦测试：`test_night_pawnshop.py` 6 项、`test_dsl_living_bindings.py` 3 项；`scripts/test_parallel.py --changed` 通过（除已修复的 schema/liveness 两项外）。

### 2026-11-23 风脊号：从“又一个镇子”到天空群岛生存沙盒

- 新增 `worlds/windward_skyfall`：10 个地点、8 名 Agent、19 种资源、6 个配方、61 个 Action、85 个 Behavior、4 个出身、5 个结局。核心循环是探索 → 采集 → 制作 → 建造 → 风暴 → 离天/锚定/成为风暴，不再依赖单一截止日、债务或店铺经济。
- 新机制：风暴压力由稀有打捞推高，35/65/90 三阶段会损伤船体、关闭远航；昼夜与野外暴露；两级船体加固；工坊/厨房/花园/药棚/信标五条建造线；`sky` 场景美术主题；`explicit_travel` 让带门槛的航线只显示 authored 版本。
- 平台修复：`nearby_entities` 优先 Agent 和资源，营地不再显示“附近没有人”；设施支持 `dim`/`working` 状态；`worlds/__init__.py` 兼容 `MultiplexedPath`，namespace 世界包可被 CLI 发现；`night_pawnshop` 与新世界补了 `__init__.py` 和 package-data；world tick 条件上下文加入 `day_phase`，夜间暴露规则现在真的会触发（此前 `context.day_phase` 只存在于 Behavior 条件）。
- 生存平衡：两级船体加固按等级降低风暴即时伤害和持续伤害，且可以满血建造；风衣改为纤维 + 陶土，避免“需要灯油 → 需要旧灯塔 → 风暴锁航 → 需要风衣”的软锁；灯油不再是提灯前置，提灯用纤维做灯芯。
- 活世界契约：Director 只选 `verb`；God 契约要求 `required_definition_type: behavior`、`assign_to: [player]`、`max_proposals: 1`、`behaviors_using_action: [dynamic_salvage]`。远程 120 分钟运行：1 个 Director plan、1 个 God session、1 个 `dyn_salvage_1` Behavior、0 次拒绝；玩家执行后木材 7→9、风暴压力 0→4。远程 180 分钟运行：玩家自动搭起工坊，Story 激活 `first_workshop` beat。
- DSL 修复：`target.location_id` 成为已知条件路径；`dynamic_salvage` 直接 `inventory_add` 现有材料，不依赖空资源节点或中文 display name；`deferred` + 地点/冷却检查保证 God 生成的打捞点只在目标岛生效。
- 聚焦测试：`tests/test_windward_skyfall.py` 8 项通过；`scripts/test_parallel.py --changed` 作为迭代门禁。

### 2026-11-26 风脊号实机修复：夜里采集与船员补给

实机实例 `windward_skyfall_3t4j`（第 4 天 21:55，风蚀林）暴露两个问题：

- **采集消失**：采集/探索 Behavior 的 `context.day_phase != night` 只在 Behavior 入口判定；夜里玩家看到的是“没有采集”，而不是“天黑了”。现在带提灯时允许夜间采集（仍受暴露伤害），并新增 `wait_for_dawn`（等天亮）与 `forage_supplies`（就地补给）。`ActionAvailability` 条件上下文补上 `day_phase`，`stargaze`/`wait_for_dawn` 这类 Action 前置条件不再永远失败。
- **船员不动/不补给**：船员没有 `take_ration`/`fill_water`，阵营角色也没有食物来源；口粮耗尽后他们只会反复 `rest_at_camp`/`rest_rough`，hydration 降到 17、satiety 降到 38 仍不进食。现在船员拥有取口粮/装淡水行为，阵营角色拥有 `forage_supplies`；`forage_supplies` 也能作为玩家的夜间/野外生存保底。`upgrade_instance` 新增 Agent 与 Player 的 `initial_bt_ids` 合并：旧存档升级后也能获得新行为，而不是只更新定义表。
- 当前实例已执行升级：`action_def 61`、`bt_def 85`、`agents_updated 8`、`players_updated 1`；玩家在风蚀林夜间拿提灯时已能看到“收集木材/伐木/收集纤维/采浆果/等天亮/就地补给”。复制存档跑 180 分钟后，船员依次 `fill_water → drink_water → take_ration → eat_ration`，阵营角色 `forage_supplies → eat_ration → drink_water`，需求开始恢复。
- 聚焦测试：`tests/test_windward_skyfall.py` 14 项、liveness + release + upgrade 共 53 项通过。

### 2026-11-26 风脊号 0.2 与框架复查

- 航线采用世界配置的 `rules.travel.player_route_mode`。Platform 拒绝旧存档里的通用 Travel，玩家视图、地图和文本入口使用已通过门槛的航线。聚焦回归 `test_authored_routes_are_shared_by_player_clients_and_platform` 通过。
- 风暴按压力升降，首次撞击保留独立历史标记。世界刻的事件通过配置唤醒 Story，探索只发布一次完成事实。聚焦回归覆盖 65→45 恢复航线、下一周期停止损伤、重返阈值无重复撞击，以及风暴和探索的 Story 任务。
- 世界规则使用同一日历。午夜同步天数，等天亮使用 Action 估时与执行共同支持的 `until_day_phase`。结局行动在效果提交前再次检验结局与船体条件。聚焦回归覆盖 04:59→05:00 和离天撞上第 30 天截止时只留一条结局。
- Director 与 God 使用同一打捞行为计数。两轮确定性运行使基线 0→1→2，首轮生成的行为实际被玩家执行。生成契约拒绝直接意图绕过权限、超量、重复绑定及同一批次第二条提案。异地打捞的聚焦回归覆盖投影与 Platform 提交。
- 阿岚、砚秋接入模型决策，缺少提供方时走 BT。旧存档升级保留进度，更新角色控制和演化配置，退休旧周期任务，旧计数计划在下一次世界刻关闭。聚焦回归覆盖两类决策和升级路径。
- 本轮验证使用确定性提供方。当前进程缺少 `remote` 凭据，历史远程结果仍保留在上文。框架复查发现包校验与模板校验仍有差异，具体证据记录在 [architecture-convergence-plan.md](architecture-convergence-plan.md)。
- 机械师新档走完采集、工具、滑翔翼、风暴、修船、信标三核心和离天。中途复制临时存档并调整后续出航路线，始终通过正常行动推进。第 10 天离天，船体约 53%。

### 风脊号 0.3、潮汐公社 2.2 与 Agent 复查

| 状态 | 变化 | 聚焦证据 |
| --- | --- | --- |
| 已验证 | 风脊号停滞事件复用 `god_structural` 契约。新打捞分配给阿筝，无需玩家接手。过期计划或已满足产量的重复唤醒不会再请求同一批内容。 | `test_world_liveness_contract.py` 验证停滞证据到达 God，也覆盖无活动计划时 Runner 消费命名任务。`test_director_reenters_a_second_salvage_plan_through_the_runner` 验证两轮打捞真实完成。 |
| 已验证 | Director 需要关联结果才能关计划，记录 `outcome_event_id`。LLM 策略读取已提交的刻末结果。 | `test_world_director.py` 覆盖无人到达的新地点保持活跃、已撤销的库存预留没有完成证据、采集实际完结后关计划。`test_automatic_director.py` 覆盖规则被角色执行并复核后才调用下一轮 Director。 |
| 已验证 | 两套世界使用已有的世界规则、库存、行动与事件唤醒实现公共储备争用。 | `test_autonomous_supply_decisions.py` 两套世界各跑 1,690 分钟，共四个分配窗口。每套都先后结算防务和民生，实际扣尽当轮库存，分配唤醒 Story。两名角色提交互斥分配时只结算一次。 |
| 已验证 | 居民配给保有独立库存。潮汐公社先把淡水放入配给桶，公共水仓有余位时再入库。 | `test_relief_water_waits_when_the_public_reserve_is_full` 验证水仓满时保有四份，水仓腾出一份后正好补一份。 |
| 已验证 | 两套世界允许后台角色冷却时继续世界刻和 BT，待回复的零分钟对话仍约束时钟。升级补齐新增资源实体和状态。 | `test_autonomous_supply_decisions.py` 使用冷却中的 Story 提供方跑完两轮，另有同分钟对话回归。`test_upgrade_teaches_existing_crew_and_player_the_new_survival_behaviors` 验证旧存档补齐库存并保留船体数值。 |
| 已验证 | 反思能按编号引用旧的可见结果。质量评分使用 God 会话的实际迭代预算。 | `test_reflection_evidence.py` 覆盖挤出最近窗口的已知事件和其他角色的不可见事件。`test_structural_god_turn_is_graded_against_its_runtime_budget` 覆盖超六步但仍在契约预算内的会话。 |
| 待远程复验 | 两世界的模型选择、Story 衔接和跨季节因果演化。 | `python scripts/verify_agent_models.py --skip-local --provider remote` 返回 `blocked`，当前进程缺少配置所需的凭据。两轮确定性分配以配置的首选项与下一轮民生约束为据。它们没有测量模型自发策略。 |

上下文、决策、反思、Story、God 和 Director 的评估口径见 [Agent Quality Audit](agent-quality-audit.md)。

最终聚焦回归共 384 项通过，覆盖两个世界包、停滞恢复、Director、现有社会授权与 Agent 上下文。十个世界包通过 `validate_config`，`compileall` 与 `git diff --check` 通过。

### Agent 请求预算与结果评估

| 状态 | 变化 | 聚焦证据 |
| --- | --- | --- |
| 已验证 | 按提供方窗口预算整次模型请求，计入系统消息、视图、会话历史、工具和响应 schema、输出预留及估算余量。保留最新的完整工具交换和当前结构阶段。预算不足时产生 `context_budget`，没有 HTTP 请求。 | `test_request_budget.py` 覆盖多轮原生工具消息、并行工具组、快照收缩、关键证据无法容纳和输出预留。`test_dynamic_social_content.py` 复验阶段恢复，并调整旧 Director 测试以真实入库结果验收已注册资源。 |
| 已验证 | `evaluate_behavior_outcome` 用执行 ID 关联完成事件和实际库存变化，同时校验期限。两套世界的评估分别覆盖首轮可竞争和必须履行民生配给的场景。 | `test_agent_outcome_quality.py` 验证四个确定性模型用例成功，已受理但未结算、无关执行的完成事件、超时和取消均不能通过。 |
| 待远程复验 | 重复试验的选择率、结果通过率与估算误差。 | `python scripts/verify_agent_outcomes.py --provider remote --runs 3` 返回 `blocked`。当前进程缺少远程凭据。 |

扩大回归 450 项通过，最终预算与结果评估相关集合复跑 228 项通过。`compileall`、示例配置 JSON 校验与 `git diff --check` 通过。

### 交互式 Shell 远程复验与请求缓存

- 交互式 Bash 加载 `~/.bashrc` 后，配置的 `remote` 凭据可用。远程 God 冒烟测试通过，三轮返回有依据的 `wait`，提供方报告命中 7,040 / 23,111 输入 token。
- 首次配给探针的 12 次提交走了 `request_behavior` 工具。`AgentTurn` 缺少结算所需的执行 ID。已补齐运行时侧的内部结果关联，模型仍只收到可见工具结果。原生工具路径聚焦回归验证能按 ID 找到实际分配结果。
- 预算允许且已发送的世界快照及工具集合仍有效时，ReAct 继续使用原请求前缀，并在末尾附当前任务和反馈。`test_request_budget.py` 验证前缀相同、新反馈存在、工具批次完整及预算上限。命中率按提供方返回了缓存用量的请求计算。
- `remote` 只读双轮对照各跑一次。第二轮重新拼接快照时命中 3,840 / 4,264 token，复用请求前缀时命中 3,968 / 4,319。未命中输入从 424 降到 351 token。配给探针 12 次试验共命中 83,968 / 94,149 输入 token，约 89.2%。两组数据均为该探针的提供方实测。
- 修复执行 ID 后，真实模型配给探针的首个决策仍是 0/12。阿岚 6 次选营地工作，玛拉 6 次选潮门检查。两套世界的配给选项符合试验条件，首个行动未触发当批分配。完整 250 分钟窗口内的真实模型跟进仍未验证。
- 聚焦运行时回归 324 项通过，缓存统计和结果评估补充回归 20 项通过。`compileall` 与 `git diff --check` 通过。

### P0/P1 执行边界收敛

| 状态 | 修正 | 聚焦证据 |
| --- | --- | --- |
| 已验证 | 演化任务的领取和恢复共享实例锁。有效领取存续期间，另一执行者无法重领、重排或结束该任务。退出后可恢复。 | `test_runtime_boundaries.py` 复现双执行者和恢复。原进程重启用例继续通过。 |
| 已验证 | 行为结束效果走 Action 的事务结算边界。效果失败会回滚本批效果和事件，记录 `behavior.failed`。 | 老存档定义含失效资源的回归保留贸易信心原值，且不会产生部分成功事件。 |
| 已验证 | 常规 NPC 决策从已提交且面向自己的事件读取争议方、剩余时间与逾期代价。决策结束或过期后简报消失。 | 两套世界的提示回归通过。`remote` 试验在每种条件跑一次，首轮完成 3/4。继续到结算或截止时完成 4/4，风脊号还债分支在第二次决策的第 800 分钟完成。 |
| 已验证 | 世界包入口通过统一模板验证，效果类型由 Definition Compiler 提供。导出保留世界 UI、初始事实和包元数据。 | 十个世界通过模板校验与导出重读等价检查。风脊号和潮汐公社分别启动原包与导出包，比较初始世界状态和角色行动。 |
| 已验证 | 新世界可省略演化配置，或在 `evolution.roles` 逐项启用 God、Story、Director。Director 依赖 God。 | 省略配置的风脊号保持居民行动和时钟，旧的关闭角色任务无人领取，Story 可单独启用。 |

扩大聚焦回归 231 项通过。浏览器检查风脊号和雾溪村在 1366×900 和 390×844 的玩家入口和核心面板，风脊号手机首屏的行动排序列入 [Web 客户端调整](web-client.md#跨题材的首屏)。

### 风脊号 0.4 与首屏

- 首航抉择通过行为、分钟规则和公共事件结算。聚焦回归验证救人和吊铜互斥，救人加声望，吊铜消耗备用铜板来修船，迟到不产生奖励。还验证同样支援一次断桥后，两条开局路线对锚定声望门槛的影响。旧档升级保留进度，错过首日清晨的存档不会再显示该开场。
- 世界配置可把准备就绪的限时目标放到游戏首屏。Chromium 走查 1366×900 与 390×844 两种尺寸，两张首航选择卡分别从页面 Y=473、Y=420 开始。桌面完成救人，手机完成吊铜，结算文字都在当前视野内。另验收了空大厅的选集封面和雾溪村的程序化封面及普通行动。
- 封面和甲板插画由世界包声明。建档和升级时导入 SQLite，按内容去重，玩家投影提供可缓存的图片 URL。回归验证原包移走后快照仍能返回两张图，替换图片时 URL 和配置摘要变化，回合进度保留。十个世界通过包导出与重读检查，安装包包含两张插画。
- Web、世界、运行时与架构的聚焦回归共 76 项通过。最终两张插画的存档、包导出与 Web 入口另复验 18 项。此前全量回归发现一项原生工具消息顺序的旧断言和一项 God 工具描述大小超限。修正后相关回归 9 项通过，加载 `~/.bashrc` 的远程 God 探针通过质量检查。

### DSL 条件、定义快照与缓存

- 世界包和动态 Action 提案共用条件语法检查。聚焦测试覆盖格式损坏的结果条件、引号内的路径字样、空值比较，以及单步和并行 Action 使用实际结算分钟。Action 的预览、启动和结算使用共享字段投影。
- 行为实例保存 Action、配方和 BT 结束定义。升级前会为仍在执行的旧实例补齐快照。回归验证升级前开工的任务仍用旧配方产出，尚未启动的后续 Action 和 BT 的奖励、事件也保持原版本。
- 表达式 AST 缓存上限为 4,096 条。索引键在求值时绑定。在四条代表性谓词、每次 48,000 次求值的对照中，中位耗时由 1.733 秒降至 0.377 秒。此数据衡量条件执行，平台基准另测得 1,000 个角色的单分钟 p95 为 132.7 毫秒。
- 扩大回归 367 项通过，最后复跑的 DSL、条件和架构聚焦回归 21 项通过，成功与失败条件的错误诊断另复跑 2 项。通过交互 Bash 加载 `~/.bashrc` 后，远程 God 冒烟测试三轮质量检查通过。

### 风脊号 0.5 可改造的航线

- 首航后可用信任和木材安装侧风信标，或用铜矿安装打捞架。两件航具分别从风蚀林和碎盐滩打开原有航线的早期通行机会。回归验证在灯岬不能直接走这两条捷径，旧工具和探索条件仍能开放原路线。
- 两条路线分别结算口粮、药草和风筝人声望，以及线圈产出的铜煤和风暴代价。各自只结算一次并唤醒 Story。铜料开局通过两轮断桥补给后也能安装信标，同一存档可以拥有两件设备。升级测试验证旧玩家获得设备行为，保留进度。
- 最终版相关回归 58 项通过，覆盖世界、存档升级、Web 入口、运行时边界、自主补给和 DSL 执行。Chromium 在 390×844 手机视口完成雨背崖援助，在 1366×900 桌面视口完成铜潮谷打捞。两条路径从首个选择到结算均通过正式游戏入口。

### DSL 行为树执行缺口（待修复）

- 用新建的 `starter_village` 实例核对已注册定义和编译结果。`mediate_conflict` 的 `conflict.resolved`、`research_source` 的 `research.new_fact_found` 都能通过包校验，但编译成执行计划时被跳过。`wait` 同样没有编译分支。`selector` 只展开第一个子节点。已有 Action 条件、动作结算与按时间触发的世界规则正常执行。

### DSL 行为树执行缺口（已修复）

- 行为实例保存选择器的全部分支。条件在运行时检查当前状态，等待按模拟分钟推进，失败时按配置顺序尝试后备分支。执行回归覆盖条件失败、嵌套回退、暂停后重开、Action 快照、并行资源预留回滚，以及保留此前已结算的效果。配置校验拒绝不可执行的节点组合，选择器各分支继续遵守授权契约。
- 雾溪村 0.1.1 用已记录的对话结算调解，研究结果区分分次推进与完成。实测从 0.1.0 升级后，升级前已开工的调解和新版新开的调解均完成。
- 相关回归 85 项通过，随后复验的 DSL 与校验 9 项、授权与运行时 38 项、村庄研究与并行执行 4 项也通过。加载交互 Bash 的远程决策探针从真实视图选中并接纳 `behavior:research_source`，God 冒烟质量检查通过。

### 2026-11-27 世界配置、Agent 证据与远程成长

| 范围 | 修复与验证 |
| --- | --- |
| Director 验收 | 规则评估按提案提交顺序、完整时间窗与实际结算的规则编号匹配。社会计划需要先登记单位，再由角色对同一单位产生可核对的结果。回归覆盖 201 条噪声事件、同分钟先后顺序、改名规则、期限、机构账簿、派系状态与关系的实际增量。 |
| 世界配置 | `evolution.yaml` 的 `director.evidence` 选择登记事件、使用事件、关联字段和条件，`director.followups` 与 `contracts` 指定等待时长、跟进角色、任务和工具。潮汐公社开启社会跟进。三组临时世界包分别选择 God、Story 和省略跟进，经过真实调度器核对入队、触发证据、工具可见性和过期任务。 |
| 反思与 Story | 反思支持通过原文修订同一条信念，Story 可按触发编号读取越过最近事件窗口的证据。真实 `remote` 在限定 `update_belief` 的三灯镇探针中保留原信念 ID、改写观点，置信度由 0.90 调至 0.85。风脊号的工坊事件之后插入 501 条背景事件，真实 Story 仍激活了对应情节。 |
| DSL | 行为条件与事件证据共用表达式求值器。编译器拒绝会被平铺的并行内多步顺序分支。真实 God 曾省略 `entity_state_set.value`，角色完成动作却写出空值到空值。世界包和动态提案现在都会拒绝该定义和缺少事件名的 `public_event`，旧 Action 结算失败会回滚先前效果。分钟效果使用行动者支持的效果类型，雾溪村按已有属性配置提升 `threat_level`。 |
| 玩家入口 | Chromium 检查空大厅初始封面和手动进入选集，再分别以 1366×900 和 390×844 进入风脊号。两项抉择位于 Y=473 和 Y=420，桌面救回斥候，手机收回铜料。危险值从世界包读取，风暴压力达到 35/65 时分别进入警告/危险。 |

远程试验读取 `~/.sim2/llm.json` 中的 `remote`，五种角色均使用 `deepseek-v4.1-flash`。

- Aster Relay 验证场景，命令为 `python scripts/verify_automatic_growth.py --provider remote --minutes 360`。修复缺失字段前的对照开了 3 项计划、完成 0 项。修复后新存档开了 4 项，完成 1 项，2 项超时，1 项在途。共新增 10 个结构单位，NPC 完成 1 种新增行为，Story 激活 6 个情节，门禁通过。第 20 分钟 God 创建 Aster Relay Wardens，第 71 分钟 Mira 把该派系的 `ration_order` 从空值改成 `checksum_first`，Director 以该事件完成首项社会计划。资源和规则计划在期限内没有获得完成证据。
- 原样加载风脊号世界包，从 0 运行到 60 分钟。4 次角色决策模型调用、3 次 God 调用，角色共完成 12 次行为。God 注册了 `dyn_salvage_1`，该行为在这一小时内尚未被角色执行。风脊号使用世界包指定的确定性 Director。
- 继续同一份 Aster 存档。第 576 分钟 Mira 完成 `tally_capacitor_salvage_sweep`，Director 结清第二项计划，随后转向地点。第 700 分钟真实 God 将 `costs.reserve` 写成对象列表，旧校验让列表进入了资源能力检查，模拟因此中断。修复后重放原模型参数会得到 `action.costs.reserve must be an object`，五个成本区段都有拒绝与无部分注册回归。以第 700 分钟的 SQLite 存档重启，正常运行至 720。累计开过 8 项计划，完成 2 项、超时 5 项、在途 1 项，角色实际完成 4 种新增行为，Story 激活 7 个情节。跨计划成长已观察到，长期完成率仍待测量。

迭代期间 `scripts/test_parallel.py --changed --workers 4` 选中的 1,550 项回归通过。第 700 分钟的修复之后又分别复跑了 101 项 DSL、行动绑定和风脊号用例，35 项社会提案、定义 schema 与发布包用例，以及 11 项垂直提案用例，全部通过。10 个世界包通过校验，最终 wheel 包含共享证据配置和 Aster 验证场景配置。`compileall` 和 `git diff --check` 通过。1,000 角色基准的 5 次测量中，单分钟推进的 p95 为 173.79 毫秒。

### 2026-11-27 玩家标题画面与场景探索

- 标题画面全屏使用世界封面。探索页用地点插画和浅纸色抉择卡呈现眼前的事件，角色、设施和公共纪事分别从导航进入。没有专属插画或抉择的世界沿用程序化画面和推荐行动。
- Chromium 从空大厅打开世界选集，再以 1366×900 和 390×844 两种视口实际进入风脊号。桌面玩家从灯岬救回斥候，手机玩家收回铜料。两种视口都在第一屏显示了两个选项；雾溪村开场显示程序化封面和可用行动。
- 浏览器回归覆盖键盘切换探索、角色、纪事和打开地图。拦住地图刷新请求，关闭弹窗后再放行响应，验证弹窗保持关闭且当前抉择仍然可见。

- 已进入世界的玩家可在探索首页直接查看姓名、出身、地点、目标、属性和行囊。回归 `test_game_navigation_keeps_the_player_and_current_choice` 实际以制图师进入，验证 375×667、390×844 和 1366×900 三种视口的角色栏与首航选择同屏；打开行囊、按 Escape 返回、打捞铜料、刷新页面后，出身和新增物品种数均正确。

### 风脊号 0.6 同首航分档实测

- 三个独立存档使用相同随机种子 87231、机械师林晚和「拉回斥候」。God、Story、Director 使用本机配置的 `remote`（`deepseek-v4.1-flash`）。世界包提供基础生存机制、可提案的结构类型与结算边界，首航后续章节由 Story 提出。Director 可以读取本档近期相关事件和居民处境。保存专属上下文的隔离由 `test_director_reads_configured_history_from_its_own_save` 验证。
- 在第 780 分钟对三个测试存档就地升级，保留已生成的行为、规则、章节与时钟。升级同步世界包的 Story 配置和提示词，使已有存档也能应用新策略。随后八名 NPC 均启用模型决策和既有离线 BT 回退。
- 第 780 分钟保存副本通过正常玩家入口试玩。甲档「向风筝人问路」使风筝人声望从 0.35 升至 0.45，并登记引航交涉。乙档「请风筝人引航」令探索计数从 0 升至 1。回放发现传送效果曾将中文地名写进角色的位置字段，现解析为地图实体 `broken_span`。丙档在这个时间点没有新玩家行动可用。对应回归 `test_generated_guidance_uses_canonical_targets_or_rolls_back` 涵盖地点名称、ID、错误目标与整笔回滚。
- `test_open_structural_contract_keeps_salvage_yields_bounded` 守住旧存档打捞动作的材料与产量约束。`test_emergent_story_chapter_takes_precedence_over_situation_anchors` 回放大量其他角色专属章节后，玩家仍可读到自己的章节。
- 回放丙档实际 God 提案发现，关系的数值 `delta: 0.2` 原先只成为关系附加字段。在全新世界重放同一提案，风筝人到林晚的 `relation_strength` 现为 0.2。测试 `test_god_relationship_delta_changes_strength_and_preserves_history` 还验证重复增量、上下限、既有描述和错误输入回滚。此修复未改写早期历史事件。
- 首轮规则在三档均有过超时。风脊号可把尚未被居民采用的新规则交回 God 复查。用真实 Platform 的 `test_unused_rule_wakes_god_then_closes_on_its_resident_followup` 验证登记事件进入跟进任务，规则由阿岚的实际行为履行后才结清。
- 居民完成章节后，Agent 上下文会过滤已关闭章节留下的旧目标。章节完成事件保存实际行为事件编号和结算结果，并可按世界配置唤醒 Story，下一章可追到上一章究竟是谁做了什么。`test_story_beat_completes_after_audience_followup` 走普通行动入口，验证前后两次决策视图和对应的结算事件。`test_emergent_beat_commits_once_per_validated_result` 核对新章节的公开事件与事实来源。
- 在第 1440 分钟对三档复核并同步新的规则跟进与 Story 唤醒配置。升级前后的分钟、完整 Story 状态、计划历史、规则和动态定义登记数一致。甲档两种生成行为已由阿岚完成。乙档生成的引航行为有 45 次居民执行。丙档的补桥绳选项曾获受理但未结算，重放其真实提案发现纤维参数未声明、消耗效果未指定合法物品字段。新定义会一次返回相关校验错误，旧定义缺少物品绑定时无法免费执行。确定性回归为 `test_unbound_bridge_toll_returns_actionable_feedback_before_registration`。在副本中将实测拒绝信息交给远程 God 后，它查看了定义，该轮没有提交替代行动。
- 甲档的模型行动曾将声望写入临时字段 `kite rep`，乙档重复引航将 `kite_rep` 推到 4.77。真实行动副本回放中，阿岚现在改变世界登记的 `kite_rep`（0.27 → 0.32）与 `camp_morale`（0.90 → 0.91），阿筝的引航将越界旧值修回 1。Action 的变更事件使用实际值。世界声明的百分比上限和字段展示名来自已有的 `world_property_labels`。回归 `test_generated_effects_change_the_configured_reputation_and_report_its_limit` 覆盖中英文展示名及旧存档的重名字段。
- 丙档第 1500 分钟收到「去风蚀林割纤维」，仅交给了离线玩家。在第 1680 分钟三档应用相同的居民跟进约束；其后的普通结构提案仍可生成玩家专属内容。跟进任务若只把行动给玩家，将得到可重试的结构反馈。回归 `test_crew_followup_can_include_a_player_and_requires_a_resident` 验证同一提案先被退回，补入模型自选居民后获准。单独的新档 180 分钟实测中，阿筝执行了 God 生成的「阿筝为林晚破例引航」，声望从 0.35 升至 0.45、探索数从 0 升至 1。
- 第 1795 分钟，阿岚与阿筝结算信任变化 0.15，关系强度到 0.30（甲档事件 3303）。第 1800 分钟 Director 以该事件完成社会计划。丙档的 Director 重新转向行为后，阿筝在第 1880 分钟割取两份风蚀林纤维，第 1900 分钟完成搓绳，背包多出两份桥绳。后一动作扣体力，没有扣前一动作的纤维。这两条库存记录可证明有新玩法被采用，但不能证明交付或材料守恒。
- 新包另开三份零分钟存档复测，仍然使用同一身份、种子和首航选择。240 分钟时，真实 Director 分别选择社会、规则、规则。甲档居民执行了两次新增行为，玩家有两种新选项；乙、丙仅登记新规则，均未观察到居民采用。短测按严格标准返回失败，三个模型有回复和文案不同均不足以认定世界分化。
- 丙档继续运行后，第 2115 分钟 God 向阿筝开放「给风筝人补桥绳」。第 2145 分钟阿筝完成交付，纤维从 14 份减到 10 份，风筝人声望从 0.43 升到 0.51（行为完成事件 3857、声望事件 3853）。这是丙档另行产生并实际扣料的行动。
- 甲档计划在第 2520 分钟到期。God 多次耗尽工具预算后，剩余窗口不断减半，第 2517、2518 和 2519 分钟都有重试。现在初次失败仍可在短窗口内补试，反复耗尽预算后遵守世界的 Director 调度间隔。对第 2518 分钟事件的副本回放，下一次排期由 2519 变为 2548，超过旧计划期限时由调度器检查其有效性。回归 `test_budget_exhausted_structural_retry_fits_the_plan_window` 和 `retry-window-replay.json` 验证该差异。两日实测的原始事件仍保留旧重试节奏。
- 三档都运行到第 2880 分钟，跨档玩家后果和居民实际采用的机制均通过检查。甲、乙、丙各有 9 个 Director 决定与已提交计划一致，各有 1 个计划结清、7 个到期、1 个仍在进行。God 有 199、223、168 条模型回复，Story 有 42、32、43 条回复。三档分别形成 18、17、22 章，其中 15、14、19 章有完成事件，3、2、4 章直接沿用上一章的居民行动结果。居民执行生成行为的次数分别为 84、155、79，对应 6、3、6 种生成行为。乙档 155 次中有 129 次是同一种引航，反复采用的次数与玩法丰富度应分别判断。
- 在第 2880 分钟的三份独立副本中用正常玩家入口试玩首项生成行动。甲档问路改变交涉状态，乙档从灯岬抵达断桥，丙档在风蚀林收获 4 份纤维。丙档最初结算失败，原因是第 1500 分钟生成的旧动作通过 `item_id` 和 `quantity_parameter` 引用已声明的参数。旧档读取现可使用这种明确的绑定，同时拒绝缺少数值、数量非正或物品未绑定的动作。修复在原始副本重放，位置、库存和体力都按动作结算。`test_saved_inventory_effect_binds_declared_item_and_quantity_together` 和包含未声明参数的旧收费提案分别覆盖成功和回滚场景。
- `scripts/verify_windward_divergence.py` 现在也要求每个存档的玩家能完成一个新增行动。两日原始记录及玩家重放位于 `artifacts/windward-open-seas-final/divergence.json`，修复前的对照位于同目录的 `divergence-before-legacy-bind.json`。短测 `artifacts/windward-release-sweep/divergence.json` 的乙、丙档在 240 分钟仍无新增可执行玩法。
- 最终全量测试以 4 个独立进程运行，1,576 项通过。风脊号世界包验证、Python 字节码编译和 Git 空白检查通过。1,000 个角色的 5 轮 Platform 单步基准在本机的 P95 为 196.483 毫秒。

### 2026-11-28 重复行动与主线导演

- 从乙档逐个检查行为树与 Action 后确认。「替风脊号引航」完成了 129 次，另一个叫「结绳人向断桥看守确认欠账」的行为也调用同一个 Action，把它的居民完成数推到 136 次。风脊号现在按世界配置给生成的社会成就去重，并给其他生成行动设 90 分钟的居民共享间隔。测试 `test_generated_social_outcome_is_shared_by_residents_and_survives_reopening` 覆盖中英文显示名与 ID 等价、不同定义重用同一社会成果、并行结算、存档重开与玩家自己的首次机会。测试 `test_generated_resident_cooldown_uses_the_world_setting` 覆盖同时开工、暂时拒绝和时限结束后恢复。
- 风脊号启用 `director.narrative_direction`。Director 的计划包含当前主线目标、玩家的两难和可核对的源事件。近期生成行动采用次数进入 Director 上下文。有效计划按世界配置唤醒 Story，计划简报同时进入 God 和 Story 的视图。测试 `test_director_wakes_story_only_from_a_grounded_mainline` 验证未出现在 Director 视图中的事件 ID 不得触发 Story。
- 使用原乙档第 2880 分钟副本，把时钟设到上一项计划在第 3030 分钟到期的位置，独立验证远程角色交接。真实 Director 引用事件 5905，将三份铜料的分配立为当前主线，明确先保船和先偿还断桥配给债务的后果。计划事件 5906 唤醒 Story 并成为新章节的证据。居民点账失败后，Story 又依据实际行为失败事件 5930 写下一章，保留在场见证要求。该诊断副本的 God 执行 1 个会话，Story 执行 3 个会话。原存档没有改时钟。证据见 `artifacts/windward-pacing/mainline-final.json`。
- 从第 2880 分钟的同一乙档复制两份，各用 `remote`（`deepseek-v4.1-flash`）续跑到第 3330 分钟。原规则再次结算同一引航 Action 42 次、点账 31 次。新规则的旧引航和点账均为 0 次，仍有居民结算代结欠账和绳桥作证，分别扣除库存中的一块备用铜板（事件 6056）与完成 Story 章节（事件 6245）。全体行动完成数 94 和 37，涉及不同 Action 8 种和 9 种。两个存档末尾的玩家副本都通过正常入口执行了「请风筝人引航」。该对照使用相同时间段。主线唤醒由单独的远程诊断副本验证。详见 `artifacts/windward-pacing/comparison.json` 和 `artifacts/windward-pacing/mainline-final.json`。
- 主线、重复结算和直接提交的 4 个最终定向用例通过；行动受理、引用解析、演化配置、Director 及 Web 资产的 39 项回归通过。风脊号配置通过验证，`compileall` 和 `git diff --check` 通过。

### 2026-11-28 风脊号采集工具

- 石镐原先已是铜潮谷采铜和采煤的条件。新版世界包让铜镐替代石镐并缩短采集时间。采铜 90 → 54 分钟，采煤 75 → 45 分钟。矿石产量和采铜的风暴压力按原动作配置结算。
- `duration.tool_bonuses` 按背包物品和可选材料标签选择最快的适用工具。石镐和铜镐能加快少量收集石块、陶土、玻璃砂与盐，石斧能加快木材和纤维，新增石刀配方能加快纤维、浆果、药草和蘑菇。晶锯能加快采晶石、取琥珀和拆鲸骨。采集预览、受理估算与行动结算共用计算，工具不扣除。测试从玩家入口核对时钟、产出及工具保留情况，浏览器核对采铜卡同时显示「54 分钟」和「铜镐省 36 分钟」。
- `test_tool_upgrade_keeps_the_duration_of_an_active_mining_job` 验证升级前已经开的采煤仍按 75 分钟完成，新开工可用铜镐在 45 分钟完成。另在实际第 2880 分钟的乙档副本上应用 0.6.1，分钟、背包、Story 章节和 Director 历史一致，确认加载了石刀配方和采煤工具加速。原乙档通过只读连接复制。
- 风脊号和浏览器回归 64 项通过，定义校验和行为接口回归 39 项通过，后来补充的木材加速与石斧不能加速药草两个用例通过。风脊号世界包校验、Python 字节码编译和 Git 空白检查通过。

### 2026-11-28 风脊号补给与开放航段审查

- 从空粮箱和水箱实际推进一天。旧世界基础口粮 2、二级花园和厨房合计 6，水由两条规则共同产 12。每人自然消耗 17.28 点饱食与 23.04 点水分。0.7 试玩中二级花园将口粮日产带到 10，并让药草生产与采收到独立库存。公共热饭用浆果与盐补六份口粮，个人口粮配方产六份，炖菜产四份。配方菜单显示具体投入与产出。
- 新增蓄水槽与集水帆。基础每六小时收三份露水，升级后六份，水箱上限由 24 增至 36。第一份水和口粮可在营地直接用。验证：`test_cistern_collects_water_and_last_servings_are_usable` 和 `test_galley_batches_pay_for_ingredients_and_feed_pantry_or_player`。
- 药园一级每十二小时长两份药草，二级四份；采收消耗实际库存。包扎用一份草药恢复 15 健康。验证：`test_garden_grows_herbs_for_harvesting_and_field_treatment`。
- 元数据里的 `integrity` 展示为健康，首页同时显示健康、精力、水分、饱食。低精力时，浏览器建议能执行的休息行动。营地休息 90 分钟恢复 60 点精力，比空等恢复同量快 30 分钟。真实浏览器验证「精力 6%」与「继续航行」以及原首屏的角色和抉择布局。
- 新档机械师以正常玩家入口从首航吊起铜料走到第 7 天离天，接着前往碎盐滩。共 120 次已结算行动，无预发资源或手改时钟，见 `artifacts/windward-balance/fresh-tour.json`。其中晶石采了 10 次，木材 9 次，铜矿 7 次。新加的整片晶脉动作实际产两块、耗时 120 分钟、涨 12 点风暴，比较两次用晶锯精采省 48 分钟、多涨 4 点。锚定耗两枚核心和 4 份纤维，并要求二级花园与风筝人信任。
- 阶段结果继续推进同一存档。第 30 天发出云涌事件并增加风暴压力，正在进行的离天仍可结算。旧存档曾触发硬截止的，可再次尝试出航。迫降恢复临时船体、消耗库存，各路线会按各自后果开启下一航段，天气与补给继续。回归覆盖三条主动路线、迫降、跨第 30 天及旧存档恢复。
- 在第 2880 分钟乙档副本准备测试信标条件，从正常玩家行动离天。第 3121 分钟开启下一航段，Director 在相同分钟根据已提交事件替换旧计划。用本机配置的 `remote`（`deepseek-v4.1-flash`）继续 180 分钟。God 登记两项定义，居民真实完成代结行为（第 3157 分钟），Story 激活并结清多段后续。可回看 `artifacts/windward-balance/stage-continuation.json`，其中包含首轮双唤醒造成的重复章节。新增归一化受众后的章节去重，使用双唤醒回归验证。
- 10 个世界包通过校验。跨世界 DSL、行为、市场与潮汐回归 110 项通过。风脊号、Director 和浏览器的大组测试 82 项通过，旧首航 fixture 在锚定新增纤维成本后需补齐材料。补齐后重新跑过首航双分支、三条航段路线、旧截止解锁、蓄水和批量采集等 8 项，均通过。Story 和 Director 最终组 19 项通过。

### 2026-11-28 动态航区、定居与章节接续

- 首轮远程试验的 God 提案省略 `state.public`，地点和回程航线已登记，玩家地图仍看不到。工具参数现在明示公开地点所需的字段。`world.location.created` 和 `world.resource.created` 在世界包校验中列为已有生产者，世界可声明由新航点唤醒定居任务。`test_compact_evolution_is_valid_and_stored_compiled` 与实际远程链均用已有运行时事件通过校验。
- 动态新增的 LLM 居民从入场分钟开始使用世界包的反思和创新周期。既有存档应用世界包配置时，补齐居民缺失的周期。定向用例覆盖入场后的到期时间和行为树控制的居民。远程测试档还记录了新居民的真实反思调用，以及后续创新任务。
- 远程试验中 Director 返回了包含两个选项和取舍的结构化对象，原先只接收字符串，丢掉了第二份计划的剧情方向。现在接收两种形态，来源仍须是本轮看到的已提交事件。定向用例覆盖有效对象、缺少取舍和不可见来源。修复后的 `frontier-d` 两份计划都保存了结构化取舍并发出 `director.plan.started`。
- 实验通过 `scripts/verify_frontier_growth.py --provider remote` 从单一泊位和遇险信号启动，无预写新地点、居民或章节。`frontier-c` 和 `frontier-d` 均在 20 个游戏分钟通过验证。五个角色均调用本机配置的 `deepseek-v4.1-flash`。两档都生成 Kettle Rock Mooring，随后分别生成 Clamp Yard Spur 与 Collar Yard Float，并分别迎来 Dessa Kettle 与 Bosun Halda Vey。玩家走正常入口抵达（事件 23），居民巡查（事件 31），Story 引用巡查信息（事件 28）激活章节（事件 34）。Director 以到达结清计划并开启下一份。重开存档仍能看到本档的新地点和居民。详情见 `artifacts/frontier-growth/frontier-c-summary.json` 和 `artifacts/frontier-growth/frontier-d-summary.json`。
- `test_god_created_route_respects_authored_travel_gates` 在风脊号新档登记动态地点及带滑翔翼条件的新航行行为。装备前玩家没有可执行航行选项，装备后从真实玩家入口正常抵达。运行时插画、整船迁移及跨港生产、贸易和自治仍待实现与验证。
- God、Director、演化配置、NPC 演化及 Story 的 44 项回归通过。风脊号动态航线和存档升级的 3 项用例通过。Python 字节码编译与 Git 空白检查通过。

### 2026-11-28 独立运行时插画

- 新增 `sim2 artwork`。可选后台进程只在玩家当前所在的公开地点选择 God 新建的地点和同处居民，由已经配置的 Story 文字模型提炼视觉描述，再向独立 HTTP 绘图服务请求 WebP。服务、模型、尺寸、风格与凭证环境变量均由部署配置提供。游戏与客户端均不依赖 Codex。
- Platform 用事务争用任务、校验 WebP 并按摘要保存，每个实体发布一张。失败按间隔重试，沿用已获得的描述。交付插画后正常刷新玩家视图，载入失败显示首字徽章或场景底图。断线后重开与世界包升级保留生成资产。定向用例覆盖到达后才触发、非法图像重试、跨进程任务争用、重复调用去重和旧档升级。
- 在 `frontier-d` 的独立副本用正常 CLI 和本机 `deepseek-v4.1-flash` Story 角色实测。两次取得视觉描述，向本地测试服务发送两个出图请求，第三次为空。测试服务返回固定 WebP，未测试正式绘图模型。真实 Chromium 手机视口显示了场景和 NPC 头像，阻断头像请求时显示首字。机器报告见 `artifacts/frontier-growth/story-art-runtime-proof/report.json`。
- 在手机视口保持人物面板打开，从另一条进程连接发布图片。客户端经正常的事件流升级场景和人物头像，页面无需重载。图片持久化与重试的 5 项测试、浏览器首屏的 8 项测试均通过，新增模块的 Python 和 JavaScript 语法检查以及 Git 空白检查通过。

### 2026-11-28 插画画质对照与运行成本

- 用已有 Codex 样图做 Kettle Rock 场景对照，并根据存档里的真实 Story 描述生成 Halda Vey 头像。本轮新场景请求遇到网络错误，回放使用同一地点早先由 Story 描述生成的样图。把两张成片转成 WebP，经本地 HTTP 服务和正常 `sim2 artwork` 流程回放。手机与桌面 Chromium 显示了真实样图，人物面板的 52 像素头像清楚，24 像素头像主要保留轮廓。报告见 `artifacts/frontier-growth/story-art-quality/report.json`。
- 独立绘图配置支持可选的 `quality` 和 `output_compression`，省略时沿用服务默认值。HTTP 回归验证这两个值按配置透传，并拒绝无效设置。图片相关 8 项用例通过，Python 编译与 Git 空白检查通过。
- OpenCode Go 官方清单未列文生图额度，OpenCode Console 将出图列为按量收费。按用户要求，游戏正常运行采用世界包与现有回退呈现，绘图 worker 保持可选，未配置线上付费绘图模型。
