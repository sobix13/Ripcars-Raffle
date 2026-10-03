# Gate / Crew / Raffle coordination

Raffle can run before Crew or a holder verifier exists. It needs existing server channels and Rippers, not another bot process running concurrently. No companion process is installed or launched here.

## Shared protocol

Use `/var/lib/ripcars-bots/coordination.sqlite3`, exactly the same configured path as Gate/Crew. Keep `/var/lib/ripcars-raffle/raffle.sqlite3` separate.

`resources`: guild, key, object_id, kind, owner, baseline, desired, state. Primary key: guild + key.

`locks`: guild, name, token, expires. Primary key: guild + name.

Read Gate keys `role:rippers` and `channel:gate_log` only when active/pinned/external. Import confirms IDs and pauses new entries. No permission changes or ownership transfer occurs.

Raffle writes only `raffle:message:<bot_user_id>:<raffle_id>` records, kind `message`, owner `bot:<bot_user_id>`. Guild and bot identity prevent independent applications from using the same logical message key. Before writing, acquire Gate/Crew's `server-setup` lease with BEGIN IMMEDIATE and a random 120-second token. Release only the matching token. The message registry operation is short.

Foreign, changed or non-active bindings are preserved. Temporary lease contention does not republish a message or invalidate a saved result. Missing bindings are retried on a later panel refresh. Existing protected bindings require review and stop display writes.

Gate's known blueprint, role claims, daily chat controller and Crew's tickets/moderation remain untouched. Gate/Crew do not own these bot-owned message records. No `support` or `holder` handoff is required or requested for Raffle.

## Permissions and responsibility

Raffle does not call create/edit/delete channel/category/role APIs. It never assigns roles, grants permissions to a new bot, registers arbitrary new members as trusted bots, deletes other bots' messages, or changes slowmode/AutoMod. Grant its own channel access manually.

Required bot permissions in the selected channels: View Channel, Read Message History, Send Messages, Embed Links and Attach Files. Server Members Intent is used for member checking. Message Content Intent, Administrator, Manage Roles/Channels/Messages, Ban, Kick and Audit Log are unnecessary.

The default invitation permissions do not override a channel deny. Doctor checks effective permissions. Rippers must be able to view/read the raffle channel, but need not send chat there to click buttons.

Self-claim keys beginning `role:claim_` are read only to reject them as platform/contributor proof. Raffle cannot determine that an unknown external role is independently verified. Choose those role IDs explicitly and maintain their owner elsewhere.

Sharing the registry is not a permission sandbox between trusted server processes. The service account has write access to that shared file, but application code writes only its own keys. Private raffle data is protected by a separate user-owned directory. Do not give another bot the Raffle token or private data directory.

The shared lease coordinates participating controllers, not arbitrary bots or Discord administrators. There is no claim that external actors cannot delete or change server objects. Raffle reports the missing/protected state and does not recreate their objects.
