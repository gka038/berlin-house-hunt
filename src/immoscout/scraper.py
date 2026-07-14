import asyncio
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

IMMOSC_BASE = "https://www.immobilienscout24.de"
_SIGNIN_DOMAIN = "sso.immobilienscout24.de"
_LOGIN_URL = "https://sso.immobilienscout24.de/sso/login"

# NOTE: ImmoScout24's DOM changes over time. If selectors stop working,
# run debug_immoscout.py against the live site and update these constants.

# Search result page — IS24 HybridView layout (updated Jul 2025)
LISTINGS_LOADED = '[data-testid="ListingsGrid"], a[href*="/expose/"]'
PAGINATION_NEXT = '[data-testid="pagination-button-next"]'

# Expose (listing detail) page
DESCRIPTION_SELECTOR = '[data-testid="text-desc-origin"], #expose-description, [class*="description__text"]'
# IS24 hash URL opens the contact modal directly without clicking any button.
CONTACT_FORM = '[data-testid="contact-form"]'
CONTACT_MODAL = '[data-testid="modal-wrapper"]'
CONTACT_SUBMIT = '[data-testid="contact-form"] button[type="submit"]'
# Suchen+/MieterPlus upsell — listing requires a paid subscription to contact
PREMIUM_WALL = (
    '[data-testid="MieterPlusPackagedArticleSelection"],'
    '[data-testid="mieterplus-packaged-package-selection"],'
    '[data-testid="suchen-plus-teaser"],'
    '[data-testid="contact-cta-suchen-plus"],'
    '[class*="MieterPlus"],[class*="mieterPlus"],[class*="suchen-plus"],'
    '[class*="SuchenPlus"]'
)
# Text phrases on expose pages that indicate a paid plan is required
_PREMIUM_PHRASES = (
    "Nur mit Suchen+",
    "Nur mit MieterPlus",
    "Anfragen nur mit",
    "Suchen+ Mitglied",
    "jetzt Suchen+ holen",
    "MieterPlus freischalten",
)


class ImmoscoutScraper(BaseScraper):
    def __init__(self, page: Page, config: AppConfig):
        super().__init__(page, config)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def login(self) -> bool:
        await self.page.goto(IMMOSC_BASE, wait_until="domcontentloaded")
        await self._delay(800, 1500)
        await self._accept_cookies()

        # Click the account link — if logged in it goes to /meinkonto/dashboard/,
        # if not logged in it redirects to the SSO login page.
        account_link = self.page.locator('a[href*="geschlossenerbereich"]').first
        try:
            if await account_link.is_visible(timeout=3000):
                print(f"[login] Clicking account link to detect session state…")
                await account_link.click()
                await self.page.wait_for_load_state("domcontentloaded")
                await self._delay(800, 1500)
        except Exception:
            pass

        if "meinkonto" in self.page.url or "dashboard" in self.page.url:
            print("[login] Already logged in.")
            return True

        if _SIGNIN_DOMAIN not in self.page.url:
            print(f"[login] Unexpected URL {self.page.url!r}, navigating to SSO directly…")
            await self.page.goto(_LOGIN_URL, wait_until="domcontentloaded")
            await self._delay(800, 1500)

        if _SIGNIN_DOMAIN not in self.page.url:
            print(f"[login] Could not reach SSO. Current URL: {self.page.url}")
            return False

        await self._accept_cookies()

        email_field = self.page.locator(
            'input[name="username"], input[type="email"], input[id="username"]'
        ).first
        try:
            await email_field.wait_for(state="visible", timeout=10000)
        except Exception:
            return False

        await self._human_type(email_field, self.config.email)
        await self._delay(800, 1500)

        # If the password field is NOT yet visible, the form is two-step:
        # click Weiter/submit to advance to the password step.
        password_field = self.page.locator('input[name="password"], input[type="password"]').first
        try:
            await password_field.wait_for(state="visible", timeout=2000)
        except Exception:
            await self._click_first([
                'button[type="submit"]:has-text("Weiter")',
                'button:has-text("Weiter")',
                'button[type="submit"]',
            ])
            await self._delay(1000, 2000)
            await self._handle_otp_if_present()

        try:
            await password_field.wait_for(state="visible", timeout=10000)
        except Exception:
            return False
        await password_field.click()
        await self._delay(400, 800)
        await self._human_type(password_field, self.config.password)
        await self._delay(1000, 2000)

        submit_selectors = [
            'button[type="submit"]:has-text("Anmelden")',
            'button:has-text("Anmelden")',
            'button[type="submit"]:has-text("Einloggen")',
            'button:has-text("Einloggen")',
            'button[type="submit"]:has-text("Login")',
            'button:has-text("Login")',
            'button[type="submit"][name="action"]',
            'form button[type="submit"]',
            'button[type="submit"]',
        ]
        clicked = False
        for sel in submit_selectors:
            try:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    txt = await el.inner_text()
                    print(f"[login] Clicking submit: {sel!r}  text={txt!r}")
                    await el.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            print(f"[login] No submit button found. Current URL: {self.page.url}")
            buttons = await self.page.locator("button").all()
            for btn in buttons:
                try:
                    print(f"  button: text={await btn.inner_text()!r}  type={await btn.get_attribute('type')!r}")
                except Exception:
                    pass
            return False

        await self._handle_otp_if_present()

        try:
            await self.page.wait_for_url(
                lambda url: url.startswith("https://www.immobilienscout24.de"),
                timeout=30000,
            )
        except Exception:
            pass

        print(f"[login] Final URL: {self.page.url}")
        return self.page.url.startswith("https://www.immobilienscout24.de")

    async def warmup(self) -> None:
        warmup_urls = [
            f"{IMMOSC_BASE}/ratgeber",
            f"{IMMOSC_BASE}/ratgeber/mieten",
        ]
        for url in warmup_urls:
            try:
                await self.page.goto(url, wait_until="domcontentloaded")
                await self.page.evaluate(f"window.scrollBy(0, {random.randint(300, 700)})")
                await self._delay(3000, 6000)
            except Exception:
                pass

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
                    reason = ("WBS" if lst.wbs_required
                              else "Premium/Suchen+" if lst.premium_only
                              else f"{lst.rooms} Zi / {lst.size}")
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
        await self._delay(2000, 5000)
        await self.page.goto(listing.url, wait_until="domcontentloaded")
        await self._delay(1500, 3000)
        await self.page.evaluate(f"window.scrollBy(0, {random.randint(200, 500)})")
        await self._delay(1000, 2000)

        if await self.page.locator(PREMIUM_WALL).count():
            listing.premium_only = True
            return listing
        page_text = await self.page.inner_text("body")
        if any(phrase in page_text for phrase in _PREMIUM_PHRASES):
            listing.premium_only = True
            return listing

        try:
            desc = await self.page.locator(DESCRIPTION_SELECTOR).inner_text(timeout=5000)
            listing.description = desc.strip()
            if requires_wbs(listing.description):
                listing.wbs_required = True
        except Exception:
            pass
        return listing

    async def apply(self, listing: Listing, message: str) -> bool:
        base_url = listing.url.split("#")[0]

        container = await self._open_contact_form(base_url, listing.id)
        if container is None:
            return False

        msg_field = container.locator('textarea[name="message"], textarea').first
        try:
            await msg_field.wait_for(state="visible", timeout=5000)
            print(f"[apply] Textarea found")
        except Exception:
            await self._screenshot(listing.id, "no_textarea")
            print(f"[apply] Textarea not visible — dumping visible fields:")
            await self._dump_form_fields(container)
            return False

        await msg_field.fill("")
        await msg_field.fill(message)
        await self._delay(300, 600)

        u = self.config.user
        parts = u.name.split(" ", 1)
        for field_name, value in [
            ("firstName", parts[0]),
            ("lastName", parts[1] if len(parts) > 1 else ""),
            ("emailAddress", self.config.email),
            ("phoneNumber", u.phone or ""),
        ]:
            if not value:
                continue
            try:
                field = container.locator(f'input[name="{field_name}"]').first
                if await field.is_visible(timeout=2000):
                    current = await field.input_value()
                    if not current:
                        await self._human_type(field, value)
                        await self._delay(300, 800)
            except Exception:
                pass

        # IS24-specific structured fields (optional; varies per listing)
        await self._fill_select(container, "salutation", ["Herr"])
        if u.household_size:
            await self._fill_input_if_empty(container, "numberOfAdults", str(u.household_size))
        await self._fill_input_if_empty(container, "numberOfKids", "0")
        if u.income_monthly:
            await self._fill_input_if_empty(container, "incomeAmount", str(int(u.income_monthly)))
        await self._fill_select(container, "employmentStatus", ["Angestellt", "Angestellte", "Arbeitnehmer"])
        for neg_field in ["hasPets", "rentArrears", "insolvencyProcess", "smoker", "forCommercialPurposes"]:
            await self._fill_select(container, neg_field, ["Nein", "Nichtraucher", "nein"])
        if u.move_in_date:
            await self._fill_input_if_empty(container, "moveInDate", u.move_in_date)
        if u.street:
            await self._fill_input_if_empty(container, "street", u.street)
            await self._fill_input_if_empty(container, "streetName", u.street)
        if u.house_number:
            await self._fill_input_if_empty(container, "houseNumber", u.house_number)
            await self._fill_input_if_empty(container, "hausnummer", u.house_number)
        if u.zip_code:
            await self._fill_input_if_empty(container, "zipCode", u.zip_code)
            await self._fill_input_if_empty(container, "postalCode", u.zip_code)
        if u.city:
            await self._fill_input_if_empty(container, "city", u.city)

        # Accept privacy policy (required — IS24 API rejects with ERROR_RESOURCE_VALIDATION if false)
        await self._accept_privacy_policy(container)

        try:
            cb_frame = container.locator('[data-testid="checkbox-frame"]').first
            if await cb_frame.is_visible(timeout=2000):
                cb = cb_frame.locator('input[type="checkbox"]').first
                try:
                    if not await cb.is_checked():
                        await cb.check()
                except Exception:
                    await cb_frame.click()
                await self._delay(200, 400)
        except Exception:
            pass

        await self._fill_extra_fields(container)
        await self._delay(800, 2000)

        # IS24's React form initialises privacyPolicyAccepted=false and there is no
        # DOM checkbox that sets it to true — intercept the POST and patch it.
        expose_pattern = f"**/expose/{listing.id}"
        api_responses: list[tuple[int, str, str]] = []

        async def _patch_and_log(route, request) -> None:
            if request.method == "POST":
                try:
                    data = json.loads(request.post_data or "{}")
                    if "privacyPolicyAccepted" in data and not data["privacyPolicyAccepted"]:
                        data["privacyPolicyAccepted"] = True
                        print(f"[apply] Patched privacyPolicyAccepted=true in request")
                        await route.continue_(post_data=json.dumps(data))
                        return
                except Exception:
                    pass
            await route.continue_()

        async def _capture_response(response) -> None:
            url = response.url
            if any(k in url for k in ["/expose/", "/contact", "/send", "/message", "/api/"]):
                try:
                    body = await response.text()
                    api_responses.append((response.status, url, body))
                except Exception:
                    api_responses.append((response.status, url, ""))

        await self.page.route(expose_pattern, _patch_and_log)
        self.page.on("response", _capture_response)

        clicked = False
        for submit_sel in [CONTACT_SUBMIT, 'button[type="submit"]']:
            submit = (
                self.page.locator(submit_sel).first
                if submit_sel == CONTACT_SUBMIT
                else container.locator(submit_sel).first
            )
            try:
                await submit.wait_for(state="visible", timeout=3000)
                await submit.click()
                clicked = True
                print(f"[apply] Submit clicked ({submit_sel!r})")
                break
            except Exception:
                continue

        if not clicked:
            await self.page.unroute(expose_pattern, _patch_and_log)
            self.page.remove_listener("response", _capture_response)
            await self._screenshot(listing.id, "no_submit")
            print(f"[apply] Submit button not found — dumping visible fields:")
            await self._dump_form_fields(container)
            return False

        await self._delay(2500, 3500)
        await self.page.unroute(expose_pattern, _patch_and_log)
        self.page.remove_listener("response", _capture_response)

        for status, url, body in api_responses:
            short_url = url.split("?")[0][-80:]
            if status >= 400 or "error" in body.lower() or "fehler" in body.lower():
                print(f"[network] {status} …{short_url}")
                print(f"[network] body: {body[:600]}")

        if not await self._verify_submission(container):
            await self._screenshot(listing.id, "submit_failed")
            print(f"[apply] Form still visible after submit — likely validation errors. Screenshot saved.")
            return False

        listing.applied = True
        return True

    # ------------------------------------------------------------------ #
    # Apply helpers                                                        #
    # ------------------------------------------------------------------ #

    async def _open_contact_form(self, base_url: str, listing_id: str):
        """Navigate to the contact modal and return its container locator, or None."""
        for hash_suffix in ["#/basicContact/email", "#/contact/email", "#/basicContact/", ""]:
            url = base_url + hash_suffix
            print(f"[apply] Trying contact URL: {url}")
            await self.page.goto(url, wait_until="domcontentloaded")
            await self._delay(2000, 3500)
            print(f"[apply] Landed on: {self.page.url}")

            if await self.page.locator(PREMIUM_WALL).count():
                print(f"[apply] Suchen+ required — skipping {listing_id}")
                return None

            for container_sel in [CONTACT_MODAL, CONTACT_FORM]:
                loc = self.page.locator(container_sel).first
                try:
                    await loc.wait_for(state="visible", timeout=5000)
                    print(f"[apply] Container found: {container_sel!r}")
                    return loc
                except Exception:
                    pass

            if hash_suffix == "":
                clicked = await self._click_first([
                    'button:has-text("Kontakt aufnehmen")',
                    'button:has-text("Anfrage senden")',
                    'a:has-text("Kontaktieren")',
                    '[data-testid="contact-button"]',
                ])
                if clicked:
                    await self._delay(2000, 3000)
                    for container_sel in [CONTACT_MODAL, CONTACT_FORM]:
                        loc = self.page.locator(container_sel).first
                        try:
                            await loc.wait_for(state="visible", timeout=5000)
                            print(f"[apply] Container found after button click: {container_sel!r}")
                            return loc
                        except Exception:
                            pass

        await self._screenshot(listing_id, "no_container")
        print(f"[apply] Contact form not found for {listing_id} — screenshot saved to /tmp/is24_apply_{listing_id}_no_container.png")
        return None

    async def _verify_submission(self, container) -> bool:
        """Return True if the form was accepted (modal/form gone or success message shown)."""
        success_selectors = [
            '[data-testid="contact-success"]',
            '[data-testid="send-success"]',
            '[data-testid="success-message"]',
            'text="Anfrage gesendet"',
            'text="Nachricht gesendet"',
            'text="Erfolgreich gesendet"',
        ]
        for sel in success_selectors:
            try:
                if await self.page.locator(sel).is_visible(timeout=500):
                    print(f"[apply] Success indicator found: {sel!r}")
                    return True
            except Exception:
                pass

        try:
            still_visible = await container.is_visible(timeout=1000)
            if not still_visible:
                print(f"[apply] Container gone after submit — treating as success")
                return True
        except Exception:
            return True

        try:
            errors = await self.page.locator(
                '[data-testid*="error"], [class*="error"], [class*="validation"], '
                '[role="alert"], .form-error, .field-error'
            ).all_inner_texts()
            errors = [e.strip() for e in errors if e.strip()]
            if errors:
                print(f"[apply] Form validation errors: {errors}")
        except Exception:
            pass

        return False

    async def _accept_privacy_policy(self, container) -> None:
        """Check the IS24 privacy-policy consent checkbox.

        IS24's contact API rejects submissions with ERROR_RESOURCE_VALIDATION when
        privacyPolicyAccepted=false.  The checkbox is often hidden or unlabelled so
        we try several strategies in order.
        """
        for name in ["privacyPolicyAccepted", "privacy", "datenschutz", "agb"]:
            try:
                cb = container.locator(f'input[type="checkbox"][name="{name}"]').first
                if await cb.count() and await cb.is_visible(timeout=800):
                    if not await cb.is_checked():
                        await cb.click()
                    print(f"[apply] Privacy policy checked (name={name!r})")
                    await self._delay(200, 400)
                    return
            except Exception:
                pass

        for phrase in ["Datenschutz", "Nutzungsbedingungen", "privacy", "AGB", "Einwilligung"]:
            try:
                label = container.locator(f'label:has-text("{phrase}")').first
                if await label.is_visible(timeout=800):
                    inner_cb = label.locator('input[type="checkbox"]').first
                    if await inner_cb.count():
                        if not await inner_cb.is_checked():
                            await inner_cb.click()
                    else:
                        await label.click()
                    print(f"[apply] Privacy policy accepted via label ({phrase!r})")
                    await self._delay(200, 400)
                    return
            except Exception:
                pass

        try:
            accepted = await container.evaluate("""el => {
                const cbs = el.querySelectorAll('input[type="checkbox"]');
                for (const cb of cbs) {
                    const key = (cb.name || cb.id || '').toLowerCase();
                    if (key.includes('privacy') || key.includes('datenschutz') || key.includes('agb')) {
                        if (!cb.checked) {
                            const setter = Object.getOwnPropertyDescriptor(
                                HTMLInputElement.prototype, 'checked').set;
                            setter.call(cb, true);
                            cb.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            cb.dispatchEvent(new Event('change', {bubbles: true}));
                            cb.dispatchEvent(new Event('input', {bubbles: true}));
                        }
                        return true;
                    }
                }
                return false;
            }""")
            if accepted:
                print(f"[apply] Privacy policy accepted via JS event dispatch")
                await self._delay(300, 500)
                return
        except Exception:
            pass

        print(f"[apply] Warning: privacy policy checkbox not found — dumping checkboxes for inspection:")
        try:
            cbs = await container.locator('input[type="checkbox"]').all()
            for cb in cbs:
                try:
                    name = await cb.get_attribute("name") or ""
                    cb_id = await cb.get_attribute("id") or ""
                    tid = await cb.get_attribute("data-testid") or ""
                    checked = await cb.is_checked()
                    print(f"  checkbox name={name!r} id={cb_id!r} testid={tid!r} checked={checked}")
                except Exception:
                    pass
        except Exception:
            pass

    async def _screenshot(self, listing_id: str, label: str) -> None:
        path = f"/tmp/is24_apply_{listing_id}_{label}.png"
        try:
            await self.page.screenshot(path=path)
            print(f"[apply] Screenshot: {path}")
        except Exception:
            pass

    async def _dump_form_fields(self, container) -> None:
        """Print all visible form fields — helps identify missing selectors."""
        try:
            fields = await container.locator(
                "input:not([type='hidden']), select, textarea, button"
            ).all()
            for f in fields:
                try:
                    if not await f.is_visible(timeout=300):
                        continue
                    tag = await f.evaluate("e => e.tagName.toLowerCase()")
                    name = await f.get_attribute("name") or ""
                    tid = await f.get_attribute("data-testid") or ""
                    ftype = await f.get_attribute("type") or ""
                    print(f"  {tag:10s} name={name!r:25s} type={ftype!r:12s} testid={tid!r}")
                except Exception:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _matches_filters(self, listing: Listing) -> bool:
        f = self.config.filters
        if listing.wbs_required:
            return False
        if listing.premium_only and not f.apply_premium:
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

    def _build_search_url(self) -> str:
        f = self.config.filters
        params = [
            f"numberofrooms={f.min_rooms}-",
            f"price=-{float(f.max_rent)}",
            f"livingspace={float(f.min_size)}-",
            "pricetype=calculatedtotalrent",
            "enteredFrom=result_list",
        ]
        return f"{IMMOSC_BASE}/Suche/de/berlin/berlin/wohnung-mieten?{'&'.join(params)}"

    async def _extract_page_listings(self) -> list[Listing]:
        try:
            await self.page.wait_for_selector(LISTINGS_LOADED, timeout=10000)
        except Exception:
            return []

        # IS24's HybridView has no result-list-entry testids; walk the DOM via JS.
        try:
            raw = await self.page.evaluate("""
                () => {
                    const seen = new Set();
                    const results = [];
                    document.querySelectorAll('a[href*="/expose/"]').forEach(a => {
                        const m = (a.href || '').match(/\\/expose\\/(\\d+)/);
                        if (!m) return;
                        const id = m[1];
                        if (seen.has(id)) return;
                        seen.add(id);

                        let card = a.parentElement;
                        for (let i = 0; i < 10 && card; i++) {
                            if (card.querySelector('[data-testid="headline"]') ||
                                card.querySelector('[data-testid="attributeSection"]')) break;
                            card = card.parentElement;
                        }

                        const headline = card && card.querySelector('[data-testid="headline"]');
                        const attrs    = card && card.querySelector('[data-testid="attributes"]');
                        const addr     = card && card.querySelector('[data-testid="hybridViewAddress"]');

                        const cardText = card ? card.innerText : '';
                        const isPremium = !!(
                            (card && card.querySelector('[class*="MieterPlus"],[class*="mieterPlus"],[class*="suchen-plus"],[class*="SuchenPlus"],[data-testid*="suchen-plus"],[data-testid*="mieterplus"]')) ||
                            cardText.includes('Suchen+') ||
                            cardText.includes('MieterPlus') ||
                            cardText.includes('Anfragen nur mit')
                        );
                        results.push({
                            id,
                            url: a.href.split('?')[0],
                            title:   headline ? headline.innerText.trim() : '',
                            facts:   attrs    ? attrs.innerText.trim()    : '',
                            address: addr     ? addr.innerText.trim()     : '',
                            premium: isPremium,
                        });
                    });
                    return results;
                }
            """)
        except Exception as e:
            print(f"[warn] JS card extraction failed: {e}")
            return []

        listings: list[Listing] = []
        for item in raw:
            rooms, size = parse_facts(item.get("facts", ""))
            rent_m = re.search(r"([\d.,]+)\s*€", item.get("facts", ""))
            rent = rent_m.group(0) if rent_m else "Preis auf Anfrage"
            title = item.get("title") or f"Inserat {item['id']}"
            listings.append(Listing(
                id=item["id"],
                url=item["url"],
                title=title,
                rent=rent,
                size=size,
                rooms=rooms,
                address=item.get("address") or "Berlin",
                wbs_required=requires_wbs(title),
                premium_only=bool(item.get("premium")),
            ))

        return listings

    async def _accept_cookies(self) -> None:
        # ImmoScout24 uses Sourcepoint CMP — try standard visible selectors first,
        # then fall back to a JS pierce for shadow-DOM implementations.
        for sel in [
            'button:has-text("Alle akzeptieren")',
            'button:has-text("Akzeptieren")',
            '[data-testid="uc-accept-all-button"]',
            'button[title*="Akzeptieren"]',
            '#consent-accept-all',
        ]:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    await self._delay(300, 600)
                    return
            except Exception:
                continue

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
        except Exception:
            pass

    async def _fill_extra_fields(self, container) -> None:
        known_names = {
            "message", "firstName", "lastName", "email", "emailAddress", "phoneNumber",
            "salutation", "numberOfAdults", "numberOfKids", "incomeAmount",
            "employmentStatus", "hasPets", "rentArrears", "insolvencyProcess",
            "smoker", "forCommercialPurposes",
            "street", "streetName", "houseNumber", "hausnummer",
            "zipCode", "postalCode", "city", "moveInDate",
        }
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
                    "id": field_id, "tag": tag, "type": el_type,
                    "label": label, "placeholder": placeholder,
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
        current_address = " ".join(filter(None, [u.street, u.house_number, u.zip_code, u.city])) or "not provided"
        fields_desc = "\n".join(
            "  - id={id!r} label={label!r} placeholder={placeholder!r} type={type!r}".format(**f)
            + (f" options={f['options']}" if "options" in f else "")
            for f in fields
        )
        prompt = f"""You are filling out a German rental application form on behalf of this applicant:

Name: {u.name}
Occupation: {u.occupation}
Monthly net income: {u.income_monthly} €
Household size: {u.household_size} person(s)
Desired move-in date: {u.move_in_date}
Phone: {u.phone}
Current address: {current_address}
Current street: {u.street or 'not provided'}
Current house number: {u.house_number or 'not provided'}
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

    async def _fill_select(self, container, name: str, preferred: list[str]) -> None:
        try:
            sel = container.locator(f'select[name="{name}"]').first
            if not await sel.is_visible(timeout=1500):
                return
            options = await sel.evaluate(
                "e => Array.from(e.options).map(o => ({v: o.value, t: o.text.trim()}))"
            )
            for pref in preferred:
                for opt in options:
                    if pref.lower() in opt["t"].lower():
                        await sel.select_option(value=opt["v"])
                        await self._delay(200, 500)
                        return
            for opt in options:
                if opt["v"]:
                    await sel.select_option(value=opt["v"])
                    await self._delay(200, 500)
                    return
        except Exception:
            pass

    async def _fill_input_if_empty(self, container, name: str, value: str) -> None:
        try:
            field = container.locator(f'input[name="{name}"]').first
            if await field.is_visible(timeout=1500):
                if not await field.input_value():
                    await field.fill(value)
                    await self._delay(200, 400)
        except Exception:
            pass

    async def _handle_otp_if_present(self) -> None:
        otp_selectors = [
            'input[autocomplete="one-time-code"]',
            'input[name="otp"]',
            'input[name="code"]',
            'input[name="tan"]',
            'input[type="number"][maxlength="6"]',
            'input[placeholder*="OTP"]',
            'input[placeholder*="Code"]',
            'input[placeholder*="PIN"]',
        ]
        for sel in otp_selectors:
            try:
                field = self.page.locator(sel).first
                if await field.is_visible(timeout=2000):
                    print("\n[OTP] ImmobilienScout24 is requesting an email verification code.")
                    print("[OTP] Check your email and enter the code here: ", end="", flush=True)
                    loop = asyncio.get_event_loop()
                    code = await loop.run_in_executor(None, input)
                    await field.fill(code.strip())
                    await self._delay(500, 1000)
                    await self._click_first(['button[type="submit"]'])
                    await self._delay(1500, 2500)
                    return
            except Exception:
                continue

    @staticmethod
    async def _text(locator, selector: str) -> str:
        try:
            return await locator.locator(selector).first.inner_text(timeout=2000)
        except Exception:
            return ""


def _extract_title(raw: str) -> str:
    lines = [l.strip() for l in raw.splitlines() if l.strip() and l.strip() != "·"]
    for line in lines:
        if "zur Miete" in line or "zu vermieten" in line or "Zimmer" in line:
            if "€" not in line and "m²" not in line:
                return line
    for line in lines:
        if not re.match(r'^[\d.,\s€m²]+$', line) and line not in ("Kaltmiete", "Warmmiete"):
            return line
    return lines[0] if lines else raw.strip()
