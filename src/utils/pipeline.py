import anthropic

from .config import AppConfig
from .models import Listing

_PROMPT_TEMPLATE = """\
You are helping {user_name} write a rental application message for an Immowelt listing.

Applicant profile:
- Name: {user_name}
- Occupation: {occupation}
- Monthly net income: {income}€
- Household size: {household} person(s)
- Desired move-in date: {move_in_date}
- About me: {about_me}

Listing:
- Title: {title}
- Rent: {rent}
- Size: {size}
- Rooms: {rooms}
- Address: {address}
- Description: {description}

Write a friendly, professional rental inquiry. Requirements:
- Use formal address ("Sie", not "du")
- Start with "Sehr geehrte Damen und Herren," if no contact name is known
- Briefly reference the listing and explain why this apartment suits the applicant
- Mention relevant profile details (occupation, move-in date, household size)
- Close with a request for a viewing appointment
- Maximum 200 words
- No subject line — message body only

Return EXACTLY two sections separated by the line "---ENGLISH---":
1. The German message (to be sent)
2. An English translation of the same message (for display only)

Format:
<German message here>
---ENGLISH---
<English translation here>\
"""


class MessagePipeline:
    def __init__(self, config: AppConfig):
        self.config = config
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    def generate_message(self, listing: Listing) -> tuple[str, str]:
        """Returns (german_message, english_message)."""
        u = self.config.user
        prompt = _PROMPT_TEMPLATE.format(
            user_name=u.name,
            occupation=u.occupation,
            income=u.income_monthly,
            household=u.household_size,
            move_in_date=u.move_in_date,
            about_me=u.about_me,
            title=listing.title,
            rent=listing.rent,
            size=listing.size,
            rooms=listing.rooms,
            address=listing.address,
            description=listing.description[:600],
        )
        response = self._client.messages.create(
            model=self.config.claude_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if "---ENGLISH---" in raw:
            german, english = raw.split("---ENGLISH---", 1)
            return german.strip(), english.strip()
        return raw, raw
