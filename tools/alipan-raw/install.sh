#!/bin/sh
set -eu
umask 077
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
program="$HOME/.local/share/alipan-raw"
settings="$HOME/.config/alipan-raw"
units="$HOME/.config/systemd/user"
install -d -m 700 "$program" "$settings" "$units"
install -m 600 "$here/backup.py" "$here/pan_direct.py" "$here/pan_sha256.py" "$program/"
install -m 600 "$here/alipan-raw.service" "$units/"
if [ ! -e "$settings/job.env" ]; then
    install -m 600 "$here/job.env.example" "$settings/job.env"
fi
printf '%s\n' "Installed code and unit. Set the paths in $settings/job.env before starting the service."
