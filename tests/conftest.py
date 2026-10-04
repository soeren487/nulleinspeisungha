"""Shared test fixtures."""

import pytest

import custom_components  # noqa: F401  # make the package importable for the loader


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the test Home Assistant load integrations from custom_components."""
