"""Canonical package delivery requires separate exact archive approval."""
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from caeval import (buyer_jobs as jobs,institution_access as access,
                    evaluator_service as api,evaluator_package_release as release)
from tests_unit.test_buyer_assessment import AssessmentHandoffTests


class ReleaseTests(unittest.TestCase):
    setUp=AssessmentHandoffTests.setUp

    def test_actual_completed_job_release_and_revocation(self):
        bound=AssessmentHandoffTests.prepare(self)
        access.provision(self.root,"operator","operator","Synthetic long password")
        grant=api.issue(self.root,"operator",bound["assessment_id"],3600,confirmed=True)
        req=api.submit(self.root,grant["token"],"synthetic-request-0001")
        rid=req["request_id"]
        jid=api.approve_queue(self.root,rid,confirmed=True)["job_id"]
        job=jobs.execute(self.root,jid)
        self.assertEqual(job["status"],"completed",job)
        family=job["result"]["packages"][0]["family_id"]
        with self.assertRaises(access.AccessDenied):
            release.download(self.root,grant["token"],rid,family)
        dest=self.root.parent/"review.zip"
        meta=release.preview(self.root,rid,family,dest)
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(),meta["sha256"])
        with zipfile.ZipFile(dest) as z:
            self.assertIn("responses.jsonl",z.namelist())
            self.assertNotIn("panel.toml",z.namelist())
        with self.assertRaises(access.AccessDenied):
            release.approve(self.root,rid,family,"0"*64,confirmed=True)
        with self.assertRaises(access.AccessDenied):
            release.approve(self.root,rid,family,meta["sha256"])
        release.approve(self.root,rid,family,meta["sha256"],confirmed=True)
        raw,download_meta=release.download(self.root,grant["token"],rid,family)
        self.assertEqual(raw,dest.read_bytes())
        self.assertEqual(meta,download_meta)
        api.revoke(self.root,grant["grant_id"])
        with self.assertRaises(access.AccessDenied):
            release.download(self.root,grant["token"],rid,family)
