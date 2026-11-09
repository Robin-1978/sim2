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
| `heal_target` | Target health changes and treatment cost is committed | Next decision / Reflection | Closed when a patient target is supplied |
| `communicate` | Dialogue exchange, testimony, relationship and belonging effects | Next decision / Reflection | Closed |
| `negotiate` | Trust changes and a negotiation result event are persisted; item movement uses the separate configured trade offer protocol | Story / God when the result has public consequences | Closed as a negotiation signal; a contract or item exchange is a separate atomic trade intent |
| `mediate_conflict` | Conversation result, configured trust change, mediation event and concise fact are persisted | Story / God / Reflection | Closed when a configured conflict relation is visible; otherwise omitted from the candidate list |
| `inspect_object` | The selected object, quantity, and observation are recorded | Reflection | Closed when an object is selected |
| `inspect_granary` | Granary stock is observed as a knowledge fact | Reflection / God when a risk signal exists | Closed after the object inspection |
| `manage_ration` | Stock observation, policy discussion, personal esteem/belonging effects, event and fact are persisted | God / Reflection | Closed as a policy recommendation; God applies any later world policy through a validated proposal |
| `govern_village` | Stock observation, governance report, and personal needs change; God receives the report | God / Reflection | Closed as a report; God decides any policy change from measured evidence |
| `patrol_route` | Route report, knowledge fact, personal needs and trend signal are persisted; God receives the report | God / Story when the report is relevant | Closed as an observation and escalation signal; a threat change requires a later validated world proposal |
| `defend_village` | Alarm and defense result event are persisted; God receives the report | Story / God | Closed as a report; threat policy is external |
| `defend_position` | Protected status and defense result event are persisted | Reflection / Story for a public threat | Closed as a response report |
| `flee_to_safety` | Location changes and escape event are persisted | Next decision / Reflection | Closed |
| `sound_alarm` | Public alarm event is persisted and Story/God are woken | Story / God | Closed as a signal; response is external |
| `explore_greywood` | Map result, discoveries, knowledge, and unlocks are persisted | Story / God | Closed |
| `research_water` | Cumulative research progress and final fact are persisted | Story / God | Closed when the configured source is available |
| `research_source` | Cumulative progress and final knowledge fact are persisted | Story | Closed |
| `recover_caravan_ledger` | Object state, trade evidence, knowledge, and public effect change | Story / God | Closed |

The matrix deliberately distinguishes a direct result from a later policy.
For example, a patrol must record what was observed; God may then lower a
threat, dispatch help, or change a rule. The patrol tree must not contain a
hard-coded world policy. A governance report must contain the measured stock
and risk facts; God decides whether to ration, trade, or change a rule.

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

