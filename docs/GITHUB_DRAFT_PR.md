# GitHub Issue → Draft PR 闭环

本项目只创建 **Draft PR**，并把“代码审批”和“外部发布审批”分成两个独立门槛。
调用最终发布端点会产生真实的 Git commit、push 和 GitHub Draft PR；准备请求、查看请求和
记录审批都不会访问或修改远端。

## 状态机

```text
GitHub Issue
  → plan → awaiting_plan_approval
  → execute/test/review → awaiting_diff_approval
  → approved_for_pr
  → freeze Draft PR request → awaiting_publish_approval
  → ready_to_publish
  → commit + push + GitHub API → draft_pr_created
```

两次审批绑定不同的 SHA-256：

- `diff` 审批绑定 base commit、候选 diff 和独立测试报告；
- `publish` 审批绑定 repository、remote、base/head branch、title、body、commit message
  以及前一阶段的 diff/test 哈希。

任何 diff 漂移都会拒绝发布并把任务退回 `ready_to_execute`。未跟踪文件不会被纳入 commit，
而是直接阻止发布。网络失败保留 `ready_to_publish`，相同分支的重试会复用已有 Draft PR，
避免重复创建。

## 本地 API 路线

以下是标准库 `service_http.py` 的本地 UI 路线。可选 FastAPI 提供相同业务路径，
但不提供 UI 的 CLI 发布模式开关或二次确认字段校验；不要将它当作 UI 启动方式，
也不要暴露到公网。

1. `POST /tasks/github`：读取公开 GitHub Issue 并生成本地计划；
2. `POST /tasks/{id}/approvals/plan`：审批计划；
3. `POST /tasks/{id}/execute`：在受限环境修改并测试；
4. `POST /tasks/{id}/approvals/diff`：审批 diff/test 组合；
5. `POST /tasks/{id}/draft-pr`：冻结 Draft PR 请求并返回 `request_sha256`；
6. `POST /tasks/{id}/approvals/publish`：单独审批这个发布请求；
7. 以 `python service_http.py --open --real-publisher` 显式启用真实发布；默认及模拟模式均拒绝真实发布。
8. `POST /tasks/{id}/draft-pr/publish`：携带最新 `subject_hash` 和最终确认字段，执行真实 commit、push 和 Draft PR 创建。

准备请求示例：

```json
{
  "repository": "owner/repo",
  "base_branch": "main",
  "head_branch": "agent/fix-issue-123",
  "title": "fix: handle issue 123",
  "remote": "origin"
}
```

省略 `body` 时，系统会从已批准计划、测试报告和审计哈希生成 PR body。

## 凭据与权限

读取公开 Issue 不需要 token。只有最后一步需要在服务进程环境中提供 `GITHUB_TOKEN`；代码
不会读取或打印 `.env`，日志和异常也不会回显 token。建议使用只授予目标仓库
Contents: write 与 Pull requests: write 的 fine-grained token。

真实发布前还必须确认：

- worktree 的 `origin` 确实指向请求中的 `owner/repo`；
- Git 用户名/邮箱已经在本机配置；
- base/head branch 与冻结请求一致；
- 当前请求的 `request_sha256` 已由用户明确批准。
- 服务由用户显式以 `--real-publisher` 启动，并在浏览器最终确认对话框中再次核对目标。

离线测试使用 fake publisher 验证审批和漂移保护，不会联网或创建 PR。真实 GitHub smoke
仍应由项目作者单独确认后执行。

## 并发与浏览器来源保护

同一个服务进程、同一个工作流实例中，每个任务的发布操作有独立非阻塞锁。
重复请求在进入 publisher 前被拒绝；失败释放锁，仍需用户明确重试；成功后状态不再
允许再次发布。这不是跨进程分布式锁，不能同时运行多个发布服务操作同一个数据库。
界面发布请求进行中禁用确认框与提交按钮；这只是体验防重，服务端锁才是写入防重门。

标准库真实发布端点同时检查本地 Host、浏览器同源 Origin（有此头时）、
`Sec-Fetch-Site` 和 JSON Content-Type，拒绝跨站点表单及跨来源请求。
无 Origin 的本机 JSON API 请求仍可使用；这些检查不是认证，服务仍只适合本机单用户。
