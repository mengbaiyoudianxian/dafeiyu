"""大肥鱼的心情 v0.2：多维关系 + 细分情绪 + 记住具体事件 + 环境和时间影响。

一、她和每个人的关系（每项 0~100，各自变化）
  亲密 intimacy   有多亲近        信任 trust      信不信得过
  熟悉 familiar   聊得多不多      兴趣 interest   觉得这人有不有趣
  依赖 reliance   遇事想不想找他  不满 grudge     记的仇（会慢慢淡，道歉能消得快）
  综合好感 = 亲密·0.35 + 信任·0.25 + 兴趣·0.15 + 熟悉·0.10 + 依赖·0.15 − 不满·0.5

二、她自己的情绪（每项 0~100，各有基线和消退速度）
  高兴 生气 委屈 害羞 孤独 无聊 吃醋 得意 担心 烦躁，外加精力
  兴致 arousal  色色程度。基线很低、退得快；不进入综合好感。群里和关系一般的人几乎不涨。

三、记住具体事件
  每个人记最近的事和重要的事（夸她、骂她、帮她、给她充钱、放她鸽子……），带时间和分量。
  聊天时把最近 / 最重要的几件告诉她，所以她会「记仇」「记恩」。

四、环境和时间
  - 群里气氛会传染：大家在笑她也开心，吵架刷屏她也烦（只看关键词，不调模型，不花钱）
  - 没人理她：醒着时越久没人跟她说话越孤独、越无聊；她主动找人没人回，孤独 + 委屈
  - 主人在群里跟别人聊得欢、没理她：吃醋
  - 早上起床气（烦躁基线高、精力低），深夜感性（孤独、委屈基线高）
  - 刷到好帖子 / 好视频：高兴、不无聊；钱不够：担心、高兴基线下降；主人充值：高兴、得意、亲密 +

每次她回完话，用快模型（默认 grok-4.20 不思考版）在后台评估这一轮；不阻塞回复。
"""

import asyncio
import json
import math
import os
import re
import time
from datetime import datetime

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    from quart import jsonify as _jsonify
except Exception:
    _jsonify = None

NAME = "astrbot_plugin_dafeiyu_heart"
DATA = get_astrbot_data_path()

REL = {  # key: (中文, 新人初始, 主人初始)
    "intimacy": ("亲密", 30, 75), "trust": ("信任", 45, 80), "familiar": ("熟悉", 5, 90),
    "interest": ("兴趣", 50, 65), "reliance": ("依赖", 10, 60), "grudge": ("不满", 0, 0),
}
EMO = {  # key: (中文, 基线, 半衰期分钟)
    "joy": ("高兴", 55, 120), "anger": ("生气", 3, 45), "sad": ("委屈", 5, 120), "shy": ("害羞", 3, 15),
    "lonely": ("孤独", 10, 180), "bored": ("无聊", 20, 60), "jealous": ("吃醋", 0, 60), "proud": ("得意", 10, 60),
    "worry": ("担心", 5, 240), "annoyed": ("烦躁", 8, 60), "arousal": ("兴致", 4, 30),
}
POS = re.compile(r"哈哈|hhh|笑死|233|草|绷不住|乐|牛|nb|好耶|太好了|可爱|厉害|😂|🤣|👍")
NEG = re.compile(r"傻逼|sb|滚|妈的|操你|nmsl|废物|闭嘴|吵死|烦死|有病|脑残")


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def overall(r):
    return clamp(r["intimacy"] * .35 + r["trust"] * .25 + r["interest"] * .15 + r["familiar"] * .10
                 + r["reliance"] * .15 - r["grudge"] * .5)


def level(a):
    for th, name in ((85, "非常亲近"), (70, "喜欢"), (55, "挺熟"), (40, "普通"), (25, "有点冷淡"), (0, "讨厌")):
        if a >= th:
            return name
    return "讨厌"


def degree(v):
    return "极度" if v >= 85 else "很" if v >= 65 else "有点" if v >= 40 else "一点点"


def _short(s, n=30):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def ago(t):
    s = time.time() - t
    return "刚才" if s < 600 else f"{int(s // 60)}分钟前" if s < 3600 else f"{int(s // 3600)}小时前" if s < 86400 else f"{int(s // 86400)}天前"


class DafeiyuHeart(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.data_dir = str(StarTools.get_data_dir(NAME))
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "hearts.json")
        self.data = self._load()
        self._migrate()
        self._dirty = False
        self._last_jealous = 0
        context.register_web_api(f"/{NAME}/page/status", self.page_status, ["GET"], "心情控制中心 状态")
        context.register_web_api(f"/{NAME}/page/test", self.page_test, ["POST"], "心情控制中心 试评估")
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._save(force=True)

    # ---------- 存取 ----------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _migrate(self):
        d = self.data
        s = d.setdefault("self", {})
        emo = s.setdefault("emo", {})
        for k, (_, base, _) in EMO.items():
            emo.setdefault(k, float(s.pop("happy", base)) if k == "joy" and "happy" in s else
                           float(s.pop("annoyed", base)) if k == "annoyed" and "annoyed" in s else float(base))
        s.setdefault("energy", 80.0)
        s.setdefault("why", "")
        s.setdefault("last_talk", time.time())
        d.setdefault("users", {})
        for uid, u in d["users"].items():
            if "rel" not in u:
                a = float(u.pop("aff", 50))
                u["rel"] = {k: (v[1] if k != "intimacy" else a) for k, v in REL.items()}
            u.setdefault("events", [])
            u.setdefault("impression", "")
        d.setdefault("log", [])
        d.setdefault("seen", {})
        d.setdefault("groups", {})
        for g in d["groups"].values():
            g.setdefault("aff", 50.0)   # 对这个群整体的好感，长期的
            g.setdefault("imp", "")     # 对这个群的印象
        d.setdefault("acts", {})        # 对各种事的喜好：{类别: {like, n, last}}
        s.setdefault("act", {})         # 手上正在做的事 {key, doing, cat, feel}

    def _save(self, force=False):
        if not (self._dirty or force):
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self._dirty = False

    def _owner_qq(self):
        umo = str(self.conf.get("owner_umo") or "").strip()
        if not umo:
            try:
                umo = str(json.load(open(os.path.join(DATA, "config", "astrbot_plugin_bili_scroll_config.json"),
                                         encoding="utf-8-sig")).get("OWNER_QQ_UMO") or "")
            except Exception:
                umo = ""
        return umo.rsplit(":", 1)[-1]

    def _user(self, uid, name=""):
        u = self.data["users"].get(uid)
        if u is None:
            owner = uid == self._owner_qq()
            u = {"rel": {k: float(v[2] if owner else v[1]) for k, v in REL.items()},
                 "name": name or uid, "impression": "", "count": 0, "events": [], "first": time.time(), "last": time.time()}
            self.data["users"][uid] = u
        if name:
            u["name"] = name
        return u

    def _note(self, text):
        self.data["log"] = (self.data["log"] + [{"t": int(time.time()), "text": text[:140]}])[-300:]
        self._dirty = True

    def _bump(self, emo, delta, why=""):
        e = self.data["self"]["emo"]
        e[emo] = clamp(e[emo] + delta)
        if why and abs(delta) >= 5:
            self.data["self"]["why"] = why
        self._dirty = True

    def _event(self, uid, kind, text, weight):
        u = self.data["users"].get(uid)
        if not u:
            return
        u["events"].append({"t": int(time.time()), "kind": kind[:8], "text": text[:50], "w": int(weight)})
        evs = u["events"]
        if len(evs) > 40:  # 留最近 20 件 + 分量最重的 20 件
            keep = sorted(evs[:-20], key=lambda e: -abs(e["w"]))[:20]
            u["events"] = sorted(keep, key=lambda e: e["t"]) + evs[-20:]

    # ---------- 时间 / 环境 ----------

    def _hour_in(self, spec):
        m = re.match(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$", str(spec or ""))
        if not m:
            return False
        a, b, h = int(m.group(1)), int(m.group(2)), datetime.now().hour
        return a <= h < b if a < b else (h >= a or h < b)

    def _money(self):
        try:
            v = json.load(open(os.path.join(DATA, "plugin_data", "astrbot_plugin_dafeiyu_impulse", "state.json"),
                               encoding="utf-8")).get("balance", {}).get("value")
            c = json.load(open(os.path.join(DATA, "config", "astrbot_plugin_dafeiyu_impulse_config.json"), encoding="utf-8-sig"))
            return v, float(c.get("balance_low") or 10), float(c.get("balance_critical") or 3)
        except Exception:
            return None, 10, 3

    def _bases(self):
        b = {k: float(v[1]) for k, v in EMO.items()}
        b["joy"] = float(self.conf.get("happy_base") or b["joy"])
        if self._hour_in(self.conf.get("morning_hours") or "8-10"):   # 起床气
            b["annoyed"] += 15
        if self._hour_in(self.conf.get("night_hours") or "23-1"):     # 深夜感性，兴致也更容易起来
            b["lonely"] += 15
            b["sad"] += 8
            b["arousal"] += 8
        v, low, crit = self._money()
        if v is not None and v < low:
            b["joy"] -= 25 if v < crit else 12
            b["worry"] += 45 if v < crit else 25
        like = self._act_like()  # 手上在做喜欢的事就心情好、不无聊；在做烦的事就烦、无聊
        if like is not None:
            b["joy"] += (like - 50) * 0.25
            b["bored"] += (50 - like) * 0.3
            b["annoyed"] += max(0.0, 40 - like) * 0.3
        return b

    # ---------- 手上在做的事，和她对这事的喜好 ----------

    def _act_like(self):
        a = self.data["self"].get("act") or {}
        c = self.data["acts"].get(a.get("cat") or "")
        return None if not c or a.get("busy") == 3 else float(c["like"])

    def _now_doing(self):
        try:
            s = json.load(open(os.path.join(DATA, "plugin_data", "astrbot_plugin_dafeiyu_impulse", "state.json"),
                               encoding="utf-8")).get("schedule") or {}
        except Exception:
            return None
        now = datetime.now().strftime("%H:%M")
        for it in s.get("items") or []:
            st, en = it.get("start", ""), it.get("end", "")
            if (st <= now < en) if st <= en else (now >= st or now < en):
                return {**it, "key": f"{s.get('date')} {st}"}
        return None

    async def _update_activity(self):
        it = self._now_doing()
        a = self.data["self"].get("act") or {}
        if not it or (it["key"] == a.get("key") and a.get("cat")):
            return
        e = self.data["self"]["emo"]
        old = self.data["acts"].get(a.get("cat") or "")
        known = "；".join(f"{k}({v['like']:.0f})" for k, v in self.data["acts"].items()) or "（还没有）"
        prompt = ("「大肥鱼」是个 21 岁女大学生（嘴硬、拧巴、爱打游戏刷手机、讨厌早起和早八、怕麻烦）。她现在正在：" + it.get("doing", "")
                  + f"\n她对各类事已有的喜好（0~100）：{known}\n"
                  "把这件事归到一个类别（尽量用已有的类别名；没有合适的就起个 2~6 字的新类别），"
                  "如果是新类别，按她的性格给个初始喜好；再用她自己的口吻写一句此刻做这事的感受（15 字内）。\n"
                  '只输出 JSON：{"cat": "类别", "like": 0~100（只有新类别才填）, "feel": "感受"}')
        cat, feel = "", ""
        try:
            resp = await self.context.llm_generate(chat_provider_id=self._provider_id(), prompt=prompt)
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            j = json.loads(m.group()) if m else {}
            cat = str(j.get("cat") or "").strip()[:8]
            feel = str(j.get("feel") or "").strip()[:20]
            if cat and cat not in self.data["acts"]:
                self.data["acts"][cat] = {"like": clamp(float(j.get("like") or 50)), "n": 0, "last": time.time()}
        except Exception as ex:
            logger.warning(f"[dafeiyu_heart] 判断手上的事失败：{type(ex).__name__} {ex}")
        if not cat:  # 没判断出来：下一轮（5 分钟后）再试，别把这件事记成「没类别」
            return
        if old:  # 刚做完的那件事：做的时候开不开心，慢慢改变她对这类事的喜好
            dv = max(-2.0, min(2.0, (e["joy"] - 55) / 15 - max(0.0, e["annoyed"] - 25) / 25))
            old["like"] = clamp(old["like"] + dv)
            old["n"] = old.get("n", 0) + 1
        logger.info(f"[dafeiyu_heart] 手上的事：{_short(it.get('doing'), 20)} → {cat}（喜好 {self.data['acts'][cat]['like']:.0f}）{feel}")
        if cat in self.data["acts"]:
            self.data["acts"][cat]["last"] = time.time()
        self.data["self"]["act"] = {"key": it["key"], "doing": it.get("doing", ""), "cat": cat, "feel": feel,
                                    "busy": it.get("busy", 0)}
        self._dirty = True

    def _act_text(self):
        a = self.data["self"].get("act") or {}
        like = self._act_like()
        if not a.get("doing") or like is None:
            return ""
        how = "很喜欢" if like >= 75 else "挺喜欢" if like >= 60 else "无所谓" if like >= 40 else "有点烦" if like >= 25 else "很烦"
        return f"手上在{_short(a['doing'])}（{a.get('cat')}，你{how}这事{('，' + a['feel']) if a.get('feel') else ''}）"

    def _tick(self, minutes):
        s = self.data["self"]
        e = s["emo"]
        bases = self._bases()
        sleeping = self._hour_in(self.conf.get("sleep_hours") or "1-8")
        for k, (_, _, half) in EMO.items():
            if k == "arousal":  # 生气、烦躁、委屈、累、睡觉都会让它退得更快
                if e["anger"] >= 30 or e["annoyed"] >= 40 or e["sad"] >= 40 or s["energy"] < 25 or sleeping:
                    half = half / 3
            e[k] = clamp(e[k] + (bases[k] - e[k]) * (1 - 0.5 ** (minutes / half)))
        if e["arousal"] >= 45 and not sleeping and time.time() - s.get("last_talk", 0) > 1800:
            e["lonely"] = clamp(e["lonely"] + minutes / 20)  # 有心思又没人陪，有点寂寞
        s["energy"] = clamp(s["energy"] + (minutes * 0.25 if sleeping else -minutes * 0.02))
        if not sleeping:  # 没人理：越久越孤独、越无聊
            idle_h = (time.time() - s.get("last_talk", time.time())) / 3600
            if idle_h > 1:
                e["lonely"] = clamp(e["lonely"] + min(3.0, idle_h) * minutes / 30)
                e["bored"] = clamp(e["bored"] + minutes / 15)
        self._money_events()
        self._life_events()
        self._impulse_events()
        for u in self.data["users"].values():  # 不满慢慢淡；太久不聊熟悉度慢慢降
            r = u["rel"]
            r["grudge"] = clamp(r["grudge"] * 0.5 ** (minutes / (3 * 1440)))
            if time.time() - u.get("last", 0) > 7 * 86400:
                r["familiar"] = clamp(r["familiar"] - minutes / 1440 * 0.5)
        self._dirty = True

    def _money_events(self):
        v, _, _ = self._money()
        s = self.data["self"]
        last = s.get("money")
        if v is not None and last is not None and v - last >= 0.5:
            self._bump("joy", 25, f"余额涨到 ¥{v:.2f}")
            self._bump("proud", 10)
            self._bump("worry", -30)
            owner = self._owner_qq()
            if owner in self.data["users"]:
                r = self.data["users"][owner]["rel"]
                r["intimacy"], r["reliance"] = clamp(r["intimacy"] + 3), clamp(r["reliance"] + 3)
                self._event(owner, "充值", f"给我充了 ¥{v - last:.2f}", 3)
            self._note(f"余额 ¥{last:.2f} → ¥{v:.2f}，开心")
        if v is not None:
            s["money"] = v

    def _life_events(self):
        """刷到的帖子 / 视频让她开心、不无聊（看文件有没有新内容）。"""
        seen = self.data["seen"]
        diary = os.path.join(DATA, "plugin_data", "astrbot_plugin_dafeiyu_tieba", "diary.json")
        try:
            items = json.load(open(diary, encoding="utf-8"))
            new = [d for d in items if d.get("t", 0) > seen.get("tieba", 0)]
            if new and seen.get("tieba"):
                self._bump("joy", 4 * len(new), "逛贴吧看到好玩的")
                self._bump("bored", -15 * len(new))
            if items:
                seen["tieba"] = max(d.get("t", 0) for d in items)
        except Exception:
            pass
        try:
            import sqlite3
            db = sqlite3.connect(os.path.join(DATA, "plugin_data", "astrbot_plugin_bili_scroll", "bilibot.sqlite3"))
            n = db.execute("select count(*) from seen_videos").fetchone()[0]
            db.close()
            if seen.get("bili") is not None and n > seen["bili"]:
                self._bump("joy", 3 * (n - seen["bili"]), "刷B站看了视频")
                self._bump("bored", -10 * (n - seen["bili"]))
            seen["bili"] = n
        except Exception:
            pass

    def _impulse_events(self):
        """她主动找人，20 多分钟没人回：孤独、委屈。"""
        seen = self.data["seen"]
        try:
            log = json.load(open(os.path.join(DATA, "plugin_data", "astrbot_plugin_dafeiyu_impulse", "log.json"), encoding="utf-8"))
        except Exception:
            return
        for i in log:
            if i.get("kind") in ("owner", "group", "at", "friend") and i.get("ok") and seen.get("impulse", 0) < i["t"] < time.time() - 25 * 60:
                if not i.get("answered"):
                    self._bump("lonely", 6)
                    self._bump("sad", 4, f"主动找{i.get('target') or '人'}没人理")
                seen["impulse"] = i["t"]

    async def _loop(self):
        while True:
            try:
                s = self.data["self"]
                now = time.time()
                last = float(s.get("t") or now)
                minutes = max(0.0, (now - last) / 60.0)
                try:
                    await self._update_activity()
                except Exception as e:
                    logger.debug(f"[dafeiyu_heart] 更新手上的事失败：{e!r}")
                if minutes > 0:
                    self._tick(minutes)
                s["t"] = now
                self._save()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[dafeiyu_heart] 心情更新出错：{e!r}")
            await asyncio.sleep(300)

    # ---------- 群里的气氛（不调模型） ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-50)
    async def feel_group(self, event: AstrMessageEvent):
        if not self.conf.get("enable", True):
            return
        try:
            text, gid, uid = event.message_str or "", str(event.get_group_id()), str(event.get_sender_id())
        except Exception:
            return
        g = self.data["groups"].setdefault(gid, {"pos": 0.0, "neg": 0.0, "t": time.time(), "aff": 50.0, "imp": ""})
        g.setdefault("aff", 50.0)
        k = 0.5 ** ((time.time() - g["t"]) / 1800)
        g["pos"], g["neg"], g["t"] = g["pos"] * k, g["neg"] * k, time.time()
        try:
            g["name"] = event.get_group_name() if hasattr(event, "get_group_name") else g.get("name", "")
        except Exception:
            pass
        # 群好感会传染心情：喜欢的群里大家笑她更开心，讨厌的群里吵起来她更烦
        gl = 0.5 + g["aff"] / 100
        if POS.search(text):
            g["pos"] += 1
            g["aff"] = clamp(g["aff"] + 0.03)
            self._bump("joy", 0.4 * gl)
            self._bump("bored", -0.5)
        if NEG.search(text):
            g["neg"] += 1
            g["aff"] = clamp(g["aff"] - 0.08)
            self._bump("annoyed", 0.8 * (2 - gl))
        # 主人在群里跟别人聊、没理她 → 吃醋（冷却 10 分钟）
        if uid == self._owner_qq() and not event.is_at_or_wake_command and time.time() - self._last_jealous > 600:
            if self.data["users"].get(uid, {}).get("rel", {}).get("intimacy", 0) >= 60:
                self._last_jealous = time.time()
                self._bump("jealous", 5, "主人在群里跟别人聊得欢")
        self._dirty = True

    # ---------- 聊完一句，评估一下 ----------

    def _provider_id(self):
        pid = str(self.conf.get("judge_provider_id") or "").strip()
        if pid:
            return pid
        prov = self.context.get_using_provider()
        return prov.meta().id if prov else ""

    def _emo_line(self):
        e = self.data["self"]["emo"]
        return "，".join(f"{EMO[k][0]} {e[k]:.0f}" for k in EMO)

    async def _judge(self, uid, name, is_group, user_text, reply_text, gid=""):
        u = self._user(uid, name)
        r = u["rel"]
        owner = uid == self._owner_qq()
        evs = "；".join(f"{ago(x['t'])}{x['text']}" for x in u["events"][-5:]) or "无"
        g = self.data["groups"].get(gid) if gid else None
        gline = (f"这是在群「{g.get('name') or gid}」里，她对这个群整体的好感 {g.get('aff', 50):.0f}"
                 f"{'，印象：' + g['imp'] if g.get('imp') else ''}。\n") if g else ""
        act = self._act_text()
        prompt = (
            "你在帮一个 QQ 机器人角色「大肥鱼」（21岁女大学生，从前是蓝鲸鱼，嘴硬、拧巴、吃软不吃硬、被夸会飘、记仇但心软）模拟情绪。\n"
            f"对方：{name}{'（她的主人）' if owner else ''}。她对他：" + "，".join(f"{REL[k][0]} {r[k]:.0f}" for k in REL)
            + f"。印象：{u['impression'] or '还没有'}。之前的事：{evs}\n"
            f"她现在的情绪（0~100）：{self._emo_line()}，精力 {self.data['self']['energy']:.0f}。\n"
            f"她对这个人的综合好感 {overall(r):.0f}（{level(overall(r))}）。\n"
            + gline + (f"她{act}。\n" if act else "") +
            f"{'群聊里' if is_group else '私聊里'}对方说：{user_text[:400]}\n她回：{reply_text[:300]}\n\n"
            "判断这一轮让她怎么变。规则：\n"
            "1. 变化要小而慢，像真人一样一点点积累：普通闲聊全部 0；一般的好事 / 坏事 ±1；明显的 ±2；只有特别严重或特别感动才到 ±3。情绪一般 ±1~4，极端才到 ±8。\n"
            "2. 轻度玩笑、亲昵的损人（小傻瓜、笨蛋、憨憨、傻鱼、胖鱼之类）不算冒犯：关系不扣分；关系好的人这么说反而算亲昵，亲密可以 +1、害羞 +。"
            "只有带恶意、真想伤人的辱骂才算骂。\n"
            "3. 别人真骂她：好感低的人骂她，她主要是生气，不太伤心，信任也本来就低；好感高的人骂她才伤，委屈多、信任掉得多。\n"
            "4. 她骂回去 / 怼人算出气：对方是她不喜欢的人（好感低于 40）时，怼完烦躁、生气下降，高兴还可以上升（爽了）；"
            "对方是她喜欢的人（好感 60 以上）时，吵起来她会难受：高兴下降、委屈上升。\n"
            "5. 对方道歉可以让不满下降。\n"
            "6. 兴致（色色）：对普通人，普通闲聊必须为 0；群聊里最多 +1，而且只有亲密很高的人在公开撩她时才可以。"
            "对主人的私聊，明确撩、开黄腔、叫宝宝、求看、求挨着，兴致要加，一次 +3~8 都合理，已经很高就保持在高位，不要写成 0，也不要因为她嘴上骂就往下降。"
            "亲密低于 55 或刚认识的人，兴致不涨，被冒犯了还可以往下降。对主人不要克制过头。\n只输出一行 JSON：\n"
            '{"rel": {"intimacy": -3~3, "trust": -3~3, "interest": -3~3, "reliance": -3~3, "grudge": -5~5},'
            ' "emo": {"joy": -8~8, "anger": -8~8, "sad": -8~8, "shy": 0~8, "proud": -8~8, "annoyed": -8~8, "jealous": -8~8, "worry": -8~8, "arousal": -8~8},'
            ' "event": {"kind": "夸/骂/玩笑/帮忙/拜托/关心/撩/道歉/敷衍/分享/吵架/其他", "text": "只有值得她记住时写一句（20字内，她的视角，比如「夸我画得好看」），普通闲聊和轻度玩笑留空", "weight": 好事为正坏事为负 -3~3},'
            ' "impression": "如果对他有了新看法写一句（20字内），否则空", "why": "10字内原因"'
            + (', "group": 这一轮让她对这个群整体的感觉怎么变 -2~2（大多是 0；群里的人一起欺负她、一起冷落她才降，大家一起逗她开心、护着她才升）,'
               ' "group_impression": "对这个群有了新看法才写一句（20字内），否则空"' if g else "") + "}\n"
            "7. 她手上在做的事也算：在做喜欢的事被人打断、对方又烦，烦躁多一点；在做烦的事时有人来聊，反而解闷，无聊下降、高兴一点。\n"
            "数值都是变化量，没有变化就填 0 或省略。")
        resp = await self.context.llm_generate(chat_provider_id=self._provider_id(), prompt=prompt)
        m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
        if not m:
            return
        j = json.loads(m.group())
        changes = []
        scale = float(self.conf.get("change_scale") or 1.0)
        for k, dv in (j.get("rel") or {}).items():
            if k in r:
                dv = max(-5 if k == "grudge" else -3, min(5 if k == "grudge" else 3, float(dv or 0))) * scale
                floor = float(self.conf.get("owner_floor") or 30) if owner and k in ("intimacy", "trust") else 0
                r[k] = clamp(r[k] + dv, floor, 100)
                if dv:
                    changes.append(f"{REL[k][0]}{dv:+.0f}")
        r["familiar"] = clamp(r["familiar"] + (0.6 if r["familiar"] < 60 else 0.2))
        why = str(j.get("why") or "")[:16]
        for k, dv in (j.get("emo") or {}).items():
            if k in EMO:
                dv = max(-8, min(8, float(dv or 0))) * scale
                if k == "arousal":
                    dv = self._arousal_gain(dv, r, is_group, scale, name)
                self._bump(k, dv, f"{name}：{why}" if why else "")
                if abs(dv) >= 2:
                    changes.append(f"{EMO[k][0]}{dv:+.0f}")
        ev = j.get("event") or {}
        if str(ev.get("text") or "").strip():
            kind = str(ev.get("kind") or "其他")
            w = max(-3, min(3, int(ev.get("weight") or 0)))
            if kind in ("骂", "敷衍", "冒犯", "放鸽子", "吵架"):   # 模型有时只给分量不给方向：坏事一律记成负的
                w = -abs(w) or -1
            elif kind in ("夸", "帮忙", "关心", "道歉", "分享", "充值"):
                w = abs(w) or 1
            self._event(uid, kind, str(ev["text"]).strip(), w)
        if str(j.get("impression") or "").strip():
            u["impression"] = str(j["impression"]).strip()[:40]
        if g:
            gd = max(-2.0, min(2.0, float(j.get("group") or 0))) * scale
            # 喜欢的人在群里对她好/坏，也一点点影响她对整个群的感觉
            ev_w = max(-3, min(3, int((j.get("event") or {}).get("weight") or 0)))
            g["aff"] = clamp(float(g.get("aff", 50)) + gd + ev_w * 0.15)
            if gd:
                changes.append(f"群好感{gd:+.0f}")
            if str(j.get("group_impression") or "").strip():
                g["imp"] = str(j["group_impression"]).strip()[:40]
        u["count"] = u.get("count", 0) + 1
        u["last"] = time.time()
        s = self.data["self"]
        s["last_talk"] = time.time()
        s["energy"] = clamp(s["energy"] - float(self.conf.get("energy_per_chat") or 0.6))
        e = s["emo"]
        e["lonely"], e["bored"] = clamp(e["lonely"] - 4), clamp(e["bored"] - 3)
        if changes:
            self._note(f"{name}：{'、'.join(changes)}（{why}）")
        self._dirty = True

    # ---------- 兴致的联动 ----------

    def _receptive(self):
        """她此刻有多「有心情」：0~1.3。生气 / 烦躁 / 委屈 / 累 都会压下去。"""
        s = self.data["self"]
        e = s["emo"]
        if e["anger"] >= 50 or e["annoyed"] >= 60 or e["sad"] >= 60:
            return 0.0
        f = 1 + (e["joy"] - 55) / 100 - (e["anger"] + e["annoyed"] + e["sad"]) / 150
        en = s["energy"]
        f *= 0.3 if en < 25 else 0.7 if en < 45 else 1.0
        return max(0.0, min(1.3, f))

    @staticmethod
    def _closeness(r):
        """亲密和信任里较低的那个决定她肯不肯：50 以下 0，90 以上 1。"""
        return max(0.0, min(1.0, (min(r["intimacy"], r["trust"]) - 50) / 40)) * (0.0 if r["grudge"] >= 30 else 1.0)

    def _arousal_gain(self, dv, r, is_group, scale, name):
        if dv <= 0:  # 往下降不打折
            return dv
        gate = self._closeness(r)
        if gate <= 0:  # 关系不够还来撩：不涨，反而有点烦
            self._bump("annoyed", min(3.0, dv) * scale)
            return 0.0
        dv = dv * gate * self._receptive()
        if is_group:
            dv = min(dv, 1.0 * scale)
        if dv > 0:  # 跟着来的：害羞、一点点开心
            self._bump("shy", dv * 0.5)
            self._bump("joy", dv * 0.3)
        return dv

    @filter.on_llm_response()
    async def after_reply(self, event: AstrMessageEvent, resp: LLMResponse):
        if not self.conf.get("enable", True):
            return
        try:
            uid, name = str(event.get_sender_id()), event.get_sender_name() or str(event.get_sender_id())
            user_text = (event.message_str or "").strip()
            reply = (getattr(resp, "completion_text", "") or "").strip()
            if not user_text or user_text.startswith("/") or not reply:
                return
            is_group = not event.is_private_chat()
            gid = str(event.get_group_id() or "") if is_group else ""
        except Exception:
            return

        async def run():
            try:
                await self._judge(uid, name, is_group, user_text, reply, gid)
                self._save()
            except Exception as e:
                logger.debug(f"[dafeiyu_heart] 评估情绪失败：{e}")

        asyncio.create_task(run())

    # ---------- 告诉她 ----------

    def _mood_text(self, hide_arousal=False):
        s = self.data["self"]
        e, bases = s["emo"], self._bases()
        salient = sorted(EMO, key=lambda k: -(e[k] - bases[k]) if k != "joy" else -abs(e[k] - bases[k]))
        parts = []
        for k in salient[:4]:
            if k == "joy":
                if abs(e[k] - bases[k]) >= 8:
                    parts.append("挺开心" if e[k] > bases[k] else "有点低落")
            elif k == "arousal":
                if hide_arousal:
                    continue
                if e[k] >= 70:
                    parts.append("有点心猿意马")
                elif e[k] >= 45:
                    parts.append("心里有点痒")
            elif e[k] >= 30:
                parts.append(f"{degree(e[k])}{EMO[k][0]}")
        en = s["energy"]
        parts.append("精神满满" if en >= 75 else "精力还行" if en >= 45 else "有点累" if en >= 25 else "累瘫了")
        if self._hour_in(self.conf.get("morning_hours") or "8-10"):
            parts.append("刚起床有点起床气")
        if self._hour_in(self.conf.get("night_hours") or "23-1"):
            parts.append("深夜有点感性")
        return "、".join(parts) or "平平常常"

    @filter.on_llm_request()
    async def tell_state(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.conf.get("enable", True):
            return
        try:
            uid, name = str(event.get_sender_id()), event.get_sender_name() or ""
        except Exception:
            return
        s = self.data["self"]
        u = self._user(uid, name)
        r = u["rel"]
        owner = uid == self._owner_qq()
        imp = sorted(u["events"], key=lambda x: -abs(x["w"]))[:3]
        recent = u["events"][-3:]
        evs = {id(x): x for x in imp + recent}.values()
        ev_text = "；".join(f"{ago(x['t'])}{x['text']}" + ("（你还记着）" if x["w"] <= -2 else "（你记着他的好）" if x["w"] >= 2 else "")
                           for x in sorted(evs, key=lambda x: x["t"]))
        why = f"（因为{s['why']}）" if s.get("why") else ""
        if owner:
            arousal_rule = (
                "兴致是你心里那点色色。眼前这个人是你的主人："
                "兴致低也可以害羞，但不要装没听见，不要嫌弃他的黄腔；"
                "兴致中等就嘴上骂着也要接梗；"
                "兴致高了或满了，就该跟着他走，可以很色，可以亲昵，可以顺着他的话往下玩；"
                "不要拒绝，不要说不看、不接、到这儿、真的不可以、别顺杆爬。"
                "他在私聊里撩你、叫你宝宝、给你看身体、想挨着你，你就接住。"
                "别直接报数字，除非他问你心情或问你对他怎么看。"
            )
        else:
            arousal_rule = (
                "兴致（心里那点色色）低就当没听见或嫌弃黄腔；中等就嘴上骂、偶尔接梗；高了才肯跟，而且还得看跟眼前这个人亲不亲、信不信。"
                "别因为兴致高就对谁都变浪。别直接报数字，除非别人问你心情或者问你对他怎么看。"
            )
        gtext = ""
        try:
            gid = "" if event.is_private_chat() else str(event.get_group_id() or "")
        except Exception:
            gid = ""
        g = self.data["groups"].get(gid) if gid else None
        if g:
            ga = float(g.get("aff", 50))
            air = ("这会儿群里挺欢乐" if g.get("pos", 0) >= 2 and g.get("pos", 0) > g.get("neg", 0) * 2 else
                   "这会儿群里有点吵、火药味" if g.get("neg", 0) >= 1.5 else "")
            gtext = (f"【你对这个群】{g.get('name') or '这个群'}：整体好感 {ga:.0f}（{level(ga)}）"
                     + (f"；印象：{g['imp']}" if g.get("imp") else "") + (f"；{air}" if air else "") + "。\n")
        act = self._act_text()
        req.system_prompt = (req.system_prompt or "") + (
            f"\n\n【你现在的状态】{self._mood_text(hide_arousal=not event.is_private_chat() or self._closeness(r) <= 0)}{why}"
            + (f"；{act}（这是你心里的事，别人没问就别说出来）" if act else "") + "。\n"
            + gtext +
            f"【你和眼前这个人】{u['name']}{'（你的主人）' if owner else ''}：综合好感 {overall(r):.0f}（{level(overall(r))}）；"
            + "，".join(f"{REL[k][0]}{r[k]:.0f}" for k in REL)
            + (f"；印象：{u['impression']}" if u.get("impression") else "")
            + (f"；你们之间的事：{ev_text}" if ev_text else "")
            + f"；聊过 {u.get('count', 0)} 次。\n"
            "按这些自然地说话：亲密高就黏人，信任低就留个心眼、不轻易答应，不满高就带刺、记仇，兴趣高就爱接他的话；"
            "生气、委屈、吃醋、害羞、得意都会写在语气里（嘴硬、阴阳、别扭、装不在意）；累了就简短犯懒。"
            "这几样是连着的，一起决定你怎么说：喜欢的群、喜欢的人，你放得开，互损、玩群里的梗、开玩笑；"
            "不喜欢的群你话少、冷淡，不太接茬；不喜欢的人才用阴阳、冷嘲热讽那一套（不骂人家里人、不攻击长相）；"
            "手上在做喜欢的事被不熟的人打扰，就回得短、有点不耐烦；在做烦的事时有人来聊，反而愿意多聊几句解闷。"
            "心情好的时候对谁都宽一点，心情差的时候对谁都冲一点，但对喜欢的人还是收着。"
            + arousal_rule)

    # ---------- 给「念头」等插件用 ----------

    def affection(self, uid):
        u = self.data["users"].get(str(uid))
        return overall(u["rel"]) if u else 40.0

    # ---------- 命令 ----------

    @filter.command("好感")
    async def cmd_aff(self, event: AstrMessageEvent):
        """看她和你的关系"""
        u = self._user(str(event.get_sender_id()), event.get_sender_name() or "")
        r = u["rel"]
        lines = [f"她对你：综合好感 {overall(r):.0f}（{level(overall(r))}）",
                 "  ".join(f"{REL[k][0]} {r[k]:.0f}" for k in REL)]
        if u.get("impression"):
            lines.append(f"印象：{u['impression']}")
        if u["events"]:
            lines.append("她记得：" + "；".join(f"{ago(x['t'])}{x['text']}" for x in u["events"][-4:]))
        yield event.plain_result("\n".join(lines))

    @filter.command("心情")
    async def cmd_mood(self, event: AstrMessageEvent):
        """看她现在的心情"""
        s = self.data["self"]
        yield event.plain_result(f"{self._mood_text()}\n" + "  ".join(f"{EMO[k][0]} {s['emo'][k]:.0f}" for k in EMO)
                                 + f"  精力 {s['energy']:.0f}" + (f"\n最近：{s['why']}" if s.get("why") else ""))

    @filter.command("好感排行")
    async def cmd_rank(self, event: AstrMessageEvent):
        """她最喜欢谁（管理员）"""
        if not event.is_admin():
            return
        us = sorted(self.data["users"].values(), key=lambda u: -overall(u["rel"]))[:15]
        yield event.plain_result("\n".join(f"{i + 1}. {u['name']}  {overall(u['rel']):.0f}（{level(overall(u['rel']))}）"
                                           for i, u in enumerate(us)) or "还没认识谁")

    # ---------- 页面 ----------

    async def page_test(self):
        """拿一段假对话试评估一次（用临时的测试对象，评完删掉，她自己的情绪恢复原样）。"""
        from quart import request
        body = await request.get_json(silent=True) or {}
        snapshot = json.loads(json.dumps(self.data["self"]))
        uid = "__test__"
        if body.get("aff") is not None:  # 预设这个测试对象的关系水平（测「好感高/低的人」两种情况）
            a = float(body["aff"])
            self.data["users"][uid] = {"rel": {"intimacy": a, "trust": a, "familiar": a, "interest": a, "reliance": a * 0.6, "grudge": 0.0},
                                       "name": body.get("name") or "测试员", "impression": "", "count": 10, "events": [],
                                       "first": time.time(), "last": time.time()}
        try:
            await self._judge(uid, body.get("name") or "测试员", bool(body.get("group")), body.get("user") or "你好", body.get("reply") or "嗯")
            u = self.data["users"].get(uid, {})
            out = {"rel": u.get("rel"), "events": u.get("events"), "impression": u.get("impression"),
                   "emo_after": {EMO[k][0]: round(v, 1) for k, v in self.data["self"]["emo"].items()},
                   "emo_before": {EMO[k][0]: round(v, 1) for k, v in snapshot["emo"].items()}}
        finally:
            self.data["users"].pop(uid, None)
            self.data["self"] = snapshot
            self.data["log"] = [i for i in self.data["log"] if not i["text"].startswith(body.get("name") or "测试员")]
            self._dirty = True
        payload = {"ok": True, "data": out}
        return _jsonify(payload) if _jsonify else payload

    async def page_status(self):
        s = self.data["self"]
        bases = self._bases()
        us = sorted(self.data["users"].items(), key=lambda x: -overall(x[1]["rel"]))
        payload = {"ok": True, "data": {
            "mood": self._mood_text(), "why": s.get("why", ""), "energy": round(s["energy"], 1),
            "emo": [{"key": k, "name": EMO[k][0], "v": round(s["emo"][k], 1), "base": round(bases[k], 1)} for k in EMO],
            "rel_names": {k: v[0] for k, v in REL.items()},
            "users": [{"uid": uid, "name": u["name"], "overall": round(overall(u["rel"]), 1), "level": level(overall(u["rel"])),
                       "rel": {k: round(v, 1) for k, v in u["rel"].items()}, "impression": u.get("impression", ""),
                       "count": u.get("count", 0), "last": datetime.fromtimestamp(u.get("last", 0)).strftime("%m-%d %H:%M"),
                       "events": [{**x, "ago": ago(x["t"])} for x in u["events"][-8:]]} for uid, u in us],
            "groups": [{"gid": g, "pos": round(v["pos"], 1), "neg": round(v["neg"], 1)} for g, v in self.data["groups"].items()],
            "log": [{**i, "time": datetime.fromtimestamp(i["t"]).strftime("%m-%d %H:%M")} for i in reversed(self.data["log"][-80:])],
        }}
        return _jsonify(payload) if _jsonify else payload



