# 表情包少发、按描述选；群聊合并并发消息、同一人连发等一下。改之前先备份。
import json, shutil, time

C = "/opt/dafeiyu/astrbot/data/config"
ts = int(time.time())


def edit(name, fn):
    p = f"{C}/{name}"
    shutil.copy2(p, f"/opt/dafeiyu/downloads/{name}.before-audit-fix-{ts}")
    d = json.load(open(p, encoding="utf-8-sig"))
    fn(d)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def meme(d):
    g = d["generation"]
    g["emotion"]["probability"] = 35
    g["prompt"]["quantity_guidance"] = (
        "[表情使用频率]\n表情包少用，像真人一样偶尔来一张：大约每 5 次回复最多配 1 次，大多数回复只用文字。"
        "只有开玩笑、得意、嫌弃、委屈、被夸、撒娇这种情绪很明显、又有意思对得上的分类时才配。"
        "正经说事、讲步骤、关心对方、对方情绪低落、深夜说亲密话的时候不配。最近两三次回复里配过图，这次就别配。\n\n"
        "[表情数量偏好]\n要配也只配 1 张。用户主动要表情包时按他的要求来，不受上面限制。"
        "本段只调整选图偏好，沿用当前任务的输出格式，不向用户说明频率或数量判断。")
    g["prompt"]["tail"] = g["prompt"]["tail"].rstrip() + (
        "\n每个分类的描述写了图的意思和图上常见的字，选描述跟你这句话意思对得上的；对不上就不配，宁可不发。\n")


def gcp(d):
    d["concurrent_mode"] = "smart"
    d["enable_smart_batch_reply_hint"] = True
    d["enable_group_wait_window"] = True
    d["group_wait_window_timeout_ms"] = 4000


edit("meme_manager_config.json", meme)
edit("astrbot_plugin_group_chat_plus_config.json", gcp)
print("ok")
