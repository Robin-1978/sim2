# Starter Village Agent Catalog

This compact English catalog is generated from the world configuration. It is a projection, not world state. The LLM receives only the permitted and relevant subset.

## Tools

| Tool | Purpose |
|---|---|
| `observe_nearby` | Read visible facts around the current location. |
| `recall_memory` | Recall the agent’s own knowledge and experiences. |
| `preview_behavior` | Check whether an offered choice is possible and estimate its cost. |
| `request_behavior` | Ask Platform to execute one offered behavior. |

## Actions

| Reference | Name | When | Time | Result |
|---|---|---|---|---|
| action:travel_to | Travel To | Platform confirms the actor, target, route, resources and safety rules. | Route-dependent | Platform applies configured effects and records the result. |
| action:rest | Rest | The location is safe. | 48 minutes | Platform applies configured effects and records the result. |
| action:gather_resource | Gather Resource | You have gathering ability and a visible resource source has supply. | 144 minutes | Platform applies configured effects and records the result. |
| action:patrol_route | Patrol Route | You are a guard and the route is known. | 96 minutes | Platform applies configured effects and records the result. |
| action:heal_target | Heal Target | You can heal and the target and treatment are available. | 72 minutes | Platform applies configured effects and records the result. |
| action:explore_area | Explore Area | You can explore and the area is not fully mapped. | 240 minutes | Platform applies configured effects and records the result. |
| action:communicate | Communicate | You can communicate and the target is available. | 6 minutes | Platform applies configured effects and records the result. |
| action:research_source | Research Source | You can research and the source is accessible. | 192 minutes | Platform applies configured effects and records the result. |
| action:inspect_object | Inspect Object | You can inspect and the object is accessible. | 24 minutes | Platform applies configured effects and records the result. |
| action:negotiate | Negotiate | You can negotiate and the target is available. | 36 minutes | Platform applies configured effects and records the result. |
| action:attack | Attack | You are alive; the target is visible, reachable and hostile. | 6 minutes | Platform applies configured effects and records the result. |
| action:defend | Defend | You are alive and can protect the target. | 24 minutes | Platform applies configured effects and records the result. |
| action:flee_to_safety | Flee To Safety | You are alive and a known route leads to safety. | Route-dependent | Platform applies configured effects and records the result. |
| action:sound_alarm | Sound Alarm | You can sound an alarm and are at a watch post. | 6 minutes | Platform applies configured effects and records the result. |
| action:craft_recipe | Craft Recipe | You can craft; the recipe, station and inputs are available. | Recipe-defined | Platform applies configured effects and records the result. |
| action:consume_food | Consume Food | You carry `rations`, `ration`, or `cooked_meal`. | 1 minute | One item is reserved, consumed, and hunger satisfaction increases. |
| action:withdraw_food | Withdraw Food | You are at the configured food store. | 1 minute | One shared ration is reserved and added to inventory. |

## Behavior Trees

| Reference | Name | Purpose | Use when | Steps | Time | Success | Failure |
|---|---|---|---|---|---:|---|---|
| behavior:govern_village | Govern Village | Review village risk and coordinate policy. | You are the village steward and a village decision is pending. | Inspect Object; Choose one offered branch | 144 | Village risk and policy are reviewed. | The council cannot reach an agreement. |
| behavior:mediate_conflict | Mediate Conflict | Resolve a conflict through conversation. | A nearby conflict needs mediation and you can communicate. | Communicate; Check the previous result | 48 | The conflict is resolved. | The conflict escalates or the other party refuses. |
| behavior:inspect_granary | Inspect Granary | Inspect the granary and report its stock. | Food stock needs review and the granary route is known. | Travel To; Inspect Object | 24 | A stock report is recorded. | The granary is locked or unreachable. |
| behavior:patrol_route | Patrol Route | Inspect a known route for threats. | A security route needs inspection and you are a guard. | Travel To; Patrol Route | 96 | A patrol report is recorded. | A hostile event forces an emergency response. |
| behavior:manage_ration | Manage Ration | Review food stock and coordinate rationing. | Food stock is low or a ration decision is pending. | Inspect Object; Communicate | 48 | A ration decision is recorded. | The stock report cannot be completed. |
| behavior:heal_target | Heal Target | Restore a target using available treatment. | A nearby target needs treatment and medicine is available. | Heal Target | 72 | The target becomes more stable. | Treatment cannot proceed. |
| behavior:explore_greywood | Explore Greywood | Map Greywood and share verified findings. | The area is not fully mapped and you can explore. | Travel To; Explore Area; Communicate | 240 | New discoveries are recorded. | The route is unsafe or the exploration is interrupted. |
| behavior:research_source | Research Source | Study an accessible source and record a fact. | A relevant source is accessible and a fact is needed. | Research Source; Check the previous result | 192 | A verified fact is recorded. | The source is unavailable or damaged. |
| behavior:inspect_object | Inspect Object | Inspect an accessible object or facility. | An object or facility is reachable and needs inspection. | Inspect Object | 24 | An inspection report is recorded. | Access is denied. |
| behavior:defend_village | Defend Village | Respond to a threat near the village. | A threat is active near the village and you are on watch. | Sound Alarm; Choose one offered branch | 48 | The threat is contained or civilians reach safety. | Defense breaks and retreat is required. |
| behavior:defend_position | Defend Position | Hold position and attack if needed. | A reachable threat is active and you can defend. | Defend; Attack | 24 | The protected target remains guarded. | You are stunned or the threat leaves range. |
| behavior:flee_to_safety | Flee To Safety | Escape to a safe location. | The threat is overwhelming and a safe route exists. | Flee To Safety | 96 | You reach safety. | You become exhausted before reaching safety. |
| behavior:sound_alarm | Sound Alarm | Raise a warning alarm. | A threat is detected and an alarm post is available. | Sound Alarm | 6 | The village receives a warning. | The alarm device is unavailable. |
| behavior:craft_meal | Craft Meal | Craft food from available inputs. | A cooking station and recipe inputs are available. | Craft Recipe | 60 | Food is created and cooking skill improves. | Inputs are missing or the station is damaged. |
| behavior:communicate | Communicate | Exchange information with another character. | Another character is available and information exchange is relevant. | Communicate | 12 | The conversation becomes testimony. | The other character refuses or leaves. |
| behavior:negotiate | Negotiate | Reach an agreement with another character. | A counterparty is available and an agreement is needed. | Negotiate | 12 | A contract or exchange is recorded. | Trust collapses or the offer is refused. |
| behavior:gather_resource | Gather Resource | Collect a configured resource from a known node. | A known resource source is visible and supply is needed. | Gather Resource | 120 | The resource enters inventory or storage. | The source is empty or the actor cannot continue. |
| behavior:eat_food | Eat Food | You carry one configured food item and need to choose whether to restore hunger. | Consume Food(item_id) | 1 | Hunger satisfaction increases and the item is removed. | The food is unavailable. |
| behavior:get_food | Get Food | You are at the granary and shared food remains. | Withdraw Food | 1 | One ration enters inventory. | The shared store is empty. |

## Output

The LLM returns only a reference, target, optional parameters, a short reason and confidence. Platform recomputes every condition, cost and effect from SQLite.
