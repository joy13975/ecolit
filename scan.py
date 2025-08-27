#!/usr/bin/env python3
"""ECHONET Lite device discovery tool - generates config suggestions."""

import asyncio
import logging
from pathlib import Path

import yaml
from pychonet import ECHONETAPIClient as api
from pychonet.lib.udpserver import UDPServer

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DEVICE_TYPES = {
    0x0279: {"name": "Solar Power Generation", "type": "solar"},
    0x027D: {"name": "Storage Battery", "type": "battery"},
    0x0287: {"name": "Power Distribution Board", "type": "distribution"},
    0x0288: {"name": "Smart Electric Energy Meter", "type": "meter"},
    0x026B: {"name": "Electric Vehicle Charger", "type": "ev_charger"},
}


async def discover_devices():
    """Discover ECHONET Lite devices and generate config."""
    logger.info("🔍 Discovering ECHONET Lite devices on your network...")
    logger.info("Listening for 10 seconds...")

    # Initialize discovery
    udp = UDPServer()
    loop = asyncio.get_event_loop()
    udp.run("0.0.0.0", 3610, loop=loop)
    server = api(server=udp)

    discovered_devices = []

    # Scan common network ranges
    networks = ["192.168.0", "192.168.1", "192.168.11", "10.0.0"]

    for network in networks:
        logger.info(f"Scanning {network}.x...")
        found_in_network = False

        for i in range(1, 25):  # Scan first 25 IPs quickly
            ip = f"{network}.{i}"
            try:
                success = await asyncio.wait_for(server.discover(ip), timeout=0.4)
                if success:
                    # Wait for discovery
                    for _ in range(300):
                        await asyncio.sleep(0.01)
                        if ip in server._state and "discovered" in server._state[ip]:
                            break

                    # Process discovered devices
                    if ip in server._state and "instances" in server._state[ip]:
                        instances = server._state[ip]["instances"]

                        for eojgc, eojcc_dict in instances.items():
                            for eojcc, instance_dict in eojcc_dict.items():
                                for instance_id in instance_dict.keys():
                                    device_key = (eojgc << 8) | eojcc
                                    device_info = DEVICE_TYPES.get(
                                        device_key,
                                        {
                                            "name": f"Unknown Device (0x{eojgc:02X}{eojcc:02X})",
                                            "type": "unknown",
                                        },
                                    )

                                    discovered_devices.append(
                                        {
                                            "name": device_info["name"],
                                            "ip": ip,
                                            "type": device_info["type"],
                                            "eojgc": eojgc,
                                            "eojcc": eojcc,
                                            "instance": instance_id,
                                        }
                                    )

                                    logger.info(f"✅ Found: {device_info['name']} at {ip}")

                        found_in_network = True

            except TimeoutError:
                continue
            except Exception:
                continue

        if found_in_network:
            # Found devices in this network, don't scan others
            break

    if not discovered_devices:
        logger.warning("❌ No ECHONET Lite devices found")
        logger.info("Tips:")
        logger.info("  - Ensure your HEMS devices support ECHONET Lite")
        logger.info("  - Check that devices are on the same network")
        logger.info("  - Verify UDP port 3610 is not blocked")
        return None

    return discovered_devices


def generate_devices_config(devices):
    """Generate devices configuration YAML from discovered devices."""
    # All discovered devices become required devices
    required_devices = []

    for device in devices:
        device_entry = {
            "name": device["name"],
            "ip": device["ip"],
            "type": device["type"],
            "eojgc": device["eojgc"],
            "eojcc": device["eojcc"],
            "instance": device["instance"],
        }
        required_devices.append(device_entry)

    # Generate devices config
    devices_config = {
        "devices": {
            "required": required_devices,
        }
    }

    return devices_config


async def main():
    """Main discovery workflow."""
    logger.info("🏠 Ecolit Device Discovery")
    logger.info("=" * 40)

    # Discover devices
    devices = await discover_devices()

    if not devices:
        return 1

    logger.info("\n📋 Discovery Summary:")
    logger.info(f"Found {len(devices)} device(s)")

    # Generate devices config
    devices_config = generate_devices_config(devices)

    # Show discovered devices
    logger.info("\n📄 Discovered Devices:")
    logger.info("-" * 30)
    for device in devices:
        logger.info(f"  • {device['name']} at {device['ip']}")

    # Ask user if they want to save devices
    try:
        response = (
            input(f"\nSave {len(devices)} device(s) to devices.yaml? (y/N): ").lower().strip()
        )
        if response == "y" or response == "yes":
            # Save devices configuration
            devices_path = Path("devices.yaml")
            with open(devices_path, "w") as f:
                yaml.dump(devices_config, f, default_flow_style=False, indent=2)
            logger.info(f"✅ Devices saved to {devices_path}")

            # Create config.yaml from template if it doesn't exist
            config_path = Path("config.yaml")
            if not config_path.exists():
                template_path = Path("config.template.yaml")
                if template_path.exists():
                    import shutil

                    shutil.copy(template_path, config_path)
                    logger.info(f"✅ Created {config_path} from template")

            logger.info("\nNext steps:")
            logger.info("  1. Run 'make run' to start with device validation")
            logger.info("  2. Customize config.yaml for your preferences")
        else:
            logger.info("Devices not saved. Run 'make scan' again anytime.")
    except (EOFError, KeyboardInterrupt):
        logger.info("\nScan cancelled. Devices not saved.")

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        exit(exit_code)
    except KeyboardInterrupt:
        logger.info("\n🛑 Discovery cancelled")
        exit(1)
