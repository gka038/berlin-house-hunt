import re
from typing import Optional

_WBS_NOT_REQUIRED = re.compile(
    r"kein\w*\s+WBS"
    r"|ohne\s+WBS"
    r"|WBS\s+(?:ist\s+)?nicht\s+\w+"
    r"|kein\w*\s+Wohnberechtigungsschein"
    r"|ohne\s+Wohnberechtigungsschein",
    re.IGNORECASE,
)
_WBS_REQUIRED = re.compile(r"\bWBS\b|Wohnberechtigungsschein", re.IGNORECASE)


def requires_wbs(text: str) -> bool:
    if _WBS_NOT_REQUIRED.search(text):
        return False
    return bool(_WBS_REQUIRED.search(text))


def parse_german_number(text: str) -> Optional[float]:
    """Parse a German-formatted number (1.200,50 → 1200.50) from a string."""
    m = re.search(r"([\d]+(?:\.[\d]{3})*(?:,[\d]+)?)", text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_facts(text: str) -> tuple[str, str]:
    """Extract rooms and size strings from a card facts string."""
    rooms = ""
    size = ""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:Zi\b|Zimmer)", text)
    if m:
        rooms = m.group(1)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", text)
    if m:
        size = f"{m.group(1)} m²"
    return rooms, size
