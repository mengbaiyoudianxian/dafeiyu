#!/usr/bin/env python3
"""Verify selected Linux x86_64 archives and installed assets offline."""

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    catalog = json.loads(Path(__file__).with_name("artifacts.json").read_bytes())
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("qq", "napcat", "asr"):
        parser.add_argument("--" + name + "-root", type=Path)
    parser.add_argument("--archive", nargs=2, action="append", default=[],
                        metavar=("KIND", "FILE"), help=", ".join(catalog["archives"]))
    args = parser.parse_args()
    files = []
    for name in ("qq", "napcat", "asr"):
        root = getattr(args, name + "_root")
        if root is not None:
            files.extend((name + "/" + rel, root / rel, expected)
                         for rel, expected in catalog[name].items())
    for kind, path in args.archive:
        if kind not in catalog["archives"]:
            parser.error("unknown archive kind " + kind)
        files.append((kind, Path(path), catalog["archives"][kind]["sha256"]))
    if not files:
        parser.error("select at least one component or archive")
    failed = False
    for label, path, expected in files:
        try:
            status = "OK" if sha256(path) == expected else "MISMATCH"
        except OSError as exc:
            status = "UNREADABLE (" + type(exc).__name__ + ")"
        print(status, label)
        failed |= status != "OK"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
