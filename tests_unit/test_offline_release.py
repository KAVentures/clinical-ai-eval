"""Offline release bundle validation; real installation is tested by wheel CI."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import offline_release as release


class OfflineReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.house=self.base/"wheelhouse"
        self.house.mkdir()
        self.app=self.wheel("clinical_ai_eval","0.22.1")
        self.dep=self.wheel("fixture_dependency","1.0")
        self.commit="a"*40

    def wheel(self,name,version,extra=None):
        path=self.house/(name+"-"+version+"-py3-none-any.whl")
        with zipfile.ZipFile(path,"w") as z:
            z.writestr(name+"-"+version+".dist-info/METADATA",
                       "Metadata-Version: 2.1\nName: "+name+"\nVersion: "+version+"\n")
            for key,value in (extra or {}).items():
                z.writestr(key,value)
        return path

    def prepare(self):
        return release.prepare(self.house,"0.22.1",self.commit)["release_id"]

    def test_approved_bundle_and_exact_inventory(self):
        ident=self.prepare()
        obj=release.verify(self.house,ident)
        self.assertEqual(obj["source_commit"],self.commit)
        self.assertEqual(len(obj["wheels"]),2)
        with self.assertRaises(release.ReleaseError):
            release.verify(self.house,"0"*64)
        with self.assertRaises(release.ReleaseError):
            self.prepare()
        self.wheel("surprise","1.0")
        with self.assertRaises(release.ReleaseError):
            release.verify(self.house,ident)

    def test_content_tampering_and_self_rehashed_manifest(self):
        ident=self.prepare()
        with self.app.open("ab") as f:
            f.write(b"changed")
        with self.assertRaises(release.ReleaseError):
            release.verify(self.house,ident)
        path=self.house/release.MANIFEST
        obj=json.loads(path.read_text())
        obj["wheels"][self.app.name].update(release._copy_hash(self.app))
        obj["release_id"]=release.digest({k:v for k,v in obj.items() if k!="release_id"})
        path.write_text(json.dumps(obj))
        with self.assertRaises(release.ReleaseError):
            release.verify(self.house,ident)

    def test_wrong_platform_and_unsafe_members(self):
        ident=self.prepare()
        with patch.object(release,"target",return_value={"platform":"different"}):
            with self.assertRaises(release.ReleaseError):
                release.verify(self.house,ident)
        (self.house/release.MANIFEST).unlink()
        self.wheel("bad","1.0",{"../../escape":"bad"})
        with self.assertRaises(release.ReleaseError):
            self.prepare()

    def test_duplicate_distributions_and_metadata_mismatch(self):
        self.wheel("fixture_dependency","2.0")
        with self.assertRaises(release.ReleaseError):
            self.prepare()
        (self.house/"fixture_dependency-2.0-py3-none-any.whl").unlink()
        self.dep.rename(self.house/"other_name-1.0-py3-none-any.whl")
        with self.assertRaises(release.ReleaseError):
            self.prepare()

    def test_install_approval_and_no_overwrite(self):
        ident=self.prepare()
        dest=self.base/"installed"
        with self.assertRaises(release.ReleaseError):
            release.install(self.house,ident,dest)
        self.assertFalse(dest.exists())
        dest.mkdir()
        marker=dest/"existing"
        marker.write_text("keep")
        with self.assertRaises(release.ReleaseError):
            release.install(self.house,ident,dest,confirmed=True)
        self.assertEqual(marker.read_text(),"keep")
        with self.assertRaises(release.ReleaseError):
            release.install(self.house,ident,self.house/"nested",confirmed=True)

    def test_failed_install_preserves_existing_release(self):
        ident=self.prepare()
        previous=self.base/"previous"
        previous.mkdir()
        (previous/"installed-release.json").write_text("prior")
        dest=self.base/"new"
        with patch.object(release.venv.EnvBuilder,"create",side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                release.install(self.house,ident,dest,confirmed=True)
        self.assertFalse(dest.exists())
        self.assertEqual((previous/"installed-release.json").read_text(),"prior")

    def test_install_only_staged_local_wheels_and_no_service_activation(self):
        ident=self.prepare()
        dest=self.base/"new"
        with patch.object(release.venv.EnvBuilder,"create"),patch.object(release,"_run",return_value=b"") as run:
            receipt=release.install(self.house,ident,dest,confirmed=True)
        install=run.call_args_list[0].args[0]
        self.assertIn("--no-index",install)
        self.assertIn("--no-deps",install)
        self.assertIn("--only-binary=:all:",install)
        staged=[p for p in install if isinstance(p,Path) and p.suffix==".whl"]
        self.assertEqual(len(staged),2)
        self.assertTrue(all(p.parent!=self.house for p in staged))
        self.assertFalse(receipt["service_activated"])
        self.assertFalse(receipt["production_approved"])
        self.assertEqual(json.loads((dest/"installed-release.json").read_text()),receipt)

    def test_environment_does_not_inherit_network_or_data_overrides(self):
        with patch.dict(release.os.environ,{"PIP_INDEX_URL":"https://bad.example",
                                          "PYTHONPATH":"/untrusted","CAEVAL_HOME":"/untrusted",
                                          "OPENAI_API_KEY":"secret"}):
            env=release._env()
        self.assertEqual(env["PIP_NO_INDEX"],"1")
        self.assertEqual(env["PIP_CONFIG_FILE"],release.os.devnull)
        for key in ("PIP_INDEX_URL","PYTHONPATH","CAEVAL_HOME","OPENAI_API_KEY"):
            self.assertNotIn(key,env)


if __name__=="__main__":
    unittest.main()
