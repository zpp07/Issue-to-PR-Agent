# W3 本地只读 API 契约

两个 HTTP 门面提供相同的三个端点：

| 端点 | 响应 |
|---|---|
| `GET /tasks?limit=50&offset=0` | `tasks` 数组，仅含 `id`、`status`、首行 `issue_summary`、`created_at`、`updated_at`；`limit` 为 1–100，`offset` 为非负 SQLite 整数 |
| `GET /tasks/{id}/artifacts` | 该任务每种公开 artifact 的最新版本及其元数据，不含正文 |
| `GET /tasks/{id}/artifacts/{kind}` | 指定公开 artifact 的完整 `content` 和元数据，不做截断 |

artifact 的**正向白名单**为 `plan`、`diff`、`test_report`、`diff_review`、
`draft_pr_request`、`draft_pr`。不存在或不在白名单内的 kind 返回 404。
`gold_patch`、隐藏测试等 evaluator-only 内容即使误入 SQLite，也不会由这些端点暴露。

元数据中的哈希各有用途，不可互换：

- `artifact_sha256`：工作流存下的哈希；结构化 artifact 使用规范化 JSON 的哈希。
- `content_sha256`：本次响应 `content` 的原始 UTF-8 字节哈希。由于格式化 JSON 含缩进，它可能与 `artifact_sha256` 不同。
- `approval_subject_sha256`：提交审批 API 的 `subject_hash`；同时给出 `approval_kind` 和 `approval_subject_kind`。计划审批绑定 `plan`，diff 审批绑定 `diff_review`，发布审批绑定 `draft_pr_request`。**不能把 `diff` 的 `content_sha256` 当作 diff 审批对象。** 如果最新 `diff_review` 未绑定最新 diff 和测试报告，`diff` 的审批对象字段为 `null`。

这些只读字段不代替工作流状态检查：按钮还必须按任务当前状态启用，审批 API
会再次核对当前 artifact。前端可以分页渲染大正文，但未展示完整 diff 前应禁用审批，
并提供查看完整 diff 的入口。响应本身始终返回完整内容。
