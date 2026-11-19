"""把大肥鱼的部署方法导出成一个可公开的 GitHub 仓库目录（脱敏）。可反复运行。

用法：python3 export_repo.py [输出目录，默认 /opt/dafeiyu/github/dafeiyu]
- 自写插件（不含 grok 订阅登录插件）、第三方插件补丁、systemd 单元、人设、配置样例（密钥全换成占位）。
- 文档（README.md / docs/*.md）放在 /opt/dafeiyu/github/templates 下，原样复制，统计段由 update_stats.py 填。
- 最后做一遍泄漏扫描：发现密钥、QQ 号、IP、群名、群友昵称就报错退出，不留半成品。
"""
import glob, json, os, re, shutil, sqlite3, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else '/opt/dafeiyu/github/dafeiyu'
D = '/opt/dafeiyu/astrbot/data/'
P = D + 'plugins/'
PD = D + 'plugin_data/'
KIT = '/opt/dafeiyu/kit/'
TPL = '/opt/dafeiyu/github/templates/'

EXCLUDE_PLUGINS = {'astrbot_plugin_dafeiyu_grok'}  # 订阅非官方登录，不公开
KIT_FILES = ['patch_bili_share.py', 'patch_bili_freetalk.py', 'patch_kugou_direct.py', 'patch_twikit.py',
             'fix_twikit_schema.py', 'qq_proxy.sh', 'caption_memes.py', 'build_picked_pack.py', 'tune_configs.py']
UNITS = ['dafeiyu-astrbot.service', 'dafeiyu-napcat.service', 'dafeiyu-mihomo.service']

# ---------- 要抹掉的私人信息 ----------
owner = ''
try:
    owner = json.load(open(D + 'config/astrbot_plugin_bili_scroll_config.json', encoding='utf-8-sig'))['OWNER_QQ_UMO'].rsplit(':', 1)[-1]
except Exception:
    pass
bot = open('/opt/dafeiyu/private/bot-qq.txt').read().strip() if os.path.exists('/opt/dafeiyu/private/bot-qq.txt') else ''
REPL = [(r'100\.102\.136\.88', '127.0.0.1'), (r'43\.142\.97\.15', '<SERVER_IP>'), (r'38\.76\.188\.50', '<SERVER_IP>'),
        (r'your-server', 'your-server'), (r'tail[0-9a-f]{6}\.ts\.net', 'your-tailnet.ts.net'), (r'主人', '主人'),
        (r'本机 Responses 接口', '本机 Responses 接口')]
for q in (owner, bot):
    if q:
        REPL.append((re.escape(q), ''))
kb = sqlite3.connect(PD + 'astrbot_plugin_dafeiyu_kb/kb.db')
gnames = sorted({r[0] for r in kb.execute('select distinct gname from raw') if r[0] and len(r[0]) >= 2}, key=len, reverse=True)
for g in gnames:
    REPL.append((re.escape(g), '某群'))
hearts = json.load(open(PD + 'astrbot_plugin_dafeiyu_heart/hearts.json'))
member_names = sorted({u.get('name') for u in hearts['users'].values() if u.get('name')}, key=len, reverse=True)
# 只抹有辨识度的昵称：含中文且 ≥2 字，或 ≥5 个字符；常见词不抹
COMMON = {'主人', '大肥鱼', '群友', 'stars', 'admin', 'user', '管理员'}
risky_names = [n for n in member_names if n not in COMMON and not n.isdigit()
               and ((re.search(r'[一-鿿]', n) and len(n) >= 2) or len(n) >= 5)]

TEXT_EXT = ('.py', '.json', '.yaml', '.yml', '.md', '.txt', '.sh', '.service', '.conf', '.html', '.js', '.css', '.toml', '.cfg')


def scrub(text):
    for p, r in REPL:
        text = re.sub(p, r, text)
    for n in risky_names:
        if n in text:
            text = text.replace(n, '某群友')
    return text


def copy_text(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if src.endswith(TEXT_EXT):
        with open(src, encoding='utf-8', errors='ignore') as f:
            s = f.read()
        with open(dst, 'w', encoding='utf-8') as f:
            f.write(scrub(s))
    else:
        shutil.copy2(src, dst)


SECRET_KEY = re.compile(r'(key|secret|token|passw|cookie|bduss|sessdata|stoken|csrf|auth|credential|phone|mobile|imei|ck$|jwt|umo|_qq|qq_|owner|admin|whitelist|blacklist|group_id|gid|uid|uin|room|vapid|seed)', re.I)


def scrub_config(o, path=''):
    """配置样例：敏感字段换占位，带 6 位以上数字的字符串（QQ 号、群号）也换掉。"""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if SECRET_KEY.search(str(k)) and not isinstance(v, (bool, dict)):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[k] = v if abs(v) < 100000 else 0
                else:
                    out[k] = '' if not isinstance(v, list) else []
            else:
                out[k] = scrub_config(v, path + '.' + str(k))
        return out
    if isinstance(o, list):
        return [scrub_config(x, path) for x in o]
    if isinstance(o, str):
        s = scrub(o)
        if re.search(r'\d{6,}', s) or re.search(r'[A-Za-z0-9+/=_-]{40,}', s):
            return '<FILL_ME>'
        return s
    if isinstance(o, int) and not isinstance(o, bool) and o > 99999999:
        return 0
    return o


# ---------- 开始导出 ----------
if os.path.isdir(OUT):
    for name in os.listdir(OUT):
        if name in ('.git',):
            continue
        p = os.path.join(OUT, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
os.makedirs(OUT, exist_ok=True)

# 1) 自写插件
n_plugins = 0
for d in sorted(glob.glob(P + 'astrbot_plugin_dafeiyu_*')):
    name = os.path.basename(d)
    if name in EXCLUDE_PLUGINS:
        continue
    for f in glob.glob(d + '/**/*', recursive=True):
        rel = os.path.relpath(f, d)
        if (not os.path.isfile(f) or '__pycache__' in f or '.bak' in rel or rel.endswith(('.pyc', '.log'))
                or rel.split(os.sep)[0] in ('data', 'tmp', 'cache')):
            continue
        copy_text(f, os.path.join(OUT, 'plugins', name, rel))
    n_plugins += 1

# 2) 第三方插件的补丁 / 工具脚本
for f in KIT_FILES:
    if os.path.exists(KIT + f):
        copy_text(KIT + f, os.path.join(OUT, 'patches' if f.startswith(('patch_', 'fix_')) else 'tools', f))

# 3) systemd 单元和启动脚本
for u in UNITS:
    src = '/etc/systemd/system/' + u
    if os.path.exists(src):
        copy_text(src, os.path.join(OUT, 'deploy', 'systemd', u))
    dropin = src + '.d'
    if os.path.isdir(dropin):
        for f in glob.glob(dropin + '/*'):
            copy_text(f, os.path.join(OUT, 'deploy', 'systemd', u + '.d', os.path.basename(f)))
if os.path.exists('/opt/dafeiyu/start-napcat.sh'):
    copy_text('/opt/dafeiyu/start-napcat.sh', os.path.join(OUT, 'deploy', 'start-napcat.sh'))

# 4) 人设
db = sqlite3.connect(D + 'data_v4.db')
row = db.execute("select system_prompt from personas where persona_id='大肥鱼'").fetchone()
if row:
    raw_p = row[0] or ''
    if owner:
        raw_p = raw_p.replace(owner, '<主人QQ号>')
    persona = re.sub(r'\d{6,}', '<QQ号>', scrub(raw_p))
    os.makedirs(os.path.join(OUT, 'deploy'), exist_ok=True)
    open(os.path.join(OUT, 'deploy', 'persona.md'), 'w', encoding='utf-8').write(
        '# 大肥鱼的人设（AstrBot → 人格 → 新建「大肥鱼」，把下面整段贴进系统提示）\n\n```text\n' + persona.strip() + '\n```\n')

# 5) 配置样例（只留结构和我们调过的值，密钥/账号/群号全换占位）
cfg = json.load(open(D + 'cmd_config.json', encoding='utf-8-sig'))
cfg['provider'] = [p for p in cfg.get('provider', []) if not str(p.get('id', '')).startswith(('chatgpt/', 'grok/'))]
cfg['provider_sources'] = [s for s in cfg.get('provider_sources', []) if s.get('id') not in ('chatgpt', 'grok')]
try:
    cfg['agent_runner']['config']['model']['provider_id'] = 'deepseek/deepseek-flash'
except Exception:
    pass
for k in ('default_image_caption_provider_id',):
    cfg.get('provider_settings', {})[k] = ''
os.makedirs(os.path.join(OUT, 'deploy', 'config-examples'), exist_ok=True)
json.dump(scrub_config(cfg), open(os.path.join(OUT, 'deploy', 'config-examples', 'cmd_config.example.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2)
for f in sorted(glob.glob(D + 'config/astrbot_plugin_*_config.json')):
    name = os.path.basename(f)
    if 'dafeiyu_grok' in name:
        continue
    try:
        j = json.load(open(f, encoding='utf-8-sig'))
    except Exception:
        continue
    j = scrub_config(j)
    for k, v in list(j.items()):  # 指向非公开模型的配置项改成空，让人自己填
        if isinstance(v, str) and v.startswith(('chatgpt/', 'grok/')):
            j[k] = ''
    json.dump(j, open(os.path.join(OUT, 'deploy', 'config-examples', name.replace('.json', '.example.json')), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

# 6) 插件清单（从各插件 metadata.yaml 生成）
def meta(d):
    m = {}
    try:
        for line in open(os.path.join(d, 'metadata.yaml'), encoding='utf-8'):
            mm = re.match(r'^(\w+):\s*(.*?)\s*(#.*)?$', line)
            if mm:
                m[mm.group(1)] = mm.group(2).strip('"\'')
    except Exception:
        pass
    return m


rows_self, rows_third = [], []
for d in sorted(glob.glob(P + '*/')):
    name = os.path.basename(d.rstrip('/'))
    m = meta(d)
    desc = scrub(m.get('desc', '')).replace('|', '/').strip()
    if name.startswith('astrbot_plugin_dafeiyu_'):
        if name not in EXCLUDE_PLUGINS:
            rows_self.append(f"| [`{name}`](../plugins/{name}) | {m.get('version', '')} | {desc[:120]} |")
    else:
        repo = m.get('repo', '')
        rows_third.append(f"| [{m.get('display_name') or name}]({repo}) | `{name}` | {m.get('version', '')} | {m.get('author', '')} | {desc[:90]} |")
os.makedirs(os.path.join(OUT, 'docs'), exist_ok=True)
with open(os.path.join(OUT, 'docs', 'plugins.md'), 'w', encoding='utf-8') as f:
    f.write('# 插件清单\n\n> 这份清单由 `scripts/export_repo.py` 从服务器上实际装着的插件自动生成。\n\n')
    f.write('## 自写插件（代码在 `plugins/`）\n\n| 插件 | 版本 | 干什么 |\n|---|---|---|\n' + '\n'.join(rows_self) + '\n\n')
    f.write('## 第三方插件（在 AstrBot 插件市场安装，代码不在本仓库）\n\n| 插件 | 目录名 | 我们用的版本 | 作者 | 说明 |\n|---|---|---|---|---|\n'
            + '\n'.join(rows_third) + '\n\n')
    f.write('其中几个第三方插件我们打了本地补丁，脚本在 `patches/`，**插件升级后要重跑一遍**：\n\n'
            '| 补丁 | 改了什么 |\n|---|---|\n'
            '| `patch_bili_share.py` | B站插件：分享视频给主人的方式 |\n'
            '| `patch_bili_freetalk.py` | B站插件：私信/评论放开，交给她自己判断 |\n'
            '| `patch_kugou_direct.py` | 更好的音乐：酷狗 node 接口被拦（20028），改成 Python 直连取歌曲地址 |\n'
            '| `patch_twikit.py` / `fix_twikit_schema.py` | twikit：适配推特接口的字段变化 |\n')

# 7) 文档模板、统计脚本
if os.path.isdir(TPL):
    for f in glob.glob(TPL + '**/*', recursive=True):
        if os.path.isfile(f):
            copy_text(f, os.path.join(OUT, os.path.relpath(f, TPL)))
for f in ('update_stats.py', 'export_repo.py'):
    src = '/opt/dafeiyu/github/' + f
    if os.path.exists(src):
        copy_text(src, os.path.join(OUT, 'scripts', f))

# ---------- 泄漏扫描 ----------
LEAK = [
    (r'sk-[A-Za-z0-9]{16,}', 'API key'), (r'eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{10,}', 'JWT'), (r'LTAI[0-9A-Za-z]{10,}', '阿里云 key'),
    (r'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}', 'GitHub token'), (r'xai-[A-Za-z0-9]{20,}', 'xAI key'),
    (r'rt\.1\.[A-Za-z0-9_-]{20,}', 'refresh token'), (r'BDUSS\s*[=:]\s*["\']?[A-Za-z0-9~-]{40,}', 'BDUSS'),
    (r'SESSDATA\s*[=:]\s*["\']?[A-Za-z0-9%,*]{20,}', 'SESSDATA'), (r'auth_token["\']?\s*[=:]\s*["\']?[0-9a-f]{30,}', 'X auth_token'),
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----', '私钥'), (r'(?<!\d)1[3-9]\d{9}(?!\d)', '手机号'),
    (r'100\.(?!64\.0\.0\b)\d{1,3}\.\d{1,3}\.\d{1,3}', 'Tailscale IP'),  # 100.64.0.0/10 是通用网段，不算 (r'43\.142\.97\.15|38\.76\.188\.50', '服务器 IP'),
    (r'your-server|tail[0-9a-f]{6}\.ts\.net', '主机名'),
]
private_literals = [x for x in (owner, bot) if x] + gnames + risky_names
problems = []
for f in glob.glob(OUT + '/**/*', recursive=True):
    if not os.path.isfile(f) or '/.git/' in f or not f.endswith(TEXT_EXT):
        continue
    s = open(f, encoding='utf-8', errors='ignore').read()
    for p, what in LEAK:
        m = re.search(p, s)
        if m:
            problems.append(f'{os.path.relpath(f, OUT)}: {what}（{m.group(0)[:6]}…）')
    for lit in private_literals:
        if lit and lit in s:
            problems.append(f'{os.path.relpath(f, OUT)}: 私人信息「{lit[:2]}…」')
total = sum(1 for f in glob.glob(OUT + '/**/*', recursive=True) if os.path.isfile(f) and '/.git/' not in f)
print(f'导出 {n_plugins} 个自写插件，共 {total} 个文件 → {OUT}')
if problems:
    print('泄漏扫描没通过：')
    for p in problems[:60]:
        print('  -', p)
    sys.exit(2)
print('泄漏扫描通过')
