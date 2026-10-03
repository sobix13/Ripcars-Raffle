"""Usage: python scripts/verify_draw.py raffle-123-audit.json[.gz]."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from ripcars_raffle.drawing import verify


def verify_file(path):
    p=Path(path)
    if p.stat().st_size>64*1024*1024:
        raise ValueError("Proof input exceeds the 64 MiB verifier limit.")
    opener=gzip.open if p.suffix==".gz" else open
    with opener(p,"rb") as handle:
        raw=handle.read(128*1024*1024+1)
    if len(raw)>128*1024*1024:
        raise ValueError("Decompressed proof exceeds 128 MiB.")
    return verify(json.loads(raw))


if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("Usage: python scripts/verify_draw.py raffle-audit.json[.gz]")
    try:
        valid=verify_file(sys.argv[1])
    except (OSError,ValueError) as exc:
        raise SystemExit(f"Invalid proof file: {exc}") from None
    print("Selection proof: VALID" if valid else "Selection proof: INVALID")
    print("This verifies recorded inputs and selection, not live wallet holdings or operator honesty.")
    raise SystemExit(0 if valid else 1)
