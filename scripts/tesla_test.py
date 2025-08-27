#!/usr/bin/env python3
"""Test Tesla API client with read-only operations."""

import asyncio
import sys
from pathlib import Path

import yaml

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from ecolit.charging.tesla_api import TeslaAPIClient


async def test_tesla_api():
    """Test Tesla API with safe read-only operations."""
    config_path = project_root / "config.yaml"

    if not config_path.exists():
        print("❌ config.yaml not found")
        return False

    with open(config_path) as f:
        config = yaml.safe_load(f)

    tesla_config = config.get("tesla", {})

    if not tesla_config.get("enabled", False):
        print("❌ Tesla API is disabled in config.yaml")
        return False

    if not all(
        [
            tesla_config.get("client_id"),
            tesla_config.get("client_secret"),
            tesla_config.get("refresh_token"),
        ]
    ):
        print("❌ Tesla API credentials not configured")
        return False

    print("🔐 Testing Tesla API authentication...")

    try:
        async with TeslaAPIClient(tesla_config) as client:
            print("✅ Authentication successful")

            # Test status reporting
            status = client.get_status()
            print(
                f"📊 Client Status: {status['enabled']}, Authenticated: {status['authenticated']}"
            )

            # Test telemetry connection (if configured)
            if tesla_config.get("vehicle_id"):
                print("🔄 Testing telemetry connection...")
                await asyncio.sleep(2)

                if client.is_connected():
                    print("✅ Telemetry WebSocket connected")
                else:
                    print("⚠️  Telemetry WebSocket not connected (normal if vehicle offline)")

                # Test data retrieval
                vehicle_data = await client.get_vehicle_data()
                if vehicle_data.timestamp:
                    print("✅ Vehicle data retrieved successfully")
                else:
                    print("ℹ️  No current vehicle data (normal if vehicle sleeping)")
            else:
                print("ℹ️  vehicle_id not configured, skipping telemetry tests")

            print("🚫 SAFETY: Skipping write operations (charging commands)")
            print("   - set_charging_amps() would modify vehicle settings")
            print("   - charge_start()/charge_stop() would control charging")
            print("   - These are intentionally not tested for safety")

            return True

    except Exception as e:
        print(f"❌ Tesla API test failed: {e}")
        return False


if __name__ == "__main__":
    success = asyncio.run(test_tesla_api())
    sys.exit(0 if success else 1)
