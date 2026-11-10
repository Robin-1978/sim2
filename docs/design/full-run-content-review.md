# Full Run Content Review

This review compares the 1,440-minute public timeline with the persisted
SQLite events. Structural Agent audit passed, but semantic world consistency
still has several gaps that the structural checks cannot detect.

## Findings

| Priority | Finding | Evidence | Verdict |
|---|---|---|---|
| P0 | The ledger Story beat claims a delivery and public reading at the north gate. | At minute 422, `recover_caravan_ledger` completed with `actual.location=greywood_edge`; the Story beat at the same minute says the ledger was brought to the north gate and read aloud. Movement or a gathering event occurs later. | Confirmed chronology and location violation. |
| P0 | Patrol success claims more than the Action records. | BT `patrol_route` lists `patrol_reported` and `marks_classified`, while Action `patrol_route` emits only `security.patrol_reported` and the fact that the route was patrolled. | Confirmed contract mismatch. Repeated patrols are routine, but they do not classify a new mark. |
| P1 | Research facts do not yet lead to configured world-changing work. | Water and pact research reach `2/2`, Story beats and later conversations occur, but no configured follow-up changes water rules, treatment state, or root-gate stability. | Partially closed: knowledge and goals exist; plan → action → verification is missing. |
| P1 | The warning bell is crafted but has no deployment state. | `craft_dawn_bell` creates `dawn_bell=1`; no install Action/BT or `warning_artifact_installed` event exists. Later dialogue still describes the bell as unfinished and asks for materials. | Confirmed production/deployment gap and narrative contradiction. |
| P1 | Reflection can turn a topic or intention into a confirmed fact. | The minute-7 memory says the merchant provided ledger and route information, while the recorded BT exchange contains only a topic and no message; the contract and ledger facts appear later. | Confirmed memory provenance violation. |
| P2 | Duplicate evolution tasks are not present in the database. | The 35 `evolution.task_due` rows have unique task IDs and all due tasks are completed. | The apparent duplicates came from a timeline that omitted task IDs. Improve display, but no scheduler duplication was found. |
| P2 | `failure_results` is displayed as `failure`. | These are configured possible outcomes on successful BT views, not observed failures. | Naming clarity issue; no execution bug. |
| P2 | End-of-run active Actions remain running. | At minute 1440 four Actions are still running and their BTs are validly executing. | Correct snapshot semantics; document that `run(N)` preserves active work. |
| P2 | God records repeated patrol narrative events with no state change. | Several `world.change.event` entries repeat the same patrol report while `state_changed=false`; some later God sessions correctly wait. | Low-value event duplication, not a direct state violation. |

## Recommended repair order

1. Restrict emergent Story beats to facts, locations, and actions present in
   the source event; add a validated delivery/meeting event before narrative
   claims of public transfer.
2. Correct the patrol BT/Action contract and add a separate configured mark
   investigation if mark classification is required.
3. Add provenance to experience memory (`observed`, `confirmed`, `promised`,
   `inferred`, `failed`) and expose only source-supported claims to Reflection.
4. Add a configured warning-bell installation Action/BT and a persisted
   installed state.
5. Add configured follow-up BTs for water mitigation and pact decisions, then
   require an observed result before Story/God considers each research thread
   complete.
6. Suppress duplicate God narrative events using a configured event key/window
   and include evolution task IDs in the observer timeline.

The complete evidence is in
[`real-agent-audit-1440-full-timeline.md`](../../artifacts/real-agent-audit-1440-full-timeline.md)
and the strict structural/database audit is in
[`real-agent-audit-1440-final-complete-review.json`](../../artifacts/real-agent-audit-1440-final-complete-review.json).

## Changes made after this review

- Story context now exposes the validated source actor, recorded location, and
  result; emergent beats claiming a later delivery, public reading, gathering,
  or movement are rejected until a matching follow-up event exists.
- `patrol_route` no longer advertises `marks_classified`; its configured result
  is only the report that the patrol Action actually records.
- Reflections now label experiences as `observed`, `confirmed`, `promised`,
  `inferred`, or `failed`. The default is `inferred`, so a topic or promise is
  not silently stored as a confirmed fact.
- Prompt numbers are rendered without floating-point tails such as
  `81.200000000004`; stored world values retain their calculation precision.

The water follow-up BTs and warning-bell installation remain configuration
work items. They need explicit Actions and observable result events before
their research/production stories can be considered closed.
