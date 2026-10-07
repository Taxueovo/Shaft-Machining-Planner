# shaftmachiningplanner 本机 API 速查

版本 1.3.0。以下是后端 `http://127.0.0.1:8001` 的业务接口，不是面向公网的多用户 API。前端通过本机代理使用同一服务层；它不会直接调用后端 Python 业务函数。

## 鉴权与地址

除 `/health` 外，业务接口需要请求头 `x-local-api-token`。统一启动器生成本机凭据；直接启动服务时在本机 `.env` 配置非空 `LOCAL_API_TOKEN`，前后端一致。示例中的环境变量代表实际本机 Token，不应将其值写入 Git 或截图。

## 任务接口

| 方法 | 后端路径 | 行为 |
| --- | --- | --- |
| POST | `/api/v1/jobs` | 创建任务；可传 1–128 字符的 `Idempotency-Key` |
| GET | `/api/v1/jobs` | 查询历史；支持 `status`、`search`、`limit`、`offset` |
| GET | `/api/v1/jobs/{job_id}` | 状态、进度、待确认事项、结果就绪标记与 Harness |
| GET | `/api/v1/jobs/{job_id}/input` | 读取原始输入，供新任务复用 |
| POST | `/api/v1/jobs/{job_id}/choices` | 校验页面实际提供的特征与选项后恢复 |
| POST | `/api/v1/jobs/{job_id}/engineering` | 校验工程回答后恢复 |
| POST | `/api/v1/jobs/{job_id}/cancel` | 请求协作式取消，重复取消返回当前状态 |
| GET | `/api/v1/jobs/{job_id}/harness` | 运行身份、累计预算、模型用量观测与故障摘要 |
| GET | `/api/v1/jobs/{job_id}/result` | 读取当前有效结果；未就绪时返回冲突 |
| POST | `/api/v1/jobs/{job_id}/process-route/customize` | 复核候选路线，成功后发布修订 |
| POST | `/api/v1/jobs/{job_id}/process-card/export` | 生成 Excel 工艺草案 |
| GET | `/api/v1/jobs/{job_id}/process-card/download` | 下载已生成的工艺草案 |
| GET | `/api/v1/system` | 本机配置与任务概览；不主动做外部实连验收 |

完整字段和校验以 `backend/models/` 与 `backend/app.py` 为准。前端 `/api/jobs/...` 是代理地址，与上表后端 `/api/v1/jobs/...` 前缀不同。

## 合成输入示例

[examples/minimal-shaft.json](examples/minimal-shaft.json) 使用 45 钢、φ50 mm 实心毛坯、一段 φ30 × 100 mm 轴段、无附加特征和明确不热处理的要求。只是 API 演示，不代表确认的工厂零件。

```bash
curl -X POST http://127.0.0.1:8001/api/v1/jobs \
  -H "x-local-api-token: $LOCAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: synthetic-shaft-example-001" \
  --data-binary @docs/examples/minimal-shaft.json
```

成功返回 HTTP 202，包含 `job_id`、实际 `status` 与接受消息。相同键与相同输入返回已有任务；相同键配不同输入返回 409。幂等键有效期跟随任务保留期限；被清理后不再提供去重保证。

将返回编号作为路径读取状态。只有 `result_ready=true` 才应请求结果，不要把 100% 进度单独作为成功依据。

```bash
curl http://127.0.0.1:8001/api/v1/jobs/JOB_ID \
  -H "x-local-api-token: $LOCAL_API_TOKEN"
curl http://127.0.0.1:8001/api/v1/jobs/JOB_ID/harness \
  -H "x-local-api-token: $LOCAL_API_TOKEN"
```

人工继续应使用状态接口的 `pending_choices` 或 `pending_engineering`，不提交模型猜测的 feature_id / task_id。恢复使用原运行身份、上下文快照与累计预算；已完成、取消或状态不符的请求不会重新启动图。

取消示例：

```bash
curl -X POST http://127.0.0.1:8001/api/v1/jobs/JOB_ID/cancel \
  -H "x-local-api-token: $LOCAL_API_TOKEN"
```

若返回 `cancelling`，继续查询状态直到 `cancelled`。本地函数或在途模型需返回 / 超时后才能完成协作式停止。取消保留输入与证据，重新规划要创建新任务。

## 错误与观测

| 状态码 | 常见含义 | 处理 |
| --- | --- | --- |
| 401 | 缺失或不匹配的本机 Token | 检查前后端配置，不去关闭鉴权 |
| 404 | 任务不存在 | 核对编号、数据库位置与历史清理 |
| 409 | 状态冲突、非法恢复、队列上限或幂等键冲突 | 读取当前状态，修正调用；不要无限重试 |
| 422 | 请求字段 / 类型 / 范围不符合输入合同 | 修正材料、几何或请求结构 |

Harness 的 `execution.policy` 是此任务已保存的策略；`configured_policy` 是当前环境的配置，两者可能不同。用量为空或未完整观测不代表零费用。运行记录接口不返回完整图纸正文或凭据，但完整输入、结果与其他业务接口仍可能包含业务信息，应保留在本机受控环境。

详见 [运行架构与故障行为](langgraph-harness.md) 和 [图文使用指南](software-guide.md)。
