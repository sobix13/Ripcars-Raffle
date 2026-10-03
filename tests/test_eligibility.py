from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import unittest

import discord

from ripcars_raffle import config,eligibility


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,3,tzinfo=timezone.utc)
        self.rule=config.rules()
    def member(self,roles=(10,),**extra):
        defaults={"id":1000,"bot":False,"roles":[SimpleNamespace(id=r) for r in roles],"guild_permissions":discord.Permissions.none(),
                  "created_at":self.now-timedelta(days=100),"joined_at":self.now-timedelta(days=30)}
        defaults.update(extra)
        return SimpleNamespace(**defaults)
    def check(self,m=None,blocked=(),managers=()):
        return eligibility.check(m or self.member(),self.rule,10,blocked,self.now.timestamp(),managers)
    def test_default_member(self):self.assertEqual(self.check(),(True,"Eligible",1))
    def test_bot_rejected(self):self.assertFalse(self.check(self.member(bot=True))[0])
    def test_member_role_required(self):self.assertFalse(self.check(self.member(roles=(11,)))[0])
    def test_blocked_user(self):self.assertFalse(self.check(blocked=[1000])[0])
    def test_any_roles(self):
        self.rule["any_roles"]=[11,12]
        self.assertFalse(self.check()[0]);self.assertTrue(self.check(self.member((10,12)))[0])
    def test_all_roles(self):
        self.rule["all_roles"]=[11,12]
        self.assertFalse(self.check(self.member((10,11)))[0]);self.assertTrue(self.check(self.member((10,11,12)))[0])
    def test_blocked_role_overrides_qualifying(self):
        self.rule.update(any_roles=[11],blocked_roles=[12])
        self.assertFalse(self.check(self.member((10,11,12)))[0])
    def test_account_age_boundary(self):
        self.rule["account_days"]=100
        self.assertTrue(self.check()[0]);self.assertFalse(self.check(self.member(created_at=self.now-timedelta(days=99)))[0])
    def test_server_age_boundary(self):
        self.rule["server_days"]=30
        self.assertTrue(self.check()[0]);self.assertFalse(self.check(self.member(joined_at=self.now-timedelta(days=29)))[0])
    def test_missing_join_date_only_blocks_age_rule(self):
        self.assertTrue(self.check(self.member(joined_at=None))[0]);self.rule["server_days"]=1
        self.assertFalse(self.check(self.member(joined_at=None))[0])
    def test_max_weight_does_not_stack(self):
        self.rule["weights"]={"11":4,"12":8}
        self.assertEqual(self.check(self.member((10,11,12)))[2],8)
    def test_sum_weight_has_one_base_ticket(self):
        self.rule.update(weights={"11":4,"12":8},weight_mode="sum")
        self.assertEqual(self.check(self.member((10,11,12)))[2],11)
    def test_weight_cap(self):
        self.rule.update(weights={"11":100,"12":100},weight_mode="sum",weight_cap=20)
        self.assertEqual(self.check(self.member((10,11,12)))[2],20)
    def test_unowned_weight_does_not_apply(self):
        self.rule["weights"]={"11":20};self.assertEqual(self.check()[2],1)
    def test_administrator_excluded(self):
        self.assertFalse(self.check(self.member(guild_permissions=discord.Permissions(administrator=True)))[0])
    def test_moderator_excluded(self):
        self.assertFalse(self.check(self.member(guild_permissions=discord.Permissions(manage_messages=True)))[0])
    def test_explicit_manager_role_excluded(self):
        self.assertFalse(self.check(self.member((10,30)),managers=[30])[0])
    def test_staff_opt_in_is_explicit(self):
        self.rule["exclude_staff"]=False
        self.assertTrue(self.check(self.member(guild_permissions=discord.Permissions(administrator=True)))[0])
