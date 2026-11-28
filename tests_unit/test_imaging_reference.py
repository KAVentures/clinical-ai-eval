"""Synthetic independent reference protocol and institutional lifecycle tests."""
import copy
import json
import unittest
from caeval import (imaging_reference as ref, diagnostic_imaging as imaging,
                    institution_imaging_reference as service, institution_imaging as studies,
                    institution_access as access, institution_committee as committee)
from tests_unit import test_institution_research as browser
from tests_unit.test_diagnostic_imaging import spec as original_spec


def spec():
    s=original_spec()
    s.pop("reference_method")
    s["attestations"].pop("independent_reference")
    for c in s["cases"]:
        c.pop("reference")
    return s


def readings(packet,label):
    return {c["slot"]:label for c in packet["cases"]}


class ReferenceTests(unittest.TestCase):
    def test_agreement_and_unresolved(self):
        p=ref.protocol(spec(),["reader-a","reader-b"],"reader-c")
        packet=ref.reader_packet(p,"reader-a")
        self.assertNotIn("candidates",packet)
        self.assertNotIn("private_nonce",packet)
        self.assertTrue(all(set(c)=={"slot","asset_sha256"} for c in packet["cases"]))
        doc=ref.ratings(packet,readings(packet,"unresolved"))
        docs={"reader-a":doc,"reader-b":doc}
        self.assertEqual(ref.adjudication_packet(p,docs,"reader-c")["cases"],[])
        final,summary=ref.resolve(p,docs,None)
        self.assertEqual(summary["n_unresolved"],len(packet["cases"]))
        self.assertEqual(summary["n_primary_agreement"],len(packet["cases"]))
        plan=imaging.freeze(final,confirmed=True)
        ev=ref.package(p,docs,None,plan)
        self.assertEqual(ref.verify(ev,ev["digest"])["final_plan_digest"],plan["digest"])
        bad=copy.deepcopy(ev)
        bad["summary"]["n_unresolved"]=0
        with self.assertRaises(ValueError):
            ref.verify(bad)
        with self.assertRaises(ValueError):
            ref.verify(ev,"0"*64)

    def test_disagreement_only_and_full_inventory(self):
        p=ref.protocol(spec(),["reader-a","reader-b"],"reader-c")
        packet=ref.reader_packet(p,"reader-a")
        a=readings(packet,"positive")
        b=dict(a)
        slot=next(iter(b))
        b[slot]="negative"
        docs={"reader-a":ref.ratings(packet,a),"reader-b":ref.ratings(packet,b)}
        ap=ref.adjudication_packet(p,docs,"reader-c")
        self.assertEqual(len(ap["cases"]),1)
        self.assertEqual(ap["cases"][0]["slot"],slot)
        self.assertEqual(ap["cases"][0]["prior_labels"],["negative","positive"])
        with self.assertRaises(ValueError):
            ref.resolve(p,docs,None)
        with self.assertRaises(ValueError):
            ref.ratings(ap,{})
        adj=ref.ratings(ap,{slot:"unresolved"})
        final,summary=ref.resolve(p,docs,adj)
        self.assertEqual(summary["n_disagreement"],1)
        self.assertEqual(summary["n_unresolved"],1)
        self.assertEqual(summary["n_positive"],len(a)-1)
        plan=imaging.freeze(final,confirmed=True)
        final["cases"][0]["reference"]="negative"
        with self.assertRaises(ValueError):
            ref.package(p,docs,adj,imaging.freeze(final,confirmed=True))
        self.assertTrue(ref.verify(ref.package(p,docs,adj,plan))["status"]=="REPLAY_VERIFIED")

    def test_invalid_roster_and_prelabelled_cases(self):
        for readers,judge in [(["a","a"],"b"),(["a","b"],"b")]:
            with self.assertRaises(ValueError):
                ref.protocol(spec(),readers,judge)
        s=spec()
        s["cases"][0]["reference"]="positive"
        with self.assertRaises(ValueError):
            ref.protocol(s,["a","b"],"c")


class ReferenceBrowserTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def reserve(self):
        p=service.preview(self.root,self.actors["admin"],spec(),["doctor-a","doctor-b"],"doctor-c")
        return service.freeze(self.root,self.actors["admin"],spec(),["doctor-a","doctor-b"],"doctor-c",
                              p["approval_digest"],confirmed=True)

    def lock(self,uid,ident,label):
        p=service.packet(self.root,self.actors[uid],ident)
        fields={"id":ident,"packet":imaging._digest(p),"confirm":"yes",
                **{"r_"+slot:v for slot,v in readings(p,label).items()}}
        self.assertEqual(self.post(uid,"/imaging-reference-lock",fields)["status"],"303 See Other")
        return p,fields

    def test_prospective_browser_review_to_candidate_study(self):
        fields={"spec":json.dumps(spec()),"reader_a":"doctor-a","reader_b":"doctor-b","adjudicator":"doctor-c"}
        self.assertEqual(self.request("admin","/imaging-reference-preview","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/imaging-reference-preview",fields)["status"],"200 OK")
        ident=self.reserve()
        imported=original_spec()
        with self.assertRaises(access.AccessDenied):
            studies.preview(self.root,self.actors["admin"],imported)
        self.assertEqual(self.request("operator","/diagnostic-imaging-study?id="+ident)["status"],"403 Forbidden")
        review="/imaging-reference-review?id="+ident
        for uid in ("doctor-d","doctor-c","operator","admin"):
            self.assertEqual(self.request(uid,review)["status"],"403 Forbidden")
        self.assertEqual(service.overview(self.root,self.actors["doctor-d"]),[])
        body=self.request("doctor-a",review)
        self.assertEqual(body["status"],"200 OK")
        self.assertNotIn(b"prior labels",body["body"].lower())
        _,locked=self.lock("doctor-a",ident,"positive")
        self.assertEqual(self.post("doctor-a","/imaging-reference-lock",locked)["status"],"403 Forbidden")
        self.assertEqual(self.request("doctor-c",review)["status"],"403 Forbidden")
        self.lock("doctor-b",ident,"negative")
        self.assertEqual(self.request("doctor-c",review)["status"],"200 OK")
        self.lock("doctor-c",ident,"unresolved")
        p=service.final_preview(self.root,self.actors["admin"],ident)
        self.assertEqual(p["summary"]["n_unresolved"],len(spec()["cases"]))
        self.assertEqual(self.post("admin","/imaging-reference-finalize",{"id":ident,"approval":"bad","confirm":"yes"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/imaging-reference-finalize",{"id":ident,"approval":p["approval_digest"],"confirm":"yes"})["status"],"303 See Other")
        row=studies.detail(self.root,self.actors["operator"],ident)
        self.assertEqual(row["state"],"collecting")
        ex=studies.download(self.root,self.actors["operator"],ident,"a","exchange")
        self.assertTrue(all(set(c)=={"slot","asset_sha256"} for c in ex["cases"]))
        self.assertEqual(len(ex["cases"]),len(spec()["cases"]))
        r=service.overview(self.root,self.actors["admin"])[0]
        ef={"id":ident,"digest":r["reference_digest"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/imaging-reference-evidence",ef)["status"],"403 Forbidden")
        out=self.post("admin","/imaging-reference-evidence",ef)
        self.assertEqual(out["status"],"200 OK")
        pkg=json.loads(out["body"])
        self.assertEqual(ref.verify(pkg)["final_plan_digest"],ex["plan_digest"])
        self.assertEqual(self.request("doctor-a",review)["status"],"403 Forbidden")
        cov=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["diagnostic_imaging_reference_reviews"]
        self.assertEqual(cov[0]["state"],"finalized")
        self.assertNotIn("primary_ratings",cov[0])
        self.assertNotIn("summary",cov[0])

    def test_agreement_finalizes_without_third_review_and_tampering_blocks_candidate(self):
        ident=self.reserve()
        self.lock("doctor-a",ident,"negative")
        self.lock("doctor-b",ident,"negative")
        self.assertEqual(self.request("doctor-c","/imaging-reference-review?id="+ident)["status"],"403 Forbidden")
        p=service.final_preview(self.root,self.actors["admin"],ident)
        service.finalize(self.root,self.actors["admin"],ident,p["approval_digest"],confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM imaging_reference_ratings WHERE actor='doctor-a'")
        with self.assertRaises(access.AccessDenied):
            studies.download(self.root,self.actors["operator"],ident,"a","exchange")

    def test_wrong_review_digest_and_role_rejected(self):
        ident=self.reserve()
        p=service.packet(self.root,self.actors["doctor-a"],ident)
        with self.assertRaises(access.AccessDenied):
            service.lock(self.root,self.actors["doctor-a"],ident,"0"*64,readings(p,"positive"),confirmed=True)
        with self.assertRaises(access.AccessDenied):
            service.lock(self.root,self.actors["doctor-d"],ident,imaging._digest(p),readings(p,"positive"),confirmed=True)
        with self.assertRaises(ValueError):
            service.lock(self.root,self.actors["doctor-a"],ident,imaging._digest(p),{},confirmed=True)
        access.disable(self.root,"doctor-a")
        with self.assertRaises(access.AccessDenied):
            service.packet(self.root,self.actors["doctor-a"],ident)


if __name__=="__main__":
    unittest.main()
