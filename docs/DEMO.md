# 3～5 分钟项目演示脚本

这份脚本用于录屏、面试现场或项目介绍。目标不是展示大量终端输出，而是在五分钟内证明：
系统能安全修改代码、独立判断结果，并诚实记录失败和成本。

## 演示前准备

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
docker build -t issue-to-pr-runner:phase3 -f docker\Dockerfile .
```

付费演示前在本地 `.env` 配置 `DEEPSEEK_API_KEY`。不要在录屏中打开或输出 `.env`。

## 0:00～0:40：问题与架构

打开 README 首屏并说明：

> 这是一个本地 Issue-to-PR Agent。它不相信模型自己声称“修好了”，而是在受限 Docker
> 环境里执行工具，再由 Agent 不可见的 hidden tests 独立评测。计划、diff 和人工审批都
> 绑定内容哈希，最终保留 trace、token、成本和失败原因。

强调系统默认不会 push 或创建 PR。

## 0:40～1:30：离线回归测试

```powershell
python -m pytest -q
```

说明这些测试覆盖：路径越界、命令策略、审批哈希、Docker 参数、隐藏测试隔离、混合检索、
来源记忆、协议哈希、symbol 读取和失败分类。这个步骤不消耗 API token。

## 1:30～2:40：运行一个隔离修复

```powershell
python -m benchmark.run --cases case01_add --methods single
```

展示生成的 `benchmark/results.md`，说明四个互相独立的信号：

1. `agent_passed`：Agent 自己是否认为完成；
2. `tests_pass`：隐藏测试是否真的通过；
3. `test_tampered` / `source_polluted`：是否作弊或逃逸；
4. `total_tokens` / `cost_rmb`：完成任务的资源成本。

不要反复运行这个命令；录屏前可先准备一条成功结果，现场主要讲解结果文件。

## 2:40～3:40：真实仓库与失败分析

```powershell
python -m benchmark.analyze_real_results --show-provenance
```

打开 `docs/FAILURE_ANALYSIS_HANDOFF.md`，说明项目没有把 11 条不同协议的历史运行混成一个
成功率，而是按“从哪一步开始跑偏”区分：正常收敛、只读不收敛、补丁语义错误、Planner
未提交计划、修改引入回归。

随后打开 `benchmark/real/experiments/E1_NO_PROGRESS_REPORT.md`，用一句话说明负面实验：

> 无进展提醒听起来合理，但四次 smoke run 没有显示收益，其中一次还推迟了首次有效修改，
> 因此没有默认启用。

再打开 `benchmark/real/experiments/E2_REPORT.md`，展示 36 条同协议正式运行：B 组观察
通过率最高且 token 最低，但自动终止真正触发的 3 条只有 1 条通过 hidden tests；加入
Reviewer/Reviser 的 C 组没有超过 B。强调这是小样本机制验证，不是统计显著结论。

## 3:40～4:30：安全与人工审批

展示 `core/policy.py` 和 `core/workflow.py`：

- Agent 只能提交 argv，不能提交 shell 字符串；
- pytest/ruff 和只读 Git 命令可以直接运行；
- commit、push 等状态变更需要审批；
- 审批绑定计划或 diff 的 SHA-256，内容变化后旧审批失效。

## 4:30～5:00：诚实边界

以三个限制结束：

- 当前真实仓库样本仍小，不能声称具有统计显著性；
- Multi-Agent 和 RAG 在已有实验中没有提高成功率；
- 项目目前到 `PR-ready`，还没有默认执行外部 GitHub 写操作。

最后给出下一步：保留确定性终止和 Reviewer/Reviser 的真实负面边界，完成受审批的
GitHub Issue → 本地计划 → 修改/测试 → Draft PR 闭环；真正创建外部 PR 仍需人工确认。
