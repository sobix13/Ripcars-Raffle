import time

from ripcars_raffle.coordination import Registry
from ripcars_raffle.storage import Conflict
from tests.fakes import AsyncCase


class CoordinationTests(AsyncCase):
    async def test_gate_ids_import_without_ownership_change(self):
        await self.bot.registry.execute("INSERT INTO resources VALUES(1,'role:rippers',10,'role','ripcars-gate','{}','{}','active')")
        before=await self.bot.registry.resources(1)
        self.assertEqual(await self.bot.registry.gate_ids(1),{"member_role":10});self.assertEqual(before,await self.bot.registry.resources(1))
    async def test_own_message_record_only(self):
        await self.bot.registry.record(1,8,200,777,"a"*64)
        r=(await self.bot.registry.resources(1))["raffle:message:777:8"]
        self.assertEqual((r["kind"],r["owner"]),("message","bot:777"))
    async def test_foreign_binding_is_not_overwritten(self):
        await self.bot.registry.record(1,8,200,777,"a"*64)
        await self.bot.registry.execute("UPDATE resources SET owner='bot:888'")
        before=await self.bot.registry.resources(1)
        with self.assertRaises(Conflict):await self.bot.registry.record(1,8,300,777,"b"*64)
        self.assertEqual(before,await self.bot.registry.resources(1))
    async def test_matching_binding_is_idempotent(self):
        await self.bot.registry.record(1,8,200,777,"a"*64);await self.bot.registry.record(1,8,200,777,"a"*64)
        self.assertEqual(len(await self.bot.registry.resources(1)),1)
    async def test_gate_setup_lease_blocks_only_registry_write(self):
        other=Registry(self.bot.registry.path)
        async with other.lease(1):
            r=await self.published()
            self.assertEqual(r["status"],"open")
            events=await self.bot.db.query("SELECT action FROM audit WHERE action='registry_retry_needed'")
            self.assertEqual(len(events),1)
        self.assertEqual(await self.bot.registry.resources(1),{})
        await self.bot.raffles.update_panel(self.guild,r["id"],True)
        self.assertEqual(len(await self.bot.registry.resources(1)),1)
    async def test_independent_raffle_apps_do_not_share_a_message_key(self):
        await self.bot.registry.record(1,8,200,777,"a"*64);await self.bot.registry.record(1,8,300,888,"b"*64)
        self.assertEqual(len(await self.bot.registry.resources(1)),2)
    async def test_manual_binding_is_preserved(self):
        await self.bot.registry.record(1,8,200,777,"a"*64)
        await self.bot.registry.execute("UPDATE resources SET state='manual'")
        before=await self.bot.registry.resources(1)
        with self.assertRaises(Conflict):await self.bot.registry.record(1,8,200,777,"a"*64)
        self.assertEqual(before,await self.bot.registry.resources(1))
    async def test_stale_lease_release_preserves_new_holder(self):
        async with self.bot.registry.lease(1):
            await self.bot.registry.execute("UPDATE locks SET token='new',expires=? WHERE guild=1",(time.time()+60,))
        self.assertEqual((await self.bot.registry.query("SELECT token FROM locks"))[0]["token"],"new")
    async def test_guild_scoped_registry(self):
        await self.bot.registry.record(2,8,200,777,"a"*64)
        self.assertEqual(await self.bot.registry.resources(1),{})
    async def test_shared_schema_matches_gate_and_crew(self):
        cols=[r["name"] for r in await self.bot.registry.query("PRAGMA table_info(resources)")]
        self.assertEqual(cols,["guild","key","object_id","kind","owner","baseline","desired","state"])
        cols=[r["name"] for r in await self.bot.registry.query("PRAGMA table_info(locks)")]
        self.assertEqual(cols,["guild","name","token","expires"])
