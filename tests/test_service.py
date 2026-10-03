import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ripcars_raffle import drawing
from ripcars_raffle.service import Raffles
from ripcars_raffle.storage import Conflict,Store
from tests.fakes import AsyncCase,Message,failure,interaction


class ServiceTests(AsyncCase):
    def click(self,r,uid,action="join"):
        return self.bot.raffles.member_action(interaction(self.bot,self.guild,self.guild.get_member(uid),self.guild.public.messages[r["message"]]),r["id"],action)
    async def fill(self,count=4,rules=None):
        r=await self.published(rules=rules)
        for uid in range(1000,1000+count):
            self.guild.add_member(uid,[10]);await self.click(r,uid)
        return r
    async def save_cfg(self,**updates):
        cfg,rev=await self.bot.db.settings(1);cfg.update(updates);await self.bot.db.save(1,cfg,rev,9001)
    async def test_publish_owned_panel_enables_join(self):
        r=await self.published();msg=self.guild.public.messages[r["message"]]
        self.assertFalse(msg.view.children[0].item.disabled)
        self.assertEqual(msg.author.id,777)
        self.assertNotIn(r["seed"],str(msg.embeds[0].to_dict()))
    async def test_publish_disabled_by_default(self):
        await self.save_cfg(enabled=False);rid=await self.draft()
        with self.assertRaises(ValueError):await self.bot.raffles.publish(self.guild,rid,9001)
    async def test_platform_needs_independently_verified_role(self):
        rid=await self.draft("platform")
        with self.assertRaises(ValueError):await self.bot.raffles.publish(self.guild,rid,9001)
        r=await self.bot.db.get(1,rid);r["spec"]["rules"]["any_roles"]=[11]
        await self.bot.db.edit(1,rid,r["spec"],0,9001)
        self.assertEqual((await self.bot.raffles.publish(self.guild,rid,9001))["status"],"open")
    async def test_self_claim_role_not_platform_proof(self):
        await self.bot.registry.execute("INSERT INTO resources VALUES(1,'role:claim_collectors',11,'role','ripcars-gate','{}','{}','active')")
        rid=await self.draft("platform",{"any_roles":[11]})
        with self.assertRaises(ValueError):await self.bot.raffles.publish(self.guild,rid,9001)
    async def test_rippers_not_contributor_proof(self):
        rid=await self.draft("contributors",{"all_roles":[10]})
        with self.assertRaises(ValueError):await self.bot.raffles.publish(self.guild,rid,9001)
    async def test_missing_rule_role_blocks_publication(self):
        rid=await self.draft(rules={"any_roles":[999]})
        with self.assertRaises(ValueError):await self.bot.raffles.publish(self.guild,rid,9001)
    async def test_placeholder_prize_not_publishable(self):
        rid=await self.draft();r=await self.bot.db.get(1,rid);r["spec"]["prize"]="Describe the prize"
        await self.bot.db.edit(1,rid,r["spec"],0,9001)
        with self.assertRaisesRegex(ValueError,"actual prize"):await self.bot.raffles.publish(self.guild,rid,9001)
    async def test_everyone_role_not_platform_proof(self):
        rid=await self.draft("platform",{"any_roles":[1]})
        with self.assertRaises(ValueError):await self.bot.raffles.publish(self.guild,rid,9001)
    async def test_missing_access_blocks_publication(self):
        self.guild.public._overwrites=[]
        rid=await self.draft()
        with self.assertRaises(ValueError):await self.bot.raffles.publish(self.guild,rid,9001)
    async def test_duplicate_click_one_account(self):
        r=await self.published();self.guild.add_member(1000,[10])
        await self.click(r,1000);self.assertIn("already",await self.click(r,1000))
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM entries")),1)
    async def test_join_requires_fresh_member_role(self):
        r=await self.published();self.guild.add_member(1000,[])
        with self.assertRaises(ValueError):await self.click(r,1000)
        self.assertIn(1000,self.guild.fetches)
    async def test_join_pause_does_not_hide_status_or_leave(self):
        r=await self.fill(1);await self.save_cfg(enabled=False)
        self.assertTrue((await self.click(r,1000,"status"))["entry"])
        await self.click(r,1000,"leave")
        with self.assertRaises(ValueError):await self.click(r,1000)
    async def test_copied_button_message_is_rejected(self):
        r=await self.published();self.guild.add_member(1000,[10])
        msg=self.guild.public.messages[r["message"]];fake=copy.copy(msg);fake.id+=99
        with self.assertRaises(ValueError):await self.bot.raffles.member_action(interaction(self.bot,self.guild,self.guild.get_member(1000),fake),r["id"],"join")
    async def test_rules_do_not_reveal_seed(self):
        r=await self.fill(1);rules=await self.click(r,1000,"rules")
        self.assertNotIn(r["seed"],str(rules));self.assertEqual(rules["contract_hash"],r["contract_hash"])
    async def test_weight_checked_at_draw_time(self):
        r=await self.fill(2,{"weights":{"11":7}})
        self.guild.add_member(1000,[10,11])
        result=await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.assertEqual({p["user"]:p["weight"] for p in result["rounds"][0]["pool"]},{1000:7,1001:1})
    async def test_role_removed_before_draw_is_excluded(self):
        r=await self.fill(2);self.guild.add_member(1000,[])
        result=await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.assertEqual(result["rounds"][0]["winners"],[1001]);self.assertFalse(result["excluded"][0]["eligible"])
    async def test_departed_member_notfound_is_excluded(self):
        r=await self.fill(2);self.guild._members.pop(1000)
        result=await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.assertEqual(result["rounds"][0]["winners"],[1001])
    async def test_forbidden_member_fetch_stops_whole_draw(self):
        r=await self.fill(2);self.guild.failures[1000]=failure()
        with self.assertRaises(discord.Forbidden):await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        failed=await self.bot.db.get(1,r["id"])
        self.assertEqual(failed["rounds"],[]);self.assertEqual(failed["frozen"],[1000,1001]);self.assertEqual(failed["seed"],r["seed"])
    async def test_network_failure_never_silently_excludes(self):
        r=await self.fill(2);self.guild.failures[1000]=OSError("Connection lost")
        with self.assertRaises(OSError):await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.assertEqual((await self.bot.db.get(1,r["id"]))["rounds"],[])
    async def test_restart_after_failure_reuses_frozen_seed(self):
        r=await self.fill(3);self.guild.failures[1000]=failure()
        with self.assertRaises(discord.Forbidden):await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.guild.failures.clear();self.bot.db=Store(self.bot.db.path)
        result=await Raffles(self.bot).draw(self.guild,r["id"])
        self.assertEqual(result["seed"],r["seed"]);self.assertTrue(drawing.verify(await self.bot.db.public_bundle(1,r["id"])))
    async def test_empty_raffle_has_no_winner(self):
        r=await self.published();result=await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.assertEqual(result["rounds"][0]["winners"],[]);self.assertTrue(drawing.verify(await self.bot.db.public_bundle(1,r["id"])))
    async def test_minimum_is_eligible_not_raw_entries(self):
        r=await self.fill(2,{"minimum_entries":2});self.guild.add_member(1000,[])
        result=await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.assertEqual(result["rounds"][0]["winners"],[])
    async def test_result_is_saved_before_failed_announcement(self):
        r=await self.fill(3);result=await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.guild.public.messages[r["message"]].edit_error=failure()
        with self.assertLogs("ripcars.raffle",level="ERROR"):self.assertFalse(await self.bot.raffles.update_panel(self.guild,r["id"],True))
        again=await self.bot.raffles.draw(self.guild,r["id"])
        self.assertEqual(result["rounds"],again["rounds"])
    async def test_concurrent_draw_does_not_duplicate_awards(self):
        r=await self.fill(4);await self.bot.db.freeze(1,r["id"],9001,"early")
        results=await asyncio.gather(self.bot.raffles.draw(self.guild,r["id"]),self.bot.raffles.draw(self.guild,r["id"]),return_exceptions=True)
        self.assertTrue(any(isinstance(x,Conflict) for x in results));self.assertEqual(len(await self.bot.db.query("SELECT * FROM awards")),1)
    async def test_cancel_during_check_never_commits_winner(self):
        r=await self.fill(2);original=self.guild.fetch_member
        async def changed(uid):
            if uid==1000:await self.bot.db.state_action(1,r["id"],9001,"cancel","Operator cancelled")
            return await original(uid)
        self.guild.fetch_member=changed
        with self.assertRaises(Conflict):await self.bot.raffles.draw(self.guild,r["id"],9001,"early")
        self.assertEqual(await self.bot.db.query("SELECT * FROM awards"),[])
    async def test_reroll_requires_forfeiture(self):
        r=await self.draw_members()
        with self.assertRaises(ValueError):await self.bot.raffles.reroll(self.guild,r["id"],9001,"test")
    async def test_reroll_keeps_original_weights_and_excludes_prior_winner(self):
        r=await self.draw_members(5,{"weights":{"11":8}});first=r["rounds"][0]["winners"][0]
        await self.bot.db.award_status(1,r["id"],first,"forfeited",9001,"Winner declined")
        for uid in range(1000,1005):self.guild.add_member(uid,[10,11])
        result=await self.bot.raffles.reroll(self.guild,r["id"],9001,"Replacement approved")
        self.assertNotIn(first,result["rounds"][1]["winners"]);self.assertEqual({p["weight"] for p in result["rounds"][1]["pool"]},{1})
        self.assertTrue(drawing.verify(await self.bot.db.public_bundle(1,r["id"])))
    async def test_reroll_no_remaining_preserves_forfeiture(self):
        r=await self.draw_members(1);first=r["rounds"][0]["winners"][0]
        await self.bot.db.award_status(1,r["id"],first,"forfeited",9001,"Declined")
        with self.assertRaises(ValueError):await self.bot.raffles.reroll(self.guild,r["id"],9001,"replacement")
        self.assertEqual((await self.bot.db.query("SELECT status FROM awards"))[0]["status"],"forfeited")
    async def test_scheduled_panel_opens_without_republication(self):
        rid=await self.draft();r=await self.bot.db.get(1,rid);r["spec"]["starts_in"]=60
        await self.bot.db.edit(1,rid,r["spec"],0,9001);r=await self.bot.raffles.publish(self.guild,rid,9001)
        self.assertEqual(r["status"],"scheduled")
        await self.bot.db.execute("UPDATE raffles SET starts=0 WHERE id=?",(rid,));await self.bot.raffles.tick()
        self.assertEqual((await self.bot.db.get(1,rid))["status"],"open");self.assertEqual(len(self.guild.public.sent),1)
    async def test_global_pause_does_not_stop_already_frozen_job(self):
        r=await self.fill(2);await self.bot.db.freeze(1,r["id"],9001,"early");await self.save_cfg(enabled=False)
        await self.bot.raffles.tick();await asyncio.gather(*self.bot.jobs.values())
        self.assertEqual((await self.bot.db.get(1,r["id"]))["status"],"drawn")
    async def test_deleted_panel_is_not_automatically_reposted(self):
        r=await self.published();self.guild.public.messages.pop(r["message"])
        self.assertFalse(await self.bot.raffles.update_panel(self.guild,r["id"],True))
        self.assertEqual((await self.bot.db.get(1,r["id"]))["display_state"],"missing");self.assertEqual(len(self.guild.public.sent),1)
    async def test_unrelated_message_never_edited(self):
        r=await self.published();msg=self.guild.public.messages[r["message"]];msg.author=SimpleNamespace(id=888);before=len(msg.edits)
        with self.assertLogs("ripcars.raffle",level="ERROR"):await self.bot.raffles.update_panel(self.guild,r["id"],True)
        self.assertEqual(len(msg.edits),before);self.assertEqual((await self.bot.db.get(1,r["id"]))["display_state"],"review")
    async def test_ambiguous_publish_is_recovered_not_resent(self):
        rid=await self.draft();original=self.bot.db.bind_message
        self.bot.db.bind_message=AsyncMock(side_effect=OSError("DB temporarily unavailable"))
        with self.assertRaises(OSError):await self.bot.raffles.publish(self.guild,rid,9001)
        self.bot.db.bind_message=original;r=await self.bot.db.get(1,rid);mid=next(iter(self.guild.public.messages))
        await self.bot.raffles.recover_message(self.guild,rid,mid,9001)
        self.assertEqual((await self.bot.db.get(1,rid))["seed"],r["seed"]);self.assertEqual(len(self.guild.public.sent),1)
    async def test_recovery_foreign_message_is_rejected(self):
        rid=await self.draft();await self.bot.db.begin_publish(1,rid,100,self.cfg,9001)
        self.guild.public.messages[123]=Message(123,self.guild.public,SimpleNamespace(id=888),discord.Embed(),None)
        with self.assertRaises(ValueError):await self.bot.raffles.recover_message(self.guild,rid,123,9001)
        self.assertEqual((await self.bot.db.get(1,rid))["status"],"publishing")
    async def test_member_cannot_inspect_private_draft(self):
        rid=await self.draft();user=self.guild.add_member(1000,[10])
        with self.assertRaises(ValueError):await self.bot.raffles.inspect(self.guild,rid,user)
    async def test_cancelled_panel_button_join_rejected(self):
        r=await self.fill(1);await self.bot.db.state_action(1,r["id"],9001,"cancel","Test cancellation")
        with self.assertRaises(ValueError):await self.click(r,1000)
    async def test_complete_join_draw_claim_delivery_flow(self):
        r=await self.fill(5);result=await self.bot.raffles.draw(self.guild,r["id"],9001,"Smoke scenario")
        winner=result["rounds"][0]["winners"][0]
        self.assertIn("recorded",await self.click(result,winner,"claim"))
        await self.bot.db.award_status(1,r["id"],winner,"delivered",9001,"Physical prize delivery confirmed")
        await self.bot.raffles.update_panel(self.guild,r["id"],True)
        self.assertIn("delivered",str(self.guild.public.messages[r["message"]].embeds[0].to_dict()))
        self.assertTrue(drawing.verify(await self.bot.db.public_bundle(1,r["id"])))
