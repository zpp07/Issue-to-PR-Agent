# 交接：把 Issue-to-PR Agent 产品化（可视化 + 可用）

> **作者**：Claude Code
> **日期**：2026-09-24
> **状态**：待 Codex 执行
> **前置**：本文的现状盘点是**读当前分支代码**得到的，不是照抄 `README.md` 或既有文档。

---

## 0. 为什么做这件事

项目现在的状态是：**功能完整（README checklist 10 项勾了 9 项），但没有任何人用过它。**

`docs/DEMO.md` 是一份 5 分钟**讲解脚本**——观众看的是演示者的终端和 Markdown 文件。这不是产品演示，是技术报告朗读。

**而这件事和项目已有的价值观是同一件事：**

> 这个项目从头到尾在主张「**不要相信声称，要看证据**」——
> 独立 hidden tests、审批绑定哈希、trace、失败分类。
>
> **一个能实时看到 Agent 每一步在做什么的界面，就是「可验证」的界面化。**

所以这不是"加个壳"，是**把项目的核心主张变成可以亲手体验的东西**。

---

## 1. 现状盘点（读代码所得）

### 1.1 ✅ 已有的：后端端点其实相当完整

`service_api.py`（FastAPI）已实现 **12 个端点**，完整覆盖审批链：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/tasks` | 建任务 + 生成计划 |
| POST | `/tasks/github` | 从 GitHub Issue 建任务 |
| POST | `/tasks/{id}/plan` | 重试规划 |
| POST | `/tasks/{id}/approvals/plan` | **审批①** 计划 |
| POST | `/tasks/{id}/execute` | 执行修复 |
| POST | `/tasks/{id}/approvals/diff` | **审批②** diff |
| POST | `/tasks/{id}/draft-pr` | 冻结 Draft PR 请求 |
| POST | `/tasks/{id}/approvals/publish` | **审批③** 发布 |
| POST | `/tasks/{id}/draft-pr/publish` | 真实发布 |
| GET | `/tasks/{id}` | **task + events** |
| GET | `/tasks/{id}/memories` | 任务记忆 |
| POST | `/tasks/{id}/memories/facts` | 写入带来源的事实 |

`service_http.py` 用**标准库**实现了同一组端点（`http://127.0.0.1:8765`，无第三方依赖）。

**底层设施也齐**：SQLite 持久化 task / artifact / approval / append-only event。

### 1.2 ❌ 缺口（**关键**）

| # | 缺口 | 位置 | 严重度 |
|---|---|---|---|
| **G1** | **执行期间零进度** | `issue_to_pr.py:92-140` | 🔴 **最高** |
| **G2** | **没有任何方式查看 diff 内容** | `workflow.py` 存了 artifact，但无端点暴露 | 🔴 高 |
| **G3** | 无任务列表端点 | 只有 `GET /tasks/{id}`，没有 `GET /tasks` | 🟡 中 |
| **G4** | **没有任何前端** | 全仓库零 HTML/JS/CSS | 🔴 高 |
| **G5** | 无 CORS 配置 | `service_api.py:77` | 🟡 低 |

#### G1 详解（**这是最要命的一条**）

我在 `issue_to_pr.py` 里 grep 了所有 `store.event` 调用，**全文只有两处**：

```
issue_to_pr.py:68   github_issue_imported
issue_to_pr.py:86   planning_failed
```

**`execute_after_plan_approval`（第 92 行起）内部一个事件都不发。**

它的结构是：

```python
def execute_after_plan_approval(self, task_id, target_file, max_rounds=3):
    ...
    passed, issues, rounds, usage = multi_agent_review(...)   # ← 阻塞数分钟
    ...
    test_result = self.sandbox.run(...)                        # ← 再阻塞
    ...
```

**后果**：前端一旦调用 `POST /execute`，就会**阻塞 3–5 分钟、期间拿不到任何信息**。用户看到的就是一个转圈。

> **对 Agent 类产品来说，这是最致命的体验问题**——Agent 的价值有一半在"它是怎么想的"，
> 而当前的实现把这一半完全藏起来了。
>
> 好消息是：**`run_agent` 本来就产出 `trace`**（每步的 action/args/status），
> 数据是现成的，只是没有被发出来。

#### G2 详解

`workflow.record_execution` 把 diff 存成了 artifact：

```python
# core/workflow.py:138
self.store.artifact(task_id, "diff", diff, diff_hash)
```

但**没有任何端点能把它取出来**。`GET /tasks/{id}` 只返回 `task` 和 `events`，而 events 里只有哈希（`diff_sha256`），**没有 diff 文本**。

**没有这个，用户在审批 diff 时看不到自己要批什么。** 审批链再严密，人也是盲批。

> 注：`real_run.py` 里的 `_agent_patch()` 证明生成 unified diff 的能力是现成的，
> 缺的只是把它暴露出来。

---

## 2. 目标定义

**一句话目标：**

> 任何人 clone 这个仓库，**不写一行代码、不读一份文档**，就能在一个浏览器界面里完成
> 「贴一个 Issue → 看 Agent 实时工作 → 审 diff → 批准 → 看到 Draft PR 请求」的完整流程。

### 2.1 三条硬性验收标准

| # | 标准 | 检验方式 |
|---|---|---|
| **A1** | **执行期间能看到步骤实时出现** | 点「执行」后，界面每秒内出现新步骤（搜索/读文件/改代码/跑测试） |
| **A2** | **diff 能在界面上看到** | 审批 diff 时，界面显示 unified diff 的可读渲染 |
| **A3** | **零构建步骤** | `git clone` → 一条命令启动 → 打开浏览器。**不需要 `npm install` / `npm run build`** |

> A3 是刻意加的限制。理由：这个项目的价值主张是「别人能验证」。
> **一个需要 build 的前端，会在"别人愿意试"这一步流失掉大半人。**

---

## 3. 工作分解

### W1 · 让 agent 循环发出实时事件 `P0`

**做什么**

1. 给 `core/agent.py::run_agent` 加一个**可选**回调参数，例如：
   ```python
   def run_agent(..., on_event: Callable[[dict], None] | None = None):
   ```
   在每次 `trace.append(...)` 的位置**同时**调用 `on_event(record)`。
2. `coder_reviewer.py::multi_agent_review` 透传这个参数。
3. `issue_to_pr.py::execute_after_plan_approval` 传入一个把事件写进 `store.event()` 的回调。

**硬约束（**这一条最重要**）**

> **新增参数必须完全可选，默认 `None` 时行为与现在逐字节一致。**
> 现有 **70 个测试必须全部继续通过**，不得修改任何断言。

**验收**
- 新增一个 fake-client 单测：传 `on_event` 后能收到有序事件序列；不传时行为不变。
- 手动跑一次 `POST /execute`，`GET /tasks/{id}` 的 `events` 里能看到执行期的每一步。

---

### W2 · 执行改为异步 + 进度可见 `P0`

**做什么**

`POST /tasks/{id}/execute` 不应阻塞 HTTP 请求分钟级。

**二选一，请你判断后写明理由：**

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. 后台线程 + 轮询** `GET /tasks/{id}/events?after=<n>` | 实现简单、无断线问题、对本地单用户足够 | 有轮询延迟 |
| **B. SSE 流式** `GET /tasks/{id}/events/stream` | 真正的实时、演示效果最好 | 需处理断线重连、需要并发支持 |

**我的倾向：B 为主 + A 兜底**（SSE 不可用时前端自动退回轮询）。
但如果你认为本地单用户场景下轮询已足够，**选 A 也合理，请在文档里说明取舍**。

**验收**
- 执行期前端能持续拿到新事件；
- 任务结束后事件流能正常终止（不能挂着不关）。

---

### W3 · 补齐三个只读端点 `P0`

| 端点 | 说明 |
|---|---|
| `GET /tasks` | 任务列表（id、状态、issue 摘要、创建时间） |
| `GET /tasks/{id}/artifacts` | 列出该任务的 artifact（kind + sha256 + 大小） |
| `GET /tasks/{id}/artifacts/{kind}` | 取指定 artifact 内容（**特别是 `diff`**） |

**注意**：artifact 内容可能很大（diff、测试报告）。**必须做长度上限和截断标记**，并且**不得**把 `gold_patch` / hidden tests 之类 evaluator-only 内容暴露出去（当前 workflow 里没有这两类 artifact，但请在实现时加一道显式白名单）。

**验收**：能用 `curl` 取到 diff 文本并在前端渲染。

---

### W4 · 前端单页应用 `P0`

**技术约束**：零构建（见 A3）。推荐单文件 HTML + 原生 JS（或 CDN 引入的轻量库）。

**必须有的五个视图**

1. **任务列表** — 状态徽章（`awaiting_plan_approval` / `ready_to_execute` / `awaiting_diff_approval` / `ready_to_publish` / `draft_pr_created` …）
2. **新建任务** — 填 repo_path + issue 文本，或 GitHub repository + issue number
3. **任务详情 · 实时步骤流** ⭐ — **这是整个界面的核心**。每一步显示：动作类型、目标文件、结果状态（ok / rejected / error）
4. **diff 视图 + 审批按钮** — 显示 unified diff，附上已绑定的 SHA-256，批准/拒绝
5. **Draft PR 请求预览 + 发布审批** — 显示冻结请求的全部字段（repository / 分支 / 标题 / 正文 / commit message / `draft=true`），再批准

**关于演示数据**

> **默认演示必须使用 fake publisher，绝不能真的创建 GitHub PR。**
> 真实发布按钮必须：默认隐藏或禁用，需要显式打开一个开关，且点击后二次确认。

**验收**：从空数据库开始，全程不碰命令行就能走到 `draft_pr_created`。

---

### W5 · 一键启动 `P1`

**做什么**
- 一个命令同时起服务 + 打开浏览器（例如 `python -m app.serve --open`）
- CORS 配置（若前后端分离）或同源托管（若前端由后端直接返回）
- `.env.example` 补上新增的环境变量说明

**验收**：`git clone` 后，安装依赖 → 一条命令 → 浏览器可用。

---

### W6 · 测试与文档 `P1`

**做什么**
1. **端到端集成测试**：用现有的 fake-client 模式（见 `tests/test_phase46.py` 的 `_tool_response` / `_client`），
   覆盖「建任务 → 审批 → 执行 → 审批 → 冻结请求」全链路。
2. **不引入真实模型调用**：所有新测试必须离线可跑。
3. `README.md` 首屏加一张界面截图（或 GIF）。
4. `docs/DEMO.md` 重写为**基于界面的**演示脚本，不再是终端命令串联。

**验收**：`python -m pytest` 通过，总数 ≥ 70（新增测试不得破坏原有断言）。

---

## 4. 明确不做

| 不做 | 理由 |
|---|---|
| ❌ **用户认证 / 多租户** | 项目定位是单用户本地原型，`README.md:240` 已明确承认 |
| ❌ **自动创建真实 GitHub PR** | 必须人工单独授权；UI 默认用 fake publisher |
| ❌ **引入需要 build 的前端框架** | 违反 A3 |
| ❌ **修改现有 70 个测试的断言** | 破坏回归基线 |
| ❌ **改动 `protocol_hash` 相关代码** | 会让未来运行与 E2 的 36 行不同组（见 `CLAUDE_FINDINGS_2026-09-23.md` 发现五） |
| ⚠️ **无上限地跑付费实验** | **允许**付费实验，但**累计不得超过 2000 万 token**（见 §4.1） |

---

## 4.1 付费实验的预算（**允许，但有上限**）

**允许跑付费模型实验，累计不超过 2000 万 token。**

### 参考量级

| | Tokens | 成本 |
|---|---:|---:|
| E2 的 36 条正式运行 | 11,244,461 | ¥23.4069 |
| **本次预算上限** | **20,000,000** | 约 ¥40（按历史单价 ≈ ¥2.08/M） |

**即：约等于 1.8 倍 E2 的规模。**

### 推荐的用法（按优先级）

1. **一次真实的端到端 smoke** —— 从建任务一路走到 Draft PR 请求。
   这是 `README.md:394` checklist 里**唯一没勾**的一项，**最值得花这笔钱**。
2. **用玩具任务验证 UI 链路** —— `python -m benchmark.run --cases case01_add --methods single`，
   历史单价约 **¥0.01/次**，几乎免费。
3. **谨慎使用真实仓库任务** —— 单价 ¥0.5–1.5/次：
   - ✅ `iterative__dvc-4124`（历史最便宜，约 255k tokens）
   - ⚠️ 避免在 `facebookresearch__hydra-1791`（历史吃掉 38.5% 预算）上盲目重复

### 仍然必须遵守（与 token 预算无关）

- **演示和回归测试一律用 fake client**，这部分**不消耗**预算；
- **不得真实创建 GitHub PR** —— 这一条不受预算放宽影响，仍需人工单独授权；
- **每次付费运行后报告实际消耗**（tokens + 估算成本），并在任务报告里维护累计值。

### ⚠️ 一个提醒

> **2000 万是上限，不是目标。** 如果不需要付费运行就能验收 W1–W6，**那就不要跑**。
> 这个项目的价值观是「每一分钱都对应一个可解释的结论」——为花而花与它相悖。

---

## 5. 风险

| # | 风险 | 缓解 |
|---|---|---|
| **R1** | 给 `run_agent` 加回调可能影响现有 70 个测试 | 参数必须可选、默认 `None` 时行为不变；先跑一遍基线再动 |
| **R2** | 前端可能被"顺手"加上构建依赖 | 把 A3 写成硬性验收标准 |
| **R3** | 事件量过大（长任务每步都写 SQLite） | 给事件写入加上限或采样；参考 `real_run.py::compact_trace` 的做法——**只存导航决策，不存源码内容** |
| **R4** | 界面可能泄露 evaluator-only 内容 | W3 的白名单；UI 只消费 `/tasks` 下的公开 artifact |
| **R5** | 执行异步化后，任务状态可能出现竞态 | 复用已有的状态机（`store.set_status`），不要在前端自己推断状态 |

---

## 6. 给你的问题

1. **W2 选 A（轮询）还是 B（SSE）？** 我倾向 B 为主 + A 兜底，但本地单用户场景下你可能认为 A 更稳。
2. **W1 的回调签名**：`on_event(record: dict)` 够用，还是你希望它带上下文（step / phase / task_id）？
3. **有无更简单的实现路径**是我没想到的？比如复用 `trace` 列表本身做钩子（传一个自定义 list 子类），
   避免改 `run_agent` 的函数签名——**如果你认为那样更干净，我接受**。
4. **W4 的前端要不要单独成仓库**（如 `issue-to-pr-ui/`）？
   - 独立仓库：简历上是"第二个项目"，但需要跨仓库协调
   - 同仓库子目录：简单，但 `multi-agent` 会变大
   - **我倾向同仓库**（`web/` 子目录），因为它不是独立产品，是这个 Agent 的界面。
5. **验收标准 A1–A3 是否认同**？特别是 A3（零构建）——这条会限制技术选型。

---

## 7. 一句话总结

> **不要给这个项目加功能。给它开一扇窗——让人能看见它已经在做的事。**
