# World Authoring Web 工作台

## 目标

世界包目前已经能通过 `sim2 world-schema`、`build-world`、`validate-world`
和 `describe-world` 生成、检查和初始化，但直接维护多份 YAML 仍然要求
作者理解较多的字段关系。网页工作台应降低配置门槛，同时保持一个事实：
它是现有 Definition Compiler 的用户界面，不是另一套 DSL，也不直接修改
运行中的世界。

## 单一契约

工作台只从以下接口生成编辑器和校验结果：

```text
world-schema / canonical Definition IR
        ↓
表单、关系选择器、BT 图编辑器、条件编辑器
        ↓
统一 YAML 模板 / 世界包文件
        ↓
结构编译 + Expression AST 编译
        ↓
引用、权限、可达性、资源守恒和执行性检查
        ↓
临时 SQLite smoke world
```

Action、Recipe、BT、Attribute、Capability、Entity、Rule 和 Evolution 的
字段说明、枚举、默认值、必填关系和错误路径都由 Schema/IR 提供。网页
代码不应重新复制 `success`、`failure`、效果原语或条件语法的规则。

## 页面和工作流

首版只需要五个工作区：

1. **World**：世界元数据、时钟、属性、地点、路线和地图位置。
2. **Entities**：资源、物体、人物、势力、关系、库存和初始知识。
3. **Definitions**：Action、Recipe、Capability 和 BT；BT 使用节点图，
   节点仍只能引用已注册的 Action 或子 BT。
4. **Evolution**：Story/God 的事件唤醒、证据计数、冷却、工具契约和
   研究阶段。页面显示“谁被什么已提交事件唤醒”，不允许用日历时间
   隐式创建任务。
5. **Compile & Test**：显示结构错误、AST 错误、引用错误、未绑定 Action、
   无法执行的 BT、资源守恒错误和 smoke run 的第一条失败结果，并支持
   导出 YAML、JSON Schema 校验报告和临时 SQLite 预览。

编辑流程是：编辑草稿 → 保存到工作区 → 编译 → 修复错误 → 运行短 smoke
test → 查看 diff → 导出世界包。只有显式执行 `sim2 init` 才创建正式
SQLite 实例；工作台不能直接修改正在运行的实例。

## 校验层次

每次保存都可以执行便宜的增量检查；发布前执行完整检查：

| 层次 | 检查 | 权威实现 |
|---|---|---|
| 语法 | YAML/JSON 结构和字段类型 | Schema loader |
| 表达式 | 条件、公式的受限语法、括号和路径 | `sim2.dsl.expressions` |
| 定义 | Action/BT/Recipe 的形状、结果契约和禁止代码字段 | Definition Compiler |
| 实例语义 | 实体引用、地点可达性、目标、权限、资源守恒 | Platform definition runtime |
| 执行性 | 新 Action 是否绑定可执行 BT，BT 是否能由至少一个角色执行 | Platform + AgentView |
| 运行 smoke | 初始化临时 SQLite，启动一个初始行为并检查结果事件 | Bootstrap/Platform |

错误必须返回 `path`、`code`、`message`、`severity` 和可选的相关定义；
例如 `behavior_trees[2].nodes[1].action_id` 未注册，而不是只返回“配置无效”。
条件错误应指出表达式位置和期望语法，不能让 Agent 或作者猜测。

## 后端接口计划

现有 HTTP API 主要服务运行中的世界。authoring 工作台需要单独的草稿
资源，不把草稿混进运行时 SQLite：

- `GET /authoring/schema`：返回当前 World Template 和 Definition Schema；
- `POST /authoring/compile`：编译内存中的模板，返回规范化 IR 和错误；
- `POST /authoring/smoke`：在临时目录创建 SQLite 并运行有限分钟；
- `POST /authoring/export`：导出 YAML 世界包和校验报告；
- `GET /authoring/diff`：比较草稿与上一次编译产物；
- `GET /authoring/jobs/{id}`：读取 smoke/compile 的结构化结果。

这些接口应调用现有 bootstrap、Definition Compiler 和 Platform 服务，
不能在 HTTP handler 中复制校验或直接写数据库。草稿文件由工作区管理；
运行中世界仍只能通过 Platform 的状态变更入口操作。

## 实现顺序

先做无界面的可复用服务，再做网页：

1. 将 `validate-world` 的结构化错误和 smoke 结果收敛为
   `WorldAuthoringService` 返回值；
2. 暴露只读 Schema、编译、diff 和 smoke HTTP 路由；
3. 用少量原生 HTML/TypeScript 页面生成表单和 BT 图，不引入新的运行时
   领域模型；
4. 添加 unrelated world、动态 God 定义、非法条件和未绑定 Action 的
   端到端测试；
5. 再增加地图画布、版本历史和多人协作等功能。

首版不做任意代码编辑器、在线修改运行中 SQLite、自动发布或让网页绕过
Platform。世界演化产生的运行时定义仍通过 God Intent → Definition
Compiler → Platform → SQLite 进入实例；工作台只帮助人创建和审查初始包。
