from __future__ import annotations

import csv
import io
import json

import discord
from discord import app_commands
from discord.ext import commands

from . import config
from .admin_ui import panel, Confirm, DraftDetails, show_raffle
from .operations import authorized, reply, attachment, doctor, health


async def statistics(bot,guild):
    cfg,_=await bot.db.settings(guild)
    counts=await bot.db.query("SELECT category,status,COUNT(*) AS raffles FROM raffles WHERE guild=? GROUP BY category,status",(guild,))
    prizes=await bot.db.query("SELECT r.category,a.status,COUNT(*) AS awards FROM awards a JOIN raffles r ON r.id=a.raffle WHERE r.guild=? GROUP BY r.category,a.status",(guild,))
    entries=await bot.db.query("SELECT r.category,COUNT(*) AS entries,COUNT(DISTINCT e.user) AS unique_accounts FROM entries e JOIN raffles r ON r.id=e.raffle WHERE r.guild=? AND e.active=1 GROUP BY r.category",(guild,))
    return {"planning_targets_percent":cfg["campaign_targets"],"raffles":counts,"awards":prizes,"entries":entries,"note":"Targets are planning guidance, not automatic spending or a user point system."}


class Commands(commands.Cog):
    raffle=app_commands.Group(name="raffle",description="Rip Cars raffles, entry, proof and administration",guild_only=True)
    def __init__(self,bot):
        self.bot=bot

    @raffle.command(name="panel",description="Open the raffle management center")
    async def open_panel(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        await panel(self.bot,i)

    @raffle.command(name="setup",description="Choose member/manager roles and existing channels")
    async def setup(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i,True)
        from .admin_ui import Setup
        cfg,rev=await self.bot.db.settings(i.guild_id)
        await reply(i,"Pick existing IDs. This bot never builds or changes roles/channels.",view=Setup(self.bot,i.user.id,cfg,rev))

    @raffle.command(name="create",description="Create a private draft from a campaign template")
    @app_commands.choices(category=[app_commands.Choice(name=k.title(),value=k) for k in config.CATEGORIES])
    async def create(self,i:discord.Interaction,category:str):
        await i.response.defer(ephemeral=True)
        cfg=await authorized(self.bot,i)
        if category not in config.CATEGORIES:
            raise ValueError("Unknown campaign category.")
        view=discord.ui.View(timeout=300)
        button=discord.ui.Button(label="Enter raffle details",style=discord.ButtonStyle.primary)
        async def open_form(j):
            if j.user.id!=i.user.id:
                await reply(j,"Open your own draft.");return
            await j.response.send_modal(DraftDetails(self.bot,i.user.id,category,initial=cfg["templates"][category]))
        button.callback=open_form;view.add_item(button)
        await reply(i,f"Template: {category}. Review requirements before publishing.",view=view,file=attachment("template.json",cfg["templates"][category]))

    @raffle.command(name="manage",description="Review a raffle and its management actions")
    async def manage(self,i:discord.Interaction,raffle_id:int):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        await show_raffle(self.bot,i,raffle_id)

    @raffle.command(name="list",description="List this server's recent raffles")
    async def list_raffles(self,i:discord.Interaction,before_id:int=0):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        rows=await self.bot.db.query("SELECT id,category,status,spec FROM raffles WHERE guild=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT 25",(i.guild_id,before_id,before_id))
        lines=[f"#{r['id']} · {r['category']} · {r['status']} · {json.loads(r['spec'])['title']}" for r in rows]
        await reply(i,("\n".join(lines) or "No raffles.")+"\nUse before_id to page through older IDs.")

    @raffle.command(name="rules",description="Read the frozen requirements and seed commitment")
    async def rules(self,i:discord.Interaction,raffle_id:int):
        await i.response.defer(ephemeral=True)
        r=await self.bot.raffles.inspect(i.guild,raffle_id,i.user)
        await reply(i,"Frozen raffle terms. One entry per Discord account.",file=attachment(f"raffle-{raffle_id}-rules.json",{"contract":r["contract"],"commitment":r["commitment"],"contract_hash":r["contract_hash"]}))

    @raffle.command(name="audit",description="Export completed draw proof and the revealed seed")
    async def audit(self,i:discord.Interaction,raffle_id:int):
        await i.response.defer(ephemeral=True)
        await self.bot.raffles.inspect(i.guild,raffle_id,i.user)
        bundle=await self.bot.db.public_bundle(i.guild_id,raffle_id)
        await reply(i,"Replayable selection proof. Role ownership evidence is not a blockchain attestation.",file=attachment(f"raffle-{raffle_id}-audit.json",bundle))

    @raffle.command(name="entries",description="Export entries and delivery statuses as CSV")
    async def entries(self,i:discord.Interaction,raffle_id:int):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        await self.bot.db.get(i.guild_id,raffle_id)
        rows=await self.bot.db.query("SELECT e.user,e.joined,e.active,COALESCE(a.status,'') AS award_status FROM entries e LEFT JOIN awards a ON a.raffle=e.raffle AND a.user=e.user WHERE e.raffle=? ORDER BY e.user",(raffle_id,))
        stream=io.StringIO(newline="")
        writer=csv.DictWriter(stream,fieldnames=["user","joined","active","award_status"]);writer.writeheader();writer.writerows(rows)
        await reply(i,"Admin export; contains member IDs.",file=attachment(f"raffle-{raffle_id}-entries.csv",stream.getvalue().encode("utf-8-sig")))

    @raffle.command(name="end",description="Confirm early closing and draw eligible winners")
    async def end(self,i:discord.Interaction,raffle_id:int,reason:str):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        async def run(j):
            await self.bot.db.freeze(j.guild_id,raffle_id,j.user.id,reason[:500])
            self.bot.raffles.enqueue(j.guild,raffle_id)
            await self.bot.raffles.update_panel(j.guild,raffle_id,True)
            await reply(j,"Entries closed. The draw is running in the background; /raffle manage shows progress.")
        await reply(i,"Confirm early closing. This freezes entries and records the reason.",view=Confirm(self.bot,i.user.id,run,"End and draw"))

    @raffle.command(name="cancel",description="Cancel an unfinished raffle with a recorded reason")
    async def cancel(self,i:discord.Interaction,raffle_id:int,reason:str):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        async def run(j):
            await self.bot.db.state_action(j.guild_id,raffle_id,j.user.id,"cancel",reason)
            await self.bot.raffles.update_panel(j.guild,raffle_id,True)
            await reply(j,"Cancelled; entries and history retained.")
        await reply(i,"Confirm cancellation. A completed result cannot be cancelled.",view=Confirm(self.bot,i.user.id,run,"Cancel raffle"))

    @raffle.command(name="reroll",description="Replace forfeited awards using the original eligible pool")
    async def reroll(self,i:discord.Interaction,raffle_id:int,reason:str):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        async def run(j):
            self.bot.raffles.enqueue(j.guild,raffle_id,True,j.user.id,reason)
            await reply(j,"Replacement draw queued. Original winners and proof are retained.")
        await reply(i,"Only forfeited awards are eligible for replacement. Prior winners cannot win again.",view=Confirm(self.bot,i.user.id,run,"Reroll forfeited"))

    @raffle.command(name="award",description="Record manual delivery or forfeiture of a prize")
    @app_commands.choices(status=[app_commands.Choice(name=x.title(),value=x) for x in ("delivered","forfeited")])
    async def award(self,i:discord.Interaction,raffle_id:int,user:discord.User,status:str,note:str):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        async def run(j):
            await self.bot.db.award_status(j.guild_id,raffle_id,user.id,status,j.user.id,note)
            await self.bot.raffles.update_panel(j.guild,raffle_id,True)
            await reply(j,"Award status recorded. This does not transfer a prize or send money.")
        await reply(i,f"Confirm award status for {user.id}: {status}.",view=Confirm(self.bot,i.user.id,run,"Record award"))

    @raffle.command(name="exclude",description="Exclude an account before entries freeze, with a reason")
    async def exclude(self,i:discord.Interaction,raffle_id:int,user:discord.User,reason:str):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        async def run(j):
            await self.bot.db.exclude(j.guild_id,raffle_id,user.id,j.user.id,reason)
            await reply(j,"Exclusion recorded in the audit log.")
        await reply(i,"Confirm manual exclusion before closing.",view=Confirm(self.bot,i.user.id,run,"Exclude account"))

    @raffle.command(name="recover",description="Bind a verified message after an ambiguous publish")
    async def recover(self,i:discord.Interaction,raffle_id:int,message_id:str):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i,True)
        mid=int(message_id)
        async def run(j):
            await self.bot.raffles.recover_message(j.guild,raffle_id,mid,j.user.id)
            await reply(j,"Publication bound to the verified existing message.")
        await reply(i,"Only a message by this bot with the same recorded contract will be accepted.",view=Confirm(self.bot,i.user.id,run,"Recover publication",True))

    @raffle.command(name="retry",description="Retry a reviewed draw without changing its seed or entries")
    async def retry(self,i:discord.Interaction,raffle_id:int,reason:str):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i,True)
        async def run(j):
            await self.bot.db.state_action(j.guild_id,raffle_id,j.user.id,"retry",reason)
            await reply(j,"Original closing job resumed. No new seed or entries were generated.")
        await reply(i,"Resolve permission/API problems first. Confirm retry of the original snapshot.",view=Confirm(self.bot,i.user.id,run,"Retry draw",True))

    @raffle.command(name="stats",description="Read campaign and prize delivery statistics")
    async def stats(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        await reply(i,"Campaign statistics.",file=attachment("raffle-statistics.json",await statistics(self.bot,i.guild_id)))

    @raffle.command(name="doctor",description="Explain permission and setup blockers")
    async def check(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        blockers,notes=await doctor(self.bot,i.guild)
        await reply(i,"Blockers:\n"+("\n".join(blockers) or "None")+"\n\nChecks:\n"+"\n".join(notes))

    @raffle.command(name="health",description="Check database, scheduling and gateway health")
    async def health(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i)
        await reply(i,"Raffle health.",file=attachment("raffle-health.json",await health(self.bot,i.guild)))

    @raffle.command(name="guide",description="Read the raffle guide")
    async def guide(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        cfg,_=await self.bot.db.settings(i.guild_id)
        await reply(i,cfg["texts"]["guide"]+"\n"+cfg["website"])

    @raffle.command(name="export-config",description="Export settings without tokens, seeds or entries")
    async def export_config(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i,True)
        cfg,_=await self.bot.db.settings(i.guild_id)
        await reply(i,"Settings only. No raffle seeds or member entry data.",file=attachment("raffle-settings.json",cfg))

    @raffle.command(name="import-config",description="Validate and confirm importing settings")
    async def import_config(self,i:discord.Interaction,file:discord.Attachment):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i,True)
        if file.size>256000:
            raise ValueError("Settings import limit is 256 KB.")
        data=await file.read()
        if len(data)>256000:
            raise ValueError("Settings import limit is 256 KB.")
        cfg=config.validate(json.loads(data))
        cfg["enabled"]=False
        _,rev=await self.bot.db.settings(i.guild_id)
        async def run(j):
            await self.bot.db.save(j.guild_id,cfg,rev,j.user.id)
            await reply(j,"Settings imported with new entries paused. Published rules/results are unchanged.")
        await reply(i,"Confirm importing validated settings. Entry activation remains off.",view=Confirm(self.bot,i.user.id,run,"Import settings",True))

    @raffle.command(name="history",description="Read recent settings and management changes")
    async def history(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i,True)
        settings=await self.bot.db.query("SELECT id,actor,at FROM history WHERE guild=? ORDER BY id DESC LIMIT 30",(i.guild_id,))
        events=await self.bot.db.query("SELECT actor,action,detail,at FROM audit WHERE guild=? ORDER BY id DESC LIMIT 100",(i.guild_id,))
        await reply(i,"Admin history.",file=attachment("raffle-history.json",{"settings":settings,"events":events}))

    @raffle.command(name="restore-config",description="Confirm settings rollback without changing past draws")
    async def restore_config(self,i:discord.Interaction,history_id:int):
        await i.response.defer(ephemeral=True)
        await authorized(self.bot,i,True)
        async def run(j):
            await self.bot.db.restore(j.guild_id,history_id,j.user.id)
            await reply(j,"Settings restored with new entry paused. Draws, awards and Discord messages were not rolled back.")
        await reply(i,"Confirm settings restore. This does not undo a draw or prize delivery.",view=Confirm(self.bot,i.user.id,run,"Restore settings",True))

    async def cog_app_command_error(self,i,error):
        exc=getattr(error,"original",error)
        if isinstance(exc,(ValueError,json.JSONDecodeError)):
            await reply(i,str(exc)[:1800])
        else:
            error_id=await self.bot.reporter.error(i.guild,"raffle command",exc)
            await reply(i,f"Error ID: {error_id}")
