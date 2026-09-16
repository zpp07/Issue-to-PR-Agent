# 升级规划：从 toy demo → 可信赖的 Issue-to-PR Agent

> 定稿 **v2**：2026-09-11。主线不变，采纳 Codex 审查，修正「隔离 / 审批 / 评测」的安全边界与依赖顺序。
> 背景：四个 demo（multi-agent / rag-demo / swe-agent / git-practice）升级为「找 agent 开发实习」的作品集。

## 0. 主线与决策

**主线**：把 `multi-agent` 收敛为 **「Python 服务 / 数据处理项目的 Issue-to-PR Agent」**：

```
Issue/需求 → 代码库检索定位 → 结构化计划 → 受限执行 → 测试+审查 → 人工审批 → diff/PR 报告
```

**已拍板的决策**：
1. **场景收敛**：只做 Python 服务/数据处理项目的 Issue-to-PR，不并通用 SWE + 通用咨询。benchmark 全部用 Python 用例。
2. **隔离措辞（v2 修正）**：分两层——「受限执行策略 + 临时工作区」是 host 上的工程手段（Phase 2），**不是安全边界**；真正的「隔离」只发生在 Docker 容器（Phase 3，无网络、非 root、资源限制）。叙事上不再把白名单叫「隔离」。

**范围声明（v2 补充）**：项目定位为**单用户本地 Agent**，先做最小 `task / approval / artifact` SQLite 数据模型 + REST 接口，不急着做复杂 SaaS。真正接 GitHub PR 时，再说明 token scope、远程操作审批、审计设计。

---

## 1. v2 关键设计修正（采纳 Codex 审查）

| # | 修正点 | 内容 |
|---|---|---|
| 1 | 白名单 ≠ 隔离 | `shell` 下允许 python/pytest 会执行仓库里任意代码，且支持重定向/管道/环境变量注入。Phase 2 只称「受限命令策略 + 临时工作区」 |
| 2 | benchmark 依赖顺序 | Phase 1 验收不依赖 20+ case（那是 Phase 3 才建）。Phase 1 用「2 个 smoke case + 故障注入测试」独立验收 |
| 3 | 评测矩阵引用未实现能力 | Planner 必须先实现（Phase 2），再跑「无检索 benchmark」；检索做完后作**第二轮消融**加入，避免评测口径与代码版本不一致 |
| 4 | Checkpoint ≠ 审计日志 | SqliteSaver 只用于图状态恢复；审计需自建 **append-only 事件表**（task_id、审批人、动作、理由、时间、base commit、diff SHA、测试产物哈希） |
| 5 | Git 基线提前 | Diff 审批依赖可复现基线，Git/worktree 从 Phase 2 就引入（每任务独立 worktree）；Phase 6 只做 commit / PR body / 远程 PR |

**补充修正**：
- `run_command` 应取消 `shell=True` 改受控 argv；但 pytest/python 仍执行不可信仓库代码，只能作为 Docker 前的便利策略。
- `make_executor` 需防 **symlink / Windows reparse point** 绕过：不能只用 `abspath + commonpath`，要解析真实路径、拒绝工作区内指向外部的链接。

---

## 2. 现状基线（survey 确认）

已具备（可复用，别重写）：`core/`（agent 循环 / tools / usage / client）、`coder_reviewer.py`（结构化审查）、`graph_agents.py`（LangGraph HIL）、`eval/`（seeds + 篡改/污染检测）、`experiment.py`。

**Phase 1 已落地**（本次完成）：
- `core/client.py`：`timeout`/`max_retries` 显式配置 + `LLMCallError` 分类异常 + .env 加载日志
- `core/tools.py`：`run_command` 加 timeout + 输出截断；`read/write_file` 加 size 上限 + try/except
- `core/agent.py`：启用 logger + request_id + 工具输出截断 + token/step/成本预算 + LLM 异常分类

当时的全局软肋（历史基线）：`run_command` 仍 `shell=True`；无 pyproject/lockfile；无单元测试；无结构化 trace；无 diff 审批/检索/任务记忆/hidden-test benchmark/git-PR/Planner。Phase 1–3 已解决其中大部分，保留此段用于说明改造起点。

---

## 3. 分阶段路线图

### Phase 1 · 可靠性底座 + 可复现工程

- **代码已改**（见上）；剩余：
- 补 `pyproject.toml` + lockfile，依赖可复现
- 单元测试：tools 策略、预算上限、异常分类
- 结构化 trace：request_id 关联（替代无序 in-memory list）
- 故障注入测试：429 / 超时 / 网络错误 / 工具超时 / 超大输出
- **LLM 重试只保留一层**：SDK 内置 `max_retries`（默认对连接失败/408/409/429/5xx 重试 2 次），**不自造第二层退避**；设总耗时/总重试上限

完成标志：2 个 smoke case + 故障注入测试全绿；不因一次 429/超时整体崩。

### Phase 2 · 受限执行 + 结构化 Planner + 审批（已落地）

- `run_command` 取消 `shell=True` → 受控 argv；命令白名单/策略层（pytest/python 为便利策略，非安全边界）
- **结构化 Planner**：输出固定 schema `{目标, 影响文件, 计划步骤, 风险, 允许命令}`
- **每任务独立 git worktree**；审批绑定 `base_commit + diff_sha256 + test_report_hash`（防 TOCTOU：批准计划 A、实际执行 B）
- 三处审批：计划 / diff / 高风险
- **审计用 append-only 事件表**（task_id、审批人、动作、理由、时间、base commit、diff SHA、测试产物哈希）；SqliteSaver 只用于图状态恢复
- 措辞：这里叫「受限执行策略」，**不叫隔离**

完成标志：Issue → 计划 → 受限执行 → 测试/审查 → 审批（可审计）→ diff 输出 全链路跑通。\
当前实现：`core/policy.py`、`core/audit.py`、`core/workflow.py`、`issue_to_pr.py` 与 `service_api.py`；`tests/test_phase2.py` 覆盖命令策略、大小限制、worktree、计划/diff 哈希审批和审计事件。

### Phase 3 · Docker 真隔离 + benchmark（已完成并实测）

- Docker：无网络、非 root、CPU/内存/时间限制、不挂载宿主密钥
- hidden tests 在 agent 工作目录外，评测后由**独立 runner** 执行
- `make_executor` 防 symlink / reparse point 绕过（解析真实路径，拒绝工作区内指向外部的链接）
- benchmark：**先跑无检索基线**「单 agent / coder+reviewer / 带 planner」（Planner 已在 Phase 2 实现），20+ seed 带 hidden tests，指标 + dashboard

当前实现：`core/sandbox.py` + `docker/` 提供容器边界；`benchmark/`
包含 22 个公开/隐藏测试分离的 case、single/reviewer/planner 统一 runner、可恢复并发执行、
JSONL 指标和 Markdown dashboard。Docker Desktop 4.91.0 / Engine 29.8.0 已完成真实验收：
容器内 UID 65532、宿主 API key 不可见、网络被阻断；13 项回归测试通过。

`deepseek-chat` 的 22×3 最终矩阵：single 22/22（平均 3,586 tokens / ¥0.0095），
reviewer 21/22（9,725 / ¥0.0264），planner 21/22（11,271 / ¥0.0321）；总费用
¥1.4955，0 次测试改写，0 次容器超时。两项失败均是「Agent 自评通过、hidden verifier
失败」。这说明当前函数级数据集偏简单，不能宣称多 Agent 提升成功率；它反而明确给出
下一轮应转向跨文件、需检索、契约更复杂任务的实验依据。

完成标志：已获得可复现数据，可回答「当前任务上独立 reviewer 不值额外成本」，并能说明
benchmark 校准、失败案例和独立 verifier 的必要性。

### Phase 4 · 代码库检索 + 第二轮消融

- AST 结构切块 + BM25 + 向量召回 + bge-reranker（`core/search.py`）
- 接进 coder 的 toolset
- **只在「跨文件 / 需读文档」子集上比较**（避免总指标被简单单文件 bug 稀释）

完成标志：面试能甩「检索让跨文件任务成功率 X%→Y%，token 省 Z%」。

### Phase 5 · 任务记忆

- 只持久化**有来源的事实、测试命令、失败记录、已批准决策**
- 模型自由推断**不能**成为长期事实

### Phase 6 · git/PR + 收尾

- commit、PR body、远程 PR 创建（token scope、远程操作审批、审计设计在此说明）
- 写技术报告：问题定义、架构、威胁模型、benchmark、消融、失败案例、成本
- 一起上传 GitHub

**不优先**：通用图片解析，只在真需要解析报错截图/设计稿时加，且要求视觉结论与代码证据交叉验证。

---

## 4. 下一步（Phase 4）

1. 新增跨文件、需读文档的 harder benchmark suite
2. 实现 AST/结构化切块 + BM25 基线
3. 再加入向量召回与 reranker
4. 在 harder suite 上做无检索/有检索消融
5. 补多随机种子与置信区间，避免单次运行结论过强

---

## 5. 安全 / 工程收尾 TODO

- [x] 仓库已自包含 `core/`、服务入口、测试、Docker、benchmark 与文档
- [x] 仓库根提供 `.env.example`，真实 `.env` 和 `.local/` 已忽略
- [ ] 首次公开前轮换本地开发使用过的 key，并再次执行 secret scan
- [ ] 评估是否把旧实验脚本迁入 `experiments/` / `legacy/`
- [ ] 最终作品集收尾时将通用包从 `core` 重命名为 `issue_to_pr_agent` 并采用 `src/` layout
