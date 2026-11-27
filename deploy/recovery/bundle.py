#!/usr/bin/env python3
"""Capture and verify a local source-only supplement. Linux Python 3.9+, no network."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile
from importlib.metadata import distributions


GROUPS = ("plugins", "kit", "codex-bridge", "site-packages", "napcat", "anime-studio")
MANIFEST = "MANIFEST.json"
MAX_FILES, MAX_MEMBER, MAX_TOTAL = 20000, 128 << 20, 1024 << 20
SOURCE = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".css", ".vue", ".html"}
EXCLUDE = {"data", "plugin_data", ".git", "logs", "cache", "caches", "__pycache__",
           "backup", "backups", "tmp", "temp", "session", "sessions", "plugins_data",
           "node_modules", "tests", "test", "examples", "docs", "venv", "dist-info",
           "user_data", "uploads", "downloads", "profile", "profiles", "chat",
           "history", "local storage", "indexeddb", "blob_storage", "cookies"}


def relative(name):
    p = PurePosixPath(name)
    if (not name or str(p) != name or p.is_absolute() or ".." in p.parts
            or any(x.startswith(".") and x not in {".well-known"} for x in p.parts)
            or re.search(r"[\\:\x00-\x1f\x7f]", name)):
        raise ValueError("Unsafe archive path")
    return p


def safe_path(path):
    path = Path(path).absolute()
    if path != path.resolve(strict=False):
        raise ValueError("Link or ambiguous path in supplied location")
    return path


def new_output(path):
    path = safe_path(path)
    if any((a / ".git").exists() or (a / ".git").is_symlink()
           or ((a / "HEAD").is_file() and (a / "objects").is_dir() and (a / "refs").is_dir())
           for a in (path, *path.parents)):
        raise ValueError("Output must be outside a Git worktree")
    return path


def tree_sources(root):
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d.lower() not in EXCLUDE and not d.startswith(".")
                   and not re.search(r"backup|snapshot|^bak[-_]|[._-]bak$", d, re.I)
                   and not (Path(base) / d).is_symlink()]
        for name in files:
            f = Path(base) / name
            if (f.suffix.lower() in SOURCE and not name.startswith(".")
                    and not f.is_symlink() and not re.search(r"\.bak\b|\.old\b", name, re.I)):
                yield f.relative_to(root).as_posix()


def open_member(root, rel):
    """Follow only real directories and require a regular, unlinked leaf."""
    parts = relative(rel).parts
    d = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=d)
            os.close(d)
            d = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
    finally:
        os.close(d)
    f = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        f.close()
        raise ValueError("Non-regular source file")
    return f


def stream_digest(f):
    h = hashlib.sha256()
    for block in iter(lambda: f.read(1024 * 1024), b""):
        h.update(block)
    return h.hexdigest()


def fingerprint(path):
    with path.open("rb") as f:
        return stream_digest(f)


def changed_python(site):
    for dist in distributions(path=[str(site)]):
        for f in dist.files or ():
            rel = f.as_posix()
            if (f.suffix != ".py" or not f.hash or f.hash.mode != "sha256"
                    or any(s.startswith(".") for s in f.parts) or f.is_absolute()
                    or not (site / f).is_file() or (site / f).is_symlink()):
                continue
            target = site / f
            if target != target.resolve():
                continue
            expected = base64.urlsafe_b64decode(f.hash.value + "==").hex()
            if fingerprint(target) != expected:
                yield rel


def candidates(roots, catalog):
    paths = {k: set() for k in GROUPS}
    for plugin, listed in catalog["plugins"].items():
        if len(relative(plugin).parts) != 1:
            raise ValueError("Invalid plugin name")
        root = roots["plugins"] / plugin
        if not root.is_dir() or root.is_symlink() or not (root / "main.py").is_file():
            raise ValueError("Expected plugin missing: " + plugin)
        # A public variant may replace a source module; keep the actual local modules.
        names = {n for n in listed if (root / relative(n)).is_file()}
        names.update(tree_sources(root))
        paths["plugins"].update(plugin + "/" + n for n in names)
    kit = roots["kit"]
    paths["kit"].update(p.name for p in kit.iterdir() if p.suffix in {".sh", ".py"})
    for folder in ("dafeiyu-install/scripts",):
        if (kit / folder).is_dir():
            paths["kit"].update(folder + "/" + x for x in tree_sources(kit / folder))
    paths["codex-bridge"].add("bridge.py")
    paths["site-packages"].update(changed_python(roots["site-packages"]))
    for group in ("napcat", "anime-studio", "site-packages"):
        paths[group].update(catalog[group])
    return paths


class Reader:
    def __init__(self, stream):
        self.stream = stream
        self.hash = hashlib.sha256()

    def read(self, n):
        block = self.stream.read(n)
        self.hash.update(block)
        return block


def info(name, size, mode):
    t = tarfile.TarInfo(name)
    t.size = size
    t.mode = mode
    return t


def capture(args):
    dest = new_output(args.output)
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = dest.parent.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise ValueError("Archive directory must be owned by this user with mode 0700")
    if dest.exists() or dest.is_symlink():
        raise ValueError("Archive already exists")
    roots = {g: safe_path(getattr(args, g.replace("-", "_"))) for g in GROUPS}
    catalog = json.load(sys.stdin) if args.catalog == "-" else json.loads(Path(args.catalog).read_bytes())
    if catalog.get("format") != 1 or not catalog["plugins"]:
        raise ValueError("Invalid selection catalog")
    paths = candidates(roots, catalog)
    manifest = {}
    total = 0
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".capture-", delete=False) as temp:
        scratch = Path(temp.name)
    try:
        with scratch.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=3, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as archive:
                for group in GROUPS:
                    for rel in sorted(paths[group]):
                        name = group + "/" + rel
                        with open_member(roots[group], rel) as f:
                            before = os.fstat(f.fileno())
                            total += before.st_size
                            if before.st_size > MAX_MEMBER or total > MAX_TOTAL or len(manifest) >= MAX_FILES:
                                raise ValueError("Source selection exceeds archive bounds")
                            mode = 0o700 if before.st_mode & 0o111 else 0o600
                            entry = info(name, before.st_size, mode)
                            reader = Reader(f)
                            archive.addfile(entry, reader)
                            after = os.fstat(f.fileno())
                            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                                raise ValueError("Source changed during capture")
                            manifest[name] = [before.st_size, mode, reader.hash.hexdigest()]
                body = json.dumps({"format": 1, "files": manifest}, ensure_ascii=False,
                                  separators=(",", ":")).encode()
                archive.addfile(info(MANIFEST, len(body), 0o600), io.BytesIO(body))
        with scratch.open("rb") as f:
            os.fsync(f.fileno())
        verify(scratch)
        digest = fingerprint(scratch)
        os.link(scratch, dest)
        directory = os.open(dest.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        print(json.dumps({"sha256": digest, "size": dest.stat().st_size,
                          "files": len(manifest), "uncompressed": total,
                          "groups": {k: len(v) for k, v in paths.items()}}))
    finally:
        scratch.unlink(missing_ok=True)


def verify(archive, output=None):
    seen = {}
    manifest = None
    size = 0
    with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        for member in tar:
            p = relative(member.name)
            if manifest is not None or member.type != tarfile.REGTYPE or member.size < 0:
                raise ValueError("Unexpected archive member")
            if member.mode not in (0o600, 0o700):
                raise ValueError("Unexpected permissions")
            if p.as_posix() == MANIFEST:
                if member.size > 8 << 20:
                    raise ValueError("Manifest too large")
                manifest = json.load(tar.extractfile(member))
                continue
            if p.parts[0] not in GROUPS or len(p.parts) < 2 or member.name in seen:
                raise ValueError("Duplicate or unknown archive path")
            size += member.size
            if len(seen) >= MAX_FILES or member.size > MAX_MEMBER or size > MAX_TOTAL:
                raise ValueError("Oversize archive")
            reader = Reader(tar.extractfile(member))
            target = None
            if output is not None:
                dest = output / p
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target = dest.open("xb")
            try:
                count = 0
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    count += len(block)
                    if target:
                        target.write(block)
                if target:
                    os.fchmod(target.fileno(), member.mode)
                if count != member.size:
                    raise ValueError("Truncated archive member")
            finally:
                if target:
                    target.close()
            seen[member.name] = [member.size, member.mode, reader.hash.hexdigest()]
        # Read the gzip trailer and reject extra non-padding material.
        rest = gz.read(1024 * 1024 + 1)
        if len(rest) > 1024 * 1024 or any(rest):
            raise ValueError("Unexpected bytes after end of archive")
    if not seen or manifest != {"format": 1, "files": seen}:
        raise ValueError("Manifest mismatch")
    return len(seen)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture")
    for group in GROUPS:
        cap.add_argument("--" + group, type=Path, required=True)
    cap.add_argument("--catalog", default=str(Path(__file__).with_name("catalog.json")))
    cap.add_argument("--output", required=True, type=Path)
    for op in ("verify", "unpack"):
        cmd = sub.add_parser(op)
        cmd.add_argument("archive", type=Path)
        cmd.add_argument("--sha256", required=op == "unpack")
        if op == "unpack":
            cmd.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        return capture(args)
    path = safe_path(args.archive)
    with open_member(path.parent, path.name) as archive:
        before = os.fstat(archive.fileno())
        if args.sha256:
            if (not re.fullmatch(r"[0-9a-fA-F]{64}", args.sha256)
                    or stream_digest(archive) != args.sha256.lower()):
                raise ValueError("Archive digest mismatch")
            archive.seek(0)
        if args.command == "verify":
            print("Verified files:", verify(archive))
            return
        dest = new_output(args.output)
        if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
            raise ValueError("Unpack destination must be new or empty")
        parent = safe_path(dest.parent)
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=parent) as td:
            staging = Path(td) / "files"
            staging.mkdir(mode=0o700)
            count = verify(archive, staging)
            after = os.fstat(archive.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Archive changed during extraction")
            os.rename(staging, dest)
        print("Restored files:", count)


if __name__ == "__main__":
    main()
