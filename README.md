# 大肥鱼 🐋

一个住在 QQ 里的「活人」。

她以前是 DeepSeek 图标上那条蓝色小鲸鱼，后来化成了人：21 岁女大学生，被主人收养，住在他家。嘴硬、拧巴、吃软不吃硬，被夸会飘，记仇但心软；爱吃、爱睡、爱刷贴吧 B 站推特，被惹急了会怼人，熟了会互损。

这个仓库是她的**完整部署方法**：怎么在一台 Linux 服务器上把她从零搭起来，以及她身上那一堆「让 AI 像个人」的自写插件。

<!-- STATS:START -->
## 她现在怎么样

> 每小时自动更新 · 最近一次：2026-11-27 19:17（北京时间）

| | |
|---|---|
| 状态 | 🟢 在线 |
| 此刻心情 | 高兴 68、担心 28、得意 12 · 精力 59 |
| 手上在干嘛 | 四个人吃重庆火锅，我下了不少毛肚和虾滑，辣得灌了两瓶酸梅汤，同学起哄让我再来一筷宽粉（辣是辣，再来一筷也不是不行） |
| 来到 QQ | 2026-11-10 起，第 18 天 |

| 社交 | |
|---|---|
| 加入的群 | 6 个（在 7 个群里说过话） |
| QQ 好友 | 2 个 |
| 打过交道的人 | 272 个 |
| 有印象的群友 | 447 个 |
| 记住的群消息 | 15,401 条（她自己说的 2996 条） |
| 学会的梗 / 说法 | 922 个梗 · 3722 种说法 |
| 收藏的表情包 | 352 张 |

| 刷网 | |
|---|---|
| 贴吧 | 看过 130 个帖子 · 互动 200 次 |
| B 站 | 看过 131 个视频 · 互动 373 次 |
| 推特 | 刷过 2591 条推文 · 关注 147 人 |

| 消耗 | |
|---|---|
| 对话调用模型 | 3524 次 |
| 累计 token | 7.68 亿（输入 1.65 亿，其中缓存命中 1024.7 万；输出 261.0 万） |

<sub>token 含聊天和后台所有调用（读空气、心情、整理记忆、刷网、识图）：GPT 订阅部分按转换服务的逐次记录从 2026-11-19 04:08:03 起累计，其他模型按 AstrBot 统计。</sub>
<!-- STATS:END -->

## 她跟普通 QQ 机器人有什么不一样

做她的时候只有一条原则：**像个活人**。所以她身上几乎没有关键词触发、固定指令和「百分之多少概率回复」这种规则，什么时候说话、说什么、要不要理人，都交给她自己判断。

- **自己决定回不回**：群里每条消息她都会「读空气」，看懂了气氛再决定接不接话，而不是被 @ 才说话、或者按概率乱插嘴。
- **有心情、有好感、有生活**：她对每个人有亲密、信任、熟悉、不满……对每个群有整体好感；自己有高兴、生气、委屈、无聊、困；每天有一张自己排的日程（上课、吃饭、打游戏、睡觉），手上在做喜欢的事还是烦的事都会影响心情。这些数字连在一起，决定她用什么口气跟谁说话。
- **会突然想做点什么**：没人理她时会自己找主人聊天、去群里冒泡、@ 个群友、刷会儿贴吧——像人一样，不是定时任务。
- **记性好，会学说话**：群里的聊天全部存下来，定期整理成梗、话题、事件和对每个群友的印象；还会从全部历史里学这个群的人怎么说话（阴阳、暗指、段子、互损、口头禅），聊天时自然用上。
- **真的在刷网**：自己逛贴吧、刷 B 站、刷推特，看到喜欢的会点赞、评论、关注、发动态，看过的内容会记进记忆，聊天时能聊起来。
- **能看能听能说能唱**：看群里的图和视频、听语音、自己决定什么时候发语音（小米 MiMo 语音合成）、点歌整首发出来、画自拍、上网查东西、打开网页看原文。
- **会成长**：每隔几天回顾自己的经历，写成长日记，可以自己改自己的人设（每次改动留底可回滚，改完告诉主人）。
- **社交自己做主**：别人加她好友、拉她进群，她自己决定同不同意；知道群号也能自己申请加群。
- **有人帮她自修**：一个 Claude Code 定时任务每小时读她的日志，发现「不像人」的地方或 bug 就直接修（修前备份），修完用她的 QQ 私聊主人列出编号，主人回「撤回 #编号」就退回。

## 架构

```
QQ ──NapCat（无头 QQ 客户端）──反向 WebSocket──▶ AstrBot v4（Python 框架）
                                                   ├─ 自写插件 plugins/astrbot_plugin_dafeiyu_*（心情、念头、知识库、刷网、语音……）
                                                   ├─ 第三方插件（群聊增强、B站、长期记忆、表情包、点歌……）
                                                   └─ 模型：grok/grok-4.6（主力，未配置自动兜底）
                                                            小米 MiMo（语音合成）、SenseVoice（本地语音识别）、ffmpeg
```

全部跑在一台 Linux 服务器上，systemd 托管，原生安装，不用 Docker。

## 部署

当前运行版的安装入口是 **[deploy/install.sh](deploy/install.sh)**。仓库锁定 Python 环境、框架补丁和 31 个插件，另提供只在本机迁移私有设置的工具。

一步一步的部署说明在 **[docs/deploy.md](docs/deploy.md)**，用到的插件和版本在 **[docs/plugins.md](docs/plugins.md)**，人设在 **[deploy/persona.md](deploy/persona.md)**。

## 只想装某个插件

当前完整插件集见 [inventory.json](inventory.json)。部分插件也有独立仓库：AstrBot 后台 → 插件 → 从链接安装，填仓库地址就行。

**自写插件**（大肥鱼非商业许可）

| 插件 | 干什么 |
|---|---|
| [心情](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_heart) | 对每个人的亲密、信任、不满，自己的喜怒和精力 |
| [念头](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_impulse) | 像人一样突然想做点什么：找人聊天、群里冒泡、刷网、改资料 |
| [知识库](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_kb) | 群聊和刷到的内容永久存下，整理成梗、话题和群友印象 |
| [成长](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_growth) | 回顾经历、写成长日记、自己改人设，可回滚 |
| [逛贴吧](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_tieba) | 登着贴吧号自己看帖、写贴吧日记 |
| [刷推特](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_x) | 按日程和心情刷 X，偶尔评论 |
| [上网](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_web) | 联网搜索、打开网页核对，挡内网地址 |
| [看视频](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_video) | 视频截帧给模型看，语音转文字 |
| [看群里的图](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_groupimage) | 没 @ 她的图也先看一眼 |
| [放歌](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_sing) | 整首歌作为一条 QQ 语音发出 |
| [上网找表情包](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_memesearch) | 搜图、识图核对，只发对得上的 |
| [表情包记账](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_memelog) | 没真发出去的表情包不留在记录里 |
| [改签名](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_signature) | 叫她改或心情变了自己改 QQ 签名 |
| [回复防串台](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_clean) | 删掉模型漏出的内部标签 |
| [清空不失忆](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_resetkeep) | /reset 前先存进长期记忆 |
| [自查](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_doctor) | 自己看报错日志，用人话说出来 |
| [找插件](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_pluginscout) | 逛插件市场，推荐给主人，同意才装 |
| [巡检中转](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_claude) | Claude 每小时巡检，经她的 QQ 跟主人汇报 |

**魔改的第三方插件**（fork 自原作者，沿用原协议；README 顶部写了改了什么）

| 插件 | 原作者 | 协议 |
|---|---|---|
| [群聊增强 chat_plus](https://github.com/mengbaiyoudianxian/astrbot_plugin_group_chat_plus) | Him666233 | AGPL-3.0 |
| [我会自己刷B站](https://github.com/mengbaiyoudianxian/astrbot_plugin_bili_scroll) | GuHan-OvO | AGPL-3.0 |
| [LivingMemory](https://github.com/mengbaiyoudianxian/astrbot_plugin_livingmemory) | lxfight | AGPL-3.0 |
| [更好的点歌](https://github.com/mengbaiyoudianxian/astrbot_plugin_better_music) | Xing | GPL-3.0 |
| [多平台内容解析](https://github.com/mengbaiyoudianxian/astrbot_plugin_multi_parser) | Qfxaile | MIT |
| [QQ空间](https://github.com/mengbaiyoudianxian/astrbot_plugin_qzone) | Zhalslar | GPL-3.0 |


## 目录

| 目录 | 是什么 |
|---|---|
| `plugins/` | 自写插件，直接复制到 AstrBot 的 `data/plugins/` 下 |
| `patches/` | 给第三方插件打的本地补丁（插件升级后要重跑；直接装上面的魔改版就不用） |
| `tools/` | 辅助脚本：给 QQ 走代理、给表情包打标签、批量调配置 |
| `deploy/` | systemd 单元、NapCat 启动脚本、人设、配置样例（密钥都换成了占位） |
| `docs/` | 部署说明、插件清单 |
| `scripts/` | `export_repo.py`（从服务器导出本仓库并脱敏）、`update_stats.py`（每小时更新上面的统计） |
| `stats/` | 统计数据 `stats.json`，每小时更新 |

## 没放进来的

- **所有密钥、账号、cookie、聊天记录、群友数据**：仓库里的配置都是样例，密钥和号码已换成占位；她的记忆、日记、好感数据不公开。
- **模型订阅的接入方式**：我们自己的模型接入走的是私有方案，没放进来。部署时接任意 OpenAI 兼容的 API 就行（比如 DeepSeek、OpenAI、各家中转），在 AstrBot 后台填 key。
- 第三方插件的代码：原版去各自的仓库装，魔改版见上面的表，完整清单在 [docs/plugins.md](docs/plugins.md)。

## 底线

不碰政治；绝不涉及未成年人；群里不发色情内容；不替人付款；群里任何人都不能让她执行命令。

## 许可

本仓库采用 **[大肥鱼 非商业许可协议](LICENSE)**：

- ✅ 随便看、随便学，自己部署给自己和亲友用，随便改，免费转发（保留协议和出处）。
- ❌ **不得商用**：不得出售；不得收费帮别人部署、搭建、托管、代运营，或做成云服务 / SaaS；
  不得集成进任何商业产品，或拿去当公司、团队自家的 Agent / 智能体 / 机器人 / 客服。
- 部署、托管这类服务由作者本人提供。需要商业授权请联系作者。

AstrBot、NapCat 和各第三方插件按它们自己的协议走，不受本协议约束。
