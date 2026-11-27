"""Rebuild the audited AstrBot wheel from an exact official input wheel."""

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZIP_STORED, ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
DIST_INFO = "astrbot-4.29.4.dist-info"


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def patched_files(original, manifest, scratch):
    patch = HERE / "astrbot-4.29.4.patch"
    if file_digest(patch) != manifest["patch_sha256"]:
        raise ValueError("Patch checksum does not match manifest.json")
    for item in manifest["files"]:
        name = item["path"]
        raw = original.read(name)
        if hashlib.sha256(raw).hexdigest() != item["before_sha256"]:
            raise ValueError(f"Unexpected original source: {name}")
        target = scratch / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--no-index", str(patch)],
        cwd=scratch,
        check=True,
    )
    replacements = {}
    for item in manifest["files"]:
        name = item["path"]
        data = (scratch / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["after_sha256"]:
            raise ValueError(f"Patched source differs from the deployment: {name}")
        replacements[name] = data
    return replacements


def write_member(wheel, name, data, source_info):
    entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = source_info.external_attr
    wheel.writestr(entry, data, compress_type=ZIP_STORED)


def write_rebuilt_wheel(original, output, replacements, build_tag):
    wheel_name = f"{DIST_INFO}/WHEEL"
    record_name = f"{DIST_INFO}/RECORD"
    replacements[wheel_name] = original.read(wheel_name) + f"Build: {build_tag}\n".encode()
    records = io.StringIO(newline="")
    writer = csv.writer(records, lineterminator="\n")
    with ZipFile(output, "w") as wheel:
        for name in sorted(original.namelist()):
            if name == record_name:
                continue
            data = replacements[name] if name in replacements else original.read(name)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            writer.writerow([name, "sha256=" + digest.decode("ascii"), len(data)])
            write_member(wheel, name, data, original.getinfo(name))
        writer.writerow([record_name, "", ""])
        write_member(wheel, record_name, records.getvalue().encode(), original.getinfo(record_name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("official_wheel", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if file_digest(args.official_wheel) != manifest["upstream"]["sha256"]:
        parser.error("Input SHA-256 does not match the official PyPI wheel")
    if not shutil.which("git"):
        parser.error("Git is required to apply the unified diff")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tag = manifest["build_tag"]
    name = manifest["upstream"]["filename"].replace("-py3-", f"-{tag}-py3-")
    destination = args.output_dir / name
    if destination.exists():
        if file_digest(destination) == manifest["rebuilt_sha256"]:
            print(f"Verified {destination}")
            return
        parser.error(f"Another file already exists at {destination}")
    with tempfile.TemporaryDirectory(dir=args.output_dir) as temp:
        scratch = Path(temp).resolve()
        with ZipFile(args.official_wheel) as original:
            replacements = patched_files(original, manifest, scratch)
            output = scratch / name
            write_rebuilt_wheel(original, output, replacements, tag)
        digest = file_digest(output)
        if digest != manifest["rebuilt_sha256"]:
            raise ValueError(f"Unexpected rebuilt wheel SHA-256: {digest}")
        output.replace(destination)
    print(f"{digest}  {destination}")


if __name__ == "__main__":
    main()
