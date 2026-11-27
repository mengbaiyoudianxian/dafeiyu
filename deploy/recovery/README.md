# 机主私有源码补充包

`bundle.py` 适用于 Linux、Python 3.9+，只用标准库。归档在运行命令的机器上生成。请保存在机主可读取的私有目录；其中的生产源码可能含机主自定义逻辑、硬编码值或私人提示。输出的归档、展开文件和私有运行配置都应保存在 Git 工作区之外。

## 在原机采集

以下环境变量由机主设置为对应安装的**真实目录**。例如 `PLUGIN_ROOT` 指向 AstrBot 的 `data/plugins`，`NAPCAT_ROOT` 指向含有 `napcat.mjs` 的发行目录。安装程序使用符号链接时，应先通过 `realpath` 核对实际来源目录。

```sh
umask 077
install -d -m 0700 "$PRIVATE_BACKUP_DIR"
python3 bundle.py capture \
  --plugins "$PLUGIN_ROOT" --kit "$KIT_ROOT" --codex-bridge "$BRIDGE_ROOT" \
  --site-packages "$VENV_SITE_PACKAGES" --napcat "$NAPCAT_ROOT" \
  --anime-studio "$MCP_ROOT" --output "$PRIVATE_BACKUP_DIR/source-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
```

工具用 `catalog.json` 列出的 31 个插件、Linux x64 NapCat 和 MCP 发行文件作为采集范围。插件包含现有源码、前端、配置 schema 与依赖描述；已知之外的源码也从插件源目录发现。归档流式写入临时文件，逐文件核对 SHA-256，成功后才以新文件名发布。采集过程中源文件变化会终止该次操作。命令输出文件数、大小及整包 SHA-256，可将摘要保存在单独的机主私有位置。

| 归档内目录 | 新机器的对应位置 |
|---|---|
| `plugins/<插件名>/` | AstrBot 数据根目录下的 `plugins/<插件名>/`。先安装项目固定版本和依赖，再对照原机代码处理个性化部分。 |
| `kit/` | 自有工具目录。包含顶层 Python/Shell 及安装工具的脚本。 |
| `codex-bridge/` | 已配置的桥接服务源码目录。服务环境文件、登录授权单独由机主管理。 |
| `site-packages/` | 已按项目锁文件安装的 Python 虚拟环境的同名相对路径。包含已知框架及客户端文件，以及安装记录中 SHA-256 与当前 Python 源文件不一致的成员。 |
| `napcat/` | 已安装相同 Linux QQ 版本后，指向其 `resources/app/app_launcher/napcat`。包含分发核心、worker、静态界面、必要 JS 依赖和 Linux x64 native 模块。QQ 主程序和入口配置参见项目外部依赖说明。 |
| `anime-studio/` | 对应 stdio MCP 的发行目录。包含两份 JS、`AGENTS.md` 及技能说明和参考文档。 |

## 核对并展开

```sh
python3 bundle.py verify "$ARCHIVE" --sha256 "$SAVED_SHA256"
python3 bundle.py unpack "$ARCHIVE" --sha256 "$SAVED_SHA256" --output "$EMPTY_RESTORE_DIR"
```

展开目标的父目录需已存在；目标本身可以不存在或为空目录。工具在同文件系统的临时目录逐文件核对校验清单，全部成功后才发布目标目录。它拒绝绝对路径、路径穿越、重复成员、链接、特殊设备、已有内容和 Git 工作区。目录权限为 0700，普通文件为 0600，原有执行文件为 0700。完成后对照表格，在新安装的停服副本中复核并选择需要的文件；展开命令本身不会修改服务或生产安装。

## 另外保管的资料

- 账号登录、授权、服务环境、账号映射及人格配置按项目私有 profile 流程备份。MCP 连接凭据和平台会话由机主单独提供。
- 聊天记录、记忆库、书架、表情库、用户上传内容、运行数据库、缓存和日志不在本工具的选取范围。恢复历史数据时使用机主的数据备份。
- 自建独立提示词文件、自定义模型素材和插件源码以外的私人美术资产由机主在自己的私有清单登记相对用途、位置和校验值。标准依赖、公开静态模板及未修改的包可从项目锁文件和发行来源重建。
