import sys, os, json, time
sys.path.insert(0, "/opt/dafeiyu/kit")
import astr_api as A
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
A.login()
PK="/opt/dafeiyu/astrbot/data/plugin_data/meme_manager/packs"
OUT="/opt/dafeiyu/private/meme_captions.json"
TMP="/tmp/dfy_cap"; os.makedirs(TMP, exist_ok=True)
CATS = ("happy 开心/好耶, smug 得意/嘚瑟/被夸飘了, pout 嘴硬/哼/不服气/傲娇, speechless 无语/问号/懵/没听懂, "
        "shy 害羞/脸红, sad 委屈/哭/可怜/难过, hungry 饿了/馋/要吃的, eating 在吃东西/吃得满足, sleepy 困/要睡了, "
        "lazy 摆烂/躺平/不想动, greet 打招呼/冒泡/我来了, love 贴贴/喜欢/谢谢/安慰对方, shock 震惊/吓到/冒汗, "
        "angry 小生气/抗议（没有武器）, okay 好的/收到/点头, no 拒绝/不要/摇头, tease 坏笑/看戏/调侃, cheer 加油/鼓励/庆祝, skip 都不合适")
PROMPT = ("这是一张聊天用的表情包（动图的话，左边是第一帧、右边是中间一帧）。只输出一个 JSON，不要别的："
          '{"text":"图上所有文字原样照抄，没有就空字符串","scene":"画面一句话","mood":"表达的意思，8个字以内",'
          '"ai_meta":图里文字或画面是否涉及AI、模型、代码、程序、token、DeepSeek、知识库、服务器、算力、开源（true/false）,'
          '"weapon":是否有刀、枪、棍、撬棍、拖鞋、打人这类威胁动作（true/false）,'
          '"specific":图上的字是不是只适合很特定的场合、拿到一般聊天里多半对不上（true/false，没字就是false）,'
          '"cat":"从下面选一个分类键"}\n分类：' + CATS)
def frame(src, dst):
    im = Image.open(src)
    n = getattr(im, "n_frames", 1)
    im.seek(0); a = im.convert("RGBA")
    if n > 2:
        im.seek(n // 2); b = im.convert("RGBA")
        w, h = a.size; c = Image.new("RGBA", (w * 2 + 10, h), (255, 255, 255, 255)); c.paste(a, (0, 0), a); c.paste(b, (w + 10, 0), b); a = c
    bg = Image.new("RGB", a.size, (255, 255, 255)); bg.paste(a, (0, 0), a)
    bg.thumbnail((768, 768)); bg.save(dst)
done = json.load(open(OUT)) if os.path.exists(OUT) else {}
jobs = []
for pack in sorted(os.listdir(PK)):
    for cat in sorted(os.listdir(f"{PK}/{pack}/memes")):
        for f in sorted(os.listdir(f"{PK}/{pack}/memes/{cat}")):
            key = f"{pack}/{cat}/{f}"
            if key not in done: jobs.append((key, f"{PK}/{pack}/memes/{cat}/{f}"))
print("todo", len(jobs), flush=True)
def work(j):
    key, src = j
    dst = f"{TMP}/{abs(hash(key))}.png"
    try:
        frame(src, dst)
        for _ in range(3):
            d = A.call("POST", "/plugins/extensions/astrbot_plugin_dafeiyu_impulse/page/diag/vision", {"path": dst, "prompt": PROMPT})
            t = (d.get("data") or {}).get("text", "") if d.get("ok") else ""
            s, e = t.find("{"), t.rfind("}")
            if s >= 0:
                try: return key, json.loads(t[s:e + 1])
                except Exception: pass
            time.sleep(2)
        return key, {"error": str(d)[:200]}
    except Exception as ex:
        return key, {"error": str(ex)[:200]}
with ThreadPoolExecutor(8) as ex:
    for i, (key, r) in enumerate(ex.map(work, jobs)):
        done[key] = r
        if i % 8 == 0:
            json.dump(done, open(OUT, "w"), ensure_ascii=False); print(i, key, json.dumps(r, ensure_ascii=False)[:150], flush=True)
json.dump(done, open(OUT, "w"), ensure_ascii=False)
os.chmod(OUT, 0o600)
print("DONE", len(done), sum(1 for v in done.values() if "error" in v), "errors", flush=True)
