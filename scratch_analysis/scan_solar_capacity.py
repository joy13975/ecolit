#!/usr/bin/env python3
"""
Comprehensive ECHONET Lite EPC scanner to find solar panel capacity values.
Scans all possible EPCs on solar devices looking for constant values around 5-6kW.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pychonet import HomeSolarPower

from ecolit.config import load_config

# Set up logging to see all EPC reads
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Common solar capacity-related EPCs to check
CAPACITY_RELATED_EPCS = {
    # Standard solar EPCs
    0x90: "Max generation power",
    0x91: "Rated generation power",
    0x92: "System capacity",
    0x93: "Nominal capacity",
    0x94: "Peak power rating",
    0x95: "Module capacity",
    0x96: "Array capacity",
    0x97: "Installation capacity",
    0x98: "Connected capacity",
    0x99: "Power limit",

    # Additional potential EPCs
    0xA0: "Output power control 1",
    0xA1: "Output power control 2",
    0xA2: "Output power setting",
    0xA3: "Output power constraint",
    0xA4: "Output upper limit",
    0xA5: "Output lower limit",
    0xA6: "Output restriction",
    0xA7: "Output threshold",
    0xA8: "Max output setting",
    0xA9: "Output configuration",

    # B0 range
    0xB0: "Capacity setting 1",
    0xB1: "Capacity setting 2",
    0xB2: "System rating",
    0xB3: "Installation rating",
    0xB4: "Design capacity",
    0xB5: "Actual capacity",
    0xB6: "Effective capacity",
    0xB7: "Usable capacity",
    0xB8: "Total capacity",
    0xB9: "Net capacity",

    # C0 range
    0xC0: "Power factor",
    0xC1: "Configuration data 1",
    0xC2: "Configuration data 2",
    0xC3: "System parameter 1",
    0xC4: "System parameter 2",
    0xC5: "Working status",
    0xC6: "Device rating",
    0xC7: "Panel configuration",
    0xC8: "Array configuration",
    0xC9: "System configuration",

    # D0 range
    0xD0: "System type",
    0xD1: "Output restraint",
    0xD2: "Maximum rating",
    0xD3: "Generation limit",
    0xD4: "Panel count",
    0xD5: "Panel rating",
    0xD6: "String configuration",
    0xD7: "Inverter rating",
    0xD8: "DC capacity",
    0xD9: "AC capacity",

    # E0 range (mostly dynamic values but check anyway)
    0xE6: "Generation capability",
    0xE7: "Max generation today",
    0xE8: "Generation setting",
    0xE9: "Generation constraint",
    0xEA: "Generation threshold",
    0xEB: "Generation upper limit",
    0xEC: "Generation control",
    0xED: "Generation parameter",
    0xEE: "Generation config",
    0xEF: "Generation info",
}

# Extended range for comprehensive scan
EXTENDED_SCAN_EPCS = range(0x80, 0xFF)  # Scan entire valid EPC range


async def scan_solar_device(ip: str, api_client, instance: int = 1):
    """Scan a solar device for all available EPCs and look for capacity values."""

    print(f"\n{'='*80}")
    print(f"Scanning Solar Device at {ip}")
    print(f"{'='*80}")

    found_values = {}
    capacity_candidates = []

    try:
        # Create solar device wrapper
        solar_device = HomeSolarPower(
            host=ip,
            api_connector=api_client,
            instance=instance
        )

        # Try to get property maps first
        print("\n📋 Getting property maps...")
        try:
            await asyncio.wait_for(solar_device.getAllPropertyMaps(), timeout=5.0)

            # Check what properties are available
            if hasattr(solar_device, '_properties') and solar_device._properties:
                available_props = list(solar_device._properties.keys())
                print(f"✅ Device reports {len(available_props)} available properties")
                print(f"   Properties: {[f'0x{p:02X}' for p in sorted(available_props)]}")
        except Exception as e:
            print(f"⚠️  Could not get property maps: {e}")

        # First scan capacity-related EPCs
        print(f"\n🔍 Scanning {len(CAPACITY_RELATED_EPCS)} capacity-related EPCs...")
        for epc, description in CAPACITY_RELATED_EPCS.items():
            try:
                value = await asyncio.wait_for(
                    solar_device.update(epc),
                    timeout=1.0
                )
                if value is not None:
                    found_values[epc] = value
                    print(f"  0x{epc:02X} ({description}): {value}")

                    # Also print any numeric values for reference
                    if isinstance(value, (int, float)) and value > 100:
                        print(f"    → Numeric value: {value}")

                    # Check if value could be a capacity in watts or kilowatts
                    if isinstance(value, (int, float)):
                        # Check for values around 5-6kW
                        if 4000 <= value <= 7000:  # Watts
                            capacity_candidates.append({
                                'epc': epc,
                                'description': description,
                                'value': value,
                                'unit': 'W',
                                'kw': value / 1000
                            })
                        elif 4 <= value <= 7:  # Kilowatts
                            capacity_candidates.append({
                                'epc': epc,
                                'description': description,
                                'value': value,
                                'unit': 'kW',
                                'kw': value
                            })
                        elif 4000000 <= value <= 7000000:  # Milliwatts
                            capacity_candidates.append({
                                'epc': epc,
                                'description': description,
                                'value': value,
                                'unit': 'mW',
                                'kw': value / 1000000
                            })

            except TimeoutError:
                pass  # Silent timeout
            except Exception as e:
                if "error" not in str(e).lower():
                    print(f"  0x{epc:02X}: Error - {e}")

        # Extended scan - SKIP for now due to timeout issues
        # print(f"\n🔍 Extended scan of all EPCs (0x80-0xFF)...")
        # Commenting out extended scan to focus on capacity EPCs

        # Try raw API access for additional EPCs
        print("\n🔍 Trying raw API access for undocumented EPCs...")
        for epc in [0x90, 0x91, 0x92, 0xB0, 0xB1, 0xD8, 0xD9]:
            try:
                response = await asyncio.wait_for(
                    api_client.echonetMessage(
                        ip,
                        0x02,  # EOJGC for solar
                        0x79,  # EOJCC for solar
                        instance,
                        0x62,  # GET
                        [{"EPC": epc}]
                    ),
                    timeout=1.0
                )
                if response and epc in response:
                    value = response[epc]
                    print(f"  Raw 0x{epc:02X}: {value}")

                    # Parse value if it's bytes
                    if isinstance(value, bytes):
                        if len(value) == 2:
                            int_val = int.from_bytes(value, 'big')
                            print(f"    -> {int_val} (16-bit)")
                            if 4000 <= int_val <= 7000:
                                capacity_candidates.append({
                                    'epc': epc,
                                    'description': f'Raw EPC 0x{epc:02X}',
                                    'value': int_val,
                                    'unit': 'W',
                                    'kw': int_val / 1000
                                })
                        elif len(value) == 4:
                            int_val = int.from_bytes(value, 'big')
                            print(f"    -> {int_val} (32-bit)")

            except:
                pass

    except Exception as e:
        print(f"❌ Error scanning device: {e}")

    return found_values, capacity_candidates


async def main():
    """Main scanning function."""
    print("☀️ Solar Panel Capacity Scanner")
    print("="*80)

    # Load configuration
    config = load_config()

    # Find solar devices
    solar_devices = []
    for device in config['devices']['required']:
        if device['type'] == 'solar':
            solar_devices.append(device)
            print(f"Found solar device: {device['name']} at {device['ip']}")

    if not solar_devices:
        print("❌ No solar devices found in configuration")
        return

    # Initialize API client with server details
    from pychonet import ECHONETAPIClient as api
    from pychonet.lib.udpserver import UDPServer

    # Create UDP server and API client
    udp_server = UDPServer()
    loop = asyncio.get_event_loop()
    udp_server.run('0.0.0.0', 3610, loop=loop)
    api_client = api(server=udp_server)

    # Need to discover devices first
    print("\nDiscovering devices on network...")
    for device in solar_devices:
        try:
            success = await asyncio.wait_for(api_client.discover(device['ip']), timeout=5.0)
            if success:
                print(f"✅ Discovered devices at {device['ip']}")
                # Wait for discovery to complete
                await asyncio.sleep(2)
            else:
                print(f"⚠️  No response from {device['ip']}")
        except Exception as e:
            print(f"❌ Discovery failed for {device['ip']}: {e}")

    all_candidates = []

    # Scan each solar device
    for device in solar_devices:
        values, candidates = await scan_solar_device(
            device['ip'],
            api_client,
            device.get('instance', 1)
        )
        all_candidates.extend(candidates)

    # Report findings
    print(f"\n{'='*80}")
    print("📊 CAPACITY CANDIDATE SUMMARY")
    print(f"{'='*80}")

    if all_candidates:
        print(f"\nFound {len(all_candidates)} potential capacity values:\n")

        # Sort by kW value
        all_candidates.sort(key=lambda x: x['kw'])

        for candidate in all_candidates:
            print(f"  EPC 0x{candidate['epc']:02X} ({candidate['description']}):")
            print(f"    Value: {candidate['value']} {candidate['unit']}")
            print(f"    In kW: {candidate['kw']:.2f} kW")

            # Check against expected values
            if 5.9 <= candidate['kw'] <= 6.2:
                print("    🎯 CLOSE MATCH to reported 6.07kW installation!")
            elif 4.8 <= candidate['kw'] <= 5.2:
                print("    🎯 CLOSE MATCH to observed ~5kW maximum!")
            print()
    else:
        print("\n⚠️  No capacity values found in the 4-7kW range")
        print("\nOther constant values found:")
        print("(These might use different units or encoding)")

    # No explicit shutdown needed for UDPServer
    print("\n✅ Scan complete")


if __name__ == "__main__":
    asyncio.run(main())
