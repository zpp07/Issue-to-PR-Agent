# Issue-to-PR Agent

[![CI](https://github.com/zpp07/Issue-to-PR-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/zpp07/Issue-to-PR-Agent/actions/workflows/ci.yml)

将真实 Python 仓库的 GitHub Issue 转化为**可审查的候选补丁**。Agent 定位代码、生成计划与补丁，工作台用 Docker 验证候选版本，并保存 Diff、测试报告与人工审批记录。

[**观看真实仓库演示 · 2 分 07 秒**](https://zpcode.online/replay.html#demo) · [直接打开 MP4](https://zpcode.online/inspect-ai-demo.mp4) · [项目设计](https://zpcode.online/project.html)

[![演示画面：Inspect AI #6474 的候选补丁通过 294 项测试，等待 Diff 审批](https://zpcode.online/inspect-ai-demo.jpg)](https://zpcode.online/replay.html#demo)

## 这次演示跑出了什么

[Inspect AI #6474](https://github.com/UKGovernmentBEIS/inspect_ai/issues/6474) 提供了 JSONL 空白行导致加载失败的复现和修复线索。真实模型在基线 `d1684677d` 上生成补丁和回归测试，验证了空白行可跳过、非法 JSON 仍报错。工作台保存了 **294 项通过**的测试报告，随后完成 Diff 审批和 Draft PR 模拟发布。本次演示未向上游推送或创建 PR。

## 值得看的三个实现

| 环节 | 实现 |
| --- | --- |
| 仓库定位 | 通过 GitHub 导入问题，用 BM25、Python AST 符号及按需文件读取定位代码。见 [检索实现](core/search.py)。 |
| 候选验证 | 每个任务使用独立 Git worktree。验证前比较候选与只读快照的 Git tree，测试在无网络、非 root 的 Docker 环境运行。见 [验证实现](core/verification.py)。 |
| 可审查发布 | 计划、Diff 和发布请求分别审批。Diff 审批绑定候选版本与测试证据，SQLite 保存执行事件与产物。见 [工作流](core/workflow.py)。 |

## 本地运行

使用 Python 3.10 或更新版本、Git 和 Docker。在仓库根目录运行：

```bash
python -m pip install -e ".[dev,web]"
python -m pytest -q
docker build -t issue-to-pr-runner:phase3 -f docker/Dockerfile .
python service_http.py --open --fake-publisher
```

离线测试无需模型密钥。实际生成计划和补丁需要配置 `DEEPSEEK_API_KEY`。工作台监听 `127.0.0.1:8765`。完整部署、模型配置与试用方式见 [工作台说明](docs/WORKBENCH_DEPLOYMENT.md)。

[真实仓库评测记录](benchmark/real/experiments/E2_REPORT.md) · [Draft PR 发布流程](docs/GITHUB_DRAFT_PR.md) · [历史开发与实验记录](docs/PROJECT_HISTORY.md)
