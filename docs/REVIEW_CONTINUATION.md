# Claude Code 交接文档：代码走读（复习）会话的续接

> 写给下一个 Claude 窗口。
> 任务性质：**陪用户逐文件走读代码、讲解答疑**，不是继续改代码。
> 事实层面的权威来源仍是同目录的 `PHASE1_3_HANDOFF.md`（下称「原交接」），本文只补充「我们走到哪了、还剩什么、什么教法有效」。

## 1. 我们在做什么

用户正在按原交接 §9 的「五段式」顺序，一段一段复习这套 Issue-to-PR Agent 的代码。
方式是：**每次只讲一个文件**，读代码 → 结合具体函数讲 → 讲「为什么这么设计」→ 末尾问「继续下一个，还是先追问」。
用户是 Python/agent 入门者，会中途插入概念提问（如「SDK 是什么」「workflow 和 worktree 是什么」），需要即时用具体代码 + 类比解释，不要跳过去。

**铁律**：不改代码、不重跑付费 benchmark、不读/打印任何 `.env` 或 API key。除非用户明确要求，否则只讲解。

## 2. 进度（截至本次交接）

已完成（用户已跟着读透）：

- **第一次 · 通用 Agent 循环**：`core/client.py` → `core/agent.py` → `core/tools.py`（`core/usage.py` 随讲带过，未单独展开）。
- **第二次 · 审批为什么绑定哈希**：`core/policy.py` → `core/audit.py` → `core/workflow.py` → `tests/test_phase2.py`。
- **第三次 · 多个 Agent 怎样编排**：`coder_reviewer.py` → `issue_to_pr.py`（原交接里可选的 `graph_agents.py` 未讲，视用户兴趣再补）。
- **第四次 · Docker 安全边界（进行中）**：已讲 `core/sandbox.py` + `docker/Dockerfile`，**还剩 `tests/test_phase3.py` 未讲**。

**剩余待讲**：

1. 第四次收尾：`tests/test_phase3.py`
2. 第五次（最后一段）：`benchmark/catalog.py` → `dataset.py` → `run.py` → `dashboard.py` → `results.md`（原始数据在 `benchmark/results.jsonl`）

## 3. 已经种下的「主线」比喻（续讲时要继续复用）

用户是靠这些心智模型建立连贯理解的，后面别换说法：

- **每层只干一件事**：client 管「连得上」、agent 管「循环」、tools 管「工具怎么安全执行」、usage 管「记账」。后续的审批、Docker、benchmark 都是通过「换掉注入点」叠上去，核心层不改。
- **多 agent = 同一个 `run_agent` + 不同 prompt + 不同 tools + 不同终态工具**。角色差异全在这三样上，不是框架魔法。
- **审批绑定哈希**：批准的不是「任务」而是「不可变内容的 SHA-256」，内容一变哈希就变、旧审批作废——这是防 TOCTOU（批准 A、实际执行 B）的核心。
- **白名单 ≠ 沙箱**：Phase 2 的命令白名单/路径检查是「受限执行策略」，只有 Phase 3 的容器（`--network none`、非 root、`--cap-drop ALL`、`--read-only`、资源限制）才是真正的安全边界。
- **Reviewer 通过 ≠ 事实**：只有独立 verifier 的测试结果才用于判定成功（`validation_passed = passed AND tests_exit_code == 0`）。

## 4. 讲法要点（对下一个窗口的提示）

1. 每个文件先 `Read` 到当前真实代码再讲，不要凭原交接文档背内容（代码可能已变；若与文档不一致，以代码为准并向用户指出）。
2. 结合**具体函数名 + 行号**（用 `[file.py:42](file.py#L42)` 链接）讲，用户会把文件在 VS Code 里开着对。
3. 主动解释用户可能不熟的 Python 语法：`@dataclass(frozen=True)`、`@property`、`Enum`、`getattr`、`SimpleNamespace`、闭包、`subprocess.run`、`json.dumps(sort_keys=...)`、`Path.resolve()`。
4. 每次末尾留一个明确的「继续 or 追问」出口，不要一口气灌完多个文件。
5. 概念问题（用户会问「X 是什么」）要即时、简短、用代码类比答，答完回到主线。

## 5. 事实口径（务必遵守，同原交接 §12）

- 可以说 Phase 1–3 已完成并真实运行；Docker 隔离探针 + 22×3=66 组矩阵已跑完。
- **不可以说多 Agent 比 single 更好**：当前数据相反（single 22/22=100%，reviewer/planner 各 21/22=95.5%，且 token 贵 2.7~3.1 倍）。正确结论是「当前函数级数据集对 single 太简单」，这是一个有价值的负结果。
- 不可以说 Phase 2 命令白名单是安全沙箱。
- 不可以说已自动创建 PR：目前只到 `approved_for_pr`。
- 不可以说已完成生产级权限系统：当前是单用户本地原型。
- 不可以说 hidden tests 通过 = 真实世界正确。
- 不读/打印/提交任何 API key。

## 6. 关键项目状态（勿混淆）

- Git 仓库根：`D:\workfiles\Markdown_PDF2\pytorch\agent项目\multi-agent`；仓库已自包含 `core/`、测试、服务、Docker、benchmark 与文档。
- 整理工作位于 `repo-consolidation` 分支；整理前基线为 `5b234fd`。
- 回归测试：`python -m pytest` → 当前预期 13 passed（从仓库根运行）。
- 权威 benchmark 数据：`benchmark/results.jsonl`（66 条）+ `results.md`（聚合）。
- 升级路线总览在 `docs/ROADMAP.md`。

## 7. 建议的开场（给下一个窗口）

> 我已经接上上次的进度。你已读完前四段的大部分：通用循环、审批绑定哈希、三 Agent 编排、Docker 边界。现在还剩两块：`tests/test_phase3.py`（把 Docker 边界用测试钉死），然后是最后一段 benchmark 的五件套（catalog → dataset → run → dashboard → results）。先从 `tests/test_phase3.py` 继续？
