"""Real mock workflow: prospectively frozen cohort, review roster and replay."""
import unittest
from caeval import (validation_cohort as c,buyer_manual as manual,buyer_assessment as assessment,
                    buyer_jobs as jobs,institution_access as access,buyer_plan as plans)
from tests_unit import test_buyer_manual as fixture

class CohortTests(unittest.TestCase):
    setUp=fixture.ManualExchangeTests.setUp
    prepare_exchange=fixture.ManualExchangeTests.prepare
    fill=fixture.ManualExchangeTests.fill
    submit_exchange=fixture.ManualExchangeTests.submit

    def test_prospective_roster_source_and_full_inventory(self):
        for uid,role in (("admin","admin"),("a","reviewer"),("b","reviewer"),("v","reviewer"),("other","reviewer")):
            access.provision(self.root,uid,role,"Synthetic long password")
        admin={"user_id":"admin","role":"admin"}
        family=self.plan["plan"]["selection"]["runnable_suites"][0]
        args=(self.root,admin,self.plan["plan_id"],"fixture",family,["a","b"],"v")
        p=c.preview(*args)
        with self.assertRaises(access.AccessDenied):c.freeze(*args,p["digest"])
        ident=c.freeze(*args,p["digest"],confirmed=True)
        self.assertEqual(ident,c.freeze(*args,p["digest"],confirmed=True))
        exchange=self.prepare_exchange()
        self.submit_exchange(self.fill(exchange))
        # Another family cannot be newly frozen after local exchange creation.
        other=next(x for x in self.plan["plan"]["selection"]["runnable_suites"] if x!=family)
        later=(self.root,admin,self.plan["plan_id"],"fixture",other,["a","b"],"v")
        p2=c.preview(*later)
        with self.assertRaises(access.AccessDenied):c.freeze(*later,p2["digest"],confirmed=True)
        with self.assertRaises(access.AccessDenied):c.analyze(self.root,admin,ident)
        self.assertEqual(c.inventory(self.root,{"user_id":"other","role":"reviewer"}),[])
        self.assertEqual(c.inventory(self.root,{"user_id":"a","role":"reviewer"}),[{"id":ident,"linked":False}])
        with access.db(self.root) as conn:conn.execute("DELETE FROM validation_cohorts")
        with self.assertRaises(access.AccessDenied):c.freeze(*args,p["digest"],confirmed=True)
