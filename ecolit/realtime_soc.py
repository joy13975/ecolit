"""Real-time SoC estimator using power integration.

Provides more frequent Home Battery SoC estimates by integrating battery power
flow between official SoC readings (which update every ~30 minutes).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SoCEstimate:
    """Represents a SoC estimate with metadata."""

    timestamp: datetime
    estimated_soc: float
    confidence: float
    source: str
    official_soc: float | None = None
    time_since_official_hours: float | None = None
    cumulative_energy_wh: float = 0.0


class RealtimeSoCEstimator:
    """Real-time SoC estimator using power integration."""

    def __init__(self, battery_capacity_kwh: float):
        """Initialize the SoC estimator.

        Args:
            battery_capacity_kwh: Battery capacity in kWh
        """
        self.battery_capacity_wh = battery_capacity_kwh * 1000  # Convert to Wh
        self.official_soc = None
        self.official_soc_time = None
        self.estimated_soc = None
        self.last_power_update = None
        self.cumulative_energy_wh = 0.0
        self.power_history = []

        # Configuration
        self.max_extrapolation_hours = 2.0  # Max time to extrapolate without official update
        self.max_history_hours = 1.0  # Keep power history for trend analysis

        logger.info(
            f"🔋 Real-time SoC Estimator initialized (capacity: {battery_capacity_kwh:.1f}kWh)"
        )

    def update_official_soc(self, soc_percent: float, timestamp: datetime | None = None) -> None:
        """Update with official SoC reading - resets baseline.

        Args:
            soc_percent: Official SoC percentage (0-100)
            timestamp: Time of reading (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Log SoC change if we had a previous reading
        if self.official_soc is not None and self.official_soc_time is not None:
            time_elapsed = (timestamp - self.official_soc_time).total_seconds() / 3600  # hours
            soc_change = soc_percent - self.official_soc

            if abs(soc_change) >= 0.1:  # Only log significant changes
                logger.info(
                    f"📊 Official Home Battery SoC: {self.official_soc:.1f}% → {soc_percent:.1f}% "
                    f"({soc_change:+.1f}% in {time_elapsed:.1f}h)"
                )

        self.official_soc = soc_percent
        self.official_soc_time = timestamp
        self.estimated_soc = soc_percent  # Reset estimate to match official
        self.cumulative_energy_wh = 0.0  # Reset cumulative tracking

    def update_power(self, power_w: float, timestamp: datetime | None = None) -> None:
        """Update with current battery power reading.

        Args:
            power_w: Battery power in watts (+ charging, - discharging)
            timestamp: Time of reading (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Store power reading
        self.power_history.append((timestamp, power_w))

        # Keep only recent power history
        cutoff = timestamp - timedelta(hours=self.max_history_hours)
        self.power_history = [(t, p) for t, p in self.power_history if t > cutoff]

        # Calculate energy change since last update
        if self.last_power_update is not None and len(self.power_history) >= 2:
            time_elapsed_hours = (timestamp - self.last_power_update).total_seconds() / 3600

            if time_elapsed_hours > 0:
                # Use average power over the interval to smooth out fluctuations
                current_power = power_w
                previous_power = (
                    self.power_history[-2][1] if len(self.power_history) >= 2 else power_w
                )
                avg_power = (current_power + previous_power) / 2

                energy_change_wh = avg_power * time_elapsed_hours
                self.cumulative_energy_wh += energy_change_wh

                # Note: Removed decay factor application to maintain truly cumulative behavior
                # RT estimates should only decrease when corrected by new official readings

        self.last_power_update = timestamp

    def get_estimated_soc(self, timestamp: datetime | None = None) -> SoCEstimate:
        """Get current SoC estimate with confidence assessment.

        Args:
            timestamp: Time for estimate (defaults to now)

        Returns:
            SoCEstimate with current best estimate and metadata
        """
        if timestamp is None:
            timestamp = datetime.now()

        # No data available
        if self.official_soc is None:
            return SoCEstimate(
                timestamp=timestamp, estimated_soc=0.0, confidence=0.0, source="no_data"
            )

        # Calculate time since official reading
        time_since_official = (timestamp - self.official_soc_time).total_seconds() / 3600

        # Don't extrapolate too far from official reading
        if time_since_official > self.max_extrapolation_hours:
            return SoCEstimate(
                timestamp=timestamp,
                estimated_soc=self.official_soc,
                confidence=0.1,
                source=f"official_too_old_{time_since_official:.1f}h",
                official_soc=self.official_soc,
                time_since_official_hours=time_since_official,
                cumulative_energy_wh=self.cumulative_energy_wh,
            )

        # Calculate SoC from power integration
        if abs(self.cumulative_energy_wh) > 1.0:  # Only if significant energy change
            # Current estimated capacity
            current_capacity_wh = (self.official_soc / 100) * self.battery_capacity_wh
            estimated_capacity_wh = current_capacity_wh + self.cumulative_energy_wh
            estimated_soc = (estimated_capacity_wh / self.battery_capacity_wh) * 100

            # Clamp to reasonable range
            estimated_soc = max(0, min(100, estimated_soc))

            # Confidence decreases with time and energy drift magnitude
            time_confidence = max(0.3, 1.0 - (time_since_official * 0.15))  # Decay over time
            energy_confidence = max(
                0.5, 1.0 - abs(self.cumulative_energy_wh / (self.battery_capacity_wh * 0.05))
            )  # Decay with large changes

            confidence = time_confidence * energy_confidence

            return SoCEstimate(
                timestamp=timestamp,
                estimated_soc=estimated_soc,
                confidence=confidence,
                source=f"power_integration_{time_since_official:.1f}h",
                official_soc=self.official_soc,
                time_since_official_hours=time_since_official,
                cumulative_energy_wh=self.cumulative_energy_wh,
            )
        else:
            # No significant power changes yet, use official reading
            confidence = max(0.5, 1.0 - (time_since_official * 0.1))

            return SoCEstimate(
                timestamp=timestamp,
                estimated_soc=self.official_soc,
                confidence=confidence,
                source=f"official_reading_{time_since_official:.1f}h",
                official_soc=self.official_soc,
                time_since_official_hours=time_since_official,
                cumulative_energy_wh=self.cumulative_energy_wh,
            )

    def get_charging_info(self) -> dict[str, Any]:
        """Get charging rate and time estimates.

        Returns:
            Dictionary with charging rate, time to full, and recent power average
        """
        if len(self.power_history) < 2:
            return {
                "charging_rate_percent_per_hour": 0.0,
                "time_to_full_hours": None,
                "average_power_w": 0.0,
                "is_charging": False,
            }

        # Calculate recent average power (last 5 readings)
        recent_powers = [p for t, p in self.power_history[-5:]]
        avg_power_w = sum(recent_powers) / len(recent_powers)

        # Convert to SoC change rate (%/hour)
        charging_rate_percent_per_hour = (avg_power_w / self.battery_capacity_wh) * 100

        # Estimate time to full
        current_estimate = self.get_estimated_soc()
        time_to_full_hours = None

        if (
            avg_power_w > 10 and current_estimate.estimated_soc is not None
        ):  # Only if actively charging
            remaining_percent = 100 - current_estimate.estimated_soc
            if remaining_percent > 0.1:
                time_to_full_hours = remaining_percent / charging_rate_percent_per_hour

        return {
            "charging_rate_percent_per_hour": charging_rate_percent_per_hour,
            "time_to_full_hours": time_to_full_hours,
            "average_power_w": avg_power_w,
            "is_charging": avg_power_w > 10,
            "is_discharging": avg_power_w < -10,
        }

    def get_time_to_target_soc(
        self, target_soc_percent: float, force_discharge: bool = False
    ) -> float | None:
        """Calculate time to reach target SOC percentage.

        Args:
            target_soc_percent: Target SOC percentage (0-100)
            force_discharge: If True, calculate for discharge even if charging

        Returns:
            Time to target in hours, or None if cannot estimate
        """
        if len(self.power_history) < 1:
            return None

        # Calculate recent average power (last 5 readings, or single reading if only one available)
        recent_powers = [p for t, p in self.power_history[-5:]]
        avg_power_w = sum(recent_powers) / len(recent_powers)

        # Skip if power is too small to calculate meaningful time
        if abs(avg_power_w) <= 10:
            return None

        # Convert to SoC change rate (%/hour)
        soc_rate_percent_per_hour = (avg_power_w / self.battery_capacity_wh) * 100

        # Get current SOC estimate
        current_estimate = self.get_estimated_soc()
        if current_estimate.estimated_soc is None:
            return None

        # For charging (positive power): calculate time to reach higher target
        if avg_power_w > 10 and not force_discharge:
            remaining_percent = target_soc_percent - current_estimate.estimated_soc
            if remaining_percent <= 0:
                return 0.0  # Already at or above target
            return remaining_percent / soc_rate_percent_per_hour

        # For discharging (negative power): calculate time to reach lower target
        elif avg_power_w < -10:
            remaining_percent = current_estimate.estimated_soc - target_soc_percent
            if remaining_percent <= 0:
                return 0.0  # Already at or below target
            return remaining_percent / abs(soc_rate_percent_per_hour)

        return None

    def get_status_summary(self) -> dict[str, Any]:
        """Get comprehensive status summary for logging/debugging.

        Returns:
            Dictionary with all current estimator state
        """
        estimate = self.get_estimated_soc()
        charging_info = self.get_charging_info()

        return {
            "estimated_soc": estimate.estimated_soc,
            "confidence": estimate.confidence,
            "source": estimate.source,
            "official_soc": self.official_soc,
            "cumulative_energy_wh": self.cumulative_energy_wh,
            "time_since_official_hours": estimate.time_since_official_hours,
            "charging_rate_percent_per_hour": charging_info["charging_rate_percent_per_hour"],
            "time_to_full_hours": charging_info["time_to_full_hours"],
            "power_readings_count": len(self.power_history),
            "battery_capacity_kwh": self.battery_capacity_wh / 1000,
        }
