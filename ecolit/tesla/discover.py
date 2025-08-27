#!/usr/bin/env python3
"""Discover Tesla vehicles and get their IDs for configuration."""

import asyncio
from pathlib import Path

import aiohttp
import yaml


async def discover_vehicles():
    """Discover Tesla vehicles using the Fleet API."""
    config_path = Path.cwd() / "config.yaml"

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

    # Get access token using Fleet API authentication
    auth_endpoint = tesla_config.get(
        "auth_endpoint", "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
    )
    auth_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        async with aiohttp.ClientSession() as session:
            # Authenticate using form data (not JSON)
            async with session.post(
                auth_endpoint,
                data=auth_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
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

            # Get Fleet API endpoints from config
            fleet_endpoints = tesla_config.get(
                "fleet_api_endpoints",
                {
                    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
                    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
                    "ap": "https://fleet-api.prd.ap.vn.cloud.tesla.com",
                },
            )

            # Determine Fleet API region from refresh token
            if refresh_token.startswith("EU_"):
                api_endpoint = fleet_endpoints["eu"]
            elif refresh_token.startswith("AP_"):
                api_endpoint = fleet_endpoints["ap"]
            else:
                api_endpoint = fleet_endpoints["na"]

            # Use Fleet API vehicles endpoint
            vehicles_url = f"{api_endpoint}/api/1/vehicles"

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
