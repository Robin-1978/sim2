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
`reported`, `promised`, and `inferred` remain explicitly weaker claims.
Dialogue is stored as reported testimony with `verification_status=unverified`
and a configured credibility prior based on the listener's trust, the
speaker's reputation, and optional content-defined traits. Credibility is a
prior, not a truth result: later observation can mark a claim confirmed,
disputed, or false. Similarity is a
deduplication hint, not an automatic merge, and long-term memory has no
time-only decay.

## World evolution

God and Story proposals are stored with a typed expectation and an observation
window.  Evaluation matches the expected property value, relationship/faction
state, or configured rule follow-up event.  An unrelated event cannot make a
proposal successful.  Narrative-only proposals finish as `no_observed_effect`
unless a configured consumer creates a real state result. Event wake policy is
content data: authors declare it as compact `wakes` entries (`on`, `for`,
`count`, and optional `every`); bootstrap compiles those entries to runtime
event groups. Routine results may accumulate into a group requiring N evidence
records before God is called, while a configured public incident may use N=1.
There is no implicit daily God call. The next God or Story view
receives the expected/observed comparison and may wait, revise, or propose a
bounded successor.

God can create any supported content type that the validated situation needs:
agents, locations, factions, objects, empty resource nodes, institutions,
relationships, rules, attributes, world-property definitions, capabilities, Actions,
behavior trees, recipes, and narrative events. The proposal vocabulary is
intentionally broader than institutions and behaviors. Platform remains the
authority: it validates references and executability, rejects code, preserves
resource conservation, and requires behavior outcomes for personal gains and
stocked resources. Story continues to consume validated facts and organize
them into goals and chapters; it does not silently create the structural
content that God owns.

An evolution trigger or evidence group may also declare an `agent_contract`.
It can restrict the allowed tools and state the required structural result,
for example allowing only `propose_institution` and `wait` when repeated public
work must become a new institution. The view includes the exact persisted
evidence selectors and the observed match count. This keeps God's creative
choice open while preventing a signal-only rule from silently replacing the
required institution or settlement behavior.

An event-driven Story session also carries the exact source event IDs through
the tool executor and intent submission. Platform never replaces that source
with whichever event happened most recently. Optional emergent-source types,
narrative claim terms, valid follow-up event types, and follow-up BT tags live
in `story.yaml`; a different content pack can replace them without Python
changes.

History is part of both world-level Agent views. Story and God receive a bounded
`world_history` projection containing recent validated events, visible actor
names, outcomes, institutions, definitions, and resource deltas. It is causal
context, not a transcript: completed work remains visible so the next session
can continue it instead of recreating it. When an older slice is needed, the
read-only `query_history` tool filters persisted events by exact event type,
visible actor, keyword, and world-minute range. Its result removes internal IDs
and never grants mutation authority. A wake contract may omit the read tool to
keep an event review submission-only; ordinary Story/God sessions expose it.

Social institutions follow the same path: no contribution metric exists until
NPC behavior produces the configured evidence threshold. Story may connect that
evidence to a continuing chapter, but only God may propose and activate the
institution. The proposal declares evidence selectors and a minimum count;
Platform checks persisted events before accepting it. After activation,
configured selectors append verified ledger entries from real behavior events;
allocation is a later declarative settlement and never grants resources at
proposal time.

Research follows the same open-ended event protocol. A source can expose one
legacy `research_discovery` or a list of independently tracked
`research_discoveries`; each completion emits the stage key and the exact
source event. Completing a stage does not mark the whole source as exhausted
while another active stage remains. God may later create a new source or
research behavior through the normal declarative entity/BT proposal path, so
the future research sequence is not a prewritten technology tree. A new fact
may declare `supersedes` and is stored as the current `active` claim while the
old claim remains auditable as `superseded`; `obsolete` and `disabled` stages
are retained for replay but are excluded from executable research choices.

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

Evolution task failures retain their source evidence and a retry count. A
semantic or tool rejection uses increasing simulated-minute backoff; a
`budget_exhausted` world review starts with a one-day delay and backs off up to
one simulated week. Completing the task clears the retry metadata. This keeps
one stale research or entity event from waking God every hour while preserving
recovery when a later model response can use the platform feedback.

Provider adapters normalize model transport envelopes before the Agent Engine
receives a response. For example, the LongCat adapter converts a bare
`{"request_behavior": {...}}` content object into the shared intent shape.
Agent logic only handles the shared schema; it does not contain provider or
model names. Adapter normalization is covered by provider compatibility tests.

Model routing and spend belong to `~/.sim2/llm.json`, not to a world package.
Each entry under `roles` combines the role's `provider` with its
`max_completion_tokens` and `max_agent_iterations`. Providers contain connection
and model settings only. The example uses a 512-token completion bound and
three ReAct turns for decisions, while WorldBuilder can request a larger
budget. This gives users one place to inspect both model choice and spend for
each Agent role, and keeps one world's YAML from silently changing another
world's LLM cost.
