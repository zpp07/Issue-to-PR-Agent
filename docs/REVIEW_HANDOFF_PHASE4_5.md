# 代码走读会话交接：Phase 4 – 4.5

> 写给压缩后的 Claude Code 自己。
> 上一份同类文档是 `REVIEW_CONTINUATION.md`（Phase 1–3 走读），本文续接它，并补充本次会话
> （Phase 4 部分走读 + Phase 4.5 失败分析 + 下一步计划）的全部结论。
>
> **任务性质不变**：陪用户逐文件走读、讲解答疑。**不是改代码。**

---

## 1. 当前仓库状态（快照，以实时命令为准）

```
工作区根：D:\workfiles\Markdown_PDF2\pytorch\agent项目
Git 仓库根：multi-agent/          ← 已自包含；tests/ 已从父目录搬进 multi-agent/
分支：phase4.5-real-benchmark
HEAD：ef90451
未跟踪文档：docs/CLAUDE_CODE_HANDOFF.md
            docs/FAILURE_ANALYSIS_HANDOFF.md   ← 本文作者写的，含 3 处已知错误待改
            docs/PLAN_2026-09-18.md            ← 已交 Codex 审查，审查意见尚未落进文档
            docs/REVIEW_HANDOFF_PHASE4_5.md    ← 本文
测试：python -m pytest --collect-only → 41 项
      (phase2=8, phase3=8, phase4=5, phase45=16, phase5=4)
```

**其它项目**（同级目录，非本仓库）：`rag-demo/`、`swe-agent/`、`git-practice/`。

---

## 2. 讲法约定（**不要变**）

1. **一次只讲一个文件**，末尾留「继续 or 追问」出口，不要一口气灌完。
2. **讲之前先 Read 到当前真实代码**，不要照搬文档背诵；文档与代码不一致时**以代码为准并指出**。
3. 结合**具体函数名 + 行号**，用 markdown 链接（`[file.py:42](file.py#L42)`）——用户会把文件在
   VS Code 里开着对。
4. 重点讲**「为什么这么设计」**；主动解释用户可能不熟的 Python 语法。
5. 用户中途问「X 是什么」时，**即时用具体代码 + 类比简短回答，答完回到主线**。
6. 用户是 Python/agent 入门者，靠**比喻**建立连贯理解（见 §4，继续复用，不要换说法）。

---

## 3. 已讲 / 未讲

### 已讲透

| 段 | 文件 |
|---|---|
| 一 · 通用 Agent 循环 | `core/client.py`、`core/agent.py`、`core/tools.py`（`core/usage.py` 随讲带过） |
| 二 · 审批绑定哈希 | `core/policy.py`、`core/audit.py`、`core/workflow.py`、`tests/test_phase2.py` |
| 三 · 多 Agent 编排 | `coder_reviewer.py`、`issue_to_pr.py`（`graph_agents.py` 未讲） |
| 四 · Docker 安全边界 | `core/sandbox.py`、`docker/Dockerfile`、`tests/test_phase3.py` |
| 五 · 评测可信度 | `benchmark/catalog.py`、`dataset.py`、`run.py`、`dashboard.py`、`results.md` |

### Phase 4（讲了约一半）

- ✅ `core/search.py`（全文讲解：切块 / BM25 / hashing dense / rerank / 融合）
- ✅ `core/tools.py` 的 `search_code` 工具 + `issue_to_pr.py` 的角色权限分工
- ❌ `benchmark/harder_catalog.py` / `harder_dataset.py`
- ❌ `benchmark/harder_run.py` 与两轮受控消融数据

### 完全未讲

- ❌ **Phase 5**：`core/memory.py`（四类记忆、逐字证据、source SHA、stale 判断）
  —— 交接文档 `PHASE4_5_HANDOFF.md` §2 第 6 项要求重点讲这个
- ❌ **Phase 4.5 全部代码**：`benchmark/real_cases.py`、`real_snapshot.py`、`real_verify.py`、
  `real_run.py`（`real_run.py` 在会话中被**详细讲过**，但那是压缩前的临时讲解，未留下独立笔记；
  关键结论见 §5）
- ❌ `benchmark/real/` 下的 `selected.jsonl` / `snapshot_audit.jsonl` / `test_audit.jsonl`

### 用户下一步的意愿

**继续理解复习各个 phase 的工作和代码。** 优先级建议：

1. Phase 4 剩余（`harder_run.py` + 受控消融）—— 补完「检索为什么没收益」的完整证据链
2. Phase 5（`core/memory.py`）—— 独立且未被覆盖
3. Phase 4.5 代码（`real_verify.py` → `real_run.py`）—— 文档认为这是「当前最重要」

---

## 4. 主线比喻（继续复用，别换说法）

- **每层只干一件事**：client 管「连得上」、agent 管「循环」、tools 管「工具怎么安全执行」、
  usage 管「记账」。后续 Phase 靠**换注入点**叠上去，核心层不改。
- **多 agent = 同一个 `run_agent` + 不同 prompt + 不同 tools + 不同终态工具**。不是框架魔法。
- **审批绑定哈希**：批准的不是「任务」而是**不可变内容的 SHA-256**，防 TOCTOU。
- **白名单 ≠ 沙箱**：Phase 2 是「受限执行策略」，只有 Phase 3 的容器才是安全边界。
- **Reviewer 通过 ≠ 事实**：`tests_pass` 来自独立 verifier，`agent_passed` 来自模型自评。
- **prompt 是请求，机制是权限**（工具表里没有 = 真正的强制）。
- **上下文工程 = 往 task 字符串里拼东西**（计划 / 记忆 / 检索证据走同一条通道）。
- **保留中间量是可观测性的地基**（4 个检索分项分数、16 个结果字段、trace）。

---

## 5. 本次会话新增的关键结论（Phase 4.5 失败分析）

> 完整分析在 `FAILURE_ANALYSIS_HANDOFF.md`，但**那份文档有 3 处已知错误**（见 §6），
> 下面这些是**已核实**的版本。

### 5.1 账本事实（已核实）

- 11 条运行、3 通过、2,743,288 tokens、¥5.7008、39.3 分钟
- **`agent_completed=True` 的 3 次全部通过；`False` 的 8 次全部失败**（样本内完美相关）
- 5 次运行零改动 = **41.8% token / 41.6% 费用**
- hydra-1791 四次吃掉 38.5% 预算、0 通过；pydantic 两次吃掉 20.9%、0 通过

### 5.2 五类失败模式（按「哪一步开始跑偏」分类）

| 类 | 模式 | 运行 | 首次编辑 | 费用占比 |
|---|---|---|---:|---:|
| A | 收敛成功 | dvc-4124 / dvc-4185 / dask-7894 | step 3 / 8 / 4 | 16% |
| **B** | **细粒度定位或决策提交失败，最终只读未收敛** | hydra-1791×2、pydantic-8977×2 | 从不 | **39%** |
| C | 改对文件，改不对补丁 | hydra-1006×2 | step 12 / 13 | 24% |
| D | 规划阶段卡死 | hydra-1791 pe t1 | — | 2% |
| E | 改对文件但破坏现有测试 | hydra-1791 pe t2 | step 4 | 18% |

**A 类的共同特征是「早」**：在 step 3–8 就动手编辑，其余步数用于跑测试确认。

### 5.3 B 类的正确表述（Codex 纠正，已接受）

**不是「找不到文件」**——B 类运行实际**读到了 gold 文件**
（hydra-1791 反复读 `hydra/core/default_element.py`，即 gold 文件）。

**是「读到了文件，但一直不提交修改」**：读同一文件的重叠区间达 6 段、覆盖近 700 行。

**这直接废掉了原定的「把 gold 文件塞进初始证据」实验**——它的前提（找不到文件）已被推翻。

### 5.4 仓库规模那条**只是相关性，不是因果**（Codex 纠正，已接受）

现象：338/372/374 文件的任务全过；401/723/1122 的全败。

**但反例很强**：`hydra-1006` 有 **723 个文件**，两轮 `gold_file_recall` 都是 **1.0** ——
它定位没问题，是补丁语义错。所以「仓库大 → 定位不了」不成立。

**正确表述**：当前样本显示成功率与仓库规模存在明显相关性，**不能确定约 400 文件的因果阈值**。

### 5.5 已核实的三条技术事实

1. **`core/search.py` 的 `_python_chunks` 只遍历 `tree.body`** ——
   不支持类方法、嵌套定义、overload；`symbol` 是裸名不是 qualified name。
   （实测：一个 `class Foo` 只产出一个 chunk，方法名一次都不出现。）
   **→ 这意味着新增 `read_symbol` 的工作量比预想大得多，需要递归 AST symbol index。**
2. **`agent_completed` 语义有洞**：`core/agent.py:147-151` 的 `free_text` 分支返回**字符串**，
   而 `real_run.py:305` 用 `not isinstance(result, dict)` 判定 → **自由文本回复也算「完成」**。
3. **`real_run.py` 三处 `run_agent` 调用都没传 `max_cost_rmb`**，`--max-tokens` 也无默认值
   → 那 4 次零改动运行是在**零预算上限**下烧掉 ¥2.37 的。

### 5.6 搜索预算的真实历史（Codex 查明，已用两路证据验证）

| 账本行 | 运行时 commit | 说明 |
|---|---|---|
| 6 条 single t1 | `bc54a5d` | **当时还没有 `with_search_budget`** |
| 3 条 single t2 | `cd142b5` | 预算已生效，但该期 trace **不记录搜索的 result_preview** |
| plan_execute t1 | `bb0e944` | 拒绝结果已可见 |
| plan_execute t2 | `acd3463` | 同上 |

**实际被拦 5 次**（hydra-1791 t2 ×1、pydantic t2 ×2、pe t2 ×2），不是 2 次。

---

## 6. 我在本次会话中犯过的错（**不要再犯**）

| # | 错误 | 教训 |
|---|---|---|
| 1 | 说「失败的运行里有 6-8 步在重试被拒的搜索」 | 从 `search_calls`（数调用不数执行）反推结论 |
| 2 | 说「全账本只有 2 次检索被拦」 | 用 `result_preview` 判定，而**该字段在相关时期的代码里对搜索是关闭的** |
| 3 | 把 B 类命名为「定位失败」 | 说过头了；实际是「到了正确文件但不提交」 |
| 4 | 说成功率与仓库规模有**因果**关系 | n=6 且有混淆变量；`hydra-1006` 是直接反例 |
| 5 | 说 `_python_chunks`「已经算出了 symbol，加个参数就行」 | **没验证就断言**；实测完全不支持类方法 |

> **共同教训**：**在用某个字段/机制下结论之前，先确认它在数据产生的那个代码版本里
> 是否存在、语义是什么。** 「现在有」不等于「当时有」。
> 凡是能从仓库验证的断言，**先验证再说**。

---

## 7. 当前未完成的动作

| 项 | 状态 |
|---|---|
| `FAILURE_ANALYSIS_HANDOFF.md` 的 3 处错误（§3 因果、§4 命名、§5 预算历史） | **未改** |
| `PLAN_2026-09-18.md` 的 Codex 审查意见 | **未落进文档** |
| 分工提议（Codex 做 W1/W2/W3/W5/W8 代码，我做 W6/W7 + 审查） | **已提出，未定** |
| 付费实验 E1（无进展干预的 off/on 对照） | **未批准** |

### Codex 对计划的实质修改（若继续推进，必须知道）

- `protocol_hash` 应覆盖：strategy、全部 prompt、工具 schema、模型参数、运行预算、
  检索配置、工具行为配置、W5 完整配置；**且 `protocol_hash` 与 `code_revision` 分开记录**
  （否则改 README 也会产生新协议哈希）
- 需另设 `evaluation_protocol_hash`（case 版本、snapshot hash、镜像 digest、隐藏测试版本等）
- **W6 分类边界**：B/C 以「是否**成功**改变生产文件内容」划分，不是「是否出现编辑调用」
  （被拒的写入、零匹配的替换都不算）
- **W4（命中窗口收窄）建议从范围中移除**，降为 P2
- **W5 阈值 N=9**（依据：三个成功样本首次编辑在 step 3/8/4，N=9 不干扰它们；
  按 LLM turn 计，不按 tool call 计；被拒编辑不重置；仅首次成功编辑前生效；不用于只读 Planner）
- **E1 目前只能算 exploratory smoke**，不是严格对照——因为 W3/W2/runner 同时变化，无法归因；
  需要同协议下的 off/on 两组
- **实施顺序应调整为**：W8 → W1 → W2 → W6/W7 → W5 → W3（W1 依赖 W8 的 schema）

---

## 8. 铁律（继续遵守）

1. **不改代码**，除非用户明确要求。
2. **不重跑付费 benchmark**；不运行任何新的 DeepSeek/API 评测，除非用户**明确批准预算**。
3. **不读、不打印、不提交任何 `.env` 内容或 API key。**
4. **不把 gold patch / 隐藏测试暴露给 Agent。**
5. **执行任何命令前**，先用一句中文说明：① 做什么 ② 为什么 ③ 是否修改文件，**再请求权限**；
   不要直接展示长命令。（此条已存入长期记忆）
6. 事实口径见 §9。

---

## 9. 事实口径（对外不能说错）

- ✅ 可以说 Phase 1–5 与 4.5 已完成并真实运行；真实仓库评测有 11 条记录
- ❌ **不能说「多 Agent 比 single 更好」**：Phase 3 数据相反（single 22/22=100%，
  reviewer/planner 各 21/22=95.5%，token 贵 2.7–3.1 倍）
- ❌ **不能说「RAG 提升成功率」**：Phase 4 是 100%→100%，token 反而 +5.3%
- ❌ **不能说默认使用 BGE**：正式消融用的是离线 hashing 后端
- ❌ 不能把 Phase 2 的命令白名单称为安全沙箱
- ❌ 不能说已自动创建 PR（目前只到 `approved_for_pr`）
- ❌ 不能说已完成生产级权限系统（单用户本地原型）
- ❌ 不能把 hidden test 通过等同于真实世界正确
- ❌ **不能把真实的 6 个任务当统计显著结论**；也不能把 11 行直接称为「成功率」——
  **它们混了至少三种协议、四个代码版本**
- ⚠️ 另注：`real/README.md` 记录的「默认离线检索器 top-10 只命中 1/6 gold 文件」
  是**离线 hashing + lexical rerank 后端**的成绩，**不是 BGE 的成绩**

---

## 10. 用户的目标（决定讲解的取舍）

用户的定位是**找 Agent 开发实习**（也在考虑模型算法岗，但这个项目本身更偏 Agent 工程）。
已明确表示：**在意的是把项目做成有价值的作品集，而不只是省 token**。

因此讲解时值得主动指出：

- 哪些地方体现了**判断力**（诊断失败、推翻自己的计划、保留负结果）；
- 哪些地方是**技术债**（已发现的：`_prepare` 里硬编码的 hydra 分支、
  `HybridCodeSearch` 索引不随文件修改更新、`agent_completed` 的语义洞）。
