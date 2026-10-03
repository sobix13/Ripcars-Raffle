#!/usr/bin/env bash
# A new physical virtual environment in a new release. Never relocate a venv.
set -Eeuo pipefail
RAFFLE_SOURCE="$(realpath "${1:-$(dirname "$0")/..}")"
if [ ! -f "$RAFFLE_SOURCE/main.py" ] || [ ! -f "$RAFFLE_SOURCE/VERSION" ]; then
    echo "Invalid Rip Cars Raffle release source." >&2
    exit 1
fi
RAFFLE_VERSION="$(<"$RAFFLE_SOURCE/VERSION")"
if [[ ! "$RAFFLE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid release version." >&2
    exit 1
fi
if [ "${2:-}" = "--verify-only" ]; then
    PYTHON_BIN="${PYTHON_BIN:-$RAFFLE_SOURCE/.venv/bin/python}" bash "$RAFFLE_SOURCE/run_tests.sh"
    exit 0
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Run installation with sudo, or use --verify-only for local tests." >&2
    exit 1
fi
RAFFLE_ROOT=/opt/ripcars-raffle
if [ -e "$RAFFLE_ROOT/current" ] && [ ! -L "$RAFFLE_ROOT/current" ]; then
    echo "Current is not a release symlink. Inspect it before installing." >&2
    exit 1
fi
if [ -e "$RAFFLE_ROOT/current.next" ] || [ -L "$RAFFLE_ROOT/current.next" ]; then
    echo "A pending release link exists. Inspect it before installing." >&2
    exit 1
fi
install -d -m 755 "$RAFFLE_ROOT"
exec 9>"$RAFFLE_ROOT/install.lock"
flock -n 9 || { echo "Another Raffle install is running." >&2; exit 1; }
RAFFLE_STAMP="$(date -u +%Y%m%dT%H%M%S%N)"
RAFFLE_RELEASE="$RAFFLE_ROOT/releases/$RAFFLE_VERSION-$RAFFLE_STAMP"
if [ -e "$RAFFLE_RELEASE" ]; then echo "Release exists." >&2; exit 1; fi
install -d -m 755 "$RAFFLE_RELEASE"
tar -C "$RAFFLE_SOURCE" --exclude=.venv --exclude=.env --exclude=.git --exclude=releases --exclude=logs --exclude=__pycache__ --exclude=.pytest_cache --exclude=data --exclude=backups --exclude='*.sqlite3*' --exclude='*.db*' -cf - . | tar -C "$RAFFLE_RELEASE" -xf -
python3 -m venv "$RAFFLE_RELEASE/.venv"
"$RAFFLE_RELEASE/.venv/bin/python" -m pip install -r "$RAFFLE_RELEASE/requirements-lock.txt"
PYTHON_BIN="$RAFFLE_RELEASE/.venv/bin/python" bash "$RAFFLE_RELEASE/run_tests.sh"
# Runtime accounts are configured only after this release passes every test.
getent group ripcars-bots >/dev/null || groupadd --system ripcars-bots
id ripcarsraffle >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/ripcars-raffle --shell /usr/sbin/nologin ripcarsraffle
usermod -aG ripcars-bots ripcarsraffle
install -d -m 700 -o ripcarsraffle -g ripcarsraffle /var/lib/ripcars-raffle
install -d -m 2770 -o root -g ripcars-bots /var/lib/ripcars-bots
for RAFFLE_SHARED in /var/lib/ripcars-bots/coordination.sqlite3 /var/lib/ripcars-bots/coordination.sqlite3-wal /var/lib/ripcars-bots/coordination.sqlite3-shm; do
    if [ -f "$RAFFLE_SHARED" ]; then chgrp ripcars-bots "$RAFFLE_SHARED"; chmod g+rw "$RAFFLE_SHARED"; fi
done
chown -R root:root "$RAFFLE_RELEASE"
chmod -R go-w "$RAFFLE_RELEASE"
if [ ! -e /etc/ripcars-raffle.env ]; then
    install -m 600 "$RAFFLE_SOURCE/.env.example" /etc/ripcars-raffle.env
fi
RAFFLE_PREVIOUS="$(readlink -f "$RAFFLE_ROOT/current" 2>/dev/null || true)"
if [ -n "$RAFFLE_PREVIOUS" ]; then printf '%s\n' "$RAFFLE_PREVIOUS" > "$RAFFLE_ROOT/previous-release"; fi
ln -s "$RAFFLE_RELEASE" "$RAFFLE_ROOT/current.next"
mv -Tf "$RAFFLE_ROOT/current.next" "$RAFFLE_ROOT/current"
install -m 644 "$RAFFLE_RELEASE/ripcars-raffle.service" /etc/systemd/system/ripcars-raffle.service
systemctl daemon-reload
echo "Installed and tested: $RAFFLE_RELEASE"
echo "Configure /etc/ripcars-raffle.env and explicitly start ripcars-raffle. No other bot service was changed."
