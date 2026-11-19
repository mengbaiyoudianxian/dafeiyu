"""大肥鱼上网找表情包：先看后发。

她想发某种特定的图（「阴阳怪气的猫」「饿到发疯」），或者别人点名要某种表情包时，调 find_meme：
1. 去发表情（fabiaoqing.com）按关键词搜，拿前十来张；
2. 挑几张交给识图模型看：图上写了什么、画的是什么、跟她这会儿想表达的对不对得上、有没有色情/血腥/AI 梗/武器；
3. 只发识图说「对得上」的那一张；一张都对不上就不发，告诉她没找到，她就只说文字；
4. 发出去的那张收进「大肥鱼精选」包对应的分类，下次按分类配图时也能用上。
"""

import asyncio
import hashlib
import io
import json
import os
import random
import re
import time
import urllib.parse
import uuid

import aiohttp

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
DATA = get_astrbot_data_path()
PACK = os.path.join(DATA, "plugin_data", "meme_manager", "packs", "dafeiyu-picked")
CAPS = "/opt/dafeiyu/private/meme_captions.json"
CATS = {
    "happy": "开心、好耶", "smug": "得意、嘚瑟", "pout": "嘴硬、哼、不服气", "speechless": "无语、问号、懵",
    "shy": "害羞、脸红", "sad": "委屈、哭、可怜", "hungry": "饿了、馋", "eating": "在吃东西", "sleepy": "困、要睡了",
    "lazy": "摆烂、躺平", "greet": "打招呼、冒泡", "love": "贴贴、喜欢、谢谢、安慰", "shock": "震惊、吓到",
    "angry": "小生气（没武器）", "okay": "好的、收到", "no": "拒绝、不要", "tease": "坏笑、看戏、调侃", "cheer": "加油、庆祝",
}
JUDGE = (
    "你在帮一个 21 岁女大学生挑 QQ 聊天表情包。她这会儿想表达：「{want}」。{said}\n"
    "看这张图（动图的话左边第一帧、右边中间一帧），只输出一个 JSON：\n"
    '{{"text":"图上的字原样照抄，没有就空","scene":"画面一句话","fit":这张图发出去意思跟她想表达的对得上吗（true/false，'
    '图上的字要和场合搭，搭不上就是 false）,"bad":有没有色情、露骨、血腥、恶心、政治、广告水印、AI/代码/模型梗、刀枪等武器（true/false）,'
    '"cat":"从这些里选一个最贴的分类键：' + ", ".join(f"{k}({v})" for k, v in CATS.items()) + '"}}'
)


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


class DafeiyuMemeSearch(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.conf = config or {}
        self.dir = os.path.join(DATA, "plugin_data", "astrbot_plugin_dafeiyu_memesearch")
        self.tmp = os.path.join(self.dir, "tmp")
        os.makedirs(self.tmp, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        try:
            self.state = json.load(open(self.state_path, encoding="utf-8"))
        except Exception:
            self.state = {}
        self.state.setdefault("sent", [])  # 发过的图片网址，不重复发
        self.state.setdefault("day", {})

    def _save(self):
        json.dump(self.state, open(self.state_path, "w", encoding="utf-8"), ensure_ascii=False)

    def _count_today(self, bump=False):
        d = time.strftime("%Y-%m-%d")
        if self.state["day"].get("d") != d:
            self.state["day"] = {"d": d, "n": 0}
        if bump:
            self.state["day"]["n"] += 1
            self._save()
        return self.state["day"]["n"]

    async def _search(self, sess, keyword):
        out = []
        for page in (1, 2):
            url = f"https://fabiaoqing.com/search/bqb/keyword/{urllib.parse.quote(keyword)}/type/bq/page/{page}.html"
            async with sess.get(url, headers={"User-Agent": UA}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                html = await r.text(errors="replace")
            for tag in re.findall(r"<img[^>]+bqppsearch[^>]+>", html):
                src = re.search(r'data-original="([^"]+)"', tag)
                title = re.search(r'title="([^"]*)"', tag)
                if src and src.group(1) not in self.state["sent"]:
                    out.append({"url": src.group(1), "title": (title.group(1) if title else "").split(" - ")[0]})
            if len(out) >= 10:
                break
        return out

    async def _download(self, sess, url):
        async with sess.get(url, headers={"User-Agent": UA, "Referer": "https://fabiaoqing.com/"},
                            timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return None
            data = await r.read()
        if len(data) < 1500 or len(data) > 4_000_000:
            return None
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".jpg"
        if ext not in (".gif", ".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        path = os.path.join(self.tmp, hashlib.md5(data).hexdigest() + ext)
        with open(path, "wb") as f:
            f.write(data)
        return path

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

    async def _judge(self, path, want, said):
        prov = self.context.get_provider_by_id(str(self.conf.get("vision_provider_id") or "grok/grok-4.6"))
        if prov is None:
            raise RuntimeError("识图模型不可用")
        look = await asyncio.to_thread(self._frame, path)
        try:
            resp = await asyncio.wait_for(prov.text_chat(
                prompt=JUDGE.format(want=want, said=f"对方刚说的是：「{_clip(said, 80)}」" if said else ""),
                session_id=uuid.uuid4().hex, image_urls=[look], persist=False), timeout=45)
        finally:
            try:
                os.remove(look)
            except OSError:
                pass
        t = getattr(resp, "completion_text", "") or ""
        s, e = t.find("{"), t.rfind("}")
        return json.loads(t[s:e + 1]) if s >= 0 else {}

    def _keep(self, path, verdict):
        """发出去的好图收进精选包，以后按分类配图也能抽到。"""
        cat = verdict.get("cat") if verdict.get("cat") in CATS else ""
        if not cat or not os.path.isdir(PACK):
            return
        os.makedirs(os.path.join(PACK, "memes", cat), exist_ok=True)
        name = "web_" + os.path.basename(path)
        dst = os.path.join(PACK, "memes", cat, name)
        if not os.path.exists(dst):
            with open(path, "rb") as fi, open(dst, "wb") as fo:
                fo.write(fi.read())
        md = os.path.join(PACK, "memes_data.json")
        try:
            data = json.load(open(md, encoding="utf-8"))
            if cat not in data:
                data[cat] = f"用于{CATS[cat]}。"
                json.dump(data, open(md, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            caps = json.load(open(CAPS, encoding="utf-8")) if os.path.exists(CAPS) else {}
            caps[f"dafeiyu-picked/{cat}/{name}"] = {**verdict, "from": "web"}
            json.dump(caps, open(CAPS, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as e:
            logger.debug(f"[dafeiyu_memesearch] 记分类失败：{e}")

    @filter.llm_tool(name="find_meme")
    async def find_meme(self, event: AstrMessageEvent, want: str, keyword: str = ""):
        '''上网搜一张表情包，看过、确认对得上才发出去。适合：别人点名要某种表情包（「来张阴阳怪气的猫」），
        或者你很想发一张特定意思的图、平时的表情包里没有。普通聊天配图别用这个（慢），用平时的表情包就行；
        用了这个，这句回复里就别再加平时的表情包标记。返回结果会告诉你发没发、图上是什么；没发就只用文字接着聊，别说「我去找图」之类的话。

        Args:
            want(string): 你想用这张图表达什么，具体点，比如「嫌弃对方说的话，翻白眼」「饿得受不了」
            keyword(string): 搜索关键词，2 到 6 个字，比如「翻白眼」「饿了」「阴阳怪气 猫」；不填就用 want
        '''
        want = _clip(want.strip(), 60)
        keyword = (keyword or want).strip()[:20]
        if not want:
            return json.dumps({"sent": False, "reason": "没说想要什么样的图"}, ensure_ascii=False)
        if self._count_today() >= int(self.conf.get("daily_limit") or 40):
            return json.dumps({"sent": False, "reason": "今天找图找太多次了，用平时的表情包或者只说文字"}, ensure_ascii=False)
        self._count_today(bump=True)
        said = (event.message_str or "").strip()
        started = time.time()
        try:
            async with aiohttp.ClientSession() as sess:
                cands = await self._search(sess, keyword)
                if not cands:
                    return json.dumps({"sent": False, "reason": f"搜「{keyword}」没搜到图"}, ensure_ascii=False)
                head, tail = cands[:6], cands[6:]
                random.shuffle(tail)
                picks = (head + tail)[:8]
                for batch in (picks[:4], picks[4:8]):
                    if not batch or time.time() - started > 60:
                        break
                    paths = await asyncio.gather(*[self._download(sess, c["url"]) for c in batch], return_exceptions=True)
                    jobs = [(c, p) for c, p in zip(batch, paths) if isinstance(p, str)]
                    verdicts = await asyncio.gather(*[self._judge(p, want, said) for _, p in jobs], return_exceptions=True)
                    for (c, p), v in zip(jobs, verdicts):
                        if isinstance(v, dict) and v.get("fit") is True and v.get("bad") is not True:
                            await event.send(MessageChain([Image.fromFileSystem(p)]))
                            self.state["sent"] = (self.state["sent"] + [c["url"]])[-500:]
                            self._save()
                            try:
                                self._keep(p, v)
                            except Exception as e:
                                logger.debug(f"[dafeiyu_memesearch] 收图失败：{e}")
                            logger.info(f"[dafeiyu_memesearch] 「{keyword}」找到并发了：{_clip(v.get('text') or v.get('scene'), 40)}"
                                        f"（{int(time.time() - started)} 秒）")
                            return json.dumps({"sent": True, "image_text": v.get("text", ""), "image_scene": v.get("scene", ""),
                                               "note": "图已经发出去了，接着说你想说的话就行，不用再描述这张图"}, ensure_ascii=False)
                logger.info(f"[dafeiyu_memesearch] 「{keyword}」看了几张都对不上，没发（{int(time.time() - started)} 秒）")
                return json.dumps({"sent": False, "reason": "看了几张都跟你想表达的对不上，没发；只用文字说就行"}, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[dafeiyu_memesearch] 找图出错：{e}")
            return json.dumps({"sent": False, "reason": "找图出错了，只用文字说就行"}, ensure_ascii=False)
        finally:
            self._clean_tmp()

    def _clean_tmp(self):
        cut = time.time() - 3600
        for f in os.listdir(self.tmp):
            p = os.path.join(self.tmp, f)
            try:
                if os.path.getmtime(p) < cut:
                    os.remove(p)
            except OSError:
                pass
