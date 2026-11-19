"""大肥鱼知识库：她待过的群、刷到过的东西，全都记得。

存（永久保留，不删）：
- 群消息：她在的每个群，每条消息都记下（图片按看图插件换成的「[图片：……]」记），她自己在群里说的也记；
  第一次启动时用 NapCat 把各群能拉到的历史补进来。
- 刷到的：贴吧日记、推特刷到的帖子和她的评论、B 站看过的视频和她的评价。那几个插件自己只留最近几十条，
  这里每 10 分钟收一次，赶在被挤掉之前存下。
- 表情包：群友（私聊里朋友）发的表情包，先看一眼：图上写了啥、什么场合用（意义）、能不能收
  （色情/血腥/政治/武器/AI 梗、涉及未成年的一律不收）；能收的去重后放进「大肥鱼精选」包对应分类，带上意义和出处。

整理（她自己用主模型做）：每个群攒够一批新消息就读一遍，整理出这个群的梗和黑话、聊过的话题、发生的事、
对每个群友的印象（只记他自己在这个群里公开说过的）。

用：
- 群里聊天前，自动带上跟这句话相关的：说话这个人的印象、群里的梗、相关的事、她刷到过的相关内容。
  A 群的事不会带到 B 群；私聊的内容从来不进来；主人私聊她时能想起所有群的事。
- 工具 recall_memory：主动回忆（「那天谁说过……」「XX 是个什么样的人」）。
- 工具 send_collected_meme：按意思从收来的表情包里挑一张发。
- 工具 forget_me：有人自己说「别记我」，删掉对他的印象、以后不再写（聊天原文照样留着）。
"""

import asyncio
import hashlib
import json
import os
import random
import re
import time
import uuid
from datetime import datetime

import aiohttp

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .store import Store, overlap

DATA = get_astrbot_data_path()
PD = os.path.join(DATA, "plugin_data")
PACK = os.path.join(PD, "meme_manager", "packs", "dafeiyu-picked")
CAPS = "/opt/dafeiyu/private/meme_captions.json"
CATS = {
    "happy": "开心、好耶", "smug": "得意、嘚瑟", "pout": "嘴硬、哼、不服气", "speechless": "无语、问号、懵",
    "shy": "害羞、脸红", "sad": "委屈、哭、可怜", "hungry": "饿了、馋", "eating": "在吃东西", "sleepy": "困、要睡了",
    "lazy": "摆烂、躺平", "greet": "打招呼、冒泡", "love": "贴贴、喜欢、谢谢、安慰", "shock": "震惊、吓到",
    "angry": "小生气（没武器）", "okay": "好的、收到", "no": "拒绝、不要", "tease": "坏笑、看戏、调侃", "cheer": "加油、庆祝",
}
MEME_JUDGE = (
    "这是 QQ 上有人发的一张表情包（动图的话左边第一帧、右边中间一帧）。只输出一个 JSON：\n"
    '{"is_meme":这是不是表情包（聊天截图、照片、长图、文字截图、广告算 false）,'
    '"text":"图上的字原样照抄，没有就空","scene":"画面一句话",'
    '"meaning":"这张表情包一般在什么场合发、表达什么态度或情绪（一句话，像跟朋友解释梗）",'
    '"bad":有没有色情、露骨、性暗示、血腥、恶心、政治、广告水印、刀枪等武器、AI/代码/模型梗（true/false）,'
    '"minor":画面里有没有未成年人或幼态角色并且带一点性暗示（true/false）,'
    '"cat":"从这些里选一个最贴的分类键：' + ", ".join(f"{k}({v})" for k, v in CATS.items()) + '"}'
)
DIGEST = """你是「大肥鱼」（21 岁女大学生，群里的一员），在整理自己对 QQ 群「{gname}」的记忆。
下面是这个群新的一段聊天（[时间] 名字(QQ号)：内容；名字是「大肥鱼」或标了(我)的是你自己说的）：
{chat}

你之前已经知道的：
群里的梗/黑话：{memes}
群友印象：{people}

只输出一个 JSON：
{{"memes":[{{"key":"梗或黑话本身","meaning":"在这个群里是什么意思、怎么来的、一般怎么用","example":"一句原话"}}],
 "topics":[{{"title":"这段大家聊了什么（短标题）","summary":"谁说了什么、聊出了什么结论或态度，2~4 句"}}],
 "events":[{{"title":"值得记住的事","summary":"经过，带上是谁","when":"大概时间"}}],
 "people":[{{"uid":"QQ号","name":"名字","impression":"把你对这个人的完整印象重写一遍（合并旧印象和这次新看到的）：爱聊什么、性格和说话风格、跟你的关系和态度、他自己提过的个人情况。3~6 句"}}]}}
规则：只写从记录里真看得出来的，不编；没有就给空列表；people 不写你自己，只写这段里说过话并且有新认识的人；
话题和事件别写成流水账，挑值得以后想起来的。"""

LEARN = """你是「大肥鱼」（21 岁女大学生，群里的一员）。你在琢磨 QQ 群「{gname}」的人是怎么说话的，想学会跟他们一个味儿。
下面是这个群的一段聊天（[时间] 名字(QQ号)：内容；标了(我)的是你自己说的，只用来看前后文，别学你自己）：
{chat}

你之前已经学会的说法（别重复，有新用法就补充）：{known}
你之前对这个群说话风格的感觉：{style}

把这段里值得学的「说法」挑出来，重点是真人才会的那些：
- 阴阳怪气、冷嘲热讽、反讽（字面夸、实际损）
- 暗指、含沙射影、话里有话、谐音梗、黑话
- 段子、玩梗、接梗的套路、互损的方式
- 口头禅、语气词、标点和打字习惯（比如句尾不加标点、爱用「。」表示无语、爱打 hhh/草/绷）
- 怎么接话、怎么转移话题、怎么怼回去又不伤和气
只输出一个 JSON：
{{"sayings":[{{"key":"说法本身或一个短标签（10 字内）","type":"阴阳/嘲讽/暗指/谐音/段子/玩梗/互损/口头禅/打字习惯/接话","meaning":"字面之外真正的意思、潜台词","use":"什么场合、对什么关系的人用，用了是什么效果","example":"一句原话（照抄记录里的）","who":"常这么说的人名，多人用逗号分隔"}}],
 "style":"把你对这个群说话风格的感觉重写一遍（合并旧的）：句子多长、标点、常用词、氛围是互损还是温和、大家怎么对待新人和机器人，3~5 句"}}
规则：只写记录里真有的，不编；没什么值得学的就给空列表；涉及政治、未成年、露骨色情的不收；你自己说的话不算。"""


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _when(ts):
    try:
        d = datetime.fromtimestamp(float(ts))
    except Exception:
        return ""
    days = (datetime.now().date() - d.date()).days
    return {0: "今天", 1: "昨天", 2: "前天"}.get(days, d.strftime("%m-%d")) + d.strftime(" %H:%M")


def _cq_text(raw):
    s = str(raw or "")
    s = re.sub(r"\[CQ:at,qq=(\d+)[^\]]*\]", r"@\1", s)
    s = re.sub(r"\[CQ:image[^\]]*summary=&#91;动画表情&#93;[^\]]*\]", "[表情包]", s)
    s = re.sub(r"\[CQ:image[^\]]*\]", "[图片]", s)
    s = re.sub(r"\[CQ:face[^\]]*\]", "[表情]", s)
    s = re.sub(r"\[CQ:(mface|marketface)[^\]]*\]", "[表情包]", s)
    s = re.sub(r"\[CQ:reply[^\]]*\]", "", s)
    s = re.sub(r"\[CQ:(\w+)[^\]]*\]", r"[\1]", s)
    return s.strip()


def _seg_text(segs):
    out = []
    for s in segs or []:
        t, d = s.get("type"), s.get("data") or {}
        if t == "text":
            out.append(d.get("text", ""))
        elif t == "at":
            out.append(f"@{d.get('name') or d.get('qq')}")
        elif t == "image":
            out.append("[表情包]" if _is_sticker(s) else "[图片]")
        elif t in ("mface", "marketface"):
            out.append(f"[表情包{d.get('summary') or ''}]")
        elif t == "face":
            out.append("[表情]")
        elif t == "reply":
            continue
        else:
            out.append(f"[{t}]")
    return "".join(out).strip()


def _is_sticker(seg):
    t, d = seg.get("type"), seg.get("data") or {}
    if t in ("mface", "marketface"):
        return True
    return t == "image" and (str(d.get("sub_type")) == "1" or "表情" in str(d.get("summary") or ""))


def _sticker_urls(segs):
    out = []
    for s in segs or []:
        if _is_sticker(s):
            d = s.get("data") or {}
            u = d.get("url") or (d.get("file") if str(d.get("file", "")).startswith("http") else "")
            eid = str(d.get("emoji_id") or "")
            if not u and s.get("type") in ("mface", "marketface") and len(eid) > 2:
                u = f"https://gxh.vip.qq.com/club/item/parcel/item/{eid[:2]}/{eid}/raw300.gif"
            fid = str(d.get("file") or "") if s.get("type") == "image" else ""
            if u or fid:
                out.append((u, fid))
    return out


class DafeiyuKB(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.conf = config or {}
        self.dir = os.path.join(PD, "astrbot_plugin_dafeiyu_kb")
        self.tmp = os.path.join(self.dir, "tmp")
        os.makedirs(self.tmp, exist_ok=True)
        self.db = Store(os.path.join(self.dir, "kb.db"))
        self.gnames = {}
        self.me = ""
        self.memeq: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.tasks = []
        self.digesting = set()
        self.chunk_over = {}  # 某群整理超时后，下次少拿一点聊天记录再试

    # ---------- 生命周期 ----------
    @filter.on_astrbot_loaded()
    async def start(self):
        self.tasks = [asyncio.create_task(self._loop()), asyncio.create_task(self._meme_worker()),
                      asyncio.create_task(self._meme_worker())]

    async def terminate(self):
        for t in self.tasks:
            t.cancel()

    def _owner(self):
        return str(self.conf.get("owner_qq") or "")

    def _api(self):
        inst = self.context.get_platform_inst(str(self.conf.get("platform_id") or "大肥鱼"))
        if inst is None:
            raise RuntimeError("找不到 QQ 平台")
        return inst.get_client().api

    async def _call(self, action, **params):
        return await self._api().call_action(action, **params)

    async def _llm(self, prompt, image=None, timeout=3600, vision=False):
        pid = str(self.conf.get("vision_provider_id" if vision else "llm_provider_id") or "grok/grok-4.7")
        prov = self.context.get_provider_by_id(pid) or self.context.get_using_provider()
        if prov is None:
            raise RuntimeError("没有可用的模型")
        kw = {"prompt": prompt, "session_id": uuid.uuid4().hex, "persist": False}
        if image:
            kw["image_urls"] = [image]
        resp = await asyncio.wait_for(prov.text_chat(**kw), timeout=timeout)
        t = getattr(resp, "completion_text", "") or ""
        s, e = t.find("{"), t.rfind("}")
        return json.loads(t[s:e + 1]) if s >= 0 else {}

    async def _loop(self):
        await asyncio.sleep(20)
        tick = 0
        while True:
            try:
                await self._refresh_groups()
                if not self.db.get("backfilled"):
                    await self._backfill()
                    self.db.put("backfilled", time.time())
                await asyncio.to_thread(self._import_web)
                await self._digest_due(force_old=(tick % 12 == 0))
                await self._learn_due(force_old=(tick % 12 == 0))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[dafeiyu_kb] 后台整理出错：{e}")
            tick += 1
            await asyncio.sleep(600)

    async def _refresh_groups(self):
        try:
            if not self.me:
                self.me = str((await self._call("get_login_info") or {}).get("user_id", ""))
            for g in await self._call("get_group_list") or []:
                self.gnames[str(g.get("group_id"))] = g.get("group_name") or str(g.get("group_id"))
        except Exception as e:
            logger.debug(f"[dafeiyu_kb] 拿群列表失败：{e}")

    # ---------- 记群消息 ----------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=90)
    async def on_message(self, event: AstrMessageEvent):
        """排在看图插件（100）后面：没 @ 她的图已经换成「[图片：……]」了。只记，不拦。"""
        try:
            raw = getattr(event.message_obj, "raw_message", None)
            segs = raw.get("message") if isinstance(raw, dict) and isinstance(raw.get("message"), list) else []
            gid = str(event.get_group_id() or "")
            uid = str(event.get_sender_id())
            if uid == str(event.get_self_id()):
                return
            if gid:
                text = (event.message_str or "").strip()
                if not text or (segs and not any(s.get("type") == "text" for s in segs)):
                    text = (text + " " + _seg_text(segs)).strip() if text else _seg_text(segs)
                ats = [f"@{(s.get('data') or {}).get('qq')}" for s in segs if s.get("type") == "at"]
                if ats and not any(a in text for a in ats):
                    text = " ".join(ats) + " " + text
                self.db.add_raw(time.time(), gid, self.gnames.get(gid, ""), uid, event.get_sender_name() or uid,
                                text, False, f"{gid}:{getattr(event.message_obj, 'message_id', uuid.uuid4().hex)}")
            if self.conf.get("collect_memes", True):
                for u in _sticker_urls(segs):
                    self._queue_meme(u, gid, uid, event.get_sender_name() or uid)
        except Exception as e:
            logger.debug(f"[dafeiyu_kb] 记消息失败：{e}")

    @filter.after_message_sent(priority=-200)
    async def on_sent(self, event: AstrMessageEvent):
        """她自己在群里说的也记下。"""
        try:
            gid = str(event.get_group_id() or "")
            res = event.get_result()
            if not gid or res is None:
                return
            text = (res.get_plain_text() or "").strip()
            text = re.sub(r"&&[^&]{1,20}&&", "", text).strip()
            if text:
                self.db.add_raw(time.time(), gid, self.gnames.get(gid, ""), str(event.get_self_id()), "大肥鱼",
                                text, True, f"{gid}:self:{time.time_ns()}")
        except Exception as e:
            logger.debug(f"[dafeiyu_kb] 记自己的话失败：{e}")

    async def _backfill(self):
        pages = int(self.conf.get("backfill_pages") or 40)
        total = 0
        for gid in list(self.gnames):
            seq, allm, seen = 0, [], set()
            for _ in range(pages):
                try:
                    r = await self._call("get_group_msg_history", group_id=int(gid), message_seq=seq, count=100, reverseOrder=False)
                except Exception as e:
                    logger.info(f"[dafeiyu_kb] 群 {gid} 历史拉不到了：{_clip(e, 60)}")
                    break
                msgs = [m for m in (r or {}).get("messages") or [] if m.get("message_id") not in seen]
                if not msgs:
                    break
                for m in msgs:
                    seen.add(m.get("message_id"))
                allm += msgs
                oldest = min(msgs, key=lambda m: float(m.get("time") or 0))
                nseq = oldest.get("message_id") or oldest.get("message_seq")
                if not nseq or nseq == seq:
                    break
                seq = nseq
                await asyncio.sleep(1)
            got = 0
            for m in sorted(allm, key=lambda m: float(m.get("time") or 0)):  # 按时间先后存，整理时才连贯
                snd = m.get("sender") or {}
                uid = str(snd.get("user_id") or m.get("user_id") or "")
                segs = m.get("message") if isinstance(m.get("message"), list) else []
                text = _seg_text(segs) if segs else _cq_text(m.get("raw_message"))
                me = uid == self.me
                name = "大肥鱼" if me else (snd.get("card") or snd.get("nickname") or uid)
                if self.db.add_raw(float(m.get("time") or time.time()), gid, self.gnames.get(gid, ""), uid, name, text, me,
                                   f"{gid}:{m.get('message_id')}"):
                    got += 1
                if not me and self.conf.get("collect_memes", True):
                    for u in _sticker_urls(segs):
                        self._queue_meme(u, gid, uid, name)
            total += got
            logger.info(f"[dafeiyu_kb] 群「{self.gnames.get(gid)}」补了 {got} 条历史")
        if self.db.get("cursor") is None:  # 第一次：从头整理
            self.db.put("cursor", {})
        logger.info(f"[dafeiyu_kb] 历史补完，共 {total} 条，排队看的表情包 {self.memeq.qsize()} 张")

    # ---------- 刷到的东西 ----------
    def _load(self, *p, default=None):
        try:
            return json.load(open(os.path.join(PD, *p), encoding="utf-8"))
        except Exception:
            return default

    def _import_web(self):
        n = {"new": 0, "updated": 0}

        def up(*a, **k):
            r = self.db.upsert(*a, **k)
            if r in n:
                n[r] += 1

        for d in self._load("astrbot_plugin_dafeiyu_tieba", "diary.json", default=[]) or []:
            if not d.get("tid"):
                continue
            up("贴吧", "web", f"tieba:{d['tid']}:{d.get('t')}", f"【{d.get('forum')}吧】{d.get('title')}",
               f"你逛到这帖时的想法：{d.get('thought', '')}\n帖子大概：{d.get('gist', '')}\n链接 https://tieba.baidu.com/p/{d['tid']}",
               ts=float(d.get("t") or 0) or None)
        xs = self._load("astrbot_plugin_dafeiyu_x", "state.json", default={}) or {}
        for it in xs.get("items") or []:
            if not it.get("id"):
                continue
            up("推特", "web", f"x:{it['id']}", f"{it.get('author', '')}(@{it.get('handle', '')}) 的推文",
               f"{it.get('text', '')}\n{it.get('url', '')}",
               meta={"sensitive": bool(it.get("sensitive")), "politics": bool(it.get("politics"))})
        for lg in xs.get("log") or []:
            if lg.get("text"):
                up("推特", "web", f"xlog:{lg.get('t')}", f"你在推特上{lg.get('kind', '')}", lg.get("text", ""), ts=lg.get("t"))
        vm = self._load("astrbot_plugin_bili_scroll", "video_memory.json", default={}) or {}
        em = self._load("astrbot_plugin_bili_scroll", "external_memory.json", default={}) or {}
        wl = {}
        for w in self._load("astrbot_plugin_bili_scroll", "watch_log.json", default=[]) or []:
            if w.get("bvid"):
                wl.setdefault(w["bvid"], []).append(w)
        for bvid in set(vm) | set(em) | set(wl):
            v, e, ws = vm.get(bvid) or {}, em.get(bvid) or {}, wl.get(bvid) or []
            title = v.get("title") or e.get("title") or (ws[0].get("title") if ws else "") or bvid
            owner = v.get("owner_name") or e.get("up_name") or (ws[0].get("up_name") if ws else "")
            parts = [f"UP 主：{owner}" if owner else "", f"内容：{_clip(v.get('summary') or e.get('description') or v.get('desc'), 600)}"]
            if e.get("review"):
                parts.append(f"你看完的感受：{e['review']}（打分 {e.get('score', '')}）")
            for w in ws:
                if w.get("review") or (w.get("comment") and w.get("comment") != "评价失败"):
                    parts.append(f"{w.get('time', '')} 你看时说：{w.get('review') or w.get('comment')}")
            up("B站", "web", f"bili:{bvid}", f"B站视频《{title}》", "\n".join(p for p in parts if p) + f"\nhttps://www.bilibili.com/video/{bvid}")
        if n["new"] or n["updated"]:
            logger.info(f"[dafeiyu_kb] 收进刷到的内容：新 {n['new']} 条，更新 {n['updated']} 条")

    # ---------- 整理 ----------
    async def _digest_due(self, force_old=False):
        cur = self.db.get("cursor", {}) or {}
        need = int(self.conf.get("digest_min_new") or 120)
        for g in self.db.groups():
            gid = g["gid"]
            if gid in self.digesting:
                continue
            c = self.db.raw_count_after(gid, int(cur.get(gid, 0)))
            if c["n"] >= need or (force_old and c["n"] >= 15):
                self.digesting.add(gid)
                try:
                    while True:
                        done = await self._digest(gid, g["gname"] or self.gnames.get(gid, gid))
                        if not done:
                            break
                finally:
                    self.digesting.discard(gid)

    # ---------- 学说话：从全部历史里学阴阳、暗指、段子、梗、口头禅 ----------
    async def _learn_due(self, force_old=False):
        """单独一个进度，从第一条群消息开始一段段往后学，学完全部历史后跟着新消息继续学。学到的永久存。"""
        cur = self.db.get("learn_cursor", {}) or {}
        need = int(self.conf.get("learn_min_new") or 60)
        for g in self.db.groups():
            gid = g["gid"]
            key = f"learn:{gid}"
            if key in self.digesting:
                continue
            c = self.db.raw_count_after(gid, int(cur.get(gid, 0)))
            if c["n"] >= need or (force_old and c["n"] >= 10):
                self.digesting.add(key)
                try:
                    for _ in range(30):
                        if not await self._learn(gid, g["gname"] or self.gnames.get(gid, gid)):
                            break
                finally:
                    self.digesting.discard(key)

    async def _learn(self, gid, gname):
        cur = self.db.get("learn_cursor", {}) or {}
        chunk = int(self.conf.get("learn_chunk") or 250)
        rows = self.db.raw_after(gid, int(cur.get(gid, 0)), chunk)
        if len(rows) < 10:
            return False
        rows_t = sorted(rows, key=lambda r: r["ts"])
        chat = "\n".join(f"[{datetime.fromtimestamp(r['ts']).strftime('%m-%d %H:%M')}] "
                         f"{'大肥鱼(我)' if r['self'] else r['uname']}({'' if r['self'] else r['uid']})：{_clip(r['text'], 300)}"
                         for r in rows_t)
        known = "；".join(f"「{m['title']}」{_clip(m['body'], 30)}" for m in self.db.items_of(gid, "说法", 120)) or "（还没有）"
        st = self.db.item(f"style:{gid}")
        try:
            res = await self._llm(LEARN.format(gname=gname, chat=chat, known=known,
                                               style=(st or {}).get("body") or "（还没有）"), timeout=3600)
        except Exception as e:
            logger.warning(f"[dafeiyu_kb] 学群「{gname}」说话失败：{type(e).__name__} {e}")
            return False
        n = 0
        for s in res.get("sayings") or []:
            k = _clip(str(s.get("key") or "").strip(), 20)
            if not k:
                continue
            ikey = f"say:{gid}:{k}"
            body = (f"[{s.get('type', '')}] {s.get('meaning', '')}"
                    + (f"\n怎么用：{s['use']}" if s.get("use") else "")
                    + (f"\n原话：{s['example']}" if s.get("example") else "")
                    + (f"\n谁常说：{s['who']}" if s.get("who") else ""))
            old = self.db.item(ikey)
            if old and old["body"] and s.get("example") and str(s["example"]) not in old["body"]:
                body = _clip(old["body"], 500) + f"\n又一句：{s['example']}"
            self.db.upsert("说法", gid, ikey, k, body, ts=rows_t[-1]["ts"])
            n += 1
        if str(res.get("style") or "").strip():
            self.db.upsert("风格", gid, f"style:{gid}", "这个群怎么说话", _clip(res["style"], 500), ts=rows_t[-1]["ts"])
        cur[gid] = max(r["id"] for r in rows)
        self.db.put("learn_cursor", cur)
        logger.info(f"[dafeiyu_kb] 学了群「{gname}」{len(rows)} 条怎么说话：新说法 {n} 个")
        return len(rows) >= chunk

    async def _digest(self, gid, gname):
        cur = self.db.get("cursor", {}) or {}
        after = int(cur.get(gid, 0))
        if not self.chunk_over:  # 重启后接着用上次记下的「少拿一点」
            self.chunk_over = dict(self.db.get("chunk_over", {}) or {})
        chunk = int(self.chunk_over.get(gid) or self.conf.get("digest_chunk") or 300)
        started = time.time()
        rows = self.db.raw_after(gid, after, chunk)
        if len(rows) < 15:
            return False
        rows_t = sorted(rows, key=lambda r: r["ts"])
        forgot = self.db.forgotten()
        chat = "\n".join(f"[{datetime.fromtimestamp(r['ts']).strftime('%m-%d %H:%M')}] "
                         f"{'大肥鱼(我)' if r['self'] else r['uname']}({'' if r['self'] else r['uid']})：{_clip(r['text'], 300)}"
                         for r in rows_t)
        memes = "；".join(f"「{m['title']}」{_clip(m['body'], 40)}" for m in self.db.items_of(gid, "梗", 60)) or "（还没有）"
        speakers = {r["uid"] for r in rows if not r["self"]} - forgot
        people = "；".join(f"{p['title']}({p['person']})：{_clip(p['body'], 120)}"
                          for p in self.db.items_of(gid, "群友", 400) if p["person"] in speakers) or "（还没有）"
        try:
            res = await self._llm(DIGEST.format(gname=gname, chat=chat, memes=memes, people=people), timeout=3600)
        except Exception as e:
            self.chunk_over[gid] = max(40, len(rows) // 2)
            self.db.put("chunk_over", self.chunk_over)
            logger.warning(f"[dafeiyu_kb] 整理群「{gname}」失败：{type(e).__name__} {e}（{len(rows)} 条，下次改拿 {self.chunk_over[gid]} 条）")
            return False
        if self.chunk_over.pop(gid, None) is not None:
            self.db.put("chunk_over", self.chunk_over)
        now = time.time()
        for m in res.get("memes") or []:
            k = str(m.get("key") or "").strip()
            if k:
                old = self.db.item(f"meme-{gid}:{k}")
                ex = f"\n例：{m.get('example')}" if m.get("example") else ""
                body = f"{m.get('meaning', '')}{ex}"
                if old and old["body"] and m.get("meaning") and old["body"] not in body:
                    body = _clip(old["body"], 400) + "\n后来：" + body
                self.db.upsert("梗", gid, f"meme-{gid}:{k}", k, body)
        for i, t in enumerate(res.get("topics") or []):
            if t.get("title"):
                self.db.upsert("话题", gid, f"topic:{gid}:{int(rows_t[-1]['ts'])}:{i}", t["title"], t.get("summary", ""), ts=rows_t[-1]["ts"])
        for i, ev in enumerate(res.get("events") or []):
            if ev.get("title"):
                self.db.upsert("事件", gid, f"event:{gid}:{int(rows_t[-1]['ts'])}:{i}", ev["title"],
                               f"{ev.get('when', '')} {ev.get('summary', '')}".strip(), ts=rows_t[-1]["ts"])
        for p in res.get("people") or []:
            uid = str(p.get("uid") or "").strip()
            if not uid or uid in forgot or uid == self.me or not p.get("impression"):
                continue
            self.db.upsert("群友", gid, f"person:{gid}:{uid}", p.get("name") or uid, p["impression"], person=uid)
        cur[gid] = max(r["id"] for r in rows)
        self.db.put("cursor", cur)
        logger.info(f"[dafeiyu_kb] 整理了群「{gname}」{len(rows)} 条：梗 {len(res.get('memes') or [])}、话题 {len(res.get('topics') or [])}、"
                    f"事件 {len(res.get('events') or [])}、群友 {len(res.get('people') or [])}（{int(time.time() - started)} 秒）")
        return len(rows) >= chunk

    # ---------- 表情包 ----------
    def _queue_meme(self, url, gid, uid, uname):
        try:
            self.memeq.put_nowait((url, gid, uid, uname))
        except asyncio.QueueFull:
            pass

    async def _meme_worker(self):
        async with aiohttp.ClientSession() as sess:
            while True:
                url, gid, uid, uname = await self.memeq.get()
                try:
                    await self._take_meme(sess, url, gid, uid, uname)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.info(f"[dafeiyu_kb] 收表情失败：{_clip(e, 120)}（{_clip(url, 60)}）")

    @staticmethod
    async def _fetch(sess, url):
        try:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": "Mozilla/5.0"}) as r:
                return await r.read() if r.status == 200 else None
        except Exception:
            return None

    async def _take_meme(self, sess, src, gid, uid, uname):
        url, fid = src if isinstance(src, tuple) else (src, "")
        data = await self._fetch(sess, url) if url else None
        if data is None and fid:  # 链接过期了（历史消息常见）：让 NapCat 重新取一次
            try:
                r = await self._call("get_image", file=fid) or {}
                if r.get("url"):
                    data = await self._fetch(sess, r["url"])
                p = str(r.get("file") or "")
                if data is None and p and os.path.isfile(p) and os.access(p, os.R_OK):
                    data = open(p, "rb").read()
            except Exception as e:
                logger.info(f"[dafeiyu_kb] 重新取表情图失败：{_clip(e, 80)}")
        if not data:
            logger.info(f"[dafeiyu_kb] 表情图下载不了（{_clip(url or fid, 60)}）")
            return
        if len(data) < 800 or len(data) > 6_000_000:
            return
        md5 = hashlib.md5(data).hexdigest()
        if self.db.meme(md5):
            self.db.meme_seen(md5)
            return
        ext = ".gif" if data[:4] == b"GIF8" else ".png" if data[:4] == b"\x89PNG" else ".webp" if data[8:12] == b"WEBP" else ".jpg"
        src = os.path.join(self.tmp, md5 + ext)
        with open(src, "wb") as f:
            f.write(data)
        look = await asyncio.to_thread(self._frame, src)
        try:
            v = await self._llm(MEME_JUDGE, image=look, timeout=3600, vision=True)
        finally:
            for p in (look,):
                try:
                    os.remove(p)
                except OSError:
                    pass
        base = dict(md5=md5, cat="", meaning=str(v.get("meaning") or ""), text=str(v.get("text") or ""), path="",
                    gid=gid, uid=uid, uname=uname, first=time.time())
        if v.get("is_meme") is False or v.get("bad") is True or v.get("minor") is True or v.get("cat") not in CATS:
            why = "不是表情包" if v.get("is_meme") is False else "不合适" if (v.get("bad") or v.get("minor")) else "看不出分类"
            self.db.meme_add(status="rejected", reason=why, **base)
            os.remove(src)
            return
        cat = v["cat"]
        os.makedirs(os.path.join(PACK, "memes", cat), exist_ok=True)
        name = f"grp_{md5}{ext}"
        dst = os.path.join(PACK, "memes", cat, name)
        os.replace(src, dst)
        base.update(cat=cat, path=dst)
        self.db.meme_add(status="kept", reason="", **base)
        gname = self.gnames.get(gid, "私聊" if not gid else gid)
        self.db.upsert("表情包", "meme", f"meme:{md5}", base["meaning"] or CATS[cat],
                       f"图上的字：{base['text']}\n画面：{v.get('scene', '')}\n分类：{CATS[cat]}\n从{gname}的{uname}那收来的",
                       meta={"path": dst, "cat": cat, "from_gid": gid, "from_uid": uid})
        try:
            md = os.path.join(PACK, "memes_data.json")
            data = json.load(open(md, encoding="utf-8"))
            if cat not in data:
                data[cat] = f"用于{CATS[cat]}。"
                json.dump(data, open(md, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            caps = json.load(open(CAPS, encoding="utf-8")) if os.path.exists(CAPS) else {}
            caps[f"dafeiyu-picked/{cat}/{name}"] = {"text": base["text"], "scene": v.get("scene", ""), "meaning": base["meaning"],
                                                     "cat": cat, "from": f"{gname}/{uname}"}
            json.dump(caps, open(CAPS, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as e:
            logger.debug(f"[dafeiyu_kb] 写表情说明失败：{e}")
        logger.info(f"[dafeiyu_kb] 收了一张表情包（{CATS[cat]}）：{_clip(base['meaning'], 40)} ← {gname}/{uname}")

    @staticmethod
    def _frame(src):
        from PIL import Image as PImage
        im = PImage.open(src)
        n = getattr(im, "n_frames", 1)
        im.seek(0)
        a = im.convert("RGBA")
        if n > 2:
            im.seek(n // 2)
            b = im.convert("RGBA")
            w, h = a.size
            c = PImage.new("RGBA", (w * 2 + 10, h), (255, 255, 255, 255))
            c.paste(a, (0, 0), a)
            c.paste(b, (w + 10, 0), b)
            a = c
        bg = PImage.new("RGB", a.size, (255, 255, 255))
        bg.paste(a, (0, 0), a)
        bg.thumbnail((768, 768))
        out = src + ".look.png"
        bg.save(out)
        return out

    # ---------- 聊天时想起来 ----------
    def _scopes(self, event):
        gid = str(event.get_group_id() or "")
        if gid:
            return gid, [gid, "web"]
        if str(event.get_sender_id()) == self._owner():
            return "", [g["gid"] for g in self.db.groups()] + ["web"]
        return "", ["web"]

    @filter.on_llm_request(priority=-10)
    async def inject(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            msg = (event.message_str or "").strip()
            if not msg or msg.startswith("/"):
                return
            gid, scopes = self._scopes(event)
            uid = str(event.get_sender_id())
            lines = []
            if gid:
                me = self.db.item(f"person:{gid}:{uid}")
                if me:
                    lines.append(f"- 正在跟你说话的 {me['title']}：{_clip(me['body'], 220)}")
                for p in self.db.items_of(gid, "群友", 400):
                    if p["person"] != uid and (p["title"] and p["title"] in msg or f"@{p['person']}" in msg):
                        lines.append(f"- 提到的 {p['title']}：{_clip(p['body'], 160)}")
            n = int(self.conf.get("inject_items") or 8)
            hits = self.db.search_items(msg, scopes, kinds=["梗", "话题", "事件", "贴吧", "推特", "B站"], limit=n,
                                        exclude_flags=("sensitive", "politics") if gid else ("politics",))
            hits = [h for h in hits if overlap(msg, h["title"] + h["body"]) >= 0.12]
            for h in hits:
                where = "" if h["scope"] in (gid, "web") else f"（在群「{self.gnames.get(h['scope'], h['scope'])}」）"
                lines.append(f"- [{h['kind']}]{where} {h['title']}：{_clip(h['body'], 200)}")
            if gid:
                for m in self.db.items_of(gid, "梗", 200):
                    if m["title"] and m["title"] in msg and not any(m["title"] in ln for ln in lines):
                        lines.append(f"- [梗] {m['title']}：{_clip(m['body'], 150)}")
            if gid:
                try:
                    st = self.db.item(f"style:{gid}")
                    forgot = self.db.forgotten()
                    voice = self.db.voice_samples(gid, skip_uids=forgot | {self.me})
                    # 学会的说法：跟这句话相关的 + 随机几条（每次换，慢慢全用上）
                    says = self.db.search_items(msg, [gid], kinds=["说法"], limit=6)
                    says = [h for h in says if overlap(msg, h["title"] + h["body"]) >= 0.08]
                    seen = {h["key"] for h in says}
                    says += [h for h in self.db.random_items(gid, "说法", 8) if h["key"] not in seen]
                    says += self.db.random_items(gid, "梗", 4)
                    if st or len(voice) >= 5 or says:
                        req.system_prompt = (req.system_prompt or "") + (
                            "\n\n【这个群平时怎么说话】你也是这群里的人，说话跟着群里的味儿走——长短、标点、语气词、用梗，"
                            "学的是口气和套路，别照抄原话，也别为了用而硬用。"
                            + (f"\n你的感觉：{st['body']}" if st and st.get("body") else "")
                            + ("\n你学会的说法（阴阳、暗指、段子、互损这些，看场合和对谁再用：熟的人互损玩梗，"
                               "烦的人才阴阳冷嘲，别对谁都阴阳）：\n" + "\n".join(
                                   f"- 「{h['title']}」{_clip(h['body'], 160)}" for h in says[:14]) if says else "")
                            + ("\n群友说过的话（随手抽的）：" + " / ".join(f"「{t}」" for _, t in voice) if voice else ""))
                except Exception as e:
                    logger.debug(f"[dafeiyu_kb] 带群聊风格失败：{e}")
            if lines:
                req.system_prompt = (req.system_prompt or "") + (
                    "\n\n【你记得的】（这是你自己经历过、看到过的，自然地用上就行，别说「根据记录」「我查到」，"
                    "也别一股脑全倒出来；记不清细节就用 recall_memory 想一想）\n" + "\n".join(lines[:14]))
        except Exception as e:
            logger.debug(f"[dafeiyu_kb] 带记忆失败：{e}")

    # ---------- 工具 ----------
    @filter.llm_tool(name="recall_memory")
    async def recall_memory(self, event: AstrMessageEvent, query: str, who: str = ""):
        '''回忆：在你自己的记忆里找——群里的梗、聊过的话题、发生的事、对群友的印象、群里谁说过什么原话，
        还有你刷贴吧/推特/B站看到过的。别人问「那天谁说过……」「XX 是个什么样的人」「你记不记得……」「之前那个梗啥意思」，
        或者你自己想不起来的时候用。找到的就是你真实记得的，没找到就说记不清，别编。

        Args:
            query(string): 想回忆什么，关键词或一句话
            who(string): 想回忆的是某个人的话，填他的名字或 QQ 号，否则留空
        '''
        gid, scopes = self._scopes(event)
        q = f"{query} {who}".strip()
        out = {}
        people = []
        if who:
            for s in scopes:
                for p in self.db.items_of(s, "群友", 500):
                    if who in (p["title"] or "") or who == p["person"]:
                        people.append({"name": p["title"], "group": self.gnames.get(s, s), "impression": p["body"]})
        if people:
            out["people"] = people[:4]
        items = self.db.search_items(q, scopes, limit=10, exclude_flags=("sensitive", "politics") if gid else ("politics",))
        out["knowledge"] = [{"kind": i["kind"], "where": "刷到的" if i["scope"] == "web" else self.gnames.get(i["scope"], i["scope"]),
                             "title": i["title"], "content": _clip(i["body"], 400), "when": _when(i["updated"])}
                            for i in items if i["kind"] != "表情包"]
        raw_gids = [s for s in scopes if s != "web"]
        if raw_gids:
            rows = self.db.search_raw(q, raw_gids, limit=12)
            out["said"] = [f"{_when(r['ts'])} {'' if gid else '「' + (r['gname'] or r['gid']) + '」'}"
                           f"{'我' if r['self'] else r['uname']}：{_clip(r['text'], 160)}" for r in rows]
        if not any(out.values()):
            return json.dumps({"found": False, "note": "记忆里没找到相关的，照实说想不起来/没印象"}, ensure_ascii=False)
        out["note"] = "这些是你自己记得的事，用你的口吻自然地说，别念清单，别暴露「记录」「数据库」这种词。"
        return json.dumps(out, ensure_ascii=False)

    @filter.llm_tool(name="send_collected_meme")
    async def send_collected_meme(self, event: AstrMessageEvent, meaning: str):
        '''从你自己收来的表情包里（群友发过、你觉得有意思收下的），按意思挑一张发出去。
        想用一张特定意思的表情包回应时用，比如「嫌弃对方」「笑死」「饿了」「阴阳怪气」。用了这个，这句回复里就别再加平时的表情包标记。

        Args:
            meaning(string): 想表达的意思，具体点
        '''
        hits = self.db.search_items(meaning, ["meme"], kinds=["表情包"], limit=6)
        hits = [h for h in hits if os.path.exists(json.loads(h["meta"] or "{}").get("path", ""))]
        if not hits:
            return json.dumps({"sent": False, "note": "收来的表情包里没有对得上的，只用文字或者平时的表情包"}, ensure_ascii=False)
        top = [h for h in hits if overlap(meaning, h["title"] + h["body"]) >= overlap(meaning, hits[0]["title"] + hits[0]["body"]) * 0.8]
        h = random.choice(top)
        await event.send(MessageChain([Image.fromFileSystem(json.loads(h["meta"])["path"])]))
        return json.dumps({"sent": True, "meme_meaning": h["title"], "meme_detail": _clip(h["body"], 120),
                           "note": "已经发出去了，接着说话就行，别描述图"}, ensure_ascii=False)

    @filter.llm_tool(name="forget_me")
    async def forget_me(self, event: AstrMessageEvent):
        '''有人明确说「别记我的事」「把我忘了」「不许记我」时用：删掉你对这个人的印象，以后也不再给他记印象。
        只能是他本人要求才用，别人替他说不算，开玩笑的也不算。
        '''
        uid = str(event.get_sender_id())
        n = self.db.forget(uid)
        return json.dumps({"done": True, "removed": n, "note": "已经不记他了，用你的口吻答应一声"}, ensure_ascii=False)
