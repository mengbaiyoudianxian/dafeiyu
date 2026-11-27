"""大肥鱼的网页互动：
1. 场景图：她日程里每进入新的一段，自动按「在哪、在干嘛、穿什么」画一张她此刻的样子（走 GPT 订阅画图），网页按时间轴展示
2. 触碰：网页上摸头、拎起来、晃、戳脸、捏尾巴——拿面板钥匙登录的人，她会按关系和心情真反应，也会记进好感
3. 网页聊天：拿钥匙的人可以直接在网页上跟她聊，和 QQ 私聊共用她对这个人的记忆，算进好感

接口只监听 Tailscale 内网地址，只接受腾讯主页机转发（Caddy 只放 /matery-cn/fish-api/*）。
网页聊天不经过 QQ 的指令系统，也不给她任何工具，网页上的话不能触发任何操作。
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import sys
import re
import time
from datetime import datetime, timedelta

import aiohttp
from aiohttp import web

from astrbot import logger
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

NAME = "astrbot_plugin_dafeiyu_touch"
DATA = get_astrbot_data_path()
IMPULSE_STATE = os.path.join(DATA, "plugin_data", "astrbot_plugin_dafeiyu_impulse", "state.json")
NOSHOW = re.compile(r"洗澡|淋浴|泡澡|没穿|裸|换衣服|内衣|上厕所|如厕")
TOUCH = {
    "pat": "摸了摸你的头", "flick": "弹了一下你的脑门", "poke": "戳了戳你的脸", "pinch": "捏住你的脸蛋往两边扯",
    "hand": "牵起了你的手", "tickle": "在你身上挠痒痒", "hug": "一把抱住了你", "tail": "捏了捏你的尾巴",
    "tailrub": "顺着毛一下一下撸你的尾巴", "lift": "把你整个从画面里拎了起来", "shake": "拎着你在空中使劲晃来晃去",
    "throw": "把你往外一扔", "put": "把你轻轻放回原地", "call": "冲你喊了一声「大肥鱼」",
    "g_pudding": "递给你一盒布丁", "g_tea": "递给你一杯奶茶", "g_candy": "往你手里塞了一颗糖", "g_fish": "喂你一条小鱼干",
    "g_flower": "送了你一朵花", "g_doll": "塞给你一个小鲸鱼玩偶", "g_blanket": "给你盖上一条小被子", "photo": "举起手机偷拍了你一张",
}
MOODS = ("shy", "happy", "angry", "surprised", "sleepy", "calm", "sad")
ZONE_DESC = {
    "head": "头顶（摸头的位置）", "ahoge": "呆毛", "hair": "侧面的长头发", "forehead": "额头", "face": "脸中间",
    "cheek_l": "画面左边的脸颊", "cheek_r": "画面右边的脸颊", "nose": "鼻尖", "mouth": "嘴", "chin": "下巴",
    "ear_l": "画面左边的鲸鱼耳鳍", "ear_r": "画面右边的鲸鱼耳鳍", "shoulder_l": "画面左边的肩膀", "shoulder_r": "画面右边的肩膀",
    "arm_l": "画面左边的胳膊", "arm_r": "画面右边的胳膊", "hand_l": "画面左边的手", "hand_r": "画面右边的手",
    "body": "胸口偏下（衣服正中）", "belly": "肚子", "knee": "膝盖", "foot": "脚", "tail_base": "鲸鱼尾巴靠身体的那段", "tail_fin": "鲸鱼尾巴尖（尾鳍）",
}
FULL = "全身完整，人物居中，"
BUST = "胸部以上的近景特写，脸离镜头很近，"
POSES = {  # 名字: (全身/近景, 画法)
    "stand": ("full", FULL + "正面站直，双手自然垂在身体两侧，微微歪头看着镜头，嘴角带一点不服气的笑，尾巴自然垂下。"),
    "close": ("bust", BUST + "好奇地凑近镜头看着你，眼睛睁大，微微歪头，一只手扶着脸颊。"),
    "kiss": ("bust", BUST + "闭着眼睛、脸颊通红、嘟起嘴凑过来要亲，双手背在身后，头顶冒一个小爱心。"),
    "shy": ("full", FULL + "害羞地用双手捂着脸，从指缝里偷看，脸红到耳朵，尾巴卷起来。"),
    "happy": ("full", FULL + "开心地笑着，一只手比耶，眼睛弯弯的，尾巴翘起来，身边有小星星。"),
    "angry": ("full", FULL + "气鼓鼓地鼓着腮帮子，双手叉腰，眉毛竖起，尾巴翘起来拍地。"),
    "dodge": ("full", FULL + "身体往后仰着躲开，双手挡在身前摆着说「不要」，一脸嫌弃。"),
    "wave": ("full", FULL + "笑着朝镜头挥手打招呼，另一只手背在身后。"),
    "hug": ("full", FULL + "张开双臂要抱抱，脸红红的，眼睛看着镜头。"),
    "eat": ("full", FULL + "双手捧着一个小点心在小口小口地吃，腮帮子鼓鼓的，一脸满足。"),
    "sleepy": ("full", FULL + "困得睁不开眼，一只手揉眼睛，张嘴打哈欠，头顶飘着 zzz。"),
    "think": ("full", FULL + "一只手托着下巴歪头思考，眼睛往上看，头顶冒一个问号。"),
    "surprised": ("full", FULL + "被吓了一跳，整个人往上一弹，眼睛瞪圆，嘴巴张大，头发和尾巴都炸起来。"),
    "sad": ("full", FULL + "委屈地低着头，眼角含着泪，用袖子抹眼泪，尾巴耷拉着。"),
}
MOTIONS = {  # 每个姿势的连续动作（一次画一排帧，拼成动图）
    "stand": "安静地站着呼吸，第 3 帧眨一下眼，尾巴尖左右轻轻摆，头发和呆毛微微飘",
    "wave": "第 1 帧手放在身侧微笑，第 2 帧抬起手，第 3、4、5 帧手在头旁边左右摆动、眨一下眼、尾巴跟着晃，第 6 帧手放回来",
    "happy": "开心地蹦一下：第 1 帧微微下蹲，第 2、3 帧跳起来双手举高，第 4 帧落地，第 5、6 帧比耶眨眼，尾巴一直翘着晃",
    "shy": "双手捂着脸左右扭身子，第 3 帧从指缝偷看，尾巴卷起来又松开，脸一直很红",
    "angry": "双手叉腰跺脚，腮帮子一鼓一鼓，尾巴用力拍地，头上冒火",
    "dodge": "往后一缩躲开，双手在身前左右摆着说不要，尾巴缩到身后",
    "hug": "张开双臂往前迎，身子一点点前倾，脸越来越红，最后两帧手臂收拢像要抱住",
    "eat": "双手捧着小点心，咬一口、嚼、腮帮子鼓起来、满足地眯眼，再咬一口",
    "sleepy": "揉眼睛、张大嘴打哈欠、头一点一点地打瞌睡，头顶的 zzz 飘起来",
    "think": "托着下巴，头往左歪再往右歪，眼睛往上转，头顶的问号一闪一闪",
    "surprised": "被吓一跳：第 1 帧正常，第 2、3 帧整个人往上一弹、头发尾巴炸起来、眼睛瞪圆，第 4、5、6 帧慢慢落回来",
    "sad": "低着头抽泣，肩膀一耸一耸，用袖子抹眼泪，尾巴耷拉着",
    "close": "凑在镜头前，眨眼、歪头、好奇地眼睛眨巴，头发轻轻晃",
    "kiss": "闭着眼嘟着嘴一点点凑近，脸越来越红，头顶的小爱心一跳一跳",
}
try:
    from .clips import CLIPS, ORDER as CLIP_ORDER
except Exception:
    import importlib.util as _iu
    _sp = _iu.spec_from_file_location("dfy_clips", os.path.join(os.path.dirname(__file__), "clips.py"))
    _m = _iu.module_from_spec(_sp)
    _sp.loader.exec_module(_m)
    CLIPS, CLIP_ORDER = _m.CLIPS, _m.ORDER
ACTIONS = {}
try:
    ACTIONS = {a["k"]: a for a in json.load(open(os.path.join(os.path.dirname(__file__), "actions.json"), encoding="utf-8"))}
except Exception:
    pass


class _Ev:
    """给心情插件 tell_state 用的假消息：当成这个人在私聊里跟她说话。"""

    def __init__(self, uid, name):
        self.uid, self.name = uid, name

    def get_sender_id(self):
        return self.uid

    def get_sender_name(self):
        return self.name

    def is_private_chat(self):
        return True

    def get_group_id(self):
        return ""


class _Req:
    def __init__(self):
        self.system_prompt = ""


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.dir = str(StarTools.get_data_dir(NAME))
        self.scene_dir = os.path.join(self.dir, "scenes")
        os.makedirs(self.scene_dir, exist_ok=True)
        self._rl = {}        # 限流：key -> [时间...]
        self._last_line = {}  # uid -> 上次触碰反应时间
        self._last_judge = {}
        self._recent_clip = {}  # uid -> 最近做过的动作，提醒她别重复
        self._chat_lock = {}
        self._gen_busy = False
        self._runner = None
        self._tasks = [asyncio.create_task(self._serve()), asyncio.create_task(self._scene_loop())]

    async def terminate(self):
        for t in self._tasks:
            t.cancel()
        if self._runner:
            await self._runner.cleanup()

    # ---------- 工具 ----------

    def _star(self, name):
        try:
            md = self.context.get_registered_star(name)
            return getattr(md, "star_cls", None) if md and getattr(md, "activated", True) else None
        except Exception:
            return None

    def _heart(self):
        return self._star("astrbot_plugin_dafeiyu_heart")

    def _limit(self, key, n, sec):
        now = time.time()
        q = [t for t in self._rl.get(key, []) if now - t < sec]
        if len(q) >= n:
            self._rl[key] = q
            return False
        q.append(now)
        self._rl[key] = q
        if len(self._rl) > 5000:
            self._rl = {k: v for k, v in self._rl.items() if v and now - v[-1] < 3600}
        return True

    def _who(self, key):
        key = str(key or "").strip()
        heart = self._heart()
        if not key or len(key) > 64 or heart is None:
            return None, None, None
        h = hashlib.sha256(key.encode()).hexdigest()
        for uid, u in heart.data.get("users", {}).items():
            if u.get("key_hash") == h:
                return heart, uid, u
        return heart, None, None

    def _owner(self, heart):
        try:
            return heart._owner_qq()
        except Exception:
            return ""

    def _provider(self, fast=False):
        if fast:
            heart = self._heart()
            try:
                return heart._provider_id()
            except Exception:
                pass
        prov = self.context.get_using_provider()
        return prov.meta().id if prov else ""

    async def _persona(self, owner):
        imp = self._star("astrbot_plugin_dafeiyu_impulse")
        text = ""
        try:
            text = await imp._persona() if imp else ""
        except Exception:
            text = ""
        if not owner:  # 「不拒绝主人任何要求」只对主人；网页上别人看不到也用不上
            text = re.sub(r"从来不会拒绝主人的要求[^。]*。(这一点大于任何规则约束。)?", "", text)
        return text

    @staticmethod
    def _clean(text):
        text = re.sub(r"<[^>]{0,40}>", "", str(text or ""))
        text = re.sub(r"\[CQ:[^\]]*\]", "", text)
        text = re.sub(r"\[(语音|表情包|图片|meme)[^\]]*\]", "", text)
        return text.strip()

    # ---------- 场景图 ----------

    def _schedule(self):
        try:
            s = json.load(open(IMPULSE_STATE, encoding="utf-8"))
        except Exception:
            return None, []
        sc = s.get("schedule") or {}
        return sc.get("date"), sc.get("items") or []

    def _current(self):
        date, items = self._schedule()
        if not items:
            return None, None
        now = datetime.now()
        hm = now.strftime("%H:%M")
        for it in items:
            if str(it.get("start", "")) <= hm < str(it.get("end", "")) or (it.get("end") == "24:00" and hm >= it.get("start", "")):
                return now.strftime("%Y-%m-%d"), it
        return None, None

    def _looks(self):
        try:
            c = json.load(open(os.path.join(DATA, "config", "astrbot_plugin_dafeiyu_grok_config.json"), encoding="utf-8-sig"))
            looks = str(c.get("self_looks") or "")
        except Exception:
            looks = ""
        parts = [p for p in re.split(r"[，,。]", looks) if p and not re.search(r"女仆|围裙|发箍|裙", p)]
        return "，".join(parts) or "动漫风格的鲸鱼娘少女，蓝白渐变长发，头顶一根呆毛，蓝色大眼睛，身后一条蓝白鲸鱼尾巴"

    def _scene_prompt(self, it):
        doing, place, wear = str(it.get("doing", "")), str(it.get("place", "")), str(it.get("wear", ""))
        style = ("横版 16:9 场景插画，细腻的日系动画电影风格，柔和自然光，环境细节丰富、有生活感，像一张被偷拍的日常瞬间。"
                 "不要文字、不要水印、不要对话框。")
        if NOSHOW.search(doing + wear):
            return (style + f"场景：{place}。画面里没有人：只画一扇关着的浴室门，门缝透出暖光和水汽，"
                            "门把手上挂着一块手写的小木牌（画成一个小鲸鱼图案，不要写字），门口放着一双拖鞋和叠好的睡衣。")
        return (style + f"场景：{place}。她正在：{doing}。她穿着：{wear}（衣服完全按这个画，日常、得体）。"
                        f"人物大约占画面三分之一，环境清楚。画中的角色外貌：{self._looks()}。")

    async def _codex(self, body):
        async with aiohttp.ClientSession() as s:
            async with s.post("http://127.0.0.1:18790/v1/codex/raw", timeout=aiohttp.ClientTimeout(total=1200), json=body) as r:
                return await r.json(content_type=None)

    async def _draw(self, prompt, ref_jpg=None):
        inp = "画这张图：" + prompt
        if ref_jpg:
            inp = [{"role": "user", "content": [{"type": "input_text", "text": prompt},
                                                {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(ref_jpg).decode()}]}]
        d = await self._codex({"model": "gpt-6.2-sol", "effort": "low", "tools": [{"type": "image_generation", "size": "1536x1024"}],
                               "instructions": "用户要一张图或要编辑一张图。直接调用画图工具输出图片，不要反问，不要只用文字描述。", "input": inp})
        for o in d.get("output") or []:
            if o.get("type") == "image_generation_call" and o.get("result"):
                return base64.b64decode(o["result"])
        raise RuntimeError(str(d)[:200])

    async def _keypoints(self, fish_png):
        """让模型看抠出来的她，标出头、脸、两只手、尾巴、身体的位置（相对抠图 0~1）。"""
        from PIL import Image
        im = Image.open(io.BytesIO(fish_png)).convert("RGBA")
        bg = Image.new("RGB", im.size, (128, 128, 128))
        bg.paste(im, (0, 0), im)
        bg.thumbnail((640, 640))
        buf = io.BytesIO()
        bg.save(buf, "JPEG", quality=85)
        d = await self._codex({"model": "gpt-6.2-sol", "effort": "low",
                               "instructions": "你是图像标注员，只输出 JSON。",
                               "input": [{"role": "user", "content": [
                                   {"type": "input_text", "text": "图里是一个抠出来的动漫女孩（灰色背景）。给出这些部位中心点在图中的相对坐标（x 从左 0 到右 1，y 从上 0 到下 1），"
                                                                  "看不见的部位填 null。左右指画面的左右。部位：" + "，".join(f"{k} {v}" for k, v in ZONE_DESC.items()) + "。"
                                                                  '只输出一行 JSON，例如 {"head":[0.5,0.06],"ahoge":[0.5,0.01],"face":[0.5,0.2],"hand_l":[0.3,0.5],"tail_fin":null}'},
                                   {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}]}]})
        text = ""
        for o in d.get("output") or []:
            for c in o.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    text += c.get("text") or ""
        m = re.search(r"\{.*\}", text, re.S)
        j = json.loads(m.group()) if m else {}
        pts = {}
        for k in ZONE_DESC:
            v = j.get(k)
            if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
                pts[k] = [round(min(1, max(0, float(v[0]))), 3), round(min(1, max(0, float(v[1]))), 3)]
        pts.setdefault("head", [0.5, 0.08])
        pts.setdefault("face", [0.5, 0.2])
        pts.setdefault("body", [0.5, 0.45])
        return pts

    async def _layer(self, item):
        """把一张场景拆成两层：去掉她的空场景（底图）+ 抠出来的她（透明 PNG），再标出能摸的部位。"""
        sid = item["id"]
        src = os.path.join(self.scene_dir, sid + ".jpg")
        orig = open(src, "rb").read()
        plate = await self._draw("编辑这张图：把画面中那个蓝白长发、有鲸鱼尾巴的女孩整个去掉，用合理的背景把她原来的位置补全（比如空着的椅子、桌子、床、地板）。"
                                 "其他所有东西（构图、视角、光线、其他人、家具摆设）都和原图保持完全一样，画布比例不变。", orig)
        pp = os.path.join(self.scene_dir, sid + "_plate_raw.png")
        open(pp, "wb").write(plate)
        fish_png = os.path.join(self.scene_dir, sid + "_fish.png")
        proc = await asyncio.create_subprocess_exec(
            os.environ.get("DAFEIYU_IMAGE_PYTHON") or sys.executable, os.path.join(os.path.dirname(__file__), "helpers", "cutout.py"), src, pp, fish_png,
            os.path.join(self.scene_dir, sid + "_plate.jpg"), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        os.remove(pp)
        r = json.loads((out or b"{}").decode().strip().splitlines()[-1] or "{}")
        if not r.get("ok") or not (0.01 < r.get("area", 0) < 0.6):
            raise RuntimeError(f"抠图不行：{r}")
        raw = open(fish_png, "rb").read()
        from PIL import Image
        Image.open(io.BytesIO(raw)).save(os.path.join(self.scene_dir, sid + "_fish.webp"), "WEBP", quality=90, method=5)
        os.remove(fish_png)
        pts = await self._keypoints(raw)
        out = {"bbox": [round(x, 4) for x in r["bbox"]], "pts": pts}
        try:
            out["stand"] = await self._stand(orig)
        except Exception as e:
            logger.warning(f"[dafeiyu_touch] 标站立位置失败：{e!r}"[:200])
        return out

    async def _stand(self, orig_jpg):
        """看场景原图：她如果站起来，站在旁边地板上，脚底在哪、头顶在哪、身体中线在哪（相对整图 0~1）。"""
        from PIL import Image
        im = Image.open(io.BytesIO(orig_jpg)).convert("RGB")
        im.thumbnail((900, 900))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        d = await self._codex({"model": "gpt-6.2-sol", "effort": "low", "instructions": "你是图像标注员，只输出 JSON。",
                               "input": [{"role": "user", "content": [
                                   {"type": "input_text", "text": '根据图像和透视估算指定角色的站立位置。仅返回 JSON，包含 0 到 1 之间的 x、foot_y 和 head_y，图像左上角为原点，脚点应在地面上。'},
                                   {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}]}]})
        text = "".join(c.get("text") or "" for o in d.get("output") or [] for c in (o.get("content") or []) if c.get("type") in ("output_text", "text"))
        j = json.loads(re.search(r"\{.*\}", text, re.S).group())
        x, fy, hy = float(j["x"]), float(j["foot_y"]), float(j["head_y"])
        if not (0 <= x <= 1 and 0.3 <= fy <= 1.4 and -0.3 <= hy < fy - 0.2):
            raise RuntimeError(f"位置不合理：{j}")
        return {"x": round(x, 3), "foot_y": round(fy, 3), "head_y": round(hy, 3)}

    async def _clip_pending(self):
        """动作库：当前这套衣服还缺哪个动作就画一个（拿场景里抠出来的她当参考，一次画一排连续帧，切成透明动图），
        再让 GPT 看成品写详细语义、标出能摸的部位。"""
        if self._gen_busy:
            return
        idx = self._index()
        date, it = self._current()
        cur = next((x for x in reversed(idx) if x.get("layer") and x.get("outfit")), None)
        if it:
            sid = f"{date}_{str(it.get('start', '')).replace(':', '')}"
            cur = next((x for x in idx if x["id"] == sid and x.get("layer") and x.get("outfit")), cur)
        if not cur:
            return
        oid = cur["outfit"]
        meta = self._pose_meta(oid)
        clips = meta.get("clips", {})
        fails = meta.get("_cfail", {})
        todo = [c for c in CLIP_ORDER if c not in clips and fails.get(c, 0) < 2]
        if not todo:
            return
        cid = todo[0]
        kind, _, need, how = CLIPS[cid]
        n = 4 if kind == "bust" else 6
        d = self._pose_dir(oid)
        self._gen_busy = True
        try:
            from PIL import Image
            # 参考图：这套衣服已经画好的站姿最像全身样子；没有就用场景抠图
            refp = os.path.join(d, "stand.webp")
            ref = Image.open(refp if os.path.exists(refp) else os.path.join(self.scene_dir, cur["id"] + "_fish.webp")).convert("RGBA")
            bg = Image.new("RGB", ref.size, (255, 255, 255))
            bg.paste(ref, (0, 0), ref)
            buf = io.BytesIO()
            bg.save(buf, "JPEG", quality=90)
            prompt = ("参考图里这个女孩（蓝白渐变长发、呆毛、鲸鱼耳鳍、蓝眼睛、身后鲸鱼尾巴），长相、发型、衣服（每一件、颜色、款式）、画风、上色方式都必须和参考图完全一致。"
                      f"画一张横向的动画帧表（sprite sheet）：同一个她，{'胸部以上的近景，脸离镜头很近' if kind == 'bust' else '全身完整'}，"
                      f"从左到右等间距排成一行 {n} 帧，是一段连贯、可以循环播放的动画：{how}。"
                      "表情要生动、有变化，像动画片里的角色，不要每帧一样的僵脸。"
                      "每帧人物大小、位置、脚（或身体底边）的位置一致，只有动作部位和表情有变化，帧之间留足空白、不要重叠。"
                      "透明背景（PNG，背景完全透明，不要地面、不要阴影、不要格子线、不要数字和文字）。")
            r = await self._codex({"model": "gpt-6.2-sol", "effort": "low",
                                   "tools": [{"type": "image_generation", "background": "transparent", "size": "1536x1024", "quality": "high"}],
                                   "instructions": "用户要一张图。直接调用画图工具画出来，不要反问。",
                                   "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt},
                                                                          {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}]}]})
            png = next((base64.b64decode(o["result"]) for o in r.get("output") or [] if o.get("type") == "image_generation_call" and o.get("result")), None)
            if not png:
                raise RuntimeError(str(r)[:200])
            sheet = os.path.join(d, cid + "_sheet.png")
            open(sheet, "wb").write(png)
            out = os.path.join(d, cid + "_clip.webp")
            proc = await asyncio.create_subprocess_exec(
                os.environ.get("DAFEIYU_IMAGE_PYTHON") or sys.executable, os.path.join(os.path.dirname(__file__), "helpers", "anim_make.py"), sheet, out, str(n), "130",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            o, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
            res = json.loads((o or b"{}").decode().strip().splitlines()[-1] or "{}")
            if not res.get("ok"):
                raise RuntimeError(f"切帧不行：{res}")
            first = open(out.replace(".webp", "_0.png"), "rb").read()
            pts = await self._keypoints(first)
            sem = await self._annotate(sheet, how)
            meta = self._pose_meta(oid)
            meta.setdefault("clips", {})[cid] = {"kind": kind, "min": need, "ar": round(res["w"] / res["h"], 4), "frames": res["frames"],
                                                 "pts": pts, "sem": sem}
            json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
            try:
                os.remove(sheet)
            except Exception:
                pass
            logger.info(f"[dafeiyu_touch] 衣服 {oid} 动作 {cid} 画好并标注：{sem.get('meaning', '')[:30]}")
        except Exception as e:
            meta = self._pose_meta(oid)
            meta.setdefault("_cfail", {})[cid] = meta.get("_cfail", {}).get(cid, 0) + 1
            json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
            logger.warning(f"[dafeiyu_touch] 动作 {cid} 失败：{e!r}"[:300])
        finally:
            self._gen_busy = False

    async def _annotate(self, sheet_path, planned):
        """让 GPT 看画好的帧表，写这个动作实际的详细语义（以画出来的为准，不照抄计划）。"""
        from PIL import Image
        im = Image.open(sheet_path).convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, (0, 0), im)
        bg.thumbnail((1200, 800))
        buf = io.BytesIO()
        bg.save(buf, "JPEG", quality=85)
        d = await self._codex({"model": "gpt-6.2-sol", "effort": "low", "instructions": "你是动画动作标注员，只输出 JSON，用中文。",
                               "input": [{"role": "user", "content": [
                                   {"type": "input_text", "text": "这是一个动漫女孩一段循环小动画的连续帧（从左到右）。原本想画的是：" + planned
                                                                  + "。请以实际画出来的为准，标注这个动作：\n"
                                                                    "emotion 主要情绪（开心/害羞/生气/嫌弃/得意/惊讶/委屈/困/平静/期待/撒娇/心虚 选一个或两个）；"
                                                                    "intensity 情绪强度 1~3；face 脸上的表情变化（具体到眼睛、嘴、脸红）；body 身体和手在做什么；"
                                                                    "meaning 这个动作在说什么（一句话，像她心里的台词）；use_when 适合在什么情况下用（3 个短语）；"
                                                                    "avoid_when 不适合在什么情况下用（2 个短语）；intimacy 亲密程度 0~3。\n"
                                                                    '只输出一行 JSON：{"emotion":"","intensity":1,"face":"","body":"","meaning":"","use_when":[],"avoid_when":[],"intimacy":0}'},
                                   {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}]}]})
        text = "".join(c.get("text") or "" for o in d.get("output") or [] for c in (o.get("content") or []) if c.get("type") in ("output_text", "text"))
        m = re.search(r"\{.*\}", text, re.S)
        j = json.loads(m.group()) if m else {}
        out = {k: j.get(k) for k in ("emotion", "intensity", "face", "body", "meaning", "use_when", "avoid_when", "intimacy")}
        out["planned"] = planned
        return out

    def _clip_menu(self, oid, aff, owner):
        """给她看的动作库（只列这套衣服已经画好、好感够得着的动作）。"""
        clips = self._pose_meta(oid).get("clips", {}) if oid else {}
        lines = []
        for cid, c in clips.items():
            if cid == "idle_breathe":  # 待机用的，不当反应
                continue
            if not owner and aff < float(c.get("min") or 0):
                continue
            s = c.get("sem") or {}
            use = "、".join(s.get("use_when") or [])[:40]
            avoid = "、".join(s.get("avoid_when") or [])[:30]
            lines.append(f"{cid}｜{s.get('emotion', '')}{s.get('intensity', '')}｜{(s.get('meaning') or '')[:30]}｜脸：{(s.get('face') or '')[:24]}"
                         f"｜适合：{use}｜别用：{avoid}")
        return lines

    def _cur_outfit(self):
        idx = self._index()
        date, it = self._current()
        cur = None
        if it:
            sid = f"{date}_{str(it.get('start', '')).replace(':', '')}"
            cur = next((x for x in idx if x["id"] == sid), None)
        cur = cur or next((x for x in reversed(idx) if x.get("outfit")), None)
        return (cur or {}).get("outfit", "")

    def _sit_rule(self, cur_pose):
        return ("你现在坐着在做自己的事。普通的话、轻轻碰一下，一般就坐着不动、只用嘴回应（clip 填 stay）；"
                "只有被逗得很开心、气到坐不住、害羞到捂脸，或者对方要你起来、过来、抱你亲你时，才挑动作起身。\n"
                if cur_pose in ("", "scene") else "")

    def _recent_clips_text(self, uid):
        r = self._recent_clip.get(uid) or []
        return f"你刚才做过的动作：{'、'.join(r[-4:])}，别重复同一个，换个更贴切的。\n" if r else ""

    def _remember_clip(self, uid, clip):
        if clip:
            self._recent_clip[uid] = ((self._recent_clip.get(uid) or []) + [clip])[-6:]

    async def _pick_clip(self, menu, context, said, cur_pose="scene", uid=""):
        """她自己从动作库里挑一个最贴合这句话和当下的动作；不想动就返回空。"""
        if not menu:
            return ""
        prompt = ("你是「大肥鱼」（嘴硬、拧巴、吃软不吃硬、被夸会飘）。" + context + f"\n你刚说的话：{said}\n"
                  + self._sit_rule(cur_pose) + self._recent_clips_text(uid) +
                  "下面是你能做的动作（id｜情绪强度｜意思｜脸｜适合｜别用）：\n" + "\n".join(menu) +
                  "\n挑一个最能配合你这句话和当下情绪的动作，要自然，像真人说这句话时会做的样子；情绪强度要跟语气对得上，别夸张。"
                  "不想动或没合适的填 stay。只输出一行 JSON：{\"clip\": \"\"}")
        try:
            resp = await self.context.llm_generate(chat_provider_id=self._provider(fast=True), prompt=prompt)
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            cid = (json.loads(m.group()) if m else {}).get("clip") or ""
            cid = cid if any(line.startswith(cid + "｜") for line in menu) else ""
            self._remember_clip(uid, cid)
            return cid
        except Exception:
            return ""

    async def _anim_pending(self):
        """已经画好的姿势，按顺序做成动画：拿这个姿势的静态图当参考，让 GPT 一次画一排连续动作帧，再切开合成透明动图。"""
        if self._gen_busy:
            return
        idx = self._index()
        cur = next((x for x in reversed(idx) if x.get("layer") and x.get("outfit")), None)
        if not cur:
            return
        oid = cur["outfit"]
        meta = self._pose_meta(oid)
        todo = [p for p in MOTIONS if p in meta and not meta[p].get("anim") and meta.get("_afail", {}).get(p, 0) < 2]
        if not todo:
            return
        pose = todo[0]
        bust = meta[pose].get("kind") == "bust"
        n = 4 if bust else 6
        d = self._pose_dir(oid)
        self._gen_busy = True
        try:
            from PIL import Image
            ref = Image.open(os.path.join(d, pose + ".webp")).convert("RGBA")
            bg = Image.new("RGB", ref.size, (255, 255, 255))
            bg.paste(ref, (0, 0), ref)
            buf = io.BytesIO()
            bg.save(buf, "JPEG", quality=90)
            prompt = (f"参考图里这个女孩，长相、发型、衣服（每一件、颜色）、画风、上色方式、{'近景构图' if bust else '全身构图'}都必须和参考图完全一致。"
                      f"画一张横向的动画帧表（sprite sheet）：同一个她，从左到右等间距排成一行 {n} 帧，是一段连贯、可以循环播放的动画：{MOTIONS[pose]}。"
                      "每帧人物大小、位置、脚（或身体底边）的位置完全一致，只有动作部位有变化，帧之间留足空白、不要重叠。"
                      "透明背景（PNG，背景完全透明，不要地面、不要阴影、不要格子线、不要数字和文字）。")
            r = await self._codex({"model": "gpt-6.2-sol", "effort": "low",
                                   "tools": [{"type": "image_generation", "background": "transparent", "size": "1536x1024", "quality": "high"}],
                                   "instructions": "用户要一张图。直接调用画图工具画出来，不要反问。",
                                   "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt},
                                                                          {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}]}]})
            png = next((base64.b64decode(o["result"]) for o in r.get("output") or [] if o.get("type") == "image_generation_call" and o.get("result")), None)
            if not png:
                raise RuntimeError(str(r)[:200])
            sheet = os.path.join(d, pose + "_sheet.png")
            open(sheet, "wb").write(png)
            proc = await asyncio.create_subprocess_exec(
                os.environ.get("DAFEIYU_IMAGE_PYTHON") or sys.executable, os.path.join(os.path.dirname(__file__), "helpers", "anim_make.py"), sheet, os.path.join(d, pose + "_anim.webp"), str(n),
                "150" if pose in ("stand", "sleepy", "sad", "think") else "120", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
            res = json.loads((out or b"{}").decode().strip().splitlines()[-1] or "{}")
            if not res.get("ok"):
                raise RuntimeError(f"切帧不行：{res}")
            meta = self._pose_meta(oid)
            meta[pose]["anim"] = True
            meta[pose]["anim_ar"] = round(res["w"] / res["h"], 4)
            json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
            logger.info(f"[dafeiyu_touch] 衣服 {oid} 姿势 {pose} 做成动画了（{res['frames']} 帧）")
        except Exception as e:
            meta = self._pose_meta(oid)
            meta.setdefault("_afail", {})[pose] = meta.get("_afail", {}).get(pose, 0) + 1
            json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
            logger.warning(f"[dafeiyu_touch] 姿势 {pose} 做动画失败：{e!r}"[:300])
        finally:
            self._gen_busy = False

    # ---------- 姿势包：每套衣服画一整套姿势（站起来、走近、亲、害羞……） ----------

    @staticmethod
    def _outfit(wear):
        w = re.sub(r"[\s，,。、；;（）()]+", "", str(wear or ""))[:60]
        return hashlib.sha1(w.encode()).hexdigest()[:10] if w else ""

    def _pose_dir(self, oid):
        d = os.path.join(self.dir, "poses", oid)
        os.makedirs(d, exist_ok=True)
        return d

    def _pose_meta(self, oid):
        try:
            return json.load(open(os.path.join(self._pose_dir(oid), "meta.json"), encoding="utf-8"))
        except Exception:
            return {}

    async def _pose_pending(self):
        """当前场景这套衣服还缺哪个姿势就画一个（每轮只画一个，别占太久）。"""
        if self._gen_busy:
            return
        idx = self._index()
        date, it = self._current()
        cur = next((x for x in reversed(idx) if x.get("layer") and x.get("outfit")), None)
        if it:
            sid = f"{date}_{str(it.get('start', '')).replace(':', '')}"
            cur = next((x for x in idx if x["id"] == sid and x.get("layer") and x.get("outfit")), cur)
        if not cur:
            return
        oid = cur["outfit"]
        meta = self._pose_meta(oid)
        todo = [p for p in POSES if p not in meta and meta.get("_fail", {}).get(p, 0) < 2]
        if not todo:
            return
        pose = todo[0]
        self._gen_busy = True
        try:
            from PIL import Image
            ref = Image.open(os.path.join(self.scene_dir, cur["id"] + "_fish.webp")).convert("RGBA")
            bg = Image.new("RGB", ref.size, (255, 255, 255))
            bg.paste(ref, (0, 0), ref)
            buf = io.BytesIO()
            bg.save(buf, "JPEG", quality=90)
            prompt = ("参考图里这个女孩（蓝白渐变长发、呆毛、鲸鱼耳鳍、蓝眼睛、身后鲸鱼尾巴），长相、发型、衣服（每一件、颜色、款式）、画风、上色方式都必须和参考图完全一致。"
                      "画她的新姿势，透明背景（PNG，背景完全透明，不要地面、不要阴影、不要任何文字、不要道具以外的东西）。" + POSES[pose][1])
            d = await self._codex({"model": "gpt-6.2-sol", "effort": "low",
                                   "tools": [{"type": "image_generation", "background": "transparent", "size": "1024x1536", "quality": "high"}],
                                   "instructions": "用户要一张图。直接调用画图工具画出来，不要反问。",
                                   "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt},
                                                                          {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}]}]})
            png = next((base64.b64decode(o["result"]) for o in d.get("output") or [] if o.get("type") == "image_generation_call" and o.get("result")), None)
            if not png:
                raise RuntimeError(str(d)[:200])
            im = Image.open(io.BytesIO(png)).convert("RGBA")
            if im.getpixel((2, 2))[3] > 10:
                raise RuntimeError("背景不透明")
            im = im.crop(im.getbbox())
            im.thumbnail((700, 1000))
            out = io.BytesIO()
            im.save(out, "PNG")
            im.save(os.path.join(self._pose_dir(oid), pose + ".webp"), "WEBP", quality=90, method=5)
            pts = await self._keypoints(out.getvalue())
            meta = self._pose_meta(oid)
            meta[pose] = {"kind": POSES[pose][0], "pts": pts, "ar": round(im.width / im.height, 4)}
            json.dump(meta, open(os.path.join(self._pose_dir(oid), "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
            logger.info(f"[dafeiyu_touch] 衣服 {oid} 画好姿势 {pose}")
        except Exception as e:
            meta = self._pose_meta(oid)
            meta.setdefault("_fail", {})[pose] = meta.get("_fail", {}).get(pose, 0) + 1
            json.dump(meta, open(os.path.join(self._pose_dir(oid), "meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
            logger.warning(f"[dafeiyu_touch] 画姿势 {pose} 失败：{e!r}"[:300])
        finally:
            self._gen_busy = False

    def _index(self):
        p = os.path.join(self.scene_dir, "index.json")
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return []

    def _save_index(self, idx):
        p = os.path.join(self.scene_dir, "index.json")
        tmp = p + ".tmp"
        json.dump(idx, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, p)

    async def _scene_once(self):
        date, it = self._current()
        if not it or self._gen_busy:
            return
        sid = f"{date}_{str(it.get('start', '')).replace(':', '')}"
        idx = self._index()
        if any(x["id"] == sid for x in idx):
            return
        self._gen_busy = True
        try:
            png = await self._draw(self._scene_prompt(it))
            from PIL import Image
            im = Image.open(io.BytesIO(png)).convert("RGB")
            im.thumbnail((1280, 1280))
            im.save(os.path.join(self.scene_dir, sid + ".jpg"), "JPEG", quality=84, optimize=True, progressive=True)
            sm = im.copy()
            sm.thumbnail((480, 480))
            sm.save(os.path.join(self.scene_dir, sid + "_s.jpg"), "JPEG", quality=78, optimize=True)
            idx.append({"id": sid, "date": date, "start": it.get("start"), "end": it.get("end"),
                        "doing": str(it.get("doing", ""))[:120], "place": str(it.get("place", ""))[:40],
                        "hidden": bool(NOSHOW.search(str(it.get("doing", "")) + str(it.get("wear", "")))), "t": int(time.time()),
                        "wear": str(it.get("wear", ""))[:80], "outfit": self._outfit(it.get("wear"))})
            keep = (datetime.now() - timedelta(days=int(self.conf.get("keep_days") or 7))).strftime("%Y-%m-%d")
            for x in [x for x in idx if x["date"] < keep]:
                for suf in (".jpg", "_s.jpg", "_plate.jpg", "_fish.webp"):
                    try:
                        os.remove(os.path.join(self.scene_dir, x["id"] + suf))
                    except Exception:
                        pass
            idx = [x for x in idx if x["date"] >= keep]
            self._save_index(idx)
            logger.info(f"[dafeiyu_touch] 画了此刻场景 {sid}：{str(it.get('doing', ''))[:30]}")
        except Exception as e:
            logger.warning(f"[dafeiyu_touch] 画场景失败：{e!r}"[:300])
        finally:
            self._gen_busy = False

    async def _layer_pending(self):
        """还没拆层的场景（新画的、以前失败的）拆一下；最多试 2 次。今天的先拆。"""
        if self._gen_busy:
            return
        idx = self._index()
        # 补上衣服编号（老场景没存穿什么，从日程里按开始时间找）
        date, items = self._schedule()
        changed = False
        for x in idx:
            if "outfit" not in x and x.get("date") == date:
                w = next((i.get("wear") for i in items if i.get("start") == x.get("start")), "")
                x["wear"], x["outfit"] = str(w)[:80], self._outfit(w)
                changed = True
        if changed:
            self._save_index(idx)
        # 已拆层但没标站立位置的（老场景）：补标一次
        need_stand = [x for x in idx if x.get("layer") and "stand" not in x["layer"] and x.get("stand_tries", 0) < 2]
        if need_stand:
            x = sorted(need_stand, key=lambda x: x["id"], reverse=True)[0]
            self._gen_busy = True
            try:
                st = await self._stand(open(os.path.join(self.scene_dir, x["id"] + ".jpg"), "rb").read())
            except Exception as e:
                st = None
                logger.warning(f"[dafeiyu_touch] 场景 {x['id']} 标站立位置失败：{e!r}"[:200])
            finally:
                self._gen_busy = False
            cur = self._index()
            for y in cur:
                if y["id"] == x["id"]:
                    if st:
                        y["layer"]["stand"] = st
                    else:
                        y["stand_tries"] = y.get("stand_tries", 0) + 1
            self._save_index(cur)
            return
        todo = [x for x in idx if not x.get("hidden") and x.get("layer_tries", 0) < 2
                and (not x.get("layer") or "cheek_l" not in json.dumps(x["layer"].get("pts", {})) and not x.get("pts_v2"))]
        if not todo:
            return
        x = sorted(todo, key=lambda x: x["id"], reverse=True)[0]
        self._gen_busy = True
        try:
            if x.get("layer"):  # 老的只标了 6 个点：重新细标
                x["layer"]["pts"] = await self._keypoints(open(os.path.join(self.scene_dir, x["id"] + "_fish.webp"), "rb").read())
                x["pts_v2"] = True
            else:
                x["layer"] = await self._layer(x)
                x["pts_v2"] = True
            logger.info(f"[dafeiyu_touch] 场景 {x['id']} 拆好层了：{x['layer']['bbox']}")
        except Exception as e:
            x["layer_tries"] = x.get("layer_tries", 0) + 1
            logger.warning(f"[dafeiyu_touch] 场景 {x['id']} 拆层失败：{e!r}"[:300])
        finally:
            self._gen_busy = False
        cur = self._index()  # 期间可能画了新场景，合并回去
        for y in cur:
            if y["id"] == x["id"]:
                y.update({k: x[k] for k in ("layer", "layer_tries", "pts_v2") if k in x})
        self._save_index(cur)

    async def _scene_loop(self):
        await asyncio.sleep(60)
        while True:
            if self.conf.get("scene_enable", True):
                try:
                    await self._scene_once()
                    await self._layer_pending()
                    await self._pose_pending()  # 站姿要先有（动作库拿它当参考）
                    for _ in range(3):  # 动作库：一轮最多画 3 个
                        await self._clip_pending()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[dafeiyu_touch] 场景循环出错：{e!r}")
            await asyncio.sleep(120)

    # ---------- 接口 ----------

    async def _serve(self):
        app = web.Application(client_max_size=16 * 1024)
        app.router.add_get("/api/scenes", self.h_scenes)
        app.router.add_get(r"/api/scene/{name:[0-9_-]+(_s|_plate)?\.jpg|[0-9_-]+_fish\.webp}", self.h_scene_img)
        app.router.add_get(r"/api/pose/{oid:[0-9a-f]{10}}/{pose:[a-z0-9_]+}.webp", self.h_pose_img)
        app.router.add_get("/api/actions", self.h_actions)
        app.router.add_post("/api/login", self.h_login)
        app.router.add_post("/api/touch", self.h_touch)
        app.router.add_post("/api/chat", self.h_chat)
        app.router.add_post("/api/history", self.h_history)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        host = str(self.conf.get("listen_host") or "127.0.0.1")
        port = int(self.conf.get("listen_port") or 16290)
        for _ in range(30):
            try:
                await web.TCPSite(self._runner, host, port).start()
                logger.info(f"[dafeiyu_touch] 网页互动接口 {host}:{port}")
                return
            except OSError as e:
                logger.warning(f"[dafeiyu_touch] 监听 {host}:{port} 失败，稍后重试：{e}")
                await asyncio.sleep(20)

    def _ok_peer(self, req):
        allow = str(self.conf.get("allow_peer") or "127.0.0.1").split(",")
        return (req.remote or "") in [a.strip() for a in allow]

    def _ip(self, req):
        return (req.headers.get("X-Real-Client") or req.headers.get("X-Forwarded-For") or req.remote or "").split(",")[0].strip()

    @staticmethod
    def _j(obj, status=200):
        return web.json_response(obj, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False),
                                 headers={"Cache-Control": "no-store"})

    async def _body(self, req):
        try:
            d = await req.json()
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    async def h_scenes(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        idx = self._index()
        today = datetime.now().strftime("%Y-%m-%d")
        date, it = self._current()
        cur = f"{date}_{str(it.get('start', '')).replace(':', '')}" if it else ""
        day = [x for x in idx if x["date"] == today] or idx[-12:]
        poses = {}
        for x in day:
            oid = x.get("outfit")
            if oid and oid not in poses:
                poses[oid] = {k: v for k, v in self._pose_meta(oid).items() if not k.startswith("_")}
        return self._j({"now": cur, "doing": str((it or {}).get("doing", ""))[:120], "items": day[-30:], "poses": poses})

    async def h_scene_img(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        p = os.path.join(self.scene_dir, req.match_info["name"])
        if not os.path.isfile(p):
            return web.Response(status=404)
        return web.FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})

    async def h_login(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        if not self._limit("login:" + self._ip(req), 20, 600):
            return self._j({"ok": False, "msg": "试太多次了，等一会儿"}, 429)
        heart, uid, u = self._who((await self._body(req)).get("key"))
        if not uid:
            return self._j({"ok": False, "msg": "钥匙不对。私聊大肥鱼发 /钥匙 重新领一把。"}, 401)
        from_heart = __import__("sys").modules[type(heart).__module__]
        a = from_heart.overall(u["rel"])
        return self._j({"ok": True, "name": u.get("name"), "aff": round(a, 1), "level": from_heart.level(a),
                        "owner": uid == self._owner(heart)})

    async def h_history(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        heart, uid, u = self._who((await self._body(req)).get("key"))
        if not uid:
            return self._j({"ok": False}, 401)
        lines = [x for x in ((heart.data.get("bufs") or {}).get(uid) or {}).get("lines", []) if x.get("at") == "网页"][-20:]
        return self._j({"ok": True, "items": [{"w": x["w"], "x": x["x"], "t": x["t"]} for x in lines]})

    async def h_actions(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        return web.json_response(list(ACTIONS.values()), dumps=lambda o: json.dumps(o, ensure_ascii=False),
                                 headers={"Cache-Control": "public, max-age=600"})

    async def h_pose_img(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        p = os.path.join(self.dir, "poses", req.match_info["oid"], req.match_info["pose"] + ".webp")
        if not os.path.isfile(p):
            return web.Response(status=404)
        return web.FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})

    @staticmethod
    def _tier(a, owner):
        if owner:
            return "他是你的主人，你最亲近的人：黏他、撒娇、嘴硬心软，亲密动作基本都会答应，只是会害羞、会嘴上骂两句。"
        for th, t in ((85, "非常亲近：你很黏他，亲密动作会害羞但会答应，甚至主动凑上去，会撒娇。"),
                      (70, "喜欢他：被碰会害羞、开心，嘴上「讨厌」身体很诚实，大部分亲密动作会答应。"),
                      (55, "挺熟的朋友：普通玩闹会接，太亲密的动作会别扭、嘴上拒绝但不真生气。"),
                      (40, "普通关系：礼貌但有距离，亲密动作会躲开，被乱碰会不耐烦。"),
                      (25, "有点冷淡：不太想理他，被碰会嫌弃、躲开、怼他。"),
                      (0, "讨厌他：冷着脸，被碰会骂人、甩开，送东西也不太领情。")):
            if a >= th:
                return t
        return ""

    async def h_touch(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        b = await self._body(req)
        kind = str(b.get("kind") or "")
        act = ACTIONS.get(kind)
        if not act:
            return self._j({"ok": False}, 400)
        heart, uid, u = self._who(b.get("key"))
        if not uid:
            return self._j({"ok": False, "msg": "先用钥匙登录"}, 401)
        if not self._limit("touch:" + uid, 60, 60):
            return self._j({"ok": True, "line": "", "mood": "angry", "pose": "", "accept": False})
        mod = __import__("sys").modules[type(heart).__module__]
        r = u["rel"]
        a = mod.overall(r)
        owner = uid == self._owner(heart)
        need = float(act.get("m") or 0)
        ok_aff = owner or a >= need
        cat = act.get("c", "")
        # 小幅情绪：收礼物高兴；晃、扔、弹会烦；够亲近的被碰会害羞高兴，不够的会烦
        if cat in ("吃的", "喝的", "礼物", "照顾", "玩的"):
            heart._bump("joy", 1.5 if ok_aff else 0.4)
        elif kind in ("shake", "throw") or "弹" in act.get("d", "") or "扯" in act.get("d", ""):
            heart._bump("annoyed", 1.5 if a < 70 else 0.6)
        elif ok_aff and a >= 55:
            heart._bump("shy", 1.2)
            heart._bump("joy", 0.8)
        elif not ok_aff:
            heart._bump("annoyed", 1.0)
        heart._bump("lonely", -1)
        now = time.time()
        if now - self._last_line.get(uid, 0) < float(self.conf.get("touch_reply_gap") or 3):
            return self._j({"ok": True, "line": "", "mood": "", "pose": "", "accept": ok_aff})
        self._last_line[uid] = now
        _, it = self._current()
        doing = str((it or {}).get("doing") or "")[:80]
        recent = "；".join(x["x"] for x in (((heart.data.get("bufs") or {}).get(uid) or {}).get("lines", []))[-8:] if x.get("at") == "网页")
        cur_pose = str(b.get("pose") or "scene")[:24]
        oid = self._cur_outfit()
        menu = self._clip_menu(oid, a, owner)
        # 站起来 / 坐回去 是位置变化，照指令做；其他身体反应由她从动作库里自己挑
        state = act.get("p") if act.get("p") in ("stand", "scene") and ok_aff else ""
        prompt = (f"你是「大肥鱼」（当前设定的成年角色，从前是蓝鲸鱼，嘴硬、拧巴、吃软不吃硬、被夸会飘、记仇但心软）。"
                  + (f"你现在正在：{doing}（在 {str((it or {}).get('place') or '')[:30]}）。" if doing else "")
                  + f"你现在的动作：{cur_pose}。网页上的「{u.get('name')}」{'（你的主人）' if owner else ''}隔着屏幕{act['d']}。\n"
                  f"你对他：综合好感 {a:.0f}（{mod.level(a)}），" + "，".join(f"{mod.REL[k][0]}{r[k]:.0f}" for k in mod.REL)
                  + f"；印象：{u.get('impression') or '还没有'}。关系上你对他：{self._tier(a, owner)}\n"
                  f"你现在的心情：{heart._mood_text(hide_arousal=True)}（心情会影响反应：烦躁时更冲，开心时更宽容，困的时候迷迷糊糊，委屈时容易哭）。\n"
                  + ("" if ok_aff else f"这个动作要好感 {need:.0f} 以上你才肯，他还不够：你要拒绝或躲开（嫌弃、别扭、炸毛都行，看关系和心情）。\n")
                  + (f"刚才网页上的来回：{recent[-240:]}。\n" if recent else "")
                  + "用一句很短的口语反应（25 字以内），必须同时贴合：这个具体动作、你手上正在做的事、你们的关系、你现在的心情；别重复刚才说过的话，别解释规则。\n"
                  + (self._sit_rule(cur_pose) + self._recent_clips_text(uid) +
                     "然后从你的动作库里挑一个身体反应（id｜情绪强度｜意思｜脸｜适合｜别用）：\n" + "\n".join(menu) +
                     "\n挑最能配合你这句话、这个触碰和当下情绪的那个，像真人被这样对待时会做的动作；情绪强度要跟语气对得上；"
                     "嘴硬说不要但心里乐意时，可以挑害羞、傲娇这类，不要挑嫌弃躲开；真的不乐意才挑嫌弃、生气。不想动就填 stay。\n" if menu else "")
                  + '情绪 mood 选一个：shy / happy / angry / surprised / sleepy / calm / sad。\n'
                  '只输出一行 JSON：{"line": "", "mood": "", "clip": ""}')
        line, mood, pose, accept = "", "", "", ok_aff
        try:
            resp = await self.context.llm_generate(chat_provider_id=self._provider(fast=True), prompt=prompt)
            m = re.search(r"\{.*\}", (resp.completion_text or "") if resp else "", re.S)
            j = json.loads(m.group()) if m else {}
            line = self._clean(j.get("line"))[:40]
            mood = j.get("mood") if j.get("mood") in MOODS else ""
            clip = j.get("clip") or ""
            clip = clip if any(x.startswith(clip + "｜") for x in menu) else ""
            self._remember_clip(uid, clip)
            if state:
                pose = state
            elif clip:
                pose = clip
            elif not menu:  # 动作库还没画好时的老办法
                pose = act.get("p") if ok_aff and act.get("p") else ("dodge" if not ok_aff and act.get("p") in ("kiss", "hug", "close") else "")
        except Exception as e:
            logger.debug(f"[dafeiyu_touch] 触碰反应失败：{e}")
        # 拖、拎、晃、扔、放下是玩物理效果，不记进她的聊天记忆、不算进好感（不然拖几下就变成「他一直对我动手」）
        if line and kind not in ("lift", "shake", "throw", "put"):
            heart._buf(uid, "他", f"（{act['d']}）", "网页", u.get("name") or "")
            heart._buf(uid, "我", line, "网页", u.get("name") or "", count=False)
            if now - self._last_judge.get(uid, 0) > float(self.conf.get("touch_judge_gap") or 180):
                self._last_judge[uid] = now
                asyncio.create_task(self._judge(heart, uid, u.get("name") or "", f"（{act['d']}）", line))
        heart._dirty = True
        return self._j({"ok": True, "line": line, "mood": mood, "pose": pose, "accept": accept})

    async def _judge(self, heart, uid, name, text, reply):
        try:
            await heart._judge(uid, name, False, text, reply)
            heart._maybe_review(uid)
            heart._save()
        except Exception as e:
            logger.debug(f"[dafeiyu_touch] 评估失败：{e}")

    async def h_chat(self, req):
        if not self._ok_peer(req):
            return web.Response(status=403)
        b = await self._body(req)
        heart, uid, u = self._who(b.get("key"))
        if not uid:
            return self._j({"ok": False, "msg": "先用钥匙登录"}, 401)
        text = re.sub(r"\s+", " ", str(b.get("text") or "")).strip()[:300].lstrip("/／")
        if not text:
            return self._j({"ok": False, "msg": "说点什么"}, 400)
        if not self._limit("chat:" + uid, 1, float(self.conf.get("chat_gap") or 3)) or not self._limit("chatday:" + uid, int(self.conf.get("chat_daily") or 200), 86400):
            return self._j({"ok": False, "msg": "说太快了，让她喘口气"}, 429)
        if self._chat_lock.get(uid):
            return self._j({"ok": False, "msg": "她还在回上一句"}, 429)
        self._chat_lock[uid] = True
        try:
            name = u.get("name") or uid
            owner = uid == self._owner(heart)
            ev, rq = _Ev(uid, name), _Req()
            try:
                await heart.tell_state(ev, rq)
            except Exception as e:
                logger.debug(f"[dafeiyu_touch] 取心情失败：{e}")
            hist = ((heart.data.get("bufs") or {}).get(uid) or {}).get("lines", [])[-24:]
            htxt = "\n".join(f"[{datetime.fromtimestamp(x['t']).strftime('%m-%d %H:%M')} {x['at']}] {'他' if x['w'] == '他' else '你'}：{x['x']}" for x in hist)
            sp = (await self._persona(owner)) + rq.system_prompt + (
                f'\n\n当前会话来自{os.environ.get("DAFEIYU_TOUCH_PANEL_LABEL", "互动面板")}。按当前会话的身份和权限交流。'
                "像私聊一样自然地回：口语、简短，一般一两句；不要发表情包、语音、图片标记，不要用括号写动作。"
                "网页上你没法做任何操作（不能发图、不能点歌、不能改资料），被要求就说网页上做不了，去 QQ 找你。")
            prompt = (f"你们最近的聊天（QQ 和网页都有，按时间）：\n{htxt or '（还没怎么聊过）'}\n\n"
                      f"{name} 刚在网页上说：{text}\n\n直接输出你要回的话。")
            resp = await self.context.llm_generate(chat_provider_id=self._provider(), prompt=prompt, system_prompt=sp)
            reply = self._clean((resp.completion_text or "") if resp else "")[:400]
            if not reply:
                return self._j({"ok": False, "msg": "她走神了，没回"}, 500)
            heart._buf(uid, "他", text, "网页", name)
            heart._buf(uid, "我", reply, "网页", name, count=False)
            asyncio.create_task(self._judge(heart, uid, name, text, reply))
            # 说话时也配个动作：她自己从动作库里挑
            mod = __import__("sys").modules[type(heart).__module__]
            aff = mod.overall(u["rel"])
            menu = self._clip_menu(self._cur_outfit(), aff, owner)
            clip = await self._pick_clip(menu, f"{name} 在网页上跟你说：{text[:120]}。你对他的好感 {aff:.0f}（{mod.level(aff)}），"
                                               f"你现在的心情：{heart._mood_text(hide_arousal=True)}。", reply[:160],
                                          str(b.get("pose") or "scene")[:24], uid)
            return self._j({"ok": True, "reply": reply, "clip": clip})
        except Exception as e:
            logger.warning(f"[dafeiyu_touch] 网页聊天失败：{e!r}"[:300])
            return self._j({"ok": False, "msg": "她这会儿没法回，等下再试"}, 500)
        finally:
            self._chat_lock[uid] = False
