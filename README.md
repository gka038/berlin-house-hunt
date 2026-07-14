# Rental Bot

Automates apartment applications on [Immowelt](https://www.immowelt.de) and [ImmobilienScout24](https://www.immobilienscout24.de). For each listing it finds, it generates a personalised German application message using Claude, shows you a preview in English, and lets you approve, skip, or edit before submitting.

## How it works

```
Search → Filter → Fetch details → Generate message (Claude) → You approve → Submit form
```

1. **Browser automation (Playwright + Firefox)** — logs in with your credentials using a persistent browser profile so cookies survive between runs, which avoids triggering bot detection on repeated logins.
2. **Warmup** — before searching, browses a couple of editorial pages to build a more human-looking session pattern.
3. **Search & filter** — paginates through search results and drops listings that exceed your rent/size/rooms limits or require a WBS (Wohnberechtigungsschein).
4. **Detail fetch** — opens each passing listing to grab the full description and check for any late-appearing filters (WBS in body text, Suchen+ paywall on IS24).
5. **Message generation** — sends the listing details and your profile to Claude, which writes a formal German inquiry (≤200 words) and an English translation for your review.
6. **CLI approval loop** — displays the listing and English preview; you choose `y` (send), `n` (skip), `e` (edit the German message), or `q` (quit).
7. **Form submission** — fills the contact form field by field. Unknown fields (those beyond name/email/message) are auto-detected and filled by Claude based on your profile.

### Project layout

```
src/
  utils/           shared code
    models.py        Listing dataclass
    config.py        AppConfig + load_config (reads .env)
    pipeline.py      Claude message generation
    cli_approval.py  interactive approval UI
    parsing.py       WBS detection, German number/facts parsing
    browser.py       BaseScraper with shared browser helpers
  immowelt/        Immowelt-specific
    scraper.py       ImmoweltScraper
    main.py          entry point
  immoscout/       ImmobilienScout24-specific
    scraper.py       ImmoscoutScraper
    main.py          entry point

debug_contact.py   inspect Immowelt selectors interactively
debug_immoscout.py inspect IS24 selectors interactively
```

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (or pip)
- Firefox (installed by Playwright)
- An [Anthropic API key](https://console.anthropic.com/)
- Accounts on Immowelt and/or ImmobilienScout24

## Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd immowelt

# 2. Create a virtual environment and install dependencies
uv venv
uv pip install -e .

# 3. Install the Playwright Firefox browser
.venv/bin/playwright install firefox

# 4. Copy the example env file and fill in your details
cp .env.example .env
```

Edit `.env` with your credentials and profile (see [Configuration](#configuration) below).

## Running

Each command opens a real Firefox window — do not close it while the bot is running.

**Immowelt:**
```bash
.venv/bin/python -m src.immowelt.main
# or, if installed as a script:
.venv/bin/immowelt
```

**ImmobilienScout24:**
```bash
.venv/bin/python -m src.immoscout.main
# or:
.venv/bin/immoscout
```

On the first run you will see a browser window open and the bot will log in. Cookies are stored in `browser_profile/` (Immowelt) or `browser_profile_immoscout/` (IS24) so subsequent runs skip the login step.

ImmobilienScout24 may ask for an email OTP on first login — the terminal will prompt you to paste the code.

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started.

| Variable | Required | Description |
|---|---|---|
| `IMMOWELT_EMAIL` | yes | Login email for both platforms |
| `IMMOWELT_PASSWORD` | yes | Login password for both platforms |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key for message generation |
| `CLAUDE_MODEL` | no | Model to use (default: `claude-sonnet-4-6`) |
| `USER_NAME` | yes | Your full name |
| `USER_OCCUPATION` | yes | Job title used in the message |
| `USER_INCOME_MONTHLY` | yes | Net monthly income in € |
| `USER_HOUSEHOLD_SIZE` | no | Number of people moving in (default: 1) |
| `USER_MOVE_IN_DATE` | no | Preferred move-in date (default: `flexibel`) |
| `USER_PHONE` | no | Phone number for contact forms |
| `USER_STREET` | no | Current street name |
| `USER_HOUSE_NUMBER` | no | Current house number |
| `USER_ZIP` | no | Current postal code |
| `USER_CITY` | no | Current city |
| `USER_ABOUT_ME` | no | A short sentence about yourself for the message |
| `SEARCH_MAX_RENT` | no | Maximum warm rent in € (default: 1500) |
| `SEARCH_MIN_SIZE` | no | Minimum size in m² (default: 50) |
| `SEARCH_MIN_ROOMS` | no | Minimum number of rooms (default: 2) |
| `APPLY_PREMIUM` | no | Apply to Suchen+/MieterPlus listings on IS24 (default: false) |

Search results are currently hardcoded to **Berlin**. To change the city, update `SEARCH_MIN_ROOMS` and the city slug in `_build_search_url()` inside the relevant scraper.

## Debugging selectors

When a site updates its DOM and selectors break, use the debug scripts to inspect what's actually on the page:

```bash
# Immowelt — dumps all data-testid attributes and form fields
.venv/bin/python debug_contact.py

# ImmobilienScout24 — dumps search cards, listing page, and contact form fields
.venv/bin/python debug_immoscout.py
```

Both scripts open the browser visibly, log in, navigate to a real listing, and print every relevant selector. Update the constants at the top of the scraper files accordingly.

## Notes

- The bot runs with `headless=False` and deliberately adds random delays between actions to reduce bot-detection risk. Do not switch to headless mode without testing first.
- Applications are only submitted after your explicit approval in the terminal.
- IS24 listings behind the Suchen+/MieterPlus paywall are skipped by default. Set `APPLY_PREMIUM=true` to attempt them (you still need to approve each one).
- WBS listings are always skipped.
