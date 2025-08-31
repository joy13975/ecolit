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

        # Generate date-based filename (YYYYMMDD.csv)
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"{date_str}.csv"
        self.csv_file_path = metrics_dir / filename

        # Define CSV headers for EV charging metrics
        self.csv_headers = [
            "timestamp",
            "home_batt_soc_percent",
            "home_batt_soc_realtime_percent",
            "home_batt_soc_confidence",
            "home_batt_soc_source",
            "home_batt_charging_rate_pct_per_hour",
            "home_batt_power_w",
            "grid_power_flow_w",
            "solar_power_w",
            "ev_charging_amps",
            "ev_policy",
            "ev_soc_percent",
            "ev_charging_power_w",
            "ev_charging_state",
            "ev_range_km",
            "ev_est_range_km",
            "ev_wc_power_w",
            "ev_wc_amps",
            "house_load_estimate_w",
            "house_load_confidence",
            "notes",
        ]

        try:
            # Check if file exists to determine if we should append or create new
            file_exists = self.csv_file_path.exists()
            
            # Open CSV file in append mode if exists, write mode if new
            mode = "a" if file_exists else "w"
            self.csv_file = open(self.csv_file_path, mode, newline="", buffering=1)
            self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=self.csv_headers)
            
            # Only write header if creating new file
            if not file_exists:
                self.csv_writer.writeheader()
                self.csv_file.flush()
                logger.info(f"Metrics logging initialized (new file): {self.csv_file_path}")
            else:
                logger.info(f"Metrics logging initialized (appending to existing): {self.csv_file_path}")

        except Exception as e:
            logger.error(f"Failed to initialize metrics logging: {e}")
            self.csv_file = None
            self.csv_writer = None

    def log_metrics(
        self,
        home_batt_soc: float | None = None,
        home_batt_soc_realtime: float | None = None,
        home_batt_soc_confidence: float | None = None,
        home_batt_soc_source: str | None = None,
        home_batt_charging_rate_pct_per_hour: float | None = None,
        home_batt_power: float | None = None,
        grid_power_flow: float | None = None,
        solar_power: float | None = None,
        ev_charging_amps: float = 0,
        ev_policy: str = "unknown",
        ev_soc: float | None = None,
        ev_charging_power: float | None = None,
        ev_charging_state: str | None = None,
        ev_range_km: float | None = None,
        ev_est_range_km: float | None = None,
        ev_wc_power: float | None = None,
        ev_wc_amps: float | None = None,
        house_load_estimate: float | None = None,
        house_load_confidence: str | None = None,
        notes: str = "",
        **kwargs,  # Catch any extra kwargs and ignore them
    ) -> None:
        """Log EV charging metrics to CSV file."""
        if not self.csv_writer or not self.csv_file:
            return

        try:
            # Log any unexpected kwargs for debugging
            if kwargs:
                logger.debug(f"Extra kwargs received (ignoring): {list(kwargs.keys())}")

            # Create metrics row with current timestamp
            row = {
                "timestamp": datetime.now().isoformat(),
                "home_batt_soc_percent": home_batt_soc,
                "home_batt_soc_realtime_percent": home_batt_soc_realtime,
                "home_batt_soc_confidence": home_batt_soc_confidence,
                "home_batt_soc_source": home_batt_soc_source,
                "home_batt_charging_rate_pct_per_hour": home_batt_charging_rate_pct_per_hour,
                "home_batt_power_w": home_batt_power,
                "grid_power_flow_w": grid_power_flow,
                "solar_power_w": solar_power,
                "ev_charging_amps": ev_charging_amps,
                "ev_policy": ev_policy,
                "ev_soc_percent": ev_soc,
                "ev_charging_power_w": ev_charging_power,
                "ev_charging_state": ev_charging_state,
                "ev_range_km": ev_range_km,
                "ev_est_range_km": ev_est_range_km,
                "ev_wc_power_w": ev_wc_power,
                "ev_wc_amps": ev_wc_amps,
                "house_load_estimate_w": house_load_estimate,
                "house_load_confidence": house_load_confidence,
                "notes": notes,
            }

            # Write row and flush to ensure data is saved immediately
            self.csv_writer.writerow(row)
            self.csv_file.flush()

            logger.debug(
                f"Logged metrics: SOC={home_batt_soc}%, Grid={grid_power_flow}W, EV={ev_charging_amps}A"
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
