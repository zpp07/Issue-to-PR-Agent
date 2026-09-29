# W2 执行作业与事件轮询契约

`POST /tasks/{id}/execute` 在原子地把任务从 `ready_to_execute` 认领为
`executing` 后返回 HTTP 202。202 只表示作业已被接受，**不表示补丁成功**。
同一任务再次请求执行会被拒绝；认领是 SQLite 条件更新，不依赖进程内锁。

前端在收到 202 后调用 `GET /tasks/{id}/events?after=0&limit=100`，此后只把
上次响应的 `next_after` 作为下一次 `after`。`sequence` 是 SQLite 的全局
`AUTOINCREMENT` 序号：严格递增、重启后稳定，不要求同一任务的序号连续。
`limit` 限于 1–200。响应包含 `events`、`next_after`、`has_more`、任务 `status`
和 `execution_done`。
轮询只应由 `POST /execute` 的 202 响应启动。页面加载或刷新时应改用
`GET /tasks/{id}` 读取状态，不要自动开始轮询。

**轮询停止条件只有 `execution_done=true`。** 它要求最新一次
`execution_started` 之后已有 `execution_finished`、`execution_failed` 或
`execution_interrupted`，该终态事件已被当前游标读到，并且没有剩余事件。
不能仅凭 `status != executing` 停止：状态变更与最终事件之间存在短暂窗口。
`agent_exit` 只表示一轮 Coder/Reviewer 的 Agent 循环结束，不是整个执行作业结束。

正常执行结束由工作流落到 `awaiting_diff_approval` 或 `validation_failed`，
随后写 `execution_finished`。未捕获的执行异常落到 `execution_failed`，事件只含
异常类型，不含可能携带源码或凭据的原始错误文本。进度事件单任务最多 2000 条；
超限会写一次 `agent_trace_truncated`，不会截断状态、审批和作业终态事件。

本地服务按**单进程、单数据库实例**运行。若进程中断，下一次服务启动会把遗留的
`executing` 任务标记为 `execution_interrupted`，不自动重放可能已产生副作用的修改。
若进程恰好在写入工作流终态后、写入 `execution_finished` 前中断，启动恢复会补一条
`recovered=true` 的 `execution_finished`，保留原状态。服务尚未重启时，浏览器会看到
连接失败；这不是 `execution_done`，应提示用户重启本地服务。多服务进程共享同一数据库
尚未支持，不能把此恢复策略用于多 worker 部署。
