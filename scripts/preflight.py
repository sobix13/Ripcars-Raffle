"""Read-only local compatibility checks. No Discord API calls or token output."""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))


def check(deployment=False):
    if sys.version_info<(3,11):
        raise ValueError("Python 3.11 or newer is required.")
    import discord
    from ripcars_raffle import __version__,config
    if discord.__version__!="2.7.1":
        raise ValueError("Install requirements-lock.txt. Tested discord.py version is 2.7.1.")
    config.validate(config.fresh())
    if deployment:
        token=os.getenv("DISCORD_TOKEN","").strip()
        if not token or token.startswith(("YOUR_","REPLACE_")):
            raise ValueError("Configure a new bot token in the private environment file.")
        gid=os.getenv("GUILD_ID","").strip()
        if gid and (not gid.isdecimal() or not config.ident(int(gid))):
            raise ValueError("GUILD_ID must be empty or a numeric server ID.")
        paths=[Path(os.getenv(k,d)) for k,d in (("DB_PATH","data/raffle.sqlite3"),("COORDINATION_PATH","data/coordination.sqlite3"))]
        if paths[0].resolve()==paths[1].resolve():
            raise ValueError("Private raffle and shared coordination database paths must differ.")
        for p in paths:
            if not p.parent.is_dir() or not os.access(p.parent,os.W_OK):
                raise ValueError(f"Runtime needs write access to the parent of {p.name}.")
    return {"version":__version__,"python":sys.version.split()[0],"discord.py":discord.__version__,"mode":"deployment" if deployment else "offline"}


if __name__=="__main__":
    try:
        print("Preflight:",check("--deployment" in sys.argv))
    except (ValueError,ImportError) as exc:
        raise SystemExit(str(exc)) from None
