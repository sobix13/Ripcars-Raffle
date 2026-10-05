# Install Rip Cars Raffle on the VPS

Version 1.0.1 is a new independent bot. Gate and Crew remain separate. Code lives under `/opt/ripcars-raffle`, while the service runs as restricted user `ripcarsraffle`. If the package was uploaded to `/root`, change only the archive source path; runtime installation remains under `/opt`.

TAR is the VPS installation package. ZIP contains the same source for Windows inspection. No database, token or virtual environment is bundled. Offline tests are complete; live VPS and Discord acceptance checks follow below.

## 1. Application and permissions

Create a new application named `Rip Cars Raffle` in the [Discord Developer Portal](https://discord.com/developers/applications). Use its own new token, separate from Gate and Crew.

On the Bot page, enable Server Members Intent. Message Content Intent is not required. Start with the Rip Cars server ID in `GUILD_ID`.

Use these two invite scopes:

```text
bot
applications.commands
```

Required bot permissions in the raffle and private log channels:

```text
View Channel
Read Message History
Send Messages
Embed Links
Attach Files
```

Administrator, Manage Roles, Manage Channels, Manage Messages, Kick and Ban are not required. The invite permissions integer is `117760`. Channel overwrites still affect access. Doctor checks effective permissions.

This bot does not create roles or channels. Create a channel such as `raffles` yourself or bind an existing Gate-managed channel. A dedicated raffle channel should use:

| Role | View and history | Send text/files/links and create threads |
| --- | --- | --- |
| Everyone | Hidden | Denied |
| Rippers | Allowed | Denied; entry uses buttons |
| Rip Cars Raffle | Allowed | Send / Embed / Attach allowed |
| Moderator / Admin / Team | Existing staff policy | Existing staff policy |

Rippers do not need Send Messages to click buttons. Even if the panel is publicly visible before Gate entry, entry without Rippers is rejected. Logs must be staff-only. Use a dedicated private channel or an existing private log with permission for this bot. An ordinary role or unknown member overwrite granting View pauses private log delivery.

## 2. Upload from Windows

Put the TAR, ZIP and checksum files in this directory:

```text
C:\Users\macbook\Desktop\files
```

In PowerShell:

```powershell
$RipCarsVps = Read-Host 'VPS IP or your existing SSH host alias'
Set-Location 'C:\Users\macbook\Desktop\files'
Get-FileHash '.\ripcars-raffle-1.0.1.tar.gz' -Algorithm SHA256
Get-FileHash '.\ripcars-raffle-1.0.1.zip' -Algorithm SHA256
scp '.\ripcars-raffle-1.0.1.tar.gz' '.\ripcars-raffle-1.0.1.zip' '.\RIPCARS_RAFFLE_SHA256SUMS.txt' "memecult@${RipCarsVps}:/tmp/"
ssh "memecult@${RipCarsVps}"
```

If your SSH account differs, adjust the connection details. This guide uses your entered VPS address rather than guessing an IP.

## 3. Transfer integrity and prerequisites

On the VPS:

```bash
cd /tmp
sha256sum -c RIPCARS_RAFFLE_SHA256SUMS.txt
python3 --version
```

Both archives should report OK. If they are in `/root`, change to that directory or use an absolute TAR path in the extraction command.

Python 3.11 or newer is required. Keep existing bot environments separate. On Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ca-certificates util-linux
```

## 4. Independent installation

```bash
RIPCARS_RAFFLE_STAGE="$(mktemp -d /tmp/ripcars-raffle-install.XXXXXX)"
tar -xzf /tmp/ripcars-raffle-1.0.1.tar.gz -C "$RIPCARS_RAFFLE_STAGE"
sudo bash "$RIPCARS_RAFFLE_STAGE/ripcars-raffle/scripts/install.sh" "$RIPCARS_RAFFLE_STAGE/ripcars-raffle"
```

The installer creates a new release and a physical virtual environment at its final location, installs locked dependencies, runs every test and switches the current symlink after success. Previous releases remain available. Resolve a failed test before proceeding.

Installation does not start, stop or restart other bot services. Raffle itself starts only after the explicit command below. Shared coordination group permissions are configured; Gate/Crew private tokens and data remain separate.

## 5. Token and service

```bash
sudo nano /etc/ripcars-raffle.env
```

Fill these values with your own configuration:

```dotenv
DISCORD_TOKEN=YOUR_NEW_RAFFLE_BOT_TOKEN
GUILD_ID=YOUR_RIP_CARS_SERVER_ID
DB_PATH=/var/lib/ripcars-raffle/raffle.sqlite3
COORDINATION_PATH=/var/lib/ripcars-bots/coordination.sqlite3
LOG_LEVEL=INFO
```

Keep the token in this private file and out of chat. An empty `GUILD_ID` uses global command sync; a numeric server ID uses guild-scoped sync.

```bash
sudo chmod 600 /etc/ripcars-raffle.env
sudo systemctl enable --now ripcars-raffle
sudo systemctl status ripcars-raffle --no-pager -l
sudo journalctl -u ripcars-raffle --since '5 minutes ago' --no-pager -l
```

Look for `online as` and version `1.0.1`. A Privileged Intent failure requires Server Members Intent on this application. Being online does not enable raffle entry; it starts disabled.

## 6. Setup inside Discord

```text
/raffle setup
```

Select Rippers, optional raffle manager roles, the raffle channel and private log. Selections should remain visible. Choose Review & save, then confirm. Reopen the panel after saving to load the current settings revision.

If Gate already uses the shared coordination registry, start with:

```text
/raffle panel
Gate coordination
Import Gate IDs
```

This reads only Gate's Rippers role and log IDs, pauses new raffle entry and transfers no permissions or ownership. Then set Raffle's channel and manager roles in its own Setup. Do not hand off Gate support or holder scope to this bot.

```text
/raffle doctor
/raffle health
```

Doctor identifies blocking role, channel or permission issues. Repair them in Discord, reopen Setup and confirm Activate / pause new entries in Quick setup.

## 7. First test raffle

```text
/raffle create category:general
```

Set a title, actual test prize, description, duration such as `5m` and winner count. The placeholder Describe the prize cannot be published. Open the draft through `/raffle manage` or the panel. Review roles, weights, minimum eligible count, account/server age, schedule and staff exclusion, then confirm Publish.

Join with an ordinary Rippers account. Staff exclusion is enabled by default. A repeated click must not create another entry. An account without Rippers must be rejected.

Record the seed commitment and rules hash at publication. After the raffle finishes:

```text
/raffle audit raffle_id:1
/raffle entries raffle_id:1
/raffle stats
```

Copy the audit file to a machine with the source and verify it:

```bash
/opt/ripcars-raffle/current/.venv/bin/python /opt/ripcars-raffle/current/scripts/verify_draw.py /tmp/raffle-1-audit.json
```

Expected output: Selection proof: VALID. Compare its hashes with the values recorded at publication. Verification checks recorded inputs and selection consistency, not wallet holdings or operator honesty.

Platform and contributor raffles require independently verified qualifying roles. Self-claimed Collector or notification roles do not prove holdings or contribution. This bot has no wallet, platform points or social credit API.

## 8. Claims, delivery and replacements

The winner clicks Claim prize. Edit the claim instructions under Messages & brand to refer to your existing support process. Raffle does not create tickets or transfer money, cars or other assets.

```text
/raffle award raffle_id:1 user:@winner status:delivered note:Team confirmed prize delivery
```

If delivery has not happened and staff explicitly decide to replace the winner:

```text
/raffle award raffle_id:1 user:@winner status:forfeited note:Winner declined
/raffle reroll raffle_id:1 reason:Replacement approved by team
```

Recorded delivery cannot be changed to Forfeited. Prior winners are not selected again. History and every draw round remain stored. A claim deadline alone does not automatically forfeit a prize.

## 9. Monitoring and troubleshooting

```bash
sudo journalctl -u ripcars-raffle -f
```

```text
/raffle health
/raffle doctor
/raffle history
```

Errors use the `RCR-` prefix in the private log. A channel that becomes public does not receive private log details. Health shows database, scheduler progress, last tick and backup status.

An API or permission uncertainty during member checks stops the entire draw. It does not silently exclude those members. Retries retain the same seed and entry list. Repeated failures require review:

```text
/raffle retry raffle_id:1 reason:Permissions repaired and reviewed
```

If the initial publication was uncertain but a matching bot panel exists, provide that message ID and its matching contract footer:

```text
/raffle recover raffle_id:1 message_id:DISCORD_MESSAGE_ID
```

This command does not send a new panel or generate a new seed. Manage > Review panel retries an existing panel after permission repair. Deleted panels are not recreated automatically. Raffle data remains in the database.

## 10. Retest, update and roll back

```bash
cd /opt/ripcars-raffle/current
PYTHON_BIN=/opt/ripcars-raffle/current/.venv/bin/python bash run_tests.sh
```

For an update, extract the new version separately and run its installer. After tests succeed, restart only this service:

```bash
sudo systemctl restart ripcars-raffle
sudo systemctl status ripcars-raffle --no-pager -l
sudo journalctl -u ripcars-raffle --since '5 minutes ago' --no-pager -l
```

If a previous release exists, roll back code with:

```bash
sudo bash /opt/ripcars-raffle/current/scripts/rollback.sh
sudo systemctl restart ripcars-raffle
```

Rollback changes code, without restoring winners, prizes or the database. Use Retry/Recovery for interrupted draws rather than replacing the database with an old backup.

Private backups live under `/var/lib/ripcars-raffle/backups`. Defaults are 14 retained copies and a 24-hour interval. Edit policy under Limits & policies; use Health & troubleshooting for an immediate backup. Complete `ACCEPTANCE.md` before the first real prize.
