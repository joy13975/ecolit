"""Core functionality for ECHONET Lite communication and management."""

import asyncio
import logging
from typing import Any

from pychonet import ECHONETAPIClient as api
from pychonet import EchonetInstance
from pychonet.lib.udpserver import UDPServer

from .charging import EnergyMetrics, EVChargingController
from .charging.tesla_api import TeslaAPIClient
from .charging.tesla_controller import TeslaChargingController
from .constants import EPC_NAMES, CommonEPC
from .device_state_manager import DeviceStateManager
from .devices import BatteryDevicePoller, SolarDevicePoller
from .metrics_logger import MetricsLogger
from .tesla.wall_connector import WallConnectorClient

logger = logging.getLogger(__name__)


class EcoliteManager:
    """Manager for ECHONET Lite device communication and monitoring."""

    def __init__(self, config: dict[str, Any], dry_run: bool = False):
        """Initialize the manager with configuration."""
        self.config = config
        self.dry_run = dry_run
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

        # Initialize Tesla clients
        tesla_config = config.get("tesla", {})
        self.tesla_client = None
        self.tesla_controller = None
        self.wall_connector_client = None
        if tesla_config.get("enabled", False):
            self.tesla_client = TeslaAPIClient(tesla_config)
            self.tesla_controller = TeslaChargingController(self.tesla_client, config)
            wall_connector_ip = tesla_config.get("wall_connector_ip")
            if wall_connector_ip:
                self.wall_connector_client = WallConnectorClient(wall_connector_ip)

        # Tesla state cache for display (synced every 10 minutes to reduce API calls)
        self._cached_tesla_state = {
            "soc": None,
            "charging_state": None,
            "range": None,
            "est_range": None,
            "last_update": 0,
        }
        self._tesla_display_sync_interval = 600  # 10 minutes
        self._last_charging_window_state = False  # Track window state changes

        # Separate polling intervals and tracking
        self._home_polling_interval = config.get("polling", {}).get("home_interval", 10)  # Default 10s for HEMS
        self._last_tesla_poll = 0
        self._last_home_metrics_log = 0
        self._latest_home_data = {}  # Shared data between home and Tesla loops
        self._wall_connector_data = {}  # Wall Connector real-time data

    async def start(self) -> None:
        """Start the ECHONET Lite manager."""
        logger.info("Starting ECHONET Lite manager")

        # Log mode information
        if self.dry_run:
            logger.info("🚀 Running in DRY-RUN mode - monitoring only, no charging control")
        else:
            logger.info("🚀 Running in CONTROL mode - will actively control Tesla charging")

        self._running = True

        # Initialize ECHONET Lite API
        await self._initialize_api()

        # Initialize Tesla API client if enabled
        if self.tesla_client:
            try:
                await self.tesla_client.start()
                logger.info("Tesla API client initialized")

                # Initialize EV controller to safe state - no startup Tesla wake-up
                if self.ev_controller.is_enabled() and self.tesla_controller:
                    try:
                        # Initialize EV controller to safe 0A state (no charging)
                        self.ev_controller.sync_with_actual_state(None, False)

                        # Try to get cached Tesla data without waking the vehicle
                        vehicle_data = await self.tesla_client.get_vehicle_data()
                        if vehicle_data and vehicle_data.battery_level is not None:
                            # Vehicle was already awake - use the data
                            import time
                            self._cached_tesla_state.update({
                                "soc": vehicle_data.battery_level,
                                "charging_state": vehicle_data.charging_state,
                                "range": vehicle_data.battery_range,
                                "est_range": vehicle_data.est_battery_range,
                                "last_update": time.time(),
                            })
                            logger.info(f"Tesla data available on startup: SOC={vehicle_data.battery_level}%, State={vehicle_data.charging_state}")
                        else:
                            logger.info("Tesla vehicle sleeping on startup - will check only when solar surplus appears")
                    except Exception as e:
                        logger.info(f"Tesla not accessible on startup: {e} - will check only when solar surplus appears")
            except Exception as e:
                logger.error(f"Failed to initialize Tesla API client: {e}")
                logger.warning("Tesla data will not be available")
                self.tesla_client = None

        # Validate required devices if specified
        await self._validate_required_devices()

        self._tasks.append(asyncio.create_task(self._discover_devices()))
        self._tasks.append(asyncio.create_task(self._home_monitor_loop()))
        self._tasks.append(asyncio.create_task(self._tesla_monitor_loop()))

    async def stop(self) -> None:
        """Stop the ECHONET Lite manager."""
        logger.info("Stopping ECHONET Lite manager")
        self._running = False

        for task in self._tasks:
            task.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Clean up Tesla client
        if self.tesla_client:
            try:
                await self.tesla_client.close()
            except Exception as e:
                logger.error(f"Error closing Tesla client: {e}")

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
                            "name": device_name,
                            "capacity_kwh": device_config.get("capacity_kwh"),
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

    async def _home_monitor_loop(self) -> None:
        """Home energy monitoring loop for device polling (fast, free HEMS data)."""
        logger.info(f"Starting home monitoring loop (polling every {self._home_polling_interval}s)")
        while self._running:
            try:
                await self._poll_home_devices()
                await asyncio.sleep(self._home_polling_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in home monitoring loop: {e}")
                await asyncio.sleep(5)

    async def _tesla_monitor_loop(self) -> None:
        """Tesla monitoring loop (event-driven, configurable retry intervals)."""
        tesla_retry_interval = self.config.get("polling", {}).get("tesla_retry_interval", 10) * 60  # Convert minutes to seconds
        logger.info(f"Starting Tesla monitoring loop (retry every {tesla_retry_interval//60} minutes during surplus)")
        while self._running:
            try:
                await self._poll_tesla_data()
                # Sleep based on configurable retry interval
                await asyncio.sleep(tesla_retry_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Tesla monitoring loop: {e}")
                await asyncio.sleep(10)

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

    async def _poll_home_devices(self) -> None:
        """Poll home energy devices (solar, battery) for current data."""
        logger.debug("Starting home device poll cycle")
        import time
        current_time = time.time()

        try:
            # Initialize home energy metrics
            solar_power = None
            grid_power_flow = None
            battery_soc = None
            battery_power = None
            official_soc = None
            battery_data = None

            # Poll Solar device using reusable poller
            if self.solar_poller:
                solar_data = await self.solar_poller.poll_solar_data()
                solar_power = solar_data.get("solar_power")
                grid_power_flow = solar_data.get("grid_power_flow")
                grid_device_faulty = solar_data.get("grid_power_flow_device_faulty", False)

            # Poll Battery device using reusable poller
            if self.battery_poller:
                battery_data = await self.battery_poller.poll_battery_data()
                official_soc = battery_data.get("battery_soc")  # Official SoC reading
                battery_power = battery_data.get("battery_power")

                # Use real-time SoC estimate if available and confident
                realtime_soc = battery_data.get("realtime_soc")
                soc_confidence = battery_data.get("soc_confidence", 0.0)

                # Use real-time estimate if confidence > 0.6, otherwise use official
                if realtime_soc is not None and soc_confidence > 0.6:
                    battery_soc = realtime_soc
                else:
                    battery_soc = official_soc

            # Skip faulty grid device reading entirely - don't try to calculate it
            if grid_device_faulty:
                # Just use None - policies will handle missing data appropriately
                grid_power_flow = None

            # Store latest home data for Tesla polling and EV control
            self._latest_home_data = {
                "battery_soc": battery_soc,
                "battery_power": battery_power,
                "solar_power": solar_power,
                "grid_power_flow": grid_power_flow,
                "official_soc": official_soc,
                "battery_data": battery_data,
                "timestamp": current_time,
            }

            # Log home metrics every 30 seconds (3x less frequent than polling)
            if current_time - self._last_home_metrics_log >= 30:
                self._log_home_metrics(battery_soc, battery_power, solar_power, official_soc, battery_data)
                self._last_home_metrics_log = current_time

        except Exception as e:
            logger.error(f"Error in home polling loop: {e}")

    def _log_home_metrics(self, battery_soc, battery_power, solar_power, official_soc, battery_data):
        """Log home energy metrics separately."""
        home_stats = []

        # Battery SOC - official reading
        if official_soc is not None:
            home_stats.append(f"SOC:{official_soc:.1f}%")
        else:
            home_stats.append("SOC:N/A")

        # Battery power flow (+ charging, - discharging)
        if battery_power is not None:
            if battery_power > 0:
                home_stats.append(f"Charging:+{battery_power}W")
            elif battery_power < 0:
                home_stats.append(f"Charging:{battery_power}W")
            else:
                home_stats.append("Charging:0W")
        else:
            home_stats.append("Charging:N/A")

        # Solar production
        if solar_power is not None:
            home_stats.append(f"Solar:{solar_power}W")
        else:
            home_stats.append("Solar:N/A")

        home_section = "Home [" + " ".join(home_stats) + "]"
        logger.info(f"📊 {home_section}")

        # Log estimates if available
        estimates = []
        if battery_data:
            realtime_soc = battery_data.get("realtime_soc")
            if realtime_soc is not None:
                estimates.append(f"RTSOC:{realtime_soc:.2f}%")

                # Add charging rate if available
                charging_info = battery_data.get("charging_rate_pct_per_hour", 0)
                if abs(charging_info) > 0.1:
                    estimates.append(f"ChargeRate:{charging_info:+.1f}%/h")

                # Add time to target SOC
                if self.battery_poller and self.battery_instance and battery_power is not None:
                    if battery_power > 10:  # Charging
                        time_to_full = self.battery_poller.realtime_soc_estimator.get_time_to_target_soc(100)
                        if time_to_full is not None and time_to_full > 0:
                            if time_to_full < 1:
                                minutes = int(time_to_full * 60)
                                estimates.append(f"To100%:{minutes}min")
                            else:
                                estimates.append(f"To100%:{time_to_full:.1f}h")
                    elif battery_power < -10:  # Discharging
                        reserve_soc = self.battery_instance.get("target_soc_percent", 20)
                        time_to_reserve = self.battery_poller.realtime_soc_estimator.get_time_to_target_soc(reserve_soc)
                        if time_to_reserve is not None and time_to_reserve > 0:
                            if time_to_reserve < 1:
                                minutes = int(time_to_reserve * 60)
                                estimates.append(f"To{reserve_soc}%:{minutes}min")
                            else:
                                estimates.append(f"To{reserve_soc}%:{time_to_reserve:.1f}h")

        if estimates:
            estimates_section = "Home Estimates [" + " ".join(estimates) + "]"
            logger.info(f"📈 {estimates_section}")

    async def _poll_tesla_data(self) -> None:
        """Event-driven Tesla polling - only check when solar surplus exists and we haven't started charging."""
        import time
        current_time = time.time()

        if not hasattr(self, '_latest_home_data'):
            logger.debug("Tesla polling skipped - no home data available yet")
            return

        if not self._latest_home_data:
            logger.debug("Tesla polling skipped - home data empty")
            return

        try:
            home_data = self._latest_home_data
            battery_soc = home_data.get("battery_soc")
            solar_power = home_data.get("solar_power")
            grid_power_flow = home_data.get("grid_power_flow")
            battery_power = home_data.get("battery_power")

            # Get surplus threshold from config
            surplus_threshold = self.config.get("polling", {}).get("surplus_threshold", 1000)

            # Check if solar surplus exists
            has_solar_surplus = solar_power is not None and solar_power > surplus_threshold

            logger.debug(f"Solar surplus check: {solar_power}W vs {surplus_threshold}W threshold = {has_solar_surplus}")

            if not has_solar_surplus:
                # No surplus - reset flag for next surplus event and stop charging if needed
                if self.tesla_controller:
                    if not self.tesla_controller.has_started_charging_this_surplus():
                        # Only log if flag was previously set
                        pass
                    self.tesla_controller.reset_surplus_event()

                    # Stop charging if currently charging
                    if self.ev_controller.is_enabled() and not self.dry_run:
                        try:
                            policy_name = self.ev_controller.get_current_policy()
                            # Use no-wake version for stopping charging
                            control_result = await self.tesla_controller.execute_charging_control(
                                0, battery_soc, solar_power, policy_name
                            )
                            if control_result["actions_taken"]:
                                for action in control_result["actions_taken"]:
                                    logger.info(f"🔋 TESLA CONTROL: {action}")
                        except Exception as e:
                            logger.error(f"Error stopping Tesla charging: {e}")
                return

            # We have solar surplus - check if we should try to start charging
            if self.tesla_controller and self.tesla_controller.has_started_charging_this_surplus():
                # Already started charging during this surplus - just monitor with wall connector
                await self._update_wall_connector_data()
                return

            # Solar surplus exists and we haven't started charging - check car availability
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

                if ev_amps > 0:
                    # We want to charge - check car availability and try to start
                    if not self.dry_run and self.tesla_controller:
                        try:
                            policy_name = self.ev_controller.get_current_policy()
                            # Use wake version for starting charging
                            control_result = await self.tesla_controller.execute_charging_control_with_wake(
                                ev_amps, battery_soc, solar_power, policy_name
                            )

                            # Log control actions taken
                            if control_result["actions_taken"]:
                                for action in control_result["actions_taken"]:
                                    logger.info(f"🔋 TESLA CONTROL: {action}")

                            # Log warnings
                            if control_result["warnings"]:
                                for warning in control_result["warnings"]:
                                    logger.warning(f"⚠️  TESLA CONTROL: {warning}")

                            # Log errors
                            if control_result["errors"]:
                                for error in control_result["errors"]:
                                    logger.error(f"❌ TESLA CONTROL: {error}")

                        except Exception as e:
                            logger.error(f"Error in Tesla charging control: {e}")
                    elif self.dry_run:
                        logger.debug(f"DRY-RUN: Would try to start Tesla charging at {ev_amps}A")

            # Update wall connector data for real-time monitoring
            await self._update_wall_connector_data()

            # Log Tesla metrics only if we have fresh data or wall connector data
            await self._maybe_log_tesla_metrics(current_time, ev_amps)

        except Exception as e:
            logger.error(f"Error in Tesla polling loop: {e}")

    async def _update_wall_connector_data(self):
        """Update wall connector data for real-time charging monitoring."""
        import time

        if not self.wall_connector_client:
            return

        try:
            vitals = await self.wall_connector_client.get_vitals()
            if vitals:
                # Get actual current and voltage
                vehicle_current = vitals.get("vehicle_current_a", 0)
                grid_voltage = vitals.get("grid_v", 0)
                if vehicle_current and grid_voltage:
                    wall_connector_power = (vehicle_current * grid_voltage) / 1000  # kW
                    # Store wall connector data for display
                    self._wall_connector_data = {
                        "power": wall_connector_power,
                        "amps": vehicle_current,
                        "last_update": time.time(),
                    }
                logger.debug(f"Wall Connector: {vehicle_current}A @ {grid_voltage}V = {wall_connector_power:.1f}kW")
        except Exception as e:
            logger.debug(f"Wall Connector data unavailable: {e}")

    async def _maybe_log_tesla_metrics(self, current_time: float, ev_amps: int):
        """Log Tesla metrics only when we have fresh data."""
        # Get cached Tesla data for display
        tesla_car_soc = self._cached_tesla_state.get("soc")
        tesla_car_charging_state = self._cached_tesla_state.get("charging_state")
        tesla_car_range = self._cached_tesla_state.get("range")
        tesla_car_est_range = self._cached_tesla_state.get("est_range")

        # Get wall connector data
        wall_connector_data = getattr(self, '_wall_connector_data', {})
        wall_connector_amps = wall_connector_data.get("amps", 0)
        tesla_car_charging_power = wall_connector_data.get("power", 0)

        # Only log if we have fresh Tesla data (within last 30 minutes) or active wall connector data
        last_update = self._cached_tesla_state.get("last_update", 0)
        tesla_data_age = current_time - last_update if last_update and last_update > 0 else float('inf')

        if tesla_data_age < 1800 or wall_connector_amps > 0:
            self._log_tesla_metrics(
                tesla_car_soc, tesla_car_charging_state, tesla_car_range, tesla_car_est_range,
                tesla_car_charging_power, wall_connector_amps, ev_amps
            )

            # Log CSV metrics with all data
            if self.ev_controller.is_enabled():
                home_data = self._latest_home_data
                self._log_csv_metrics(
                    home_data, tesla_car_soc, tesla_car_charging_power, tesla_car_charging_state,
                    tesla_car_range, tesla_car_est_range, tesla_car_charging_power, wall_connector_amps,
                    ev_amps
                )

    def _log_tesla_metrics(self, tesla_car_soc, tesla_car_charging_state, tesla_car_range,
                          tesla_car_est_range, tesla_car_charging_power, wall_connector_amps, ev_amps):
        """Log Tesla metrics separately when fresh data is available."""
        tesla_stats = []

        # Tesla car SOC and range
        if tesla_car_soc is not None:
            soc_str = f"SOC:{tesla_car_soc}%"
            # Add range if available
            if tesla_car_range is not None:
                soc_str += f"/{tesla_car_range:.0f}km"
            tesla_stats.append(soc_str)
        else:
            tesla_stats.append("SOC:N/A")

        # Tesla car charging power (actual power from Fleet API)
        if tesla_car_charging_power is not None and tesla_car_charging_power > 0:
            tesla_stats.append(f"Charging:{tesla_car_charging_power:.1f}kW")
        elif tesla_car_charging_state:
            # Show charging state even if no power data
            if tesla_car_charging_state != "Stopped":
                tesla_stats.append(f"Charging:0kW({tesla_car_charging_state})")
            else:
                tesla_stats.append("Charging:0kW(Stopped)")
        else:
            tesla_stats.append("Charging:N/A")

        # Wall Connector actual current
        if wall_connector_amps is not None and wall_connector_amps > 0:
            tesla_stats.append(f"WC:{wall_connector_amps:.1f}A")
        else:
            tesla_stats.append("WC:N/A")

        tesla_section = "Tesla [" + " ".join(tesla_stats) + "]"
        logger.info(f"📊 {tesla_section}")

        # Tesla-specific estimates
        estimates = []

        # EV charging target calculation
        if self.ev_controller.is_enabled():
            policy_name = self.ev_controller.get_current_policy()
            estimates.append(f"EVAmps:{ev_amps}A({policy_name})")

        # Tesla estimated range (if significantly different from EPA range)
        if tesla_car_est_range is not None and tesla_car_range is not None:
            # Show estimated range if it differs by more than 10% from EPA range
            if abs(tesla_car_est_range - tesla_car_range) / tesla_car_range > 0.1:
                estimates.append(f"EVRangeEst:{tesla_car_est_range:.0f}km")

        if estimates:
            estimates_section = "Tesla Estimates [" + " ".join(estimates) + "]"
            logger.info(f"📈 {estimates_section}")

    def _log_csv_metrics(self, home_data, tesla_car_soc, tesla_car_charging_power, tesla_car_charging_state,
                        tesla_car_range, tesla_car_est_range, wall_connector_power, wall_connector_amps, ev_amps):
        """Log comprehensive metrics to CSV file."""
        # Get real-time SoC data for logging
        realtime_soc_data = {}
        battery_data = home_data.get("battery_data")
        if battery_data:
            realtime_soc_data = {
                "home_batt_soc_realtime": battery_data.get("realtime_soc"),
                "home_batt_soc_confidence": battery_data.get("soc_confidence"),
                "home_batt_soc_source": battery_data.get("soc_source"),
                "home_batt_charging_rate_pct_per_hour": battery_data.get("charging_rate_pct_per_hour"),
            }

        # Prepare Tesla data for logging
        tesla_data = {
            "ev_soc": tesla_car_soc,
            "ev_charging_power": tesla_car_charging_power,
            "ev_charging_state": tesla_car_charging_state,
            "ev_range_km": tesla_car_range,
            "ev_est_range_km": tesla_car_est_range,
            "ev_wc_power": wall_connector_power,
            "ev_wc_amps": wall_connector_amps,
            "house_load_estimate": None,  # Not calculated in current implementation
            "house_load_confidence": None,
        }

        policy_name = self.ev_controller.get_current_policy()

        self.metrics_logger.log_metrics(
            home_batt_soc=home_data.get("official_soc"),  # Log official SoC separately
            home_batt_power=home_data.get("battery_power"),
            grid_power_flow=home_data.get("grid_power_flow"),
            solar_power=home_data.get("solar_power"),
            ev_charging_amps=ev_amps,
            ev_policy=policy_name,
            **realtime_soc_data,  # Include real-time SoC data
            **tesla_data,  # Include Tesla data
        )

    def _is_charging_window_open(self, battery_soc: float, solar_power: float) -> bool:
        """Check if conditions allow EV charging based on policy and thresholds.

        Args:
            battery_soc: Home battery SOC percentage
            solar_power: Solar power generation in watts

        Returns:
            True if charging window is open, False otherwise
        """
        if not self.ev_controller.is_enabled():
            return False

        # Get current policy from EV controller
        policy_name = self.ev_controller.get_current_policy()

        if policy_name == "ECO":
            # ECO policy: Charge when home battery > 95% (has surplus)
            return battery_soc is not None and battery_soc > 95.0
        elif policy_name == "SOLAR":
            # SOLAR policy: Only when significant solar available
            return solar_power is not None and solar_power > 1000  # 1kW minimum
        elif policy_name == "FORCE":
            # FORCE policy: Always charge (ignore conditions)
            return True
        else:
            # Unknown policy: conservative approach
            return False

    async def _sync_tesla_display_data(self, battery_soc: float = None, solar_power: float = None, force: bool = False) -> None:
        """Sync Tesla display data only when charging window is open to minimize API calls.

        Args:
            battery_soc: Home battery SOC percentage
            solar_power: Solar power generation in watts
            force: Force sync regardless of conditions
        """
        import time

        if not (self.tesla_client and self.tesla_client.is_enabled()):
            return

        # Skip sync if charging window is closed (unless forced)
        if not force and not self._is_charging_window_open(battery_soc, solar_power):
            logger.debug("Skipping Tesla sync - charging window closed")
            return

        current_time = time.time()
        time_since_last = current_time - self._cached_tesla_state["last_update"]

        # Only sync if it's been more than 10 minutes or we have no data (unless forced)
        if not force and time_since_last < self._tesla_display_sync_interval and self._cached_tesla_state["soc"] is not None:
            return

        try:
            # Use get_vehicle_data (never wakes sleeping vehicles) only every 10 minutes
            tesla_vehicle_data = await self.tesla_client.get_vehicle_data()
            if tesla_vehicle_data and tesla_vehicle_data.timestamp:
                self._cached_tesla_state.update({
                    "soc": tesla_vehicle_data.battery_level,
                    "charging_state": tesla_vehicle_data.charging_state,
                    "range": tesla_vehicle_data.battery_range,
                    "est_range": tesla_vehicle_data.est_battery_range,
                    "last_update": current_time,
                })
                logger.debug(f"Tesla display data synced: SOC={tesla_vehicle_data.battery_level}%, State={tesla_vehicle_data.charging_state}")
            else:
                logger.debug("Tesla vehicle data unavailable for display sync")
        except Exception as e:
            logger.debug(f"Tesla display sync failed: {e}")

    def get_device_data(self, device_id: str) -> dict[str, Any] | None:
        """Get current data for a specific device."""
        return self.devices.get(device_id)

    def get_all_devices(self) -> dict[str, Any]:
        """Get all discovered devices."""
        return self.devices
