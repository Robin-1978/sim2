# Tidebound Commons

`Tidebound Commons` is the first deliberately playable non-village content pack
for sim2. It is a four-person coastal survival and civic mystery rather than a
single linear quest. The residents must decide how to spend the same working
time and renewable materials:

- repair the damaged tide gate before storm pressure rises;
- preserve food and water so long expeditions remain possible;
- survey and research the drowned observatory to recover a safe route;
- share evidence so a private discovery becomes a commons decision.

The package contains no Platform branch for this setting. Locations, distance,
attributes, actions, recipes, behaviors, resource regeneration, Story wakes and
God wakes are all declarative YAML. The runtime instance is SQLite after
bootstrap, so later God proposals become instance definitions rather than edits
to this package.

## Balance targets

The starting reserve is intentionally neither a timer nor an infinite sandbox.
Each resident starts with one ration and one water unit. The marsh pool and
shellfish bed regenerate every 12 hours, while driftwood regenerates daily. A
single gathering action reserves two units, so an early trip can fund either a
repair kit (two driftwood plus one shellfish) or three rations (two shellfish
plus one water). The caps prevent stockpiling from dominating the choice.

Travel is weighted: the observatory is two distance units from the marsh and
three from the gatehouse. A normal trip therefore costs time and stamina, while
stamina recovers slowly each minute. The repair kit is capped at two total
units and preserved food at thirty; the player can prepare for a storm, but
cannot solve the world by repeatedly crafting.

The four residents have complementary options:

| Resident | Strength | Tension |
| --- | --- | --- |
| Mara | stewardship and repair | must spend scarce kits on a public gate |
| Ivo | renewable gathering and preservation | harvesting too often delays repairs |
| Selene | research and route discovery | needs food and water for long surveys |
| Taro | communication and exploration | can carry knowledge, but cannot prove it alone |

No single initial behavior can complete the main arc. A healthy run needs
material collection, workshop production, gate repair, and two research stages.
The route is not considered open after the first fact: Story receives each
verified stage while the world milestone is emitted only after the configured
progress is complete.

## Emergent continuation contract

Story turns a verified inspection, harvest, craft, repair, survey, or research
outcome into a grounded next chapter. It may name an already executable choice,
but cannot claim that an unperformed journey or repair happened.

God wakes on accumulated distinct evidence rather than on a fixed daily clock.
Two distinct chart stages can justify one new route object or executable
continuation; repeated repairs can justify a maintenance structure; repeated
harvests can justify preservation or transport. Platform rejects duplicate,
unreachable, or resource-minting proposals. If the current choices remain
adequate, God is allowed to wait.

## Verification

```bash
sim2 validate-world --config worlds/tidebound_commons
sim2 describe-world --config worlds/tidebound_commons
sim2 init --config worlds/tidebound_commons --instance-id tidebound-demo
python scripts/run_long_simulation.py \
  --config worlds/tidebound_commons \
  --instance-id tidebound-deterministic \
  --minutes 2880
```

For a real-model causal audit, each round records the rendered decision context,
tool calls and final response, world events, progression states, resource
levels and provider usage:

```bash
python scripts/audit_world_evolution.py \
  --config worlds/tidebound_commons \
  --provider remote \
  --minutes 1440 \
  --round-minutes 360 \
  --runtime-dir /tmp/sim2-tidebound-audit \
  --instance-id tidebound-audit
```

The five public stages in `rules.progression` are content declarations. They do
not grant resources or bypass prerequisites. A gate measurement disappears once
its verified event exists; repair becomes ready only after a carried kit exists;
the chart stage advances from distinct verified research stages rather than
duplicate reports. Story and God still own narrative and structural changes.

The focused regression test checks package validation, package isolation from
Starter Village fields, route distances, regenerative resource caps, and that
every configured behavior is assigned to at least one resident. A real model
run should audit the same loop at the event level: material collected, recipe
inputs reserved, repair result, each chart stage, Story beat, and any God
proposal must all be visible in the timeline.

## Expansion beyond the starting shore

The five starting locations are a foothold, not the world boundary. The chart
has two separate research stages. After both are verified, God may create one
new location (for example, an outer shelf or a second settlement) and then add a
weighted reciprocal route with the generic `route` world change. A new location
is only a navigable possibility; it becomes part of play after an existing
resident receives an executable exploration behavior and completes the travel.
Story can frame that opening, but cannot claim the journey happened. This keeps
map growth causal and gives every expansion a time, stamina and supply cost.

## Named characters and the wider population

Mara, Ivo, Selene and Taro are the four decision-bearing characters. They have
LLM controllers, private memories, reflection and the ability to ask for a
social response. Nia and Ren are dockhands with `controller: bt`: they use the
same gathering, preservation, travel and recovery BTs, but their routine work
never spends a model call. Their survival choice is scored from configured need
urgency before the optional `bt_priority` ordering. This makes the commons feel
populated while reserving model budget for choices that can change the story.

The opening season deliberately has no universal war system. Conflict is a
content branch: once an expanded route produces a rival salvage faction, God
may register a relationship, a guarded route, a negotiation behavior or a
conserved raid/defense Action. An attack must have a visible reachable target,
explicit capability and cost, an observable target effect, a failure result and
an event that Story can ground. If the evidence does not justify escalation,
the world stays social and exploratory; adding a war clock would remove the
meaning of the commons' early choices.

The mid-game has a second concrete branch: once the tide gate reaches the
configured stability threshold, Mara or Selene can spend conserved repair kits
to retune the seasonal signal lens. Two verified tunings open the signal route;
only then can God justify a new external location. This makes the route opening
a result of resident work rather than a free story unlock.

The survival guard is deliberately visible in the action catalog. When
hydration or satiety falls below 45, optional research, travel, inspection and
repair behaviors disappear until the resident has a recovery choice; eating
remains available even when dehydrated, and drinking remains available even
when hungry. Shellfish gathering remains available while hungry because the
raw catch is the first step in a recovery chain when no ration exists; it is
not treated as an immediate meal. A resident without rations therefore follows
gather shellfish / draw water / prepare rations / eat rather than being left
with no executable recovery behavior. Hydration still has to be above 45 for
the marsh trip so the survival guard sends a dehydrated resident to water
first.

The catalog separates direct recovery from preparation. `drive_tags` lists a
need that the behavior itself changes immediately; `supports_drive_tags` lists
a need that its produced item can help later. Gathering shellfish therefore
supports satiety, while only eating a preserved ration directly restores it.
The Agent prompt repeats this distinction beside the authoritative `impact`
field so an ingredient is never mistaken for a completed meal. Preserved
rations have no world-wide inventory cap: each new ration still consumes
carried shellfish and freshwater and takes recipe time, so one resident's
stored meals cannot strand another resident who has the inputs to cook.
