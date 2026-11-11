# Runtime Core Protocols

This document freezes the four boundaries that keep a configurable world
causal and replayable.  The Platform is the only writer of world state;
agents submit intents and proposals.

## Tick and commit

`Platform.tick_snapshot()` returns `world_minute`, `world_revision`, and the
current `agent_revision` for each character.  A simulation minute is one
SQLite transaction: minute effects, active process progress, result events,
rule signals, proposal evaluation, and the new clock value commit together.
The transaction reserves the next world revision before writing events, so all
events in one commit carry the same revision.  Rollback leaves both state and
revision unchanged.

An Agent decision may use a stale world revision because another character's
unrelated work should not cause a retry.  Its own revision is a strong
boundary: if location, inventory, status, or another personal projection
changed after the view was compiled, Platform rejects the intent with
`stale_agent_revision` and the Agent receives a fresh catalog.  Entry
conditions, permissions, reservations, and targets are always rechecked at
the commit barrier.

The store uses WAL with `synchronous=NORMAL`. Large worlds disable SQLite's
threshold auto-checkpoint and run bounded passive checkpoints on a separate
connection, so a 1,000-person minute does not block while copying the WAL.
Snapshot, restore and close wait for maintenance checkpoint work; close then
performs a final truncate checkpoint. A process or OS crash leaves the database
consistent. A host power loss may lose the latest WAL commit, so operators use
snapshots as explicit recovery points.

## Activity lifecycle

One primary activity is represented by one `bt_instance` (`execution_id`).
Its `data_json` contains the validated BT snapshot, expected outcome, decision
summary, current node, and final outcome.  Each Action is a child
`action_instance` with elapsed/remaining minutes, reservations, and the exact
Action definition snapshot and version used at start.  A later definition
change affects new activities only.  The lifecycle is:

`intent received → BT executing → Action running → Action completed/failed →
BT completed/failed/interrupted → reflection input`.

An active process survives `run(N)` boundaries and resumes after restart from
these rows; an unfinished process is not silently cancelled.

## Outcome and learning

At BT completion the Platform writes `outcome.evaluated` and the terminal BT
event in the same transaction.  The outcome records expected results, actual
state, numeric/state delta, failure or interruption reason, and causation IDs.
Execution cursors and the previous outcome are excluded from the projection;
one result cannot recursively embed the entire prior history.
Reflection may retain a short experience only when it is new and useful.
`observed`, `confirmed`, and `failed` claims must resolve to a recorded event;
`promised` and `inferred` remain explicitly weaker claims.  Similarity is a
deduplication hint, not an automatic merge, and long-term memory has no
time-only decay.

## World evolution

God and Story proposals are stored with a typed expectation and an observation
window.  Evaluation matches the expected property value, relationship/faction
state, or configured rule follow-up event.  An unrelated event cannot make a
proposal successful.  Narrative-only proposals finish as `no_observed_effect`
unless a configured consumer creates a real state result.  Event wake policy
is content data: routine results may accumulate into a group requiring N
evidence records before God is called, while a configured public incident may
use N=1.  There is no implicit daily God call.  The next God or Story view
receives the expected/observed comparison and may wait, revise, or propose a
bounded successor.

An event-driven Story session also carries the exact source event IDs through
the tool executor and intent submission. Platform never replaces that source
with whichever event happened most recently. Optional emergent-source types,
narrative claim terms, valid follow-up event types, and follow-up BT tags live
in `story.yaml`; a different content pack can replace them without Python
changes.

Social institutions follow the same path: no contribution metric exists until
a person proposes an institution and the world activates it.  After activation,
configured selectors append verified ledger entries from real behavior events;
allocation is a later declarative settlement and never grants resources at
proposal time.

## Breaking schema and audit boundaries

The runtime is still in development and does not silently preserve obsolete
world-specific aliases. A content pack must declare package values under
`rules.context_values`, inventory groups under `rules.item_groups`, and
resolvers under `rules.context_resolvers`. A food-like resource is only one
possible group; the runtime has no `food_items` or `first_food` vocabulary.
Actions refer to a resolver name such as `context.item_id`, so another world
can use cells, medicine, fuel, or any other configured group without Python
changes.

Action and BT definitions are immutable snapshots once a process starts. The
active `action_instance` stores its definition version, while new processes see
the newer registry version. `capability.evidence` is evidence only; an Agent
must submit a bounded capability update backed by that evidence. Ordinary
behavior completion never grants arbitrary capability XP.

World progress and evolution consumers use the same validated source-event
window. A progress update carries `source_event_id`; an event-group wake
counts matching persisted events before creating a God/Story task. The final
review does not rewrite failed Agent quality checks. Historical provider
errors, budget exhaustion, weak choices, and unsupported proposals remain
visible in the audit, even when a later code revision can explain them.

Provider cooldown is scoped to the affected Agent role/session. A paused
session can retry at the same world boundary; unrelated active behaviors and
world-minute execution remain resumable. A process restart also requeues any
due evolution task left in transient `claimed` status and records an
`evolution.tasks_recovered` event.

Provider adapters normalize model transport envelopes before the Agent Engine
receives a response. For example, the LongCat adapter converts a bare
`{"request_behavior": {...}}` content object into the shared intent shape.
Agent logic only handles the shared schema; it does not contain provider or
model names. Adapter normalization is covered by provider compatibility tests.
