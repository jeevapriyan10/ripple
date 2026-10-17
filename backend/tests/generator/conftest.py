"""Pytest fixtures for generator tests."""

import pytest

from ripple.generator.renderers import register_all_renderers


@pytest.fixture(autouse=True)
def setup_renderers() -> None:
    """Ensure pure renderers are registered for all networks before each test."""
    register_all_renderers()
