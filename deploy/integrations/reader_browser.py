"""Prepare the reader's sandboxed Chromium launcher in the bot user's session directory."""

import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apparmor-profile", help="installed AppArmor profile, e.g. chrome on Ubuntu")
    parser.add_argument("--sessions", type=Path, default=Path(os.environ.get(
        "DAFEIYU_COMIC_SESSIONS", "~/.local/share/dafeiyu-comic-sessions")))
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    import playwright

    lock = json.loads(Path(__file__).with_name("components.lock.json").read_text())["reader"]
    if version("playwright") != lock["playwright"]:
        parser.error(f"expected playwright=={lock['playwright']}")
    registry = Path(playwright.__file__).parent / "driver/package/browsers.json"
    browsers = json.loads(registry.read_text())["browsers"]
    entry = next(b for b in browsers if b["name"] == "chromium")
    if (entry["revision"], entry["browserVersion"]) != (lock["chromium_revision"], lock["chromium_version"]):
        parser.error("Chromium version or revision differs from the lock")
    with sync_playwright() as driver:
        browser = Path(driver.chromium.executable_path)
    if not browser.is_file() or not os.access(browser, os.X_OK):
        parser.error("install the full Chromium binary with python -m playwright install chromium")

    command = []
    if args.apparmor_profile:
        aa_exec = shutil.which("aa-exec")
        if not aa_exec:
            parser.error("aa-exec executable not found")
        command = [aa_exec, "-p", args.apparmor_profile, "--"]
    command.append(str(browser))
    installed = subprocess.check_output([*command, "--version"], text=True).split()
    if lock["chromium_version"] not in installed:
        parser.error("installed Chromium binary differs from the lock")

    session_dir = args.sessions.expanduser()
    session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    launcher = session_dir / "chromium"
    with tempfile.NamedTemporaryFile(mode="w", dir=session_dir, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n')
            handle.close()
            temp.chmod(0o700)
            os.replace(temp, launcher)
        finally:
            temp.unlink(missing_ok=True)
    print(launcher)


if __name__ == "__main__":
    main()
