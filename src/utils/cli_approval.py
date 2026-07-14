from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.text import Text

from .models import Listing

console = Console()


def show_listing(listing: Listing, english_message: str, german_message: str) -> tuple[bool, str]:
    """Display listing and English preview. Returns (approved, final_german_message)."""
    console.rule("[bold cyan]New Listing[/bold cyan]")

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", width=12)
    table.add_column()
    table.add_row("Title", listing.title)
    table.add_row("Rent", listing.rent)
    table.add_row("Size", listing.size or "–")
    table.add_row("Rooms", listing.rooms or "–")
    table.add_row("Address", listing.address)
    table.add_row("URL", listing.url)
    console.print(Panel(table, title="Listing Details", border_style="blue"))

    console.print(Panel(Text(english_message), title="Generated Message (English preview)", border_style="green"))

    while True:
        choice = Prompt.ask(
            "[bold]Action[/bold]",
            choices=["y", "n", "e", "q"],
            default="n",
            show_choices=True,
        )
        console.print(
            "  [dim]y[/dim] = send  "
            "[dim]n[/dim] = skip  "
            "[dim]e[/dim] = edit German message  "
            "[dim]q[/dim] = quit",
            highlight=False,
        )

        if choice == "q":
            raise KeyboardInterrupt

        if choice == "n":
            console.print("[yellow]Skipped.[/yellow]\n")
            return False, german_message

        if choice == "y":
            console.print("[green]Sending…[/green]\n")
            return True, german_message

        if choice == "e":
            console.print("[dim]Edit German message (blank line to finish):[/dim]")
            console.print(Panel(Text(german_message), title="Current German message", border_style="dim"))
            lines = []
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
            if lines:
                german_message = "\n".join(lines)
            console.print(Panel(Text(german_message), title="Edited German message", border_style="yellow"))


def print_summary(applied: list[Listing], skipped: list[Listing]) -> None:
    console.rule("[bold]Summary[/bold]")
    console.print(f"[green]Sent:[/green] {len(applied)}")
    for listing in applied:
        console.print(f"  ✓ {listing.title} — {listing.address}")
    console.print(f"[yellow]Skipped:[/yellow] {len(skipped)}")
    console.rule()
