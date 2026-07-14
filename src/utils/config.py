import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class UserProfile:
    name: str
    occupation: str
    income_monthly: int
    household_size: int
    children: int
    move_in_date: str
    phone: str
    about_me: str
    street: str
    house_number: str
    zip_code: str
    city: str


@dataclass
class SearchFilters:
    city: str = "berlin"
    max_rent: int = 1500
    min_size: int = 50
    min_rooms: float = 2.0
    apply_premium: bool = False


@dataclass
class AppConfig:
    email: str
    password: str
    anthropic_api_key: str
    claude_model: str
    user: UserProfile
    filters: SearchFilters


def load_config() -> AppConfig:
    return AppConfig(
        email=_require("IMMOWELT_EMAIL"),
        password=_require("IMMOWELT_PASSWORD"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        user=UserProfile(
            name=_require("USER_NAME"),
            occupation=_require("USER_OCCUPATION"),
            income_monthly=int(_require("USER_INCOME_MONTHLY")),
            household_size=int(os.getenv("USER_HOUSEHOLD_SIZE", "1")),
            children=int(os.getenv("USER_CHILDREN", "0")),
            move_in_date=os.getenv("USER_MOVE_IN_DATE", "flexibel"),
            phone=os.getenv("USER_PHONE", ""),
            about_me=os.getenv("USER_ABOUT_ME", ""),
            street=os.getenv("USER_STREET", ""),
            house_number=os.getenv("USER_HOUSE_NUMBER", ""),
            zip_code=os.getenv("USER_ZIP", ""),
            city=os.getenv("USER_CITY", ""),
        ),
        filters=SearchFilters(
            max_rent=int(os.getenv("SEARCH_MAX_RENT", "1500")),
            min_size=int(os.getenv("SEARCH_MIN_SIZE", "50")),
            min_rooms=float(os.getenv("SEARCH_MIN_ROOMS", "2")),
            apply_premium=os.getenv("APPLY_PREMIUM", "false").lower() == "true",
        ),
    )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Missing required env var: {key}")
    return value
