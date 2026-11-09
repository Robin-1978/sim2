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

| Name | When | Time | Result |
|---|---|---|---|
| Travel To | Platform confirms the actor, target, route, resources and safety rules. | Route-dependent | Platform applies configured effects and records the result. |
| Rest | The location is safe. | 48 minutes | Platform applies configured effects and records the result. |
| Gather Resource | You have gathering ability and a visible resource source has supply. | 144 minutes; energy 0.8/min; two source units reserved | Two source units enter inventory when the action completes. |
| Patrol Route | You are a guard and the route is known. | 96 minutes | Platform applies configured effects and records the result. |
| Heal Target | You can heal and the target and treatment are available. | 72 minutes | Platform applies configured effects and records the result. |
| Explore Area | You can explore and the area is not fully mapped. | 240 minutes | Platform applies configured effects and records the result. |
| Communicate | You can communicate and the target is available; the BT has a 1,440-minute cooldown. | 6 minutes; topic defaults to `general` | Platform records the exchange and the recipient's testimony. |
| Research Source | You can research and the source is accessible. | 192 minutes | Platform applies configured effects and records the result. |
| Inspect Object | You can inspect and the object is accessible. | 24 minutes | Platform applies configured effects and records the result. |
| Recover Object | You can investigate and a discovered object is here and not recovered. | 180 minutes; one ration reserved; energy 0.1/min; exposure 0.01/min | The object is marked recovered, its configured fact is recorded, and configured world effects are applied. |
| Negotiate | You can negotiate and the target is available. | 36 minutes | Platform applies configured effects and records the result. |
| Attack | You are alive; the target is visible, reachable and hostile. | 6 minutes | Platform applies configured effects and records the result. |
| Defend | You are alive and can protect the target. | 24 minutes | Platform applies configured effects and records the result. |
| Flee To Safety | You are alive and a known route leads to safety. | Route-dependent | Platform applies configured effects and records the result. |
| Sound Alarm | You can sound an alarm and are at a watch post. | 6 minutes | Platform applies configured effects and records the result. |
| Craft Recipe | You can craft; the recipe, station and inputs are available. | Recipe-defined | Platform applies configured effects and records the result. |
| Consume Food | You carry `rations`, `ration`, or `cooked_meal`; the model weighs food against its other needs. | 1 minute | One item is reserved, consumed, and Satiety increases. |
| Withdraw Food | You are at the configured food store. | 10 minutes | One shared ration is reserved and added to inventory. |
| Buy Food | You are at the inn market; you have one coin and market stock remains. | 5 minutes; one coin and one market ration reserved | One ration enters inventory and both the coin and market stock decrease. |

## Behavior Trees

| Name | Purpose | Use when | Steps | Time | Success | Failure |
|---|---|---|---|---:|---|---|
| Govern Village | Review village risk and coordinate policy. | You are the village steward and a village decision is pending. | Inspect Object | 24 | Village risk and policy are reviewed. | The council cannot reach an agreement. |
| Mediate Conflict | Resolve a conflict through conversation. | A nearby conflict needs mediation and you can communicate. | Communicate; Check the previous result | 48 | The conflict is resolved. | The conflict escalates or the other party refuses. |
| Inspect Granary | Inspect the granary and report its stock. | Food stock needs review and the granary route is known. | Travel To; Inspect Object | 24 | A stock report is recorded. | The granary is locked or unreachable. |
| Patrol Route | Inspect a known route for threats. | A security route needs inspection and you are a guard. | Travel To; Patrol Route | 96 | A patrol report is recorded. | A hostile event forces an emergency response. |
| Manage Ration | Review food stock and coordinate rationing. | Food stock is low or a ration decision is pending. | Inspect Object; Communicate | 48 | A ration decision is recorded. | The stock report cannot be completed. |
| Heal Target | Restore a target using available treatment. | A nearby target needs treatment and medicine is available. | Heal Target | 72 | The target becomes more stable. | Treatment cannot proceed. |
| Explore Greywood | Map Greywood and record verified findings. | The area is not fully mapped and you can explore. | Travel To; Explore Area | 241 | New discoveries are recorded. | The route is unsafe or the exploration is interrupted. |
| Research Source | Study an accessible source and record a fact. | A relevant source is accessible and a fact is needed. | Research Source; Check the previous result | 192 | A verified fact is recorded. | The source is unavailable or damaged. |
| Inspect Object | Inspect an accessible object or facility. | An object or facility is reachable and needs inspection. | Inspect Object | 24 | An inspection report is recorded. | Access is denied. |
| Defend Village | Respond to a threat near the village. | A threat is active near the village and you are on watch. | Sound Alarm; Choose one offered branch | 48 | The threat is contained or civilians reach safety. | Defense breaks and retreat is required. |
| Defend Position | Hold position and attack if needed. | A reachable threat is active and you can defend. | Defend; Attack | 24 | The protected target remains guarded. | You are stunned or the threat leaves range. |
| Flee To Safety | Escape to a safe location. | The threat is overwhelming and a safe route exists. | Flee To Safety | 96 | You reach safety. | You become exhausted before reaching safety. |
| Sound Alarm | Raise a warning alarm. | A threat is detected and an alarm post is available. | Sound Alarm | 6 | The village receives a warning. | The alarm device is unavailable. |
| Craft Meal | Craft food from available inputs. | A cooking station and recipe inputs are available. | Craft Recipe | 60 | Food is created and cooking skill improves. | Inputs are missing or the station is damaged. |
| Communicate | Exchange information with another character. | Another character is available; no communication in the last 1,440 minutes. | Communicate | 6 | The conversation becomes testimony. | The other character refuses or leaves. |
| Negotiate | Reach an agreement with another character. | A counterparty is available; no negotiation in the last 2,880 minutes. | Negotiate | 36 | A contract or exchange is recorded. | Trust collapses or the offer is refused. |
| Gather Resource | Collect a configured resource from a known node. | A known resource source is visible and supply is needed. | Gather Resource | 144 | Two source units enter inventory and the source decreases. | The source is empty or the actor cannot continue. |
| Eat Food | You carry one configured food item; the 40-point value is only an alert signal and the model weighs this choice against other needs. | Consume Food(item_id) | 1 | Satiety increases and the item is removed. | The food is unavailable. |
| Get Food | You have food-withdrawal rights and a known route to the granary. | Travel To; Withdraw Food | Route minutes + 10 | One ration enters inventory. | The route or shared store is unavailable. |
| Buy Food | You are at the inn market; the model weighs the purchase against its other needs and the configured coin and market stock are available. | Buy Food | 5 | One ration is purchased. | The market has no stock or the coin is unavailable. |
| Recover Caravan Ledger | The Greywood survey revealed the ledger clue and the ledger is still recoverable. | Recover Object(caravan ledger) | 180 | The ledger is marked recovered; a trade-route fact is recorded; configured public-trust effects are applied. | Recovery fails or is interrupted. |

## Output

The LLM returns only the exact visible behavior name in `choice`, a target, optional parameters, a short reason and confidence. Platform maps that choice to its internal behavior reference, then recomputes every condition, cost and effect from SQLite. Internal IDs are never required in the model-facing catalog. Exploration is a transition, not an ending: a configured discovery can unlock a validated follow-up behavior, and that behavior must still pass location, object, resource and capacity checks at execution time.
