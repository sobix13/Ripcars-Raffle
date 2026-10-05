import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock,patch

import discord

from ripcars_raffle import __version__
from ripcars_raffle.bot import RaffleBot,load_env,main
from ripcars_raffle.commands import Commands
from tests.fakes import AsyncCase


class BootTests(AsyncCase):
    async def test_version(self):self.assertEqual(__version__,(Path(__file__).resolve().parents[1]/'VERSION').read_text().strip())
    async def test_only_guild_and_members_intents(self):
        self.assertEqual(self.bot.intents.value,discord.Intents(guilds=True,members=True).value)
        self.assertFalse(self.bot.intents.message_content)
    async def test_user_install_and_direct_message_context_disabled(self):
        self.assertFalse(self.bot.tree.allowed_installs.user);self.assertTrue(self.bot.tree.allowed_installs.guild)
        self.assertFalse(self.bot.tree.allowed_contexts.dm_channel)
    async def test_private_and_shared_database_cannot_be_same(self):
        with self.assertRaises(ValueError):RaffleBot(self.root/"same.db",self.root/"same.db")
    async def test_sdk_registers_expected_commands(self):
        await self.bot.add_cog(Commands(self.bot));group=self.bot.tree.get_command("raffle")
        names={x.name for x in group.commands}
        self.assertEqual(len(names),23);self.assertLessEqual(len(names),25)
        self.assertTrue({"setup","audit","reroll","award","health","doctor","import-config"}<=names)
    async def test_setup_hook_offline_sync_and_background_tasks(self):
        with patch.object(self.bot.tree,"sync",new=AsyncMock()),patch.dict(os.environ,{"GUILD_ID":"1"}):
            await self.bot.setup_hook()
        self.assertIsNotNone(self.bot.scheduler_task);self.assertIsNotNone(self.bot.backup_task);self.assertIsNotNone(self.bot.watchdog_task)
    async def test_close_cancels_worker(self):
        worker=asyncio.create_task(asyncio.Event().wait());self.bot.jobs[1]=worker
        await self.bot.close();self.assertTrue(worker.cancelled());self.assertEqual(self.bot.jobs,{})
    async def test_environment_loader_does_not_override_existing_token(self):
        with patch("pathlib.Path.is_file",return_value=True),patch("pathlib.Path.read_text",return_value="DISCORD_TOKEN=from-file\nDB_PATH=test.db\nUNKNOWN=ignored\n"),patch.dict(os.environ,{"DISCORD_TOKEN":"already-set"},clear=True):
            load_env("test.env")
            self.assertEqual(os.environ["DISCORD_TOKEN"],"already-set");self.assertEqual(os.environ["DB_PATH"],"test.db");self.assertNotIn("UNKNOWN",os.environ)
    async def test_missing_token_never_attempts_live_login(self):
        with patch.dict(os.environ,{},clear=True),patch("ripcars_raffle.bot.load_env"),patch("ripcars_raffle.bot.RaffleBot") as sdk:
            with self.assertRaises(SystemExit):main()
        sdk.assert_not_called()
