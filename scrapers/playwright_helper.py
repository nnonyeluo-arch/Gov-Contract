"""
Shared Playwright helper for JS-rendered procurement portals.
Used by houston.py and fort_worth.py.

Requires: playwright (pip install playwright && playwright install chromium)
In GitHub Actions, add these steps before running scrapers:
  - run: pip install playwright
  - run: playwright install chromium --with-deps
"""

import time


def get_page_html(url: str, wait_selector: str = None, wait_seconds: float = 5.0, timeout_ms: int = 30000) -> str:
    """
    Launch headless Chromium, navigate to url, wait for content, return full page HTML.
    
    Args:
        url: Page to load
        wait_selector: CSS selector to wait for before extracting HTML (e.g. ".bid-card", "table tr")
        wait_seconds: Additional seconds to wait after selector appears (for JS rendering)
        timeout_ms: Max ms to wait for selector
    
    Returns:
        Page HTML as string, or "" on failure
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except Exception:
                    pass  # selector may not exist — still grab whatever loaded
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            html = page.content()
            browser.close()
            return html
    except ImportError:
        raise ImportError("playwright not installed. Run: pip install playwright && playwright install chromium")
    except Exception as e:
        print(f"[playwright_helper] Error loading {url}: {e}")
        return ""


def get_network_json(url: str, api_pattern: str = "", wait_seconds: float = 10.0, timeout_ms: int = 30000) -> list[dict]:
    """
    Load a JS-rendered page and capture ALL JSON API responses.
    If api_pattern is provided, only captures responses whose URL contains it.
    If api_pattern is empty, captures every JSON response that looks like a list of items.
    Also tries to extract embedded JSON from <script> tags (initial state hydration).
    """
    try:
        import json
        import re
        from playwright.sync_api import sync_playwright
        captured = []
        captured_urls = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            def handle_response(response):
                try:
                    if response.status != 200:
                        return
                    if api_pattern and api_pattern.lower() not in response.url.lower():
                        return
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        body = response.json()
                        captured.append(body)
                        captured_urls.append(response.url)
                except Exception:
                    pass

            page.on("response", handle_response)
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            time.sleep(wait_seconds)

            # Also try extracting embedded JSON from script tags
            try:
                html = page.content()
                # Look for window.__INITIAL_STATE__, window.__data__, etc.
                for pattern in [
                    r"window\.__(?:INITIAL_STATE|data|state|redux|store)__\s*=\s*(\{.*?\});",
                    r""solicitations"\s*:\s*(\[.*?\])",
                    r""opportunities"\s*:\s*(\[.*?\])",
                    r""bids"\s*:\s*(\[.*?\])",
                ]:
                    match = re.search(pattern, html, re.DOTALL)
                    if match:
                        try:
                            extracted = json.loads(match.group(1))
                            if isinstance(extracted, list) and len(extracted) > 0:
                                print(f"[playwright_helper] Found {len(extracted)} items in embedded script JSON")
                                browser.close()
                                return extracted
                            elif isinstance(extracted, dict):
                                for key in ["solicitations", "opportunities", "bids", "data", "results", "items"]:
                                    val = extracted.get(key)
                                    if isinstance(val, list) and val:
                                        browser.close()
                                        return val
                        except Exception:
                            pass
            except Exception:
                pass

            browser.close()

        if captured_urls:
            print(f"[playwright_helper] Captured {len(captured)} JSON responses from: {[u[:80] for u in captured_urls[:5]]}")

        # Return first response that looks like a list of items
        for body in captured:
            if isinstance(body, list) and len(body) > 0:
                return body
            if isinstance(body, dict):
                for key in ["data", "results", "solicitations", "opportunities", "bids", "items", "records"]:
                    val = body.get(key)
                    if isinstance(val, list) and val:
                        return val
        return []
    except ImportError:
        raise ImportError("playwright not installed. Run: pip install playwright && playwright install chromium")
    except Exception as e:
        print(f"[playwright_helper] Network intercept error for {url}: {e}")
        return []
