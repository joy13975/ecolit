#!/usr/bin/env python3
"""
Enhanced SoC calculator that combines multiple data sources for more real-time estimation.

Combines:
1. Official SoC readings (every 30min)
2. Raw capacity readings (potentially more frequent)
3. Power flow integration (real-time)
4. Alternative percentage readings

Creates a hybrid SoC that updates more frequently than the official reading.
"""

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Add parent directory to Python path
sys.path.append("..")


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class SoCReading:
    """Represents a single SoC reading with metadata."""

    timestamp: datetime
    official_soc: float | None
    raw_capacity_wh: float | None
    alt_percentage: float | None
    power_w: float | None
    estimated_soc: float | None = None
    confidence: float = 0.0
    source: str = "unknown"


class EnhancedSoCCalculator:
    """Enhanced SoC calculator using multiple data sources."""

    def __init__(self, battery_capacity_wh: float = 12700):
        self.battery_capacity_wh = battery_capacity_wh
        self.readings_history: list[SoCReading] = []
        self.last_official_soc = None
        self.last_official_time = None
        self.cumulative_energy_wh = 0.0
        self.baseline_established = False

        # Configuration
        self.max_history = 100  # Keep last 100 readings
        self.integration_decay = 0.95  # Decay factor for cumulative estimation

    def add_reading(
        self,
        official_soc: float | None = None,
        raw_capacity_wh: float | None = None,
        alt_percentage: float | None = None,
        power_w: float | None = None,
    ) -> SoCReading:
        """Add a new reading and calculate enhanced SoC."""

        now = datetime.now()
        reading = SoCReading(
            timestamp=now,
            official_soc=official_soc,
            raw_capacity_wh=raw_capacity_wh,
            alt_percentage=alt_percentage,
            power_w=power_w,
        )

        # Calculate enhanced SoC
        reading.estimated_soc, reading.confidence, reading.source = self._calculate_enhanced_soc(
            reading
        )

        # Store reading
        self.readings_history.append(reading)
        if len(self.readings_history) > self.max_history:
            self.readings_history.pop(0)

        return reading

    def _calculate_enhanced_soc(self, reading: SoCReading) -> tuple[float | None, float, str]:
        """Calculate the best SoC estimate from available data."""

        # Strategy priority:
        # 1. Official SoC (if available) - highest confidence
        # 2. Raw capacity calculation - high confidence if capacity looks valid
        # 3. Alternative percentage - medium confidence
        # 4. Power integration from last official reading - decreasing confidence over time
        # 5. Extrapolation from recent trends - low confidence

        if reading.official_soc is not None:
            # Official reading available - update baseline
            self.last_official_soc = reading.official_soc
            self.last_official_time = reading.timestamp
            self.cumulative_energy_wh = 0.0  # Reset cumulative tracking
            self.baseline_established = True
            return reading.official_soc, 1.0, "official"

        # Try raw capacity calculation
        if reading.raw_capacity_wh is not None and reading.raw_capacity_wh > 0:
            capacity_soc = (reading.raw_capacity_wh / self.battery_capacity_wh) * 100
            capacity_soc = max(0, min(100, capacity_soc))  # Clamp to valid range

            # High confidence if capacity seems reasonable
            if 0 <= capacity_soc <= 100:
                return capacity_soc, 0.9, "raw_capacity"

        # Try alternative percentage
        if reading.alt_percentage is not None:
            alt_soc = max(0, min(100, reading.alt_percentage))
            return alt_soc, 0.7, "alt_percentage"

        # Power integration from last official reading
        if self.baseline_established and reading.power_w is not None:
            integrated_soc = self._calculate_integrated_soc(reading)
            if integrated_soc is not None:
                # Confidence decreases over time since last official reading
                time_since_official = (
                    reading.timestamp - self.last_official_time
                ).total_seconds() / 3600  # hours
                confidence = max(
                    0.3, 0.8 - (time_since_official * 0.1)
                )  # Decay confidence over time
                return integrated_soc, confidence, f"integrated_{time_since_official:.1f}h"

        # Trend extrapolation (last resort)
        trend_soc = self._calculate_trend_soc(reading)
        if trend_soc is not None:
            return trend_soc, 0.2, "trend"

        return None, 0.0, "no_data"

    def _calculate_integrated_soc(self, reading: SoCReading) -> float | None:
        """Calculate SoC by integrating power from last official reading."""
        if not self.baseline_established or reading.power_w is None:
            return None

        # Find time elapsed and integrate power
        if len(self.readings_history) > 0:
            last_reading = self.readings_history[-1]
            time_elapsed_hours = (reading.timestamp - last_reading.timestamp).total_seconds() / 3600

            if time_elapsed_hours > 0:
                # Add energy change to cumulative total
                energy_change_wh = reading.power_w * time_elapsed_hours
                self.cumulative_energy_wh += energy_change_wh

                # Apply decay to prevent drift
                self.cumulative_energy_wh *= self.integration_decay

                # Calculate new SoC
                baseline_capacity_wh = (self.last_official_soc / 100) * self.battery_capacity_wh
                estimated_capacity_wh = baseline_capacity_wh + self.cumulative_energy_wh
                integrated_soc = (estimated_capacity_wh / self.battery_capacity_wh) * 100

                return max(0, min(100, integrated_soc))

        return self.last_official_soc

    def _calculate_trend_soc(self, reading: SoCReading) -> float | None:
        """Calculate SoC based on recent trend extrapolation."""
        if len(self.readings_history) < 3:
            return None

        # Look at last few readings to establish trend
        recent_readings = self.readings_history[-3:]
        valid_readings = [r for r in recent_readings if r.estimated_soc is not None]

        if len(valid_readings) >= 2:
            # Simple linear extrapolation
            first = valid_readings[0]
            last = valid_readings[-1]

            time_diff = (last.timestamp - first.timestamp).total_seconds() / 3600  # hours
            soc_diff = last.estimated_soc - first.estimated_soc

            if time_diff > 0:
                soc_rate = soc_diff / time_diff  # % per hour

                # Extrapolate to current time
                extrap_time = (reading.timestamp - last.timestamp).total_seconds() / 3600
                extrapolated_soc = last.estimated_soc + (soc_rate * extrap_time)

                return max(0, min(100, extrapolated_soc))

        return None

    def get_current_soc(self) -> tuple[float | None, float, str]:
        """Get the current best SoC estimate."""
        if len(self.readings_history) == 0:
            return None, 0.0, "no_data"

        latest = self.readings_history[-1]
        return latest.estimated_soc, latest.confidence, latest.source

    def get_soc_summary(self) -> dict[str, Any]:
        """Get summary of SoC calculation status."""
        current_soc, confidence, source = self.get_current_soc()

        summary = {
            "current_soc": current_soc,
            "confidence": confidence,
            "source": source,
            "baseline_established": self.baseline_established,
            "last_official_soc": self.last_official_soc,
            "last_official_time": self.last_official_time,
            "cumulative_energy_wh": self.cumulative_energy_wh,
            "readings_count": len(self.readings_history),
        }

        # Add recent trend
        if len(self.readings_history) >= 2:
            recent = self.readings_history[-2:]
            if all(r.estimated_soc is not None for r in recent):
                time_diff = (
                    recent[-1].timestamp - recent[-2].timestamp
                ).total_seconds() / 60  # minutes
                soc_diff = recent[-1].estimated_soc - recent[-2].estimated_soc
                if time_diff > 0:
                    summary["trend_percent_per_hour"] = (soc_diff / time_diff) * 60

        return summary


def demo_enhanced_soc():
    """Demonstrate enhanced SoC calculation."""
    logger.info("🔋 ENHANCED SOC CALCULATOR DEMONSTRATION")
    logger.info("=" * 60)

    calculator = EnhancedSoCCalculator()

    # Simulate realistic battery monitoring scenario
    scenarios = [
        # (official_soc, raw_capacity_wh, alt_pct, power_w, description)
        (34.2, 4350, 34.3, 450, "Initial official reading"),
        (None, 4355, 34.3, 470, "1min: Raw capacity increased slightly"),
        (None, 4362, 34.4, 485, "2min: More capacity increase"),
        (None, 4368, 34.4, 455, "3min: Continued charging"),
        (None, 4375, 34.5, 440, "4min: Raw capacity shows steady climb"),
        (34.3, 4380, 34.6, 465, "5min: New official reading (+0.1%)"),
        (None, 4385, 34.6, 480, "6min: Continue monitoring from new baseline"),
        (None, 4390, 34.7, 475, "7min: Raw capacity tracking"),
    ]

    logger.info("📊 Simulating realistic charging session with mixed data sources:\n")

    for i, (official, capacity, alt_pct, power, desc) in enumerate(scenarios):
        # Add some realistic timing
        if i > 0:
            time.sleep(0.5)  # Brief pause for demo

        reading = calculator.add_reading(
            official_soc=official, raw_capacity_wh=capacity, alt_percentage=alt_pct, power_w=power
        )

        logger.info(f"⏰ {reading.timestamp.strftime('%H:%M:%S')} - {desc}")
        logger.info(
            f"   📊 Enhanced SoC: {reading.estimated_soc:.2f}% (confidence: {reading.confidence:.2f}, source: {reading.source})"
        )

        if official is not None:
            logger.info(f"   🎯 Official SoC update: {official}% (baseline reset)")

        print()  # Blank line

    # Final summary
    summary = calculator.get_soc_summary()

    logger.info("=" * 60)
    logger.info("📋 ENHANCED SOC CALCULATOR SUMMARY")
    logger.info("=" * 60)

    logger.info(
        f"Current Enhanced SoC: {summary['current_soc']:.2f}% (confidence: {summary['confidence']:.2f})"
    )
    logger.info(f"Data Source: {summary['source']}")
    logger.info(f"Last Official SoC: {summary['last_official_soc']}%")

    if "trend_percent_per_hour" in summary:
        trend = summary["trend_percent_per_hour"]
        logger.info(f"Current Trend: {trend:+.2f}% per hour")

    logger.info(f"Cumulative Energy Tracking: {summary['cumulative_energy_wh']:+.1f}Wh")
    logger.info(f"Total Readings: {summary['readings_count']}")

    logger.info("\n💡 KEY BENEFITS:")
    logger.info("✅ More frequent SoC updates (every polling cycle vs every 30min)")
    logger.info("✅ Higher precision between official readings")
    logger.info("✅ Confidence scoring for reliability assessment")
    logger.info("✅ Multiple data source fusion")
    logger.info("✅ Trend analysis for predictive SoC")


if __name__ == "__main__":
    demo_enhanced_soc()
