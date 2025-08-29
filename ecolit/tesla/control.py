#!/usr/bin/env python3
"""Interactive Tesla vehicle charging control CLI."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import yaml

from ecolit.charging.tesla_api import TeslaAPIClient
from ecolit.tesla.utils import (
    ensure_vehicle_awake_for_command,
)


def format_charging_schedule(schedule_data: dict) -> str:
    """Format charging schedule data for display."""
    if not schedule_data:
        return "❌ No charging schedule data available"

    if schedule_data.get("status") == "vehicle_sleeping":
        return "😴 Vehicle is sleeping - cannot retrieve charging schedule"

    lines = []

    # Handle charge_schedules array (main scheduling data)
    if "charge_schedules" in schedule_data:
        schedules = schedule_data["charge_schedules"]
        if schedules:
            lines.append(f"📅 Active Schedules: {len(schedules)} configured")

            for i, schedule in enumerate(schedules, 1):
                if schedule.get("enabled", False):
                    # Convert start/end times from minutes since midnight
                    start_minutes = schedule.get("start_time", 0)
                    end_minutes = schedule.get("end_time", 0)

                    start_hour, start_min = divmod(start_minutes, 60)
                    end_hour, end_min = divmod(end_minutes, 60)

                    # Convert days_of_week bitmask to readable format
                    days_mask = schedule.get("days_of_week", 0)
                    days = []
                    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    for j, day in enumerate(day_names):
                        if days_mask & (1 << j):
                            days.append(day)

                    days_str = (
                        ", ".join(days) if days else "Daily" if days_mask == 127 else "Custom"
                    )

                    lines.append(
                        f"  📋 Schedule {i}: {start_hour:02d}:{start_min:02d} - {end_hour:02d}:{end_min:02d} ({days_str})"
                    )

                    # Show name if available
                    name = schedule.get("name", "").strip()
                    if name:
                        lines.append(f"     Name: {name}")
                else:
                    lines.append(f"  ⏸️  Schedule {i}: Disabled")
        else:
            lines.append("📅 No charging schedules configured")

    # Handle charge_schedule_window (current/next window)
    if "charge_schedule_window" in schedule_data:
        window = schedule_data["charge_schedule_window"]
        if window and window.get("enabled", False):
            start_minutes = window.get("start_time", 0)
            end_minutes = window.get("end_time", 0)

            start_hour, start_min = divmod(start_minutes, 60)
            end_hour, end_min = divmod(end_minutes, 60)

            lines.append(
                f"⏰ Current Window: {start_hour:02d}:{start_min:02d} - {end_hour:02d}:{end_min:02d}"
            )

    # Show next schedule status
    if "next_schedule" in schedule_data:
        next_active = schedule_data["next_schedule"]
        lines.append(f"⏭️  Next Schedule: {'Active' if next_active else 'None'}")

    # Show charge buffer if available
    if "charge_buffer" in schedule_data:
        buffer_minutes = schedule_data["charge_buffer"]
        lines.append(f"🛡️  Charge Buffer: {buffer_minutes} minutes")

    # Check for old-format scheduled charging data (fallback)
    if not lines:
        # Check for scheduled charging pending
        if "scheduled_charging_pending" in schedule_data:
            pending = schedule_data["scheduled_charging_pending"]
            lines.append(f"📅 Scheduled Charging Pending: {'Yes' if pending else 'No'}")

        # Check for scheduled start time
        if "scheduled_charging_start_time" in schedule_data:
            start_time = schedule_data["scheduled_charging_start_time"]
            if start_time:
                try:
                    dt = datetime.fromtimestamp(start_time)
                    lines.append(f"⏰ Scheduled Start Time: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                except (ValueError, TypeError):
                    lines.append(f"⏰ Scheduled Start Time: {start_time}")

        # Check for scheduled departure time
        if "scheduled_departure_time" in schedule_data:
            departure_time = schedule_data["scheduled_departure_time"]
            if departure_time:
                try:
                    dt = datetime.fromtimestamp(departure_time)
                    lines.append(f"🚗 Scheduled Departure: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                except (ValueError, TypeError):
                    lines.append(f"🚗 Scheduled Departure: {departure_time}")

    # If we found some data, return it
    if lines:
        return "\n".join(lines)

    return "ℹ️  No active charging schedule configured"


def format_charging_config(config_data: dict) -> str:
    """Format charging configuration data for display."""
    if not config_data:
        return "❌ No charging configuration data available"

    if config_data.get("status") == "vehicle_sleeping":
        return "😴 Vehicle is sleeping - cannot retrieve charging configuration"

    lines = []

    # Current charging amperage request
    if config_data.get("charge_current_request") is not None:
        amps = config_data["charge_current_request"]
        lines.append(f"⚡ Current Charging Amps: {amps}A")

    # Maximum available amps
    if config_data.get("charge_current_request_max") is not None:
        max_amps = config_data["charge_current_request_max"]
        lines.append(f"📈 Maximum Available Amps: {max_amps}A")

    # Charge limit SOC
    if config_data.get("charge_limit_soc") is not None:
        limit = config_data["charge_limit_soc"]
        lines.append(f"🔋 Charge Limit: {limit}%")

    # Charging state
    if config_data.get("charging_state"):
        state = config_data["charging_state"]
        lines.append(f"🔌 Charging State: {state}")

    # Charger voltage and power (only show if actively charging)
    charging_state = config_data.get("charging_state", "").lower()
    is_actively_charging = charging_state in ["charging", "supercharging"]

    if config_data.get("charger_power") is not None:
        power = config_data["charger_power"]
        lines.append(f"💪 Charger Power: {power}kW")

    if config_data.get("charger_voltage") is not None and is_actively_charging:
        voltage = config_data["charger_voltage"]
        # Only show voltage if it seems reasonable for AC charging
        if voltage > 100:  # Minimum reasonable AC voltage
            lines.append(f"⚡ Charger Voltage: {voltage}V")
        else:
            lines.append("⚡ Charger Voltage: N/A (not actively charging)")

    return "\n".join(lines) if lines else "ℹ️  No charging configuration data available"


async def get_vehicle_status_with_wake_option(client: TeslaAPIClient) -> bool:
    """Get vehicle status with option to wake if sleeping."""
    return await ensure_vehicle_awake_for_command(client, "get current data")


async def show_current_status(client: TeslaAPIClient):
    """Display current vehicle charging status and Wall Connector status."""
    print("\n" + "=" * 60)
    print("🚗 CURRENT TESLA VEHICLE STATUS")
    print("=" * 60)

    # Check if vehicle is awake or offer to wake it
    is_awake = await get_vehicle_status_with_wake_option(client)

    if not is_awake:
        print("\n💤 Cannot retrieve data while vehicle is sleeping")
        return

    # Get charging schedule
    print("\n📅 CHARGING SCHEDULE:")
    print("-" * 30)
    schedule_data = await client.get_charging_schedule()
    schedule_formatted = format_charging_schedule(schedule_data)
    if "vehicle_sleeping" in schedule_formatted:
        print("⚠️  Vehicle may still be waking up, trying basic data...")
    print(schedule_formatted)

    # Get charging configuration
    print("\n⚙️  CHARGING CONFIGURATION:")
    print("-" * 30)
    config_data = await client.get_charging_config()
    config_formatted = format_charging_config(config_data)
    if "vehicle_sleeping" in config_formatted:
        print("⚠️  Vehicle may still be waking up, trying basic data...")
    print(config_formatted)

    # Get basic vehicle data for additional context
    print("\n🔋 LIVE VEHICLE DATA:")
    print("-" * 30)
    vehicle_data = await client.get_vehicle_data()
    if vehicle_data.timestamp:
        if vehicle_data.battery_level is not None:
            print(f"🔋 EV SOC: {vehicle_data.battery_level}%")
        if vehicle_data.charging_power is not None:
            print(f"⚡ Current Power: {vehicle_data.charging_power}kW")
        if vehicle_data.charge_amps is not None:
            print(f"📊 Live Charging Amps: {vehicle_data.charge_amps}A")
        if vehicle_data.charging_state:
            print(f"🔌 Status: {vehicle_data.charging_state}")
        print(f"⏱️  Last Update: {vehicle_data.timestamp}")
    else:
        print("⚠️  Vehicle may still be waking up. Try again in a few minutes.")

    # Get Wall Connector status from Tesla Fleet API (authoritative data)
    print("\n🔌 WALL CONNECTOR STATUS:")
    print("-" * 30)
    try:
        live_status = await client.get_wall_connector_live_status()
        if live_status and "response" in live_status:
            live_data = live_status["response"]
            wall_connectors = live_data.get("wall_connectors", [])

            if wall_connectors:
                for i, wc in enumerate(wall_connectors):
                    wc_power = wc.get("wall_connector_power", 0)
                    wc_state = wc.get("wall_connector_state", "unknown")

                    # Map Tesla Fleet API wall connector states to human-readable names
                    # Based on community research and Tesla behavior patterns
                    fleet_state_map = {
                        1: "Standby",
                        2: "Vehicle Detected",
                        3: "Ready to Charge",
                        4: "Vehicle Connected (Not Charging)",
                        5: "Sleep Mode",
                        9: "Vehicle Connected (Waiting)",
                        10: "Charging Starting",
                        11: "Charging",
                        12: "Charging Stopping",
                        13: "Charging Complete",
                        14: "Vehicle Connected (Scheduled)",
                    }

                    state_name = fleet_state_map.get(wc_state, f"Unknown State ({wc_state})")

                    # Format Wall Connector status
                    if wc_power > 50:  # Active power consumption
                        print(f"⚡ Wall Connector {i + 1}: {wc_power:.0f}W")
                        if wc_power > 500:
                            print("   🔋 Battery conditioning/trickle charging")
                        else:
                            print("   ⏸️  Standby power consumption")
                    else:
                        print(f"🔌 Wall Connector {i + 1}: {wc_power:.0f}W")

                    print(f"   📊 Status: {state_name}")

            else:
                print("ℹ️  No Wall Connectors found in energy site")
                # Show other site power data for context
                site_power = live_data.get("load_power", 0)
                solar_power = live_data.get("solar_power", 0)
                grid_power = live_data.get("grid_power", 0)
                if any([site_power, solar_power, grid_power]):
                    print(
                        f"🏠 Site Power - Load: {site_power}W, Solar: {solar_power}W, Grid: {grid_power}W"
                    )
        else:
            print("❌ No Tesla energy site data available")
            print("   Ensure energy_device_data scope is granted and energy site is registered")
    except Exception as e:
        print(f"❌ Tesla Fleet API error: {e}")
        print("   Check that Tesla API token has energy_device_data scope")


async def start_charging_interactive(client: TeslaAPIClient):
    """Start charging."""
    print("\n" + "=" * 60)
    print("⚡ START CHARGING")
    print("=" * 60)

    # Ensure vehicle is awake
    if not await ensure_vehicle_awake_for_command(client, "start charging"):
        return

    print("\n🚀 Starting charging...")

    try:
        success = await client.charge_start()

        if success:
            print("✅ Charging started successfully")
        else:
            print("❌ Failed to start charging")
            print("💡 Check if vehicle is plugged in and ready to charge")

    except Exception as e:
        print(f"❌ Error: {e}")


async def stop_charging_interactive(client: TeslaAPIClient):
    """Stop charging."""
    print("\n" + "=" * 60)
    print("🛑 STOP CHARGING")
    print("=" * 60)

    # Ensure vehicle is awake
    if not await ensure_vehicle_awake_for_command(client, "stop charging"):
        return

    print("\n🛑 Stopping charging...")

    try:
        success = await client.charge_stop()

        if success:
            print("✅ Charging stopped successfully")
        else:
            print("❌ Failed to stop charging")
            print("💡 Vehicle may not be charging")

    except Exception as e:
        print(f"❌ Error: {e}")


async def set_charging_amps_interactive(client: TeslaAPIClient):
    """Interactively set charging amperage."""
    print("\n" + "=" * 60)
    print("⚡ SET CHARGING AMPERAGE")
    print("=" * 60)

    # Ensure vehicle is awake using shared logic
    if not await ensure_vehicle_awake_for_command(client, "set charging amperage"):
        return

    # Show current limits from config
    print(f"🛡️  Safety Limits: {client.min_amps}A - {client.max_amps}A")

    # Get current config to show current setting
    config_data = await client.get_charging_config()
    current_amps = config_data.get("charge_current_request")
    if current_amps is not None:
        print(f"📊 Current Setting: {current_amps}A")

    max_available = config_data.get("charge_current_request_max")
    if max_available is not None:
        print(f"📈 Vehicle Maximum: {max_available}A")

    print()

    try:
        # Get user input
        amps_input = input(
            f"Enter new charging amperage ({client.min_amps}-{client.max_amps}A): "
        ).strip()

        if not amps_input:
            print("❌ No input provided, cancelled")
            return

        amps = int(amps_input)

        # Validate range
        if amps < client.min_amps or amps > client.max_amps:
            print(f"❌ Amperage must be between {client.min_amps}A and {client.max_amps}A")
            return

        # Confirmation with default Yes
        print(f"\n🔧 Setting charging amperage to {amps}A...")

        # Execute the command
        success = await client.set_charging_amps(amps)

        if success:
            print(f"✅ Charging amperage set to {amps}A successfully")
            print("📊 Updated configuration will be visible in the next status check")
        else:
            print("❌ Failed to set charging amperage")
            print("💡 Check if vehicle is online and not sleeping")

    except ValueError:
        print("❌ Invalid input - please enter a number")
    except KeyboardInterrupt:
        print("\n❌ Cancelled")
    except Exception as e:
        error_msg = str(e)
        if "Tesla Vehicle Command Protocol required" in error_msg:
            print("\n❌ Tesla Vehicle Command Protocol Required")
            print("🔧 Your Tesla requires TVCP for vehicle commands")
            if "Set use_tvcp_proxy: true" in error_msg:
                print("\n💡 Quick Fix:")
                print("1. Add to your config.yaml:")
                print("   tesla:")
                print("     use_tvcp_proxy: true")
                print("2. Set up Tesla HTTP proxy:")
                print("   https://github.com/teslamotors/vehicle-command")
            elif "TVCP proxy is configured but" in error_msg:
                print("\n🔍 TVCP proxy seems configured but not working:")
                print("- Check if tesla-http-proxy is running on port 4443")
                print("- Verify vehicle keys are properly added")
                print("- Check proxy logs for errors")
            print("\n📚 Full setup guide: docs/tesla-integration.md#tvcp-setup-for-modern-vehicles")
        else:
            print(f"\n❌ Unexpected error: {e}")


async def main_menu(client: TeslaAPIClient):
    """Main interactive menu."""
    while True:
        print("\n" + "=" * 60)
        print("🚗 TESLA CHARGING CONTROL")
        print("=" * 60)
        print("1. Show current status (vehicle, schedule & Wall Connector)")
        print("2. Set charging amperage")
        print("3. Start charging")
        print("4. Stop charging")
        print("5. Exit")
        print()

        try:
            choice = input("Select option (1-5): ").strip()

            if choice == "1":
                await show_current_status(client)
            elif choice == "2":
                await set_charging_amps_interactive(client)
            elif choice == "3":
                await start_charging_interactive(client)
            elif choice == "4":
                await stop_charging_interactive(client)
            elif choice == "5":
                print("👋 Goodbye!")
                break
            else:
                print("❌ Invalid choice, please select 1-5")

        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except EOFError:
            print("\n👋 Goodbye!")
            break


async def tesla_control():
    """Main Tesla control CLI function."""
    config_path = Path.cwd() / "config.yaml"

    if not config_path.exists():
        print("❌ config.yaml not found")
        print("💡 Copy config.template.yaml to config.yaml and configure Tesla settings")
        return False

    with open(config_path) as f:
        config = yaml.safe_load(f)

    tesla_config = config.get("tesla", {})

    if not tesla_config.get("enabled", False):
        print("❌ Tesla API is disabled in config.yaml")
        print("💡 Set tesla.enabled: true to enable Tesla API")
        return False

    if not all(
        [
            tesla_config.get("client_id"),
            tesla_config.get("client_secret"),
            tesla_config.get("refresh_token"),
            tesla_config.get("vehicle_tag"),
        ]
    ):
        print("❌ Tesla API not properly configured")
        print("💡 Configure client_id, client_secret, refresh_token, and vehicle_tag")
        print("💡 Use 'make tesla-discover' to find your vehicle configuration")
        return False

    print("🔐 Connecting to Tesla API...")

    try:
        async with TeslaAPIClient(tesla_config) as client:
            print("✅ Tesla API connection established")
            print(f"🚗 Vehicle: {tesla_config.get('vehicle_tag', 'Unknown')}")

            # Start interactive menu
            await main_menu(client)

        return True

    except Exception as e:
        print(f"❌ Tesla API error: {e}")
        print()
        print("💡 Troubleshooting:")
        print("  - Check if refresh_token is valid")
        print("  - Verify client_id and client_secret")
        print("  - Ensure Tesla account has API access enabled")
        print("  - Make sure vehicle_tag is correct")
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(tesla_control())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        sys.exit(0)
