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


def get_network_json(url: str, api_pattern: str, wait_seconds: float = 8.0, timeout_ms: int = 30000) -> list[dict]:
    """
    Load a JS-rendered page and intercept XHR/fetch responses matching api_pattern.
    Returns parsed JSON from the first matching response that contains a list.
    
    Args:
        url: Page URL to navigate to
        api_pattern: URL substring to match (e.g. "solicitations", "opportunities")
        wait_seconds: Time to wait for background XHR calls
        timeout_ms: Navigation timeout
    """
    try:
        import json
        from playwright.sync_api import sync_playwright
        captured = []

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
                    if api_pattern.lower() in response.url.lower() and response.status == 200:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct:
                            body = response.json()
                            captured.append(body)
                except Exception:
                    pass

            page.on("response", handle_response)
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            time.sleep(wait_seconds)
            browser.close()

        # Return first response that looks like a list of items
        for body in captured:
            if isinstance(body, list) and body:
                return body
            if isinstance(body, dict):
                for key in ["data", "results", "solicitations", "opportunities", "bids", "items"]:
                    val = body.get(key)
                    if isinstance(val, list) and val:
                        return val
        return []
    except ImportError:
        raise ImportError("playwright not installed. Run: pip install playwright && playwright install chromium")
    except Exception as e:
        print(f"[playwright_helper] Network intercept error for {url}: {e}")
        return []
