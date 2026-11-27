# 阅读器、网页互动与漫造 MCP

以下命令以本目录为当前路径。Python 阅读器、图像助手和 MCP 属于三个独立运行环境。网页示例只需要浏览器与静态服务器。

## 阅读器

在机器人服务账号的环境中运行。主环境的 Playwright 版本为 1.65.1，对应完整 Chromium 158.0.8127.18，浏览器构建号 1254。Ubuntu 24.04 的原部署通过已加载的 AppArmor `chrome` 配置启动浏览器。脚本会核对 Python 包、驱动清单和可执行文件版本，随后在会话目录生成启动器。[Playwright 浏览器安装说明](https://playwright.dev/python/docs/browsers)

```sh
python -m playwright install --with-deps chromium
python reader_browser.py --apparmor-profile chrome
```

系统已配置浏览器沙箱且无需 AppArmor 配置时，可省略 `--apparmor-profile chrome`。默认会话目录为 `~/.local/share/dafeiyu-comic-sessions`。浏览器、阅读器与登录助手须使用同一个服务账号和会话目录。

需要账号的站点由部署者自行登录。远程无桌面环境可用附带的临时登录助手。Ubuntu 依赖可通过 `sudo apt install xvfb xauth x11vnc websockify` 安装。原部署使用 noVNC v1.5.0 的静态文件，安装版本、标签和提交见 [components.lock.json](components.lock.json)。[noVNC 官方仓库](https://github.com/novnc/noVNC)

```sh
git clone --branch v1.5.0 --depth 1 https://github.com/novnc/noVNC.git "$HOME/noVNC"
git -C "$HOME/noVNC" rev-parse HEAD
export DAFEIYU_NOVNC_WEB="$HOME/noVNC"
python comic_login.py pica
```

在本机另开终端，将 `BOT_SSH` 换成自己的 SSH 目标（登录用户应与服务账号一致）。打开输出的一次性本机链接，手动完成网站登录。在服务器端按 Ctrl-C 关闭临时窗口，阅读器即可使用此账号的浏览器资料。JM 对应的站点参数为 `jm`。助手只在回环地址监听，20 分钟后退出。临时登录链接、钥匙、会话资料和 Cookie 均由部署者本地持有。

```sh
python comic_login_local.py pica BOT_SSH
```

## 图像助手

网页互动插件会调用同目录下的 `helpers/cutout.py` 和 `helpers/anim_make.py` 生成抠图及动画。原独立运行环境使用 CPython 3.12.3、Linux x86-64 与 glibc。本目录锁定了该平台官方 NumPy、SciPy、Pillow 三个 wheel 的 SHA-256。运行以下命令后，将 `DAFEIYU_IMAGE_PYTHON` 写入机器人服务环境。每次启动服务都须提供这一设置。

```sh
python3.12 -m venv "$HOME/.venvs/fish-image"
"$HOME/.venvs/fish-image/bin/python" -m pip install --only-binary=:all: --require-hashes -r image-helpers-linux-cp312.lock
export DAFEIYU_IMAGE_PYTHON="$HOME/.venvs/fish-image/bin/python"
```

## 网页互动

[touch](touch/) 是独立编写的最小客户端，支持场景图、钥匙登录、动作选择、网页聊天、历史记录与退出。把 `index.html`、`panel.css`、`panel.js` 放在同一个静态目录。页面内存保存当前钥匙，刷新后需重新登录。图片由启用场景生成的实例自行生成。

[Caddyfile.example](touch/Caddyfile.example) 将 `/fish/` 指向静态目录，将同源的 `/fish-api/*` 转发到插件的 `/api/*`。为 Caddy 服务设置 `FISH_SITE`（自己的域名）、`FISH_STATIC_ROOT`（上述三个文件的绝对目录），以及可选的 `FISH_TOUCH_UPSTREAM`。同机默认上游为 `127.0.0.1:16290`。跨机时将插件的 `listen_host`、`listen_port`、`allow_peer` 配置为自己的内网监听地址、端口和反向代理的实际来源地址。Caddy 覆写来源请求头供后端限流使用。

| 接口 | 请求及用途 |
| --- | --- |
| `GET /api/scenes`、`GET /api/scene/{name}` | 场景列表、活动描述和图片 |
| `GET /api/actions`、`GET /api/pose/{outfit}/{pose}.webp` | 动作目录及实例自行生成的姿势图 |
| `POST /api/login`、`POST /api/history` | JSON 含 `key`，分别返回本人关系等级和最近 20 条网页记录 |
| `POST /api/touch` | JSON 含 `key`、`kind`，返回动作结果与台词 |
| `POST /api/chat` | JSON 含 `key`、`text`，返回网页回复 |

在机器人私聊中发送 `/钥匙` 获取本人的面板钥匙。网页对话依赖 heart 插件中的关系与对话状态；图像生成依赖部署者自己的桥接服务与模型授权。聊天记录、用户关系、登录资料和生成的图片留在部署者环境。可用合成数据在本地测试六个 JSON 接口和浏览器交互，全程拦截浏览器的网络请求。

```sh
python test_touch.py
```

## 漫造 MCP

实机的 AstrBot 通过 stdio 启动官方 `anime-studio`。当时使用 Node.js 22.22.2 和供应商 build 14（`2026.11.08-14`）。供应商公开安装入口跟随最新版，发布核对时清单已是 build 18。若要复现旧版，需向供应商取得相应旧安装包，或使用部署者自己合法保存的副本。当前公开入口未提供经验证的 build 14 版本化下载地址。[供应商安装页](https://manzaoai.top/mcp/) · [公开清单](https://manzaoai.top/mcp/manifest.json) · [Node.js 归档](https://nodejs.org/dist/v22.22.2/)

对持有的旧版运行以下命令。验证器核对 Node.js 版本和供应商 7 个发布文件的 SHA-256，通过后输出最小 MCP JSON。该程序只执行 `node --version`，不会启动供应商客户端。按原部署把生成的 `mcpServers.anime-studio` 项合并至 AstrBot 数据目录的 `mcp_server.json`。采用供应商现行版本时请使用其安装页的 stdio 配置并记录所选版本。服务授权由部署者在自己的账号下完成。

```sh
python verify_mcp.py "$HOME/.manzao/anime-studio"
```
