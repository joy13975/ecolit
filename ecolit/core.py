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
            except Exception as e:
                logger.error(f"Failed to initialize Tesla API client: {e}")
                logger.warning("Tesla data will not be available")
                self.tesla_client = None

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

            # Track charging window state changes
            charging_window_open = self._is_charging_window_open(battery_soc, solar_power)
            if not hasattr(self, '_last_charging_window_state'):
                self._last_charging_window_state = False

            # Force sync if charging window just opened
            force_sync = charging_window_open and not self._last_charging_window_state
            if force_sync:
                logger.debug("Charging window opened - forcing Tesla sync")

            self._last_charging_window_state = charging_window_open

            # Sync Tesla display data only when charging window is open (with parameters)
            await self._sync_tesla_display_data(battery_soc, solar_power, force=force_sync)

            # Use cached Tesla data for display and Wall Connector for real-time charging data
            tesla_car_charging_power = None
            tesla_car_soc = self._cached_tesla_state["soc"]
            tesla_car_charging_state = self._cached_tesla_state["charging_state"]
            tesla_car_range = self._cached_tesla_state["range"]
            tesla_car_est_range = self._cached_tesla_state["est_range"]

            # Poll Wall Connector data for actual power consumption and current
            # This is FREE (local network call) and provides real-time charging data
            wall_connector_power = None
            wall_connector_amps = None
            if self.wall_connector_client:
                try:
                    vitals = await self.wall_connector_client.get_vitals()
                    if vitals:
                        # Get actual current and voltage
                        vehicle_current = vitals.get("vehicle_current_a", 0)
                        grid_voltage = vitals.get("grid_v", 0)
                        if vehicle_current and grid_voltage:
                            wall_connector_power = (vehicle_current * grid_voltage) / 1000  # kW
                            wall_connector_amps = vehicle_current  # Store actual amps for display
                            # Use Wall Connector power for Tesla charging display (FREE and real-time)
                            tesla_car_charging_power = wall_connector_power
                        logger.debug(
                            f"Wall Connector: {vehicle_current}A @ {grid_voltage}V = {wall_connector_power}kW"
                        )
                except Exception as e:
                    logger.debug(f"Wall Connector data unavailable: {e}")

            # Skip faulty grid device reading entirely - don't try to calculate it
            if grid_device_faulty:
                # Just use None - policies will handle missing data appropriately
                grid_power_flow = None

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

                # Execute actual charging control if not in dry-run mode
                if not self.dry_run and self.tesla_controller:
                    try:
                        control_result = await self.tesla_controller.execute_charging_control(
                            ev_amps, battery_soc, solar_power
                        )

                        # Log control actions taken
                        if control_result["actions_taken"]:
                            for action in control_result["actions_taken"]:
                                logger.info(f"🔋 TESLA CONTROL: {action}")

                        # Log warnings
                        if control_result["warnings"]:
                            for warning in control_result["warnings"]:
                                logger.warning(f"⚠️  TESLA CONTROL: {warning}")

                        # Log errors with detailed explanations
                        if control_result["errors"]:
                            for error in control_result["errors"]:
                                logger.error(f"❌ TESLA CONTROL: {error}")
                                # Log detailed explanation for user-facing errors
                                if self.tesla_controller:
                                    explanation = (
                                        self.tesla_controller._get_detailed_error_explanation(error)
                                    )
                                    logger.info(f"💡 HELP: {explanation}")

                    except Exception as e:
                        logger.error(f"Error in Tesla charging control: {e}")
                elif self.dry_run:
                    logger.debug(f"DRY-RUN: Would set Tesla charging to {ev_amps}A")

            # Log essential stats for EV charging optimization - always show both sections
            home_stats = []  # Home energy system stats
            tesla_stats = []  # Tesla vehicle and charging stats

            # === HOME SECTION ===

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

            # === TESLA SECTION ===

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
                tesla_stats.append(f"Charging:0kW({tesla_car_charging_state})")
            else:
                tesla_stats.append("Charging:N/A")

            # Wall Connector actual current
            if wall_connector_amps is not None and wall_connector_amps > 0:
                tesla_stats.append(f"WC:{wall_connector_amps:.1f}A")
            else:
                tesla_stats.append("WC:N/A")

            # === ESTIMATED VALUES ===

            # Initialize house load variables for later use
            house_load = None
            confidence = None

            # === ESTIMATES SECTION ===
            estimates = []

            # Real-time battery SOC estimate
            if realtime_soc is not None and battery_data:
                estimates.append(f"RTSOC:{realtime_soc:.2f}%")

                # Add charging rate if available
                charging_info = battery_data.get("charging_rate_pct_per_hour", 0)
                if abs(charging_info) > 0.1:
                    estimates.append(f"ChargeRate:{charging_info:+.1f}%/h")

                # Add time to target SOC based on charging/discharging state
                if self.battery_poller and self.battery_instance and battery_power is not None:
                    if battery_power > 10:  # Charging
                        # Show time to 100% when charging
                        time_to_full = (
                            self.battery_poller.realtime_soc_estimator.get_time_to_target_soc(100)
                        )
                        if time_to_full is not None and time_to_full > 0:
                            if time_to_full < 1:
                                minutes = int(time_to_full * 60)
                                estimates.append(f"To100%:{minutes}min")
                            else:
                                estimates.append(f"To100%:{time_to_full:.1f}h")
                    elif battery_power < -10:  # Discharging
                        # Show time to emergency reserve when discharging
                        reserve_soc = self.battery_instance.get("target_soc_percent", 20)
                        time_to_reserve = (
                            self.battery_poller.realtime_soc_estimator.get_time_to_target_soc(
                                reserve_soc
                            )
                        )
                        if time_to_reserve is not None and time_to_reserve > 0:
                            if time_to_reserve < 1:
                                minutes = int(time_to_reserve * 60)
                                estimates.append(f"To{reserve_soc}%:{minutes}min")
                            else:
                                estimates.append(f"To{reserve_soc}%:{time_to_reserve:.1f}h")

            # EV charging target calculation
            if self.ev_controller.is_enabled():
                policy_name = self.ev_controller.get_current_policy()
                estimates.append(f"EVAmps:{ev_amps}A({policy_name})")

            # Tesla estimated range (if significantly different from EPA range)
            if tesla_car_est_range is not None and tesla_car_range is not None:
                # Show estimated range if it differs by more than 10% from EPA range
                if abs(tesla_car_est_range - tesla_car_range) / tesla_car_range > 0.1:
                    estimates.append(f"EVRangeEst:{tesla_car_est_range:.0f}km")

            # Log the structured stats with clear sections
            home_section = "Home [" + " ".join(home_stats) + "]"
            tesla_section = "Tesla [" + " ".join(tesla_stats) + "]"
            logger.info(f"📊 {home_section} {tesla_section}")

            if estimates:
                estimates_section = "Estimates [" + " ".join(estimates) + "]"
                logger.info(f"📈 {estimates_section}")

            # Log metrics to CSV file
            if self.ev_controller.is_enabled():
                # Get real-time SoC data for logging
                realtime_soc_data = {}
                if battery_data:
                    realtime_soc_data = {
                        "home_batt_soc_realtime": battery_data.get("realtime_soc"),
                        "home_batt_soc_confidence": battery_data.get("soc_confidence"),
                        "home_batt_soc_source": battery_data.get("soc_source"),
                        "home_batt_charging_rate_pct_per_hour": battery_data.get(
                            "charging_rate_pct_per_hour"
                        ),
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
                    "house_load_estimate": house_load,
                    "house_load_confidence": confidence,
                }

                self.metrics_logger.log_metrics(
                    home_batt_soc=official_soc,  # Log official SoC separately
                    home_batt_power=battery_power,
                    grid_power_flow=grid_power_flow,
                    solar_power=solar_power,
                    ev_charging_amps=ev_amps,
                    ev_policy=policy_name,
                    **realtime_soc_data,  # Include real-time SoC data
                    **tesla_data,  # Include Tesla data
                )

        except Exception as e:
            logger.error(f"Error in polling loop: {e}")

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
