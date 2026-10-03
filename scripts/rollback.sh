#!/usr/bin/env bash
set -Eeuo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi
RAFFLE_ROOT=/opt/ripcars-raffle
exec 9>"$RAFFLE_ROOT/install.lock"
flock -n 9 || { echo "An install or rollback is running." >&2; exit 1; }
if [ ! -f "$RAFFLE_ROOT/previous-release" ]; then echo "No previous Raffle release was recorded." >&2; exit 1; fi
RAFFLE_PREVIOUS="$(<"$RAFFLE_ROOT/previous-release")"
RAFFLE_RESOLVED="$(realpath -e "$RAFFLE_PREVIOUS")"
case "$RAFFLE_RESOLVED" in "$RAFFLE_ROOT/releases/"*) ;; *) echo "Refusing a non-Raffle release target." >&2; exit 1;; esac
if [ ! -f "$RAFFLE_RESOLVED/main.py" ] || [ ! -x "$RAFFLE_RESOLVED/.venv/bin/python" ]; then echo "Previous release is incomplete." >&2; exit 1; fi
if [ -e "$RAFFLE_ROOT/current.next" ] || [ -L "$RAFFLE_ROOT/current.next" ]; then echo "Pending release needs review." >&2; exit 1; fi
PYTHON_BIN="$RAFFLE_RESOLVED/.venv/bin/python" bash "$RAFFLE_RESOLVED/run_tests.sh"
ln -s "$RAFFLE_RESOLVED" "$RAFFLE_ROOT/current.next"
mv -Tf "$RAFFLE_ROOT/current.next" "$RAFFLE_ROOT/current"
install -m 644 "$RAFFLE_RESOLVED/ripcars-raffle.service" /etc/systemd/system/ripcars-raffle.service
systemctl daemon-reload
echo "Code rolled back. Inspect journal and restart ripcars-raffle explicitly. Databases, entries and results were not rolled back."
