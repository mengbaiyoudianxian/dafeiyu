#!/usr/bin/env python3
"""Configure a freshly extracted Linux QQ copy to start the pinned NapCat shell."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from verify_external import sha256


STOCK = "e7d8f160704a8704a4f531ef97602ea6fea8338f738f74ee9b10155cb9fc0a85"
LINKED = "1dccfed1e5a1652f80ee64d98fe84d900cdbeca4f3c138bc944096cc0b8bd90e"
LOADER = (
    "const path = require('node:path');\n"
    "const { pathToFileURL } = require('node:url');\n"
    "import(pathToFileURL(path.join(__dirname, 'app_launcher/napcat/napcat.mjs')).href);\n"
).encode("ascii")


def replace(path, data, mode):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        temp.write_bytes(data)
        temp.chmod(mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True, type=Path)
    args = parser.parse_args()
    catalog = json.loads(Path(__file__).with_name("artifacts.json").read_bytes())
    app = args.qq_root / "resources/app"
    nap = app / "app_launcher/napcat"
    for rel, digest in catalog["napcat"].items():
        if sha256(nap / rel) != digest:
            parser.exit(1, "Unexpected NapCat file: " + rel + "\n")
    package = app / "package.json"
    status = sha256(package)
    if status not in (STOCK, LINKED):
        parser.exit(1, "Unknown QQ application manifest.\n")
    metadata = json.loads(package.read_bytes())
    metadata["main"] = "./loadNapCat.js"
    linked = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()
    if hashlib.sha256(linked).hexdigest() != LINKED:
        parser.exit(1, "Linked QQ manifest differs from the reference.\n")
    loader = app / "loadNapCat.js"
    if loader.is_symlink() or (loader.exists() and loader.read_bytes() != LOADER):
        parser.exit(1, "Another QQ loader is present.\n")
    if not loader.exists():
        replace(loader, LOADER, 0o644)
    if status != LINKED:
        replace(package, linked, stat.S_IMODE(package.stat().st_mode))
    print("QQ manifest and NapCat loader match the reference layout")


if __name__ == "__main__":
    main()
