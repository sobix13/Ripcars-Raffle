from __future__ import annotations

import copy
import json

import discord

from . import config
from .operations import authorized, reply, doctor, attachment, is_admin, is_manager

SECTIONS = {"setup": "Quick setup", "new": "Create raffle", "raffles": "Raffles & results",
            "templates": "Eligibility & templates", "delivery": "Prize delivery", "brand": "Messages & brand",
            "policy": "Limits & policies", "stats": "Campaign statistics", "health": "Health & troubleshooting", "gate": "Gate coordination", "guide": "Guide"}


class SafeView(discord.ui.View):
    def __init__(self, bot, user, admin=False):
        super().__init__(timeout=900)
        self.bot, self.user, self.admin = bot, user, admin

    async def interaction_check(self, i):
        if i.user.id != self.user:
            await reply(i, "Open your own /raffle panel.")
            return False
        cfg = self.bot.settings_cache.get(i.guild_id, {"manager_roles": []})
        if not (is_admin(i.user) if self.admin else is_manager(i.user, cfg)):
            await reply(i, "Your raffle permission is unavailable. Reopen /raffle panel.")
            return False
        # Opening a form is read-only; every mutation rechecks fresh settings after deferring.
        return True

    async def on_error(self, i, exc, item):
        if isinstance(exc, ValueError):
            await reply(i, str(exc)[:1800])
        else:
            error_id = await self.bot.reporter.error(i.guild, "raffle admin panel", exc)
            await reply(i, f"Error ID: {error_id}")


class SafeModal(discord.ui.Modal):
    def __init__(self, bot, user, title, admin=False):
        super().__init__(title=title[:45], timeout=900)
        self.bot, self.user, self.admin = bot, user, admin

    async def check(self, i):
        if i.user.id != self.user:
            raise ValueError("This form belongs to another admin.")
        return await authorized(self.bot, i, self.admin)

    async def on_error(self, i, exc):
        if isinstance(exc, ValueError):
            await reply(i, str(exc)[:1800])
        else:
            error_id = await self.bot.reporter.error(i.guild, "raffle form", exc)
            await reply(i, f"Error ID: {error_id}")


class Confirm(SafeView):
    def __init__(self, bot, user, action, label="Confirm", admin=False):
        super().__init__(bot, user, admin)
        self.action, self.used = action, False
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.danger)
        async def run(i):
            if self.used:
                raise ValueError("This confirmation was already used.")
            self.used = True
            await i.response.defer(ephemeral=True)
            await authorized(self.bot, i, self.admin)
            await self.action(i)
        button.callback = run
        self.add_item(button)


async def checked_save(bot, guild, cfg, revision, actor):
    config.validate(cfg)
    if cfg["enabled"]:
        blockers, _ = await doctor(bot, guild, cfg)
        if blockers:
            raise ValueError("Fix the blockers before activating:\n"+"\n".join(blockers))
    await bot.db.save(guild.id, cfg, revision, actor)


class Setup(SafeView):
    def __init__(self, bot, user, cfg, revision):
        super().__init__(bot, user, True)
        self.cfg, self.revision = copy.deepcopy(cfg), revision
        for key, placeholder, kind in (("member_role", "Rippers member role", "role"), ("manager_roles", "Raffle manager roles (optional)", "role"),
                                       ("channel", "Existing public raffle text channel", "channel"), ("log_channel", "Existing staff-only log channel", "channel")):
            current = cfg[key] if isinstance(cfg[key], list) else ([cfg[key]] if cfg[key] else [])
            defaults = [discord.SelectDefaultValue(id=x, type=discord.SelectDefaultValueType.role if kind=="role" else discord.SelectDefaultValueType.channel) for x in current]
            menu = discord.ui.RoleSelect(placeholder=placeholder, min_values=0 if key=="manager_roles" else 1, max_values=10 if key=="manager_roles" else 1, default_values=defaults) if kind=="role" else discord.ui.ChannelSelect(placeholder=placeholder, channel_types=[discord.ChannelType.text], default_values=defaults)
            async def choose(i, key=key, menu=menu):
                self.cfg[key] = [r.id for r in menu.values] if key=="manager_roles" else menu.values[0].id
                await i.response.edit_message(content="Selections retained. Review and save when ready.", view=Setup(bot,user,self.cfg,revision))
            menu.callback = choose
            self.add_item(menu)
        button = discord.ui.Button(label="Review & save", row=4, style=discord.ButtonStyle.primary)
        async def review(i):
            async def save(j):
                await checked_save(bot, j.guild, self.cfg, revision, j.user.id)
                await reply(j, "Setup saved. Run /raffle doctor, then enable raffles in Quick setup.")
            details = {k:self.cfg[k] for k in ("member_role","manager_roles","channel","log_channel")}
            await reply(i, "Review the selected IDs. No role or channel permissions will be changed.", file=attachment("raffle-setup.json", details), view=Confirm(bot,user,save,"Save setup",True))
        button.callback = review
        self.add_item(button)
        activate = discord.ui.Button(label="Activate / pause new entries", row=4)
        async def toggle(i):
            candidate = copy.deepcopy(self.cfg)
            candidate["enabled"] = not candidate["enabled"]
            async def save(j):
                await checked_save(bot,j.guild,candidate,revision,j.user.id)
                await reply(j,f"New entries enabled: {candidate['enabled']}. Existing raffles still close and draw at their recorded deadlines.")
            await reply(i,"Confirm activation/pause and the selected setup IDs. Existing draw deadlines remain unchanged.",file=attachment("activation-settings.json",candidate),view=Confirm(bot,user,save,"Save activation",True))
        activate.callback=toggle;self.add_item(activate)


def paths(node, prefix=""):
    out=[]
    for k,v in node.items():
        path = prefix+"."+k if prefix else k
        if isinstance(v,dict) and v and k not in ("weights","campaign_targets"):
            out.extend(paths(v,path))
        else:
            out.append(path)
    return out


def value_at(cfg,path):
    node=cfg
    for k in path.split("."):
        node=node[k]
    return node


class SettingEditor(SafeModal):
    def __init__(self, bot, user, cfg, revision, path):
        super().__init__(bot,user,"Edit raffle setting",True)
        self.cfg,self.revision,self.path = cfg,revision,path
        original=value_at(cfg,path)
        self.is_text=isinstance(original,str)
        raw=original if self.is_text else json.dumps(original,ensure_ascii=False)
        if len(raw)>4000:
            raise ValueError("This setting exceeds Discord's form limit. Use /raffle export-config and /raffle import-config. Nothing was truncated or saved.")
        self.field=discord.ui.TextInput(label=path[-45:],default=raw,style=discord.TextStyle.paragraph,max_length=4000,required=True)
        self.add_item(self.field)

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        await self.check(i)
        value=self.field.value if self.is_text else json.loads(self.field.value)
        candidate=config.set_path(self.cfg,self.path,value)
        await checked_save(self.bot,i.guild,candidate,self.revision,i.user.id)
        await reply(i,"Setting saved. Published raffle rules remain unchanged.")


class Settings(SafeView):
    def __init__(self,bot,user,cfg,revision,prefix="",page=0):
        super().__init__(bot,user,True)
        fields=[p for p in paths(cfg) if p.startswith(prefix)]
        total=max(1,(len(fields)+24)//25)
        page=max(0,min(page,total-1))
        menu=discord.ui.Select(placeholder=f"Setting: page {page+1}/{total}",options=[discord.SelectOption(label=p[:100],value=p) for p in fields[page*25:(page+1)*25]])
        async def select(i):
            await i.response.send_modal(SettingEditor(bot,user,cfg,revision,menu.values[0]))
        menu.callback=select;self.add_item(menu)
        for label, offset in (("Previous",-1),("Next",1)):
            button=discord.ui.Button(label=label,disabled=not 0<=page+offset<total)
            async def move(i,offset=offset):
                await i.response.edit_message(view=Settings(bot,user,cfg,revision,prefix,page+offset))
            button.callback=move;self.add_item(button)


class DraftDetails(SafeModal):
    def __init__(self,bot,user,category,r=None,initial=None):
        super().__init__(bot,user,"Raffle details")
        self.category,self.r=category,r
        spec=r["spec"] if r else initial or config.DEFAULTS["templates"][category]
        for key,label,limit in (("title","Raffle title",100),("prize","Prize description",400),("description","Public details",1200),("duration","Duration, e.g. 24h or 3d",20),("winners","Number of winners: 1 to 25",2)):
            f=discord.ui.TextInput(label=label,default=str(spec[key]),max_length=limit,required=key!="description",style=discord.TextStyle.paragraph if key in ("prize","description") else discord.TextStyle.short)
            setattr(self,"field_"+key,f);self.add_item(f)

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        cfg=await self.check(i)
        spec=copy.deepcopy(self.r["spec"] if self.r else cfg["templates"][self.category])
        for k in ("title","prize","description"):
            spec[k]=getattr(self,"field_"+k).value.strip()
        spec["duration"]=config.duration(self.field_duration.value)
        spec["winners"]=int(self.field_winners.value)
        config.validate_spec(spec)
        if self.r:
            rid=self.r["id"]
            await self.bot.db.edit(i.guild_id,rid,spec,self.r["revision"],i.user.id)
        else:
            rid=await self.bot.db.create(i.guild_id,i.user.id,self.category,spec)
        await show_raffle(self.bot,i,rid)


class DraftJSON(SafeModal):
    def __init__(self,bot,user,r,part):
        super().__init__(bot,user,"Edit draft rules / schedule")
        self.r,self.part=r,part
        value=r["spec"]["rules"] if part=="rules" else {"starts_in":r["spec"]["starts_in"],"duration":r["spec"]["duration"]}
        self.field=discord.ui.TextInput(label="JSON values; seconds for schedule",default=json.dumps(value,indent=2),style=discord.TextStyle.paragraph,max_length=4000)
        self.add_item(self.field)

    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        await self.check(i)
        value=json.loads(self.field.value)
        spec=copy.deepcopy(self.r["spec"])
        if self.part=="rules":
            spec["rules"]=config.validate_rules(value)
        else:
            if not isinstance(value,dict) or set(value)!={"starts_in","duration"}:
                raise ValueError("Schedule JSON needs starts_in and duration only.")
            spec.update(value)
        await self.bot.db.edit(i.guild_id,self.r["id"],spec,self.r["revision"],i.user.id)
        await show_raffle(self.bot,i,self.r["id"])


class DraftRoles(SafeView):
    def __init__(self,bot,user,r):
        super().__init__(bot,user)
        self.r=copy.deepcopy(r)
        for key,label in (("any_roles","Any one qualifying role"),("all_roles","Every required role"),("blocked_roles","Excluded roles")):
            defaults=[discord.SelectDefaultValue(id=x,type=discord.SelectDefaultValueType.role) for x in self.r["spec"]["rules"][key]]
            menu=discord.ui.RoleSelect(placeholder=label,min_values=0,max_values=20,default_values=defaults)
            async def choose(i,key=key,menu=menu):
                self.r["spec"]["rules"][key]=[x.id for x in menu.values]
                await i.response.edit_message(content="Role selections retained. Save when ready.",view=DraftRoles(bot,user,self.r))
            menu.callback=choose;self.add_item(menu)
        button=discord.ui.Button(label="Save role rules",style=discord.ButtonStyle.primary)
        async def save(i):
            await i.response.defer(ephemeral=True)
            await authorized(bot,i)
            await bot.db.edit(i.guild_id,r["id"],self.r["spec"],r["revision"],i.user.id)
            await show_raffle(bot,i,r["id"])
        button.callback=save;self.add_item(button)


class Manage(SafeView):
    def __init__(self,bot,user,r):
        super().__init__(bot,user)
        if r["status"]=="draft":
            actions=("Edit details","Role eligibility","Weights & limits","Schedule","Publish")
        else:
            actions=("Refresh","Pause entries","Resume entries","End now","Cancel","Review panel","Reroll forfeited")
        for label in actions:
            button=discord.ui.Button(label=label)
            async def act(i,label=label):
                latest=r
                if label not in ("Edit details","Role eligibility","Weights & limits","Schedule"):
                    await i.response.defer(ephemeral=True)
                    await authorized(bot,i)
                    latest=await bot.db.get(i.guild_id,r["id"])
                if label=="Edit details":
                    await i.response.send_modal(DraftDetails(bot,user,latest["category"],latest))
                elif label=="Role eligibility":
                    await reply(i,"Pick roles. Existing selections remain visible.",view=DraftRoles(bot,user,latest))
                elif label in ("Weights & limits","Schedule"):
                    await i.response.send_modal(DraftJSON(bot,user,latest,"rules" if label=="Weights & limits" else "schedule"))
                elif label=="Refresh":
                    await show_raffle(bot,i,r["id"])
                else:
                    async def run(j):
                        if label=="Publish":
                            await bot.raffles.publish(j.guild,r["id"],j.user.id)
                        elif label=="End now":
                            await bot.db.freeze(j.guild_id,r["id"],j.user.id,"Manager ended early from the panel")
                            bot.raffles.enqueue(j.guild,r["id"])
                        elif label=="Reroll forfeited":
                            bot.raffles.enqueue(j.guild,r["id"],True,j.user.id,"Manager approved replacement draw")
                        elif label=="Review panel":
                            fresh=await bot.db.get(j.guild_id,r["id"])
                            ch=j.guild.get_channel(fresh["channel"])
                            if not ch:
                                raise ValueError("Recorded channel is missing. It will not be recreated.")
                            msg=await ch.fetch_message(fresh["message"])
                            bot.raffles.verify_message(fresh,msg)
                            await bot.db.execute("UPDATE raffles SET display_state='active',display_attempts=0,display_retry=0,dirty=1 WHERE id=?",(r["id"],))
                            await bot.db.audit(j.guild_id,j.user.id,"panel_reviewed",f"raffle={r['id']}")
                        else:
                            await bot.db.state_action(j.guild_id,r["id"],j.user.id,{"Pause entries":"pause","Resume entries":"resume","Cancel":"cancel"}[label],"Manager action from panel")
                        await bot.raffles.update_panel(j.guild,r["id"],True)
                        await show_raffle(bot,j,r["id"])
                    await reply(i,f"Confirm: {label}. Published eligibility rules cannot be edited. Entries and recorded results are retained.",view=Confirm(bot,user,run,label))
            button.callback=act;self.add_item(button)


async def show_raffle(bot,i,rid):
    r=await bot.raffles.inspect(i.guild,rid,i.user,True)
    cfg,_=await bot.db.settings(i.guild_id)
    counts=await bot.db.query("SELECT COUNT(*) AS n FROM entries WHERE raffle=? AND active=1",(rid,))
    summary=f"#{rid} · {r['category']} · {r['status']}\nPrize: {r['spec']['prize']}\nEntries: {counts[0]['n']}\nPanel: {r['display_state']}\nUse /raffle award for delivery, /raffle audit for proof, /raffle entries for CSV."
    await reply(i,embed=discord.Embed(title=r["spec"]["title"],description=summary,color=cfg["color"]),view=Manage(bot,i.user.id,r))


class Hub(SafeView):
    def __init__(self,bot,user):
        super().__init__(bot,user)
        menu=discord.ui.Select(placeholder="Choose a raffle section",options=[discord.SelectOption(label=v,value=k) for k,v in SECTIONS.items()])
        async def choose(i):
            await i.response.defer(ephemeral=True)
            await authorized(bot,i)
            cfg,rev=await bot.db.settings(i.guild_id)
            key=menu.values[0]
            if key=="setup":
                await authorized(bot,i,True)
                await reply(i,"Choose IDs in setup. Enable with the settings editor after Doctor passes.",view=Setup(bot,user,cfg,rev))
            elif key=="new":
                view=SafeView(bot,user)
                select=discord.ui.Select(placeholder="Campaign category",options=[discord.SelectOption(label=x.title(),value=x) for x in config.CATEGORIES])
                async def create(j):
                    await j.response.send_modal(DraftDetails(bot,user,select.values[0],initial=cfg["templates"][select.values[0]]))
                select.callback=create;view.add_item(select)
                await reply(i,"Choose a template. The draft is private until you publish it.",view=view)
            elif key in ("templates","brand","policy"):
                await authorized(bot,i,True)
                prefix={"templates":"templates.","brand":("brand","bot_name","website","color","texts."),"policy":("blocked_users","max_active","max_entries","panel_update_seconds","poll_seconds","backup_hours","backup_keep","error_alert_seconds","campaign_targets")}[key]
                await reply(i,"Edit typed settings. Template changes affect new drafts only.",view=Settings(bot,user,cfg,rev,prefix))
            elif key in ("raffles","delivery"):
                rows=await bot.db.query("SELECT id,spec,status FROM raffles WHERE guild=? ORDER BY id DESC LIMIT 25",(i.guild_id,))
                if not rows:
                    await reply(i,"No raffles yet. Use Create raffle.");return
                view=SafeView(bot,user)
                select=discord.ui.Select(placeholder="Recent raffles; /raffle list for older IDs",options=[discord.SelectOption(label=f"#{r['id']} {json.loads(r['spec'])['title']}"[:100],value=str(r['id']),description=r["status"]) for r in rows])
                async def manage(j):
                    await j.response.defer(ephemeral=True)
                    await authorized(bot,j)
                    await show_raffle(bot,j,int(select.values[0]))
                select.callback=manage;view.add_item(select)
                await reply(i,"Choose a raffle. Delivery is recorded with /raffle award; winners use Claim prize.",view=view)
            elif key=="health":
                blockers,notes=await doctor(bot,i.guild)
                view=SafeView(bot,user,True)
                retry=discord.ui.Button(label="Backup now")
                async def backup_now(j):
                    await j.response.defer(ephemeral=True)
                    current=await authorized(bot,j,True)
                    from .operations import backup
                    path=await backup(bot.db,bot.backup_path,current["backup_keep"])
                    await reply(j,f"Backup saved: {path.name}")
                retry.callback=backup_now;view.add_item(retry)
                await reply(i,"Blockers:\n"+("\n".join(blockers) or "None")+"\n\nChecks:\n"+"\n".join(notes),view=view)
            elif key=="gate":
                await authorized(bot,i,True)
                async def run(j):
                    ids=await bot.registry.gate_ids(j.guild_id)
                    fresh,revision=await bot.db.settings(j.guild_id)
                    fresh.update(ids)
                    fresh["enabled"]=False
                    await bot.db.save(j.guild_id,fresh,revision,j.user.id)
                    await reply(j,"Gate IDs imported with new entries paused. No ownership, roles or channel permissions changed.")
                await reply(i,"Import Rippers and the private Gate log IDs only. Configure managers and a raffle channel yourself.",view=Confirm(bot,user,run,"Import Gate IDs",True))
            elif key=="stats":
                from .commands import statistics
                await reply(i,"Campaign counts and targets. Targets are planning percentages, not automatic budget transfers.",file=attachment("raffle-statistics.json",await statistics(bot,i.guild_id)))
            else:
                await reply(i,cfg["texts"]["guide"]+"\n\nManagers: create a draft, review its rules, publish once. Weight rules and eligibility conditions freeze at publishing; actual weights and role checks are evaluated at draw time. Claims are separate from manual delivery. /raffle doctor explains setup problems.")
        menu.callback=choose;self.add_item(menu)


async def panel(bot,i):
    cfg=await authorized(bot,i)
    embed=discord.Embed(title=cfg["texts"]["title"]+" · Admin",description=cfg["texts"]["intro"]+f"\nNew entries enabled: {cfg['enabled']}",color=cfg["color"])
    embed.set_footer(text=cfg["brand"])
    await reply(i,embed=embed,view=Hub(bot,i.user.id))
