# 用识图标注把两套表情包重新整理成一套「大肥鱼精选」：
# 去掉 AI/代码梗、刀枪撬棍、图上文字只适合特定场合的，剩下的按真实意思细分类。
import json, os, shutil
from collections import defaultdict
from datetime import datetime, timezone

DATA = "/opt/dafeiyu/astrbot/data/plugin_data/meme_manager"
PK = f"{DATA}/packs"
CAP = json.load(open("/opt/dafeiyu/private/meme_captions.json"))
PACK_ID = "dafeiyu-picked"

DESC = {
    "happy": "开心、好耶、高兴得蹦起来",
    "smug": "得意、嘚瑟、被夸了飘起来",
    "pout": "嘴硬、哼、不服气、傲娇别过头",
    "speechless": "无语、问号、懵了、没听懂",
    "shy": "害羞、脸红、不好意思",
    "sad": "委屈、哭唧唧、可怜巴巴",
    "hungry": "饿了、馋了、想要吃的",
    "eating": "正在吃东西、吃得很满足",
    "sleepy": "困了、要睡了、打哈欠",
    "lazy": "摆烂、躺平、不想动",
    "greet": "打招呼、冒泡、我来了",
    "love": "贴贴、喜欢、谢谢、安慰对方",
    "shock": "震惊、吓一跳、冒冷汗",
    "angry": "小生气、鼓着脸抗议（没有武器）",
    "okay": "好的、收到、点头答应",
    "no": "拒绝、不要、摇头",
    "tease": "坏笑、看戏、调侃对方",
    "cheer": "加油、鼓励、庆祝",
}


def keep(v):
    if not isinstance(v, dict) or "error" in v:
        return False
    if v.get("ai_meta") is True or v.get("weapon") is True or v.get("specific") is True:
        return False
    return v.get("cat") in DESC


groups = defaultdict(list)
for key, v in CAP.items():
    if keep(v):
        groups[v["cat"]].append((key, v))
groups = {c: items for c, items in groups.items() if len(items) >= 2}

dst = f"{PK}/{PACK_ID}"
if os.path.exists(dst):
    shutil.move(dst, f"/opt/dafeiyu/downloads/{PACK_ID}-old-{int(datetime.now().timestamp())}")
os.makedirs(f"{dst}/memes")
memes_data, cats = {}, {}
for cat, items in sorted(groups.items()):
    os.makedirs(f"{dst}/memes/{cat}")
    for key, _ in items:
        pack, c0, f = key.split("/", 2)
        src = f"{PK}/{pack}/memes/{c0}/{f}"
        out = f"{dst}/memes/{cat}/{pack.split('-')[0]}_{f}"
        try:
            os.link(src, out)
        except OSError:
            shutil.copy2(src, out)
    words = []
    for _, v in items:
        t = (v.get("text") or "").strip()
        if t and t not in words and len(t) <= 8:
            words.append(t)
    desc = f"用于{DESC[cat]}。" + (f"图上常见的字：{'、'.join(words[:4])}。" if words else "图上基本没字。")
    memes_data[cat] = desc
    cats[cat] = {"description": desc}

json.dump(memes_data, open(f"{dst}/memes_data.json", "w"), ensure_ascii=False, indent=2)
json.dump({"schema_version": 1, "id": PACK_ID, "name": "大肥鱼精选", "version": "1.0.0",
           "description": "从蓝色大肥鱼、DeepSeek 二创两套里挑的：去掉 AI 梗、武器、只适合特定场合的图，按真实意思重新分类。",
           "user_created": True, "categories": cats},
          open(f"{dst}/manifest.json", "w"), ensure_ascii=False, indent=2)

reg_p = f"{DATA}/registry.json"
reg = json.load(open(reg_p))
reg["installed_packs"] = [p for p in reg["installed_packs"] if p.get("id") != PACK_ID]
reg["installed_packs"].append({"id": PACK_ID, "name": "大肥鱼精选", "version": "1.0.0", "enabled": True,
                               "installed_at": datetime.now(timezone.utc).isoformat()})
json.dump(reg, open(reg_p, "w"), ensure_ascii=False, indent=2)

rules_p = f"{DATA}/selection_rules.json"
shutil.copy2(rules_p, "/opt/dafeiyu/downloads/meme-selection_rules-before-picked.json")
rules = json.load(open(rules_p))
for r in rules.get("rules", []):
    if r.get("id") == "default":
        r["pack_id"] = PACK_ID
        r["pack_ids"] = [PACK_ID]
json.dump(rules, open(rules_p, "w"), ensure_ascii=False, indent=2)

total = sum(len(v) for v in CAP.values())
kept = sum(len(v) for v in groups.values())
why = defaultdict(int)
for v in CAP.values():
    if not isinstance(v, dict) or "error" in v: why["识图失败"] += 1
    elif v.get("ai_meta") is True: why["AI/代码梗"] += 1
    elif v.get("weapon") is True: why["刀枪撬棍"] += 1
    elif v.get("specific") is True: why["图上字太特定"] += 1
    elif v.get("cat") not in DESC: why["没合适分类"] += 1
print(f"共 {total} 张，留下 {kept} 张，{len(groups)} 类")
print("去掉：", dict(why))
for c, items in sorted(groups.items()):
    print(f"  {c} {len(items)}：{memes_data[c][:60]}")
