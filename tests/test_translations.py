"""Tests for Nulleinspeisung translations."""

from __future__ import annotations

import json
from pathlib import Path


def get_all_keys(obj: dict, prefix: str = "") -> set[str]:
    """Get all nested keys from a dict."""
    keys = set()
    for key, value in obj.items():
        full_key = f"{prefix}.{key}" if prefix else key
        keys.add(full_key)
        if isinstance(value, dict):
            keys.update(get_all_keys(value, full_key))
    return keys


def get_all_values(obj: dict) -> list[str]:
    """Get all values from a dict recursively."""
    values = []
    for value in obj.values():
        if isinstance(value, dict):
            values.extend(get_all_values(value))
        else:
            values.append(str(value))
    return values


def test_translation_keys_match() -> None:
    """strings.json, en.json and de.json have exactly the same set of nested keys."""
    base_path = Path(__file__).parent.parent / "custom_components/nulleinspeisung"
    strings_file = base_path / "strings.json"
    en_file = base_path / "translations/en.json"
    de_file = base_path / "translations/de.json"

    with open(strings_file) as f:
        strings_data = json.load(f)
    with open(en_file) as f:
        en_data = json.load(f)
    with open(de_file) as f:
        de_data = json.load(f)

    strings_keys = get_all_keys(strings_data)
    en_keys = get_all_keys(en_data)
    de_keys = get_all_keys(de_data)

    assert strings_keys == en_keys, "strings.json and en.json keys differ"
    assert en_keys == de_keys, "en.json and de.json keys differ"


def test_no_empty_values_in_translations() -> None:
    """No value in de.json or en.json is an empty string."""
    base_path = Path(__file__).parent.parent / "custom_components/nulleinspeisung"
    en_file = base_path / "translations/en.json"
    de_file = base_path / "translations/de.json"

    with open(en_file) as f:
        en_data = json.load(f)
    with open(de_file) as f:
        de_data = json.load(f)

    en_values = get_all_values(en_data)
    de_values = get_all_values(de_data)

    empty_in_en = [v for v in en_values if v == ""]
    empty_in_de = [v for v in de_values if v == ""]

    assert not empty_in_en, "Found empty values in en.json"
    assert not empty_in_de, "Found empty values in de.json"


def test_german_translation_differs_from_english() -> None:
    """The German file is actually German (differs from English value)."""
    base_path = Path(__file__).parent.parent / "custom_components/nulleinspeisung"
    en_file = base_path / "translations/en.json"
    de_file = base_path / "translations/de.json"

    with open(en_file) as f:
        en_data = json.load(f)
    with open(de_file) as f:
        de_data = json.load(f)

    en_values = get_all_values(en_data)
    de_values = get_all_values(de_data)

    different = [en for en, de in zip(en_values, de_values, strict=False) if en != de]
    assert different, "German translation is identical to English"


def test_price_level_states_are_translated_into_real_german() -> None:
    """Price Level is 'Preisniveau' and its five states are German words."""
    base_path = Path(__file__).parent.parent / "custom_components/nulleinspeisung"
    with open(base_path / "translations/de.json") as f:
        de_data = json.load(f)
    level = de_data["entity"]["sensor"]["price_level"]
    assert level["name"] == "Preisniveau"
    assert level["state"] == {
        "very_cheap": "Sehr günstig",
        "cheap": "Günstig",
        "normal": "Normal",
        "expensive": "Teuer",
        "very_expensive": "Sehr teuer",
    }


def test_duplicate_serial_text_names_dtu_and_opendtu_setting() -> None:
    """The duplicate serial error carries the existing DTU and points to OpenDTU."""
    base = Path(__file__).parent.parent / "custom_components/nulleinspeisung"
    for name, setting in (
        ("strings.json", "Settings > DTU Settings"),
        ("translations/en.json", "Settings > DTU Settings"),
        ("translations/de.json", "Einstellungen > DTU-Einstellungen"),
    ):
        data = json.loads((base / name).read_text(encoding="utf-8"))
        text = data["config_subentries"]["dtu"]["error"]["duplicate_serial"]
        assert "{existing}" in text
        assert setting in text
