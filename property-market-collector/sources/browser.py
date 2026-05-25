"""Shared Playwright browser singleton for headless rendering."""
import asyncio

from playwright.async_api import Browser, Playwright, async_playwright

_pw: Playwright | None = None
_browser: Browser | None = None
_lock = asyncio.Lock()

_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]

_CONTEXT_OPTS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "locale": "es-AR",
    "extra_http_headers": {
        "Accept-Language": "es-AR,es;q=0.9",
    },
}


async def get_browser() -> Browser:
    global _pw, _browser
    async with _lock:
        if _browser is None or not _browser.is_connected():
            if _pw is None:
                _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
    return _browser


async def fetch_rendered(url: str, wait_selector: str | None = None, timeout: int = 30_000) -> str:
    """Fetch a URL using a headless browser and return the rendered HTML."""
    browser = await get_browser()
    ctx = await browser.new_context(**_CONTEXT_OPTS)
    try:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if wait_selector:
            try:
                await page.wait_for_selector(wait_selector, timeout=8_000)
            except Exception:
                pass
        html = await page.content()
    finally:
        await ctx.close()
    return html


async def close():
    global _pw, _browser
    if _browser:
        await _browser.close()
        _browser = None
    if _pw:
        await _pw.stop()
        _pw = None
