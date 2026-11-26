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

包校验和 Action 提案使用同一组条件字段和语法检查。成功条件中的独立旧式结果
标签继续兼容，例如 `resource.collected`。带运算符的成功条件必须得到可判定的
真值才会提交效果。运行时遇到旧定义中的非法表达式会判定失败。

Action 的预览、单步启动、并行启动和结算共享条件投影。日历提供
`context.day_phase`、`context.calendar_day` 和 `context.minute_of_day`。
执行阶段取当前处理的模拟分钟。成功条件在效果应用后检查，判失败时回滚
同一次 Action 的效果和事件。

## 行为实例和版本

接纳行为时，实例保存选中的 Action 序列、Action 定义、配方和行为定义。
行为的结束效果、事件和事实来自实例保存的定义。升级旧包前，会把仍在
运行或暂停的旧实例补上当时注册的定义。升级之后新建的行为读取新版定义。
世界状态和当前规则仍随世界推进。

行为树当前展开为可执行 Action 序列，选择节点延续已有的分支规则。条件节点的
完整树执行语义仍属于独立的行为树演进工作。
