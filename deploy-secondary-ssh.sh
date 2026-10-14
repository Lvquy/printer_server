#!/usr/bin/env bash
# Run from a clean developer checkout. The Pi does not need GitHub credentials.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./deploy-secondary-ssh.sh --target pi@DEVICE_IP [--check | --apply]

--check  Compare the current and target commits; do not restart the service.
--apply  Build and activate the current commit on the secondary appliance.

Run from a clean Git checkout. SSH asks for the Pi password/key as usual.
EOF
}
fail() { echo "ERROR: $*" >&2; exit 1; }

TARGET=''
MODE=check
while (($#)); do
  case $1 in
    --target) (($# >= 2)) || fail 'Missing --target value.'; TARGET=$2; shift 2 ;;
    --check) MODE=check; shift ;;
    --apply) MODE=apply; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done
[[ $TARGET =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+$ ]] \
  || fail 'Use --target USER@HOST (for example pi@192.168.0.123).'
command -v git >/dev/null || fail 'Git is required.'
command -v ssh >/dev/null || fail 'SSH is required.'
command -v scp >/dev/null || fail 'SCP is required.'
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -z $(git -C "$SOURCE" status --porcelain) ]] || fail 'Commit local changes before deployment.'
COMMIT=$(git -C "$SOURCE" rev-parse HEAD)
[[ $COMMIT =~ ^[0-9a-f]{40}$ ]] || fail 'Cannot resolve the current commit.'

STAGE=$(mktemp -d)
REMOTE_ARCHIVE="/tmp/print-appliance-secondary-${COMMIT:0:12}-$RANDOM.tgz"
cleanup() { rm -f -- "$STAGE/source.bundle" "$STAGE/update-secondary.sh" "$STAGE/payload.tgz"; rmdir -- "$STAGE"; }
trap cleanup EXIT
git -C "$SOURCE" bundle create "$STAGE/source.bundle" HEAD
cp "$SOURCE/update-secondary.sh" "$STAGE/update-secondary.sh"
COPYFILE_DISABLE=1 tar -czf "$STAGE/payload.tgz" -C "$STAGE" source.bundle update-secondary.sh

echo "Sending commit $COMMIT to $TARGET; mode: $MODE"
scp "$STAGE/payload.tgz" "$TARGET:$REMOTE_ARCHIVE"
# The remote command uses only a locally generated path and fixed arguments.
# A terminal allows sudo and the final deployment confirmation to prompt.
ssh -tt "$TARGET" "set -eu; stage=\$(mktemp -d /tmp/print-appliance-secondary.XXXXXXXX); trap 'rm -f -- \"\$stage/source.bundle\" \"\$stage/update-secondary.sh\" \"$REMOTE_ARCHIVE\"; rmdir -- \"\$stage\"' EXIT; tar -xzf '$REMOTE_ARCHIVE' -C \"\$stage\"; sudo bash \"\$stage/update-secondary.sh\" --bundle \"\$stage/source.bundle\" --commit '$COMMIT' --$MODE"
