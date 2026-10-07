# shaftmachiningplanner：工业 Agent 技术研究与升级路线

研究日期：2026-10-06（Asia/Shanghai）。本文保留最初选型时的架构观察与分阶段建议。后续于 2026-10-07 实现了工序状态校验、评测、模型用量记录、可选 GEPA 提示词优化入口和腾讯记忆只读适配器；使用方法见 `evaluation/README.md` 与 [腾讯接入评估](tencentdb-agent-memory-integration.md)。未在真实设备上验证，也未证明真实模型优化增益。

## 判断

当前架构已有 LangGraph、受限 Planner、任务合同、并行专家、证据引用、规则校验、增量重跑和 SQLite 人工中断恢复。下一步优先补足可衡量的持续改进、工艺状态约束及真实资源数据，而不是增加无明确职责的 Agent。

必须区分两个图：

- Agent 任务 DAG：路线生成、资源匹配、各领域评审，调度计算任务。当前已实现。
- 制造工序图：工序先后关系、机床占用、换装、班次、加工时间与交期。当前不能因存在前一种 DAG 而声称实现了车间排程。

## 本次检查范围

读取当前项目 README、工作流、调度器、Planner、专家、工具注册、RAG 接口、LLM 客户端及相关测试。检查 9 个外部仓库的公开 GitHub API 元数据和文件树，阅读仓库说明，并抽查 3 个仓库共 6 个公开源码文件。未运行这些外部仓库；文件名包含 test 不代表测试有效或 CI 通过。

## GitHub 候选与适配判断

| 仓库 | 可借鉴的技术 | 对本项目的用法 | 边界与优先级 |
|---|---|---|---|
| [rwth-iat/MP-LLM](https://github.com/rwth-iat/MP-LLM) | LLM 候选生成、FSA 状态检查、BFS 与优化求解回退 | 将轴加工工序表示为有前置条件和状态变化的操作，检查热处理、精加工、余量与装夹等约束 | 高优先级；原场景是 ISA-88 模块化过程装置，必须重建轴加工规则，不能直接搬用。研究软件，未证明本项目适用性 |
| [gepa-ai/gepa](https://github.com/gepa-ai/gepa) | 依据执行轨迹和自然语言失败反馈演化提示词，使用 Pareto 搜索保留互补候选 | 离线优化 Planner 和专家提示词；从现有 trace 提取反例与评分 | 高优先级；先建立可信评测集，冻结测试集，不让优化器修改生产规则或自动上线 |
| [aws-samples/sample-ukg-for-mfg](https://github.com/aws-samples/sample-ukg-for-mfg) | Discovery 与 Explorer 分工、ISA-95 概念映射、跨 MES/ERP/CMMS/PLM 查询 | 建立工序—资源—设备—工单—维护记录的语义注册表，查询时携带来源和时间 | 中高优先级；AWS 示例，借鉴数据合同而非整体迁移云架构；自动字段映射需要复核 |
| [OPCFoundation/UA-.NETStandard/McpServer](https://github.com/OPCFoundation/UA-.NETStandard/tree/master/Applications/McpServer) | OPC UA 数据访问包装成 MCP 工具，浏览、读取、历史和订阅 | 为资源匹配提供设备状态、维护状态和数据时间戳；先连接模拟服务 | 中优先级；该目录也暴露写入、方法调用和配置管理，接入前实施服务端只读工具白名单与最小权限。基金会项目不等于该 MCP 子模块具备现场部署认证 |
| [JoMinsu-KU/A2M](https://github.com/JoMinsu-KU/A2M) | AAS 资产元数据、MCP 工具与制造流程发现 | 将机床、刀具、装夹的能力描述与工具参数关联；人工审核后注册查询工具 | 中优先级；公开数据为部分资产；README 的实线部署描述属于作者报告。本次源码确实有 PLC 写入函数，但未复现作者实验 |
| [ekhurtado/SMIA](https://github.com/ekhurtado/smia) | AAS 合规工业 Agent、资产能力与服务发现 | 参考统一资产模型、语义能力查询及资产侧协商 | 中优先级；这是工业 MAS/数字孪生方向，不等同于 LLM 多 Agent。GPL-3.0，代码复用需单独评估许可兼容性 |
| [OpenFactoryTwin/ofact](https://github.com/OpenFactoryTwin/ofact) | 订单、资源、过程和事件组成的工厂状态模型，场景仿真 | 当具备加工时间、故障、班次等数据后，比选故障换机、插单、外协情景 | 后续阶段；Agent 包含传统仿真 Agent。缺少真实参数时只能做明确标注的情景演示 |
| [aimclub/SAMPO](https://github.com/aimclub/SAMPO) | 资源约束任务图、HEFT、遗传算法、多目标排程 | 参考独立的制造排程求解层与不可行解释 | 后续阶段；不能把其多 Agent 调度等同于 LLM Agent，也不能默认完整覆盖轴加工车间约束 |
| [microsoft/agent-lightning](https://github.com/microsoft/agent-lightning) | 在真实 Agent harness 中收集交互并做强化学习 | 有足够可信样本、奖励函数和训练预算后再考虑本地模型训练 | 暂缓；v1.0 的 GPU/verl/vLLM 训练栈与当前轻量应用不同，公开代码任务成绩不能外推到制造规划 |

## 可复核的源码证据

### 工艺状态检查

- [MP-LLM 校验器](https://github.com/rwth-iat/MP-LLM/blob/1edb61b265d2674861821ecd0c489ab9e7132f28/ModPlant_ui_lib/ModPlant_fsa_checker_core.py)：`check_prediction_against_rules` 从第 240 行起执行预测与规则匹配。
- [MP-LLM 会话控制](https://github.com/rwth-iat/MP-LLM/blob/1edb61b265d2674861821ecd0c489ab9e7132f28/ModPlant_ui_lib/session.py)：第 304 行的 `step_validator` 检查生成序列的当前前缀，并接入推理会话。
- 以上验证的是其装置模型内的可行性，不是通用制造正确性证明。

### 跨系统语义关联

- [AWS 数据注册](https://github.com/aws-samples/sample-ukg-for-mfg/blob/583018d336e18e86f6dc9366c3d0608d7ac65e55/agent-discovery/tools/register.py)：第 294 行起的 `register_equivalences` 拒绝未注册系统或表的关联。
- [AWS 系统查询](https://github.com/aws-samples/sample-ukg-for-mfg/blob/583018d336e18e86f6dc9366c3d0608d7ac65e55/agent-explorer/tools/query_system.py)：包含 SQL 修改关键词拒绝、查询条数限制以及 OpenAPI GET 查询。不能仅凭关键词过滤推断整个系统具备严格只读安全性。

### 设备调用边界

- [A2M MCP 服务](https://github.com/JoMinsu-KU/A2M/blob/404955f404bd7239c29052325751093d6f5e4eb7/AI_Agent/mcp_server.py)：包含 `start_manufacturing`、`set_coil_turn` 和 `ModbusTcpClient`。它不是纯只读知识库。
- [A2M 工具包装](https://github.com/JoMinsu-KU/A2M/blob/404955f404bd7239c29052325751093d6f5e4eb7/AI_Agent/Tool/tool_wrapper.py)：同步包装异步工具。检查了此文件，但未运行设备调用。

## 版本快照

以下是本次读取的默认分支最新提交日期（UTC），不是 release 日期，也不同于仓库 `pushed_at`。分支活跃和存在测试文件不能替代运行验证。

| 仓库 | 默认分支提交日期 | 固定提交 | GitHub 许可标识 |
|---|---|---|---|
| MP-LLM | 2026-07-14 | `1edb61b265d2674861821ecd0c489ab9e7132f28` | MIT |
| sample-ukg-for-mfg | 2026-07-09 | `583018d336e18e86f6dc9366c3d0608d7ac65e55` | MIT-0 |
| A2M | 2025-08-08 | `404955f404bd7239c29052325751093d6f5e4eb7` | Apache-2.0 |
| UA-.NETStandard（全仓库） | 2026-10-06 | `e78c6482958ad431d5a1f11a335bf3630fa6cd2c` | NOASSERTION；需核对实际许可及子模块 |
| GEPA | 2026-10-01 | `fb1ed589fd83372caef499cffc2c73173d3b096b` | MIT |
| Agent Lightning | 2026-09-29 | `d381995396274039f2bb1cbe5ff42ac8067f4e47` | MIT |
| OFacT | 2026-03-10 | `45d044da62e7e3f993845c41b37471ea16bb2771` | Apache-2.0 |
| SAMPO | 2025-10-02 | `81ca965176988d4bfa626d250a16114eead46e3c` | BSD-3-Clause |
| SMIA | 2026-10-05 | `6c66601a1234e76216bbd0ac3d3376abc0fda697` | GPL-3.0 |

## 分阶段升级建议

### 第一阶段：真实案例评测与失败反馈优化

接入点：`backend/models/workflow.py`、`backend/llm_client.py`、`backend/agents/planner.py`、`backend/agents/specialists.py`。

当前 trace 已记录输入输出、工具和时长；LLM 客户端返回文本，尚未把 usage 与模型/提示词版本形成可直接用于对比的完整评测记录。本次未找到独立真实模型运行基准集；已有单元和集成测试不能替代它。

建议先由工程师确认一组代表性轴件及不可行反例，建立可重复评测，记录：

- 工艺硬约束违反情况，以及不可行方案被错误判为可行的情况。
- 关键问题发现、无依据建议及引用证据是否真正支持结论。
- 缺失资料是否明确标记；模型、RAG、工具失败时降级是否合规。
- 修复后应失效任务是否重跑，不受影响任务是否复用。
- 端到端时长、实际 token、模型费用与人工修改量；缺失字段明确标为未采集。

把同类轴件变体放在同一数据划分，避免优化器记住近似案例。优化用训练与验证集，最终测试集冻结。用 GEPA 离线提出提示词候选，针对硬约束设置拒绝门槛，保留旧版本和回滚入口。只优化提示词的第一版不允许修改规则、权限或生产放行条件。

参考 [OpenAI agent evals](https://developers.openai.com/api/docs/guides/agent-evals)：从执行轨迹评分走向可重复数据集与评测运行。无需为使用此思路迁移现有 LangGraph 框架。

### 第二阶段：工序状态约束

接入点：`backend/rules/engine.py`、`backend/models/process.py`、`backend/workflow/nodes/verification.py`。

将零件状态、余量、基准、热处理状态和已完成特征明确建模；为操作定义工程确认的前置条件与状态变化。对候选路线逐步检查，返回具体工序、状态冲突和违反的规则。有限修复后再次独立检查。求解器若加入，应使用领域定义的操作集合与约束，而不是把自由文本直接当合法操作。

这与当前几何及资源检查互补；不得因使用 FSA 或求解器就宣称覆盖所有装夹、刀具、材料与加工物理条件。

### 第三阶段：工厂数据与能力图

接入点：`backend/repositories.py`、`backend/workflow/tool_registry.py`、`backend/agents/specialists.py`。

先用模拟 OPC UA 和模拟 MES 提供只读适配器，统一资产 ID、能力、状态、来源、采集时间与质量标识。资源匹配应区分“型号理论能力符合”“当前可用”“数据过期”和“未知”，不能从制造商样本推断现场库存或可用性。

借鉴 AAS 和 ISA-95 做语义关联，模型仅提议字段映射，工程人员确认关键字段。MCP 是工具接口协议，不提供资产数据可信性或工程可行性证明。

### 第四阶段：制造排程与情景仿真

新增独立工序图和资源日历。需要实际或明确标注的加工时间、换装时间、工艺先后、设备占用、维护与交期数据。确定性求解器负责可行性与目标优化，Agent 负责解释缺口及建议情景。

可以借鉴 SAMPO 的资源约束调度与 OFacT 的事件状态模型，但先做小范围排程验证，不直接引入完整工厂孪生。对模拟结论标明参数来源、假设与未校准范围。

## 持续跟踪的筛选规则

优先观察上述仓库及工业 process planning、AAS、ISA-95、OPC UA、MES、约束排程与 Agent evaluation 领域的新实现。每次候选都需要说明：新能力、证据与固定版本、本项目具体接入点、所需数据与依赖、许可、验证方法及回滚方式。

信息更新与代码上线分别处理：每周检索只在有实质进展时报告；技术试验在隔离分支进行；基准验证通过并完成工程审查后才考虑替换默认路径。不根据 star 数、宣传词、其他领域 benchmark 成绩或单次演示自动升级。
