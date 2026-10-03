import asyncio
import copy
import json
import time

from ripcars_raffle import drawing
from ripcars_raffle.storage import Conflict,Store
from tests.fakes import AsyncCase


class StorageTests(AsyncCase):
    async def low_level_published(self):
        rid=await self.draft()
        r=await self.bot.db.begin_publish(1,rid,100,self.cfg,9001)
        await self.bot.db.bind_message(1,rid,999)
        return r
    async def test_settings_compare_and_swap(self):
        with self.assertRaises(Conflict):await self.bot.db.save(1,self.cfg,0,9001)
    async def test_guild_isolation(self):
        rid=await self.draft()
        with self.assertRaises(ValueError):await self.bot.db.get(2,rid)
        self.assertFalse((await self.bot.db.settings(2))[0]["enabled"])
    async def test_file_permissions_private(self):
        self.assertEqual(self.bot.db.path and __import__('os').stat(self.bot.db.path).st_mode&0o777,0o600)
    async def test_draft_revision_and_publish_freeze(self):
        rid=await self.draft();r=await self.bot.db.get(1,rid)
        await self.bot.db.edit(1,rid,r["spec"],0,9001)
        with self.assertRaises(Conflict):await self.bot.db.edit(1,rid,r["spec"],0,9001)
        await self.bot.db.begin_publish(1,rid,100,self.cfg,9001)
        with self.assertRaises(Conflict):await self.bot.db.edit(1,rid,r["spec"],1,9001)
    async def test_duplicate_join_and_leave_rejoin(self):
        r=await self.low_level_published();rid=r["id"]
        self.assertTrue(await self.bot.db.enter(1,rid,1000))
        self.assertFalse(await self.bot.db.enter(1,rid,1000))
        self.assertTrue(await self.bot.db.leave(1,rid,1000))
        self.assertTrue(await self.bot.db.enter(1,rid,1000))
        rows=await self.bot.db.query("SELECT * FROM entries WHERE raffle=?",(rid,));self.assertEqual(len(rows),1)
    async def test_deadline_exclusive(self):
        r=await self.low_level_published()
        with self.assertRaises(ValueError):await self.bot.db.enter(1,r["id"],1000,now=r["ends"])
    async def test_scheduled_no_early_join(self):
        rid=await self.draft();r=await self.bot.db.get(1,rid);r["spec"]["starts_in"]=3600
        await self.bot.db.edit(1,rid,r["spec"],0,9001)
        await self.bot.db.begin_publish(1,rid,100,self.cfg,9001)
        await self.bot.db.bind_message(1,rid,999)
        with self.assertRaises(ValueError):await self.bot.db.enter(1,rid,1000)
    async def test_capacity_cross_connection_atomic(self):
        cfg=copy.deepcopy(self.cfg);cfg["max_entries"]=5
        rid=await self.draft();await self.bot.db.begin_publish(1,rid,100,cfg,9001);await self.bot.db.bind_message(1,rid,999)
        other=Store(self.bot.db.path)
        async def enter(n):
            try:return await (other if n%2 else self.bot.db).enter(1,rid,1000+n)
            except ValueError:return False
        results=await asyncio.gather(*(enter(n) for n in range(40)))
        self.assertEqual(sum(results),5)
    async def test_freeze_locks_entries(self):
        r=await self.low_level_published();await self.bot.db.enter(1,r["id"],1000)
        await self.bot.db.freeze(1,r["id"],9001,"early")
        with self.assertRaises(ValueError):await self.bot.db.leave(1,r["id"],1000)
        with self.assertRaises(ValueError):await self.bot.db.enter(1,r["id"],1001)
        self.assertEqual((await self.bot.db.get(1,r["id"]))["frozen"],[1000])
    async def test_seed_not_exported_before_draw(self):
        r=await self.low_level_published()
        with self.assertRaises(ValueError):await self.bot.db.public_bundle(1,r["id"])
    async def test_duplicate_binding_rejected(self):
        r=await self.low_level_published()
        with self.assertRaises(Conflict):await self.bot.db.bind_message(1,r["id"],10000)
    async def test_pause_resume(self):
        r=await self.low_level_published();rid=r["id"]
        await self.bot.db.state_action(1,rid,9001,"pause","test")
        with self.assertRaises(ValueError):await self.bot.db.enter(1,rid,1000)
        await self.bot.db.state_action(1,rid,9001,"resume","test")
        self.assertTrue(await self.bot.db.enter(1,rid,1000))
    async def test_cancel_retains_entries(self):
        r=await self.low_level_published();await self.bot.db.enter(1,r["id"],1000)
        await self.bot.db.state_action(1,r["id"],9001,"cancel","cancelled test")
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM entries")),1)
        with self.assertRaises(ValueError):await self.bot.db.enter(1,r["id"],1001)
    async def test_past_draw_cannot_cancel(self):
        r=await self.draw_members()
        with self.assertRaises(ValueError):await self.bot.db.state_action(1,r["id"],9001,"cancel","bad")
    async def test_live_exclusion_does_not_delete_history(self):
        r=await self.low_level_published();await self.bot.db.enter(1,r["id"],1000)
        await self.bot.db.exclude(1,r["id"],1000,9001,"Reviewed abuse")
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM entries")),1)
        with self.assertRaises(ValueError):await self.bot.db.enter(1,r["id"],1000)
    async def test_exclusion_closed_after_freeze(self):
        r=await self.low_level_published();await self.bot.db.freeze(1,r["id"],9001,"early")
        with self.assertRaises(ValueError):await self.bot.db.exclude(1,r["id"],1000,9001,"too late")
    async def test_lease_conflict_two_connections(self):
        other=Store(self.bot.db.path)
        async with self.bot.db.job(1,"draw"):
            with self.assertRaises(Conflict):
                async with other.job(1,"draw"):pass
        async with other.job(1,"draw"):pass
    async def test_stale_renew_rejected(self):
        async with self.bot.db.job(1,"draw"):
            with self.assertRaises(Conflict):await self.bot.db.renew_job(1,"draw","wrong-token")
    async def test_claim_idempotent_and_nonwinner_rejected(self):
        r=await self.draw_members();winner=r["rounds"][0]["winners"][0]
        await self.bot.db.claim(1,r["id"],winner);await self.bot.db.claim(1,r["id"],winner)
        with self.assertRaises(ValueError):await self.bot.db.claim(1,r["id"],99999)
        self.assertEqual((await self.bot.db.query("SELECT status FROM awards"))[0]["status"],"claimed")
    async def test_claim_deadline(self):
        r=await self.draw_members();winner=r["rounds"][0]["winners"][0]
        with self.assertRaises(ValueError):await self.bot.db.claim(1,r["id"],winner,now=r["claim_until"]+1)
    async def test_delivery_is_final(self):
        r=await self.draw_members();winner=r["rounds"][0]["winners"][0]
        await self.bot.db.award_status(1,r["id"],winner,"delivered",9001,"Team confirmed delivery")
        with self.assertRaises(ValueError):await self.bot.db.award_status(1,r["id"],winner,"forfeited",9001,"bad")
    async def test_failed_draw_moves_to_review_then_retry(self):
        r=await self.low_level_published();rid=r["id"];await self.bot.db.freeze(1,rid,9001,"early")
        for _ in range(5):await self.bot.db.fail_job(1,rid,"API unavailable")
        failed=await self.bot.db.get(1,rid);self.assertEqual(failed["status"],"review")
        await self.bot.db.state_action(1,rid,9001,"retry","Permission repaired")
        retried=await self.bot.db.get(1,rid);self.assertEqual(retried["seed"],r["seed"]);self.assertEqual(retried["status"],"closing")
    async def test_config_restore_pauses_not_draw_rollback(self):
        r=await self.draw_members();cfg,rev=await self.bot.db.settings(1)
        cfg["brand"]="Another title";await self.bot.db.save(1,cfg,rev,9001)
        rows=await self.bot.db.query("SELECT id FROM history WHERE guild=1 ORDER BY id DESC")
        restored=await self.bot.db.restore(1,rows[0]["id"],9001)
        self.assertFalse(restored["enabled"]);self.assertEqual((await self.bot.db.get(1,r["id"]))["status"],"drawn")
    async def test_history_retention(self):
        for n in range(35):
            cfg,rev=await self.bot.db.settings(1);cfg["brand"]=f"Rip Cars {n}";await self.bot.db.save(1,cfg,rev,9001)
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM history WHERE guild=1")),30)
    async def test_config_export_has_no_raffle_seed(self):
        r=await self.low_level_published();cfg,_=await self.bot.db.settings(1)
        self.assertNotIn(r["seed"],json.dumps(cfg))
    async def test_successful_draw_is_immutable_and_replayable(self):
        r=await self.draw_members()
        bundle=await self.bot.db.public_bundle(1,r["id"])
        self.assertTrue(drawing.verify(bundle))
        with self.assertRaises(Conflict):await self.bot.db.finalize(1,r["id"],r["rounds"][0],r["excluded"])
    async def test_worker_cancellation_finishes_transaction(self):
        import threading
        ready=threading.Event()
        def write(c):
            ready.set();time.sleep(0.03)
            c.execute("INSERT INTO audit(guild,actor,action,detail,at) VALUES(1,1,'cancel_test','done',0)")
        task=asyncio.create_task(self.bot.db.run(write))
        await asyncio.to_thread(ready.wait)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(len(await self.bot.db.query("SELECT * FROM audit WHERE action='cancel_test'")),1)
