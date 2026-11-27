# 安装大肥鱼的 31 个 AstrBot 插件

在仓库根目录运行以下命令。需要 Python 3.10 或更高版本、Git、可访问 GitHub 的网络，以及一个新的或空的目标目录。默认安装全部 31 个插件。

```sh
python install_plugins.py --dest /srv/astrbot/data/plugins
```

安装器会先在目标目录的同级临时目录准备 22 个自有插件和 9 个第三方插件。第三方插件取自 `inventory.json` 指定的完整 Git 提交，6 个插件另有公开补丁。安装器会核对提交、校验补丁、检查补丁能否应用，并校验清单所列文件的 SHA-256。所有插件通过后才交付目标目录。重复安装时需要新的空目录，现有非空目录会被保留。

已有公共 Git 镜像时，可以将每个仓库放在与清单内插件目录同名的位置后安装。

```sh
python install_plugins.py --dest /srv/test/astrbot-plugins --mirrors /srv/plugin-mirrors
```

## 安装与运行

插件目录安装完成后，使用 AstrBot 的插件依赖安装流程，并在实际服务环境配置所用模型、账号、平台权限及启用状态。公开清单涵盖部署过的插件。具体启用哪些插件由部署者决定。

| 组件 | 运行时配置 |
|---|---|
| Grok 图片和视频 | 通过服务环境的 `XAI_API_KEY` 使用部署者获授权的 xAI API。图片还支持 `DAFEIYU_BRIDGE_RESPONSES_URL` 指定的本机图像服务。公开版本用 API 密钥配置替代原部署的订阅设备登录与令牌续期。 |
| X 浏览 | 使用 `DAFEIYU_X_COOKIE_FILE` 指定自有账号的私有授权文件。公开版本通过部署者提供的独立会话工作，浏览器资料读取和密码登录已移除。 |
| 贴吧 | 浏览、书目和调度保留。可选的写入操作交付给 `DAFEIYU_TIEBA_WRITE_ADAPTER` 指定的回环 HTTP(S) 服务。它接收包含 `operation` 和 `fields` 的 JSON 对象，并返回与该操作对应的 JSON 结果。部署者须自行提供获授权的写入实现，公开版本省略移动客户端签名材料。 |
| 漫画书架 | 安装 Playwright 及相应 Chromium。用 `DAFEIYU_COMIC_SESSIONS` 指定私有会话目录。平台要求登录或验证时，由账号所有者在平台完成。各站资料、作品、笔记分别留在各自的运行机器上。 |
| 触控和形象 | 默认仅允许回环连接。纯图像处理脚本在 `astrbot_plugin_dafeiyu_touch/helpers/`。可用 `DAFEIYU_IMAGE_PYTHON` 指定装有 Pillow、NumPy 与 SciPy 的解释器，实际形象和生成素材由部署者提供。 |
| 主人、桥接与二维码 | 对应插件识别 `DAFEIYU_OWNER_QQ`、`DAFEIYU_BRIDGE_URL`、`DAFEIYU_CLAUDE_RELAY_DIR` 和 `DAFEIYU_NAPCAT_QR`。Claude 中转的默认位置在当前实例的插件数据目录。原部署的个人标识和一次性历史修复已移除。 |
| 面板与人格 | `DAFEIYU_PANEL_URL` 设置可分享的面板地址。`DAFEIYU_TOUCH_PANEL_LABEL` 设置交互面板显示名。`DAFEIYU_HEART_JUDGMENT_TEXT` 可以从服务的私有环境补充关系与情绪判断说明。公开默认使用当前配置的人格和会话输入。 |
| 音乐 | 保留经校验的上游播放器和下载重试补丁。上游渠道负责账号授权与播放权限。公开补丁省略部署机器上的特定移动端签名取流实现。 |

## 来源与校验

`inventory.json` 的 `repository` 和 `commit` 给出实际取件位置。`declared_repository` 保留部署元数据中的来源。QQProfile 和 Qzone 的旧地址在整理时不可访问，清单锁定了按源文件内容核对过的公共镜像。QQProfile 的 8 个受审查文件与镜像精确对应。Qzone 的运行时代码改动由补丁提供，其 README、署名、元数据和许可证随公共镜像原样保留。其他列入 `retained_upstream_files` 的文件也采用锁定提交版本。

清单中的哈希先将 CRLF 替换为 LF，再计算 SHA-256。它覆盖受审查的运行源码、插件元数据、相应前端或配置文本，并覆盖额外随包提供的依赖文件。公开上游仓库可能还包含图标、文档和测试等文件。请保留每个第三方插件自己的 `LICENSE` 及版权署名。仓库根目录的许可证适用于本项目自有部分。

本版在一个新的空目录完成了 31 项安装校验，并用 Python 3.12 编译了安装结果的 933 份 Python 源码。另行核对了群聊插件的私聊会话保护模块导入，以及长期记忆调度器的类名。可以在独立测试目录安装后运行 `python -m compileall -q /srv/test/astrbot-plugins` 复查语法。

源码校验和干净安装不构成平台账号实测。实际运行的模型服务、合法授权、数据库和私有媒体需要由部署者在自己的环境中准备。
