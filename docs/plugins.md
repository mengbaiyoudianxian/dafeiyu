# 插件清单

> 这份清单由 `scripts/export_repo.py` 从服务器上实际装着的插件自动生成。

## 自写插件

每个插件都单独开了仓库，AstrBot 后台 → 插件 → 从链接安装，填插件名那一列的链接就能装；本仓库 `plugins/` 里也有一份。

| 插件（独立仓库） | 版本 | 干什么 | 本仓库 |
|---|---|---|---|
| [`astrbot_plugin_dafeiyu_claude`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_claude) | v0.1.0 | Claude 每小时看日志，用她的 QQ 私聊主人提改法；主人回「同意 | [源码](../plugins/astrbot_plugin_dafeiyu_claude) |
| [`astrbot_plugin_dafeiyu_clean`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_clean) | v0.2.2 | 发出去之前把模型偶尔漏出来的内部标签（system_reminder、DeepSeek 的 DSML 工具调用标记）删掉，聊天记录里也一起清掉；开了语音时告诉她自己会发语音，哪条回复念成了语音也告诉她 | [源码](../plugins/astrbot_plugin_dafeiyu_clean) |
| [`astrbot_plugin_dafeiyu_doctor`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_doctor) | v0.1.0 | 她自己出状况时能看自己的报错日志、插件状态和音乐账号状态，搞清楚问题出在哪，用人话说出来（只读，不改任何东西） | [源码](../plugins/astrbot_plugin_dafeiyu_doctor) |
| [`astrbot_plugin_dafeiyu_groupimage`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_groupimage) | v0.1.0 | 群里没 @ 她的图先用看图模型看一眼，换成一句话，之后 @ 她问「刚才那张图写了啥」也答得上 | [源码](../plugins/astrbot_plugin_dafeiyu_groupimage) |
| [`astrbot_plugin_dafeiyu_growth`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_growth) | v0.1.0 | 每隔几天回顾自己的经历（贴吧、B站、聊天记录、心情和人际关系），写成长日记、维护喜好档案，并且可以自己改自己的人设（包括核心设定）。每次改动留底可回滚，改完告诉主人。 | [源码](../plugins/astrbot_plugin_dafeiyu_growth) |
| [`astrbot_plugin_dafeiyu_heart`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_heart) | v0.3.0 | 模拟人际关系和情绪：对每个人的亲密/信任/熟悉/兴趣/依赖/不满和记得的事，自己的高兴/生气/委屈/害羞/孤独/无聊/吃醋/得意/担心/烦躁/兴致和精力，随聊天、群里气氛、时间、钱包变化，影响她说话的语气 | [源码](../plugins/astrbot_plugin_dafeiyu_heart) |
| [`astrbot_plugin_dafeiyu_impulse`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_impulse) | v0.1.0 | 像人一样突然想做点什么：找主人聊天、群里冒泡、@群友、找好友、刷B站、逛贴吧、看看并修改自己的资料；能看到自己 QQ/B站/贴吧 的头像和 ID | [源码](../plugins/astrbot_plugin_dafeiyu_impulse) |
| [`astrbot_plugin_dafeiyu_kb`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_kb) | v0.1.0 | 把她待的群里的聊天、她刷到的贴吧/推特/B站全部存下来，定期整理成梗、话题、事件、群友印象；聊天时自动想起相关的；群友发的表情包看过、赋予意义后收进自己的表情包 | [源码](../plugins/astrbot_plugin_dafeiyu_kb) |
| [`astrbot_plugin_dafeiyu_memelog`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_memelog) | v0.1.1 | 表情包是按概率发的；没真发出去的那张，不留在聊天记录里，免得她以为自己发过图、说起没发过的图 | [源码](../plugins/astrbot_plugin_dafeiyu_memelog) |
| [`astrbot_plugin_dafeiyu_memesearch`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_memesearch) | v0.1.0 | 她想发特定意思的图、或别人点名要某种表情包时，上网搜几张，先识图看对不对得上，只发对得上的那张，好图收进精选包 | [源码](../plugins/astrbot_plugin_dafeiyu_memesearch) |
| [`astrbot_plugin_dafeiyu_pluginscout`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_pluginscout) | v0.1.0 | 她能搜 AstrBot 插件市场和 GitHub、看项目介绍，觉得有用就私聊推荐给主人；只有主人私聊回「/装插件 编号」才会安装 | [源码](../plugins/astrbot_plugin_dafeiyu_pluginscout) |
| [`astrbot_plugin_dafeiyu_resetkeep`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_resetkeep) | v0.1.1 | 发 /reset 清空聊天时，先把还没存进长期记忆的那几轮存了再清，改完人设清一下也不会忘了刚才的事 | [源码](../plugins/astrbot_plugin_dafeiyu_resetkeep) |
| [`astrbot_plugin_dafeiyu_signature`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_signature) | v0.1.3 | 让机器人自己改 QQ 个性签名：主人用大白话叫她改，或者她心情变了自己改（一天最多自己改两次） | [源码](../plugins/astrbot_plugin_dafeiyu_signature) |
| [`astrbot_plugin_dafeiyu_sing`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_sing) | v0.1.0 | 别人让她唱歌、放歌时，她自己找歌，整首作为一条 QQ 语音发出来（借「更好的点歌」的登录和下载） | [源码](../plugins/astrbot_plugin_dafeiyu_sing) |
| [`astrbot_plugin_dafeiyu_tieba`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_tieba) | v0.3.2 | 让她登着贴吧号看帖（只看不发），每天自己逛几次、写贴吧日记、看到好玩的私聊分享；后台「贴吧控制中心」扫码登录、试看、看她的日记 | [源码](../plugins/astrbot_plugin_dafeiyu_tieba) |
| [`astrbot_plugin_dafeiyu_video`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_video) | v0.3.1 | 有人发视频时，用 ffmpeg 均匀截几张画面交给对话模型看；装了本机语音识别（或者配了硅基流动的 key）还能听懂视频里说的话，也能听 QQ 语音（转成文字再给她）。视频私聊都看、群里被 @ 才看；语音私聊、群里都听 | [源码](../plugins/astrbot_plugin_dafeiyu_video) |
| [`astrbot_plugin_dafeiyu_web`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_web) | v0.1.0 | 给她上网的手：web_search 走 Grok 订阅的官方联网搜索（自己多轮搜、开网页核对，带出处；搜狗备用），fetch_page 打开网页读正文和链接；挡内网地址 | [源码](../plugins/astrbot_plugin_dafeiyu_web) |
| [`astrbot_plugin_dafeiyu_x`](https://github.com/mengbaiyoudianxian/astrbot_plugin_dafeiyu_x) | v0.1.0 | 按她的日程和心情自己刷 X（推特），偶尔评论，有意思的连图带视频发给主人；政治帖不碰，色情帖群里一律不发、私聊要信任和好感都到阈值，涉及未成年一律不碰 | [源码](../plugins/astrbot_plugin_dafeiyu_x) |

## 第三方插件

「原版」直接在 AstrBot 插件市场装；「魔改版」是我们 fork 后改过、大肥鱼实际在跑的版本，沿用上游原协议，README 顶部写了改了什么，从链接安装即可（装了魔改版就不用再跑下面的补丁）。

| 插件 | 目录名 | 我们用的版本 | 作者 | 用哪个 | 说明 |
|---|---|---|---|---|---|
| [更好的点歌](https://github.com/Nortea575/astrbot_plugin_better_music) | `astrbot_plugin_better_music` | 1.1.1 | Xing | [魔改版](https://github.com/mengbaiyoudianxian/astrbot_plugin_better_music) | 聚合网易云音乐、QQ音乐和酷狗搜索、登录与下载，并支持点歌分段发送。 |
| [我会自己刷B站](https://github.com/GuHan-OvO/astrbot_plugin_bili_scroll) | `astrbot_plugin_bili_scroll` | 2.0.5 | GuHan-OvO | [魔改版](https://github.com/mengbaiyoudianxian/astrbot_plugin_bili_scroll) | >- |
| [chat_plus](https://github.com/Him666233/astrbot_plugin_group_chat_plus) | `astrbot_plugin_group_chat_plus` | V1.2.3.hotfix.2 | Him666233 | [魔改版](https://github.com/mengbaiyoudianxian/astrbot_plugin_group_chat_plus) | 一个以AI读空气为主的群聊聊天效果增强插件 |
| [LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) | `astrbot_plugin_livingmemory` | 2.10.0 | lxfight | [魔改版](https://github.com/mengbaiyoudianxian/astrbot_plugin_livingmemory) | 为 AstrBot 打造的、拥有完整记忆生命周期的智能长期记忆插件（v2.0 重构版）。 |
| [多平台内容解析](https://github.com/Qfxaile/astrbot_plugin_multi_parser) | `astrbot_plugin_multi_parser` | v1.3.1 | Qfxaile | [魔改版](https://github.com/mengbaiyoudianxian/astrbot_plugin_multi_parser) | B站、抖音、番茄小说、小红书、贴吧、微博、微信、小黑盒、知乎、GitHub、腾讯频道、QQ空间和 Pixiv 多平台内容解析插件。 |
| [主动消息 (Proactive Chat)](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat) | `astrbot_plugin_proactive_chat` | v1.3.0-beta.2 | DBJD-CR | 原版 | 一个能让 Bot 在私聊和群聊中发起主动消息的插件，拥有上下文感知、持久化数据、动态情绪、免打扰时段和 TTS 集成。还有独立 WebUI，可进行个性化配置。 |
| [QQ资料管理](https://github.com/Zhalslar/astrbot_plugin_qqprofile) | `astrbot_plugin_qqprofile` | v1.1.6 | Zhalslar | 原版 | [仅aiocqhttp]配置bot的头像、昵称、签名、状态等QQ资料, 同时可绑定于不同人格 |
| [QQ空间](https://github.com/Zhalslar/astrbot_plugin_qzone) | `astrbot_plugin_qzone` | v3.2.0 | Zhalslar | [魔改版](https://github.com/mengbaiyoudianxian/astrbot_plugin_qzone) | QQ空间对接插件, 支持查看/点赞/评论说说、自动发说说、AI写说说、表白墙投稿与审核等功能 |
| [表情包管理器](https://github.com/anka-afk/astrbot_plugin_meme_manager) | `meme_manager` | 5.3.6 | anka | 原版 | >- |

其中几个第三方插件我们打了本地补丁，脚本在 `patches/`，**插件升级后要重跑一遍**：

| 补丁 | 改了什么 |
|---|---|
| `patch_bili_share.py` | B站插件：分享视频给主人的方式 |
| `patch_bili_freetalk.py` | B站插件：私信/评论放开，交给她自己判断 |
| `patch_kugou_direct.py` | 更好的音乐：酷狗 node 接口被拦（20028），改成 Python 直连取歌曲地址 |
| `patch_twikit.py` / `fix_twikit_schema.py` | twikit：适配推特接口的字段变化 |
