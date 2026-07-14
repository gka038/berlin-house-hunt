import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright
from rich.console import Console

from .cli_approval import print_summary, show_listing
from .config import load_config
from .models import Listing
from .pipeline import MessagePipeline
from .immoscout_scraper import ImmoscoutScraper

console = Console()

_PROFILE_DIR = Path(__file__).parent.parent / "browser_profile_immoscout"


async def _run() -> None:
    config = load_config()
    pipeline = MessagePipeline(config)
    _PROFILE_DIR.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        context = await pw.firefox.launch_persistent_context(
            user_data_dir=str(_PROFILE_DIR),
            headless=False,
            slow_mo=80,
            viewport={"width": 1280, "height": 900},
            locale="de-DE",
            timezone_id="Europe/Berlin",
            args=["--width=1280", "--height=900"],
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        page = context.pages[0] if context.pages else await context.new_page()
        scraper = ImmoscoutScraper(page, config)

        console.print("[bold]Logging in to ImmobilienScout24…[/bold]")
        if not await scraper.login():
            console.print("[red]Login failed. Check IMMOWELT_EMAIL / IMMOWELT_PASSWORD.[/red]")
            await context.close()
            return

        console.print("[green]Login successful.[/green]")
        console.print(
            f"[bold]Searching:[/bold] Berlin · max {config.filters.max_rent}€ warm · "
            f"min {config.filters.min_size}m² · min {config.filters.min_rooms} Zimmer"
        )

        listings = await scraper.search_listings()
        console.print(f"Found [bold]{len(listings)}[/bold] listings.")

        applied: list[Listing] = []
        skipped: list[Listing] = []

        try:
            for listing in listings:
                await scraper.fetch_details(listing)

                if listing.wbs_required:
                    console.print(f"[yellow]Skipping WBS listing: {listing.title}[/yellow]")
                    skipped.append(listing)
                    continue

                if listing.premium_only and not config.filters.apply_premium:
                    console.print(f"[yellow]Skipping premium-only listing: {listing.title}[/yellow]")
                    skipped.append(listing)
                    continue

                console.print(f"\n[dim]Generating message for: {listing.title}…[/dim]")
                german_message, english_message = pipeline.generate_message(listing)

                approved, final_german = show_listing(listing, english_message, german_message)

                if not approved:
                    skipped.append(listing)
                    continue

                success = await scraper.apply(listing, final_german)
                if success:
                    applied.append(listing)
                    console.print(f"[green]✓ Application sent for {listing.title}[/green]")
                else:
                    console.print(
                        f"[red]✗ Could not submit application for {listing.title}. "
                        "Run debug_immoscout.py to inspect selectors.[/red]"
                    )
                    skipped.append(listing)

        except KeyboardInterrupt:
            console.print("\n[yellow]Aborted by user.[/yellow]")

        await context.close()
        print_summary(applied, skipped)


def run() -> None:
    try:
        asyncio.run(_run())
    except EnvironmentError as e:
        Console().print(
            f"[red]Configuration error:[/red] {e}\n"
            "Copy .env.example to .env and fill in your details."
        )
        sys.exit(1)


if __name__ == "__main__":
    run()
