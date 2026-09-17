# Claude Code 交接文档：Issue-to-PR Agent Phase 4–5

> 更新时间：2026-09-17
> 用途：在 VS Code 中逐文件讲解检索消融与来源约束记忆。

## 1. 一句话结论

Phase 4 已把代码检索接入真实 Agent，并用跨文件 hidden-test suite 做了两轮消融；结果没有
证明检索提高成功率或节省 token，但证明“系统单次预检索”明显优于让 Agent 自由反复搜索。
Phase 5 已实现只接受可验证来源、能随来源失效的任务记忆，不保存聊天摘要或模型自由推断。

## 2. 推荐阅读顺序

1. `core/search.py`：从 `CodeChunk`、`chunk_repository`、`BM25Index` 开始，再看
   `HybridCodeSearch.search` 和 `build_code_search`。
2. `core/tools.py`：搜索 `search_code`，理解工具 schema 与 worktree 绑定执行器。
3. `issue_to_pr.py`：看 Planner、Coder、Reviewer 如何共享检索能力。
4. `benchmark/harder_catalog.py` 与 `harder_dataset.py`：理解为何契约放在多个文件，以及
   hidden tests 为什么永远不进入 Agent workspace。
5. `benchmark/harder_run.py`：重点解释 browse 与 hybrid 只差一次受控预检索。
6. `core/memory.py`：重点看四类记忆、逐字证据、source SHA 与 stale 判断。
7. `core/workflow.py`：审批与验证失败如何自动产生可信记忆。
8. `tests/test_phase4.py`、`tests/test_phase5.py`：用测试理解安全边界。

## 3. Phase 4 的检索管线

```text
repository
  → Python AST / document heading chunks
  → BM25 lexical recall
  + dense-vector recall
  → candidate fusion
  → reranker
  → path:line + symbol + content evidence
```

默认 dense backend 是确定性 hashing vector，默认 reranker 是词项覆盖率，因此 clone 后离线
即可运行测试。设置下列环境变量后才加载学习模型：

```text
AGENT_DENSE_MODEL=BAAI/bge-small-en-v1.5
AGENT_RERANKER_MODEL=BAAI/bge-reranker-base
```

学习模型需要安装 `pip install -e ".[rag]"`。这种设计避免测试时偷偷联网下载几百 MB
权重，同时保留真实 BGE 接口。不要声称最终消融使用了 BGE 权重；正式结果使用默认离线后端。

## 4. 两轮消融为什么都保留

### Agent-driven search pilot

| 方法 | Hidden pass | Avg tokens | Avg seconds | Reads | Search calls |
|---|---:|---:|---:|---:|---:|
| browse | 6/6 | 7,345 | 42.28 | 38 | 0 |
| hybrid tool | 6/6 | 10,545 | 48.48 | 36 | 8 |

结论：Agent 会重复搜索，少量 read 节省抵不过搜索结果反复进入上下文。

### Controlled one-shot retrieval

| 方法 | Hidden pass | Avg tokens | Avg seconds | Reads |
|---|---:|---:|---:|---:|
| browse | 6/6 | 6,624 | 37.62 | 37 |
| hybrid | 6/6 | 6,978 | 35.12 | 34 |

结论：固定一次、按文件去重到四条证据后，hybrid 的额外 token 从 43.6% 降到 5.3%，
耗时下降 6.6%，read 下降 8.1%；但成功率没有变化，仍不能声称检索带来质量收益。
单次模型运行存在方差，两轮数字不能直接互相比快慢；下一步需多随机种子与更大仓库。

原始数据：

- `benchmark/harder_results.jsonl`：Agent-driven pilot
- `benchmark/harder_controlled_results.jsonl`：最终受控消融
- 同名 `.md`：自动生成 dashboard

## 5. Phase 5 的信任边界

SQLite `memories` 表只允许：

- `sourced_fact`：内容必须逐字等于 worktree 文件中的 evidence；
- `test_command`：只能从已批准计划的 pytest argv 自动提取；
- `failure`：只能引用当前任务的 test-report artifact；
- `approved_decision`：只能引用 approval 或已批准 plan artifact。

每条记录都有 `source_type/source_ref/source_sha256`。文件变更后，读取接口返回
`stale=true`，`render_context` 会跳过该记录。没有“把对话总结存进长期记忆”的接口。

## 6. REST 接口

```text
GET  /tasks/{task_id}/memories
POST /tasks/{task_id}/memories/facts
```

POST body 包含 `statement/source_path/evidence`，其中 statement 必须与 evidence 去除首尾空白后
完全一致。测试命令、失败和审批决策不允许客户端直接伪造，只由 workflow 自动写入。

## 7. 验证命令

```powershell
python -m pytest
python -m benchmark.harder_run --help
```

Docker 中也应执行同一测试集。Phase 4 两轮真实矩阵共 24 个 job，均通过 hidden tests，
没有测试篡改或容器超时。

## 8. 不要夸大的结论

- 不要说“RAG 提升了成功率”：当前是 100%→100%。
- 不要说“RAG 节省 token”：最终受控组仍增加 5.3%。
- 不要说默认使用 BGE：BGE 是可选后端，正式消融用离线默认后端。
- 不要把 provenance memory 叫“模型学会了”：它只是可审计的任务状态与证据层。
- 不要把六个 case 当统计显著结论；它们是工程消融，不是学术 benchmark。
