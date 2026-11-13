# World authoring and WorldBuilder

A sim2 world has two forms:

1. **World template** — one YAML object used by a person or the creation-time
   WorldBuilder Agent.
2. **World package** — the reviewed multi-file YAML source imported by
   `sim2 init` into one independent SQLite instance.

The package is initial material. After initialization, SQLite is the runtime
source of truth. The original package and its `config_version` remain an audit
reference; runtime evolution writes validated definitions and state only to the
instance database.

Wheels include Starter Village as the `worlds.starter_village` package and the
compact Three Lanterns Town validation world as `worlds.three_lanterns_town`. A fresh
installation can therefore run `sim2 init --instance-id my-world-001` without a
source checkout. Additional bundled worlds use sibling packages under `worlds/`;
`--world <id>` selects one, while `--config <path>` initializes a reviewed local
package.

## Authoring commands

```bash
# Print the exact JSON Schema supplied to WorldBuilder.
sim2 world-schema > world-template.schema.json

# Build a package from an existing unified YAML template.
sim2 build-world \
  --template examples/orbital-world-template.yaml \
  --output worlds/orbital_relay

# Ask the configured Beam provider to draft, validate and submit a template.
sim2 build-world \
  --brief-file my-world-brief.txt \
  --provider remote_beam \
  --output worlds/my_world \
  --audit-output artifacts/my-world-builder-audit.json

# Validate references without creating SQLite.
sim2 validate-world --config worlds/my_world
sim2 describe-world --config worlds/my_world

# Create a fresh instance only after review.
sim2 init --config worlds/my_world --instance-id my-world-001
```

The deterministic long-run verifier accepts any reviewed package. For example,
the three-character world can be checked without a network model:

```bash
python scripts/run_long_simulation.py \
  --config worlds/three_lanterns_town \
  --instance-id three-lanterns-deterministic \
  --minutes 10080
```

WorldBuilder normally uses the provider and budget from
`roles.world_builder` in `~/.sim2/llm.json`. Pass `--max-completion-tokens`
only for a deliberate one-run override.

`build-world` never edits an existing instance. With `--brief`, WorldBuilder
must first call `validate_world_template` and then submit the exact validated
object. Platform rejects changed or invalid submissions. The command writes
human-editable YAML before any optional `--instance-id` bootstrap.

Validation checks executable condition paths, declared attributes and world
properties, required effect fields such as `delta`, entity types, Action/BT
references, and generic context resolvers. It then creates a disposable SQLite
instance and executes one initial behavior per individual. When that smoke test
cannot start a behavior, its feedback names the failed BT conditions or Action
preconditions so WorldBuilder can correct the actual cause instead of guessing.

## Unified template

The template uses these top-level sections. The machine-readable source is
`sim2 world-schema`; this table explains the intent.

| Section | Purpose |
| --- | --- |
| `template_version` | Version recorded as the package configuration version. |
| `world` | ID, title, premise, genre, locale, clock, seed and initial world properties. |
| `attributes` | Every numeric state or need, including direct meaning, range and default. |
| `locations` | Place nodes, visible names, tags, safety, optional map position and route IDs. |
| `resources` | Finite or regenerating world stocks at configured locations. A stock is an entity with a physical `location_id`; optional `storage_scope`, `owner_id`, and `access_policy` keep separate settlements or institutions from sharing it accidentally. |
| `objects` | Machines, documents, hazards, items and other non-location entities. |
| `factions` / `relationships` | Initial social structures and typed links. |
| `agents` | Identity, goal, beliefs, needs, capabilities, state, inventory and initial BTs. |
| `capabilities` | Named qualifications and proficiency dimensions. |
| `actions` | Atomic execution contracts: target, conditions, duration, costs, effects and outcomes. |
| `recipes` | Input-to-output transformations used by configured Actions. |
| `behavior_trees` | Agent-readable choices composed from registered Actions or subtrees. A behavior created during runtime must be assigned to at least one existing character before it can enter the executable catalog. |
| `rules` | Generic motivation, minute effects, navigation, interaction, memory and world policies. |
| `evolution` | Optional cadence and event wake declarations. Use the compact `cadence`/`wakes` form below; the runtime compiles it to its internal policy. |
| `story` | Optional evidence-triggered beats and registered BT unlocks. |
| `player_template` | Optional entry location, origins and initial knowledge. |
| `initial_events` | Opening facts recorded at configured world minutes. |
| `prompts` | Optional world-owned runtime prompt policies. |

`story.emergent_source_event_types` declares which recorded events may seed a
fallback emergent beat when no task-bound source is supplied. A
`story.narrative_claim_guard` may list locale-specific claim terms plus the
event types or BT tags that prove a later movement, meeting, delivery or public
statement. Runtime Story tasks always bind their exact causal event IDs first;
these fields are a configurable validation fallback, not a source guess.

The recommended authoring form is deliberately small:

```yaml
cadence: {reflection: 1d, god: 1w}
wakes:
  - {"on": research.completed, for: god, count: 2, every: 1w}
  - "on": cistern.reviewed
    for: god
    count: 3
    tools: [propose_institution, wait]
    purpose: Repeated reviews justify deciding whether a public institution is needed.
    result: Propose one evidence-backed institution, or wait when the evidence is insufficient.
  - {"on": institution.activated, for: story}
```

`on` is a persisted event type, `for` is `god`, `story`, or an individual
consumer, `count` is the number of new events needed, and `every` is the
cooldown (`m`, `h`, `d`, or `w`). `tools`, `purpose`, `result`, and optional
`definition_type` constrain that one wake when a particular kind of definition
is required. For example, ledger review may require `definition_type: behavior`,
which prevents God from substituting a signal rule or unrelated capability.
An optional `assign_to: [梅岚]` makes the expected executor explicit and ensures
the new behavior enters that character's next decision catalog.
A wake that reviews `research.completed` (or `research.progressed`) evidence also shows
the God how to submit a research source (`propose_entity` with a `document`,
`research_discoveries`, `research_required` and `research_assignees`). The wording of
`result` does not switch that note on, so a wake may list a research source next to
other levers such as `revise_institution` without steering the choice.
These fields are optional
guidance for that one wake. Bootstrap compiles this into the internal
`schedule`, `event_wake_policy`, `event_groups`, and evidence selector fields.
Authors do not need to repeat `external_event_types` or write the same event
selector in every proposal.

Only events emitted by a runtime subsystem are treated as external by default.
For an event supplied by an extension rather than an Action, BT, rule, or
runtime subsystem, add `external: true` to that wake. A misspelled ordinary
event therefore fails validation before the world is created.

`god` and `story` are configurable consumers, not mandatory background clocks:

- **Story** receives validated events and turns them into a grounded fact,
  character goal, next chapter, or unlock of a behavior already registered in
  the world. It does not invent a resource result or activate an institution.
- **God** receives accumulated world evidence and may create any supported
  world content that the verified situation needs: an agent, location,
  faction, object, empty resource node, institution, relationship, rule,
  attribute, world-property definition, capability, Action, behavior tree, recipe, or
  narrative event. Platform validates names, references, conservation, and
  executable consequences. A runtime behavior proposal must name at least one
  existing character in `assign_to`; a new `agent` entity must reference an
  existing location and executable behavior and is initialized from the
  world's configured attribute, need, and capability defaults.

Both roles receive a bounded `<world_history>` section with recent validated
events (minute ticks are skipped, and a wake's own source events appear once, as
`<triggering_evidence>`). They can use the read-only `query_history` tool for an older causal
slice (`event_type`, visible `actor`, `contains`, `since_minute`, and
`until_minute`). The tool returns visible names and compact details without
internal IDs. Add `query_history` to a wake's `tools` list when that event
contract needs historical lookup; a submission-only contract can leave it out.

An entry with only `on` and `for` wakes the consumer immediately for that event.
`count` and `every` are a gate for repeated evidence: the platform waits until
the count is reached, then applies the optional cooldown to that evidence group.
They do not delay a direct Story response and there is no hidden daily God call.
Omit a consumer entirely when a world does not need that evolution role.

Actions and behavior trees may also declare `wake: [story, god]` on the event
they produce. That producer declaration is the narrow opt-in for a one-off
event; the `wakes` consumer policy still decides grouping and cooldown. For
events emitted by runtime infrastructure, define the consumer in `wakes`.

Use `on: 'rule:*'` for a direct task-prefix wake when a world rule should wake
the same consumer. The runtime expands that wildcard into its task-prefix
allowlist; ordinary event names remain exact and countable.

### Delivery, novelty and stagnation

These optional fields keep an evolving world from settling into a loop that
no consumer notices:

- `manual_only: true` delivers the event to that consumer **only** through an
  explicit producer declaration (`wake:` or `outcome_events` on an Action or
  BT). Platform's own publication of the same event (for example an
  institution allocation it settled) is the automatic path and does not wake
  the consumer one event at a time. A manual-only event is not compiled into
  `direct_event_types`.
- `distinct_by: [source_id, stage_id]` makes `count` count only **novel**
  evidence. The listed keys (payload fields, or the `actor_id` and
  `event_type` columns) identify what is new; a combination already counted
  never counts again, so a repeated outcome needs no calendar `every`
  cooldown. The last 512 combinations are remembered per group. A wake with `distinct_by` carries its matching events
  as `<triggering_evidence>`, like any other contract wake.
- `stagnation: {window: 1d, max_interval: 1w}` turns on the derived
  `world.stagnation` event. Once per window Platform compares each living
  character's cumulative set of executable options (behaviors plus parameter
  options such as configured research sources) with the previous window; the
  first window only records the baseline. Any growth resets the counter. When
  nothing grew, the event is published after 1, 2, 4, ... stagnant windows,
  never farther apart than `max_interval`, and lists per character what they
  can do now and which conditions block the rest. A blocked Action names the
  failing check in its own terms: the unmet precondition, the exhausted
  resource, or the pool and policy minimum of an institution allocation
  (`Action settle_institution_allocation cannot start: the pool holds 1.5 but
  the policy minimum needs 3`). The world decides who
  receives it, for example
  `- {"on": world.stagnation, for: god, tools: [...], purpose: ..., result: ...}`;
  validation requires `stagnation.window` for any wake on `world.stagnation`.
  Each evaluated character costs one decision view, which grows with the
  population, so a window evaluates at most `max_characters` of them (default
  12, the number the event lists; at most 64). A larger roster is walked by a
  deterministic rotating cohort in id order, each character keeping its own
  cumulative options, and a character met for the first time only records its
  baseline instead of counting as growth. In a roster that fits in one cohort
  a newly appeared character does count as growth.

A configured Story beat whose trigger is a source event (for example
`research.completed`) is also offered in the `civilization.milestone` session
that source produced: Platform reads the originating event type from the
stored milestone, never from the model. While such a beat applies, Story
cannot submit an emergent beat in its place. Activating a configured beat
always records the `story:<beat id>` knowledge key, so a behavior guarded by
`actor.knowledge.story:<beat id>` becomes executable.

### Proposals must be reachable

Platform rejects God content that no character could ever use, inside the same
transaction that would have stored it (nothing remains after a rejection):

- A research source whose `research_assignees` names an unknown character is a
  schema rejection. One whose assignee has no research choice, or sits at a
  location the assignee cannot reach, is rejected with
  `failed_check: entity_unreachable`; the answer lists the characters who can
  research now. The check reads each assignee's own decision view.
- A `recipe` definition is rejected while no registered Action can craft one
  (`costs.reserve.recipe_inputs` or `duration.mode: recipe_duration`).
- An institution's `policy.allocation` (`minimum_per_subject >= 0`,
  `contribution_share` between 0 and 1) and `ledger.selectors` (each naming
  `event_types`, `behavior_ids` or `action_ids`; a selector without a source
  would credit every event) are validated when proposed and when revised.

An active institution can follow its own results. God's `revise_institution`
replaces `policy` and/or `ledger.selectors` once the institution has recorded
a ledger entry or applied an allocation since its latest terms. The ledger
`period` cannot change, and each revision needs fresh operating evidence.
Platform emits `institution.revised` (usable in `wakes`) and the God view shows
the current `[terms: ...]` next to each active institution.

Keep loops open in content, not in code: declare `resource_regeneration`
(`resource_id`, `every_minutes`, `amount`, `cap`) for materials characters
keep consuming, and gate consumption behaviors with the world's own threshold
(for example `actor.need.hydration <= context.hydration_alert_threshold`) so
an item is not spent when its effect would be clamped to nothing.

The evidence check is structural provenance, not a social-trust score: it asks
whether the named event was actually persisted and whether the threshold was
met. A model does not need to calculate credibility or copy selectors into an
event-triggered institution proposal; the platform supplies the triggering
contract and performs the count. Explicit internal policies are still checked
before a SQLite instance is created.

Triggers and evidence groups can add an `agent_contract` without embedding
world logic in Platform:

```yaml
agent_contract:
  allowed_tools: [propose_institution, wait]
  purpose: Repeated verified work needs a public institution decision.
  required_result: Create one evidence-backed institution proposal or wait only when evidence is insufficient.
  evidence_sources:
    - {event_types: [work.completed], behavior_ids: [public_work]}
  minimum_evidence: 3
```

The runtime counts these persisted selectors and shows the count to the Agent.
The Agent still chooses the institution name, purpose, policy and later
continuation. A signal-only rule cannot satisfy a contract that requires a
structural proposal.

The same boundary applies to other content types. God may create a person,
place, faction, object, mechanic, or narrative situation, but cannot award an
actor a capability, fill a shared resource, or write a personal/social outcome
without a behavior result. Those rules keep creation open while preserving a
causal audit trail in Platform.

An object or resource may expose research content declaratively:

```yaml
research_discoveries:
  - id: researched:source:first_reading
    fact: "The first measurement identifies a seasonal pattern."
    required: 2
  - id: researched:source:revision
    fact: "A later measurement narrows the pattern to the dry phase."
    supersedes: researched:source:first_reading
```

The generic research BT/Action selects the first active stage whose fact has
not been recorded for the researcher. A source may set `research_required` in
its entity data (or `initial_state`) to require the same number of work units
for each stage; an individual stage may override it with `required`. If a
source omits both values, the package-level `rules.research_default_required`
is used. The number is data owned by the source, never a hard-coded Action
constant.

The generic BT accepts one visible `source_id` parameter and derives the
source location from SQLite. A package may give an opening source a
`parameter_defaults.source_id`; later sources must be assigned by persisted
data, for example `initial_state.research_assignees: [agent_id]`, or by a
Story/God continuation that records the target for the audience. This keeps a
research object from creating a source-specific BT. The same registered BT
can travel to and research any assigned source, including a source created
after world initialization.

A completed stage wakes only the configured event consumers; it does not create
a permanent source-specific branch in Platform. Story turns the validated
result into a concrete audience goal and an exact registered follow-up when
one is available. God receives only its configured accumulated evidence and
may create a new source, stage, entity, rule, or genuinely new capability
through the normal validated proposal tools. Neither Agent may claim the
future fact before a character performs the generic research BT.
`active`, `superseded`, `obsolete`, and `disabled` are data lifecycle values,
so a world can revise or retire a result without deleting its audit trail.

IDs are internal references and use stable `snake_case`. Labels, summaries and
prompts are short natural language written for people and Agents. References
must resolve inside the same template. The validator rejects missing routes,
locations, Actions, BTs, capabilities and undeclared state attributes.

`world.world_clock.start_minute` (or the package setting equivalent) sets the local
clock at world minute zero. Optional `calendar.day_phases` entries declare
named ranges such as `day`, `dusk` and a range that crosses midnight. Action
and BT conditions can then use `context.day_phase`; the Runtime computes the
phase from configuration and contains no setting-specific day/night rule.

An Agent `knowledge_seed` may be a compact string or a structured fact with
`id`, `fact`, `source_type`, `confidence` and `claim_status`. Use structured
facts for rumors, promises and uncertain reports. An opaque label proves only
that the label is known; it must not stand in for details the Agent has never
observed.

## Attribute rule

No character attribute is supplied by Platform. A world declares all of them.
Each number has a direct meaning: a higher value must mean more of the named
thing. For example, `integrity=90` means high integrity and `signal_clarity=90`
means a clear signal. Avoid reverse values such as “damage remaining” when the
display label implies health.

State attributes declare `cost_mode`:

- `decrease`: action cost consumes the value, such as charge or stamina.
- `increase`: action cost accumulates the value, such as heat or exposure.

The rules file names the vital and capacity attributes when the world uses
them. A science-fiction world can use `integrity` and `charge`; a biological
world can use different terms. Platform does not inject `health`, `energy`,
`satiety`, food or village concepts.

Rules may expose package-owned constants through `context_values` and generic
inventory selection through `item_groups` plus `context_resolvers`. For
example, Starter Village defines its eating threshold as a context value and
resolves `item_id` to the first carried item in its configured `food` group.
The Runtime sees only a named context value and a generic
`first_inventory_item` resolver; another world can use the same mechanism for
oxygen canisters, batteries or ritual tokens without adding Python code.

`rules.perception` is the information boundary between SQLite truth and an
Agent observation. `visible_fields` and `visible_state_fields` list properties
that a nearby inspection may reveal. The optional `types` map overrides those
lists for any configured entity type, and `global` defines the bounded view
used by world-level review. Properties absent from these lists remain private.
A merely known but distant entity exposes identity and public description, not
its live state. This policy lets a new world add arbitrary properties without
adding projection code or leaking complete entity rows to a model.

Character attributes describe the individual. Environmental, institutional,
vehicle and facility values belong to world properties or entity state. For
example, a diver may have `suit_integrity` while a station has
`hull_integrity`; the same number must not appear in both scopes.

Civilization progress definitions may declare `evidence_event_types`,
`evidence_behaviors`, or precise `evidence_sources` clauses combining an event,
behavior, Action and entity. `evidence_value` is the contribution of one unique
matching source and defaults to one. Story and God choose a matching category;
Platform owns the number and rejects reused or cross-domain evidence.

## Action and BT rule

An Action is executable mechanics. It declares:

- parameters and target requirements;
- machine-evaluable preconditions;
- duration or route-distance mode;
- reserve and per-minute costs;
- typed effects;
- success and failure outcomes;
- interruption and interaction policy.

Recipe inputs default to `scope: agent`, which means the material must be in
the individual's carried inventory. Use `scope: world` only for an explicitly
shared world stock and `scope: auto` only when either carried or shared stock is
intended. A resource-node Action uses its selected world resource directly;
having an item with the same name in personal inventory never turns later
gathering into a no-op.

Knowledge-dependent remediation must name its evidence as a machine
precondition, for example `actor.knowledge.researched:source:finding`. Plain
description text does not unlock an Action. A source-dependent Action also
requires the configured source at the actor's current location; travel belongs
in the surrounding BT.

A BT is the choice shown to an individual Agent. Its `agent_label`, summary,
requirements, estimated time, costs and likely effects are compiled into short
model-facing text. The model never receives the raw tree. Selecting a BT only
submits intent; Platform resolves targets, reserves resources and advances its
Actions minute by minute.

When a BT needs content chosen by the Agent, declare `agent_parameters` on the
BT. Each entry states its type, whether it is required, its length bound and a
plain-language description. The compiled behavior shows these requirements;
Platform rejects a missing or invalid value before starting the activity. For
example, a communication BT requires both a topic and the actual opening
message. A recorded message is testimony only. Inventory and currency move
only through an accepted transaction intent.

## Package files

The compiler writes a stable package layout:

```text
manifest.yaml
setting.yaml
world_state.yaml
attributes.yaml
locations.yaml
objects.yaml
factions.yaml
capabilities.yaml
agents.yaml
actions.yaml
recipes.yaml
behavior_trees.yaml
rules.yaml
evolution.yaml
story.yaml
opening_events.yaml
player_template.yaml
prompts/*.yaml        # optional
```

Prompt files are optional. Runtime supplies a compact world-neutral protocol
for Decision, Reflection, Story and God roles when a package omits them. A
package prompt may replace that wording, while tool schemas and Platform's
state-mutation boundary remain enforced by code.

`manifest.yaml` lists every imported file. `sim2 validate-world` checks the
whole package before SQLite exists. Every actual execution mechanism also has
focused runtime tests; validation alone does not prove that a world is
balanced, so a new content pack should still run a deterministic week and a
real-model audit before release.
