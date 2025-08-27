#!/usr/bin/env python3
"""Guide for configuring Tesla vehicle ID and tag manually."""

from pathlib import Path

import yaml

# Add project root to path
project_root = Path(__file__).parent.parent


def show_config_guide():
    """Show how to configure Tesla vehicle ID and tag."""
    config_path = project_root / "config.yaml"

    print("🚗 Tesla Vehicle Configuration Guide")
    print("=" * 50)
    print()
    print("Since you have only 1 Tesla vehicle, you need to configure these values:")
    print()
    print("1. vehicle_id - This is typically your VIN or a Tesla-assigned ID")
    print("2. vehicle_tag - This is usually a numeric ID for API calls")
    print()
    print("📋 Manual Configuration Steps:")
    print()
    print("Option 1: Use your VIN as both values (most common):")
    print('  vehicle_id: "<YOUR_VIN>"')
    print('  vehicle_tag: "<YOUR_VIN>"')
    print()
    print("Option 2: Find your vehicle ID using Tesla app or third-party tools:")
    print("  - Tesla app > Vehicle > Software tab (look for ID)")
    print("  - TeslaMate, TeslaFi, or similar tools show vehicle IDs")
    print("  - Some show numeric ID (like 1234567890) and string ID")
    print()

    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)

        tesla_config = config.get("tesla", {})
        current_vehicle_id = tesla_config.get("vehicle_id")
        current_vehicle_tag = tesla_config.get("vehicle_tag")

        print("📄 Current Configuration:")
        print(f"  vehicle_id: {current_vehicle_id}")
        print(f"  vehicle_tag: {current_vehicle_tag}")
        print()

        if not current_vehicle_id or not current_vehicle_tag:
            print("🔧 To configure your vehicle, edit config.yaml and add:")
            print()
            print("tesla:")
            print("  enabled: true")
            print("  # ... (keep your existing auth settings) ...")
            print("  ")
            print("  # Vehicle Configuration - ADD THESE LINES:")
            print('  vehicle_id: "YOUR_VIN_OR_ID_HERE"')
            print('  vehicle_tag: "YOUR_VIN_OR_ID_HERE"  # Often same as vehicle_id')
            print()
            print("💡 If you don't know your vehicle ID:")
            print("  1. Use your VIN (17 characters, like 5YJ3E1EA1JF000123)")
            print("  2. Or find it in Tesla app under Software > Additional Vehicle Information")
            print("  3. Some third-party apps like TeslaMate show both numeric and string IDs")
            print()
        else:
            print("✅ Vehicle ID and tag are already configured!")
            print()
            print("🧪 Test your configuration with:")
            print("  make tesla-list")
            print("  make test-tesla")
    else:
        print("❌ config.yaml not found")

    print()
    print("🔄 After configuration, test with:")
    print("  make tesla-list  # Should show vehicle data")
    print("  make test-tesla  # Should connect to telemetry")
    print()
    print("📚 More info:")
    print("  - Fleet API docs: https://developer.tesla.com/docs/fleet-api")
    print("  - Vehicle IDs are usually your VIN for personal accounts")
    print("  - For Fleet API, numeric IDs may differ from string IDs")


if __name__ == "__main__":
    show_config_guide()
