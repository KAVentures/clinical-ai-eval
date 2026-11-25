"""Synthetic image contract regression tests."""
import base64
import hashlib
import json
import struct
import unittest
import zlib
from caeval import image_text_readiness as image,buyer_agent as buyer

TARGET={"name":"Fixture","version":"1"}


class ImageTextTests(unittest.TestCase):
    def test_fixture_and_replay(self):
        p=image.build(TARGET)
        self.assertEqual(p,image.build(TARGET))
        self.assertEqual(len(p["rows"]),12)
        exchange=image.exchange(p)
        self.assertNotIn("expected_value",json.dumps(exchange))
        self.assertNotIn("target_change",json.dumps(exchange))
        for row in p["rows"]:
            raw=base64.b64decode(row["input"]["image"]["base64"],validate=True)
            self.assertEqual(hashlib.sha256(raw).hexdigest(),row["input"]["image"]["sha256"])
            self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
            off=8
            chunks={}
            while off<len(raw):
                n=struct.unpack(">I",raw[off:off+4])[0]
                kind,payload=raw[off+4:off+8],raw[off+8:off+8+n]
                self.assertEqual(zlib.crc32(kind+payload)&0xffffffff,
                                 struct.unpack(">I",raw[off+8+n:off+12+n])[0])
                chunks[kind]=payload
                off+=12+n
            self.assertEqual(len(zlib.decompress(chunks[b"IDAT"])),96*49)
            image.validate_request(image.request(p,row["slot"]))
        responses=image.template(p)
        for row,expected in zip(responses["responses"],p["rows"]):
            row.update(status=expected["expected_status"],value=expected["expected_value"])
        pkg=image.package(p,responses)
        self.assertEqual(image.verify(pkg)["summary"],{"planned":12,"exact_match":12,"mismatch":0,"unavailable":0})
        responses["responses"][0].update(status="complete",value="999")
        responses["responses"][1].update(status="unavailable",value=None)
        self.assertEqual(image.package(p,responses)["report"]["summary"],{"planned":12,"exact_match":10,"mismatch":1,"unavailable":1})
        self.assertEqual(image.assess(p,image.template(p))["summary"]["unavailable"],12)

    def test_arbitrary_input_and_mismatched_reply_refused(self):
        p=image.build(TARGET)
        req=image.request(p,p["rows"][0]["slot"])
        with self.assertRaises(buyer.BuyerError):
            image.response(req,target=TARGET,status="complete",value="72")
        reply=image.response(req,target=TARGET,status="complete",value="072")
        with self.assertRaises(buyer.BuyerError):
            image.validate_response(req,{**reply,"request_id":"0"*32})
        req["input"]["image"]["base64"]="AA=="
        with self.assertRaises(buyer.BuyerError):
            image.validate_request(req)
