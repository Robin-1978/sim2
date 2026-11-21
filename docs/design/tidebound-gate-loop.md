# 潮门：从单血条到两段式公共工程

## 问题

旧版潮门只有一条 `tide_gate_integrity`：

- 初始 28%，每次 `repair_gate` +28%，两次修复到 84%，风暴阈值 80%。
- 风暴到点自动结算，玩家在最后阶段没有动作。
- 临时支撑只加 8% 完整性，永远不是必需项。
- 玩家修完只能干等，NPC 的测量、打捞、分工规则在界面上不可见。

结果是一个“点两次修复”的清单，而不是公共工程。

## 新循环

潮门现在有两条必须同时满足的防线：

| 防线 | 属性 | 初始 | 目标 | 主要来源 |
| --- | --- | --- | --- | --- |
| 结构 | `tide_gate_integrity` | 28% | 80% | `repair_gate` +28%，`repair_priority_today` +5%，`final_night_repair` +10% |
| 临时支撑 | `gate_supports` | 15% | 40% | `brace_gate` +15%，`repair_priority_today` +10%，`final_night_repair` +15% |

第一场风暴的可行路径不是唯一的：

- 公共决策“优先抢修”+ 一次支撑件 + 两次修复：结构 89%，支撑 40%。
- 两次支撑件 + 两次修复：结构 84%，支撑 45%。
- 夜班紧急修复可以把 70%/25% 最后一夜拉到 80%/40%。

支撑件由 `gate_brace` 配方制作（浮木 1 + 芦苇绳 1），不消耗修理工具包；工具包仍然只用于结构修复。

## 风暴最后六小时

`storm_eta_hours <= 6` 时：

1. 分钟效果把 `storm_final_watch` 从 0 置为 1。
2. world rule 发布 `storm.final_watch`，唤醒 Story/God。
3. 进度目标 `storm_final_watch` 出现三个互斥选择：
   - **通宵抢修潮门**：结构 +10%、支撑 +15%、信心 +5%，180 分钟。
   - **撤走公共物资**：`storm_prepared=1`，信心 +2%，90 分钟；风暴失败时损失减半。
   - **让居民避风**：信心 +12%，归属 +3，120 分钟。
4. 任一行动把 `storm_final_watch` 从 1 置为 2，另外两个行动立即消失；风暴结算时重置为 0。

`storm_prepared` 只影响失败分支：

| 结果 | 条件 | 未准备 | 已准备 |
| --- | --- | --- | --- |
| 守住 | 结构 >= 80% 且支撑 >= 40% | 信心 +5%，支撑 -30% | 同左 |
| 被撕开 | 结构 >= 20%，且结构 < 80% 或支撑 < 40% | 结构 -16%，贝类 -10，芦苇 -8 | 结构 -10%，贝类 -4，芦苇 -3 |
| 被淹没 | 结构 < 20% | 信心 -22%，浮木 -8，淡水 -6 | 信心 -10%，浮木 -3，淡水 -2 |

风暴后临时支撑会衰减：守住 -30%，被撕开 -50%，被淹没 -60%。下一轮风暴需要重新补支撑，结构修复不会自动补支撑。

## NPC 与 Agent

- `gate_repair_needed` 在结构或支撑不足时触发，唤醒 Story/God。远程运行中 God 会生成“双人抢修分工”规则，禁止单人反复修补。
- 玛拉可以修复结构或支撑；伊沃负责浮木、芦苇和工具包；塞琳研究海图；塔罗勘察观测台。
- 玩家界面在“居民动向”里显示这些决定和公开理由；`brace_gate`、`repair_priority_today` 和最终三选一都由 Platform 的同一套行为校验执行。

## 界面

- `ui.crisis_metrics` 用“临时支撑”替换“公共工具包”：潮门完整性、临时支撑、风暴倒计时、公共信心。
- 潮门设施卡显示完整性、临时支撑、已修复次数。
- 章节、上手提示、公共决策和结局统计都写入两条防线的目标。
- 最终六小时由普通目标卡呈现，三个 ready behavior 直接可点。

## 验证

- `tests/test_tidebound_commons.py::test_first_storm_coop_path_uses_the_public_tool_rack`：两次修复 + 一次支撑，风暴守住。
- `test_structure_alone_breaches_when_supports_are_missing`：结构 85%、支撑 15% 仍被撕开。
- `test_gate_supports_are_required_and_final_watch_evacuation_reduces_losses`：最后六小时开启、撤离减少贝类损失、结算后状态复位。
- `test_final_night_repair_can_close_the_last_gap`：70%/25% 经夜班修复到 80%/40%，风暴守住，支撑衰减到 10%。
