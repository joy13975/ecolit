"""Core functionality for ECHONET Lite communication and management."""

import asyncio
import logging
from typing import Any

from pychonet import ECHONETAPIClient as api
from pychonet import EchonetInstance, HomeSolarPower, StorageBattery
from pychonet.lib.udpserver import UDPServer
from epc_constants import CommonEPC, SolarEPC, BatteryEPC, EPC_NAMES

from .charging import EVChargingController, EnergyMetrics

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
        self._technical_soc_warning_shown = False
        
        # Initialize EV charging controller
        self.ev_controller = EVChargingController(config)

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

                                # Use centralized EPC name mapping

                                prop_names = []
                                for p in available_props:
                                    if isinstance(p, int):
                                        name = EPC_NAMES.get(p, f"Unknown(0x{p:02X})")
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
                            discovery_config = self.config.get("network", {}).get("discovery", {})
                            device_timeout = discovery_config.get("device_timeout", 0.4)
                            success = await asyncio.wait_for(
                                self.api_client.discover(ip), timeout=device_timeout
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
                if CommonEPC.OPERATION_STATUS in get_props:
                    status = await instance.getMessage(CommonEPC.OPERATION_STATUS)
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
            # Initialize essential metrics for EV charging optimization
            solar_power = None
            grid_power_flow = None
            battery_soc = None
            battery_power = None
            battery_mode = None

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

                            # Read the GET property map to see what properties are supported
                            if CommonEPC.GET_PROPERTY_MAP in available_props:
                                try:
                                    get_map = await asyncio.wait_for(solar_device.update(CommonEPC.GET_PROPERTY_MAP), timeout=3.0)
                                    if get_map is not None:
                                        logger.debug(f"☀️ Solar GET property map (0x9F): {get_map}")
                                        # The property map should be a list of supported EPC codes
                                        if isinstance(get_map, (list, tuple)):
                                            logger.debug(f"☀️ Solar supported properties: {[f'0x{p:02X}' for p in get_map if isinstance(p, int)]}")
                                except Exception as e:
                                    logger.debug(f"Failed to read solar property map: {e}")

                            # Read instantaneous power - confirmed supported
                            if SolarEPC.INSTANTANEOUS_POWER_GENERATION in [SolarEPC.INSTANTANEOUS_POWER_GENERATION, CommonEPC.INSTANTANEOUS_POWER]:  # Check if supported in property map
                                try:
                                    power_val = await asyncio.wait_for(solar_device.update(SolarEPC.INSTANTANEOUS_POWER_GENERATION), timeout=3.0)
                                    if power_val is not None:
                                        solar_power = power_val
                                        logger.debug(f"Solar power reading successful: {power_val}W")
                                except Exception as e:
                                    logger.debug(f"Failed to read solar power ({hex(SolarEPC.INSTANTANEOUS_POWER_GENERATION)}): {e}")
                                    # Try alternative EPC
                                    try:
                                        power_val = await asyncio.wait_for(solar_device.update(CommonEPC.INSTANTANEOUS_POWER), timeout=3.0)
                                        if power_val is not None:
                                            solar_power = power_val
                                    except:
                                        pass

                        # Check for real-time grid power flow (found through solar device!)
                        try:
                            # Real-time grid power flow (+ import, - export)
                            grid_flow_val = await asyncio.wait_for(solar_device.update(SolarEPC.GRID_POWER_FLOW), timeout=3.0)
                            if grid_flow_val is not None:
                                grid_power_flow = grid_flow_val
                                logger.debug(f"Grid power flow reading successful: {grid_flow_val}W")
                        except:
                            pass

                        try:
                            # Cumulative grid import (for reference)
                            import_total = await asyncio.wait_for(solar_device.update(SolarEPC.CUMULATIVE_POWER_GENERATION), timeout=3.0)
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
                            self.api_client.echonetMessage(ip, eojgc, eojcc, inst, 0x62, [{"EPC": CommonEPC.OPERATION_STATUS}]),
                            timeout=2.0
                        )
                        if status_resp and CommonEPC.OPERATION_STATUS in status_resp:
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

                            # Read the GET property map to see what properties are supported
                            if CommonEPC.GET_PROPERTY_MAP in available_props:
                                try:
                                    get_map = await asyncio.wait_for(battery_device.update(CommonEPC.GET_PROPERTY_MAP), timeout=3.0)
                                    if get_map is not None:
                                        logger.debug(f"🔋 Battery GET property map (0x9F): {get_map}")
                                        # The property map should be a list of supported EPC codes
                                        if isinstance(get_map, (list, tuple)):
                                            logger.debug(f"🔋 Battery supported properties: {[f'0x{p:02X}' for p in get_map if isinstance(p, int)]}")
                                except Exception as e:
                                    logger.debug(f"Failed to read battery property map: {e}")

                            # Read the essential battery metrics for EV charging optimization
                            technical_soc = None
                            display_soc = None

                            # Read SOC - prioritize display SOC, fall back to technical
                            soc_candidates = [
                                (BatteryEPC.USER_DISPLAY_SOC, "display"),    # User display SOC (preferred)
                                (BatteryEPC.DISPLAY_SOC_ALT, "display"),     # Alternative user display SOC
                                (BatteryEPC.REMAINING_STORED_ELECTRICITY, "technical"),  # Technical SOC (fallback)
                            ]

                            for epc, soc_type in soc_candidates:
                                try:
                                    soc_val = await asyncio.wait_for(battery_device.update(epc), timeout=2.0)
                                    if soc_val is not None:
                                        # Convert from technical units (usually 0.01% increments)
                                        if isinstance(soc_val, (int, float)) and soc_val > 100:
                                            soc_percentage = soc_val / 100
                                        else:
                                            soc_percentage = soc_val

                                        logger.debug(f"Battery {soc_type} SOC (0x{epc:02X}): {soc_percentage:.1f}%")

                                        if soc_type == "display":
                                            display_soc = soc_percentage
                                            break  # Prefer display SOC, stop searching
                                        elif soc_type == "technical" and display_soc is None:
                                            technical_soc = soc_percentage

                                except Exception as e:
                                    logger.debug(f"Failed to read {soc_type} SOC (0x{epc:02X}): {e}")

                            # Set the SOC value to use
                            if display_soc is not None:
                                battery_soc = display_soc
                            elif technical_soc is not None:
                                battery_soc = technical_soc
                                # Only show this warning once
                                if not self._technical_soc_warning_shown:
                                    logger.warning(f"⚠️  Using technical SOC - display SOC unavailable via ECHONET")
                                    self._technical_soc_warning_shown = True

                            # Read operation mode - confirmed supported
                            try:
                                mode_val = await asyncio.wait_for(battery_device.update(BatteryEPC.OPERATION_MODE), timeout=3.0)
                                if mode_val is not None:
                                    # Handle both numeric and string mode values
                                    if isinstance(mode_val, str):
                                        battery_mode = mode_val
                                    else:
                                        battery_mode = StorageBattery.DICT_OPERATION_MODE.get(mode_val, f"Code({mode_val})")
                                    logger.debug(f"Battery mode reading successful: {mode_val} -> {battery_mode}")
                            except Exception as e:
                                logger.debug(f"Failed to read battery mode (0xDA): {e}")

                            # Read critical battery power metrics for EV charging decisions
                            primary_power = None

                            # Charging/discharging amount (main power flow: + charging, - discharging)
                            try:
                                power_val = await asyncio.wait_for(battery_device.update(BatteryEPC.CHARGING_DISCHARGING_AMOUNT), timeout=3.0)
                                if power_val is not None:
                                    primary_power = power_val
                                    battery_power = power_val  # Store for essential metrics
                                    logger.debug(f"Battery primary power flow (0xD3): {power_val}W")
                            except:
                                # Try alternative power readings
                                try:
                                    # Instantaneous charging power
                                    charge_val = await asyncio.wait_for(battery_device.update(BatteryEPC.CHARGING_POWER), timeout=3.0)
                                    discharge_val = await asyncio.wait_for(battery_device.update(BatteryEPC.DISCHARGING_POWER), timeout=3.0)

                                    if charge_val and charge_val > 0:
                                        battery_power = charge_val
                                    elif discharge_val and discharge_val > 0:
                                        battery_power = -discharge_val  # Make discharge negative
                                    else:
                                        battery_power = 0
                                except:
                                    battery_power = 0

                            logger.debug(f"Battery data collected: SOC={battery_soc}, Mode={battery_mode}, Power={battery_power}W")

                    except Exception as wrapper_error:
                        logger.debug(f"Battery wrapper failed: {wrapper_error}")
                except asyncio.TimeoutError:
                    logger.error(f"Timeout reading battery data")
                except Exception as e:
                    logger.error(f"Error reading battery data: {e}")

            # EV Charging Control - Calculate optimal charging amps based on policy
            ev_amps = 0
            if self.ev_controller.is_enabled():
                # Create energy metrics for EV controller
                metrics = EnergyMetrics(
                    battery_soc=battery_soc,
                    battery_power=battery_power,
                    grid_power_flow=grid_power_flow,
                    solar_power=solar_power
                )
                
                # Calculate target amps based on current policy
                ev_amps = self.ev_controller.calculate_charging_amps(metrics)

            # Log essential stats for EV charging optimization in one consolidated line
            if battery_soc is not None or solar_power is not None or grid_power_flow is not None:
                essential_stats = []

                # Battery SOC - most critical metric
                if battery_soc is not None:
                    essential_stats.append(f"Battery SOC: {battery_soc:.1f}%")

                # Battery power flow (+ charging, - discharging)
                if battery_power is not None:
                    if battery_power > 0:
                        essential_stats.append(f"Battery: +{battery_power}W (charging)")
                    elif battery_power < 0:
                        essential_stats.append(f"Battery: {battery_power}W (discharging)")
                    else:
                        essential_stats.append(f"Battery: 0W (idle)")

                # Grid power flow (+ import, - export)
                if grid_power_flow is not None:
                    if grid_power_flow > 0:
                        essential_stats.append(f"Grid: +{grid_power_flow}W (importing)")
                    elif grid_power_flow < 0:
                        essential_stats.append(f"Grid: {grid_power_flow}W (exporting)")
                    else:
                        essential_stats.append(f"Grid: 0W (balanced)")

                # Solar production
                if solar_power is not None:
                    essential_stats.append(f"Solar: {solar_power}W")

                # EV charging status
                if self.ev_controller.is_enabled():
                    policy_name = self.ev_controller.get_current_policy()
                    essential_stats.append(f"EV: {ev_amps}A ({policy_name})")

                # Log the consolidated essential stats
                logger.info("⚡ EV CHARGE METRICS: " + " | ".join(essential_stats))
            else:
                logger.warning("⚠️  No essential metrics available for EV charging optimization")

        except Exception as e:
            logger.error(f"Error in polling loop: {e}")

    def get_device_data(self, device_id: str) -> dict[str, Any] | None:
        """Get current data for a specific device."""
        return self.devices.get(device_id)

    def get_all_devices(self) -> dict[str, Any]:
        """Get all discovered devices."""
        return self.devices
