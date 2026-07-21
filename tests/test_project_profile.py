"""ProjectProfile unit tests: normalization, completeness, routing inputs."""
from __future__ import annotations

from backend.project_profile import (
    ProjectProfile,
    normalize_country,
    normalize_state_or_province,
    states_for_country,
)


def test_country_normalization():
    for raw in ("US", "usa", "U.S.A.", "United States", "america"):
        assert normalize_country(raw) == "US"
    for raw in ("CA", "canada", "Can"):
        assert normalize_country(raw) == "CA"
    assert normalize_country("France") == ""
    assert normalize_country("") == ""


def test_state_normalization_accepts_names_and_codes():
    assert normalize_state_or_province("Virginia") == "VA"
    assert normalize_state_or_province("va") == "VA"
    assert normalize_state_or_province("British Columbia") == "BC"
    # Unrecognized input is preserved trimmed, never dropped.
    assert normalize_state_or_province(" Atlantis ") == "Atlantis"
    assert states_for_country("usa")["IA"] == "Iowa"
    assert states_for_country("nowhere") == {}


def test_profile_normalizes_on_construction_and_reports_completeness():
    profile = ProjectProfile("  Ashburn ", "virginia", "USA", " ExampleCo ")
    assert profile.city == "Ashburn"
    assert profile.state_or_province == "VA"
    assert profile.country == "US"
    assert profile.client_name == "ExampleCo"
    assert profile.is_complete()
    assert profile.state_display == "Virginia"
    assert profile.country_display == "USA"
    assert "Ashburn, Virginia, USA" in profile.display_line()

    assert not ProjectProfile("Ashburn", "VA", "France", "X").is_complete()
    assert not ProjectProfile("", "VA", "USA", "X").is_complete()


def test_routing_inputs_use_display_forms():
    profile = ProjectProfile("Ashburn", "VA", "US", "ExampleCo")
    kwargs = profile.prompt_format_kwargs()
    assert kwargs == {
        "city": "Ashburn",
        "state_or_province": "Virginia",
        "country": "USA",
        "client_name": "ExampleCo",
    }
    location = profile.web_search_user_location()
    assert location == {
        "type": "approximate",
        "country": "US",
        "region": "Virginia",
        "city": "Ashburn",
    }


def test_fingerprint_is_case_insensitive_and_stable():
    a = ProjectProfile("Ashburn", "VA", "US", "X").jurisdiction_fingerprint()
    b = ProjectProfile("ashburn", "va", "us", "Y").jurisdiction_fingerprint()
    c = ProjectProfile("Reston", "VA", "US", "X").jurisdiction_fingerprint()
    assert a == b and a != c and len(a) == 16


def test_from_dict_round_trip_and_garbage():
    profile = ProjectProfile("Ashburn", "VA", "US", "ExampleCo")
    assert ProjectProfile.from_dict(profile.to_dict()) == profile
    assert ProjectProfile.from_dict(None) is None
    assert ProjectProfile.from_dict("nope") is None
    assert ProjectProfile.from_dict({}) is None
    partial = ProjectProfile.from_dict({"city": "Ashburn"})
    assert partial is not None and not partial.is_complete()
