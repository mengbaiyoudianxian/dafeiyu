"""Verify an existing vendor bundle and print an AstrBot MCP configuration."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="installed anime-studio directory")
    parser.add_argument("--node", default="node", help="Node.js executable")
    args = parser.parse_args()
    root = args.bundle.expanduser().resolve()
    lock = json.loads(Path(__file__).with_name("components.lock.json").read_text())["anime-studio"]
    node = shutil.which(args.node)
    if not node:
        parser.error("Node.js executable not found")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    if version != "v" + lock["node"]:
        parser.error(f"expected Node.js {lock['node']}, found {version}")
    for name, digest in lock["files_sha256"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            parser.error(f"{name} differs from vendor build {lock['version']}")
    service = {"command": node, "args": [str(root / "mcp.js")]}
    print(json.dumps({"mcpServers": {"anime-studio": service}}, indent=2))


if __name__ == "__main__":
    main()
