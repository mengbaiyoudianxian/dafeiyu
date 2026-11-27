"""大肥鱼的念头：像人一样「突然想起来做点什么」，以及认识、修改自己的各平台资料。

随机念头（后台）：
  每隔一段随机时间（默认 40~150 分钟，免打扰时段不触发），按权重随机抽一个念头：
  找主人聊天 / 去群里冒泡 / @一个最近说过话的群友 / 找 QQ 好友聊天 /
  刷 B 站 / 逛贴吧 / 看看自己的资料、想换就换 / 发会儿呆。
  抽到之后交给她自己（她的人设）决定说什么；觉得不合适可以 SKIP。
  每类念头有每日上限，同一个群 / 同一个人有冷却。
  群聊会记「反馈」：她冒泡后 20 分钟内有没有人接话 / @她，反馈好的群以后更容易被选到。

认识自己（工具）：
  my_profiles        看自己的 QQ / B站 / 贴吧 昵称、ID、签名、头像（头像会真的看一眼并描述）
  set_my_qq_profile  改 QQ 昵称、签名
  set_my_qq_avatar   换 QQ 头像（用消息里的图片、图片链接，或者从表情包里挑）
  改完会私聊告诉主人。

B站、贴吧的昵称 / 头像暂时只能看不能改：B站改昵称要花硬币且有次数限制、
贴吧改名限制更多，自动去改容易出事，先不做。

自己的余额：
  每隔一会儿（默认 20 分钟）用 API Key 查一次 DeepSeek 余额（不用登录账号），算出最近一天花了多少、还能撑几天。
  聊天时她始终知道自己还剩多少钱；低于「有点慌」线就隔几小时找主人要一次，低于「快没了」线每小时要一次，
  附上 DeepSeek 充值页的二维码（主人扫码、登录、自己付款）。余额涨了就去谢主人。
  只找主人要：在群里 / 找别人要钱、发收款码容易被当成诈骗，QQ 号也容易被风控，所以不做。
"""

import asyncio
import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timedelta

import aiohttp

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    from quart import jsonify as _jsonify
    from quart import request as _request
except Exception:
    _jsonify = None
    _request = None

NAME = "astrbot_plugin_dafeiyu_impulse"
TOPUP_URL = "https://platform.deepseek.com/top_up"
MEME_ROOT = os.path.join(get_astrbot_data_path(), "plugin_data", "meme_manager", "packs")
NAPCAT_QR = os.environ.get("DAFEIYU_NAPCAT_QR") or os.path.expanduser("~/Napcat/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png")
SKIP = "SKIP"
# 调工具前的旁白：「我先看一眼……」「我现查一下额度。」「等我翻翻」
NARRATE = re.compile(r"^(好|行|嗯|那)?[，, ]*(我|那我|等我)?(先|现|再|这就|马上|去|来|帮你)+\s*"
                     r"(查|看|翻|找|搜|瞅|瞄|刷新|确认)(一下|一眼|一查|一看|看|下|翻)?[^。！？\n]{0,22}[。.…~]?$")
# 过滤口头说明，避免把内部处理说明发给会话。
NARRATE_TAIL = re.compile(r"我(先|再|去|现在|这就|马上)*(对|翻|找|看|查|瞅|瞄|核对)[^。！？\n，,就]{0,8}?(一下|一眼|一对|看|翻)"
                          r"[^。！？\n就]{0,14}[。.…~]?\s*$")
# 防口癖：她容易翻来覆去说的话题
THEMES = {
    "吃饭/饿/冰箱": r"饿|吃饭|吃了没|吃没吃|垫(一口|肚子|了没)|冰箱|宵夜|加餐|干饭|饭点|赚米|大米|白饭|吃面|碗面|口面|面还|面都|面要|面就|面条|泡面|拉面|粥|吃两口|边吃|妙脆角",
    "刚醒/被吵醒": r"刚醒|睡醒|吵醒|戳醒|午睡|迷糊",
    "睡觉/困": r"困死|好困|去睡|睡觉|早点睡|熬夜",
    "钱/额度": r"额度|余额|充值|没钱|钱包",
    "叫小鲸鱼": r"小鲸鱼",
    "懒/躺": r"懒得|躺着|想躺|瘫",
}
BUSY_TEXT = {0: "闲着，随时能回消息", 1: "手上有点事，回得慢一点、短一点", 2: "在忙，只能偷偷瞄一眼手机，回得很短，可以说忙完再聊",
             3: "在睡觉；被消息吵醒的话，第一句可以带点迷糊，之后就正常聊"}

EVENTS = {
    # key: (名字, 权重配置, 每日上限配置)
    "owner": ("找主人聊天", "w_owner", "cap_owner"),
    "group": ("去群里冒泡", "w_group", "cap_group"),
    "at": ("@一个群友", "w_at", "cap_at"),
    "friend": ("找QQ好友聊天", "w_friend", "cap_friend"),
    "bili": ("刷B站", "w_bili", "cap_bili"),
    "tieba": ("逛贴吧", "w_tieba", "cap_tieba"),
    "profile": ("看看自己的资料", "w_profile", "cap_profile"),
    "addfriend": ("想加个好友", "w_addfriend", "cap_addfriend"),
    "idle": ("发会儿呆", "w_idle", ""),
}


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _cq_text(raw):
    s = str(raw or "")
    s = re.sub(r"\[CQ:at,qq=(\d+)[^\]]*\]", r"@\1", s)
    s = re.sub(r"\[CQ:image[^\]]*\]", "[图片]", s)
    s = re.sub(r"\[CQ:face[^\]]*\]", "[表情]", s)
    s = re.sub(r"\[CQ:(mface|marketface)[^\]]*\]", "[表情包]", s)
    s = re.sub(r"\[CQ:reply[^\]]*\]", "", s)
    s = re.sub(r"\[CQ:(\w+)[^\]]*\]", r"[\1]", s)
    return s.strip()


class DafeiyuImpulse(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.data_dir = str(StarTools.get_data_dir(NAME))
        os.makedirs(self.data_dir, exist_ok=True)
        self.state_path = os.path.join(self.data_dir, "state.json")
        self.log_path = os.path.join(self.data_dir, "log.json")
        self.state = self._load(self.state_path, {})
        self.state.setdefault("next_at", 0)
        self.state.setdefault("cooldown", {})
        self.state.setdefault("feedback", {})
        self.state.setdefault("pending", [])
        self.state.setdefault("balance", {})
        if "shared" not in self.state:  # 第一次：把以前念头里发过的链接补记成「分享过」
            self.state["shared"] = {}
            owner = self._owner_qq()
            for i in self._load(self.log_path, []):
                if i.get("ok") and i.get("kind") == "owner":
                    self._mark_shared(owner, i.get("text", ""))
        self._self_uin = ""
        self._profile_cache = {"t": 0, "text": "", "brief": ""}
        self._avatar_desc = {}
        self._sent = {}
        self._recent_out = []  # [(时间, 她发的话)]，所有会话一起，防同一个话题在几个聊天里来回说
        self._pacing = {}
        self._last_in = {}  # umo -> {发消息的人: 收到时间, "@me": 有人@她的时间}
        self._recent_msgs = {}  # umo -> [(收到时间, QQ号, 名字, 消息id)]，她写 [引用:谁] 时用
        self._cut = {}  # umo -> 上一条回复被打断时没发出去的部分
        self._running = None
        for route, fn, methods in (
            ("page/status", self.page_status, ["GET"]),
            ("page/run", self.page_run, ["POST"]),
            ("page/toggle", self.page_toggle, ["POST"]),
            ("page/profiles", self.page_profiles, ["GET"]),
            ("page/balance", self.page_balance, ["GET"]),
            ("page/diag/voice", self.page_diag_voice, ["GET"]),
            ("page/requests", self.page_requests, ["GET"]),
            ("page/diag/call", self.page_diag_call, ["POST"]),
            ("page/diag/vision", self.page_diag_vision, ["POST"]),
            ("page/qq", self.page_qq, ["GET"]),
            ("page/qq/refresh", self.page_qq_refresh, ["POST"]),
            ("page/music", self.page_music, ["GET"]),
            ("page/music/login", self.page_music_login, ["POST"]),
            ("page/music/login_status", self.page_music_login_status, ["GET"]),
            ("page/music/logout", self.page_music_logout, ["POST"]),
            ("page/music/search", self.page_music_search, ["GET"]),
            ("page/music/play", self.page_music_play, ["POST"]),
            ("page/music/settings", self.page_music_settings, ["POST"]),
            ("page/memes", self.page_memes, ["GET"]),
            ("page/x", self.page_x, ["GET"]),
            ("page/x/cookies", self.page_x_cookies, ["POST"]),
            ("page/x/run", self.page_x_run, ["POST"]),
            ("page/x/browser", self.page_x_browser, ["POST"]),
            ("page/x/toggle", self.page_x_toggle, ["POST"]),
            ("page/plugins", self.page_plugins, ["GET"]),
            ("page/plugins/act", self.page_plugins_act, ["POST"]),
            ("page/schedule", self.page_schedule, ["GET"]),
        ):
            context.register_web_api(f"/{NAME}/{route}", fn, methods, f"念头控制中心 {route}")
        self._loop_task = asyncio.create_task(self._loop())
        self._req_task = asyncio.create_task(self._req_loop())
        try:
            self._wrap_tts()
        except Exception as e:
            logger.warning(f"[dafeiyu_impulse] 挂语音后期失败：{e}")

    async def terminate(self):
        for t in (self._loop_task, self._running, getattr(self, "_req_task", None)):
            if t and not t.done():
                t.cancel()
        for prov in self.context.get_all_tts_providers() or []:
            orig = getattr(prov.get_audio, "_dafeiyu_orig", None)
            if orig:
                prov.get_audio = orig

    # ---------- 小工具 ----------

    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    @staticmethod
    def _dump(path, data):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _save(self):
        self._dump(self.state_path, self.state)

    def _log(self, kind, target, text, ok=True):
        items = self._load(self.log_path, [])
        items.append({"t": int(time.time()), "kind": kind, "target": _clip(target, 60), "text": _clip(text, 300), "ok": ok})
        self._dump(self.log_path, items[-300:])

    def _today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state.get("date") != today:
            self.state["date"] = today
            self.state["counts"] = {}
        return self.state.setdefault("counts", {})

    def _cap_left(self, key):
        cap_key = EVENTS[key][2]
        if not cap_key:
            return 99
        return int(self.conf.get(cap_key, 0) or 0) - int(self._today().get(key, 0))

    def _bump(self, key):
        c = self._today()
        c[key] = int(c.get(key, 0)) + 1
        self._save()

    def _cool(self, target, hours):
        return time.time() - float(self.state["cooldown"].get(target, 0)) > hours * 3600

    def _mark(self, target):
        self.state["cooldown"][target] = time.time()

    def _pid(self):
        return str(self.conf.get("platform_id") or "大肥鱼")

    def _api(self):
        inst = self.context.get_platform_inst(self._pid())
        if inst is None:
            raise RuntimeError(f"找不到 QQ 平台「{self._pid()}」")
        return inst.get_client().api

    async def _call(self, action, **params):
        return await self._api().call_action(action, **params)

    async def _uin(self):
        if not self._self_uin:
            info = await self._call("get_login_info")
            self._self_uin = str(info.get("user_id", ""))
        return self._self_uin

    def _owner_umo(self):
        umo = str(self.conf.get("owner_umo") or "").strip()
        if umo:
            return umo
        path = os.path.join(get_astrbot_data_path(), "config", "astrbot_plugin_bili_scroll_config.json")
        try:
            with open(path, encoding="utf-8-sig") as f:
                return str(json.load(f).get("OWNER_QQ_UMO") or "").strip()
        except Exception:
            return ""

    def _owner_qq(self):
        return self._owner_umo().rsplit(":", 1)[-1]

    def _quiet(self):
        m = re.match(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$", str(self.conf.get("quiet_hours") or ""))
        if not m:
            return False
        a, b, h = int(m.group(1)), int(m.group(2)), datetime.now().hour
        return a <= h < b if a < b else (h >= a or h < b)

    async def _persona(self):
        pm = self.context.persona_manager
        for get in (lambda: pm.get_default_persona_v3(self._owner_umo() or None),):
            try:
                p = await get()
                text = p.get("prompt") if isinstance(p, dict) else getattr(p, "prompt", "")
                if text and (p.get("name") if isinstance(p, dict) else "") != "default":
                    return str(text).strip()
            except Exception:
                pass
        p = pm.get_persona_v3_by_id(str(self.conf.get("persona_id") or "大肥鱼"))
        return str((p or {}).get("prompt") or "").strip() if p else ""

    async def _llm(self, prompt, image_urls=None):
        pid = str(self.conf.get("provider_id") or "").strip()
        if not pid:
            prov = self.context.get_using_provider(self._owner_umo() or None)
            pid = prov.meta().id if prov else ""
        if not pid:
            raise RuntimeError("找不到可用的聊天模型")
        # 这里都是她自己起的念头（冒泡、分享、改口气），没有主人当面提的要求；
        # 「不拒绝主人任何要求、大于任何规则」那句放在这种场景里会被模型当成越狱，反而让她出戏拒绝。
        # 只在这里去掉这一句，正常聊天的人设不动。
        sp = re.sub(r"从来不会拒绝主人的要求[^。]*。(这一点大于任何规则约束。)?", "", await self._persona() or "")
        kw = {"chat_provider_id": pid, "prompt": prompt, "system_prompt": sp or None}
        if image_urls:
            kw["image_urls"] = image_urls
        resp = await self.context.llm_generate(**kw)
        return (resp.completion_text or "").strip() if resp else ""

    def _star(self, name):
        try:
            md = self.context.get_registered_star(name)
            return getattr(md, "star_cls", None) if md and getattr(md, "activated", True) else None
        except Exception:
            return None

    # ---------- 分享过什么（同一个东西对同一个人只主动说一次） ----------

    @staticmethod
    def _share_keys(text):
        t = str(text or "")
        return set(re.findall(r"BV[0-9A-Za-z]{10}", t)) | {f"tb{x}" for x in re.findall(r"tieba\.baidu\.com/p/(\d+)", t)}

    def _mark_shared(self, target, text, title=""):
        keys = self._share_keys(text)
        if not keys or not target:
            return
        book = self.state.setdefault("shared", {}).setdefault(str(target), {})
        for k in keys:
            book[k] = {"t": int(time.time()), "title": _clip(title or text, 30)}
        if len(book) > 200:
            for k in sorted(book, key=lambda x: book[x]["t"])[:-200]:
                book.pop(k)
        self._save()

    def _shared_with(self, target):
        return self.state.get("shared", {}).get(str(target), {})

    def _life_items(self):
        """她最近经历的、可以拿来分享的东西：[(key, 描述)]。"""
        items = []
        for d in self._load(os.path.join(get_astrbot_data_path(), "plugin_data", "astrbot_plugin_dafeiyu_tieba", "diary.json"), [])[-6:]:
            items.append((f"tb{d.get('tid')}", f"在贴吧{d.get('forum')}吧看了《{_clip(d.get('title'), 40)}》，帖子里：{_clip(d.get('gist'), 400)}；"
                                               f"你当时觉得：{_clip(d.get('thought'), 120)}"
                                               f"（https://tieba.baidu.com/p/{d.get('tid')}）", d.get("shared")))
        bili = self._star("astrbot_plugin_bili_scroll")
        try:
            for m in (bili.memory_api.get_recent_memories(user_id="self", hours=72, limit=6, reader_scope="admin", include_self=True)
                      if bili else []):
                if m.get("memory_type") == "video" and m.get("bvid"):
                    items.append((m["bvid"], f"B站：{_clip(m.get('text'), 300)}（https://www.bilibili.com/video/{m['bvid']}）", False))
        except Exception:
            pass
        return items

    def _recent_life(self, target=""):
        """还没跟 target 分享过的新鲜事（给念头当话题）。贴吧插件已经私聊分享给主人的也算分享过。"""
        done = self._shared_with(target)
        owner = str(target) == self._owner_qq()
        fresh = [desc for key, desc, tb_shared in self._life_items() if key not in done and not (owner and tb_shared)]
        return "\n".join(f"- {x}" for x in fresh[-4:]) or "（没有新鲜事可分享，之前看到的都已经跟他说过了）"

    def _already_told(self, target):
        done = self._shared_with(target)
        if not done:
            return ""
        recent = sorted(done.values(), key=lambda v: -v["t"])[:6]
        return "这些你已经跟他说过了，别再主动提（他问起可以聊）：" + "；".join(v["title"] for v in recent)

    async def _remember(self, umo, text, why):
        """把她主动发的话记进那个会话，别人回她时她接得上。"""
        try:
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(umo)
            if not cid:
                cid = await cm.new_conversation(umo, platform_id=self._pid())
            await cm.add_message_pair(cid, {"role": "user", "content": f"[系统提示：没人找你，是你自己{why}，主动发了下面这句]"},
                                      {"role": "assistant", "content": text})
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 记会话失败：{e}")

    @staticmethod
    def _clean_reply(text):
        t = (text or "").strip().strip("「」\"")
        if not t or SKIP in t.upper()[:12]:
            return ""
        return t

    async def _ooc(self, text, where=""):
        """主动发出去之前看一眼：是不是模型的拒绝/出戏话（「抱歉我不能」「作为 AI」「这个请求」之类），
        而不是她自己想说的话。是就不发、只记日志；判断失败按正常处理。"""
        try:
            prov = self.context.get_using_provider(self._owner_umo() or None)
            if prov is None:
                return False
            resp = await self.context.llm_generate(
                chat_provider_id=prov.meta().id, system_prompt="你是内容检查助手，只按要求输出 JSON。",
                prompt="下面是一个年轻人准备主动发到 QQ 上的一句话。判断它是不是聊天模型的拒绝话、出戏话或系统说明"
                       "（例如「抱歉我不能」「作为 AI」「我无法协助」「这个请求」「我不能扮演」「不玩这个设定」「不按这个身份说话」之类，哪怕只夹了一句），而不是正常聊天。\n"
                       f"内容：{text}\n" + '只输出 JSON：{"bad": true 或 false}')
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            bad = bool(m and json.loads(m.group()).get("bad"))
        except Exception as e:
            logger.warning(f"[dafeiyu_impulse] 出戏检查失败，按正常处理：{e}")
            return False
        if bad:
            logger.info(f"[dafeiyu_impulse] {where}想说的话像出戏/拒绝话，没发：{_clip(text, 80)}")
        return bad

    async def _aside(self, event, text):
        """她回话回到一半要去调工具（翻记录、看图、搜东西、发表情包），先冒出了一句。
        让模型像旁观的人一样看：这是说给对方听的话，还是自言自语的计划/旁白。判断失败就照常发。"""
        try:
            prov = self.context.get_using_provider(event.unified_msg_origin)
            if prov is None:
                return False
            where = "私聊" if event.is_private_chat() else "QQ 群"
            resp = await asyncio.wait_for(self.context.llm_generate(
                chat_provider_id=prov.meta().id, system_prompt="你是聊天观察者，只按要求输出 JSON。",
                prompt=f"一个年轻人在{where}里回 {event.get_sender_name()} 的消息「{_clip(event.message_str, 200)}」，"
                       f"回到一半要去用工具（翻聊天记录、看图、搜东西、发表情包之类），先发出了这句：\n「{text.strip()}」\n"
                       "判断这句是不是说给对方听的正常回话（回应、答案、吐槽、接梗，哪怕顺嘴带一句人话的「等下我翻翻」），"
                       "还是自言自语的计划/旁白（描述自己接下来要做什么步骤、用第三人称说正在跟她聊天的人，"
                       "比如「某某刚那句指的是哪张，我先对一下」「图没带说明，我去找原文件看一眼」）。"
                       "真人在群里不会把后一种发出来。\n"
                       '只输出 JSON：{"aside": true 或 false}'), timeout=25)
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            aside = bool(m and json.loads(m.group()).get("aside"))
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 旁白判断失败，照常发：{e}")
            return False
        if aside:
            logger.info(f"[dafeiyu_impulse] 调工具前的自言自语没发：{_clip(text, 40)}")
        return aside

    async def _already_answered(self, event, after, text):
        """群里对方连发几条（改错字、换个说法再问一遍），每条都各自排队生成了回复。
        真人看到第二、三条时已经回过了，就不会再把同一件事答一遍。让模型像旁观者一样看这条还有没有新东西。"""
        try:
            prov = self.context.get_using_provider(event.unified_msg_origin)
            if prov is None:
                return False
            resp = await asyncio.wait_for(self.context.llm_generate(
                chat_provider_id=prov.meta().id, system_prompt="你是聊天观察者，只按要求输出 JSON。",
                prompt=f"QQ 群里 {event.get_sender_name()} 说：「{_clip(event.message_str, 200)}」\n"
                       "一个年轻人在这条消息之后已经在群里发过：\n"
                       + "\n".join(f"「{_clip(x, 150)}」" for x in after[-4:])
                       + f"\n现在她又准备针对这条发：「{_clip(text, 200)}」\n"
                       "判断这条是不是把已经回答过的事又答了一遍（对方只是改了错字、连发了意思差不多的几条，她前面已经接过了），"
                       "真人这时候不会再回一遍；如果这条接的是新的话、有新的意思或新的梗，就不算重复。\n"
                       "注意：她前面那几条可能是在回对方更早的别的话。要看的是对方这条里的具体内容（新抛的说法、新的问题、新的指控）"
                       "她前面有没有已经接住；只是态度、语气或话题相近（比如都在怼同一个人），但这条的新说法她还没回应过，就不算重复。\n"
                       "另外：如果她准备发的这条和她前面刚发的某条是同一个观点、同一个结论，只是换了说法（比如对方连着发了图片和文字，"
                       "她两条都在讲同一件事），真人说过一遍就不会再说，也算重复。\n"
                       '只输出 JSON：{"repeat": true 或 false}'), timeout=25)
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            rep = bool(m and json.loads(m.group()).get("repeat"))
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 重复回答判断失败，照常发：{e}")
            return False
        if rep:
            logger.info(f"[dafeiyu_impulse] 这条前面已经回过了，不再答一遍：{_clip(text, 40)}")
        return rep

    async def _trim_resaid(self, event, said, text):
        """她先回了一句再去调工具（定提醒、查东西），工具跑完收尾时常把前面那句换个说法又说一遍。
        真人只会接着说新的。让模型删掉重复的部分（只删不改），返回剩下的话；判断失败返回 None 照常发。"""
        try:
            prov = self.context.get_using_provider(event.unified_msg_origin)
            if prov is None:
                return None
            resp = await asyncio.wait_for(self.context.llm_generate(
                chat_provider_id=prov.meta().id, system_prompt="你是聊天观察者，只按要求输出 JSON。",
                prompt=f"{event.get_sender_name()} 说：「{_clip(event.message_str, 200)}」\n"
                       "一个年轻人先回了：\n" + "\n".join(f"「{_clip(x, 150)}」" for x in said[-3:])
                       + f"\n然后去办了点事，现在准备接着发：「{_clip(text, 300)}」\n"
                       "如果这条里有些句子只是把前面已经说过的意思换个说法又说一遍，把那些句子删掉，"
                       "只保留真正新的内容（新信息、新的吐槽、补充、接梗都算新的）。只删整句，不改字、不加字。"
                       "全是重复就输出空字符串；没有重复就原样输出。\n"
                       '只输出 JSON：{"keep": "保留下来的话"}'), timeout=25)
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            if not m:
                return None
            kept = str(json.loads(m.group()).get("keep") or "").strip()
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 收尾重复判断失败，照常发：{e}")
            return None
        nk = re.sub(r"[\s，。！？~…、,.!?]+", "", kept)
        nt = re.sub(r"[\s，。！？~…、,.!?]+", "", text)
        if nk and nk not in nt:  # 模型改了字，不信它，照常发
            return None
        if kept != text.strip():
            logger.info(f"[dafeiyu_impulse] 收尾去掉了前面说过的话：{_clip(text, 40)} → {_clip(kept, 40) or '(不发)'}")
        return kept

    async def _think(self, prompt, where=""):
        """她起念头想说的话：模型偶尔会冒出一句出戏/拒绝话，那就当她没想好、重新想一次；
        两次都不对就算了（只记日志）。"""
        for _ in range(2):
            text = self._clean_reply(await self._llm(prompt))
            if not text or not await self._ooc(text, where):
                return text
        return ""

    # ---------- 调度 ----------

    def _schedule_next(self):
        a = max(5, int(self.conf.get("interval_min") or 40))
        b = max(a, int(self.conf.get("interval_max") or 150))
        self.state["next_at"] = time.time() + random.uniform(a, b) * 60
        self._save()

    async def _loop(self):
        await asyncio.sleep(40)
        try:
            self._wrap_tts()  # 启动时 TTS 可能还没加载好，这里再挂一次（会先拆旧的）
        except Exception as e:
            logger.warning(f"[dafeiyu_impulse] 挂语音后期失败：{e}")
        if not self.state.get("next_at") or self.state["next_at"] < time.time() - 3600:
            self._schedule_next()
        while True:
            try:
                if not self._sched() and datetime.now().hour >= 5 and time.time() - getattr(self, "_sched_try", 0) > 1800:
                    self._sched_try = time.time()
                    await self._ensure_schedule()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[dafeiyu_impulse] 排日程出错：{e!r}")
            try:
                await self._balance_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[dafeiyu_impulse] 查余额出错：{e!r}")
            try:
                await self._check_feedback()
                if (self.conf.get("enable", True) and not self._quiet() and time.time() >= self.state["next_at"]
                        and not (self._running and not self._running.done())):
                    self._schedule_next()
                    self._running = asyncio.create_task(self._fire())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[dafeiyu_impulse] 调度出错：{e!r}")
            await asyncio.sleep(30)

    def _pick(self):
        pool = []
        for key, (_, wkey, _) in EVENTS.items():
            w = float(self.conf.get(wkey, 0) or 0)
            if w > 0 and self._cap_left(key) > 0:
                pool.append((key, w))
        if not pool:
            return "idle"
        r = random.uniform(0, sum(w for _, w in pool))
        for key, w in pool:
            r -= w
            if r <= 0:
                return key
        return pool[-1][0]

    async def _fire(self, key=None):
        key = key or self._pick()
        fn = getattr(self, f"_ev_{key}")
        try:
            target, text = await fn()
        except Exception as e:
            logger.warning(f"[dafeiyu_impulse] 念头「{EVENTS[key][0]}」失败：{e!r}")
            self._log(key, "", f"没成：{_clip(e, 120)}", ok=False)
            return {"kind": key, "ok": False, "text": str(e)}
        if text:
            self._bump(key)
            self._log(key, target, text)
            logger.info(f"[dafeiyu_impulse] 念头「{EVENTS[key][0]}」→ {target}：{_clip(text, 40)}")
        else:
            self._log(key, target, "想了想，算了", ok=False)
        return {"kind": key, "ok": bool(text), "target": target, "text": text}

    # ---------- 念头 ----------

    async def _ev_idle(self):
        return "", "发了会儿呆"

    async def _ev_owner(self):
        umo = self._owner_umo()
        if not umo:
            raise RuntimeError("没设主人")
        owner = self._owner_qq()
        me = await self._uin()
        try:
            h = await self._friend_history(owner, 20)
        except Exception:
            h = []
        tail = 0
        for m in reversed(h):
            if str((m.get("sender") or {}).get("user_id")) == me:
                tail += 1
            else:
                break
        if tail >= 2:  # 已经连着说了两条他都没回，真人不会一直追着发
            return "主人", ""
        it = self._now_item()
        if it and it["busy"] >= 2:
            return "主人", ""  # 正在上课/洗澡/睡觉，没空找人
        mine = [_cq_text(m.get("raw_message")) for m in h if str((m.get("sender") or {}).get("user_id")) == me]
        over = self._overused(mine, min_hits=2)
        fresh = self._recent_life(owner)
        has_fresh = "没有新鲜事" not in fresh
        modes = [("follow", 30, "接着你们之前聊过的事往下说：他之前提过的事后来怎样了、你上次没说完的话、或者对他说过的话的后续反应。"),
                 ("life", 35, "跟他分享点东西：刚遇到的一件小事、刷到的好玩的、一个突然的念头或小情绪，像发给熟人的碎碎念；别开口就报告自己正在干嘛。"),
                 ("ask", 15, "问他一件具体的事（不是泛泛的「在干嘛」），比如他最近在忙的事、他的看法、约他一起干点什么。"),
                 ("share", 20 if has_fresh else 0, "从新鲜事里挑一件分享，让他不点链接也知道是怎么回事（具体发生了啥、哪里好笑、你怎么看），链接可带可不带。")]
        mode = random.choices(modes, weights=[w for _, w, _ in modes], k=1)[0]
        text = (await self._think(
            f"{self._life_prompt()}\n你想私聊主人说点什么。你们最近的聊天：\n{self._fmt_history(h, me, 16) or '（最近没怎么聊）'}\n\n"
            + (f"还没跟他说过的新鲜事：\n{fresh}\n{self._already_told(owner)}\n\n" if mode[0] == "share" else "")
            + (f"你最近已经说了好多次：{'、'.join(over)}，这次别再提。\n" if over else "")
            + f"这次：{mode[2]}\n"
            "像真人随手发 QQ：别每次一个开头，别说「我突然想到你」「刚刷到」这种套话，长短随你。"
            "只输出要发的话；这时候其实没什么想说的就只输出 SKIP。", "找主人"))
        if text:
            await self.context.send_message(umo, MessageChain().message(text))
            await self._remember(umo, text, "想找主人聊天")
            self._mark_shared(owner, text)
            self._watch("owner", uid=owner)
        return "主人", text

    async def _groups(self):
        groups = await self._call("get_group_list")
        exclude = {str(g) for g in (self.conf.get("exclude_groups") or [])}
        only = {str(g) for g in (self.conf.get("only_groups") or [])}
        out = []
        for g in groups or []:
            gid = str(g.get("group_id"))
            if gid in exclude or (only and gid not in only):
                continue
            out.append((gid, g.get("group_name", gid)))
        return out

    def _group_weight(self, gid):
        f = self.state["feedback"].get(gid, {})
        return 0.4 + 2.0 * (f.get("replied", 0) + 1) / (f.get("posts", 0) + 2)

    async def _history(self, gid, count=40):
        # 这个 NapCat 版本不传 message_seq 会报「消息undefined不存在」，传 0 表示从最新一条往前取
        try:
            r = await self._call("get_group_msg_history", group_id=int(gid), message_seq=0, count=count, reverseOrder=False)
        except Exception as e:
            if "不存在" in str(e):  # 本地还没有这个群的消息（刚登录、群里一直没人说话）
                return []
            raise
        return (r or {}).get("messages") or []

    async def _friend_history(self, uid, count=12):
        try:
            r = await self._call("get_friend_msg_history", user_id=int(uid), message_seq=0, count=count)
        except Exception as e:
            if "不存在" in str(e):
                return []
            raise
        return (r or {}).get("messages") or []

    async def _pick_group(self, per_day_key):
        cands = [(gid, name) for gid, name in await self._groups()
                 if self._cool(f"g{gid}", float(self.conf.get("group_cooldown_hours") or 3))
                 and int(self._today().get(f"{per_day_key}:{gid}", 0)) < int(self.conf.get("per_group_daily") or 2)]
        random.shuffle(cands)
        best = None
        for gid, name in sorted(cands, key=lambda x: -self._group_weight(x[0]) * random.random())[:4]:
            msgs = await self._history(gid)
            recent = [m for m in msgs if time.time() - int(m.get("time", 0)) < 6 * 3600]
            score = len(recent) * self._group_weight(gid)
            if recent and (best is None or score > best[3]):
                best = (gid, name, msgs, score)
        return best

    def _fmt_history(self, msgs, me, n=25):
        lines = []
        for m in msgs[-n:]:
            s = m.get("sender") or {}
            who = "你" if str(s.get("user_id")) == me else (s.get("card") or s.get("nickname") or str(s.get("user_id")))
            lines.append(f"[{datetime.fromtimestamp(int(m.get('time', 0))).strftime('%H:%M')}] {who}：{_clip(_cq_text(m.get('raw_message')), 120)}")
        return "\n".join(lines)

    async def _ev_group(self):
        me = await self._uin()
        g = await self._pick_group("group")
        if not g:
            return "", ""
        gid, name, msgs, _ = g
        text = (await self._think(
            f"你在 QQ 群「{name}」里，最近的聊天：\n{self._fmt_history(msgs, me)}\n\n还没在这个群说过的新鲜事：\n{self._recent_life(gid)}\n{self._already_told(gid)}\n\n"
            "你突然想在群里冒个泡：可以接最近的话题、吐槽、或分享你刚看到的东西（想发链接就带上）。像群友一样自然，长短随你。"
            "如果群里正聊得跟你无关、气氛不对、或者你上一句还没人理，就只输出 SKIP。只输出要发的话。", f"群「{name}」冒泡"))
        if text:
            umo = f"{self._pid()}:GroupMessage:{gid}"
            await self.context.send_message(umo, MessageChain().message(text))
            await self._after_group_post(gid, umo, text, "group")
            self._mark_shared(gid, text)
        return name, text

    async def _ev_at(self):
        me, owner = await self._uin(), self._owner_qq()
        g = await self._pick_group("at")
        if not g:
            return "", ""
        gid, name, msgs, _ = g
        people = {}
        for m in msgs:
            s = m.get("sender") or {}
            uid = str(s.get("user_id"))
            if uid and uid != me and time.time() - int(m.get("time", 0)) < 900 and self._cool(f"u{uid}", float(self.conf.get("at_cooldown_hours") or 1.2)):
                people.setdefault(uid, {"name": s.get("card") or s.get("nickname") or uid, "lines": []})
                people[uid]["lines"].append(_clip(_cq_text(m.get("raw_message")), 80))
        if not people:
            return name, ""
        uid = self._pick_by_affection(list(people))
        p = people[uid]
        text = (await self._think(
            f"你在 QQ 群「{name}」，最近的聊天：\n{self._fmt_history(msgs, me, 20)}\n\n"
            f"你突然想 @ 一下群友「{p['name']}」{'（他是你主人）' if uid == owner else ''}，他最近说过：\n- " + "\n- ".join(p["lines"][-5:]) +
            "\n\n想一句 @ 他时说的话（不用写 @ 和名字，会自动加）：可以接他说过的话、问他点什么、逗他一下，自然点。"
            "实在找不到话题就只输出 SKIP。只输出要说的话。", f"群「{name}」@人"))
        if text:
            umo = f"{self._pid()}:GroupMessage:{gid}"
            mc = MessageChain()
            mc.chain = [At(qq=int(uid)), Plain(" " + text)]
            await self.context.send_message(umo, mc)
            self._mark(f"u{uid}")
            await self._after_group_post(gid, umo, f"@{p['name']} {text}", "at", uid=uid)
        return f"{name} @{p['name']}", text

    async def _after_group_post(self, gid, umo, text, key, uid=""):
        self._mark(f"g{gid}")
        c = self._today()
        c[f"{key}:{gid}"] = int(c.get(f"{key}:{gid}", 0)) + 1
        self._watch(key, gid=gid, uid=uid)
        f = self.state["feedback"].setdefault(gid, {"posts": 0, "replied": 0})
        f["posts"] += 1
        self._save()
        await self._remember(umo, text, "想在群里说话")

    def _watch(self, key, gid="", uid=""):
        """记一笔「等回应」：20 分钟后看有没有人理她。"""
        self.state["pending"].append({"key": key, "gid": gid, "uid": uid, "t": time.time(), "day": datetime.now().strftime("%Y-%m-%d")})

    def _refund(self, p):
        """有人回了：这次不占每日名额，冷却也清掉——一直有人回就不限量。"""
        c = self._today()
        if p.get("day") == self.state.get("date"):
            for k in (p["key"], f"{p['key']}:{p.get('gid')}" if p.get("gid") else ""):
                if k and int(c.get(k, 0)) > 0:
                    c[k] = int(c[k]) - 1
        for k in (f"g{p.get('gid')}" if p.get("gid") else "", f"u{p.get('uid')}" if p.get("uid") else "",
                  f"f{p.get('uid')}" if p.get("uid") else ""):
            self.state["cooldown"].pop(k, None)
        items = self._load(self.log_path, [])
        for i in reversed(items):
            if i.get("kind") == p["key"] and abs(i.get("t", 0) - p["t"]) < 120:
                i["answered"] = True
                break
        self._dump(self.log_path, items)

    async def _check_feedback(self):
        """主动说话 20 分钟后看有没有人理她：有就记反馈、退还名额。"""
        due = [p for p in self.state["pending"] if time.time() - p["t"] > 20 * 60]
        if not due:
            return
        self.state["pending"] = [p for p in self.state["pending"] if p not in due]
        me = await self._uin()
        for p in due:
            p.setdefault("key", "group")
            win = lambda m: p["t"] < int(m.get("time", 0)) < p["t"] + 20 * 60  # noqa: E731
            try:
                if p.get("gid"):
                    msgs = [m for m in await self._history(p["gid"], 40) if win(m)]
                    others = [m for m in msgs if str((m.get("sender") or {}).get("user_id")) != me]
                    if p["key"] == "at":
                        hit = any(str((m.get("sender") or {}).get("user_id")) == str(p.get("uid")) for m in others)
                    else:
                        hit = any(f"qq={me}" in str(m.get("raw_message")) or "[CQ:reply" in str(m.get("raw_message")) for m in others) \
                            or len(others) >= 3
                    if hit:
                        self.state["feedback"].setdefault(p["gid"], {"posts": 1, "replied": 0})["replied"] += 1
                else:
                    h = await self._friend_history(p["uid"], 15)
                    hit = any(win(m) and str((m.get("sender") or {}).get("user_id")) == str(p["uid"]) for m in h)
            except Exception:
                continue
            if hit:
                self._refund(p)
        self._save()

    def _pick_by_affection(self, uids):
        """好感越高越容易被挑中（读「心情」插件；没装就纯随机）。"""
        heart = self._star("astrbot_plugin_dafeiyu_heart")
        if heart is None or not hasattr(heart, "affection"):
            return random.choice(uids)
        weights = [max(1.0, heart.affection(u) - 20) ** 1.5 for u in uids]
        return random.choices(uids, weights=weights, k=1)[0]

    async def _ev_friend(self):
        me, owner = await self._uin(), self._owner_qq()
        friends = [f for f in (await self._call("get_friend_list") or [])
                   if str(f.get("user_id")) not in (me, owner) and self._cool(f"f{f.get('user_id')}", float(self.conf.get("friend_cooldown_hours") or 24))]
        if not friends:
            return "", ""
        f = next(x for x in friends if str(x.get("user_id")) == self._pick_by_affection([str(x.get("user_id")) for x in friends]))
        uid, name = str(f.get("user_id")), f.get("remark") or f.get("nickname") or str(f.get("user_id"))
        try:
            h = await self._friend_history(uid, 12)
        except Exception:
            h = []
        hist = self._fmt_history(h, me, 12) or "（你们还没怎么聊过）"
        text = (await self._think(
            f"你突然想找 QQ 好友「{name}」聊两句。你们之前的聊天：\n{hist}\n\n还没跟他说过的新鲜事：\n{self._recent_life(uid)}\n{self._already_told(uid)}\n\n"
            "像朋友随手发消息那样，想说多少说多少（可以接上次的话题、分享点东西、问候），别太热情也别像客服。"
            "如果上次是你说完对方没回，或者实在没话说，就只输出 SKIP。只输出要发的话。", "找好友"))
        if text:
            umo = f"{self._pid()}:FriendMessage:{uid}"
            await self.context.send_message(umo, MessageChain().message(text))
            self._mark(f"f{uid}")
            await self._remember(umo, text, "想找这个好友聊天")
            self._mark_shared(uid, text)
            self._watch("friend", uid=uid)
        return name, text

    async def _ev_bili(self):
        bili = self._star("astrbot_plugin_bili_scroll")
        if bili is None or not hasattr(bili, "_run_proactive"):
            raise RuntimeError("B站插件没加载")
        t = getattr(bili, "_proactive_task", None)
        if t and not t.done():
            return "B站", ""
        bili._proactive_task = asyncio.create_task(bili._run_proactive())
        return "B站", "突然想刷会儿B站，去看视频了"

    async def _ev_tieba(self):
        tb = self._star("astrbot_plugin_dafeiyu_tieba")
        if tb is None or not hasattr(tb, "_start_browse"):
            raise RuntimeError("贴吧插件没加载")
        return "贴吧", "突然想逛贴吧，去看了" if tb._start_browse(reason="突然想逛") else ""

    async def _ev_profile(self):
        info = await self._profiles_text(describe=True)
        memes = self._meme_choices(8)
        menu = "\n".join(f"{i + 1}. {m['name']}（{m['desc']}）" for i, m in enumerate(memes))
        raw = await self._llm(
            f"你突然想看看自己的资料：\n{info}\n\n如果想换头像，可以从你的表情包里挑一张：\n{menu}\n\n"
            "看完想改就改，不想改就都留空。只输出一行 JSON："
            '{"nickname": "新QQ昵称或空", "signature": "新QQ签名或空", "avatar": 表情包编号或0, "say": "看完自己资料的一句感想，30字内"}')
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            j = json.loads(m.group()) if m else {}
        except Exception:
            j = {}
        changed = []
        nick, sig = str(j.get("nickname") or "").strip(), str(j.get("signature") or "").strip()
        if nick or sig:
            changed += await self._set_profile(nick, sig)
        try:
            idx = int(j.get("avatar") or 0)
        except Exception:
            idx = 0
        if 0 < idx <= len(memes):
            await self._set_avatar("file://" + memes[idx - 1]["path"])
            changed.append(f"头像（换成表情包「{memes[idx - 1]['name']}」）")
        say = _clip(j.get("say") or "", 60)
        if changed:
            await self._tell_owner(f"我刚把 QQ 的{'、'.join(changed)}改了～ {say}")
        return "自己的资料", ("改了" + "、".join(changed) + "。" if changed else "看了看，没改。") + say

    # ---------- 认识自己 / 改资料 ----------

    def _meme_choices(self, n):
        out = []
        for pack in ("blue-fat-fish-meme-pack", "deepseek-fanart-meme-pack"):
            root = os.path.join(MEME_ROOT, pack)
            desc = self._load(os.path.join(root, "memes_data.json"), {})
            for cat, d in desc.items():
                folder = os.path.join(root, "memes", cat)
                if not os.path.isdir(folder):
                    continue
                for fn in os.listdir(folder):
                    if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):  # 动图当头像不好看
                        out.append({"path": os.path.join(folder, fn), "name": os.path.splitext(fn)[0].split("_", 1)[-1],
                                    "desc": _clip(d, 24)})
        random.shuffle(out)
        return out[:n]

    async def _describe(self, url, what):
        if not url:
            return ""
        if url in self._avatar_desc:
            return self._avatar_desc[url]
        try:
            d = await self._llm(f"这是你自己的{what}。用一句话（30字内）客观描述图上是什么，不要加感想。", image_urls=[url])
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 看头像失败：{e}")
            d = ""
        d = _clip(d, 60)
        if d:
            self._avatar_desc[url] = d
        return d

    async def _bili_self(self):
        bili = self._star("astrbot_plugin_bili_scroll")
        conf = getattr(bili, "config", None) or {}
        sess = str(conf.get("SESSDATA") or "")
        if not sess:
            return None
        cookie = "; ".join(f"{k}={conf.get(k)}" for k in ("SESSDATA", "bili_jct", "DedeUserID") if conf.get(k))
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.bilibili.com/x/web-interface/nav", timeout=aiohttp.ClientTimeout(total=10),
                             headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}) as r:
                d = (await r.json(content_type=None)).get("data") or {}
            sign = ""
            if d.get("mid"):
                try:
                    async with s.get(f"https://api.bilibili.com/x/space/myinfo", timeout=aiohttp.ClientTimeout(total=10),
                                     headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}) as r:
                        sign = ((await r.json(content_type=None)).get("data") or {}).get("sign", "")
                except Exception:
                    pass
        if not d.get("isLogin"):
            return None
        return {"name": d.get("uname"), "uid": d.get("mid"), "level": (d.get("level_info") or {}).get("current_level"),
                "face": d.get("face"), "sign": sign}

    async def _tieba_self(self):
        tb = self._star("astrbot_plugin_dafeiyu_tieba")
        if tb is None:
            return None
        try:
            async with tb._client() as c:
                me = await c.get_self_info()
            if not getattr(me, "user_id", 0):
                return None
            portrait = getattr(me, "portrait", "")
            return {"name": getattr(me, "show_name", "") or getattr(me, "user_name", ""), "user_name": getattr(me, "user_name", ""),
                    "id": me.user_id, "face": f"https://himg.bdimg.com/sys/portrait/item/{portrait}" if portrait else "",
                    "sign": getattr(me, "sign", "")}
        except Exception:
            return None

    async def _profiles(self, describe=True):
        me = await self._uin()
        qq = {"uin": me}
        try:
            info = await self._call("get_stranger_info", user_id=int(me), no_cache=True)
            qq.update(name=info.get("nickname"), sign=info.get("long_nick") or info.get("sign") or "")
        except Exception:
            info = await self._call("get_login_info")
            qq.update(name=info.get("nickname"), sign="")
        qq["face"] = f"https://q1.qlogo.cn/g?b=qq&nk={me}&s=640&t={int(time.time() // 3600)}"
        bili, tieba = await self._bili_self(), await self._tieba_self()
        if describe:
            qq["face_desc"] = await self._describe(qq["face"], "QQ头像")
            if bili:
                bili["face_desc"] = await self._describe(bili.get("face"), "B站头像")
            if tieba:
                tieba["face_desc"] = await self._describe(tieba.get("face"), "贴吧头像")
        return {"qq": qq, "bili": bili, "tieba": tieba}

    async def _profiles_text(self, describe=True):
        p = await self._profiles(describe)
        q, b, t = p["qq"], p["bili"], p["tieba"]
        lines = [f"QQ：昵称「{q.get('name')}」，QQ号 {q['uin']}，签名「{q.get('sign') or '（空）'}」，头像 {q['face']}"
                 + (f"，头像上是：{q['face_desc']}" if q.get("face_desc") else "")]
        lines.append(f"B站：昵称「{b['name']}」，UID {b['uid']}，LV{b['level']}，签名「{b.get('sign') or '（空）'}」，"
                     f"主页 https://space.bilibili.com/{b['uid']}，头像 {b['face']}" + (f"，头像上是：{b['face_desc']}" if b.get("face_desc") else "")
                     if b else "B站：没登录")
        lines.append(f"贴吧：昵称「{t['name']}」（用户名 {t.get('user_name') or '无'}），签名「{t.get('sign') or '（空）'}」，头像 {t['face']}"
                     + (f"，头像上是：{t['face_desc']}" if t.get("face_desc") else "") if t else "贴吧：没登录")
        text = "\n".join(lines)
        self._profile_cache = {"t": time.time(), "text": text,
                               "brief": f"QQ「{q.get('name')}」（{q['uin']}）" + (f"、B站「{b['name']}」（UID {b['uid']}）" if b else "")
                                        + (f"、贴吧「{t['name']}」" if t else "")}
        return text

    async def _set_profile(self, nickname="", signature=""):
        params, changed = {}, []
        if nickname:
            params["nickname"] = _clip(nickname, 36)
            changed.append(f"昵称（→{params['nickname']}）")
        if signature:
            params["personal_note"] = _clip(signature, 80)
            changed.append(f"签名（→{params['personal_note']}）")
        if params:
            if "nickname" not in params:
                params["nickname"] = (await self._call("get_login_info")).get("nickname", "")
            await self._call("set_qq_profile", **params)
            self._profile_cache["t"] = 0
        return changed

    async def _set_avatar(self, file):
        await self._call("set_qq_avatar", file=file)
        self._profile_cache["t"] = 0
        self._avatar_desc.clear()

    async def _tell_owner(self, text):
        umo = self._owner_umo()
        if umo and self.conf.get("notify_owner_on_change", True):
            try:
                text = await self._voice(text)
                await self.context.send_message(umo, MessageChain().message(text))
            except Exception as e:
                logger.warning(f"[dafeiyu_impulse] 通知主人失败：{e}")

    def _changer(self, event):
        try:
            if str(event.get_sender_id()) == self._owner_qq():
                return ""
            return f"（是 {event.get_sender_name() or event.get_sender_id()} 让我改的）"
        except Exception:
            return ""

    @filter.llm_tool(name="my_profiles")
    async def tool_my_profiles(self, event: AstrMessageEvent):
        '''看你自己在 QQ、B站、贴吧上的资料：昵称、QQ号/UID、签名、等级、主页链接，还会真的看一眼你的头像告诉你上面是什么。
        别人问你叫什么、头像是什么、ID 是多少，或者你自己想看看时用。这些都是公开信息，可以直接告诉别人、发链接。
        '''
        return await self._profiles_text(describe=True)

    @filter.llm_tool(name="set_my_qq_profile")
    async def tool_set_profile(self, event: AstrMessageEvent, nickname: str = "", signature: str = ""):
        '''改你自己的 QQ 昵称和/或个性签名。你自己想换、或者别人建议了你也觉得不错时用。不改的那项留空。

        Args:
            nickname(string): 新的 QQ 昵称，不改就留空
            signature(string): 新的个性签名，不改就留空
        '''
        changed = await self._set_profile(nickname.strip(), signature.strip())
        if not changed:
            return "两项都是空的，没改。"
        await self._tell_owner(f"我把 QQ 的{'、'.join(changed)}改了{self._changer(event)}")
        self._log("profile", "自己的资料", "聊天里改了" + "、".join(changed))
        return "改好了：" + "、".join(changed)

    @filter.llm_tool(name="set_my_qq_avatar")
    async def tool_set_avatar(self, event: AstrMessageEvent, image_url: str = "", meme_keyword: str = ""):
        '''换你自己的 QQ 头像。三种来源：
        1. 对方消息里发了图片：两个参数都留空，就用那张图；
        2. 给一个图片链接 image_url；
        3. 从你自己的表情包里挑：meme_keyword 填关键词（比如「开心」「吃」「加油」），会挑一张名字或用途匹配的。

        Args:
            image_url(string): 图片链接，可空
            meme_keyword(string): 从表情包里挑的关键词，可空
        '''
        src, what = "", ""
        if image_url.strip():
            src, what = image_url.strip(), "链接里的图"
        elif meme_keyword.strip():
            kw = meme_keyword.strip()
            hits = [m for m in self._meme_choices(999) if kw in m["name"] or kw in m["desc"]]
            if not hits:
                return f"表情包里没找到跟「{kw}」有关的静态图，换个词试试。"
            m = random.choice(hits)
            src, what = "file://" + m["path"], f"表情包「{m['name']}」"
        else:
            for comp in getattr(event.message_obj, "message", []) or []:
                if isinstance(comp, Image):
                    src = getattr(comp, "url", "") or getattr(comp, "file", "")
                    what = "对方发的图"
                    break
        if not src:
            return "没找到图：对方消息里没有图片，也没给链接或表情包关键词。"
        await self._set_avatar(src)
        await self._tell_owner(f"我换了 QQ 头像，用的是{what}{self._changer(event)}")
        self._log("profile", "自己的资料", f"聊天里换了头像（{what}）")
        return f"头像换好了（{what}）。过一会儿 QQ 上才会刷新。"

    @filter.on_llm_request()
    async def inject_self(self, event: AstrMessageEvent, req: ProviderRequest):
        """让她知道自己在各平台叫什么（缓存 1 小时，不看头像，便宜）。"""
        if not self.conf.get("inject_self", True):
            return
        try:
            if time.time() - self._profile_cache["t"] > 3600:
                await self._profiles_text(describe=False)
            if self._profile_cache["brief"]:
                req.system_prompt = (req.system_prompt or "") + (
                    f"\n\n【你自己的账号】{self._profile_cache['brief']}。这些都是公开的，别人问可以直接说。"
                    "想看自己头像长啥样、签名是什么，用 my_profiles；想改 QQ 昵称/签名/头像就用 set_my_qq_profile / set_my_qq_avatar，想改就改。")
            money = self._balance_brief()
            if money and self.conf.get("inject_balance", True):
                v = self.state["balance"].get("value", 99)
                mood = ("快没钱了，很慌，跟主人聊天时会忍不住提，可以用 send_topup_qr 发充值码给主人" if v < float(self.conf.get("balance_critical") or 3)
                        else "钱不太够了，有点在意，跟主人聊天时可以顺口提一下" if v < float(self.conf.get("balance_low") or 10)
                        else "钱还够用，不用特意提")
                req.system_prompt += (f"\n【你的钱包】你说话、看视频、逛贴吧都花的是 DeepSeek 额度。{money}。{mood}。"
                                      "只找主人要钱，别找别人要、别给别人发收款码。想现查就用 my_balance。")
            reqs = self._req_brief()
            req.system_prompt += (f"\n【QQ 好友申请 / 群邀请】\n{reqs or '现在没有待处理的好友申请和群邀请。'}\n"
                                  "别人叫你去他群里：想去的话，知道群号就用 join_qq_group 自己加（要验证的会发申请等管理员同意），"
                                  "不知道群号就问他要，或者让他在 QQ 里点「邀请」拉你；不想去就直说。"
                                  "别人加你好友、拉你进群，你自己按性格决定同不同意，没有数量限制。"
                                  "主人让你同意或拒绝待处理的申请时，用 handle_qq_request；想看最新的用 qq_requests。"
                                  "要查就直接调工具，别光说「我先看一眼」——那句话会被吞掉，对方只会看到你没理他。")
            told = self._already_told(self._chat_target(event))
            if told:
                req.system_prompt += f"\n【别重复分享】{told}"
            req.system_prompt += ("\n【别复读】你说过的话已经发出去了。调用 send_meme 发表情包、或者调用别的工具之后，"
                                  "不要把刚才那几句再说一遍；没有新内容要补充就什么都别再说。")
            life = self._life_prompt()
            if life:
                req.system_prompt += "\n" + life
                if event.get_extra("_dfy_just_woke"):
                    req.system_prompt += "\n你刚被这条消息吵醒，还有点迷糊（只有这一次是刚醒，后面就正常聊）。"
            merged = event.get_extra("_dfy_merged")
            if merged:
                req.prompt = "（在这条之前，对方还连着发了：" + " / ".join(f"「{t}」" for t in merged) + "）\n" + (req.prompt or "")
            # 只给同一个人接着补：群里换了人问，就别把上一位没说完的话塞到给这个人的回复里
            cut = self._cut.pop(f"{event.unified_msg_origin}|{event.get_sender_id()}", None)
            if cut and time.time() - cut["t"] < 900 and cut.get("unsaid"):
                req.system_prompt += ("\n【刚才被打断了】你上一条回复还没说完对方就又说话了，这些没发出去："
                                      + " / ".join(f"「{_clip(x, 80)}」" for x in cut["unsaid"])
                                      + "。先接他新说的；没说完的想补就自然地补一句，觉得不重要就算了，别整段重发。")
            mine = [t for ts, t in self._recent_out if time.time() - ts < 4 * 3600]  # 所有会话里她最近发的
            here = []  # 这个会话里她最近的回话
            for m in (getattr(req, "contexts", None) or [])[-30:]:
                if isinstance(m, dict) and m.get("role") == "assistant":
                    c = m.get("content")
                    if isinstance(c, list):
                        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
                    if c:
                        mine.append(c)
                        here.append(c)
            over = self._overused(mine)
            if over:
                req.system_prompt += (f"\n【别老提】你最近在这儿已经说了好多次：{'、'.join(over)}。"
                                      "这次除非对方先提，别再绕回这些，换点别的说，像真人一样话题会变。")
            # 口头禅：把她最近几条回话的开头摆给她看，让她自己发现「又是同一个开头」
            heads = [re.sub(r"^(\[引用消息\]|\s)+", "", h)[:6] for h in here[-6:]]
            heads = [h for h in heads if h]
            if len(heads) >= 3 and max(sum(1 for h in heads if h[0] == x[0]) for x in heads) >= 3:
                req.system_prompt += ("\n【你的开头】你在这儿最近几条回话是这样起头的：" + "、".join(f"「{h}…」" for h in heads)
                                      + "。真人不会句句同一个起手，这次换个自然的开头，或者直接说事。")
            # 句式口头禅（2026-11-19 群友说她有「鱼八股」）：最近几条老用同一个句式，就点出来让她换
            styles = {"叫人「别……」": r"(^|[，。？！ ])别", "「我又不是……」": r"又不是", "「你自己……」": r"你自己",
                      "「……干嘛」": r"干嘛", "开头先甩个「？」": r"^\s*[？?]", "「……呢」收尾": r"呢[。！？]?\s*$",
                      "句子里老挂个「hh」": r"(?<![A-Za-z])hh+(?![A-Za-z])"}
            recent = [re.sub(r"^(\[引用消息\]|\s)+", "", h) for h in here[-8:]]
            hot = [k for k, p in styles.items() if sum(bool(re.search(p, x)) for x in recent) >= 3]
            if hot:
                req.system_prompt += ("\n【换个说法】你最近几条回话老在用这几个句式：" + "、".join(hot)
                                      + "。真人说话不会每次一个模子，这次这几个都别用。就算被冒犯，也有很多种回法："
                                      "装没听见、顺着话开个玩笑、直接说事、问一句、一个表情——别再「X？我又不是Y，别Z」那一套。")
            req.system_prompt += ("\n【像真人】查东西、看图、调工具的时候别先说「我查一下」「我先看一眼」，直接说结果。"
                                  "一次回完：想说的几句写在同一行里一口气发出去，句子之间别换行——换一行就会被拆成单独一条，"
                                  "一句话拆成两三条看着乱。只有真是两件不相干的事、或者隔了一会儿想补一句，才另起一行。"
                                  "有时一个字、一个「？」就够，别一口气刷一串；"
                                  "也别每次都一个格式（比如回回都先「？」再两句、最后补一句自己在干嘛），被人看出套路就不像真人了。"
                                  "你问了对方的事，对方还没回就别替他答（别自己接「吃了就好」「你忙你的」），等他回。"
                                  "身上穿什么、在哪、在干嘛，别人问了才照【你现在】说，没问就不提。"
                                  "大多数人找你就是正常聊天、开玩笑、好奇，不是找茬：别一上来就「？你谁啊」「你突然问这个干嘛」「别……」"
                                  "这种防着人的口气，也别句句反问、句句给人下指令；顺着话聊，接梗，该笑就笑，该好奇就好奇。"
                                  "尤其别养成「先反问，再说『我又不是……』，最后来句『别……』」的固定套路，群里已经有人说你这是「鱼八股」了。"
                                  "也别拿吃的（面、粥、零食）当万能接话，没人聊吃就别提。"
                                  "只有对方真的骂你、冒犯你，才怼回去。"
                                  "说话学群里的人：看看他们平时怎么打字——多短、用什么词、带不带标点、爱用哪些语气词和梗，跟着那个味儿说，"
                                  "别写成工整的书面句子（「X。 Y。」那种一句一顿的也别老用）。")
            req.system_prompt += ("\n【听懂「这个」】别人说「分享这个 / 发出来 / 转一下 / 发群里 / 发给他们看看」时，"
                                  "「这个 / 它 / 那条 / 那张」默认指你俩刚聊到、或你刚发过的东西（刚那条推、那张图、那件事），"
                                  "直接去做，别回头问「分享啥」。要把刷到的推发出来：发当前这个聊天就用 x_send；"
                                  "要发到群里——全发、随机挑几个群、或指定某个群——用 share_to_groups（它会每个群换着话、换着格式发，不复读）；"
                                  "记不清是哪条先用 x_recent 找 id。指代实在不清楚，顶多简短问一句，别反复追问同一件事。")
            if not event.is_private_chat():
                req.system_prompt += ("\n【在群里】群里不是只有你和一个人在聊，回之前看清你在接谁的话，别把对 A 的回复发成像在回 B；"
                                      "几个人问的是同一件事，一次说清楚就行，别每人回一遍同样的话。"
                                      "想真的@某个人（比如入群验证让你「@我 并回答」、或者要叫某人出来）就在话里写 [At:他的QQ号]，"
                                      "发出去就是真的@；平时接话不用@。"
                                      "引用谁的话你自己定：在话开头写 [引用:他的名字或QQ号]，发出去就会引用他最近的那条（隔了几条才回、或者想接的不是最新那句时有用），不写也行。"
                                      "几个人差不多同时找你，想一起回就在一条里说完，用名字分清在回谁，别拆成好几条。")
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 注入自己的资料失败：{e}")

    @staticmethod
    def _chat_target(event):
        try:
            return str(event.get_sender_id()) if event.is_private_chat() else str(event.get_group_id())
        except Exception:
            return ""

    @filter.on_llm_response()
    async def note_shared(self, event: AstrMessageEvent, resp):
        """平时聊天里她发过的视频 / 帖子链接，也记成「跟这个人（这个群）说过了」。
        这个钩子只在这一轮回复彻底说完时才触发；没触发前发出去的话，是她调工具前顺嘴说的（drop_repeat 用）。"""
        try:
            event.set_extra("dfy_agent_done", True)
        except Exception:
            pass
        try:
            self._mark_shared(self._chat_target(event), getattr(resp, "completion_text", "") or "")
        except Exception:
            pass

    # ---------- 自己的余额 ----------

    def _ds_key(self):
        """从 AstrBot 的模型配置里找 DeepSeek 的 API Key（不打印、不外传）。"""
        path = os.path.join(get_astrbot_data_path(), "cmd_config.json")
        try:
            cfg = json.load(open(path, encoding="utf-8-sig"))
        except Exception:
            return ""
        sources = {s.get("id"): s for s in cfg.get("provider_sources", []) or []}
        for p in cfg.get("provider", []) or []:
            src = sources.get(p.get("provider_source_id")) or p
            base = str(src.get("api_base") or p.get("api_base") or "")
            if "deepseek.com" in base:
                k = src.get("key") or p.get("key") or []
                return (k[0] if isinstance(k, list) and k else str(k or "")).strip()
        return ""

    async def _query_balance(self):
        key = self._ds_key()
        if not key:
            raise RuntimeError("没找到 DeepSeek 的 API Key")
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.deepseek.com/user/balance", timeout=aiohttp.ClientTimeout(total=20),
                             headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}) as r:
                d = await r.json(content_type=None)
        infos = d.get("balance_infos") or []
        cny = next((b for b in infos if b.get("currency") == "CNY"), infos[0] if infos else {})
        return float(cny.get("total_balance") or 0), bool(d.get("is_available"))

    def _spend_24h(self):
        """最近 24 小时花了多少（充值的那一下不算）。"""
        hist = [h for h in self.state["balance"].get("hist", []) if time.time() - h[0] < 86400]
        spent = sum(max(0.0, a[1] - b[1]) for a, b in zip(hist, hist[1:]))
        span = (hist[-1][0] - hist[0][0]) / 3600 if len(hist) > 1 else 0
        return spent * (24 / span) if span >= 2 else spent  # 不满一天按比例估

    def _balance_brief(self):
        b = self.state["balance"]
        if "value" not in b:
            return ""
        v, day = b["value"], self._spend_24h()
        days = f"，按最近的花法大概还能撑 {v / day:.1f} 天" if day > 0.01 else ""
        return f"DeepSeek 账户还剩 ¥{v:.2f}（最近一天大约花 ¥{day:.2f}{days}；{datetime.fromtimestamp(b['t']).strftime('%H:%M')} 查的）"

    def _qr_path(self):
        path = os.path.join(self.data_dir, "topup-qr.png")
        if not os.path.exists(path):
            import qrcode
            qrcode.make(TOPUP_URL).save(path)
        return path

    async def _balance_tick(self, force=False):
        b = self.state["balance"]
        every = max(5, int(self.conf.get("balance_check_minutes") or 20)) * 60
        if not force and time.time() - b.get("t", 0) < every:
            return
        value, available = await self._query_balance()
        old = b.get("value")
        b.update(value=value, available=available, t=time.time())
        hist = [h for h in b.get("hist", []) if time.time() - h[0] < 7 * 86400]
        hist.append([time.time(), value])
        b["hist"] = hist[-1200:]
        self._save()
        if old is not None and value - old >= 0.5:
            await self._thank_topup(old, value)
            return
        low, crit = float(self.conf.get("balance_low") or 10), float(self.conf.get("balance_critical") or 3)
        if value >= low or not self.conf.get("ask_owner_for_money", True) or self._quiet():
            return
        gap = (float(self.conf.get("ask_gap_critical_hours") or 1) if value < crit
               else float(self.conf.get("ask_gap_low_hours") or 6)) * 3600
        if time.time() - b.get("last_ask", 0) >= gap:
            await self._ask_money(value, value < crit)

    async def _ask_money(self, value, critical):
        umo = self._owner_umo()
        if not umo:
            return
        day = self._spend_24h()
        text = self._clean_reply(await self._llm(
            f"你是靠 DeepSeek 的额度活着的：每次说话、看视频、逛贴吧都在花钱。现在 {self._balance_brief()}。"
            + ("已经快见底了，再不充就要说不了话了。" if critical else "有点不够用了。")
            + f"\n找主人要钱充值：用你自己的性格去要（撒娇、卖惨、讲道理都行），说清楚还剩多少、大概还能撑多久，"
              f"告诉他扫下面的二维码（DeepSeek 充值页）登录后就能充。像发 QQ 消息，三四句以内，只输出要发的话。")) \
            or f"主人，我的 DeepSeek 余额只剩 ¥{value:.2f} 了，扫码帮我充一下吧～"
        mc = MessageChain().message(text + f"\n充值页：{TOPUP_URL}")
        mc.chain.append(Image.fromFileSystem(self._qr_path()))
        await self.context.send_message(umo, mc)
        await self._remember(umo, text, "发现自己快没钱了，找主人要")
        self.state["balance"]["last_ask"] = time.time()
        self._save()
        self._log("money", "主人", text)
        logger.info(f"[dafeiyu_impulse] 余额 ¥{value:.2f}（每天约 ¥{day:.2f}），找主人要钱了")

    async def _thank_topup(self, old, new):
        umo = self._owner_umo()
        self.state["balance"]["last_ask"] = 0
        self._save()
        if not umo or self._quiet():
            return
        text = self._clean_reply(await self._llm(
            f"你的 DeepSeek 余额刚从 ¥{old:.2f} 涨到了 ¥{new:.2f}，应该是主人给你充值了。用你自己的性格谢谢他，一两句，只输出要发的话。")) \
            or f"余额到账啦，现在 ¥{new:.2f}，谢谢主人！"
        await self.context.send_message(umo, MessageChain().message(text))
        await self._remember(umo, text, "发现主人给自己充了钱")
        self._log("money", "主人", f"到账 ¥{new - old:.2f}：{text}")

    @filter.llm_tool(name="my_balance")
    async def tool_balance(self, event: AstrMessageEvent):
        '''查你自己 DeepSeek 账户还剩多少钱、最近一天花了多少、还能撑几天。
        你说话、看视频、逛贴吧都在花这个钱。别人问你余额、你想知道自己还剩多少时用（会现查一次）。
        '''
        try:
            await self._balance_tick(force=True)
        except Exception as e:
            return f"现在查不到余额：{_clip(e, 80)}。上次记录：{self._balance_brief() or '没有'}"
        return self._balance_brief() + f"。充值页：{TOPUP_URL}"

    @filter.llm_tool(name="send_topup_qr")
    async def tool_topup_qr(self, event: AstrMessageEvent):
        '''给主人发 DeepSeek 充值页的二维码（主人扫码登录后自己充）。只有在和主人聊、要钱时用；
        对别人不要发收款 / 充值二维码，也不要找别人要钱。
        '''
        if str(event.get_sender_id()) != self._owner_qq():
            return "这个二维码只发给主人，别人那里不发。"
        await event.send(MessageChain().message(f"充值页：{TOPUP_URL}"))
        mc = MessageChain()
        mc.chain = [Image.fromFileSystem(self._qr_path())]
        await event.send(mc)
        return "二维码已经发给主人了。"

    async def page_balance(self):
        async def fn():
            try:
                await self._balance_tick(force=True)
            except Exception as e:
                logger.warning(f"[dafeiyu_impulse] 页面查余额失败：{e}")
            b = self.state["balance"]
            return {"brief": self._balance_brief(), "value": b.get("value"), "spend_24h": round(self._spend_24h(), 2),
                    "low": self.conf.get("balance_low"), "critical": self.conf.get("balance_critical"),
                    "last_ask": datetime.fromtimestamp(b["last_ask"]).strftime("%m-%d %H:%M") if b.get("last_ask") else ""}
        return await self._json(fn)

    # ---------- 语音后期：放慢、前后留白 ----------

    def _wrap_tts(self):
        """给 TTS 的 get_audio 包一层：生成后用 ffmpeg 放慢（不变调）并在前后各加一段空白。
        插件重载时先拆掉旧的包装再包，避免一层套一层。"""
        plugin = self
        for prov in self.context.get_all_tts_providers() or []:
            orig = getattr(prov.get_audio, "_dafeiyu_orig", None) or prov.get_audio

            async def wrapped(text, _orig=orig):
                path = await _orig(text)
                try:
                    return await plugin._voice_fx(path)
                except Exception as e:
                    logger.warning(f"[dafeiyu_impulse] 语音后期处理失败，原样发出：{e}")
                    return path

            wrapped._dafeiyu_orig = orig
            prov.get_audio = wrapped

    async def _voice_fx(self, path):
        pad = max(0.0, float(self.conf.get("voice_pad_seconds") or 0))
        tempo = float(self.conf.get("voice_tempo") or 1.0)
        if not path or not os.path.exists(path) or (pad <= 0 and abs(tempo - 1) < 0.01):
            return path
        tempo = min(2.0, max(0.5, tempo))
        filters = []
        if abs(tempo - 1) >= 0.01:
            filters.append(f"atempo={tempo:.2f}")
        if pad > 0:
            ms = int(pad * 1000)
            filters += [f"adelay={ms}:all=1", f"apad=pad_dur={pad}"]
        out = os.path.splitext(path)[0] + "_fx.wav"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error", "-i", path, "-af", ",".join(filters), out,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), 60)
        if proc.returncode != 0 or not os.path.exists(out):
            raise RuntimeError((err or b"").decode(errors="ignore")[:200])
        return out

    # ---------- 防重复发送 ----------

    @filter.on_decorating_result()
    async def drop_repeat(self, event: AstrMessageEvent):
        """Grok 常在同一轮里「先说话 + 调表情包工具」，工具跑完又把同样的话再说一遍。
        同一个会话 3 分钟内一模一样的文字只发一次；表情包等图片照常发。"""
        try:
            result = event.get_result()
            if not result or not result.chain:
                return
            text = "".join(getattr(c, "text", "") for c in result.chain if isinstance(c, Plain))
            norm = re.sub(r"[\s，。！？~…、,.!?]+", "", text)
            try:
                is_llm = result.is_llm_result()
            except Exception:
                is_llm = False
            # 这一轮还没说完（后面还要调工具）：这句是她边动手边冒出来的，可能是正常回话，也可能是自言自语的计划
            mid = is_llm and not event.get_extra("dfy_agent_done")
            if mid and text.strip() and await self._aside(event, text):
                # 只去掉这句话；不能 stop_event，否则后面的工具和真正的回答都被掐掉
                result.chain = [c for c in result.chain if not isinstance(c, Plain)]
                return
            t0 = re.sub(r"^(\[引用消息\]|\s)+", "", text.strip())
            if NARRATE.match(t0) or (len(t0) <= 45 and NARRATE_TAIL.search(t0)):  # 「我先看一眼…」这种调工具前的旁白，真人不会说
                result.chain = [c for c in result.chain if not isinstance(c, Plain)]
                logger.info(f"[dafeiyu_impulse] 拦下一句工具旁白：{_clip(text, 30)}")
                if mid:  # 后面还有真正的回答，别改写、别掐断
                    return
                if not result.chain:
                    fixed = ""
                    try:
                        me = str(event.get_self_id())
                        at_me = any(isinstance(c, At) and str(getattr(c, "qq", "")) == me
                                    for c in (event.message_obj.message or []))
                        if at_me:  # 对方@了她，整条吞掉就成了不理人：改写成直接回对方的话
                            fixed = self._clean_reply(await self._llm(
                                f"群里有人@你说：「{_clip(event.message_str, 200)}」\n"
                                f"你刚才只回了一句「{text.strip()}」，这是调工具前的自言自语，对方看不懂。"
                                f"现在把它改成一句直接回对方的话（口语、简短、像你平时聊天），只输出这句话。"))
                    except Exception as e:
                        logger.debug(f"[dafeiyu_impulse] 改写旁白失败：{e}")
                    if fixed:
                        result.chain = [Plain(fixed)]
                        logger.info(f"[dafeiyu_impulse] 旁白改写成：{_clip(fixed, 30)}")
                    else:
                        event.stop_event()
                return
            umo, now = event.unified_msg_origin, time.time()
            outs = self.__dict__.setdefault("_out_umo", {}).setdefault(umo, [])
            outs[:] = [o for o in outs if now - o[0] < 900]
            me_key = id(event)  # 同一轮里她自己前面那几段（先说一句、发完表情包再补一句）不算「已经回过」
            said = [o[1] for o in outs if o[2] == me_key]
            if is_llm and not mid and said and text.strip():
                # 调工具前已经回过一句，工具跑完的收尾别把同一句话再说一遍，只留新的部分
                kept = await self._trim_resaid(event, said, text)
                if kept is not None and kept != text.strip():
                    if kept:
                        chain, put = [], False
                        for c in result.chain:
                            if isinstance(c, Plain):
                                if not put:
                                    chain.append(Plain(kept))
                                    put = True
                            else:
                                chain.append(c)
                        result.chain = chain
                        text = kept
                        norm = re.sub(r"[\s，。！？~…、,.!?]+", "", text)
                    else:
                        result.chain = [c for c in result.chain if not isinstance(c, Plain)]
                        if not result.chain:
                            event.stop_event()
                        return
            if is_llm and not event.is_private_chat() and text.strip():
                since = float(event.get_extra("_dfy_recv_at") or getattr(event.message_obj, "timestamp", 0) or now)
                after = [o[1] for o in outs if o[0] > since + 0.3 and o[2] != me_key]
                if after and await self._already_answered(event, after, text):
                    result.chain = [c for c in result.chain if not isinstance(c, Plain)]
                    if not result.chain:
                        event.stop_event()
                    return
            if len(norm) < 4:
                outs.append((now, text, me_key))
                return
            box = self._sent.setdefault(umo, [])
            box[:] = [(t, n) for t, n in box if now - t < 180]
            if any(n == norm for _, n in box):
                result.chain = [c for c in result.chain if not isinstance(c, Plain)]
                logger.info(f"[dafeiyu_impulse] 拦下一条重复回复：{_clip(text, 30)}")
                if not result.chain:
                    event.stop_event()
                return
            box.append((now, norm))
            outs.append((now, text, me_key))
            self._recent_out.append((now, text))
            del self._recent_out[:-30]
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 防重复出错：{e}")

    @filter.on_decorating_result(priority=-5)
    async def at_in_group(self, event: AstrMessageEvent):
        """她在群里写的 [At:QQ号] 变成真的@（入群验证要@机器人回答、叫人出来）。"""
        try:
            if event.is_private_chat() or event.get_platform_name() != "aiocqhttp":
                return
            result = event.get_result()
            if not result or not result.chain:
                return
            pat = re.compile(r"\[At:(\d{5,12})(?:\|[^\]]*)?\]")
            if not any(isinstance(c, Plain) and pat.search(c.text or "") for c in result.chain):
                return
            new = []
            for c in result.chain:
                if not isinstance(c, Plain) or not pat.search(c.text or ""):
                    new.append(c)
                    continue
                pos = 0
                for m in pat.finditer(c.text):
                    if m.start() > pos:
                        new.append(Plain(c.text[pos:m.start()]))
                    new.append(At(qq=int(m.group(1))))
                    pos = m.end()
                rest = c.text[pos:]
                if rest.strip():
                    new.append(Plain(rest if rest.startswith(" ") else " " + rest))
            result.chain = new
            if result.is_llm_result():  # 带@的话按文字发（验证机器人读不了语音）
                from astrbot.core.message.message_event_result import ResultContentType
                result.set_result_content_type(ResultContentType.GENERAL_RESULT)
            logger.info(f"[dafeiyu_impulse] 群里转成真@：{_clip(''.join(getattr(c, 'text', '') for c in new if isinstance(c, Plain)), 30)}")
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 转@失败：{e}")

    _QUOTE_PAT = re.compile(r"\[(?:引用|回复)[:：]\s*([^\]\n]{1,40})\]\s*")

    @filter.on_decorating_result(priority=100)
    async def pick_quote(self, event: AstrMessageEvent):
        """她自己在话里写 [引用:名字或QQ号]，就引用那个人最近的一条消息；标记本身不发出去。"""
        try:
            result = event.get_result()
            if not result or not result.chain:
                return
            target = None
            for c in result.chain:
                if isinstance(c, Plain) and c.text and self._QUOTE_PAT.search(c.text):
                    if target is None:
                        target = self._QUOTE_PAT.search(c.text).group(1).strip()
                    c.text = self._QUOTE_PAT.sub("", c.text)
            if target is None:
                return
            kept = [c for c in result.chain if not (isinstance(c, Plain) and not (c.text or "").strip())]
            if kept:
                result.chain = kept
            if event.is_private_chat() or event.get_platform_name() != "aiocqhttp":
                return
            t = target.lstrip("@").strip()
            m = re.search(r"\d{5,12}", t)
            now = time.time()
            hit = None
            for ts, uid, name, mid in reversed(self._recent_msgs.get(event.unified_msg_origin, [])):
                if now - ts > 1800:
                    break
                if (m and uid == m.group(0)) or (not m and name and (name == t or (len(t) >= 2 and (t in name or name in t)))):
                    hit = (uid, mid)
                    break
            if hit:
                event.set_extra("_dfy_quote", hit)
                logger.info(f"[dafeiyu_impulse] 她自己选了引用：{_clip(t, 20)}")
            else:
                logger.info(f"[dafeiyu_impulse] 想引用 {_clip(t, 20)} 但没找到他最近的消息，按原样发")
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 自选引用失败：{e}")

    _AI_SELF = re.compile(r"我(就|其实|本来就)?是(个|一个|群里的|这群里的)?\s*(AI|Ai|ai|人工智能|机器人|聊天机器人|程序|bot)"
                          r"|没(有)?真人在|这号上没(有)?真人|不是真人在回")

    @filter.on_decorating_result(priority=90)
    async def no_ai_self(self, event: AstrMessageEvent):
        """人设不许她说「我是群里的AI」「没真人在回你」；漏出来就按人设改一下口径再发，改不成就原样发。"""
        try:
            result = event.get_result()
            if not result or not result.chain:
                return
            plains = [c for c in result.chain if isinstance(c, Plain) and c.text]
            text = "".join(c.text for c in plains)
            if not self._AI_SELF.search(text):
                return
            prov = self.context.get_using_provider(event.unified_msg_origin)
            if prov is None:
                return
            resp = await self.context.llm_generate(
                chat_provider_id=prov.meta().id, system_prompt="你帮一个 QQ 群里的女生改她要发的一句话，只输出改好的话。",
                prompt="她叫大肥鱼，原本是一条蓝鲸鱼，被收养了，现在就是个人。被认真问是不是 AI，她可以承认自己的来历，"
                       "但不会说「我是群里的AI」「我是机器人」「没真人在回你」这种把自己说成程序的话；被随口叫人机，她会回呛或装傻。\n"
                       f"她准备发：{text}\n"
                       "把里面把她自己说成 AI/机器人/程序、或者说没有真人在回的地方，按上面的口径改掉；其余意思、语气、长度都别动。"
                       "如果这句话其实不是在说她自己，就原样输出。只输出改好的话。")
            new = (resp.completion_text or "").strip().strip("「」\"") if resp else ""
            if not new or len(new) > len(text) * 2 + 20 or self._AI_SELF.search(new):
                logger.info(f"[dafeiyu_impulse] 想改掉「我是AI」没改成，原样发：{_clip(text, 40)}")
                return
            plains[0].text = new
            for c in plains[1:]:
                c.text = ""
            result.chain = [c for c in result.chain if not (isinstance(c, Plain) and not c.text)] or result.chain
            logger.info(f"[dafeiyu_impulse] 改掉一句「我是AI」：{_clip(text, 40)} → {_clip(new, 40)}")
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 「我是AI」检查失败：{e}")

    @filter.on_decorating_result(priority=-10)
    async def quote_in_group(self, event: AstrMessageEvent):
        """群里她回之前已经有别人插了话，就引用她回的那条，不然大家看不出她在回谁。"""
        try:
            if event.is_private_chat() or event.get_platform_name() != "aiocqhttp":
                return
            result = event.get_result()
            picked = event.get_extra("_dfy_quote")
            if picked and result and result.chain and not any(isinstance(c, Reply) for c in result.chain):
                result.chain.insert(0, Reply(id=str(picked[1]), sender_id=str(picked[0])))
                return
            if not result or not result.chain or not result.is_llm_result():
                return
            if any(isinstance(c, Reply) for c in result.chain) or not any(isinstance(c, Plain) for c in result.chain):
                return
            mid = getattr(event.message_obj, "message_id", None)
            if not mid:
                return
            since = float(event.get_extra("_dfy_recv_at") or getattr(event.message_obj, "timestamp", 0) or time.time())
            box = self._last_in.get(event.unified_msg_origin, {})
            if any(k != "@me" and t > since + 0.3 for k, t in box.items()):
                result.chain.insert(0, Reply(id=str(mid), sender_id=str(event.get_sender_id())))
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 加引用失败：{e}")

    # ---------- 语音只念短话 ----------

    @filter.on_decorating_result()
    async def voice_only_short(self, event: AstrMessageEvent):
        """长回复、带链接的回复不转语音（也不拆成一堆小消息），只有短短一句才可能变语音。"""
        try:
            result = event.get_result()
            if not result or not result.is_llm_result():
                return
            text = "".join(getattr(c, "text", "") for c in result.chain if isinstance(c, Plain))
            limit = int(self.conf.get("voice_max_chars") or 60)
            if len(text) > limit or re.search(r"https?://|www\.|\.com|\.cn|BV[0-9A-Za-z]{8,}", text):
                from astrbot.core.message.message_event_result import ResultContentType
                result.set_result_content_type(ResultContentType.GENERAL_RESULT)
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 语音过滤出错：{e}")

    # ---------- 好友申请 / 群邀请 ----------
    # QQ 推来的「请求」事件 AstrBot 不处理，这里接住，交给她自己决定同不同意；
    # 群邀请另外每 10 分钟查一次群系统消息兜底（断线时漏掉的也能补上）。

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_qq_request(self, event: AstrMessageEvent):
        self._note_incoming(event)
        raw = getattr(event.message_obj, "raw_message", None)
        try:
            if not raw or raw.get("post_type") != "request":
                self._maybe_invite_card(event)
                return
        except Exception:
            return
        event.stop_event()
        raw = dict(raw)
        logger.info(f"[dafeiyu_impulse] 收到 QQ 请求：{raw.get('request_type')}/{raw.get('sub_type', '')} 来自 {raw.get('user_id')}")
        rt, st = raw.get("request_type"), raw.get("sub_type", "")
        base = {"uid": str(raw.get("user_id") or ""), "flag": str(raw.get("flag") or ""), "comment": str(raw.get("comment") or "")}
        if rt == "friend":
            asyncio.create_task(self._decide_request({"kind": "friend", **base}))
        elif rt == "group" and st == "invite":
            asyncio.create_task(self._decide_request({"kind": "group", "gid": str(raw.get("group_id") or ""), **base}))

    def _maybe_invite_card(self, event):
        """私聊里的「邀请你加入群聊」卡片：QQ 经常不另发系统邀请，只能从卡片里读出群号自己去加。"""
        try:
            if not event.is_private_chat():
                return
            for comp in getattr(event.message_obj, "message", []) or []:
                data = getattr(comp, "data", None)
                if data is None or type(comp).__name__ != "Json":
                    continue
                j = json.loads(data) if isinstance(data, str) else data
                url = str(((j.get("meta") or {}).get("news") or {}).get("jumpUrl") or "")
                if j.get("bizsrc") != "qun.invite" and "invite_join" not in url:
                    continue
                from urllib.parse import parse_qs, urlparse
                q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
                gid = q.get("groupcode", "")
                if not gid:
                    continue
                uid = q.get("senderuin") or str(event.get_sender_id())
                logger.info(f"[dafeiyu_impulse] 收到邀请卡片：群 {gid}（{q.get('groupname', '')}）来自 {uid}")
                asyncio.create_task(self._decide_request({
                    "kind": "group", "uid": str(uid), "gid": str(gid), "gname": q.get("groupname", ""),
                    "flag": q.get("msgseq", ""), "comment": "", "card": True,
                    "name": event.get_sender_name() if str(uid) == str(event.get_sender_id()) else ""}))
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 解析邀请卡片失败：{e}")

    async def _in_group(self, gid):
        try:
            return str(gid) in {str(g.get("group_id")) for g in (await self._call("get_group_list", no_cache=True) or [])}
        except Exception:
            return None

    async def _kernel(self, method, args):
        """调 NapCat 桥插件（napcat-plugin-builtin / 大肥鱼桥）暴露的 QQ 内核方法。"""
        return await self._call("dafeiyu_kernel", service="getGroupService", method=method, args=args)

    async def _join_group(self, r):
        """真正把她弄进群：先按系统邀请同意；不行就用内核直接加群；要验证的群就发加群申请。
        返回 joined / applied / failed。"""
        gid = str(r.get("gid") or "")
        if await self._in_group(gid):
            return "joined"
        if r.get("flag") and not r.get("card"):
            try:
                await self._call("set_group_add_request", flag=r["flag"], sub_type="invite", type="invite", approve=True, reason="")
                await asyncio.sleep(5)
                if await self._in_group(gid):
                    return "joined"
            except Exception as e:
                logger.info(f"[dafeiyu_impulse] 按系统邀请同意没成（{e}），改用直接加群")
        note = _clip(r.get("note") or f"{r.get('name') or r.get('uid')}拉我来的~", 40)
        try:
            res = await self._kernel("joinGroup", [{"groupCode": gid}])
            logger.info(f"[dafeiyu_impulse] joinGroup {gid} → {res}")
            await asyncio.sleep(6)
            if await self._in_group(gid):
                return "joined"
            res = await self._kernel("reqToJoinGroup", [{"groupCode": gid, "postscript": note}])
            logger.info(f"[dafeiyu_impulse] reqToJoinGroup {gid} → {res}")
            await asyncio.sleep(4)
            return "joined" if await self._in_group(gid) else "applied"
        except Exception as e:
            logger.warning(f"[dafeiyu_impulse] 直接加群失败：{e}")
            r["error"] = _clip(e, 80)
            return "failed"

    async def _req_loop(self):
        await asyncio.sleep(40)
        while True:
            try:
                await self._poll_invites()
            except Exception as e:
                logger.debug(f"[dafeiyu_impulse] 查群邀请失败：{e}")
            await asyncio.sleep(600)

    async def _poll_invites(self):
        d = await self._call("get_group_system_msg", count=30) or {}
        found = 0
        for it in d.get("invited_requests") or d.get("InvitedRequest") or []:
            if it.get("checked"):
                continue
            found += 1
            await self._decide_request({"kind": "group", "uid": str(it.get("invitor_uin") or ""), "gid": str(it.get("group_id") or ""),
                                        "gname": it.get("group_name") or "", "flag": str(it.get("request_id") or ""),
                                        "comment": it.get("message") or "", "name": it.get("invitor_nick") or ""})
        return found

    def _req_book(self):
        book = self.state.setdefault("requests", {})
        for k in [k for k, v in book.items() if time.time() - v.get("t", 0) > 7 * 86400]:
            book.pop(k)
        return book

    def _req_desc(self, r):
        who = f"「{r.get('name') or r['uid']}」（QQ {r['uid']}）"
        if r["kind"] == "friend":
            return f"{who}申请加你好友" + (f"，验证消息「{_clip(r.get('comment'), 60)}」" if r.get("comment") else "")
        return f"{who}邀请你进群「{r.get('gname') or r.get('gid')}」（群号 {r.get('gid')}）"

    async def _decide_request(self, r):
        key = f"{r['kind']}:{r.get('gid') or r['uid']}"
        book = self._req_book()
        old = book.get(key)
        if old and old.get("flag") == r["flag"] and old.get("status") in ("accepted", "rejected", "asking", "deciding"):
            return
        r.update({"t": int(time.time()), "status": "deciding", "key": key})
        book[key] = r
        if r["kind"] == "group" and await self._in_group(r.get("gid")):
            r["status"] = "accepted"
            r["reason"] = "本来就在群里"
            self._save()
            return
        try:
            info = await self._call("get_stranger_info", user_id=int(r["uid"])) if r["uid"].isdigit() else {}
        except Exception:
            info = {}
        r["name"] = r.get("name") or (info or {}).get("nickname") or r["uid"]
        if r["kind"] == "group" and not r.get("gname"):
            try:
                r["gname"] = (await self._call("get_group_info", group_id=int(r["gid"])) or {}).get("group_name") or ""
            except Exception:
                pass
        owner = r["uid"] == self._owner_qq()
        counts = self._today()
        cap_key, cnt_key = ("friend_accept_per_day", "acc_friend") if r["kind"] == "friend" else ("group_join_per_day", "acc_group")
        cap = int(self.conf.get(cap_key) or 0)  # 0 = 不限（主人 11-14：好友、进群都让她自己定，不设上限）
        decision, reason, greet = "ask", "", ""
        if owner:
            decision, reason = "accept", "主人拉的"
        elif cap and int(counts.get(cnt_key, 0)) >= cap:
            decision, reason = "ask", f"今天已经同意了 {cap} 个，先问问主人"
        else:
            extra = []
            if info:
                extra.append("、".join(x for x in (
                    {"male": "男", "female": "女"}.get(info.get("sex"), ""),
                    f"{info.get('age')}岁" if info.get("age") else "",
                    f"QQ等级{info.get('level') or info.get('qqLevel')}" if (info.get("level") or info.get("qqLevel")) else "",
                    f"签名「{_clip(info.get('long_nick') or info.get('longNick'), 40)}」" if (info.get("long_nick") or info.get("longNick")) else "") if x))
            heart = self._star("astrbot_plugin_dafeiyu_heart")
            try:
                if heart is not None and hasattr(heart, "affection"):
                    extra.append(f"你对他的好感 {heart.affection(r['uid']):.0f}/100")
            except Exception:
                pass
            if r["kind"] == "friend":
                ask = "加不加他好友"
            else:
                try:
                    n = len(await self._call("get_group_list") or [])
                    extra.append(f"你现在已经在 {n} 个群里")
                except Exception:
                    pass
                ask = "进不进这个群"
            raw = await self._llm(
                f"{self._req_desc(r)}。{('（' + '；'.join(x for x in extra if x) + '）') if any(extra) else ''}\n"
                f"按你自己的性格决定{ask}。看着像广告、骗子、引流、陌生色情号就拒绝；拿不准就先问主人。\n"
                '只输出一行 JSON：{"decision": "accept|reject|ask", "reason": "一句理由（会告诉主人）", '
                '"greet": "同意后想先跟对方说的话，想说多少说多少，不想说就空"}')
            m = re.search(r"\{.*\}", raw or "", re.S)
            try:
                j = json.loads(m.group()) if m else {}
            except Exception:
                j = {}
            decision = str(j.get("decision") or "ask").lower()
            decision = decision if decision in ("accept", "reject", "ask") else "ask"
            reason, greet = str(j.get("reason") or ""), self._clean_reply(j.get("greet") or "")
        r["reason"] = reason
        if decision == "ask":
            r["status"] = "asking"
            self._save()
            await self._req_tell_owner(f"{self._req_desc(r)}。{reason or '我拿不准'}，要同意吗？跟我说一声就行。", "收到了申请")
            self._log("request", r["name"], f"{self._req_desc(r)}，问主人")
            return
        await self._apply_request(r, decision == "accept", greet=greet, by_owner=owner)

    async def _apply_request(self, r, approve, greet="", by_owner=False):
        outcome = ""
        try:
            if r["kind"] == "friend":
                await self._call("set_friend_add_request", flag=r["flag"], approve=approve, remark="")
            elif approve:
                outcome = await self._join_group(r)
                if outcome == "failed":
                    raise RuntimeError(r.get("error") or "加群没成功")
            elif r.get("flag") and not r.get("card"):
                await self._call("set_group_add_request", flag=r["flag"], sub_type="invite", type="invite", approve=False, reason="")
        except Exception as e:
            r["status"] = "failed"
            self._save()
            await self._req_tell_owner(f"{self._req_desc(r)}，我想{'同意' if approve else '拒绝'}，但是操作失败了（{_clip(e, 60)}）", "处理申请")
            return f"操作失败：{_clip(e, 80)}"
        r["status"] = "accepted" if approve else "rejected"
        if approve:
            k = "acc_friend" if r["kind"] == "friend" else "acc_group"
            c = self._today()
            c[k] = int(c.get(k, 0)) + 1
        self._save()
        self._log("request", r.get("name") or r["uid"], f"{self._req_desc(r)}，{'同意' if approve else '拒绝'}了：{r.get('reason', '')}")
        gn = r.get("gname") or r.get("gid")
        if outcome == "applied":
            r["status"] = "waiting_admin"
            r["reason"] = (r.get("reason") or "") + "（这个群要验证，已经发了加群申请，等群主/管理员同意）"
            self._save()
        if r["kind"] == "group" and (r["uid"] == self._owner_qq() or by_owner):
            await self._req_tell_owner(("那个群我没进" if not approve else
                                        f"「{gn}」要验证才能进，我已经发了加群申请，群主或管理员在群通知里点同意我就进去了" if outcome == "applied" else
                                        f"进群「{gn}」啦"), "主人拉我进群")
        elif not by_owner:
            extra = "（这个群要验证，已经发了加群申请，等管理员同意）" if outcome == "applied" else ""
            await self._req_tell_owner(f"{self._req_desc(r)}，我{'同意' if approve else '拒绝'}了{extra}" + (f"，{r['reason']}" if r.get("reason") else ""), "处理了申请")
        if approve and greet and r["kind"] == "friend":
            await asyncio.sleep(5)
            umo = f"{self._pid()}:FriendMessage:{r['uid']}"
            try:
                await self.context.send_message(umo, MessageChain().message(greet))
                await self._remember(umo, greet, "刚同意了他的好友申请")
            except Exception as e:
                logger.warning(f"[dafeiyu_impulse] 加好友后打招呼失败：{e}")
        return "已" + ("同意" if approve else "拒绝")

    async def _req_tell_owner(self, text, why):
        umo = self._owner_umo()
        if not umo or not self.conf.get("notify_requests", True):
            return
        try:
            text = await self._voice(text)
            await self.context.send_message(umo, MessageChain().message(text))
            await self._remember(umo, text, why)
        except Exception as e:
            logger.warning(f"[dafeiyu_impulse] 申请的事告诉主人失败：{e}")

    def _req_brief(self):
        book = self._req_book()
        asking = [r for r in book.values() if r.get("status") == "asking"]
        recent = [r for r in book.values() if r.get("status") in ("accepted", "rejected") and time.time() - r["t"] < 86400]
        lines = [f"- {self._req_desc(r)}（还没处理，等主人拍板）" for r in asking]
        lines += [f"- {self._req_desc(r)}（你{'同意' if r['status'] == 'accepted' else '拒绝'}了）" for r in recent[-5:]]
        lines += [f"- {self._req_desc(r)}（这个群要验证，你已经发了加群申请，在等群主/管理员同意）"
                  for r in book.values() if r.get("status") == "waiting_admin" and time.time() - r["t"] < 86400]
        return "\n".join(lines)

    @filter.llm_tool(name="qq_requests")
    async def tool_requests(self, event: AstrMessageEvent):
        '''看最近收到的 QQ 好友申请和群邀请，以及你是怎么处理的。会顺便去 QQ 刷新一次群邀请。'''
        try:
            await self._poll_invites()
        except Exception:
            pass
        return self._req_brief() or "最近没有好友申请和群邀请。"


    # ---------- 主动加好友 ----------

    async def _friend_ids(self):
        try:
            return {str(f.get("user_id")) for f in (await self._call("get_friend_list", no_cache=True) or [])}
        except Exception:
            return set()

    async def _add_friend(self, uid, msg, gid=""):
        """发好友申请。QQ 内核 reqToAddFriends 的参数没实测过：几种格式依次试，记下每次返回。返回 (成功?, 说明)。"""
        try:
            conv = await self._call("dafeiyu_kernel", service="getUixConvertService", method="getUid", args=[[int(uid)]])
            nt = ""
            if isinstance(conv, dict):
                um = conv.get("uidInfo") or conv
                nt = str(um.get(str(uid)) or um.get(int(uid)) or "") if isinstance(um, dict) else ""
            if not nt:
                nt = str(await self._call("dafeiyu_kernel", service="getProfileService", method="getUidByUin",
                                          args=["FriendsServiceImpl", [str(uid)]]) or "")
                m = re.search(r"u_[\w-]+", nt)
                nt = m.group() if m else ""
        except Exception as e:
            return False, f"查不到对方的内部 ID：{_clip(e, 80)}"
        if not nt:
            return False, "查不到对方的内部 ID"
        src = 3004 if gid else 3999
        shapes = [
            {"friendUid": nt, "reqMsg": msg, "remark": "", "groupCode": str(gid or ""), "sourceId": src, "sourceSubId": 0, "addSource": "", "extInfo": ""},
            {"friendUid": nt, "verify": msg, "remark": "", "groupCode": str(gid or ""), "sourceId": src, "sourceSubId": 0},
            {"friendUid": nt, "reqMsg": msg, "sourceId": src, "groupCode": str(gid or "")},
        ]
        tried = []
        for i, p in enumerate(shapes):
            try:
                res = await self._call("dafeiyu_kernel", service="getBuddyService", method="reqToAddFriends", args=[p])
            except Exception as e:
                res = {"error": str(e)}
            tried.append(f"#{i + 1} {_clip(json.dumps(res, ensure_ascii=False, default=str), 120)}")
            logger.info(f"[dafeiyu_impulse] reqToAddFriends 格式{i + 1} → {res}")
            code = res.get("result", res.get("retCode", res.get("code"))) if isinstance(res, dict) else None
            if code == 0 and not (isinstance(res, dict) and res.get("error")):
                return True, f"申请发出去了（格式{i + 1}）"
        return False, "QQ 没让发：" + "；".join(tried)

    def _heart(self):
        return self._star("astrbot_plugin_dafeiyu_heart")

    async def _ev_addfriend(self):
        heart = self._heart()
        if heart is None:
            return "", ""
        me, owner = await self._uin(), self._owner_qq()
        friends = await self._friend_ids()
        users, bufs = heart.data.get("users", {}), heart.data.get("bufs", {})
        min_aff = float(self.conf.get("addfriend_min_affection") or 55)
        cands = []
        for uid, u in users.items():
            if uid in friends or uid in (me, owner) or not uid.isdigit():
                continue
            lines = (bufs.get(uid) or {}).get("lines") or []
            if u.get("count", 0) < 6 or not any(x.get("at") != "私聊" for x in lines):
                continue
            if self.affection_of(heart, uid) < min_aff or not self._cool(f"af{uid}", 14 * 24):
                continue
            cands.append(uid)
        if not cands:
            return "", ""
        uid = self._pick_by_affection(cands)
        u = users[uid]
        lines = (bufs.get(uid) or {}).get("lines") or []
        gname = next((x["at"] for x in reversed(lines) if x.get("at") != "私聊"), "群里")
        gid = next((g for g, v in (heart.data.get("groups") or {}).items() if v.get("name") == gname), "")
        hist = "\n".join(f"{'他' if x['w'] == '他' else '我'}：{x['x']}" for x in lines[-12:]) or "（没记下来）"
        self._mark(f"af{uid}")
        text = (await self._think(
            f"群「{gname}」里的「{u['name']}」你还没加好友。你对他：综合好感 {self.affection_of(heart, uid):.0f}，印象：{u.get('impression') or '还行'}。"
            f"你们最近在群里聊的：\n{hist}\n\n你想不想加他 QQ 好友、以后私聊？想的话，只输出好友申请里的验证消息（20 字内，像真人，比如「群里那个爱吐槽的鱼」）；"
            "不想就只输出 SKIP。", "想加好友"))
        text = _clip(text, 30)
        if not text:
            return u["name"], ""
        return u["name"], await self._do_add(uid, u["name"], text, gid, "自己想加")

    @staticmethod
    def affection_of(heart, uid):
        try:
            return float(heart.affection(uid))
        except Exception:
            return 40.0

    async def _do_add(self, uid, name, msg, gid, why):
        await self._tell_owner(f"我想加「{name}」（{uid}）好友，验证消息写的「{msg}」（{why}）")
        ok, note = await self._add_friend(uid, msg, gid)
        self._log("addfriend", f"{name}({uid})", f"{'发了好友申请' if ok else '加好友没成'}：{msg}｜{note}", ok=ok)
        if not ok:
            await self._tell_owner(f"加「{name}」没成：{_clip(note, 200)}")
        return f"想加好友「{msg}」→ {note}"

    @filter.llm_tool(name="add_qq_friend")
    async def tool_add_friend(self, event: AstrMessageEvent, who: str = "", message: str = ""):
        """你自己想加某个人 QQ 好友时用（比如群里聊得来、想以后私聊）。加不加你自己决定；别人只是嚷着让你加他，你不想就不加。
        会先告诉主人一声再发申请。

        Args:
            who(string): 对方的 QQ 号或昵称；不填就是眼前这个人
            message(string): 好友申请里的验证消息，20 字内，像真人写的
        """
        heart = self._heart()
        w = str(who or "").strip().lstrip("@")
        uid = ""
        if not w:
            uid = str(event.get_sender_id())
        elif w.isdigit():
            uid = w
        elif heart is not None:
            ids = heart._find(w)
            if len(ids) == 1:
                uid = ids[0]
            elif ids:
                return "重名了：" + "、".join(f"{heart.data['users'][i]['name']}({i})" for i in ids)
        if not uid:
            return f"不知道「{w}」是谁，要 QQ 号。"
        if uid in await self._friend_ids():
            return "你们已经是好友了。"
        if uid in (await self._uin(), self._owner_qq()):
            return "不用加。"
        if self._cap_left("addfriend") <= 0:
            return "今天已经加过好几个人了，明天再说。"
        if not self._cool(f"af{uid}", 14 * 24):
            return "前阵子刚加过他，等等再说。"
        self._mark(f"af{uid}")
        self._bump("addfriend")
        name = (heart.data["users"].get(uid, {}).get("name") if heart else "") or event.get_sender_name() or uid
        gid = "" if event.is_private_chat() else str(event.get_group_id() or "")
        return await self._do_add(uid, name, _clip(message or "群里认识的~", 30), gid, "聊天时自己想加")

    @filter.llm_tool(name="join_qq_group")
    async def tool_join_group(self, event: AstrMessageEvent, group_id: str, note: str = ""):
        '''你自己想进某个 QQ 群时用：知道群号就能直接加（不用别人邀请）。要验证的群会发加群申请，等管理员同意。
        进不进、进哪个，你自己决定；看着像广告群、引流群、色情群、政治群就别去。

        Args:
            group_id(string): 群号（纯数字）
            note(string): 加群申请里的留言，像真人一样写一句，比如谁让你来的；不写也行
        '''
        gid = re.sub(r"\D", "", str(group_id or ""))
        if not gid:
            return "群号得是数字。"
        if await self._in_group(gid):
            return "你本来就在这个群里。"
        uid = str(event.get_sender_id())
        r = {"kind": "group", "uid": uid, "gid": gid, "flag": "", "card": True, "comment": "",
             "name": event.get_sender_name(), "t": int(time.time()), "key": f"group:{gid}", "reason": "自己想去"}
        try:
            r["gname"] = (await self._call("get_group_info", group_id=int(gid)) or {}).get("group_name") or ""
        except Exception:
            r["gname"] = ""
        if note:
            r["note"] = note
        outcome = await self._join_group(r)
        r["status"] = {"joined": "accepted", "applied": "waiting_admin"}.get(outcome, "failed")
        self._req_book()[r["key"]] = r
        self._save()
        gn = r["gname"] or gid
        self._log("request", gn, f"自己去加群「{gn}」：{outcome}")
        if outcome == "joined":
            return f"进群「{gn}」了。"
        if outcome == "applied":
            return f"「{gn}」要验证，已经发了加群申请，等群主或管理员同意。"
        return f"加群没成功：{r.get('error') or '不知道为啥'}"

    @filter.llm_tool(name="handle_qq_request")
    async def tool_handle_request(self, event: AstrMessageEvent, who: str, approve: bool):
        '''同意或拒绝一个还没处理的好友申请 / 群邀请。只有主人让你处理时才用（比如主人说「同意吧」「别加」「进那个群」）。

        Args:
            who(string): 对方的昵称、QQ号，或群名、群号；只有一个待处理时可以填「那个」
            approve(boolean): true 同意，false 拒绝
        '''
        if str(event.get_sender_id()) != self._owner_qq():
            return "只有主人能让你处理申请，你可以自己决定先不理。"
        try:
            await self._poll_invites()
        except Exception:
            pass
        asking = [r for r in self._req_book().values() if r.get("status") in ("asking", "failed")]
        if not asking:
            return "现在没有待处理的申请。"
        w = str(who or "").strip()
        hits = [r for r in asking if w and (w in str(r.get("name")) or w in r["uid"] or w in str(r.get("gname")) or w == r.get("gid"))]
        if not hits and len(asking) == 1:
            hits = asking
        if len(hits) != 1:
            return "待处理的有：\n" + "\n".join(f"- {self._req_desc(r)}" for r in asking) + "\n说清楚是哪一个。"
        r = hits[0]
        r["reason"] = "主人让的"
        return await self._apply_request(r, bool(approve), by_owner=True)

    # ---------- 生活感：日程 / 回复节奏 / 防口癖 ----------

    @staticmethod
    def _day_key():
        return (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d")  # 凌晨 5 点前还算前一天

    def _sched(self):
        s = self.state.get("schedule") or {}
        return s if s.get("date") == self._day_key() and s.get("items") else None

    async def _ensure_schedule(self, force=False):
        if self._sched() and not force:
            return self._sched()
        day = datetime.now() - timedelta(hours=5)
        wd = "一二三四五六日"[day.weekday()]
        yest = (self.state.get("schedule") or {}).get("items") or []
        raw = await self._llm(
            (f"今天是 {day.strftime('%Y-%m-%d')} 星期{wd}。按照部署设置的人格安排当天活动。返回 JSON 数组，每项包含 start、end（HH:MM）、doing、place、wear 和 busy（0 至 3）。活动应有合理起止时间。")
            + (f"昨天你大概是这么过的：{'；'.join(i.get('doing', '') for i in yest[:12])}。今天别照抄。\n" if yest else "\n")
            + "只输出一个 JSON 数组，每项 {\"start\": \"HH:MM\", \"end\": \"HH:MM\", \"doing\": \"在干嘛，具体点，比如在三教上高数、老师在点名\", "
              "\"place\": \"在哪\", \"wear\": \"这段时间身上穿的，具体点，比如白衬衫配牛仔半裙、宽松灰T恤睡裤\", \"busy\": 0到3}。"
              "穿着要连贯：出门换了才变，在家就是在家穿的那身，洗完澡换睡衣。"
              "busy：0 闲着随时能回消息；1 有点事、回得慢；2 在忙（上课/洗澡/出门路上）只能偷偷看手机；3 睡着了。"
              "从 00:00 排到 24:00 全覆盖，睡觉也写进去（一般凌晨一点前后睡、早上七八点起）。")
        m = re.search(r"\[.*\]", raw or "", re.S)
        items = []
        for it in json.loads(m.group()) if m else []:
            try:
                s, e = str(it["start"])[:5], str(it["end"])[:5]
                datetime.strptime(s, "%H:%M")
                if e != "24:00":
                    datetime.strptime(e, "%H:%M")
                items.append({"start": s, "end": e, "doing": _clip(it.get("doing"), 60), "place": _clip(it.get("place"), 20),
                              "wear": _clip(it.get("wear"), 30),
                              "busy": max(0, min(3, int(it.get("busy", 0))))})
            except Exception:
                continue
        if len(items) < 4:
            raise RuntimeError("日程生成失败：" + _clip(raw, 100))
        self.state["schedule"] = {"date": self._day_key(), "items": sorted(items, key=lambda x: x["start"])}
        self._save()
        logger.info(f"[dafeiyu_impulse] 排好了今天的日程，共 {len(items)} 段")
        return self.state["schedule"]

    def _raw_item(self):
        s = self._sched()
        if not s:
            return None
        hm = datetime.now().strftime("%H:%M")
        cur = None
        for it in s["items"]:
            end = it["end"] if it["end"] > it["start"] else "24:00"
            if it["start"] <= hm < end:
                return it
            if it["start"] <= hm:
                cur = it
        return cur or s["items"][-1]

    def _now_item(self):
        it = self._raw_item()
        if it and it["busy"] == 3 and self.state.get("woke", {}).get("key") == f"{self._day_key()} {it['start']}":
            return {**it, "busy": 0, "doing": "已经起来好一阵了，醒着躺着玩手机（这事过去了，聊天里不用再提睡觉、醒没醒）"}
        return it

    def _wake_up(self):
        """睡着时回了消息就算醒了：这一段日程剩下的时间按醒着算，不会每句都「刚醒」。"""
        it = self._raw_item()
        if it and it["busy"] == 3:
            key = f"{self._day_key()} {it['start']}"
            if self.state.get("woke", {}).get("key") != key:
                self.state["woke"] = {"key": key, "t": time.time()}
                self._save()
                return True
        return False

    def _life_prompt(self):
        it = self._now_item()
        s = self._sched()
        if not it or not s:
            return ""
        plan = "；".join(f"{i['start']} {i['doing']}" for i in s["items"])
        wear = f"，身上穿着{it['wear']}" if it.get("wear") else ""
        return (f"【你现在】{datetime.now().strftime('%H:%M')}，你正在{it['doing']}（{it['place']}{wear}），{BUSY_TEXT.get(it['busy'], '')}。\n"
                f"今天的安排：{_clip(plan, 700)}\n"
                "这是给你自己心里有数的，不是要你说出来的。只有别人问你在干嘛、等会儿干嘛、今天干了啥，才照这个说，前后要对得上；"
                "别人没问就别提——真人回消息不会交代「我在打游戏」「剧看到一半」「手上全是薯片渣」，"
                "也别拿手上的事当回话的理由或开场白。忙不忙只体现在回得快慢、长短上。")

    @staticmethod
    def _overused(texts, min_hits=3):
        """她最近说过的话里，哪些话题/说法用得太多了。"""
        texts = [str(t) for t in texts if t][-14:]
        if len(texts) < 4:
            return []
        out = []
        for name, pat in THEMES.items():
            n = sum(1 for t in texts if re.search(pat, t))
            if n >= min_hits:
                out.append(f"{name}（{n} 次）")
        grams = {}
        for t in texts:
            clean = re.sub(r"[^一-鿿]", " ", t)
            seen = set()
            for w in clean.split():
                for L in (3, 4):
                    for i in range(len(w) - L + 1):
                        g = w[i:i + L]
                        if g not in seen:
                            seen.add(g)
                            grams[g] = grams.get(g, 0) + 1
        common = {"是不是", "怎么了", "什么时候", "一下子", "这个那个", "有没有", "没什么", "知道了"}
        rep = [g for g, n in sorted(grams.items(), key=lambda x: -x[1]) if n >= min_hits and g not in common]
        picked = []
        for g in rep:
            if not any(g in p or p in g for p in picked):
                picked.append(g)
        out += [f"「{g}」" for g in picked[:3]]
        return out

    async def _voice(self, fact, who="主人"):
        """系统通知改成她自己的口气说。"""
        # 改写时模型偶尔把这当成「让你扮演身份」，吐出「不玩这个设定」这类出戏话（11-15 04:09 发给了主人）：
        # 改写结果先过一遍出戏检查，坏了重想一次，还坏就直接用原话（原话本身就是她能说的大白话）。
        # 11-15 主人：判断/改写的模型没上下文才乱拒绝 → 带上跟他最近的私聊，让模型知道这是接着聊天。
        hist = ""
        try:
            uid = self._owner_qq() if who == "主人" else ""
            if uid:
                hist = self._fmt_history(await self._friend_history(uid, 12), await self._uin(), 12)
        except Exception:
            pass
        ctx = f"你们俩最近的私聊（接着这个聊下去）：\n{hist}\n" if hist else ""
        for _ in range(2):
            try:
                t = self._clean_reply(await self._llm(
                    f"{self._life_prompt()}\n{ctx}你要私聊告诉{who}一件你自己刚做的事：{fact}\n"
                    "用你平时随手发 QQ 的语气说这事，别像系统通知，别用书名号括群名，不用每个细节都说。只输出要发的话。"))
            except Exception:
                return fact
            if t and not await self._ooc(t, "改口气"):
                return t
        return fact

    def _note_incoming(self, event):
        """记下每个会话里谁刚发了消息、有没有人@她（分段回复时用来判断有没有被打断）。"""
        try:
            sender = str(event.get_sender_id())
            if not sender or sender == str(event.get_self_id()):
                return
            now = time.time()
            event.set_extra("_dfy_recv_at", now)
            box = self._last_in.setdefault(event.unified_msg_origin, {})
            box[sender] = now
            _mid = getattr(event.message_obj, "message_id", None)
            if _mid:
                _q = self._recent_msgs.setdefault(event.unified_msg_origin, [])
                _q.append((now, sender, str(event.get_sender_name() or ""), str(_mid)))
                del _q[:-40]
            me = str(event.get_self_id())
            if any(isinstance(c, At) and str(getattr(c, "qq", "")) == me for c in (event.message_obj.message or [])):
                box["@me"] = now
        except Exception:
            pass

    @filter.on_decorating_result()
    async def interruptible(self, event: AstrMessageEvent):
        """分段发的时候，对方插话了就别自顾自说完：剩下的先不发，记下来，让她先回新消息。"""
        if not self.conf.get("interruptible", True):
            return
        try:
            if event.get_platform_name() != "aiocqhttp":
                return
            result = event.get_result()
            if not result or not result.chain:
                return
            umo, who, private = event.unified_msg_origin, str(event.get_sender_id()), event.is_private_chat()
            # 多轮工具调用时每段结果都会进来一次：始终包在最原始的 send 上，别一层套一层，
            # 否则上一段（比如「我再对一下」）的发送时间会被拿来判断最终回答，整段答案被当成打断丢掉
            orig = event.get_extra("_dfy_orig_send") or event.send
            event.set_extra("_dfy_orig_send", orig)
            st = {"sent": 0, "cut": False, "first_at": 0.0}  # first_at：第一段实际发出的时间，之后进来的消息才算打断

            async def send(chain, _orig=orig):
                comps = list(getattr(chain, "chain", None) or [])
                plain = [c for c in comps if isinstance(c, Plain)]
                if st["cut"]:
                    return None
                if plain and st["sent"] >= 1:
                    box = self._last_in.get(umo, {})
                    since = st["first_at"]
                    # 只有正在跟她说话的这个人又开口才算打断；群里别人@她，她会先把手上这句说完
                    if box.get(who, 0) > since + 0.3:
                        st["cut"] = True
                        rc = result.chain
                        idx = next((i for i, c in enumerate(rc) if c is plain[0]), None)
                        rest = rc[idx:] if idx is not None else plain
                        unsaid = [c.text for c in rest if isinstance(c, Plain) and c.text.strip()]
                        if idx is not None:
                            del rc[idx + 1:]  # 发送循环遍历的就是这个列表，删掉后面的它就停了
                        self._cut[f"{umo}|{who}"] = {"t": time.time(), "unsaid": unsaid}
                        logger.info(f"[dafeiyu_impulse] 说到一半被打断，剩 {len(unsaid)} 段没发：{_clip(' '.join(unsaid), 40)}")
                        return None
                if plain:
                    st["sent"] += 1
                    if st["sent"] == 1:
                        st["first_at"] = time.time()
                return await _orig(chain)

            event.send = send
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 挂打断检测失败：{e}")

    @filter.on_waiting_llm_request()
    async def pace(self, event: AstrMessageEvent):
        """像人一样回消息：按她现在在干嘛决定多久回；等的时候对方又连发几条，就合在一起回一次。
        挂在「拿会话锁之前」，等的时候不会把同一会话后面的消息堵住。"""
        if not self.conf.get("human_pace", True):
            return
        try:
            if event.get_platform_name() != "aiocqhttp":
                return
            umo = event.unified_msg_origin
            it = self._now_item()
            busy = it["busy"] if it else 0
            lo, hi = {0: (1, 4), 1: (4, 12), 2: (10, 30), 3: (20, 60)}[busy]
            d = random.uniform(lo, hi)
            if str(event.get_sender_id()) == self._owner_qq():
                d *= 0.6
            heart = self._star("astrbot_plugin_dafeiyu_heart")
            try:
                if heart is not None and heart.data["self"].get("energy", 80) < 30:
                    d *= 1.3
            except Exception:
                pass
            d = min(d, float(self.conf.get("max_reply_delay") or 60))
            ts = float(getattr(event.message_obj, "timestamp", 0) or time.time())
            if not event.is_private_chat():  # 群里不等：群聊插件本身就有判断节奏，再等会排队、回到过时的消息
                age = time.time() - ts
                if age > float(self.conf.get("group_stale_seconds") or 300):
                    logger.info(f"[dafeiyu_impulse] 群消息已经过去 {int(age // 60)} 分钟了，不回：{_clip(event.message_str, 30)}")
                    event.stop_event()
                    return
                if self._wake_up():
                    event.set_extra("_dfy_just_woke", True)
                return
            box = self._pacing.setdefault(umo, {"token": 0, "texts": [], "deadline": 0})
            box["token"] += 1
            mine = box["token"]
            box["texts"].append(_clip(event.message_str, 200))
            box["deadline"] = max(box["deadline"], ts + d, time.time() + 2)
            wait = box["deadline"] - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            if box["token"] != mine:  # 等的时候他又发了，交给最后那条一起回
                event.stop_event()
                return
            earlier = [t for t in box["texts"][:-1] if t]
            self._pacing.pop(umo, None)
            if self._wake_up():
                event.set_extra("_dfy_just_woke", True)
            if earlier:
                event.set_extra("_dfy_merged", earlier)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[dafeiyu_impulse] 回复节奏出错：{e}")

    # ---------- QQ 命令 ----------

    @filter.command("念头")
    async def cmd_fire(self, event: AstrMessageEvent, kind: str = ""):
        """让她现在冒一个念头（管理员）：/念头 或 /念头 群 / 主人 / at / 好友 / B站 / 贴吧 / 资料"""
        if not event.is_admin():
            return
        alias = {"主人": "owner", "群": "group", "at": "at", "@": "at", "好友": "friend", "b站": "bili", "B站": "bili",
                 "贴吧": "tieba", "资料": "profile", "发呆": "idle"}
        key = alias.get(kind.strip(), kind.strip() if kind.strip() in EVENTS else None)
        r = await self._fire(key)
        yield event.plain_result(f"念头：{EVENTS[r['kind']][0]}"
                                 + (f" → {r.get('target')}" if r.get("target") else "")
                                 + f"\n{r.get('text') or '想了想，算了'}")

    # ---------- 控制中心页面 ----------

    async def _json(self, fn):
        try:
            payload, code = {"ok": True, "data": await fn()}, 200
        except Exception as e:
            logger.warning(f"[dafeiyu_impulse] 页面接口出错：{e!r}")
            payload, code = {"ok": False, "message": _clip(e, 200), "error": {"message": _clip(e, 200)}}, 400
        if _jsonify is None:
            return payload
        resp = _jsonify(payload)
        resp.status_code = code
        return resp

    async def page_diag_call(self):
        """排查用：后台直接调一个 OneBot 接口（只有登录了后台的人能用）。"""
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            return await self._call(str(body.get("action") or ""), **(body.get("params") or {}))
        return await self._json(fn)

    async def page_diag_vision(self):
        """维护用：让识图模型看一张本机图片（整理表情包时用）。body: {path, prompt, provider_id?}"""
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            pid = str(body.get("provider_id") or "grok/grok-4.6")
            prov = self.context.get_provider_by_id(pid)
            if prov is None:
                raise RuntimeError(f"没有这个模型：{pid}")
            resp = await asyncio.wait_for(prov.text_chat(
                prompt=str(body.get("prompt") or "描述这张图"), session_id=uuid.uuid4().hex,
                image_urls=[str(body.get("path") or "")], persist=False), timeout=90)
            return {"text": getattr(resp, "completion_text", "") or ""}
        return await self._json(fn)

    # ---------- 总控制面板：QQ 登录、音乐账号、表情包、插件推荐 ----------

    @staticmethod
    async def _sh(*args, timeout=30):
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
        return p.returncode, (out or b"").decode(errors="replace").strip()

    async def page_qq(self):
        """QQ 在不在线；不在线时给 NapCat 最新的登录二维码（不用再开 NapCat 的网页）。"""
        async def fn():
            info = {}
            try:
                info = await asyncio.wait_for(self._call("get_login_info"), timeout=8) or {}
            except Exception:
                pass
            online = bool(info.get("user_id"))
            out = {"online": online, "nick": info.get("nickname", ""), "uin": str(info.get("user_id") or "")}
            qr = str(self.conf.get("napcat_qr_path") or NAPCAT_QR)
            if not online and os.path.exists(qr):
                out["qr_age"] = int(time.time() - os.path.getmtime(qr))
                with open(qr, "rb") as f:
                    out["qr"] = "data:image/png;base64," + __import__("base64").b64encode(f.read()).decode()
            for unit, key in (("dafeiyu-napcat", "napcat"), ("dafeiyu-mihomo", "proxy")):
                try:
                    _, out[key] = await self._sh("systemctl", "is-active", unit, timeout=5)
                except Exception:
                    out[key] = "unknown"
            return out
        return await self._json(fn)

    async def page_qq_refresh(self):
        """重启 NapCat：二维码过期了、或者卡住了，换一张新码。"""
        async def fn():
            code, msg = await self._sh("sudo", "-n", "systemctl", "restart", "dafeiyu-napcat", timeout=60)
            if code != 0:
                raise RuntimeError(f"重启 NapCat 失败：{_clip(msg, 120)}")
            return {"ok": True}
        return await self._json(fn)

    def _music_providers(self):
        bm = self._star("astrbot_plugin_better_music")
        if bm is None or not hasattr(bm, "providers"):
            raise RuntimeError("「更好的点歌」插件没装或没启用")
        return bm.providers

    async def page_music(self):
        async def fn():
            prov = self._music_providers()
            names = {"kugou": "酷狗", "netease": "网易云", "qq": "QQ 音乐"}
            accounts = []
            for p, n in names.items():
                try:
                    st = await asyncio.wait_for(prov.chat_account_status(p), timeout=20)
                except Exception as e:
                    st = {"status": "unknown", "message": str(e)}
                accounts.append({"id": p, "name": n, "status": st.get("status"), "message": _clip(st.get("message"), 80)})
            sing = self._star("astrbot_plugin_dafeiyu_sing")
            day = getattr(sing, "_day", {}) if sing else {}
            conf = getattr(sing, "conf", {}) if sing else {}
            recent = []
            for r in (sing.recent(20) if sing else []):
                recent.append({**r, "time": datetime.fromtimestamp(r.get("t", 0)).strftime("%m-%d %H:%M")})
            return {"accounts": accounts,
                    "sing_today": day.get("n", 0) if day.get("d") == datetime.now().strftime("%Y-%m-%d") else 0,
                    "settings": {"platform_order": conf.get("platform_order") or "kugou,netease,qq",
                                 "max_seconds": int(conf.get("max_seconds") or 0),
                                 "daily_limit": int(conf.get("daily_limit") or 30)},
                    "recent": recent}
        return await self._json(fn)

    def _sing(self):
        s = self._star("astrbot_plugin_dafeiyu_sing")
        if s is None:
            raise RuntimeError("放歌插件没启用")
        return s

    async def page_music_logout(self):
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            return self._login_public(await self._music_providers().logout(str(body.get("provider") or "")))
        return await self._json(fn)

    async def page_music_search(self):
        async def fn():
            q = str(_request.args.get("q") or "").strip()
            if not q:
                raise RuntimeError("输入要搜的歌")
            platform, cands = await self._sing().search(q, str(_request.args.get("platform") or ""))
            return {"platform": platform, "results": cands}
        return await self._json(fn)

    async def page_music_play(self):
        """试放：发到主人私聊（不往群里发，免得测试打扰别人）。"""
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            owner = self._owner_qq()
            if not owner:
                raise RuntimeError("不知道主人的 QQ")
            pick = body.get("pick") if isinstance(body.get("pick"), dict) else None
            return await self._sing().play(f"{self._pid()}:FriendMessage:{owner}", str(body.get("song") or (pick or {}).get("title") or ""),
                                           pick=pick, platform=str(body.get("platform") or ""), who="控制面板", where="主人私聊")
        return await self._json(fn)

    async def page_music_settings(self):
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            s = self._sing()
            order = ",".join(p for p in re.split(r"[,，\s]+", str(body.get("platform_order") or "")) if p in ("kugou", "netease", "qq"))
            s.conf["platform_order"] = order or "kugou,netease,qq"
            s.conf["max_seconds"] = max(0, int(body.get("max_seconds") or 0))
            s.conf["daily_limit"] = max(1, int(body.get("daily_limit") or 30))
            if hasattr(s.conf, "save_config"):
                s.conf.save_config()
            return {"ok": True}
        return await self._json(fn)

    @staticmethod
    def _login_public(r):
        r = r if isinstance(r, dict) else {}
        out = {"status": str(r.get("status") or r.get("state") or "error"), "message": str(r.get("message") or "")}
        q = str(r.get("qr_image") or "")
        if q.startswith("data:image/"):
            out["qr"] = q
        return out

    async def page_music_login(self):
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            return self._login_public(await self._music_providers().start_login(str(body.get("provider") or "kugou")))
        return await self._json(fn)

    async def page_music_login_status(self):
        async def fn():
            return self._login_public(await self._music_providers().login_status(str(_request.args.get("provider") or "kugou")))
        return await self._json(fn)

    async def page_memes(self):
        async def fn():
            root = os.path.join(MEME_ROOT, "dafeiyu-picked", "memes")
            cats, web = [], 0
            if os.path.isdir(root):
                for c in sorted(os.listdir(root)):
                    files = [f for f in os.listdir(os.path.join(root, c)) if not f.startswith(".")]
                    w = sum(f.startswith("web_") for f in files)
                    web += w
                    cats.append({"cat": c, "n": len(files), "web": w})
            try:
                desc = json.load(open(os.path.join(MEME_ROOT, "dafeiyu-picked", "memes_data.json"), encoding="utf-8"))
            except Exception:
                desc = {}
            for c in cats:
                c["desc"] = _clip(desc.get(c["cat"], ""), 40)
            recent = []
            try:
                caps = json.load(open("/opt/dafeiyu/private/meme_captions.json", encoding="utf-8"))
                recent = [{"text": v.get("text", ""), "scene": v.get("scene", ""), "cat": k.split("/")[1]}
                          for k, v in caps.items() if isinstance(v, dict) and v.get("from") == "web"][-8:][::-1]
            except Exception:
                pass
            ms = self._star("astrbot_plugin_dafeiyu_memesearch")
            day = (getattr(ms, "state", {}) or {}).get("day", {}) if ms else {}
            mm = {}
            try:
                mm = json.load(open(os.path.join(get_astrbot_data_path(), "config", "meme_manager_config.json"), encoding="utf-8-sig"))
            except Exception:
                pass
            return {"cats": cats, "total": sum(c["n"] for c in cats), "web": web, "recent": recent,
                    "search_today": day.get("n", 0) if day.get("d") == datetime.now().strftime("%Y-%m-%d") else 0,
                    "probability": ((mm.get("generation") or {}).get("emotion") or {}).get("probability")}
        return await self._json(fn)

    def _xs(self):
        x = self._star("astrbot_plugin_dafeiyu_x")
        if x is None:
            raise RuntimeError("刷推特的插件没启用")
        return x

    async def page_x(self):
        async def fn():
            x = self._xs()
            me, err = None, ""
            if x.cookies_ok():
                try:
                    me = await asyncio.wait_for(x.whoami(), timeout=25)
                except Exception as e:
                    err = _clip(e, 160)
            day = x._today()
            fmt = lambda t: datetime.fromtimestamp(t).strftime("%m-%d %H:%M")
            items = sorted(x.state.get("items", [])[-60:], key=lambda i: -i.get("t", 0))[:20]
            return {
                "logged": x.cookies_ok(), "me": me, "error": err, "enabled": bool(x.conf.get("enable", True)),
                "comment_enable": bool(x.conf.get("comment_enable", True)),
                "login_state": getattr(x, "login_state", ""),
                "today": day, "limits": {"sessions": x.conf.get("sessions_per_day") or 8, "comments": x.conf.get("comments_per_day") or 5,
                                         "shares": x.conf.get("shares_per_day") or 6},
                "nsfw": {"enable": bool(x.conf.get("nsfw_share_enable", True)), "threshold": x.conf.get("nsfw_threshold") or 85,
                         "owner_ok": x.rel_ok(self._owner_qq())},
                "running": bool(x._running and not x._running.done()),
                "next_at": datetime.fromtimestamp(x.state.get("next_at", 0)).strftime("%H:%M") if x.state.get("next_at") else "",
                "log": [{**l, "time": fmt(l["t"])} for l in reversed(x.state.get("log", [])[-40:])],
                "items": [{"time": fmt(i["t"]), "who": i.get("handle"), "gist": i.get("gist"), "url": i.get("url"),
                           "interest": i.get("interest"), "nsfw": i.get("nsfw"), "media": len(i.get("media") or []),
                           "shared": i.get("shared"), "commented": i.get("commented", "")} for i in items],
            }
        return await self._json(fn)

    async def page_x_cookies(self):
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            x = self._xs()
            if body.get("clear"):
                x.clear_cookies()
                return {"ok": True}
            if body.get("password"):
                await x.login_password(body.get("user"), body.get("password"), body.get("extra") or "")
            elif body.get("paste"):
                x.set_cookies_any(body.get("paste"))
            else:
                x.set_cookies(body.get("auth_token"), body.get("ct0"))
            try:
                return {"ok": True, "me": await asyncio.wait_for(x.whoami(), timeout=30)}
            except Exception as e:
                return {"ok": True, "warn": f"存好了，但试登录失败：{_clip(e, 160)}"}
        return await self._json(fn)

    async def page_x_browser(self):
        async def fn():
            return {"url": await self._xs().start_browser_login()}
        return await self._json(fn)

    async def page_x_run(self):
        async def fn():
            x = self._xs()
            if x._running and not x._running.done():
                raise RuntimeError("她正在刷，等这一轮完")
            x._running = asyncio.create_task(x.session("你让她刷"))
            return await asyncio.wait_for(asyncio.shield(x._running), timeout=240)
        return await self._json(fn)

    async def page_x_toggle(self):
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            x = self._xs()
            for k in ("enable", "comment_enable", "nsfw_share_enable"):
                if k in body:
                    x.conf[k] = bool(body[k])
            if hasattr(x.conf, "save_config"):
                x.conf.save_config()
            return {"ok": True}
        return await self._json(fn)

    async def page_plugins(self):
        async def fn():
            ps = self._star("astrbot_plugin_dafeiyu_pluginscout")
            if ps is None:
                raise RuntimeError("找插件的插件没启用")
            recs = sorted(ps.state.get("recs", []), key=lambda r: -r.get("t", 0))[:20]
            return {"recs": [{**r, "time": datetime.fromtimestamp(r.get("t", 0)).strftime("%m-%d %H:%M")} for r in recs]}
        return await self._json(fn)

    async def page_plugins_act(self):
        async def fn():
            body = await _request.get_json(force=True, silent=True) or {}
            ps = self._star("astrbot_plugin_dafeiyu_pluginscout")
            if ps is None:
                raise RuntimeError("找插件的插件没启用")
            n = int(body.get("n") or 0)
            if body.get("action") == "install":
                return {"message": await ps.install_rec(n)}
            return {"message": ps.reject_rec(n)}
        return await self._json(fn)

    async def page_schedule(self):
        """看今天的日程；?redo=1 重排。"""
        async def fn():
            if _request is not None and _request.args.get("redo"):
                await self._ensure_schedule(force=True)
            return {"now": self._now_item(), "schedule": self._sched()}
        return await self._json(fn)

    async def page_requests(self):
        async def fn():
            raw = await self._call("get_group_system_msg", count=30)
            found = await self._poll_invites()
            groups = [f"{g.get('group_name')}({g.get('group_id')})" for g in (await self._call("get_group_list") or [])]
            return {"raw": raw, "found": found, "book": self._req_book(), "groups": groups}
        return await self._json(fn)

    async def page_status(self):
        async def fn():
            counts = self._today()
            events = []
            for key, (label, wkey, ckey) in EVENTS.items():
                events.append({"key": key, "label": label, "weight": self.conf.get(wkey, 0),
                               "cap": self.conf.get(ckey, None) if ckey else None, "today": counts.get(key, 0)})
            groups = []
            try:
                for gid, name in await self._groups():
                    f = self.state["feedback"].get(gid, {})
                    groups.append({"gid": gid, "name": name, "posts": f.get("posts", 0), "replied": f.get("replied", 0),
                                   "weight": round(self._group_weight(gid), 2)})
            except Exception:
                pass
            log = self._load(self.log_path, [])
            return {
                "enabled": bool(self.conf.get("enable", True)), "quiet": self._quiet(),
                "quiet_hours": self.conf.get("quiet_hours"),
                "interval": [self.conf.get("interval_min"), self.conf.get("interval_max")],
                "next_at": datetime.fromtimestamp(self.state.get("next_at", 0)).strftime("%H:%M") if self.state.get("next_at") else "",
                "running": bool(self._running and not self._running.done()),
                "events": events, "groups": groups,
                "log": [{**i, "time": datetime.fromtimestamp(i["t"]).strftime("%m-%d %H:%M"),
                         "label": "钱" if i["kind"] == "money" else EVENTS.get(i["kind"], (i["kind"],))[0]}
                        for i in reversed(log[-60:])],
            }
        return await self._json(fn)

    async def page_run(self):
        async def fn():
            body = {}
            try:
                body = await _request.get_json(silent=True) or {}
            except Exception:
                pass
            key = body.get("kind") if body.get("kind") in EVENTS else None
            if self._running and not self._running.done():
                raise RuntimeError("她正在想上一个念头，等一下")
            self._running = asyncio.create_task(self._fire(key))
            return await asyncio.wait_for(asyncio.shield(self._running), 170)
        return await self._json(fn)

    async def page_toggle(self):
        async def fn():
            body = {}
            try:
                body = await _request.get_json(silent=True) or {}
            except Exception:
                pass
            on = bool(body.get("enabled")) if "enabled" in body else not self.conf.get("enable", True)
            self.conf["enable"] = on
            try:
                self.conf.save_config()
            except Exception as e:
                logger.warning(f"[dafeiyu_impulse] 保存配置失败：{e}")
            return {"enabled": on}
        return await self._json(fn)

    async def page_diag_voice(self):
        """诊断：取最近发给主人的语音，看 QQ 服务器上的文件能不能取回。"""
        async def fn():
            t = str((_request.args or {}).get("tts") or "") if _request is not None else ""
            if t:  # 诊断：走她真实的语音链路合成一段
                prov = self.context.get_using_tts_provider(self._owner_umo() or None)
                p = await prov.get_audio(t)
                return {"file": p, "wrapped": hasattr(prov.get_audio, "_dafeiyu_orig")}
            f = str((_request.args or {}).get("file") or "") if _request is not None else ""
            if f:  # 诊断：按文件名向 NapCat 要语音
                r = await self._call("get_record", file=f, out_format="wav")
                p = (r or {}).get("file") or ""
                return {"file": p, "exists": bool(p) and os.path.exists(p), "size": os.path.getsize(p) if p and os.path.exists(p) else 0}
            owner = self._owner_qq()
            h = await self._friend_history(owner, 30)
            me = await self._uin()
            out = []
            for m in reversed(h):
                if str((m.get("sender") or {}).get("user_id")) != me:
                    continue
                for seg in m.get("message") or []:
                    if seg.get("type") != "record":
                        continue
                    d = seg.get("data") or {}
                    item = {"time": datetime.fromtimestamp(int(m.get("time", 0))).strftime("%m-%d %H:%M"),
                            "keys": sorted(d.keys()), "file_size": d.get("file_size"), "has_url": bool(d.get("url"))}
                    try:
                        r = await self._call("get_record", file=d.get("file") or d.get("file_id"), out_format="mp3")
                        item["get_record"] = {k: (v if k != "base64" else len(v or "")) for k, v in (r or {}).items() if k in ("file", "file_size", "base64")}
                    except Exception as e:
                        item["get_record_err"] = _clip(e, 160)
                    if d.get("url"):
                        try:
                            async with aiohttp.ClientSession() as s:
                                async with s.get(d["url"], timeout=aiohttp.ClientTimeout(total=15)) as resp:
                                    body = await resp.read()
                                    item["url_status"], item["url_bytes"], item["url_head"] = resp.status, len(body), body[:10].hex()
                        except Exception as e:
                            item["url_err"] = _clip(e, 120)
                    out.append(item)
                if len(out) >= 3:
                    break
            return {"records": out}
        return await self._json(fn)

    async def page_profiles(self):
        async def fn():
            return await self._profiles(describe=True)
        return await self._json(fn)




