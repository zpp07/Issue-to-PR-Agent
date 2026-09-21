# Claude Code 项目交接与讲解指南

> 更新时间：2026-09-17  
> 目标读者：VS Code 中的 Claude Code 及项目作者  
> 当前分支：`phase4.5-real-benchmark`  
> 当前提交：`ef90451`（本文提交后会再产生一个仅文档/交接提交）

## 0. 给 Claude 的任务

请把这个仓库当作一个需要带作者逐层理解的工程项目，而不是只做代码摘要。
推荐一边打开本文列出的文件，一边用中文讲解：

1. 先说明项目试图解决什么问题，以及为什么它已经不再是 toy demo；
2. 按 Phase 1 → 5 → 4.5 的顺序解释能力如何逐步建立；
3. 每一阶段都采用“问题 → 设计 → 关键代码 → 测试/数据 → 诚实边界”的结构；
4. 重点讲真实仓库 benchmark 如何改变了我们对 Agent 的判断；
5. 最后带作者设计下一步 Reviewer/Revision，而不是直接替他继续跑付费评测。

不要执行新的 DeepSeek/API 评测，除非作者再次明确批准预算。不要读取或展示 `.env`
中的 API Key。不要把上游参考补丁或隐藏测试提供给 Agent。

## 1. 一句话定位

这是一个面向 Python 服务和数据处理仓库的本地 **Issue-to-PR Agent**：

```text
Issue
  → 代码检索与证据定位
  → 结构化计划
  → 人工批准
  → 受限工具执行 / Docker 隔离运行
  → 独立测试与 Reviewer
  → diff 哈希审批
  → PR-ready 产物与审计记录
```

它的作品集价值不在于“又调用了一次大模型”，而在于回答以下工程问题：

- Agent 说自己修好了，怎样由独立 verifier 证明？
- 如何区分模型失败、API 故障、环境故障和测试收集失败？
- 如何防止改测试、读参考答案、逃逸工作区、泄露宿主密钥？
- 计划、命令、diff 和远程写操作应在什么位置需要人工批准？
- Reviewer、Planner、检索和记忆到底提升了什么，成本是多少？
- 如果实验没有提升，怎样保留负面结果并据此改进架构？

## 2. 当前状态速览

### 已完成

- Phase 1：模型调用可靠性、超时、预算、错误分类、结构化 trace；
- Phase 2：结构化 Planner、Git worktree、计划/diff 哈希审批、SQLite 审计；
- Phase 3：Docker 真隔离、22 个 hidden-test synthetic cases、三方法基线；
- Phase 4：AST/文档切块、BM25 + 向量 + rerank、跨文件受控消融；
- Phase 5：只保存有来源事实的 provenance memory；
- Phase 4.5：六个真实仓库任务的精确快照、可复现实验环境和真实 Agent runner；
- 真实任务的 `single` 与 `plan_execute` 策略、分阶段 trace、成本和失败证据。

### 当前验证状态

- 本地单元/回归测试：`41 passed`；
- Git 工作树：交接前为 clean；
- 六个真实任务均完成“修复前失败、参考修复后通过”的独立验证；
- 四个本地镜像：Hydra、DVC、Pydantic、Dask；
- 真实评测累计记录 11 行，详见 `benchmark/real/results.md`；
- 当前不应继续盲目增加 trial，瓶颈已经定位为候选补丁的 review/revision。

### Git 状态

- GitHub 主仓库：`https://github.com/zpp07/Issue-to-PR-Agent.git`；
- `origin/master` 仍停在较早的公开基线；
- 当前工作位于 `phase4.5-real-benchmark`，尚未合并或推送这批真实评测提交；
- 不要直接强推。最终公开前应先审查提交历史、secret scan、再决定 merge/PR。

## 3. 建议的代码讲解顺序

### 第一站：Agent 循环与工具边界

依次打开：

1. `core/agent.py`
2. `core/client.py`
3. `core/tools.py`
4. `core/policy.py`
5. `core/sandbox.py`

讲解重点：

- `run_agent` 如何累计 token、步骤和成本，并把 provider/request id 写入 trace；
- `LLMCallError.retryable` 如何区分临时故障与鉴权/参数错误；
- 文件 API 如何解析真实路径并阻止工作区外读写；
- `read_file(start_line, end_line)` 和 `replace_text` 为什么是被真实 Dask 任务逼出来的；
- `CommandPolicy` 只是 host 上的受限策略，不是安全沙箱；
- 真正的安全边界是 `DockerSandbox`：无网络、非 root、资源限制、只读验证挂载；
- hidden verifier 与 Agent 命令运行使用不同挂载权限。

必须主动指出的边界：

- 当前工具仍是原型级，不等价于完整 IDE/patch API；
- `run_command` 的 Agent 反馈主要是文本，命令退出码没有作为独立结构字段回传；
- PowerShell 中标准库 fallback 的中文日志可能乱码，不影响 JSONL 结果，但应收尾修复；
- SDK 有请求级重试；真实 evaluator 对可重试的整任务故障额外重跑一次，并且不记作模型失败。

### 第二站：审批、审计和来源记忆

依次打开：

1. `core/workflow.py`
2. `core/audit.py`
3. `core/memory.py`
4. `issue_to_pr.py`
5. `service_api.py` 或 `service_http.py`

讲解重点：

- 每个任务为何使用独立 Git worktree；
- 计划审批绑定 plan SHA，diff 审批绑定 review/test/diff SHA，避免 TOCTOU；
- append-only event 与 checkpoint 是两种不同概念；
- SQLite 中 task、artifact、approval、event、memory 的职责；
- 记忆只允许四类：逐字文件事实、批准计划中的测试命令、测试失败、批准决策；
- 文件哈希变化后 sourced fact 自动 stale；
- FastAPI 与标准库 HTTP 服务为何同时保留。

### 第三站：检索系统

打开：

1. `core/search.py`
2. `benchmark/harder_run.py`
3. `benchmark/harder_controlled_results.md`

讲解重点：

- Python AST 和文档标题如何切块；
- BM25、hashing dense vector、lexical reranker 的默认离线组合；
- Sentence Transformers/BGE 是可选后端，不是运行项目的硬依赖；
- 为什么自由搜索 pilot 比受控单次检索更贵；
- 当前小仓库实验没有证明检索提高成功率，只有较少读文件/较短耗时的弱信号。

### 第四站：真实仓库评测（当前最重要）

依次打开：

1. `benchmark/real/README.md`
2. `benchmark/real/verified_manifest.json`
3. `benchmark/real_snapshot.py`
4. `benchmark/real_verify.py`
5. `benchmark/real_run.py`
6. `benchmark/real/results.md`
7. `benchmark/real/results.jsonl`

讲解重点：

- 数据来自 SWE-Gym Lite，但 importer 主动丢弃可能泄漏答案的 `hints_text`；
- `selected.jsonl` 含 evaluator-only 参考信息，绝不能挂载给 Agent；
- 快照固定到精确 base commit；patch apply 只是结构验证，不等于测试验证；
- 四级状态：metadata → snapshot → patch → tests verified；
- hidden test patch 只在 Agent 结束后注入；
- Agent 工作时只看到 issue、原始快照、检索证据和允许工具；
- 最终验证在只读、无网络容器中运行；
- `verified_manifest.json` 固定六个 scored cases，MONAI 只是 reserve。

## 4. Phase 1–5 的结果应该怎样介绍

### Phase 1：先让系统失败得清楚

起点问题包括：隐式超时/重试、异常直接崩溃、无预算、工具输出无限、日志不可关联。
现在有：显式 timeout/retry、错误分类、request id、step/token/cost budget、输出截断。

不要宣传“永不失败”；正确表述是：基础设施失败可识别、可审计，不会混入模型得分。

### Phase 2：审批的是确定对象，不是口头同意

计划、命令、diff 都有具体 hash；审批记录包含 actor、decision、reason、timestamp。
这使项目可以回答“批准后内容变了怎么办”这一类真实工程问题。

### Phase 3：toy benchmark 的主要价值是校准

22 × 3 的 DeepSeek 单次矩阵：

| 方法 | Hidden pass | 平均 token | 平均成本 |
|---|---:|---:|---:|
| single | 22/22 | 3,586 | ¥0.0095 |
| reviewer | 21/22 | 9,725 | ¥0.0264 |
| planner | 21/22 | 11,271 | ¥0.0321 |

正确结论：函数级用例太简单，single 已饱和；多 Agent 更贵且没有提升，不能用来证明
Reviewer/Planner 有效。hidden verifier 则成功暴露了“Agent 自评通过、实际失败”。

### Phase 4：负面消融也是结果

6 个跨文件任务中 browse 和 hybrid 都是 6/6。受控 hybrid 少读约 8.1% 文件、耗时低
约 6.6%，但 token 高约 5.3%；自由检索更差。正确结论不是“RAG 提升 X%”，而是：
在小仓库中尚未证明成功率收益，检索调用必须由系统控制。

### Phase 5：长期记忆不是保存聊天总结

项目选择 provenance-first：只有可引用、可重新验证的事实才进入长期上下文。模型推断不能
直接持久化。这比“把所有对话塞进向量库”更容易审计，也更适合作品集答辩。

## 5. Phase 4.5：真实任务是怎样改变项目的

### 5.1 六个可复现任务

| 任务 | 修复前 | 参考修复后 |
|---|---:|---:|
| Hydra-1791 | 4 failed | 52 passed |
| Hydra-1006 | 6 failed | 35 passed |
| DVC-4124 | 6 failed | 11 passed |
| DVC-4185 | 7 failed | 12 passed |
| Pydantic-8977 | 4 failed | 288 passed |
| Dask-7894 | 5 failed | 26 passed |

这一步只证明任务与环境可复现，不代表 Agent 能解决。

### 5.2 Dask smoke 带来的工具改造

第一次高预算 smoke：检索找到了正确文件，但旧 `read_file` 只让模型看到文件开头，
`write_file` 又要求覆盖整个文件。结果是 27 次搜索、461,167 token、零修改、失败。

加入按行读取和 `replace_text` 后，同一任务：

- 正确修改 `dask/array/overlap.py`；
- 未篡改测试；
- evaluator 26 passed；
- 325,328 token，约 ¥0.6915。

这是目前最值得面试时讲的案例之一：Agent benchmark 失败可能是工具接口失败，不应直接归因
于模型；修工具后必须用同一真实任务复测。

### 5.3 当前正式结果

不要把 11 行混合策略记录直接称为模型“成功率”。应分组解释：

- `single` 首轮六任务：3/6，通过两个 DVC 和 Dask；
- 对三个失败任务追加 single trial 2：0/3；
- Hydra-1791 `plan_execute` trial 1：Planner 未提交计划，Executor 未启动；
- 修复终止协议后的 trial 2：Planner 完成、Executor 修改正确文件，但补丁破坏现有测试，失败。

累计原始账本是 3/11、2,743,288 tokens、估算 ¥5.7008、39.3 分钟，但这只是不同策略的
运行记录总计，不是同分布下的置信区间。

### 5.4 已确认的失败层级

真实任务把瓶颈逐层推进：

1. 检索能否找到文件；
2. 工具能否看到正确区间并做局部修改；
3. Agent 能否从调查切换到编辑；
4. Planner 能否按时提交计划；
5. Executor 的候选补丁是否正确；
6. 发现回归后能否利用测试证据完成 revision。

当前项目已经走到第 6 层。最新 Hydra-1791 中，Planner 成功，Executor 命中参考文件且主动
发现现有测试回归，但没有在预算内修正。所以下一步不是继续加搜索或盲目加 token。

## 6. 下一阶段：Reviewer + Revision（最高优先级）

推荐新增 `plan_execute_review` 策略，严格保持 evaluator 隔离：

```text
Planner（只读 search/read）
  → plan_finish
Executor（无 search，read/edit/public tests）
  → candidate diff
Reviewer（只读 read/run tests，看到 plan + candidate diff + public test evidence）
  → review_finish {passed, issues}
Reviser（无 search，看到 review issues，最多一次局部修订）
  → focused tests + finish
独立 evaluator 最后才注入 regression patch 并评分
```

实施要求：

1. Reviewer 不得看到 gold patch、test patch、FAIL_TO_PASS 的新增测试内容；
2. Reviewer 可以看到 issue、Planner 计划、Agent diff、允许的原仓库测试输出；
3. `review_finish` 必须结构化，不能解析自然语言中的“通过/不通过”；
4. Reviser 不再开放搜索，只能依据 review issues 做一次修订；
5. 修订轮数先固定为 1，防止成本失控；
6. 保存 candidate diff、review report、revision diff、测试命令和退出状态；
7. API/环境失败不记模型失败；append-only JSONL 保留所有真实运行；
8. 先只复测 Hydra-1791，再复测 Hydra-1006/Pydantic，最后才考虑全矩阵。

在实现 Reviewer 前还应补一个小工程项：让 Docker command runner 返回结构化
`exit_code/output/timed_out`，而不只是文本。这样 Reviewer 能明确区分断言失败、命令被策略拒绝、
测试超时和测试收集错误。

## 7. 后续路线图（按优先级）

### P0：不花 API 额度即可完成

- 实现上述 Reviewer + 单轮 Reviser；
- 命令结果结构化，并在 trace 中保存安全摘要；
- 为三阶段策略写 fake-client 单元测试；
- 报告按 strategy/repo 聚合，不混算不同协议；
- 为 Agent patch、plan、review、revision 建立 artifact hash；
- 修复 Windows 控制台中文日志编码；
- 将旧的 `WIP_RESUME.md` 内容合并进正式状态文档，避免两个事实源漂移。

### P1：需要作者再次批准费用

- Hydra-1791 `plan_execute_review` 单次 smoke；
- 若成功，再跑 Hydra-1006 与 Pydantic；
- 冻结策略后对六任务做相同次数 trial；
- 报告 Wilson interval/置信区间，但明确小样本限制；
- 不要在调参过程中反复把同一任务结果当作无偏测试集，可留至少一个 reserve task。

### P2：产品 Phase 6

- commit、push、PR 创建分别作为需要审批的远程副作用；
- GitHub token 使用最小 scope，绝不进入容器或 trace；
- PR body 自动汇总 issue、批准计划、diff、测试、风险、Reviewer 结论；
- 远程写入前再次绑定 base commit 和 diff hash；
- 所有远程动作进入 append-only audit；
- 先支持 dry-run PR body，再接真实 GitHub API。

### P3：作品集发布

- README 增加一张架构图和一张真实 benchmark 结果图；
- 写技术报告：威胁模型、失败分类、消融、成本、负面结果；
- 清理/标注 legacy 脚本，避免面试官误以为它们是主路径；
- 最终考虑 `src/issue_to_pr_agent/` 包布局；
- 轮换开发 Key、执行 secret scan、检查大文件；
- 审查当前 feature branch 后，通过 PR 合并到 `master` 并推送 GitHub。

## 8. 安全与评测不变量

继续开发时不得破坏以下约束：

- Agent 永远看不到 gold patch；
- regression test patch 只能在 Agent 完成后由 evaluator 注入；
- Agent 不能写测试、文档、依赖或 scratch 文件；
- 容器运行无网络、非 root、不继承宿主 API Key；
- hidden verification 使用只读 workspace；
- `metadata_only/patch_verified` 不能冒充 `tests_verified`；
- API/网络/环境故障不能计为模型失败；
- 参考修复通过只证明任务有效，不证明 Agent 成功；
- 结果必须 append-only，失败实验不能删除；
- 不同 prompt、工具、策略的结果必须带 strategy/protocol 标识；
- 未经作者明确批准，不运行新的付费 trial，不 push/merge，不创建远程 PR。

## 9. 不花钱的检查命令

从仓库根目录运行：

```powershell
python -m pytest -q
python -m compileall -q benchmark core tests
git status --short
git log --oneline --decorate -15
Get-Content benchmark/real/results.md
```

真实快照和本地运行目录在 `.local/real_benchmark/`，已被 Git 忽略。不要删除，除非作者明确
要求；重新下载/构建会浪费时间。Docker 镜像也是本地实验资产，不需要提交到 Git。

## 10. Claude 给作者讲解时的推荐节奏

### 第一轮：15 分钟建立全局图

- 项目定位、威胁模型、主流程；
- Phase 1–5 各解决一个什么问题；
- synthetic/harder/real 三层 benchmark 为什么逐步升级。

### 第二轮：30–45 分钟看主路径代码

- `core/agent.py` 与 `core/tools.py`；
- `core/sandbox.py`；
- `core/workflow.py`、`audit.py`、`memory.py`；
- `benchmark/real_run.py`。

每看一个文件，都让作者先回答“输入、输出、信任边界、失败模式”，再补充代码细节。

### 第三轮：20 分钟复盘真实失败

- 对比 Dask 改工具前后；
- 对比 Hydra plan_execute trial 1/2；
- 解释为什么下一步是 Reviewer/Revision，而不是更多搜索或更多 token。

### 第四轮：让作者亲自设计 Reviewer

Claude 不要直接一次性写完。先让作者回答：

1. Reviewer 应该看到哪些 artifact？
2. 哪些信息会造成 evaluator 泄漏？
3. Reviewer 通过是否等于任务通过？
4. Reviser 最多几轮，为什么？
5. 如何区分测试失败和基础设施失败？

然后再一起修改 `benchmark/real_run.py` 并补 fake-client 测试。

## 11. 面试时可以主动讲的亮点

- 不是只展示成功案例：保留了“多 Agent 更贵但没更准”“RAG 未提升成功率”等负面结果；
- 真实观察到 sandbox escape/test pollution，再建立 Docker 边界与污染检测；
- 用真实仓库失败反推按行读取、局部编辑、阶段终止和下一步 Reviewer；
- 参考答案、隐藏测试、Agent 上下文三者严格分离；
- 审批绑定 hash，记忆绑定来源，失败绑定 artifact；
- 能把“模型失败”和“评测基础设施失败”分开；
- 费用、token、耗时和测试篡改都有记录，不只报一个 pass rate。

## 12. 当前最诚实的项目结论

项目已经从几个 toy Agent demo 升级成了一个具备可靠性、审批审计、Docker 安全边界、检索、
来源记忆和多层 benchmark 的 Issue-to-PR 原型。它已经足以作为 Agent 开发实习作品集的主体，
因为关键工程问题和实验方法都是真实的。

但它还不是“自动修复任意 GitHub Issue”的产品：真实六任务首轮只有 3/6，Planner/Executor
能提高行为可控性，却尚未证明提高最终成功率。当前最值得完成的技术闭环，是让 Reviewer
消费候选 diff 和失败测试证据，指导一次受限 revision；之后再用相同协议做多 trial 对比，
最后完成 Git/PR 远程副作用审批与公开报告。

