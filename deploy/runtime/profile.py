"""Initialize a new AstrBot data directory, optionally importing local-only settings."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "deploy/config-examples"
TABLES = {
    "persona_folders": ("folder_id", "name", "parent_id", "description", "sort_order"),
    "personas": ("persona_id", "system_prompt", "begin_dialogs", "tools", "skills",
                 "custom_error_message", "folder_id", "sort_order"),
    "preferences": ("scope", "scope_id", "key", "value"),
}


def read_json(path):
    return json.loads(path.read_bytes())


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def outside_checkout(path):
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Use a private path outside all Git checkouts.")


def capture(root, destination):
    data = root / "data"
    destination = destination.resolve()
    outside_checkout(destination)
    config = {f.name: read_json(f) for f in sorted((data / "config").glob("*_config.json"))}
    core = {name: read_json(data / name) for name in ("cmd_config.json", "skills.json", "mcp_server.json")
            if (data / name).is_file()}
    with sqlite3.connect((data / "data_v4.db").resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        rows = {}
        for table, cols in TABLES.items():
            rows[table] = list(db.execute(f"SELECT {', '.join(cols)} FROM {table}"))
    profile = {"format": 1, "core": core, "config": config, "rows": rows}
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False)


def load_private(path):
    outside_checkout(path)
    if not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Private profile must be a regular, owner-only file (chmod 600).")
    value = read_json(path)
    if value["format"] != 1:
        raise ValueError("Unknown private profile format.")
    return value


def merge(base, overrides):
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            merge(base[key], val)
        else:
            base[key] = val
    return base


def seed_data(root, private):
    data = root / "data"
    config = data / "config"
    config.mkdir(parents=True)
    # Import only after setting the isolated root. AstrBot creates local dashboard credentials here.
    from astrbot.core import astrbot_config, db_helper

    if private:
        for name in ("cmd_config.json", "mcp_server.json", "skills.json"):
            if name in private["core"]:
                save_json(data / name, private["core"][name])
    else:
        public = read_json(TEMPLATES / "cmd_config.example.json")
        save_json(data / "cmd_config.json", merge(dict(astrbot_config), public))
    for source in sorted(TEMPLATES.glob("*_config.example.json")):
        if source.name != "cmd_config.example.json":
            save_json(config / source.name.replace(".example.json", ".json"), read_json(source))
    if private:
        for name, value in private["config"].items():
            if not re.fullmatch(r"[A-Za-z0-9_]+_config\.json", name):
                raise ValueError("Invalid plugin configuration filename.")
            save_json(config / name, value)

    async def initialize():
        await db_helper.initialize()
        await db_helper.engine.dispose()

    asyncio.run(initialize())
    if private:
        rows = private["rows"]
    else:
        persona = (REPO / "deploy/persona.md").read_text(encoding="utf-8").split("\x60\x60\x60text\n", 1)[1].split("\n\x60\x60\x60", 1)[0]
        preferences = read_json(Path(__file__).with_name("global-preferences.json"))
        rows = {
            "persona_folders": [],
            "personas": [("大肥鱼", persona, None, None, None, None, None, 0)],
            "preferences": [("global", "global", key, json.dumps({"val": value}, ensure_ascii=False))
                            for key, value in preferences.items()],
        }
    with sqlite3.connect(data / "data_v4.db") as db:
        for table, cols in TABLES.items():
            placeholders = ", ".join("?" for _ in cols)
            db.executemany(f"INSERT OR REPLACE INTO {table} (created_at, updated_at, {', '.join(cols)}) "
                           f"VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, {placeholders})", rows[table])


def initialize(root, private_file):
    outside_checkout(root)
    if (root / "data").exists():
        raise ValueError("This command initializes a fresh root. Choose an unused data directory.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private = load_private(private_file.resolve()) if private_file else None
    with tempfile.TemporaryDirectory(prefix=".init-", dir=root) as work:
        scratch = Path(work)
        os.environ["ASTRBOT_ROOT"] = work
        os.chdir(scratch)
        seed_data(scratch, private)
        (scratch / "data").rename(root / "data")
        (root / ".astrbot").touch()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture_args = commands.add_parser("capture", help="Save settings and personas locally. Contains credentials.")
    capture_args.add_argument("root", type=Path)
    capture_args.add_argument("output", type=Path)
    init_args = commands.add_parser("init", help="Initialize a new root without starting the bot.")
    init_args.add_argument("root", type=Path)
    init_args.add_argument("--private-profile", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.root.resolve(), args.output)
    else:
        initialize(args.root.resolve(), args.private_profile)


if __name__ == "__main__":
    main()

