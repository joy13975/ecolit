"""EV charging controller with policy management and rate limiting."""

import logging
import time
from typing import Any

from .policies import EnergyMetrics, create_policy

logger = logging.getLogger(__name__)


class EVChargingController:
    """Main EV charging controller with policy management and safety."""

    def __init__(self, config: dict[str, Any]):
        """Initialize the EV charging controller."""
        self.config = config.get("ev_charging", {})
        self.enabled = self.config.get("enabled", False)

        if not self.enabled:
            logger.info("EV charging controller disabled")
            return

        # Initialize policy
        policy_name = self.config.get("policy", "eco")
        self.policy = create_policy(policy_name, self.config)

        # Rate limiting configuration
        self.adjustment_interval = self.config.get("adjustment_interval", 30)  # seconds
        self.measurement_interval = self.config.get("measurement_interval", 10)  # seconds

        # Safety limits
        self.max_amps = self.config.get("max_amps", 20)
        self.min_amps = 6  # Tesla minimum charging amps

        # State tracking - start at minimum amps, not 0
        self.current_amps = self.min_amps  # Start at minimum viable charging level
        self.target_amps = self.min_amps
        self.last_adjustment_time = 0.0
        self.last_measurement_time = 0.0

        logger.info(
            f"EV charging controller initialized: policy={self.policy.get_name()}, max_amps={self.max_amps}"
        )

    def is_enabled(self) -> bool:
        """Check if EV charging is enabled."""
        return self.enabled

    def get_current_policy(self) -> str:
        """Get current policy name."""
        return self.policy.get_name() if self.enabled else "DISABLED"

    def should_measure(self) -> bool:
        """Check if it's time to take new measurements."""
        if not self.enabled:
            return False

        current_time = time.time()
        return (current_time - self.last_measurement_time) >= self.measurement_interval

    def should_adjust(self) -> bool:
        """Check if it's time to make amperage adjustments."""
        if not self.enabled:
            return False

        current_time = time.time()
        return (current_time - self.last_adjustment_time) >= self.adjustment_interval

    def calculate_charging_amps(self, metrics: EnergyMetrics) -> int:
        """Calculate target charging amps based on current policy and metrics."""
        if not self.enabled:
            return 0

        current_time = time.time()

        # Always update measurement time when we receive new metrics
        if self.should_measure():
            self.last_measurement_time = current_time

        # Only adjust amps if enough time has passed
        if self.should_adjust():
            self.target_amps = self.policy.calculate_target_amps(self.current_amps, metrics)

            # Safety check - enforce min/max limits
            # If target is between 1-5A, round to 0 (stop) or 6 (minimum)
            if 0 < self.target_amps < self.min_amps:
                self.target_amps = self.min_amps  # Round up to minimum
            # Never exceed max_amps
            self.target_amps = min(self.target_amps, self.max_amps)

            # Only update if there's a change
            if self.target_amps != self.current_amps:
                logger.info(
                    f"🔌 EV CHARGING: {self.policy.get_name()} policy → {self.current_amps}A to {self.target_amps}A"
                )
                self.current_amps = self.target_amps
                self.last_adjustment_time = current_time
            else:
                logger.debug(
                    f"EV charging: {self.policy.get_name()} policy maintains {self.current_amps}A"
                )
        else:
            # Rate limited - return current value
            time_until_next = self.adjustment_interval - (current_time - self.last_adjustment_time)
            logger.debug(f"EV charging: Rate limited, next adjustment in {time_until_next:.1f}s")

        return self.current_amps

    def get_status_info(self) -> dict[str, Any]:
        """Get current controller status for logging/debugging."""
        if not self.enabled:
            return {"enabled": False}

        current_time = time.time()
        return {
            "enabled": True,
            "policy": self.policy.get_name(),
            "current_amps": self.current_amps,
            "target_amps": self.target_amps,
            "max_amps": self.max_amps,
            "time_since_last_adjustment": current_time - self.last_adjustment_time,
            "next_adjustment_in": max(
                0, self.adjustment_interval - (current_time - self.last_adjustment_time)
            ),
        }

    async def update_policy(self, policy_name: str) -> bool:
        """Update the charging policy at runtime."""
        if not self.enabled:
            logger.warning("Cannot update policy - EV charging disabled")
            return False

        try:
            new_policy = create_policy(policy_name, self.config)
            old_policy_name = self.policy.get_name()
            self.policy = new_policy
            logger.info(f"EV charging policy updated: {old_policy_name} → {policy_name}")
            return True
        except ValueError as e:
            logger.error(f"Failed to update policy: {e}")
            return False
