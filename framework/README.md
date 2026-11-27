# 重建大肥鱼使用的 AstrBot 4.29.4

这里保存大肥鱼部署相对于官方 AstrBot 4.29.4 的完整框架源码差异。构建需要 Python 3.12 和 Git。构建器从经过校验的官方 wheel 取出 8 个文件，应用补丁，逐一核对部署版本的 SHA-256，然后生成带有 `1dafeiyu` 构建标识的 wheel。其余 Python 模块和内置网页资源沿用官方版本。

## 构建与安装

以下命令从本仓库的根目录运行。请在一个新建并已激活的 Python 3.12 虚拟环境执行安装。

```sh
python -m pip download --only-binary=:all: --no-deps --dest vendor astrbot==4.29.4
python framework/build_wheel.py vendor/astrbot-4.29.4-py3-none-any.whl --output-dir dist
python -m pip install dist/astrbot-4.29.4-1dafeiyu-py3-none-any.whl
python framework/test_chat.py
```

获取官方 wheel 后，构建过程可以离线运行。离线安装时，先准备包含 AstrBot 全部依赖的本地 wheel 目录，再给安装命令加上 `--no-index --find-links 本地依赖目录`。应用运行时需要管理的数据和各个插件由本仓库的部署说明配置。

| 文件 | SHA-256 |
| --- | --- |
| `astrbot-4.29.4-py3-none-any.whl` | `189e9b07bff6fb91fe0ebaa3e9273af6b028f190023dcc43cbbbe4d3ec00a5b5` |
| `astrbot-4.29.4-1dafeiyu-py3-none-any.whl` | `e0ad64000b3224183b8068336c0339278a085bf91f4a92cbbb1ecd21158d8e66` |

`manifest.json` 记录了官方 wheel 的下载地址、补丁摘要以及 8 个文件的原始和部署版本哈希。构建器固定归档成员顺序、时间戳和存储方式，更新 wheel 构建标识和 `RECORD`，最后校验整个生成物的 SHA-256。[PyPI 4.29.4 发布记录](https://pypi.org/project/astrbot/4.29.4/#files)

## 部署差异

比较以官方 wheel 的 861 个文件为基线，覆盖安装目录里的 `astrbot/` 和 `astrbot-4.29.4.dist-info/`。8 个框架文件有源码差异，其余框架源码和内置资源均与官方文件一致。安装器会另外生成 `INSTALLER`、`REQUESTED`、命令行启动器和本机的 `RECORD`；Python 还可能生成字节码缓存。

| 文件（位于 `astrbot/core/`） | 部署差异 |
| --- | --- |
| `astr_main_agent_resources.py` | 删除原有“健康模式”系统提示常量。 |
| `astr_main_agent.py` | 删除相应的构建参数、提示注入函数及普通主代理调用点。 |
| `pipeline/process_stage/method/agent_sub_stages/internal.py` | 普通聊天初始化不再读取这两个旧人格字段。 |
| `astr_agent_tool_exec.py` | 子代理的构建参数去除了这两个字段。 |
| `cron/manager.py` | 定时任务的主代理构建参数同步去除这两个字段。 |
| `config/agent_runner.py` | 本地 runner 的默认人格配置去除这两个字段。 |
| `config/default.py` | 配置面板去除对应开关、策略和显示条件。 |
| `utils/migra_helper.py` | 配置迁移过程不再从旧版提供商设置生成这两个字段。 |

## 离线聊天回归测试

`test_chat.py` 使用已安装框架的普通聊天阶段初始化和完整主代理请求构建函数。它从一条普通问候与一个示例人格开始，在 runner 边界读取最终请求。测试隔离了模型、插件和数据库调用，禁止外部网络访问，并使用临时应用目录。它分别覆盖旧字段 `safety_mode=true` 和 `false`。部署版两种情况都保留示例人格原文。若要核对正对照，可在另外一个只安装官方 wheel 的虚拟环境运行 `python framework/test_chat.py --upstream`。此时旧字段为真会注入官方提示，为假时会保留原文。

这项验证覆盖请求构建；真实模型回复、QQ 接入、定时任务调度以及各插件的权限和行为属于各自的运行验证范围。

## 许可

AstrBot 原项目及本目录框架补丁遵循 AGPL-3.0-or-later。原作者和贡献者信息见项目仓库，许可证全文见本目录的 `LICENSE`。本目录同时保留构建器、补丁和测试，供复查与重建。[AstrBot 官方源码](https://github.com/AstrBotDevs/AstrBot)
