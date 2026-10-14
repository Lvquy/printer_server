#!/usr/bin/env bash
# Update only print-appliance-secondary. Runtime data and the 8081 service stay in place.
set -euo pipefail

usage() {
  echo 'Usage: sudo ./update-secondary.sh --bundle FILE --commit SHA [--check | --apply]'
}
fail() { echo "ERROR: $*" >&2; exit 1; }

BUNDLE=''
COMMIT=''
MODE=check
while (($#)); do
  case $1 in
    --bundle) (($# >= 2)) || fail 'Missing --bundle value.'; BUNDLE=$2; shift 2 ;;
    --commit) (($# >= 2)) || fail 'Missing --commit value.'; COMMIT=$2; shift 2 ;;
    --check) MODE=check; shift ;;
    --apply) MODE=apply; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

[[ $(uname -s) == Linux && $EUID == 0 ]] || fail 'Run as root on the Pi.'
[[ $COMMIT =~ ^[0-9a-f]{40}$ ]] || fail 'A full 40-character commit SHA is required.'
[[ -f $BUNDLE ]] || fail 'Git bundle is missing.'
APP=/opt/print-appliance-secondary
DATA=/var/lib/print-appliance-secondary
SERVICE=print-appliance-secondary.service
[[ -d $APP/source && -x $APP/.venv/bin/python && -d $DATA ]] \
  || fail 'Secondary instance is not installed. Use install-secondary.sh first.'
[[ -f $APP/SOURCE_COMMIT ]] || fail 'Installed commit marker is missing.'
[[ -z $(git -c safe.directory="$APP/source" -C "$APP/source" status --porcelain) ]] \
  || fail 'Installed source checkout has local changes.'
CURRENT=$(cat "$APP/SOURCE_COMMIT")
[[ $CURRENT =~ ^[0-9a-f]{40}$ ]] || fail 'Installed commit marker is invalid.'
[[ $(git -c safe.directory="$APP/source" -C "$APP/source" rev-parse HEAD) == "$CURRENT" ]] \
  || fail 'Installed source and commit marker disagree.'
git bundle list-heads "$BUNDLE" | awk '{print $1}' | grep -Fxq "$COMMIT" \
  || fail 'Bundle does not contain the requested commit as its head.'
command -v curl >/dev/null || fail 'curl is required for the health check.'

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

[[ -f $DATA/appliance.sqlite3 ]] || fail 'Print database is missing.'
ACTIVE=$(active_jobs)
echo "Current: $CURRENT"
echo "Target:  $COMMIT"
echo "Active or unresolved print jobs: $ACTIVE"
[[ $ACTIVE == 0 ]] || fail 'Wait for jobs to finish or resolve them before deploying.'
if [[ $CURRENT == "$COMMIT" ]]; then
  echo 'Already deployed; no changes made.'
  exit 0
fi
if [[ $MODE == check ]]; then
  echo 'Preflight passed; no service changes made.'
  exit 0
fi
[[ -t 0 ]] || fail 'An interactive terminal is required to confirm deployment.'
read -r -p 'Deploy this commit to the secondary service? [y/N] ' ANSWER
[[ $ANSWER == y || $ANSWER == Y ]] || fail 'Cancelled.'

UV=/opt/print-appliance/tools/bin/uv
[[ -x $UV && $($UV --version) == 'uv 0.8.22' ]] || UV="$APP/tools/bin/uv"
[[ -x $UV && $($UV --version) == 'uv 0.8.22' ]] \
  || fail 'uv 0.8.22 is required on the Pi.'
STAMP=$(date -u +%Y%m%d%H%M%S)
PREV_SOURCE="$APP/source.previous-$STAMP"
PREV_VENV="$APP/.venv.previous-$STAMP"
BACKUP="/var/backups/print-appliance-secondary/$STAMP"
[[ ! -e $PREV_SOURCE && ! -e $PREV_VENV && ! -e $BACKUP ]] \
  || fail 'A previous deployment already uses this timestamp; retry in a second.'
STAGE=$(mktemp -d "$APP/.deploy.XXXXXXXX")
# mktemp creates a root-only directory; the service account must traverse it
# for the read-only CUPS import check before activation.
chgrp print-appliance-secondary "$STAGE"
chmod 0750 "$STAGE"
STOPPED=0
ACTIVATING=0
SUCCEEDED=0
cleanup() {
  result=$?
  if ((ACTIVATING && !SUCCEEDED)); then
    echo 'Activation failed. Restoring the previous application code.' >&2
    systemctl stop "$SERVICE" || true
    if [[ -e $PREV_SOURCE ]]; then
      [[ ! -e $APP/source ]] || mv -- "$APP/source" "$STAGE/failed-source"
      mv -- "$PREV_SOURCE" "$APP/source"
    fi
    if [[ -e $PREV_VENV ]]; then
      [[ ! -e $APP/.venv ]] || mv -- "$APP/.venv" "$STAGE/failed-venv"
      mv -- "$PREV_VENV" "$APP/.venv"
    fi
    systemctl start "$SERVICE" || echo 'Previous service could not restart; inspect its log.' >&2
    echo "Protected data backup: $BACKUP/data" >&2
  elif ((STOPPED && !SUCCEEDED)); then
    systemctl start "$SERVICE" || echo 'Previous service could not restart; inspect its log.' >&2
  fi
  # Keep failed builds for inspection only when activation failed.
  if ((SUCCEEDED)) && [[ -d $STAGE ]]; then rmdir -- "$STAGE"; fi
  exit "$result"
}
trap cleanup EXIT

git clone --quiet --no-local "$BUNDLE" "$STAGE/source"
git -C "$STAGE/source" checkout --quiet --detach "$COMMIT"
git -C "$STAGE/source" remote set-url origin https://github.com/Lvquy/printer_server.git
/usr/bin/python3 -m venv --system-site-packages "$STAGE/.venv"
VIRTUAL_ENV="$STAGE/.venv" "$UV" sync \
  --project "$STAGE/source" --active --frozen --no-dev --no-editable --python /usr/bin/python3
runuser -u print-appliance-secondary -- "$STAGE/.venv/bin/python" -c 'import cups; print("CUPS binding OK")'

systemctl stop "$SERVICE"
STOPPED=1
ACTIVE=$(active_jobs)
if [[ $ACTIVE != 0 ]]; then
  fail "Found $ACTIVE active or unresolved jobs after stopping; deployment cancelled."
fi
install -d -m 0700 "$BACKUP"
cp -a -- "$DATA" "$BACKUP/data"
ACTIVATING=1
mv -- "$APP/source" "$PREV_SOURCE"
mv -- "$APP/.venv" "$PREV_VENV"
mv -- "$STAGE/source" "$APP/source"
mv -- "$STAGE/.venv" "$APP/.venv"
# Console scripts created by uv contain the staging venv path in their shebang.
# Repoint them after the atomic move, before systemd tries to execute one.
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
HEALTHY=0
for attempt in {1..10}; do
  if curl --fail --silent --show-error --output /dev/null --max-time 3 \
    "http://$HOST:$PORT/client"; then
    HEALTHY=1
    break
  fi
  sleep 1
done
((HEALTHY)) || fail 'New service did not pass its HTTP health check.'
systemctl is-active --quiet "$SERVICE" || fail 'New service is not active.'
printf '%s\n' "$COMMIT" > "$APP/SOURCE_COMMIT"
SUCCEEDED=1
echo "Deployed $COMMIT at http://$HOST:$PORT/"
echo "Previous code: $PREV_SOURCE and $PREV_VENV"
echo "Protected data backup: $BACKUP/data"
