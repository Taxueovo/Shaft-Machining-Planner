# 更新记录

## 1.3.0 — 2026-10-07

- Planner 改为按决策证据变化介入，普通调度波次复用计划；Send 只传必要业务状态和依赖产物。
- 新增持久化运行 Harness：跨恢复的预算、上下文快照、运行身份、配置比对、协作式取消与故障记录。
- 服务和离线评测共用执行入口，路线编辑复核也纳入预算与合同控制。
- 任务失败按类型有限重试；人工恢复原子提交，队列失败保留问题；创建任务支持幂等键和队列上限。
- 任务页面增加运行控制面板和工作台内取消确认对话框；评测保存预算停止的 badcase。
- 补充 Harness 架构、默认策略、验证方法和部署边界文档。
- 重写中文图文首页，增加 7 张合成任务实拍截图、职责分层图、恢复 / 编辑 / 评测图、本机 API 速查与示例；同步修正旧的上下文、清理和语言说明。

## 1.2.0 — 2026-10-07

- 统一为本地工艺工作台：侧边导航、真实任务概览、最近任务和系统状态页。
- 新增持久化任务中心，支持名称 / 材料 / 编号查找、状态筛选、分页和历史输入复用。
- 新建页支持零件名称、重量、表面处理、批量以及主动保存 / 恢复浏览器草稿；错误持续显示，离开未保存输入时提醒。
- 启动后显式标记中断任务，保留等待人工输入的任务；空闲自动退出默认关闭。
- 启动器校验服务身份和就绪状态，启动失败退出并清理其启动的进程。
- 结果页突出工程草案与复核入口，技术证据按需展开；缺失任务和无结果终态停止轮询。
- 延续可选腾讯记忆只读适配器、工艺状态校核、执行遥测及离线评测能力。记忆默认关闭，未在真实腾讯云服务或工厂现场完成验收。
- 新增本地软件指南及任务查询、重启、状态接口和启动器的回归验证。

## [0.2.0] - 2026-08-16

### Added

- **RAG retrieval enhancement**:
  - Deterministic feature-penalty reranking — penalize candidates missing the
    query's discriminating feature keywords (thread/spline/hole/gear/chrome...),
    compensating for semantic rerankers' weak feature-level discrimination
  - Cloud rerank via `RERANKER_CLOUD_MODEL` (DashScope qwen3-rerank) with
    graceful fallback to the local CrossEncoder
  - `EMBEDDING_DIMENSIONS` config and batch-size 10 to support text-embedding-v4
  - Long `###` spec subsections are now auto-split by character count

## [0.1.0] - 2026-08-14

Initial public release.

### Added

- **shaftmachiningplanner** — structured machining process planning for motor shafts:
  - Input validation and process-route planning via a LangGraph workflow
  - Machine tool / cutting tool capability libraries with resource verification
  - Process rules engine (sequence, dependencies, heat treatment)
  - RAG index over specification and case libraries (ChromaDB)
- **frontend** — Jinja2 web UI with dynamic forms, status polling, and RAG
  management pages
- Tests for the backend route engine, models, and resource verification
- MIT license, contributing guide, code of conduct, and security policy
