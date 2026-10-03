#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
RAFFLE_PYTHON="${PYTHON_BIN:-.venv/bin/python}"
if [ ! -x "$RAFFLE_PYTHON" ]; then
    echo "Set PYTHON_BIN to a Python environment with requirements-lock.txt installed." >&2
    exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
"$RAFFLE_PYTHON" scripts/preflight.py
"$RAFFLE_PYTHON" -m pip check
"$RAFFLE_PYTHON" -c 'import pathlib; paths=[pathlib.Path("main.py"),*pathlib.Path("ripcars_raffle").glob("*.py"),*pathlib.Path("tests").glob("*.py"),*pathlib.Path("scripts").glob("*.py")]; [compile(p.read_text(encoding="utf-8"),str(p),"exec") for p in paths]; print("Compilation: OK")'
"$RAFFLE_PYTHON" -m pyflakes main.py ripcars_raffle tests scripts
for RAFFLE_SCRIPT in run_tests.sh scripts/install.sh scripts/rollback.sh; do
    bash -n "$RAFFLE_SCRIPT"
done
"$RAFFLE_PYTHON" -m unittest discover -s tests -v
