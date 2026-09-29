# W5/W6 产品化验收记录（2026-09-29）

## 范围与安全边界

W5 加入启动自检、`--open` 和清晰的端口错误。W6 加入 HTTP 全链路离线测试、
README 首屏的界面说明及 W2–W5 文档索引，并把演示脚本改为浏览器走读。
W6 测试使用 fake client 和 fake sandbox 验证状态机，**不调用付费模型、不启动
Docker、不访问 GitHub、不创建 PR**。本轮付费 token：`0 / 20,000,000`。
没有取得真实界面截图；README 明确标注“界面截图待人工补充”。

## 测试环境与可复现命令

解释器：
`C:\Users\32744\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
（Python `3.12.14`）。这个解释器**本身没有 FastAPI、httpx 或 pytest**。
测试时额外把本工作副本的 `.local/w3-test-deps` 置于 `PYTHONPATH`；其中实际版本为
pytest `8.3.5`、FastAPI `0.141.1`、httpx `0.28.1`。

```powershell
$env:PYTHONPATH = (Resolve-Path '.local/w3-test-deps').Path
& 'C:\Users\32744\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest -o addopts= -q
```

结果：**收集 110 / 通过 110 / 跳过 0**；另有 3 条第三方弃用警告。
W5 前一轮的 **109 / 109 / 0** 也使用了相同的额外 `PYTHONPATH`，当时只报解释器
而漏报此路径，是报告遗漏。未安装可选 FastAPI/httpx 的工作副本会跳过对应路由测试；
用户报告的 `108 passed + 1 skipped` 与此相符，但不是上面这组环境的结果。
普通虚拟环境要覆盖可选路由测试，可安装 `dev`、`web` 依赖及 `httpx`；本地标准库
UI 不需要 FastAPI/httpx。

## W6 全链路测试实际断言

`tests/test_phase_productization_w6.py` 经 HTTP Handler 路由走过：

`created → awaiting_plan_approval → ready_to_execute → executing →
awaiting_diff_approval → approved_for_pr → awaiting_publish_approval → ready_to_publish`

它逐页读取单调事件游标，核对 Coder/Reviewer 步骤和执行终态；分别核对
`plan`、`diff_review`、`draft_pr_request` 的 `approval_subject_sha256`，包括
diff 的内容哈希**不等于** diff 审批对象哈希。最终没有 `draft_pr` artifact，
工作区只有预期的 `buggy.py` 修改。fake sandbox 返回的公开测试结果只是确定性
测试替身，不应解释为一次真实 Docker 或 hidden-test 验证。
