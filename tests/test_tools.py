import asyncio
import gzip
import json
import os
from pathlib import Path
import subprocess
import tarfile
import zipfile
from unittest.mock import patch

from ripcars_raffle import drawing
from ripcars_raffle.storage import Store
from scripts.build_release import build
from scripts.preflight import check
from scripts.verify_draw import verify_file
from tests.fakes import AsyncCase


class ToolTests(AsyncCase):
    async def test_preflight_without_credentials(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(check()["mode"],"offline")
    async def test_preflight_rejects_token_placeholder(self):
        with patch.dict(os.environ,{"DISCORD_TOKEN":"YOUR_NEW_RAFFLE_BOT_TOKEN"},clear=True):
            with self.assertRaises(ValueError):check(True)
    async def test_preflight_rejects_old_python(self):
        with patch("scripts.preflight.sys.version_info",(3,10)):
            with self.assertRaises(ValueError):check()
    async def test_preflight_rejects_invalid_guild(self):
        with patch.dict(os.environ,{"DISCORD_TOKEN":"not-a-real-token","GUILD_ID":"server"},clear=True):
            with self.assertRaises(ValueError):check(True)
    async def test_preflight_accepts_distinct_writable_paths(self):
        with patch.dict(os.environ,{"DISCORD_TOKEN":"not-a-real-token","GUILD_ID":"1","DB_PATH":str(self.root/"db.sqlite3"),"COORDINATION_PATH":str(self.root/"shared.sqlite3")},clear=True):
            self.assertEqual(check(True)["mode"],"deployment")
    async def test_offline_verifier_plain_and_gzip(self):
        r=await self.draw_members();raw=json.dumps(await self.bot.db.public_bundle(1,r["id"])).encode()
        plain=self.root/"proof.json";compressed=self.root/"proof.json.gz"
        plain.write_bytes(raw);compressed.write_bytes(gzip.compress(raw))
        self.assertTrue(verify_file(plain));self.assertTrue(verify_file(compressed))
    async def test_verifier_cli_success_and_tamper_exit_codes(self):
        import sys
        r=await self.draw_members();bundle=await self.bot.db.public_bundle(1,r["id"]);path=self.root/"proof.json"
        path.write_bytes(json.dumps(bundle).encode());script=Path(__file__).resolve().parent.parent/"scripts"/"verify_draw.py"
        proc=await asyncio.to_thread(subprocess.run,[sys.executable,str(script),str(path)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0);self.assertIn("VALID",proc.stdout)
        bundle["seed"]="f"*64;path.write_bytes(json.dumps(bundle).encode())
        proc=await asyncio.to_thread(subprocess.run,[sys.executable,str(script),str(path)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,1);self.assertIn("INVALID",proc.stdout)
    async def test_archive_builder_excludes_private_state_and_cache(self):
        source=self.root/"source";source.mkdir()
        (source/"VERSION").write_bytes(b"1.0.0\n");(source/"main.py").write_bytes(b"pass\n")
        for name in (".env","live.sqlite3","cache.pyc"):(source/name).write_bytes(b"private")
        (source/".env.example").write_bytes(b"DISCORD_TOKEN=YOUR_NEW_TOKEN\n")
        for dirname in (".venv","data","backups","__pycache__"):
            (source/dirname).mkdir();(source/dirname/"private.txt").write_bytes(b"private")
        tarpath,zippath,count=build(source,self.root/"output")
        self.assertEqual(count,3)
        with tarfile.open(tarpath) as archive:tarset={m.name for m in archive.getmembers()}
        with zipfile.ZipFile(zippath) as archive:zipset=set(archive.namelist())
        self.assertEqual(tarset,zipset);self.assertTrue(all("private" not in x for x in tarset));self.assertIn("ripcars-raffle/.env.example",tarset)
    async def test_archive_builder_rejects_in_source_destination(self):
        with self.assertRaises(ValueError):build(Path(__file__).resolve().parent.parent,Path(__file__).resolve().parent)
    async def test_thousand_concurrent_entries_cross_connections(self):
        rid=await self.draft();cfg=dict(self.cfg,max_entries=700)
        await self.bot.db.begin_publish(1,rid,100,cfg,9001);await self.bot.db.bind_message(1,rid,10000)
        other=Store(self.bot.db.path)
        async def enter(n):
            try:return await (self.bot.db if n%2 else other).enter(1,rid,10000+n)
            except ValueError:return False
        results=await asyncio.gather(*(enter(n) for n in range(1000)))
        self.assertEqual(sum(results),700);self.assertEqual(len(await self.bot.db.query("SELECT * FROM entries WHERE raffle=?",(rid,))),700)
    async def test_large_selection_unique_without_network(self):
        pool=[{"user":10000+n,"weight":n%10+1} for n in range(100000)]
        result=await asyncio.to_thread(drawing.select,"a"*64,"large-pool",pool,25)
        self.assertEqual(len(result),25);self.assertEqual(len(set(result)),25)
