# W5 本地服务启动契约

在项目根目录安装依赖后，运行 `python service_http.py --open`。服务仅监听
`127.0.0.1:8765`；`--open` 尝试打开浏览器，失败或无 GUI 时只打印手动访问地址，
不停止 HTTP 服务。端口已被占用时返回非零退出码并指出 `127.0.0.1:8765`。
`--fake-publisher` 是显式的模拟发布开关，不会接通真实 GitHub PR 路径；
W4/W5 没有新增必填环境变量。

启动自检只做只读探测，不调用模型、不运行容器：

| 项目 | 检查 | 缺失时影响 |
|---|---|---|
| Python | 解释器版本 >=3.10 | 服务无法启动 |
| 项目依赖 | `openai`、`python-dotenv` 是否可导入 | 页面可打开；模型调用与配置加载使用后备实现，建议安装依赖 |
| Git | CLI 是否可见 | 无法准备任务 worktree |
| Docker | CLI 与 daemon 是否可用 | 执行和测试无法在隔离容器中运行 |
| runner 镜像 | `AGENT_SANDBOX_IMAGE` 指定标签是否存在 | 执行时容器无法启动；默认标签是 `issue-to-pr-runner:phase3` |
| 模型配置 | 环境变量名或项目 `.env` 文件是否存在 | 建任务/规划的模型调用可能失败 |

自检**不读取 `.env`，不读取或打印密钥值**。检测到 `.env` 只说明文件存在，
检测到环境变量也只说明变量名存在；两者都不证明值非空或凭据有效。
Git/Docker 检查也不代替一次真实执行。缺少运行前提时界面仍会启动，让用户先查看
已有任务；相应动作会失败，不会回退到宿主机执行不可信仓库代码。

真实使用的前提：Python >=3.10、Git、`pip install -e ".[dev]"`、已启动的
Docker Desktop、按 README 构建的 runner 镜像，以及本地模型配置。
离线测试与浏览已有任务不需要模型密钥。项目的 HTTP 层使用标准库，前端不需要
npm、构建步骤或 CDN。服务无身份认证，只供本机单用户使用。

## 测试环境说明

W5 首次报告的 `109 passed, 0 skipped` 使用了
`C:\Users\32744\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
（Python 3.12.14），**并额外将本工作副本的 `.local/w3-test-deps` 放进
`PYTHONPATH`**；该目录提供 pytest、FastAPI 与 httpx，不是解释器自带。
仅报解释器路径而省略这一步不可复现，属于报告遗漏。在未安装可选
FastAPI/httpx 测试依赖的环境，相关测试会跳过；应分别报告收集数、通过数和
跳过数。要在普通虚拟环境覆盖该可选路由测试，可安装项目的 `dev`、`web`
依赖及 `httpx`；运行本地标准库 UI 不需要 FastAPI/httpx。
