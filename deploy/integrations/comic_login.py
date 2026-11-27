"""Open one private web-reader login in a temporary loopback-only desktop."""
import argparse
import asyncio
import json
import os
import secrets
import signal
import socket
import string
import subprocess
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(os.environ.get("DAFEIYU_COMIC_SESSIONS", "~/.local/share/dafeiyu-comic-sessions")).expanduser()
NOVNC_WEB = Path(os.environ.get("DAFEIYU_NOVNC_WEB", "/usr/share/novnc")).expanduser()
READERS = {"pica": "https://manhuabika.com/", "jm": "https://18comic.vip/albums"}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_port(port, process):
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError(f"Desktop helper exited with {process.returncode}")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError("Desktop listener did not start")


async def run(site):
    os.umask(0o077)
    session = ROOT / site
    session.mkdir(exist_ok=True, parents=True)
    profile = session / "profile"
    state = session / "login.json"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    with tempfile.TemporaryDirectory(prefix="comic-desktop-") as temp:
        tmp = Path(temp)
        auth = tmp / "xauth"
        auth.touch(mode=0o600)
        display_number = next(i for i in range(211, 300) if not (Path("/tmp/.X11-unix") / f"X{i}").exists() and not Path(f"/tmp/.X{i}-lock").exists())
        display = f":{display_number}"
        subprocess.run(["xauth", "-f", str(auth)], input=f"add {display} . {secrets.token_hex(16)}\n", text=True, check=True, stdout=subprocess.DEVNULL)
        env = dict(os.environ, DISPLAY=display, XAUTHORITY=str(auth))
        secret = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        password_file = tmp / "vnc-pass"
        password_file.write_text(secret + "\n")
        processes = []

        def spawn(command):
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            processes.append(process)
            return process

        try:
            xvfb = spawn(["Xvfb", display, "-auth", str(auth), "-screen", "0", "1280x800x24", "-nolisten", "tcp"])
            for _ in range(60):
                if Path(f"/tmp/.X11-unix/X{display_number}").exists():
                    break
                if xvfb.poll() is not None:
                    raise RuntimeError("Xvfb could not start")
                await asyncio.sleep(0.1)
            port = free_port()
            vnc = spawn(["x11vnc", "-display", display, "-auth", str(auth), "-localhost", "-no6", "-listen", "127.0.0.1", "-rfbport", str(port), "-passwdfile", str(password_file), "-forever", "-noxdamage", "-quiet", "-nosel", "-noxrecord", "-noremote", "-o", "/dev/null"])
            await wait_port(port, vnc)
            webport = free_port()
            if not (NOVNC_WEB / "vnc.html").is_file():
                raise RuntimeError("Set DAFEIYU_NOVNC_WEB to the noVNC web directory")
            web = spawn(["websockify", "--web", str(NOVNC_WEB), f"127.0.0.1:{webport}", f"127.0.0.1:{port}"])
            await wait_port(webport, web)
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(str(profile), headless=False, executable_path=str(ROOT / "chromium"), chromium_sandbox=True, env=env, viewport={"width": 1240, "height": 680}, accept_downloads=False, args=["--window-size=1280,800", "--disable-save-password-bubble", "--password-store=basic"])
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(READERS[site], wait_until="domcontentloaded", timeout=30000)
                    if site == "pica":
                        front = page.get_by_role("button", name="网页版 · 登录阅读", exact=False)
                        if await front.is_visible(timeout=2000):
                            await front.click(timeout=8000)
                            await page.get_by_role("button", name="继续登录", exact=True).click(timeout=20000)
                    ready = tmp / "ready"
                    ready.write_text(json.dumps({"port": webport, "password": secret, "pid": os.getpid()}))
                    state.write_text(ready.read_text())
                    print(f"{site}: interactive login ready", flush=True)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=1200)
                    except TimeoutError:
                        pass
                finally:
                    await context.close()
        finally:
            if state.exists():
                try:
                    if json.loads(state.read_text()).get("pid") == os.getpid():
                        state.unlink()
                except (ValueError, OSError):
                    pass
            for proc in reversed(processes):
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("site", choices=READERS.keys())
    asyncio.run(run(parser.parse_args().site))
