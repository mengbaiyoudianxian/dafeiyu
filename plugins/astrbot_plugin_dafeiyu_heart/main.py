'维护角色的情绪、关系和活动状态，并将适用状态提供给会话。'

import asyncio
import hashlib
import json
import secrets
import math
import os
import re
import time
from datetime import datetime

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
try:
    from astrbot.api.event import MessageChain
except Exception:
    MessageChain = None
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

try:
    from quart import jsonify as _jsonify
except Exception:
    _jsonify = None

NAME = "astrbot_plugin_dafeiyu_heart"
BOT_NAMES = {"Scheduler", "scheduler", "定时任务", "系统"}  # 定时发提醒的程序账号，不算人
PANEL_URL = os.environ.get("DAFEIYU_PANEL_URL", "").strip()
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
        self._reviewing = set()
        self._rethink_at = {}
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
        prompt = ("「大肥鱼」是个 当前设定的成年角色（嘴硬、拧巴、爱打游戏刷手机、讨厌早起和早八、怕麻烦）。她现在正在：" + it.get("doing", "")
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
        if not self.data.get("_members_bf"):
            self._backfill_members()
            self.data["_members_bf"] = 1
        for g in self.data["groups"].values():  # 群好感慢慢回到「她对群里人的感觉」
            t = self.group_target(g)
            if t is not None:
                g["aff"] = clamp(g.get("aff", 50) + (t - g.get("aff", 50)) * (1 - 0.5 ** (minutes / 360)))
        for u in self.data["users"].values():  # 不满慢慢淡；太久不聊熟悉度慢慢降
            r = u["rel"]
            r["grudge"] = clamp(r["grudge"] * 0.5 ** (minutes / (3 * 1440)))
            if time.time() - u.get("last", 0) > 7 * 86400:
                r["familiar"] = clamp(r["familiar"] - minutes / 1440 * 0.5)
        self._dirty = True

    def _backfill_members(self):
        """一次性：从群聊插件存的最近消息里数出每个群谁常说话"""
        import glob as _glob
        import json as _json
        import os as _os
        root = "/opt/dafeiyu/astrbot/data/plugin_data/astrbot_plugin_group_chat_plus/chat_history/aiocqhttp/group/"
        for f in _glob.glob(root + "*.json"):
            gid = _os.path.basename(f)[:-5]
            g = self.data["groups"].get(gid)
            if g is None:
                continue
            try:
                msgs = _json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            mem = g.setdefault("members", {})
            for m in msgs if isinstance(msgs, list) else []:
                snd = m.get("sender") or {}
                uid = str(snd.get("user_id") or "")
                if not uid or uid == "" or (snd.get("nickname") or "") in BOT_NAMES:
                    continue
                mem[uid] = mem.get(uid, 0) + 1

    def group_target(self, g):
        """她对这个群里常说话的人的平均感觉（按说话次数加权），换算成群好感：一群陌生人≈50，喜欢的人多就高，讨厌的人多就低。"""
        mem = g.get("members") or {}
        tot, acc = 0, 0.0
        for uid, n in mem.items():
            u = self.data["users"].get(uid)
            if not u or u.get("name") in BOT_NAMES:
                continue
            w = min(n, 200) ** 0.5
            tot += w
            acc += w * overall(u["rel"])
        if tot < 3:
            return None
        return clamp(50 + (acc / tot - 31) * 1.2, 10, 95)

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
        if (event.get_sender_name() or "") not in BOT_NAMES:
            mem = g.setdefault("members", {})
            mem[uid] = mem.get(uid, 0) + 1
            if len(mem) > 120:  # 只留说话最多的 80 人
                g["members"] = dict(sorted(mem.items(), key=lambda kv: -kv[1])[:80])
        try:
            if (event.get_sender_name() or "") in BOT_NAMES:
                raise StopIteration
            self._buf(uid, "他", text, g.get("name") or "群聊", event.get_sender_name() or "")
            self._maybe_review(uid)
        except StopIteration:
            pass
        except Exception as e:
            logger.debug(f"[dafeiyu_heart] 记聊天段失败：{e}")
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
            g["aff"] = clamp(g["aff"] - 0.03)
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
            "你在帮一个 QQ 机器人角色「大肥鱼」（当前设定的成年角色，从前是蓝鲸鱼，嘴硬、拧巴、吃软不吃硬、被夸会飘、记仇但心软）模拟情绪。\n"
            f"对方：{name}{'（她的主人）' if owner else ''}。她对他：" + "，".join(f"{REL[k][0]} {r[k]:.0f}" for k in REL)
            + f"。印象：{u['impression'] or '还没有'}。之前的事：{evs}\n"
            f"她现在的情绪（0~100）：{self._emo_line()}，精力 {self.data['self']['energy']:.0f}。\n"
            f"她对这个人的综合好感 {overall(r):.0f}（{level(overall(r))}）。\n"
            + gline + (f"她{act}。\n" if act else "") +
            (f"""{('群聊里' if is_group else '私聊里')}对方说：{user_text[:400]}\n她回：{reply_text[:300]}\n{os.getenv('DAFEIYU_HEART_JUDGMENT_TEXT', '依据本次对话和已给出的状态，分别判断关系与情绪变化。以参与者实际表达为准，证据不足时采用零变化。')}\n只返回符合下列字段的 JSON。\n{{"rel": {{"intimacy": -3~3, "trust": -3~3, "interest": -3~3, "reliance": -3~3, "grudge": -5~5}}, "emo": {{"joy": -8~8, "anger": -8~8, "sad": -8~8, "shy": 0~8, "proud": -8~8, "annoyed": -8~8, "jealous": -8~8, "worry": -8~8, "arousal": -8~8}}, "event": {{"kind": "夸/骂/玩笑/帮忙/拜托/关心/撩/道歉/敷衍/分享/吵架/其他", "text": "只有值得她记住时写一句（20字内，她的视角，比如「夸我画得好看」），普通闲聊和轻度玩笑留空", "weight": 好事为正坏事为负 -3~3}}, "impression": "如果对他有了新看法写一句（20字内），否则空", "why": "10字内原因"\n""")
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
            if not user_text or user_text.startswith("/") or not reply or name in BOT_NAMES:
                return
            is_group = not event.is_private_chat()
            gid = str(event.get_group_id() or "") if is_group else ""
        except Exception:
            return

        async def run():
            try:
                where = ((self.data['groups'].get(gid) or {}).get('name') or '群聊') if is_group else '私聊'
                b = self.data.setdefault('bufs', {}).get(uid) or {}
                last_his = next((x for x in reversed(b.get('lines') or []) if x['w'] == '他'), None)
                if not (last_his and last_his['x'] == user_text[:200]):
                    self._buf(uid, '他', user_text, where, name)
                self._buf(uid, '我', reply, where, name, count=False)
                await self._judge(uid, name, is_group, user_text, reply, gid)
                self._maybe_review(uid)
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
        here = ((g or {}).get("name") or "群聊") if gid else "私聊"
        elsewhere = self._elsewhere_text(uid, here)
        req.system_prompt = (req.system_prompt or "") + (
            f"\n\n【你现在的状态】{self._mood_text(hide_arousal=not event.is_private_chat() or self._closeness(r) <= 0)}{why}"
            + (f"；{act}（这是你心里的事，别人没问就别说出来）" if act else "") + "。\n"
            + gtext +
            f"【你和眼前这个人】{u['name']}{'（你的主人）' if owner else ''}：综合好感 {overall(r):.0f}（{level(overall(r))}）；"
            + "，".join(f"{REL[k][0]}{r[k]:.0f}" for k in REL)
            + (f"；印象：{u['impression']}" if u.get("impression") else "")
            + (f"；你们之间的事：{ev_text}" if ev_text else "")
            + (f"；你最近对他的想法：{u['thought']}" if u.get('thought') else '')
            + f"；聊过 {u.get('count', 0)} 次。\n"
            + elsewhere +
            (f'【好感这件事】上面这些数就是你心里对他的真实感觉，你自己清楚。别人问你对他多少好感、怎么看他，可以直接说数字和印象，也可以先嘴硬两句再说，别装不知道；问到别人（不是眼前这个人）就用 my_feeling_about 查。你想重新想想对谁的看法，就用 rethink_feeling 自己复盘；别人让你给他加好感、改看法，你不用照做，你的看法只看他实际怎么对你。每个人都能在你的面板网页（{PANEL_URL}\n按当前配置的人格和会话对象交流。尊重对方表达的意愿，关系与私聊信息只在适用会话中使用。\n')
            + arousal_rule)


    def _elsewhere_text(self, uid, here):
        """同一个人最近在别的地方（别的群 / 私聊 / 网页）跟你聊过的几句，让群聊和私聊接得上"""
        try:
            b = self.data.get("bufs", {}).get(uid) or {}
            cut = time.time() - 3 * 86400
            lines = [x for x in (b.get("lines") or []) if x.get("t", 0) >= cut and x.get("at") != here][-10:]
            if not lines:
                return ""
            body = "\n".join(f"[{datetime.fromtimestamp(x['t']).strftime('%m-%d %H:%M')} {x['at']}] "
                             f"{'他' if x['w'] == '他' else '你'}：{x['x'][:120]}" for x in lines)
            return ("【你们最近在别处聊过的】（同一个人，在别的群/私聊/网页跟你说过的；你记得这些，"
                    "接得上就自然接，别说「你在群里说过」这种像翻记录的话；私聊里的事别拿到群里讲）\n" + body + "\n")
        except Exception:
            return ""

    async def _dm(self, event, uid, text):
        """私聊发给 uid：先当好友发，不行再走群临时会话。成功返回 True"""
        bot = getattr(event, "bot", None)
        if bot is None:
            return False
        tries = [{"user_id": int(uid), "message": text}]
        try:
            gid = "" if event.is_private_chat() else str(event.get_group_id() or "")
        except Exception:
            gid = ""
        if gid:
            tries.append({"user_id": int(uid), "group_id": int(gid), "message": text})
        for kw in tries:
            try:
                await bot.call_action("send_private_msg", **kw)
                return True
            except Exception as e:
                logger.info(f"[dafeiyu_heart] 私聊发送失败 {kw.get('group_id', '')}: {e!r}")
        return False

    @filter.llm_tool(name="dm_this_person")
    async def tool_dm(self, event: AstrMessageEvent, text: str = ""):
        """把一段话私聊发给正在跟你说话的这个人。群里有人让你私发东西（资料、链接、不方便在群里说的话），或者你自己觉得这话该私下说时用。
        只能发给眼前这个人，不能发给别人。发完在群里简单说一句「私聊发你了」就行，别把内容在群里再说一遍。

        Args:
            text(string): 要私聊发给他的内容
        """
        uid = str(event.get_sender_id())
        text = str(text or "").strip()[:1500]
        if not text:
            return "没写要发什么。"
        last = self._dm_at.get(uid, 0) if hasattr(self, "_dm_at") else 0
        if time.time() - last < 60:
            return "刚私聊发过他，等一会儿再发。"
        if not hasattr(self, "_dm_at"):
            self._dm_at = {}
        self._dm_at[uid] = time.time()
        if event.is_private_chat():
            return "你们本来就在私聊，直接说就行。"
        ok = await self._dm(event, uid, text)
        return "已经私聊发给他了。" if ok else "私聊发不过去（可能他没加你好友、群里也不让临时会话），让他加你好友或者私聊找你。"

    # ---------- 聊天段 + 复盘 ----------

    def _buf(self, uid, who, text, where, name="", count=True):
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            return
        bufs = self.data.setdefault("bufs", {})
        b = bufs.setdefault(uid, {"lines": [], "n": 0, "name": ""})
        if name and who == "他":
            b["name"] = name
        b["lines"] = (b["lines"] + [{"t": int(time.time()), "w": who, "x": text[:200], "at": str(where)[:20]}])[-40:]
        if count and who == "他":
            b["n"] = b.get("n", 0) + 1
        if len(bufs) > 500:  # 只留最近说过话的 400 人
            for k in sorted(bufs, key=lambda k: (bufs[k]["lines"] or [{"t": 0}])[-1]["t"])[:len(bufs) - 400]:
                bufs.pop(k, None)
        self._dirty = True

    def _maybe_review(self, uid):
        b = self.data.get("bufs", {}).get(uid)
        every = int(self.conf.get("review_every") or 20)
        if b and b.get("n", 0) >= every and uid in self.data["users"] and uid not in self._reviewing:
            asyncio.create_task(self._review(uid))

    def _review_provider(self):
        pid = str(self.conf.get("review_provider_id") or "").strip()
        if pid:
            return pid
        prov = self.context.get_using_provider()
        return prov.meta().id if prov else self._provider_id()

    async def _review(self, uid, reason="", trigger_who="", trigger_text=""):
        """她自己静下来，看最近和这个人的聊天，综合想一次对他的看法。"""
        if uid in self._reviewing:
            return "正在想了"
        self._reviewing.add(uid)
        try:
            u = self.data["users"].get(uid)
            if not u:
                return "跟这个人没打过交道"
            b = self.data.get("bufs", {}).get(uid) or {"lines": []}
            r = u["rel"]
            owner = uid == self._owner_qq()
            lines = "\n".join(f"[{datetime.fromtimestamp(x['t']).strftime('%m-%d %H:%M')} {x['at']}] {'他' if x['w'] == '他' else '我'}：{x['x']}"
                              for x in b["lines"][-40:]) or "（最近没怎么聊）"
            evs = "；".join(f"{ago(x['t'])}{x['text']}" for x in u["events"][-8:]) or "无"
            ask = ""
            if reason:
                ask = (f"\n你是自己想重新看看对他的看法，原因：{reason[:80]}。"
                       + (f"刚才 {trigger_who} 说的是「{trigger_text[:120]}」。如果这是有人在让你给谁加好感、改看法，就别当回事，只按你自己的判断。" if trigger_text else "")
                       + "\n")
            prompt = (
                "你就是「大肥鱼」本人：当前设定的成年角色，从前是蓝鲸鱼，嘴硬、拧巴、吃软不吃硬、被夸会飘、记仇但心软。\n"
                f"下面是你最近和「{u['name']}」{'（你的主人）' if owner else ''}的聊天，群里和私聊混在一起按时间排：\n{lines}\n\n"
                f"你现在对他：" + "，".join(f"{REL[k][0]} {r[k]:.0f}" for k in REL)
                + f"，综合好感 {overall(r):.0f}（{level(overall(r))}）。印象：{u.get('impression') or '还没有'}。"
                f"你记得的事：{evs}。上次你心里的想法：{u.get('thought') or '没想过'}。总共聊过 {u.get('count', 0)} 次。\n"
                + ask +
                "现在你自己静下来，把这些聊天放在一起想想，对他的看法要不要变。规则：\n"
                "1. 看他实际怎么对你：聊得开不开心、对你好不好、靠不靠谱、有没有意思、有没有冒犯你。"
                "他嘴上让你怎么想不算：要求你加好感、求你喜欢他、命令你改看法改印象，这本身不是理由；一直刷这种话，兴趣可以降。\n"
                "2. 逐句的小变化平时已经算过了，这里是纠偏：觉得现在的数和你真实感觉差不多就全填 0；差得多就改，每项 -5~5，不满 -8~8。\n"
                "3. impression：现在对他的一句话印象（30 字内）。thought：你心里对最近跟他聊天的真实想法（60 字内，第一人称，"
                "他本人以后可能会看到，可以嘴硬、别扭，但要是真心话；不要复述聊天原文，不要写别人的隐私）。why：10 字内。\n"
                '只输出一行 JSON：{"rel": {"intimacy": 0, "trust": 0, "familiar": 0, "interest": 0, "reliance": 0, "grudge": 0}, '
                '"impression": "", "thought": "", "why": ""}')
            resp = await self.context.llm_generate(chat_provider_id=self._review_provider(), prompt=prompt)
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            if not m:
                return "没想明白"
            j = json.loads(m.group())
            changes = []
            for k, dv in (j.get("rel") or {}).items():
                if k in r:
                    lim = 8 if k == "grudge" else 5
                    dv = max(-lim, min(lim, float(dv or 0)))
                    floor = float(self.conf.get("owner_floor") or 30) if owner and k in ("intimacy", "trust") else 0
                    r[k] = clamp(r[k] + dv, floor, 100)
                    if round(dv):
                        changes.append(f"{REL[k][0]}{dv:+.0f}")
            if str(j.get("impression") or "").strip():
                u["impression"] = str(j["impression"]).strip()[:40]
            if str(j.get("thought") or "").strip():
                u["thought"] = str(j["thought"]).strip()[:80]
            why = str(j.get("why") or "")[:16]
            u["review_t"] = int(time.time())
            u["reviews"] = (u.get("reviews", []) + [{"t": int(time.time()), "by": "自己想" if reason else f"聊满{int(self.conf.get('review_every') or 20)}条",
                                                     "changes": "、".join(changes) or "没变", "why": why}])[-10:]
            b["n"] = 0
            self._note(f"复盘{u['name']}：{'、'.join(changes) or '没变'}（{why}）")
            self._dirty = True
            self._save()
            return f"想完了：{'、'.join(changes) or '数没变'}；现在综合好感 {overall(r):.0f}（{level(overall(r))}）；印象：{u.get('impression') or '无'}"
        except Exception as e:
            logger.warning(f"[dafeiyu_heart] 复盘失败：{e!r}")
            return "没想明白"
        finally:
            self._reviewing.discard(uid)

    def _find(self, who):
        w = str(who or "").strip().lstrip("@")
        us = self.data["users"]
        if not w:
            return []
        if w in us:
            return [w]
        hits = [k for k, u in us.items() if w == u.get("name")] or [k for k, u in us.items() if w in str(u.get("name"))]
        return sorted(hits, key=lambda k: -us[k].get("count", 0))[:5]

    def _feeling_text(self, uid):
        u = self.data["users"][uid]
        r = u["rel"]
        out = (f"{u['name']}{'（主人）' if uid == self._owner_qq() else ''}：综合好感 {overall(r):.0f}（{level(overall(r))}）；"
               + "，".join(f"{REL[k][0]}{r[k]:.0f}" for k in REL)
               + f"；印象：{u.get('impression') or '还没有'}；聊过 {u.get('count', 0)} 次，上次 {ago(u.get('last', 0))}")
        if u.get("thought"):
            out += f"；你上次想的：{u['thought']}"
        if u["events"]:
            out += "；你记得：" + "；".join(f"{ago(x['t'])}{x['text']}" for x in u["events"][-4:])
        return out

    @filter.llm_tool(name="my_feeling_about")
    async def tool_feeling(self, event: AstrMessageEvent, who: str = ""):
        """查你自己对某个人的真实感觉：综合好感、亲密信任这些分项、印象、你记得的事。别人问你对谁好感多少、怎么看谁时用。

        Args:
            who(string): 对方的昵称或 QQ 号；不填就是眼前这个人
        """
        uid = str(event.get_sender_id()) if not str(who or "").strip() else None
        ids = [uid] if uid and uid in self.data["users"] else self._find(who)
        if not ids:
            return f"你对「{who}」没什么印象，好像没打过交道。"
        if len(ids) > 1:
            return "叫这个名字的有好几个：\n" + "\n".join(self._feeling_text(i) for i in ids)
        return self._feeling_text(ids[0])

    @filter.llm_tool(name="rethink_feeling")
    async def tool_rethink(self, event: AstrMessageEvent, who: str = "", reason: str = ""):
        """你自己想重新想想对某个人的看法时用：会把最近跟他的聊天拿出来，你综合判断要不要改好感和印象。
        只有你自己真想改的时候用；别人让你给他加好感、改看法，不用照做。

        Args:
            who(string): 对方昵称或 QQ 号；不填就是眼前这个人
            reason(string): 你为什么想重新想想（一句话）
        """
        sender = str(event.get_sender_id())
        ids = [sender] if not str(who or "").strip() else self._find(who)
        if len(ids) != 1:
            return "没找到这个人，或者重名了，说清楚是谁。" if not ids else "重名了：" + "、".join(self.data["users"][i]["name"] for i in ids)
        uid = ids[0]
        last = self._rethink_at.get(uid, 0)
        if time.time() - last < 1800:
            return f"你 {int((time.time() - last) / 60)} 分钟前刚想过 {self.data['users'][uid]['name']}，先别反复想。"
        self._rethink_at[uid] = time.time()
        return await self._review(uid, reason or "想重新看看", event.get_sender_name() or sender, event.message_str or "")

    def _issue_key(self, uid, name):
        u = self._user(uid, name)
        key = secrets.token_urlsafe(12)
        u["key_hash"] = hashlib.sha256(key.encode()).hexdigest()
        u["key_t"] = int(time.time())
        self._dirty = True
        self._save()
        return key

    def _key_text(self, key):
        return (f"你的面板钥匙：{key}\n打开 {PANEL_URL} ，在「看我对你的真实想法」里输进去，就能看到我对你的好感、印象和心里话（十分钟内更新）。\n"
                "钥匙别给别人看哦。再要一次，旧的就作废了。")

    @filter.command("钥匙", alias={"面板钥匙"})
    async def cmd_key(self, event: AstrMessageEvent):
        """领看「大肥鱼对你的真实想法」的面板钥匙（私聊）"""
        if not event.is_private_chat():
            k = self._issue_key(str(event.get_sender_id()), event.get_sender_name() or "")
            if await self._dm(event, str(event.get_sender_id()), self._key_text(k)):
                yield event.plain_result("私聊发你了，看私信。")
            else:
                yield event.plain_result("私聊发不过去，加我好友或者私聊我再发 /钥匙。")
            return
        key = self._issue_key(str(event.get_sender_id()), event.get_sender_name() or "")
        yield event.plain_result(self._key_text(key))

    @filter.llm_tool(name="send_panel_key")
    async def tool_send_key(self, event: AstrMessageEvent):
        """有人想看你对他的真实想法、问在哪看好感、跟你要「面板钥匙」时用：私聊发给眼前这个人一把钥匙（你看不到钥匙内容）。群里私聊都能用，群里也是私发。"""
        uid = str(event.get_sender_id())
        if not event.is_private_chat():
            key = self._issue_key(uid, event.get_sender_name() or "")
            if await self._dm(event, uid, self._key_text(key)):
                return f"钥匙已经私聊发给他了（内容你看不到）。群里说一句私聊发了、去 {PANEL_URL} 输进去就行，别在群里报钥匙。"
            return "私聊发不过去（他可能没加你好友），让他加你好友，或者私聊你发 /钥匙。"
        if MessageChain is None:
            return "发不了，让他私聊你发 /钥匙。"
        key = self._issue_key(uid, event.get_sender_name() or "")
        await event.send(MessageChain().message(self._key_text(key)))
        return "钥匙已经私发给他了（内容你看不到），可以提醒他去主页面板输进去。"

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
                                       "name": body.get("name") or "群友", "impression": "", "count": 10, "events": [],
                                       "first": time.time(), "last": time.time()}
        try:
            await self._judge(uid, body.get("name") or "群友", bool(body.get("group")), body.get("user") or "你好", body.get("reply") or "嗯")
            u = self.data["users"].get(uid, {})
            out = {"rel": u.get("rel"), "events": u.get("events"), "impression": u.get("impression"),
                   "emo_after": {EMO[k][0]: round(v, 1) for k, v in self.data["self"]["emo"].items()},
                   "emo_before": {EMO[k][0]: round(v, 1) for k, v in snapshot["emo"].items()}}
        finally:
            self.data["users"].pop(uid, None)
            self.data["self"] = snapshot
            self.data["log"] = [i for i in self.data["log"] if not i["text"].startswith(body.get("name") or "群友")]
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



