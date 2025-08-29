#!/usr/bin/env python3
"""
Scan solar device for correct grid power flow properties.

Current implementation uses 0xE5 (GRID_POWER_FLOW) but it returns constant +100W.
This script will scan all solar device properties to find alternatives that show
correct grid import/export values.

Focus areas:
- 0xE0-0xEF: Energy-related properties
- 0xD0-0xDF: System properties  
- 0xA0-0xAF: Device control properties
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


class SolarGridScanner:
    def __init__(self):
        self.api_client = None
        self.solar_device = None
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

    async def find_solar_device(self) -> dict[str, Any]:
        """Find the solar device in the system."""
        logger.info("🔍 Using known solar device configuration...")

        # Use known device info from main app configuration
        # This is the same device that's working in the main app
        solar_device = {
            "ip": "192.168.0.2",
            "eojgc": 0x02,
            "eojcc": 0x79,
            "instance": 31,  # Instance from config template
            "type": "solar",
        }
        
        logger.info(f"☀️ Using solar device: {solar_device['ip']} (0x{solar_device['eojgc']:02X}{solar_device['eojcc']:02X})")
        return solar_device

    async def scan_property_range(self, device: dict[str, Any], start_epc: int, end_epc: int) -> dict[int, Any]:
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
                        0x62,  # Get request
                        [{"EPC": epc}]
                    ),
                    timeout=2.0
                )

                if response and epc in response:
                    value = response[epc]
                    results[epc] = value
                    
                    # Check if this could be grid power flow
                    if self.is_grid_candidate(value):
                        logger.info(f"🔍 EPC 0x{epc:02X}: {value} (POTENTIAL GRID FLOW)")
                    else:
                        logger.debug(f"    EPC 0x{epc:02X}: {value}")

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.debug(f"    EPC 0x{epc:02X}: Error - {e}")
                continue

        return results

    def is_grid_candidate(self, value: Any) -> bool:
        """Check if a value could represent grid power flow."""
        try:
            if isinstance(value, (int, float)):
                # Grid power typically ranges from -10000W (export) to +5000W (import)
                # Look for values that could represent power flow
                if isinstance(value, int):
                    # Direct power values
                    if -10000 <= value <= 5000:
                        return True
                    # Scaled values (mW to W)  
                    if -10000000 <= value <= 5000000:
                        return True
                return False
        except:
            return False

    async def scan_all_properties(self):
        """Scan all relevant property ranges for grid power flow."""
        logger.info("🔍 Starting comprehensive solar device property scan")
        logger.info("Current 0xE5 returns constant +100W - looking for alternatives")

        # Initialize and find device
        await self.initialize()
        device = await self.find_solar_device()

        # Test current problematic property first
        logger.info("\n🧪 Testing current property (0xE5 - known faulty):")
        current_results = await self.scan_property_range(device, 0xE5, 0xE5)

        # Scan property ranges most likely to contain grid flow data
        property_ranges = [
            (0xE0, 0xEF, "Energy Properties Range"),
            (0xD0, 0xDF, "System Properties Range"), 
            (0xA0, 0xAF, "Device Control Range"),
            (0xB0, 0xBF, "Status Properties Range"),
            (0xC0, 0xCF, "Configuration Properties Range"),
        ]

        all_results = {}
        for start, end, name in property_ranges:
            logger.info(f"\n📊 {name} (0x{start:02X}-0x{end:02X})")
            range_results = await self.scan_property_range(device, start, end)
            all_results.update(range_results)

            # Show promising candidates
            candidates = {
                epc: val for epc, val in range_results.items() 
                if self.is_grid_candidate(val) and val != 100  # Exclude the faulty +100W
            }
            if candidates:
                logger.info(f"  🎯 Grid flow candidates in {name}:")
                for epc, val in candidates.items():
                    logger.info(f"    0x{epc:02X}: {val}W")

        # Summary of findings
        logger.info("\n📋 SCAN SUMMARY:")
        logger.info("=" * 50)

        # Current faulty property
        if 0xE5 in all_results:
            logger.info(f"❌ 0xE5 (current): {all_results[0xE5]}W (constant/faulty)")

        # All potential grid flow candidates
        grid_candidates = {
            epc: val for epc, val in all_results.items() 
            if self.is_grid_candidate(val) and epc != 0xE5
        }

        if grid_candidates:
            logger.info(f"✅ Found {len(grid_candidates)} potential grid flow properties:")
            for epc, val in sorted(grid_candidates.items()):
                # Try to interpret the value
                interpretation = self.interpret_grid_value(val)
                logger.info(f"  0x{epc:02X}: {val} {interpretation}")
        else:
            logger.info("❌ No alternative grid flow properties found")

        logger.info("=" * 50)

    def interpret_grid_value(self, value: Any) -> str:
        """Interpret what a grid value might represent."""
        try:
            if isinstance(value, int):
                if value > 0:
                    if value < 100:
                        return "(possible small import)"
                    elif 100 <= value <= 5000:
                        return "(possible grid import - W)"
                    elif value > 10000:
                        return "(possible scaled value - mW?)"
                elif value < 0:
                    if value > -100:
                        return "(possible small export)"
                    elif -5000 <= value <= -100:
                        return "(possible grid export - W)"
                    elif value < -10000:
                        return "(possible scaled export - mW?)"
                else:
                    return "(zero/balanced)"
            return f"(type: {type(value).__name__})"
        except:
            return "(unknown format)"


async def main():
    """Main scanning function."""
    scanner = SolarGridScanner()
    
    try:
        await scanner.scan_all_properties()
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))