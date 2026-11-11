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
| `behavior_trees` | Agent-readable choices composed from registered Actions or subtrees. |
| `rules` | Generic motivation, minute effects, navigation, interaction, memory and world policies. |
| `evolution` | Reflection cadence and evidence-group wake policies. |
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

`evolution.event_wake_policy` is also an admission contract. If a consumer has
`event_groups`, an Action or BT that wakes that consumer must emit an event in
one of those groups. Exceptional channels use `direct_event_types` or
`direct_task_type_prefixes`. `validate-world` rejects mismatches before a
SQLite instance is created.

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
