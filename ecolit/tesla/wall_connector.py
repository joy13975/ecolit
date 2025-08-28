#!/usr/bin/env python3
"""Tesla Wall Connector Gen 3 API client for reading live status."""

import asyncio
import ssl
from typing import Dict, Optional, Any
import aiohttp
from datetime import datetime


class WallConnectorClient:
    """Client for Tesla Wall Connector Gen 3 API."""

    def __init__(self, host: str, port: int = 80, use_https: bool = False):
        """Initialize Wall Connector client.

        Args:
            host: IP address or hostname of the Wall Connector
            port: Port number (default 80 for HTTP)
            use_https: Whether to use HTTPS (default False for HTTP)
        """
        self.host = host
        self.port = port
        self.use_https = use_https
        protocol = "https" if use_https else "http"
        self.base_url = f"{protocol}://{host}:{port}" if port != (443 if use_https else 80) else f"{protocol}://{host}"
        self.timeout = aiohttp.ClientTimeout(total=10)

        # Create SSL context for HTTPS
        if use_https:
            self.ssl_context = ssl.create_default_context()
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE
        else:
            self.ssl_context = None

    async def _get_api(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Make GET request to Wall Connector API.

        Args:
            endpoint: API endpoint path (e.g., "/api/1/vitals")

        Returns:
            JSON response as dict or None if error
        """
        url = f"{self.base_url}{endpoint}"

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                ssl_param = self.ssl_context if self.use_https else None
                async with session.get(url, ssl=ssl_param) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Wall Connector API error: HTTP {response.status}")
                        return None
        except aiohttp.ClientConnectorError as e:
            print(f"❌ Cannot connect to Wall Connector at {self.base_url}")
            print(f"   Error: {e}")
            return None
        except asyncio.TimeoutError:
            print(f"⏱️  Wall Connector connection timed out")
            return None
        except Exception as e:
            print(f"❌ Unexpected error accessing Wall Connector: {e}")
            return None

    async def get_vitals(self) -> Optional[Dict[str, Any]]:
        """Get real-time vitals from Wall Connector.

        Returns:
            Dict with vitals data or None if error
        """
        return await self._get_api("/api/1/vitals")

    async def get_lifetime(self) -> Optional[Dict[str, Any]]:
        """Get lifetime statistics from Wall Connector.

        Returns:
            Dict with lifetime stats or None if error
        """
        return await self._get_api("/api/1/lifetime")

    async def get_wifi_status(self) -> Optional[Dict[str, Any]]:
        """Get WiFi status from Wall Connector.

        Returns:
            Dict with WiFi status or None if error
        """
        return await self._get_api("/api/1/wifi_status")

    async def get_version(self) -> Optional[Dict[str, Any]]:
        """Get version information from Wall Connector.

        Returns:
            Dict with version info or None if error
        """
        return await self._get_api("/api/1/version")


def format_wall_connector_status(vitals: Dict[str, Any], lifetime: Dict[str, Any] = None) -> str:
    """Format Wall Connector status data for display.

    Args:
        vitals: Vitals data from API
        lifetime: Optional lifetime stats from API

    Returns:
        Formatted string for display
    """
    if not vitals:
        return "❌ No Wall Connector data available"

    lines = []

    # Connection status
    vehicle_connected = vitals.get("vehicle_connected", False)
    contactor_closed = vitals.get("contactor_closed", False)

    if vehicle_connected:
        lines.append("🔌 Vehicle: Connected")
        if contactor_closed:
            lines.append("⚡ Status: Charging Active")
        else:
            lines.append("⏸️  Status: Connected (Not Charging)")
    else:
        lines.append("🔌 Vehicle: Not Connected")

    # EVSE State
    evse_state = vitals.get("evse_state")
    if evse_state is not None:
        state_map = {
            0: "Starting",
            1: "Standby (Not Connected)",
            2: "Vehicle Detected",  
            3: "Ready to Charge",
            4: "Vehicle Connected (Not Charging)",
            5: "Sleep Mode",
            6: "Booting",
            7: "Error/Fault",
            8: "Self Test",
            9: "Vehicle Connected (Waiting)",
            10: "Charging Starting",
            11: "Charging",
            12: "Charging Stopping",
            13: "Charging Complete",
            14: "Vehicle Connected (Scheduled)",
        }
        state_name = state_map.get(evse_state, f"Unknown State ({evse_state})")
        lines.append(f"📊 Charger State: {state_name}")

    # Power delivery
    if contactor_closed:
        vehicle_current = vitals.get("vehicle_current_a", 0)
        grid_voltage = vitals.get("grid_v", 0)

        if vehicle_current and grid_voltage:
            power_kw = (vehicle_current * grid_voltage) / 1000
            lines.append(f"⚡ Current: {vehicle_current:.1f}A")
            lines.append(f"🔌 Voltage: {grid_voltage:.0f}V")
            lines.append(f"💪 Power: {power_kw:.1f}kW")

    # Session info
    session_energy = vitals.get("session_energy_wh", 0)
    session_time = vitals.get("session_s", 0)

    if session_energy > 0:
        lines.append(f"📈 Session Energy: {session_energy / 1000:.2f}kWh")

        if session_time > 0:
            hours = session_time // 3600
            minutes = (session_time % 3600) // 60
            if hours > 0:
                lines.append(f"⏱️  Session Duration: {hours}h {minutes}m")
            else:
                lines.append(f"⏱️  Session Duration: {minutes}m")

    # Temperature monitoring
    pcba_temp = vitals.get("pcba_temp_c")
    handle_temp = vitals.get("handle_temp_c")

    if pcba_temp is not None or handle_temp is not None:
        temps = []
        if pcba_temp is not None:
            temps.append(f"PCB: {pcba_temp:.0f}°C")
        if handle_temp is not None:
            temps.append(f"Handle: {handle_temp:.0f}°C")
        lines.append(f"🌡️  Temps: {', '.join(temps)}")

    # Grid info
    grid_hz = vitals.get("grid_hz")
    if grid_hz:
        lines.append(f"🔌 Grid: {grid_voltage:.0f}V @ {grid_hz:.1f}Hz")

    # Alerts
    current_alerts = vitals.get("current_alerts", [])
    if current_alerts:
        lines.append(f"⚠️  Alerts: {', '.join(current_alerts)}")

    # Lifetime stats if available
    if lifetime:
        total_energy = lifetime.get("energy_wh", 0)
        charge_starts = lifetime.get("charge_starts", 0)

        if total_energy > 0:
            lines.append("")
            lines.append("📊 LIFETIME STATS:")
            lines.append(f"   Total Energy: {total_energy / 1000:.1f}kWh")
            lines.append(f"   Charge Sessions: {charge_starts}")

            uptime = lifetime.get("uptime_s", 0)
            if uptime > 0:
                days = uptime // 86400
                lines.append(f"   Uptime: {days} days")

    return "\n".join(lines)


async def test_wall_connector(host: str):
    """Test Wall Connector connection and display status.

    Args:
        host: IP address or hostname of Wall Connector
    """
    print(f"\n🔍 Testing Wall Connector at {host}...")
    print("-" * 40)

    client = WallConnectorClient(host)

    # Get vitals
    vitals = await client.get_vitals()
    if vitals:
        print("✅ Successfully connected to Wall Connector")

        # Get lifetime stats
        lifetime = await client.get_lifetime()

        # Format and display
        status = format_wall_connector_status(vitals, lifetime)
        print("\n" + status)

        # Show version info
        version = await client.get_version()
        if version:
            fw_version = version.get("firmware_version", "Unknown")
            print(f"\n📱 Firmware: {fw_version}")
    else:
        print("❌ Failed to connect to Wall Connector")
        print("💡 Check that:")
        print("   - Wall Connector is powered on")
        print("   - WiFi is configured and connected")
        print("   - IP address is correct")
        print("   - Port 443 is accessible")


if __name__ == "__main__":
    # Test with a sample IP
    import sys

    if len(sys.argv) > 1:
        host = sys.argv[1]
    else:
        print("Usage: python wall_connector.py <wall_connector_ip>")
        print("Example: python wall_connector.py 192.168.1.100")
        sys.exit(1)

    asyncio.run(test_wall_connector(host))
