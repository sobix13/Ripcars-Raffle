# Validation: Raffle 1.1.0

Validated 2026-10-20 with Python 3.12.14 and discord.py 2.7.1.

- `PYTHON_BIN=<runtime>/bin/python bash run_tests.sh`: 240 tests passed, no failures, errors or skips. Compilation, pinned dependency compatibility, Pyflakes and shell syntax passed.
- New tests cover additive tickets without changing legacy arithmetic, registration and role assignment, duplicate clicks, deferred role retry, authorized native manual grants, setup reuse, publication approval, automatic results, post-publication cleanup, uncertain delivery recovery, archive pagination, public counts, old-raffle cleanup isolation and stale draft confirmations.
- The separate four-process suite passed 21 tests with current Raffle/Verifier and the existing Gate/Crew sources. Each process used its own private data and the same temporary coordination database.
- The matching Maestro adapter source hashes and current source passed the coordinated updater's read-only validation.

No new runtime dependency was added. Existing contracts and draw algorithms are retained. New schema fields are additive. The fixed entry role has no server permissions and cannot be used as its own qualification condition.

Not checked here: production Discord gateway/buttons, current guild permissions, the user's VPS, live provider services or production backup repository. Run the small native acceptance raffle after installation. Offline tests do not establish production deployment.
