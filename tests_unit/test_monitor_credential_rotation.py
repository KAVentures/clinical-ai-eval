import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from caeval import monitor_credential_rotation as rotation, monitor_alert_worker as worker


@unittest.skipUnless(os.name=='posix','POSIX rotation only')
class RotationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        for name,char in [('check-token','a'),('next-check-token','b'),('relay-token','c')]:
            self.write(name,char)

    def write(self,name,char):
        p=self.root/name
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:f.write(char*64+'\n')

    def validate(self,token):
        if token!='b'*64:raise ValueError('invalid staged grant')
        return {'schema':'monitor-polling-credential-status/1','scope':'check','source_authentication_checked':True,
                'expires':1234,'monitor_id':'m','plan_digest':'d'*64}

    def preview(self):return rotation.preview(self.root,'e'*64,self.validate)

    def rotate(self,approval,confirmed=True):
        return rotation.rotate(self.root,'e'*64,approval,self.validate,confirmed=confirmed)

    def test_atomic_rotation_and_redacted_result(self):
        doc=self.preview(); result=self.rotate(doc['approval_digest'])
        self.assertTrue(result['credential_replaced']);self.assertFalse(result['old_grant_revoked'])
        self.assertFalse(result['live_http_checked'])
        self.assertEqual(worker._secret(self.root/'check-token'),'b'*64)
        self.assertEqual(worker._secret(self.root/'next-check-token'),'b'*64)
        self.assertEqual((self.root/'check-token').stat().st_mode & 0o777,0o600)
        for token in ('a'*64,'b'*64,'c'*64): self.assertNotIn(token,json.dumps(result))
        with self.assertRaises(ValueError): self.rotate(doc['approval_digest'])

    def test_stale_approval_and_invalid_grant_do_not_replace(self):
        doc=self.preview(); self.write('check-token','f')
        with self.assertRaises(ValueError): self.rotate(doc['approval_digest'])
        self.write('next-check-token','d')
        with self.assertRaises(ValueError): self.preview()
        self.assertEqual(worker._secret(self.root/'check-token'),'f'*64)

    def test_approval_required_and_relay_token_reuse_refused(self):
        doc=self.preview()
        with self.assertRaises(ValueError):self.rotate(doc['approval_digest'],False)
        self.write('next-check-token','c')
        with self.assertRaises(ValueError):self.preview()
        self.assertEqual(worker._secret(self.root/'check-token'),'a'*64)

    def test_replace_failure_leaves_old_and_cleans_temp(self):
        doc=self.preview()
        with patch.object(rotation.os,'replace',side_effect=OSError('simulated')):
            with self.assertRaises(OSError):self.rotate(doc['approval_digest'])
        self.assertEqual(worker._secret(self.root/'check-token'),'a'*64)
        self.assertEqual(list(self.root.glob('.check-token-*')),[])

    def test_revalidation_before_replace(self):
        doc=self.preview()
        with patch.object(self,'validate',side_effect=[self.validate('b'*64),ValueError('expired')]):
            with self.assertRaises(ValueError): self.rotate(doc['approval_digest'])
        self.assertEqual(worker._secret(self.root/'check-token'),'a'*64)

    def test_lock_and_concurrent_rotation(self):
        doc=self.preview()
        def run(_):
            try:self.rotate(doc['approval_digest']);return True
            except (ValueError,OSError):return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(run,range(2))),1)

    def test_linked_staging_file_rejected(self):
        (self.root/'next-check-token').unlink()
        (self.root/'next-check-token').symlink_to(self.root/'check-token')
        with self.assertRaises(ValueError):self.preview()


if __name__=='__main__':unittest.main()
