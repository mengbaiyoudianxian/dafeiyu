"""每小时算一次大肥鱼的状态和统计，写进仓库的 stats/stats.json 和 README 的统计段，有变化就提交并推到 GitHub。

只输出聚合数字和她自己的状态，不输出任何群名、群友名字、聊天内容。
用法：python3 update_stats.py [仓库目录，默认 /opt/dafeiyu/github/dafeiyu] [--no-push]
"""
import json, os, re, sqlite3, subprocess, sys, time
from datetime import datetime

REPO = next((a for a in sys.argv[1:] if not a.startswith('--')), '/opt/dafeiyu/github/dafeiyu')
PUSH = '--no-push' not in sys.argv
D = '/opt/dafeiyu/astrbot/data/'
PD = D + 'plugin_data/'
PROBE = '/opt/dafeiyu/private/claude_relay/probe/'


def napcat(action, params=None, timeout=45):
    """通过巡检中转插件的 probe 目录调 NapCat（插件每 15 秒扫一次）。"""
    try:
        os.makedirs(PROBE, exist_ok=True)
        name = 'stats_%s_%d' % (action, int(time.time() * 1000))
        with open(PROBE + name + '.json', 'w') as f:
            json.dump({'action': action, 'params': params or {}}, f)
        out = PROBE + name + '.out'
        t = time.time()
        while time.time() - t < timeout:
            if os.path.exists(out):
                r = json.load(open(out))
                os.remove(out)
                return r.get('data') if r.get('ok') else None
            time.sleep(2)
        if os.path.exists(PROBE + name + '.json'):
            os.remove(PROBE + name + '.json')
    except Exception:
        pass
    return None


def jload(p, default=None):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def svc(name):
    return subprocess.run(['systemctl', 'is-active', name], capture_output=True, text=True).stdout.strip() == 'active'


s = {'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M')}

# ---- 在线状态 ----
groups = napcat('get_group_list')
friends = napcat('get_friend_list')
s['online'] = bool(svc('dafeiyu-astrbot') and svc('dafeiyu-napcat') and groups is not None)
s['groups_joined'] = len(groups) if isinstance(groups, list) else None
s['friends'] = len(friends) if isinstance(friends, list) else None

# ---- 心情 / 手上的事 ----
h = jload(PD + 'astrbot_plugin_dafeiyu_heart/hearts.json', {}) or {}
EMO = {'joy': '高兴', 'anger': '生气', 'sad': '委屈', 'shy': '害羞', 'lonely': '孤独', 'bored': '无聊',
       'jealous': '吃醋', 'proud': '得意', 'worry': '担心', 'annoyed': '烦躁'}
emo = (h.get('self') or {}).get('emo') or {}
top = sorted(((k, v) for k, v in emo.items() if k in EMO), key=lambda kv: -kv[1])[:3]
s['mood'] = '、'.join('%s %d' % (EMO[k], round(v)) for k, v in top)
s['energy'] = round((h.get('self') or {}).get('energy') or 0)
act = (h.get('self') or {}).get('act') or {}
_d = re.sub(r'\s+', ' ', str(act.get('doing') or '')).strip()
if len(_d) > 60:
    # 在 60 字以内最后一个标点处截断，不在半句话中间切
    _cut = max(_d.rfind(c, 0, 60) for c in '，。！？；,.!?;')
    if _cut >= 15:
        _d = _d[:_cut + 1] if _d[_cut] in '。！？.!?' else _d[:_cut] + '…'
    else:
        _d = _d[:60] + '…'
s['doing'] = _d
s['doing_feel'] = str(act.get('feel') or '')[:20]

# ---- 认识多少人 ----
s['people_known'] = len(h.get('users') or {})  # 跟她有过来往、有好感数据的人
kb_path = PD + 'astrbot_plugin_dafeiyu_kb/kb.db'
try:
    kb = sqlite3.connect('file:%s?mode=ro' % kb_path, uri=True)
    kinds = dict(kb.execute('select kind, count(*) from items group by kind').fetchall())
    s['impressions'] = kb.execute("select count(distinct person) from items where kind='群友' and person!=''").fetchone()[0]
    s['groups_chatted'] = kb.execute('select count(distinct gid) from raw').fetchone()[0]
    s['group_messages_remembered'] = kb.execute('select count(*) from raw').fetchone()[0]
    s['her_group_messages'] = kb.execute('select count(*) from raw where self=1').fetchone()[0]
    s['memes_learned'] = kinds.get('梗', 0)
    s['sayings_learned'] = kinds.get('说法', 0)
    s['topics_events'] = kinds.get('话题', 0) + kinds.get('事件', 0)
    s['stickers_collected'] = kinds.get('表情包', 0)
except Exception:
    kinds = {}

# ---- 刷网 ----
bili = PD + 'astrbot_plugin_bili_scroll/bilibot.sqlite3'
try:
    b = sqlite3.connect('file:%s?mode=ro' % bili, uri=True)
    s['bili_videos_watched'] = b.execute('select count(*) from seen_videos').fetchone()[0]
    s['bili_actions'] = b.execute('select count(*) from actions').fetchone()[0]
except Exception:
    pass
x = jload(PD + 'astrbot_plugin_dafeiyu_x/state.json', {}) or {}
s['tweets_seen'] = len(x.get('seen') or [])
s['x_following'] = len(x.get('followed') or [])
th = jload(PD + 'astrbot_plugin_dafeiyu_tieba/history.json', []) or []
td = jload(PD + 'astrbot_plugin_dafeiyu_tieba/diary.json', []) or []
s['tieba_posts_read'] = len(td)
s['tieba_actions'] = len(th)

# ---- token ----
try:
    db = sqlite3.connect('file:%sdata_v4.db?mode=ro' % D, uri=True)
    row = db.execute('select count(*), coalesce(sum(token_input_other),0), coalesce(sum(token_input_cached),0), '
                     'coalesce(sum(token_output),0), min(created_at) from provider_stats').fetchone()
    s['llm_calls'] = row[0]
    s['tokens_input'] = row[1] + row[2]
    s['tokens_cached'] = row[2]
    s['tokens_output'] = row[3]
    s['tokens_total'] = row[1] + row[2] + row[3]
    first = row[4]
except Exception:
    first = None
born = datetime(2026, 11, 10)
if first:
    try:
        born = min(born, datetime.fromisoformat(str(first)[:19]))
    except Exception:
        pass
s['since'] = born.strftime('%Y-%m-%d')
s['days_alive'] = (datetime.now() - born).days + 1


def n(v):
    if v is None:
        return '—'
    if isinstance(v, int) and v >= 10000:
        return '{:,}'.format(v)
    return str(v)


def wan(v):
    if not v:
        return '—'
    return ('%.1f 万' % (v / 1e4)) if v < 1e8 else ('%.2f 亿' % (v / 1e8))


status = '🟢 在线' if s['online'] else '🔴 离线'
doing = s['doing'] + ('（%s）' % s['doing_feel'] if s['doing_feel'] else '') if s['doing'] else '—'
block = f"""## 她现在怎么样

> 每小时自动更新 · 最近一次：{s['updated_at']}（北京时间）

| | |
|---|---|
| 状态 | {status} |
| 此刻心情 | {s['mood'] or '—'} · 精力 {s['energy']} |
| 手上在干嘛 | {doing} |
| 来到 QQ | {s['since']} 起，第 {s['days_alive']} 天 |

| 社交 | |
|---|---|
| 加入的群 | {n(s.get('groups_joined'))} 个（在 {n(s.get('groups_chatted'))} 个群里说过话） |
| QQ 好友 | {n(s.get('friends'))} 个 |
| 打过交道的人 | {n(s.get('people_known'))} 个 |
| 有印象的群友 | {n(s.get('impressions'))} 个 |
| 记住的群消息 | {n(s.get('group_messages_remembered'))} 条（她自己说的 {n(s.get('her_group_messages'))} 条） |
| 学会的梗 / 说法 | {n(s.get('memes_learned'))} 个梗 · {n(s.get('sayings_learned'))} 种说法 |
| 收藏的表情包 | {n(s.get('stickers_collected'))} 张 |

| 刷网 | |
|---|---|
| 贴吧 | 看过 {n(s.get('tieba_posts_read'))} 个帖子 · 互动 {n(s.get('tieba_actions'))} 次 |
| B 站 | 看过 {n(s.get('bili_videos_watched'))} 个视频 · 互动 {n(s.get('bili_actions'))} 次 |
| 推特 | 刷过 {n(s.get('tweets_seen'))} 条推文 · 关注 {n(s.get('x_following'))} 人 |

| 消耗 | |
|---|---|
| 对话调用模型 | {n(s.get('llm_calls'))} 次 |
| 累计 token | {wan(s.get('tokens_total'))}（输入 {wan(s.get('tokens_input'))}，其中缓存命中 {wan(s.get('tokens_cached'))}；输出 {wan(s.get('tokens_output'))}） |

<sub>token 只统计聊天主流程（AstrBot 自带统计），后台整理记忆、刷网、心情判断这些额外调用没算进去。</sub>"""

os.makedirs(os.path.join(REPO, 'stats'), exist_ok=True)
with open(os.path.join(REPO, 'stats', 'stats.json'), 'w', encoding='utf-8') as f:
    json.dump(s, f, ensure_ascii=False, indent=2)
rp = os.path.join(REPO, 'README.md')
if os.path.exists(rp):
    r = open(rp, encoding='utf-8').read()
    r2 = re.sub(r'<!-- STATS:START -->.*?<!-- STATS:END -->', '<!-- STATS:START -->\n' + block + '\n<!-- STATS:END -->', r, flags=re.S)
    if r2 != r:
        open(rp, 'w', encoding='utf-8').write(r2)
print(json.dumps(s, ensure_ascii=False))

# ---- 提交并推送 ----
if os.path.isdir(os.path.join(REPO, '.git')):
    def git(*a):
        return subprocess.run(['git', '-C', REPO] + list(a), capture_output=True, text=True)
    git('add', 'README.md', 'stats/stats.json')
    if git('diff', '--cached', '--quiet').returncode != 0:
        git('commit', '-m', '更新统计 ' + s['updated_at'])
        if PUSH and git('remote').stdout.strip():
            r = git('push', '-q', 'origin', 'HEAD')
            print('push:', 'ok' if r.returncode == 0 else r.stderr.strip()[:200])
