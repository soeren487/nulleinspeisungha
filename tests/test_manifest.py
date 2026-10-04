"""Tests for Nulleinspeisung manifest and HACS configuration."""

from __future__ import annotations

import json
from pathlib import Path


def test_manifest_required_fields() -> None:
    """manifest.json has domain, config_flow, single_config_entry, and version."""
    base_path = Path(__file__).parent.parent
    manifest_file = base_path / "custom_components/nulleinspeisung/manifest.json"

    with open(manifest_file) as f:
        manifest = json.load(f)

    assert manifest.get("domain") == "nulleinspeisung"
    assert manifest.get("config_flow") is True
    assert manifest.get("single_config_entry") is True
    assert "version" in manifest


def test_hacs_json_exists_and_has_name() -> None:
    """hacs.json exists at the repo root and has a name."""
    base_path = Path(__file__).parent.parent
    hacs_file = base_path / "hacs.json"

    assert hacs_file.exists(), "hacs.json does not exist at repo root"

    with open(hacs_file) as f:
        hacs_config = json.load(f)

    assert "name" in hacs_config, "hacs.json does not have a name field"
    assert hacs_config["name"] == "Nulleinspeisung"
