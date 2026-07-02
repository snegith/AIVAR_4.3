"""Shared pytest fixtures for the adversarial pattern detector test suite."""

import pytest

from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Isolate settings cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Fresh settings instance for unit tests."""
    return Settings()
