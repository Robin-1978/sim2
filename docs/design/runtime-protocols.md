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
