# 发现 Issue、公开回放和私有工作台

## 简历入口与日常使用

发给招聘人员的入口为 [项目体验](https://zpcode.online/replay.html)。点击可展开修复步骤和测试证据。需要动手时，从「自己复现」下载 ZIP，按其中的 `RUN.md` 应用补丁或在自己的设备运行真实 Agent。

本人使用云端私有工作台时，先在 Windows PowerShell 检查隧道：

```powershell
curl.exe --noproxy "*" -fsS http://127.0.0.1:8765/ui-config
```

如端口未监听，用下文 SSH 隧道命令连接。打开 [私有工作台](http://127.0.0.1:8765/)。填仓库路径时使用腾讯云上的绝对路径。需要干净的练习仓库时，在 Ubuntu SSH 窗口运行：

```bash
cd /opt/issue-to-pr/current
demo="$(mktemp -d "$HOME/issue-to-pr-demo.XXXXXX")"
.venv/bin/python -c 'from pathlib import Path; import sys; from core.trial import create_preset; print(create_preset(Path(sys.argv[1])))' "$demo"
```

把输出的目录填入「本地 Issue 文本」，Issue 可填「修复 buggy.py 的加法，只修改 buggy.py，保留 test_buggy.py」。生成后先检查允许修改的文件和命令。通过后点「批准计划 → 开始执行」，在 Diff 页查看测试及修改。若要走模拟发布，依次批准 Diff、冻结 Draft PR 请求、批准发布请求并点击「模拟发布」。创建计划与执行使用服务端配置的 DeepSeek 额度。

## 本机使用

在仓库根目录运行 `python service_http.py --open --fake-publisher`，工作台监听 `127.0.0.1:8765`。选择 **GitHub Issue**，输入 `owner/repo` 或仓库 URL，点击 **查找开放 Issue**。推荐按 Issue 标签和正文线索排序，查询最近更新的最多 50 条开放记录，显示前 5 条候选，跳过已分配的 Issue 和 PR。选择后创建任务。路径留空时浅克隆公开仓库的默认分支，本机已有仓库也可以填写其根目录。

选择 **扫描测试失败** 可发现本地仓库中连续两次可复现的 pytest 失败。扫描需要现有 runner 能运行仓库的公开测试。依赖缺失、没有测试、通过或无法复现时不会生成修复任务。发现和复现不调用模型。创建计划和执行会使用当前工作台配置的模型 Key。

## 云端常驻私有工作台

在 Windows 项目目录执行 `python deploy/package_workbench.py`。将输出的 `.tar.gz` 和同名 `.sha256` 上传到 `ubuntu` 用户的家目录。服务使用独立的数据目录，版本放在 `/opt/issue-to-pr/releases`，`current` 指向当前版本。首次在 Ubuntu SSH 会话执行以下命令，替换 `bundle` 的文件名为实际上传的包名。

```bash
cd /home/ubuntu
bundle='issue-to-pr-workbench-实际版本.tar.gz'
sha256sum -c "$bundle.sha256" || exit 1
version=${bundle%.tar.gz}
release="/opt/issue-to-pr/releases/$version"
sudo install -d -o ubuntu -g ubuntu /opt/issue-to-pr/releases
mkdir "$release" || exit 1
tar -xzf "$bundle" -C "$release" || exit 1
cd "$release" || exit 1
sudo apt-get install python3-venv git || exit 1
python3 -m venv .venv || exit 1
.venv/bin/python -m pip install -e '.[web]' || exit 1
sudo docker image inspect issue-to-pr-runner:phase3 >/dev/null 2>&1 || \
  sudo docker build -t issue-to-pr-runner:phase3 -f docker/Dockerfile .
```

首次配置模型时，执行 `sudo install -m 0600 /dev/null /etc/issue-to-pr.env`，然后 `sudoedit /etc/issue-to-pr.env`，在文件中填入 `DEEPSEEK_API_KEY=你的Key`。已有该文件时直接编辑，保留原值和权限。不要把 Key 放到命令行、压缩包或公开网页。

```bash
sudo install -m 0644 "$release/deploy/issue-to-pr.service" /etc/systemd/system/issue-to-pr.service
sudo ln -sfnT "$release" /opt/issue-to-pr/current
sudo systemctl daemon-reload
sudo systemctl enable --now issue-to-pr.service
sudo systemctl restart issue-to-pr.service
systemctl is-active issue-to-pr.service
curl --fail --show-error http://127.0.0.1:8765/ui-config
```

Docker system service 应存在，运行单元通过 `SupplementaryGroups=docker` 使用本机 daemon。它适用于你自己的私有工作台。查看启动错误可运行 `sudo journalctl -u issue-to-pr.service -n 60 --no-pager`。下一次更新先记录 `readlink /opt/issue-to-pr/current`，再切换到新版。回滚时恢复旧链接并重启。共享数据目录 `/var/lib/issue-to-pr` 不随切换删除。

回到 Windows PowerShell，建立隧道，再在浏览器打开 `http://127.0.0.1:8765/`。

```powershell
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 -i "$env:USERPROFILE\.ssh\tencent-learning-ed25519" -o IdentitiesOnly=yes ubuntu@43.142.123.91
```

隧道使用同一个本地端口，工作台的 Host 检查也基于这个端口。本机已有工作台占用时，先关掉旧进程。服务默认启用模拟发布。

## 发布无需 Key 的回放

回放用 `python public-site/scripts/generate_agent_replay.py` 重新记录。它运行预设 Python 仓库的真实 Docker 测试，模型响应和 Draft PR 发布由离线替身提供。页面的六个阶段来自运行产物，只有静态 HTML 和 CSS。

在仓库根目录运行 `node public-site/scripts/package.mjs`。将新包和 `.sha256` 上传到服务器，使用以下命令解包至新目录。

```bash
cd /home/ubuntu
bundle='zpcode-site-实际版本.tar.gz'
sha256sum -c "$bundle.sha256" || exit 1
new="$HOME/sites/${bundle%.tar.gz}"
mkdir "$new" || exit 1
tar -xzf "$bundle" -C "$new" || exit 1
cd "$new" || exit 1
python3 scripts/verify_bundle.py || exit 1
python3 scripts/prepare_release.py --approved --icp '赣ICP备2026027949号-1' || exit 1
sudo docker inspect zpcode-site-web-1 \
  --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
```

上一个命令输出线上使用的 Compose 文件。若沿用原项目 `zpcode-site` 和单个 Compose 文件，把 `live` 改成该文件所在目录。切换仅重建静态 `web` 容器，并保留原 release、原 Nginx 配置与 Caddy 数据卷。

```bash
live="$HOME/sites/zpcode-20261028/deploy"
site() {
  sudo env "SITE_RELEASE_ROOT=$new" docker compose -p zpcode-site \
    --env-file "$live/public.env" -f "$live/compose.yaml" \
    -f "$new/deploy/replay.override.yaml" "$@"
}
site config --quiet || exit 1
site run --rm --no-deps web -t || exit 1
site up -d --no-deps web || exit 1
python3 "$new/scripts/smoke.py" --url https://zpcode.online
```

若需回滚，使用切换前的 Compose 文件列表执行 `up -d --no-deps web`。如果线上已有其他 override，应在其后追加新 override，并保留旧文件列表用于回滚。试用 API 和私有工作台无需加入这个静态站点的反向代理。

## 自带 Key 的独立试用

本机启动 `python -m uvicorn service_trial:app --host 127.0.0.1 --port 8766 --workers 1 --no-access-log`，打开 `http://127.0.0.1:8766/trial/`。需要安装 `.[web]` 并启动 runner。界面按 **生成计划 → 批准计划并执行 → 下载补丁** 操作。只有预设仓库，不接受访客路径、远端仓库或模型端点。模型 Key 每次由用户提交，只传给固定 DeepSeek 端点，服务无平台 Key 回退。服务的短期计划用单次随机凭证读取，十分钟过期，任务结束清理工作区。试用当前以一个工作进程运行，至多一个在执行的请求，规划与执行共享 16 次模型调用限额。试用没有真实 GitHub 写入端点。

面向访客提供实时模型调用前，先确认现有个人静态展示备案可否变更为该用途，并核对生成式 AI 服务适用要求。开放时使用独立的服务进程和数据目录，设置 `TRIAL_ORIGIN` 为准确的 HTTPS origin，代理只转发 `/trial/` 下的试用路由。代理层应限制每访客的请求频率并禁记请求体。当前的应用并发上限是资源上限，不能替代公开服务的按访客限流。公开部署应在单独的 runner 主机上验收资源配额与权限，避免让工作台和公网试用共享模型配置或宿主机 Docker 权限。

### 邀请链接

从工作台源码目录运行以下两条命令，启动本机邀请预览。第二条命令需在运行服务的终端执行。

```powershell
& .\.local\handoff-venv\Scripts\python.exe -m core.trial_invites .local/trial-invites issue --hours 48 --origin http://127.0.0.1:8766
$env:TRIAL_INVITE_DIR = (Resolve-Path .local/trial-invites).Path
& .\.local\handoff-venv\Scripts\python.exe -m uvicorn service_trial:app --host 127.0.0.1 --port 8766 --workers 1 --no-access-log
```

第一条命令输出邀请链接和撤销编号。接收者打开链接后，页面先检查邀请，再展示 Key 表单。服务端每次生成计划和执行都会核验邀请，同一个计划只能由创建它的邀请执行。可执行 `python -m core.trial_invites .local/trial-invites revoke <撤销编号>` 停用链接。刷新试用页面时请从原邀请链接重新打开。

HTTPS 模式要求设置邀请目录。确认可对外开放后，再用正式 origin 签发链接，并在只发送给指定面试官的简历版本中附上该链接。需要长期投递的简历可保留公开回放入口。
