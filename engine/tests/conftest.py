"""Shared test setup."""

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_libraries(tmp_path, monkeypatch):
    """Tests never read or write the user's preset / text style libraries (a default text style
    there would change every new caption track)."""
    monkeypatch.setenv("LUMINA_TEXT_STYLES", str(tmp_path / "text_styles.json"))
    if "LUMINA_PRESETS" not in os.environ:
        monkeypatch.setenv("LUMINA_PRESETS", str(tmp_path / "presets.json"))
