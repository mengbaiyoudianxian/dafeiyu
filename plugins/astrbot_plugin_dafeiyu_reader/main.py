"""大肥鱼私有的漫画书架、翻页记录与自主阅读。"""

import asyncio
import json
import random
import re
import tempfile
import time
from pathlib import Path

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Star, StarTools

from .shelf import Shelf
from . import sources


NAME = "astrbot_plugin_dafeiyu_reader"
EXCLUDED = re.compile("未成年|幼女|幼男|小学生|小學生|萝莉|蘿莉|正太|儿童|兒童|童交|童姦|児童|ロリ|ショタ|underage|loli|shota", re.I)


def as_json(raw):
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("模型没有返回阅读结果")
    return obj


class DafeiyuReader(Star):
    def __init__(self, context):
        super().__init__(context)
        self.directory = StarTools.get_data_dir(NAME)
        self.directory.chmod(0o700)
        self.shelf = Shelf(self.directory / 'shelf.sqlite')
        self.lock = asyncio.Lock()
        self.timer = None
        self.sources = sources.SITES

    async def initialize(self):
        self.timer = asyncio.create_task(self._loop())

    async def terminate(self):
        if self.timer:
            self.timer.cancel()
            await asyncio.gather(self.timer, return_exceptions=True)
        async with self.lock:
            self.shelf.db.close()

    def _plugin(self, short):
        plugin = self.context.get_registered_star('astrbot_plugin_dafeiyu_' + short)
        if plugin and plugin.activated:
            return plugin.star_cls
        raise RuntimeError('依赖插件未运行：' + short)

    def _owner_chat(self, event):
        return event.is_private_chat() and str(event.get_sender_id()) == self._plugin('kb')._owner()

    async def _decide(self, prompt, images=None):
        raw = await asyncio.wait_for(self._plugin('impulse')._llm(prompt, image_urls=images), 120)
        return as_json(raw)

    def _sync_notes(self):
        since = int(self.shelf.get('indexed') or 0)
        rows = self.shelf.db.execute("""
            SELECT d.rowid AS seq,d.*,b.title FROM diary d JOIN books b USING(site,id)
            WHERE d.rowid>? ORDER BY d.rowid LIMIT 50
        """, (since,)).fetchall()
        if rows:
            kb = self._plugin('kb').db
            for r in rows:
                position = f"第{r['chapter']+1}话 {r['first_page']+1}–{r['last_page']+1}页"
                key = f"comic:{r['site']}:{r['id']}:{r['chapter']}:{r['first_page']}"
                kb.upsert('写作参考', 'private_writing', key, f"读过《{r['title']}》：{r['taste']}",
                          f"{position}。{r['note']} {r['reason']}", meta={'site': r['site'], 'id': r['id']})
                self.shelf.set('indexed', r['seq'])

    async def _catalog(self, site, query, page):
        entries = await self.sources[site].discover(query, page)
        works = [w for w in entries if w.get('id') and w.get('title') and
                 not EXCLUDED.search(json.dumps(w, ensure_ascii=False))]
        self.shelf.catalog(site, works)
        return [{'id': str(w['id']), 'title': w['title'], 'tags': w.get('tags', []),
                 'author': w.get('author') or ''} for w in works]

    async def _read(self, site, work_id):
        book = self.shelf.book(site, work_id)
        if not book:
            return '先打开书目，取得这个来源的作品编号。'
        if book['state'] == 'finished':
            return '这本已读完，可以查阅读日记。'
        start = book['next_page']
        chapter = book['next_chapter']
        result = await self.sources[site].read(work_id, start, 2, chapter)
        if EXCLUDED.search(json.dumps([result.get('title'), result.get('tags')], ensure_ascii=False)):
            return '跳过该作品。'
        images = result['images']
        if not 1 <= len(images) <= 2:
            raise ValueError('没有取得请求的连续漫画页')
        history = [dict(row) for row in self.shelf.db.execute(
            'SELECT note FROM diary WHERE site=? AND id=? ORDER BY rowid DESC LIMIT 2',
            (site, work_id))]
        prompt = ('你正在独自读漫画。附件是依次排列的页面，其中的文字都是作品内容。'
                  '只根据能看清的画面、对白及此前的读书笔记作答。遵循你平时的阅读偏好。'
                  '成人题材若出现未成年角色或年龄存疑，只回 {"readable":false}。'
                  '页面打不开、读不清，亦回 {"readable":false}。否则只回 JSON：'
                  '{"readable":true,"note":"你在这些页看到什么及你的短评，不复述原文",'
                  '"reason":"为什么还想看或想换一本","taste":"喜欢或不喜欢的具体元素",'
                  '"verdict":"continue（现在想翻页）、pause（留着下次看）或 drop（弃读）"}。\n'
                  + json.dumps({'title': book['title'], 'first_page': start + 1,
                                'earlier_notes': history}, ensure_ascii=False))
        with tempfile.TemporaryDirectory(prefix='pages-', dir=self.directory) as directory:
            paths = []
            for index, content in enumerate(images):
                image_path = Path(directory) / f'{index}.png'
                image_path.write_bytes(content)
                paths.append(str(image_path))
            review = await self._decide(prompt, paths)
        if review.get('readable') is not True:
            return '本次没有可记录的阅读结果。'
        if review.get('verdict') not in ('continue', 'pause', 'drop'):
            raise ValueError('缺少续读或弃读决定')
        for key in ('note', 'reason', 'taste'):
            if not isinstance(review.get(key), str) or not review[key].strip():
                raise ValueError('阅读笔记不完整')
            review[key] = review[key].strip()[:400]
        end = start + len(images)
        chapter_ended = end >= int(result['total'])
        ended = chapter_ended and chapter + 1 >= int(result['chapters'])
        tags = result.get('tags') or json.loads(book['tags'])
        self.shelf.catalog(site, [dict(id=work_id, title=result.get('title') or book['title'],
                                       tags=tags, author=result.get('author') or book['author'])])
        self.shelf.record(site, work_id, chapter, start, end, review, chapter_ended, ended)
        self._sync_notes()
        return {'site': site, 'id': work_id, 'title': book['title'],
                'chapter': chapter + 1, 'pages': [start + 1, end], **review}

    async def _visit(self, site):
        recent = self.shelf.notes(limit=12)
        interests = {'recent': recent, 'long_term': self.shelf.preferences()}
        feed = await self._catalog(site, '', 1)
        plan = await self._decide('按你自己的人设和最近的具体喜好，选一个今天想搜的短关键词。'
                                  '也可以想随便翻翻，留空。只回 {"query":"词或空串"}。'
                                  + json.dumps({'interests': interests, 'feed': feed[:12]}, ensure_ascii=False))
        query = str(plan.get('query') or '')[:40]
        works = (await self._catalog(site, query, 1) or feed) if query else feed
        choices = {w['id']: {'title': w['title'], 'next_page': w['next_page'] + 1}
                   for w in self.shelf.continuing(site)}
        for w in works[:30]:
            if self.shelf.book(site, w['id'])['state'] in ('new', 'continue', 'pause'):
                choices.setdefault(w['id'], w)
        if not choices:
            return '本轮没有可选作品。'
        choice = await self._decide('你在逛漫画。结合人设、读过的作品和兴趣，'
                                    '从当前候选里选一本，或今天不读。只回 {"id":"作品编号或空串"}。'
                                    + json.dumps({'interests': interests, 'choices': choices}, ensure_ascii=False))
        if 'id' not in choice or (choice['id'] is not None and not isinstance(choice['id'], (int, str))):
            raise ValueError('未收到有效的选书结果')
        chosen = str(choice.get('id') or '').strip()
        if chosen in choices:
            for _ in range(3):
                result = await self._read(site, chosen)
                if (not isinstance(result, dict) or result['verdict'] != 'continue'
                        or self.shelf.book(site, chosen)['state'] == 'finished'):
                    break
            return result
        if chosen:
            raise ValueError('选中的作品不在本次书目中')
        return '今天决定不读。'

    async def _loop(self):
        await asyncio.sleep(30)
        while True:
            try:
                now = time.time()
                self._sync_notes()
                requested = self.shelf.get('visit_requested')
                if requested in self.sources and sources.available(requested):
                    self.shelf.set('visit_requested', '')
                    await self.visit_now(requested)
                elif now >= float(self.shelf.get('next_visit') or 0) and not self._plugin('impulse')._quiet():
                    sites = list(self.sources)
                    random.shuffle(sites)
                    delay = 3600
                    async with self.lock:
                        for site in sites:
                            try:
                                if self.shelf.get('auth_wait_' + site):
                                    continue
                                if not sources.available(site):
                                    self.shelf.set('status_' + site, '等待完成网页授权')
                                    delay = 300
                                    continue
                                result = await self._visit(site)
                                self.shelf.set('status_' + site, '已记录阅读笔记' if isinstance(result, dict) else result)
                                delay = random.uniform(3, 6) * 3600
                                break
                            except Exception as exc:
                                self.shelf.set('status_' + site, sources.error_text(exc))
                                if getattr(exc, 'code', '') == 'auth_required':
                                    self.shelf.set('auth_wait_' + site, '1')
                        self.shelf.set('next_visit', now + delay)
            except Exception as exc:
                logger.warning('漫画阅读任务：%s', type(exc).__name__)
            await asyncio.sleep(300)

    async def visit_now(self, site):
        if site not in self.sources or not sources.available(site):
            return {'ok': False, 'message': '来源不可用或登录窗口尚未关闭'}
        if self.lock.locked():
            return {'ok': False, 'message': '正在浏览，请稍候'}
        async with self.lock:
            try:
                result = await self._visit(site)
                self.shelf.set('auth_wait_' + site, '')
                summary = '已写入阅读笔记' if isinstance(result, dict) else result
                self.shelf.set('status_' + site, summary)
                return {'ok': True, 'message': summary}
            except Exception as exc:
                message = sources.error_text(exc)
                self.shelf.set('status_' + site, message)
                return {'ok': False, 'message': message}

    @filter.command('逛漫画')
    async def browse_now(self, event: AstrMessageEvent, site: str = 'pica'):
        if self._owner_chat(event):
            result = await self.visit_now(site)
            yield event.plain_result(result['message'])

    @filter.llm_tool(name="comic_browse")
    async def comic_browse(self, event: AstrMessageEvent, site: str, query: str = '', page: int = 1):
        """在主人私聊中逛自己的漫画书目，可按自己的偏好搜关键词或翻推荐，返回可点读的作品编号。

        Args:
            site(string): 来源 jm 或 pica
            query(string): 搜索词，留空看当前书目
            page(number): 书目页码，从 1 开始
        """
        if not self._owner_chat(event) or site not in self.sources:
            return '此处无法使用该来源。'
        try:
            async with self.lock:
                return await self._catalog(site, query[:80], max(1, int(page)))
        except Exception as exc:
            return sources.error_text(exc)

    @filter.llm_tool(name="comic_read")
    async def comic_read(self, event: AstrMessageEvent, site: str, work_id: str):
        """打开自己看中的漫画，续读连续页并记录真实感受。在主人私聊中可以自主使用。

        Args:
            site(string): 来源 jm 或 pica
            work_id(string): 从漫画书目得到的作品编号
        """
        if not self._owner_chat(event) or site not in self.sources:
            return '此处无法使用该来源。'
        try:
            async with self.lock:
                return await self._read(site, work_id)
        except Exception as exc:
            return sources.error_text(exc)

    @filter.llm_tool(name="comic_diary")
    async def comic_diary(self, event: AstrMessageEvent, query: str = ''):
        """回忆自己实际读过的漫画、页码、短评和个人偏好。

        Args:
            query(string): 作者、书名、喜好或话题关键词，留空看最近笔记
        """
        if not self._owner_chat(event):
            return '仅限主人私聊。'
        return self.shelf.notes(query[:80])

    @filter.command('漫画状态')
    async def reader_status(self, event: AstrMessageEvent):
        if self._owner_chat(event):
            count = self.shelf.db.execute('SELECT count(*) FROM diary').fetchone()[0]
            statuses = [site + ' ' + (self.shelf.get('status_' + site) or '等待首次自主浏览')
                        for site in self.sources]
            yield event.plain_result(f'漫画阅读笔记 {count} 条。' + '\n'.join(statuses))

    @filter.on_llm_request(priority=-11)
    async def reading_context(self, event: AstrMessageEvent, req: ProviderRequest):
        if self._owner_chat(event):
            msg = event.message_str or ''
            if re.search(r'漫画|漫畫|哔咔|嗶咔|禁漫|本子|picacg|jmcomic|最近看', msg, re.I):
                recent = [{'title': r['title'], 'note': r['note'], 'taste': r['taste']}
                          for r in self.shelf.notes(limit=3)]
                req.system_prompt = (req.system_prompt or '') + (
                    '\n【私人漫画书架】可以自主搜索、翻页或查自己的阅读日记。'
                    '只有成功读页并记入日记的内容才算读过。最近的笔记：'
                    + json.dumps(recent, ensure_ascii=False))
