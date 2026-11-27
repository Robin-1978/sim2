# DSL、IR 与 AST 边界

## 世界包和模型提案

YAML 是世界包的作者格式，适合阅读、评审和版本管理。世界编辑器可以生成
同一份配置。模型提案使用 JSON Schema。两条路径验证声明式定义，Platform
负责绑定实例里的实体、校验行动并提交状态变化。

YAML 中的短谓词保持一行。需要换行时可以用折叠标量。

```yaml
preconditions:
  - >-
    actor.alive and context.day_phase == 'dawn'
    and world[context.reserve_key] >= 2
success:
  condition: context.calendar_day == 1
  result: supplies_delivered
```

## 表达式编译与求值

`sim2.dsl.expressions` 把逻辑、比较、成员关系、列表和基本算术解析为不可变 AST。
检查引用时遍历 AST，因此字符串 `'world.example'` 仍是值。
`world["public confidence"]` 和 `world[context.reserve_key]` 使用索引节点，
求值阶段读取当前绑定。缓存以去掉首尾空白的表达式文本为键，最多保留
4,096 棵树。条件和公式共用语法树，每次求值读取当前角色与世界数据。

行为条件与 Director 的事件证据条件共用 AST 求值器。行为条件从角色或世界读取
字段，证据条件从一条已提交事件的 `event.*` 字段读取。两者共用逻辑运算、比较、
短路和缺失值处理。行为树的分钟预估和持久化执行计划仍有各自的遍历逻辑。

包校验和 Action 提案使用同一组条件字段和语法检查。成功条件中的独立旧式结果
标签继续兼容，例如 `resource.collected`。带运算符的成功条件必须得到可判定的
真值才会提交效果。运行时遇到旧定义中的非法表达式会判定失败。

Action 的预览、单步启动、并行启动和结算共享条件投影。日历提供
`context.day_phase`、`context.calendar_day` 和 `context.minute_of_day`。
执行阶段取当前处理的模拟分钟。成功条件在效果应用后检查，判失败时回滚
同一次 Action 的效果和事件。

世界包和 God 动态提案共用两项效果校验。`entity_state_set` 需要实体、状态字段
和显式的 `value`。可以写 `value: false`、零或 `null`。`public_event` 用
`event` 指定事件名。旧存档中的 Action 或行为结算遇到缺少这些字段的效果时，
会回滚同批效果，角色不会得到一条虚假的完成记录。

`progress_effects` 每分钟更新行动者的状态、需求、库存、位置或状态标记。
发布公共事件或改变其他实体应写在 `effects`，在行动成功时结算。
配方的 `effects` 支持实体状态更新和能力经验凭据，其他结算效果写在执行配方的 Action 上。
行动成本的 `reserve`、`per_minute`、`on_success`、`on_failure`、`refund`
均使用映射。例如 `costs: {reserve: {wood: 1}}`。动态提案填错类型时，God 会收到
具体字段路径，可以在同一轮调用中修正。

## 行为实例和版本

接纳行为时，实例保存完整的执行计划、各分支的 Action 定义、配方和行为定义。
行为的结束效果、事件和事实来自实例保存的定义。升级旧包前，会把仍在
运行或暂停的旧实例补上当时注册的定义。升级之后新建的行为读取新版定义。
世界状态和当前规则仍随世界推进。

## 行为树流程控制

`sequence` 依次执行子节点。`condition` 在到达节点时检查当时的角色、目标和世界
状态，可以通过 `context.last_action_result` 读取本次行为最近一次成功结算的 Action
结果。`wait` 用正整数 `minutes` 指定等待时长，暂停与重开存档保留剩余时间。

```yaml
nodes:
  - type: selector
    children:
      - type: sequence
        children:
          - {type: condition, expression: "world.route_open == 1"}
          - {type: action, action_id: depart}
      - type: sequence
        children:
          - {type: wait, minutes: 3}
          - {type: condition, expression: "world.route_open == 1"}
          - {type: action, action_id: depart}
```

`selector` 按配置顺序尝试子分支。任一子分支成功即结束选择。条件失败或 Action
无法启动时，可以在当前分钟尝试后备分支。消耗过时间的 Action 结算失败时，后备
分支从下一分钟开始。失败的 Action 退回未消耗的预留资源，已经结算的其他 Action
继续保留效果。决策视图会检查当前可以开始的分支，后续条件由实际运行时校验。

`parallel` 同时启动组内 Action 和等待节点，可作为选择器的一个分支。需要顺序执行
的步骤放在并行组之前或之后。配置校验和动态定义提交都会检查展开后的子树，拒绝
并行组内的多步顺序分支、条件和选择器，允许嵌套并行组。
授权操作通过 `entry_conditions` 设置条件。

旧的执行实例沿用其已保存的 Action 序列。雾溪村 0.1.1 的调解条件使用实际的对话
记录，研究条件接受分次推进与完成的结果。用 `scripts/upgrade_instance.py` 把新版本
应用到已有村庄存档后，新开启的行为使用这些条件。
