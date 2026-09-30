# 2026-09-30 上传前核验

本报告记录此次待上传代码的离线验证，不替代历史 W6 验收，也不宣称已经完成真实 GitHub PR smoke。

## 上传范围

- 显式 `--real-publisher` 本地界面入口、最终外部写入确认；默认发布关闭，模拟模式仍独立。
- 单服务进程、同 workflow 实例内的每任务发布防重；真实发布端点的本地 Host、Origin、
  `Sec-Fetch-Site`、JSON Content-Type 校验。没有认证，不应暴露公网，也不能多进程共享发布数据库。
- Planner 终止工具预留、规划失败状态与重试、独立任务 worktree、Windows 长路径诊断。
- 空补丁不得生成有效 diff 审批对象，审批时再次核对当前补丁；未修改协议哈希算法。
- 产品默认模型切换为 `deepseek-flash`；显式模型和已有 `AGENT_MODEL` 仍优先。
  thinking tool-call 的 `reasoning_content` 只保留在模型历史，不进入浏览器事件。
- 用户指南、真实人工截图、演示与契约说明。两份早期 Claude 交接文档原样保留，
  其中“待执行”等措辞描述的是当时状态，当前行为以代码、README 和 W2–W5 契约为准。

## 验证环境与数字

- 解释器：`D:\workfiles\Markdown_PDF2\pytorch\agent项目\inspect_ai\.venv\Scripts\python.exe`
- Python：**3.12.14**。
- 此环境已有 pytest、OpenAI SDK、FastAPI、httpx、Ruff 和 Node；本轮未安装额外依赖。
- 完整测试：**155 收集 / 155 passed / 0 skipped**，46.69 秒。
- 修改的 Python 文件和新增测试的 Ruff 检查通过；`node --check web/app.js`、
  `git diff --check` 通过。未宣称整个历史仓库 Ruff 全绿。
- 发布并发、异常后明确重试、不同任务并行、跨来源/表单拒绝、前端发布期间防重复提交
  均有离线回归；所有 publisher 和模型响应均使用 fake 或 mock。

Node 行为测试是可选离线测试，没有 Node 的环境会明确 skip；可选 FastAPI 缺失时相关测试
也会明确 skip。其他机器的结果需同时报告解释器、收集数、通过数和跳过数。

## 密钥与运行隔离

完整测试使用临时 `AGENT_DATA_DIR`，避免读写已有任务数据库。在 pytest 收集前由插件将
`core.client._load_env` 替换为返回空列表的离线实现；未读取 `.env`，未打印任何密钥或 token。
模型配置相关用例仅使用 monkeypatch 注入假配置。

Git 待提交文件采用明确清单；`.env`、`.local/` 数据库和任务 worktree 不在上传范围。
新增及修改文本进行了凭据模式检查，没有发现匹配项；此检查不是完整安全审计。

## 未验证与预算

- 未调用真实 publisher：**本轮没有创建 GitHub PR**。
- 未做付费模型实验：**本轮消耗 0 token**。
- 新模型的实际修复质量提升未验证；历史 benchmark、报告和协议不重跑、不混算。
- README 的“真实 GitHub Draft PR smoke”继续保持未勾选。
