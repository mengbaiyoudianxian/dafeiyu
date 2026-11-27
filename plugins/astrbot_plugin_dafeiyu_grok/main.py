"""Image and video tools for an operator-authorized xAI API account."""
import asyncio
import json
import os
import time

import aiohttp

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Video
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class DafeiyuGrok(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self._quota = {}

    def _count(self, kind):
        today = time.strftime("%Y-%m-%d")
        if self._quota.get("date") != today:
            self._quota = {"date": today}
        return self._quota.get(kind, 0)

    async def _xai(self, method, path, body=None, timeout=120):
        key = os.environ.get("XAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Set XAI_API_KEY in the service environment.")
        async with aiohttp.ClientSession() as s:
            async with s.request(method, "https://api.x.ai/v1" + path, json=body, timeout=aiohttp.ClientTimeout(total=timeout),
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    msg = data.get("error") if isinstance(data, dict) else data
                    raise RuntimeError(f"Grok 接口 HTTP {r.status}：{str(msg)[:160]}")
                return r.status, data

    def _with_self(self, prompt, draw_self):
        if not draw_self:
            return prompt
        looks = str(self.conf.get("self_looks") or "").strip()
        return f"{prompt}。画中的角色是她本人，外貌必须是：{looks}" if looks else prompt

    @filter.llm_tool(name="grok_draw")
    async def tool_draw(self, event: AstrMessageEvent, prompt: str, draw_self: bool = False):
        '''画一张图并直接发出来。别人让你画画、P图、生成图片，或者你自己想用图表达时用。
        prompt 写清楚画面内容和风格（中文就行），越具体越好。
        画的是你自己（自拍、你在干嘛、你的样子）时，draw_self 填 true，会自动按你的长相画。

        Args:
            prompt(string): 要画什么：主体、动作、场景、风格
            draw_self(boolean): 画面里有没有你自己
        '''
        if self._count("image") >= int(self.conf.get("image_daily_limit") or 30):
            return "今天画图次数用完了，明天再画。"
        prompt = self._with_self(prompt, draw_self)
        last_err = None
        b64 = None
        try:  # 2026-11-19 先用 ChatGPT 订阅画（grok 欠费 402），不行再退回 grok
            async with aiohttp.ClientSession() as s:
                async with s.post(os.environ.get("DAFEIYU_BRIDGE_RESPONSES_URL", "http://127.0.0.1:18790/v1/codex/raw"), timeout=aiohttp.ClientTimeout(total=3600), json={
                        "model": "gpt-6.2-sol", "effort": "low", "tools": [{"type": "image_generation"}],
                        "instructions": "用户要一张图。直接调用画图工具画出来，不要反问，不要只用文字描述。",
                        "input": "画这张图：" + prompt}) as r:
                    d = await r.json(content_type=None)
                    if r.status >= 400:
                        raise RuntimeError(str(d.get("error") if isinstance(d, dict) else d)[:160])
            for o in d.get("output") or []:
                if o.get("type") == "image_generation_call" and o.get("result"):
                    b64 = o["result"]
            if not b64:
                raise RuntimeError("GPT 没返回图片")
        except Exception as e:
            last_err = e
            logger.warning(f"[dafeiyu_grok] GPT 画图失败，退回 grok：{str(e)[:120]}")
        for model in (() if b64 else (str(self.conf.get("image_model") or "grok-imagine-image-2.0"), "grok-imagine-image")):
            try:
                _, d = await self._xai("POST", "/images/generations", {"model": model, "prompt": prompt, "n": 1, "response_format": "b64_json"})
                b64 = ((d.get("data") or [{}])[0]).get("b64_json")
                if not b64:
                    raise RuntimeError("没返回图片")
                break
            except Exception as e:
                last_err = e
                b64 = None
        if not b64:
            return f"画图失败：{last_err}"
        self._quota["image"] = self._count("image") + 1
        mc = MessageChain()
        mc.chain = [Image.fromBase64(b64)]
        await event.send(mc)
        return "图已经发出去了。简单说一两句就行，别描述图里每个细节。"

    @filter.llm_tool(name="grok_video")
    async def tool_video(self, event: AstrMessageEvent, prompt: str, draw_self: bool = False):
        '''用 Grok 生成一段几秒钟的小视频，做好后自动发出来（要等一两分钟）。别人让你做视频、动起来、拍个小片时用。
        调用后先回一句「在做了」之类的，不用等。视频里是你自己时 draw_self 填 true。

        Args:
            prompt(string): 视频内容：主体、动作、镜头、风格
            draw_self(boolean): 视频里有没有你自己
        '''
        if self._count("video") >= int(self.conf.get("video_daily_limit") or 10):
            return "今天做视频次数用完了，明天再做。"
        prompt = self._with_self(prompt, draw_self)
        model = str(self.conf.get("video_model") or "grok-imagine-video-1.5-lite")
        _, d = await self._xai("POST", "/videos/generations", {"model": model, "prompt": prompt,
                                                               "duration": int(self.conf.get("video_seconds") or 5)})
        rid = d.get("request_id")
        if not rid:
            return f"视频没开始做：{str(d)[:120]}"
        self._quota["video"] = self._count("video") + 1
        umo = event.unified_msg_origin

        async def wait_and_send():
            deadline = time.time() + 600
            while time.time() < deadline:
                await asyncio.sleep(8)
                try:
                    _, r = await self._xai("GET", f"/videos/{rid}", timeout=30)
                except Exception as e:
                    logger.warning(f"[dafeiyu_grok] 查视频进度失败：{e}")
                    continue
                st = str(r.get("status") or "")
                url = (r.get("video") or {}).get("url")
                if url:
                    path = os.path.join(get_astrbot_data_path(), "temp", f"grok_video_{rid[:8]}.mp4")
                    async with aiohttp.ClientSession() as s:
                        async with s.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                            with open(path, "wb") as f:
                                f.write(await resp.read())
                    mc = MessageChain()
                    mc.chain = [Video.fromFileSystem(path)]
                    await self.context.send_message(umo, mc)
                    return
                if st in ("failed", "expired", "error"):
                    await self.context.send_message(umo, MessageChain().message(f"视频没做出来（{st}）"))
                    return
            await self.context.send_message(umo, MessageChain().message("视频做太久了，先算了"))

        asyncio.create_task(wait_and_send())
        return "视频开始做了，大概一两分钟后会自动发出来。先跟对方说一声在做了。"

    @filter.on_llm_request()
    async def tell_skills(self, event: AstrMessageEvent, req):
        """提醒她：你会画画、会做小视频，别人要就直接动手，别说自己不会。"""
        if not self.conf.get("inject_skills", True):  # 画图现在先走 GPT，不再看 grok 登没登
            return
        req.system_prompt = (req.system_prompt or "") + (
            '\n可按对话需要调用绘图或视频工具。需要角色形象时使用部署者配置的形象参考，生成结果由工具返回。')

    # ---------- QQ 命令 ----------

    @filter.command("grok画")
    async def cmd_draw(self, event: AstrMessageEvent, prompt: str = ""):
        """直接画图测试（管理员）：/grok画 画面描述"""
        if not event.is_admin():
            return
        r = await self.tool_draw(event, event.message_str.split(" ", 1)[-1] if " " in event.message_str else (prompt or "一只蓝色胖鲸鱼"))
        if not r.startswith("图已经"):
            yield event.plain_result(r)

    @filter.command("grok视频")
    async def cmd_video(self, event: AstrMessageEvent, prompt: str = ""):
        """直接做视频测试（管理员）：/grok视频 画面描述"""
        if not event.is_admin():
            return
        r = await self.tool_video(event, event.message_str.split(" ", 1)[-1] if " " in event.message_str else (prompt or "一只蓝色胖鲸鱼在海里翻身"))
        yield event.plain_result("在做了，一两分钟后发过来" if r.startswith("视频开始") else r)

    @filter.command("grok状态")
    async def cmd_status(self, event: AstrMessageEvent):
        if event.is_admin():
            configured = bool(os.environ.get("XAI_API_KEY", "").strip())
            yield event.plain_result("xAI API 已配置" if configured else "请配置 XAI_API_KEY")

    @filter.command("grok登录")
    async def cmd_login(self, event: AstrMessageEvent):
        if event.is_admin():
            yield event.plain_result("在服务环境设置自己的 XAI_API_KEY，再重启插件。")
