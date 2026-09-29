# W4 本地界面契约

在项目根目录运行 `python service_http.py`，然后打开
`http://127.0.0.1:8765/`。HTML、CSS、JavaScript 由同一个仅监听
`127.0.0.1` 的标准库 HTTP 服务提供；没有 npm、前端构建或 CDN 依赖。
创建任务和规划仍需原有模型配置，执行仍需原有 sandbox 配置；仅打开页面、
读取任务和离线回归测试不会调用模型。

界面覆盖任务列表、本地/GitHub Issue 建任务、计划审批、逐步执行流、完整 diff
审查、Draft PR 请求冻结和发布审批。执行目标文件从已批准计划的
`affected_files` 填入下拉框。只有 `POST /tasks/{id}/execute` 返回 202 后，
页面才启动事件轮询；页面加载和刷新仅做普通 GET。若刷新时任务仍显示
`executing`，可手动刷新状态查看已持久化的事件，但不会自动重启轮询。

diff API 返回完整内容。大 diff 在页面内按 160 行分页；访问全部分页或打开
“查看完整 diff”后，且任务状态为 `awaiting_diff_approval`、
`approval_subject_sha256` 非空，审批按钮才启用。提交的 `subject_hash`
始终来自 `diff_review` 审批对象，不是 diff 的内容哈希。

默认流程在 `ready_to_publish` 停止，界面没有真实 GitHub 发布按钮。
仅在显式运行 `python service_http.py --fake-publisher` 时，界面显示“模拟发布”
按钮，并调用独立的 `/tasks/{id}/draft-pr/simulate`。模拟响应必须包含
`simulated: true`；模拟发布器不会 commit、push 或创建 GitHub PR，界面以
醒目的“模拟发布”徽章标示结果。该模式同时禁用原有 HTTP 真实发布路由。
不带标志时模拟端点返回 404。

这是本机单用户原型，没有身份认证，也不应作为公网服务运行。
