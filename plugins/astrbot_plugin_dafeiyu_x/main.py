"""大肥鱼刷推特：像刷 B 站一样，按她的日程和心情自己去刷 X（推特），偶尔评论，有意思的发给主人。

规矩（写死在代码里，不靠模型自觉）：
- 政治、时政争议、战争站队的帖子：不看、不评、不转。
- 色情帖：永远不在群里发；私聊里只有对方的「信任」和「好感」（心情插件里的 trust / intimacy）都到阈值（默认 85）才发；
  不评论色情帖；涉及未成年或幼态角色的，一律不碰，没有阈值可言——发图前还要识图再确认一遍。
- 所有推特流量走 19224 节点（和 QQ 同一个出口），免得主人的账号被当成异地登录。
"""

import asyncio
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
from astrbot.api.message_components import Image, Plain, Video
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

COOKIES = "/opt/dafeiyu/private/x_cookies.json"
OWNER_FILE = "/opt/dafeiyu/private/owner-qq.txt"
POLITICS = re.compile(r"(?i)trump|biden|harris|election|vote|democrat|republican|maga|gaza|israel|palestin|ukrain|russia|putin|"
                      r"zelensky|nato|ccp|taiwan|hong ?kong|tibet|xinjiang|uyghur|习近平|共产党|台独|港独|藏独|疆独|六四|民主|选举|"
                      r"总统|政府|政治|中共|川普|拜登|普京|以色列|巴勒斯坦|乌克兰|俄罗斯|战争|抗议|示威")
CLASSIFY = (
    "下面是一批推特帖子。逐条判断，只输出一个 JSON 数组，每项："
    '{{"i":序号,"politics":是否涉及政治/时政争议/选举/战争站队/国家民族对立(true/false),'
    '"nsfw":是否色情、裸露、性暗示明显(true/false),'
    '"minor":是否涉及未成年人，或者画得像小孩的角色出现在性/裸露相关内容里(true/false，拿不准就 true),'
    '"interest":一个爱吃、爱睡、爱二次元和猫猫狗狗、喜欢搞笑日常的 21 岁女大学生会多感兴趣（0-10）,'
    '"gist":"用中文一句话说这条讲了什么（30 字内）"}}\n\n{items}'
)


def _clip(s, n):
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _load(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


class DafeiyuX(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.conf = config or {}
        self.dir = os.path.join(get_astrbot_data_path(), "plugin_data", "astrbot_plugin_dafeiyu_x")
        self.media_dir = os.path.join(self.dir, "media")
        os.makedirs(self.media_dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.state = _load(self.state_path, {})
        for k, v in (("seen", []), ("items", []), ("log", []), ("day", {}), ("next_at", 0)):
            self.state.setdefault(k, v)
        self._client = None
        self._running = None
        self._watch = None
        self.login_state = ""
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        for t in (self._task, self._watch):
            if t:
                t.cancel()

    def _save(self):
        self.state["seen"] = self.state["seen"][-3000:]
        self.state["items"] = self.state["items"][-200:]
        self.state["log"] = self.state["log"][-300:]
        json.dump(self.state, open(self.state_path, "w", encoding="utf-8"), ensure_ascii=False)

    def _today(self):
        d = datetime.now().strftime("%Y-%m-%d")
        if self.state["day"].get("d") != d:
            self.state["day"] = {"d": d, "sessions": 0, "comments": 0, "shares": 0}
        return self.state["day"]

    def _log(self, kind, text, **kw):
        self.state["log"].append({"t": time.time(), "kind": kind, "text": _clip(text, 300), **kw})
        self._save()

    # ---------- 账号 ----------

    def cookies_ok(self):
        c = _load(COOKIES, {})
        return bool(c.get("auth_token") and c.get("ct0"))

    def set_cookies(self, auth_token, ct0):
        auth_token, ct0 = str(auth_token or "").strip(), str(ct0 or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{20,80}", auth_token) or not re.fullmatch(r"[0-9a-fA-F]{20,300}", ct0):
            raise ValueError("格式不对：auth_token 和 ct0 都是一长串十六进制字符")
        json.dump({"auth_token": auth_token, "ct0": ct0}, open(COOKIES, "w"))
        os.chmod(COOKIES, 0o600)
        self._client = None

    def set_cookies_any(self, text):
        """随便贴：整段 Cookie 字符串、Cookie-Editor 导出的 JSON、或者两行值，都能认出 auth_token 和 ct0。"""
        text = str(text or "").strip()
        found = {}
        try:
            j = json.loads(text)
            rows = j if isinstance(j, list) else [{"name": k, "value": v} for k, v in j.items()] if isinstance(j, dict) else []
            for r in rows:
                if isinstance(r, dict) and r.get("name") in ("auth_token", "ct0"):
                    found[r["name"]] = str(r.get("value") or "")
        except Exception:
            pass
        if len(found) < 2:
            for name in ("auth_token", "ct0"):
                m = re.search(rf"{name}\s*[=:\t ]\s*\"?([0-9a-fA-F]{{20,300}})", text)
                if m:
                    found[name] = m.group(1)
        if len(found) < 2:
            hexes = re.findall(r"\b[0-9a-fA-F]{40}\b|\b[0-9a-fA-F]{100,300}\b", text)
            short = [h for h in hexes if len(h) == 40]
            long_ = [h for h in hexes if len(h) >= 100]
            if short and long_:
                found.setdefault("auth_token", short[0])
                found.setdefault("ct0", long_[0])
        if not found.get("auth_token") or not found.get("ct0"):
            raise ValueError("没认出 auth_token 和 ct0，贴的内容里要包含这两个")
        self.set_cookies(found["auth_token"], found["ct0"])

    async def login_password(self, user, password, extra=""):
        """账号密码登录一次，只留下登录后的 Cookie，密码不存。"""
        from twikit import Client
        user, password = str(user or "").strip(), str(password or "")
        if not user or not password:
            raise ValueError("账号和密码都要填")
        cl = Client("en-US", proxy=str(self.conf.get("proxy") or "").strip() or None)
        try:
            await asyncio.wait_for(cl.login(auth_info_1=user, auth_info_2=(str(extra).strip() or None), password=password), timeout=90)
        except Exception as e:
            msg = str(e)
            if "LoginFlow" in msg or "denied" in msg.lower() or "suspicious" in msg.lower():
                raise RuntimeError("推特拦了这次登录（异地/人机验证）。填上「邮箱或手机号」再试，还不行就用「粘贴 Cookie」")
            raise RuntimeError(f"登录失败：{_clip(msg, 160)}")
        ck = cl.get_cookies()
        self.set_cookies(ck.get("auth_token"), ck.get("ct0"))

    # ---------- 网页登录：服务器上开个真浏览器，主人远程在里面登录，登好了自动取凭证 ----------

    VNC_URL = "http://127.0.0.1:16080/vnc.html?autoconnect=1&resize=scale&reconnect=1"

    @staticmethod
    async def _sh(*args, timeout=30):
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
        return p.returncode, (out or b"").decode(errors="replace").strip()

    async def _browser_cookies(self):
        async with aiohttp.ClientSession() as s:
            ver = await (await s.get("http://127.0.0.1:9333/json/version", timeout=aiohttp.ClientTimeout(total=5))).json()
            async with s.ws_connect(ver["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                await ws.send_json({"id": 1, "method": "Storage.getCookies"})
                while True:
                    m = await asyncio.wait_for(ws.receive_json(), timeout=10)
                    if m.get("id") == 1:
                        return {c["name"]: c["value"] for c in m["result"]["cookies"]
                                if c.get("domain", "").endswith("x.com") and c["name"] in ("auth_token", "ct0")}

    async def start_browser_login(self):
        code, msg = await self._sh("sudo", "-n", "systemctl", "restart", "dafeiyu-xlogin")
        if code != 0:
            raise RuntimeError(f"开浏览器失败：{_clip(msg, 120)}")
        if self._watch and not self._watch.done():
            self._watch.cancel()
        self._watch = asyncio.create_task(self._watch_login())
        return self.VNC_URL

    async def _watch_login(self):
        """盯着远程浏览器，主人一登好就把凭证存下来、把浏览器关掉。最多等 20 分钟。"""
        self.login_state = "waiting"
        end = time.time() + 1200
        await asyncio.sleep(8)
        while time.time() < end:
            try:
                ck = await self._browser_cookies()
                if ck.get("auth_token") and ck.get("ct0"):
                    self.set_cookies(ck["auth_token"], ck["ct0"])
                    self.login_state = "done"
                    self._log("登录", "在网页里登好了推特")
                    await asyncio.sleep(2)
                    await self._sh("sudo", "-n", "systemctl", "stop", "dafeiyu-xlogin")
                    return
            except Exception:
                pass
            await asyncio.sleep(4)
        self.login_state = "timeout"

    def clear_cookies(self):
        if os.path.exists(COOKIES):
            os.remove(COOKIES)
        self._client = None

    async def client(self):
        if self._client is None:
            from twikit import Client
            if not self.cookies_ok():
                raise RuntimeError("推特还没登录：去控制中心「推特」页填 Cookie")
            proxy = str(self.conf.get("proxy") or "").strip() or None
            cl = Client("en-US", proxy=proxy)
            cl.set_cookies(_load(COOKIES, {}))
            self._client = cl
        return self._client

    async def whoami(self):
        cl = await self.client()
        # 推特现在把「取自己资料」的接口（/user、v1.1 settings）封了，会 404。
        # 先照常试一次，拿不到就退一步：能拉到首页就算登录成功（刷推不需要知道自己是谁）。
        try:
            u = await cl.user()
            return {"name": u.name, "screen_name": u.screen_name,
                    "followers": u.followers_count, "following": u.following_count}
        except Exception:
            await cl.get_latest_timeline(count=1)
            return {"name": "", "screen_name": "", "followers": None, "following": None, "limited": True}

    # ---------- 别的插件 ----------

    def _star(self, name):
        try:
            md = self.context.get_registered_star(name)
            return getattr(md, "star_cls", None) if md and getattr(md, "activated", True) else None
        except Exception:
            return None

    def _owner(self):
        try:
            return open(OWNER_FILE).read().strip()
        except Exception:
            return ""

    def _owner_umo(self):
        o = self._owner()
        return f"{self.conf.get('platform_id') or '大肥鱼'}:FriendMessage:{o}" if o else ""

    def rel_ok(self, uid):
        """色情内容门槛：心情插件里这个人的信任和好感都要到阈值。"""
        heart = self._star("astrbot_plugin_dafeiyu_heart")
        try:
            rel = heart.data["users"][str(uid)]["rel"]
            th = float(self.conf.get("nsfw_threshold") or 85)
            return float(rel.get("trust", 0)) >= th and float(rel.get("intimacy", 0)) >= th
        except Exception:
            return False

    async def _plain_llm(self, prompt):
        prov = self.context.get_using_provider()
        if prov is None:
            raise RuntimeError("没有可用的聊天模型")
        resp = await self.context.llm_generate(chat_provider_id=prov.meta().id, prompt=prompt,
                                               system_prompt="你是内容审核和分类助手，只按要求输出 JSON。")
        return (resp.completion_text or "") if resp else ""

    async def _her(self, prompt):
        """用她自己的人设和口气说话（借念头插件的模型和人设）。"""
        imp = self._star("astrbot_plugin_dafeiyu_impulse")
        if imp is None:
            return ""
        life = ""
        try:
            life = imp._life_prompt()
        except Exception:
            pass
        return imp._clean_reply(await imp._llm(f"{life}\n{prompt}"))

    # ---------- 刷 ----------

    def _mood_ok(self):
        """按日程和心情决定现在想不想刷。返回 (想不想, 原因)。"""
        if not self.conf.get("enable", True):
            return False, "关着"
        if not self.cookies_ok():
            return False, "没登录"
        d = self._today()
        lim = int(self.conf.get("sessions_per_day") or 0)  # 0 = 不限
        if lim and d["sessions"] >= lim:
            return False, "今天刷够了"
        imp = self._star("astrbot_plugin_dafeiyu_impulse")
        busy = 0
        if imp is not None:
            try:
                if imp._quiet():
                    return False, "在睡觉"
                it = imp._now_item() or {}
                busy = int(it.get("busy", 0))
            except Exception:
                pass
        if busy >= 3:
            return False, "在睡觉"
        chance = {0: 0.55, 1: 0.3, 2: 0.08}.get(busy, 0.3)
        heart = self._star("astrbot_plugin_dafeiyu_heart")
        try:
            emo = heart.data["self"]["emo"]
            chance *= 1 + float(emo.get("bored", 0)) / 100 + float(emo.get("lonely", 0)) / 200
            if float(heart.data["self"].get("energy", 80)) < 25:
                chance *= 0.5
        except Exception:
            pass
        return random.random() < min(chance, 0.9), f"忙碌度 {busy}"

    async def _loop(self):
        await asyncio.sleep(90)
        while True:
            try:
                if time.time() >= self.state.get("next_at", 0):
                    ok, why = self._mood_ok()
                    if ok and not (self._running and not self._running.done()):
                        self._running = asyncio.create_task(self.session("自己想刷"))
                    gap = random.uniform(float(self.conf.get("check_min") or 25), float(self.conf.get("check_max") or 70))
                    self.state["next_at"] = time.time() + gap * 60
                    self._save()
            except Exception as e:
                logger.warning(f"[dafeiyu_x] 调度出错：{e}")
            await asyncio.sleep(60)

    @staticmethod
    def _tweet_dict(t):
        rt = getattr(t, "retweeted_tweet", None)
        src = rt or t
        media = []
        for m in (getattr(src, "media", None) or []):
            d = getattr(m, "_data", {}) or {}
            if d.get("type") == "photo":
                media.append({"type": "photo", "url": (d.get("media_url_https") or "") + "?name=orig"})
            elif d.get("type") in ("video", "animated_gif"):
                vs = [v for v in (d.get("video_info") or {}).get("variants", []) if v.get("content_type") == "video/mp4"]
                if vs:
                    best = max(vs, key=lambda v: v.get("bitrate") or 0)
                    media.append({"type": "video", "url": best["url"], "thumb": d.get("media_url_https")})
        u = getattr(src, "user", None)
        return {"id": str(src.id), "author": getattr(u, "name", ""), "handle": getattr(u, "screen_name", ""),
                "author_id": str(getattr(u, "id", "") or ""),
                "text": getattr(src, "full_text", None) or getattr(src, "text", ""), "lang": getattr(src, "lang", ""),
                "sensitive": bool(getattr(src, "possibly_sensitive", False)), "media": media[:4],
                "url": f"https://x.com/{getattr(u, 'screen_name', 'i')}/status/{src.id}", "via_rt": bool(rt)}

    async def _classify(self, tws):
        items = "\n".join(f"[{i}] @{t['handle']}：{_clip(t['text'], 280)}"
                          + (f"（带 {len(t['media'])} 个图/视频）" if t["media"] else "")
                          + ("（推特标了敏感内容）" if t["sensitive"] else "") for i, t in enumerate(tws))
        raw = await self._plain_llm(CLASSIFY.format(items=items))
        m = re.search(r"\[.*\]", raw, re.S)
        out = {}
        for r in (json.loads(m.group()) if m else []):
            try:
                out[int(r["i"])] = r
            except Exception:
                continue
        return out

    async def session(self, why="自己想刷"):
        """刷一轮：拉首页 → 过滤政治 → 打标签 → 可能评论一条 → 可能发主人一条。"""
        d = self._today()
        d["sessions"] += 1
        self._save()
        cl = await self.client()
        feed = await (cl.get_timeline(count=30) if random.random() < 0.6 else cl.get_latest_timeline(count=30))
        seen = set(self.state["seen"])
        tws = []
        for t in feed or []:
            try:
                td = self._tweet_dict(t)
            except Exception:
                continue
            if td["id"] in seen or not td["text"]:
                continue
            seen.add(td["id"])
            self.state["seen"].append(td["id"])
            tws.append(td)
        if not tws:
            self._log("刷", f"{why}：刷了一圈，没新东西")
            return {"seen": 0}
        tags = await self._classify(tws[:25])
        kept = []
        for i, t in enumerate(tws[:25]):
            tg = tags.get(i, {})
            t["politics"] = bool(tg.get("politics")) or bool(POLITICS.search(t["text"]))
            t["nsfw"] = bool(tg.get("nsfw")) or t["sensitive"]
            t["minor"] = bool(tg.get("minor")) if t["nsfw"] else bool(tg.get("minor") and tg.get("nsfw"))
            t["interest"] = int(tg.get("interest") or 0)
            t["gist"] = _clip(tg.get("gist") or t["text"], 60)
            t["t"] = time.time()
            if t["politics"] or t["minor"]:
                continue  # 政治、涉及未成年：直接当没看见，连记录都不留
            kept.append(t)
        self.state["items"].extend(kept)
        self._save()
        nsfw_n = sum(t["nsfw"] for t in kept)
        self._log("刷", f"{why}：看了 {len(tws)} 条，留下 {len(kept)} 条（跳过政治/不该看的 {min(len(tws), 25) - len(kept)} 条，色情 {nsfw_n} 条）")
        done = {"seen": len(tws), "kept": len(kept)}
        today = time.strftime("%Y-%m-%d")
        for name, fn in (("engage", self._engage), ("comment", self._maybe_comment), ("post", self._maybe_post),
                         ("share", self._maybe_share)):
            if name in ("comment", "post") and self.state.get("write_limited") == today:
                continue  # 推特说今天发够了：今天就不再硬发，明天自然恢复
            try:
                done[name] = await fn(kept)
            except Exception as e:
                if name in ("comment", "post") and ("344" in str(e) or "daily limit" in str(e).lower()):
                    self.state["write_limited"] = today
                    self._save()
                    logger.info(f"[dafeiyu_x] 推特说今天发推/回复到上限了，今天先不发了")
                    continue
                logger.warning(f"[dafeiyu_x] {name} 出错：{e}")
        return done

    @staticmethod
    async def _gap(a=8, b=30):
        """动作之间像人一样停一下，也免得被推特当成机器刷接口。"""
        await asyncio.sleep(random.uniform(a, b))

    async def _engage(self, kept):
        """喜欢的点赞，特别喜欢的作者关注。不设上限，按她的兴趣来。"""
        if not self.conf.get("like_enable", True):
            return {}
        cl = await self.client()
        liked, followed = 0, 0
        follows = set(self.state.setdefault("followed", []))
        for t in sorted(kept, key=lambda x: -x["interest"]):
            if t["politics"] or t.get("minor"):
                continue
            if t["interest"] >= int(self.conf.get("like_min_interest") or 5) and not t.get("liked"):
                try:
                    await cl.favorite_tweet(t["id"])
                    t["liked"] = True
                    liked += 1
                    self._log("点赞", f"@{t['handle']}：{t['gist']}", url=t["url"])
                except Exception as e:
                    logger.info(f"[dafeiyu_x] 点赞失败：{_clip(e, 80)}")
                await self._gap()
            aid = t.get("author_id")
            if (aid and aid not in follows and not t["nsfw"]
                    and t["interest"] >= int(self.conf.get("follow_min_interest") or 8)):
                try:
                    await cl.follow_user(aid)
                    follows.add(aid)
                    followed += 1
                    self._log("关注", f"@{t['handle']}（因为这条：{t['gist']}）", url=t["url"])
                except Exception as e:
                    logger.info(f"[dafeiyu_x] 关注失败：{_clip(e, 80)}")
                await self._gap()
        self.state["followed"] = list(follows)[-2000:]
        self._save()
        if liked or followed:
            logger.info(f"[dafeiyu_x] 点赞 {liked} 条，关注 {followed} 个")
        return {"liked": liked, "followed": followed}

    async def _maybe_comment(self, kept):
        """想回的都回：兴趣够的挨个问她，她不想回就 SKIP。不设每日上限。"""
        d = self._today()
        if not self.conf.get("comment_enable", True):
            return []
        cap = int(self.conf.get("comments_per_day") or 0)  # 0 = 不限
        pool = sorted([t for t in kept if not t["nsfw"] and not t["politics"] and not t.get("commented")
                       and t["interest"] >= int(self.conf.get("comment_min_interest") or 6)], key=lambda x: -x["interest"])
        out = []
        for t in pool:
            if cap and d["comments"] >= cap:
                break
            lang = "中文" if (t["lang"] or "").startswith("zh") else ("日语" if t["lang"] == "ja" else "英语")
            text = await self._her(
                f"你在刷推特，看到 @{t['handle']} 发了：\n「{_clip(t['text'], 400)}」\n"
                f"你想在下面回一句的话，用{lang}，像普通网友随手回复，一两句、口语、别客套别像客服，别提自己是 AI，"
                "别带话题标签和链接，不涉及政治。没什么想说的就只输出 SKIP。只输出要回的话。")
            text = _clip(text, 200)
            if not text or "SKIP" in text.upper() or POLITICS.search(text):
                continue
            if await self._out_of_character(text):
                logger.info(f"[dafeiyu_x] 想回 @{t['handle']} 的话像出戏/拒绝话，没发：{_clip(text, 80)}")
                continue
            await (await self.client()).create_tweet(text=text, reply_to=t["id"])
            d["comments"] += 1
            t["commented"] = text
            self._log("评论", text, url=t["url"], gist=t["gist"])
            logger.info(f"[dafeiyu_x] 评论了 {t['url']}：{text}")
            out.append(text)
            await self._gap(20, 60)
        return out

    async def _out_of_character(self, text, where="发到推特上"):
        """发前看一眼：是不是模型的拒绝/出戏话（比如「我不能…」「作为 AI…」「不演这个人设」），哪怕只夹了一句。判断失败就当正常。"""
        try:
            raw = await self._plain_llm(
                f"下面是一个年轻人准备{where}的内容。判断它里面有没有聊天模型的拒绝话、出戏话或系统说明"
                "（例如「抱歉我不能」「作为 AI」「我无法协助」「这个请求」「不演这个人设/不按设定说话」之类，哪怕只夹了一句），"
                "而不全是普通人自然说的话。\n"
                f"内容：{text}\n"
                '只输出 JSON：{"bad": true 或 false}')
            m = re.search(r"\{.*\}", raw or "", re.S)
            return bool(m and json.loads(m.group()).get("bad"))
        except Exception as e:
            logger.warning(f"[dafeiyu_x] 出戏检查失败，按正常处理：{e}")
            return False

    async def _maybe_post(self, kept):
        """刷完问问她想不想自己发一条推（日常、心情、刚刷到的东西的感想）。不设上限，她不想就不发。"""
        if not self.conf.get("post_enable", True):
            return ""
        recent = [l["text"] for l in self.state.get("log", []) if l.get("kind") == "发推"][-5:]
        seen = "\n".join(f"- @{t['handle']}：{t['gist']}" for t in sorted(kept, key=lambda x: -x["interest"])[:5] if not t["nsfw"])
        text = await self._her(
            "你刚刷完一会儿推特。想不想自己发一条推？可以是你现在在干嘛、心情、吃了啥，或者对刚刷到的东西的想法，"
            "像普通年轻人发推那样随手、口语、可以中文也可以夹点英文，别像营销号，别提自己是 AI，不涉及政治，不发色情。\n"
            f"刚刷到的：\n{seen or '（没什么特别的）'}\n"
            f"你最近发过的（别重复）：{recent or '（还没发过）'}\n"
            "想发就只输出推文内容（280 字以内）；不想发就只输出 SKIP。")
        text = _clip(text, 270)
        if not text or "SKIP" in text.upper() or POLITICS.search(text):
            return ""
        if await self._out_of_character(text):
            logger.info(f"[dafeiyu_x] 想发的推像出戏/拒绝话，没发：{_clip(text, 80)}")
            return ""
        await (await self.client()).create_tweet(text=text)
        self._log("发推", text)
        logger.info(f"[dafeiyu_x] 发了一条推：{text}")
        return text

    async def _maybe_share(self, kept):
        d = self._today()
        owner = self._owner()
        if not owner or d["shares"] >= int(self.conf.get("shares_per_day") or 6):
            return ""
        pool = [t for t in kept if t["interest"] >= 8 and not t.get("shared")]
        if not pool or random.random() > float(self.conf.get("share_chance") or 0.6):
            return ""
        pool.sort(key=lambda x: -x["interest"])
        t = pool[0]
        if t["nsfw"] and not (self.conf.get("nsfw_share_enable", True) and self.rel_ok(owner)):
            pool = [x for x in pool if not x["nsfw"]]
            if not pool:
                return ""
            t = pool[0]
        r = await self.send_item(t, self._owner_umo(), private=True, requester=owner, mode="owner")
        if r.get("sent"):
            d["shares"] += 1
            self._save()
        return r.get("reason") or "发了"

    # ---------- 发 ----------

    async def _download(self, url, ext):
        proxy = str(self.conf.get("proxy") or "").strip() or None
        path = os.path.join(self.media_dir, uuid.uuid4().hex + ext)
        async with aiohttp.ClientSession() as s:
            async with s.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=120),
                             headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status != 200:
                    return None
                size = 0
                with open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > int(self.conf.get("max_video_mb") or 80) * 1024 * 1024:
                            f.close()
                            os.remove(path)
                            return None
                        f.write(chunk)
        return path

    async def _vision_minor_check(self, paths):
        """色情帖发图前再看一眼：画面里有没有未成年/幼态。拿不准就算有。"""
        prov = self.context.get_provider_by_id(str(self.conf.get("vision_provider_id") or "grok/grok-4.6"))
        if prov is None or not paths:
            return False
        resp = await asyncio.wait_for(prov.text_chat(
            prompt='只输出 JSON：{"minor": 图里出现的人物（包括动漫角色）是否看起来未成年或明显幼态（true/false，拿不准就 true）}',
            session_id=uuid.uuid4().hex, image_urls=paths[:2], persist=False), timeout=60)
        t = getattr(resp, "completion_text", "") or ""
        m = re.search(r'"minor"\s*:\s*(true|false)', t)
        return (m.group(1) == "false") if m else False

    async def send_item(self, t, umo, private, requester="", mode=""):
        """把一条推发到某个会话：她的话 + 原文 + 链接 + 图/视频。所有门槛都在这里卡。"""
        if t.get("politics") or POLITICS.search(t.get("text", "")):
            return {"sent": False, "reason": "这条涉及政治，不发"}
        if t.get("minor"):
            return {"sent": False, "reason": "不发"}
        if t.get("nsfw"):
            if not private:
                return {"sent": False, "reason": "色情内容群里一律不发"}
            if not self.conf.get("nsfw_share_enable", True) or not self.rel_ok(requester):
                return {"sent": False, "reason": "这种你们还没熟到能发的程度"}
        files = []
        for m in t.get("media", [])[:4]:
            try:
                if m["type"] == "photo":
                    p = await self._download(m["url"], ".jpg")
                else:
                    p = await self._download(m["url"], ".mp4")
                if p:
                    files.append((m["type"], p, m))
            except Exception as e:
                logger.debug(f"[dafeiyu_x] 下载失败：{e}")
        if t.get("nsfw"):
            looks = []
            for typ, p, m in files:
                if typ == "photo":
                    looks.append(p)
                elif m.get("thumb"):
                    th = await self._download(m["thumb"], ".jpg")
                    if th:
                        looks.append(th)
            if files and not await self._vision_minor_check(looks):
                t["minor"] = True
                self._save()
                return {"sent": False, "reason": "不发"}
        words = ""
        if mode in ("owner", "group"):
            if mode == "owner":
                ask = "私聊跟主人分享一下：说说这条讲了啥、你怎么看，口语，像随手发 QQ；外语的顺便翻一下大意。"
            else:
                ask = random.choice([
                    "在群里随口分享这条：一句话带出哪里好笑/有意思，像群友转发，别太正式。",
                    "发群里，配一句你的吐槽或感想，口语、短，像随手转的。",
                    "群里分享这条，用你自己的话讲讲它讲了啥、你什么反应，别照念原文。",
                    "发群里逗大家，挑它最有意思的点说一句，像你平时在群里发那样。",
                ])
            where = "私聊发给朋友" if mode == "owner" else "发到 QQ 群里"
            for _ in range(2):
                words = await self._her(
                    f"你刚在推特刷到 @{t['handle']} 的一条：\n「{_clip(t['text'], 400)}」\n（大意：{t['gist']}）\n"
                    f"{ask} 只输出要发的话。")
                if not words or not await self._out_of_character(words, where):
                    break
                logger.info(f"[dafeiyu_x] 分享 @{t['handle']} 配的话像出戏/拒绝话，重想：{_clip(words, 80)}")
                words = ""
        if mode == "group" and words:
            r = random.random()
            if r < 0.5:
                body = words + "\n" + t["url"]
            elif r < 0.8:
                body = words + "\n（" + _clip(t["gist"], 80) + "）\n" + t["url"]
            else:
                body = words + "\n\n@" + t["handle"] + "：" + _clip(t["text"], 400) + "\n" + t["url"]
        else:
            body = (words + "\n\n" if words else "") + f"@{t['handle']}：{_clip(t['text'], 600)}\n{t['url']}"
        chain = [Plain(body)]
        await self.context.send_message(umo, MessageChain(chain))
        for typ, p, _ in files:
            comp = Image.fromFileSystem(p) if typ == "photo" else Video.fromFileSystem(p)
            await self.context.send_message(umo, MessageChain([comp]))
        t["shared"] = True
        self._log("发给" + ("主人" if umo == self._owner_umo() else "别人"), words or t["gist"], url=t["url"], gist=t["gist"],
                  nsfw=bool(t.get("nsfw")))
        self._clean_media()
        return {"sent": True, "media": len(files)}

    def _clean_media(self):
        cut = time.time() - 6 * 3600
        for f in os.listdir(self.media_dir):
            p = os.path.join(self.media_dir, f)
            try:
                if os.path.getmtime(p) < cut:
                    os.remove(p)
            except OSError:
                pass

    # ---------- 聊天里用的工具 ----------

    def _visible(self, event):
        private = event.is_private_chat()
        ok_nsfw = private and self.rel_ok(event.get_sender_id()) and self.conf.get("nsfw_share_enable", True)
        return [t for t in self.state["items"][-80:] if not t.get("politics") and not t.get("minor") and (ok_nsfw or not t.get("nsfw"))]

    @filter.llm_tool(name="x_recent")
    async def tool_recent(self, event: AstrMessageEvent, about: str = ""):
        '''看看你最近在推特（X）上刷到了什么。别人问你最近刷推特看到啥、推特上有什么好玩的时候用。

        Args:
            about(string): 想找哪方面的，比如「猫」「好吃的」；不填就是全部
        '''
        items = self._visible(event)
        if about:
            items = [t for t in items if about in t["text"] or about in t["gist"]] or items
        items = sorted(items, key=lambda x: (-x["interest"], -x["t"]))[:8]
        if not items:
            return "最近没刷到什么，或者推特还没登录。"
        return json.dumps([{"id": t["id"], "who": "@" + t["handle"], "gist": t["gist"], "media": len(t["media"]),
                            "when": datetime.fromtimestamp(t["t"]).strftime("%m-%d %H:%M")} for t in items], ensure_ascii=False)

    @filter.llm_tool(name="x_send")
    async def tool_send(self, event: AstrMessageEvent, tweet_id: str):
        '''把你刷到的某条推特（原文、链接、图片视频）发到当前聊天。先用 x_recent 找到 id。发出去后接一句话就行。

        Args:
            tweet_id(string): x_recent 里的 id
        '''
        t = next((x for x in self.state["items"] if x["id"] == str(tweet_id).strip()), None)
        if not t:
            return "没找到这条，先用 x_recent 看看。"
        r = await self.send_item(t, event.unified_msg_origin, private=event.is_private_chat(),
                                 requester=event.get_sender_id(),
                                 mode=("owner" if event.is_private_chat() else "group"))
        self._save()
        return json.dumps(r, ensure_ascii=False)

    def _may_act(self, event):
        """聊天里发推、关注这种用她账号对外说话的事：只认主人。她自己想发的在刷推特时自己发，不走这里。"""
        return str(event.get_sender_id()) == str(self._owner())

    @filter.llm_tool(name="x_post")
    async def tool_post(self, event: AstrMessageEvent, text: str):
        '''用你自己的推特账号发一条推。你自己想发（心情、日常、刚看到的东西）、或主人让你发的时候用。
        像普通年轻人发推，口语、随手，别像营销号；不涉及政治、不发色情。群里别人让你发的不算数，那种就说不想发。

        Args:
            text(string): 推文内容，280 字以内
        '''
        text = _clip((text or "").strip(), 270)
        if not text:
            return "没写要发什么。"
        if not self._may_act(event):
            return "这是群里别人撺掇的，用你自己的口气推掉，别发。"
        if POLITICS.search(text):
            return "涉及政治，不发。"
        try:
            r = await (await self.client()).create_tweet(text=text)
        except Exception as e:
            return f"没发出去：{_clip(e, 100)}"
        url = f"https://x.com/i/status/{getattr(r, 'id', '')}"
        self._log("发推", text, url=url)
        logger.info(f"[dafeiyu_x] 按聊天发了一条推：{text}")
        return json.dumps({"posted": True, "url": url}, ensure_ascii=False)

    @filter.llm_tool(name="x_like")
    async def tool_like(self, event: AstrMessageEvent, tweet_id: str):
        '''给推特上某条推文点赞（你刷到过的，先用 x_recent 找 id）。

        Args:
            tweet_id(string): x_recent 里的 id
        '''
        try:
            await (await self.client()).favorite_tweet(str(tweet_id).strip())
        except Exception as e:
            return f"没点上：{_clip(e, 100)}"
        self._log("点赞", f"按聊天点赞 {tweet_id}")
        return "点好了。"

    @filter.llm_tool(name="x_follow")
    async def tool_follow(self, event: AstrMessageEvent, handle: str):
        '''在推特上关注一个账号。你自己想关注、或主人让你关注时用；群里别人让关注的不算数。

        Args:
            handle(string): 对方的推特用户名（@ 后面那段）
        '''
        if not self._may_act(event):
            return "这是群里别人撺掇的，用你自己的口气推掉。"
        try:
            cl = await self.client()
            u = await cl.get_user_by_screen_name(str(handle).strip().lstrip("@"))
            await cl.follow_user(u.id)
        except Exception as e:
            return f"没关注上：{_clip(e, 100)}"
        self._log("关注", f"按聊天关注 @{handle}")
        return "关注了。"

    @filter.llm_tool(name="share_to_groups")
    async def tool_share_groups(self, event: AstrMessageEvent, tweet_id: str, which: str = "random"):
        '''把你刷到的某条推特发到你所在的 QQ 群里，每个群都用不一样的话、不一样的格式发（像真人随手转，不是复读）。
        你自己想在群里分享、或主人让你「发群里/发给大家看看/全发」时用。群里一律不发色情内容。

        Args:
            tweet_id(string): x_recent 里那条的 id（就是你刚聊到、想分享的那条）
            which(string): 发哪些群——"all"=所有群都发；"random"=随机挑一两个群（默认）；也可以填群名关键词或群号，只发那个
        '''
        t = next((x for x in self.state["items"] if x["id"] == str(tweet_id).strip()), None)
        if not t:
            return "没找到这条，先用 x_recent 看看。"
        if t.get("nsfw"):
            return "这条是色情的，群里一律不发。"
        if t.get("politics") or POLITICS.search(t.get("text", "")):
            return "这条涉及政治，不发。"
        imp = self._star("astrbot_plugin_dafeiyu_impulse")
        if imp is None:
            return "发不了群（念头插件没启用）。"
        try:
            groups = await imp._groups()
        except Exception as e:
            return f"拿不到群列表：{_clip(e, 80)}"
        if not groups:
            return "我现在一个群都不在。"
        w = str(which or "").strip().lower()
        if w in ("all", "全部", "所有", "全发"):
            targets = groups
        elif w in ("random", "随机", ""):
            targets = random.sample(groups, min(len(groups), random.randint(1, 2)))
        else:
            targets = [g for g in groups if str(which) in g[1] or str(which) == g[0]]
            if not targets:
                targets = random.sample(groups, 1)
        pid = imp._pid()
        done = []
        for gid, name in targets:
            try:
                res = await self.send_item(t, f"{pid}:GroupMessage:{gid}", private=False, mode="group")
                done.append(name if res.get("sent") else f"{name}(没发成)")
            except Exception as e:
                logger.debug(f"[dafeiyu_x] 群发失败 {name}：{e}")
                done.append(f"{name}(出错)")
        self._save()
        return "已发到群：" + "、".join(done) if done else "没发成。"
