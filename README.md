# Local Issue-to-PR Agent

从 Coder + Reviewer demo 演化而来的**可审批、可审计、容器隔离、可评测**的本地
Issue-to-PR Agent。仓库已自包含 `core/`、服务入口、测试、Docker 与 benchmark；clone 后
不依赖父目录代码。

## 核心目标

不是重复 demo，而是回答三个能被面试官/审稿人问到的问题：

1. **这个 agent 真的修好了吗？** → pytest 独立验证，不信任 agent 自说自话
2. **它花了多少资源？** → token / 成本的全程统计
3. **它有没有作弊 / 沙箱逃逸？** → 篡改测试检测 + 源文件污染检测

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m pytest
```

运行真实模型任务前，在本地 `.env` 中填写 `DEEPSEEK_API_KEY`；该文件已被 Git 忽略。
只运行测试和查看 benchmark 结果不需要 API key。Docker 隔离运行还需要启动 Docker
Desktop，并按 Phase 3 小节构建 runner 镜像。

## Phase 2：本地 Issue-to-PR 工作流

新增的主线不是安全沙箱，而是一个可审计的本地受限执行流程：

```
Issue → 结构化计划 → 计划审批 → 独立 Git worktree → 修复/审查/测试
      → diff + 测试报告哈希 → diff 审批 → PR-ready 状态
```

- `core/policy.py`：只接收无 shell 的 argv；质量检查和只读 Git 可直接运行，
  Git 写操作需要单独审批，其余命令拒绝。
- `core/audit.py`：SQLite 的 task、artifact、approval 与 append-only 审计事件。
- `core/workflow.py`：计划与 diff 审批绑定精确 SHA-256，避免审批对象在执行后漂移。
- `issue_to_pr.py`：把 Planner 和 Coder/Reviewer 接入状态机。
- `service_api.py`：可选 FastAPI 接口；安装 `pip install -e '.[web]'` 后运行
  `uvicorn service_api:app --reload`。
- 若开发机尚未安装第三方依赖，可直接运行 `python service_http.py`；它只用
  Python 标准库提供同一组本地 REST 端点，`core/client.py` 也会在缺少 OpenAI SDK 时
  降级为 OpenAI-compatible HTTP 调用。

注意：pytest/Python 仍会执行任务仓库代码，因此本阶段的 host 策略不是隔离边界；
Docker 无网络运行环境属于下一阶段。

为了保证 worktree、diff 和 hidden-test 结果可复现，创建任务会拒绝含未提交或未跟踪
文件的仓库。先把测试和源代码提交到一个基线 commit，再创建 Issue-to-PR 任务；不要
把主工作树中临时的未跟踪测试悄悄复制进任务环境。

## Phase 3：Docker 隔离与 hidden-test benchmark

`benchmark/` 提供 22 个确定性 Python 修复任务，以及 `single`、`reviewer`、
`planner` 三种无检索基线。每个任务运行时物化为两个互不包含的目录：Agent 只看到
`workspace/` 中的源码和公开测试，独立 verifier 才能只读挂载 `hidden_tests/`。

所有仓库命令通过 `core.sandbox.DockerSandbox` 在临时容器执行，参数固定包含：无网络、
非 root、只读容器根、capabilities 全移除、`no-new-privileges`、CPU/内存/PID/超时限制。
宿主 key 不会被传入容器。Docker 不存在时 runner 直接失败，不回退到宿主执行。

```powershell
docker build -t issue-to-pr-runner:phase3 -f docker\Dockerfile .

# Docker Hub / PyPI 直连不稳定时可覆盖为国内镜像；默认值仍是官方源
docker build -t issue-to-pr-runner:phase3 `
  --build-arg BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim `
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple `
  -f docker\Dockerfile .

python -m benchmark.run --cases case01_add,case02_grade --methods single,reviewer,planner
python -m benchmark.run --cases all --methods single,reviewer,planner --workers 3
# 中断后跳过已有 case/method 键继续执行
python -m benchmark.run --cases all --methods single,reviewer,planner --workers 3 --resume
```

结果写入 append-only JSONL，并生成包含成功率、token、成本、耗时、测试篡改、超时和失败
明细的 Markdown dashboard。benchmark 的 `write_file` 仅允许修改 `buggy.py`；公开测试即使
被模型尝试改写也会被拒绝，独立 verifier 还会恢复可信副本后再挂载 hidden tests。

### 实测结果（2026-09-16，deepseek-chat）

| 方法 | Hidden pass | 平均 tokens | 平均成本 | 平均耗时 | 测试改写 |
|---|---:|---:|---:|---:|---:|
| single | 22/22 (100%) | 3,586 | ¥0.0095 | 27.21s | 0 |
| reviewer | 21/22 (95.5%) | 9,725 | ¥0.0264 | 60.22s | 0 |
| planner | 21/22 (95.5%) | 11,271 | ¥0.0321 | 67.70s | 0 |

66 个唯一任务组合共花费 ¥1.4955，容器超时为 0。失败的 reviewer/case22 和
planner/case12 都出现「Agent 自评通过，但 hidden verifier 失败」。当前结论不是多 Agent
更强，而是这 22 个函数级任务对 single 过于简单；reviewer/planner 增加了 2.7–3.4 倍
token 成本，成功率反而略低。下一轮应在跨文件、需要检索和复杂契约的子集上做消融。

这是单次运行的工程基线，不是带置信区间的统计结论；原始 JSONL 和完整失败明细见
`benchmark/results.jsonl` 与 `benchmark/results.md`。

## 文件结构

```
multi-agent/
├── core/                   # Agent loop、tools、policy、audit、workflow、Docker sandbox
├── benchmark/              # 22-case hidden-test benchmark + 正式结果
├── docker/                 # 隔离执行镜像
├── docs/                   # 路线图与 Phase 1–3 交接文档
├── tests/                  # Phase 2/3 主回归测试
├── pyproject.toml          # 可安装项目与依赖声明
├── service_http.py         # 标准库本地 REST 服务
├── service_api.py          # 可选 FastAPI 服务
├── issue_to_pr.py          # Planner + Coder/Reviewer + workflow 适配层
├── coder_reviewer.py       # 用 core 重写的 Coder+Reviewer（结构化审查 + 沙箱）
├── graph_agents.py         # LangGraph 版编排（StateGraph + 条件边 + human-in-loop）
├── experiment.py           # 对照实验：独立 reviewer vs 自我审查
├── multi_agent.py          # 旧版（极端 Reviewer，保留对照）
├── multi_agent_normal.py   # 旧版（正常 Reviewer，保留对照）
├── eval/
│   ├── seeds/              # 纯净 bug 用例（真 bug，每次评测从这里恢复）
│   │   ├── case01_add/     #   add 减法 / get_grade 顺序 / average 除零
│   │   └── case02_stats/   #   mean 除零 / median 原地排序+偶数取错
│   ├── cases/              # 运行时临时题目（可被 agent 污染，评测前自动恢复）
│   ├── eval.py             # 评测主流程
│   └── results.jsonl       # 旧版实验结果（本地生成）
└── .env.example            # 环境变量模板；真实 .env 永不提交
```

## 与旧版的三处关键差异

| | 旧版 multi_agent_normal.py | 新版 coder_reviewer.py |
|---|---|---|
| 审查判定 | `"不通过" in review` 字符串匹配 | 结构化 `review_finish` → `{passed, issues}` |
| 资源统计 | 无 | 全程 token / 成本（`Usage`） |
| 可评测性 | 只能命令行跑 | 可 import，eval.py 批量测评 |
| 沙箱 | 无（agent 可逃逸污染源文件） | 文件 API 路径锁定在工作区内 |

## 一个真实的发现：沙箱逃逸

**第一版运行时观察到一个 bug 级现象**：agent 通过 `run_command` 里的
`cd` 溜回真实项目目录，把「修复结果」直接写回了源 case 文件（污染了测试集）。
这是 LLM agent 领域的经典安全问题——**sandbox escape / test pollution**。

**解决方案（`core/tools.py` 的 `make_executor`）**：所有文件读写的路径被
强制约束在 workdir 内 —— 相对路径解析到 workdir 下，越界的绝对路径直接拒绝。
效果：评测从 token 26 万 + 污染，降到 token 8 千 + 零污染。

**诚实的边界**：本方案锁定「文件 API」这条主通道；纯 shell 命令内部仍可
`cd` 逃逸，要做到 100% 防逃逸必须上容器（docker runtime）。这个残余风险
由 eval 的 `source_polluted` 指标兜底监测——这正是无容器沙箱的务实边界，
也是面试时可以主动谈的 trade-off。

## 运行

```bash
# 单次演示（从仓库根目录运行，会就地改 buggy.py）
python coder_reviewer.py

# 跑完整评测（从 seeds 恢复纯净题目 → 修复 → pytest 验证 → 记录指标）
python eval/eval.py
```

## 评测指标（results.jsonl 每条记录）

| 字段 | 含义 |
|---|---|
| `agent_passed` | Reviewer 是否判定通过 |
| `tests_pass` | pytest 独立验证是否真实通过 |
| `review_rounds` | 用了多少轮修复 |
| `test_tampered` | 是否篡改测试文件 |
| `source_polluted` | 是否逃逸污染了源文件 |
| `total_tokens` / `cost_rmb` | 资源消耗 |

## LangGraph 版（graph_agents.py）

把「Coder + Reviewer」编排用 LangGraph `StateGraph` 重写，图结构显式化：

```
  ┌──────┐   ┌──────────┐   通过/达上限 ──▶ END
  │ coder │──▶│ reviewer │
  └──────┘   └──────────┘
     ▲            │
     └── 不通过 <──┘        （条件边路由）
```

两个版本对比：

| | coder_reviewer.py（手写 while） | graph_agents.py（LangGraph） |
|---|---|---|
| 控制流 | 隐式（循环体里写逻辑） | 显式（节点 + 条件边可视化） |
| 状态 | 闭包变量 | TypedDict 状态节点间流转 |
| 人工介入 | 无 | interrupt + checkpoint 断点续跑 |
| 可观测 | 需自己 print | 原生 step 流 + checkpoint |

运行（`--human` 版每轮审查后暂停，等人工确认）：

```bash
python graph_agents.py            # 自动跑
python graph_agents.py --human    # human-in-loop，敲 y/n 决定是否继续修
```

**踩坑记录（值得知道的 LangGraph 1.x 变化）**：`StateGraph` 不再识别
裸 `dict` 子类作为状态 schema，必须用 `TypedDict` 定义，否则节点返回的
字段不会在节点间合并（表现为状态永远停留在初始值、无限循环）。

## 对照实验：独立 Reviewer vs 自我审查（experiment.py）

**研究问题**：把「修代码」和「查代码」拆成两个 agent，相比让同一个 agent
自我审查，到底值不值 —— 修复率有没有提升？多花多少成本？

**结论（初步，2 用例 × 2 方法，deepseek-chat）**：

| 用例 | 独立 Reviewer | 自我审查 | 差异 |
|---|---|---|---|
| case01_add | ✅ 修复 / 10.4K token / ¥0.027 | ✅ 修复 / 39.2K token / ¥0.089 | self 反而贵 3.7× |
| case02_stats | ✅ 修复 / 10.6K token / ¥0.027 | ✅ 修复 / 32.7K token / ¥0.080 | self 反而贵 3.1× |

**本实验的第一个发现（反直觉）**：在这两个简单用例上，两种方式修复率都是
100%，但「自我审查」反而**更费 token（约 3–4 倍）**——因为被要求「先修再自查」
的单 agent 会反复读写文件、来回确认，而独立 reviewer 分工明确、一步到位。

**但这不能反推「独立 reviewer 无用」**，恰恰相反：
1. 本实验用例太简单（2 个确定性 bug），无法区分「更会查错」的能力差异；
2. 独立 reviewer 的价值应在**更难、更模糊**的任务上暴露——自我审查的
   思维定式（「自己写的自己看不出毛病」）只会在复杂代码里显现；
3. 下一步要补的：更难的种子用例 + 修复率上拉开差距的场景 + self-review
   的误判率（修错了却说通过了）。

这是一个**诚实的方法论示范**：小样本不急着下结论，而是明确说出
「当前数据证明了什么、证明不了什么、下一步怎么补」——面试时这样复盘
一个实验，比甩一个漂亮数据更打动人。

```bash
python experiment.py                            # 全矩阵
python experiment.py --cases case01_add --methods mreview,self
```

## 下一步（规划）

- [x] 扩充到 22 个公开/隐藏测试分离的确定性用例
- [x] 加入 Planner，并完成 single/reviewer/planner 的 22×3 基线
- [ ] 新增跨文件、需检索、契约更复杂的 harder suite
- [ ] 用正式 dashboard 和失败案例写一页 mini-paper / 技术博客
