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
