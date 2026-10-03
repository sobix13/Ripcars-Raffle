import copy
import hashlib
import unittest

from ripcars_raffle import drawing,config


class DrawingTests(unittest.TestCase):
    seed="11"*32
    pool=[{"user":10,"weight":1},{"user":20,"weight":4},{"user":30,"weight":2}]
    def test_commitment_exact(self):
        self.assertEqual(drawing.commitment(self.seed),hashlib.sha256(bytes.fromhex(self.seed)).hexdigest())
    def test_seed_shape(self):
        for seed in ("1","AA"*32,"zz"*32,11):
            with self.assertRaises(ValueError):drawing.commitment(seed)
    def test_deterministic_and_order_independent(self):
        self.assertEqual(drawing.select(self.seed,"contract",self.pool,2),drawing.select(self.seed,"contract",list(reversed(self.pool)),2))
    def test_unique_and_exhausted_pool(self):
        result=drawing.select(self.seed,"contract",self.pool,25)
        self.assertEqual(len(result),3);self.assertEqual(set(result),{10,20,30})
    def test_zero_winners(self):
        self.assertEqual(drawing.select(self.seed,"contract",self.pool,0),[])
    def test_empty_pool(self):
        self.assertEqual(drawing.select(self.seed,"contract",[],1),[])
    def test_duplicate_users_rejected(self):
        with self.assertRaises(ValueError):drawing.select(self.seed,"x",self.pool+[self.pool[0]],1)
    def test_bad_weights_and_ids_rejected(self):
        for p in ({"user":10,"weight":0},{"user":10,"weight":True},{"user":False,"weight":1},{"user":10,"weight":101}):
            with self.assertRaises(ValueError):drawing.select(self.seed,"x",[p],1)
    def test_invalid_winner_count(self):
        for count in (-1,26,True):
            with self.assertRaises(ValueError):drawing.select(self.seed,"x",self.pool,count)
    def test_weighted_distribution_reproducible_sample(self):
        pool=[{"user":10,"weight":1},{"user":20,"weight":4}]
        heavy=sum(drawing.select(f"{n:064x}","test",pool,1)==[20] for n in range(1000))
        self.assertGreater(heavy,740);self.assertLess(heavy,860)
    def test_contract_canonical_order(self):
        self.assertEqual(drawing.digest({"a":1,"b":2}),drawing.digest({"b":2,"a":1}))
    def bundle(self):
        contract={"spec":config.fresh()["templates"]["general"]}
        context=drawing.digest(contract)
        result=drawing.round_result(self.seed,context,self.pool,1);result["requested"]=1
        checks=[{"user":p["user"],"eligible":True,"weight":p["weight"]} for p in self.pool]
        return {"algorithm":drawing.ALGORITHM,"seed":self.seed,"commitment":drawing.commitment(self.seed),"contract":contract,"contract_hash":context,"rounds":[result],"frozen_entries":[10,20,30],"eligibility_checks":checks}
    def test_valid_proof(self):
        self.assertTrue(drawing.verify(self.bundle()))
    def test_tampered_seed(self):
        b=self.bundle();b["seed"]="22"*32
        self.assertFalse(drawing.verify(b))
    def test_tampered_contract(self):
        b=self.bundle();b["contract"]["spec"]["prize"]="Other prize"
        self.assertFalse(drawing.verify(b))
    def test_tampered_winner(self):
        b=self.bundle();b["rounds"][0]["winners"]=[999]
        self.assertFalse(drawing.verify(b))
    def test_tampered_requested_count(self):
        b=self.bundle();b["rounds"][0]["requested"]=2
        self.assertFalse(drawing.verify(b))
    def test_foreign_pool_user(self):
        b=self.bundle();b["frozen_entries"]=[10]
        self.assertFalse(drawing.verify(b))
    def test_replacement_round_keeps_original_weights(self):
        b=self.bundle();seen=set(b["rounds"][0]["winners"])
        pool=[p for p in self.pool if p["user"] not in seen]
        result=drawing.round_result(self.seed,b["contract_hash"],pool,1,1);result["requested"]=1
        result["checks"]=[{"user":p["user"],"eligible":True,"weight":p["weight"]} for p in pool]
        result["replaces"]=list(seen)
        b["rounds"].append(result)
        self.assertTrue(drawing.verify(b))
        b2=copy.deepcopy(b);b2["rounds"][1]["pool"][0]["weight"]+=1
        self.assertFalse(drawing.verify(b2))
    def test_tampered_eligible_evidence(self):
        b=self.bundle();b["eligibility_checks"][0]["eligible"]=False
        self.assertFalse(drawing.verify(b))
    def test_missing_eligible_evidence(self):
        b=self.bundle();b.pop("eligibility_checks")
        self.assertFalse(drawing.verify(b))
    def test_reordered_pool_cannot_create_another_valid_draw(self):
        b=self.bundle();r=b["rounds"][0];r["pool"].reverse();r["pool_hash"]=drawing.digest(r["pool"])
        r["winners"]=drawing.select(b["seed"],f"{r['context']}:0:{r['pool_hash']}",r["pool"],1)
        self.assertFalse(drawing.verify(b))
