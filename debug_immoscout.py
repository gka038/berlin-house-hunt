"""
Run: .venv/bin/python3.13 debug_immoscout.py

Logs in, opens the search page, dumps all data-testid values found on cards,
then opens the first listing, dumps its testids, clicks the contact button,
and dumps all form fields — so we can update selectors in immoscout_scraper.py.
"""
import asyncio
from pathlib import Path
from src.config import load_config
from src.immoscout_scraper import ImmoscoutScraper, IMMOSC_BASE
from playwright.async_api import async_playwright

PROFILE_DIR = Path("browser_profile_immoscout")


async def dump_testids(page, label: str) -> None:
    print(f"\n--- data-testid on {label} ---")
    els = await page.locator("[data-testid]").all()
    seen = set()
    for el in els:
        try:
            tid = await el.get_attribute("data-testid")
            vis = await el.is_visible()
            tag = await el.evaluate("e => e.tagName.toLowerCase()")
            if tid and tid not in seen:
                seen.add(tid)
                print(f"  {tag:12s}  visible={str(vis):5s}  {tid}")
        except Exception:
            pass


async def main():
    config = load_config()
    PROFILE_DIR.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        ctx = await pw.firefox.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            slow_mo=80,
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
            timezone_id="Europe/Berlin",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        scraper = ImmoscoutScraper(page, config)

        print("Logging in…")
        ok = await scraper.login()
        print(f"Login: {'OK' if ok else 'FAILED'}")
        if not ok:
            await ctx.close()
            return

        # Open search page
        search_url = scraper._build_search_url()
        print(f"\nSearch URL: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded")
        await scraper._delay(2000, 3000)
        await page.screenshot(path="/tmp/is24_search.png")
        print("Screenshot: /tmp/is24_search.png")

        await dump_testids(page, "search page")

        # Dump first 3 result card structures
        print("\n--- first card HTML snippet ---")
        try:
            card = page.locator('[data-testid="result-list-entry"]').first
            if await card.count():
                snippet = await card.evaluate("e => e.outerHTML.slice(0, 2000)")
                print(snippet)
            else:
                print("  No cards found with [data-testid='result-list-entry']")
                print("  Trying article and li elements...")
                for sel in ["article", "li[id]", "[data-serp-id]", "[class*='result']"]:
                    count = await page.locator(sel).count()
                    if count:
                        print(f"  Found {count} elements matching {sel!r}")
                        snippet = await page.locator(sel).first.evaluate("e => e.outerHTML.slice(0, 1000)")
                        print(snippet[:500])
                        break
        except Exception as e:
            print(f"  Error: {e}")

        # Get listings and open first one
        listings = await scraper.search_listings()
        if not listings:
            print("\nNo listings found — check card selectors in immoscout_scraper.py.")
            await ctx.close()
            return

        # Test the known listing first, then fall back to first search result
        test_url = "https://www.immobilienscout24.de/expose/168864412"
        contact_url = f"{test_url}#/basicContact/email"
        print(f"\nOpening contact form directly: {contact_url}")
        await page.goto(contact_url, wait_until="domcontentloaded")
        await scraper._delay(3000, 4000)
        await page.screenshot(path="/tmp/is24_contact.png")
        print("Screenshot: /tmp/is24_contact.png")

        await dump_testids(page, "page after #/basicContact/email")

        print("\n--- form fields ---")
        for sel in ["textarea", "input[type='text']", "input[type='email']", "select", "input[name]"]:
            fields = await page.locator(sel).all()
            for f in fields:
                try:
                    name = await f.get_attribute("name") or ""
                    ph = await f.get_attribute("placeholder") or ""
                    tid = await f.get_attribute("data-testid") or ""
                    vis = await f.is_visible()
                    if vis:
                        print(f"  {sel:25s}  name={name!r:20s}  placeholder={ph!r:35s}  testid={tid!r}")
                except Exception:
                    pass

        await ctx.close()


asyncio.run(main())
