"""Per-project identity for location- and client-aware drafting.

Ported ≈verbatim from Claude-Spec-Critic ``src/core/project_profile.py``
(WS-2 of its hyperscale plan), with the Build-a-Spec entry path swapped in:
Spec Critic collects the profile through a standalone GUI form; here the
same ``set_project_profile`` document operation backs two entry paths —
the model records it as the user states location and client in the
interview, and the panel's project-profile form (``ResearchDrawer``)
posts the identical op through ``POST /api/doc/edit`` so the user can
fill it out directly at any time, start to finish. Either way the stored
dict lives on ``SpecSection.project_profile`` (riding the tree's
transactional apply / undo / project-file machinery), and the model sees
a per-turn completeness reminder so it can chase whatever is still
missing incrementally instead of only asking once.

**Normalization is load-bearing, not cosmetic.**
:meth:`ProjectProfile.web_search_user_location` steers every research
web_search and :meth:`jurisdiction_fingerprint` keys any future
jurisdiction-scoped cache, so a typo'd or inconsistently-cased city
silently misroutes both. Construction trims every field and folds the
country to a canonical ``"US"`` / ``"CA"`` code; the state/province is a
canonical code from a closed table; the city stays free text but trimmed
(casefolded only inside the fingerprint).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Canonical state / province tables (code -> display name)
# ---------------------------------------------------------------------------
# The frontend dropdown (and the model, via the op description) store these
# codes; display surfaces render the names. One source of truth for both.

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

CA_PROVINCES: dict[str, str] = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}

# Country storage code -> display form.
COUNTRY_DISPLAY: dict[str, str] = {"US": "USA", "CA": "Canada"}

# Everything accepted and folded to a canonical country code. Keys are
# casefolded on lookup, so "usa" / "United States" / "canada" all resolve.
_COUNTRY_ALIASES: dict[str, str] = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united states": "US", "united states of america": "US", "america": "US",
    "ca": "CA", "can": "CA", "canada": "CA",
}

# Reverse lookup: full state/province name (casefolded) -> code, so the
# model can record "Virginia" and it lands as "VA" — the interview speaks
# display names, the store keeps codes. Codes themselves also resolve.
_NAME_TO_CODE: dict[str, str] = {
    **{name.casefold(): code for code, name in US_STATES.items()},
    **{name.casefold(): code for code, name in CA_PROVINCES.items()},
}


def normalize_country(value: str) -> str:
    """Fold a free-form country string to a canonical ``"US"`` / ``"CA"`` code.

    Returns ``""`` for anything unrecognized so
    :meth:`ProjectProfile.is_complete` can reject it rather than silently
    mis-routing searches.
    """
    key = (value or "").strip().casefold()
    return _COUNTRY_ALIASES.get(key, "")


def normalize_state_or_province(value: str) -> str:
    """Fold a state/province name or code to its canonical two-letter code.

    ``"Virginia"`` → ``"VA"``; ``"va"`` → ``"VA"``; unrecognized input is
    returned trimmed as-is (nothing silently dropped; ``is_complete`` still
    passes only if the *country* is known, and searches degrade gracefully).
    """
    trimmed = (value or "").strip()
    if not trimmed:
        return ""
    by_name = _NAME_TO_CODE.get(trimmed.casefold())
    if by_name:
        return by_name
    upper = trimmed.upper()
    if upper in US_STATES or upper in CA_PROVINCES:
        return upper
    return trimmed


def states_for_country(country: str) -> dict[str, str]:
    """The ``code -> name`` table for a country code (``{}`` if unknown)."""
    code = normalize_country(country)
    if code == "US":
        return US_STATES
    if code == "CA":
        return CA_PROVINCES
    return {}


@dataclass(frozen=True)
class ProjectProfile:
    """One project's identity (city / state-or-province / country / client).

    Frozen and JSON-friendly (``to_dict`` / ``from_dict``). Every field is
    trimmed at construction; ``country`` folds to a canonical ``"US"`` /
    ``"CA"`` code and ``state_or_province`` to a canonical table code where
    recognized (free-form input is preserved trimmed so nothing is silently
    dropped).
    """

    city: str
    state_or_province: str
    country: str
    client_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "city", (self.city or "").strip())
        object.__setattr__(
            self,
            "state_or_province",
            normalize_state_or_province(self.state_or_province),
        )
        object.__setattr__(self, "client_name", (self.client_name or "").strip())
        normalized = normalize_country(self.country)
        object.__setattr__(
            self, "country", normalized or (self.country or "").strip()
        )

    # -- Derived display forms ------------------------------------------------

    @property
    def country_display(self) -> str:
        return COUNTRY_DISPLAY.get(self.country, self.country)

    @property
    def state_display(self) -> str:
        return states_for_country(self.country).get(
            self.state_or_province, self.state_or_province
        )

    def display_line(self) -> str:
        """``"Ashburn, Virginia, USA — Client: ExampleCo"``."""
        return (
            f"{self.city}, {self.state_display}, {self.country_display} "
            f"— Client: {self.client_name}"
        )

    # -- Routing inputs -------------------------------------------------------

    def prompt_format_kwargs(self) -> dict[str, str]:
        """Per-project values for research prompt-template placeholders.

        Display forms throughout — a research prompt says "Ashburn,
        Virginia, USA", not "Ashburn, VA, US" — matching the dummy values
        module registration format-checks templates against.
        """
        return {
            "city": self.city,
            "state_or_province": self.state_display,
            "country": self.country_display,
            "client_name": self.client_name,
        }

    def web_search_user_location(self) -> dict[str, str]:
        """The ``user_location`` dict for the web_search server tool."""
        return {
            "type": "approximate",
            "country": self.country,
            "region": self.state_display,
            "city": self.city,
        }

    def jurisdiction_fingerprint(self) -> str:
        """Stable 16-hex fingerprint of ``country|state|city`` (casefolded)."""
        raw = "|".join(
            part.casefold()
            for part in (self.country, self.state_or_province, self.city)
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    # -- Validation -----------------------------------------------------------

    def is_complete(self) -> bool:
        """True when every field is present and the country is a known code."""
        return bool(
            self.city
            and self.state_or_province
            and self.client_name
            and self.country in COUNTRY_DISPLAY
        )

    # -- Serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, str]:
        return {
            "city": self.city,
            "state_or_province": self.state_or_province,
            "country": self.country,
            "client_name": self.client_name,
        }

    @classmethod
    def from_dict(cls, data: object) -> "ProjectProfile | None":
        """Defensive inverse of :meth:`to_dict`; ``None`` for missing/garbage."""
        if not isinstance(data, dict):
            return None
        profile = cls(
            city=str(data.get("city", "") or ""),
            state_or_province=str(data.get("state_or_province", "") or ""),
            country=str(data.get("country", "") or ""),
            client_name=str(data.get("client_name", "") or ""),
        )
        if not (
            profile.city
            or profile.state_or_province
            or profile.country
            or profile.client_name
        ):
            return None
        return profile
