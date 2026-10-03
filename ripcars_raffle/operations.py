from __future__ import annotations

import asyncio
import io
import json
import gzip
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

import discord

from . import __version__


def is_admin(member):
    return not member.bot and (member.guild_permissions.administrator or member.guild_permissions.manage_guild)


def is_manager(member, cfg):
    return is_admin(member) or (not member.bot and bool({r.id for r in member.roles} & set(cfg["manager_roles"])))


def private_channel(guild, channel, cfg):
    if not channel or not guild.me:
        return False
    for role in guild.roles:
        if role.is_bot_managed() and role.tags.bot_id == guild.me.id:
            continue
        staff = role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_messages or role.id in cfg["manager_roles"]
        if not staff and channel.permissions_for(role).view_channel:
            return False
    for target, overwrite in channel.overwrites.items():
        if isinstance(target,discord.Object) and overwrite.view_channel is True:
            return False
        if isinstance(target, discord.Member) and overwrite.view_channel is True and target.id != guild.me.id and not is_manager(target, cfg) and not target.guild_permissions.manage_messages:
            return False
    return True


def visible(channel, user):
    return bool(channel and channel.permissions_for(user).view_channel and channel.permissions_for(user).read_message_history)


def text_channel(guild, channel_id):
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Choose an existing text channel. Raffle does not create or rename channels.")
    return channel


def channel_flags(channel, guild):
    perm = channel.permissions_for(guild.me)
    return [k for k in ("view_channel", "read_message_history", "send_messages", "embed_links", "attach_files") if not getattr(perm, k)]


async def doctor(bot, guild, cfg=None):
    cfg = cfg or (await bot.db.settings(guild.id))[0]
    blockers, notes = [], []
    role = guild.get_role(cfg["member_role"])
    if not role or role.is_default() or role.managed:
        blockers.append("Select the ordinary Rippers member role.")
    if not bot.intents.members:
        blockers.append("Server Members Intent is required.")
    for key in ("channel", "log_channel"):
        try:
            ch = text_channel(guild, cfg[key])
            missing = channel_flags(ch, guild)
            if missing:
                blockers.append(key+": missing "+", ".join(missing))
            if key == "log_channel" and not private_channel(guild, ch, cfg):
                blockers.append("Log channel must be staff-only.")
        except ValueError as exc:
            blockers.append(f"{key}: {exc}")
    for role_id in cfg["manager_roles"]:
        if not guild.get_role(role_id):
            blockers.append(f"Manager role {role_id} is missing.")
    if guild.me.guild_permissions.administrator:
        notes.append("Administrator is unnecessary. Raffle needs no role, channel, ban, or message-management permissions.")
    notes.append("Eligibility uses Discord roles, not live wallet balances, platform points, message counts, or X likes.")
    rows = await bot.db.query("SELECT id,status,display_state FROM raffles WHERE guild=? AND (status IN ('publishing','review') OR display_state IN ('missing','review'))", (guild.id,))
    notes.extend(f"Raffle #{r['id']}: {r['status']}; panel={r['display_state']} needs review." for r in rows)
    return blockers, notes


async def reply(interaction, content=None, **kwargs):
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
    if isinstance(content,str) and len(content)>1900:
        exports=list(kwargs.pop("files",[]))
        existing=kwargs.pop("file",None)
        if existing:
            exports.append(existing)
        exports.append(attachment("raffle-details.txt",content.encode("utf-8")))
        kwargs["files"]=exports
        content=content[:1700]+"\nFull details attached."
    if interaction.response.is_done():
        return await interaction.followup.send(content, **kwargs)
    return await interaction.response.send_message(content, **kwargs)


async def authorized(bot, interaction, admin=False):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise ValueError("Use this command in the server.")
    cfg, _ = await bot.db.settings(interaction.guild_id)
    bot.settings_cache[interaction.guild_id] = cfg
    if not (is_admin(interaction.user) if admin else is_manager(interaction.user, cfg)):
        raise ValueError("This action needs a server admin." if admin else "This action needs a configured raffle manager.")
    return cfg


def attachment(name, value):
    data = value if isinstance(value, bytes) else json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode()
    if len(data) > 6*1024*1024:
        data = gzip.compress(data, mtime=0)
        name += ".gz"
    if len(data) > 6*1024*1024:
        raise ValueError("Export is too large for a safe Discord attachment. Use the server backup and local verifier.")
    return discord.File(io.BytesIO(data), filename=name)


class Reporter:
    def __init__(self, bot):
        self.bot = bot
        self.last_alert = {}

    async def error(self, guild, area, exc):
        error_id = "RCR-" + time.strftime("%y%m%d", time.gmtime()) + "-" + secrets.token_hex(3).upper()
        detail = str(exc)
        token = os.getenv("DISCORD_TOKEN", "")
        if token:
            detail = detail.replace(token, "[redacted]")
        detail = re.sub(r"[0-9a-f]{64}", "[hash redacted]", detail,flags=re.IGNORECASE)
        logging.getLogger("ripcars.raffle").error("%s %s %s: %s", error_id, area, type(exc).__name__, detail, exc_info=(RuntimeError, RuntimeError(detail), exc.__traceback__))
        if guild:
            await self.bot.db.audit(guild.id, 0, "error", f"{error_id}; {area}; {type(exc).__name__}; {detail[:500]}")
            cfg, _ = await self.bot.db.settings(guild.id)
            if time.monotonic() - self.last_alert.get(guild.id, -1e9) >= cfg["error_alert_seconds"]:
                self.last_alert[guild.id] = time.monotonic()
                await self.log(guild, "Raffle error", f"Error ID: {error_id}\nArea: {area}\nType: {type(exc).__name__}\nDetails: {detail[:1000]}\nCheck /raffle doctor and the service journal.")
        return error_id

    async def log(self, guild, title, detail):
        cfg, _ = await self.bot.db.settings(guild.id)
        ch = guild.get_channel(cfg["log_channel"])
        if not private_channel(guild, ch, cfg):
            return False
        try:
            await ch.send(embed=discord.Embed(title=title[:200], description=detail[:3500], color=cfg["color"]), allowed_mentions=discord.AllowedMentions.none())
            return True
        except discord.HTTPException:
            return False


async def backup(db, root, keep):
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    target = path / ("raffle-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-{time.time_ns()%1000000000:09d}.sqlite3")
    def write():
        with sqlite3.connect(db.path) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed.")
        os.chmod(target, 0o600)
        existing = sorted(path.glob("raffle-*.sqlite3"))
        for stale in existing[:-keep]:
            stale.unlink()
    await asyncio.to_thread(write)
    return target


async def health(bot, guild):
    now = time.time()
    cfg, _ = await bot.db.settings(guild.id)
    database = (await bot.db.query("PRAGMA integrity_check"))[0]
    integrity = next(iter(database.values()))
    states = await bot.db.query("SELECT status,COUNT(*) AS count FROM raffles WHERE guild=? GROUP BY status", (guild.id,))
    return {"version": __version__, "uptime_seconds": round(now-bot.started), "gateway_ms": round(bot.latency*1000) if bot.is_ready() else None,
            "database": integrity, "enabled": cfg["enabled"], "states": states,
            "scheduler_alive": bool(bot.scheduler_task and not bot.scheduler_task.done()),
            "last_scheduler_tick":bot.last_tick,"backup_task_alive":bool(bot.backup_task and not bot.backup_task.done()),"latest_backup":bot.backup_last,
            "jobs_running": len(bot.jobs), "coordination": "ok" if await bot.registry.query("SELECT 1") else "unavailable"}
