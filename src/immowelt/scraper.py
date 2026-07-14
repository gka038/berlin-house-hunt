import json
import random
import re
from typing import Optional

import anthropic
from playwright.async_api import Page

from ..utils.browser import BaseScraper
from ..utils.config import AppConfig
from ..utils.models import Listing
from ..utils.parsing import requires_wbs, parse_german_number, parse_facts

IMMOWELT_BASE = "https://www.immowelt.de"
# Do NOT navigate here directly — Auth0 requires the OAuth flow to be initiated
# from the Immowelt homepage. Direct navigation causes the "session not found" error.
_SIGNIN_DOMAIN = "signin.immowelt.de"

# NOTE: Immowelt's DOM structure changes over time.
# If selectors stop working, inspect the live site and update these constants.
CARD_SELECTOR = '[data-testid^="classified-card-mfe-"]'
CARD_LINK = '[data-testid="card-mfe-covering-link-testid"]'
CARD_TITLE = '[data-testid="cardmfe-description-box-text-test-id"]'
CARD_PRICE = '[data-testid="cardmfe-price-testid"]'
CARD_FACTS = '[data-testid="cardmfe-keyfacts-testid"]'
CARD_ADDRESS = '[data-testid="cardmfe-description-box-address"]'
PAGINATION_NEXT = '[data-testid="serp-pagination-testid"] a[aria-label*="nächste"], [data-testid="serp-pagination-testid"] a[aria-label*="next"], [data-testid="serp-pagination-testid"] [rel="next"]'
DESCRIPTION_SELECTOR = '[data-testid="cardmfe-description-text-test-id"]'
# The form lives permanently on the listing page — no modal.
# "Kontaktieren" just scrolls to it.
CONTACT_SCROLL_BTN = (
    '[data-testid="aviv.CDP.Contacting.ProviderSection.ContactCard.ContactButton.email"]'
)
CONTACT_FORM = '[data-testid="cdp-contact-form"]'
CONTACT_SUBMIT = '[data-testid="cdp-contact-form-submit.email"]'


class ImmoweltScraper(BaseScraper):
    def __init__(self, page: Page, config: AppConfig):
        super().__init__(page, config)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def login(self) -> bool:
        # Start from homepage so Immowelt initiates the OAuth flow with proper
        # client_id/state/nonce before handing off to Auth0.
        await self.page.goto(IMMOWELT_BASE, wait_until="domcontentloaded")
        await self._delay()
        await self._accept_cookies()

        # If the persistent profile has a live session, the header shows a user
        # avatar / profile link instead of the "Anmelden" button.  Detect that
        # and skip the full OAuth dance.
        already_logged_in_selectors = [
            '[data-testid="header-profile-button"]',
            '[data-testid="header-user-menu"]',
            '[data-testid*="profile"]',
            'a[href*="/mein-immowelt/profil"]',
        ]
        for sel in already_logged_in_selectors:
            try:
                if await self.page.locator(sel).first.is_visible(timeout=2000):
                    return True
            except Exception:
                pass

        login_btn_selectors = [
            '[data-testid="login-button"]',
            '[data-testid="header-login-button"]',
            'a[href*="signin.immowelt.de"]',
            'button:has-text("Anmelden")',
            'a:has-text("Anmelden")',
        ]
        if not await self._click_first(login_btn_selectors):
            await self.page.goto(f"{IMMOWELT_BASE}/mein-immowelt", wait_until="domcontentloaded")

        try:
            await self.page.wait_for_url(f"**/{_SIGNIN_DOMAIN}/**", timeout=15000)
        except Exception:
            return _SIGNIN_DOMAIN not in self.page.url and "immowelt.de" in self.page.url

        await self._delay()
        await self._accept_cookies()

        # Auth0 shows a social-login chooser first — click the email option to
        # reveal the username/password form.
        await self._click_first([
            'a:has-text("Mit E-Mail-Adresse anmelden")',
            'button:has-text("Mit E-Mail-Adresse anmelden")',
            '[data-action="sign-in-with-email"]',
        ])
        await self._delay(600, 1000)

        username_field = self.page.locator('input[name="username"]')
        try:
            await username_field.wait_for(state="visible", timeout=10000)
        except Exception:
            return False

        await self._human_type(username_field, self.config.email)
        await self._delay(1000, 5000)

        password_field = self.page.locator('input[name="password"]')
        await password_field.wait_for(state="visible", timeout=10000)
        await password_field.click()
        await self._delay(500, 1500)
        await self._human_type(password_field, self.config.password)
        await self._delay(1000, 5000)

        await self._click_first([
            'button[type="submit"][name="action"]',
            'form button[type="submit"]',
            'button[type="submit"]',
        ])
        await self._delay(2000, 3000)

        return _SIGNIN_DOMAIN not in self.page.url and "error" not in self.page.url

    async def warmup(self) -> None:
        """Browse a couple of pages naturally before hitting the search API.
        A session that reads articles before searching looks human; one that
        jumps straight to search with perfect filter params does not."""
        warmup_urls = [
            f"{IMMOWELT_BASE}/ratgeber",
            f"{IMMOWELT_BASE}/ratgeber/mieten",
        ]
        for url in warmup_urls:
            await self.page.goto(url, wait_until="domcontentloaded")
            scroll = random.randint(300, 800)
            await self.page.evaluate(f"window.scrollBy(0, {scroll})")
            await self._delay(3000, 7000)

    async def search_listings(self) -> list[Listing]:
        url = self._build_search_url()
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._delay(2000, 4000)
        await self.page.evaluate("window.scrollBy(0, 600)")
        await self._delay(1500, 2500)

        listings: list[Listing] = []
        page_num = 0

        while page_num < 5:
            page_listings = await self._extract_page_listings()
            for lst in page_listings:
                if self._matches_filters(lst):
                    listings.append(lst)
                else:
                    reason = "WBS" if lst.wbs_required else f"{lst.rooms} Zi / {lst.size}"
                    print(f"[filter] Skipped: {lst.address} — {reason}")

            next_btn = self.page.locator(PAGINATION_NEXT)
            try:
                visible = await next_btn.is_visible(timeout=3000)
            except Exception:
                visible = False
            if not visible:
                break

            await next_btn.click()
            await self.page.wait_for_load_state("domcontentloaded")
            await self._delay()
            page_num += 1

        return listings

    async def fetch_details(self, listing: Listing) -> Listing:
        # Random pause before each listing — visiting them back-to-back at
        # fixed intervals is a strong bot signal.
        await self._delay(2000, 6000)
        await self.page.goto(listing.url, wait_until="domcontentloaded")
        await self._delay(1500, 3000)
        await self.page.evaluate(f"window.scrollBy(0, {random.randint(200, 500)})")
        await self._delay(1000, 2500)
        try:
            desc = await self.page.locator(DESCRIPTION_SELECTOR).inner_text(timeout=5000)
            listing.description = desc.strip()
            if requires_wbs(listing.description):
                listing.wbs_required = True
        except Exception:
            pass
        return listing

    async def apply(self, listing: Listing, message: str) -> bool:
        await self.page.goto(listing.url, wait_until="domcontentloaded")
        await self._delay(2000, 5000)

        await self._click_first([CONTACT_SCROLL_BTN, 'button:has-text("Kontaktieren")'])
        await self._delay(1500, 2500)

        # Scope all interactions to the modal dialog so we don't confuse
        # fields in the popup with any background form on the page.
        container = self.page.locator('[role="dialog"]').first
        try:
            await container.wait_for(state="visible", timeout=6000)
        except Exception:
            container = self.page.locator(CONTACT_FORM).first

        msg_field = container.locator('textarea[name="message"]').first
        try:
            await msg_field.wait_for(state="visible", timeout=5000)
        except Exception:
            return False
        await msg_field.fill("")
        await msg_field.fill(message)
        await self._delay(300, 600)

        u = self.config.user
        parts = u.name.split(" ", 1)
        for field_name, value in [
            ("firstName", parts[0]),
            ("lastName", parts[1] if len(parts) > 1 else ""),
            ("email", self.config.email),
        ]:
            try:
                field = container.locator(f'input[name="{field_name}"]').first
                if await field.is_visible(timeout=2000):
                    if not await field.input_value():
                        await self._human_type(field, value)
                        await self._delay(500, 2000)
            except Exception:
                pass

        await self._fill_extra_fields(container)
        await self._delay(1000, 3000)

        submit = container.locator(CONTACT_SUBMIT)
        if not await submit.count():
            submit = self.page.locator(CONTACT_SUBMIT)
        try:
            await submit.wait_for(state="visible", timeout=5000)
            await submit.click()
            await self._delay(2000, 3000)
            listing.applied = True
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _matches_filters(self, listing: Listing) -> bool:
        f = self.config.filters

        if listing.wbs_required:
            return False

        if listing.rent and listing.rent != "Preis auf Anfrage":
            rent_val = parse_german_number(listing.rent)
            if rent_val is not None and rent_val > f.max_rent:
                return False

        if listing.size:
            size_val = parse_german_number(listing.size)
            if size_val is not None and size_val < f.min_size:
                return False

        if listing.rooms:
            rooms_val = parse_german_number(listing.rooms)
            if rooms_val is not None and rooms_val < f.min_rooms:
                return False

        return True

    async def _fill_extra_fields(self, container) -> None:
        """Discover any non-standard visible fields in the form, ask Claude what
        to fill, and fill them. This handles per-listing variation automatically."""
        known_names = {"message", "firstName", "lastName", "email"}

        candidates: list[tuple] = []

        all_els = await container.locator(
            'input:not([type="hidden"]):not([type="submit"])'
            ':not([type="button"]):not([type="checkbox"]):not([type="radio"]),'
            "select, textarea"
        ).all()

        for i, el in enumerate(all_els):
            try:
                if not await el.is_visible(timeout=1000):
                    continue
                name = (await el.get_attribute("name")) or ""
                if name in known_names:
                    continue
                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                el_type = (await el.get_attribute("type")) or "text"
                testid = (await el.get_attribute("data-testid")) or ""
                placeholder = (await el.get_attribute("placeholder")) or ""
                label = await el.evaluate("""e => {
                    if (e.id) {
                        const l = document.querySelector('label[for="' + e.id + '"]');
                        if (l) return l.innerText.trim();
                    }
                    const p = e.closest('label');
                    if (p) {
                        const clone = p.cloneNode(true);
                        clone.querySelectorAll('input,select,textarea').forEach(n => n.remove());
                        return clone.innerText.trim();
                    }
                    let sib = e.previousElementSibling;
                    while (sib) {
                        if (['LABEL','SPAN','P','DIV'].includes(sib.tagName) && sib.innerText.trim())
                            return sib.innerText.trim();
                        sib = sib.previousElementSibling;
                    }
                    return '';
                }""")

                field_id = name or testid or f"field_{i}"
                info: dict = {
                    "id": field_id,
                    "tag": tag,
                    "type": el_type,
                    "label": label,
                    "placeholder": placeholder,
                }
                if tag == "select":
                    info["options"] = await el.evaluate(
                        "e => Array.from(e.options).map(o => o.text.trim()).filter(Boolean)"
                    )
                candidates.append((el, info))
            except Exception:
                pass

        if not candidates:
            return

        values = self._ask_claude_for_field_values([info for _, info in candidates])

        for el, info in candidates:
            value = values.get(info["id"])
            if not value:
                continue
            try:
                if info["tag"] == "select":
                    try:
                        await el.select_option(label=value)
                    except Exception:
                        await el.select_option(value=value)
                else:
                    await el.fill(str(value))
                await self._delay(300, 700)
                print(f"[form] Filled {info['id']!r} = {value!r}")
            except Exception as e:
                print(f"[warn] Could not fill {info['id']!r}: {e}")

    def _ask_claude_for_field_values(self, fields: list[dict]) -> dict[str, str]:
        u = self.config.user
        fields_desc = "\n".join(
            "  - id={id!r} label={label!r} placeholder={placeholder!r} type={type!r}".format(**f)
            + (f" options={f['options']}" if "options" in f else "")
            for f in fields
        )
        current_address = " ".join(filter(None, [u.street, u.zip_code, u.city])) or "not provided"
        prompt = f"""You are filling out a German rental application form on behalf of this applicant:

Name: {u.name}
Occupation: {u.occupation}
Monthly net income: {u.income_monthly} €
Household size: {u.household_size} person(s)
Desired move-in date: {u.move_in_date}
Phone: {u.phone}
Current address: {current_address}
Current street: {u.street or 'not provided'}
Current ZIP code: {u.zip_code or 'not provided'}
Current city: {u.city or 'not provided'}

The form has these extra fields (beyond name/email/message which are already handled):
{fields_desc}

Return a JSON object mapping each field id to the value to enter.
For select fields, use the exact option text from the options list.
Skip any field you cannot meaningfully fill.
Return only valid JSON — no markdown, no explanation.
Example: {{"salutation": "Herr", "phone": "+4917627752034"}}"""

        client = anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        response = client.messages.create(
            model=self.config.claude_model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        try:
            return json.loads(raw)
        except Exception:
            print(f"[warn] Could not parse Claude field response: {raw!r}")
            return {}

    def _build_search_url(self) -> str:
        f = self.config.filters
        params = [
            f"prima=warm",
            f"mpr={f.max_rent}",
            f"ami={f.min_size}",
            f"rn={int(f.min_rooms)}",
        ]
        return f"{IMMOWELT_BASE}/suche/{f.city}/wohnungen/mieten?{'&'.join(params)}"

    async def _extract_page_listings(self) -> list[Listing]:
        try:
            await self.page.wait_for_selector(CARD_SELECTOR, timeout=10000)
        except Exception:
            return []

        cards = self.page.locator(CARD_SELECTOR)
        count = await cards.count()
        listings: list[Listing] = []

        for i in range(count):
            try:
                listing = await self._parse_card(cards.nth(i))
                if listing:
                    listings.append(listing)
            except Exception as e:
                print(f"[warn] Skipping card {i}: {e}")

        return listings

    async def _parse_card(self, card) -> Optional[Listing]:
        link = card.locator(CARD_LINK).first
        href = await link.get_attribute("href")
        if not href:
            return None
        url = href if href.startswith("http") else f"{IMMOWELT_BASE}{href}"
        listing_id = url.rstrip("/").split("/")[-1]

        raw_title = await self._text(card, CARD_TITLE) or "Kein Titel"
        title = _extract_title(raw_title)
        rent = await self._text(card, CARD_PRICE) or "Preis auf Anfrage"
        facts = await self._text(card, CARD_FACTS) or ""
        address = await self._text(card, CARD_ADDRESS) or "Berlin"
        rooms, size = parse_facts(facts)

        return Listing(
            id=listing_id,
            url=url,
            title=title,
            rent=rent.strip(),
            size=size,
            rooms=rooms,
            address=address.strip(),
            wbs_required=requires_wbs(raw_title),
        )

    async def _accept_cookies(self) -> None:
        # Usercentrics v2 renders inside a Shadow DOM on #usercentrics-root,
        # so standard Playwright locators can't see the accept button.
        # We pierce the shadow root via JavaScript first, then fall back to
        # plain selectors for older consent implementations.
        try:
            accepted = await self.page.evaluate("""
                () => {
                    const root = document.querySelector('#usercentrics-root');
                    if (!root) return false;
                    const sr = root.shadowRoot;
                    if (!sr) return false;
                    const btn = sr.querySelector('[data-testid="uc-accept-all-button"]')
                             || sr.querySelector('button[data-testid*="accept"]')
                             || sr.querySelector('button');
                    if (btn) { btn.click(); return true; }
                    return false;
                }
            """)
            if accepted:
                await self._delay(400, 700)
                return
        except Exception:
            pass

        for sel in [
            '[data-testid="uc-accept-all-button"]',
            'button:has-text("Alle akzeptieren")',
            'button:has-text("Akzeptieren")',
        ]:
            try:
                btn = self.page.locator(sel)
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self._delay(300, 600)
                    return
            except Exception:
                continue

    async def _find_first(self, selectors: list[str]):
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    return el
            except Exception:
                continue
        return None

    @staticmethod
    async def _text(locator, selector: str) -> str:
        try:
            return await locator.locator(selector).inner_text(timeout=2000)
        except Exception:
            return ""


def _extract_title(raw: str) -> str:
    """Pull the listing-type line out of the card's multi-line description blob."""
    lines = [l.strip() for l in raw.splitlines() if l.strip() and l.strip() != "·"]
    for line in lines:
        if "zur Miete" in line or "zu vermieten" in line:
            return line
    for line in lines:
        if not re.match(r'^[\d.,\s€m²]+$', line) and line not in ("Kaltmiete", "Warmmiete"):
            return line
    return lines[0] if lines else raw.strip()
