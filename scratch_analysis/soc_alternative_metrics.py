#!/usr/bin/env python3
"""
Test alternative metrics for real-time SoC estimation.

Alternative approaches:
1. Raw capacity readings (0xBA REMAINING_CAPACITY)
2. Alternative percentage (0xE5 REMAINING_CAPACITY_PERCENTAGE)
3. Cumulative power-based SoC estimation
4. Integration of charging/discharging power over time
"""

import logging
import sys
import time
from datetime import datetime
from typing import Any

# Add parent directory to Python path
sys.path.append("..")


# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class AlternativeSoCCalculator:
    def __init__(self):
        self.battery_capacity_wh = 12700  # 12.7kWh in Wh
        self.soc_history = []
        self.power_history = []
        self.last_update = None
        self.estimated_soc = None

    def calculate_soc_from_capacity(self, remaining_wh: float, rated_wh: float = None) -> float:
        """Calculate SoC from raw capacity values."""
        if rated_wh is None:
            rated_wh = self.battery_capacity_wh

        if rated_wh > 0:
            soc = (remaining_wh / rated_wh) * 100
            return max(0, min(100, soc))  # Clamp to 0-100%
        return 0

    def update_cumulative_soc(
        self, power_w: float, time_elapsed_seconds: float, base_soc: float = None
    ) -> float | None:
        """Update SoC based on power integration over time."""
        if base_soc is not None:
            self.estimated_soc = base_soc
            self.last_update = time.time()
            return base_soc

        if self.estimated_soc is None or self.last_update is None:
            return None

        # Calculate energy change: Power (W) * Time (h) = Energy (Wh)
        time_hours = time_elapsed_seconds / 3600
        energy_change_wh = power_w * time_hours

        # Update estimated capacity
        current_capacity_wh = (self.estimated_soc / 100) * self.battery_capacity_wh
        new_capacity_wh = current_capacity_wh + energy_change_wh

        # Calculate new SoC
        new_soc = self.calculate_soc_from_capacity(new_capacity_wh)

        logger.debug(
            f"🔄 Cumulative SoC: {self.estimated_soc:.2f}% + {energy_change_wh:.1f}Wh ({power_w}W × {time_hours:.4f}h) = {new_soc:.2f}%"
        )

        self.estimated_soc = new_soc
        self.last_update = time.time()
        return new_soc

    def analyze_soc_metrics(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Analyze and compare different SoC calculation methods."""
        result = {
            "timestamp": datetime.now(),
            "official_soc": metrics.get("battery_soc"),
            "power_w": metrics.get("battery_power", 0),
            "alternative_methods": {},
        }

        # Method 1: Raw remaining capacity (if available)
        if "remaining_capacity_wh" in metrics:
            capacity_soc = self.calculate_soc_from_capacity(metrics["remaining_capacity_wh"])
            result["alternative_methods"]["capacity_based"] = capacity_soc
            logger.info(
                f"📊 Capacity-based SoC: {capacity_soc:.2f}% (from {metrics['remaining_capacity_wh']:.0f}Wh)"
            )

        # Method 2: Alternative percentage property
        if "alt_percentage" in metrics:
            result["alternative_methods"]["alt_percentage"] = metrics["alt_percentage"]
            logger.info(f"📊 Alternative percentage: {metrics['alt_percentage']:.2f}%")

        # Method 3: Cumulative estimation
        if result["power_w"] is not None:
            current_time = time.time()
            time_elapsed = 0

            if self.last_update is not None:
                time_elapsed = current_time - self.last_update

            cumulative_soc = self.update_cumulative_soc(
                result["power_w"],
                time_elapsed,
                base_soc=result["official_soc"] if self.estimated_soc is None else None,
            )

            if cumulative_soc is not None:
                result["alternative_methods"]["cumulative"] = cumulative_soc
                logger.info(f"📊 Cumulative SoC: {cumulative_soc:.2f}%")

        return result


def mock_battery_monitoring():
    """Mock battery monitoring to demonstrate alternative SoC methods."""
    logger.info("🔋 ALTERNATIVE SOC CALCULATION METHODS TEST")
    logger.info("=" * 60)

    calculator = AlternativeSoCCalculator()

    # Simulate battery data with real-time power but static official SoC
    scenarios = [
        # Format: (official_soc, power_w, remaining_wh, alt_percentage, time_offset_minutes)
        (34.2, 450, 4350, 34.3, 0),  # Initial reading
        (34.2, 470, 4350, 34.3, 1),  # 1 min later - same SoC, different power
        (34.2, 485, 4350, 34.3, 2),  # 2 min later - still same SoC
        (34.2, 455, 4350, 34.3, 3),  # 3 min later - still same SoC
        (34.2, 440, 4360, 34.4, 4),  # 4 min later - capacity slightly up
        (34.2, 465, 4370, 34.4, 5),  # 5 min later - capacity up more
    ]

    logger.info("📊 Simulating 5 minutes of battery monitoring:")
    logger.info("   Official SoC stays at 34.2% (realistic)")
    logger.info("   Power varies 440-485W (realistic)")
    logger.info("   Testing if alternative methods show more granular changes\n")

    results = []

    for i, (official_soc, power_w, remaining_wh, alt_pct, time_offset) in enumerate(scenarios):
        logger.info(f"⏰ Time +{time_offset}min:")

        # Simulate time passage
        if i > 0:
            time.sleep(1)  # Brief pause for demo

        metrics = {
            "battery_soc": official_soc,
            "battery_power": power_w,
            "remaining_capacity_wh": remaining_wh,
            "alt_percentage": alt_pct,
        }

        analysis = calculator.analyze_soc_metrics(metrics)
        results.append(analysis)

        logger.info(f"   Official: {official_soc}% | Power: {power_w}W")
        print()  # Blank line for readability

    # Summary analysis
    logger.info("=" * 60)
    logger.info("📋 ALTERNATIVE SOC METHOD ANALYSIS")
    logger.info("=" * 60)

    if len(results) > 1:
        first = results[0]
        last = results[-1]

        official_change = last["official_soc"] - first["official_soc"]
        logger.info(f"Official SoC change: {official_change:.2f}% over 5 minutes")

        for method_name in ["capacity_based", "alt_percentage", "cumulative"]:
            if (
                method_name in first["alternative_methods"]
                and method_name in last["alternative_methods"]
            ):
                change = (
                    last["alternative_methods"][method_name]
                    - first["alternative_methods"][method_name]
                )

                logger.info(f"{method_name.capitalize()} SoC change: {change:+.2f}%")

        # Calculate expected change based on power integration
        total_energy_wh = sum(r["power_w"] for r in results) / 60  # Approximate Wh over 5 minutes
        expected_soc_change = (total_energy_wh / calculator.battery_capacity_wh) * 100

        logger.info(f"Expected SoC change (from power): {expected_soc_change:+.2f}%")

    logger.info("\n💡 RECOMMENDATIONS:")
    logger.info("1. 📊 If raw capacity (0xBA) updates more frequently, use capacity-based SoC")
    logger.info("2. 🔄 Implement cumulative SoC estimation between official updates")
    logger.info("3. ⚡ Use power integration for real-time SoC estimation")
    logger.info("4. 🎯 Validate alternative methods against actual charging sessions")


if __name__ == "__main__":
    mock_battery_monitoring()
