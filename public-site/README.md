# 代码实践手记 · zpcode.online

个人编程与项目展示网站。2026-10-18 完成首版源码，2026-10-26 补充个人介绍、真实工作台静态截图、历史评测、后端实践及 inspect_ai 问题报告；不接真实 Agent API，不调用模型，不执行访客代码。使用 Sites 技能的本地、无构建依赖路线；目标部署在用户已有腾讯云服务器，不注册或发布到 Sites 托管。

## 状态与验证边界

最新状态（2026-10-31）：用户已完成腾讯云公网部署，提供 HTTPS 证书签发、正式域名 smoke 全通过日志及在线页面截图。下方首版部署记录仅供追溯；公安联网备案最近确认仍为待审核。全站首屏吸顶备案条的最新补丁已准备，尚未收到云端应用确认。

- 域名：用户截图显示 `zpcode.online` 状态正常，注册有效至 2027-10-18。不是 `zpcode.cn`。
- ICP：2026-10-28 用户提供管局审核通过截图，并给出实际编号 `赣ICP备2026027949号`；按用户提供的江西编号原样生成本地正式页面，不自行追加网站序号。公安联网备案申请待审核，暂不显示公安备案号。公网部署仍需单独完成。
- 本地：2026-10-26 重跑网站 14 项、release 6 项测试全部通过；静态截图也纳入发布白名单与二进制一致性检查。Compose `config --quiet` 是首版历史验证，本轮未实际启动 Nginx/Caddy。
- 浏览器：已检查首页、项目页与案例页导航；390px 下首页、案例页无页面级横向溢出。没有代替所有设备的完整可访问性审计。
- 云端：助手 SSH 非交互认证返回 `Permission denied (publickey)`，没有上传文件、启动容器、开放端口或更改 SSH 配置。Nginx/Caddy 容器与公网 TLS 仍需实际部署验证。
- 身份照片、详细住址、验证码、人脸核验、法定承诺只在官方入口本人操作；不放入这个目录或压缩包。

## 文件结构

```text
dist/                 唯一允许对外提供的静态页面、样式与截图（8个文件）
deploy/               Nginx、Compose、私有/公开环境文件、Caddy配置
scripts/preview.mjs   本地回环预览，不是生产服务器
scripts/smoke.py      HTTP/TLS与路由边界验收，不访问模型
scripts/prepare_release.py  备案获批后生成独立正式页面
scripts/verify_bundle.py    校验部署包文件与SHA-256
scripts/package.mjs        从明确白名单生成部署包
ICP_GUIDE.md          备案填写与提交说明（早期安徽假设保留为历史，实际为江西）
tests/               不调用模型/GitHub的测试
artifacts/           本地产物（不入Git、不自动公开）
release/             仅在实际获批后生成，不覆盖dist
```

只有 4 个 HTML、CSS、favicon、robots、工作台静态 PNG 和健康探测可访问。没有目录列表、API代理、数据库或Docker socket挂载。Nginx返回未知路径404，写方法405。CSP禁止脚本、表单及页面发起API连接；此配置不是完整安全认证。

## 本地运行

在 `public-site` 目录中：

```powershell
node --test tests/site.test.mjs
python -m unittest discover -s tests -p test_release.py -v
node scripts/preview.mjs 18082
```

浏览器访问 `http://127.0.0.1:18082/`，停止用 Ctrl+C。Python需3.10及以上；可复用学习实验的受支持解释器，不使用PATH中的旧Python。无 npm 安装、外部字体、分析脚本或付费依赖。

## 一、上传私有部署包（ICP备案前也可以做）

本机测试完成后运行 `node scripts/package.mjs`，它会输出实际 `.tar.gz` 路径；重复运行产生新包，不覆盖旧包。包中不包含整个项目、用户 `.env`、凭据、数据库、旧工作区或测试临时目录；仅包含明确白名单内、已公开的工作台静态截图。最终归档清单和校验结果另见 `VERIFICATION.md`。

Windows PowerShell上传（将文件名替换为打包命令输出的实际名称）：

```powershell
scp -i "$env:USERPROFILE\.ssh\tencent-learning-ed25519" -o IdentitiesOnly=yes "实际压缩包的完整路径.tar.gz" ubuntu@43.142.123.91:/home/ubuntu/
scp -i "$env:USERPROFILE\.ssh\tencent-learning-ed25519" -o IdentitiesOnly=yes "实际压缩包的完整路径.tar.gz.sha256" ubuntu@43.142.123.91:/home/ubuntu/
```

如果你的密钥需口令，在你自己的终端中输入，不发到聊天；首次主机指纹仍需核验，不使用跳过校验参数。

服务器中，新建一次性目录，避免覆盖任何已有部署（路径已固定在新网站目录下；如果目录已存在，先停止并选另一个新目录）：

```sh
(
set -eu
cd /home/ubuntu
sha256sum -c 实际压缩包文件名.tar.gz.sha256
test ! -e /home/ubuntu/sites/zpcode-20261018
mkdir -p /home/ubuntu/sites/zpcode-20261018
cd /home/ubuntu/sites/zpcode-20261018
# 改成实际上传的文件名，先查看包只含dist/deploy/scripts与说明文件
tar -tzf /home/ubuntu/实际压缩包文件名.tar.gz
tar -xzf /home/ubuntu/实际压缩包文件名.tar.gz
python3 scripts/verify_bundle.py
)
cd /home/ubuntu/sites/zpcode-20261018
```

若第一个 `test` 或校验命令失败，**不要继续往下复制执行**。不对现有部署目录直接解压，不使用 `down -v`。

启动私有静态容器：

```sh
sudo docker compose --env-file deploy/preview.env -f deploy/compose.yaml -p zpcode-site config --quiet
sudo docker compose --env-file deploy/preview.env -f deploy/compose.yaml -p zpcode-site pull web
sudo docker compose --env-file deploy/preview.env -f deploy/compose.yaml -p zpcode-site run --rm --no-deps web -t
sudo docker compose --env-file deploy/preview.env -f deploy/compose.yaml -p zpcode-site up -d web
sudo docker compose --env-file deploy/preview.env -f deploy/compose.yaml -p zpcode-site ps
python3 scripts/smoke.py --url http://127.0.0.1:18080
```

`run ... web -t` 验证真实Nginx配置；等待 `healthy` 后跑smoke。已有腾讯镜像加速配置可以继续使用；拉取超时不要换不明第三方镜像或修改SSH。镜像标签会更新，首次拉取成功后用 `sudo docker image inspect nginx:stable-alpine --format '{{json .RepoDigests}}'` 记录实际digest，后续升级应固定并核验版本。

若启动失败：

```sh
sudo docker compose --env-file deploy/preview.env -f deploy/compose.yaml -p zpcode-site logs --tail=80 web
```

Windows 新终端建立隧道（必须保持运行；新端口18080不要加云防火墙规则）：

```powershell
ssh -i "$env:USERPROFILE\.ssh\tencent-learning-ed25519" -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -N -L 127.0.0.1:18080:127.0.0.1:18080 ubuntu@43.142.123.91
```

然后打开 `http://127.0.0.1:18080/`。看到的是云服务器上的私有网站，不是公开网站。按 [ICP_GUIDE.md](ICP_GUIDE.md) 完成官方备案；不必先让访客能访问。

## 二、仅在实际ICP获批后执行

这部分仅在管局实际通过后执行。2026-10-28 用户已提供通过截图与编号；仍须核对域名为本站，不把域名实名通过或腾讯初审当作管局通过。

### 2.1 生成正式页面

在云端网站目录运行，替换成通知原文中的真实编号：

```sh
python3 scripts/prepare_release.py --approved --icp '赣ICP备2026027949号'
```

脚本只生成 `release/`、替换4页备案链接、移除预览提示和noindex；它不会核验管局状态，不会改DNS或开放服务。默认拒绝覆盖已有release；无真实编号会失败，不要拿测试中的编号来凑。

先在回环地址验证正式内容（只重建本站web，不影响学习服务）：

```sh
sudo docker compose --env-file deploy/public.env -f deploy/compose.yaml -p zpcode-site config --quiet
sudo docker compose --env-file deploy/public.env -f deploy/compose.yaml -p zpcode-site up -d web
python3 scripts/smoke.py --url http://127.0.0.1:18080
```

### 2.2 备份、DNS与HTTPS

先保留当前上传的原始压缩包，另外备份真实release。每次使用新备份文件名，不覆盖旧版本；Caddy数据卷后续包含证书私钥，不上传聊天或Git。

```sh
tar -czf /home/ubuntu/zpcode-approved-20261018.tar.gz release deploy
```

若已存在同名备份先改日期后缀。正式开放前，查看 `sudo ss -lntp '( sport = :80 or sport = :443 )'`，发现已有服务则先检查，不停止或覆盖其他服务。

1. DNSPod为 `zpcode.online` 添加一条 `@` / `A` / `43.142.123.91`。首版只用根域名，不添加www或无实际IPv6服务的AAAA，不购买专业DNS。
2. 在腾讯云轻量防火墙放行TCP80与TCP443；不改现有SSH认证，不放行18080、MySQL或Agent端口。若系统另启了防火墙，也须按实际规则配置。
3. 确保DNS已指向这台机器，再启动独立的Caddy入口。Caddy只转发到静态web服务，没有真实工作台上游。

```sh
sudo docker compose --env-file deploy/public.env -f deploy/compose.yaml -p zpcode-site --profile public pull edge
sudo docker compose --env-file deploy/public.env -f deploy/compose.yaml -p zpcode-site --profile public run --rm --no-deps edge caddy validate --config /etc/caddy/Caddyfile
sudo docker compose --env-file deploy/public.env -f deploy/compose.yaml -p zpcode-site --profile public up -d
sudo docker compose --env-file deploy/public.env -f deploy/compose.yaml -p zpcode-site --profile public logs --tail=80 edge
python3 scripts/smoke.py --url https://zpcode.online
```

首次申请公网证书要求服务器能联系CA，域名正确解析，80/443可达，数据卷可写；配置存在不等于证书已签发。网络失败查看日志，**不要使用curl -k或关闭证书校验假装成功**。不在备案前反复申请证书，不提前配置DNS API凭据。

用手机关闭Wi-Fi/SSH隧道后访问正式域名，检查4页、备案链接和 `/tasks`、`/docs` 均不可用；核对HTTPS证书。通过后再按官方要求办理公安联网备案。没有公安备案号时不虚构页脚；实际获批后补真实链接。

## 三、停止与回滚

随时撤下公网入口，不删除数据卷、不停止其他Compose项目：

```sh
sudo docker compose --env-file deploy/public.env -f deploy/compose.yaml -p zpcode-site --profile public stop edge
```

先停edge，才可重新用preview.env切回私有预览。不要在edge仍公开运行时把web切回未备案页脚版本。若要回滚正式内容：保留新release目录，恢复已备份且仍带正确备案号的旧release，再重建本站web、私有验收后恢复edge。不要对整个项目或家目录执行递归删除。

## 相关说明

- [官方备案填写清单](ICP_GUIDE.md)
- [腾讯：首次备案](https://cloud.tencent.com/document/product/243/37402)
- [Docker：端口发布](https://docs.docker.com/engine/network/port-publishing/)
- [Caddy：自动HTTPS](https://caddyserver.com/docs/automatic-https)
