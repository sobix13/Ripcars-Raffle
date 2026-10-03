from __future__ import annotations

import asyncio
import time

import discord

from . import drawing, eligibility
from .operations import channel_flags, text_channel, doctor, visible
from .storage import Conflict


class Raffles:
    def __init__(self, bot):
        self.bot = bot

    async def publish(self, guild, rid, actor):
        cfg, _ = await self.bot.db.settings(guild.id)
        if not cfg["enabled"]:
            raise ValueError("Finish setup and activate raffles first.")
        blockers, _ = await doctor(self.bot, guild, cfg)
        if blockers:
            raise ValueError("Setup blockers:\n"+"\n".join(blockers))
        r = await self.bot.db.get(guild.id, rid)
        rule = r["spec"]["rules"]
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
            r = await self.bot.db.begin_publish(guild.id, rid, channel.id, cfg, actor)
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
            return {"contract": r["contract"] or {"category": r["category"], "spec": r["spec"]}, "commitment": r["commitment"], "contract_hash": r["contract_hash"]}
        if interaction.user.bot:
            raise ValueError("Bot accounts cannot enter raffles.")
        if action == "claim":
            await self.bot.db.claim(interaction.guild_id, rid, interaction.user.id)
            return cfg["texts"]["claim_instructions"]
        if action == "status":
            entered = await self.bot.db.query("SELECT active FROM entries WHERE raffle=? AND user=?", (rid, interaction.user.id))
            awards = await self.bot.db.query("SELECT round,status FROM awards WHERE raffle=? AND user=?", (rid, interaction.user.id))
            return {"raffle": rid, "status": r["status"], "entry": bool(entered and entered[0]["active"]), "awards": awards}
        if action == "leave":
            await self.bot.db.leave(interaction.guild_id, rid, interaction.user.id)
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
        return (cfg["texts"]["joined"] if new else "You're already entered. Repeated clicks do not add entries.") + f"\nCurrent weight: {weight}. Final weight is checked at draw time."

    async def checked_pool(self, guild, r, users, token, prior=None):
        cfg, _ = await self.bot.db.settings(guild.id)
        rule = r["spec"]["rules"]
        role_ids = set(rule["any_roles"] + rule["all_roles"] + rule["blocked_roles"]) | {int(x) for x in rule["weights"]} | {r["member_role"]}
        if any(not guild.get_role(x) for x in role_ids):
            raise ValueError("A published eligibility role was deleted. Review or cancel; role names are not replacements.")
        exclusions = {x["user"]: x["reason"] for x in await self.bot.db.query("SELECT * FROM exclusions WHERE raffle=?", (r["id"],))}
        pool, checks = [], []
        for uid in users:
            await self.bot.db.renew_job(r["id"], "draw", token)
            if uid in exclusions:
                checks.append({"user": uid, "eligible": False, "weight": 0, "reason": "Manager exclusion", "checked_at": time.time()})
                continue
            try:
                member = await asyncio.wait_for(guild.fetch_member(uid), timeout=45)
            except discord.NotFound:
                checks.append({"user": uid, "eligible": False, "weight": 0, "reason": "No longer in this server", "checked_at": time.time()})
                continue
            # Forbidden, rate-limit exhaustion, transport failure or timeout stop the entire draw.
            # Unknown eligibility never turns into silent exclusion.
            ok, reason, weight = eligibility.check(member, rule, r["member_role"], cfg["blocked_users"],manager_roles=cfg["manager_roles"])
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
        now = time.time()
        rows = await self.bot.db.query("SELECT * FROM raffles WHERE (status IN ('scheduled','open') AND ends<=? OR status='closing') AND retry_at<=?", (now, now))
        for row in rows:
            guild = self.bot.get_guild(row["guild"])
            if guild and row["id"] not in self.bot.jobs:
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
