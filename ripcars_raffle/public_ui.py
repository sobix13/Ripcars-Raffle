from __future__ import annotations

import re
import time

import discord

from .operations import attachment, reply


def footer(r):
    return f"RCR #{r['id']} · {r['contract_hash']}"


def terms(r):
    rule = r["spec"]["rules"]
    parts = [f"Member role: <@&{r['member_role']}>. One entry per Discord account."]
    for key, label in (("any_roles", "Any qualifying role"), ("all_roles", "All required roles"), ("blocked_roles", "Excluded roles")):
        if rule[key]:
            parts.append(label+": "+", ".join(f"<@&{x}>" for x in rule[key]))
    if rule["weights"]:
        parts.append("Role weights: "+", ".join(f"<@&{k}>={v}" for k,v in rule["weights"].items()))
    parts.append(f"Weight mode: {rule['weight_mode']}; cap: {rule['weight_cap']}. Weights are checked at draw time, then frozen for replacement draws.")
    parts.append(f"Minimum eligible entries: {rule['minimum_entries']}; account age: {rule['account_days']}d; server age: {rule['server_days']}d; staff excluded: {rule['exclude_staff']}.")
    parts.append(f"Claim window: {rule['claim_hours']}h. Full rules: Rules button or /raffle rules.")
    raw = "\n".join(parts)
    return raw if len(raw)<=1024 else raw[:900]+"\nFull role lists and weights: Rules button or /raffle rules."


def panel_embed(r, cfg, count):
    t = cfg["texts"]
    description = r["spec"]["description"]
    if r["paused"]:
        description += "\n\n"+t["paused_notice"]
    if r["status"] == "cancelled":
        description += "\n\nCancelled: "+r["error"][:500]
    if r["status"] == "review":
        description += "\n\nDraw paused for an admin review. No winners were selected."
    published=not r.get('entry_role') or r.get('result_state') in ('published','legacy')
    if r['status']=='drawn' and not published:
        description+='\n\nEntries closed. Results are awaiting publication.'
    e = discord.Embed(title=r["spec"]["title"], description=description[:3000], color=cfg["color"])
    e.set_author(name=cfg["bot_name"],url=cfg["website"])
    e.add_field(name=t["prize_label"], value=r["spec"]["prize"], inline=False)
    rule=r['spec']['rules']
    roles=rule['any_roles'] or [r['member_role']]
    who=', '.join(f'<@&{x}>' for x in roles)
    if rule['all_roles']:
        who+='\nAlso required: '+', '.join(f'<@&{x}>' for x in rule['all_roles'])
    e.add_field(name='Who can join',value=who[:1024],inline=False)
    schedule=f"Closes <t:{int(r['ends'])}:R> · <t:{int(r['ends'])}:f>"
    if r['status']=='scheduled':schedule=f"Opens <t:{int(r['starts'])}:R>\n"+schedule
    elif r['status']=='drawn':schedule=f"Ended <t:{int(r['ends'])}:R>"
    elif r['status']=='open':schedule=f"Opened <t:{int(r['starts'])}:R>\n"+schedule
    if r["status"]=="drawn" and r["rounds"]:
        schedule+=f"\nDraw completed <t:{int(r['rounds'][0]['completed_at'])}:F>"
    e.add_field(name=t["schedule_label"], value=schedule, inline=False)
    e.add_field(name=t["entries_label"], value=f"{count} entered · {r['spec']['winners']} winner(s) requested", inline=False)
    if r["status"] == "drawn" and published:
        awards = r.get("award_states", {})
        lines = []
        for rnd in r["rounds"]:
            for uid in rnd["winners"]:
                lines.append(f"Round {rnd['round']}: <@{uid}> · {awards.get(str(uid),'pending')}")
        e.add_field(name=t["result_label"], value=("\n".join(lines) or t["no_winners"])[:1024], inline=False)
    e.set_footer(text=footer(r))
    if len(e) > 5900:
        e.description = e.description[:max(0, len(e.description)-(len(e)-5900))]
    return e


class RaffleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rcr:raffle:(?P<rid>[1-9][0-9]{0,18}):(?P<action>join|leave|status|rules|claim)"):
    def __init__(self, rid, action, label, disabled=False):
        self.rid, self.action = rid, action
        super().__init__(discord.ui.Button(label=label[:80], custom_id=f"rcr:raffle:{rid}:{action}", style=discord.ButtonStyle.primary if action in ("join","claim") else discord.ButtonStyle.secondary, disabled=disabled))

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["rid"]), match["action"], item.label or match["action"], item.disabled)

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        bot = interaction.client
        try:
            if not interaction.guild or not isinstance(interaction.user, discord.Member):
                raise ValueError("Use the raffle panel in the server.")
            value = await bot.raffles.member_action(interaction, self.rid, self.action)
            if isinstance(value, dict):
                await reply(interaction, "Raffle rules and state.", file=attachment(f"raffle-{self.rid}-{self.action}.json", value))
            else:
                await reply(interaction, value)
        except ValueError as exc:
            await reply(interaction, str(exc)[:1800])
        except Exception as exc:
            error_id = await bot.reporter.error(interaction.guild, "raffle button", exc)
            await reply(interaction, f"This action needs a check. Error ID: {error_id}")


def public_view(r, cfg):
    view = discord.ui.View(timeout=None)
    active = r["status"] in ("open", "scheduled") and not r["paused"] and r["starts"] <= time.time() < r["ends"]
    published=not r.get('entry_role') or r.get('result_state') in ('published','legacy')
    actions=(("claim","status","rules") if published else ('status','rules')) if r['status']=='drawn' else ("join","leave","rules")
    for action in actions:
        label = cfg["texts"][action]
        disabled = (action in ("join", "leave") and not active) or (action == "claim" and r["status"] != "drawn")
        view.add_item(RaffleButton(r["id"], action, label, disabled))
    return view
