"""
Run: .venv/bin/python3.13 debug_contact.py
Logs in, opens the first search result, screenshots it, then clicks the contact
button (if found) and screenshots the form — prints all data-testid values at
each stage so we can update the selectors in scraper.py.
"""
import asyncio
from pathlib import Path
from src.utils.config import load_config
from src.immowelt.scraper import ImmoweltScraper, IMMOWELT_BASE
from playwright.async_api import async_playwright

PROFILE_DIR = Path("browser_profile")


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
        scraper = ImmoweltScraper(page, config)

        print("Logging in…")
        ok = await scraper.login()
        print(f"Login: {'OK' if ok else 'FAILED'}")
        if not ok:
            await ctx.close()
            return

        # Grab first listing from search
        listings = await scraper.search_listings()
        if not listings:
            print("No listings found.")
            await ctx.close()
            return

        listing = listings[0]
        print(f"\nOpening: {listing.url}")
        await page.goto(listing.url, wait_until="domcontentloaded")
        await scraper._delay(2000, 3000)
        await page.screenshot(path="/tmp/iw_listing.png")
        print("Screenshot: /tmp/iw_listing.png")

        # Dump all testids on the listing page
        print("\n--- data-testid on listing page ---")
        els = await page.locator("[data-testid]").all()
        seen = set()
        for el in els:
            try:
                tid = await el.get_attribute("data-testid")
                vis = await el.is_visible()
                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                if tid and "shell" not in tid and tid not in seen:
                    seen.add(tid)
                    print(f"  {tag:10s}  visible={str(vis):5s}  {tid}")
            except Exception:
                pass

        # Try clicking contact button
        print("\n--- trying contact button ---")
        CONTACT_CANDIDATES = [
            '[data-testid="contact-button"]',
            '[data-testid*="contact"]',
            '[data-testid*="Contact"]',
            '[data-testid*="anfrage"]',
            '[data-testid*="Anfrage"]',
            'button:has-text("Kontaktieren")',
            'button:has-text("Anfrage")',
            'a:has-text("Kontaktieren")',
            'a:has-text("Anfrage senden")',
        ]
        clicked = False
        for sel in CONTACT_CANDIDATES:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    txt = await el.inner_text()
                    print(f"  FOUND & clicking: {sel!r}  text={txt!r}")
                    await el.click()
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            print("  No contact button found with any selector.")
        else:
            await scraper._delay(1500, 2500)
            await page.screenshot(path="/tmp/iw_contact_form.png")
            print("Screenshot after click: /tmp/iw_contact_form.png")

            print("\n--- data-testid after contact click ---")
            els = await page.locator("[data-testid]").all()
            seen2 = set()
            for el in els:
                try:
                    tid = await el.get_attribute("data-testid")
                    vis = await el.is_visible()
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    if tid and "shell" not in tid and tid not in seen2:
                        seen2.add(tid)
                        print(f"  {tag:10s}  visible={str(vis):5s}  {tid}")
                except Exception:
                    pass

            # Dump all textarea / input fields
            print("\n--- form fields after contact click ---")
            for sel in ["textarea", "input[type='text']", "input[type='email']"]:
                fields = await page.locator(sel).all()
                for f in fields:
                    try:
                        name = await f.get_attribute("name") or ""
                        ph = await f.get_attribute("placeholder") or ""
                        tid = await f.get_attribute("data-testid") or ""
                        vis = await f.is_visible()
                        print(f"  {sel:25s}  name={name!r:20s}  placeholder={ph!r:35s}  testid={tid!r}  visible={vis}")
                    except Exception:
                        pass

        await ctx.close()


asyncio.run(main())
