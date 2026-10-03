# Acceptance and test boundaries

Offline release tests execute against a clean Python virtual environment and pinned discord.py. Real SDK Guild, Member, Role, TextChannel, permission overwrite, UI/form and application-command objects are constructed. Controlled adapters replace sending, editing, member fetching and command synchronization. Temporary SQLite databases exercise real transactions, not in-memory mocked SQL.

These tests do not connect to Discord, use a real token, create a cloud Discord server or deploy to the production VPS. A passing offline suite is not a live deployment claim. See `TEST_RESULTS.txt` for actual output and versions.

## Automated coverage

| Area | Checks |
| --- | --- |
| Settings | Types, ranges, missing/extra fields, role conflicts, public bounds, independent templates |
| Entry | Rippers, verified roles, age, bots, staff, blocklist, unique account, capacity and exact deadline |
| Storage | Concurrent connections, revisions, leases, frozen entries, cancellation, settings-only restore, private modes |
| Drawing | Commitment, deterministic ordering, weighted sample, unique winners, zero/underfilled pool, tampered proof |
| Recovery | API errors halt all eligibility, fixed snapshot/seed after restart, duplicate workers, cancelled closing |
| Awards | Claim, expiry, protected delivery, explicit forfeiture, original-weight replacement and no remaining pool |
| Discord UI | Actual SDK serialization, persistent dynamic IDs, early acknowledgement, selector defaults, owner/auth guards, embed bounds |
| Privacy | Public/unknown-member log leaks rejected, token/seed redaction, no raw member-facing failures, no mention pings |
| Coordination | Exact shared schema, foreign/manual preservation, Gate contention, ID-only reads, separate application keys |
| Boot | Intents, guild-only installation, command registration, offline sync, shutdown, missing token |
| Operations | SQLite backup integrity/retention, health schema, compressed exports, preflight and shell syntax |

## Live Discord checks: pending

Use a test campaign with a dummy prize and ordinary accounts. Do not use a manager account to test member entry while staff exclusion is enabled.

1. Invite the new application with only the required permissions. Enable Server Members Intent, leave Message Content off. Start the service and verify version 1.0.0.
2. Choose existing roles/channels in `/raffle setup`. Select another channel, navigate the form and verify the new selection remains visible. Confirm the IDs, save, reopen, run Doctor.
3. Confirm the log is invisible to Rippers/OG/nonstaff, and the raffle channel is readable with history. Administrator is not required for the bot.
4. Activate new entries. Create General with a real-looking dummy prize and a short duration. Review the private draft and confirmation. Publish exactly once.
5. An account without Rippers must be refused. Ordinary Rippers can enter and leave. Repeated clicks must not add entries. Staff and bots must be refused under the default rule.
6. Save the initial seed commitment/rules hash. Restart the service during entry and test the same message's buttons afterward.
7. Test an independently verified role campaign. Remove that role from a test entrant before closing. That entrant must be excluded from the final pool, with evidence in the completed audit.
8. Let a campaign close naturally and compare the stored result, panel and audit. Run the local verifier and compare its hashes with the initially saved hashes.
9. Have a winner claim twice. Only one acknowledgement should be recorded. Record delivered with a note. A subsequent forfeiture of that delivered award must be refused.
10. In a separate test campaign, forfeit an undelivered award, confirm replacement, and verify original winners/weights/history and the new round.
11. Temporarily remove a required bot channel permission on a test campaign. Doctor should explain it. Restoring permission plus Review panel should retry the same existing owned panel, not choose winners again.
12. Test deletion only on a dummy raffle message. It must be marked missing, retain data, and not silently repost.
13. Have a nonmanager try the admin panel/config commands. Revoke a manager role while their confirmation is open. The change must be checked again on submit.
14. Confirm that Gate onboarding/claim roles and Crew moderation/tickets work unchanged. No Raffle command should create/delete roles, channels, categories or tickets.
15. Review startup journal, health, last scheduler tick and backup status. Retain this completed checklist with the release's offline report.

Large guilds, prolonged rate-limit exhaustion, real network outages, live command propagation, and production systemd permissions need deployment-specific verification. Role checking intentionally uses sequential live member fetches and may take time on large campaigns. Closing freezes entries first and works in the background. Scale limits in settings are validation bounds, not a benchmark promise.
