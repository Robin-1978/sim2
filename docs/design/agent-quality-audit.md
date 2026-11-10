# Agent Quality Audit

This audit checks whether an Agent understood the supplied world view and
whether its choice or proposal follows from that view. Schema validity and an
`accepted` Platform result are necessary, but neither is sufficient evidence of
quality.

## Context criteria

Each session keeps a stable protocol and tool schema at the front, followed by
the current dynamic view: world time, the actor's state and goals, drives,
relationships, scoped knowledge/memory, recent outcomes, and only executable
behaviors. The view must state that Platform owns costs and state changes. A
behavior description must expose its purpose, requirements, target, time, cost,
impact, success, and failure in plain language. Internal references are for
Platform validation; model-facing choices use visible names.

The behavior view still has a small amount of intentional repetition: `when`
and `requires` are both shown because they answer different questions for the
model. Nested action costs are collapsed before rendering. These are clarity
concerns, not permission defects.

## Dialogue cost and quality

The first 967-minute remote baseline recorded 396 instant dialogue sessions
and 2.30M dialogue prompt tokens. The messages were fluent, but the same
topics (ration shares, trade ledgers, and river contamination) were reopened
every few minutes with little new information. The cause was a pair-only round
counter combined with a 30-minute retry window, while each reply received the
full planning view.

The runtime now treats an interaction as a bounded thread:

- dialogue replies use a compact context containing the incoming message,
  nearby participants, relevant facts and testimony, and open offers;
- a dialogue reply can only send one short message or wait; it cannot start an
  unrelated long behavior;
- repeated or near-identical messages within the thread window are suppressed;
- a thread closes after three exchanges and can reopen only after the
  configured window; ordinary direct-message retries use the configured
  120-minute cooldown in `interaction_policy`.

The Platform still records every submitted exchange, so suppression affects
LLM work only and does not delete world history. `SimulationReport` exposes
`interaction_replies_suppressed` and `interaction_threads_closed`; compare
those with `llm_call` usage when reviewing a long run.

Completion-token spikes have a separate cause from prompt size.  The remote
provider was allowed to choose its default completion budget, so a single
decision could spend thousands of tokens on internal planning and several
read-tool calls before returning one small choice.  The gateway now sends a
bounded `max_tokens` value (default 2048; configurable per provider) while the
existing ReAct read/tool budgets remain in force.  The larger bound leaves
free providers enough room for one tool call and a concise decision while
remaining below the unbounded 3k+ completions seen in the baseline. `llm_call`
records prompt, completion, latency, and tool iteration counts, so a review
can distinguish a large dynamic context from a verbose model response.  A
provider requiring a different field can override it through `extra_body`.

Decision, Story, and God sessions also cap ordinary read tools at two per
turn and retain only the initial snapshot plus a short ReAct tail.  This keeps
an Agent from reopening the same scene for six iterations and prevents tool
results from being resent as an ever-growing conversation.  A rejected read
budget is fed back as a normal Platform error, after which the model must use
the facts it already has or submit `wait`/one intent.

## Full-run verification

The latest complete run used DeepSeek v4.1 Flash for 1,440 world minutes. It
recorded 279 Agent sessions: 244 decisions, 11 reflections, 18 God sessions,
and 6 Story sessions. All 279 quality checks passed; there were no LLM errors,
rejected turns, orphan behavior intents, unresolved evolution tasks, or direct
God property changes. The Platform persisted 86 started behaviors and 82
closed behaviors. It recorded 141 action starts and 137 action completions; the
remaining four actions were still validly executing at the exact end minute.

The run produced 90 dialogue exchanges and 55 instant reply calls. The
instant-reply prompt averaged 1,976 provider prompt tokens. Thirty replies were
suppressed as repeated content and 28 topic threads were closed by the
configured round/window policy. The full prompt, transcript, output, and
SQLite path are in `artifacts/real-agent-audit-1440-final-complete.json`.

`scripts/review_audit.py` now performs the final audit over every session and
the complete SQLite database. It checks prompt order and feedback continuity,
role quality, behavior/action closure, evolution-task status, world-minute
completion, dialogue participants and placeholders, and God's property-change
boundary. A pending quality check or unresolved database row fails the audit.

## Quality evidence

| Role | Observed result | Assessment |
|---|---|---|
| Decision | DeepSeek selected “Explore Greywood” over eating while the view showed an exploration goal, safety pressure, and the missing-person goal. It used a listed behavior and did not invent costs. | **Pass: comparative grounded choice** |
| Reflection | After eating raised hunger satisfaction to 100, the Agent returned `wait` instead of recording a redundant lesson. | **Pass: low-value memory declined** |
| God | The Agent proposed an event claiming that threat, public trust, and food changed. Platform recorded only the event (`applied_effects=false`). | **Fail: accepted narrative signal without a structured world effect** |

The God case is the important remaining gap. A direct change must be proposed
as a typed world property, rule, relationship, or faction update, then the next
God context must compare the recorded before/after value and decide whether a
follow-up is needed. An event may describe what happened or wake another loop,
but it cannot imply that a numeric, resource, relationship, or condition value
changed.

The controlled game audit then advanced one Agent from world minute 0 to 241.
Its exploration outcome was persisted, Reflection wrote one grounded long-term
lesson, and the next Decision retrieved that lesson and chose recovery before
the newly unlocked ledger task. A second God audit showed why the boundary is
needed: a real food-crisis task caused the model to submit `village_food=100`
while the value was already 100, while claiming that the granary was full and
residents were safe. Platform previously accepted this as an applied change;
the resource projection guard now rejects direct God property changes and
requires behavior-driven resource evolution. A stored God rule is graded
`accepted_needs_world_followup` until a later behavior outcome verifies that
the rule changed the world.

## Acceptance checklist

1. The selected behavior or proposal is present in the supplied candidate set.
2. The choice is supported by visible goals, drives, state, relationships, or
   a retrieved fact; a short reason is enough when the alignment is clear.
3. The output does not fill in Platform-owned time, cost, or effects.
4. Reflection keeps only a specific, durable, outcome-grounded lesson, or
   returns `wait` when the outcome adds no reusable knowledge.
5. God proposals have an observable typed effect, their declared evidence is
   represented in the condition, and a later context verifies the effect rather
   than treating acceptance as evolution.

The full prompt, tool calls, Platform results, and quality checks are kept in
the redacted local artifact `artifacts/deepseek-quality-review.json`; it does
not contain API credentials.

After the God schema was narrowed, a fresh remote trigger run selected a
configured-looking `Winter Ration Line` rule without attempting a resource
property write or a stocked resource entity. The Platform correctly graded the
turn `accepted_needs_world_followup`: the rule is stored, but a later behavior
must execute rationing and produce the expected resource outcome before the
world change is considered complete.

Current implementation note: `RuleCompiler` connects validated rule records to
the Agent view, BT/Action admission, edge-triggered world signals, and
`world.rule.followed` outcome records. It rejects unknown `world.*` condition
fields and maps visible entity names such as “旧井” back to configured keys at
the Platform boundary. A stored rule is still not an applied world change: the
God grade remains pending until the named behavior completes and its observed
result is visible in a later world review. A signal has no enforcement
capability; blocking requires a typed `behavior_constraint` or denial effect.
Natural-language descriptions are advisory and are never parsed as policy. The
Platform also rejects a second signal for the same event/follow-up pair and a
same-role signal chain sourced from `world.rule.*`, preventing a notification
from recursively generating notifications without a behavior result. Story
context exposes exact character names from recent event participants; one
emergent beat is recorded per validated evidence event.
