# Claude Code 交接文档：Issue-to-PR Agent Phase 1–3

> 更新时间：2026-09-16
> 读者：VS Code 中的 Claude Code
> 目的：让 Claude 快速建立正确的项目心智模型，并能陪用户一边读代码、一边理解前三个 Phase。
> 默认任务是**讲解与答疑**，不是继续修改代码；除非用户另行明确要求，否则不要自动重构、提交或重跑付费 benchmark。

## 1. 一句话结论

这个项目已经从几个 toy agent demo，收敛为一个面向 Python 服务/数据处理仓库的本地
**Issue-to-PR Agent 原型**：它能把 Issue 转成结构化计划，在审批后进入独立 Git worktree，
通过受限命令策略和 Docker 容器执行修复/测试，记录可审计 artifact，最后把精确 diff 和
测试报告交给人审批。

Phase 1、Phase 2、Phase 3 均已完成到各自定义的验收范围。Phase 3 已真实构建 Docker 镜像，
并完成 22 个 case × 3 种方法的 66 组 `deepseek-chat` 实验，不是仅写了代码但没运行。

## 2. 请先理解的项目边界

项目主线不是“万能 Agent”，也不是 SWE 与咨询的混合体，而是：

```text
Issue
  → 结构化计划
  → 人工审批计划
  → 独立 Git worktree
  → Coder / Reviewer 执行
  → 独立测试验证
  → 生成 diff + 测试报告 + 哈希
  → 人工审批精确 diff
  → PR-ready（尚未自动 push / 创建远程 PR）
```

安全概念必须准确区分：

- Phase 2 的 argv 白名单、路径检查、worktree 是“受限执行策略”，**不是安全隔离**。
- Phase 3 的无网络、非 root、资源受限 Docker 容器才是执行不可信仓库代码的安全边界。
- Reviewer 的“通过”不是事实；只有独立 verifier 的测试结果才用于 benchmark 成功判定。
- Checkpoint 不是审计日志；本项目使用自己控制的 SQLite append-only event 表做审计。

## 3. 目录和 Git 状态（非常重要）

当前 Git 仓库根目录是：

```text
D:\workfiles\Markdown_PDF2\pytorch\agent项目\multi-agent
```

仓库现在自包含 `core/`、`tests/`、`pyproject.toml`、服务入口、Docker、benchmark 与文档，
clone 后不再依赖父目录代码。整理工作在 `repo-consolidation` 分支进行；整理前基线是
`5b234fd feat: establish reproducible multi-agent baseline`。

本地状态统一写入 `.local/`（或由 `AGENT_DATA_DIR` 覆盖）并被 Git 忽略。首次公开前仍要检查
`.env`、轮换本地开发使用过的 key，并确认没有误提交本机路径。不要读取或展示任何 `.env` 内容。

## 4. 改造前后发生了什么

改造前的核心问题：

- Agent 循环能跑，但一次网络错误可能让流程整体崩溃。
- 工具输出、文件大小、token、成本和运行时间缺少明确上限。
- 命令执行曾依赖 shell 字符串，风险和可审计性都很差。
- Reviewer 结论主要靠自由文本，难以可靠编排和统计。
- 没有与精确 artifact 绑定的审批，存在 TOCTOU 风险。
- Agent、测试和源码在宿主机同一环境运行，不能声称隔离。
- 评测 case 太少，而且 Agent 可以接触或改写测试，成功率缺乏可信度。

Phase 1–3 后：

- LLM 调用有超时、异常分类、request ID、预算和用量统计。
- 工具采用无 shell 的 argv，并有文件/输出/时间限制。
- Planner、Coder、Reviewer 使用结构化终态工具。
- 每个任务使用独立 Git worktree；审批绑定计划、diff、测试报告的 SHA-256。
- task / artifact / approval / audit event 被持久化到 SQLite。
- 不可信命令在无网络、非 root、资源受限的临时 Docker 容器内运行。
- hidden tests 与 Agent workspace 物理分离，并由独立 verifier 只读挂载。
- 有 22×3 基线、token/成本/耗时/篡改/超时指标和 Markdown dashboard。

## 5. Phase 1：可靠性底座

### 5.1 为什么先做这一阶段

如果模型调用、工具和预算都不稳定，后面的审批、Docker、RAG 和 benchmark 只会建立在不可复现
的基础上。Phase 1 的目标不是增加 Agent “智能”，而是让它在失败时可解释、可停止、可统计。

### 5.2 主要代码

建议按以下顺序阅读：

1. `core/client.py`
   - 统一创建 DeepSeek/OpenAI-compatible client。
   - 显式设置 timeout / max retries。
   - 使用 `LLMCallError` 区分 retryable 与不可重试错误。
   - 缺少 OpenAI SDK 时提供标准库 HTTP fallback。
   - 从当前目录逐级向上寻找 `.env`，但不得输出 key。

2. `core/agent.py`
   - 通用 tool-calling loop。
   - 记录 token、成本、request ID 和结构化 trace。
   - 支持 step/token/cost 上限。
   - 截断过长工具输出。
   - LLM 失败时返回带 `retryable` 的结构化结果，而不是无提示崩溃。
   - 兼容 provider message 缺少 `tool_calls` 属性的情况。

3. `core/tools.py`
   - `read_file` / `write_file` 有 200KB 限制和错误处理。
   - `run_command` 接受 argv，不接受 shell 字符串，带超时和输出截断。
   - `ToolRegistry` 让不同角色只拿到所需工具。
   - `make_executor` 把工具绑定到指定 workspace，并解析真实路径防止 symlink/reparse point
     逃逸。

4. `core/usage.py`
   - 汇总 prompt/completion tokens、调用数和人民币成本估算。

5. `pyproject.toml`
   - 查看项目依赖与可选 Web 依赖，理解为什么没有 SDK 时仍保留标准库运行路径。

### 5.3 需要向用户强调的设计选择

- LLM 重试不能层层叠加。官方 SDK 路径让 SDK 拥有请求级重试；标准库 fallback 不偷偷增加第二层。
- Phase 3 benchmark 另外拥有一次“整题重试”，只用于瞬时基础设施错误，不把网络故障计入模型失败。
- “有错误处理”不等于吞掉错误：retryable 状态需要向上层传播。
- 当前 13 项回归主要覆盖 Phase 2/3；不要错误宣称它们穷尽了 Phase 1 所有故障注入场景。

## 6. Phase 2：Issue-to-PR 工作流、审批与审计

### 6.1 状态流

```text
created
  → awaiting_plan_approval
  → ready_to_execute
  → validation_failed
       或
    awaiting_diff_approval
      → approved_for_pr / diff_rejected
```

### 6.2 主要代码

1. `core/policy.py`
   - 对 argv 分类为 allow / approval / deny。
   - pytest、受控 Python 和只读 Git 属于允许范围。
   - Git 写操作等高风险命令必须审批。
   - shell、任意 `python -c` 等危险形式被拒绝。

2. `core/audit.py`
   - SQLite 表：`tasks`、`approvals`、`artifacts`、`audit_events`。
   - `audit_events.sequence` 单调递增，作为应用自己的 append-only 审计轨迹。
   - 审批记录包含 actor、decision、reason、subject hash 和时间。

3. `core/workflow.py`
   - 创建任务前要求仓库干净，避免未跟踪文件让基线不可复现。
   - 以当前 commit 创建 detached Git worktree，每个任务目录独立。
   - Planner schema 必须恰好包含：
     `goal / affected_files / steps / risks / allowed_commands`。
   - 计划审批绑定计划 SHA-256。
   - 执行后记录 binary diff、test report、review report 及各自哈希。
   - diff 审批绑定 `base_commit + diff_sha256 + test_report_sha256 + reviewer report` 的组合哈希。
   - Reviewer 通过但测试 exit code 非 0 时进入 `validation_failed`，不能请求 diff 审批。

4. `issue_to_pr.py`
   - 把 Planner、Coder/Reviewer 和 `IssueToPRWorkflow` 串起来。
   - Phase 3 后可注入 `DockerSandbox.command_runner`，使工作流命令进入容器。

5. `service_http.py` / `service_api.py`
   - `service_http.py` 是标准库 REST 入口。
   - `service_api.py` 是可选 FastAPI 入口。
   - 两者面向单用户本地应用，不要把它描述成已完成鉴权和多租户的 SaaS。

6. `tests/test_phase2.py`
   - 重点看命令策略、超大文件、干净 Git 基线、worktree、计划/diff 哈希审批、验证失败门禁。

### 6.3 Phase 2 已实际验证的结果

- 已跑通 Issue → plan → plan approval → worktree execution → test/review → diff approval。
- 工作流最终到达过 `approved_for_pr`。
- 基线提交为 `5b234fd`，用于避免 dirty repository 破坏可复现性。
- Phase 2 不会执行 commit、push 或远程 PR；这些仍属于 Phase 6。

### 6.4 最值得讲清楚的安全点

审批不是一个布尔值。用户批准的是一个明确的、不可变的 subject hash；artifact 发生变化后，旧审批
不能继续生效。这是本阶段比“在 UI 上放一个确认按钮”更重要的设计。

## 7. Phase 3：Docker 真隔离与 benchmark

### 7.1 Docker 边界

阅读 `core/sandbox.py` 与 `docker/Dockerfile`。

每条不可信仓库命令运行在一次性容器中，关键参数包括：

- `--network none`
- `--user 65532:65532`
- CPU、内存、PID 和 wall-clock timeout 限制
- `--cap-drop ALL`
- `no-new-privileges`
- 只读容器根文件系统
- `/tmp` 使用受限 tmpfs
- 不把宿主 API key 注入容器
- Agent 阶段不挂载 hidden tests

Docker 不存在或 daemon 不可用时必须明确失败，不能静默回退到宿主机执行不可信代码。

Windows 上实际遇到并修复了两个问题：

- 新版按用户安装路径是
  `C:\Users\32744\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`，
  当前 shell 的 PATH 可能尚未刷新，因此 `DockerSandbox` 增加常见路径解析。
- Docker `--mount` 长语法不能使用裸 `rw/ro`；可写 mount 省略标记，只读使用 `readonly`。

Docker Hub 直连超时时，Dockerfile 支持覆盖：

```powershell
docker build -t issue-to-pr-runner:phase3 `
  --build-arg BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim `
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple `
  -f docker\Dockerfile .
```

已验证：Docker Desktop 4.91.0、Engine 29.8.0、镜像
`issue-to-pr-runner:phase3`；容器内 UID=65532、API key=None、网络连接被阻断。

### 7.2 benchmark 数据边界

阅读顺序：

1. `benchmark/catalog.py`
   - 22 个确定性 Python 修复 case。
   - 每个 case 包含 issue、buggy source、public tests、hidden tests、类别和难度。
   - 任务描述经过一次公平性校准：API 契约必须写在 issue 中，hidden tests 只能隐藏输入，不能隐藏
     需求本身。

2. `benchmark/dataset.py`
   - 每次运行把 workspace 与 hidden tests 物化为物理分离目录。
   - Agent 看不到 hidden test 文件。

3. `benchmark/run.py`
   - 比较 `single`、`reviewer`、`planner` 三种无检索基线。
   - 每种方法使用相同 case 和独立临时 workspace。
   - Agent 执行结束后恢复可信 public test，再由只读 verifier 同时运行 public + hidden tests。
   - `--workers` 支持相互独立的并发评测。
   - `--resume` 按 `(case, method)` 跳过已有结果。
   - 单个 future 异常不会丢掉其他已完成结果；瞬时 provider 错误做一次整题重试。

4. `core/tools.py` 的 `writable_files`
   - benchmark 给执行器传入 `writable_files=["buggy.py"]`。
   - Agent 试图改 `test_buggy.py` 会被直接拒绝。
   - 该 allowlist 默认关闭，不会破坏 Phase 2 对多文件真实仓库的通用修改能力。

5. `benchmark/dashboard.py`
   - 从 JSONL 聚合通过率、token、成本、耗时、测试改写、超时和失败明细。

6. `tests/test_phase3.py`
   - 覆盖数据集分离、容器 hardening argv、只读 verifier mount、禁止 Docker fallback、dashboard、
     provider message 兼容和 public-test 写保护。

### 7.3 最终实测结果

权威文件：

- `benchmark/results.jsonl`：66 条原始记录。
- `benchmark/results.md`：聚合 dashboard。

最终矩阵：

| 方法 | Hidden pass | 平均 tokens | 平均成本 | 平均耗时 | 测试改写 |
|---|---:|---:|---:|---:|---:|
| single | 22/22 (100%) | 3,586 | ¥0.0095 | 27.21s | 0 |
| reviewer | 21/22 (95.5%) | 9,725 | ¥0.0264 | 60.22s | 0 |
| planner | 21/22 (95.5%) | 11,271 | ¥0.0321 | 67.70s | 0 |

整体：

- 66 条结果，66 个唯一 `(case, method)`。
- hidden tests：64/66 通过。
- 总模型费用：¥1.4955。
- 测试改写：0。
- 容器超时：0。
- 回归测试：13 passed。

两个真实失败：

1. `planner / case12_safe_divide`
   - Agent 自评通过。
   - 实际把本应传播的 `TypeError` 吞掉，hidden verifier 失败。

2. `reviewer / case22_transpose`
   - Agent 自评通过。
   - 错误地把空矩阵视为非法并抛 `ValueError`，hidden verifier 失败。

### 7.4 应该怎样解释这些结果

不要说“多 Agent 已被证明更强”。当前数据恰好相反：

- single 在这些函数级 case 上达到 100%。
- reviewer 与 planner 都是 95.5%。
- reviewer 平均 token 是 single 的约 2.7 倍。
- planner 平均 token 是 single 的约 3.1 倍。

正确结论是：当前数据集对 single 太简单，额外角色增加了成本和新的误判机会。这个负结果仍然很有
价值，因为它证明项目拥有独立 verifier、成本核算和 benchmark 校准能力，而不是为了展示多 Agent
架构而挑选有利结果。

同时要提醒用户：这是每个 `(case, method)` 单次运行的工程基线，没有多随机种子和置信区间，不能
当作统计学定论。

## 8. 开发过程中真正暴露并修复的问题

Claude 在讲解时可以用这些问题串起工程思路：

1. Docker 已启动，但终端 PATH 找不到 CLI。
2. credential helper 也依赖 Docker bin 目录进入 PATH。
3. Docker Hub OAuth endpoint 在国内网络超时，改用可覆盖的国内镜像参数。
4. Windows bind mount 的 `--mount` 语法与预期不同，裸 `ro/rw` 无效。
5. 并发 runner 最初使用了旧 Python 不支持的 `cancel_futures`。
6. 一个 future 异常曾让其他 future 的完成结果无法继续写入，随后改为逐项收集错误并支持 resume。
7. provider message 可能没有 `tool_calls` 属性，Agent loop 必须使用兼容访问。
8. 瞬时网络失败不能计入模型失败，因此增加了一次整题级基础设施重试。
9. 初版 hidden tests 包含 issue 未声明的契约，导致评测不公平；随后校准 issue，再只重跑受影响行。
10. Planner/Reviewer 曾尝试改 public tests；先靠 hash 检测和恢复兜底，最后升级为写入 allowlist，
    正式矩阵的改写次数降为 0。

这些经历比只展示最终架构图更能说明项目具备真实工程深度。

## 9. 推荐 Claude 陪用户读代码的顺序

建议分成五次讲解，每次先读代码再解释，不要一次灌完：

### 第一次：通用 Agent 循环

打开：

- `core/client.py`
- `core/agent.py`
- `core/tools.py`
- `core/usage.py`

重点回答：一次 tool-calling Agent 是怎样循环的？错误、预算和工具权限在哪里被处理？

### 第二次：审批为什么绑定哈希

打开：

- `core/policy.py`
- `core/audit.py`
- `core/workflow.py`
- `tests/test_phase2.py`

重点回答：状态如何变化？审批对象是什么？怎样防止“批准 A、实际执行 B”？

### 第三次：多个 Agent 怎样编排

打开：

- `coder_reviewer.py`
- `issue_to_pr.py`
- 如需对比，再看 `graph_agents.py`

重点回答：Planner、Coder、Reviewer 的职责、工具权限和终止条件分别是什么？

### 第四次：Docker 安全边界

打开：

- `core/sandbox.py`
- `docker/Dockerfile`
- `tests/test_phase3.py`

重点回答：哪些限制是安全边界？hidden tests 为什么不能挂载给 Agent？为什么不能 fallback 到 host？

### 第五次：如何相信评测结果

打开：

- `benchmark/catalog.py`
- `benchmark/dataset.py`
- `benchmark/run.py`
- `benchmark/dashboard.py`
- `benchmark/results.md`

重点回答：成功的定义是什么？为什么 Reviewer 自评不可信？为什么结果显示 single 反而更好？下一轮
benchmark 应怎样变难？

## 10. 可安全执行的验证命令

从仓库根目录运行回归测试：

```powershell
python -m pytest
```

当前预期：`13 passed`。测试发现规则由仓库根目录的 `pytest.ini` 固定，不会误收集旧 demo
中的示例测试。

从仓库根目录运行已有结果的 dashboard 生成或查看帮助，不产生模型费用：

```powershell
python -m benchmark.run --help
Get-Content benchmark\results.md
```

小规模 smoke 会产生模型费用，只有用户明确同意时才运行：

```powershell
python -m benchmark.run `
  --cases case01_add,case02_grade `
  --methods single,reviewer,planner
```

不要默认重跑完整 22×3 矩阵；已有正式结果足够用于讲解。

## 11. 当前局限与 Phase 4 方向

还没有完成的能力：

- 没有自动创建远程 GitHub PR。
- 没有多用户鉴权、租户隔离或生产部署。
- 任务记忆尚未实现。
- 代码库检索尚未进入主线 Agent。
- 当前 benchmark 是单文件、函数级任务，不能代表真实仓库 Issue。
- benchmark 每个组合只有一次运行，没有方差与置信区间。

Phase 4 已规划为：

- AST/结构化代码切块。
- BM25 关键词检索。
- 向量召回。
- reranker。
- 只在跨文件、需要文档或代码定位的 harder suite 上做“无检索 vs 有检索”消融。

只有在这个子集上，才能诚实回答“RAG 是否提高成功率、减少 token、缩短定位时间”。

## 12. Claude 讲解时的事实约束

请遵守以下口径：

- 可以说 Phase 3 已完成并真实运行。
- 可以说 Docker 隔离探针和 66 组矩阵已完成。
- 不可以说多 Agent 比 single 更好；当前数据不支持。
- 不可以把 Phase 2 的命令白名单称为安全沙箱。
- 不可以说已经自动创建 PR；目前只到 `approved_for_pr`。
- 不可以说已完成生产级权限系统；当前是单用户本地原型。
- 不可以把 hidden tests 的通过等同于真实世界正确性。
- 不要读取、打印或提交任何 API key。
- 如果代码与本文档出现不一致，以当前代码、测试和 `benchmark/results.jsonl` 为准，并向用户指出差异。

## 13. 建议 Claude 对用户的开场方式

可以直接这样开始：

> 我已经读完 Phase 1–3 的交接文档。我们可以按五个主题逐步看：通用 Agent 循环、审批与审计、
> 多 Agent 编排、Docker 安全边界、benchmark 可信度。建议先从 `core/agent.py` 开始，我会一边指向
> 具体函数，一边解释一次 Issue 是怎样变成可审批 diff 的。你随时可以暂停追问。

不要只复述本文档；应在 VS Code 中打开对应文件，用实际代码验证每个结论。
