"""JMComic web reader using the ordinary browser profile and in-memory rendered PNGs."""
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

ORIGIN = "https://18comic.vip"


class JmAccessError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@asynccontextmanager
async def _tab():
    profile = Path(os.environ.get("DAFEIYU_JM_PROFILE", "~/.local/share/dafeiyu-comic-sessions/jm/profile")).expanduser()
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    async with async_playwright() as driver:
        try:
            context = await driver.chromium.launch_persistent_context(
                str(profile), executable_path=str(Path.home() / ".local/share/dafeiyu-comic-sessions/chromium"),
                chromium_sandbox=True, viewport={"width": 800, "height": 960},
                accept_downloads=False, service_workers="block",
            )
        except BrowserError as exc:
            raise JmAccessError("unavailable", "JM 浏览器未启动，可能正在交互登录") from exc
        try:
            await context.route("**/*", lambda route: route.continue_())  # Disables HTTP caching.
            page = await context.new_page()
            page.set_default_timeout(15000)
            yield page
        finally:
            await context.close()


async def _visit(page, path):
    try:
        response = await page.goto(ORIGIN + path, wait_until="domcontentloaded", timeout=30000)
    except BrowserError as exc:
        raise JmAccessError("unavailable", "JM 网页连接失败") from exc
    if not response:
        raise JmAccessError("unavailable", "JM 网页没有返回响应")
    if response.headers.get("cf-mitigated") == "challenge" or response.status in (401, 403):
        raise JmAccessError("auth_required", "JM 网页需要站方的浏览器验证或登录")
    if response.status == 429:
        raise JmAccessError("rate_limited", "JM 网页限制访问频率")
    if not response.ok or urlsplit(page.url).hostname != urlsplit(ORIGIN).hostname:
        raise JmAccessError("unavailable", f"JM 页面不可用（HTTP {response.status}）")
    if "login" in urlsplit(page.url).path or await page.title() == "Just a moment...":
        raise JmAccessError("auth_required", "JM 网页需要站方的浏览器验证或登录")


async def _detail(page, work_id):
    title_el = page.locator("#book-name").first
    if not await title_el.count():
        raise JmAccessError("unavailable", "JM 作品资料页未提供预期的标题")
    title = (await title_el.inner_text()).strip()
    tags = await page.locator("#tags a, [itemprop=genre] a").all_text_contents()
    authors = await page.locator('[data-type="author"] a').all_text_contents()
    episodes = await page.locator("[data-album]").evaluate_all(
        "els => els.map(e => e.getAttribute('data-album'))"
    )
    chapters = list(dict.fromkeys(v for v in episodes if v and re.fullmatch(r"[0-9]{1,12}", v)))
    links = await page.locator('a[href*="/photo/"]').evaluate_all(
        "els => els.map(e => e.href)"
    )
    for link in ([] if chapters else links):
        parsed = urlsplit(link)
        match = re.match(r"^/photo/([0-9]+)(?:/|$)", parsed.path)
        if parsed.hostname == urlsplit(ORIGIN).hostname and match and match[1] not in chapters:
            chapters.append(match[1])
    if not chapters:
        chapters.append(work_id)
    return title, [s.strip() for s in tags if s.strip()], "、".join(s.strip() for s in authors if s.strip()), chapters


async def discover(query="", page=1):
    page_number = max(1, int(page))
    q = str(query).strip()[:120]
    params = {"page": page_number}
    if q:
        params["search_query"] = q
    path = ("/search/photos" if q else "/albums") + "?" + urlencode(params)
    async with _tab() as tab:
        await _visit(tab, path)
        direct = re.match(r"^/album/([0-9]+)(?:/|$)", urlsplit(tab.url).path)
        if direct:
            title, tags, author, _ = await _detail(tab, direct[1])
            return [{"id": direct[1], "title": title, "tags": tags, "author": author}]
        rows = await tab.locator('a[href*="/album/"]').evaluate_all("""anchors => anchors.map(a => {
            const card = a.closest('.thumb-overlay')?.parentElement || a.parentElement;
            const image = a.querySelector('img');
            const name = card?.querySelector('.title');
            return { href: a.href, title: name?.textContent?.trim() || a.title ||
                image?.alt || a.innerText?.trim() || '',
                tags: [...(card?.querySelectorAll('.tags a, .tag a') || [])]
                    .map(e => e.textContent.trim()).filter(Boolean) };
        })""")
        found = {}
        for row in rows:
            parsed = urlsplit(row["href"])
            match = re.match(r"^/album/([0-9]+)(?:/|$)", parsed.path)
            if parsed.hostname == urlsplit(ORIGIN).hostname and match and row["title"]:
                found.setdefault(match[1], {"id": match[1], "title": row["title"][:240],
                                            "tags": row["tags"][:24]})
        if not found and not q:
            raise JmAccessError("unavailable", "JM 目录未显示可读作品")
        return list(found.values())


async def read(work_id, offset=0, limit=2, chapter_index=0):
    """Offset indexes pages within the selected chapter. Returns displayed page images as PNG bytes."""
    work_id = str(work_id)
    if not re.fullmatch(r"[0-9]{1,12}", work_id):
        raise ValueError("invalid JM work ID")
    offset, limit, chapter_index = int(offset), int(limit), int(chapter_index)
    if offset < 0 or not 1 <= limit <= 4 or chapter_index < 0:
        raise ValueError("invalid JM page range")
    async with _tab() as tab:
        await _visit(tab, f"/album/{work_id}")
        title, tags, author, chapters = await _detail(tab, work_id)
        if chapter_index >= len(chapters):
            raise ValueError("JM chapter out of range")
        await _visit(tab, f"/photo/{chapters[chapter_index]}")
        pages = tab.locator('img[id^="album_photo"]')
        count = await pages.count()
        if not count:
            raise JmAccessError("unavailable", "JM 阅读器未显示可读分页")
        images = []
        for index in range(offset, min(count, offset + limit)):
            element = pages.nth(index)
            await element.evaluate("e => e.parentElement.scrollIntoView({block:'center'})")
            try:
                rendered = await tab.wait_for_function(r"""index => {
                    const e = document.querySelectorAll('img[id^="album_photo"]')[index];
                    const canvas = e?.parentElement?.querySelector('canvas');
                    if (canvas && canvas.width > 8 && canvas.height > 8 &&
                        canvas.getBoundingClientRect().height > 8) return canvas;
                    if (e && e.complete && e.naturalWidth > 8 &&
                        e.getBoundingClientRect().height > 8 && /\/media\/photos\//.test(e.currentSrc)) return e;
                    return false;
                }""", arg=index)
                images.append(await rendered.as_element().screenshot(type="png", timeout=15000))
            except BrowserError as exc:
                raise JmAccessError("unavailable", "JM 阅读器未能显示所选分页") from exc
        return {"title": title, "tags": tags, "author": author, "id": work_id, "images": images,
                "chapter_index": chapter_index, "chapters": len(chapters), "total": count,
                "offset": offset, "url": f"{ORIGIN}/album/{work_id}"}
