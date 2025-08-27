"""Core functionality for ECHONET Lite communication and management."""

import asyncio
import logging
from typing import Any

from pychonet import ECHONETAPIClient as api
from pychonet import EchonetInstance
from pychonet.lib.udpserver import UDPServer

from .charging import EnergyMetrics, EVChargingController
from .constants import EPC_NAMES, CommonEPC
from .device_state_manager import DeviceStateManager
from .devices import BatteryDevicePoller, SolarDevicePoller
from .metrics_logger import MetricsLogger

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

        # Device pollers - initialized after device discovery
        self.solar_poller = None
        self.battery_poller = None

        # Initialize EV charging controller
        self.ev_controller = EVChargingController(config)

        # Initialize metrics logger
        self.metrics_logger = MetricsLogger(config)

        # Initialize device state manager (after api_client is available)
        self.device_state_manager = None

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

        # Clean up metrics logger
        if hasattr(self, "metrics_logger"):
            self.metrics_logger.close()

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
                    discovery_state = self.device_state_manager.get_discovery_state(ip)
                    if discovery_state and "discovered" in discovery_state:
                        break

                # Check if the specific device exists using DeviceStateManager
                if self.device_state_manager.device_exists(ip, eojgc, eojcc, instance):
                    logger.info(f"✅ Required device validated: {device_name}")

                    # Log available properties for debugging with meaningful names
                    available_props = self.device_state_manager.get_available_properties(
                        ip, eojgc, eojcc, instance
                    )
                    prop_names = []
                    for p in available_props:
                        name = EPC_NAMES.get(p, f"Unknown(0x{p:02X})")
                        prop_names.append(name)

                    logger.debug(f"Available properties for {device_name}: {prop_names}")

                    # Store the raw ECHONET instance for direct access
                    if device_config.get("type") == "solar" and eojcc == 0x79:
                        self.solar_instance = {
                            "ip": ip,
                            "eojgc": eojgc,
                            "eojcc": eojcc,
                            "instance": instance,
                        }
                        logger.info(f"Stored solar device info for {device_name}")
                    elif device_config.get("type") == "battery" and eojcc == 0x7D:
                        self.battery_instance = {
                            "ip": ip,
                            "eojgc": eojgc,
                            "eojcc": eojcc,
                            "instance": instance,
                        }
                        logger.info(f"Stored battery device info for {device_name}")
                elif self.device_state_manager.is_device_discovered(ip):
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

        # Initialize device pollers once after validation
        if self.solar_instance:
            self.solar_poller = SolarDevicePoller(self.solar_instance, self.api_client)
        if self.battery_instance:
            self.battery_poller = BatteryDevicePoller(self.battery_instance, self.api_client)

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

            # Initialize device state manager now that api_client is available
            self.device_state_manager = DeviceStateManager(self.api_client)

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

            # Poll Solar device using reusable poller
            if self.solar_poller:
                solar_data = await self.solar_poller.poll_solar_data()
                solar_power = solar_data.get("solar_power")
                grid_power_flow = solar_data.get("grid_power_flow")

            # Poll Battery device using reusable poller
            if self.battery_poller:
                battery_data = await self.battery_poller.poll_battery_data()
                battery_soc = battery_data.get("battery_soc")
                battery_power = battery_data.get("battery_power")

            # EV Charging Control - Calculate optimal charging amps based on policy
            ev_amps = 0
            if self.ev_controller.is_enabled():
                # Create energy metrics for EV controller
                metrics = EnergyMetrics(
                    battery_soc=battery_soc,
                    battery_power=battery_power,
                    grid_power_flow=grid_power_flow,
                    solar_power=solar_power,
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
                        essential_stats.append("Battery: 0W (idle)")

                # Grid power flow (+ import, - export)
                if grid_power_flow is not None:
                    if grid_power_flow > 0:
                        essential_stats.append(f"Grid: +{grid_power_flow}W (importing)")
                    elif grid_power_flow < 0:
                        essential_stats.append(f"Grid: {grid_power_flow}W (exporting)")
                    else:
                        essential_stats.append("Grid: 0W (balanced)")

                # Solar production
                if solar_power is not None:
                    essential_stats.append(f"Solar: {solar_power}W")

                # EV charging status
                if self.ev_controller.is_enabled():
                    policy_name = self.ev_controller.get_current_policy()
                    essential_stats.append(f"EV: {ev_amps}A ({policy_name})")

                # Log the consolidated essential stats
                logger.info("⚡ EV CHARGE METRICS: " + " | ".join(essential_stats))

                # Log metrics to CSV file
                if self.ev_controller.is_enabled():
                    self.metrics_logger.log_metrics(
                        battery_soc=battery_soc,
                        battery_power=battery_power,
                        grid_power_flow=grid_power_flow,
                        solar_power=solar_power,
                        ev_charging_amps=ev_amps,
                        ev_policy=policy_name,
                    )
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
