"""Local bridge between the configured Codex account and AstrBot."""
import asyncio, base64, glob, hashlib, json, logging, os, re, time, uuid
from aiohttp import ClientSession, ClientTimeout, web

PRIV = '/opt/dafeiyu/private'
PREF = PRIV + '/codex-bridge-state.json'  # {"preferred": "账号名"}
BASE = 'https://chatgpt.com/backend-api/codex'
TOKEN_URL = 'https://auth.openai.com/oauth/token'
CLIENT_ID = os.environ["CODEX_OAUTH_CLIENT_ID"]
CLIENT_VERSION = '1.0.0'
DEFAULT_EFFORT = 'low'
PLAN_NAMES = {'plus': 'Plus', 'pro': 'Pro', 'prolite': 'Pro Lite', 'pro_lite': 'Pro Lite', 'team': 'Team',
              'business': 'Business', 'enterprise': 'Enterprise', 'free': 'Free', 'go': 'Go'}
log = logging.getLogger('codex-bridge')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# 多个订阅号：/opt/dafeiyu/private/codex-auth*.json，每个文件一个号。一个号额度用完就自动换下一个，等它重置再用。
state = {'session': None, 'accounts': {}}


class Acct:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)[:-5].replace('codex-auth', 'main').replace('main-', 'acct')
        self.lock = asyncio.Lock()
        self.auth = None
        self.limited_until = 0
        self.last_error = ''
        self.last_ok = 0
        self.ok_count = 0
        self.rate = {}
        self.load()

    def load(self):
        with open(self.path) as f:
            self.auth = json.load(f)
        return self.auth

    def save(self, auth):
        tmp = self.path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(auth, f)
        os.replace(tmp, self.path)
        self.auth = auth

    def claims(self):
        try:
            p = self.auth['tokens']['id_token'].split('.')[1]
            c = json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4)))
        except Exception:
            return {}, {}
        return c, c.get('https://api.openai.com/auth') or {}

    def info(self):
        c, a = self.claims()
        email = str(c.get('email') or '')
        plan = str(a.get('chatgpt_plan_type') or '')
        return {'name': self.name, 'email': (email[:3] + '***@' + email.split('@')[-1]) if '@' in email else '',
                'plan': PLAN_NAMES.get(plan, plan or '未知'), 'plan_raw': plan,
                'subscription_until': a.get('chatgpt_subscription_active_until') or '',
                'subscription_checked': a.get('chatgpt_subscription_last_checked') or '',
                'limited': self.limited_until > time.time(),
                'limit_resets_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(self.limited_until)) if self.limited_until > time.time() else '',
                'last_error': self.last_error[:200], 'ok_count': self.ok_count,
                'last_ok': time.strftime('%m-%d %H:%M', time.localtime(self.last_ok)) if self.last_ok else '',
                'rate_limits': self.rate}


def scan():
    for p in sorted(glob.glob(PRIV + '/codex-auth*.json'), key=lambda p: (p != PRIV + '/codex-auth.json', p)):
        if p.endswith('.tmp') or p in state['accounts']:
            continue
        try:
            state['accounts'][p] = Acct(p)
            log.info('loaded account %s', state['accounts'][p].name)
        except Exception as e:
            log.error('load %s: %s', os.path.basename(p), e)
    return list(state['accounts'].values())


def preferred():
    try:
        return json.load(open(PREF)).get('preferred') or ''
    except Exception:
        return ''


def order():
    accts = scan()
    pref = preferred()
    accts.sort(key=lambda a: (a.name != pref,))
    return accts


def exp_of(token):
    try:
        p = token.split('.')[1]
        return float(json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4))).get('exp') or 0)
    except Exception:
        return 0


async def refresh(acct, reason):
    async with acct.lock:
        auth = acct.load()
        tok = auth['tokens']
        body = {'client_id': CLIENT_ID, 'grant_type': 'refresh_token', 'refresh_token': tok['refresh_token'],
                'scope': 'openid profile email'}
        async with state['session'].post(TOKEN_URL, json=body, timeout=ClientTimeout(total=60)) as r:
            text = await r.text()
            if r.status != 200:
                log.error('refresh failed %s (%s): HTTP %s %s', acct.name, reason, r.status, text[:200])
                acct.last_error = f'续期失败 HTTP {r.status}'
                return False
            j = json.loads(text)
        for k in ('access_token', 'refresh_token', 'id_token'):
            if j.get(k):
                tok[k] = j[k]
        auth['last_refresh'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        acct.save(auth)
        log.info('token refreshed %s (%s), valid %.1f days', acct.name, reason, (exp_of(tok['access_token']) - time.time()) / 86400)
        return True


async def ensure_fresh(acct):
    if exp_of(acct.auth['tokens']['access_token']) - time.time() < 2 * 86400:
        await refresh(acct, 'near expiry')


def headers(acct):
    t = acct.auth['tokens']
    return {'Authorization': 'Bearer ' + t['access_token'], 'chatgpt-account-id': t['account_id'],
            'OpenAI-Beta': 'responses=experimental', 'originator': 'codex_cli_rs', 'session_id': str(uuid.uuid4()),
            'User-Agent': 'codex_cli_rs/' + CLIENT_VERSION, 'version': CLIENT_VERSION,
            'Accept': 'text/event-stream', 'Content-Type': 'application/json'}


# ---------- Chat Completions -> Responses ----------

def text_of(content):
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    return ''.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') in ('text', 'input_text', 'output_text'))


def user_parts(content):
    if isinstance(content, str):
        return [{'type': 'input_text', 'text': content}]
    parts = []
    for p in content or []:
        if not isinstance(p, dict):
            continue
        t = p.get('type')
        if t in ('text', 'input_text'):
            parts.append({'type': 'input_text', 'text': p.get('text', '')})
        elif t == 'image_url':
            url = p.get('image_url')
            url = url.get('url') if isinstance(url, dict) else url
            if url:
                parts.append({'type': 'input_image', 'image_url': url})
        elif t == 'input_image':
            parts.append(p)
    return parts or [{'type': 'input_text', 'text': ''}]


def convert(req):
    model = str(req.get('model') or 'gpt-6.2-sol')
    effort = DEFAULT_EFFORT
    if '@' in model:
        model, effort = model.split('@', 1)
    instructions, items = [], []
    for m in req.get('messages') or []:
        role = m.get('role')
        if role in ('system', 'developer'):
            instructions.append(text_of(m.get('content')))
        elif role == 'user':
            items.append({'type': 'message', 'role': 'user', 'content': user_parts(m.get('content'))})
        elif role == 'assistant':
            txt = text_of(m.get('content'))
            if txt:
                items.append({'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': txt}]})
            for tc in m.get('tool_calls') or []:
                fn = tc.get('function') or {}
                items.append({'type': 'function_call', 'call_id': tc.get('id') or 'call_' + uuid.uuid4().hex[:12],
                              'name': fn.get('name', ''), 'arguments': fn.get('arguments') or '{}'})
        elif role == 'tool':
            out = m.get('content')
            items.append({'type': 'function_call_output', 'call_id': m.get('tool_call_id', ''),
                          'output': out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)})
    tools = []
    for t in req.get('tools') or []:
        fn = t.get('function') if t.get('type') == 'function' else None
        if fn:
            tools.append({'type': 'function', 'name': fn.get('name'), 'description': fn.get('description', ''),
                          'parameters': fn.get('parameters') or {'type': 'object', 'properties': {}}, 'strict': False})
    choice = req.get('tool_choice') or 'auto'
    if isinstance(choice, dict) and choice.get('function'):
        choice = {'type': 'function', 'name': choice['function'].get('name')}
    instr = '\n\n'.join(i for i in instructions if i) or 'You are a helpful assistant.'
    body = {'model': model, 'instructions': instr, 'input': items, 'tools': tools,
            'tool_choice': choice if tools else 'auto', 'parallel_tool_calls': bool(tools) and req.get('parallel_tool_calls', True),
            'store': False, 'stream': True, 'include': [], 'reasoning': {'effort': effort},
            'prompt_cache_key': hashlib.sha256(instr.encode()).hexdigest()[:32]}
    return body


def note_rate(acct, r):
    """记下上游回的额度头（用了百分之几、多久后重置）"""
    rl = {k[len('x-codex-'):].lower(): v for k, v in r.headers.items()
          if k.lower().startswith('x-codex-') and 'turn-state' not in k.lower() and len(v) < 80}
    if rl:
        acct.rate = rl


async def upstream(body):
    """按顺序试每个订阅号：额度用完（429）就记下重置时间、换下一个。返回 (status, final_or_error)"""
    last = (503, 'no subscription account available')
    accts = order()
    live = [a for a in accts if a.limited_until <= time.time()] or accts  # 全都限额时还是试一遍（可能已提前重置）
    for acct in live:
        status, res = await upstream_one(acct, body)
        if status == 200:
            acct.ok_count += 1
            acct.last_ok = time.time()
            acct.last_error = ''
            return status, res
        acct.last_error = f'HTTP {status} ' + str(res)[:160]
        if status == 429 or (status == 401 and 'unauthorized after refresh' in str(res)):
            m = re.search(r'resets_at"?\s*:\s*(\d{9,})', str(res))
            acct.limited_until = float(m.group(1)) if m else time.time() + 1800
            log.warning('account %s limited until %s, trying next', acct.name,
                        time.strftime('%m-%d %H:%M', time.localtime(acct.limited_until)))
            last = (status, res)
            continue
        return status, res
    return last


async def upstream_one(acct, body):
    for attempt in (1, 2):
        await ensure_fresh(acct)
        async with state['session'].post(BASE + '/responses', json=body, headers=headers(acct),
                                         timeout=ClientTimeout(total=3600, sock_read=600)) as r:
            note_rate(acct, r)
            if r.status == 401 and attempt == 1:
                await refresh(acct, '401 from upstream')
                continue
            if r.status != 200:
                return r.status, (await r.text())[:2000]
            final, items, buf = None, [], b''
            # 画图结果是一整行几 MB 的 base64，不能用 aiohttp 的按行读（有 64KB 行长上限），自己分块切行
            async for chunk in r.content.iter_chunked(1 << 16):
                buf += chunk
                while final is None and b'\n' in buf:
                    rawline, buf = buf.split(b'\n', 1)
                    line = rawline.decode('utf-8', 'ignore').strip()
                    if not line.startswith('data:'):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except ValueError:
                        continue
                    if ev.get('type') == 'response.output_item.done' and ev.get('item'):
                        # 有时 response.completed 里的 output 是空的，结果只在流里给：自己收集
                        items.append(ev['item'])
                    if ev.get('type') in ('response.completed', 'response.incomplete', 'response.failed'):
                        final = ev.get('response') or {}
                        if ev['type'] == 'response.failed':
                            return 502, json.dumps(final.get('error') or {'message': 'response.failed'}, ensure_ascii=False)
                if final is not None:
                    break
            if final is None:
                return 502, 'stream ended without response.completed'
            if not final.get('output'):
                final['output'] = items
            return 200, final
    return 401, 'unauthorized after refresh'


def to_chat(final, model):
    text, calls = [], []
    for item in final.get('output') or []:
        if item.get('type') == 'message':
            text.append(''.join(c.get('text', '') for c in item.get('content') or [] if c.get('type') == 'output_text'))
        elif item.get('type') == 'function_call':
            calls.append({'id': item.get('call_id') or item.get('id'), 'type': 'function',
                          'function': {'name': item.get('name'), 'arguments': item.get('arguments') or '{}'}})
    msg = {'role': 'assistant', 'content': ''.join(text) or (None if calls else '')}
    if calls:
        msg['tool_calls'] = calls
    u = final.get('usage') or {}
    return {'id': 'chatcmpl-' + uuid.uuid4().hex, 'object': 'chat.completion', 'created': int(time.time()), 'model': model,
            'choices': [{'index': 0, 'message': msg, 'finish_reason': 'tool_calls' if calls else 'stop'}],
            'usage': {'prompt_tokens': u.get('input_tokens', 0), 'completion_tokens': u.get('output_tokens', 0),
                      'total_tokens': u.get('total_tokens', 0)}}


async def chat(request):
    try:
        req = await request.json()
    except Exception:
        return web.json_response({'error': {'message': 'bad json'}}, status=400)
    body = convert(req)
    t = time.time()
    status, res = await upstream(body)
    if status != 200:
        log.warning('upstream %s for %s: %s', status, body['model'], str(res)[:300])
        return web.json_response({'error': {'message': 'codex upstream %s: %s' % (status, str(res)[:500]), 'type': 'upstream_error'}},
                                 status=status if status >= 400 else 502)
    out = to_chat(res, req.get('model'))
    m = out['choices'][0]['message']
    log.info('ok %s %.1fs in=%s out=%s tools=%d', body['model'], time.time() - t, out['usage']['prompt_tokens'],
             out['usage']['completion_tokens'], len(m.get('tool_calls') or []))
    if not req.get('stream'):
        return web.json_response(out)
    # 调用方要流式：把完整结果当成一块发出去
    resp = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
    await resp.prepare(request)
    delta = {'role': 'assistant', 'content': m.get('content') or ''}
    if m.get('tool_calls'):
        delta['tool_calls'] = [dict(tc, index=i) for i, tc in enumerate(m['tool_calls'])]
    chunk = {'id': out['id'], 'object': 'chat.completion.chunk', 'created': out['created'], 'model': out['model'],
             'choices': [{'index': 0, 'delta': delta, 'finish_reason': out['choices'][0]['finish_reason']}], 'usage': out['usage']}
    await resp.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n').encode())
    await resp.write(b'data: [DONE]\n\n')
    return resp


async def raw(request):
    """给插件直接用 Responses 原生工具（联网搜索 web_search、画图 image_generation）。
    入参 {model, instructions, input(字符串或 input 列表), tools, effort}；返回 {output: [...], usage}"""
    try:
        req = await request.json()
    except Exception:
        return web.json_response({'error': {'message': 'bad json'}}, status=400)
    inp = req.get('input') or ''
    if isinstance(inp, str):
        inp = [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': inp}]}]
    model = str(req.get('model') or 'gpt-6.2-sol')
    body = {'model': model, 'instructions': req.get('instructions') or 'You are a helpful assistant.', 'input': inp,
            'tools': req.get('tools') or [], 'tool_choice': 'auto', 'parallel_tool_calls': False,
            'store': False, 'stream': True, 'include': [], 'reasoning': {'effort': req.get('effort') or 'medium'}}
    t = time.time()
    status, res = await upstream(body)
    if status != 200:
        log.warning('raw upstream %s: %s', status, str(res)[:300])
        return web.json_response({'error': {'message': 'codex upstream %s: %s' % (status, str(res)[:500])}}, status=status)
    kinds = [o.get('type') for o in res.get('output') or []]
    log.info('raw ok %s %.1fs tools=%s items=%s', model, time.time() - t, [x.get('type') for x in body['tools']], kinds)
    return web.json_response({'output': res.get('output') or [], 'usage': res.get('usage') or {}})


async def status(request):
    """各订阅号：套餐、订阅到期、是否限额、何时重置、额度用量"""
    pref = preferred()
    accts = order()
    return web.json_response({'preferred': pref or (accts[0].name if accts else ''),
                              'active': next((a.name for a in accts if a.limited_until <= time.time()), ''),
                              'accounts': [dict(a.info(), preferred=(a.name == pref)) for a in accts]})


async def switch(request):
    """指定优先用哪个号（其余照样当备用）"""
    try:
        name = str((await request.json()).get('account') or '')
    except Exception:
        name = ''
    names = [a.name for a in scan()]
    if name not in names:
        return web.json_response({'ok': False, 'error': '没有这个号', 'accounts': names}, status=400)
    tmp = PREF + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'preferred': name, 't': time.time()}, f)
    os.replace(tmp, PREF)
    log.info('preferred account -> %s', name)
    return web.json_response({'ok': True, 'preferred': name})


async def models(request):
    acct = next((a for a in order() if a.limited_until <= time.time()), order()[0])
    await ensure_fresh(acct)
    h = dict(headers(acct), Accept='application/json')
    async with state['session'].get(BASE + '/models?client_version=' + CLIENT_VERSION, headers=h, timeout=ClientTimeout(total=60)) as r:
        data = await r.json(content_type=None)
    ms = [m.get('slug') for m in data.get('models', []) if m.get('visibility') == 'list']
    return web.json_response({'object': 'list', 'data': [{'id': s, 'object': 'model', 'owned_by': 'chatgpt'} for s in ms]})


async def keeper(app):
    async def loop():
        while True:
            for a in scan():
                try:
                    await ensure_fresh(a)
                except Exception as e:
                    log.error('keeper %s: %s', a.name, e)
            await asyncio.sleep(3600)
    task = asyncio.create_task(loop())
    yield
    task.cancel()


async def session_ctx(app):
    state['session'] = ClientSession()
    scan()
    yield
    await state['session'].close()


app = web.Application(client_max_size=64 * 1024 * 1024)
app.cleanup_ctx.append(session_ctx)
app.cleanup_ctx.append(keeper)
app.router.add_post('/v1/chat/completions', chat)
app.router.add_get('/v1/models', models)
app.router.add_post('/v1/codex/raw', raw)
app.router.add_get('/accounts', status)
app.router.add_post('/accounts/switch', switch)

if __name__ == '__main__':
    web.run_app(app, host='127.0.0.1', port=18790, access_log=None)
