"""Test critical charging policy edge cases and factory logic."""

import pytest

from ecolit.charging.policies import (
    EcoPolicy,
    EmergencyPolicy,
    HurryPolicy,
    create_policy,
)


class TestPolicyFactory:
    """Test policy factory edge cases that could break at runtime."""

    def test_create_valid_policies(self):
        """Test factory creates all valid policy types."""
        eco = create_policy("eco", {"export_threshold": 100})
        hurry = create_policy("hurry", {"max_import": 500})
        emergency = create_policy("emergency", {})

        assert isinstance(eco, EcoPolicy)
        assert isinstance(hurry, HurryPolicy)
        assert isinstance(emergency, EmergencyPolicy)

    def test_case_insensitive_policy_names(self):
        """Test that policy names are case insensitive."""
        policy1 = create_policy("ECO", {"export_threshold": 50})
        policy2 = create_policy("eco", {"export_threshold": 50})
        policy3 = create_policy("Eco", {"export_threshold": 50})

        assert all(isinstance(p, EcoPolicy) for p in [policy1, policy2, policy3])

    def test_invalid_policy_raises_error(self):
        """Test that invalid policy names raise proper errors."""
        with pytest.raises(ValueError, match="Unknown charging policy"):
            create_policy("invalid_policy", {})
