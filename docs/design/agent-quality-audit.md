# Agent Quality Audit

This audit checks whether an Agent understood the supplied world view and
whether its choice or proposal follows from that view. Schema validity and an
`accepted` Platform result are necessary, but neither is sufficient evidence of
quality.

## Current architecture review

The platform uses a bounded ReAct loop for characters and world roles, typed
tools, persisted tasks and a single owner for world mutations. Keep that
boundary and select model calls by the ambiguity of the task. A policy with one
valid choice can be deterministic. Ambiguous choices can use a model and still
commit through the same intent and outcome contracts.

| Area | What works today | Remaining gap and acceptance measure |
| --- | --- | --- |
| Context | Role-specific AgentView, paged world index, ranked memory, and a preflight budget for the full request. | Compare request estimates with measured usage by provider and model. Calibrate headroom for long tool results and multilingual inputs. |
| Decision | The Agent receives executable behaviors and current targets. Platform checks revisions, preconditions and costs before applying intents. Background BTs and configured fallback can progress without a provider. | Some costs and tradeoffs are still conveyed as prose. Evaluate whether choices change when deadlines, capacity, scarcity or goal priorities change, and whether committed outcomes follow from those choices. |
| Reflection | Evidence gates and claim statuses distinguish reported information, promises and confirmed outcomes. A visible `event:<id>` now resolves beyond the recent event window. | Belief updates still lack a mandatory source and a way to supersede outdated beliefs. Compare later decisions with and without the retained lesson. Evaluate memory retrieval and correction separately. |
| Story | Story receives committed source events and validates follow-up availability when it proposes an actionable beat. | A grounded narrative beat can be informational. Evaluate playable beats by who can act, when the choice expires, which branches resolve and which outcomes change the world. |
| God | Tools, scope and proposal counts are bounded by a task contract. Ordered stages can resume from persisted evidence. Turn quality uses the actual iteration budget. | Evaluate reachability, reuse, terminal state and duplicate proposals after restart. Score authored content by its use in the world as well as its admission. |
| Director | Counts identify candidates. A plan completes only after a linked result, and the LLM policy receives the committed outcome boundary before choosing again. | Counts and selected context properties still dominate prioritization. Evaluate whether plans respond to unmet claims, repeated failures and prior outcomes. Compare the LLM policy with deterministic planning over the same seeds. |

Use deterministic replay for authority, conservation, deadlines and causal
links. For model-driven behavior, run repeated remote trials against matching
seeds and context variants, and inspect decisions, traces and committed world
outcomes. Measure success rate, repeatability and token use. The two-world
supply regression demonstrates a bounded NPC loop. It does not measure model
preference, long-horizon playability or spontaneous adversarial choices.

### Full request budget and outcome evaluation

The runtime now budgets the complete provider request. It removes old tool
exchanges in groups, retains the latest exchange with its matching results,
and puts the active structural stage in the protected decision section. When
the remaining budget cannot fit the required evidence, the runtime records a
`context_budget` rejection before calling the provider. The audit records
request input, native tool schema size, output reserve and omitted history.

For ReAct turns with the same world snapshot and tool schemas, the next
request extends the prior input prefix and puts current feedback last. If
the cached form exceeds the budget or the visible world state changes, the
engine sends the fresh compact form. Cache hit rates use only token counts
for which the provider returned cache usage. Repeated outcome trials reuse
one provider session per world. The provider routes the requests and owns
the cache.

`scripts/verify_agent_outcomes.py` runs Windward and Tidebound in fresh
instances with and without unmet relief. It reports the visible allocation
choices, trace grade and outcome separately. The outcome grade requires the
same execution to complete on time and commit the expected resource changes.
Each scenario also checks the stock and downstream world conditions. It
measures how the agent responds to a change in the valid choices. Autonomous
preference among competing valid choices requires repeated real-provider
trials. This first scenario suite evaluates allocation decisions.

- [Anthropic on context engineering and selective retrieval](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)
- [Anthropic on memory retrieval and consolidation](https://www.anthropic.com/research/components-of-agent-memory)
- [Anthropic on bounded workflows and agents](https://www.anthropic.com/research/building-effective-agents)
- [OpenAI on agent evals](https://developers.openai.com/api/docs/guides/agent-evals) and [Anthropic on evaluating agent traces and outcomes](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)
- [Park et al. on observation, reflection and planning in simulated characters](https://arxiv.org/abs/2304.03442)

## Context criteria

Each session keeps a stable protocol and tool schema at the front, followed by
the current dynamic view: world time, the actor's state and goals, drives,
relationships, scoped knowledge/memory, recent outcomes, and only executable
behaviors. The view must state that Platform owns costs and state changes. A
behavior description must expose its purpose, requirements, target, time, cost,
impact, success, and failure in plain language. Internal references are for
Platform validation; model-facing choices use visible names.

The behavior view keeps `when` and `requires` when their wording communicates
different information. If a configured `purpose` exactly repeats `summary`, or
`requires` exactly repeats `when`, the compiler renders the sentence once.
Nested action costs are collapsed before rendering. These are clarity and
cost controls, not permission boundaries.

## Dialogue cost and quality

The first 967-minute remote baseline recorded 396 instant dialogue sessions
and 2.30M dialogue prompt tokens. The messages were fluent, but the same
topics (ration shares, trade ledgers, and river contamination) were reopened
every few minutes with little new information. The cause was a thread key based
on model-controlled topic text combined with a short retry window: changing a
few topic words reset the round budget while each reply received the full
planning view.

The runtime now treats an interaction as a bounded thread:

- dialogue replies use a compact context containing the incoming message,
  nearby participants, relevant facts and testimony, and open offers;
- a dialogue reply can only send one short message or wait; it cannot start an
  unrelated long behavior;
- one participant pair shares one dialogue chain throughout the configured
  window, even when the model rephrases the topic; trade offers use a separate
  pair chain;
- repeated or near-identical messages within the thread window are suppressed;
- a thread closes after three exchanges and can reopen only after the
  configured window; ordinary direct-message retries use the configured
  120-minute cooldown in `interaction_policy`.

The Platform still records every submitted exchange, so suppression affects
LLM work only and does not delete world history. `SimulationReport` exposes
`interaction_replies_suppressed` and `interaction_threads_closed`; compare
those with `llm_call` usage when reviewing a long run.

Communication BTs now require the actual topic and opening message before the
activity starts. The resulting `dialogue.completed` event and testimony memory
contain that text; an empty topic-only interaction is rejected at the behavior
parameter boundary. Instant replies may state only incoming, known or retained
facts. Rumor seeds carry explicit fact text and confidence so a bare rumor name
cannot be expanded into invented visual details. `send_message` records words
and promises only: payment, delivery, ownership, contracts and item exchange
remain pending until a Platform transaction confirms them.

Event-driven Story/God sessions retain the exact source event ID through the
evolution task and Runner. A real-model regression caught a dropped task data
envelope that made Story interpret the latest unrelated relationship event as
the source of a bell-installation task. The Runner now accepts the store's
flattened task projection, and tests assert the source event type, actor and
summary in the final AgentView.

Completion-token spikes have a separate cause from prompt size. Remote models
could choose an unbounded completion budget, so one decision might spend
thousands of tokens on internal planning and several read-tool calls before
returning one small choice. Budgets now belong to Agent roles in
`~/.sim2/llm.json`; `max_completion_tokens` and `max_agent_iterations` are one
setting under each role, while providers describe only connection and model
details. The gateway sends the resolved role's `max_tokens` value. `llm_call`
records prompt, completion, latency, and tool iteration counts, so a review can
distinguish a large dynamic context from a verbose model response. A provider
requiring a different transport field can override it through `extra_body`.

Decision, Story, and God sessions cap ordinary read tools through the role
configuration (the example gives NPC decisions three focused reads and five
ReAct turns; world reviewers may use a larger evidence budget). The decision
schema is narrowed to intent and wait after the NPC read budget is reached, and
reworded reads of the same scene/entity/behavior are rejected with guidance to
use the existing result. This keeps an Agent from reopening the same scene for
six iterations and prevents tool results from being resent as an ever-growing
conversation. A rejected read budget is fed back as a normal Platform error,
after which the model must use the facts it already has or submit `wait`/one
intent.

Event-driven Story and God tasks now receive their exact validated source in
the initial view and expose submission tools only. They cannot spend extra
rounds re-reading the event from unrelated projections. Scheduled weekly or
monthly world reviews have no single bound source and retain their configured
read tools for targeted inspection.

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

The controlled world audit then advanced one Agent from world minute 0 to 241.
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
