# Role raffles 1.1.0

Raffle consumes Discord roles only. Verifier checks qualifications separately. No API, RPC, wallet proof, shared identity store or Crew ticket is required by a raffle.

1. Open `/raffle panel` > Settings > Quick setup. Choose the existing Rippers/member role, managers, public raffle channel and staff-only log channel. Save and activate. The bot needs Manage Roles, View Audit Log, Server Members Intent and ordinary channel access. Place its highest role above the fixed Raffle role. Administrator is unnecessary.
2. Set Ticket defaults by selecting role names. Rippers 1 and OG 3 are optional starter values. Only selected qualifying roles contribute tickets.
3. New raffle: choose roles, prize, duration and winner count. Review the preview and choose automatic results or admin approval. Publish one raffle at a time.
4. A successful Join registers once and assigns Raffle. Duplicate clicks add nothing. Admin/moderator native grants are accepted only when attributable to an authorized manager during open entries. The audit log supplies the actor; there can be a short synchronization delay. Manual approvals do not bypass membership, blocks or staff exclusions.
5. Closing freezes entries and checks current Discord roles. Selected role tickets add. Failed Discord checks stop the draw for review rather than removing entrants silently. The saved draw cannot be changed by approving its announcement.
6. Results are sent in the original raffle channel. In approval mode the winners remain private until approved. Automatic mode sends without a second confirmation. After confirmed publication the entry role is removed in batches, never deleted. The public entry count, original panel, results and previous draws stay available.
7. More controls provides manual role clearing, participant exclusion, participant CSV, draw proof, management history and delivery/replacement controls. Manual clearing pauses new entries and keeps registrations. An old raffle cannot clear a newer raffle's role.

If result delivery is uncertain, use Find or retry announcement. It reads the channel first and either binds the existing result or sends the saved result if none exists. A failed channel read does not permit another send. You can also recover one exact message link after review.

Historical raffles keep their contracts, seeds, max/sum arithmetic and proof format. This upgrade does not rewrite their outcomes. Run the native test raffle acceptance checks after installation; offline tests do not prove live Discord permissions or connectivity.

The coordinated updater is `scripts/update_role_raffles.py` in the matching Maestro 1.1.2 source. It stages and tests all three releases before stopping interfaces, verifies the existing checkpoint, backs up databases and private configuration, then changes only Raffle, Verifier and Maestro. Failed readiness restores previous code and units, not an old database over newer user data. Gate and Crew are not reinstalled.
