# shaftmachiningplanner 与 TencentDB-Agent-Memory 接入评估

核对日期：2026-10-07。结论：**可以结合，适合先作为可选历史经验层；当前无需迁移业务数据库。**

核对仓库为 [TencentCloud/TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)，默认分支 `feat/server_team`，固定提交 `8b86874a2daea49e3ff0fb53d699203146c5c77d`。
源码 [LICENSE](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/8b86874a2daea49e3ff0fb53d699203146c5c77d/LICENSE) 为 MIT；GitHub 元数据的 NOASSERTION 不能替代实际许可证。
该提交 README 标识 Team Memory Beta，接入合同应固定版本并随升级复验。

## 适合什么

| 数据 | 本阶段处理 | 原因 |
|---|---|---|
| 过去发现的工艺缺口、常见错误、历史协作经验 | 检索为未确认参考，保留记录 ID/版本 | 可提示专家重点检查，仍要核对当前零件 |
| 当前图纸、材质、尺寸、公差 | 继续由结构化请求提供 | 历史记忆不能覆盖本次输入 |
| 当前设备、刀具、供应商能力 | 继续来自权威资源库 | “上次能做”不能证明现在可用 |
| Agent 任务、人工中断、检查点 | 保留项目 JobStore 与 LangGraph checkpoint | 长期记忆与执行恢复承担不同职责 |
| 审批、生产放行、设备操作 | 不由记忆授予权限 | 经验和生成 Skills 不能充当授权 |

上游提供 Chat Memory、Skill、LLM-Wiki、Code-Graph 及 team/user/agent 治理。
本阶段只接 L1 episodic 历史记录；不调用 Skill，不接自动对话捕获 Proxy，不写回模型结论。
当前工艺方案是否适用于某轴件，仍由独立专家、确定性检查与工程师判断。

## 已实现接口

独立 wire adapter 位于 `backend/agent_memory.py`，未复制上游 SDK，也未新增运行依赖。
依据 [v3 SDK](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/8b86874a2daea49e3ff0fb53d699203146c5c77d/sdk/memory-core/python/tencentdb_agent_memory/v3/client.py)、[HTTP transport](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/8b86874a2daea49e3ff0fb53d699203146c5c77d/sdk/memory-core/python/tencentdb_agent_memory/_v3_http.py)、[服务端路由](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/8b86874a2daea49e3ff0fb53d699203146c5c77d/MemoryCore/src/gateway/v2-router.ts) 与 [API 文档](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/8b86874a2daea49e3ff0fb53d699203146c5c77d/MemoryCore/v3-api-memorycore-doc.md) 核实：

```text
POST <memory-core origin>/v3/atomic/search
Authorization: Bearer <gateway key>
x-tdai-service-id: <instance id>

{"team_id":"...","agent_id":"...","user_id":"...","query":"...","type":"episodic","limit":5}

{"code":0,"data":{"items":[{"id":"...","version":2,"type":"episodic","content":"...","team_id":"...","agent_id":"...","user_id":"..."}]}}
```

SDK 与实际路由显示 atomic search 的 version 是数字，update 的版本可能是 `v2` 字符串。
适配器只接受检索的正整数版本；无版本、缺身份、跨身份或 instruction 类型记录丢弃。
查询只发送材料、毛坯类型、热处理和特征类型，不发送完整图纸、尺寸、工艺路线或对话。
每次图执行/人工恢复读一次，最多 5 条、单条 1600 字符、记录 JSON 总计 6000 字符、响应最多 64 KiB。
网络超时为 2 秒每次网络操作，不重试，不跟随重定向，远程服务必须 HTTPS。
返回记录的来源是记忆服务记录，**原始工程文档来源与审核状态尚未核实**，统一标记未确认历史建议。
结果 API 返回 `memory_context`，记录检索/空结果/不可用及摘要；调用 telemetry 仅存摘要，不存凭据。
失败保持当前规则/专家流程可运行；启用记忆时绕过整任务缓存，快照变化使旧任务结果失效。
记忆正文以不可信数据注入模型，当前输入和服务器规则持续约束输出；文字提醒本身不构成完整提示注入防护。

## 启用条件

先独立部署并检查上游服务，再在项目 `.env` 填写：

```dotenv
AGENT_MEMORY_ENABLED=true
AGENT_MEMORY_URL=http://127.0.0.1:8420
AGENT_MEMORY_API_KEY=
AGENT_MEMORY_SERVICE_ID=
AGENT_MEMORY_TEAM_ID=
AGENT_MEMORY_AGENT_ID=
AGENT_MEMORY_USER_ID=
```

空白项必须填入实际值并重启应用，否则检索返回 unavailable。
URL 指 memory-core，不是 Panel 8125 或 Proxy 8096。本地 HTTP 限 loopback；其他地址用 HTTPS。
在上游管理端配置真实 team/user/agent 绑定、实例 ID 和网关 key；不允许以 default 桶承载工厂经验。
该服务身份目前是固定的单个应用 principal。本项目没有多用户系统；字段隔离不是用户授权，不应据此声称多租户就绪。

[上游部署环境模板](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/8b86874a2daea49e3ff0fb53d699203146c5c77d/deploy/global-images/.env.example) 显示：

- 开源 memory-core 默认 SQLite，零外部数据库依赖；因此不必先采购腾讯云数据库。
- MongoDB 模式是可选试验特性，要求带 mongot；不是任意 MongoDB 实例均可用。
- 内部提取/总结模型和 Proxy 上游模型分别配置。部署在本地不意味着所配模型或观测链路也在本地。
- 默认 `MEMORY_CORE_GATEWAY_API_KEY` 为空会关闭 Bearer gate。接入本适配器应启用网关凭据；模板注明当前 Proxy 在非空 key 下有鉴权兼容限制，不能照搬为工厂生产部署。
- 服务端检索代码存在 recall metrics/OTel 上报点。试部署前确认实际配置的观测后端与数据去向。

升级部署应固定经核验镜像 tag/digest，而非把 latest 当稳定版本。
本次没有启动腾讯服务、创建云实例、导入真实工厂数据或发起付费模型调用。
已用模拟 HTTP 验证 API 合同、身份检查、错误回退、内容上限、版本失效及现有 LangGraph 接入；实际服务连通、质量和延迟尚待验证。

如果以后需要共享机床/工单主数据，再独立评估 TencentDB PostgreSQL/MySQL；需要规模化向量检索则评估 VectorDB。
那是第二个架构决策，还要同步设计事务、队列/lease、文件存储和分布式 checkpoint，不能只替换 SQLite 连接串。
