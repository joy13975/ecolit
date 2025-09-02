"""Tesla Fleet API client for real-time charging control and telemetry."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import websockets

logger = logging.getLogger(__name__)


@dataclass
class TeslaVehicleData:
    """Tesla vehicle telemetry data."""

    battery_level: float | None = None  # EV battery SOC %
    charging_power: float | None = None  # Current charging power (kW)
    charge_amps: float | None = None  # Current charging amperage
    charging_state: str | None = None  # "Charging", "Complete", "Stopped", etc.
    charge_port_status: str | None = None  # Charge port status
    timestamp: datetime | None = None  # Data timestamp


class TeslaAPIClient:
    """Tesla Fleet API client for charging control and real-time telemetry."""

    def __init__(self, config: dict[str, Any]):
        """Initialize Tesla API client with configuration."""
        self.config = config
        self.enabled = config.get("enabled", False)

        if not self.enabled:
            logger.info("Tesla API client disabled")
            return

        # Authentication configuration
        self.refresh_token = config.get("refresh_token")
        self.client_id = config.get("client_id")
        self.client_secret = config.get("client_secret")

        # Vehicle configuration
        self.vehicle_id = config.get("vehicle_id")
        self.vehicle_tag = config.get("vehicle_tag")

        # Fleet Telemetry configuration
        self.telemetry_endpoint = config.get(
            "telemetry_endpoint", "wss://streaming.vn.tesla.services/connect"
        )
        self.telemetry_fields = config.get(
            "telemetry_fields",
            ["Battery_level", "Charging_power", "Charge_amps", "Charge_port_status"],
        )

        # Charging limits
        self.min_amps = config.get("min_charging_amps", 6)
        self.max_amps = config.get("max_charging_amps", 20)
        self.charging_voltage = config.get("charging_voltage", 200)

        # Rate limiting and safety
        self.command_rate_limit = config.get("command_rate_limit", 5)  # per minute
        self.retry_attempts = config.get("retry_attempts", 3)
        self.timeout = config.get("timeout", 10)

        # State tracking
        self.access_token = None
        self.token_expires_at = None
        self.websocket = None
        self.telemetry_task = None
        self.token_refresh_task = None
        self.vehicle_data = TeslaVehicleData()
        self.command_timestamps = []  # Rate limiting

        # Regional Configuration (auto-detected from refresh_token prefix)
        region = config.get("region", "auto")

        if region == "auto":
            # Auto-detect from refresh token prefix
            if self.refresh_token and self.refresh_token.startswith("EU_"):
                self.base_url = "https://fleet-api.prd.eu.vn.cloud.tesla.com"
                self.detected_region = "eu"
            elif self.refresh_token and self.refresh_token.startswith("AP_"):
                self.base_url = "https://fleet-api.prd.ap.vn.cloud.tesla.com"
                self.detected_region = "ap"
            else:
                self.base_url = "https://fleet-api.prd.na.vn.cloud.tesla.com"
                self.detected_region = "na"
        else:
            # Use configured region
            if region == "na":
                self.base_url = "https://fleet-api.prd.na.vn.cloud.tesla.com"
            elif region == "eu":
                self.base_url = "https://fleet-api.prd.eu.vn.cloud.tesla.com"
            elif region == "ap":
                self.base_url = "https://fleet-api.prd.ap.vn.cloud.tesla.com"
            self.detected_region = region

        # TVCP proxy configuration
        self.use_tvcp_proxy = config.get("use_tvcp_proxy", False)
        self.proxy_base_url = config.get("proxy_base_url", "https://localhost:4443")
        self.proxy_cert_path = config.get("proxy_cert_path")

        # Override base URL if using TVCP proxy
        if self.use_tvcp_proxy:
            self.base_url = self.proxy_base_url
            logger.info(f"Using TVCP proxy: {self.proxy_base_url}")

        # HTTP session
        self.session = None

        logger.info(
            f"Tesla API client initialized: vehicle_id={self.vehicle_id}, region={self.detected_region}, min_amps={self.min_amps}, max_amps={self.max_amps}, tvcp_proxy={self.use_tvcp_proxy}"
        )

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def start(self):
        """Initialize the Tesla API client and optionally start telemetry."""
        if not self.enabled:
            return

        # Configure SSL context for TVCP proxy
        ssl_context = None
        if self.use_tvcp_proxy:
            import ssl

            ssl_context = ssl.create_default_context()

            if "localhost" in self.proxy_base_url or "127.0.0.1" in self.proxy_base_url:
                # For local proxy, disable hostname verification
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                logger.info("Using local TVCP proxy with disabled SSL verification")
            elif self.proxy_cert_path:
                # For remote proxy with custom cert
                ssl_context.load_verify_locations(self.proxy_cert_path)
                logger.info(f"Using TVCP proxy with custom cert: {self.proxy_cert_path}")

        # Create session with appropriate SSL context
        connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout), connector=connector
        )

        # Authenticate and get access token
        await self._authenticate()

        # Start automatic token refresh task
        self.token_refresh_task = asyncio.create_task(self._token_refresh_loop())
        logger.info("Tesla automatic token refresh started")

        # Only start telemetry if explicitly enabled and configured
        enable_telemetry = self.config.get("enable_telemetry", False)
        if enable_telemetry and self.telemetry_endpoint and self.vehicle_id:
            logger.info("Starting Tesla telemetry WebSocket...")
            self.telemetry_task = asyncio.create_task(self._telemetry_loop())
        else:
            logger.info("Tesla telemetry disabled - using polling mode")

        logger.info("Tesla API client started successfully")

    async def close(self):
        """Close the Tesla API client and cleanup resources."""
        if not self.enabled:
            return

        # Stop token refresh task
        if self.token_refresh_task:
            self.token_refresh_task.cancel()
            try:
                await self.token_refresh_task
            except asyncio.CancelledError:
                pass

        # Stop telemetry task
        if self.telemetry_task:
            self.telemetry_task.cancel()
            try:
                await self.telemetry_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self.websocket:
            await self.websocket.close()

        # Close HTTP session
        if self.session:
            await self.session.close()

        logger.info("Tesla API client closed")

    async def _authenticate(self):
        """Authenticate with Tesla Fleet API and get access token."""
        if not all([self.client_id, self.client_secret, self.refresh_token]):
            logger.error("Missing Tesla authentication credentials")
            raise ValueError("Tesla authentication credentials required")

        auth_url = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
        auth_data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            async with self.session.post(
                auth_url,
                data=auth_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    self.access_token = data["access_token"]
                    expires_in = data.get("expires_in", 3600)  # Default 1 hour
                    self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)

                    # Also update refresh token if provided (some auth flows return new refresh tokens)
                    if "refresh_token" in data:
                        self.refresh_token = data["refresh_token"]
                        logger.debug("Refresh token updated")

                    logger.info(f"Tesla authentication successful (expires in {expires_in}s)")
                else:
                    error_text = await response.text()
                    logger.error(f"Tesla authentication failed: {response.status} - {error_text}")
                    raise RuntimeError(f"Authentication failed: {response.status}")

        except Exception as e:
            logger.error(f"Tesla authentication error: {e}")
            raise

    async def _ensure_authenticated(self):
        """Ensure we have a valid access token."""
        if not self.access_token or (
            self.token_expires_at and datetime.now() >= self.token_expires_at
        ):
            logger.info("Access token expired, refreshing...")
            await self._authenticate()

    async def _token_refresh_loop(self):
        """Background task to automatically refresh access token before expiry."""
        while True:
            try:
                if self.token_expires_at:
                    # Calculate time until token expires
                    now = datetime.now()
                    time_until_expiry = (self.token_expires_at - now).total_seconds()

                    # Refresh 10 minutes before expiry (or 25% of total time, whichever is less)
                    refresh_buffer = min(
                        600, time_until_expiry * 0.25
                    )  # 10 min or 25% of token life
                    sleep_time = time_until_expiry - refresh_buffer

                    if sleep_time > 0:
                        logger.debug(f"Tesla token refresh scheduled in {sleep_time:.0f} seconds")
                        await asyncio.sleep(sleep_time)

                    # Refresh the token proactively
                    logger.info("Proactively refreshing Tesla access token...")
                    try:
                        await self._authenticate()
                        logger.info("Tesla token refreshed successfully")
                    except Exception as auth_error:
                        logger.error(f"Failed to refresh Tesla token: {auth_error}")
                        # Continue the loop to retry later
                        await asyncio.sleep(300)  # Wait 5 minutes before next attempt
                        continue
                else:
                    # If no expiry time, check every hour
                    await asyncio.sleep(3600)

            except asyncio.CancelledError:
                logger.info("Tesla token refresh task cancelled")
                break
            except Exception as e:
                logger.error(f"Tesla token refresh error: {e}")
                # Wait 5 minutes before retrying on error
                await asyncio.sleep(300)

    async def _rate_limit_check(self):
        """Check and enforce API rate limits."""
        now = time.time()
        # Remove timestamps older than 1 minute
        self.command_timestamps = [ts for ts in self.command_timestamps if now - ts < 60]

        if len(self.command_timestamps) >= self.command_rate_limit:
            oldest_timestamp = min(self.command_timestamps)
            wait_time = 60 - (now - oldest_timestamp)
            if wait_time > 0:
                logger.warning(f"Rate limit reached, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)

        self.command_timestamps.append(now)

    async def _telemetry_loop(self):
        """Main telemetry streaming loop."""
        while True:
            try:
                await self._connect_telemetry()
                await self._stream_telemetry()
            except Exception as e:
                logger.error(f"Telemetry error: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def _connect_telemetry(self):
        """Connect to Tesla Fleet Telemetry WebSocket."""
        await self._ensure_authenticated()

        headers = {"Authorization": f"Bearer {self.access_token}"}

        # Connect to WebSocket
        self.websocket = await websockets.connect(
            self.telemetry_endpoint, additional_headers=headers, ping_interval=30, ping_timeout=10
        )

        # Send subscription message
        subscription = {
            "msg_type": "data:subscribe",
            "tag": self.vehicle_tag or self.vehicle_id,
            "value": self.telemetry_fields,
            "intervalMs": 500,  # 500ms updates
        }

        await self.websocket.send(json.dumps(subscription))
        logger.info(f"Tesla telemetry connected: fields={self.telemetry_fields}")

    async def _stream_telemetry(self):
        """Stream and process telemetry data."""
        async for message in self.websocket:
            try:
                data = json.loads(message)
                await self._process_telemetry_data(data)
            except Exception as e:
                logger.error(f"Telemetry processing error: {e}")

    async def _process_telemetry_data(self, data: dict[str, Any]):
        """Process incoming telemetry data."""
        if data.get("msg_type") != "data:update":
            return

        # Extract vehicle data
        values = data.get("value", {})

        # Update vehicle data
        if "Battery_level" in values:
            self.vehicle_data.battery_level = values["Battery_level"]

        if "Charging_power" in values:
            self.vehicle_data.charging_power = values["Charging_power"]

        if "Charge_amps" in values:
            self.vehicle_data.charge_amps = values["Charge_amps"]

        if "Charge_port_status" in values:
            self.vehicle_data.charge_port_status = values["Charge_port_status"]

        self.vehicle_data.timestamp = datetime.now()

        logger.debug(
            f"Tesla telemetry: SOC={self.vehicle_data.battery_level}%, "
            f"Power={self.vehicle_data.charging_power}kW, "
            f"Amps={self.vehicle_data.charge_amps}A"
        )

    async def get_vehicle_data(self) -> TeslaVehicleData:
        """Get current vehicle data - either from telemetry stream or by polling."""
        # If telemetry is active and recent, use that data
        if (
            self.vehicle_data.timestamp
            and (datetime.now() - self.vehicle_data.timestamp).total_seconds() < 30
        ):
            return self.vehicle_data

        # Otherwise, poll the vehicle directly
        return await self.poll_vehicle_data()

    async def poll_vehicle_data(self) -> TeslaVehicleData:
        """Poll vehicle data directly from Tesla API."""
        if not self.enabled or not self.vehicle_tag:
            return TeslaVehicleData()

        try:
            await self._ensure_authenticated()

            # Try Fleet API first, fallback to Owner API
            url = f"{self.base_url}/api/1/vehicles/{self.vehicle_tag}/vehicle_data"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    vehicle_data = result.get("response", {})

                    # Extract relevant data
                    charge_state = vehicle_data.get("charge_state", {})

                    # Update our data structure
                    new_data = TeslaVehicleData(
                        battery_level=charge_state.get("battery_level"),
                        charging_power=charge_state.get("charger_power"),
                        charge_amps=charge_state.get("charge_current_request"),
                        charging_state=charge_state.get("charging_state"),
                        charge_port_status=charge_state.get("charge_port_door_open"),
                        timestamp=datetime.now(),
                    )

                    # Cache the polled data
                    self.vehicle_data = new_data

                    logger.debug(
                        f"Tesla polled: SOC={new_data.battery_level}%, "
                        f"Power={new_data.charging_power}kW, "
                        f"State={new_data.charging_state}"
                    )

                    return new_data

                else:
                    error_text = await response.text()
                    logger.warning(
                        f"Tesla vehicle polling failed: {response.status} - {error_text}"
                    )

                    if response.status == 412:
                        logger.info("Fleet API not registered - trying Owner API fallback")
                        # Try Owner API as fallback
                        owner_url = f"https://owner-api.teslamotors.com/api/1/vehicles/{self.vehicle_tag}/vehicle_data"
                        async with self.session.get(owner_url, headers=headers) as owner_response:
                            if owner_response.status == 200:
                                result = await owner_response.json()
                                vehicle_data = result.get("response", {})

                                # Extract relevant data
                                charge_state = vehicle_data.get("charge_state", {})

                                # Update our data structure
                                new_data = TeslaVehicleData(
                                    battery_level=charge_state.get("battery_level"),
                                    charging_power=charge_state.get("charger_power"),
                                    charge_amps=charge_state.get("charge_current_request"),
                                    charging_state=charge_state.get("charging_state"),
                                    charge_port_status=charge_state.get("charge_port_door_open"),
                                    timestamp=datetime.now(),
                                )

                                # Cache the polled data
                                self.vehicle_data = new_data

                                logger.info(
                                    f"Tesla Owner API: SOC={new_data.battery_level}%, "
                                    f"Power={new_data.charging_power}kW, "
                                    f"State={new_data.charging_state}"
                                )

                                return new_data
                            else:
                                logger.warning(f"Owner API also failed: {owner_response.status}")
                    elif response.status == 401:
                        logger.info("Tesla authentication may have expired")

                    return TeslaVehicleData()

        except Exception as e:
            logger.error(f"Tesla polling error: {e}")
            return TeslaVehicleData()

    async def _vehicle_command(self, command: str, **kwargs) -> dict[str, Any]:
        """Execute a vehicle command via Fleet API."""
        if not self.enabled:
            raise RuntimeError("Tesla API client disabled")

        await self._ensure_authenticated()
        await self._rate_limit_check()

        url = f"{self.base_url}/api/1/vehicles/{self.vehicle_tag}/command/{command}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        for attempt in range(self.retry_attempts):
            try:
                async with self.session.post(url, headers=headers, json=kwargs) as response:
                    result = await response.json()

                    if response.status == 200:
                        logger.info(f"Tesla command '{command}' successful")
                        return result
                    else:
                        logger.error(
                            f"Tesla command '{command}' failed: {response.status} - {result}"
                        )

                        # Handle TVCP requirement specifically
                        if response.status == 403:
                            error_msg = result.get("error", "")
                            if "Tesla Vehicle Command Protocol required" in str(error_msg):
                                if self.use_tvcp_proxy:
                                    logger.error(
                                        f"TVCP proxy configured but command '{command}' still failed with 403. Check proxy setup."
                                    )
                                    raise RuntimeError(
                                        f"TVCP proxy is configured but command '{command}' failed. "
                                        "Check that the Tesla HTTP proxy is running and properly configured."
                                    )
                                else:
                                    logger.error(
                                        f"TVCP required for command '{command}'. Your vehicle requires the Tesla Vehicle Command Protocol. "
                                        "Configure use_tvcp_proxy: true in your config to enable TVCP support."
                                    )
                                    raise RuntimeError(
                                        "Tesla Vehicle Command Protocol required. "
                                        "Set use_tvcp_proxy: true in your Tesla configuration and ensure the HTTP proxy is running."
                                    )

                        # Don't retry certain error types
                        if response.status in [401, 403, 404]:
                            break

            except Exception as e:
                logger.error(f"Tesla command '{command}' error (attempt {attempt + 1}): {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff

        raise RuntimeError(f"Tesla command '{command}' failed after {self.retry_attempts} attempts")

    async def set_charging_amps(self, amps: int) -> bool:
        """Set Tesla charging amperage."""
        if not self.enabled:
            return False

        # Clamp to safety limits
        amps = max(self.min_amps, min(amps, self.max_amps))

        try:
            await self._vehicle_command("set_charging_amps", charging_amps=amps)
            logger.info(f"Tesla charging amps set to {amps}A")
            return True
        except Exception as e:
            logger.error(f"Failed to set Tesla charging amps: {e}")
            return False

    async def charge_start(self) -> bool:
        """Start Tesla charging."""
        if not self.enabled:
            return False

        try:
            await self._vehicle_command("charge_start")
            logger.info("Tesla charging started")
            return True
        except Exception as e:
            logger.error(f"⚠️　Failed to start Tesla charging: {e}")
            return False

    async def charge_stop(self) -> bool:
        """Stop Tesla charging."""
        if not self.enabled:
            return False

        try:
            await self._vehicle_command("charge_stop")
            logger.info("Tesla charging stopped")
            return True
        except Exception as e:
            logger.error(f"Failed to stop Tesla charging: {e}")
            return False

    async def set_charge_limit(self, percent: int) -> bool:
        """Set Tesla charge limit percentage."""
        if not self.enabled:
            return False

        percent = max(50, min(100, percent))  # Tesla limits

        try:
            await self._vehicle_command("set_charge_limit", percent=percent)
            logger.info(f"Tesla charge limit set to {percent}%")
            return True
        except Exception as e:
            logger.error(f"Failed to set Tesla charge limit: {e}")
            return False

    async def wake_up(self) -> bool:
        """Wake up a sleeping Tesla vehicle."""
        if not self.enabled:
            return False

        try:
            await self._ensure_authenticated()
            await self._rate_limit_check()

            # Wake_up is NOT a command endpoint - it's a direct endpoint
            url = f"{self.base_url}/api/1/vehicles/{self.vehicle_tag}/wake_up"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            async with self.session.post(url, headers=headers) as response:
                result = await response.json()

                if response.status == 200:
                    logger.info("Tesla vehicle wake_up command sent successfully")
                    return True
                else:
                    logger.error(f"Tesla wake_up failed: {response.status} - {result}")
                    return False

        except Exception as e:
            logger.error(f"Failed to wake Tesla vehicle: {e}")
            return False

    async def poll_vehicle_data_with_wake_option(self) -> tuple[TeslaVehicleData, bool]:
        """
        Poll vehicle data and return data plus whether vehicle was sleeping.
        Returns (vehicle_data, is_sleeping).
        """
        if not self.enabled or not self.vehicle_tag:
            return TeslaVehicleData(), False

        try:
            await self._ensure_authenticated()

            url = f"{self.base_url}/api/1/vehicles/{self.vehicle_tag}/vehicle_data"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    vehicle_data = result.get("response", {})

                    # Extract relevant data
                    charge_state = vehicle_data.get("charge_state", {})

                    # Update our data structure
                    new_data = TeslaVehicleData(
                        battery_level=charge_state.get("battery_level"),
                        charging_power=charge_state.get("charger_power"),
                        charge_amps=charge_state.get("charge_current_request"),
                        charging_state=charge_state.get("charging_state"),
                        charge_port_status=charge_state.get("charge_port_door_open"),
                        timestamp=datetime.now(),
                    )

                    # Cache the polled data
                    self.vehicle_data = new_data
                    return new_data, False

                elif response.status == 408:
                    # Vehicle is sleeping/offline
                    error_text = await response.text()
                    logger.info(f"Vehicle sleeping: {error_text}")
                    return TeslaVehicleData(), True

                else:
                    error_text = await response.text()
                    logger.warning(
                        f"Tesla vehicle polling failed: {response.status} - {error_text}"
                    )
                    return TeslaVehicleData(), False

        except Exception as e:
            logger.error(f"Tesla polling error: {e}")
            return TeslaVehicleData(), False

    def is_enabled(self) -> bool:
        """Check if Tesla API client is enabled."""
        return self.enabled

    def is_connected(self) -> bool:
        """Check if telemetry is connected."""
        return self.websocket is not None and not self.websocket.closed

    async def get_charging_schedule(self) -> dict[str, Any]:
        """Get current vehicle charging schedule configuration."""
        if not self.enabled or not self.vehicle_tag:
            return {}

        try:
            await self._ensure_authenticated()

            # Request specific charge schedule data from vehicle
            url = f"{self.base_url}/api/1/vehicles/{self.vehicle_tag}/vehicle_data?endpoints=charge_schedule_data"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    vehicle_data = result.get("response", {})

                    # Extract charging schedule data
                    charge_schedule = vehicle_data.get("charge_schedule_data", {})

                    logger.debug(f"Tesla charging schedule: {charge_schedule}")
                    return charge_schedule

                elif response.status == 408:
                    logger.info("Vehicle sleeping - cannot get charging schedule")
                    return {"status": "vehicle_sleeping"}

                else:
                    error_text = await response.text()
                    logger.warning(
                        f"Failed to get charging schedule: {response.status} - {error_text}"
                    )
                    return {}

        except Exception as e:
            logger.error(f"Error getting charging schedule: {e}")
            return {}

    async def get_charging_config(self) -> dict[str, Any]:
        """Get current vehicle charging configuration including amp settings."""
        if not self.enabled or not self.vehicle_tag:
            return {}

        try:
            await self._ensure_authenticated()

            # Get vehicle data for charge state information
            url = f"{self.base_url}/api/1/vehicles/{self.vehicle_tag}/vehicle_data?endpoints=charge_state"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    vehicle_data = result.get("response", {})

                    # Extract charge state data
                    charge_state = vehicle_data.get("charge_state", {})

                    config = {
                        "charge_current_request": charge_state.get("charge_current_request"),
                        "charge_current_request_max": charge_state.get(
                            "charge_current_request_max"
                        ),
                        "charge_limit_soc": charge_state.get("charge_limit_soc"),
                        "charging_state": charge_state.get("charging_state"),
                        "charger_voltage": charge_state.get("charger_voltage"),
                        "charger_power": charge_state.get("charger_power"),
                    }

                    logger.debug(f"Tesla charging config: {config}")
                    return config

                elif response.status == 408:
                    logger.info("Vehicle sleeping - cannot get charging config")
                    return {"status": "vehicle_sleeping"}

                else:
                    error_text = await response.text()
                    logger.warning(
                        f"Failed to get charging config: {response.status} - {error_text}"
                    )
                    return {}

        except Exception as e:
            logger.error(f"Error getting charging config: {e}")
            return {}

    def get_status(self) -> dict[str, Any]:
        """Get Tesla API client status."""
        if not self.enabled:
            return {"enabled": False}

        return {
            "enabled": True,
            "authenticated": self.access_token is not None,
            "telemetry_connected": self.is_connected(),
            "vehicle_id": self.vehicle_id,
            "region": getattr(self, "detected_region", "unknown"),
            "base_url": getattr(self, "base_url", "unknown"),
            "min_amps": self.min_amps,
            "max_amps": self.max_amps,
            "last_telemetry": self.vehicle_data.timestamp.isoformat()
            if self.vehicle_data.timestamp
            else None,
        }
