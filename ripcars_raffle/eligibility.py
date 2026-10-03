from __future__ import annotations

import time


def check(member, rule, member_role, blocked_users=(), now=None, manager_roles=()):
    now = time.time() if now is None else now
    if member.bot:
        return False, "Bot accounts are not eligible.", 0
    if member.id in blocked_users:
        return False, "This account is excluded by the server's raffle policy.", 0
    role_ids = {r.id for r in member.roles}
    if member_role not in role_ids:
        return False, "Complete Gate entry and get Rippers first.", 0
    if rule["exclude_staff"] and (member.guild_permissions.administrator or member.guild_permissions.manage_guild or member.guild_permissions.manage_messages or role_ids & set(manager_roles)):
        return False, "Staff accounts are excluded from this raffle.", 0
    if role_ids & set(rule["blocked_roles"]):
        return False, "You hold a role excluded from this raffle.", 0
    if rule["any_roles"] and not role_ids & set(rule["any_roles"]):
        return False, "You need at least one of the listed qualifying roles.", 0
    if not set(rule["all_roles"]) <= role_ids:
        return False, "You need all of the listed required roles.", 0
    if now - member.created_at.timestamp() < rule["account_days"] * 86400:
        return False, "Your Discord account is too new for this raffle.", 0
    if rule["server_days"] and (member.joined_at is None or now - member.joined_at.timestamp() < rule["server_days"] * 86400):
        return False, "You haven't been in the server long enough for this raffle.", 0
    weights = [v for k, v in rule["weights"].items() if int(k) in role_ids]
    weight = max([1, *weights]) if rule["weight_mode"] == "max" else 1 + sum(v - 1 for v in weights)
    return True, "Eligible", min(weight, rule["weight_cap"])
