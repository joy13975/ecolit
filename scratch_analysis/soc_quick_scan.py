#!/usr/bin/env python3
"""
Quick scan to identify all battery properties and look for real-time SoC candidates.
Uses raw ECHONET commands to avoid conflicts with running system.
"""

import logging
import socket
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class QuickSoCScanner:
    def __init__(self):
        self.battery_ip = None
        self.sock = None

    def find_battery_ip(self) -> str:
        """Find battery device IP from config or network scan."""
        # Battery IP from devices.yaml
        candidate_ips = [
            "192.168.0.2",  # Actual battery IP from config
        ]

        logger.info("🔍 Looking for battery device...")

        # Try to ping each IP to see which responds
        for ip in candidate_ips:
            if self.test_ip_connectivity(ip):
                logger.info(f"🔋 Found responsive device at {ip}")
                return ip

        # If no predefined IP works, ask user
        logger.error("❌ Could not find battery device automatically")
        logger.info("💡 You can manually set the battery IP in the script")
        logger.info("   Check your home energy system documentation for the battery IP")
        raise RuntimeError("Battery device IP not found")

    def test_ip_connectivity(self, ip: str) -> bool:
        """Test if an IP responds to ECHONET requests."""
        try:
            # Create a UDP socket for ECHONET communication
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)

            # ECHONET Lite device discovery frame
            # Format: EHD1(0x10) + EHD2(0x81) + TID(0x0001) + SEOJ(0x0EF001) + DEOJ(0x0EF001) + ESV(0x62) + OPC(0x01) + EPC(0x80) + PDC(0x00)
            discovery_frame = bytes(
                [
                    0x10,
                    0x81,  # EHD1, EHD2
                    0x00,
                    0x01,  # TID
                    0x0E,
                    0xF0,
                    0x01,  # SEOJ (NodeProfile)
                    0x0E,
                    0xF0,
                    0x01,  # DEOJ (NodeProfile)
                    0x62,  # ESV (Get_Req)
                    0x01,  # OPC (1 property)
                    0x80,
                    0x00,  # EPC 0x80 (Operation Status), PDC 0
                ]
            )

            sock.sendto(discovery_frame, (ip, 3610))
            response, addr = sock.recvfrom(1024)
            sock.close()

            # If we get any response, the device exists
            return len(response) > 0

        except Exception:
            if sock:
                sock.close()
            return False

    def scan_battery_properties_manual(self, battery_ip: str):
        """Manually scan battery properties using raw UDP."""
        logger.info(f"📡 Scanning battery properties on {battery_ip}")
        logger.info("=" * 60)

        # Define EPC ranges to scan
        epc_ranges = [
            (0xA0, 0xAF, "Control Range"),
            (0xB0, 0xBF, "Status Range"),
            (0xC0, 0xCF, "Config Range"),
            (0xD0, 0xDF, "Measurement Range"),
            (0xE0, 0xEF, "Energy Range"),
        ]

        soc_candidates = []

        for start_epc, end_epc, range_name in epc_ranges:
            logger.info(f"\n📊 {range_name} (0x{start_epc:02X}-0x{end_epc:02X})")

            for epc in range(start_epc, end_epc + 1):
                try:
                    value = self.read_epc_property(battery_ip, epc)
                    if value is not None:
                        # Check if this could be a SoC value
                        is_candidate = self.analyze_soc_candidate(value)
                        candidate_str = " 🎯 SOC CANDIDATE!" if is_candidate else ""

                        logger.info(f"  0x{epc:02X}: {value}{candidate_str}")

                        if is_candidate:
                            soc_candidates.append((epc, value))

                except Exception:
                    # Skip properties that fail
                    pass

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("📋 SOC CANDIDATE SUMMARY")
        logger.info("=" * 60)

        if soc_candidates:
            logger.info(f"✅ Found {len(soc_candidates)} potential SoC properties:")
            for epc, value in soc_candidates:
                logger.info(f"  • 0x{epc:02X}: {value}")

            logger.info("\n💡 Known SoC properties for comparison:")
            known_soc_props = [
                (0xBF, "USER_DISPLAY_SOC"),
                (0xC9, "DISPLAY_SOC_ALT"),
                (0xE2, "REMAINING_STORED_ELECTRICITY"),
                (0xE5, "REMAINING_CAPACITY_PERCENTAGE"),
            ]

            for epc, name in known_soc_props:
                try:
                    value = self.read_epc_property(battery_ip, epc)
                    if value is not None:
                        logger.info(f"  • 0x{epc:02X} ({name}): {value}")
                    else:
                        logger.info(f"  • 0x{epc:02X} ({name}): Not available")
                except:
                    logger.info(f"  • 0x{epc:02X} ({name}): Read failed")
        else:
            logger.info("❌ No SoC candidate properties found")
            logger.info("   Current SoC properties may be the only available ones")

    def read_epc_property(self, ip: str, epc: int) -> Any:
        """Read a specific EPC property using raw ECHONET."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3.0)

            # ECHONET frame to read property from storage battery (0x027D)
            frame = bytes(
                [
                    0x10,
                    0x81,  # EHD1, EHD2
                    0x00,
                    0x01,  # TID
                    0x0E,
                    0xF0,
                    0x01,  # SEOJ (NodeProfile)
                    0x02,
                    0x7D,
                    0x01,  # DEOJ (Storage Battery, instance 1)
                    0x62,  # ESV (Get_Req)
                    0x01,  # OPC (1 property)
                    epc,
                    0x00,  # EPC, PDC 0
                ]
            )

            sock.sendto(frame, (ip, 3610))
            response, addr = sock.recvfrom(1024)
            sock.close()

            # Parse response
            if len(response) >= 12:
                esv = response[10]  # Response service
                if esv == 0x72:  # Get_Res (success)
                    if len(response) >= 14:
                        pdc = response[13]  # Property data counter
                        if pdc > 0 and len(response) >= 14 + pdc:
                            # Extract property data
                            prop_data = response[14 : 14 + pdc]
                            return self.parse_property_data(prop_data)

            return None

        except Exception:
            if sock:
                sock.close()
            return None

    def parse_property_data(self, data: bytes) -> Any:
        """Parse ECHONET property data."""
        if len(data) == 0:
            return None
        elif len(data) == 1:
            return data[0]
        elif len(data) == 2:
            return (data[0] << 8) | data[1]
        elif len(data) == 4:
            return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
        else:
            # Return as hex string for complex data
            return data.hex()

    def analyze_soc_candidate(self, value: Any) -> bool:
        """Analyze if a value could represent battery SoC."""
        if value is None:
            return False

        # Convert to numeric
        numeric_val = None
        try:
            if isinstance(value, (int, float)):
                numeric_val = float(value)
            elif isinstance(value, str) and value.isdigit():
                numeric_val = float(value)
        except:
            return False

        if numeric_val is None:
            return False

        # SoC candidate ranges:
        # 1. Direct percentage (0-100)
        if 0 <= numeric_val <= 100:
            return True

        # 2. Technical units (0-10000, representing 0.01% increments)
        if 0 <= numeric_val <= 10000:
            return True

        # 3. User-mentioned range (3000-5000)
        if 3000 <= numeric_val <= 5000:
            return True

        return False

    def run_scan(self):
        """Run the battery property scan."""
        try:
            # Find battery IP
            battery_ip = self.find_battery_ip()

            logger.info("🔋 HOME BATTERY PROPERTY SCAN")
            logger.info("=" * 60)
            logger.info(f"Target: {battery_ip}")
            logger.info("Purpose: Find real-time SoC properties")

            # Scan all battery properties
            self.scan_battery_properties_manual(battery_ip)

        except Exception as e:
            logger.error(f"❌ Scan failed: {e}")
            logger.info("\n💡 If your battery IP is different, edit this script and set:")
            logger.info("   candidate_ips = ['YOUR_BATTERY_IP_HERE']")


if __name__ == "__main__":
    scanner = QuickSoCScanner()
    scanner.run_scan()
