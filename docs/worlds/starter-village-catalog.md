# 雾溪村配置目录

本目录列出 Platform 规范配置的世界属性、Agent、物体、能力、元动作和基础 BT。给 LLM 的简明英文目录见 [starter-village-agent-catalog.md](starter-village-agent-catalog.md)。

## 世界属性

| 属性 | 初值 |
|---|---:|
| `fog_level` | 0.1 |
| `water_quality` | 0.72 |
| `public_trust` | 0.55 |
| `village_food` | 320 |
| `winter_days_remaining` | 30 |
| `root_gate_stability` | 0.64 |
| `north_gate_threat_level` | 0.22 |

## Agent 状态

| ID | 角色 | 终极目标 | 初始地点 | 状态 | 初始 BT |
|---|---|---|---|---|---|
| `lin_bo` 林泊 | village_steward | 让雾溪村在下一次深雾周期后保持自治和人口完整 | `village_square` | idle，生命 100，体力 100 | govern_village, mediate_conflict, inspect_granary, sound_alarm |
| `he_yan` 何砚 | watch_captain | 建立能够保护所有人的巡逻和预警体系 | `watchtower` | idle，生命 100，体力 100 | patrol_route, defend_village, sound_alarm |
| `liang_he` 梁禾 | granary_keeper | 让村庄安全度过冬季且不发生饥荒 | `granary` | idle，生命 100，体力 100 | manage_ration, gather_resource, inspect_granary, craft_meal |
| `qiao_ning` 乔宁 | herbalist | 找到灰热病的稳定疗法并恢复河水安全 | `clinic` | idle，生命 100，体力 100 | heal_target, gather_resource, research_source |
| `tian_yan` 田岩 | blacksmith | 在深雾前完成晨钟并为村庄建立可靠的预警工具 | `smithy` | idle，生命 100，体力 100 | craft_meal, gather_resource |
| `lu_xingzhou` 陆行舟 | hunter_mapper | 完成灰林地图并找到失踪的妹妹陆素 | `north_gate` | idle，生命 100，体力 100 | explore_greywood, patrol_route, defend_position, flee_to_safety |
| `yu_ge` 余歌 | innkeeper_information_broker | 把旅店变成各方都能安全交换信息的中立场所 | `inn` | idle，生命 100，体力 100 | communicate, mediate_conflict |
| `fei_lan` 费兰 | traveling_merchant | 恢复商路并找回失踪车队的账册 | `inn` | idle，生命 100，体力 100 | communicate, negotiate, explore_greywood |
| `su_wu` 苏巫 | pact_scholar | 理解根门并决定续约、改约或终止契约 | `old_well` | idle，生命 100，体力 100 | research_source, inspect_object, craft_meal |

## 物体

| ID | 类型 | 名称 | 初始状态 |
|---|---|---|---|
| `dawn_bell` | artifact | 晨钟 | {created: false, durability: 100, alarm_radius: 3} |
| `star_metal_fragment` | material | 星铁碎片 | {quantity: 1, location_id: old_quarry, hidden: true} |
| `old_pact_tablet` | document | 旧契约石板 | {location_id: hidden_root_gate, readable: false} |
| `caravan_ledger` | document | 失踪车队账册 | {location_id: greywood_edge, recovered: false} |
| `water_test_kit` | tool | 水样检测盒 | {location_id: clinic, durability: 3} |
| `ritual_material` | material | 根门仪式材料 | {quantity: 1, location_id: old_well, visibility: keeper_only} |
| `village_seed_grain` | resource | 留种粮 | {quantity: 40, location_id: granary, protected: true} |
| `grain` | material | 谷物 | {quantity: 80, location_id: granary} |
| `cooked_meal` | consumable | 熟食 | {quantity: 0, location_id: granary} |
| `medicine` | consumable | 草药药剂 | {quantity: 0, location_id: clinic} |

## 元动作

| ID | 目的 | 参数 | 时长 | 前置条件 | 成本 | 结果 |
|---|---|---|---:|---|---|---|
| `travel_to` | 沿已知路线移动到目标地点 | {location_id: string} | route_distance 分钟 | route.known<br>actor.mobile<br>destination.reachable | per_minute: {fatigue: 0.5} | 成功：{condition: actor.location == target.location, result: arrived}<br>失败：{condition: actor.incapacitated, result: failed} |
| `rest` | 在安全地点休息 | {location_id: string} | 48 分钟 | actor.at_location<br>location.safe | per_minute: {time: 1} | 成功：{condition: fatigue <= 20, result: rested}<br>失败：{condition: danger.detected, result: interrupted} |
| `gather_resource` | 在可见资源节点采集指定类型资源 | {resource_tag: string, location_id: string} | 144 分钟 | actor.can_gather<br>location.has_food<br>route.known | reserve: {tool_durability: 1}<br>per_minute: {fatigue: 0.8} | 成功：{condition: inventory.food >= 1, result: food_gathered}<br>失败：{condition: actor.incapacitated, result: failed} |
| `patrol_route` | 巡逻已知路线并记录发现 | {route_id: string} | 96 分钟 | actor.is_guard<br>route.known | per_minute: {fatigue: 0.6, exposure: 0.2} | 成功：{condition: route.completed, result: patrol_reported}<br>失败：{condition: hostile_contact, result: emergency} |
| `heal_target` | 使用可用治疗资源恢复目标状态 | {target_id: string} | 72 分钟 | actor.is_healer<br>patient.in_clinic<br>medicine.available | reserve: {medicine: 1}<br>per_minute: {fatigue: 0.4} | 成功：{condition: patient.fever_reduced, result: patient_stabilized}<br>失败：{condition: medicine.failed, result: failed} |
| `explore_area` | 探索一块尚未充分了解的区域并记录发现 | {area_id: string} | 240 分钟 | actor.can_explore<br>route.known<br>area.not_fully_mapped | reserve: {rations: 1}<br>per_minute: {fatigue: 0.7, exposure: 0.4} | 成功：{condition: area.survey_complete, result: exploration_completed}<br>失败：{condition: actor.incapacitated, result: failed} |
| `communicate` | 与目标角色沟通并记录可传播信息 | {target_id: string, topic: string} | 6 分钟 | actor.can_speak<br>target.available<br>communication.channel_open | per_minute: {attention: 1} | 成功：{condition: dialogue.completed, result: dialogue_recorded}<br>失败：{condition: target.refused, result: conversation_refused} |
| `research_source` | 研究可访问的信息源并记录事实 | {source_id: string} | 192 分钟 | actor.can_research<br>source.accessible | per_minute: {fatigue: 0.3, attention: 0.8} | 成功：{condition: research.new_fact_found, result: research_completed}<br>失败：{condition: source.destroyed, result: failed} |
| `inspect_object` | 检查可访问的物体、库存或设施 | {object_id: string} | 24 分钟 | actor.can_inspect<br>location.reachable | per_minute: {attention: 0.2} | 成功：{condition: inventory.read_complete, result: stock_reported}<br>失败：{condition: location.locked, result: denied} |
| `negotiate` | 与目标角色协商交换或契约 | {target_id: string, topic: string} | 36 分钟 | actor.can_negotiate<br>counterparty.available | per_minute: {attention: 0.6} | 成功：{condition: contract.signed, result: contract_signed}<br>失败：{condition: trust.collapsed, result: negotiation_failed} |
| `attack` | 使用指定武器攻击可达目标 | {target_id: string, weapon_id: string} | 6 分钟 | actor.alive<br>actor.can_attack<br>target.visible<br>target.reachable<br>combat.allowed | reserve: {weapon_durability: 0.1}<br>per_minute: {stamina: 1.0, attention: 0.3} | 成功：{condition: attack.resolved, result: attack_completed}<br>失败：{condition: target.out_of_range, result: attack_blocked} |
| `defend` | 保持防御姿态并保护附近目标 | {protected_target_id: string} | 24 分钟 | actor.alive<br>actor.can_defend<br>protected_target.available | per_minute: {stamina: 0.3, attention: 0.4} | 成功：{condition: defense.window_complete, result: defended}<br>失败：{condition: actor.stunned, result: interrupted} |
| `flee_to_safety` | 沿已知路线撤离到安全地点 | {destination_id: string} | route_distance 分钟 | actor.alive<br>route.known<br>destination.safe_or_defensible | per_minute: {stamina: 1.2, fatigue: 0.8} | 成功：{condition: actor.at_safe_location, result: escaped}<br>失败：{condition: actor.exhausted, result: collapsed} |
| `sound_alarm` | 向村庄广播边界或战斗警报 | {alarm_type: string, location_id: string} | 6 分钟 | actor.can_sound_alarm<br>location.watch_post | per_minute: {attention: 0.2} | 成功：{condition: alarm.broadcast, result: alarm_raised}<br>失败：{condition: alarm_device.broken, result: failed} |
| `craft_recipe` | 在可用工作站按配方制作物品 | {recipe_id: string, station_id: string} | recipe_duration 分钟 | actor.can_craft<br>recipe.available<br>station.available<br>inputs.available | reserve: {recipe_inputs: 1}<br>per_minute: {fatigue: 0.4, attention: 0.5} | 成功：{condition: outputs.created, result: recipe_completed}<br>失败：{condition: inputs.lost or station.damaged, result: crafting_failed} |
| `consume_food` | 消耗一份携带的食物恢复饥饿满足度 | {item_id: string} | 1 分钟 | actor.alive<br>actor.has_food | reserve: {item_parameter: item_id} | 成功：{result: ate_food, hunger: +30}<br>失败：{result: eating_failed} |
| `withdraw_food` | 在粮仓从共享库存领取一份口粮 | {} | 1 分钟 | actor.alive<br>location.food_store | reserve: {village_food: 1} | 成功：{result: ration_received, inventory.rations: +1}<br>失败：{result: ration_unavailable} |

## 基础 BT

| ID | 作用域 | 入口条件 | 时长 | 节点 | 成功 | 失败 |
|---|---|---|---:|---|---|---|
| `govern_village` | village_council | actor.is_steward | 144 分钟 | inspect_object → 选择[mediate_conflict] | rations_reviewed, risk_reviewed | council_blocked |
| `mediate_conflict` | personal | conflict.available<br>actor.can_mediate | 48 分钟 | communicate → 条件 | — | — |
| `inspect_granary` | personal | actor.can_inspect_granary | 24 分钟 | travel_to → inspect_object | — | — |
| `patrol_route` | village_watch | actor.is_guard | 96 分钟 | travel_to → patrol_route | patrol_reported, marks_classified | emergency |
| `manage_ration` | personal | actor.is_granary_keeper | 48 分钟 | inspect_object → communicate | — | — |
| `heal_target` | personal | actor.is_healer<br>patient.available | 72 分钟 | heal_target | — | — |
| `explore_greywood` | personal | actor.can_explore<br>area.unknown | 240 分钟 | travel_to → explore_area → communicate | exploration_completed, knowledge_updated | lost, injured, emergency |
| `research_source` | root_keepers | actor.can_research<br>secret.source_available | 192 分钟 | research_source → 条件 | new_pact_fact, ritual_candidate | source_lost, belief_conflict |
| `inspect_object` | personal | actor.can_inspect | 24 分钟 | inspect_object | — | — |
| `defend_village` | village_watch | actor.is_guard<br>threat.at_village | 48 分钟 | sound_alarm → 选择[defend_position, flee_to_safety] | threat_contained, civilians_safe | defense_broken, retreat_required |
| `defend_position` | personal | actor.can_defend<br>threat.reachable | 24 分钟 | defend → attack | — | — |
| `flee_to_safety` | personal | actor.alive<br>threat.overwhelming | 96 分钟 | flee_to_safety | — | — |
| `sound_alarm` | village_watch | actor.can_sound_alarm<br>threat.detected | 6 分钟 | sound_alarm | — | — |
| `craft_meal` | personal | actor.can_craft<br>recipe.available<br>inputs.available | 60 分钟 | craft_recipe | food_created, capability_improved | crafting_failed |
| `communicate` | personal | actor.can_communicate<br>target.available | 12 分钟 | communicate | — | — |
| `negotiate` | personal | actor.can_communicate<br>target.available | 12 分钟 | negotiate | — | — |
| `gather_resource` | personal | actor.can_gather<br>resource.available | 120 分钟 | gather_resource | action_completed | action_failed |
| `eat_food` | universal | actor.alive<br>actor.has_food | 1 分钟 | consume_food(item_id) | ate_food | eating_failed |
| `get_food` | universal | actor.alive<br>location.food_store | 1 分钟 | withdraw_food | ration_received | ration_unavailable |

## Platform 执行边界

动作和 BT 配置只声明目标、条件、成本、时长、效果和结果。Platform 使用通用 `target_resolver`、`condition_engine`、`effect_pipeline` 和 `event_writer` 执行；LLM 只提交 AgentView 中的稳定引用、目标和参数。

执行中的 Action 默认不接受外部即时互动；需要在同一 tick 继续聊天或交易的 Action 显式声明 `interaction_policy: allow`。雾溪村允许休息、巡逻、治疗、检查、防御和报警期间互动；移动、探索、制作、攻击、逃跑和研究期间不允许互动。暂停保留游标和资源锁，取消才释放资源并结束当前 BT。
