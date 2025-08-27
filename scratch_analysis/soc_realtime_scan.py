#!/usr/bin/env python3
"""
Scan for real-time Home Battery SoC properties beyond the standard ones.

The current implementation uses:
- 0xBF (USER_DISPLAY_SOC) - User display SOC (preferred)
- 0xC9 (DISPLAY_SOC_ALT) - Alternative display SOC
- 0xE2 (REMAINING_STORED_ELECTRICITY) - Technical SOC

This script will scan ALL properties on the battery device to find any that:
1. Have values in the 30-50% range (matching current SoC)
2. Have values in the 3000-5000 range (100x scale)
3. Update more frequently than every 30 minutes

High confidence candidates for real-time SoC:
- 0xE2: REMAINING_STORED_ELECTRICITY (technical) - already using this
- 0xE5: REMAINING_CAPACITY_PERCENTAGE - percentage format
- 0xBA: REMAINING_CAPACITY - capacity format
- 0xD3: CHARGING_DISCHARGING_AMOUNT - power flow (not SoC but real-time)

Additional properties to scan systematically:
- All 0xA0-0xFF range (device-specific properties)
- Focus on 0xE0-0xEF range (energy-related properties)
"""

import asyncio
import logging
import sys
from datetime import datetime
from typing import Any

# Add parent directory to Python path
sys.path.append("..")

from pychonet import ECHONETAPIClient as api
from pychonet.lib.udpserver import UDPServer

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class SoCRealtimeScan:
    def __init__(self):
        self.api_client = None
        self.battery_device = None
        self.results = {}

    async def initialize(self):
        """Initialize API client."""
        # Set up UDP server
        loop = asyncio.get_event_loop()
        self.udp_server = UDPServer()
        interface = "0.0.0.0"
        port = 3610

        self.udp_server.run(interface, port, loop=loop)
        self.api_client = api(server=self.udp_server)

    async def find_battery_device(self) -> dict[str, Any]:
        """Find the battery device in the system."""
        logger.info("🔍 Discovering devices...")

        # Discover devices on network
        discovery_result = await self.api_client.discover()
        logger.info(f"Discovery found {len(discovery_result)} devices")

        # Look for battery device (0x027D = Storage Battery)
        for ip, device_info in discovery_result.items():
            if hasattr(device_info, "instances"):
                for instance in device_info.instances:
                    if instance.eojgc == 0x02 and instance.eojcc == 0x7D:
                        battery_device = {
                            "ip": ip,
                            "eojgc": instance.eojgc,
                            "eojcc": instance.eojcc,
                            "instance": instance.eojci,
                            "type": "battery",
                        }
                        logger.info(
                            f"🔋 Found battery device: {ip} (0x{instance.eojgc:02X}{instance.eojcc:02X})"
                        )
                        return battery_device

        raise RuntimeError("❌ No battery device found")

    async def scan_property_range(
        self, device: dict[str, Any], start_epc: int, end_epc: int
    ) -> dict[int, Any]:
        """Scan a range of EPC properties for values."""
        logger.info(f"📡 Scanning EPC range 0x{start_epc:02X} - 0x{end_epc:02X}")

        results = {}
        api_client = self.api_client

        for epc in range(start_epc, end_epc + 1):
            try:
                # Try direct property read
                response = await asyncio.wait_for(
                    api_client.echonetMessage(
                        device["ip"],
                        device["eojgc"],
                        device["eojcc"],
                        device["instance"],
                        0x62,  # GET command
                        [{"EPC": epc}],
                    ),
                    timeout=2.0,
                )

                if response and epc in response:
                    value = response[epc]
                    results[epc] = value

                    # Check if this could be a SoC value
                    is_candidate = self.is_soc_candidate(value)
                    candidate_str = " 🎯" if is_candidate else ""

                    logger.info(f"  0x{epc:02X}: {value}{candidate_str}")

            except (TimeoutError, Exception):
                # Skip properties that don't respond
                pass

        return results

    def is_soc_candidate(self, value: Any) -> bool:
        """Check if a value could be a real-time SoC reading."""
        if value is None:
            return False

        # Convert to numeric if possible
        numeric_val = None
        if isinstance(value, (int, float)):
            numeric_val = float(value)
        elif isinstance(value, str):
            try:
                numeric_val = float(value)
            except ValueError:
                # Handle fraction format like "1500/3000"
                if "/" in value:
                    try:
                        num, denom = map(int, value.split("/"))
                        if denom > 0:
                            numeric_val = (num / denom) * 100
                    except ValueError:
                        pass

        if numeric_val is None:
            return False

        # Check for SoC-like ranges
        # Range 1: Direct percentage (0-100)
        if 0 <= numeric_val <= 100:
            return True

        # Range 2: Technical units (0-10000, 0.01% increments)
        if 0 <= numeric_val <= 10000:
            return True

        # Range 3: Specific range mentioned by user (3000-5000)
        if 3000 <= numeric_val <= 5000:
            return True

        return False

    async def monitor_property_updates(
        self, device: dict[str, Any], epc_codes: list[int], duration_minutes: int = 5
    ) -> dict[int, list[tuple[datetime, Any]]]:
        """Monitor specific properties for update frequency."""
        logger.info(f"⏱️  Monitoring {len(epc_codes)} properties for {duration_minutes} minutes")

        api_client = self.api_client
        monitoring_data = {epc: [] for epc in epc_codes}

        end_time = datetime.now().timestamp() + (duration_minutes * 60)

        while datetime.now().timestamp() < end_time:
            for epc in epc_codes:
                try:
                    response = await asyncio.wait_for(
                        api_client.echonetMessage(
                            device["ip"],
                            device["eojgc"],
                            device["eojcc"],
                            device["instance"],
                            0x62,  # GET command
                            [{"EPC": epc}],
                        ),
                        timeout=2.0,
                    )

                    if response and epc in response:
                        value = response[epc]
                        timestamp = datetime.now()
                        monitoring_data[epc].append((timestamp, value))

                except Exception:
                    pass

            # Wait 10 seconds between polls
            await asyncio.sleep(10)

        return monitoring_data

    def analyze_update_frequency(
        self, monitoring_data: dict[int, list[tuple[datetime, Any]]]
    ) -> dict[int, dict[str, Any]]:
        """Analyze how frequently each property updates."""
        analysis = {}

        for epc, readings in monitoring_data.items():
            if len(readings) < 2:
                analysis[epc] = {"status": "insufficient_data", "update_count": len(readings)}
                continue

            # Count actual value changes
            value_changes = 0
            last_value = readings[0][1]

            for timestamp, value in readings[1:]:
                if value != last_value:
                    value_changes += 1
                    last_value = value

            # Calculate time span
            time_span = (readings[-1][0] - readings[0][0]).total_seconds() / 60  # minutes

            analysis[epc] = {
                "total_readings": len(readings),
                "value_changes": value_changes,
                "time_span_minutes": time_span,
                "change_frequency": value_changes / time_span if time_span > 0 else 0,
                "first_value": readings[0][1],
                "last_value": readings[-1][1],
                "is_static": value_changes == 0,
            }

        return analysis

    async def run_comprehensive_scan(self):
        """Run comprehensive scan for real-time SoC properties."""
        try:
            # Initialize and find battery
            await self.initialize()
            device = await self.find_battery_device()

            logger.info("\n" + "=" * 60)
            logger.info("🔋 HOME BATTERY SOC REAL-TIME PROPERTY SCAN")
            logger.info("=" * 60)

            # Scan device-specific property ranges
            property_ranges = [
                (0xA0, 0xAF, "Device Control Range"),
                (0xB0, 0xBF, "Device Status Range"),
                (0xC0, 0xCF, "Device Config Range"),
                (0xD0, 0xDF, "Device Measurement Range"),
                (0xE0, 0xEF, "Device Energy Range"),
                (0xF0, 0xFF, "Device Extended Range"),
            ]

            all_candidates = {}

            for start, end, name in property_ranges:
                logger.info(f"\n📊 {name} (0x{start:02X}-0x{end:02X})")
                range_results = await self.scan_property_range(device, start, end)

                # Filter for SoC candidates
                candidates = {
                    epc: val for epc, val in range_results.items() if self.is_soc_candidate(val)
                }
                all_candidates.update(candidates)

            if not all_candidates:
                logger.warning("⚠️  No SoC candidate properties found")
                return

            logger.info(f"\n🎯 Found {len(all_candidates)} SoC candidate properties:")
            for epc, value in all_candidates.items():
                logger.info(f"  0x{epc:02X}: {value}")

            # Monitor the most promising candidates
            monitor_epcs = list(all_candidates.keys())

            # Always include known SoC properties for comparison
            known_soc_epcs = [0xBF, 0xC9, 0xE2, 0xE5, 0xBA]
            for epc in known_soc_epcs:
                if epc not in monitor_epcs:
                    monitor_epcs.append(epc)

            logger.info(f"\n⏱️  Monitoring {len(monitor_epcs)} properties for update frequency...")
            monitoring_data = await self.monitor_property_updates(
                device, monitor_epcs, duration_minutes=3
            )

            # Analyze update patterns
            logger.info("\n📈 UPDATE FREQUENCY ANALYSIS:")
            logger.info("-" * 50)

            frequency_analysis = self.analyze_update_frequency(monitoring_data)

            real_time_candidates = []

            for epc, stats in frequency_analysis.items():
                epc_name = f"0x{epc:02X}"

                if stats.get("status") == "insufficient_data":
                    logger.info(f"{epc_name}: No data collected")
                    continue

                changes = stats["value_changes"]
                readings = stats["total_readings"]
                time_span = stats["time_span_minutes"]
                is_static = stats["is_static"]

                status = "STATIC" if is_static else f"{changes} changes"

                logger.info(f"{epc_name}: {readings} readings, {status} over {time_span:.1f}min")
                logger.info(f"      First: {stats['first_value']}, Last: {stats['last_value']}")

                # Flag real-time candidates (properties that change)
                if not is_static and changes > 0:
                    real_time_candidates.append((epc, stats))
                    logger.info("      🚀 REAL-TIME CANDIDATE!")

            # Summary
            logger.info("\n" + "=" * 60)
            logger.info("📋 SUMMARY")
            logger.info("=" * 60)

            if real_time_candidates:
                logger.info(f"✅ Found {len(real_time_candidates)} real-time SoC candidates:")
                for epc, stats in real_time_candidates:
                    logger.info(
                        f"  • 0x{epc:02X}: {stats['value_changes']} updates in {stats['time_span_minutes']:.1f}min"
                    )
            else:
                logger.info("❌ No real-time updating properties found")
                logger.info(
                    "   This suggests SoC might indeed update infrequently (30min intervals)"
                )

            logger.info("\n💡 Current system uses these SoC properties:")
            for epc in [0xBF, 0xC9, 0xE2]:
                if epc in frequency_analysis:
                    stats = frequency_analysis[epc]
                    status = (
                        "STATIC"
                        if stats.get("is_static")
                        else f"{stats.get('value_changes', 0)} changes"
                    )
                    logger.info(f"  • 0x{epc:02X}: {status}")
                else:
                    logger.info(f"  • 0x{epc:02X}: Not available")

        except Exception as e:
            logger.error(f"❌ Scan failed: {e}")
            raise
        finally:
            if hasattr(self, "udp_server") and self.udp_server:
                self.udp_server.stop()


async def main():
    """Main entry point."""
    scanner = SoCRealtimeScan()
    await scanner.run_comprehensive_scan()


if __name__ == "__main__":
    asyncio.run(main())
