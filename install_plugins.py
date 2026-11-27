"""Install the pinned Dafeiyu plugin set into an empty AstrBot plugins directory."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent


def run_git(*args, cwd=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    proc = subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "core.autocrlf=false", *map(str, args)],
        cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode:
        raise RuntimeError(f"git {args[0]} failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout.strip()


def safe_path(root, relative):
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"Invalid bundle path: {relative!r}")
    return path


def normalized_hash(path):
    data = path.read_bytes()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def remove_checkout_metadata(checkout):
    metadata = safe_path(checkout, ".git")

    def retry_readonly(operation, path, error):
        item = Path(path)
        if os.name != "nt" or not isinstance(error[1], PermissionError) or not item.resolve().is_relative_to(metadata):
            raise error[1]
        os.chmod(item, stat.S_IWRITE | stat.S_IREAD)
        operation(path)

    shutil.rmtree(metadata, onerror=retry_readonly)


def verify(folder, record):
    for name, expected in record["files_sha256_lf"].items():
        path = safe_path(folder, name)
        if not path.is_file() or normalized_hash(path) != expected:
            raise RuntimeError(f"Verification failed: {record['directory']}/{name}")
    if not (folder / "metadata.yaml").is_file() or not (folder / "main.py").is_file():
        raise RuntimeError(f"Missing plugin entry point in {folder.name}")


def prepare(record, directory, mirrors):
    name = record["directory"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(f"Invalid plugin directory name: {name!r}")
    target = safe_path(directory, name)
    if record["kind"] == "bundled":
        shutil.copytree(safe_path(ROOT, record["source"]), target)
    elif record["kind"] == "pinned-git":
        commit = record["commit"]
        if not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError(f"Invalid revision for {name}")
        slug = record["repository"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
            raise ValueError(f"Invalid repository for {name}")
        origin = str(mirrors / name) if mirrors else f"https://github.com/{slug}.git"
        target.mkdir()
        run_git("init", "--quiet", cwd=target)
        run_git("fetch", "--quiet", "--no-tags", "--depth=1", origin, commit, cwd=target)
        run_git("checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=target)
        if run_git("rev-parse", "HEAD", cwd=target) != commit:
            raise RuntimeError(f"Wrong revision for {name}")
        patch = record.get("patch")
        if patch:
            patch_file = safe_path(ROOT, patch)
            if normalized_hash(patch_file) != record["patch_sha256_lf"]:
                raise RuntimeError(f"Patch verification failed for {name}")
            run_git("apply", "--check", "--whitespace=nowarn", patch_file, cwd=target)
            run_git("apply", "--whitespace=nowarn", patch_file, cwd=target)
        remove_checkout_metadata(target)
    else:
        raise ValueError(f"Unknown plugin source for {name}")
    verify(target, record)


def install(destination, mirrors=None):
    records = json.loads((ROOT / "inventory.json").read_text(encoding="utf-8"))["plugins"]
    if len(records) != 31 or len({r["directory"] for r in records}) != len(records):
        raise ValueError("The inventory must describe 31 unique plugins.")
    if destination.is_symlink():
        raise ValueError("The destination must be a real directory.")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Choose a new or empty destination directory.")
    if mirrors:
        mirrors = mirrors.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix=".dafeiyu-plugins-", dir=destination.parent) as scratch:
        staging = Path(scratch).resolve() / "plugins"
        staging.mkdir()
        for rec in records:
            prepare(rec, staging, mirrors)
            print(f"Verified {rec['directory']}", flush=True)
        # Rename only fully verified output. A nonempty destination always survives a race.
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--mirrors", type=Path, help="Optional local Git repositories named by plugin directory")
    args = parser.parse_args()
    try:
        install(args.dest, args.mirrors)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
