# DSL、IR 与 AST 边界

## 当前实现状态

sim2 当前不是一套完整的 AST DSL 编译器。配置文件由 YAML 解析为 Python
字典，`world.definition_compiler` 做结构归一化和校验，随后定义以 JSON
对象写入 SQLite。行为树运行时直接读取归一化后的节点字典，因此它是
声明式 IR（以及逻辑上的树结构），不是已经实现的带节点类型和来源位置的
编译器 AST。

条件也还不是 AST。`actions.conditions.ConditionEngine` 使用有限的字符串
解析规则处理 `and`、`or`、`not`、比较和成员关系。效果公式目前只在
`actions.effects` 中用受限的 Python `ast.parse` 解析算术表达式；这只是
一个安全白名单求值器，不是整个 DSL 的 AST。

## 是否现在实现完整 parser

不实现一套新的、覆盖所有 YAML 字段的大语言。YAML 已经承担配置语法，
再增加一层完整语法会提高世界包和 God 提案的复杂度，不能直接解决运行时
通用性问题。

需要实现的是一个小而稳定的规范化层：

```text
YAML / God proposal
    -> raw mapping
    -> canonical Definition IR
       (action / recipe / behavior / rule)
    -> Expression AST for conditions and formulas
    -> SQLite definition version
    -> AgentView / JSON Schema / Platform execution
```

其中 `Definition IR` 仍允许故事包声明新属性、实体和参数，不把任何世界
名词写进 Python。行为树节点应归一化为带 `kind`、`children`、`action_id`
和来源路径的节点对象；未知节点在提交时被拒绝，而不是由执行器猜测。

## 第一阶段：表达式 AST

先为条件和公式增加受限 parser，语法只覆盖配置已经使用的能力：

- 路径：`actor.state.location_id`、`target.available`、`world.foo`；
- 字面量：字符串、数字、布尔值、空值和列表；
- 运算：`==`、`!=`、`>`、`>=`、`<`、`<=`、`in`、`not_in`；
- 逻辑：`not`、`and`、`or` 和括号；
- 公式：加、减、乘、除和一元正负。

解析结果包含节点类型和配置来源路径。求值器只接受运行时投影，禁止
Python、SQL、Shell、函数调用和任意属性访问。解析后的表达式按定义版本
缓存，避免每个模拟分钟重复做字符串拆分。

这一步会替代当前条件字符串的正则/分割逻辑，并最终替代公式对 Python
AST 的依赖；对 Agent 的工具协议和可见文本没有变化。

## 第二阶段：定义 IR 与行为树节点

`compile_definition_shape` 继续作为入口，但输出增加统一的规范化节点：

- 每个定义带 `definition_id`、`version`、`kind` 和来源路径；
- Action、Recipe、BT、Rule 共用参数、结果、条件和版本元数据；
- BT 节点使用受限的判别联合：`sequence`、`selector`、`parallel`、
  `condition`、`action`、`subtree`；
- Platform 校验器、AgentView 和工具 Schema 都从同一份 IR 派生；
- SQLite 保存规范化定义和版本，运行中的 Process 绑定创建时的版本。

Agent 永远不接收 AST 或内部路径，只接收由 IR 生成的简短可执行视图。
God 可以提出新的定义，但必须提交声明式 IR，经同一编译和语义校验后才
能注册并进入 NPC 的行为目录。

## 验收标准

实现 parser 后必须有确定性测试覆盖：

1. 运算优先级、括号和未知路径的三态结果一致；
2. 非法 Python、SQL、函数调用和未知节点全部被拒绝；
3. 同一配置版本解析结果可缓存且结果不依赖世界名称；
4. God 创建的 Action/BT 能从 IR 生成工具 Schema、AgentView 并进入
   NPC 可执行目录；
5. 旧定义版本的运行中 Process 不受新定义覆盖。

因此当前结论是：**先做表达式 AST，再逐步把行为树和定义归一化为统一
IR；暂时不做一套全新的通用编程语言。**
