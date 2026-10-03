# Primary-source review

Reviewed for the 1.0.0 build on 2026-10-03. This is a small self-hosted bot, not a wrapper around an external giveaway provider.

| Primary source | Design consequence |
| --- | --- |
| [Discord interactions](https://docs.discord.com/developers/interactions/receiving-and-responding) | Acknowledge interactions promptly. Longer eligibility/draw work runs in background jobs, not on a short-lived interaction token. |
| [Discord rate limits](https://docs.discord.com/developers/topics/rate-limits) | Let discord.py handle route buckets and retry headers. Debounce public edits. Do not hardcode assumptions about Discord's changing request buckets. |
| [discord.py interactions API](https://discordpy.readthedocs.io/en/stable/interactions/api.html) | DynamicItem restores namespaced public buttons after restart. Native Role/Channel selectors retain default values. Actual SDK forms and command registration are tested. |
| [discord.py 2.7.1 on PyPI](https://pypi.org/project/discord.py/2.7.1/) | Pin the tested SDK and complete dependency versions in requirements-lock.txt. No automatic SDK upgrade during installation. |
| [GiveawayBot source repository](https://github.com/jagrosh/GiveawayBot) | Creation, timed completion, manager ending and reroll are familiar giveaway workflows. Rip Cars adds its own verified-role/contributor segmentation, private prize lifecycle and replayable audit. No third-party code/service is invoked. |
| [Python secrets](https://docs.python.org/3/library/secrets.html) | Generate seeds and lease tokens from the operating system's secure randomness source, not a predictable default PRNG. |
| [Python SQLite backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup) | Online consistent database backups instead of copying a live WAL database file. |

Implementation decisions are our own: explicit verification boundaries, default staff exclusion, fixed published contracts, no low-effort engagement scoring, stopped draws on uncertain eligibility, no implicit resource adoption, and manual prize transfer. A seed commitment is deliberately described as replayable auditability, not a claim of external verifiable randomness or a tamper-proof server operator.

No paid third-party API, account, wallet service or raffle subscription is required by this release. Future holder/account linking would require separately chosen data sources and an explicit role policy.
