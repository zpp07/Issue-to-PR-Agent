# Claude Code 交接：E2 实验、收敛控制与 GitHub Draft PR 闭环

> 面向对象：在 VS Code 中带项目作者逐文件阅读代码的 Claude Code。
>
> 交接目标：让 Claude 不需要重跑付费实验，就能准确讲清楚 2026-09-21～2026-09-22
> 这一阶段为什么改、改了什么、数据是否支持，以及下一步还缺什么。

## 0. 先确认仓库状态

- GitHub：`https://github.com/zpp07/Issue-to-PR-Agent`
- 分支：`phase4.5-real-benchmark`
- 本阶段主体提交：`3caffcf feat: complete E2 and add approved draft PR flow`
- E2 正式运行代码版本：`189bd42542196f616072c8692a9b57036524ddb9`
- 当前离线测试基线：`70 passed`
- 当前定位：可用于 Agent 开发实习展示的研究型工程原型，不是无人监管的生产机器人。

阅读时不要打开或输出 `.env`，不要寻找 API key，不要运行任何付费模型实验。E2 的 36 条
正式运行已经固化，讲解时读取报告、分析脚本和少量必要 trace 即可。

## 1. 一句话理解项目

这是一个本地 Issue-to-PR Agent：它从真实 Issue 出发，在带预算和权限边界的环境中搜索、
修改并测试代码，再用 Agent 不可见的 hidden tests 独立评分，最后通过内容哈希绑定的人工
审批，才能进入 GitHub Draft PR 发布阶段。

本阶段的核心不是继续增加 Agent 角色，而是回答三个更难的问题：

1. Agent 已经产生补丁并跑过测试时，系统怎么判断应该结束？
2. 在补丁之后增加 Reviewer/Reviser 是否真的值得额外成本？
3. 从本地 `PR-ready` 到真实 GitHub Draft PR，怎样避免把一次 diff 审批偷换成外部写权限？

## 2. 这一阶段完成的工作

### 2.1 项目包装成为可展示作品

入口：

- `README.md`
- `docs/DEMO.md`
- `.github/workflows/ci.yml`

README 第一屏现在直接给出问题、架构、能力证据、快速运行方式和诚实边界。`docs/DEMO.md`
是一份 3～5 分钟演示脚本，顺序是：架构 → 离线测试 → 隔离修复 → 真实失败分析 → 安全
审批 → E2 负面结论。

讲解重点：这个项目不以“角色数量很多”为卖点，而以可验证补丁、失败 trace、协议版本、
token/成本和权限收敛为卖点。

### 2.2 确定性收敛与终止控制

主要入口：

- `core/agent.py::run_agent`
- `core/sandbox.py`
- `tests/test_phase46.py`

新增状态：

- `patch_revision`：每次成功修改后递增；
- `successful_mutations`：真正生效的修改次数；
- `verified_revision`：最后一次被可见 pytest 验证的补丁版本；
- `completion_evidence`：测试 argv、退出码、step 和补丁版本；
- `budget_state`：剩余 step、token 和成本；
- `exit_reason`：如 `verified_patch`、`max_steps_unverified_patch`、
  `max_steps_no_patch`。

控制器只在“最新一次修改之后，实际 pytest 退出码为 0”时允许 `verified_patch` 自动终止。
`pytest --collect-only`、`--help`、`--version` 等探测不算验证。命令执行结果由 sandbox 以结构化
metadata 返回，不再靠解析一段自由文本猜退出码。

需要特别提醒作者：这个机制证明的是“某个可见测试命令通过”，不是完整任务正确。E2 已经
观察到 3 次自动终止中只有 1 次通过 hidden tests，所以不能把变量名 `verified_patch`
解释为 evaluator 已经验证。

### 2.3 补丁之后的 Reviewer/Reviser

主要入口：

- `benchmark/real_run.py::run_review_revise`
- `benchmark/real_run.py::run_one`
- `core/tools.py` 中的 `review_finish`
- `tests/test_phase46.py`

控制流：

```text
Coder 产生候选补丁
  → 只读 Reviewer
  → passed=true：直接进入独立 evaluator
  → passed=false 且 issues 有效：最多一次、受预算限制的 Reviser
  → 独立 hidden tests 最终评分
```

安全边界：Reviewer/Reviser 只接触 Issue、候选 diff、可见测试证据和仓库源码；hidden tests、
gold patch 和 evaluator 失败信息从不进入上下文。

这一阶段连续修复了三个控制问题：

1. Reviewer 到最后一步仍继续读文件，没有提交结构化结论；
2. provider 在 `passed=true` 时省略空的 `issues=[]`；
3. provider 调用了当前 turn schema 没有暴露的工具。

对应提交：

- `38ab0d3`：最终 Reviewer turn 只暴露 `review_finish`；
- `dd4fa06`：仅规范化无歧义的 `passed=true` 缺失空 issues；
- `189bd42`：按每个 turn 的实际工具集合拒绝未暴露工具调用。

注意不要把这段讲成“Multi-Agent 已经更强”。E2 的 C 组没有超过单 Agent 的 B 组。

### 2.4 协议版本和正式 E2 实验

主要入口：

- `benchmark/protocol.py`
- `benchmark/real_run.py`
- `benchmark/real/experiments/E2_PLAN.md`
- `benchmark/real/experiments/E2_REPORT.md`
- `benchmark/analyze_e2.py`
- `benchmark/real/experiments/e2_v6_completion_review_2026-09-22.jsonl`

正式协议为 v6。每行记录 `protocol_version`、`protocol_hash`、
`evaluation_protocol_hash`、`code_revision`、工作树状态、trace schema、exit reason 和
completion evidence。三组协议 manifest 位于 `benchmark/real/protocols/`。

冻结设计：

- 4 个真实任务：DVC-4124、DVC-4185、Hydra-1791、Pydantic-8977；
- A：single，模型自行调用 finish；
- B：single，启用确定性自动终止；
- C：B + 只读 Reviewer + 最多一次 Reviser；
- 每个任务每组 3 次 provider-default 重复，共 36 条；
- API 没有显式 seed，所以文档只称“重复试验”，不伪称随机种子实验；
- 每次软上限 700k tokens / RMB 2，正式总消耗 11,244,461 tokens / RMB 23.4069。

正式结果：

| 组 | Hidden pass | Wilson 95% CI | Tokens | 成本 RMB |
|---|---:|---:|---:|---:|
| A：模型自行 finish | 7/12 (58.3%) | 32.0%–80.7% | 4,214,901 | 8.7336 |
| B：确定性终止 | 9/12 (75.0%) | 46.8%–91.1% | 2,945,377 | 6.1339 |
| C：Reviewer/Reviser | 8/12 (66.7%) | 39.1%–86.2% | 4,084,183 | 8.5394 |

正确解读：

- B 的观察通过率最高，token 比 A 少 30.1%，但不是共享随机种子的配对实验，不能把差异
  全归因于控制器；
- B 中控制器真正触发 3 次，hidden pass 只有 1/3，说明可见测试仍会产生假收敛；
- C 中 Reviewer 直接通过 5 次，这 5 次都通过 hidden tests；负面审查并触发 Reviser
  5 次，最终通过 3/5；另有 2 次 Coder 没有候选补丁；
- C 比 B 多 38.7% token，观察通过率反而更低，所以不默认开启 Reviewer/Reviser；
- 每组只有 12 条，区间很宽，只能做机制诊断，不能声称统计显著提升。

`benchmark/analyze_e2.py --strict` 会验证 36 条矩阵、重复键、协议漂移、代码 revision、
工作树干净状态和 hidden-test 隔离，再生成 Wilson 区间与机制分解。它不调用模型或 Docker。

### 2.5 GitHub Issue → Draft PR 闭环

主要入口：

- `core/github.py`
- `core/workflow.py::prepare_draft_pr`
- `core/workflow.py::approve_publish`
- `core/workflow.py::publish_draft_pr`
- `issue_to_pr.py::create_from_github_issue`
- `service_api.py` / `service_http.py`
- `docs/GITHUB_DRAFT_PR.md`
- `tests/test_phase6.py`

现在可以读取公开 GitHub Issue，并进入原有 plan → plan approval → execute/test → diff approval
流程。diff 获批后不会立即 push，而是生成新的 `draft_pr_request` artifact，其中冻结：

- repository 与 remote；
- base/head branch；
- PR title/body 与 commit message；
- base commit；
- 已批准的 diff review、diff 和 test-report SHA-256；
- `draft=true`。

用户必须再批准这个精确 request hash，状态才会进入 `ready_to_publish`。真实 publisher 随后：

1. 在任何 Git 写操作前检查 `GITHUB_TOKEN`；
2. 拒绝未跟踪文件和 diff 漂移；
3. 验证 remote 确实指向请求中的 GitHub repository；
4. 创建分支、只 stage 已跟踪修改并 commit；
5. push 分支；
6. 查找相同 head 的已有 Draft PR，安全重试时复用；
7. 没有已有 PR 时用 GitHub API 强制 `draft=true` 创建；
8. 工作流再次检查 publisher 返回的 repository 和 draft 标记。

当前只做了离线 fake-publisher 回归测试，没有替作者创建真实 PR。真正发布仍需作者单独确认。

## 3. 本阶段提交时间线

| Commit | 内容 |
|---|---|
| `5f3876f` | 协议化无进展干预与 trace 状态 |
| `08a9d2d` | 兼容旧 Python 的 symbol 解析修复 |
| `84da576` | E1 无进展干预 smoke 与负面结果 |
| `7219e1b` | 确定性终止、补丁 Reviewer/Reviser、README/演示包装 |
| `38ab0d3` | 强制 Reviewer 终态结构化决策 |
| `dd4fa06` | 规范化正向 Reviewer 输出 |
| `189bd42` | 拒绝每个 turn 未暴露的工具调用；E2 冻结代码版本 |
| `3caffcf` | 36 条 E2、可复现分析器、报告、GitHub Draft PR 闭环 |

## 4. 建议的代码讲解顺序

请在 VS Code 里按以下顺序带作者看，不要一次性把所有文件讲完：

1. `README.md` 第一屏：产品问题、架构和作品定位；
2. `core/agent.py::run_agent`：补丁状态、pytest evidence、预算和 exit reason；
3. `benchmark/real_run.py::run_review_revise`：为什么 Reviewer 放在候选补丁之后；
4. `benchmark/protocol.py` 与一份 v6 manifest：为什么协议也要进入实验版本控制；
5. `benchmark/real/experiments/E2_REPORT.md`：先看总结果，再回到少量失败 trace；
6. `benchmark/analyze_e2.py`：怎样防止混协议、混 revision 和手工抄表；
7. `core/workflow.py` + `core/github.py`：两个审批哈希怎样保护真实外部写操作；
8. `tests/test_phase46.py` + `tests/test_phase6.py`：把控制语义固定成回归测试。

每一步都应回答五个问题：原先有什么失败、代码加了什么状态或约束、测试如何证明、真实实验
是否支持、面试时应该怎样诚实表述。

## 5. 当前边界和下一步建议

已经完成：

- 项目首屏包装和 3～5 分钟演示；
- 确定性终止控制；
- 候选补丁后的 Reviewer/Reviser；
- 36 条同协议 E2 和可复现分析；
- 受双重审批保护的 GitHub Draft PR 代码路径；
- 70 个离线回归测试。

仍未完成：

- 没有真实执行 GitHub Draft PR smoke；
- E2 每组 12 条，不能做强统计结论；
- 自动终止的可见测试证据仍可能过窄；
- Reviewer 诊断精度和 Reviser 的净修复收益尚未拆开测量；
- Reviser 失败时保留/回退候选补丁的策略仍值得加强；
- 项目仍是单用户本地原型，没有多租户身份、远端密钥托管和生产级队列。

求职表述建议：不要说“Multi-Agent 提升成功率”，而应说“我实现并冻结了可验证的
Reviewer/Reviser 机制，真实实验没有显示净收益，所以把它保持为可选策略，并用 trace 找到
假收敛和修订失败的下一批控制问题”。这比只展示漂亮成功率更有可信度。

## 6. 可直接粘贴给 Claude Code 的提示词

```text
请作为我的 Agent 系统代码导师，带我在 VS Code 中逐文件理解这个项目最近一阶段的更新。

先完整阅读：
1. docs/CLAUDE_HANDOFF_2026-09-22.md
2. README.md
3. benchmark/real/experiments/E2_REPORT.md
4. docs/GITHUB_DRAFT_PR.md

边界：
- 本轮先讲解，不修改代码；
- 不运行任何付费模型/API 实验；
- 不读取或打印 .env、API key、GitHub token；
- 不把 hidden tests、gold patch 或 evaluator 失败信息提供给 Agent；
- 不要把 E2 的小样本结果描述成统计显著提升。

请按以下 8 个模块依次讲，每讲完一个模块暂停，问我是否理解以及是否继续：
1. README 第一屏和项目求职定位；
2. core/agent.py 中补丁 revision、pytest completion evidence、预算和 exit_reason；
3. benchmark/real_run.py 中 Coder → Reviewer → 最多一次 Reviser 的控制流；
4. protocol v6 如何解决 Reviewer 终态、缺失 issues、未暴露工具调用；
5. E2 的 A/B/C 设计、36 条结果、Wilson 区间和为什么不能下强因果结论；
6. benchmark/analyze_e2.py 如何校验协议、revision、重复键和 hidden-test 隔离；
7. core/workflow.py 与 core/github.py 中 diff 审批和 publish 审批为什么必须分开；
8. tests/test_phase46.py、tests/test_phase6.py 如何固定这些控制语义。

讲解每个模块时请：
- 先说原来的具体失败现象；
- 指出函数名和关键代码段，让我能在编辑器里跟着定位；
- 用一条真实 trace 或测试说明机制；
- 解释设计 trade-off 和仍然存在的风险；
- 最后给我一句适合面试时使用、但不过度包装的表述。

如果交接文档与代码不一致，以当前分支代码为准，并明确指出差异。不要自动修复，等我确认。
```
