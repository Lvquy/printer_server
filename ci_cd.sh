#!/usr/bin/env bash
# Update the installed Print Appliance from its GitHub ubuntu branch.
# Run this script from the installed source directory.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo ./ci_cd.sh [--branch ubuntu] [--check]

Fetch a branch from origin, build a fresh environment, and activate it for the
configured service. --check only compares commits and checks
for unresolved print jobs; it does not alter the checkout or service.
EOF
}
fail() { echo "ERROR: $*" >&2; exit 1; }

BRANCH=ubuntu
CHECK=0
while (($#)); do
  case $1 in
    --branch) (($# >= 2)) || fail 'Missing --branch value.'; BRANCH=$2; shift 2 ;;
    --check) CHECK=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ $(uname -s) == Linux && $EUID == 0 ]] || fail 'Run with sudo on the Pi.'
[[ $BRANCH =~ ^[A-Za-z0-9._/-]+$ ]] && [[ $BRANCH != -* ]] \
  || fail 'Branch name is invalid.'

SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP=$(dirname "$SOURCE")
SERVICE_NAME=$(basename "$APP")
[[ $APP == /opt/* && $SOURCE == "$APP/source" ]] \
  || fail 'Run the installed script from /opt/SERVICE_NAME/source/.'
[[ $SERVICE_NAME =~ ^[a-z][a-z0-9-]{0,30}$ ]] \
  || fail 'The installed service name is invalid.'
ENV_FILE="$APP/.env"
if [[ -f $ENV_FILE ]]; then
  CONFIGURED_SERVICE_NAME=$(sed -n 's/^PRINT_APPLIANCE_SERVICE_NAME=//p' "$ENV_FILE" | tail -1)
  [[ -z $CONFIGURED_SERVICE_NAME || $CONFIGURED_SERVICE_NAME == "$SERVICE_NAME" ]] \
    || fail "Service name in $ENV_FILE does not match $APP."
fi
DATA="/var/lib/$SERVICE_NAME"
SERVICE="$SERVICE_NAME.service"
ACCOUNT=$SERVICE_NAME
[[ -d $DATA && -x $APP/.venv/bin/python && -f $DATA/appliance.sqlite3 ]] \
  || fail 'Print Appliance is not installed completely.'
command -v git >/dev/null || fail 'Git is required.'
command -v curl >/dev/null || fail 'curl is required for the health check.'
[[ -z $(git -c safe.directory="$SOURCE" -C "$SOURCE" status --porcelain) ]] \
  || fail 'Installed source has local changes. Commit or discard them before updating.'

active_jobs() {
  /usr/bin/python3 - "$DATA/appliance.sqlite3" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    row = db.execute(
        "SELECT COUNT(*) FROM jobs WHERE status IN "
        "('queued','held','submitting','submitted','unknown')"
    ).fetchone()
    print(row[0])
finally:
    db.close()
PY
}

CURRENT=$(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD)
DEPLOYED=$(cat "$APP/SOURCE_COMMIT")
[[ $DEPLOYED =~ ^[0-9a-f]{40}$ ]] || fail 'Installed commit marker is invalid.'
git -c safe.directory="$SOURCE" -C "$SOURCE" fetch --prune origin "$BRANCH"
TARGET=$(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse "origin/$BRANCH")
ACTIVE=$(active_jobs)
echo "Current: $CURRENT"
echo "Deployed: $DEPLOYED"
echo "Target:  $TARGET"
echo "Active or unresolved print jobs: $ACTIVE"
[[ $ACTIVE == 0 ]] || fail 'Wait for jobs to finish or resolve them before updating.'

if ((CHECK)); then
  if [[ $CURRENT == "$TARGET" && $DEPLOYED == "$TARGET" ]]; then
    echo 'Already up to date; no changes made.'
  else
    echo 'Preflight passed; no source or service changes made.'
  fi
  exit 0
fi

if [[ $CURRENT == "$TARGET" && $DEPLOYED == "$TARGET" ]]; then
  echo 'Already up to date; no changes made.'
  exit 0
fi
[[ -t 0 ]] || fail 'An interactive terminal is required to confirm update.'
read -r -p "Update $SERVICE to $TARGET? [y/N] " ANSWER
[[ $ANSWER == y || $ANSWER == Y ]] || fail 'Cancelled.'

STAGE=$(mktemp -d "$APP/.git-update.XXXXXXXX")
chgrp "$ACCOUNT" "$STAGE"
chmod 0750 "$STAGE"
PREV_VENV=''
BACKUP=''
STOPPED=0
ACTIVATING=0
SOURCE_UPDATED=0
SUCCEEDED=0
cleanup() {
  result=$?
  if (( ! SUCCEEDED )); then
    if (( ACTIVATING )); then
      echo 'Activation failed. Restoring the previous environment and source.' >&2
      systemctl stop "$SERVICE" || true
      [[ ! -e $APP/.venv ]] || mv -- "$APP/.venv" "$STAGE/failed-venv"
      [[ -z $PREV_VENV || ! -e $PREV_VENV ]] || mv -- "$PREV_VENV" "$APP/.venv"
    elif (( STOPPED )); then
      systemctl start "$SERVICE" || true
    fi
    if (( SOURCE_UPDATED )); then
      git -c safe.directory="$SOURCE" -C "$SOURCE" switch --detach --quiet "$CURRENT" \
        || echo 'Could not restore the previous source checkout; inspect it before retrying.' >&2
    fi
    if (( ACTIVATING )); then
      systemctl start "$SERVICE" || echo 'Previous service could not restart; inspect its log.' >&2
    fi
  fi
  rm -rf -- "$STAGE"
  exit "$result"
}
trap cleanup EXIT

# Existing installations were created at a detached commit. Make a
# local branch once, then use fast-forward-only pulls on later updates.
if git -c safe.directory="$SOURCE" -C "$SOURCE" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git -c safe.directory="$SOURCE" -C "$SOURCE" switch --quiet "$BRANCH"
else
  git -c safe.directory="$SOURCE" -C "$SOURCE" switch --quiet --track -c "$BRANCH" "origin/$BRANCH"
fi
git -c safe.directory="$SOURCE" -C "$SOURCE" pull --ff-only origin "$BRANCH"
SOURCE_UPDATED=1
[[ $(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD) == "$TARGET" ]] \
  || fail 'Source checkout did not reach the requested commit.'

UV=/opt/print-appliance/tools/bin/uv
[[ -x $UV && $($UV --version) == 'uv 0.8.22' ]] || UV="$APP/tools/bin/uv"
[[ -x $UV && $($UV --version) == 'uv 0.8.22' ]] \
  || fail 'uv 0.8.22 is required on the Pi.'
/usr/bin/python3 -m venv --system-site-packages "$STAGE/.venv"
VIRTUAL_ENV="$STAGE/.venv" "$UV" sync \
  --project "$SOURCE" --active --frozen --no-dev --no-editable --python /usr/bin/python3
runuser -u "$ACCOUNT" -- "$STAGE/.venv/bin/python" -c 'import cups; print("CUPS binding OK")'

DATA_BYTES=$(du -sb "$DATA" | awk '{print $1}')
AVAILABLE_BYTES=$(df -B1 --output=avail /var/backups | tail -1)
((AVAILABLE_BYTES >= DATA_BYTES + 100 * 1024 * 1024)) \
  || fail 'Not enough disk space for a full data backup and 100 MiB reserve.'

systemctl stop "$SERVICE"
STOPPED=1
ACTIVE=$(active_jobs)
[[ $ACTIVE == 0 ]] || fail "Found $ACTIVE active or unresolved jobs after stopping; update cancelled."
STAMP=$(date -u +%Y%m%d%H%M%S)
BACKUP="/var/backups/$SERVICE_NAME/$STAMP"
install -d -m 0700 "$BACKUP"
cp -a -- "$DATA" "$BACKUP/data"

PREV_VENV="$APP/.venv.previous-$STAMP"
ACTIVATING=1
mv -- "$APP/.venv" "$PREV_VENV"
mv -- "$STAGE/.venv" "$APP/.venv"
# Console scripts generated in the temporary virtual environment contain its
# absolute path in the shebang; correct it before systemd starts the service.
/usr/bin/python3 - "$STAGE/.venv/bin" "$APP/.venv/bin" <<'PY'
from pathlib import Path
import sys

old = f"#!{sys.argv[1]}/".encode()
new = f"#!{sys.argv[2]}/".encode()
for script in Path(sys.argv[2]).iterdir():
    if not script.is_file() or script.is_symlink():
        continue
    with script.open("rb") as stream:
        first = stream.readline(512)
        if not first.startswith(old):
            continue
        rest = stream.read()
    script.write_bytes(new + first[len(old):] + rest)
PY
"$APP/.venv/bin/print-appliance" --help >/dev/null
systemctl start "$SERVICE"

LISTEN_CONF="/etc/systemd/system/$SERVICE.d/listen.conf"
[[ -f $LISTEN_CONF ]] || fail 'Listener configuration is missing.'
HOST=$(sed -n 's/^Environment=PRINT_APPLIANCE_HOST=//p' "$LISTEN_CONF" | tail -1)
PORT=$(sed -n 's/^Environment=PRINT_APPLIANCE_PORT=//p' "$LISTEN_CONF" | tail -1)
[[ -n $HOST && $PORT =~ ^[0-9]{4,5}$ ]] || fail 'Listener configuration is invalid.'
for attempt in {1..10}; do
  if curl --fail --silent --show-error --output /dev/null --max-time 3 "http://$HOST:$PORT/client"; then
    printf '%s\n' "$TARGET" > "$APP/SOURCE_COMMIT"
    SUCCEEDED=1
    echo "Updated $TARGET at http://$HOST:$PORT/"
    echo "Previous environment: $PREV_VENV"
    echo "Protected data backup: $BACKUP/data"
    exit 0
  fi
  sleep 1
done
fail 'New service did not pass its HTTP health check.'
