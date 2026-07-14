from dataclasses import dataclass, field


@dataclass
class Listing:
    id: str
    url: str
    title: str = ""
    rent: str = ""
    size: str = ""
    rooms: str = ""
    address: str = ""
    description: str = ""
    contact_name: str = ""
    applied: bool = False
    wbs_required: bool = False
    premium_only: bool = False
