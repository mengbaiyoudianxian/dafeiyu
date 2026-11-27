#!/usr/bin/env bash
# Install a new Linux x86_64 release without starting any service.
set -euo pipefail
umask 077
repo=$(cd "$(dirname "$0")/.." && pwd -P)
prefix=$(realpath -m "${1:-/opt/dafeiyu}")
uv=${UV_BIN:-uv}
python=${PYTHON_BIN:-python3.12}
[[ $(uname -sm) == 'Linux x86_64' ]] || { echo "Use Linux x86_64 and Python 3.12." >&2; exit 1; }
"$python" -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'
[[ "$prefix" != / && ! -e "$prefix/astrbot/data" ]] || { echo "Choose a fresh root; data already exists or path is invalid." >&2; exit 1; }
mkdir -p "$prefix"
exec 9>"$prefix/.dafeiyu-release.lock"
flock -n 9 || { echo "Another install is running." >&2; exit 1; }
envdir="$prefix/uv/tools/astrbot"
if [[ -e "$envdir" && ! -f "$prefix/.dafeiyu-installing" ]]; then
    echo "A separate environment already exists here. Choose a fresh prefix." >&2
    exit 1
fi
touch "$prefix/.dafeiyu-installing"
scratch=$(mktemp -d "$prefix/.release-XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
mkdir -p "$prefix/uv/tools" "$prefix/uv/bin" "$prefix/astrbot" "$prefix/logs" "$prefix/private"
if [[ ! -x "$envdir/bin/python" ]]; then "$uv" venv --python "$python" "$envdir"; fi
py="$envdir/bin/python"
"$uv" pip install --python "$py" --link-mode copy --no-deps -r "$repo/deploy/runtime/python-linux-x86_64.lock"
wheel=${DAFEIYU_UPSTREAM_WHEEL:-}
if [[ -z "$wheel" ]]; then
    wheel="$scratch/astrbot-4.29.4-py3-none-any.whl"
    "$python" - "$repo/framework/manifest.json" "$wheel" <<'PY'
import json, sys, urllib.request
from pathlib import Path
url = json.loads(Path(sys.argv[1]).read_text())["upstream"]["url"]
with urllib.request.urlopen(url, timeout=90) as response:
    Path(sys.argv[2]).write_bytes(response.read())
PY
fi
"$py" "$repo/framework/build_wheel.py" "$wheel" --output-dir "$scratch/wheels"
"$uv" pip install --python "$py" --no-deps --reinstall "$scratch"/wheels/*.whl
"$py" "$repo/external/apply_twikit.py"
if [[ $("$py" -c 'import urllib3; print(urllib3.__version__)') != 2.25.906 ]]; then
    "$uv" pip install --python "$py" --no-deps --reinstall 'urllib3-future==2.25.906'
fi
"$uv" pip check --python "$py"
"$py" "$repo/framework/test_chat.py"
args=()
if [[ -n "${DAFEIYU_PRIVATE_PROFILE:-}" ]]; then args+=(--private-profile "$DAFEIYU_PRIVATE_PROFILE"); fi
"$py" "$repo/deploy/runtime/profile.py" init "$scratch/runtime" "${args[@]}"
mirrors=()
if [[ -n "${DAFEIYU_PLUGIN_MIRRORS:-}" ]]; then mirrors+=(--mirrors "$DAFEIYU_PLUGIN_MIRRORS"); fi
"$py" "$repo/install_plugins.py" --dest "$scratch/runtime/data/plugins" "${mirrors[@]}"
mkdir -p "$prefix/codex-bridge" "$prefix/kit"
install -m 600 "$repo/services/codex-bridge/bridge.py" "$prefix/codex-bridge/bridge.py"
install -m 755 "$repo/deploy/start-napcat.sh" "$prefix/start-napcat.sh"
install -m 755 "$repo/tools/qq_proxy.sh" "$prefix/kit/qq_proxy.sh"
ln -sfn "$envdir/bin/astrbot" "$prefix/uv/bin/astrbot"
mv -T "$scratch/runtime/data" "$prefix/astrbot/data"
touch "$prefix/astrbot/.astrbot"
rm "$prefix/.dafeiyu-installing"
echo "Installed under $prefix. See docs/deploy.md for local credentials and systemd."

