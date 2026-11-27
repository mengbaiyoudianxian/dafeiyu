#!/usr/bin/env python3
"""Apply or verify the user-schema compatibility patch in an installed twikit wheel."""

import argparse
import hashlib
from importlib.metadata import distributions
import os
from pathlib import Path
import re
import stat
import sysconfig
import tempfile


USER_RELEASE = {
    "febcfe61f07eeb7599920243dc70bf4ba5d864349df48f20703c1c1b7f9e1b89",
    "02ffe34ac895d19f18d0787b2f4d626a7a55cd3595d43b89b4537c03c56395c4",
}
USER_DEPLOYED = "838c2b0117f5c1df46ffe351a4e9294af2298e0e8a9789343ea441373f0f07c2"
CLIENT_RELEASE = {
    "73bf83702e481089be53f025b7d6b0bc0a8f7cf1594981782cfadaa90c543321",
    "6e7a26ca3a75352cc08f820f86e3385d8883046b39cff4742058697af5543c9c",
}
CLIENT_DEPLOYED = "61b7a242abdab0f57131b0689503e52b02a62e2553edafbdd716fcb19dc5f561"
PATCH = Path(__file__).with_name("twikit-user.patch")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patched_user(data):
    """Apply the reviewed unified diff against a byte-verified upstream file."""
    original = data.replace(b"\r\n", b"\n").splitlines(keepends=True)
    patch = PATCH.read_bytes().replace(b"\r\n", b"\n").splitlines(keepends=True)
    if patch[:2] != [b"--- a/twikit/user.py\n", b"+++ b/twikit/user.py\n"]:
        raise ValueError("Unexpected patch target")
    output = []
    index = 0
    for line in patch[2:]:
        if line.startswith(b"@@ "):
            match = re.fullmatch(rb"@@ -(\d+),\d+ \+\d+,\d+ @@[^\n]*\n", line)
            if not match or int(match[1]) - 1 < index:
                raise ValueError("Invalid patch offsets")
            end = int(match[1]) - 1
            output.extend(original[index:end])
            index = end
            continue
        if line[:1] in (b"-", b" "):
            if index >= len(original) or line[1:] != original[index]:
                raise ValueError("Patch context differs from upstream")
            index += 1
        elif line[:1] != b"+":
            raise ValueError("Invalid patch line")
        if line[:1] in (b"+", b" "):
            output.append(line[1:])
    output.extend(original[index:])
    result = b"".join(output)
    if digest(result) != USER_DEPLOYED:
        raise ValueError("Patched user.py differs from the expected deployment")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-packages", type=Path,
                        default=Path(sysconfig.get_path("purelib")))
    parser.add_argument("--check", action="store_true", help="verify only")
    parser.add_argument("--exact", action="store_true",
                        help="also require the historical client.py hash")
    args = parser.parse_args()
    matches = [dist for dist in distributions(path=[str(args.site_packages)])
               if dist.metadata.get("Name", "").lower() == "twikit"]
    if len(matches) != 1 or matches[0].version != "2.3.3":
        parser.exit(1, "Expected one installed twikit 2.3.3 distribution.\n")
    package = args.site_packages / "twikit"
    client = digest((package / "client" / "client.py").read_bytes())
    if client == CLIENT_DEPLOYED:
        print("client.py: historical deployment hash")
    elif client in CLIENT_RELEASE and not args.exact:
        print("client.py: upstream release")
    else:
        parser.exit(1, "client.py failed the requested fingerprint check.\n")
    path = package / "user.py"
    data = path.read_bytes()
    if digest(data) == USER_DEPLOYED:
        print("user.py: matches deployed schema patch")
        return
    if digest(data) not in USER_RELEASE:
        parser.exit(1, "Unknown user.py fingerprint.\n")
    if args.check:
        parser.exit(1, "user.py is the unpatched upstream file.\n")
    updated = patched_user(data)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            out.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != data:
            raise ValueError("user.py changed during patching")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print("user.py: applied and verified deployed schema patch")


if __name__ == "__main__":
    main()
