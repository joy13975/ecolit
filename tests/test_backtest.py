"""Test suite for backtesting functionality."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ecolit.config import load_config
from ecolit.util.backtest import BacktestRunner, MockDataSource, MockTimeProvider
from ecolit.util.synth_metrics import MetricsSynthesizer


class TestMockTimeProvider:
    """Test the mock time provider."""

    def test_time_acceleration(self):
        """Test that time acceleration works correctly."""
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        provider = MockTimeProvider(start_time, acceleration_factor=60.0)

        # Initially should be at start time
        now1 = provider.now()
        assert abs((now1 - start_time).total_seconds()) < 1

        # After 1 real second, should be 60 virtual seconds later
        import time

        time.sleep(1.1)  # Sleep slightly more than 1 second
        now2 = provider.now()
        elapsed = (now2 - start_time).total_seconds()
        assert 55 < elapsed < 70  # Allow some tolerance


class TestMockDataSource:
    """Test the mock data source."""

    @pytest.fixture
    def sample_csv_file(self, tmp_path):
        """Create a sample CSV file for testing."""
        csv_path = tmp_path / "test_metrics.csv"
        csv_content = """timestamp,home_batt_soc_percent,solar_power_w,grid_power_flow_w,ev_charging_amps,ev_policy,ev_soc_percent,ev_charging_state
2025-01-01T12:00:00,75.0,3000,,-500,0,ECO,45.0,Stopped
2025-01-01T12:01:00,75.2,3100,-600,0,ECO,45.0,Stopped
2025-01-01T12:02:00,75.4,3200,-700,6,ECO,45.1,Charging"""

        csv_path.write_text(csv_content)
        return str(csv_path)

    def test_data_loading(self, sample_csv_file):
        """Test that CSV data loads correctly."""
        source = MockDataSource(sample_csv_file)
        assert len(source.metrics_data) == 3

        first_row = source.metrics_data[0]
        assert first_row["home_batt_soc_percent"] == "75.0"
        assert first_row["solar_power_w"] == "3000"

    def test_get_current_metrics(self, sample_csv_file):
        """Test getting metrics for a specific time."""
        source = MockDataSource(sample_csv_file)

        # Should find closest match
        test_time = datetime(2025, 1, 1, 12, 1, 30)  # Between first and second record
        metrics = source.get_current_metrics(test_time)

        assert metrics is not None
        assert metrics["solar_power_w"] in ["3000", "3100"]  # Should match one of the records


class TestBacktestRunner:
    """Test the backtesting runner."""

    @pytest.fixture
    def sample_config(self):
        """Sample configuration for testing."""
        return {
            "ev": {
                "max_amps": 16,
                "tesla": {
                    "enabled": False  # Disable Tesla API for testing
                },
            },
            "metrics": {"enabled": True, "folder": "data/test/metrics"},
        }

    @pytest.fixture
    def sample_synthetic_csv(self, tmp_path):
        """Create sample synthetic data."""
        csv_path = tmp_path / "synthetic.csv"

        # Generate some realistic test data
        rows = []
        base_time = datetime(2025, 1, 1, 12, 0, 0)

        for i in range(10):  # 10 minutes of data
            timestamp = base_time + timedelta(minutes=i)

            # Simulate changing conditions
            home_soc = 75.0 + i * 0.1  # Slowly charging battery
            solar_power = 3000 + i * 50  # Increasing solar
            ev_soc = 45.0 + i * 0.02  # Slowly charging EV
            ev_amps = 6 if i > 3 else 0  # Start charging after minute 3

            row = {
                "timestamp": timestamp.isoformat(),
                "home_batt_soc_percent": str(home_soc),
                "home_batt_soc_realtime_percent": str(home_soc + 0.1),
                "home_batt_soc_confidence": "0.95",
                "home_batt_soc_source": "power_integration_0.0h",
                "home_batt_charging_rate_pct_per_hour": "20.0",
                "home_batt_power_w": str(-1000 - i * 10),  # Negative = charging
                "grid_power_flow_w": str(-500 - i * 20),  # Negative = exporting
                "solar_power_w": str(solar_power),
                "ev_charging_amps": str(ev_amps),
                "ev_policy": "ECO",
                "ev_soc_percent": str(ev_soc),
                "ev_charging_power_w": str(ev_amps * 240) if ev_amps > 0 else "0.0",
                "ev_charging_state": "Charging" if ev_amps > 0 else "Stopped",
                "ev_range_km": str(300 * (ev_soc / 100)),
                "ev_est_range_km": str(300 * (ev_soc / 100)),
                "ev_wc_power_w": "",
                "ev_wc_amps": "",
                "house_load_estimate_w": "",
                "house_load_confidence": "",
                "notes": "test_data",
            }
            rows.append(row)

        # Write CSV
        import csv

        with open(csv_path, "w", newline="") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

        return str(csv_path)

    def test_runner_initialization(self, sample_config, sample_synthetic_csv):
        """Test that runner initializes correctly."""
        runner = BacktestRunner(sample_config, sample_synthetic_csv, acceleration_factor=10.0)

        assert runner.config == sample_config
        assert runner.acceleration_factor == 10.0
        assert runner.ev_controller is not None
        assert runner.mock_data is not None

    @pytest.mark.asyncio
    async def test_short_scenario_run(self, sample_config, sample_synthetic_csv):
        """Test running a short backtesting scenario."""
        runner = BacktestRunner(
            sample_config, sample_synthetic_csv, acceleration_factor=600.0
        )  # Very fast

        # Run for just 2 virtual minutes
        results = await runner.run_scenario("test_scenario", duration_minutes=2.0)

        # Verify results structure
        assert results["scenario"] == "test_scenario"
        assert results["duration_minutes"] == 2.0
        assert "total_decisions" in results
        assert "charging_changes" in results
        assert "decisions" in results
        assert "events" in results

        # Should have made some decisions
        assert results["total_decisions"] > 0
        assert len(results["decisions"]) > 0

    def test_validation_safety_checks(self, sample_config, sample_synthetic_csv):
        """Test that validation catches safety violations."""
        runner = BacktestRunner(sample_config, sample_synthetic_csv)

        # Create results with safety violation
        bad_results = {
            "scenario": "test",
            "max_charging_amps": 20,  # Exceeds config max of 16
            "decisions": [
                {"recommended_amps": -5, "timestamp": "2025-01-01T12:00:00"},  # Negative amps
            ],
        }

        errors = runner.validate_results(bad_results)
        assert len(errors) >= 2  # Should catch both violations
        assert any("Exceeded max_amps" in error for error in errors)
        assert any("Negative charging amps" in error for error in errors)

    def test_validation_policy_logic(self, sample_config, sample_synthetic_csv):
        """Test validation of policy logic."""
        runner = BacktestRunner(sample_config, sample_synthetic_csv)

        # ECO policy shouldn't charge at very low battery SOC
        bad_results = {
            "scenario": "test",
            "max_charging_amps": 10,
            "decisions": [
                {
                    "policy": "ECO",
                    "home_batt_soc": 15.0,  # Very low
                    "recommended_amps": 8,  # But still recommending charging
                    "timestamp": "2025-01-01T12:00:00",
                }
            ],
        }

        errors = runner.validate_results(bad_results)
        assert any("ECO policy charging at low battery SOC" in error for error in errors)


class TestMetricsSynthesizer:
    """Test the metrics synthesizer."""

    @pytest.fixture
    def real_metrics_csv(self):
        """Use the actual metrics file if it exists."""
        real_path = Path("data/ecolit/metrics/20250831.csv")
        if real_path.exists():
            return str(real_path)
        else:
            pytest.skip("Real metrics CSV not found")

    def test_synthesizer_initialization(self, real_metrics_csv):
        """Test synthesizer loads real data correctly."""
        synthesizer: MetricsSynthesizer = MetricsSynthesizer(real_metrics_csv)
        assert len(synthesizer.source_data) > 0
        assert len(synthesizer.headers) > 0

    def test_metric_synthesis(self, real_metrics_csv):
        """Test that synthesizer generates reasonable data."""
        synthesizer: MetricsSynthesizer = MetricsSynthesizer(real_metrics_csv)

        # Generate 30 minutes of data
        synth_data = synthesizer.synthesize_metrics(
            duration_hours=0.5, scenario="moderate_midday_solar_70pct_soc"
        )

        assert len(synth_data) > 0

        # Check data structure
        first_record = synth_data[0]
        required_fields = [
            "timestamp",
            "home_batt_soc_percent",
            "solar_power_w",
            "ev_charging_amps",
            "ev_soc_percent",
            "ev_policy",
        ]

        for field in required_fields:
            assert field in first_record

        # Verify data reasonableness
        soc_values = [
            float(r["home_batt_soc_percent"]) for r in synth_data if r["home_batt_soc_percent"]
        ]
        assert all(0 <= soc <= 100 for soc in soc_values), "Home battery SOC out of range"

        ev_soc_values = [float(r["ev_soc_percent"]) for r in synth_data if r["ev_soc_percent"]]
        assert all(0 <= soc <= 100 for soc in ev_soc_values), "EV SOC out of range"


@pytest.mark.integration
class TestFullBacktestPipeline:
    """Integration tests for the full backtesting pipeline."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not Path("data/ecolit/metrics/20250831.csv").exists(),
        reason="Real metrics data not available",
    )
    async def test_end_to_end_backtest(self, tmp_path):
        """Test the complete backtesting pipeline."""
        # Use real config if available, otherwise minimal config
        try:
            config = load_config("config.yaml")
        except:
            config = {
                "ev": {"max_amps": 16, "tesla": {"enabled": False}},
                "metrics": {"enabled": True},
            }

        # Generate synthetic data
        real_csv = "data/ecolit/metrics/20250831.csv"
        synthesizer: MetricsSynthesizer = MetricsSynthesizer(real_csv)
        synth_data = synthesizer.synthesize_metrics(
            duration_hours=0.25, scenario="moderate_midday_solar_70pct_soc"
        )

        # Export to temp file
        temp_csv = tmp_path / "test_synth.csv"
        synthesizer.export_to_csv(synth_data, str(temp_csv))

        # Run backtest
        runner = BacktestRunner(config, str(temp_csv), acceleration_factor=240.0)  # 4 min per hour
        results = await runner.run_scenario("integration_test", duration_minutes=15.0)

        # Validate
        errors = runner.validate_results(results)

        # Assertions
        assert results["total_decisions"] > 0, "No decisions recorded"
        assert len(errors) == 0, f"Validation errors: {errors}"
        assert results["max_charging_amps"] >= 0, "Invalid max charging amps"

        print(
            f"✅ Integration test passed: {results['total_decisions']} decisions, "
            f"{results['charging_changes']} changes"
        )
