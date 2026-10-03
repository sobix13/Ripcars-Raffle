"""Replayable weighted draw, with a public seed commitment and no replacement."""
from __future__ import annotations

import hashlib
import hmac
import re

from .config import canonical, ident, validate_spec

ALGORITHM = "ripcars-weighted-hmac-sha256-v1"


def seed_bytes(seed):
    if not isinstance(seed, str) or not re.fullmatch(r"[0-9a-f]{64}", seed):
        raise ValueError("Seed must be 32 bytes as lowercase hexadecimal.")
    return bytes.fromhex(seed)


def commitment(seed):
    return hashlib.sha256(seed_bytes(seed)).hexdigest()


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def select(seed, context, pool, count):
    key = seed_bytes(seed)
    if type(count) is not int or not 0 <= count <= 25:
        raise ValueError("Winner count must be 0–25.")
    if not isinstance(pool, list) or any(not isinstance(p, dict) or set(p) != {"user", "weight"} or not ident(p["user"]) or type(p["weight"]) is not int or not 1 <= p["weight"] <= 100 for p in pool):
        raise ValueError("Invalid weighted pool.")
    if len({p["user"] for p in pool}) != len(pool):
        raise ValueError("Duplicate member in weighted pool.")
    remaining = sorted((dict(p) for p in pool), key=lambda p: p["user"])
    counter = 0
    winners = []
    for _ in range(min(count, len(remaining))):
        total = sum(p["weight"] for p in remaining)
        bound = 2**256 - (2**256 % total)
        while True:
            value = int.from_bytes(hmac.new(key, (context + ":" + str(counter)).encode(), hashlib.sha256).digest(), "big")
            counter += 1
            if value < bound:
                ticket = value % total
                break
        for index, candidate in enumerate(remaining):
            if ticket < candidate["weight"]:
                winners.append(candidate["user"])
                remaining.pop(index)
                break
            ticket -= candidate["weight"]
    return winners


def round_result(seed, context, pool, count, round_number=0):
    frozen = sorted(pool, key=lambda p: p["user"])
    pool_hash = digest(frozen)
    return {"algorithm": ALGORITHM, "round": round_number, "context": context,
            "pool": frozen, "pool_hash": pool_hash,
            "winners": select(seed, f"{context}:{round_number}:{pool_hash}", frozen, count)}


def verify(bundle):
    """Verify hashes and selection, not the honesty of live eligibility evidence."""
    try:
        if bundle["algorithm"] != ALGORITHM or commitment(bundle["seed"]) != bundle["commitment"]:
            return False
        if digest(bundle["contract"]) != bundle["contract_hash"]:
            return False
        validate_spec(bundle["contract"]["spec"])
        frozen=bundle["frozen_entries"]
        if not isinstance(frozen,list) or any(not ident(x) for x in frozen) or frozen!=sorted(set(frozen)):
            return False
        seen = set()
        replaced=set()
        base = None
        for number, result in enumerate(bundle["rounds"]):
            if result["round"] != number or result["algorithm"] != ALGORITHM or result["context"] != bundle["contract_hash"]:
                return False
            if result["pool"]!=sorted(result["pool"],key=lambda p:p["user"]):
                return False
            if digest(result["pool"]) != result["pool_hash"]:
                return False
            if base is None:
                base = {p["user"]: p["weight"] for p in result["pool"]}
                if set(base)-set(frozen):
                    return False
                spec=bundle["contract"]["spec"]
                expected_count=spec["winners"] if len(base)>=spec["rules"]["minimum_entries"] else 0
                if result["requested"]!=expected_count:
                    return False
            checks=bundle["eligibility_checks"] if number==0 else result["checks"]
            expected_checked=set(frozen) if number==0 else set(base)-seen
            if not isinstance(checks,list) or len(checks)!=len(expected_checked) or {c["user"] for c in checks}!=expected_checked:
                return False
            if any(type(c["eligible"]) is not bool or type(c["weight"]) is not int or (not c["eligible"] and c["weight"]!=0) for c in checks):
                return False
            if {c["user"]:c["weight"] for c in checks if c["eligible"]}!={p["user"]:p["weight"] for p in result["pool"]}:
                return False
            if number:
                targets=result["replaces"]
                if not isinstance(targets,list) or len(targets)!=len(set(targets)) or len(targets)!=len(result["winners"]) or not set(targets)<=seen-replaced:
                    return False
                replaced.update(targets)
            if any(p["user"] in seen or base.get(p["user"]) != p["weight"] for p in result["pool"]):
                return False
            expected = select(bundle["seed"], f"{result['context']}:{number}:{result['pool_hash']}", result["pool"], result["requested"])
            if expected != result["winners"]:
                return False
            seen.update(expected)
        return bool(bundle["rounds"])
    except (ValueError, KeyError, TypeError, OverflowError,AttributeError):
        return False
