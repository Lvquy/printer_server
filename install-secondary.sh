#!/usr/bin/env bash
# Install a second, isolated instance beside the existing print-appliance service.
# Does not create/import printers, change CUPS queues, or submit print jobs.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo ./install-secondary.sh [--check] [--host DEVICE_IPV4] [--port PORT]

Copy .env.example to .env and set PRINT_APPLIANCE_HOST / PRINT_APPLIANCE_PORT.
--host and --port can override .env for one invocation.
--check validates the configuration, free port, checkout and target paths without changes.
EOF
}
fail() { echo "ERROR: $*" >&2; exit 1; }

SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE="$SOURCE/.env"

load_env() {
  [[ -f $ENV_FILE ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n $line ]]; do
    [[ -z $line || $line == \#* ]] && continue
    [[ $line == *=* ]] || fail "Invalid line in $ENV_FILE: $line"
    key=${line%%=*}
    value=${line#*=}
    case $key in
      PRINT_APPLIANCE_HOST) HOST=$value ;;
      PRINT_APPLIANCE_PORT) PORT=$value ;;
      *) fail "Unknown setting in $ENV_FILE: $key" ;;
    esac
  done < "$ENV_FILE"
}

HOST=''
PORT=''
CHECK=0
load_env
while (($#)); do
  case $1 in
    --host) (($# >= 2)) || fail 'Missing --host value.'; HOST=$2; shift 2 ;;
    --port) (($# >= 2)) || fail 'Missing --port value.'; PORT=$2; shift 2 ;;
    --check) CHECK=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ $(uname -s) == Linux ]] || fail 'Run on the Debian device.'
[[ $EUID == 0 ]] || fail 'Run with sudo.'
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 12 ]] || fail 'Debian 12 is required.'
[[ -n $HOST ]] || fail "Create $ENV_FILE from .env.example and set PRINT_APPLIANCE_HOST."
[[ -n $PORT ]] || fail "Create $ENV_FILE from .env.example and set PRINT_APPLIANCE_PORT."
[[ $PORT =~ ^[0-9]{4,5}$ ]] && ((10#$PORT >= 1024 && 10#$PORT <= 65535)) \
  || fail 'Port must be an integer from 1024 to 65535.'
PORT=$((10#$PORT))
((PORT != 8081)) || fail 'Port 8081 is reserved for the existing instance.'
/usr/bin/python3 - "$HOST" <<'PY'
import ipaddress
import sys

try:
    ip = ipaddress.IPv4Address(sys.argv[1])
except ipaddress.AddressValueError as exc:
    raise SystemExit(f"Invalid IPv4: {exc}") from exc
if ip.is_unspecified or ip.is_multicast or ip.is_link_local or int(ip) == 0xffffffff:
    raise SystemExit('Use a specific device IPv4 or 127.0.0.1.')
PY
ip -4 -o addr show | awk '{split($4,a,"/"); print a[1]}' | grep -Fxq "$HOST" \
  || fail 'This IPv4 is not assigned to the device.'
ss -H -ltn "( sport = :$PORT )" | grep -q . \
  && fail "TCP port $PORT is already in use."

APP=/opt/print-appliance-secondary
DATA=/var/lib/print-appliance-secondary
UNIT=/etc/systemd/system/print-appliance-secondary.service
ACCOUNT=print-appliance-secondary
for path in "$APP" "$DATA" "$UNIT" "$UNIT.d"; do
  [[ ! -e $path && ! -L $path ]] || fail "Already exists: $path. Refusing to overwrite an installation."
done
! id "$ACCOUNT" >/dev/null 2>&1 || fail 'Secondary service account already exists.'
! getent group "$ACCOUNT" >/dev/null || fail 'Secondary service group already exists.'
! systemctl cat print-appliance-secondary.service >/dev/null 2>&1 \
  || fail 'Secondary service definition already exists.'
[[ -f $SOURCE/uv.lock && -f $SOURCE/deploy/print-appliance-secondary.service ]] \
  || fail 'Run from a complete repository checkout.'
command -v git >/dev/null || fail 'Git is required.'
[[ -z $(git -c safe.directory="$SOURCE" -C "$SOURCE" status --porcelain) ]] \
  || fail 'Checkout has local changes. Commit them before installation.'
COMMIT=$(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD)

echo "Secondary instance: http://$HOST:$PORT/ at commit $COMMIT"
echo "It uses $APP, $DATA and print-appliance-secondary.service."
if ((CHECK)); then
  echo 'Preflight passed; no changes made.'
  exit 0
fi
[[ -t 0 ]] || fail 'An interactive terminal is required to confirm installation.'
read -r -p 'Install this secondary instance? [y/N] ' ANSWER
[[ $ANSWER == y || $ANSWER == Y ]] || fail 'Cancelled.'
trap 'echo "Installation stopped. Inspect partial secondary files before retrying; no data was deleted." >&2' ERR

# On an existing appliance these packages should already be present. Avoid an
# apt transaction when they are, so the original service keeps running.
PACKAGES=(cups python3-cups python3-venv ca-certificates)
MISSING=()
for package in "${PACKAGES[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -Fxq 'install ok installed' \
    || MISSING+=("$package")
done
if ((${#MISSING[@]})); then
  apt-get update
  apt-get install -y --no-install-recommends "${MISSING[@]}"
fi
systemctl is-active --quiet cups.service || fail 'CUPS is not running; start it before installation.'

useradd --system --user-group --home-dir "$DATA" --shell /usr/sbin/nologin "$ACCOUNT"
usermod -a -G lpadmin "$ACCOUNT"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 0750 "$DATA"
install -d -o root -g "$ACCOUNT" -m 0750 "$APP"
git -c safe.directory="$SOURCE" clone --no-local "$SOURCE" "$APP/source"
git -C "$APP/source" checkout --detach "$COMMIT"
git -C "$APP/source" remote set-url origin https://github.com/Lvquy/printer_server.git
printf '%s\n' "$COMMIT" > "$APP/SOURCE_COMMIT"
/usr/bin/python3 -m venv --system-site-packages "$APP/.venv"
UV=/opt/print-appliance/tools/bin/uv
if [[ ! -x $UV || $($UV --version) != 'uv 0.8.22' ]]; then
  /usr/bin/python3 -m venv "$APP/tools"
  "$APP/tools/bin/pip" install 'uv==0.8.22'
  UV="$APP/tools/bin/uv"
fi
VIRTUAL_ENV="$APP/.venv" "$UV" sync \
  --project "$APP/source" --active --frozen --no-dev --no-editable --python /usr/bin/python3
runuser -u "$ACCOUNT" -- "$APP/.venv/bin/python" -c 'import cups; print("CUPS binding OK")'

# Bootstrap only the fresh database. Generate a unique web password and print
# it once at the end; do not put it in the repository or the systemd unit.
WEB_PASSWORD=$(/usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
printf '%s' "$WEB_PASSWORD" | runuser -u "$ACCOUNT" -- "$APP/.venv/bin/python" -c '
import sys
from dataclasses import replace
from pathlib import Path
from print_appliance.cli import _bootstrap
from print_appliance.config import Settings

settings = replace(Settings.from_env(), data_dir=Path(sys.argv[1]))
if settings.database_path.exists():
    raise SystemExit("Database already exists; refusing to reset credentials.")
_bootstrap(settings, sys.stdin.read())
' "$DATA"
install -m 0644 "$APP/source/deploy/print-appliance-secondary.service" "$UNIT"
install -d -m 0755 "$UNIT.d"
printf '[Service]\nEnvironment=PRINT_APPLIANCE_HOST=%s\nEnvironment=PRINT_APPLIANCE_PORT=%s\n' \
  "$HOST" "$PORT" > "$UNIT.d/listen.conf"
systemctl daemon-reload
systemctl enable print-appliance-secondary.service
trap - ERR
echo "Initial web password (shown once): $WEB_PASSWORD"
echo 'Installed. Start with: sudo systemctl start print-appliance-secondary.service'
echo "Then open http://$HOST:$PORT/ and change the web password."
