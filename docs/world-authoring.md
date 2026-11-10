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

`build-world` never edits an existing instance. With `--brief`, WorldBuilder
must first call `validate_world_template` and then submit the exact validated
object. Platform rejects changed or invalid submissions. The command writes
human-editable YAML before any optional `--instance-id` bootstrap.

## Unified template

The template uses these top-level sections. The machine-readable source is
`sim2 world-schema`; this table explains the intent.

| Section | Purpose |
| --- | --- |
| `template_version` | Version recorded as the package configuration version. |
| `world` | ID, title, premise, genre, locale, clock, seed and initial world properties. |
| `attributes` | Every numeric state or need, including direct meaning, range and default. |
| `locations` | Place nodes, visible names, tags, safety, optional map position and route IDs. |
| `resources` | Finite or regenerating world stocks at configured locations. |
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

IDs are internal references and use stable `snake_case`. Labels, summaries and
prompts are short natural language written for people and Agents. References
must resolve inside the same template. The validator rejects missing routes,
locations, Actions, BTs, capabilities and undeclared state attributes.

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

Character attributes describe the individual. Environmental, institutional,
vehicle and facility values belong to world properties or entity state. For
example, a diver may have `suit_integrity` while a station has
`hull_integrity`; the same number must not appear in both scopes.

## Action and BT rule

An Action is executable mechanics. It declares:

- parameters and target requirements;
- machine-evaluable preconditions;
- duration or route-distance mode;
- reserve and per-minute costs;
- typed effects;
- success and failure outcomes;
- interruption and interaction policy.

A BT is the choice shown to an individual Agent. Its `agent_label`, summary,
requirements, estimated time, costs and likely effects are compiled into short
model-facing text. The model never receives the raw tree. Selecting a BT only
submits intent; Platform resolves targets, reserves resources and advances its
Actions minute by minute.

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

`manifest.yaml` lists every imported file. `sim2 validate-world` checks the
whole package before SQLite exists. Every actual execution mechanism also has
focused runtime tests; validation alone does not prove that a world is
balanced, so a new content pack should still run a deterministic week and a
real-model audit before release.
