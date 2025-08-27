"""Core functionality for ECHONET Lite communication and management."""

import asyncio
import logging
from typing import Any

from pychonet import ECHONETAPIClient as api
from pychonet import EchonetInstance, HomeSolarPower, StorageBattery
from pychonet.lib.udpserver import UDPServer

logger = logging.getLogger(__name__)


class EcoliteManager:
    """Manager for ECHONET Lite device communication and monitoring."""

    def __init__(self, config: dict[str, Any]):
        """Initialize the manager with configuration."""
        self.config = config
        self.devices: dict[str, Any] = {}
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self.api_client = None
        self.udp_server = None
        self.discovered_ips: set[str] = set()
        self.solar_instance = None
        self.battery_instance = None

    async def start(self) -> None:
        """Start the ECHONET Lite manager."""
        logger.info("Starting ECHONET Lite manager")
        self._running = True

        # Initialize ECHONET Lite API
        await self._initialize_api()

        # Validate required devices if specified
        await self._validate_required_devices()

        self._tasks.append(asyncio.create_task(self._discover_devices()))
        self._tasks.append(asyncio.create_task(self._monitor_loop()))

    async def stop(self) -> None:
        """Stop the ECHONET Lite manager."""
        logger.info("Stopping ECHONET Lite manager")
        self._running = False

        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _validate_required_devices(self) -> None:
        """Validate that all required devices are present and accessible."""
        required_devices = self.config.get("devices", {}).get("required", [])

        if not required_devices:
            logger.info("No required devices specified - running in discovery mode")
            return

        logger.info(f"Validating {len(required_devices)} required device(s)...")
        missing_devices = []

        for device_config in required_devices:
            device_name = device_config.get("name", "Unknown")
            ip = device_config.get("ip")
            eojgc = device_config.get("eojgc")
            eojcc = device_config.get("eojcc")
            instance = device_config.get("instance")

            if not all([ip, eojgc is not None, eojcc is not None, instance is not None]):
                logger.error(f"Invalid device configuration for {device_name}")
                missing_devices.append(device_name)
                continue

            try:
                # Try to discover this specific device
                success = await asyncio.wait_for(self.api_client.discover(ip), timeout=10)

                if not success:
                    logger.error(f"Failed to discover devices at {ip} for {device_name}")
                    missing_devices.append(device_name)
                    continue

                # Wait for discovery to complete
                for _ in range(300):
                    await asyncio.sleep(0.01)
                    if ip in self.api_client._state and "discovered" in self.api_client._state[ip]:
                        break

                # Check if the specific device exists
                if ip in self.api_client._state and "instances" in self.api_client._state[ip]:
                    instances = self.api_client._state[ip]["instances"]
                    found = False

                    if eojgc in instances and eojcc in instances[eojgc]:
                        if instance in instances[eojgc][eojcc]:
                            found = True
                            logger.info(f"✅ Required device validated: {device_name}")
                            
                            # Log available properties for debugging with meaningful names
                            if ip in self.api_client._state and "instances" in self.api_client._state[ip]:
                                inst_state = self.api_client._state[ip]["instances"][eojgc][eojcc][instance]
                                available_props = list(inst_state.keys())
                                
                                # Map EPC codes to meaningful names
                                epc_names = {
                                    # Common properties
                                    0x80: "Operation status", 0x81: "Installation location", 0x82: "Standard version info",
                                    0x83: "ID number", 0x84: "Instantaneous power", 0x85: "Cumulative power",
                                    0x88: "Fault status", 0x8A: "Manufacturer code", 0x8B: "Business facility code",
                                    0x8C: "Product code", 0x8D: "Production number", 0x8E: "Production date",
                                    0x8F: "Power saving operation", 0x93: "Remote control", 0x97: "Current time",
                                    0x98: "Current date", 0x99: "Power limit", 0x9A: "Cumulative runtime",
                                    0x9D: "Status notification property map", 0x9E: "Set property map", 0x9F: "Get property map",
                                    
                                    # Solar specific
                                    0xC0: "Power factor", 0xE0: "Instantaneous power generation", 0xE1: "Cumulative power generation",
                                    0xE2: "Instantaneous current", 0xE3: "Cumulative current", 0xE4: "Instantaneous voltage",
                                    
                                    # Battery specific  
                                    0xBA: "Battery remaining capacity", 0xC5: "Working operation status",
                                    0xD3: "Charging/discharging amount", 0xDA: "Operation mode", 0xE2: "Remaining stored electricity",
                                    0xE3: "Charging power", 0xE4: "Discharging power", 0xE5: "Remaining capacity percentage",
                                    
                                    # Smart meter specific
                                    0xE7: "Measured instantaneous power", 0xE8: "Measured cumulative power consumption (normal)",
                                    0xEA: "Measured cumulative power generation (reverse)",
                                }
                                
                                prop_names = []
                                for p in available_props:
                                    if isinstance(p, int):
                                        name = epc_names.get(p, f"Unknown(0x{p:02X})")
                                        prop_names.append(name)
                                
                                logger.debug(f"Available properties for {device_name}: {prop_names}")
                            
                            # Store the raw ECHONET instance for direct access
                            # The instances are already in api_client._state
                            if device_config.get("type") == "solar" and eojcc == 0x79:
                                self.solar_instance = {
                                    "ip": ip,
                                    "eojgc": eojgc, 
                                    "eojcc": eojcc,
                                    "instance": instance
                                }
                                logger.info(f"Stored solar device info for {device_name}")
                            elif device_config.get("type") == "battery" and eojcc == 0x7D:
                                self.battery_instance = {
                                    "ip": ip,
                                    "eojgc": eojgc,
                                    "eojcc": eojcc,
                                    "instance": instance
                                }
                                logger.info(f"Stored battery device info for {device_name}")

                    if not found:
                        logger.error(
                            f"❌ Required device not found: {device_name} (0x{eojgc:02X}{eojcc:02X}:{instance})"
                        )
                        missing_devices.append(device_name)
                else:
                    logger.error(f"❌ No devices found at {ip} for {device_name}")
                    missing_devices.append(device_name)

            except Exception as e:
                logger.error(f"❌ Error validating {device_name}: {e}")
                missing_devices.append(device_name)

        if missing_devices:
            error_msg = f"Required devices missing or not accessible: {', '.join(missing_devices)}"
            logger.error(error_msg)
            logger.error("Application cannot start without required devices")
            raise RuntimeError(error_msg)

        logger.info("✅ All required devices validated successfully")

    async def _initialize_api(self) -> None:
        """Initialize the ECHONET Lite API client."""
        try:
            # Set up UDP server
            self.udp_server = UDPServer()
            loop = asyncio.get_event_loop()
            echonet_config = self.config.get("network", {}).get("echonet", {})
            port = echonet_config.get("port", 3610)
            interface = echonet_config.get("interface", "0.0.0.0")

            self.udp_server.run(interface, port, loop=loop)
            self.api_client = api(server=self.udp_server)

            logger.info(f"ECHONET Lite API initialized on {interface}:{port}")
        except Exception as e:
            logger.error(f"Failed to initialize ECHONET Lite API: {e}")
            raise

    async def _discover_devices(self) -> None:
        """Discover ECHONET Lite devices on the network."""
        initial_discovery = True

        # Check if we have required devices configured
        required_devices = self.config.get("devices", {}).get("required", [])
        scan_ranges = self.config.get("network", {}).get("scan_ranges", [])
        
        # If we have required devices but no scan ranges, skip ongoing discovery
        if required_devices and not scan_ranges:
            logger.info("Required devices configured - skipping network discovery")
            return

        while self._running:
            try:
                if initial_discovery:
                    logger.info("Starting initial device discovery...")
                else:
                    logger.debug("Running periodic device discovery...")

                # Get scan ranges from config
                if not scan_ranges:
                    # Discovery mode - use default ranges
                    scan_ranges = ["192.168.1", "192.168.0", "192.168.11", "10.0.0"]
                    logger.info("Running in discovery mode - scanning common network ranges")
                else:
                    logger.info(f"Scanning configured ranges: {scan_ranges}")

                new_devices = 0
                for prefix in scan_ranges:
                    if not self._running:
                        break

                    # Quick scan of subnet
                    for i in range(1, 255):
                        if not self._running:
                            break

                        ip = f"{prefix}.{i}"
                        if ip in self.discovered_ips:
                            continue

                        try:
                            success = await asyncio.wait_for(
                                self.api_client.discover(ip), timeout=0.3
                            )

                            if success and self.api_client.devices:
                                self.discovered_ips.add(ip)
                                device_count = len(self.api_client.devices)
                                new_devices += device_count

                                for _device_id, instance in self.api_client.devices.items():
                                    await self._process_discovered_device(ip, instance)

                        except TimeoutError:
                            continue
                        except Exception:
                            continue

                if initial_discovery:
                    logger.info(f"Initial discovery complete: Found {len(self.devices)} device(s)")
                    initial_discovery = False
                elif new_devices > 0:
                    logger.info(f"Discovered {new_devices} new device(s)")

                # Wait before next discovery cycle
                polling_interval = self.config.get("app", {}).get("polling_interval", 10)
                await asyncio.sleep(60 if not initial_discovery else polling_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during device discovery: {e}")
                await asyncio.sleep(5)

    async def _monitor_loop(self) -> None:
        """Main monitoring loop for device data."""
        logger.info("Starting monitoring loop for device polling")
        while self._running:
            try:
                await self._poll_devices()
                polling_interval = self.config.get("app", {}).get("polling_interval", 10)
                await asyncio.sleep(polling_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(5)

    async def _process_discovered_device(self, ip: str, instance: EchonetInstance) -> None:
        """Process a discovered ECHONET Lite device."""
        try:
            # Create unique device ID
            device_id = f"{ip}_{instance.eojgc:02X}{instance.eojcc:02X}_{instance.eojci}"

            # Get device type name
            device_classes = {
                0x0279: "Solar Power Generation",
                0x027D: "Storage Battery",
                0x0287: "Power Distribution Board",
                0x0288: "Smart Electric Energy Meter",
                0x026B: "Electric Vehicle Charger",
            }

            device_type = device_classes.get(
                (instance.eojgc << 8) | instance.eojcc,
                f"Unknown (0x{instance.eojgc:02X}{instance.eojcc:02X})",
            )

            # Store device information
            self.devices[device_id] = {
                "ip": ip,
                "instance": instance,
                "type": device_type,
                "eojgc": instance.eojgc,
                "eojcc": instance.eojcc,
                "eojci": instance.eojci,
                "last_seen": asyncio.get_event_loop().time(),
                "properties": {},
            }

            # Try to get initial properties
            try:
                get_props = await instance.getPropertyMap()
                self.devices[device_id]["get_properties"] = get_props or []

                # Get operation status if available
                if 0x80 in get_props:
                    status = await instance.getMessage(0x80)
                    self.devices[device_id]["properties"]["status"] = (
                        "ON" if status == 0x30 else "OFF"
                    )

            except Exception as e:
                logger.debug(f"Could not get properties for {device_id}: {e}")

            logger.info(f"Discovered device: {device_type} at {ip} (ID: {device_id})")

        except Exception as e:
            logger.error(f"Error processing device: {e}")

    async def _poll_devices(self) -> None:
        """Poll all discovered devices for current data."""
        logger.debug("Starting device poll cycle")
        try:
            # Initialize grid power tracking
            grid_power_flow = None
            
            # Poll Solar device
            if self.solar_instance:
                logger.debug("Polling solar device...")
                try:
                    ip = self.solar_instance["ip"]
                    eojgc = self.solar_instance["eojgc"]
                    eojcc = self.solar_instance["eojcc"]
                    inst = self.solar_instance["instance"]
                    
                    # First, get the property maps to see what's available
                    try:
                        solar_device = HomeSolarPower(host=ip, api_connector=self.api_client, instance=inst)
                        
                        # Read property maps to populate available properties
                        await asyncio.wait_for(solar_device.getAllPropertyMaps(), timeout=5.0)
                        
                        # Check what properties are now available
                        if ip in self.api_client._state and "instances" in self.api_client._state[ip]:
                            inst_state = self.api_client._state[ip]["instances"][eojgc][eojcc][inst]
                            available_props = [p for p in inst_state.keys() if isinstance(p, int)]
                            logger.debug(f"☀️ Solar properties after map read: {[f'0x{p:02X}' for p in available_props]}")
                            
                            # Read the GET property map (0x9F) to see what properties are supported
                            if 0x9F in available_props:
                                try:
                                    get_map = await asyncio.wait_for(solar_device.update(0x9F), timeout=3.0)
                                    if get_map is not None:
                                        logger.debug(f"☀️ Solar GET property map (0x9F): {get_map}")
                                        # The property map should be a list of supported EPC codes
                                        if isinstance(get_map, (list, tuple)):
                                            logger.debug(f"☀️ Solar supported properties: {[f'0x{p:02X}' for p in get_map if isinstance(p, int)]}")
                                except Exception as e:
                                    logger.debug(f"Failed to read solar property map: {e}")
                            
                            # Now try to read the actual supported properties
                            power_str = "N/A"
                            
                            # Read instantaneous power (0xE0) - confirmed supported
                            if 0xE0 in [0xE0, 0x84]:  # Check if supported in property map
                                try:
                                    power_val = await asyncio.wait_for(solar_device.update(0xE0), timeout=3.0)
                                    if power_val is not None:
                                        power_str = f"{power_val}W"
                                        logger.debug(f"Solar power reading successful: {power_val}W")
                                except Exception as e:
                                    logger.debug(f"Failed to read solar power (0xE0): {e}")
                                    # Try alternative EPC
                                    try:
                                        power_val = await asyncio.wait_for(solar_device.update(0x84), timeout=3.0)
                                        if power_val is not None:
                                            power_str = f"{power_val}W"
                                    except:
                                        pass
                            
                        
                        logger.info(f"☀️ Solar: {power_str}")
                        
                        # CRITICAL: Check for real-time grid power flow (found through solar device!)
                        grid_cumulative_import = None
                        
                        try:
                            # 0xE5: Real-time grid power flow (+ import, - export)
                            grid_flow_val = await asyncio.wait_for(solar_device.update(0xE5), timeout=3.0)
                            if grid_flow_val is not None:
                                grid_power_flow = grid_flow_val
                                if grid_flow_val > 0:
                                    logger.info(f"🔌 GRID: Importing {grid_flow_val}W from grid")
                                elif grid_flow_val < 0:
                                    logger.info(f"🔌 GRID: Exporting {abs(grid_flow_val)}W to grid") 
                                else:
                                    logger.info(f"🔌 GRID: Balanced (0W grid flow)")
                        except:
                            pass
                            
                        try:
                            # 0xE1: Cumulative grid import (for reference)
                            import_total = await asyncio.wait_for(solar_device.update(0xE1), timeout=3.0)
                            if import_total is not None:
                                grid_cumulative_import = import_total
                                # Convert large values to kWh for readability
                                if import_total > 10000:
                                    logger.debug(f"📊 Total grid import: {import_total/1000:.1f}kWh")
                                else:
                                    logger.debug(f"📊 Total grid import: {import_total}Wh")
                        except:
                            pass
                        
                        
                    except Exception as wrapper_error:
                        logger.error(f"Wrapper class failed: {wrapper_error}, trying raw API...")
                        import traceback
                        logger.debug(f"Solar wrapper traceback: {traceback.format_exc()}")
                        
                        # Fallback to raw API
                        status_resp = await asyncio.wait_for(
                            self.api_client.echonetMessage(ip, eojgc, eojcc, inst, 0x62, [{"EPC": 0x80}]),
                            timeout=2.0
                        )
                        if status_resp and 0x80 in status_resp:
                            logger.info(f"☀️ Solar: Status property available")
                        else:
                            logger.info(f"☀️ Solar: No response to status query")
                except asyncio.TimeoutError:
                    logger.error(f"Timeout reading solar data")
                except Exception as e:
                    logger.error(f"Error reading solar data: {e}")
            
            # Poll Battery device
            if self.battery_instance:
                logger.debug("Polling battery device...")
                try:
                    ip = self.battery_instance["ip"]
                    eojgc = self.battery_instance["eojgc"]
                    eojcc = self.battery_instance["eojcc"]
                    inst = self.battery_instance["instance"]
                    
                    # First, get the property maps to see what's available
                    try:
                        battery_device = StorageBattery(host=ip, api_connector=self.api_client, instance=inst)
                        
                        # Read property maps to populate available properties
                        await asyncio.wait_for(battery_device.getAllPropertyMaps(), timeout=5.0)
                        
                        # Check what properties are now available
                        if ip in self.api_client._state and "instances" in self.api_client._state[ip]:
                            inst_state = self.api_client._state[ip]["instances"][eojgc][eojcc][inst]
                            available_props = [p for p in inst_state.keys() if isinstance(p, int)]
                            logger.debug(f"🔋 Battery properties after map read: {[f'0x{p:02X}' for p in available_props]}")
                            
                            # Read the GET property map (0x9F) to see what properties are supported
                            if 0x9F in available_props:
                                try:
                                    get_map = await asyncio.wait_for(battery_device.update(0x9F), timeout=3.0)
                                    if get_map is not None:
                                        logger.debug(f"🔋 Battery GET property map (0x9F): {get_map}")
                                        # The property map should be a list of supported EPC codes
                                        if isinstance(get_map, (list, tuple)):
                                            logger.debug(f"🔋 Battery supported properties: {[f'0x{p:02X}' for p in get_map if isinstance(p, int)]}")
                                except Exception as e:
                                    logger.debug(f"Failed to read battery property map: {e}")
                            
                            # Now try to read the actual supported properties for EV charging optimization
                            soc_str = "N/A"
                            mode_str = "N/A"
                            charge_power_str = "N/A"  # Battery charging power
                            discharge_power_str = "N/A"  # Battery discharging power
                            status_str = "N/A"
                            
                            # Investigation: Technical SOC vs User Display SOC
                            # Physical display shows 63% but technical reading is 70.7% - WHY?
                            technical_soc = None
                            display_soc = None
                            
                            soc_candidates = [
                                (0xE2, "Technical SOC"),  # What we found working
                                (0xBF, "Display SOC"),    # Potential user display value
                                (0xE1, "Alternative SOC 1"),
                                (0xE7, "Alternative SOC 2"), 
                                (0xE8, "Usable capacity"),
                                (0xC9, "User SOC"),
                            ]
                            
                            for epc, desc in soc_candidates:
                                try:
                                    soc_val = await asyncio.wait_for(battery_device.update(epc), timeout=2.0)
                                    if soc_val is not None:
                                        # Convert from technical units (usually 0.01% increments)
                                        if isinstance(soc_val, (int, float)) and soc_val > 100:
                                            soc_percentage = soc_val / 100
                                        else:
                                            soc_percentage = soc_val
                                        
                                        logger.info(f"🔋 {desc}: {soc_percentage:.1f}%")
                                        
                                        if epc == 0xE2:
                                            technical_soc = soc_percentage
                                        elif epc in [0xBF, 0xC9]:  # Potential display SOC
                                            display_soc = soc_percentage
                                            
                                except Exception as e:
                                    logger.debug(f"Failed to read {desc} (0x{epc:02X}): {e}")
                                    
                            # Determine which SOC to use
                            if display_soc is not None:
                                soc_str = f"{display_soc:.1f}% (display)"
                                logger.info(f"💡 SOC Analysis: Display={display_soc:.1f}% vs Technical={technical_soc:.1f}% (Δ={technical_soc-display_soc:.1f}%)")
                            elif technical_soc is not None:
                                soc_str = f"{technical_soc:.1f}% (technical)"
                                logger.warning(f"⚠️  Using technical SOC - display SOC unavailable via ECHONET")
                            else:
                                soc_str = "N/A"
                            
                            # Read operation mode (0xDA) - confirmed supported
                            try:
                                mode_val = await asyncio.wait_for(battery_device.update(0xDA), timeout=3.0)
                                if mode_val is not None:
                                    # Handle both numeric and string mode values
                                    if isinstance(mode_val, str):
                                        mode_str = mode_val
                                    else:
                                        mode_str = StorageBattery.DICT_OPERATION_MODE.get(mode_val, f"Code({mode_val})")
                                    logger.debug(f"Battery mode reading successful: {mode_val} -> {mode_str}")
                            except Exception as e:
                                logger.debug(f"Failed to read battery mode (0xDA): {e}")
                                
                            # Read critical battery power metrics for EV charging decisions
                            primary_power = None
                            charging_power = None
                            discharging_power = None
                            
                            # 0xD3: Charging/discharging amount (main power flow: + charging, - discharging)
                            try:
                                power_val = await asyncio.wait_for(battery_device.update(0xD3), timeout=3.0)
                                if power_val is not None:
                                    primary_power = power_val
                                    logger.debug(f"Battery primary power flow (0xD3): {power_val}W")
                            except:
                                pass
                                
                            # 0xE3: Instantaneous charging power (when battery is charging)
                            try:
                                power_val = await asyncio.wait_for(battery_device.update(0xE3), timeout=3.0)
                                if power_val is not None and power_val > 0:
                                    charging_power = power_val
                                    logger.debug(f"Battery charging power (0xE3): {power_val}W")
                            except:
                                pass
                                
                            # 0xE4: Instantaneous discharging power (when battery is discharging)
                            try:
                                power_val = await asyncio.wait_for(battery_device.update(0xE4), timeout=3.0)
                                if power_val is not None and power_val > 0:
                                    discharging_power = power_val
                                    logger.debug(f"Battery discharging power (0xE4): {power_val}W")
                            except:
                                pass
                                
                            # Determine the main power value and direction for display
                            if primary_power is not None:
                                if primary_power > 0:
                                    power_str = f"+{primary_power}W (charging)"
                                elif primary_power < 0:
                                    power_str = f"{primary_power}W (discharging)"
                                else:
                                    power_str = "0W (idle)"
                            elif discharging_power and discharging_power > 0:
                                power_str = f"-{discharging_power}W (discharging)"
                            elif charging_power and charging_power > 0:
                                power_str = f"+{charging_power}W (charging)"
                            else:
                                power_str = "0W"
                                    
                            # Try to read operational status (0x80) - basic on/off
                            try:
                                status_val = await asyncio.wait_for(battery_device.update(0x80), timeout=3.0)
                                if status_val is not None:
                                    if isinstance(status_val, bytes):
                                        status_str = "ON" if status_val == b'\x30' else "OFF"
                                    elif status_val == 0x30:
                                        status_str = "ON"
                                    else:
                                        status_str = f"Status({status_val})"
                            except:
                                pass
                        
                        # Show comprehensive battery status
                        battery_status = f"🔋 Battery: SOC={soc_str} | Mode={mode_str}"
                        if power_str != "N/A":
                            battery_status += f" | Power={power_str}"
                        if status_str != "N/A":
                            battery_status += f" | Status={status_str}"
                        logger.info(battery_status)
                        
                    except Exception as wrapper_error:
                        logger.debug(f"Battery wrapper failed: {wrapper_error}, trying raw API...")
                        
                        # Fallback to raw API
                        status_resp = await asyncio.wait_for(
                            self.api_client.echonetMessage(ip, eojgc, eojcc, inst, 0x62, [{"EPC": 0x80}]),
                            timeout=2.0
                        )
                        if status_resp and 0x80 in status_resp:
                            logger.info(f"🔋 Battery: Status property available")
                        else:
                            logger.info(f"🔋 Battery: No response to status query")
                            
                            # Summary of battery status for EV charging decisions
                            logger.info(f"🔋 Battery: SOC={soc_str} | Mode={mode_str} | {power_str} | Status={status_str}")
                            
                            # Check if battery device has grid/consumption data (some HEMS integrate this)
                            battery_grid_epcs = [
                                (0xE1, "Grid import power"),
                                (0xE5, "Grid power flow"),
                                (0xC1, "Consumption power"),
                                (0xC2, "Grid tie power"),
                                (0xC4, "House load power"),
                                (0xC6, "Grid import/export"),
                            ]
                            
                            for epc, desc in battery_grid_epcs:
                                try:
                                    grid_val = await asyncio.wait_for(battery_device.update(epc), timeout=2.0)
                                    if grid_val is not None:
                                        logger.info(f"🌐 Battery {desc} (0x{epc:02X}): {grid_val}W")
                                except:
                                    continue
                except asyncio.TimeoutError:
                    logger.error(f"Timeout reading battery data")
                except Exception as e:
                    logger.error(f"Error reading battery data: {e}")
                    
            # Look for Smart Electric Energy Meter (0x0288) for grid import/export data
            # This is CRITICAL for Tesla charging optimization decisions!
            try:
                logger.info("🔌 Searching for smart meter data (grid import/export)...")
                
                # Debug: Show all available devices in API state
                logger.debug(f"API state hosts: {list(self.api_client._state.keys())}")
                
                # Check if we have any smart meter devices in our API state
                smart_meter_found = False
                for host_ip in self.api_client._state:
                    if "instances" not in self.api_client._state[host_ip]:
                        logger.debug(f"No instances at {host_ip}")
                        continue
                        
                    instances = self.api_client._state[host_ip]["instances"]
                    logger.debug(f"Available device groups at {host_ip}: {list(instances.keys())}")
                    
                    # Check all device groups for any meter-like devices
                    for group_id, group_devices in instances.items():
                        logger.debug(f"Group 0x{group_id:02X} has classes: {list(group_devices.keys())}")
                        # Look for any meter devices (0x88 = smart meter, but check others too)
                        for class_id in group_devices:
                            if class_id in [0x88, 0x80]:  # 0x88 = smart meter, 0x80 = general meter
                                for instance_id in group_devices[class_id]:
                                    device_name = self.DEVICE_CLASSES.get((group_id << 8) | class_id, f"Unknown(0x{group_id:02X}{class_id:02X})")
                                    logger.info(f"🔌 Found meter device: {device_name} at {host_ip}:{instance_id}")
                                    smart_meter_found = True
                        
                    # Look specifically for Smart Electric Energy Meter (Group=0x02, Class=0x88)
                    if 0x02 in instances and 0x88 in instances[0x02]:
                        for instance_id in instances[0x02][0x88]:
                            logger.info(f"🔌 Found Smart Meter at {host_ip}, instance {instance_id}")
                            smart_meter_found = True
                            
                            # Key EPCs for grid import/export:
                            # 0xE0: Measured instantaneous power (+ = import, - = export)
                            # 0xE1: Cumulative power consumption (import from grid)
                            # 0xE3: Cumulative power generation (export to grid)
                            meter_epcs = [
                                (0xE0, "Grid power flow (W)"),  # + import, - export
                                (0xE1, "Grid import total (kWh)"), 
                                (0xE3, "Grid export total (kWh)"),
                            ]
                            
                            for epc, desc in meter_epcs:
                                try:
                                    meter_val = await asyncio.wait_for(
                                        self.api_client.echonetMessage(host_ip, 0x02, 0x88, instance_id, 0x62, [{"EPC": epc}]),
                                        timeout=3.0
                                    )
                                    if meter_val and epc in meter_val:
                                        val = meter_val[epc]
                                        if isinstance(val, bytes):
                                            val = int.from_bytes(val, 'big', signed=True)  # Allow negative for export
                                        logger.info(f"🔌 {desc}: {val}")
                                except Exception as e:
                                    logger.debug(f"Failed to read smart meter {desc} (0x{epc:02X}): {e}")
                                    
                if not smart_meter_found:
                    if grid_power_flow is not None:
                        logger.info("✅ Grid power flow found via solar device (EPC 0xE5)")
                    else:
                        logger.warning("⚠️  No grid power flow data found")
                        logger.warning("💡 For Tesla charging optimization, we need real-time grid data")
                    
            except Exception as e:
                logger.debug(f"Error reading smart meter data: {e}")
                
        except Exception as e:
            logger.error(f"Error in polling loop: {e}")

    def get_device_data(self, device_id: str) -> dict[str, Any] | None:
        """Get current data for a specific device."""
        return self.devices.get(device_id)

    def get_all_devices(self) -> dict[str, Any]:
        """Get all discovered devices."""
        return self.devices
