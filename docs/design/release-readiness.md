# Release Readiness

This checklist records reproducible evidence for the complete Platform and
client release. A feature is not considered ready merely because it is present
in the source tree.

## Current evidence

| Area | Evidence | Status |
|---|---|---|
| Agent budgets | Role configuration owns provider, completion bound and ReAct iteration bound; provider entries contain transport settings only. | Ready |
| Installed world package | The wheel includes `worlds.starter_village`; a clean virtual environment ran `sim2 init` without a source checkout. | Ready |
| Generic world | A non-village orbital template compiled to package YAML, initialized SQLite, and completed a behavior that changed reactor state and charge. | Ready |
| WorldBuilder | The template schema, exact-template validation, initial runtime smoke test and real Beam transcript are covered. WorldBuilder inherits its role iteration budget. | Ready |
| TUI and map | Textual tests cover map, timeline, character selection, pause and step. A separate orbital-world test proves the map is not village-specific. | Ready |
| Deterministic week | Current code reached world minute 10080 with 751 provider calls, no LLM/rejection errors and no degeneration flags. | Ready |
| Performance | 1000-agent, five-run Platform benchmark completed at p95 127.832 ms. | Ready |
| Short real-model run | DeepSeek v4.1 Flash reached minute 360 with 45/45 quality-passing sessions, zero provider errors and 20 grounded testimony records carrying credibility and unverified status. | Ready |
| Real-model week | DeepSeek v4.1 Flash run is in progress from a fresh SQLite instance. | Pending |
| Protocol tests | `pytest -q` currently reports 257 passing tests; `compileall` passes. | Ready |
| Packaging | Wheel and source distribution build successfully; wheel contains the bundled world and Apache license. | Ready |

## Short run statistics

`artifacts/real-agent-audit-360-testimony.json` records the complete Agent
views, ReAct prompts, tool results and final world projection. Its final review
reports:

- 42 Decision, 2 Story and 1 God sessions; all 45 pass quality checks.
- 3 waits; no provider errors or rejected turns.
- 22 behaviors started and 17 completed; 35 Actions started and 30 completed;
  five Actions were still validly executing at the exact run boundary.
- 20 dialogue exchanges. Every received testimony was stored as `reported`
  with `verification_status=unverified` and a configured credibility prior.
- No orphan behavior intents, direct God property changes or degeneration
  flags.

## Provider status

The free endpoints were not usable during the current acceptance window.
Beam stopped at minute 1 after 21 HTTP 429 responses; LongCat stopped at minute
16 after 11 HTTP 429 and one HTTP 500 responses; Iceman stopped at minute 212
with seven failed turns. Those attempts remain provider-error evidence and are
not counted as successful model runs. A short current-code DeepSeek run
passed, so the long semantic audit uses the configured DeepSeek provider. The
model adapter and role configuration boundary remain provider-neutral.

The one-week DeepSeek run is checkpointed at
`artifacts/real-agent-audit-10080-deepseek-final-v6.checkpoint.json`. It must
reach minute 10080, pass `review_audit.py`, and have its timeline and statistics
exported before this checklist can be marked complete.
