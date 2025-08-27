"""Test critical configuration validation - only the stuff that prevents bugs."""

from ecolit.config import load_config


class TestConfigValidation:
    """Test configuration validation that prevents production failures."""

    def test_config_loads_without_crashing(self):
        """Test that config loading doesn't crash with defaults."""
        config = load_config()
        assert config is not None
        assert "network" in config
        assert "ev_charging" in config
