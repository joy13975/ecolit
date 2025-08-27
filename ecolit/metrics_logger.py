"""Metrics logging functionality for EV charging data."""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MetricsLogger:
    """CSV logger for EV charging metrics data."""

    def __init__(self, config: dict[str, Any]):
        """Initialize metrics logger with configuration."""
        self.config = config
        self.csv_file = None
        self.csv_writer = None
        self.csv_headers = None
        self._initialize_logging()

    def _initialize_logging(self) -> None:
        """Initialize CSV logging with timestamp-based filename."""
        metrics_config = self.config.get("metrics", {})
        if not metrics_config.get("enabled", False):
            logger.info("Metrics logging disabled")
            return

        folder_path = metrics_config.get("folder", "data/ecolit/metrics")

        # Create metrics folder if it doesn't exist
        metrics_dir = Path(folder_path)
        metrics_dir.mkdir(parents=True, exist_ok=True)

        # Generate timestamp-based filename (YYYYMMDD_HHMMSS.csv)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}.csv"
        self.csv_file_path = metrics_dir / filename

        # Define CSV headers for EV charging metrics
        self.csv_headers = [
            "timestamp",
            "battery_soc_percent",
            "battery_power_w",
            "grid_power_flow_w",
            "solar_power_w",
            "ev_charging_amps",
            "ev_policy",
            "notes",
        ]

        try:
            # Open CSV file and write headers
            self.csv_file = open(self.csv_file_path, "w", newline="")
            self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=self.csv_headers)
            self.csv_writer.writeheader()
            self.csv_file.flush()

            logger.info(f"Metrics logging initialized: {self.csv_file_path}")
        except Exception as e:
            logger.error(f"Failed to initialize metrics logging: {e}")
            self.csv_file = None
            self.csv_writer = None

    def log_metrics(
        self,
        battery_soc: float | None = None,
        battery_power: float | None = None,
        grid_power_flow: float | None = None,
        solar_power: float | None = None,
        ev_charging_amps: float = 0,
        ev_policy: str = "unknown",
        notes: str = "",
    ) -> None:
        """Log EV charging metrics to CSV file."""
        if not self.csv_writer or not self.csv_file:
            return

        try:
            # Create metrics row with current timestamp
            row = {
                "timestamp": datetime.now().isoformat(),
                "battery_soc_percent": battery_soc,
                "battery_power_w": battery_power,
                "grid_power_flow_w": grid_power_flow,
                "solar_power_w": solar_power,
                "ev_charging_amps": ev_charging_amps,
                "ev_policy": ev_policy,
                "notes": notes,
            }

            # Write row and flush to ensure data is saved immediately
            self.csv_writer.writerow(row)
            self.csv_file.flush()

            logger.debug(
                f"Logged metrics: SOC={battery_soc}%, Grid={grid_power_flow}W, EV={ev_charging_amps}A"
            )

        except Exception as e:
            logger.error(f"Failed to log metrics: {e}")

    def close(self) -> None:
        """Close CSV file and cleanup resources."""
        if self.csv_file:
            try:
                self.csv_file.close()
                logger.info(f"Metrics logging closed: {self.csv_file_path}")
            except Exception as e:
                logger.error(f"Error closing metrics file: {e}")
            finally:
                self.csv_file = None
                self.csv_writer = None

    def __del__(self) -> None:
        """Cleanup on object destruction."""
        self.close()
