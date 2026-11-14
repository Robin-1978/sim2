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

### 网页工作台的交互状态

工作台的每个页面都显示同一个草稿版本和编译状态，避免作者把旧诊断误认为
当前结果：

```text
draft → compiling → diagnostics
                 ├── errors → edit draft → compiling
                 └── valid → smoke running → smoke result
                                      ├── failed → inspect event → edit draft
                                      └── passed → review diff → export
```

浏览器保存的是草稿和 `draft_revision`，不是运行时 SQLite。编译或 smoke
返回时必须带回该 revision；如果用户在任务完成前继续编辑，旧结果标记为
`stale`，不能覆盖新的错误列表或绿色状态。导出按钮只对当前 revision 的
完整编译和 smoke 结果开放。

页面布局采用左侧 Schema 导航、中间表单/BT 图、右侧诊断和底部运行日志：

- 字段、标签、单位、枚举和默认值由 `/authoring/schema` 产生；前端不维护
  世界专用的属性名或 Action 名。
- 条件和公式编辑器只提供受限 Expression AST 的节点选择与文本预览，编译
  错误落到字段路径和字符位置。
- BT 图节点只能从已编译的 Action/子 BT 目录中选择；节点旁显示参数 Schema、
  资源锁和可执行角色，未绑定或不可达节点直接标红。
- “运行 smoke”显示临时世界的分钟、第一条失败事件、资源变化和行为闭环；
  它不显示或保存真实实例路径。
- “导出”同时下载世界包、规范化 IR 摘要和校验报告，方便提交审查。

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

接口返回的任务 ID 和草稿版本必须可轮询。建议的最小任务响应为：

```json
{
  "status": "running",
  "job_id": "compile-123",
  "draft_revision": 7,
  "phase": "references",
  "errors": [],
  "warnings": []
}
```

`status=passed` 只表示当前 revision 已完成相应检查；它不代表已经创建
SQLite。只有作者明确调用 CLI `sim2 init`（或后续受控的初始化接口）才会
实例化世界。

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
