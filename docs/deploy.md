# 部署大肥鱼

这是我们这台服务器上实际在用的部署方式：一台 Linux，原生安装（不用 Docker），systemd 托管。下面路径都按 `/opt/dafeiyu`、运行用户 `agentuser` 写，换成你自己的就行。

> 实测环境：Ubuntu 24.04、2 核、7.4G 内存、Python 3.12、AstrBot 4.29.4、uv 0.13。

## 0. 准备

- 一台 Linux 服务器（2 核 4G 起步；要本地语音识别、刷 B 站截帧，内存大点更稳）
- 一个给她用的 **QQ 小号**（会挂在服务器上长期登录）
- 至少一个 **OpenAI 兼容的大模型 API**（主力）+ 一个兜底的（我们用 DeepSeek）。要看图，主力模型得支持图片输入
- 可选：小米 MiMo 语音合成的 key（发语音）、B站 / 贴吧 / 推特小号的登录信息（刷网）

```bash
sudo apt update
sudo apt install -y python3 python3-venv ffmpeg xvfb curl git
sudo mkdir -p /opt/dafeiyu/{logs,private,kit} && sudo chown -R agentuser:agentuser /opt/dafeiyu
chmod 700 /opt/dafeiyu/private      # 密钥、cookie、登录信息都放这里
```

## 1. 装 AstrBot（用 uv，装到 /opt/dafeiyu 下面）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export UV_TOOL_DIR=/opt/dafeiyu/uv/tools UV_TOOL_BIN_DIR=/opt/dafeiyu/uv/bin
uv tool install --python 3.12 "astrbot==4.29.4"
mkdir -p /opt/dafeiyu/astrbot && cd /opt/dafeiyu/astrbot
/opt/dafeiyu/uv/bin/astrbot run      # 第一次启动会生成 data/，看日志里的后台初始密码，Ctrl+C 停掉
```

自写插件还用到这些库，装进 AstrBot 的环境：

```bash
/opt/dafeiyu/uv/tools/astrbot/bin/python -m pip install "aiotieba>=4.8,<5" twikit beautifulsoup4 lxml playwright
```

## 2. 装 NapCat（无头 QQ）

按 [NapCat 官方文档](https://napneko.github.io/) 装 Linux 版（会装一个 Linux QQ 并把 NapCat 注进去）。我们装在 `/home/agentuser/Napcat`，用 `xvfb-run` 无界面跑，启动脚本见 [`deploy/start-napcat.sh`](../deploy/start-napcat.sh)：

- 把小号的 QQ 号写进 `/opt/dafeiyu/private/bot-qq.txt`，之后就是快速登录；没有就出二维码，用小号手机 QQ 扫码。
- 在 NapCat 的 OneBot 11 网络配置里加一个 **WebSocket 客户端**（反向 WS），连 AstrBot：
  - 地址 `ws://127.0.0.1:6199/ws`
  - 消息格式 `array`，不上报自己发的消息
- 在 AstrBot 后台 → 平台 → 新建 **aiocqhttp（OneBot v11）**，反向 WS 监听 `127.0.0.1:6199`，平台名我们叫「大肥鱼」（不少插件按这个名字找平台）。

> 可选：QQ 对登录 IP 敏感。我们让 QQ 单独走家里宽带出口（mihomo + cgroup 分流），扫码和日常登录同一个 IP，不容易被风控。脚本在 [`tools/qq_proxy.sh`](../tools/qq_proxy.sh)，单元在 [`deploy/systemd/dafeiyu-mihomo.service`](../deploy/systemd/dafeiyu-mihomo.service)；不需要就删掉 NapCat 单元里的那段 drop-in。

## 3. systemd 托管

把 [`deploy/systemd/`](../deploy/systemd/) 下的单元拷到 `/etc/systemd/system/`，按你的路径改一下：

```bash
sudo cp deploy/systemd/dafeiyu-astrbot.service deploy/systemd/dafeiyu-napcat.service /etc/systemd/system/
sudo cp -r deploy/systemd/dafeiyu-napcat.service.d /etc/systemd/system/   # 不走代理就别拷这个
sudo cp deploy/start-napcat.sh /opt/dafeiyu/ && chmod +x /opt/dafeiyu/start-napcat.sh
sudo systemctl daemon-reload
sudo systemctl enable --now dafeiyu-astrbot dafeiyu-napcat
tail -f /opt/dafeiyu/logs/astrbot.log
```

后台（AstrBot 管理面板）默认只听 `127.0.0.1:6185`。我们用 Tailscale 的 `tailscale serve` 把它只开给自己的设备，不对公网暴露；也可以用 SSH 隧道。

## 4. 接模型

AstrBot 后台 → 模型提供商：

| 用途 | 我们的做法 |
|---|---|
| 主力聊天 / 读空气 / 心情 / 知识库 / 看图 | 任意 OpenAI 兼容接口，要支持图片和工具调用 |
| 兜底 | DeepSeek（`deepseek-flash`），主力挂了自动切过去 |
| 语音合成 | 小米 MiMo `mimo-v2.5-tts`，音色「茉莉」，风格见下 |
| 嵌入向量（长期记忆用） | 任意嵌入接口（我们用硅基流动） |

然后在「配置 → 对话模型」里把主力设成默认、DeepSeek 设成备用；各插件里需要模型的地方（读空气、心情、知识库、B站、识图）填同一个主力模型 ID。完整的配置结构见 [`deploy/config-examples/`](../deploy/config-examples/)（密钥、号码都是占位，照着填）。

语音风格（MiMo 是按描述生成声音的，描述越稳声音越稳）：

```text
年轻女生，茉莉的本嗓，音色清亮甜，语速适中，语气自然轻松带一点笑意，每句话的音色和情绪都保持一致，不夸张不拖腔
```

AstrBot 自带的「按概率把回复转语音」我们关掉了（概率设 0），改成她自己用 `send_voice` 工具决定什么时候用声音说话。

## 5. 人设

后台 → 人格 → 新建「大肥鱼」，系统提示贴 [`deploy/persona.md`](../deploy/persona.md) 里那段，把 `<主人QQ号>` 换成你自己的 QQ。然后在配置里把默认人格设成「大肥鱼」。

## 6. 装插件

1. **第三方插件**：在 AstrBot 后台插件市场按 [docs/plugins.md](plugins.md) 里的版本装。
2. **自写插件**：

   ```bash
   cp -r plugins/astrbot_plugin_dafeiyu_* /opt/dafeiyu/astrbot/data/plugins/
   sudo systemctl restart dafeiyu-astrbot
   ```

3. **补丁**：第三方插件装好后跑一遍 `patches/` 里的脚本（插件每次升级后也要重跑）：

   ```bash
   /opt/dafeiyu/uv/tools/astrbot/bin/python patches/patch_kugou_direct.py   # 其它同理
   ```

4. 各插件的配置在后台「插件配置」里填，参考 `deploy/config-examples/astrbot_plugin_*.example.json`。几个要注意的：
   - **群聊增强（group_chat_plus）**：我们把触发词、概率、频率调整全关了，读空气概率设 1.0（每条消息都让她自己判断要不要回），决策模型用主力模型。
   - **B站 / 贴吧 / 推特**：各自要小号的登录信息（cookie / BDUSS），放后台配置或 `/opt/dafeiyu/private/` 下，别提交到任何仓库。
   - **知识库（dafeiyu_kb）**：会把她待的群的消息全部存下来，部署前请确认群友知情。

## 7. 语音识别（可选）

她听群里的语音用本地的 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) + SenseVoice 小模型（int8），放在 `/opt/dafeiyu/asr`：

```text
/opt/dafeiyu/asr/
├─ sherpa-onnx-v1.13.8-linux-x64-shared-no-tts/   # sherpa-onnx 预编译包
├─ model.int8.onnx                                 # SenseVoiceSmall int8
└─ tokens.txt
```

然后在 `dafeiyu_video` 插件配置里把 `asr_dir` 指到这里。

## 8. 每小时自修（可选）

我们用 Claude Code 的定时任务每小时读一次日志：发现她不像人的地方或报错，就直接改（改前备份），改完往 `/opt/dafeiyu/private/claude_relay/outbox/` 写一条消息，`dafeiyu_claude` 插件会用她的 QQ 私聊主人；主人回「撤回 #编号」就退回。这部分需要你自己有 Claude Code，不是必需的。

## 常见问题

- **她不说话**：先看 `astrbot.log` 里有没有模型报错（402 欠费、401 key 错、超时）；读空气的模型挂了，她会默认不回。
- **QQ 掉线 / 要重新扫码**：看 `napcat.log`；换了登录 IP 容易被要求重新验证。
- **看不见图**：主力模型要支持图片；QQ 旧图链接（`gchat.qpic.cn`）有时会 400，群图插件会改用 NapCat 的 `get_image` 重新取。
