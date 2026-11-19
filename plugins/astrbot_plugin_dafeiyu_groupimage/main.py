"""大肥鱼看群里的图：没 @ 她的图先看一眼，换成一句话，再交给群聊插件。

群聊插件（group_chat_plus）只看 @ 她那条消息里的图。先发图、过一会儿再 @ 她问「图里写了啥」，那张图早被跳过了
（它本想从 AstrBot 自带的群聊上下文拿图片描述，AstrBot 4.29 那边换了实现，拿不到，10-28 测试群实测）。
这里排在群聊插件前面：白名单群里没 @ 她的图，用后台的「看图模型」看一眼，把图换成「[图片：……]」一行字，
群聊插件记下的上下文里就有这张图是什么。@ 她的图不动，照旧由群聊插件和 AstrBot 自己看。
每张图调一次看图模型（DeepSeek 不到半分钱）；同一张图一小时内不重复看。出错只记日志，图原样留着。
"""

import asyncio
import os
import time
import uuid
from collections import OrderedDict

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

PROMPT = (
    "这是 QQ 群里有人发的一张图。用一两句中文说清楚图里是什么；图里有字就把字原样写出来（太长就写前面的）；"
    "是表情包就说是什么表情包、什么情绪。只写描述，不要开场白。"
)
STILL_DIR = os.path.join(get_astrbot_data_path(), "temp", "dafeiyu_groupimage")


class DafeiyuGroupImage(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.cache = OrderedDict()  # 图片地址 → (描述, 时间)

    def _provider(self, event):
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        pid = (cfg.get("provider_settings") or {}).get("default_image_caption_provider_id") or ""
        return self.context.get_provider_by_id(pid) if pid else None

    async def _caption(self, prov, url):
        hit = self.cache.get(url)
        if hit and time.time() - hit[1] < 3600:
            return hit[0]
        resp = await asyncio.wait_for(
            prov.text_chat(prompt=PROMPT, session_id=uuid.uuid4().hex, image_urls=[url], persist=False), timeout=40
        )
        text = " ".join((resp.completion_text or "").split())[:300]
        if text:
            self.cache[url] = (text, time.time())
            while len(self.cache) > 300:
                self.cache.popitem(last=False)
        return text

    async def _refetch(self, event, img):
        """调 NapCat 的 get_image 拿这张图的本地缓存文件（优先）或新地址；拿不到返回空。"""
        try:
            fid = img.file or ""
            if not fid or fid.startswith(("http://", "https://")):
                return ""
            ret = await event.bot.api.call_action("get_image", file=fid)
            ret = (ret or {}).get("data", ret) if isinstance(ret, dict) else {}
            path = str(ret.get("file") or "")
            if path and os.path.isfile(path) and os.access(path, os.R_OK):
                return self._still(path)
            return str(ret.get("url") or "")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[群里的图] get_image 失败：{e}")
            return ""

    @staticmethod
    def _still(path):
        """群里的表情包大多是 GIF（扩展名常写成 .jpg/.png），看图模型（grok）不收 GIF、直接回 400。
        GIF 就取一帧（动图取中间那帧，更像它在表达的样子）存成 PNG 再看；不是 GIF 或转换失败就原样返回。"""
        try:
            with open(path, "rb") as f:
                if f.read(6) not in (b"GIF87a", b"GIF89a"):
                    return path
            from PIL import Image as PILImage

            os.makedirs(STILL_DIR, exist_ok=True)
            out = os.path.join(STILL_DIR, os.path.splitext(os.path.basename(path))[0] + ".png")
            if not os.path.isfile(out):
                with PILImage.open(path) as im:
                    n = getattr(im, "n_frames", 1) or 1
                    im.seek(n // 2)
                    im.convert("RGBA").save(out, "PNG")
            return out
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[群里的图] GIF 转 PNG 失败：{e}")
            return path

    async def _localize(self, event, img):
        """@她的图、引用里的图要原样交给她自己看：群图地址常 400，换成 NapCat 本地缓存文件。"""
        p = img.path or ""
        if p and os.path.isfile(p):
            s = self._still(p)
            if s != p:
                img.file = img.path = img.url = s
                return True
            return False
        path = await self._refetch(event, img)
        if path and os.path.isfile(path):
            img.file = img.path = img.url = path
            return True
        return False

    # 群聊插件处理群消息的 handler 是 priority=-1，这里要排在它前面
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=100)
    async def on_group(self, event: AstrMessageEvent):
        if not self.conf.get("enable", True):
            return
        chain = event.message_obj.message
        # 引用消息里的图（「这是不是真的？」引用一张图）：换成本地文件，她才能看到被引用的那张
        n = 0
        for c in chain:
            if isinstance(c, Reply) and c.chain:
                for rc in c.chain:
                    if isinstance(rc, Image) and await self._localize(event, rc):
                        n += 1
        imgs = [c for c in chain if isinstance(c, Image)]
        me = str(event.get_self_id())
        if any(isinstance(c, At) and str(c.qq) == me for c in chain):
            for img in imgs:
                if await self._localize(event, img):
                    n += 1
            if n:
                logger.info(f"[群里的图] @她/引用里的 {n} 张图换成本地文件给她看")
            return
        if n:
            logger.info(f"[群里的图] 引用里的 {n} 张图换成本地文件给她看")
        if not imgs:
            return
        prov = self._provider(event)
        if not prov:
            logger.info("[群里的图] 后台没设看图模型，跳过")
            return
        changed = False
        for img in imgs[: int(self.conf.get("max_images", 3) or 3)]:
            url = img.url or img.file
            if not url:
                continue
            t0 = time.time()
            try:
                text = await self._caption(prov, url)
            except Exception as e:  # noqa: BLE001
                # QQ 群图地址（gchat.qpic.cn）常回 400：找 NapCat 用 get_image 取本地文件/新地址再看一次
                alt = await self._refetch(event, img)
                if not alt or alt == url:
                    logger.warning(f"[群里的图] 没看成：{e}")
                    continue
                try:
                    text = await self._caption(prov, alt)
                    logger.info("[群里的图] 原地址看不了，换 get_image 取到的图看成了")
                except Exception as e2:  # noqa: BLE001
                    logger.warning(f"[群里的图] 没看成（get_image 重试也失败）：{e} / {e2}")
                    continue
            if text:
                chain[chain.index(img)] = Plain(f"[图片：{text}]")
                changed = True
                logger.info(f"[群里的图] 看了 {time.time() - t0:.1f} 秒：{text[:60]}")
        if changed:
            s = "".join(c.text for c in chain if isinstance(c, Plain)).strip()
            event.message_obj.message_str = s
            event.message_str = s
