# Long-run model and world audit

This record separates three kinds of evidence: a real provider audit, a
current-code deterministic week, and provider failures. A provider error is
never counted as an Agent success.

## Current-code deterministic week

`scripts/run_long_simulation.py --minutes 10080` completed at world minute
10080 with no LLM or network calls. The run produced:

- 10,080 world-minute events and 751 bounded Agent calls through the
  deterministic provider.
- 673 decision sessions, 72 reflection sessions, 3 Story sessions, 3 God
  sessions, 286 waits, and no provider or rejected-turn errors.
- 20 distinct behaviors, 38 event types, 54 food recovery cycles, 48 public
  food withdrawals, 70 known-node gathering runs, 123 safe recovery runs, and
  140 resource changes.
- 382 completed behaviors, 1 failed behavior, and 4 active behaviors retained
  at the exact end boundary. There were no degeneration flags.
- World properties ended at food 272, water quality 0.96, public trust 0.55,
  root-gate stability 0.64, and north-gate threat 0.22. Resources were only
  changed by configured behavior effects and regeneration.
- Evolution tasks had 5 completed and 20 future scheduled tasks. The run did
  not leave an orphan behavior intent.

This is the runtime causal and persistence proof. It is not a substitute for
reviewing real model language.

## Real Beam evidence

`artifacts/real-agent-audit-1440-beam-current.json` is a current-code real
model run through world minute 1440 using `beam-free` with an 8192 completion
budget. It contains 121 sessions: 108 individual decisions, 9 reflections, 3
Story sessions, and 1 God session. All 121 passed the quality checks. Database
review passed with 46 behavior starts, 45 closed behaviors, 80 Action starts,
79 closed Actions, 18 dialogue exchanges, no empty messages, no repeated
messages, no remote object observations, and no unresolved task.

The prompt review found visible names and bounded sections for time, self,
attributes, drives, memories, relationships, candidates, and outcomes. Model
outputs used visible behavior choices and typed tool calls; no internal UUID or
entity key was exposed. Platform rejection feedback was rendered in the next
ReAct prompt.

The earlier `artifacts/real-agent-audit-10080-beam-v1.json` reached world
minute 10080, but its database was created before the current food resolver and
behavior-definition fixes. Its review intentionally retains the historical
provider failures, old `first_food` failures, stale claimed task, and old
remote-observation findings. It is long-run historical evidence, not a current
code pass.

## LongCat provider audit

`artifacts/real-agent-audit-10080-longcat-4096.json` records the attempted
current-code LongCat run after the provider adapter was added. The adapter
converts LongCat's bare `{"request_behavior": {...}}` response into the shared
intent protocol before the Agent Engine sees it. The first 9 accepted decisions
were valid and semantically grounded, including communication, investigation,
governance, exploration, research, and crafting.

The provider endpoint then returned repeated HTTP 500/429 failures. The run
stopped at world minute 16 with 14 provider errors and no reflection, Story, or
God phase. The audit is marked incomplete; no part of it is presented as a
one-week success. The provider failure is distinct from the runtime protocol,
which passed the 240-test suite and the LongCat adapter regression tests.

## Performance and prompt limits

The 1000-agent platform benchmark after these changes measured p95 around
133 ms for a non-network minute. Real provider latency is measured separately
in `llm_call`; it is not mixed into the platform benchmark. The current-code
Beam day had median rendered prompt size about 9k characters and maximum about
12.8k. God event review used cached static prompt prefixes; a provider may
still produce a long reasoning response, but the Platform accepts only one
bounded final intent.
