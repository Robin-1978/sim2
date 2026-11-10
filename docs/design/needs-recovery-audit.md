# Need and Attribute Recovery Audit

Every numeric state must have a configured way to decline, recover, or receive a meaningful outcome. A need is not automatically “recovered” by adding a BT; the BT must have a Platform effect that changes the value or records a valid social/goal outcome.

| Attribute | Meaning shown to Agent | Pressure source | Required BT/effect | Current audit |
|---|---|---|---|---|
| Configured vital attribute | The world-defined value at its incapacitation bound | damage, illness, accidents | A configured treatment or recovery BT applies `attribute_delta` to the declared vital attribute | **Covered by package configuration** |
| Configured capacity attribute | The world-defined short-term action capacity | long actions and movement | Rest at current safe place; move to a configured safe place then rest | **Covered after `recover_at_safe_location` in the starter package** |
| Satiety | 0 = starving; 100 = full | world-time decay | Eat carried food; withdraw from the granary; buy at the configured market; produce food through gather → craft → store | **Covered for survival**: missing food now has a coin-and-market path, and starvation damages health through a configured hourly rule |
| Safety | 0 = threatened; 100 = secure | threats, exposure, hostile events | Flee/seek shelter, defend, alarm, and a resolved safe-location state | **Covered at the action boundary**: response reports wake Story/God; policy changes remain external |
| Belonging | 0 = socially isolated; 100 = connected | relationship loss, exclusion, conflict | Communicate, socialize, mediate, and apply configured relationship/need outcomes | **Covered**: communication applies configured belonging/trust effects; `mediate_conflict` records a mediation result and trust change when a visible conflict exists |
| Esteem | 0 = no competence/recognition; 100 = respected | failure, loss of status, norm pressure | Competent service, repair, governance, or recognized achievement with configured outcome | **Covered for configured duties**: governance and patrol apply personal esteem effects |
| Self-actualization | 0 = no goal progress; 100 = fulfilled | blocked goals, lack of novelty/meaning | Explore, research, craft, solve goals, and record actual goal progress | **Partial**: exploration/research close knowledge loops; goal-specific progress remains package configuration |
| Exposure | 0 = sheltered; 100 = highly exposed | travel, patrol, exploration, hostile environment | Rest/shelter and idle recovery reduce exposure | **Covered** by configured rest and idle effects |
| Attention load | 0 = fully available; 100 = occupied | communication, research, inspection, combat | Rest and idle recovery reduce attention load | **Covered** by configured rest and idle effects |

The core BT package must cover the first four survival and safety paths. Social and growth needs use outcome-producing BTs rather than an artificial “restore” action. The validator should reject a world package when a configured need has neither a recovery/maintenance BT nor an explicit external-only declaration.

Self-actualization remains intentionally package-specific: exploration and research close their knowledge/progress loops, while a story package must define any additional goal progress. This is a declared extension point, not a missing universal recovery action.

The baseline DeepSeek run had 279 food actions at high satiety and 182 rest actions at full energy. The current deterministic audit at world minute 20,160 has no degeneration flags, one unique dawn bell, one unique exploration result, 15 bounded communications with relationship changes, 30 retained memories, no incapacitated Agents, and two structured God world-property updates. The old baseline remains preserved in [deepseek-long-baseline-audit.json](../artifacts/deepseek-long-baseline-audit.json).
