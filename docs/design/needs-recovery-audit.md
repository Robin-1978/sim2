# Need and Attribute Recovery Audit

Every numeric state must have a configured way to decline, recover, or receive a meaningful outcome. A need is not automatically “recovered” by adding a BT; the BT must have a Platform effect that changes the value or records a valid social/goal outcome.

| Attribute | Meaning shown to Agent | Pressure source | Required BT/effect | Current audit |
|---|---|---|---|---|
| Health | 0 = incapacitated; 100 = healthy | damage, illness, accidents | Seek treatment / heal self; `health_restore` must target the actor or a permitted clinic service | **Gap**: only `heal_target` exists; no self-treatment path |
| Energy | 0 = unable to continue; 100 = ready | long actions and movement | Rest at current safe place; move to a configured safe place then rest | **Covered** after `recover_at_safe_location` |
| Satiety | 0 = starving; 100 = full | world-time decay | Eat carried food; acquire food; produce food through gather → craft → store | **Partial**: eating/acquisition exist; production chain and stock motive are incomplete |
| Safety | 0 = threatened; 100 = secure | threats, exposure, hostile events | Flee/seek shelter, defend, alarm, and a resolved safe-location state | **Partial**: emergency trees exist; ordinary safety recovery is not unified |
| Belonging | 0 = socially isolated; 100 = connected | relationship loss, exclusion, conflict | Communicate, socialize, mediate, and apply configured relationship/need outcomes | **Gap**: communication is recorded but does not consistently change belonging |
| Esteem | 0 = no competence/recognition; 100 = respected | failure, loss of status, norm pressure | Competent service, repair, governance, or recognized achievement with configured outcome | **Gap**: actions carry tags but rarely produce esteem effects |
| Self-actualization | 0 = no goal progress; 100 = fulfilled | blocked goals, lack of novelty/meaning | Explore, research, craft, solve goals, and record actual goal progress | **Partial**: actions exist, but goal-progress effects are not consistently configured |
| Exposure | 0 = sheltered; 100 = highly exposed | travel, patrol, exploration, hostile environment | Rest/shelter and idle recovery reduce exposure | **Covered** by configured rest and idle effects |
| Attention load | 0 = fully available; 100 = occupied | communication, research, inspection, combat | Rest and idle recovery reduce attention load | **Covered** by configured rest and idle effects |

The core BT package must cover the first four survival and safety paths. Social and growth needs use outcome-producing BTs rather than an artificial “restore” action. The validator should reject a world package when a configured need has neither a recovery/maintenance BT nor an explicit external-only declaration.

The baseline DeepSeek run confirms the audit is needed: by world minute 2257 it had 279 food actions at high satiety and 182 rest actions at full energy, while only two durable experiences were recorded and no world proposal was applied. The report is preserved in [deepseek-long-baseline-audit.json](../artifacts/deepseek-long-baseline-audit.json).
