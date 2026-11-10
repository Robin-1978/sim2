# Starter Village Agent Catalog

This compact English catalog is generated from the world configuration. It is a
projection, not world state. The model receives only the permitted and relevant
subset; Platform recomputes every condition, cost and effect from SQLite.

## Tools

| Tool | Purpose |
|---|---|
| `observe_nearby` | Read visible facts around the current location. |
| `recall_memory` | Recall the agent's own knowledge and experiences. |
| `preview_behavior` | Check an offered choice's requirements, time, costs and outcomes. |
| `request_behavior` | Ask Platform to execute one offered behavior. |

## Actions

| Name | When | Time | Result |
|---|---|---|---|
| Travel To | The destination is connected by a known route. | 5 minutes per route hop | The actor arrives and Platform records the movement. |
| Rest | The location is safe and energy is below full. | 60 minutes; energy recovery is 2/minute | Energy, exposure and attention are updated. |
| Gather Resource | You can gather and the visible node has supply. | 180 minutes; energy 0.25/min; two units reserved | Two units enter inventory and the node decreases. |
| Patrol Route | You are a guard and the route is known. | 120 minutes; energy 0.25/min; exposure 0.2/min | A patrol fact and security event are recorded. |
| Heal Target | You can heal and the target and treatment are available. | 90 minutes; one medicine reserved | The target's health changes and treatment is recorded. |
| Explore Area | You can explore and the area is not fully mapped. | 300 minutes; one ration reserved; energy 0.2/min; exposure 0.3/min | Discoveries, routes and facts are recorded. |
| Communicate | You can communicate and the target is visible. | 15 minutes; topic defaults to `general` | The exchange becomes testimony and can change trust. |
| Research Source | You can research and the source has a new fact. | 240 minutes; energy 0.2/min; attention 0.4/min | Research progress and, at 2/2, a verified fact are recorded. |
| Inspect Object | You can inspect and the object is reachable. | 30 minutes | An inspection report is recorded. |
| Recover Object | You can investigate a discovered object that is not recovered. | 240 minutes; one ration reserved; energy 0.1/min | The object and its configured fact are updated. |
| Discuss Terms | You can negotiate and the counterparty is available; provide the actual topic and proposed terms. | 60 minutes; attention 0.6/min | The terms are delivered as testimony; no contract, payment or trade is completed. |
| Attack | You are alive; the target is visible, reachable and combat is allowed. | 6 minutes | Damage, hostility and the combat result are recorded. |
| Defend | You are alive and can protect the target. | 30 minutes; energy 0.3/min; attention 0.4/min | The protection window and result are recorded. |
| Flee To Safety | A known route leads to a safe or defensible destination. | Route-dependent; energy 0.5/min | The actor reaches safety or fails from exhaustion. |
| Secure Boundary | You can defend, are at a watch post, and the configured public threat is high. | 120 minutes; energy 0.3/min; attention 0.2/min | The verified threat falls and a defense result is recorded. |
| Sound Alarm | An installed alarm is ready and a real threat exists. | 3 minutes; attention 0.2/min | A warning event is broadcast. |
| Craft Recipe | The recipe, station and inputs are available. | Recipe-defined: 90–180 minutes in this world | Inputs are consumed and outputs and skill evidence are recorded. |
| Consume Food | You carry `rations`, `ration`, or `cooked_meal`; the model weighs food against other needs. | 1 minute | One item is consumed and Satiety increases. |
| Withdraw Food | You are at the configured food store and have permission. | 15 minutes | One shared ration is reserved and added to inventory. |
| Buy Food | You are at the market with a coin and stock remains. | 10 minutes | One ration is purchased and both reserves decrease. |

## Behavior Trees

| Name | Purpose | Use when | Steps | Time |
|---|---|---|---|---:|
| Govern Village | Review village risk and coordinate policy. | You are the steward and the configured cooldown has elapsed. | Inspect Object | 30 |
| Mediate Conflict | Resolve a visible conflict through conversation. | A conflict exists and you can mediate. | Communicate; verify result | 15 |
| Inspect Granary | Inspect and report public food stock. | Inspection is due and the route is known. | Travel To; Inspect Object | Route + 30 |
| Patrol Route | Inspect a known route and record security information. | A route needs inspection and you are a guard. | Travel To; Patrol Route | Route + 120 |
| Manage Ration | Review stock and coordinate rationing. | The granary keeper has a pending ration decision. | Inspect; Travel; Communicate | 45 + route |
| Heal Target | Restore a target with available treatment. | A nearby target needs treatment and medicine exists. | Heal Target | 90 |
| Explore Greywood | Map Greywood and record findings. | The area is not fully mapped and you can explore. | Travel To; Explore Area | Route + 300 |
| Research Source | Study an accessible source and record cumulative progress. | A relevant source is at the current location and has a new fact. | Research Source | 240 per sample |
| Inspect Object | Inspect an accessible object or facility. | Inspection is due and the object is reachable. | Inspect Object | 30 |
| Defend Village | Respond to a verified boundary alarm. | The alarm was raised, the threat remains high, and you can defend. | Travel To North Gate; Secure Boundary | Route + 120 |
| Defend Position | Hold position and attack if needed. | A reachable threat is active. | Defend; Attack | 36 |
| Flee To Safety | Escape to a safe location. | The threat is overwhelming. | Flee To Safety | Route-dependent |
| Sound Alarm | Raise an audible warning and wake the next response decision. | A threat is detected, the installed alarm is ready, and the cooldown elapsed. | Sound Alarm | 3 |
| Craft Meal | Craft food from available inputs. | A cooking station and recipe inputs are available. | Craft Recipe | 90 |
| Communicate | Exchange information with another character. | A participant is available and its cooldown has elapsed. | Communicate | 15 |
| Negotiate | Reach an agreement with another character. | A counterparty is available and its cooldown has elapsed. | Negotiate | 60 |
| Gather Resource | Collect a configured resource from a known node. | A visible source has supply. | Gather Resource | 180 |
| Eat Food | Carry one configured food item and food is relevant. | Consume Food | 1 |
| Get Food | Have withdrawal rights and a known route to the granary. | Travel To; Withdraw Food | Route + 15 |
| Buy Food | Be at the market with coin and stock. | Buy Food | 10 |
| Recover Caravan Ledger | The Greywood survey revealed the ledger clue. | Recover Object(caravan ledger) | 240 |

All times are platform estimates. Route and capability modifiers can change the
actual value. A selected behavior reserves configured resources before its first
Action, advances one world minute at a time, and persists its result. God can
observe these verified results and propose follow-ups, but cannot execute an
Action directly.
