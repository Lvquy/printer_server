#!/usr/bin/env bash
# Fresh Debian 12 installation only. Does not install printer drivers or submit jobs.
set -euo pipefail

if [[ ${1:-} == --help ]]; then
  echo 'Usage: sudo ./install.sh (interactive, fresh Debian 12 installation only)'
  exit 0
fi
[[ $# == 0 ]] || { echo 'Unknown arguments. Use --help.' >&2; exit 1; }
fail() { echo "ERROR: $*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || fail 'Run on the Debian device, not your workstation.'
[[ $EUID == 0 ]] || fail 'Run with sudo.'
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 12 ]] || fail 'Debian 12 is required.'
[[ -t 0 ]] || fail 'An interactive terminal is required for configuration.'

APP=/opt/print-appliance
DATA=/var/lib/print-appliance
UNIT=/etc/systemd/system/print-appliance.service
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for path in "$APP" "$DATA" "$UNIT" "$UNIT.d"; do
  [[ ! -e $path && ! -L $path ]] || fail "Already exists: $path. Use docs/git-deployment.md; do not delete existing data."
done
! id print-appliance >/dev/null 2>&1 || fail 'Service account already exists; inspect the previous installation.'
! getent group print-appliance >/dev/null || fail 'Service group already exists.'
! systemctl cat print-appliance.service >/dev/null 2>&1 || fail 'A service definition already exists.'
[[ -f $SOURCE/uv.lock && -f $SOURCE/deploy/print-appliance.service ]] || fail 'Run from a complete repository checkout.'
command -v git >/dev/null || fail 'Install git and clone the repository first.'
[[ -z $(git -c safe.directory="$SOURCE" -C "$SOURCE" status --porcelain) ]] || fail 'Checkout has local changes. Commit/review them before installation.'
COMMIT=$(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD)

read -r -p 'Device LAN IPv4 (Enter = local-only 127.0.0.1): ' HOST
HOST=${HOST:-127.0.0.1}
/usr/bin/python3 - "$HOST" <<'PY'
import ipaddress
import sys
ip = ipaddress.IPv4Address(sys.argv[1])
if ip.is_unspecified or ip.is_multicast or ip.is_link_local or int(ip) == 0xffffffff:
    raise SystemExit('Use a specific device LAN IPv4 or 127.0.0.1.')
PY
# Reject addresses not assigned to this device before installing anything.
ip -4 -o addr show | awk '{split($4,a,"/"); print a[1]}' | grep -Fxq "$HOST" \
  || fail 'This IPv4 is not assigned to the device.'
echo "Install commit $COMMIT; listen on http://$HOST:8081."
echo 'HTTP is unencrypted: use only a trusted LAN. Drivers remain a separate installation.'
read -r -p 'Continue? [y/N] ' ANSWER
[[ $ANSWER == y || $ANSWER == Y ]] || fail 'Cancelled.'
trap 'echo "Installation stopped. Partial files may remain; inspect them before retrying. No automatic deletion of data." >&2' ERR

apt-get update
apt-get install -y --no-install-recommends cups python3-cups python3-venv ca-certificates
systemctl enable --now cups
useradd --system --user-group --home-dir "$DATA" --shell /usr/sbin/nologin print-appliance
usermod -a -G lpadmin print-appliance
install -d -o print-appliance -g print-appliance -m 0750 "$DATA"
install -d -o root -g print-appliance -m 0750 "$APP"
# Install a committed snapshot, not a mutable user's working tree.
git clone --no-local "$SOURCE" "$APP/source"
git -C "$APP/source" checkout --detach "$COMMIT"
git -C "$APP/source" remote set-url origin https://github.com/Lvquy/printer_server.git
printf '%s\n' "$COMMIT" > "$APP/SOURCE_COMMIT"
/usr/bin/python3 -m venv --system-site-packages "$APP/.venv"
/usr/bin/python3 -m venv "$APP/tools"
"$APP/tools/bin/pip" install 'uv==0.8.22'
VIRTUAL_ENV="$APP/.venv" "$APP/tools/bin/uv" sync \
  --project "$APP/source" --active --frozen --no-dev --no-editable --python /usr/bin/python3
runuser -u print-appliance -- "$APP/.venv/bin/python" -c 'import cups; print("CUPS binding OK")'
# Fresh data directory only; never reset credentials on an existing installation.
runuser -u print-appliance -- "$APP/.venv/bin/python" - "$DATA" <<'BOOTSTRAP'
import sys
from dataclasses import replace
from pathlib import Path
from print_appliance.cli import _bootstrap
from print_appliance.config import Settings
settings = replace(Settings.from_env(), data_dir=Path(sys.argv[1]))
if settings.database_path.exists():
    raise SystemExit("Database already exists; refusing to reset credentials.")
_bootstrap(settings, "admin12345678")
BOOTSTRAP
install -m 0644 "$APP/source/deploy/print-appliance.service" "$UNIT"
install -d -m 0755 "$UNIT.d"
printf '[Service]\nEnvironment=PRINT_APPLIANCE_HOST=%s\n' "$HOST" > "$UNIT.d/listen.conf"
systemctl daemon-reload
systemctl enable print-appliance
trap - ERR
echo 'Initial web password: admin12345678. Change it immediately after first login.'
echo 'Installed. Start with: sudo systemctl start print-appliance'
echo "Then open http://$HOST:8081/ (local-only when 127.0.0.1)."
