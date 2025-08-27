#!/usr/bin/env python3
"""
Real-time SoC estimator using power integration.

Since alternative ECHONET properties don't provide more frequent updates,
this creates a real-time SoC estimate by integrating battery power flow
between official SoC readings.

Usage:
- Run alongside your main system
- Provides SoC estimates every few seconds
- Self-calibrates when official SoC updates
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class RealtimeSoCEstimator:
    """Real-time SoC estimator using power integration."""

    def __init__(self, battery_capacity_wh: float = 12700):
        self.battery_capacity_wh = battery_capacity_wh
        self.official_soc = None
        self.official_soc_time = None
        self.estimated_soc = None
        self.last_power_update = None
        self.cumulative_energy_wh = 0.0
        self.power_history = []

        # Configuration
        self.calibration_decay = 0.98  # Slowly decay estimates to prevent drift
        self.max_extrapolation_hours = 2.0  # Max time to extrapolate without official update

        logger.info("🔋 Real-time SoC Estimator initialized")
        logger.info(f"   Battery capacity: {battery_capacity_wh / 1000:.1f}kWh")
        logger.info(f"   Max extrapolation: {self.max_extrapolation_hours:.1f}h")

    def update_official_soc(self, soc_percent: float, timestamp: datetime | None = None):
        """Update with official SoC reading - resets baseline."""
        if timestamp is None:
            timestamp = datetime.now()

        # Log SoC change if we had a previous reading
        if self.official_soc is not None and self.official_soc_time is not None:
            time_elapsed = (timestamp - self.official_soc_time).total_seconds() / 3600  # hours
            soc_change = soc_percent - self.official_soc
            logger.info(
                f"📊 Official SoC update: {self.official_soc:.1f}% → {soc_percent:.1f}% ({soc_change:+.1f}% in {time_elapsed:.1f}h)"
            )
        else:
            logger.info(f"📊 Initial official SoC: {soc_percent:.1f}%")

        self.official_soc = soc_percent
        self.official_soc_time = timestamp
        self.estimated_soc = soc_percent  # Reset estimate to match official
        self.cumulative_energy_wh = 0.0  # Reset cumulative tracking

    def update_power(self, power_w: float, timestamp: datetime | None = None):
        """Update with current battery power reading."""
        if timestamp is None:
            timestamp = datetime.now()

        # Store power reading
        self.power_history.append((timestamp, power_w))

        # Keep only recent power history (last hour)
        cutoff = timestamp - timedelta(hours=1)
        self.power_history = [(t, p) for t, p in self.power_history if t > cutoff]

        # Calculate energy change since last update
        if self.last_power_update is not None:
            time_elapsed_hours = (timestamp - self.last_power_update).total_seconds() / 3600

            if time_elapsed_hours > 0:
                # Use average power over the interval
                avg_power = (
                    power_w
                    + (self.power_history[-2][1] if len(self.power_history) >= 2 else power_w)
                ) / 2
                energy_change_wh = avg_power * time_elapsed_hours
                self.cumulative_energy_wh += energy_change_wh

                # Apply decay to prevent unbounded drift
                self.cumulative_energy_wh *= self.calibration_decay

        self.last_power_update = timestamp

    def get_estimated_soc(self, timestamp: datetime | None = None) -> dict[str, Any]:
        """Get current SoC estimate with confidence assessment."""
        if timestamp is None:
            timestamp = datetime.now()

        result = {
            "timestamp": timestamp,
            "estimated_soc": None,
            "confidence": 0.0,
            "source": "no_data",
            "official_soc": self.official_soc,
            "time_since_official_hours": None,
            "cumulative_energy_wh": self.cumulative_energy_wh,
        }

        if self.official_soc is None:
            return result

        # Calculate time since official reading
        time_since_official = (timestamp - self.official_soc_time).total_seconds() / 3600
        result["time_since_official_hours"] = time_since_official

        # Don't extrapolate too far
        if time_since_official > self.max_extrapolation_hours:
            result["estimated_soc"] = self.official_soc
            result["confidence"] = 0.1
            result["source"] = f"official_too_old_{time_since_official:.1f}h"
            return result

        # Calculate SoC from power integration
        if self.cumulative_energy_wh != 0:
            # Current estimated capacity
            current_capacity_wh = (self.official_soc / 100) * self.battery_capacity_wh
            estimated_capacity_wh = current_capacity_wh + self.cumulative_energy_wh
            estimated_soc = (estimated_capacity_wh / self.battery_capacity_wh) * 100

            # Clamp to reasonable range
            estimated_soc = max(0, min(100, estimated_soc))

            # Confidence decreases with time and energy drift
            time_confidence = max(0.3, 1.0 - (time_since_official * 0.2))  # Decay over time
            energy_confidence = max(
                0.5, 1.0 - abs(self.cumulative_energy_wh / (self.battery_capacity_wh * 0.1))
            )  # Decay with large energy changes

            result["estimated_soc"] = estimated_soc
            result["confidence"] = time_confidence * energy_confidence
            result["source"] = f"power_integration_{time_since_official:.1f}h"

        else:
            # No power changes yet, use official reading
            result["estimated_soc"] = self.official_soc
            result["confidence"] = max(0.5, 1.0 - (time_since_official * 0.1))
            result["source"] = f"official_reading_{time_since_official:.1f}h"

        return result

    def get_charging_rate_estimate(self) -> dict[str, Any]:
        """Estimate current charging rate and time to full."""
        if len(self.power_history) < 2:
            return {"charging_rate_percent_per_hour": 0, "time_to_full_hours": None}

        # Calculate recent average power
        recent_powers = [p for t, p in self.power_history[-10:]]  # Last 10 readings
        avg_power_w = sum(recent_powers) / len(recent_powers)

        # Convert to SoC change rate
        charging_rate_percent_per_hour = (avg_power_w / self.battery_capacity_wh) * 100

        # Estimate time to full
        current_estimate = self.get_estimated_soc()
        time_to_full_hours = None

        if avg_power_w > 0 and current_estimate["estimated_soc"] is not None:
            remaining_percent = 100 - current_estimate["estimated_soc"]
            if remaining_percent > 0:
                time_to_full_hours = remaining_percent / charging_rate_percent_per_hour

        return {
            "charging_rate_percent_per_hour": charging_rate_percent_per_hour,
            "time_to_full_hours": time_to_full_hours,
            "average_power_w": avg_power_w,
        }


def demo_realtime_estimator():
    """Demonstrate real-time SoC estimation."""
    logger.info("🔋 REAL-TIME SOC ESTIMATOR DEMONSTRATION")
    logger.info("=" * 60)

    estimator = RealtimeSoCEstimator()

    # Simulate realistic charging scenario
    scenarios = [
        # (minutes, official_soc, power_w, description)
        (0, 34.2, 450, "Initial reading - battery charging"),
        (1, None, 470, "1min: Power increased"),
        (2, None, 485, "2min: More charging power"),
        (3, None, 455, "3min: Power varies"),
        (4, None, 440, "4min: Less power"),
        (5, None, 465, "5min: Power back up"),
        (10, None, 480, "10min: Continued charging"),
        (15, None, 475, "15min: Steady charging"),
        (30, 34.3, 460, "30min: Official SoC update (+0.1%)"),
        (31, None, 470, "31min: Continue from new baseline"),
        (35, None, 485, "35min: Higher power"),
        (40, None, 490, "40min: Peak charging"),
    ]

    logger.info("📊 Simulating realistic charging session:\n")

    base_time = datetime.now()

    for minutes, official_soc, power_w, desc in scenarios:
        current_time = base_time + timedelta(minutes=minutes)

        # Update estimator
        if official_soc is not None:
            estimator.update_official_soc(official_soc, current_time)

        estimator.update_power(power_w, current_time)

        # Get estimate
        estimate = estimator.get_estimated_soc(current_time)
        charging_info = estimator.get_charging_rate_estimate()

        logger.info(f"⏰ +{minutes}min - {desc}")
        logger.info(f"   Power: {power_w}W")

        if official_soc is not None:
            logger.info(f"   🎯 Official SoC: {official_soc}%")

        logger.info(
            f"   📊 Estimated SoC: {estimate['estimated_soc']:.2f}% (confidence: {estimate['confidence']:.2f})"
        )
        logger.info(f"   🔄 Energy tracking: {estimate['cumulative_energy_wh']:+.1f}Wh")

        if charging_info["charging_rate_percent_per_hour"] > 0:
            logger.info(
                f"   ⚡ Charging rate: {charging_info['charging_rate_percent_per_hour']:.1f}%/hour"
            )
            if charging_info["time_to_full_hours"]:
                hours = int(charging_info["time_to_full_hours"])
                minutes = int((charging_info["time_to_full_hours"] - hours) * 60)
                logger.info(f"   🏁 Time to full: {hours}h{minutes}m")

        print()  # Blank line

        time.sleep(0.3)  # Brief pause for demo

    logger.info("=" * 60)
    logger.info("💡 REAL-TIME SOC ESTIMATOR BENEFITS")
    logger.info("=" * 60)
    logger.info("✅ Provides SoC estimates every few seconds (vs 30min official)")
    logger.info("✅ Shows charging progress in real-time")
    logger.info("✅ Estimates time to full charge")
    logger.info("✅ Self-calibrates when official SoC updates")
    logger.info("✅ Confidence scoring for reliability")
    logger.info("✅ Handles power fluctuations smoothly")

    logger.info("\n🔧 INTEGRATION SUGGESTIONS:")
    logger.info("1. Add this as a separate service alongside main system")
    logger.info("2. Feed it battery power readings every few seconds")
    logger.info("3. Update with official SoC when it changes")
    logger.info("4. Use estimated SoC for real-time display/decisions")
    logger.info("5. Fall back to official SoC if estimate confidence is low")


if __name__ == "__main__":
    demo_realtime_estimator()
