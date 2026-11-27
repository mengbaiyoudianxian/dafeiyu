"""Exercise the static touch client in Chromium with entirely synthetic HTTP fixtures."""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="existing Chromium-family executable")
    args = parser.parse_args()
    root = Path(__file__).with_name("touch")
    posted = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=args.browser)
        page = browser.new_page(service_workers="block")

        def serve(route):
            request = route.request
            path = request.url.removeprefix("https://fish.test/")
            assets = {"fish/": ("index.html", "text/html"), "fish/panel.css": ("panel.css", "text/css"),
                      "fish/panel.js": ("panel.js", "application/javascript")}
            if path in assets:
                filename, mime = assets[path]
                route.fulfill(body=(root / filename).read_bytes(), content_type=mime + "; charset=utf-8")
                return
            if request.method == "POST":
                posted.append((path, request.post_data_json))
            response = {
                "fish-api/scenes": {"items": [], "doing": "在看书"},
                "fish-api/login": {"ok": True, "name": "测试访客"},
                "fish-api/history": {"ok": True, "items": [{"w": "我", "x": "早安", "t": 1}]},
                "fish-api/actions": [{"k": "wave", "n": "挥手", "c": "互动"}],
                "fish-api/touch": {"ok": True, "accept": True, "line": "看见了"},
                "fish-api/chat": {"ok": True, "reply": "吃过啦"},
            }
            if path in response:
                route.fulfill(json=response[path])
            else:
                route.abort()

        page.route("**/*", serve)
        page.goto("https://fish.test/fish/")
        expect(page.locator("#scene-text")).to_have_text("在看书")
        page.locator("#key").fill("example")
        page.get_by_role("button", name="登录").click()
        expect(page.locator("#history")).to_contain_text("早安")
        page.get_by_role("button", name="互动").click()
        expect(page.locator("#notice")).to_have_text("看见了")
        page.locator("#message").fill("吃过了吗")
        page.get_by_role("button", name="发送").click()
        expect(page.locator("#history")).to_contain_text("吃过啦")
        assert ("fish-api/touch", {"key": "example", "kind": "wave"}) in posted
        assert ("fish-api/chat", {"key": "example", "text": "吃过了吗"}) in posted
        page.get_by_role("button", name="退出").click()
        expect(page.locator("#history")).to_be_empty()
        expect(page.locator("#login")).to_be_visible()
        assert not page.evaluate("localStorage.length || sessionStorage.length")
        browser.close()
    print("OK: scene, login, history, actions, touch, chat and logout")


if __name__ == "__main__":
    main()
