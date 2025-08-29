#!/usr/bin/env python3
"""
Real-time grid power monitoring to find the correct EPC property.

Monitor multiple EPC properties simultaneously and look for:
1. Values that fluctuate (not constant like the faulty +100W from 0xE5)
2. Values in the expected range for grid export (negative values or large positive values)
3. Values that correlate with solar production changes
"""

import asyncio
import logging
import sys
from datetime import datetime
from typing import Any, Dict

# Add parent directory to Python path
sys.path.append("..")

from pychonet import ECHONETAPIClient as api
from pychonet.lib.udpserver import UDPServer

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class GridMonitor:
    def __init__(self):
        self.api_client = None
        self.udp_server = None
        self.solar_device = {
            "ip": "192.168.0.2",
            "eojgc": 0x02,
            "eojcc": 0x79,
            "instance": 31,
        }
        
        # EPC properties to monitor simultaneously
        self.epcs_to_monitor = [
            0xE0,  # Instantaneous power generation (solar)
            0xE1,  # Cumulative power generation  
            0xE2,  # Instantaneous current
            0xE3,  # Cumulative current
            0xE4,  # Instantaneous voltage
            0xE5,  # Grid power flow (known faulty - constant +100W)
            0xE6,  # Unknown
            0xE7,  # Unknown  
            0xE8,  # Unknown
            0xE9,  # Unknown
            0xEA,  # Unknown
            0xEB,  # Unknown
            0xEC,  # Unknown
            0xED,  # Unknown
            0xEE,  # Unknown
            0xEF,  # Unknown
            0xD0,  # System interconnected type
            0xD1,  # Output power restraint status
            0xD2,  # Unknown
            0xD3,  # Unknown
            0xD4,  # Unknown
            0xD5,  # Unknown
        ]
        
        # Track value history for each EPC
        self.value_history = {}
        self.last_values = {}

    async def initialize(self):
        """Initialize API client."""
        loop = asyncio.get_event_loop()
        self.udp_server = UDPServer()
        interface = "0.0.0.0"
        port = 3610

        self.udp_server.run(interface, port, loop=loop)
        self.api_client = api(server=self.udp_server)
        
        logger.info(f"🔍 Initialized ECHONET client on {interface}:{port}")

    async def read_epc_property(self, epc: int) -> Any:
        """Read a single EPC property."""
        try:
            response = await asyncio.wait_for(
                self.api_client.echonetMessage(
                    self.solar_device["ip"],
                    self.solar_device["eojgc"],
                    self.solar_device["eojcc"], 
                    self.solar_device["instance"],
                    0x62,  # Get request
                    [{"EPC": epc}]
                ),
                timeout=1.0
            )
            
            if response and epc in response:
                return response[epc]
            else:
                return None
                
        except Exception as e:
            return None

    def analyze_value(self, epc: int, value: Any) -> str:
        """Analyze if a value could be grid power flow."""
        if value is None:
            return ""
            
        analysis = []
        
        # Check if it's the known faulty property
        if epc == 0xE5:
            analysis.append("(KNOWN FAULTY +100W)")
        
        # Check for fluctuation
        if epc in self.value_history:
            history = self.value_history[epc]
            if len(history) > 1:
                # Check if values are changing
                unique_values = set(history[-5:])  # Last 5 readings
                if len(unique_values) > 1:
                    analysis.append("(FLUCTUATING)")
                elif len(unique_values) == 1:
                    analysis.append("(CONSTANT)")
        
        # Check value characteristics for grid power
        try:
            if isinstance(value, (int, float)):
                val = float(value)
                
                if val == 100:
                    analysis.append("(SUSPICIOUS +100W)")
                elif -10000 < val < -100:
                    analysis.append("(POSSIBLE EXPORT)")
                elif 100 < val < 5000:
                    analysis.append("(POSSIBLE IMPORT)")
                elif val == 0:
                    analysis.append("(ZERO/BALANCED)")
                elif abs(val) > 10000:
                    analysis.append("(SCALED VALUE?)")
                    
        except:
            analysis.append(f"(TYPE: {type(value).__name__})")
        
        return " ".join(analysis)

    async def monitor_properties(self):
        """Monitor all EPC properties in real-time."""
        logger.info("🚀 Starting real-time grid power monitoring...")
        logger.info(f"📡 Monitoring {len(self.epcs_to_monitor)} EPC properties")
        logger.info("🔍 Looking for fluctuating values that could be grid export")
        logger.info("⏹️  EV charging stopped - should see export values")
        logger.info("=" * 70)
        
        iteration = 0
        while True:
            iteration += 1
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            # Read all properties
            readings = {}
            for epc in self.epcs_to_monitor:
                value = await self.read_epc_property(epc)
                readings[epc] = value
                
                # Track history
                if epc not in self.value_history:
                    self.value_history[epc] = []
                
                if value is not None:
                    self.value_history[epc].append(value)
                    # Keep only last 10 readings
                    if len(self.value_history[epc]) > 10:
                        self.value_history[epc].pop(0)

            # Display results
            print(f"\n[{timestamp}] Iteration {iteration}")
            print("-" * 50)
            
            candidates = []
            for epc in self.epcs_to_monitor:
                value = readings[epc]
                analysis = self.analyze_value(epc, value)
                
                if value is not None:
                    print(f"0x{epc:02X}: {value} {analysis}")
                    
                    # Collect potential candidates
                    if "EXPORT" in analysis or "FLUCTUATING" in analysis:
                        if epc != 0xE5:  # Exclude known faulty
                            candidates.append((epc, value, analysis))
                else:
                    print(f"0x{epc:02X}: (no response)")
            
            # Highlight candidates
            if candidates:
                print("\n🎯 POTENTIAL GRID FLOW CANDIDATES:")
                for epc, value, analysis in candidates:
                    print(f"   0x{epc:02X}: {value} {analysis}")
            
            # Show value changes from last iteration  
            changes = []
            for epc in self.epcs_to_monitor:
                current = readings[epc]
                if epc in self.last_values and current is not None:
                    last = self.last_values[epc]
                    if current != last:
                        changes.append(f"0x{epc:02X}: {last}→{current}")
            
            if changes:
                print(f"\n📈 VALUE CHANGES: {', '.join(changes)}")
            
            self.last_values = readings.copy()
            
            # Wait before next reading
            await asyncio.sleep(3)  # 3-second intervals


async def main():
    """Main monitoring function."""
    monitor = GridMonitor()
    
    try:
        await monitor.initialize()
        await monitor.monitor_properties()
    except KeyboardInterrupt:
        logger.info("\n⏹️  Monitoring stopped by user")
        return 0
    except Exception as e:
        logger.error(f"Monitor failed: {e}")
        return 1


if __name__ == "__main__":
    import sys
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n⏹️  Monitoring stopped")
        sys.exit(0)