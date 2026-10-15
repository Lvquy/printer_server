#!/usr/bin/env bash
# Install a Print Appliance instance from this repository checkout.
# Does not create/import printers, change CUPS queues, or submit print jobs.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo ./install-secondary.sh [--check] [--host DEVICE_IPV4] [--port PORT]

Copy .env.example to .env and set PRINT_APPLIANCE_HOST / PRINT_APPLIANCE_PORT.
Set PRINT_APPLIANCE_SERVICE_NAME and PRINT_APPLIANCE_ADMIN_PASSWORD as well.
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
      PRINT_APPLIANCE_SERVICE_NAME) SERVICE_NAME=$value ;;
      PRINT_APPLIANCE_HOST) HOST=$value ;;
      PRINT_APPLIANCE_PORT) PORT=$value ;;
      PRINT_APPLIANCE_ADMIN_PASSWORD) ADMIN_PASSWORD=$value ;;
      *) fail "Unknown setting in $ENV_FILE: $key" ;;
    esac
  done < "$ENV_FILE"
}

HOST=''
PORT=''
SERVICE_NAME=''
ADMIN_PASSWORD=''
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
[[ $SERVICE_NAME =~ ^[a-z][a-z0-9-]{0,30}$ ]] \
  || fail 'PRINT_APPLIANCE_SERVICE_NAME must use lowercase letters, digits and hyphens.'
[[ ${#ADMIN_PASSWORD} -ge 12 && $ADMIN_PASSWORD != CHANGE_THIS* ]] \
  || fail 'Set PRINT_APPLIANCE_ADMIN_PASSWORD to a private password of at least 12 characters.'
ENV_MODE=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || true)
[[ -n $ENV_MODE ]] || fail "Create $ENV_FILE from .env.example before installation."
(( (8#$ENV_MODE & 8#077) == 0 )) \
  || fail "$ENV_FILE contains a password; run: chmod 600 $ENV_FILE"
[[ $PORT =~ ^[0-9]{4,5}$ ]] && ((10#$PORT >= 1024 && 10#$PORT <= 65535)) \
  || fail 'Port must be an integer from 1024 to 65535.'
PORT=$((10#$PORT))
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

SERVICE="$SERVICE_NAME.service"
APP="/opt/$SERVICE_NAME"
DATA="/var/lib/$SERVICE_NAME"
UNIT="/etc/systemd/system/$SERVICE"
ACCOUNT=$SERVICE_NAME
for path in "$APP" "$DATA" "$UNIT" "$UNIT.d"; do
  [[ ! -e $path && ! -L $path ]] || fail "Already exists: $path. Refusing to overwrite an installation."
done
! id "$ACCOUNT" >/dev/null 2>&1 || fail 'Service account already exists.'
! getent group "$ACCOUNT" >/dev/null || fail 'Service group already exists.'
! systemctl cat "$SERVICE" >/dev/null 2>&1 \
  || fail 'Service definition already exists.'
[[ -f $SOURCE/uv.lock ]] \
  || fail 'Run from a complete repository checkout.'
command -v git >/dev/null || fail 'Git is required.'
[[ -z $(git -c safe.directory="$SOURCE" -C "$SOURCE" status --porcelain) ]] \
  || fail 'Checkout has local changes. Commit them before installation.'
COMMIT=$(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD)

echo "Print appliance: http://$HOST:$PORT/ at commit $COMMIT"
echo "It uses $APP, $DATA and $SERVICE."
if ((CHECK)); then
  echo 'Preflight passed; no changes made.'
  exit 0
fi
[[ -t 0 ]] || fail 'An interactive terminal is required to confirm installation.'
read -r -p 'Install this Print Appliance? [y/N] ' ANSWER
[[ $ANSWER == y || $ANSWER == Y ]] || fail 'Cancelled.'
trap 'echo "Installation stopped. Inspect partial installation files before retrying; no data was deleted." >&2' ERR

# Skip the apt transaction when all required packages are already installed.
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
install -m 0600 "$ENV_FILE" "$APP/.env"
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

# Bootstrap only the fresh database with the password from the private .env.
printf '%s' "$ADMIN_PASSWORD" | runuser -u "$ACCOUNT" -- "$APP/.venv/bin/python" -c '
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
install -m 0644 /dev/stdin "$UNIT" <<EOF
[Unit]
Description=Print Appliance API and CUPS coordinator
After=network.target cups.service

[Service]
Type=simple
User=$ACCOUNT
Group=$ACCOUNT
SupplementaryGroups=lpadmin
Environment=PRINT_APPLIANCE_DATA_DIR=$DATA
Environment=PRINT_APPLIANCE_HOST=127.0.0.1
Environment=PRINT_APPLIANCE_PORT=$PORT
Environment=PRINT_APPLIANCE_SECURE_COOKIE=0
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/print-appliance run
Restart=on-failure
RestartSec=3
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=$DATA
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
EOF
install -d -m 0755 "$UNIT.d"
printf '[Service]\nEnvironment=PRINT_APPLIANCE_HOST=%s\nEnvironment=PRINT_APPLIANCE_PORT=%s\n' \
  "$HOST" "$PORT" > "$UNIT.d/listen.conf"
systemctl daemon-reload
systemctl enable "$SERVICE"
trap - ERR
echo "Installed. Start with: sudo systemctl start $SERVICE"
echo "Then open http://$HOST:$PORT/."
