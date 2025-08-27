"""EV charging control policies."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EnergyMetrics:
    """Current energy system metrics for charging decisions."""

    battery_soc: float | None = None
    battery_power: int | None = None  # +charging, -discharging
    grid_power_flow: int | None = None  # +import, -export
    solar_power: int | None = None


class ChargingPolicy(ABC):
    """Abstract base class for EV charging policies."""

    def __init__(self, config: dict[str, Any]):
        """Initialize policy with configuration."""
        self.config = config
        self.max_amps = config.get("max_amps", 20)

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
    """ECO policy: Export-following, home battery priority."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.export_threshold = config.get("eco", {}).get("export_threshold", 50)

    def calculate_target_amps(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """ECO logic: Increase on export >threshold, decrease on no export."""
        if metrics.grid_power_flow is None:
            logger.debug("No grid flow data - maintaining current amps")
            return current_amps

        # Grid export is negative, import is positive
        if metrics.grid_power_flow < -self.export_threshold:
            # Exporting > threshold - can increase charging
            target_amps = current_amps + 1
            logger.debug(
                f"ECO: Exporting {abs(metrics.grid_power_flow)}W > {self.export_threshold}W, increase to {target_amps}A"
            )
        elif metrics.grid_power_flow >= 0:
            # Not exporting (importing or balanced) - decrease charging
            target_amps = current_amps - 2
            logger.debug(
                f"ECO: Grid flow {metrics.grid_power_flow}W ≥ 0, decrease to {target_amps}A"
            )
        else:
            # Exporting but less than threshold - maintain current
            target_amps = current_amps
            logger.debug(
                f"ECO: Exporting {abs(metrics.grid_power_flow)}W < {self.export_threshold}W, maintain {target_amps}A"
            )

        return self._clamp_amps(target_amps)

    def get_name(self) -> str:
        return "ECO"


class HurryPolicy(ChargingPolicy):
    """HURRY policy: Allow grid import up to configured limit."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.max_import = config.get("hurry", {}).get("max_import", 1000)
        self.export_threshold = config.get("eco", {}).get(
            "export_threshold", 50
        )  # Reuse ECO threshold

    def calculate_target_amps(self, current_amps: int, metrics: EnergyMetrics) -> int:
        """HURRY logic: Allow import up to max_import, otherwise like ECO."""
        if metrics.grid_power_flow is None:
            logger.debug("No grid flow data - maintaining current amps")
            return current_amps

        if metrics.grid_power_flow < -self.export_threshold:
            # Exporting > threshold - can increase charging
            target_amps = current_amps + 1
            logger.debug(
                f"HURRY: Exporting {abs(metrics.grid_power_flow)}W > {self.export_threshold}W, increase to {target_amps}A"
            )
        elif metrics.grid_power_flow <= self.max_import:
            # Importing but within limit - can still increase (but more cautiously)
            target_amps = current_amps + 1
            logger.debug(
                f"HURRY: Grid flow {metrics.grid_power_flow}W ≤ {self.max_import}W limit, increase to {target_amps}A"
            )
        else:
            # Importing too much - decrease charging
            target_amps = current_amps - 2
            logger.debug(
                f"HURRY: Importing {metrics.grid_power_flow}W > {self.max_import}W limit, decrease to {target_amps}A"
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
