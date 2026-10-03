import copy
import unittest

from ripcars_raffle import config


class ConfigTests(unittest.TestCase):
    def test_defaults_paused_three_groups(self):
        c=config.validate(config.fresh())
        self.assertFalse(c["enabled"]);self.assertEqual(set(c["templates"]),set(config.CATEGORIES))
    def test_independent_defaults(self):
        a=config.fresh();a["templates"]["general"]["rules"]["any_roles"].append(10)
        self.assertEqual(config.fresh()["templates"]["general"]["rules"]["any_roles"],[])
    def test_strict_top_level_shape(self):
        for key in ("unknown","DISCORD_TOKEN","seed"):
            c=config.fresh();c[key]="bad"
            with self.assertRaises(ValueError):config.validate(c)
    def test_missing_field_rejected(self):
        c=config.fresh();del c["color"]
        with self.assertRaises(ValueError):config.validate(c)
    def test_boolean_not_numeric_id(self):
        for key in ("member_role","channel","log_channel"):
            c=config.fresh();c[key]=True
            with self.assertRaises(ValueError):config.validate(c)
    def test_large_negative_and_float_ids(self):
        for v in (-1,2**63,1.5,"10"):
            c=config.fresh();c["channel"]=v
            with self.assertRaises(ValueError):config.validate(c)
    def test_duplicate_manager_ids(self):
        c=config.fresh();c["manager_roles"]=[10,10]
        with self.assertRaises(ValueError):config.validate(c)
    def test_real_boolean_required(self):
        c=config.fresh();c["enabled"]=1
        with self.assertRaises(ValueError):config.validate(c)
    def test_website_https_credentials_and_spaces(self):
        for v in ("http://app.ripcars.io","https://user:pass@app.ripcars.io","https://app.ripcars.io/ bad","javascript:alert(1)"):
            c=config.fresh();c["website"]=v
            with self.assertRaises(ValueError):config.validate(c)
    def test_target_percentages_sum(self):
        c=config.fresh();c["campaign_targets"]["general"]=11
        with self.assertRaises(ValueError):config.validate(c)
    def test_role_rules_contradictory(self):
        r=config.rules();r["any_roles"]=[10];r["blocked_roles"]=[10]
        with self.assertRaises(ValueError):config.validate_rules(r)
    def test_weights_canonical_ids_and_range(self):
        for weights in ({"010":2},{"x":2},{"10":True},{"10":101},{"0":1}):
            r=config.rules();r["weights"]=weights
            with self.assertRaises(ValueError):config.validate_rules(r)
    def test_rule_limits(self):
        for key,val in (("weight_mode","multiply"),("weight_cap",0),("account_days",-1),("claim_hours",721),("minimum_entries",0)):
            r=config.rules();r[key]=val
            with self.assertRaises(ValueError):config.validate_rules(r)
    def test_maximum_role_rules(self):
        r=config.rules();r["any_roles"]=list(range(1,21));r["weights"]={str(x):100 for x in range(1,21)}
        self.assertEqual(config.validate_rules(r),r)
    def test_overflow_role_rules(self):
        r=config.rules();r["all_roles"]=list(range(1,22))
        with self.assertRaises(ValueError):config.validate_rules(r)
    def test_public_text_bounds(self):
        for key in ("join","result_label"):
            c=config.fresh();c["texts"][key]="x"*81
            with self.assertRaises(ValueError):config.validate(c)
    def test_nested_edit_copy_and_validation(self):
        c=config.fresh();edited=config.set_path(c,"templates.general.rules.weights",{"12":3})
        self.assertEqual(c["templates"]["general"]["rules"]["weights"],{})
        self.assertEqual(edited["templates"]["general"]["rules"]["weights"],{"12":3})
    def test_unknown_edit_path(self):
        with self.assertRaises(ValueError):config.set_path(config.fresh(),"templates.general.secret",10)
    def test_duration_units(self):
        for raw,value in (("10m",600),("3d",259200),("1w",604800),("60",60),(" 24H ",86400)):
            self.assertEqual(config.duration(raw),value)
    def test_invalid_duration(self):
        for raw in ("-1h","one day","2.5h","1h 30m"):
            with self.assertRaises(ValueError):config.duration(raw)
    def test_winner_and_schedule_bounds(self):
        for key,val in (("winners",26),("duration",59),("starts_in",-1)):
            s=copy.deepcopy(config.DEFAULTS["templates"]["general"]);s[key]=val
            with self.assertRaises(ValueError):config.validate_spec(s)
    def test_finite_and_json_nan(self):
        self.assertFalse(config.finite(float("inf")));self.assertFalse(config.finite(True))
        with self.assertRaises(ValueError):config.canonical({"value":float("nan")})
