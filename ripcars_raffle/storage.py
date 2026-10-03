from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager

from . import config, drawing


class Conflict(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(guild INTEGER PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,guild INTEGER,data TEXT,actor INTEGER,at REAL);
CREATE TABLE IF NOT EXISTS raffles(id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,actor INTEGER NOT NULL,category TEXT NOT NULL,
 spec TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'draft',channel INTEGER NOT NULL DEFAULT 0,
 message INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,starts REAL NOT NULL DEFAULT 0,ends REAL NOT NULL DEFAULT 0,
 seed TEXT,commitment TEXT,contract TEXT,contract_hash TEXT,member_role INTEGER,entry_limit INTEGER,
 frozen TEXT,rounds TEXT NOT NULL DEFAULT '[]',excluded TEXT NOT NULL DEFAULT '[]',paused INTEGER NOT NULL DEFAULT 0,
 dirty INTEGER NOT NULL DEFAULT 1,last_update REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '',claim_until REAL NOT NULL DEFAULT 0,
 display_state TEXT NOT NULL DEFAULT 'unbound',display_attempts INTEGER NOT NULL DEFAULT 0,display_retry REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS raffles_guild_state ON raffles(guild,status);
CREATE TABLE IF NOT EXISTS entries(raffle INTEGER NOT NULL,user INTEGER NOT NULL,joined REAL NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(raffle,user));
CREATE TABLE IF NOT EXISTS exclusions(raffle INTEGER,user INTEGER,reason TEXT,actor INTEGER,at REAL,PRIMARY KEY(raffle,user));
CREATE TABLE IF NOT EXISTS awards(raffle INTEGER,round INTEGER,user INTEGER,status TEXT NOT NULL,at REAL,note TEXT NOT NULL DEFAULT '',PRIMARY KEY(raffle,round,user));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,guild INTEGER,actor INTEGER,action TEXT,detail TEXT,at REAL);
CREATE TABLE IF NOT EXISTS locks(raffle INTEGER,job TEXT,token TEXT,expires REAL,PRIMARY KEY(raffle,job));
"""


class SQLite:
    def __init__(self, path):
        self.path = str(path)
        self.lock = asyncio.Lock()

    def _run(self, fn):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            value = fn(conn)
            conn.commit()
            return value
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def run(self, fn):
        async with self.lock:
            task = asyncio.create_task(asyncio.to_thread(self._run, fn))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await task
                finally:
                    raise

    async def query(self, sql, args=()):
        return await self.run(lambda c: [dict(r) for r in c.execute(sql, args).fetchall()])

    async def execute(self, sql, args=()):
        return await self.run(lambda c: c.execute(sql, args).rowcount)

    async def initialize(self, schema):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        def init():
            with sqlite3.connect(self.path) as c:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA busy_timeout=15000")
                c.executescript(schema)
        await asyncio.to_thread(init)


def decode(row):
    if row is None:
        return None
    result = dict(row)
    for k in ("spec", "contract", "frozen", "rounds", "excluded"):
        if result.get(k) is not None:
            result[k] = json.loads(result[k])
    return result


class Store(SQLite):
    async def open(self):
        await self.initialize(SCHEMA)
        os.chmod(self.path,0o600)

    async def settings(self, guild):
        rows = await self.query("SELECT * FROM settings WHERE guild=?", (guild,))
        return (json.loads(rows[0]["data"]), rows[0]["revision"]) if rows else (config.fresh(), 0)

    async def save(self, guild, cfg, revision, actor):
        config.validate(cfg)
        data = config.canonical(cfg)
        def change(c):
            old = c.execute("SELECT * FROM settings WHERE guild=?", (guild,)).fetchone()
            if (old["revision"] if old else 0) != revision:
                raise Conflict("Settings changed. Reopen the panel before saving.")
            if old:
                c.execute("INSERT INTO history(guild,data,actor,at) VALUES(?,?,?,?)", (guild, old["data"], actor, time.time()))
            c.execute("INSERT INTO settings VALUES(?,?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data,revision=excluded.revision", (guild, data, revision+1))
            c.execute("DELETE FROM history WHERE guild=? AND id NOT IN (SELECT id FROM history WHERE guild=? ORDER BY id DESC LIMIT 30)", (guild, guild))
            self._audit(c, guild, actor, "settings", f"revision={revision+1}")
        await self.run(change)

    @staticmethod
    def _audit(c, guild, actor, action, detail):
        c.execute("INSERT INTO audit(guild,actor,action,detail,at) VALUES(?,?,?,?,?)", (guild, actor, action, str(detail)[:2000], time.time()))
        c.execute("DELETE FROM audit WHERE id < (SELECT COALESCE(MAX(id),0)-10000 FROM audit)")

    async def audit(self, guild, actor, action, detail):
        await self.run(lambda c: self._audit(c, guild, actor, action, detail))

    async def create(self, guild, actor, category, spec):
        if category not in config.CATEGORIES:
            raise ValueError("Unknown campaign category.")
        config.validate_spec(spec)
        def add(c):
            rid = c.execute("INSERT INTO raffles(guild,actor,category,spec,created) VALUES(?,?,?,?,?)", (guild, actor, category, config.canonical(spec), time.time())).lastrowid
            self._audit(c, guild, actor, "draft_created", f"raffle={rid}; category={category}")
            return rid
        return await self.run(add)

    async def get(self, guild, rid):
        rows = await self.query("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid))
        if not rows:
            raise ValueError("Raffle not found in this server.")
        return decode(rows[0])

    async def edit(self, guild, rid, spec, revision, actor):
        config.validate_spec(spec)
        def update(c):
            if c.execute("UPDATE raffles SET spec=?,revision=revision+1 WHERE guild=? AND id=? AND status='draft' AND revision=?", (config.canonical(spec), guild, rid, revision)).rowcount != 1:
                raise Conflict("Draft changed or is already published. Reopen it.")
            self._audit(c, guild, actor, "draft_edited", f"raffle={rid}")
        await self.run(update)

    async def begin_publish(self, guild, rid, channel, cfg, actor, now=None):
        now = time.time() if now is None else now
        def begin(c):
            row = decode(c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone())
            if not row or row["status"] != "draft":
                raise Conflict("Only an unpublished draft can be published.")
            active = c.execute("SELECT COUNT(*) FROM raffles WHERE guild=? AND status IN ('publishing','scheduled','open','closing','review')", (guild,)).fetchone()[0]
            if active >= cfg["max_active"]:
                raise ValueError("The active raffle limit has been reached.")
            start = now + row["spec"]["starts_in"]
            end = start + row["spec"]["duration"]
            seed = secrets.token_hex(32)
            contract = {"id": rid, "guild": guild, "channel": channel, "category": row["category"], "spec": row["spec"],
                        "starts": start, "ends": end, "member_role": cfg["member_role"], "entry_limit": cfg["max_entries"],
                        "server_policy": "Current server account blocklist and raffle-manager staff exclusions apply at entry and draw."}
            c.execute("UPDATE raffles SET status='publishing',channel=?,starts=?,ends=?,seed=?,commitment=?,contract=?,contract_hash=?,member_role=?,entry_limit=?,revision=revision+1 WHERE id=?", (channel, start, end, seed, drawing.commitment(seed), config.canonical(contract), drawing.digest(contract), cfg["member_role"], cfg["max_entries"], rid))
            self._audit(c, guild, actor, "publish_started", f"raffle={rid}")
        await self.run(begin)
        return await self.get(guild, rid)

    async def bind_message(self, guild, rid, message, now=None):
        now = time.time() if now is None else now
        def bind(c):
            row = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not row or row["status"] != "publishing" or row["message"]:
                raise Conflict("This publication is already bound or needs review.")
            state = "scheduled" if row["starts"] > now else "open"
            c.execute("UPDATE raffles SET message=?,status=?,display_state='active',dirty=1,revision=revision+1 WHERE id=?", (message, state, rid))
            self._audit(c, guild, 0, "published", f"raffle={rid}; message={message}")
        await self.run(bind)

    async def enter(self, guild, rid, user, now=None):
        now = time.time() if now is None else now
        def add(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not r or r["status"] not in ("open", "scheduled") or r["paused"] or not r["starts"] <= now < r["ends"]:
                raise ValueError("Entries are not open for this raffle.")
            if c.execute("SELECT 1 FROM exclusions WHERE raffle=? AND user=?", (rid, user)).fetchone():
                raise ValueError("You were excluded from this raffle.")
            old = c.execute("SELECT active FROM entries WHERE raffle=? AND user=?", (rid, user)).fetchone()
            if old and old[0]:
                return False
            if c.execute("SELECT COUNT(*) FROM entries WHERE raffle=? AND active=1", (rid,)).fetchone()[0] >= r["entry_limit"]:
                raise ValueError("This raffle has reached its entry limit.")
            c.execute("INSERT INTO entries VALUES(?,?,?,1) ON CONFLICT(raffle,user) DO UPDATE SET joined=excluded.joined,active=1", (rid, user, now))
            c.execute("UPDATE raffles SET dirty=1,revision=revision+1 WHERE id=?", (rid,))
            return True
        return await self.run(add)

    async def leave(self, guild, rid, user, now=None):
        now = time.time() if now is None else now
        def remove(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not r or r["status"] not in ("scheduled", "open") or now >= r["ends"]:
                raise ValueError("The entry snapshot has closed.")
            changed = c.execute("UPDATE entries SET active=0 WHERE raffle=? AND user=? AND active=1", (rid, user)).rowcount
            c.execute("UPDATE raffles SET dirty=1,revision=revision+1 WHERE id=?", (rid,))
            return bool(changed)
        return await self.run(remove)

    async def freeze(self, guild, rid, actor=0, early_reason=None, now=None):
        now = time.time() if now is None else now
        def close(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not r:
                raise ValueError("Raffle not found.")
            if r["status"] == "closing":
                return
            if r["status"] not in ("open", "scheduled") or (r["ends"] > now and not early_reason):
                raise Conflict("Raffle is not ready to close.")
            if r["starts"] > now:
                raise ValueError("A scheduled raffle cannot be drawn before it opens.")
            entrants = [x[0] for x in c.execute("SELECT user FROM entries WHERE raffle=? AND active=1 ORDER BY user", (rid,))]
            c.execute("UPDATE raffles SET status='closing',frozen=?,dirty=1,revision=revision+1 WHERE id=?", (config.canonical(entrants), rid))
            self._audit(c, guild, actor, "entries_frozen", f"raffle={rid}; count={len(entrants)}; early_reason={early_reason or ''}")
        await self.run(close)
        return await self.get(guild, rid)

    async def finalize(self, guild, rid, result, checks, now=None):
        now = time.time() if now is None else now
        def finish(c):
            r = decode(c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone())
            if not r or r["status"] != "closing" or r["rounds"]:
                raise Conflict("Draw already finished or was stopped.")
            if result["context"] != r["contract_hash"] or result["round"] != 0:
                raise ValueError("Draw context mismatch.")
            if {p["user"] for p in result["pool"]} - set(r["frozen"]):
                raise ValueError("Draw pool contains an account outside the frozen entries.")
            requested=r["spec"]["winners"] if len(result["pool"])>=r["spec"]["rules"]["minimum_entries"] else 0
            expected=drawing.round_result(r["seed"],r["contract_hash"],result["pool"],requested)
            if result.get("requested")!=requested or any(result[k]!=expected[k] for k in ("pool_hash","winners","algorithm")):
                raise ValueError("Selection does not match the committed draw.")
            if len(checks)!=len(r["frozen"]) or {x["user"] for x in checks}!=set(r["frozen"]):
                raise ValueError("Eligibility evidence must cover every frozen entrant exactly once.")
            if {x["user"]:x["weight"] for x in checks if x["eligible"] is True}!={p["user"]:p["weight"] for p in result["pool"]}:
                raise ValueError("Eligible checks and final weights must match the selection pool.")
            claim_until = now + r["spec"]["rules"]["claim_hours"] * 3600
            c.execute("UPDATE raffles SET status='drawn',rounds=?,excluded=?,claim_until=?,dirty=1,error='',attempts=0,revision=revision+1 WHERE id=?", (config.canonical([result]), config.canonical(checks), claim_until, rid))
            for uid in result["winners"]:
                c.execute("INSERT INTO awards VALUES(?,?,?,'pending',?,'')", (rid, 0, uid, now))
            self._audit(c, guild, 0, "draw_completed", f"raffle={rid}; eligible={len(result['pool'])}; winners={result['winners']}")
        await self.run(finish)

    async def claim(self, guild, rid, user, now=None):
        now = time.time() if now is None else now
        def change(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not r or r["status"] != "drawn":
                raise ValueError("Winners haven't been drawn.")
            row = c.execute("SELECT * FROM awards WHERE raffle=? AND user=? ORDER BY round DESC LIMIT 1", (rid, user)).fetchone()
            if not row or row["status"] not in ("pending", "claimed", "delivered"):
                raise ValueError("You don't have an active prize in this raffle.")
            if row["status"] == "pending":
                if now > row["at"] + json.loads(r["spec"])["rules"]["claim_hours"]*3600:
                    raise ValueError("The claim deadline has passed. Contact the team.")
                c.execute("UPDATE awards SET status='claimed',note='Winner acknowledged the prize' WHERE raffle=? AND round=? AND user=?", (rid, row["round"], user))
                self._audit(c, guild, user, "prize_claimed", f"raffle={rid}; round={row['round']}")
                c.execute("UPDATE raffles SET dirty=1,revision=revision+1 WHERE id=?", (rid,))
            return row["status"]
        return await self.run(change)

    async def award_status(self, guild, rid, user, status, actor, note):
        if status not in ("delivered", "forfeited") or not note.strip():
            raise ValueError("Choose delivered/forfeited and provide a note.")
        def change(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not r or r["status"] != "drawn":
                raise ValueError("Raffle is not drawn.")
            row = c.execute("SELECT * FROM awards WHERE raffle=? AND user=? ORDER BY round DESC LIMIT 1", (rid, user)).fetchone()
            if not row or row["status"] in ("delivered", "forfeited", "replaced"):
                raise ValueError("This award is already final or does not exist.")
            c.execute("UPDATE awards SET status=?,note=? WHERE raffle=? AND user=? AND round=?", (status, note[:500], rid, user, row["round"]))
            self._audit(c, guild, actor, "prize_"+status, f"raffle={rid}; user={user}; note={note[:500]}")
            c.execute("UPDATE raffles SET dirty=1,revision=revision+1 WHERE id=?", (rid,))
        await self.run(change)

    async def add_round(self, guild, rid, result, checks, replacements, actor, reason):
        def change(c):
            r = decode(c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone())
            if not r or r["status"] != "drawn" or result["round"] != len(r["rounds"]):
                raise Conflict("Raffle rounds changed. Review before rerolling.")
            forfeited = {x[0] for x in c.execute("SELECT user FROM awards WHERE raffle=? AND status='forfeited'", (rid,))}
            if not replacements or len(replacements)!=len(set(replacements)) or not set(replacements) <= forfeited or len(result["winners"]) != len(replacements) or result["requested"]!=min(len(forfeited),25):
                raise ValueError("Only forfeited awards can be replaced.")
            prior={p["user"]:p["weight"] for p in r["rounds"][0]["pool"]}
            seen={u for rnd in r["rounds"] for u in rnd["winners"]}
            if any(p["user"] in seen or prior.get(p["user"])!=p["weight"] for p in result["pool"]):
                raise ValueError("Replacement pool changed original weights or includes a prior winner.")
            if len(checks)!=len(set(prior)-seen) or {x["user"] for x in checks}!=set(prior)-seen or {x["user"]:x["weight"] for x in checks if x["eligible"] is True}!={p["user"]:p["weight"] for p in result["pool"]}:
                raise ValueError("Replacement evidence does not match the original remaining pool.")
            expected=drawing.round_result(r["seed"],r["contract_hash"],result["pool"],result["requested"],len(r["rounds"]))
            if any(result[k]!=expected[k] for k in ("pool_hash","winners","algorithm","context")):
                raise ValueError("Replacement selection does not match the committed seed.")
            for user in replacements:
                c.execute("UPDATE awards SET status='replaced' WHERE raffle=? AND user=? AND status='forfeited'", (rid, user))
            for user in result["winners"]:
                c.execute("INSERT INTO awards VALUES(?,?,?,'pending',?,'')", (rid, result["round"], user, time.time()))
            result["replaces"] = replacements
            result["checks"] = checks
            result["reason"] = reason[:500]
            r["rounds"].append(result)
            c.execute("UPDATE raffles SET rounds=?,dirty=1,revision=revision+1 WHERE id=?", (config.canonical(r["rounds"]), rid))
            self._audit(c, guild, actor, "reroll", f"raffle={rid}; reason={reason[:500]}; winners={result['winners']}")
        await self.run(change)

    async def exclude(self, guild, rid, user, actor, reason):
        config.text(reason,500,"Exclusion reason")
        def change(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not r or r["status"] not in ("draft", "scheduled", "open"):
                raise ValueError("Exclusions are locked after entries close.")
            c.execute("INSERT INTO exclusions VALUES(?,?,?,?,?) ON CONFLICT(raffle,user) DO UPDATE SET reason=excluded.reason,actor=excluded.actor,at=excluded.at", (rid, user, reason[:500], actor, time.time()))
            self._audit(c, guild, actor, "entry_excluded", f"raffle={rid}; user={user}; reason={reason[:500]}")
        await self.run(change)

    async def state_action(self, guild, rid, actor, action, reason):
        config.text(reason,500,"Action reason")
        def change(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if not r:
                raise ValueError("Raffle not found.")
            if action == "cancel":
                if r["status"] in ("drawn", "cancelled"):
                    raise ValueError("A completed raffle cannot be cancelled.")
                c.execute("UPDATE raffles SET status='cancelled',error=?,dirty=1,revision=revision+1 WHERE id=?", (reason[:500], rid))
            elif action in ("pause", "resume"):
                if r["status"] not in ("open", "scheduled"):
                    raise ValueError("Only entry to an open/scheduled raffle can be paused.")
                c.execute("UPDATE raffles SET paused=?,dirty=1,revision=revision+1 WHERE id=?", (action == "pause", rid))
            elif action == "retry":
                if r["status"] != "review" or r["frozen"] is None or r["rounds"] != "[]":
                    raise ValueError("No failed closing job to retry.")
                c.execute("UPDATE raffles SET status='closing',attempts=0,retry_at=0,error='',dirty=1 WHERE id=?", (rid,))
            else:
                raise ValueError("Unknown state action.")
            self._audit(c, guild, actor, action, f"raffle={rid}; reason={reason[:500]}")
        await self.run(change)

    async def fail_job(self, guild, rid, error):
        def change(c):
            r = c.execute("SELECT * FROM raffles WHERE guild=? AND id=?", (guild, rid)).fetchone()
            if r and r["status"] == "closing":
                attempts = r["attempts"] + 1
                c.execute("UPDATE raffles SET status=?,attempts=?,retry_at=?,error=?,dirty=1 WHERE id=?", ("review" if attempts >= 5 else "closing", attempts, time.time()+min(60*2**(attempts-1), 3600), error[:500], rid))
        await self.run(change)

    @asynccontextmanager
    async def job(self, rid, name, seconds=120):
        token = secrets.token_hex(16)
        def acquire(c):
            old = c.execute("SELECT * FROM locks WHERE raffle=? AND job=?", (rid, name)).fetchone()
            if old and old["expires"] > time.time():
                raise Conflict("Another worker is already handling this raffle.")
            c.execute("INSERT OR REPLACE INTO locks VALUES(?,?,?,?)", (rid, name, token, time.time()+seconds))
        await self.run(acquire)
        try:
            yield token
        finally:
            await self.execute("DELETE FROM locks WHERE raffle=? AND job=? AND token=?", (rid, name, token))

    async def renew_job(self, rid, name, token, seconds=120):
        changed = await self.execute("UPDATE locks SET expires=? WHERE raffle=? AND job=? AND token=? AND expires>?", (time.time()+seconds, rid, name, token, time.time()))
        if changed != 1:
            raise Conflict("Raffle job lease expired. No further writes are safe.")

    async def public_bundle(self, guild, rid):
        r = await self.get(guild, rid)
        if r["status"] != "drawn":
            raise ValueError("The seed is revealed only after the draw completes.")
        return {"algorithm": drawing.ALGORITHM, "commitment": r["commitment"], "seed": r["seed"],
                "contract": r["contract"], "contract_hash": r["contract_hash"], "rounds": r["rounds"],
                "eligibility_checks": r["excluded"], "frozen_entries": r["frozen"]}

    async def restore(self, guild, history_id, actor):
        rows = await self.query("SELECT data FROM history WHERE guild=? AND id=?", (guild, history_id))
        if not rows:
            raise ValueError("Settings history entry not found.")
        cfg = copy.deepcopy(json.loads(rows[0]["data"]))
        cfg["enabled"] = False
        _, revision = await self.settings(guild)
        await self.save(guild, cfg, revision, actor)
        return cfg
