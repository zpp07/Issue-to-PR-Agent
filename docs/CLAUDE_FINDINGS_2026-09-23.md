# 走读发现交接：四处文档/代码不一致

> **作者**：Claude Code
> **日期**：2026-09-23
> **来源**：按 `docs/CLAUDE_HANDOFF_2026-09-22.md` §6 的提示词，逐模块走读 README /
> `core/agent.py` / `benchmark/real_run.py` / `benchmark/protocol.py` / `E2_REPORT.md` /
> `benchmark/analyze_e2.py` / `core/workflow.py` / `core/github.py` /
> `tests/test_phase46.py` / `tests/test_phase6.py` 时发现。
>
> **状态**：**未修改任何文件**。本文件是唯一新增产物。四条都等你裁定。

---

## 0. 验证方式与边界

### 0.1 我实际做了什么

| 手段 | 说明 |
|---|---|
| 读源码 | 上述文件全部按**当前分支代码**读取，不依赖交接文档转述 |
| `grep` | 在 `multi-agent/` 下全文搜索函数定义 |
| `pytest --collect-only` | 只收集不执行，得到测试分布 |
| 本地 Python 读已提交 JSONL | 统计行数、抽取 `exit_reason` / `completion_evidence` |
| 本地复算 Wilson 区间 | 纯 `math`，无第三方库 |

### 0.2 我**没有**做的事（请勿把我的结论当作已覆盖这些）

- 没有运行 `python -m benchmark.analyze_e2 --strict`
- 没有运行任何付费模型 / API / Docker
- 没有访问 GitHub（包括没有点开 CI 徽章确认流水线状态）
- 没有读取或输出 `.env`、API key、`GITHUB_TOKEN`
- 没有修改、重排或追加任何账本行

### 0.3 环境

```
分支：phase4.5-real-benchmark（与 origin 同步）
HEAD：3167ace docs: add Claude walkthrough handoff
测试分布：phase2=8 phase3=10 phase4=5 phase45=16 phase46=20 phase5=4 phase6=7 → 70
```

> 注：`CLAUDE_HANDOFF_2026-09-22.md` §0 记录主体提交为 `3caffcf`。当前 HEAD 已前进到
> `3167ace`（提交交接文档本身），**代码内容无变化**。

---

## 发现一：`benchmark/real_run.py::run_one` 这个函数不存在

### 位置

`docs/CLAUDE_HANDOFF_2026-09-22.md` §2.3「补丁之后的 Reviewer/Reviser」，主要入口列表：

```text
- benchmark/real_run.py::run_review_revise
- benchmark/real_run.py::run_one            ← 这一行
- core/tools.py 中的 review_finish
- tests/test_phase46.py
```

### 实际

仓库中**不存在任何名为 `run_one` 的定义**。

### 证据

在 `multi-agent/` 下全文搜索 `def run_one|def evaluate_one|def run_repair|def run_review_revise`：

```
benchmark/harder_run.py:48   def evaluate_one(case, method, sandbox, model):
benchmark/real_run.py:263    def run_repair(client, strategy: str, task: str, executor, model: str,
benchmark/real_run.py:330    def run_review_revise(client, task, str...
benchmark/real_run.py:436    def evaluate_one(case: RealCase, image: str, cache_root: str | Path,
benchmark/run.py:77          def evaluate_one(client, case, method, sandbox, model):
```

`real_run.py` 里承担「跑单个 case 一次」职责的函数是 **`evaluate_one`**（第 436 行）。

### 影响评估：**低**

读者按这个名字去编辑器里找会扑空，但不会导致错误结论。

### 建议（未执行，待裁定）

把 `run_one` 改为 `evaluate_one`。

### 附：同节的一个**非错误**，但值得补全

§2.2 写：

```text
- `exit_reason`：如 `verified_patch`、`max_steps_unverified_patch`、
  `max_steps_no_patch`。
```

**这里用了「如」，所以不是错误**，只是不完整。当前代码实际可产生的 `exit_reason` 至少有：

| 值 | 产生位置 |
|---|---|
| `llm_error` | `core/agent.py:227` |
| `max_tokens` | `core/agent.py:236` |
| `max_cost` | `core/agent.py:241` |
| `free_text` | `core/agent.py:254` |
| `no_progress` | `core/agent.py:342` |
| `verified_patch` | `core/agent.py:383` |
| `finish` | `core/agent.py:394` |
| `max_steps_unverified_patch` | `core/agent.py:410` |
| `max_steps_no_patch` | `core/agent.py:410` |
| `planner_incomplete` | `benchmark/real_run.py:307` |
| `review_incomplete` | `benchmark/real_run.py:393` |
| `review_passed` | `benchmark/real_run.py:396` |
| `no_candidate_patch` | `benchmark/real_run.py:514` |
| `revision_incomplete` | `benchmark/real_run.py:428`（默认值） |

补全对读 trace 的人有帮助。**是否补，由你决定。**

---

## 发现二：`analyze_e2.py` 的叙事数字是硬编码的

### 位置

`benchmark/analyze_e2.py` 的 `markdown_report()`，共 5 处：

| 行 | 硬编码内容 | 是否随数据更新 |
|---:|---|---|
| 181 | `"> 结论先行：…每组 12 次 …"` | ❌ 整块静态 |
| 206 | `"…B 相比 A 的观察通过率高 **16.7** 个百分点，"` | ❌ 静态 |
| 208 | `"C 相比 B 的观察通过率低 **8.3** 个百分点，"` | ❌ 静态 |
| 235 | `f"…只触发 {b['controller_triggers']}/12 次，**且都发生在 Pydantic**；"` | ⚠️ 计数是算的，`/12` 和「且都发生在 Pydantic」是静态 |
| 250 | `"- C 没有超过 B，并额外消耗约 **39%** token；…"` | ❌ 静态 |

### 证据

```
$ grep -n "16\.7|8\.3 个百分点|39% token|且都发生在|/12 次|结论先行" benchmark/analyze_e2.py
181:        "> 结论先行：在 4 个真实仓库任务、每组 12 次 provider-default 重复运行中，",
206:        "这是诊断性小样本，不支持显著性或因果结论。B 相比 A 的观察通过率高 16.7 个百分点，",
208:        "C 相比 B 的观察通过率低 8.3 个百分点，",
235:        f"- `verified_patch` 只触发 {b['controller_triggers']}/12 次，且都发生在 Pydantic；",
250:        "- C 没有超过 B，并额外消耗约 39% token；因此当前证据不支持默认开启",
```

### 为什么危险

这些数字**现在是对的**（75.0 − 58.3 = 16.7；66.7 − 75.0 = −8.3），所以不会立刻暴露。

但同一个函数里，**相邻的一行恰恰是用 `_delta_percent()` 算出来的**：

```python
# 第 207 行——算出来的
f"token 变化 {_delta_percent(summaries['B']['total_tokens'], summaries['A']['total_tokens'])}；"
# 第 206 行——写死的
"这是诊断性小样本，不支持显著性或因果结论。B 相比 A 的观察通过率高 16.7 个百分点，"
```

**同一句话里，一半是算的，一半是写死的。**

后果是具体的：如果将来给 E2 补跑了几条、或者数据被重新归组，**表格和置信区间会自动更新，但这几句叙事文字会静默保留旧数字，而且不会报错**。

> 这不只是"不够优雅"。`analyze_e2.py` 的全部价值在于 **"报告不可能是手工抄错的"**（见
> `load_rows` 的白名单、`validate_rows` 的三重漂移检测、`is not False` 的 fail-closed 检查）。
> **硬编码的叙事段落是这套保证里唯一的缺口。**

### 建议（未执行，待裁定）

三个选项，供你选：

1. **改成计算值**：把 `16.7` / `8.3` / `39%` 换成对 `summaries` 的表达式。**最小改动，但「且都发生在 Pydantic」这类定性断言仍需硬编码**。
2. **加约束注释**：保留硬编码，但在源码里加一行注释标明「此数值与 E2 冻结数据绑定，数据变更时必须同步」，并**在 `validate_rows` 里加一个断言**：如果实际通过率差 ≠ 16.7/8.3 个百分点，就报错。
3. **拆层**：`markdown_report()` 只生成表格与区间；叙事结论放回 `E2_REPORT.md` 由人写，并在文档里注明「以下为人工撰写」。

**我个人倾向选项 2**——因为选项 1 无法覆盖定性断言，选项 3 会失去「报告可复现」这个卖点，而选项 2 用一条断言就把硬编码数字变成了**受保护的不变量**。

---

## 发现三：README 第一屏的「11 次」与实际总条数相差 5 倍

### 位置

`README.md:37-39`：

```text
截至 2026-09-22，确定性测试基线为 **70 passed**。真实仓库历史账本包含 11 次混合协议
运行；项目明确保留 Multi-Agent、RAG 和无进展干预未带来稳定收益的负面结果，不把小样本
包装成能力提升。
```

### 实际盘点

统计 `benchmark/real/` 下所有 `.jsonl`：

| 行数 | 文件 | 性质 |
|---:|---|---|
| 11 | `results.jsonl` | **历史账本**（Phase 4.5 旧 11 行） |
| 4 | `experiments/e1_no_progress_2026-09-21.jsonl` | E1 运行 |
| 3 | `experiments/e2_completion_review_2026-09-21.jsonl` | E2 v3 pilot |
| 36 | `experiments/e2_v6_completion_review_2026-09-22.jsonl` | **E2 正式** |
| — | —— 以上合计 | **54 条真实运行** |
| 12 | `selected.jsonl` | case 定义（非运行） |
| 7 | `snapshot_audit.jsonl` | case 审计（非运行） |
| 6 | `test_audit.jsonl` | case 审计（非运行） |

### 影响评估：**中等，但性质特殊**

**这不算事实错误** —— 「真实仓库历史账本」在这个仓库里确实特指 `results.jsonl`，而
`README.md:183` 也解释了「旧 11 行通过行哈希做只读 provenance 映射」。

**但它是第一屏唯一出现的"真实运行次数"。** 读者记住的是 **11**，而项目实际做过 **54 次**真实运行、花了 **¥34.3315**
（¥5.7008 历史账本 + ¥3.1633 E1 + ¥2.0605 E2 pilot + ¥23.4069 E2 正式 —— 四份账本逐行求和，可复现）。

> **问题不是夸大，而是低估。** 一个刻意保留大量负面结果的项目，第一屏却把最大的工作量藏起来了——这与首屏其余部分的诚实基调不一致。

### 建议（未执行，待裁定）

在第一屏把两个数字分开写，例如：

> 真实仓库评测累计 **54 次运行**（历史混合协议账本 11 条 + E1 4 条 + E2 39 条），
> 其中 E2 的 36 条来自同一 v6 冻结协议。

**具体措辞请你定**——我只是指出当前这处会让读者低估规模。

---

## 发现四（次要）：`exit_reason` 的词表跨版本漂移

### 位置

`benchmark/real/experiments/e1_no_progress_2026-09-21.jsonl`

### 实际

E1 的 4 行 `exit_reason` 全部是 **`max_steps`**：

```
pydantic__pydantic-8977  proto=single-v2-np-off   tests_pass=True   completed=False  exit=max_steps
pydantic__pydantic-8977  proto=single-v2-np9      tests_pass=True   completed=False  exit=max_steps
iterative__dvc-4124      proto=single-v2-np9      tests_pass=False  completed=True   exit=finish
iterative__dvc-4185      proto=single-v2-np9      tests_pass=True   completed=True   exit=finish
```

**而当前代码已经产生不出 `max_steps` 这个值了。** `core/agent.py:410` 现在写的是：

```python
set_exit("max_steps_unverified_patch" if successful_mutation else "max_steps_no_patch")
```

### 影响评估：**低，但值得记录**

- 这是 `exit_reason` 的**词表在 E1 之后发生过拆分**。E2 的 `analyze_e2.py` 靠
  `PROTOCOL_ARMS` 白名单严格隔离了协议，所以不会读到 E1 的行 ✅
- 但 **E1 的 4 行没有任何等价的校验**——`analyze_e2.py` 不管它，`analyze_real_results.py`
  管的是 `results.jsonl` 的 11 行。**E1 目前处于两个分析器之间的空当。**
- 顺带说明一个**不是 bug** 的行为：那两行 pydantic 是
  `tests_pass=True` 且 `agent_completed=False`。这符合 `real_run.py:515-517` 的定义——
  `agent_completed` 测的是「是否经由 `finish` / `verified_patch` / `review_passed` 正常收口」，
  **不是「补丁对不对」**。跑满步数但补丁已经正确，就会是这个组合。

### 建议（未待裁定，仅记录）

如果将来有人要用 E1 的数据，需要先知道 `max_steps` 在当时的语义（是「有改动」还是「无改动」），
**否则无法与 E2 的 `max_steps_unverified_patch` / `max_steps_no_patch` 对齐**。
是否要把 E1 纳入某个分析器的校验范围，请你判断。

---

## 发现五（后补）：协议里的检索配置记录不完整

> **背景**：这一条最初只是我在模块 4 走读时的疑问，**当时没有验证**。用户要求先核对再写，
> 下面是对照 `core/search.py` 逐项比对后的结果。

### 位置

`benchmark/protocol.py:79-88`，`build_agent_protocol()` 的 `retrieval` 段。

### 比对结果：**数值本身全部正确，但描述不完整**

| 协议字段 | 协议值 | `core/search.py` 实际 | 一致 |
|---|---|---|---|
| `chunk_max_lines` | 120 | `_python_chunks(max_lines=120)`（L91） | ⚠️ **仅对 `.py` 成立** |
| `initial_evidence_file_limit` | 3 | `real_run.py:143` `file_limit=3` | ✅ |
| `default_top_k` | 5 | `search(top_k=5)`（L312）+ `tools.py:424` `args.get("top_k", 5)` | ✅ |
| `candidate_k` | 20 | `search(candidate_k=20)`（L312） | ✅ |
| `hybrid_weights` | 0.65 / 0.35 | `search.py:321-322` `0.65 * lexical + 0.35 * semantic` | ✅ |
| `final_weights` | 0.45 / 0.55 | `search.py:328` `0.45 * hybrid + 0.55 * normalized_rerank` | ✅ |
| `dense_backend` | `hashing` | `HashingDenseEncoder()` 为默认（L308） | ✅ |
| `reranker_backend` | `lexical` | `LexicalReranker()` 为默认（L309） | ✅ |

**权重和默认值逐项对上，`protocol.py` 当前的硬编码值是准的。** 这一点可以放心。

**但有两处不完整：**

#### ① `chunk_max_lines: 120` 只对 Python 文件成立

```python
# core/search.py:180-181
chunks.extend(_python_chunks(relative_text, text) if path.suffix.lower() == ".py"
              else _document_chunks(relative_text, text))
```

| 文件类型 | 实际上限 | 协议声称 |
|---|---:|---:|
| `.py` | **120**（`_python_chunks` 默认，L91） | 120 ✅ |
| `.md/.rst/.txt/.toml/.yaml/.yml/.json` | **80**（`_document_chunks` 默认，L139） | 120 ❌ |
| 语法错误的 `.py` | **80**（`_line_windows(window=80)`，L74） | 120 ❌ |

#### ② **初始检索的参数完全没有被记录**（这条更实际）

协议记录的是**工具调用的默认值**，但**给 Agent 的初始证据用的是另一套参数**：

```python
# benchmark/real_run.py:146
for hit in search.search(issue, top_k=10, candidate_k=50):
```

全仓库的检索调用点盘点：

| 调用点 | `top_k` | `candidate_k` | 协议是否记录 |
|---|---:|---:|---|
| `core/tools.py:424`（`search_code` 工具） | 模型传入，默认 **5** | 默认 **20** | ✅ |
| **`benchmark/real_run.py:146`（初始检索）** | **10** | **50** | ❌ **未记录** |
| `benchmark/real_snapshot.py:233`（离线审计） | 10 | 100 | ❌ 未记录（不参与 Agent 运行） |
| `benchmark/harder_run.py:38`（Phase 4 消融） | 10 | 30 | ❌ 未记录（不同实验） |

还有两个钳制值也没记录（`core/search.py:315-316`）：

```python
top_k = max(1, min(int(top_k), 10))
candidate_k = max(top_k, min(int(candidate_k), 100))
```

### 影响评估：**对 E2 有效性无影响；对"照协议复现"有影响**

- ✅ **对 E2 的 A/B/C 对比无影响**：三组都走 `run_repair` → `evaluate_one` → `initial_evidence`，
  **初始检索参数完全相同**，不构成混淆变量。
- ❌ **对复现有影响**：如果有人只读 `protocol.py` 去复现，会以为初始检索是
  `top_k=5, candidate_k=20`，**实际是 `10/50`，会拿到不同的初始证据**。

> 这触及 `protocol.py` 自己声明的目标——其 docstring 说该哈希「describes behavior」。
> **行为描述不完整，但当前恰好没有造成错误结论。**

### 建议（未执行）

在 `retrieval` 段补上：

```python
"initial_evidence_top_k": 10,
"initial_evidence_candidate_k": 50,
"top_k_clamp": [1, 10],
"candidate_k_clamp": [5, 100],
```

并把 `chunk_max_lines` 拆成：

```python
"chunk_max_lines": {"python": 120, "document": 80, "fallback": 80},
```

### ⚠️ 一个必须先决定的副作用

**改这些字段会改变 `protocol_hash`。**

`build_agent_protocol` 的返回值**整个**参与 `canonical_hash`（`protocol.py:106`），所以任何字段增删都会产生新的哈希。

**这意味着**：改了之后，将来的运行会落在新的协议哈希下，**与 E2 的 36 行（三个固定哈希）不再同组**。

请在动手前决定：

| 选项 | 后果 |
|---|---|
| **A. 接受新哈希** | 未来的运行与 E2 不可直接合并；但协议描述完整 |
| **B. 不改哈希，只在文档里说明差异** | E2 的 36 行仍可与后续比较；但 `protocol.py` 继续记录不完整的行为 |
| **C. 改成"哈希只覆盖影响行为的字段，注释字段不进哈希"** | 最干净，但要改 `canonical_hash` 的输入构造，风险最大 |

**我倾向 B（短期）+ C（长期）**：先补文档不动哈希，等下一次真正要开新协议版本（如 v7）时再把字段补齐。

**这个取舍需要你判断**——因为它牵涉"E2 结果还能不能和未来数据比"。

---

## 5. 汇总

| # | 发现 | 性质 | 影响 | 建议动作 |
|---|---|---|---|---|
| 1 | `run_one` 不存在 | **事实错误** | 低 | 改为 `evaluate_one` |
| 2 | `analyze_e2.py` 叙事数字硬编码（5 处） | **真实风险** | 中 | 见发现二的三个选项 |
| 3 | README 首屏「11 次」vs 实际 54 次 | **表述问题**（非错误） | 中 | 首屏拆分两个数字 |
| 4 | `exit_reason` 词表漂移 / E1 不在任何校验范围内 | 观察 | 低 | 记录即可 |
| 5 | 协议里检索配置记录不完整（初始检索 `10/50` 未记录；文档 chunk 上限 80 记成 120） | 观察 | 低–中 | **注意会改 `protocol_hash`**，见发现五 |

**五条我都没有动。** 请你逐条裁定：**接受 / 不接受 / 需要我先补证据**。

> 其中 **发现二** 和 **发现五** 是仅有的两条建议"动手"的，且**两者都有副作用**：
> 发现二会影响报告生成，发现五会改变 `protocol_hash`。**建议先裁定取舍，再说改不改。**

---

## 6. 给你的问题

1. **发现一**：`run_one` 是笔误，还是你指的是另一个我没找到的入口？
2. **发现二**：你倾向哪个选项？如果选选项 2（加断言），断言应该写在
   `validate_rows` 里，还是单独一个 `assert_report_invariants()`？
3. **发现三**：首屏数字你希望怎么表述？我不确定「54」是不是你想强调的量级——
   因为它混合了四种协议。
4. **发现四**：E1 是否需要一个专属的分析器，还是应该并入 `analyze_real_results.py`？
5. **发现五的取舍**：补全 `retrieval` 字段会让 `protocol_hash` 改变，从而让未来的运行
   与 E2 的 36 行不同组。**你倾向 A（接受新哈希）/ B（不动哈希、补文档）/ C（改哈希输入构造）？**
   我倾向 **B + C 组合**（短期 B，下次开 v7 时做 C），但这是我基于「E2 数据还能不能继续对比」
   这一个维度的判断——**你更了解这个项目的数据规划。**
6. **一个我没能力判断的问题**：发现二和发现五都指向同一件事——**协议/报告里存在
   "不受任何检查保护的常量"**。要不要做一个统一的机制（比如把这类常量集中到一个
   `frozen_facts` 模块并配断言），而不是逐个打补丁？**这超出我这次走读的范围，仅供参考。**

---

## 7. 附：本次走读中**验证通过**的部分（供你放心）

以下是我逐条核对过、**与文档一致**的：

- `pytest --collect-only` = **70**（8+10+5+16+20+4+7），与 `README.md:37` 和交接文档 §0 一致 ✅
- Wilson 区间 `32.0%–80.7% / 46.8%–91.1% / 39.1%–86.2%`，**本地独立复算逐字相符** ✅
- `E2_REPORT.md` 的 `evaluator 协议：19b8930f…55a513`，与 `protocols/` 下 10 个文件名后半段**全部一致** ✅
- `protocol.py` 的 `build_agent_protocol` 覆盖字段，与 Codex 审查意见（`protocol_hash` 与
  `code_revision` 分开）**完全对应** ✅
- 三次 v6 修复（`38ab0d3` / `dd4fa06` / `189bd42`）在代码中的落地位置，**逐条属实** ✅
- `docs/GITHUB_DRAFT_PR.md` 描述的两次审批绑定内容，与 `workflow.py:228-242` **一字不差** ✅
- `prepare_draft_pr` 内**只有本地 `git diff`，无任何网络调用** ✅（对应文档「准备请求不会访问远端」）
- `analyze_e2.py` 的 `is not False`（fail-closed）确认存在 ✅
- `tests/test_phase46.py` 20 项 = 18 个函数（2 个 parametrize 各展开 2 次）✅
- 三条 `verified_patch` 真实 trace 已抽取，`completion_evidence` 结构完整 ✅
- 四份真实账本逐行求和：`11 + 4 + 3 + 36 = 54` 条、`¥5.7008 + ¥3.1633 + ¥2.0605 + ¥23.4069 = ¥34.3315` ✅

> **一处自纠**：本文初稿在发现三里把总花费写成「¥28.6 以上」，且示例措辞写成「E2 33 条」。
> 两者均为口算错误，已按上表逐行求和更正为 **¥34.3315** 与 **E2 39 条**。留此记录以便你核对。
