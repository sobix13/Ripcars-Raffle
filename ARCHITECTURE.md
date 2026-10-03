# Architecture and drawing protocol

## Layers

| File | Responsibility |
| --- | --- |
| `config.py` | Strict typed settings, three templates, public text, validation |
| `eligibility.py` | Rippers, roles, account/server age, staff policy and weights |
| `drawing.py` | Seed commitment, canonical hashes, weighted selection, replay verification |
| `storage.py` | SQLite transactions, settings revisions, entries, snapshots, awards and leases |
| `coordination.py` | Shared protocol, Gate ID reads and namespaced own-message bindings |
| `service.py` | Publish/recover, member actions, live eligibility, draws, display and scheduling |
| `public_ui.py` | Restart-safe dynamic buttons and bounded public embeds |
| `admin_ui.py` | Owner-bound panels/forms, confirmation, fresh permission checks |
| `commands.py` | One guild-only `/raffle` group, CSV/config/audit exports |
| `operations.py` | Permissions, staff-only reporting, backups, Doctor and Health |
| `bot.py` | Discord application, command sync, scheduler, watchdog, shutdown |

## State and interruption behaviour

| State | Allowed behaviour | Recovery |
| --- | --- | --- |
| draft | Private editing | Revision mismatch requires reopening |
| publishing | Commitment/rules journaled, public message being sent | No automatic resend. Admin binds a matching owned message or cancels |
| scheduled | Bound panel, entry waits for opening | Scheduler opens the same panel |
| open | Eligible accounts enter or leave | Restart preserves IDs, rules, capacity and deadline |
| closing | Active entry IDs frozen, live eligibility being checked | Same entry snapshot and seed resume after restart |
| review | Five closing failures, no completed result | Admin resolves cause and confirms Retry |
| drawn | Result persisted, seed reveal, prize acknowledgement | Announcement failure never draws again |
| cancelled | No entry or draw | Entries and history retained |

Pausing a raffle stops new entry, not its deadline. Global deactivation/import/restore stops new entry and publication, not the already published closing schedule. Closing early is an explicit logged manager action. A scheduled raffle cannot draw before it opens. Completed draws cannot be cancelled or rewritten.

Display has its own active/missing/review state. A deleted message is not silently reposted. A mismatched author/contract, protected shared binding or repeated write failure pauses display. Entries/results stay in storage. Review panel verifies the same existing message before retrying. A deleted channel/message is not recreated by this release.

## Draw

1. Generate a 32-byte random seed with Python `secrets` when publication starts.
2. Canonicalize the complete rules contract with sorted JSON keys and compact separators. Publish its SHA-256 digest and `SHA256(seed_bytes)` before Join becomes usable.
3. At closing, transactionally freeze active Discord IDs. No new join/leave is allowed afterward.
4. Fetch each member with the Discord API. Recheck the fixed role/weight conditions and the disclosed current server account/staff policy. Only a definitive missing-member response is an automatic departure exclusion. Forbidden, timeout and transport errors stop the entire check.
5. Store eligibility evidence for every frozen ID, including final weight. Roles unrelated to the published rule are not included in public evidence.
6. Sort eligible IDs, hash the weighted pool and select with HMAC-SHA256. Rejection sampling removes modulo bias. A selected account is removed before selecting the next winner.
7. Persist the result and awards in one guarded transaction. Only then update the panel and reveal the seed.

Algorithm: `ripcars-weighted-hmac-sha256-v1`. Counter input is `contract_hash:round_number:pool_hash:counter`. Numeric ticket selection is proportional to current remaining weights. Each account can win only once across all rounds of a raffle.

No eligible pool, or a pool smaller than `minimum_entries`, produces a completed zero-winner result. Otherwise the requested winners are selected up to the number of eligible accounts. A draw never invents additional entrants.

## Prize and replacement rules

Pending → Claimed → Delivered. Managers can explicitly mark an undelivered award Forfeited with a note. Delivered/Forfeited/Replaced are final states. Claiming does not transfer anything. Contact details and shipping/payment operations are left to the existing support workflow.

Forfeiture is not automatic when the claim window expires. The team reviews it. Replacement draws only fill forfeited awards. They use the original eligible population and original weights, omit every prior winner, and recheck live membership/roles. If nobody remains, prior forfeiture is left untouched. All rounds, reasons and prior winners stay in the audit bundle.

## Verification and limitations

Export with `/raffle audit` after completion:

```bash
.venv/bin/python scripts/verify_draw.py raffle-123-audit.json
.venv/bin/python scripts/verify_draw.py raffle-123-audit.json.gz
```

The verifier checks the seed commitment, rules hash, pool hashes, frozen input coverage, eligibility/pool consistency, deterministic results, unchanged original weights, unique prior winners and replacement references. It does not independently query Discord, a wallet or the platform.

Record the publicly posted commitment and rules hash before entries close, and compare them with the export. Verifying a self-contained bundle alone does not prove that these same hashes were published earlier. The local verifier cannot recover historical Discord message edits.

Commitment/replay makes later seed substitution and arithmetic inconsistencies detectable. It does not prove the seed was selected without advance grinding, that role assignments were honest, that a manager never cancelled an unfavourable campaign, or that manual exclusions were justified. The seed is known to the server operator and revealed after round zero. Replacement selection is subsequently predictable. For financially significant campaigns, an external auditable randomness source and independent account/holdings data would be a separate design.

One Discord account is not necessarily one person. Account-age and Gate entry checks reduce trivial abuse, not coordinated multi-account participation. Messages/likes are not converted into chances, so low-effort posting cannot farm tickets in this release.

## Storage and operations

Private SQLite: `raffle.sqlite3`, file mode 600, parent owned by the runtime user with mode 700. Includes future unrevealed seeds, so backups are private too. Shared coordination contains only resource metadata, never entry data, winner secrets or onboarding answers.

WAL, 15-second database busy timeout, BEGIN IMMEDIATE, unique entry keys and revision guards protect concurrent changes. Transaction threads complete before cancellation releases the in-process lock. Cross-process draw/publish/display leases use random tokens, expiry, renewal and token-checked release. Database writes happen outside the event loop.

Public buttons have IDs `rcr:raffle:<id>:<action>`. Every action verifies the stored guild, channel, message, bot author and contract footer. User installs and direct messages are disabled. Public mutation callbacks acknowledge first. Admin changes recheck current permissions/settings after acknowledgement, not just permissions cached when the panel opened.

Backups use SQLite's online backup API and an integrity check. Settings history retains 30 previous revisions per guild. Audit history is capped at about 10,000 events globally. Raffle/entry/prize history is not automatically deleted. Settings restore does not roll back draws. Restoring an old database during an active campaign is not an automatic or safe substitute for job recovery.
