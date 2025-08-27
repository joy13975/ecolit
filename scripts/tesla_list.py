#!/usr/bin/env python3
"""List registered Tesla products and test API connectivity."""

import asyncio
import sys
from pathlib import Path

import yaml

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from ecolit.charging.tesla_api import TeslaAPIClient


async def list_tesla_products():
    """List Tesla products and test API connectivity."""
    config_path = project_root / "config.yaml"

    if not config_path.exists():
        print("❌ config.yaml not found")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    tesla_config = config.get("tesla", {})

    if not tesla_config.get("enabled", False):
        print("❌ Tesla API is disabled in config.yaml")
        print("💡 Set tesla.enabled: true to enable Tesla API")
        return

    if not all(
        [
            tesla_config.get("client_id"),
            tesla_config.get("client_secret"),
            tesla_config.get("refresh_token"),
        ]
    ):
        print("❌ Tesla API credentials not configured")
        print("💡 Configure client_id, client_secret, and refresh_token in config.yaml")
        return

    print("🔐 Authenticating with Tesla API...")

    try:
        async with TeslaAPIClient(tesla_config) as client:
            print("✅ Tesla API authentication successful")
            print(f"🆔 Client configured for vehicle: {tesla_config.get('vehicle_id', 'Not set')}")
            print(f"🏷️  Vehicle tag: {tesla_config.get('vehicle_tag', 'Not set')}")

            status = client.get_status()
            print()
            print("📊 Tesla API Client Status:")
            for key, value in status.items():
                print(f"  {key}: {value}")

            # Get vehicle data if available
            try:
                print()
                print("🔄 Polling vehicle data...")

                vehicle_data = await client.poll_vehicle_data()
                if vehicle_data.timestamp:
                    print("🚗 Recent Vehicle Data:")
                    if vehicle_data.battery_level is not None:
                        print(f"  Battery Level: {vehicle_data.battery_level}%")
                    if vehicle_data.charging_power is not None:
                        print(f"  Charging Power: {vehicle_data.charging_power}kW")
                    if vehicle_data.charge_amps is not None:
                        print(f"  Charging Amps: {vehicle_data.charge_amps}A")
                    if vehicle_data.charge_port_status:
                        print(f"  Charge Port: {vehicle_data.charge_port_status}")
                    print(f"  Last Update: {vehicle_data.timestamp}")
                else:
                    print("ℹ️  No recent telemetry data available")
                    print("   (This is normal if vehicle is sleeping or offline)")
            except Exception as e:
                print(f"⚠️  Could not retrieve vehicle data: {e}")
                if "must be registered" in str(e):
                    print()
                    print("💡 Tesla Fleet API Registration Required:")
                    print(
                        "   Tesla now requires all API access to go through Fleet API registration"
                    )
                    print(
                        "   This requires a domain, hosted public key, and business application approval"
                    )
                    print(
                        "   See: https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints#register"
                    )

    except Exception as e:
        print(f"❌ Tesla API error: {e}")
        print()
        print("💡 Troubleshooting:")
        print("  - Check if refresh_token is valid")
        print("  - Verify client_id and client_secret")
        print("  - Ensure Tesla account has API access enabled")
        print("  - Make sure vehicle_id and vehicle_tag are configured")


if __name__ == "__main__":
    asyncio.run(list_tesla_products())
