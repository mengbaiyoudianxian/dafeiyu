"""大肥鱼放歌：别人让她唱歌/放首歌时，她自己去找歌，整首作为一条 QQ 语音发出来。

找歌、登录、下载都借「更好的点歌」（astrbot_plugin_better_music）插件：它管酷狗/网易云/QQ 音乐的扫码登录（主人的酷狗 SVIP）
和下载缓存；这里只是给她一个工具，不用别人敲指令，而且不切成 60 秒一段，整首一条语音发。
"""

import asyncio
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import uuid

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain, Record
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

NAMES = {"kugou": "酷狗", "netease": "网易云", "qq": "QQ 音乐"}
PICK_N = 5      # 判断「搜得准不准」时看前几首
PAGE_SIZE = 10  # 候选卡片一页几首
PICK_TTL = 600  # 卡片发出后多少秒内回编号/再来点有效
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"


class DafeiyuSing(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.conf = config or {}
        base = os.path.join(get_astrbot_data_path(), "plugin_data", "astrbot_plugin_dafeiyu_sing")
        self.tmp = os.path.join(base, "tmp")
        os.makedirs(self.tmp, exist_ok=True)
        self.log_path = os.path.join(base, "plays.json")
        self._day = {"d": "", "n": 0}
        self._recent = {}  # umo -> (歌名, 时间)，防同一首连放
        self._pending = {}  # umo -> 等对方回数字的候选列表

    @filter.on_astrbot_loaded()
    async def hide_cover_tools(self):
        """「更好的点歌」自带的两个工具要外部翻唱服务，我们没有；关掉，免得她调错。"""
        mgr = self.context.get_llm_tool_manager()
        for name in ("better_music_search", "better_music_sing"):
            try:
                if mgr.deactivate_llm_tool(name):
                    logger.info(f"[dafeiyu_sing] 关掉了 {name}，放歌统一用 play_song")
            except Exception as e:
                logger.debug(f"[dafeiyu_sing] 关 {name} 失败：{e}")

    def _music(self):
        meta = self.context.get_registered_star("astrbot_plugin_better_music")
        inst = getattr(meta, "star_cls", None) if meta else None
        if inst is None or not getattr(meta, "activated", True):
            raise RuntimeError("「更好的点歌」插件没装或没启用")
        return inst

    async def _platform(self, bm):
        order = [p.strip() for p in str(self.conf.get("platform_order") or "kugou,netease,qq").split(",") if p.strip()]
        states = []
        for p in order:
            try:
                st = await bm.providers.chat_account_status(p)
            except Exception as e:
                st = {"status": "unknown", "message": str(e)}
            states.append(f"{NAMES.get(p, p)}：{st.get('status')}")
            if st.get("status") == "valid":
                return p, states
        return None, states

    async def _to_mp3(self, src, max_seconds):
        ff = shutil.which("ffmpeg") or "ffmpeg"
        out = os.path.join(self.tmp, f"{uuid.uuid4().hex}.mp3")
        args = [ff, "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "44100", "-b:a", "96k"]
        if max_seconds > 0:
            args += ["-t", str(int(max_seconds))]
        args.append(out)
        p = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(p.communicate(), timeout=180)
        if p.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"转码失败：{(err or b'').decode(errors='replace')[:120]}")
        return out

    async def _duration(self, path):
        fp = shutil.which("ffprobe") or "ffprobe"
        p = await asyncio.create_subprocess_exec(fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                                 str(path), stdout=asyncio.subprocess.PIPE)
        out, _ = await p.communicate()
        try:
            return float(out.decode().strip())
        except Exception:
            return 0.0

    def _clean(self):
        cut = time.time() - 3600
        for f in os.listdir(self.tmp):
            try:
                if os.path.getmtime(os.path.join(self.tmp, f)) < cut:
                    os.remove(os.path.join(self.tmp, f))
            except OSError:
                pass

    @filter.llm_tool(name="play_song")
    async def play_song(self, event: AstrMessageEvent, song: str = "", pick: int = 0):
        '''别人让你唱歌、放首歌、点歌时用：你去音乐平台找这首歌，把原唱整首当成一条语音发出来（不是你自己唱的，是放给他们听）。
        要找一两分钟，发出去之前别说「我去找」，直接调这个；它返回后再接一句自然的话，比如这歌你觉得怎么样。
        没说具体哪首（「随便唱一首」）就挑一首你喜欢的、大家熟的歌。
        给了 song：搜得准就直接放；拿不准是哪个版本/哪个歌手，会自动发一张带封面的候选卡片让对方挑。
        已经发过候选卡片（search_songs 或上一次 play_song），对方说了要哪首（「3」「第二个」「那个女版」「刘若英那个」），
        或者让你自己挑，就对照卡片的编号，把编号填进 pick（song 可以不填）。

        Args:
            song(string): 歌名；对方说了歌手、或你真的很确定是谁唱的，才带上歌手（如「晴天 周杰伦」），拿不准就只写歌名，别凭印象猜歌手，猜错会搜出一堆别人的歌；用 pick 选卡片里的歌时可以留空
            pick(number): 候选卡片上的编号（从 1 开始），没有就填 0
        '''
        umo, where = event.unified_msg_origin, "私聊" if event.is_private_chat() else "群聊"
        who = event.get_sender_name() or ""
        song = (song or "").strip()[:60]
        try:
            pick = int(pick or 0)
        except (TypeError, ValueError):
            pick = 0
        if pick > 0:
            p = self._pending.get(umo)
            if not p or pick > len(p["items"]):
                return json.dumps({"sent": False, "reason": "没有这个编号的候选（卡片过期了或编号不对），重新 search_songs 搜一下"},
                                  ensure_ascii=False)
            return await self._play_item(umo, p, pick, who, where, tool=True)
        if not song:
            return "没说是哪首歌。"
        try:
            items, total = await self._kg_search(song, 1)
        except Exception as e:
            logger.info(f"[dafeiyu_sing] 酷狗卡片搜索不可用，退回老办法：{e}")
            r = await self.play(umo, song, who=who, where=where)
            r["note"] = ("歌已经作为语音发出去了，接着说句话就行，别再描述你发了什么" if r.get("sent") else
                         f"没放成，酷狗搜索也不可用（{str(e)[:60]}）。用人话说清楚卡在哪，看不明白就调 check_myself 查日志。")
            return json.dumps(r, ensure_ascii=False)
        if not items:
            return json.dumps({"sent": False, "reason": f"酷狗上没搜到「{song}」"}, ensure_ascii=False)
        if self._confident(song, items):
            p = {"keyword": song, "page": 1, "total": total, "items": items, "t": time.time(), "uid": str(event.get_sender_id())}
            return await self._play_item(umo, p, 1, who, where, tool=True)
        return await self._send_card(event, song, 1, items, total, "这首有好几个版本，拿不准是哪个")

    @filter.llm_tool(name="search_songs")
    async def search_songs(self, event: AstrMessageEvent, keyword: str = "", more: bool = False):
        '''在酷狗搜歌，把结果做成一张带封面、歌名（含版本）、歌手、专辑、时长的候选卡片发出去，一次 10 首，编号接着上一页往下排。
        用在：对方想看看有哪些版本/让你搜一下某首歌；对方说「再来点」「换一批」「都不是」「下一页」「加载更多」时把 more 设成 true 接着往下翻。
        卡片发出去以后，对方回编号或者描述要哪首，就用 play_song 的 pick 放；让你自己挑就从卡片里挑一首用 pick 放。

        Args:
            keyword(string): 搜索词，比如「黄昏」「黄昏 周传雄」；翻页时可以留空，沿用上次的
            more(boolean): true = 接着上一次的结果加载下一页
        '''
        umo = event.unified_msg_origin
        p = self._pending.get(umo)
        keyword = (keyword or "").strip()[:60]
        if more or not keyword:
            if not p:
                if not keyword:
                    return "没有可以接着翻的搜索结果，告诉我要搜什么。"
            else:
                return await self._more(event, p)
        try:
            items, total = await self._kg_search(keyword, 1)
        except Exception as e:
            return json.dumps({"ok": False, "reason": f"搜索失败：{str(e)[:80]}"}, ensure_ascii=False)
        if not items:
            return json.dumps({"ok": False, "reason": f"酷狗上没搜到「{keyword}」"}, ensure_ascii=False)
        return await self._send_card(event, keyword, 1, items, total)

    # ---------- 选歌：搜索、卡片、翻页 ----------

    @staticmethod
    def _norm(s):
        return re.sub(r"[\s\W_]+", "", str(s or "")).lower()

    @staticmethod
    def _title(c):
        t, a = str(c.get("title") or ""), str(c.get("artist") or "")
        if a and t.startswith(a + " - "):
            t = t[len(a) + 3:]
        return t

    @staticmethod
    def _dur(d):
        try:
            d = float(d)
            if d > 1000:  # 毫秒
                d /= 1000
            return f"（{int(d) // 60}:{int(d) % 60:02d}）" if d > 0 else ""
        except (TypeError, ValueError):
            return ""

    def _confident(self, song, cands):
        """歌名对得上、歌手（说了的话）也对得上，并且前几首里没有同名的别的歌手，就算准，直接放。"""
        strip = lambda s: self._norm(re.sub(r"[（(\[【].*?[）)\]】]", "", self._title(s)))
        q, top = self._norm(song), cands[0]
        t = strip(top)
        if not t or t not in q:
            return False
        arts = lambda c: [self._norm(a) for a in re.split(r"[、,/&，]| feat\.? ", str(c.get("artist") or "")) if self._norm(a)]
        if any(a in q for c in cands[:PICK_N] for a in arts(c)):
            return any(a in q for a in arts(top))
        same = {self._norm(c.get("artist")) for c in cands[1:PICK_N] if strip(c) == t}
        return not (same - {self._norm(top.get("artist"))})

    async def _kg_search(self, keyword, page):
        """直接拿酷狗搜索的原始结果：完整歌名、歌手、专辑、封面、时长、日期。"""
        bm = self._music()
        st = await bm.providers.chat_account_status("kugou")
        if st.get("status") != "valid":
            raise RuntimeError("酷狗没登录")
        res, _ = await bm.providers._kugou_request(
            "/search", {"keywords": keyword, "type": "song", "page": page, "pagesize": PAGE_SIZE}, authenticated=True)
        data = (res or {}).get("data") or {}
        items = []
        for s in data.get("lists") or []:
            h = str(s.get("FileHash") or "").upper()
            if not h:
                continue
            link = {"hash": h}
            if s.get("AlbumID"):
                link["album_id"] = str(s["AlbumID"])
            if s.get("MixSongID"):
                link["album_audio_id"] = str(s["MixSongID"])
            items.append({
                "title": str(s.get("SongName") or s.get("OriSongName") or "未知歌曲"),
                "artist": str(s.get("SingerName") or "未知歌手"),
                "album": str(s.get("AlbumName") or ""),
                "duration": int(float(s.get("Duration") or 0)),
                "date": str(s.get("PublishDate") or "")[:10],
                "cover": str(s.get("Image") or (s.get("trans_param") or {}).get("union_cover") or "").replace("{size}", "150"),
                "vip": str(s.get("PayType")) == "3" or str(s.get("Privilege")) in ("10",),
                "query": "https://www.kugou.com/song/?" + urllib.parse.urlencode(link),
            })
        return items, int(data.get("total") or len(items))

    async def _send_card(self, event, keyword, page, items, total, why=""):
        umo = event.unified_msg_origin
        old = self._pending.get(umo)
        start = 0
        if page > 1 and old and old.get("keyword") == keyword:
            start = len(old["items"])
            all_items = old["items"] + items
        else:
            all_items = items
        self._pending[umo] = {"keyword": keyword, "page": page, "total": total, "items": all_items,
                              "t": time.time(), "uid": str(event.get_sender_id())}
        try:
            img = await asyncio.to_thread(self._render_card, keyword, page, total, items, start)
            chain = [Image.fromFileSystem(img),
                     Plain("回编号就放；想看更多就说「再来点」。")]
        except Exception as e:
            logger.warning(f"[dafeiyu_sing] 卡片图没画成，改发文字：{e}")
            lines = [f"「{keyword}」第 {page} 页（共 {total} 首）："]
            for i, it in enumerate(items, start + 1):
                lines.append(f"{i}. {it['title']} - {it['artist']}《{it['album']}》{self._dur(it['duration'])}")
            lines.append("回编号就放；想看更多就说「再来点」。")
            chain = [Plain("\n".join(lines))]
        await self.context.send_message(umo, MessageChain(chain))
        listing = [f"{i}. {it['title']} / {it['artist']} / 《{it['album']}》 / {self._dur(it['duration']).strip('（）')}"
                   for i, it in enumerate(items, start + 1)]
        return json.dumps({
            "card_sent": True, "keyword": keyword, "page": page, "total": total, "why": why,
            "list": listing,
            "note": "带封面的候选卡片已经发出去了（上面是卡片内容，编号一致）。别把列表再念一遍，最多简短说一句。"
                    "对方说要哪首就 play_song(pick=编号)；说再来点/换一批就 search_songs(more=true)；让你挑就自己选个编号 play_song(pick=编号)。",
        }, ensure_ascii=False)

    async def _more(self, event, p):
        page = int(p.get("page") or 1) + 1
        if (page - 1) * PAGE_SIZE >= int(p.get("total") or 0):
            return json.dumps({"ok": False, "reason": "已经没有更多结果了，换个搜索词试试"}, ensure_ascii=False)
        try:
            items, total = await self._kg_search(p["keyword"], page)
        except Exception as e:
            return json.dumps({"ok": False, "reason": f"加载失败：{str(e)[:80]}"}, ensure_ascii=False)
        if not items:
            return json.dumps({"ok": False, "reason": "已经没有更多结果了"}, ensure_ascii=False)
        return await self._send_card(event, p["keyword"], page, items, total)

    async def _play_item(self, umo, p, n, who, where, tool=False):
        """放卡片里第 n 首；放不成就自己想办法：网络问题重试一次，再不行换同一个歌手唱的同一首别的版本。"""
        it = p["items"][n - 1]
        tried = []

        async def attempt(x):
            pick = {"query": x["query"], "title": x["title"], "artist": x["artist"]}
            r = await self.play(umo, f"{x['title']} {x['artist']}", pick=pick, platform="kugou", who=who, where=where)
            tried.append({"song": f"{x['title']} - {x['artist']}", "ok": bool(r.get("sent")), "cause": r.get("cause", "")})
            return r

        r = await attempt(it)
        if not r.get("sent") and r.get("retryable"):
            await asyncio.sleep(2)
            r = await attempt(it)
        fallback = None
        if not r.get("sent"):
            base = self._norm(re.sub(r"[（(\[【].*?[）)\]】]", "", it["title"]))
            alts = [x for x in p["items"] if x is not it and self._norm(x["artist"]) == self._norm(it["artist"])
                    and self._norm(re.sub(r"[（(\[【].*?[）)\]】]", "", x["title"])) == base]
            if not alts:
                try:
                    more, _ = await self._kg_search(f"{base} {it['artist']}", 1)
                    alts = [x for x in more if x["query"] != it["query"] and self._norm(x["artist"]) == self._norm(it["artist"])
                            and self._norm(re.sub(r"[（(\[【].*?[）)\]】]", "", x["title"])) == base]
                except Exception:
                    alts = []
            for x in alts[:3]:
                r2 = await attempt(x)
                if r2.get("sent"):
                    r, fallback = r2, x
                    break
        if len(tried) > 1:
            r["tried"] = tried
        if fallback:
            r["fallback"] = f"原来那版放不了（{tried[0]['cause']}），换成了同一个歌手的「{fallback['title']}」"
        if tool:
            if r.get("sent"):
                r["note"] = "歌已经作为语音发出去了，接着说句话就行，别再描述你发了什么" + (
                    "；顺口告诉对方原版放不了、换了个版本" if fallback else "")
            else:
                r["note"] = ("没放成。用人话跟对方说清楚卡在哪（cause）、你试了什么（tried），别只说「放不出来」；"
                             "原因看不明白就先调 check_myself 查日志；能换别的歌手版本就问对方要不要换。")
            return json.dumps(r, ensure_ascii=False)
        return r

    def _render_card(self, keyword, page, total, items, start):
        from PIL import Image as PImage, ImageDraw, ImageFont
        W, ROW, PAD, CV = 760, 112, 18, 88
        H = 74 + ROW * len(items) + 16
        bg, fg, sub, line, acc = (24, 26, 33), (240, 242, 247), (150, 156, 170), (44, 47, 58), (64, 158, 255)
        im = PImage.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(im)
        font = lambda size, bold=False: ImageFont.truetype(FONT_BOLD if bold and os.path.exists(FONT_BOLD) else FONT, size, index=2)  # 2 = 简体中文
        f_head, f_title, f_sub, f_num, f_tag = font(26, True), font(25, True), font(19), font(28, True), font(15, True)
        d.text((PAD, 20), f"「{keyword[:20]}」", font=f_head, fill=fg)
        hw = d.textlength(f"「{keyword[:20]}」", font=f_head)
        d.text((PAD + hw + 10, 27), f"第 {page} 页 · 共 {total} 首", font=f_sub, fill=sub)
        covers = self._fetch_covers([it.get("cover") for it in items])
        fit = lambda s, f, w: s if d.textlength(s, font=f) <= w else next(
            (s[:k] + "…" for k in range(len(s), 0, -1) if d.textlength(s[:k] + "…", font=f) <= w), "…")
        for i, it in enumerate(items):
            y = 70 + i * ROW
            if i:
                d.line((PAD, y, W - PAD, y), fill=line, width=1)
            num = str(start + i + 1)
            d.text((PAD + 22 - d.textlength(num, font=f_num) / 2, y + ROW / 2 - 18), num, font=f_num, fill=acc)
            cx, cy = PAD + 54, y + (ROW - CV) // 2
            cov = covers[i]
            if cov is not None:
                mask = PImage.new("L", (CV, CV), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, CV, CV), 12, fill=255)
                im.paste(cov.resize((CV, CV)), (cx, cy), mask)
            else:
                d.rounded_rectangle((cx, cy, cx + CV, cy + CV), 12, fill=line)
                d.text((cx + CV / 2 - 12, cy + CV / 2 - 14), "♪", font=f_num, fill=sub)
            tx, tw = cx + CV + 16, W - (cx + CV + 16) - PAD - 70
            d.text((tx, y + 18), fit(it["title"], f_title, tw), font=f_title, fill=fg)
            d.text((tx, y + 52), fit(f"{it['artist']}  ·  《{it['album']}》" if it["album"] else it["artist"], f_sub, tw), font=f_sub, fill=sub)
            meta = " · ".join(x for x in (self._dur(it["duration"]).strip("（）"), it.get("date") or "") if x)
            d.text((tx, y + 78), meta, font=f_sub, fill=(115, 120, 135))
            if it.get("vip"):
                d.rounded_rectangle((W - PAD - 52, y + 20, W - PAD, y + 44), 6, outline=(232, 180, 70), width=2)
                d.text((W - PAD - 43, y + 22), "VIP", font=f_tag, fill=(232, 180, 70))
        out = os.path.join(self.tmp, f"card_{uuid.uuid4().hex}.png")
        im.save(out)
        return out

    @staticmethod
    def _fetch_covers(urls):
        from concurrent.futures import ThreadPoolExecutor
        from io import BytesIO
        from PIL import Image as PImage
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def one(u):
            if not u or not u.startswith("http"):
                return None
            try:
                raw = op.open(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=6).read()
                return PImage.open(BytesIO(raw)).convert("RGB")
            except Exception:
                return None
        with ThreadPoolExecutor(8) as ex:
            return list(ex.map(one, urls))

    async def search(self, song, platform=""):
        """控制面板用：在第一个已登录的平台（或指定平台）搜歌，返回候选。"""
        bm = self._music()
        if not platform:
            platform, states = await self._platform(bm)
            if not platform:
                raise RuntimeError("音乐账号都没登录：" + "；".join(states))
        results = await bm.providers.search(platform, song, limit=8, page=1)
        cands = [c for it in (results or []) if isinstance(it, dict) and (c := bm._public_candidate(it))]
        return platform, cands

    async def play(self, umo, song, pick=None, platform="", who="", where=""):
        """找歌、下载、整首转一条语音发到 umo。工具和控制面板共用。pick 是面板里选好的候选。"""
        d = time.strftime("%Y-%m-%d")
        if self._day["d"] != d:
            self._day = {"d": d, "n": 0}
        limit = int(self.conf.get("daily_limit") or 0)  # 0 = 不限
        if limit > 0 and self._day["n"] >= limit:
            return {"sent": False, "reason": "今天放歌放太多次了，跟人说明天再放"}
        last = self._recent.get(umo)
        if not pick and last and last[0] == song and time.time() - last[1] < 180:
            return {"sent": False, "reason": "这首刚放过，不用再放"}
        started = time.time()
        try:
            bm = self._music()
            if not pick:
                platform, cands = await self.search(song)
                if not cands:
                    return {"sent": False, "reason": f"{NAMES.get(platform, platform)}上没搜到「{song}」"}
                pick = cands[0]
            rec = await bm.runtime.download_selected(platform, str(pick["query"]))
            orig = next((f for f in rec.get("files") or [] if f.get("kind") == "original"), None)
            if not orig:
                return {"sent": False, "reason": "下载下来没找到音频"}
            src = bm.runtime.storage.song_dir(str(rec["id"])) / orig["name"]
            mp3 = await self._to_mp3(src, int(self.conf.get("max_seconds") or 0))
            secs = await self._duration(mp3)
            await self.context.send_message(umo, MessageChain([Record(file=mp3, url=mp3)]))
            self._day["n"] += 1
            self._recent[umo] = (song, time.time())
            artist = rec.get("artist") or pick.get("artist")
            title = self._title({"title": rec.get("title") or pick.get("title"), "artist": artist})
            self._log({"t": time.time(), "title": title, "artist": artist, "platform": platform, "seconds": int(secs),
                       "who": who, "where": where, "ok": True})
            logger.info(f"[dafeiyu_sing] 放了《{title}》{artist}（{NAMES.get(platform, platform)}，{int(secs)} 秒，用时 {int(time.time() - started)} 秒）")
            return {"sent": True, "title": title, "artist": artist, "seconds": int(secs), "maybe_preview": bool(secs and secs < 65)}
        except Exception as e:
            logger.warning(f"[dafeiyu_sing] 放《{song}》失败：{e}")
            diag = self._diagnose(str(e))
            self._log({"t": time.time(), "title": song, "artist": "", "platform": platform, "who": who, "where": where,
                       "ok": False, "error": str(e)[:120], "cause": diag["cause"]})
            return {"sent": False, "reason": f"没放成：{str(e)[:80]}", **diag, "detail": str(e)[:200]}
        finally:
            self._clean()

    @staticmethod
    def _diagnose(err):
        """把报错翻成人话：原因、重试有没有用、该怎么办。"""
        e = err.lower()
        table = [
            (("20028", "需要验证", "check r failed"), "酷狗风控拦了取歌地址（要求验证）", False,
             "换个版本可能能放；一直这样就是取歌通道被封了，得告诉主人"),
            (("完整mp3", "完整MP3".lower(), "试听", "加密音源"), "酷狗没给这首的完整音频（可能版权/会员限制或被拦）", False,
             "换同一首的别的版本试试"),
            (("下载不完整", "incompleteread", "timed out", "timeout", "urlopen error", "connection reset",
              "remote end closed", "temporarily", "502", "503", "504"), "下载时网络断了或超时", True, "再试一次一般就好"),
            (("没登录", "请先登录", "登录已过期", "expired", "invalid"), "音乐账号没登录或登录过期", False,
             "得让主人重新扫码登录（点歌插件面板）"),
            (("转码失败", "ffmpeg"), "音频转成语音时出错", True, "换个版本或者再试一次"),
            (("没装或没启用",), "点歌插件没装或被关了", False, "得让主人去后台把「更好的点歌」打开"),
            (("250 mib", "超过"), "音频文件太大", False, "换个短一点的版本"),
        ]
        for keys, cause, retry, advice in table:
            if any(k.lower() in e for k in keys):
                return {"cause": cause, "retryable": retry, "advice": advice}
        return {"cause": "没见过的错误", "retryable": True, "advice": "可以先重试；还不行就用 check_myself 看看日志里具体报了什么"}

    def _log(self, item):
        try:
            items = json.load(open(self.log_path, encoding="utf-8")) if os.path.exists(self.log_path) else []
        except Exception:
            items = []
        items = (items + [item])[-100:]
        json.dump(items, open(self.log_path, "w", encoding="utf-8"), ensure_ascii=False)

    def recent(self, n=20):
        try:
            return json.load(open(self.log_path, encoding="utf-8"))[-n:][::-1]
        except Exception:
            return []
