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

The current review found two presentation issues to fix later: some behavior
views repeat `purpose`, `when`, and `requires`, and nested action costs can be
shown as duplicate lines. These are clarity defects, not permission defects.

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
result is visible in a later world review. A signal cannot claim to gate or
prioritize a behavior; that requires a typed `behavior_constraint` effect. The
Platform also rejects a second signal for the same event/follow-up pair and a
same-role signal chain sourced from `world.rule.*`, preventing a notification
from recursively generating notifications without a behavior result. Story
context exposes exact character names from recent event participants; one
emergent beat is recorded per validated evidence event.
