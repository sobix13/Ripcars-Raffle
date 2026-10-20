from __future__ import annotations

import asyncio
import time
from datetime import datetime,timezone

import discord

from . import drawing, eligibility
from .operations import channel_flags, text_channel, doctor, visible, is_manager
from .storage import Conflict


class Raffles:
    def __init__(self, bot):
        self.bot = bot
        self.manual_locks={}
        self.role_locks={}

    @staticmethod
    def entry_role(guild,rid):
        role=guild.get_role(rid)
        if not role or role.managed or role.is_default() or role.permissions.value or role>=guild.me.top_role:
            raise ValueError("Choose a separate zero-permission Raffle role below this bot's highest role.")
        if not guild.me.guild_permissions.manage_roles:
            raise ValueError("Give this bot Manage Roles, then try again.")
        return role

    @staticmethod
    async def ensure_members(guild):
        if not guild.chunked:
            await asyncio.wait_for(guild.chunk(cache=True),timeout=60)
        if not guild.chunked:
            raise ValueError('The member list is still loading. Try again when it has finished.')

    async def setup(self,guild,cfg,revision,actor):
        current,live_revision=await self.bot.db.settings(guild.id)
        if revision!=live_revision:
            raise Conflict("Settings changed. Reopen Quick setup.")
        if current.get('raffle_role') and cfg.get('raffle_role')!=current['raffle_role']:
            raise ValueError("The saved entry role is fixed. Review it before changing its ID.")
        blockers,_=await doctor(self.bot,guild,{**cfg,'raffle_role':0})
        if blockers:
            raise ValueError('Finish Quick setup first:\n'+'\n'.join(blockers))
        if not guild.me.guild_permissions.view_audit_log:
            raise ValueError('Enable View Audit Log to register manual Raffle role grants.')
        if not cfg.get('raffle_role'):
            if not guild.me.guild_permissions.manage_roles:
                raise ValueError("Enable Manage Roles for this bot. It needs this to create and assign the Raffle role.")
            records=await self.bot.registry.resources(guild.id)
            saved=records.get(f"raffle:entry-role:{self.bot.user.id}")
            if saved:
                cfg['raffle_role']=saved['object_id']
            else:
                if any(r.name.casefold()=='raffle' for r in guild.roles):
                    raise ValueError("A Raffle role already exists. Choose Use existing Raffle role in Quick setup.")
                role=await guild.create_role(name='Raffle',permissions=discord.Permissions.none(),mentionable=False,reason='Raffle Quick setup')
                cfg['raffle_role']=role.id
        self.entry_role(guild,cfg['raffle_role'])
        cfg['max_active']=1
        cfg['role_tickets'].setdefault(str(cfg['member_role']),1)
        from .admin_ui import checked_save
        await self.bot.registry.record_role(guild.id,cfg['raffle_role'],self.bot.user.id)
        await checked_save(self.bot,guild,cfg,revision,actor)

    async def sync_manual_roles(self,guild):
        cfg,_=await self.bot.db.settings(guild.id)
        if not cfg.get('raffle_role'):
            return
        async with self.manual_locks.setdefault(guild.id,asyncio.Lock()):
            rows=await self.bot.db.query("SELECT event FROM audit_cursors WHERE guild=?",(guild.id,))
            cursor=rows[0]['event'] if rows else 0
            active=await self.bot.db.query("SELECT id FROM raffles WHERE guild=? AND entry_role=? AND status IN ('open','scheduled')",(guild.id,cfg['raffle_role']))
            if not active:
                return
            r=await self.bot.db.get(guild.id,active[0]['id'])
            after=discord.Object(id=cursor) if cursor else discord.Object(id=discord.utils.time_snowflake(datetime.fromtimestamp(r['starts'],timezone.utc)))
            # The cursor advances only after a grant/removal has been handled.
            async for event in guild.audit_logs(limit=None,action=discord.AuditLogAction.member_role_update,after=after,oldest_first=True):
                added={x.id for x in getattr(event.after,'roles',[])}
                removed={x.id for x in getattr(event.before,'roles',[])}
                stamp=event.created_at.timestamp()
                if event.user_id and event.user_id!=self.bot.user.id and cfg['raffle_role'] in added|removed and r['starts']<=stamp<r['ends']:
                    actor=guild.get_member(event.user_id) or await guild.fetch_member(event.user_id)
                    if is_manager(actor,cfg) and event.target:
                        if cfg['raffle_role'] in added:
                            member=await guild.fetch_member(event.target.id)
                            base={**r['spec']['rules'],'any_roles':[],'all_roles':[]}
                            ok,reason,_=eligibility.check(member,base,r['member_role'],cfg['blocked_users'],manager_roles=cfg['manager_roles'])
                            if ok and cfg['enabled'] and not r['paused']:
                                await self.bot.db.enter(guild.id,r['id'],member.id,stamp,method='manual',actor=actor.id)
                                await self.bot.db.audit(guild.id,actor.id,'manual_entry',f"raffle={r['id']}; user={member.id}; event={event.id}")
                            elif not ok:
                                await self.bot.db.audit(guild.id,actor.id,'manual_entry_rejected',f"user={member.id}; {reason}")
                        else:
                            await self.bot.db.leave(guild.id,r['id'],event.target.id,stamp)
                await self.bot.db.execute("INSERT INTO audit_cursors VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET event=excluded.event",(guild.id,event.id))

    async def sync_entry_roles(self,guild):
        async with self.role_locks.setdefault(guild.id,asyncio.Lock()):
            await self._sync_entry_roles(guild)

    async def _sync_entry_roles(self,guild):
        finished=await self.bot.db.query("SELECT id,entry_role FROM raffles WHERE guild=? AND entry_role>0 AND cleanup_done=0 AND (cleanup_requested=1 OR status='cancelled' OR (status='drawn' AND result_state IN ('published','legacy')))",(guild.id,))
        if finished:
            await self.ensure_members(guild)
        for r in finished:
            rows=await self.bot.db.query("SELECT user FROM entries WHERE raffle=?",(r['id'],))
            role=guild.get_role(r['entry_role'])
            holders={m.id for m in role.members} if role else set()
            users={x['user'] for x in rows}|holders
            for uid in users:
                await self.bot.db.execute("INSERT INTO role_changes VALUES(?,?,0,1) ON CONFLICT(raffle,user) DO UPDATE SET desired=0,pending=CASE WHEN desired=1 OR ? THEN 1 ELSE pending END",(r['id'],uid,uid in holders))
        rows=await self.bot.db.query("SELECT c.*,r.entry_role,r.status FROM role_changes c JOIN raffles r ON r.id=c.raffle WHERE r.guild=? AND c.pending=1 ORDER BY c.desired,c.raffle LIMIT 20",(guild.id,))
        for change in rows:
            role=self.entry_role(guild,change['entry_role'])
            try:
                member=await guild.fetch_member(change['user'])
            except discord.NotFound as exc:
                if exc.code!=10007:
                    raise
            else:
                latest=await self.bot.db.query("SELECT desired FROM role_changes WHERE raffle=? AND user=?",(change['raffle'],change['user']))
                if not latest or latest[0]['desired']!=change['desired']:
                    continue
                owns=role.id in {r.id for r in member.roles}
                if owns!=bool(change['desired']):
                    await (member.add_roles if change['desired'] else member.remove_roles)(role,reason=f"Raffle #{change['raffle']} entry",atomic=True)
            await self.bot.db.execute("UPDATE role_changes SET pending=0 WHERE raffle=? AND user=? AND desired=?",(change['raffle'],change['user'],change['desired']))
        for r in finished:
            pending=await self.bot.db.query("SELECT 1 FROM role_changes WHERE raffle=? AND pending=1",(r['id'],))
            role=guild.get_role(r['entry_role'])
            if not pending and (not role or not role.members):
                await self.bot.db.execute("UPDATE raffles SET cleanup_done=1,cleanup_requested=0 WHERE id=?",(r['id'],))

    async def announce(self,guild,rid,actor=0):
        async with self.bot.db.job(rid,'announce'):
            r=await self.bot.db.get(guild.id,rid)
            if r['status']!='drawn':raise ValueError('Draw this raffle before publishing results.')
            if r['result_state'] in ('published','legacy'):return
            if r['result_state'] in ('sending','review'):
                return
            if r['result_mode']=='review' and not r['result_approved']:
                return
            channel=text_channel(guild,r['channel'])
            from .public_ui import footer
            winners=[uid for rnd in r['rounds'] for uid in rnd['winners']]
            embed=discord.Embed(title=r['spec']['title']+' · Results',description='\n'.join(f'<@{uid}>' for uid in winners) or 'No eligible winners.',color=(await self.bot.db.settings(guild.id))[0]['color'])
            embed.add_field(name='Prize',value=r['spec']['prize'],inline=False)
            embed.set_footer(text=footer(r)+' · results')
            await self.bot.db.execute("UPDATE raffles SET result_state='sending' WHERE id=?",(rid,))
            try:
                message=await channel.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden:
                await self.bot.db.execute("UPDATE raffles SET result_state='pending' WHERE id=?",(rid,))
                raise
            except BaseException:
                await self.bot.db.execute("UPDATE raffles SET result_state='review' WHERE id=?",(rid,))
                raise
            await self.record_announcement(guild.id,rid,message.id)
            await self.bot.db.audit(guild.id,actor,'results_published',f'raffle={rid}; message={message.id}')

    async def record_announcement(self,guild,rid,message):
        def save(c):
            row=c.execute('SELECT spec,result_state,result_message FROM raffles WHERE id=? AND guild=?',(rid,guild)).fetchone()
            if row['result_state']=='published' and row['result_message']==message:
                return
            if row['result_state'] not in ('sending','review'):
                raise Conflict('Result publication changed. Reopen this raffle.')
            import json
            now=time.time();hours=json.loads(row['spec'])['rules']['claim_hours']
            c.execute("UPDATE raffles SET result_state='published',result_message=?,claim_until=?,dirty=1 WHERE id=?",(message,now+hours*3600,rid))
            c.execute("UPDATE awards SET at=? WHERE raffle=? AND status='pending'",(now,rid))
        await self.bot.db.run(save)

    async def recover_announcement(self,guild,rid,message_id,actor):
        async with self.bot.db.job(rid,'announce'):
            r=await self.bot.db.get(guild.id,rid)
            if r['result_state'] not in ('sending','review'):raise ValueError('No uncertain announcement needs recovery.')
            msg=await text_channel(guild,r['channel']).fetch_message(message_id)
            from .public_ui import footer
            if msg.author.id!=self.bot.user.id or not msg.embeds or msg.embeds[0].footer.text!=footer(r)+' · results':
                raise ValueError('This message is not the recorded raffle result.')
            await self.record_announcement(guild.id,rid,msg.id)
            await self.bot.db.audit(guild.id,actor,'result_recovered',f'raffle={rid}; message={msg.id}')

    async def clear_entry_role(self,guild,rid,actor):
        r=await self.bot.db.get(guild.id,rid)
        if not r.get('entry_role'):raise ValueError('This legacy raffle has no entry role to clear.')
        newer=await self.bot.db.query('SELECT 1 FROM raffles WHERE guild=? AND entry_role=? AND id>?',(guild.id,r['entry_role'],rid))
        if newer:
            raise ValueError('A newer raffle uses this fixed role. Clear it from the current raffle instead.')
        await self.bot.db.execute('UPDATE raffles SET cleanup_requested=1,cleanup_done=0,paused=1,dirty=1 WHERE id=?',(rid,))
        await self.bot.db.audit(guild.id,actor,'entry_role_cleanup_requested',f'raffle={rid}')
        await self.sync_entry_roles(guild)

    async def review_announcement(self,guild,rid,actor):
        """Resolve an uncertain send by reading the channel before allowing another send."""
        async with self.bot.db.job(rid,'announce'):
            r=await self.bot.db.get(guild.id,rid)
            if r['result_state'] not in ('sending','review'):
                raise ValueError('No uncertain announcement needs review.')
            from .public_ui import footer
            after=datetime.fromtimestamp(r['rounds'][0]['completed_at'],timezone.utc)
            found=[]
            async for message in text_channel(guild,r['channel']).history(limit=None,after=after):
                if message.author.id==self.bot.user.id and message.embeds and message.embeds[0].footer.text==footer(r)+' · results':
                    found.append(message.id)
            if len(found)>1:
                raise ValueError('Several result messages were found. Review the channel before selecting one to recover.')
            if found:
                await self.record_announcement(guild.id,rid,found[0])
            else:
                await self.bot.db.execute("UPDATE raffles SET result_state='pending' WHERE id=?",(rid,))
            await self.bot.db.audit(guild.id,actor,'result_send_reviewed',f'raffle={rid}; matches={len(found)}')
        await self.announce(guild,rid,actor)

    async def publish(self, guild, rid, actor, *, draft_revision=None):
        cfg, settings_revision = await self.bot.db.settings(guild.id)
        if not cfg["enabled"]:
            raise ValueError("Finish setup and activate raffles first.")
        blockers, _ = await doctor(self.bot, guild, cfg)
        if blockers:
            raise ValueError("Setup blockers:\n"+"\n".join(blockers))
        r = await self.bot.db.get(guild.id, rid)
        if draft_revision is not None and r['revision']!=draft_revision:
            raise Conflict('The draft changed after the preview. Reopen it before publishing.')
        rule = r["spec"]["rules"]
        if cfg.get('raffle_role'):
            await self.ensure_members(guild)
            role=self.entry_role(guild,cfg['raffle_role'])
            awaiting=await self.bot.db.query("SELECT 1 FROM raffles WHERE guild=? AND entry_role=? AND status='drawn' AND result_state NOT IN ('published','legacy')",(guild.id,role.id))
            if awaiting:
                raise ValueError('Publish the previous results before starting another raffle with this role.')
            if role.members:
                await self.bot.db.execute("UPDATE raffles SET cleanup_done=0 WHERE id=(SELECT MAX(id) FROM raffles WHERE guild=? AND entry_role=? AND status IN ('drawn','cancelled'))",(guild.id,role.id))
            await self.sync_entry_roles(guild)
            pending=await self.bot.db.query("SELECT 1 FROM role_changes c JOIN raffles r ON r.id=c.raffle WHERE r.guild=? AND c.pending=1",(guild.id,))
            if pending or role.members:
                raise ValueError("The previous Raffle role is still being cleared. Wait for cleanup before publishing.")
            if role.id in set(rule['any_roles']+rule['all_roles'])|{int(x) for x in rule['weights']}:
                raise ValueError("The temporary Raffle role cannot be an entry requirement or a ticket bonus.")
        if r["spec"]["prize"].strip()=="Describe the prize":
            raise ValueError("Enter an actual prize description before publishing.")
        if rule["minimum_entries"]>cfg["max_entries"]:
            raise ValueError("Minimum eligible entries cannot exceed this raffle's entry limit.")
        if r["category"] != "general" and not (rule["any_roles"] or rule["all_roles"]):
            raise ValueError("Platform/contributor raffles need at least one qualifying role. Wallet and contribution verification are separate systems.")
        roles = set(rule["any_roles"] + rule["all_roles"] + rule["blocked_roles"]) | {int(x) for x in rule["weights"]}
        if any(not guild.get_role(x) for x in roles):
            raise ValueError("One of this draft's roles no longer exists.")
        if any(guild.get_role(x).is_default() or guild.get_role(x).is_bot_managed() for x in rule["any_roles"]+rule["all_roles"]+[int(x) for x in rule["weights"]]):
            raise ValueError("Everyone and bot integration roles are not qualifying or weighted member roles.")
        if r["category"] != "general" and ((set(rule["any_roles"] + rule["all_roles"])|{int(x) for x in rule["weights"]}) & ({cfg["member_role"]} | await self.bot.registry.self_claim_roles(guild.id))):
            raise ValueError("Self-claim roles or Rippers cannot prove platform holdings/contributions. Choose independently verified roles.")
        channel = text_channel(guild, cfg["channel"])
        member_role = guild.get_role(cfg["member_role"])
        if not visible(channel,member_role):
            raise ValueError("Rippers needs View Channel and Read Message History in the selected raffle channel.")
        async with self.bot.db.job(rid, "publish"):
            r = await self.bot.db.begin_publish(guild.id, rid, channel.id, cfg, actor, draft_revision=r["revision"], settings_revision=settings_revision)
            from .public_ui import public_view, panel_embed
            # Journal before send: network ambiguity never causes automatic republishing.
            message = await channel.send(embed=panel_embed(r, cfg, 0), view=public_view(r, cfg), allowed_mentions=discord.AllowedMentions.none())
            await self.bot.db.bind_message(guild.id, rid, message.id)
            try:
                await self.bot.registry.record(guild.id, rid, message.id, self.bot.user.id, r["contract_hash"])
            except Conflict:
                await self.bot.db.audit(guild.id, actor, "registry_retry_needed", f"raffle={rid}; message={message.id}")
            await self.bot.reporter.log(guild, "Raffle published", f"#{rid}: {r['spec']['title']}\nChannel: {channel.id}\nCategory: {r['category']}")
        await self.update_panel(guild,rid,True)
        return await self.bot.db.get(guild.id, rid)

    async def recover_message(self, guild, rid, message_id, actor):
        r = await self.bot.db.get(guild.id, rid)
        if r["status"] != "publishing":
            raise ValueError("Only a pending publication can be recovered.")
        channel = text_channel(guild, r["channel"])
        message = await channel.fetch_message(message_id)
        self.verify_message(r, message)
        await self.bot.db.bind_message(guild.id, rid, message.id)
        try:
            await self.bot.registry.record(guild.id, rid, message.id, self.bot.user.id, r["contract_hash"])
        except Conflict:
            await self.bot.db.audit(guild.id,actor,"registry_retry_needed",f"raffle={rid}; message={message.id}")
        await self.bot.db.audit(guild.id, actor, "publication_recovered", f"raffle={rid}; message={message.id}")
        await self.update_panel(guild,rid,True)

    def verify_message(self, r, message):
        from .public_ui import footer
        if message.author.id != self.bot.user.id or not message.embeds or message.embeds[0].footer.text != footer(r):
            raise ValueError("Message ownership or raffle contract does not match. No unrelated message will be edited.")

    async def member_action(self, interaction, rid, action):
        r = await self.bot.db.get(interaction.guild_id, rid)
        if not interaction.message or interaction.message.id != r["message"] or interaction.channel_id != r["channel"]:
            raise ValueError("This button is not on the recorded raffle message.")
        self.verify_message(r, interaction.message)
        cfg, _ = await self.bot.db.settings(interaction.guild_id)
        if action == "rules":
            from .public_ui import terms
            return terms(r)+f"\n\nRaffle #{rid}\nRules proof: {r['contract_hash']}\nDraw commitment: {r['commitment']}"
        if interaction.user.bot:
            raise ValueError("Bot accounts cannot enter raffles.")
        if action == "claim":
            if r.get('entry_role') and r['result_state'] not in ('published','legacy'):
                raise ValueError('The results have not been published yet.')
            await self.bot.db.claim(interaction.guild_id, rid, interaction.user.id)
            return cfg["texts"]["claim_instructions"]
        if action == "status":
            if r.get('entry_role') and r['status']=='drawn' and r['result_state'] not in ('published','legacy'):
                return 'The draw is complete. Results are awaiting publication.'
            entered = await self.bot.db.query("SELECT active FROM entries WHERE raffle=? AND user=?", (rid, interaction.user.id))
            awards = await self.bot.db.query("SELECT round,status FROM awards WHERE raffle=? AND user=?", (rid, interaction.user.id))
            state="You're entered." if entered and entered[0]['active'] else "You haven't joined this raffle."
            return state+("\nPrize: "+awards[-1]['status'] if awards else '')
        if action == "leave":
            await self.bot.db.leave(interaction.guild_id, rid, interaction.user.id)
            await self.sync_entry_roles(interaction.guild)
            await self.update_panel(interaction.guild,rid,True)
            return cfg["texts"]["left"]
        if action != "join":
            raise ValueError("Unknown raffle action.")
        if not cfg["enabled"]:
            raise ValueError("New entries are paused by the server admin.")
        member = await interaction.guild.fetch_member(interaction.user.id)
        ok, reason, weight = eligibility.check(member, r["spec"]["rules"], r["member_role"], cfg["blocked_users"],manager_roles=cfg["manager_roles"])
        if not ok:
            raise ValueError(reason)
        new = await self.bot.db.enter(interaction.guild_id, rid, member.id)
        if r.get('entry_role'):
            try:
                await self.sync_entry_roles(interaction.guild)
            except (discord.HTTPException,ValueError):
                return "Your entry is saved. Your Raffle role is waiting for synchronization."
        if new:
            await self.update_panel(interaction.guild,rid,True)
        count=(await self.bot.db.query('SELECT COUNT(*) AS n FROM entries WHERE raffle=? AND active=1',(rid,)))[0]['n']
        return (cfg["texts"]["joined"] if new else "You're already entered. Repeated clicks do not add entries.") + f"\nYour tickets: {weight}. Participants: {count}."

    async def checked_pool(self, guild, r, users, token, prior=None):
        cfg, _ = await self.bot.db.settings(guild.id)
        rule = r["spec"]["rules"]
        role_ids = set(rule["any_roles"] + rule["all_roles"] + rule["blocked_roles"]) | {int(x) for x in rule["weights"]} | {r["member_role"]}
        if any(not guild.get_role(x) for x in role_ids):
            raise ValueError("A published eligibility role was deleted. Review or cancel; role names are not replacements.")
        exclusions = {x["user"]: x["reason"] for x in await self.bot.db.query("SELECT * FROM exclusions WHERE raffle=?", (r["id"],))}
        manual={x['user'] for x in await self.bot.db.query("SELECT user FROM entry_approvals WHERE raffle=? AND method='manual'",(r['id'],))}
        pool, checks = [], []
        for uid in users:
            await self.bot.db.renew_job(r["id"], "draw", token)
            if uid in exclusions:
                checks.append({"user": uid, "eligible": False, "weight": 0, "reason": "Manager exclusion", "checked_at": time.time()})
                continue
            try:
                member = await asyncio.wait_for(guild.fetch_member(uid), timeout=45)
            except discord.NotFound as exc:
                if exc.code!=10007:
                    raise
                checks.append({"user": uid, "eligible": False, "weight": 0, "reason": "No longer in this server", "checked_at": time.time()})
                continue
            # Forbidden, rate-limit exhaustion, transport failure or timeout stop the entire draw.
            # Unknown eligibility never turns into silent exclusion.
            checked_rule={**rule,'any_roles':[],'all_roles':[]} if uid in manual else rule
            ok, reason, weight = eligibility.check(member, checked_rule, r["member_role"], cfg["blocked_users"],manager_roles=cfg["manager_roles"])
            final_weight=prior[uid] if prior and ok else weight if ok else 0
            checks.append({"user": uid, "eligible": ok, "weight": final_weight, "reason": reason, "role_ids": sorted(x.id for x in member.roles if x.id in role_ids), "checked_at": time.time()})
            if ok:
                pool.append({"user": uid, "weight": prior[uid] if prior else weight})
        return pool, checks

    async def draw(self, guild, rid, actor=0, reason=None):
        async with self.bot.db.job(rid, "draw") as token:
            r = await self.bot.db.get(guild.id, rid)
            if r["status"] == "drawn":
                return r
            if r["status"] not in ("open", "scheduled", "closing"):
                raise ValueError("Raffle is not available for drawing.")
            r = await self.bot.db.freeze(guild.id, rid, actor, reason)
            try:
                pool, checks = await self.checked_pool(guild, r, r["frozen"], token)
                requested = r["spec"]["winners"] if len(pool) >= r["spec"]["rules"]["minimum_entries"] else 0
                result = drawing.round_result(r["seed"], r["contract_hash"], pool, requested)
                result["requested"] = requested
                result["completed_at"] = time.time()
                await self.bot.db.renew_job(rid, "draw", token)
                await self.bot.db.finalize(guild.id, rid, result, checks)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.bot.db.fail_job(guild.id, rid, str(exc))
                raise
        await self.bot.reporter.log(guild, "Raffle drawn", f"#{rid}: {len(result['winners'])} winner(s), {len(pool)} eligible entrant(s). Result is saved before announcement.")
        return await self.bot.db.get(guild.id, rid)

    async def reroll(self, guild, rid, actor, reason):
        if not reason.strip():
            raise ValueError("Give a reason for the replacement draw.")
        async with self.bot.db.job(rid, "draw") as token:
            r = await self.bot.db.get(guild.id, rid)
            if r["status"] != "drawn" or not r["rounds"]:
                raise ValueError("Draw the original raffle first.")
            replacements = [x["user"] for x in await self.bot.db.query("SELECT user FROM awards WHERE raffle=? AND status='forfeited'", (rid,))]
            if not replacements:
                raise ValueError("Mark an unfulfilled award forfeited before rerolling. Claimed/delivered awards are protected.")
            seen = {u for rnd in r["rounds"] for u in rnd["winners"]}
            prior = {p["user"]: p["weight"] for p in r["rounds"][0]["pool"] if p["user"] not in seen}
            pool, checks = await self.checked_pool(guild, r, sorted(prior), token, prior)
            if not pool:
                raise ValueError("No original eligible entrants remain for replacement. No awards were changed.")
            requested = min(len(replacements), 25)
            result = drawing.round_result(r["seed"], r["contract_hash"], pool, requested, len(r["rounds"]))
            result["requested"] = requested
            result["completed_at"] = time.time()
            await self.bot.db.renew_job(rid, "draw", token)
            await self.bot.db.add_round(guild.id, rid, result, checks, replacements[:len(result["winners"])], actor, reason)
        return await self.bot.db.get(guild.id, rid)

    async def update_panel(self, guild, rid, force=False):
        r = await self.bot.db.get(guild.id, rid)
        if r['status']=='drawn' and r.get('entry_role'):
            await self.announce(guild,rid)
            r=await self.bot.db.get(guild.id,rid)
        cfg, _ = await self.bot.db.settings(guild.id)
        if not r["message"] or r["display_state"] in ("missing", "review"):
            return False
        periodic = r["status"] in ("open","scheduled","closing") and time.time()-r["last_update"]>=60
        if not force and (r["display_retry"] > time.time() or (not r["dirty"] and not periodic) or time.time()-r["last_update"] < cfg["panel_update_seconds"]):
            return False
        try:
            async with self.bot.db.job(rid, "display"):
                channel = text_channel(guild, r["channel"])
                if channel_flags(channel, guild):
                    raise ValueError("Raffle channel permissions changed.")
                msg = await channel.fetch_message(r["message"])
                self.verify_message(r, msg)
                registry=await self.bot.registry.resources(guild.id)
                binding=registry.get(f"raffle:message:{self.bot.user.id}:{rid}")
                if binding and (binding["owner"]!=f"bot:{self.bot.user.id}" or binding["object_id"]!=r["message"] or binding["state"]!="active"):
                    raise ValueError("The shared message binding is protected. Review it instead of overwriting another controller.")
                if not binding:
                    try:
                        await self.bot.registry.record(guild.id,rid,r["message"],self.bot.user.id,r["contract_hash"])
                    except Conflict:
                        pass
                rows = await self.bot.db.query("SELECT COUNT(*) AS n FROM entries WHERE raffle=? AND active=1", (rid,))
                awards = await self.bot.db.query("SELECT user,status FROM awards WHERE raffle=? ORDER BY round", (rid,))
                r["award_states"] = {str(x["user"]):x["status"] for x in awards}
                from .public_ui import public_view, panel_embed
                await msg.edit(embed=panel_embed(r, cfg, rows[0]["n"]), view=public_view(r, cfg), allowed_mentions=discord.AllowedMentions.none())
                # A concurrent join may set dirty during this write: periodic refresh is also based on elapsed time.
                await self.bot.db.execute("UPDATE raffles SET dirty=0,last_update=?,display_attempts=0,display_retry=0 WHERE id=? AND revision=?", (time.time(), rid,r["revision"]))
            return True
        except Conflict:
            return False
        except discord.NotFound:
            await self.bot.db.execute("UPDATE raffles SET display_state='missing',error='Recorded message was deleted' WHERE id=?", (rid,))
            await self.bot.reporter.log(guild, "Raffle panel missing", f"#{rid}: the recorded message was deleted. It will not be silently reposted. Stored entries and results are intact.")
            return False
        except (discord.HTTPException, ValueError) as exc:
            attempts = r["display_attempts"]+1
            await self.bot.db.execute("UPDATE raffles SET display_state=?,display_attempts=?,display_retry=? WHERE id=?", ("review" if attempts>=5 or isinstance(exc, ValueError) else "active", attempts, time.time()+min(60*2**(attempts-1),3600), rid))
            await self.bot.reporter.error(guild, "raffle display", exc)
            return False

    async def tick(self):
        blocked=set()
        for guild in self.bot.guilds:
            try:
                await self.sync_manual_roles(guild)
                await self.sync_entry_roles(guild)
                pending=await self.bot.db.query('SELECT 1 FROM role_changes c JOIN raffles r ON r.id=c.raffle WHERE r.guild=? AND c.pending=1 LIMIT 1',(guild.id,))
                if pending:blocked.add(guild.id)
            except (discord.HTTPException,ValueError) as exc:
                blocked.add(guild.id)
                await self.bot.reporter.error(guild,'entry role synchronization',exc)
        now = time.time()
        rows = await self.bot.db.query("SELECT * FROM raffles WHERE (status IN ('scheduled','open') AND ends<=? OR status='closing') AND retry_at<=?", (now, now))
        for row in rows:
            guild = self.bot.get_guild(row["guild"])
            if guild and guild.id not in blocked and row["id"] not in self.bot.jobs:
                self.enqueue(guild,row["id"])
        # Open future panels without requiring a new message or another publish action.
        await self.bot.db.execute("UPDATE raffles SET status='open',dirty=1,revision=revision+1 WHERE status='scheduled' AND starts<=? AND ends>?", (now, now))
        displays = await self.bot.db.query("SELECT id,guild FROM raffles WHERE message>0 AND display_state='active' AND (dirty=1 OR (status IN ('open','scheduled','closing') AND last_update<?))", (now-60,))
        for row in displays:
            guild = self.bot.get_guild(row["guild"])
            if guild:
                # Bound to a low number of changes each tick; each SDK request respects its route bucket.
                await self.update_panel(guild, row["id"], force=False)

    def enqueue(self,guild,rid,reroll=False,actor=0,reason=""):
        if rid in self.bot.jobs:
            raise Conflict("This raffle already has a running draw job.")
        async def work():
            try:
                if reroll:
                    await self.reroll(guild,rid,actor,reason)
                else:
                    await self.draw(guild,rid)
                await self.update_panel(guild,rid,True)
            except Conflict:
                pass
            except Exception as exc:
                await self.bot.reporter.error(guild,"replacement draw" if reroll else "scheduled draw",exc)
            finally:
                self.bot.jobs.pop(rid,None)
        self.bot.jobs[rid]=asyncio.create_task(work())

    async def inspect(self, guild, rid, user, manager=False):
        r = await self.bot.db.get(guild.id, rid)
        if r["channel"] and not visible(guild.get_channel(r["channel"]), user):
            raise ValueError("You cannot read the channel for this raffle.")
        if r["status"] == "draft" and not manager:
            raise ValueError("Drafts are private to raffle managers.")
        return r
