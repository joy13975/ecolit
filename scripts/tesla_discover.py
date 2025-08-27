#!/usr/bin/env python3
"""Discover Tesla vehicles and get their IDs for configuration."""

import asyncio
import sys
from pathlib import Path

import yaml

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import aiohttp


async def discover_vehicles():
    """Discover Tesla vehicles using the Fleet API."""
    config_path = project_root / "config.yaml"

    if not config_path.exists():
        print("❌ config.yaml not found")
        return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    tesla_config = config.get("tesla", {})

    if not tesla_config.get("enabled", False):
        print("❌ Tesla API is disabled in config.yaml")
        return

    client_id = tesla_config.get("client_id")
    client_secret = tesla_config.get("client_secret")
    refresh_token = tesla_config.get("refresh_token")

    if not all([client_id, client_secret, refresh_token]):
        print("❌ Tesla API credentials not configured")
        return

    print("🔐 Authenticating with Tesla API...")

    # Get access token
    auth_url = "https://auth.tesla.com/oauth2/v3/token"
    auth_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        async with aiohttp.ClientSession() as session:
            # Authenticate
            async with session.post(auth_url, json=auth_data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"❌ Authentication failed: {response.status} - {error_text}")
                    return

                auth_result = await response.json()
                access_token = auth_result.get("access_token")
                print("✅ Authentication successful")

            # Get vehicles list
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            # Try the owner's API first (more accessible for personal accounts)
            vehicles_url = "https://owner-api.teslamotors.com/api/1/vehicles"

            async with session.get(vehicles_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    vehicles = data.get("response", [])

                    if not vehicles:
                        print("ℹ️  No vehicles found on this account")
                        print()
                        print("💡 Make sure:")
                        print("  - Your Tesla account has vehicles registered")
                        print("  - You've granted API access to your vehicles")
                        print("  - The Fleet API application has proper permissions")
                        return

                    print(f"🚗 Found {len(vehicles)} vehicle(s):")
                    print()

                    for idx, vehicle in enumerate(vehicles, 1):
                        print(f"Vehicle #{idx}:")
                        print(f"  Name: {vehicle.get('display_name', 'Unknown')}")
                        print(f"  VIN: {vehicle.get('vin', 'Unknown')}")
                        print(f"  ID: {vehicle.get('id', 'Unknown')}")
                        print(f"  ID String: {vehicle.get('id_s', 'Unknown')}")
                        print(f"  State: {vehicle.get('state', 'Unknown')}")
                        print(f"  Access Type: {vehicle.get('access_type', 'Unknown')}")
                        print()

                        # Show configuration snippet
                        print("📝 Add these to your config.yaml under 'tesla:':")
                        print(f'  vehicle_id: "{vehicle.get("id_s", "")}"  # ID string')
                        print(f'  vehicle_tag: "{vehicle.get("id", "")}"  # Numeric ID')
                        print(f"  # VIN: {vehicle.get('vin', 'Unknown')}")
                        print(f"  # Name: {vehicle.get('display_name', 'Unknown')}")
                        print()

                    if len(vehicles) == 1:
                        vehicle = vehicles[0]
                        print("✅ Since you have only one vehicle, here's your complete config:")
                        print()
                        print("# Tesla Fleet API Integration")
                        print("tesla:")
                        print("  enabled: true")
                        print("  ")
                        print("  # Fleet API Authentication (keep your existing values)")
                        print("  refresh_token: <your_existing_token>")
                        print("  client_id: <your_existing_client_id>")
                        print("  client_secret: <your_existing_client_secret>")
                        print("  ")
                        print("  # Vehicle Configuration")
                        print(f'  vehicle_id: "{vehicle.get("id_s", "")}"  # ID string')
                        print(f'  vehicle_tag: "{vehicle.get("id", "")}"  # Numeric ID')
                        print("  ")
                        print("  # Fleet Telemetry")
                        print('  telemetry_endpoint: "wss://streaming.vn.tesla.services/connect"')
                        print(
                            '  telemetry_fields: ["Battery_level", "Charging_power", "Charge_amps", "Charge_port_status"]'
                        )
                        print("  ")
                        print("  # Charging Limits")
                        print("  min_charging_amps: 6")
                        print("  max_charging_amps: 20")
                        print("  charging_voltage: 200")
                        print()

                else:
                    error_text = await response.text()
                    print(f"❌ Failed to get vehicles: {response.status}")
                    print(f"Error: {error_text}")
                    print()
                    print("💡 Troubleshooting:")
                    print("  - Check if your application has 'vehicle_device_data' scope")
                    print("  - Ensure the access token has proper permissions")
                    print("  - Try regenerating your refresh token")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(discover_vehicles())
