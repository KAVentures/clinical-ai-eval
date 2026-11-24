"""Tests for the local buyer browser. No external server or cloud calls."""
from __future__ import annotations

import http.cookiejar
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from caeval import buyer_agent as buyer
from caeval.buyer_web import BuyerHTTPServer, parse_upload, render_page


class LocalBuyerBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        buyer.init(self.root, "Example regional hospital",
                   "Evaluate clinician AI assistant", "Emergency department", "SE")
        self.app = BuyerHTTPServer(self.root, 0)
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close)
        self.base = f"http://127.0.0.1:{self.app.server_port}"
        self.client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _close(self):
        self.app.shutdown()
        self.app.server_close()
        self.thread.join(timeout=3)

    def _request(self, path, data=None, headers=None):
        return self.client.open(urllib.request.Request(
            self.base + path, data=data, headers=headers or {}), timeout=3)

    def _login(self):
        response = self.client.open(self.app.launch_url, timeout=3)
        self.assertEqual(response.status, 200)
        self.assertIn(b"Example regional hospital", response.read())

    def _form_post(self, path, fields, origin=None):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if origin is not None:
            headers["Origin"] = origin
        return self._request(path, data=urllib.parse.urlencode(fields).encode("utf-8"),
                             headers=headers)

    def test_browser_bound_to_loopback(self):
        self.assertEqual(self.app.server_address[0], "127.0.0.1")

    def test_no_access_before_login(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self._request("/")
        self.assertEqual(exc.exception.code, 401)

    def test_dashboard_after_token_login(self):
        self._login()
        with self._request("/") as response:
            html = response.read().decode("utf-8")
            self.assertIn("Vendor assessment", html)
            self.assertIn("No cloud connection", html)
            self.assertIn("Clinical evaluation", html)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_combined_dossier_download_requires_authentication(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self._request("/dossier")
        self.assertEqual(exc.exception.code, 401)
        self._login()
        with self._request("/dossier") as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertIn("text/plain", response.headers["Content-Type"])
            report = response.read().decode("utf-8")
            self.assertIn("Administrative inventory only", report)
            self.assertIn("No verified clinical comparison", report)
            self.assertNotIn(self.app.session_token, report)

    def test_browser_questionnaire_roundtrip(self):
        from caeval import buyer_questionnaire as q
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self._request("/questionnaire?vendor_id=v1")
        self.assertEqual(exc.exception.code, 401)
        self._login()
        buyer.add_vendor(self.root, "v1", "Vendor One")
        with self._request("/questionnaire?vendor_id=v1") as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            doc = json.loads(response.read())
        doc["submission"]["respondent"] = "Supplier"
        doc["submission"]["product_version"] = "v1"
        for answer in doc["submission"]["answers"]:
            answer.update(status="not_available", answer="Not supplied")
        boundary = "----questionnaire-fixture"
        form = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="vendor_id"\r\n\r\nv1\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="q.json"\r\n'
            f'Content-Type: application/json\r\n\r\n'
        ).encode("utf-8") + json.dumps(doc).encode("utf-8")
        form += f"\r\n--{boundary}--\r\n".encode("utf-8")
        headers = {"Origin": self.base, "Content-Type": f"multipart/form-data; boundary={boundary}"}
        with self._request("/questionnaire-import", data=form, headers=headers) as response:
            self.assertIn(b"self-reported and unverified", response.read())
        self.assertEqual(buyer.status(self.root)["vendors"][0]["questionnaires"][0]["schema_status"], "valid")

    def test_research_and_plan_views_require_local_session(self):
        for route in ("/research", "/plan"):
            with self.assertRaises(urllib.error.HTTPError) as exc:
                self._request(route)
            self.assertEqual(exc.exception.code, 401)
        self._login()
        with self._request("/research") as response:
            self.assertIn(b"Scientific evaluation workspace", response.read())
        with self._request("/plan") as response:
            self.assertIn(b"Plan an evaluation", response.read())

    def test_browser_scientific_plan_roundtrip(self):
        from caeval import intake, selection
        self._login()
        buyer.add_vendor(self.root, "v1", "Vendor One")
        fields = {"vendor_id": "v1", "target_name": "Synthetic assistant",
                  "target_version": "1.2", "profiles": "clinician_decision_support",
                  **{q: "Explicit synthetic answer for " + q for q in intake.INTAKE_QUESTIONS}}
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self._form_post("/plan-preview", fields, origin="https://untrusted.example")
        self.assertEqual(exc.exception.code, 403)
        self.assertFalse((self.root / "plans").exists())
        with self._form_post("/plan-preview", fields, origin=self.base) as response:
            self.assertIn(b"Saved draft scope preview", response.read())
            url = response.geturl()
        self.assertIn("/plan?id=", url)
        identifier = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["id"][0]
        with self._request("/plan?id=" + identifier + "&format=json") as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            record = json.loads(response.read())
        self.assertEqual(record["plan"]["selection"],
                         selection.select_suites(["clinician_decision_support"]))
        self.assertFalse(record["execution_authorized"])
        self.assertFalse(record["decision_grade"])

    def test_rehearsal_browser_routes_require_explicit_authenticated_post(self):
        from unittest.mock import patch
        from caeval import buyer_plan, buyer_rehearsal, intake
        self._login()
        buyer.add_vendor(self.root, "v1", "Vendor One")
        plan = buyer_plan.preview(self.root, {
            "vendor_id": "v1", "target_name": "Candidate", "target_version": "1",
            "profiles": ["clinician_decision_support"],
            "answers": {q: "Synthetic answer " + q for q in intake.INTAKE_QUESTIONS},
        })
        with self._request("/plan?id=" + plan["plan_id"]) as response:
            self.assertIn(b"Run offline software rehearsal", response.read())
        record = {"job_id": "demo-test", "source_plan_id": plan["plan_id"],
                  "status": "failed", "expected_families": [], "packages": [],
                  "reason": "fixture"}
        with patch.object(buyer_rehearsal, "run", return_value=record) as run:
            with patch.object(buyer_rehearsal, "load", return_value=record):
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    self._form_post("/rehearsal-run", {"plan_id": plan["plan_id"]},
                                    origin="https://untrusted.example")
                self.assertEqual(exc.exception.code, 403)
                run.assert_not_called()
                with self._form_post("/rehearsal-run", {"plan_id": plan["plan_id"]},
                                     origin=self.base) as response:
                    page = response.read()
                run.assert_called_once_with(self.root, plan["plan_id"])
                self.assertIn(b"No candidate was evaluated", page)
                self.assertIn(b"Status: failed", page)

    def test_reject_cross_origin_vendor_write(self):
        self._login()
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self._form_post("/vendor-add", {"vendor_id": "v1", "name": "A"},
                            origin="https://untrusted.example")
        self.assertEqual(exc.exception.code, 403)
        self.assertEqual(buyer.status(self.root)["vendors"], [])

    def test_reject_write_without_origin(self):
        self._login()
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self._form_post("/vendor-add", {"vendor_id": "v1", "name": "A"})
        self.assertEqual(exc.exception.code, 403)

    def test_register_vendor_via_browser(self):
        self._login()
        response = self._form_post("/vendor-add",
                                   {"vendor_id": "v1", "name": "Vendor One"},
                                   origin=self.base)
        self.assertEqual(response.status, 200)
        self.assertIn(b"Vendor One", response.read())
        self.assertEqual(buyer.status(self.root)["vendors"][0]["name"], "Vendor One")

    def test_upload_document_locally(self):
        self._login()
        buyer.add_vendor(self.root, "v1", "Vendor One")
        boundary = "----synthetic-boundary"
        file_bytes = b"Synthetic non-clinical document"
        form = b""
        for name, val in [("vendor_id", "v1"), ("kind", "security"),
                          ("title", "Security overview")]:
            form += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{val}\r\n").encode("utf-8")
        form += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
                 f"filename=\"security.txt\"\r\nContent-Type: text/plain\r\n\r\n").encode("utf-8")
        form += file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        with self._request("/evidence-add", data=form, headers={
            "Origin": self.base,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }) as response:
            self.assertEqual(response.status, 200)
        state = buyer.status(self.root)
        self.assertEqual(state["vendors"][0]["documents"][0]["integrity"], "intact")
        self.assertNotIn("security", state["vendors"][0]["missing_evidence_kinds"])
        saved = buyer.load(self.root)["evidence"][0]
        self.assertEqual(saved["source_filename"], "security.txt")
        self.assertEqual(saved["title"], "Security overview")
        self.assertFalse((self.root / "evidence" / saved["sha256"]).is_symlink())

    def test_html_escapes_untrusted_vendor_text(self):
        buyer.add_vendor(self.root, "v1", "<img src=x onerror=alert(1)>")
        page = render_page(buyer.status(self.root)).decode("utf-8")
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img", page)

    def test_token_not_exposed_on_dashboard(self):
        self._login()
        html = self._request("/").read().decode("utf-8")
        self.assertNotIn(self.app.session_token, html)


class FirstRunBrowserTests(unittest.TestCase):
    def test_setup_wizard_creates_private_local_workspace(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-yet-created"
            with BuyerHTTPServer(root, 0) as app:
                thread = threading.Thread(target=app.serve_forever, daemon=True)
                thread.start()
                try:
                    client = urllib.request.build_opener(
                        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                    page = client.open(app.launch_url, timeout=3).read().decode("utf-8")
                    self.assertIn("Start a healthcare AI assessment", page)
                    fields = urllib.parse.urlencode({
                        "name": "Regional AI Procurement",
                        "intended_use": "Clinician decision support",
                        "setting": "Emergency ward",
                        "jurisdiction": "SE",
                    }).encode("utf-8")
                    response = client.open(urllib.request.Request(
                        f"http://127.0.0.1:{app.server_port}/project-init",
                        data=fields,
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Origin": f"http://127.0.0.1:{app.server_port}",
                        }), timeout=3)
                    self.assertIn(b"Regional AI Procurement", response.read())
                    self.assertEqual(buyer.load(root)["jurisdiction"], "SE")
                finally:
                    app.shutdown()
                    thread.join(timeout=3)

    def test_duplicate_file_parts_rejected(self):
        boundary = "testboundary"
        blob = (
            f"--{boundary}\\r\\nContent-Disposition: form-data; "
            f"name=\\\"document\\\"; filename=\\\"a.txt\\\"\\r\\n\\r\\na"
            f"\\r\\n--{boundary}\\r\\nContent-Disposition: form-data; "
            f"name=\\\"document\\\"; filename=\\\"b.txt\\\"\\r\\n\\r\\nb"
            f"\\r\\n--{boundary}--\\r\\n"
        ).encode("utf-8")
        with self.assertRaises(buyer.BuyerError):
            parse_upload(blob, f"multipart/form-data; boundary={boundary}")


if __name__ == "__main__":
    unittest.main()
