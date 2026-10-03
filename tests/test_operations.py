import copy
import gzip
import json
import os
import sqlite3
from unittest.mock import patch

from ripcars_raffle import operations
from tests.fakes import AsyncCase,Channel,failure,interaction


class OperationsTests(AsyncCase):
    async def test_real_sdk_private_log_own_bot_allowed(self):
        self.assertTrue(operations.private_channel(self.guild,self.guild.log,self.cfg))
        self.assertTrue(self.guild.log.permissions_for(self.guild.me).view_channel)
        self.assertFalse(self.guild.log.permissions_for(self.guild.get_role(10)).view_channel)
    async def test_public_log_is_rejected(self):
        self.assertFalse(operations.private_channel(self.guild,self.guild.public,self.cfg))
    async def test_role_overwrite_leak_is_rejected(self):
        private=Channel(self.guild,103,"leaked-log",[{"id":"1","type":0,"allow":"0","deny":"1024"},{"id":"11","type":0,"allow":"1024","deny":"0"}])
        self.assertFalse(operations.private_channel(self.guild,private,self.cfg))
    async def test_member_overwrite_leak_is_rejected(self):
        self.guild.add_member(1000,[10])
        private=Channel(self.guild,103,"leaked-log",[{"id":"1","type":0,"allow":"0","deny":"1024"},{"id":"1000","type":1,"allow":"1024","deny":"0"}])
        self.assertFalse(operations.private_channel(self.guild,private,self.cfg))
    async def test_uncached_member_allow_is_not_assumed_private(self):
        private=Channel(self.guild,103,"unknown-member-log",[{"id":"1","type":0,"allow":"0","deny":"1024"},{"id":"9876","type":1,"allow":"1024","deny":"0"}])
        self.assertFalse(operations.private_channel(self.guild,private,self.cfg))
    async def test_doctor_no_administrator_required(self):
        blockers,notes=await operations.doctor(self.bot,self.guild)
        self.assertEqual(blockers,[]);self.assertFalse(self.guild.me.guild_permissions.administrator)
        self.assertTrue(any("wallet" in x for x in notes))
    async def test_doctor_rejects_managed_member_role(self):
        cfg=copy.deepcopy(self.cfg);cfg["member_role"]=50
        self.assertTrue((await operations.doctor(self.bot,self.guild,cfg))[0])
    async def test_doctor_rejects_missing_manager_role(self):
        cfg=copy.deepcopy(self.cfg);cfg["manager_roles"]=[999]
        self.assertIn("Manager role 999 is missing.",(await operations.doctor(self.bot,self.guild,cfg))[0])
    async def test_manager_cannot_change_admin_settings(self):
        i=interaction(self.bot,self.guild,self.guild.add_member(1000,[10,30]))
        self.assertEqual((await operations.authorized(self.bot,i))["member_role"],10)
        with self.assertRaises(ValueError):await operations.authorized(self.bot,i,True)
    async def test_bot_account_never_admin(self):
        user=self.guild.add_member(1000,[31],bot=True)
        self.assertFalse(operations.is_admin(user));self.assertFalse(operations.is_manager(user,self.cfg))
    async def test_removed_manager_role_is_rechecked(self):
        i=interaction(self.bot,self.guild,self.guild.add_member(1000,[10,30]))
        cfg,rev=await self.bot.db.settings(1);cfg["manager_roles"]=[];await self.bot.db.save(1,cfg,rev,9001)
        with self.assertRaises(ValueError):await operations.authorized(self.bot,i)
    async def test_reply_never_pings_everyone(self):
        i=interaction(self.bot,self.guild)
        await operations.reply(i,"@everyone")
        flags=i.response.send_message.call_args.kwargs
        self.assertTrue(flags["ephemeral"]);self.assertFalse(flags["allowed_mentions"].everyone)
    async def test_long_diagnostics_preserve_full_text_as_attachment(self):
        i=interaction(self.bot,self.guild);detail="Diagnostic "*500
        await operations.reply(i,detail)
        args=i.response.send_message.call_args
        self.assertLessEqual(len(args.args[0]),2000);self.assertEqual(args.kwargs["files"][0].fp.getvalue().decode(),detail)
    async def test_reporter_redacts_token_and_seed(self):
        with patch.dict(os.environ,{"DISCORD_TOKEN":"fake-test-token-not-real"}):
            with self.assertLogs("ripcars.raffle",level="ERROR") as logs:
                eid=await self.bot.reporter.error(self.guild,"test",RuntimeError("fake-test-token-not-real "+"a"*64))
        output=" ".join(logs.output)+str(await self.bot.db.query("SELECT detail FROM audit WHERE action='error'"))
        self.assertNotIn("fake-test-token-not-real",output);self.assertNotIn("a"*64,output);self.assertTrue(eid.startswith("RCR-"))
    async def test_log_never_sent_if_channel_becomes_public(self):
        cfg,rev=await self.bot.db.settings(1);cfg["log_channel"]=100;await self.bot.db.save(1,cfg,rev,9001)
        self.assertFalse(await self.bot.reporter.log(self.guild,"Private","secret"));self.assertEqual(self.guild.public.sent,[])
    async def test_log_forbidden_is_safe(self):
        self.guild.log.error=failure();self.assertFalse(await self.bot.reporter.log(self.guild,"Test","Details"))
    async def test_error_alert_rate_limit(self):
        with self.assertLogs("ripcars.raffle",level="ERROR"):
            await self.bot.reporter.error(self.guild,"test",RuntimeError("one"));await self.bot.reporter.error(self.guild,"test",RuntimeError("two"))
        self.assertEqual(len(self.guild.log.sent),1)
    async def test_backup_integrity_permissions_and_retention(self):
        target=None
        for _ in range(4):target=await operations.backup(self.bot.db,self.root/"backups",2)
        self.assertEqual(len(list((self.root/"backups").glob("*.sqlite3"))),2);self.assertEqual(os.stat(target).st_mode&0o777,0o600)
        with sqlite3.connect(target) as c:self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    async def test_health_schema_and_private_data(self):
        result=await operations.health(self.bot,self.guild)
        self.assertEqual(result["database"],"ok");self.assertEqual(result["coordination"],"ok")
        self.assertNotIn("seed",result);self.assertNotIn("token",result)
    async def test_small_and_compressed_exports_roundtrip(self):
        f=operations.attachment("data.json",{"test":"ok"})
        self.assertEqual(json.loads(f.fp.getvalue()),{"test":"ok"});f.close()
        original=b"x"*(6*1024*1024+1);f=operations.attachment("data.csv",original)
        self.assertEqual(f.filename,"data.csv.gz");self.assertEqual(gzip.decompress(f.fp.getvalue()),original);f.close()
