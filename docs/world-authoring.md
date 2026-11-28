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

For authors who do not want to maintain the YAML by hand, the World Authoring
Web 工作台 is described in
[`docs/design/world-authoring-web.md`](design/world-authoring-web.md). It will
It generates forms and BT graphs from the same `world-schema` and Definition
Compiler used by these commands, then run the same expression, reference,
execution and temporary SQLite smoke checks. It will export a reviewed package;
it will not edit a running instance or introduce a second configuration
language.

Validation checks executable condition paths, declared attributes and world
properties, required effect fields such as `delta`, entity types, Action/BT
references, and generic context resolvers. In an expression, a dotted word
(`context.limit`, `supply.level`) is always a value reference, a bare word
(`pathfinder`) is a label and a quoted string is text; the parser keeps no list
of allowed roots, so a path whose root the world does not provide is reported
as an unsupported condition path instead of being compared as text. It then creates a disposable SQLite
instance and executes one initial behavior per individual. When that smoke test
cannot start a behavior, its feedback names the failed BT conditions or Action
preconditions so WorldBuilder can correct the actual cause instead of guessing.

## Unified template

The template uses these top-level sections. The machine-readable source is
`sim2 world-schema`; this table explains the intent.

### Optional world roles

Omit `evolution.yaml` and its manifest entry for a world driven by resident
decisions, behavior trees and authored rules. To opt in to selected roles, use
the `roles` map in `evolution.yaml`. Omitted names in this map are off.

```yaml
roles:
  story: true
```

Add the Story wake rules for the events the world should follow. God may run
through its own wakes or cadence. Director plans structural work for God, so
enable both `god` and `director` when using it. `roles: {}` switches off all
three. Existing nonempty evolution configurations without `roles` use their
previous behavior. The switches govern Agent execution and task creation.
Authored goals, endings, world rules and NPC control use their own configuration.

### Time-sensitive decisions

For a committed event to prompt a resident to reconsider, put its event type in
`rules.decision_policy.wake_events` and include the resident IDs in `audience`.
An event can include `deadline_minutes`, `claimants`, `active_when` and
`miss_cost`. The resident sees live decisions in the normal AgentView. Only the
audience receives the brief. It clears when the condition closes or time expires.

Routes are authored as topology plus optional edge data. For example:

```yaml
locations:
  - id: harbor
    name: Harbor
    routes: [ridge]
    route_distances: {ridge: 4}
```

Missing edge weights use one distance unit. `rules.route_minutes_per_hop` is
the fallback minutes-per-distance conversion; a package may instead use
`rules.travel.minutes_per_distance`. A character's configured state attribute
(`rules.travel.agent_speed_attribute`) divides the travel time. A transport
entity selected by the Action's configured `transport_parameter` can expose
the configured `transport_speed_field`; when neither actor nor transport has a
speed, the fixed distance conversion is used. Transport speed is absolute by
default; `rules.travel.transport_speed_mode: multiplier` makes it multiply the
actor's configured speed. These are declarative fields,
so a later God proposal can register a vehicle entity or a new speed-bearing
attribute without a Platform branch.

| Section | Purpose |
| --- | --- |
| `template_version` | Version recorded as the package configuration version. |
| `world` | ID, title, premise, genre, locale, clock, seed, optional `ui_text` phrases and initial world properties. |
| `attributes` | Every numeric state or need, including direct meaning, range and default. |
| `locations` | Place nodes, visible names, tags, safety, optional map position, route IDs and optional `route_distances` edge weights. |
| `resources` | Finite or regenerating world stocks at configured locations. A stock is an entity with a physical `location_id`; optional `storage_scope`, `owner_id`, and `access_policy` keep separate settlements or institutions from sharing it accidentally. |
| `objects` | Machines, documents, hazards, items and other non-location entities. |
| `factions` / `relationships` | Initial social structures and typed links. |
| `agents` | Identity, goal, beliefs, needs, capabilities, state, inventory and initial BTs. |
| `capabilities` | Named qualifications and proficiency dimensions. |
| `actions` | Atomic execution contracts: target, conditions, duration, costs, effects and outcomes. |
| `recipes` | Input-to-output transformations used by configured Actions. |
| `behavior_trees` | Agent-readable choices composed from registered Actions or subtrees. A behavior created during runtime must be assigned to at least one existing character before it can enter the executable catalog. |
| `rules` | Generic motivation, minute effects, navigation, interaction, memory and world policies. `travel` can set the minutes-per-distance conversion, an optional agent speed attribute, and the transport speed field. |
| `evolution` | Optional cadence and event wake declarations. Use the compact `cadence`/`wakes` form below; the runtime compiles it to its internal policy. |
| `story` | Optional evidence-triggered beats and registered BT unlocks. |
| `player_template` | Optional entry location, origins and initial knowledge. |
| `initial_events` | Opening facts recorded at configured world minutes. |
| `prompts` | Optional world-owned runtime prompt policies. |

### World-owned presentation text

Clients and public projections do not embed wording that belongs to one world.
`world.ui_text` (`ui_text` in `setting.yaml`) holds the phrases a world wants to
override. Every key is optional and falls back to neutral English:

| Key | Placeholders | Neutral default |
| --- | --- | --- |
| `travel_choice` | `{name}` | `Travel to {name}` |
| `travel_summary` | `{name}` | `Follow the known route to {name}.` |
| `travel_purpose` | none | `Move to another known place` |
| `travel_time` | `{minutes}` | `{minutes} minutes` |
| `travel_impact` | none | `Arrive at the selected location` |
| `world_label` | none | `The world` |

```yaml
ui_text:
  travel_choice: "前往 {name}"
  travel_time: "预计 {minutes} 分钟"
  world_label: 世界
```

The travel phrases word the generic travel action in the player view, and the
game client's map reuses that action instead of composing its own text.
`world_label` names the world as the actor of an event nobody caused in the
observer feeds. Validation rejects unknown keys, empty phrases and placeholders
a key does not offer.

The web client reads optional presentation settings from `setting.ui`.
Use `cover_art` for the entry illustration, `scene_art` to map location
IDs to illustrations, and `entry_action` to name the button for a new
character.
Use paths relative to the world package, such as `assets/cover.webp`,
and list each file in `manifest.files`. Each image can be up to 8 MiB.
Initialization and upgrade copy the images to the save's SQLite database
and publish URLs keyed by their content digest. The client generates an
illustration from the world's palette when a path is absent or fails to load.
Template export preserves files through the optional `world.artwork` map of
package-relative paths to Base64 bytes. Compiling the template writes
the binary files back to the package.

Use `ui.spotlights` to feature one or more `rules.progression` IDs.
Set `deadline_property` to a world property measured in remaining minutes,
or set `deadline_clock_minute` to the cutoff minute of the world day.
The player sees the highest-priority matching goal with executable
choices. During an active task, task progress takes priority. Set the
actual admission and completion conditions in the action definitions,
and encode expiry and any outstanding obligation in world rules.

### Player entry template

`player_template` has one canonical top-level shape. Origins are objects so a
simple world can provide only `id`, while a richer world can add traits and a
starting goal. Player capabilities, starting inventory, initial behaviors and
the controller mode are world configuration, not framework constants:

```yaml
entry_location_id: river_bridge
controller: manual
capabilities: {gathering: 0.5, conversation: 0.5}
inventory: {food_ration: 2, fresh_water: 2}
initial_bt_ids: [gather_water, communicate]
origins:
  - id: courier
    name: 渡船信使
    description: 熟悉河道与住户，愿意把消息带到最远的地方。
    traits: {mobility: 0.7, negotiation: 0.5}
    capabilities: {exploration: 0.4}
    inventory: {food_ration: 1}
    starting_goal: 找到失踪车队的下落
knowledge_seed: [public:river_bridge, public:village_square]
```

`controller: manual` keeps player characters out of autonomous LLM decision
loops; use `controller: llm` only when the world intentionally wants an
auto-driven player avatar. Origin `name` and `description` are shown by the game
client's entry screen. Origin-level `capabilities` and `inventory` override the
top-level defaults per key.

Do not wrap this object in a `player:` key or use `origin_choices`,
`entry_location`, or string-only origin entries. World validation and runtime
entry accept only the canonical form above.

Existing instances snapshot their player template at creation. After changing
`player_template.yaml`, apply the new contract explicitly:

```bash
sim2 sync-player-template --db "$DB"
```

The command reloads the package template recorded in `bootstrap_report` and
backfills missing capabilities, inventory and behaviors for existing players.

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
cooldown (`m`, `h`, `d`, or `w`).
### Continuous evolution instead of a fixed plot

A world does not need a complete pre-written story or a full development tree.
Ship the starting facts, the available survival/public behaviors, and an
evolution policy; let Story and God create the next chapter from validated
events:

- keep `story.beats` empty (or only a seed) and list the event types that may
  become emergent sources in `story.emergent_source_event_types`;
- wake Story on discovery, repair, harvest, conversation and storm events,
  using `count`, `every` and `distinct_by` so the same outcome is not retold
  forever;
- wake God on public works, resource pressure and `world.stagnation`, and add a
  periodic `cadence.god` / `cadence.god_structural` review;
- configure `stagnation: {window: 1d, max_interval: 7d, growth: structural}`.
  Platform compares each character's cumulative executable options and the
  persisted structural surface; when the world stops producing new places,
  verbs, factions or rules it publishes `world.stagnation`, which wakes God to
  evolve the world instead of letting residents repeat a closed loop. A new
  research source is content, not structural growth, and does not reset the
  structural clock;
- do not mark evolution sources `manual_only` unless the world intentionally
  requires an operator to trigger them. Automatic runs and synchronized player
  waits execute the same Story/God tasks;
- make sure the runtime provider selected for play also serves Story and God.
  A failing local model otherwise stops both NPC decisions and evolution.

Actions and behavior trees can still declare `wake: [story, god]` for a
one-off producer event, but the `wakes` policy should remain the main source of
continuous evolution.
 `tools`, `purpose`, `result`, and optional
`definition_type` constrain that one wake when a particular kind of definition
is required. For example, ledger review may require `definition_type: behavior`,
which prevents God from substituting a signal rule or unrelated capability.
An optional `assign_to: [梅岚]` makes the expected executor explicit and ensures
the new behavior enters that character's next decision catalog.
A wake that explicitly asks for research (`continuation: research` or
`definition_type: research`) also shows the God how to submit a research source
(`propose_entity` with a `document`, `research_discoveries`,
`research_required` and `research_assignees`). Research evidence alone no longer
injects that note, so a structural wake can review a discovery without being
steered into adding another archive.
These fields are optional
guidance for that one wake. Bootstrap compiles this into the internal
`schedule`, `event_wake_policy`, `event_groups`, and evidence selector fields.
Authors do not need to repeat `external_event_types` or write the same event
selector in every proposal.

Only events emitted by a runtime subsystem are treated as external by default.
For an event supplied by an extension rather than an Action, BT, rule, or
runtime subsystem, add `external: true` to that wake. A misspelled ordinary
event therefore fails validation before the world is created.

### Cadence contracts

A periodic `cadence.god_structural` task has no event trigger, so it needs an
explicit contract:

```yaml
contracts:
  god_structural:
    tools: [query_history, propose_definition, wait]
    purpose: Add one new playable dimension.
    result: >-
      Add one non-research Action and its bound behavior at an existing
      reachable place. Assign it to an existing character; the behavior must be
      executable now and must produce a new result event.
    definition_type: action
    assign_to: [玛拉]
```

Platform attaches this contract exactly as it attaches an event-triggered
contract. The task shows the required result, permitted tools, assignment and
definition type; a model cannot silently replace it with another research
source. `assign_to` accepts the bound `followup_behavior.assign_to` for an
Action, so the contract enforces the executor without asking an Action to carry
an invalid top-level assignment.

### Director and season plan

`evolution.director` turns the growth ledger into one bounded intent:

```yaml
director:
  cadence: 1d
  window: 3d
  targets: [place, verb, social, rule]
  policy: llm   # optional; default is deterministic
```

At each cadence the Director selects the least-covered target cell (or rotates
when all cells are equal), records an active `season_plan` with the target,
baseline and expiry, and queues a `god_structural` task. It never creates an
entity, definition, rule or event itself. Story and God both receive the active
plan as `<season_plan>`; God must fill that cell, and a research source is not a
substitute. When the target cell count grows above the baseline, or the plan
expires, the next cadence advances the plan.

With `policy: llm`, the ordinary simulation runner asks the separately
configured `roles.director` provider for the target before the tick. A provider
error or an invalid answer falls back to the deterministic policy in the same
minute, so growth never depends on one successful model response.

A generic (stageless) plan contract should bound the unit and require a real
increase in the target cell:

```yaml
contracts:
  god_structural:
    max_proposals: 3
    required_content:
      growth_cell: true
```

`max_proposals` stops the turn after that many accepted proposals. Without it a
broad God contract stays open until the model waits or exhausts its budget.
`required_content.growth_cell` compares the target cell's growth-ledger count
with the plan baseline after every accepted proposal and returns a correction
while the cell has not grown. The God prompt lists what each cell counts
(`social` is factions, relationships and institutions, for example), so the
model can choose a proposal that actually advances the Director's intent.

A budget-exhausted structural session retries at the Director cadence, capped
to half the remaining plan window, instead of the one-day retry used for an
ordinary God task. The plan therefore gets another attempt before it expires.

`limits.max_ready_god_tasks` (default 4) bounds how many ready God tasks one
scheduler pass may execute. Excess tasks stay queued for the next minute, so
one event with several follow-ups cannot fan out into an unbounded batch of
provider calls.

The roles are intentionally separate:

| Role | Owns | Must not |
| --- | --- | --- |
| Director | the next structural target and pacing | write content or state |
| Story | the meaning, chapter and audience-facing continuation | create structure or numeric state |
| God | a place, verb, faction, rule or other structural proposal | narrate the player chapter or bypass Platform |
| Platform | legality, conservation, transactions and execution | invent content |

### Capability bindings and catalog bounds

A behavior should be a **capability**, not a target. Prefer one `Take Resource`
behavior with `agent_parameters: {resource_id: string}` and a node parameter
`context.resource_id` over `Take Water`, `Take Coolant`, and one behavior per
stock. A new resource or place is data: making it visible widens the parameter
options without registering another behavior tree.

```yaml
definition:
  verb: transfer_resource
  parameters: {resource_id: string}
  effects:
    - type: resource_withdrawal
      resource_id: context.resource_id
      amount: 1
      scope: resource_withdrawal
```

`verb` is the stable capability family (`transfer_resource`, `consume`, `move`,
`inspect`, `research`, `craft`, `communicate`, ...). When omitted, the
validator infers it from the effect mix. A new Action that names one literal
resource, item or destination in a targeted effect is rejected even when no
generic Action exists yet: the first specific Action is not a bootstrap
exception. The message tells God to declare a parameter and use
`context.<parameter>` instead. All shipped world packs follow this rule, and
`tests/test_world_pack_capabilities.py` enforces it.

A Behavior may bind a generic Action to a literal target in its Action node
when the Behavior itself is a policy composition, such as `get_food` binding
`withdraw_stock` to `village_food` and `rations`. The Action stays reusable for
another stock. When the Behavior is the capability itself, expose the target as
an `agent_parameters` entry so a new stock widens the visible options instead of
registering another tree.

A withdrawal or take Behavior whose stock is chosen by the Agent must travel to
that stock before the stock Action. Declare the stock location as a derived
binding; Platform resolves it from the same `resource_id` target used for
authorization:

```yaml
agent_parameters: {resource_id: {type: string, required: true}}
parameter_bindings: {resource_location_id: resource_location}
nodes:
  - type: action
    action_id: travel_to
    parameters: {location_id: context.resource_location_id}
  - type: action
    action_id: withdraw_resource
    parameters: {resource_id: context.resource_id}
```

Platform records `resource_location_id` in the admitted intent. The Action
runtime re-checks the stock location at start, parallel start and completion,
so a Behavior that binds a remote stock without Travel is not executable. A
literal stock binding may instead name a literal Travel destination. Validation
rejects `parameter_bindings` on an Action definition, and rejects a Behavior
node that uses `context.resource_location_id` without declaring the
`resource_location` binding (or an explicit `resource_location_id` Agent
parameter). A God-proposed withdrawal/take Behavior must also place Travel
before the stock operation or declare a `resource.here` entry condition; the
runtime still blocks a remote stock change even if content bypasses that
validation.

A composite Action that intentionally consumes or produces a literal item
(craft, repair, trade, install, allocate) declares that verb and is exempt from
the target-binding rule. Only the generic families `transfer_resource`,
`consume`, `move`, `gather` and `inventory` must bind their target.

A structural contract can require that a capability exists:

```yaml
required_content:
  behavior_effects: [resource_withdrawal]
  generic_parameters: [resource_id]
```

`generic_parameters` is satisfied only by an Action that declares the parameter
and references `context.<parameter>` in an effect.

Three optional bounds keep an add-only world from outgrowing its prompts:

```yaml
rules:
  decision_policy:
    max_unlocked_behaviors: 24   # rotate the oldest assigned behavior
    full_behavior_choices: 5     # full blocks; the rest are one-line entries
director:
  max_cell_units: 12             # 0 disables; saturation stops new plans
```

- `max_unlocked_behaviors`: initial behaviors and the active behavior always
  stay. Older assigned behaviors are dropped from the default catalog but
  remain registered and discoverable with `query_behavior_catalog`.
- `full_behavior_choices`: the most urgent behaviors get full requirements,
  steps, costs and effects; the rest are compact name/time/requirement lines.
  `preview_behavior` returns the full entry for one choice.
- `max_cell_units`: once every target cell reaches the cap, the Director writes
  `season_plan.status: saturated` and queues no new structural task. Removing
  dead or duplicate content lowers a cell and growth can resume.

Definition validation also rejects a reskinned Action with the same
effect/duration/outcome shape, and a taken visible name. Names alone are not
enough to make a new Action distinct. A Behavior that only rebinds an existing
generic verb is a binding, not a new capability; the binding belongs in
`agent_parameters`/`parameter_options` rather than a new tree.

### Growth events

Creating a location emits `world.location.created`; creating a resource or
material emits `world.resource.created`. Both carry the visible name and
summary and use the normal configured wake policy. A world can therefore follow
a new place with one narrow "add its activity" wake, and a new stock with one
"add an action that changes it" wake, instead of leaving dynamic content dead.
The generic `entity.created` event is still emitted for consumers that need the
broader fact.

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

`world_history` and current research gaps are separate projections. History is
the validated `event_log`: it answers what happened and supports causal
continuation. `query_knowledge_gaps` answers which configured or God-created
research questions are still active (or already resolved). It is read-only and
available to Story and God; an NPC's private knowledge is not a substitute for
the world gap projection. A completed research stage updates its source gap in
SQLite, and a multi-stage gap becomes `resolved` only after every active stage
has a completed research event. Before proposing a new source, God should read
the active gaps and continue one of them when appropriate; a resolved question
must not be reopened under a new title.

An entry with only `on` and `for` wakes the consumer immediately for that event.
`count` and `every` are a gate for repeated evidence: the platform waits until
the count is reached, then applies the optional cooldown to that evidence group.
They do not delay a direct Story response and there is no hidden daily God call.
Omit a consumer entirely when a world does not need that evolution role.

Actions and behavior trees may also declare `wake: [story, god]` on the event
they produce. That producer declaration is the narrow opt-in for a one-off
event; the `wakes` consumer policy still decides grouping and cooldown. For
events emitted by runtime infrastructure, define the consumer in `wakes`.

`world.location.created` and `world.resource.created` are runtime events.
For example, a wake on `world.location.created` can reference a God contract
that settles the site with a resident. A God-created location connects to at
least one existing neighbor. Set `state.public: true` to show it on the player
map immediately. A world with `rules.travel.player_route_mode: authored`
also needs a travel behavior the player can execute. New LLM residents start
their configured reflection and innovation cadence from the minute they join.

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
- Every wake group whose event filter matches the same event queues its own
  task. Two follow-ups can subscribe to one fact (for example a new place
  needs both an activity and a local stock); neither is silently dropped.
- `distinct_by: [source_id, stage_id]` makes `count` count only **novel**
  evidence. The listed keys (payload fields, or the `actor_id` and
  `event_type` columns) identify what is new; a combination already counted
  never counts again, so a repeated outcome needs no calendar `every`
  cooldown. The last 512 combinations are remembered per group. A wake with `distinct_by` carries its matching events
  as `<triggering_evidence>`, like any other contract wake.
- `stagnation: {window: 1d, max_interval: 1w, growth: structural}` turns on the
  derived `world.stagnation` event. `growth: option` (the default) compares
  each living character's cumulative set of executable options, including
  parameter options such as a new research source. `growth: structural`
  compares behaviors, reachable places, factions and rules, plus a persisted
  world-structure signature, so adding another parameter to an existing verb
  does not reset the clock. The first window only records the baseline. Any
  structural growth resets the counter. When
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

For a persistent shortage, `rules.resource_safety` may also declare
`repeat_every_minutes`. The first crossing emits the configured safety event;
while the reserve remains below its floor, the same event is emitted again only
at that interval. Omit the field for one crossing per recovery cycle. This lets
God revisit an unresolved shortage without waking it on every simulation tick.

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

Every discovery must declare a readable `fact`; a bare identifier such as
`relay_checksum_parity` is rejected because it would become an ungrounded
knowledge fact. The generic research BT/Action selects the first active stage
whose fact has not been recorded for the researcher. A source may set `research_required` in
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

A character whose vital attribute reaches its minimum becomes `unconscious`
and Platform publishes `agent.incapacitated`. The behavior it was executing
fails with the result `incapacitated` (its reservations are released), it is
not asked for decision turns, and it is left out of the stagnation evaluation.
Without further configuration the state never ends, so a world whose
characters are all unconscious stops evolving. `rules.incapacitation.recovery`
ends it:

```yaml
incapacitation:
  recovery:
    after_minutes: 720     # how long a character stays unconscious
    vital_fraction: 0.25   # share of the vital attribute's range regained on waking
```

Platform then sets the status back to `idle`, removes the `incapacitated`
condition, raises the vital attribute to that share of its range (never below
its current value) and publishes `agent.recovered`. Validation rejects unknown
fields, a non-positive `after_minutes`, a `vital_fraction` outside (0, 1] and a
recovery rule without `rules.vital_attribute`. A character that is incapacitated
again simply restarts the clock. Every shipped world declares a recovery rule;
a world that wants permanent incapacitation omits the section, and `dead` is a
status only a world rule or Action can set.

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

Keep predicates as YAML strings. For a long predicate, use a folded scalar
so the compiled condition reads as one expression. Action preconditions and
outcomes are checked during package validation. Conditions can use
`context.day_phase`, `context.calendar_day` and `context.minute_of_day`.
Use `result` for a descriptive outcome code. The
[DSL execution notes](design/dsl-and-ast.md) describe expression caching
and how upgrades preserve in-flight definitions.

An Action definition must not contain Behavior-only fields. `entry_conditions`,
`success_results`, `failure_results`, `outcome_events`, `agent_parameters`,
`agent_label`, `nodes`, `estimated_minutes`, `goal_tags`, `drive_tags`,
`requirements`, `parameter_bindings` and `authorization_operation` belong in
`followup_behavior`; validation rejects them at the Action level.

`world_resource_delta` changes an existing resource or material as the
behavior's declared outcome; `resource_transfer` moves a reserved carried item
into a stock; `resource_withdrawal` and `resource_take` remove stock. All three
require the actor to be at the stock's location. A Behavior using one of them
must include a Travel node to the stock or expose the target as an Agent
parameter resolved by the `resource_location` binding.
Use `world_resource_delta` when the behavior itself produces or consumes public
stock, and give it a non-zero numeric `delta` plus optional `minimum`/`maximum`
bounds.
Platform rejects an unknown target. A positive
delta must declare `maximum` or a `rules.resource_caps.<id>.max`; execution
clamps to the configured cap so production cannot inflate a stock.

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

Use `drive_tags` only for needs that the BT changes immediately. When a BT
collects or produces an item that a later behavior consumes, put the eventual
need in `supports_drive_tags` and state the distinction in `purpose` (for
example, raw food supports satiety, while an eating behavior restores it). The
Agent view renders both fields and `impact`; Platform does not infer that a
positive inventory change already repaired a need.

Optional `rules.progression` entries expose long world stages to the relevant
Agents. A stage declares `target_behaviors`, optional `support_behaviors`, and a
verified completion contract such as
`{event_type: research.completed, match: {source_id: chart}, distinct_by: [stage_id], count: 2}`.
The projection shows only currently executable target/support labels and the
existing precondition diagnostics. Completed stages are omitted and completion
counts use the full persisted event history. Stage ids, audiences, behavior
references and contract fields are validated when authoring a package.
An event may have one publishing owner: do not repeat the same BT outcome with
an Action `information` event or `public_event` effect. Duplicate publication is
rejected during package validation because it would multiply evidence and can
incorrectly satisfy later Story/God thresholds.

Dynamic locations are navigable objects, not prose labels. A `propose_entity`
location must declare a non-empty `state.routes` list whose entries resolve to
existing locations and may provide positive `route_distances`. Platform inserts
the new location and the reciprocal links in one transaction. A proposal that
only names an `adjacent_to` place, or otherwise leaves a public location
disconnected, is rejected.

When a BT needs content chosen by the Agent, declare `agent_parameters` on the
BT. Each entry states its type, whether it is required, its length bound and a
plain-language description. The compiled behavior shows these requirements;
Platform rejects a missing or invalid value before starting the activity. For
example, a communication BT requires both a topic and the actual opening
message. A recorded message is testimony only. Inventory and currency move
only through an accepted transaction intent.

### Location tag groups

Conditions such as `location.safe` and `location.health_service`, and the BT
parameter bindings `nearest_safe_location` and `nearest_health_service`, ask
whether a location carries a tag. Tag names are world content, so the engine
assumes none: `rules.location_tag_groups` declares them, and a group a world
leaves out is empty.

```yaml
location_tag_groups:
  safe: [safe]
  resource_node: [resource, water]
  health_service: [clinic]
```

| Group | Read by |
| --- | --- |
| `safe` | `location.safe` and `nearest_safe_location`. A location's own `safe: true` flag, and a public location while `rules.public_locations_are_safe` is true (the default), count without any tag. |
| `resource_node` | `location.resource_node` |
| `health_service` | `location.health_service` and `nearest_health_service` |

A world that wants characters treated at a place declares `health_service` and
tags the place; without the group neither the condition nor the binding finds
one, so a behavior bound to `nearest_health_service` resolves to nothing. A
condition can also name a tag directly with `location.tag.<name>`. The shipped
worlds declare the groups they use.

### Failure recovery events

A behavior whose last run did not complete is blocked for that actor: its choice
is not offered (the view says to wait for a configured recovery event) and a
direct submission is rejected as `failed_behavior_blocked`. The block lifts when
an event from the recovery list is recorded at a later world minute.
`rules.failure_recovery_events` is that list, written as event types:

```yaml
failure_recovery_events: [resource.regenerated, entity.created, gate.repaired]
```

When the key is absent or empty, the engine's default list applies:
`resource.regenerated`, `resource.changed`, `entity.created`,
`definition.registered`, `behavior.registered`, `institution.activated`,
`institution.allocation_completed`, `story.beat.activated`, `research.completed`
and `exploration.completed`. A list in the rules replaces the default instead of
extending it, so repeat any default event the world still wants. A world whose
own actions record the event that should unblock a behavior, such as a repaired
gate, lists it here.

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

### Background population and map growth

An agent entry may set `controller: bt` and an optional `bt_priority` list. The
scheduler then runs the selected full behavior tree without an LLM call; the
actor still has persistent needs, inventory, travel, reservations, Action
results and event history. Keep `controller` omitted (the default `llm`) for
named characters whose memories, reflection and social choices matter. This is
a content choice, not a Platform world branch.

For expansion, God may create a location entity after verified research and then
submit a `route` world change with `from`, `to` and a positive `distance`. The
route is committed in both directions in one transaction. A route proposal is
a topology change only; a later NPC behavior must pay the configured travel
cost and produce the exploration result.

### Conditional and origin stages

`rules.progression` entries may additionally declare:

| Field | Meaning |
| --- | --- |
| `origins` | Show the stage only to players whose stored `origin_id` matches. |
| `visible_when` | A declarative condition evaluated against current world properties (for example `world.public_decision_open >= 1`). |
| `requires_goal` | Hide the stage until another progression stage's `complete_when` contract is already satisfied. |
| `complete_when.self_only` | Count only events whose `actor_id` is this player, so another resident cannot finish a personal first task. |

These fields are projection rules only. They never bypass Platform checks or
grant resources.

### Onboarding hints

`rules.onboarding` is a bounded, ordered list of first-run hints:

```yaml
onboarding_window_minutes: 2880
onboarding:
  - id: first_look
    title: Inspect the gate
    body: Confirm the damage before spending material.
    when: world.maintenance_evidence < 1
    action_ref: behavior:inspect_gate
    goal_id: inspect_gate
```

The player view projects only the first step whose `when` condition is true.
`onboarding_window_minutes` makes the hint list expire; after the window the
client falls back to normal goals and Agent-driven Story. Hints are not story
beats and do not mutate state.

### Opening situation anchors

`story.situation_anchors` is a read-only narration fallback for the opening:

```yaml
situation_anchor_window_minutes: 2880
situation_anchors:
  - id: storm_watch
    title: Storm watch
    summary: The gate is damaged and the first storm is close.
    trigger_events: [storm.watch_begun]
```

A player-readable `story.beat.activated` always takes precedence. Anchors expire
after the configured window, never unlock behavior or gate goals, and never stop
Story or God from adding new content.

### World-scope effect lists

A world-scope `minute_effects` entry may use `effects` instead of a single
`effect`:

```yaml
- id: storm_closes_the_quay
  scope: world
  every_minutes: 1
  condition: world.storm_eta_hours <= 0 and world.tide_gate_integrity < 0.2
  effects:
    - {type: world_property_delta, property: public_confidence, delta: -0.22, minimum: 0}
    - {type: world_resource_delta, resource_id: driftwood, delta: -8, minimum: 0}
    - {type: entity_state_set, entity_id: commons_quay, state: open, value: false}
  event_type: storm.flooded
  event_summary: Storm tide floods the quay.
```

`entity_state_set` can close any location by setting `initial_state.open: false`
or `initial_state.closed: true`. Closed locations disappear from reachable
routes, player route cards and travel actions, and map nodes expose
`open: false` plus an optional `closed_reason`. Reopen them with the same effect
from a repair Action or a later world rule.

### Context budgets

`rules.context_limits` configures per-role prompt budgets:

```yaml
context_limits:
  default_chars: 16000
  default_tokens: 6000
  roles:
    god: {max_chars: 24000, max_tokens: 9000}
```

The renderer pins the current decision, executable behaviors, latest platform
result, interaction history and God's exact `rule_condition_fields`; background
sections are compacted first. See
[Context Management](design/context-management.md) for metrics and memory
ranking.
