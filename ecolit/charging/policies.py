"""EV charging control policies."""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EnergyMetrics:
    """Current energy system metrics for charging decisions."""

    battery_soc: float | None = None
    battery_power: int | None = None  # +charging, -discharging
    grid_power_flow: int | None = None  # +import, -export (may be calculated/unreliable)
    solar_power: int | None = None


class ChargingPolicy(ABC):
    """Abstract base class for EV charging policies."""

    def __init__(self, config: dict[str, Any]):
        """Initialize policy with configuration."""
        self.config = config
        self.max_amps = config.get("max_amps", 20)

        # Amp adjustment settings
        amp_adjustments = config.get("amp_adjustments", {})
        self.increase_step = amp_adjustments.get("increase_step", 1)
        self.decrease_step = amp_adjustments.get("decrease_step", 2)

    @abstractmethod
    def calculate_target_amps(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """Calculate target charging amps based on current state.

        Args:
            current_amps: Current EV charging amperage
            metrics: Current energy system metrics

        Returns:
            Target amperage (0 to max_amps)
        """

    @abstractmethod
    def get_name(self) -> str:
        """Return policy name for logging."""

    def _clamp_amps(self, amps: int) -> int:
        """Ensure amps stay within safe bounds."""
        return max(0, min(amps, self.max_amps))


class EcoPolicy(ChargingPolicy):
    """ECO policy: Battery-feedback control with dual-phase operation."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        eco_config = config.get("eco", {})

        # Simple battery feedback control parameters
        self.target_battery_soc = eco_config.get("target_soc", 99.0)  # Default 99%

        # Common battery feedback parameters (shared across all policies)
        self.battery_charging_threshold = config.get(
            "battery_charging_threshold", 100
        )  # W threshold for "charging"
        self.adjustment_interval = config.get(
            "adjustment_interval", 30
        )  # 30 seconds between adjustments
        self.amp_step = config.get("amp_step", 1)  # Amp adjustment per step

        # State tracking
        self.last_adjustment_time = 0.0

        # Legacy grid-flow fallback (preserved for compatibility)
        self.export_threshold = eco_config.get("export_threshold", 50)

    def calculate_target_amps(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """Simple battery feedback control: When homeSOC ≥ target, respond to battery power flow."""
        # If battery SOC is below target, prioritize battery charging
        if metrics.battery_soc < self.target_battery_soc:
            logger.debug(
                f"ECO: Battery SOC {metrics.battery_soc:.1f}% < {self.target_battery_soc}%, stop EV charging"
            )
            return 0  # Stop charging to prioritize home battery

        # At target SOC - use battery power flow to control EV charging
        target_amps = current_amps

        if metrics.battery_power > self.battery_charging_threshold:
            # Home battery is charging - can increase EV charging
            target_amps = min(self.max_amps, current_amps + self.amp_step)
            action = "increase"
            reason = f"charging at {metrics.battery_power}W"
        elif metrics.battery_power < -self.battery_charging_threshold:
            # Home battery is discharging - reduce EV charging
            # If we're at minimum, stop charging rather than going below minimum
            if current_amps <= 6:  # Tesla minimum is 6A
                target_amps = 0  # Stop charging
                action = "stop"
            else:
                target_amps = max(6, current_amps - self.amp_step)  # Don't go below 6A
                action = "reduce"
            reason = f"discharging at {metrics.battery_power}W"
        else:
            # Home battery is flat (within threshold) - keep current setting
            action = "maintain"
            reason = f"flat at {metrics.battery_power}W"

        # Log the decision
        if target_amps != current_amps:
            logger.debug(f"ECO: Battery {reason}, {action} EV charging to {target_amps}A")

        return target_amps

    def _legacy_grid_control(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """Legacy grid-flow based control (preserved for compatibility)."""
        if metrics.grid_power_flow is None:
            logger.debug("No grid flow data - maintaining current amps")
            return current_amps

        # Grid export is negative, import is positive
        if metrics.grid_power_flow < -self.export_threshold:
            target_amps = current_amps + self.increase_step
            logger.debug(
                f"ECO: Legacy mode - exporting {abs(metrics.grid_power_flow)}W > {self.export_threshold}W, increase to {target_amps}A"
            )
        elif metrics.grid_power_flow >= 0:
            target_amps = current_amps - self.decrease_step
            logger.debug(
                f"ECO: Legacy mode - grid flow {metrics.grid_power_flow}W ≥ 0, decrease to {target_amps}A"
            )
        else:
            target_amps = current_amps
            logger.debug(
                f"ECO: Legacy mode - exporting {abs(metrics.grid_power_flow)}W < {self.export_threshold}W, maintain {target_amps}A"
            )

        return self._clamp_amps(target_amps)

    def get_name(self) -> str:
        return "ECO"


class HurryPolicy(ChargingPolicy):
    """HURRY policy: Battery-feedback with lower SOC target and higher power tolerance."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        hurry_config = config.get("hurry", {})

        # Simple battery feedback control parameters
        self.target_battery_soc = hurry_config.get("target_soc", 90.0)  # Lower than ECO

        # Common battery feedback parameters (shared across all policies)
        self.battery_charging_threshold = config.get(
            "battery_charging_threshold", 100
        )  # W threshold for "charging"
        self.adjustment_interval = config.get(
            "adjustment_interval", 30
        )  # 30 seconds between adjustments
        self.amp_step = config.get("amp_step", 1)  # Amp adjustment per step

        # State tracking
        self.last_adjustment_time = 0.0

        # Legacy parameters (preserved for compatibility)
        self.max_import = hurry_config.get("max_import", 1000)
        self.export_threshold = config.get("eco", {}).get("export_threshold", 50)

    def calculate_target_amps(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """HURRY logic: Simple battery-feedback with lower SOC target, fallback to grid flow."""
        current_time = time.time()

        # Use simple battery feedback control if we have battery data
        if metrics.battery_soc is not None and metrics.battery_power is not None:
            return self._simple_battery_feedback(current_amps, metrics, current_time)

        # Fallback to legacy grid-flow based control
        logger.debug("HURRY: No battery data available, using legacy grid-flow control")
        return self._legacy_grid_control(current_amps, metrics)

    def _simple_battery_feedback(
        self, current_amps: int, metrics: EnergyMetrics, current_time: float
    ) -> int:
        """Simple battery feedback control for HURRY mode: Lower SOC target than ECO."""
        # If battery SOC is below target, prioritize battery charging
        if metrics.battery_soc < self.target_battery_soc:
            logger.debug(
                f"HURRY: Battery SOC {metrics.battery_soc:.1f}% < {self.target_battery_soc}%, no EV charging"
            )
            return 0

        # Check if enough time has passed since last adjustment
        if current_time - self.last_adjustment_time < self.adjustment_interval:
            return current_amps

        # At target SOC - use battery power flow to control EV charging
        target_amps = current_amps

        if metrics.battery_power > self.battery_charging_threshold:
            # Home battery is charging - can increase EV charging
            target_amps = min(self.max_amps, current_amps + self.amp_step)
            action = "increase"
            reason = f"charging at {metrics.battery_power}W"
        elif metrics.battery_power < -self.battery_charging_threshold:
            # Home battery is discharging - reduce EV charging
            # If we're at minimum, stop charging rather than going below minimum
            if current_amps <= 6:  # Tesla minimum is 6A
                target_amps = 0  # Stop charging
                action = "stop"
            else:
                target_amps = max(6, current_amps - self.amp_step)  # Don't go below 6A
                action = "reduce"
            reason = f"discharging at {metrics.battery_power}W"
        else:
            # Home battery is flat (within threshold) - keep current setting
            action = "maintain"
            reason = f"flat at {metrics.battery_power}W"

        # Only log and update if there's a change
        if target_amps != current_amps or action == "maintain":
            logger.debug(f"HURRY: Battery {reason}, {action} EV charging to {target_amps}A")
            self.last_adjustment_time = current_time

        return target_amps

    def _legacy_grid_control(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """Legacy grid-flow based control (preserved for compatibility)."""
        if metrics.grid_power_flow is None:
            logger.debug("No grid flow data - maintaining current amps")
            return current_amps

        if metrics.grid_power_flow < -self.export_threshold:
            target_amps = current_amps + self.increase_step
            logger.debug(
                f"HURRY: Legacy mode - exporting {abs(metrics.grid_power_flow)}W > {self.export_threshold}W, increase to {target_amps}A"
            )
        elif metrics.grid_power_flow <= self.max_import:
            target_amps = current_amps + self.increase_step
            logger.debug(
                f"HURRY: Legacy mode - grid flow {metrics.grid_power_flow}W ≤ {self.max_import}W limit, increase to {target_amps}A"
            )
        else:
            target_amps = current_amps - self.decrease_step
            logger.debug(
                f"HURRY: Legacy mode - importing {metrics.grid_power_flow}W > {self.max_import}W limit, decrease to {target_amps}A"
            )

        return self._clamp_amps(target_amps)

    def get_name(self) -> str:
        return "HURRY"


class EmergencyPolicy(ChargingPolicy):
    """EMERGENCY policy: Charge at max amps immediately."""

    def calculate_target_amps(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """EMERGENCY logic: Always charge at maximum safe amperage."""
        target_amps = self.max_amps
        logger.debug(f"EMERGENCY: Charging at maximum {target_amps}A")
        return target_amps

    def get_name(self) -> str:
        return "EMERGENCY"


def create_policy(policy_name: str, config: dict[str, Any]) -> ChargingPolicy:
    """Factory function to create charging policies."""
    policies = {
        "eco": EcoPolicy,
        "hurry": HurryPolicy,
        "emergency": EmergencyPolicy,
    }

    policy_class = policies.get(policy_name.lower())
    if not policy_class:
        raise ValueError(
            f"Unknown charging policy: {policy_name}. Available: {list(policies.keys())}"
        )

    return policy_class(config)
