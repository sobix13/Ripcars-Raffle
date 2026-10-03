from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import socket
import time

import discord
from discord.ext import commands

from . import __version__
from .storage import Store
from .coordination import Registry
from .operations import Reporter, backup
from .service import Raffles


def notify(message):
    address=os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address="\0"+address[1:]
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(address);sock.send(message.encode())
        except OSError:
            logging.getLogger("ripcars.raffle").warning("systemd notification unavailable")


class RaffleBot(commands.Bot):
    def __init__(self,db_path=None,coordination_path=None):
        intents=discord.Intents.none()
        intents.guilds=True;intents.members=True
        super().__init__(command_prefix=commands.when_mentioned,intents=intents,allowed_mentions=discord.AllowedMentions.none(),
                         allowed_installs=discord.app_commands.AppInstallationType(guild=True,user=False),
                         allowed_contexts=discord.app_commands.AppCommandContext(guild=True,dm_channel=False,private_channel=False))
        self.db=Store(db_path or os.getenv("DB_PATH","data/raffle.sqlite3"))
        self.registry=Registry(coordination_path or os.getenv("COORDINATION_PATH","data/coordination.sqlite3"))
        if Path(self.db.path).resolve()==Path(self.registry.path).resolve():
            raise ValueError("Private raffle database and shared coordination must have different paths.")
        self.raffles=Raffles(self)
        self.reporter=Reporter(self)
        self.settings_cache={}
        self.started=time.time()
        self.jobs={}
        self.scheduler_task=None
        self.backup_task=None
        self.watchdog_task=None
        self.backup_path=Path(self.db.path).parent/"backups"
        self.backup_last={}
        self.last_tick=None

    async def setup_hook(self):
        await self.db.open();await self.registry.open()
        from .commands import Commands
        from .public_ui import RaffleButton
        await self.add_cog(Commands(self))
        self.add_dynamic_items(RaffleButton)
        gid=os.getenv("GUILD_ID","").strip()
        if gid:
            guild=discord.Object(id=int(gid))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.scheduler_task=asyncio.create_task(self.scheduler(),name="raffle-scheduler")
        self.backup_task=asyncio.create_task(self.backups(),name="raffle-backups")
        self.watchdog_task=asyncio.create_task(self.watchdog(),name="raffle-watchdog")

    async def on_ready(self):
        notify("READY=1")
        logging.getLogger("ripcars.raffle").info("online as %s version %s in %d guilds",self.user,__version__,len(self.guilds))

    async def scheduler(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.raffles.tick()
                self.last_tick=time.time()
            except Exception as exc:
                await self.reporter.error(None,"scheduler",exc)
            # Per-guild settings are used for display/entry; poll at the smallest configured cadence.
            periods=[(await self.db.settings(g.id))[0]["poll_seconds"] for g in self.guilds]
            await asyncio.sleep(min(periods,default=15))

    async def backups(self):
        await self.wait_until_ready()
        last=0
        while not self.is_closed():
            try:
                cfgs=[(await self.db.settings(g.id))[0] for g in self.guilds]
                hours=min((c["backup_hours"] for c in cfgs),default=24)
                keep=max((c["backup_keep"] for c in cfgs),default=14)
                if time.time()-last>=hours*3600:
                    target=await backup(self.db,self.backup_path,keep)
                    last=time.time()
                    self.backup_last={"at":last,"file":target.name}
            except Exception as exc:
                await self.reporter.error(None,"backup",exc)
            await asyncio.sleep(60)

    async def watchdog(self):
        while not self.is_closed():
            if self.scheduler_task and not self.scheduler_task.done():
                notify("WATCHDOG=1")
            await asyncio.sleep(30)

    async def close(self):
        pending=[x for x in (self.scheduler_task,self.backup_task,self.watchdog_task,*self.jobs.values()) if x and x is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending,return_exceptions=True)
        self.jobs.clear()
        await super().close()


def load_env(path=".env"):
    p=Path(path)
    if p.is_file():
        for raw in p.read_text().splitlines():
            line=raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key,value=line.split("=",1)
            if key in ("DISCORD_TOKEN","GUILD_ID","DB_PATH","COORDINATION_PATH","LOG_LEVEL"):
                os.environ.setdefault(key,value.strip().strip("\"'"))


def main():
    load_env()
    token=os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Configure the private environment file.")
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(asctime)s %(levelname)s %(name)s %(message)s")
    RaffleBot().run(token,log_handler=None)
