# Rip Cars Raffle 1.0.1

A standalone Discord raffle bot for Rip Cars. English public copy, burgundy theme (`#800020`), and `https://app.ripcars.io`. Management stays inside Discord under `/raffle`.

## Repository contents and downloads

This repository contains the complete 1.0.1 bot source, 217 offline tests, dependency files, installation and rollback scripts, systemd service, administrative guides and matching release packages. Previous packages remain available.

Version 1.0.1 fixes shared setup coordination with Gate/Crew/Verifier without changing raffle rules or results. See [SUITE_DEPLOYMENT.md](SUITE_DEPLOYMENT.md) and [SUITE_TEST_RESULTS.txt](SUITE_TEST_RESULTS.txt) for the release matrix and four-process test evidence.

| File or directory | Purpose |
| --- | --- |
| [DEPLOYMENT.md](DEPLOYMENT.md) | English copy/paste instructions from Windows upload through VPS installation and Discord setup |
| [ADMIN_GUIDE.md](ADMIN_GUIDE.md) | Discord setup, campaign management, settings, claims and troubleshooting |
| [ARCHITECTURE.md](ARCHITECTURE.md) | State machine, eligibility, drawing rules, proof and recovery |
| [COORDINATION.md](COORDINATION.md) | Boundaries and coordination with Ripcars Gate and Crew |
| [ACCEPTANCE.md](ACCEPTANCE.md) | Live Discord acceptance checks to run after installation |
| [RESEARCH.md](RESEARCH.md) | Primary reference links used for the implementation |
| [TEST_RESULTS.txt](TEST_RESULTS.txt) | Actual offline release verification output |
| [requirements.txt](requirements.txt) | Runtime dependencies |
| [requirements-dev.txt](requirements-dev.txt) | Development and static check dependencies |
| [requirements-lock.txt](requirements-lock.txt) | Exact dependency versions used by the installer and recorded test run |
| [.env.example](.env.example) | Placeholder configuration for a new, separate Discord application |
| [scripts/install.sh](scripts/install.sh) | Tested release installation with a separate virtual environment |
| [scripts/rollback.sh](scripts/rollback.sh) | Code rollback while retaining the database |
| [ripcars-raffle.service](ripcars-raffle.service) | Non-root systemd service and watchdog |
| [scripts/verify_draw.py](scripts/verify_draw.py) | Local raffle proof verifier |
| [tests](tests) | Complete offline test suite |

English install packages and matching delivery documents are available in [releases/1.0.1](releases/1.0.1):

- [ripcars-raffle-1.0.1.tar.gz](releases/1.0.1/ripcars-raffle-1.0.1.tar.gz)
- [ripcars-raffle-1.0.1.zip](releases/1.0.1/ripcars-raffle-1.0.1.zip)
- [RIPCARS_RAFFLE_SHA256SUMS.txt](releases/1.0.1/RIPCARS_RAFFLE_SHA256SUMS.txt)
- [DEPLOYMENT.md](releases/1.0.1/DEPLOYMENT.md)
- [ADMIN_GUIDE.md](releases/1.0.1/ADMIN_GUIDE.md)
- [TEST_RESULTS.txt](releases/1.0.1/TEST_RESULTS.txt)

All repository content and files inside these packages are English. Repository additions include navigation, Git file policies, translated deployment documentation and exclusions for Git metadata and bundled releases during installation and future builds. Bot runtime behavior is unchanged.

## Work from GitHub

```bash
git clone https://github.com/sobix13/Ripcars-Raffle.git
cd Ripcars-Raffle
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
bash run_tests.sh
```

To install this checkout on the VPS, follow [DEPLOYMENT.md](DEPLOYMENT.md), replacing its archive extraction step with the clone above. From the checkout, `sudo bash scripts/install.sh "$PWD"` performs installation and validation. Configure the new token and server ID separately in `/etc/ripcars-raffle.env`, then start the service as described in the guide.

The GitHub publication includes code and install files. Offline tests are complete. Production VPS installation and live Discord acceptance checks remain separate steps.

## Scope

| Bot | Responsibility | Raffle's interaction |
| --- | --- | --- |
| Ripcars Gate | CAPTCHA, at least one onboarding answer, Rippers, role claims and server structure | Raffle requires the existing Rippers role. It never grants it. |
| Ripcars Crew | Moderation, scam/word filters, support tickets and staff workflows | Prize instructions can direct winners to an existing ticket system. No ticket is created by Raffle. |
| Rip Cars Raffle | Campaigns, entry conditions, draws, proof, claims and manual delivery tracking | Own private database, application, token, service and message IDs. |

This release does not create, rename or repair channels, categories or roles. It does not read Gate questionnaire answers. It does not transfer prizes, connect wallets, check token balances, read platform points, score community contributions, count X likes, or award activity credits.

## Features

- General, platform/collecting and contributor campaign templates.
- Existing Rippers membership required for entry. Platform/contributor campaigns additionally require an independently verified qualifying role.
- Any-role, all-role, excluded-role and account blocklist conditions.
- Minimum account/server age, minimum eligible participants, staff exclusion, up to 25 distinct winners.
- Configurable role weights. `max` avoids stacking tiers. `sum` adds extra tickets with an explicit cap.
- One entry per Discord account, leave/rejoin, fixed capacity and deadline checks.
- Immediate or scheduled opening. Deadlines, rules and seed commitment freeze at publication.
- Live member/role checks at entry and closing. Uncertain API checks stop the draw instead of quietly excluding an entrant.
- Transactional storage, persistent buttons, background draw jobs, job leases, retry/backoff and restart recovery.
- SHA-256 commitment before entry opens, HMAC-SHA256 weighted selection, revealed seed after completion, downloadable proof and local verifier.
- Winner acknowledgement, manual delivery/forfeiture, and replacement draws from the original eligible pool without selecting prior winners again.
- Staff-only error logs, Doctor, Health, statistics, private backups, settings history and confirmed configuration restore.
- Editable campaign templates, branding, public text, labels and policies. Large settings use confirmed Discord file import/export instead of truncating forms.

See `ADMIN_GUIDE.md` for exact controls, `ARCHITECTURE.md` for state and draw rules, and `COORDINATION.md` for integration boundaries.

## Quick local verification

Python 3.11 or newer. The recorded test run uses Python 3.12.14 and discord.py 2.7.1.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
bash run_tests.sh
```

No bot token or Discord account is needed for these offline tests. Actual discord.py objects are used for roles, channel overwrites, interactions components and command registration. Discord HTTP, Gateway and the production VPS are not simulated servers. HTTP methods are replaced with controlled adapters.

`TEST_RESULTS.txt` contains the actual release test output. `ACCEPTANCE.md` lists the remaining live Discord checks.

## Deploy

Use `DEPLOYMENT.md` for copy/paste instructions from Windows through VPS installation and Discord setup. Runtime is non-root. A physical virtual environment is created in each release before activating its symlink. Installer runs every test before activation. Nothing else is started or stopped.

The bot starts with new entries disabled. It cannot publish until role IDs, a readable raffle text channel, a staff-only log channel and required bot permissions are configured.

## First campaign

```text
/raffle setup
/raffle doctor
/raffle panel
/raffle create category:general
```

Activate in Quick setup after Doctor passes. Give the draft an actual prize, review its eligibility, weights, schedule and winner count, then confirm Publish. Use a small test raffle before a real campaign.

Member buttons: Join raffle, Leave raffle, My entry, Rules and Claim prize. Bot responses are private and suppress mentions. Public results show winner mentions without pinging everyone.

## Role policy and fairness

Gate's self-claim interests/notification roles cannot prove holdings or contributions. The publisher rejects registered self-claim roles as platform/contributor qualifiers or weights. It also rejects Everyone and bot integration roles. Only bind actual verified role IDs. Unregistered arbitrary self-claim systems cannot be identified automatically by this bot.

Role weights are evaluated at closing, not permanently assigned when a person first clicks Join. Replacement draws preserve the original eligible weights and recheck current eligibility.

The proof verifies consistency of recorded input, the prior commitment and deterministic selection. It is not an external randomness oracle, blockchain attestation or guarantee of operator honesty. An operator can influence timing, exclusions, account policy or the input population. Later rounds are predictable after the original seed is revealed. See `ARCHITECTURE.md`.

Default campaign targets are 70% platform, 20% contributors and 10% general. These are editable planning figures, not money allocation, entry weighting or an implemented contribution-credit economy.
