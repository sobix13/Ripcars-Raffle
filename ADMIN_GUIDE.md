# Discord administration

## Roles

Server Owner, Administrator or Manage Server can configure the bot. Configured Raffle Manager roles can manage campaigns and awards, but cannot edit global settings, import/restore configuration, or change setup. Bot accounts are never admitted as managers just because they have a role.

Rippers is a prerequisite, not a role that Raffle gives out. OG, contributor tiers, points/$CARS thresholds and car-specific roles are optional existing qualifiers. Their assignment and validation belong to staff or a separately approved verifier.

## Panel sections

| Section | Contents |
| --- | --- |
| Quick setup | Rippers, optional manager roles, existing raffle/log text channels, reviewed save, activation/pause |
| Create raffle | General, platform or contributor template, details form |
| Raffles & results | Recent drafts/campaigns, inspect, publish, pause/resume, close/cancel, review display, replacement |
| Eligibility & templates | Default required/excluded roles, weights, ages, winners, timing and claim window |
| Prize delivery | Choose a raffle, review result, use `/raffle award` to record delivery/forfeiture |
| Messages & brand | Brand/display name, website, burgundy colour, public copy and labels |
| Limits & policies | Account blocklist, capacities, poll/display intervals, backup and alert policy, planning percentages |
| Campaign statistics | Counts by category, distinct entrants and award states |
| Health & troubleshooting | Doctor blockers, review cases, manual backup |
| Gate coordination | Confirm ID import without handing off resources |
| Guide | Member/manager usage and boundaries |

Selectors retain selected values when the form refreshes. Confirming save uses a settings revision guard. After saving, reopen the panel before a second change. A stale form cannot overwrite newer settings.

Settings pages show at most 25 paths. Values are typed: text stays text, role IDs/lists and structured values use JSON. Unknown fields, duplicate IDs, malformed URLs, contradictory rules, invalid ranges and non-integer numeric values are rejected. A value over Discord's form limit requires file import, never silent truncation.

The editable `bot_name` affects embeds, not the application's account name/avatar. Change the latter in the Discord Developer Portal.

## Creating a campaign

Open Create raffle, select a category, give title/prize/details/duration/winner count. Duration accepts seconds or `10m`, `24h`, `3d`, `1w`. Maximum duration/delay is 90 days. Drafts are private.

Open Role eligibility for Any / All / Excluded role selectors. Weights & limits edits the full rules JSON. Schedule edits `starts_in` and `duration`, in seconds. Review all fields before Publish. An unchanged placeholder prize is rejected.

Example role-weight rule, replacing example IDs with existing server IDs:

```json
{
  "any_roles": [111111111111111111, 222222222222222222],
  "all_roles": [],
  "blocked_roles": [],
  "weights": {"111111111111111111": 2, "222222222222222222": 4},
  "weight_mode": "max",
  "weight_cap": 4,
  "minimum_entries": 3,
  "account_days": 7,
  "server_days": 1,
  "exclude_staff": true,
  "claim_hours": 72
}
```

Each person still has one entry. Their weight changes their selection probability, not how often they can click Join. With `max`, a person holding both sample tiers receives weight 4. With `sum`, the formula is `1 + sum(role_weight - 1)` before the cap. Weight 1 adds no bonus.

Rippers is always required in addition to the campaign rule. A platform/contributor raffle needs a separate qualifying role. Registered self-claim Collector/notification roles, Rippers, Everyone and bot integration roles cannot substitute for verified holdings/contributions.

Publication freezes this campaign's settings. Changing a template affects later drafts. Changing a published raffle's channel, prize, winner count, schedule or weight rules is not permitted. Current account blocklist/manager staff status are a disclosed server policy. Actual weights are checked at draw time and then retained for replacement draws.

## Commands

| Command | Access / purpose |
| --- | --- |
| `/raffle panel`, `/raffle create`, `/raffle manage`, `/raffle list` | Manager campaign UI and paging |
| `/raffle setup` | Admin ID setup |
| `/raffle rules`, `/raffle audit` | Members who can read the campaign channel. Audit only after completion |
| `/raffle guide` | Server members |
| `/raffle entries` | Manager CSV export with Discord IDs |
| `/raffle end`, `/raffle cancel`, `/raffle exclude` | Manager, confirmed action and recorded reason |
| `/raffle reroll`, `/raffle award` | Manager, replacement/delivery operations |
| `/raffle recover` | Admin, bind a known matching owned message after ambiguous publication |
| `/raffle retry` | Admin, retry reviewed closing using the original seed/snapshot |
| `/raffle stats`, `/raffle health`, `/raffle doctor` | Manager diagnostics |
| `/raffle export-config`, `/raffle import-config` | Admin settings-only JSON, confirmed import pauses new entry |
| `/raffle history`, `/raffle restore-config` | Admin change review and confirmed settings rollback |

For more than 25 recent raffles, `/raffle list before_id:<last_id>` pages older IDs, then `/raffle manage raffle_id:<id>` opens one. Long diagnostics attach complete text so Discord's message limit does not lose information.

## Awards

Winner clicks Claim prize to acknowledge, then uses the existing support process to arrange delivery. Raffle stores no shipping address, private key or wallet signature. Change claim instructions in Messages & brand for your actual workflow.

```text
/raffle award raffle_id:123 user:@winner status:delivered note:Delivery confirmed by team
/raffle award raffle_id:123 user:@winner status:forfeited note:Winner declined the prize
/raffle reroll raffle_id:123 reason:Approved replacement after forfeiture
```

Each command requires confirmation. Forfeiture can also be recorded for a departed user by their Discord ID. It does not automatically revoke a role, refund/charge anything or alter an already delivered prize.

## Troubleshooting

| Situation | Behaviour / next step |
| --- | --- |
| Save/activate reports blockers | Doctor shows exact missing roles, channel flags or private-log issue. Fix manually, reopen setup |
| Staff member cannot join | Staff exclusion defaults true. Use ordinary test members or explicitly change the draft before publishing |
| New entries paused | Global enable/raffle pause is off. Existing deadlines still run |
| Member/permission API failure | Whole closing stops, then backoff. Five failures require Retry |
| Recorded message deleted | Display marked missing. Stored data stays. No automatic replacement |
| Publication uncertain | Find message by this bot with the same `RCR #id` contract footer, then `/raffle recover` with its ID |
| No message was actually published | Cancel the pending campaign with a recorded reason and create a reviewed new draft |
| Existing panel permissions repaired | Manage > Review panel verifies the existing owned message before display retry |
| Foreign/protected registry binding | Inspect with the owning controller. Do not overwrite another bot's binding |
| Settings edited elsewhere | Reopen the panel to get its current revision |

Errors have `RCR-` IDs. Guild-specific errors are audited and rate-limited to the private log channel. Raw exceptions and secrets are not returned to members. If the log channel becomes public or has an unresolved View allow, logging is suppressed rather than leaking private data. Global startup/database problems remain in the service journal.
