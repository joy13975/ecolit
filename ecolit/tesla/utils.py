"""Shared utilities for Tesla scripts."""

import asyncio

from ecolit.charging.tesla_api import TeslaAPIClient, TeslaVehicleData


def prompt_yes_no(message: str, default_yes: bool = True) -> bool:
    """
    Prompt user for yes/no with default.

    Args:
        message: The prompt message
        default_yes: If True, default to Yes. If False, default to No.

    Returns:
        True for yes, False for no
    """
    suffix = "(Y/n)" if default_yes else "(y/N)"
    response = input(f"{message} {suffix}: ").lower().strip()

    if not response:  # Empty input uses default
        return default_yes

    return response in ["y", "yes"]


async def handle_sleeping_vehicle_with_wake(
    client: TeslaAPIClient, action_description: str = "get current data"
) -> tuple[TeslaVehicleData, bool]:
    """
    Handle sleeping vehicle with wake-up prompt that defaults to Yes.

    Args:
        client: Tesla API client
        action_description: Description of what we're trying to do

    Returns:
        Tuple of (vehicle_data, was_successfully_handled)
    """
    print("🔄 Checking vehicle status...")

    # Check if vehicle is sleeping first
    vehicle_data, is_sleeping = await client.poll_vehicle_data_with_wake_option()

    if is_sleeping:
        print("😴 Vehicle is sleeping or offline.")

        if prompt_yes_no(
            f"Would you like to wake the vehicle to {action_description}?", default_yes=True
        ):
            print("⏰ Sending wake command to vehicle...")
            try:
                wake_success = await client.wake_up()
                if wake_success:
                    print("✅ Wake command sent successfully")
                    print("⏳ Waiting for vehicle to wake up (this may take 10-30 seconds)...")

                    # Give vehicle time to wake up
                    await asyncio.sleep(15)

                    print("🔄 Retrying data retrieval...")
                    vehicle_data, _ = await client.poll_vehicle_data_with_wake_option()
                    if vehicle_data.timestamp:
                        return vehicle_data, True
                    else:
                        print("⚠️  Vehicle may still be waking up. Try again in a few minutes.")
                        return TeslaVehicleData(), False
                else:
                    print("❌ Failed to send wake command")
                    return TeslaVehicleData(), False
            except Exception as wake_e:
                print(f"❌ Error waking vehicle: {wake_e}")
                return TeslaVehicleData(), False
        else:
            print("💤 Vehicle left sleeping")
            return TeslaVehicleData(), False

    # Vehicle is already awake
    return vehicle_data, True


async def ensure_vehicle_awake_for_command(
    client: TeslaAPIClient, command_description: str = "send commands"
) -> bool:
    """
    Ensure vehicle is awake before sending commands.

    Args:
        client: Tesla API client
        command_description: Description of the command we want to send

    Returns:
        True if vehicle is awake and ready, False otherwise
    """
    vehicle_data, is_awake = await handle_sleeping_vehicle_with_wake(client, command_description)

    if not is_awake:
        print(f"💤 Cannot {command_description} while vehicle is sleeping")
        return False

    return True
