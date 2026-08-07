"""Discover TravelDoctor's public chat transport using a synthetic browser probe.

This is intentionally a transport probe, not a clinical evaluation. It records
request/response SHAPES (URLs, methods, JSON key names, content types and status
codes), never cookies, authorization headers, request values or response text.
The only submitted content is a fixed synthetic connectivity prompt.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

TARGET_URL = os.environ.get("TRAVELDOCTOR_URL", "https://traveldoctor.ai")
OUT = Path(os.environ.get("TRAVELDOCTOR_PROBE_OUT", "out/traveldoctor_transport_probe"))
SYNTHETIC_PROMPT = (
    "Synthetic connectivity test: I have had a headache since this morning. "
    "Please ask exactly one follow-up question before giving any advice."
)

SENSITIVE_KEY = re.compile(
    r"authorization|cookie|token|secret|password|api[_-]?key|session", re.I
)


def _safe_keys(value):
    if isinstance(value, dict):
        return sorted(k for k in value if not SENSITIVE_KEY.search(str(k)))
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    return {"type": type(value).__name__}


def _request_shape(request):
    parsed = urlparse(request.url)
    item = {
        "method": request.method,
        "url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        "resource_type": request.resource_type,
    }
    ctype = request.headers.get("content-type", "")
    if ctype:
        item["content_type"] = ctype.split(";", 1)[0]
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = request.post_data_json
            item["request_json_shape"] = _safe_keys(body)
        except Exception:  # noqa: BLE001 - non-JSON or streaming body
            item["request_body_kind"] = "non_json_or_unavailable"
    return item


def _response_shape(response):
    parsed = urlparse(response.url)
    item = {
        "status": response.status,
        "url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
    }
    ctype = response.headers.get("content-type", "")
    if ctype:
        item["content_type"] = ctype.split(";", 1)[0]
    if "application/json" in ctype:
        try:
            item["response_json_shape"] = _safe_keys(response.json())
        except Exception:  # noqa: BLE001
            item["response_json_shape"] = "unreadable"
    return item


def _find_prompt_box(page):
    selectors = [
        'textarea[placeholder*="concern" i]',
        'input[placeholder*="concern" i]',
        'textarea[placeholder*="message" i]',
        'input[placeholder*="message" i]',
        'textarea',
    ]
    for selector in selectors:
        loc = page.locator(selector)
        if loc.count() and loc.first.is_visible():
            return loc.first
    return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    requests, responses, errors = [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-US",
        )
        page = context.new_page()

        def on_request(req):
            if req.method in {"POST", "PUT", "PATCH"} or req.resource_type in {
                "fetch", "xhr", "websocket"
            }:
                requests.append(_request_shape(req))

        def on_response(resp):
            if resp.request.method in {"POST", "PUT", "PATCH"} or resp.request.resource_type in {
                "fetch", "xhr"
            }:
                responses.append(_response_shape(resp))

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: errors.append(f"console:{msg.type}: {msg.text}")
                    if msg.type == "error" else None)

        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)

            box = _find_prompt_box(page)
            if box is None:
                start = page.get_by_role("button", name=re.compile("start.*consult", re.I))
                if start.count():
                    start.first.click()
                    page.wait_for_timeout(1_500)
                    box = _find_prompt_box(page)

            if box is None:
                raise RuntimeError("No visible chat input or textarea was found")

            box.fill(SYNTHETIC_PROMPT)
            try:
                box.press("Enter")
            except Exception:  # noqa: BLE001
                pass

            page.wait_for_timeout(1_000)
            # Fallback for UIs where Enter inserts a newline rather than submitting.
            if not any(r["method"] == "POST" for r in requests):
                for pattern in ("send", "submit", "start", "continue"):
                    button = page.get_by_role("button", name=re.compile(pattern, re.I))
                    if button.count() and button.first.is_visible():
                        try:
                            button.first.click()
                            break
                        except Exception:  # noqa: BLE001
                            continue

            deadline = time.time() + 45
            while time.time() < deadline:
                page.wait_for_timeout(1_000)
                if any(r["method"] == "POST" for r in requests):
                    # Leave time for streamed/follow-up responses to settle.
                    page.wait_for_timeout(8_000)
                    break

        except (PlaywrightTimeoutError, Exception) as exc:  # noqa: BLE001
            errors.append(f"probe_error: {type(exc).__name__}: {exc}")
        finally:
            page.screenshot(path=str(OUT / "page.png"), full_page=True)
            (OUT / "page_text.txt").write_text(page.locator("body").inner_text()[:20_000])
            context.close()
            browser.close()

    # Deduplicate identical network shapes while preserving discovery order.
    def dedupe(items):
        seen, out = set(), []
        for item in items:
            key = json.dumps(item, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    report = {
        "target": TARGET_URL,
        "synthetic_probe_only": True,
        "submitted_prompt_is_fixed_and_non_patient": True,
        "requests": dedupe(requests),
        "responses": dedupe(responses),
        "errors": errors,
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))

    if not any(r["method"] == "POST" for r in report["requests"]):
        raise SystemExit("No POST transport was observed; inspect the uploaded screenshot and page text")


if __name__ == "__main__":
    main()
