# 从仓库重建大肥鱼

训练机运行 Ubuntu 24.04 x86_64、Python 3.12、AstrBot 4.29.4 和 NapCat。主聊天模型是 `grok/grok-4.6`，备用模型列表为空。公开版使用独立生成的面板密码、公开人格起点和空的插件数据。要恢复自己的账号、人格与权限范围，请在安装时提供本地私有配置档。

## 1. 准备新机器

安装 Python 3.12、uv、Git 及系统依赖。示例使用已有的普通服务账号 `agentuser`，由该账号执行安装。

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git curl ffmpeg xvfb xauth socat libgl1 libglib2.0-0
sudo install -d -m 700 -o agentuser -g agentuser /opt/dafeiyu
git clone https://github.com/mengbaiyoudianxian/dafeiyu.git
cd dafeiyu
# 重建某次发布时，将下面的提交号换成该版本的完整 Git SHA。
# git checkout --detach <commit>
```

使用 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装 uv，并确认 `uv --version` 可以运行。程序、凭据和数据放在 Git 工作树以外。

## 2. 选择初始状态

**新账号。** 直接运行第 3 节的安装命令。安装后配置自己的聊天服务密钥、QQ 管理员及各插件的账号。预设的面板、OneBot 和触摸服务监听回环地址。其他设备要使用触摸服务时，在本机插件配置中填写自己的监听地址、允许的对端及凭据。

**还原原训练机的配置。** 在原机执行下面的命令，将生成的文件通过自己的私有通道送到新机同一路径。文件包含模型密钥、插件登录配置、人格、工具权限和会话级覆盖。每次导出使用新文件名。

```bash
install -d -m 700 "$HOME/dafeiyu-private"
python3 deploy/runtime/profile.py capture /opt/dafeiyu/astrbot "$HOME/dafeiyu-private/profile.json"
chmod 600 "$HOME/dafeiyu-private/profile.json"
# 在新机安装时设置
export DAFEIYU_PRIVATE_PROFILE="$HOME/dafeiyu-private/profile.json"
```

原机的 `/opt/dafeiyu/private/`、NapCat 登录状态、插件数据、记忆、书库和个人图片由机主另外保管。配置档只迁移设置。需要完整历史时，在停服后的文件副本或一致性快照上恢复原机的 `astrbot/data/`、私有文件和外部程序，然后核对插件目录与本次锁文件。已有真实数据的目录应保留，安装脚本只接受新的数据目录。

## 3. 安装和校验

```bash
bash deploy/install.sh /opt/dafeiyu
```

脚本安装 [187 个固定版本的 Python 包](../deploy/runtime/python-linux-x86_64.lock)，验证官方 AstrBot wheel 的 SHA-256 并构建带本地补丁的 wheel，执行聊天请求测试、初始化 SQLite，再按 [插件锁](../inventory.json) 安装 22 个自写插件和 9 个第三方插件。下载和校验全部完成后才安装插件目录。临时构建文件在完成后清理。要使用自己下载的官方 wheel，可设置 `DAFEIYU_UPSTREAM_WHEEL`。框架补丁和独立测试见 [framework/README.md](../framework/README.md)。

Python 包与插件需要访问它们的公开来源。固定版本避免常规升级引入差异。第三方私有登录、生成服务、网页接口和模型可用性取决于提供方和自己的账号。外部程序版本、校验与本地补充文件见 [external/README.md](../external/README.md)。

默认人格名为“大肥鱼”。全局预设停用 B 站、贴吧、X 三个插件和两个点歌工具，保留训练机的工具及指令权限。清单在 [global-preferences.json](../deploy/runtime/global-preferences.json)。应用程序、定时任务及工具回调使用本版本的框架补丁。

公开版插件逐项差异和授权接入方式在 [插件安装说明](pinned-plugins.md)。例如 Grok 图片和视频使用机主提供的 `XAI_API_KEY`。LivingMemory 启用了访问白名单，新账号需在本机填自己的允许 ID 才能使用记忆。阅读器的独立 Chromium、扫码登录、网页互动和漫造 MCP 见 [集成部署说明](../deploy/integrations/README.md)。

## 4. 接上账号并启动

1. 按 [NapCat 官方文档](https://napneko.github.io/) 准备 Linux QQ 和 NapCat，对照外部程序清单核验版本。服务模板约定 QQ 可执行文件为 `/home/agentuser/Napcat/opt/QQ/qq`。
2. 在 NapCat 中配置 OneBot 11 反向 WebSocket 客户端，连接 `ws://127.0.0.1:6199/ws`，使用 array 消息格式。两侧填相同的 token。首次使用自己的 QQ 扫码。需要快速登录时把自己的 QQ 号写在 `/opt/dafeiyu/private/bot-qq.txt`。
3. 没有采用私有配置档时，在 AstrBot 本机面板录入模型凭据和管理员账号，核对 Grok、MiMo、嵌入及插件提供方设置。账号信息与历史数据均留在运行目录。
4. 根据需要安装下列服务。命令按上面的默认账号和目录编写。目录或账号不同，先调整单元文件和插件中的本机路径。

```bash
sudo install -m 644 deploy/systemd/dafeiyu-{astrbot,napcat}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dafeiyu-astrbot dafeiyu-napcat
systemctl is-active dafeiyu-astrbot dafeiyu-napcat
tail -n 50 /opt/dafeiyu/logs/astrbot.log
```

AstrBot 面板监听 `127.0.0.1:6185`。面板的初始密码由 AstrBot 在新机生成，可以使用 SSH 本地转发访问。按需要选择以下功能。

- **Codex 本地桥接**。源码在 [services/codex-bridge](../services/codex-bridge/)，监听 `127.0.0.1:18790`。在机主自己的登录环境生成 `private/codex-auth*.json`，并在权限为 600 的 `private/codex-bridge.env` 写入 `CODEX_OAUTH_CLIENT_ID`，其值与生成凭据的 OAuth 客户端一致。安装并启动 `dafeiyu-codex-bridge.service`。已有部署可从原程序的 `CLIENT_ID` 配置本地转入环境文件。
- **QQ 单独代理或内网面板**。按需配置自己的 `private/mihomo/` 与 mihomo 二进制，安装相应的服务及 NapCat drop-in。需要在自己尾网地址提供面板时，在 `private/dashboard.env` 中设置 `TAILNET_IP`，再安装 `dafeiyu-dash-tsip.service`。
- **备份**。训练机到阿里云盘的独立分卷任务和首次配置见 [tools/alipan-raw/README.md](../tools/alipan-raw/README.md)。安装机器人不会启动备份。
- **统计**。可选脚本 [scripts/update_stats.py](../scripts/update_stats.py) 依赖本地记录。首次校对内容时给脚本传 `--no-push` 并传入单独目录。历史用量补差通过机主设置的 `DAFEIYU_HISTORICAL_TOKENS` 提供。

