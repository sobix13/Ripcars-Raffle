"""Shared registry reads; only our own message keys are written."""
from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager

from .config import canonical
from .storage import SQLite, Conflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources(guild INTEGER NOT NULL,key TEXT NOT NULL,object_id INTEGER NOT NULL,kind TEXT NOT NULL,owner TEXT NOT NULL,baseline TEXT NOT NULL,desired TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(guild,key));
CREATE TABLE IF NOT EXISTS locks(guild INTEGER NOT NULL,name TEXT NOT NULL,token TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(guild,name));
"""


class Registry(SQLite):
    async def open(self):
        await self.initialize(SCHEMA)

    async def resources(self, guild):
        return {r["key"]: r for r in await self.query("SELECT * FROM resources WHERE guild=?", (guild,))}

    @asynccontextmanager
    async def lease(self, guild):
        token = secrets.token_hex(16)
        def acquire(c):
            r = c.execute("SELECT * FROM locks WHERE guild=? AND name='server-setup'", (guild,)).fetchone()
            if r and r["expires"] > time.time():
                raise Conflict("Gate or Crew is updating this server. Retry after that setup finishes.")
            c.execute("INSERT OR REPLACE INTO locks VALUES(?,'server-setup',?,?)", (guild, token, time.time()+120))
        await self.run(acquire)
        try:
            yield token
        finally:
            await self.execute("DELETE FROM locks WHERE guild=? AND name='server-setup' AND token=?", (guild, token))

    async def record(self, guild, rid, message, bot_id, contract_hash):
        async with self.lease(guild):
            key = f"raffle:message:{bot_id}:{rid}"
            owner = f"bot:{bot_id}"
            baseline = canonical({"raffle": rid, "contract_hash": contract_hash})
            def put(c):
                old = c.execute("SELECT * FROM resources WHERE guild=? AND key=?", (guild, key)).fetchone()
                if old and (old["owner"] != owner or old["object_id"] != message or old["state"]!="active" or old["baseline"]!=baseline or old["desired"]!=baseline):
                    raise Conflict("A foreign registry binding was preserved.")
                c.execute("INSERT INTO resources VALUES(?,?,?,'message',?,?,?,'active') ON CONFLICT(guild,key) DO UPDATE SET baseline=excluded.baseline,desired=excluded.desired", (guild, key, message, owner, baseline, baseline))
            await self.run(put)

    async def gate_ids(self, guild):
        r = await self.resources(guild)
        out = {}
        for key, field in (("role:rippers", "member_role"), ("channel:gate_log", "log_channel")):
            row = r.get(key)
            if row and row["state"] in ("active", "pinned", "external"):
                out[field] = row["object_id"]
        return out

    async def self_claim_roles(self, guild):
        return {r["object_id"] for k, r in (await self.resources(guild)).items() if k.startswith("role:claim_")}
