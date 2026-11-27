"""Read PicACG through its ordinary authenticated website and locally rendered pages."""
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeout
from playwright.async_api import async_playwright


ORIGIN = "https://manhuabika.com"
SESSIONS = Path(os.environ.get("DAFEIYU_COMIC_SESSIONS", str(Path.home() / ".local/share/dafeiyu-comic-sessions")))
WORK_ID = re.compile(r"[a-f0-9]{24}", re.I)


class PicaAccessError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@asynccontextmanager
async def _tab():
    site = SESSIONS / "pica"
    profile = site / "profile"
    if (site / "login.json").is_file():
        raise PicaAccessError("auth_required", "哔咔交互登录窗口正在使用，完成后需要关闭该窗口")
    if not profile.is_dir():
        raise PicaAccessError("auth_required", "请先在哔咔官方网页版完成登录")
    async with async_playwright() as driver:
        try:
            ctx = await driver.chromium.launch_persistent_context(
                str(profile), executable_path=str(SESSIONS / "chromium"), chromium_sandbox=True,
                viewport={"width": 900, "height": 960}, accept_downloads=False, service_workers="block",
            )
        except BrowserError as exc:
            raise PicaAccessError("unavailable", "哔咔浏览器未能启动或该资料正在使用") from exc
        try:
            # Routing disables the persistent HTTP cache while retaining the site's login session.
            await ctx.route("**/*", lambda route: route.continue_())
            page = await ctx.new_page()
            page.set_default_timeout(15000)
            yield page
        finally:
            await ctx.close()


async def _visit(page, path, ready):
    try:
        response = await page.goto(ORIGIN + path, wait_until="domcontentloaded", timeout=30000)
        if not response or not response.ok or urlsplit(page.url).hostname != "manhuabika.com":
            raise PicaAccessError("unavailable", "哔咔网页当前不可用")
        outcome = await page.wait_for_function(r"""(selector) => {
            if (Array.from(document.querySelectorAll(selector)).some(el => el.getClientRects().length)) return 'ready';
            if (/没有找到|未找到漫画|无搜索结果|没有符合条件的漫画/.test(document.body.innerText)) return 'empty';
            return false;
        }""", arg=ready, timeout=20000)
        return await outcome.json_value()
    except BrowserTimeout as exc:
        # The website briefly renders its guest shell before restoring the account.
        needs_login = await page.evaluate(r"""() => Array.from(document.querySelectorAll('input[type=password],button,a')).some(el =>
            el.getClientRects().length && (el.matches('input[type=password]') || /^(登录|登入|登录阅读|网页版\s*·\s*登录阅读)/.test(el.innerText.trim())))""")
        if needs_login:
            raise PicaAccessError("auth_required", "哔咔要求先在官方网页版登录")
        raise PicaAccessError("unavailable", "哔咔页面未在等待时间内显示所需内容") from exc
    except BrowserError as exc:
        raise PicaAccessError("unavailable", "哔咔页面未在等待时间内显示所需内容") from exc


async def discover(query="", page=1):
    number = max(1, int(page))
    query = str(query).strip()[:80]
    params = {"page": number, "q": query} if query else {"page": number, "s": "dd"}
    path = ("/comics/search" if query else "/comics") + "?" + urlencode(params)
    async with _tab() as tab:
        link_selector = 'a[href*="/comic/"]:not([href*="/comic/reader/"])'
        if await _visit(tab, path, link_selector) == "empty":
            return []
        rows = await tab.locator(link_selector).evaluate_all("""links => links.map(a => {
            const item = a.closest('article,li') || a.parentElement;
            const tags = Array.from(item.querySelectorAll('a[href*="/tag/"],a[href*="/category/"],a[href*="tag="],a[href*="category="]')).map(e => e.textContent.trim()).filter(Boolean);
            return {href:a.href,title:a.title || a.querySelector('img')?.alt || a.innerText.trim(),tags};
        })""")
        found = {}
        for row in rows:
            url = urlsplit(row["href"])
            route = url.fragment.split("?")[0] if url.fragment.startswith("/") else url.path
            match = re.fullmatch(r"/comic/([0-9a-fA-F]{24})/?", route)
            if url.hostname == "manhuabika.com" and match and row["title"]:
                key = match[1].lower()
                found.setdefault(key, {"id": key, "title": row["title"][:240], "tags": row["tags"][:24]})
        return list(found.values())


async def read(work_id, offset=0, limit=2, chapter_index=0):
    work_id = str(work_id)
    if not WORK_ID.fullmatch(work_id):
        raise ValueError("invalid PicACG work ID")
    offset, limit, chapter_index = int(offset), int(limit), int(chapter_index)
    if offset < 0 or not 1 <= limit <= 4 or chapter_index < 0:
        raise ValueError("invalid PicACG page range")
    async with _tab() as page:
        # The detail shell and its h1 mount before the actual book metadata arrives.
        await _visit(page, f"/comic/{work_id}", "article[data-comic-detail] [data-detail-stat] dd")
        detail = page.locator("article[data-comic-detail]")
        title = (await detail.locator("h1").first.inner_text()).strip()
        tags = await detail.locator('[aria-label="标签"] a, [aria-label="標籤"] a, [aria-label="Tags"] a, [aria-label="分类"] a, [aria-label="分類"] a, a[href*="/tag/"], a[href*="/category/"], a[href*="tag="], a[href*="category="]').all_text_contents()
        authors = await detail.locator('a[href*="/author/"],a[href*="author="]').all_text_contents()
        counts = await detail.locator('[data-detail-stat="chapters"] dd').evaluate_all(
            "els => els.map(el => el.title || el.textContent)"
        )
        orders = []
        for href in await detail.locator('a[href*="/comic/reader/"]').evaluate_all("els => els.map(el=>el.href)"):
            order = re.search(rf"/comic/reader/{re.escape(work_id)}/(\d+)", href, re.I)
            if order:
                orders.append(int(order[1]))
        chapters = max([int(n.replace(",", "")) for n in counts if re.fullmatch(r"[\d,]+", n)] + orders + [1])
        if chapter_index >= chapters:
            raise ValueError("PicACG chapter out of range")
        path = f"/comic/reader/{work_id}/{chapter_index + 1}/waterfall"
        slots = '[data-reader-stage] figure[data-page]'
        await _visit(page, path, slots)
        total = await page.locator(slots).evaluate_all(
            "els => Math.max(...els.map(el => Number(el.getAttribute('data-page'))))"
        )
        images = []
        for number in range(offset + 1, min(total, offset + limit) + 1):
            slot = page.locator(f'[data-reader-stage] figure[data-page="{number}"]')
            await slot.scroll_into_view_if_needed()
            image = slot.locator("img[alt]:not([alt=''])").first
            try:
                await image.wait_for(state="visible")
                await image.evaluate("img => img.decode()")
                images.append(await image.screenshot(type="png", timeout=15000))
            except BrowserError as exc:
                raise PicaAccessError("unavailable", "哔咔阅读器未能显示选定分页") from exc
        return {"title": title, "tags": list(dict.fromkeys(t.strip(" #") for t in tags if t.strip(" #")))[:24],
                "author": "、".join(a.strip() for a in authors if a.strip()), "id": work_id,
                "images": images, "chapter_index": chapter_index, "chapters": chapters,
                "total": total, "offset": offset, "url": f"{ORIGIN}/comic/{work_id}"}
