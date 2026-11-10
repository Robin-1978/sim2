# Behavior Closure Matrix

This matrix is the acceptance checklist for a world package. A behavior is
closed when Platform can execute its configured actions, persist the direct
result, and expose that result to the next decision. Story, God, and
Reflection consume reports at a boundary; they are not hidden steps inside a
behavior tree.

| Behavior | Direct result that must be persisted | Follow-up consumer | Status in Starter Village |
|---|---|---|---|
| `travel` | Location changes after route time and cost | Next decision | Closed |
| `rest_and_recover` | Energy, exposure, and attention change | Next decision / Reflection | Closed |
| `recover_at_safe_location` | Safe location, recovery values | Next decision | Closed |
| `seek_treatment` | Health changes at a configured service | Next decision / Reflection | Closed |
| `eat_food` | Inventory decreases and the configured need increases | Next decision | Closed; the model chooses when |
| `get_food` | Shared stock decreases and carried stock increases | Next decision / God trend | Closed; the model chooses when |
| `buy_food` | Currency and market stock decrease; carried stock increases | Next decision / God trend | Closed; the model chooses when |
| `gather_resource` | Source quantity decreases and inventory increases | Next decision / Reflection | Closed |
| `gather_at_known_node` | Same resource result after travel | Next decision / Reflection | Closed |
| `craft_meal` | Inputs are consumed and food is produced | Next decision / Reflection | Closed |
| `craft_medicine` | Inputs are consumed and medicine is produced | Next decision / Reflection | Closed |
| `craft_dawn_bell` | Materials are consumed and the configured object state changes | God trend | Closed |
| `install_dawn_bell` | The carried bell is consumed, the entity is installed at the watch post, alarm readiness becomes true, and the installation is persisted | God evidence group / next decision | Closed; installation alone does not create a Story goal |
| `heal_target` | Target health changes and treatment cost is committed | Next decision / Reflection | Closed when a patient target is supplied |
| `communicate` | Dialogue exchange, testimony, relationship and belonging effects | Next decision / Reflection | Closed |
| `negotiate` | Concrete terms and the actual opening message are persisted and delivered to the other participant; no contract is claimed | Recipient instant response; item movement uses the separate atomic trade offer protocol | Closed as a terms-sharing interaction; only a later accepted response or trade intent can form an agreement |
| `mediate_conflict` | Conversation result, configured trust change, mediation event and concise fact are persisted | Story / God / Reflection | Closed when a configured conflict relation is visible; otherwise omitted from the candidate list |
| `inspect_object` | The selected object, quantity, and observation are recorded | Reflection | Closed when an object is selected |
| `inspect_granary` | Granary stock is observed as a knowledge fact | Reflection / God when a risk signal exists | Closed after the object inspection |
| `manage_ration` | Stock observation, policy discussion, personal esteem/belonging effects, event and fact are persisted | God / Reflection | Closed as a policy recommendation; God applies any later world policy through a validated proposal |
| `govern_village` | Stock observation, governance report, and personal needs change | Reflection; God after the configured governance evidence count | Closed as a report; one review does not wake a world-level Agent |
| `patrol_route` | Route report, knowledge fact, personal needs and trend signal are persisted | Reflection; Story/God only when a configured discovery or incident is relevant | Closed as an observation; routine patrols do not wake God |
| `defend_village` | After a verified alarm, the defender travels to the boundary, spends configured time and capacity, lowers the configured threat property, and records the defense result | Story / God | Closed; the world delta is produced by the configured defense Action |
| `defend_position` | Protected status and defense result event are persisted | Reflection / Story for a public threat | Closed as a response report |
| `flee_to_safety` | Location changes and escape event are persisted | Next decision / Reflection | Closed |
| `sound_alarm` | The alarm is persisted, is audible through the perception policy, clears the actor's decision backoff, and unlocks the configured defense response while threat remains high | Story / next individual decision | Closed; the alarm itself does not lower threat |
| `explore_greywood` | Map result, discoveries, knowledge, and unlocks are persisted | Story; God only for a configured world-level discovery | Closed |
| `research_water` | Cumulative research progress and final fact are persisted; water mitigation remains unavailable until that exact fact exists | Story per discovery; God after the configured research synthesis count | Closed when the configured source is co-located after travel |
| `research_source` | Cumulative progress and final knowledge fact are persisted; pact stabilization remains unavailable until that exact fact exists | Story; God only when the configured fact has a world-level consequence | Closed; a source at another location cannot be researched remotely |
| `recover_caravan_ledger` | Object state, trade evidence, knowledge, and public effect change | Story / God | Closed |

The matrix deliberately distinguishes a direct result from a later policy.
For example, a patrol must record what was observed; God may then lower a
threat, dispatch help, or change a rule. The patrol tree must not contain a
hard-coded world policy. A governance report must contain the measured stock
and risk facts; God decides whether to ration, trade, or change a rule.

Civilization progress uses the same evidence boundary. Every configured
progress dimension may declare accepted event types and behavior names. The
world-level Agent receives a plain description of those sources, while
Platform binds a submission to the task's actual source event. A pact
stabilization result therefore cannot be posted to governance capacity merely
because both are beneficial public work. A source that matches no configured
dimension produces no progress update.
The Agent does not choose the numeric contribution. Each unique source event
adds the definition's configured evidence value, one by default, and the same
event cannot be counted twice for the same progress dimension.

The Platform may validate generic fields such as `state_delta`,
`need_delta`, `inventory_add`, `entity_state_set`, `world_property_delta`,
`relationship_delta`, `public_event`, `information`, and `progress`. It must
not branch on a story-specific property name, object ID, role name, or BT ID.
Those values are imported into SQLite and resolved through the same generic
condition, target, resource, effect, and event pipelines.

The closure test is mechanical: every registered tree must expose a success
and failure result (from the tree or its configured Action), and must persist
at least one consequence through an effect, progress record, information
channel, recipe output, outcome event, outcome fact, or relationship change.
This prevents a “busy” behavior from becoming a dead end. Reports are useful
results: they become facts and trend signals that can wake the low-frequency
God/Story loop, while a later policy change remains a separate validated
intent.

Incremental remediation, such as one water treatment or one pact-stability
measure, remains a visible world event but does not need a new Story synthesis
on every repetition. The content package can route it to an evidence group for
low-frequency God review; Story wakes for discoveries, configured beats and
milestones that actually create a new narrative continuation.

## Trigger ownership

The behavior result owns the first state change. A configured `wake` list only
selects which consumer interprets the persisted result. Routine governance,
ration review, patrol, treatment, and production reports stay in the individual
and Story loops. The world package may define evidence groups such as “three
completed research results”, “five governance reviews”, or “one public safety
incident”; Platform wakes God only when that configured count is reached. This
is causal evidence accumulation, not a daily timer. High-impact public results
may use a group threshold of one.

An event-group policy is also an admission rule. An unmatched event is not
silently promoted to a direct call. Worlds list exceptional direct events or
task prefixes explicitly, and package validation checks every Action and BT
wake against the consumer policy before a SQLite instance is created.

God is not an autonomous resource operator. It is scheduled for a weekly local
review and a monthly structural review, and it may be woken by a configured
evidence group or a derived threshold signal. A threshold signal must include
the source events that caused the crossing; it is not an unexplained model
call.
God may propose a rule, entity, faction strategy, or narrative event. Shared
resource quantities, personal needs, relationships, and production outcomes
must be changed by configured behavior effects. A God proposal cannot directly
increase food, trust, safety, or another behavior-owned value.

Signal rules are bounded as well: Platform rejects a duplicate event/follow-up
signal and a same-role `world.rule.*` signal chain. A signal wakes its consumer
once; the named behavior result is the next causal step. Natural-language
signal descriptions do not enforce ordering or blocking; only the typed effect
does. Emergent Story beats
carry the validated evidence event and are deduplicated until a new discovery,
research result, recovery, or relationship result arrives.

Writing a rule is only a proposal result. The God quality audit remains open
until a later behavior outcome shows that the rule was used and produced its
configured effect. `RuleCompiler` now exposes active rules, gates configured
Actions, emits an edge-triggered signal for configured world conditions, and
records `world.rule.followed` when the named BT completes. The rule still never
mutates a resource by itself; the named behavior owns the observable delta.

## Audit conclusion

The current Starter Village catalog passes the direct-result closure test: each
offered tree has a configured execution path and leaves a persisted state,
fact, progress record, relationship change, or event. Health recovery,
conflict mediation, exploration follow-up, and resource/food flows are included
in that conclusion.

Governance, ration management, patrol, defense, and alarm behaviors close as
reports or public signals. Their later policy is deliberately a separate God
proposal. A report is useful only when the God loop consumes it and submits a
structured `rule`, `relationship`, or `faction` change when a world-policy
change is warranted. Resource, need, relationship, safety, trust, and
production values change through behavior effects; a narrative-only `event`
cannot claim that they changed. The Agent quality audit records this as a
remaining end-to-end risk even when Platform accepted the event row.

