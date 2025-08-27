"""Tesla Fleet API client using tesla-fleet-api library with proper TVCP support."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tesla_fleet_api import TeslaFleetApi
from tesla_fleet_api.exceptions import VehicleOffline

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
    """Tesla Fleet API client using tesla-fleet-api library with TVCP support."""

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
        self.vin = config.get("vin", self.vehicle_tag)  # VIN for signed commands

        # Charging limits
        self.min_amps = config.get("min_charging_amps", 6)
        self.max_amps = config.get("max_charging_amps", 20)
        self.charging_voltage = config.get("charging_voltage", 200)

        # Rate limiting and safety
        self.command_rate_limit = config.get("command_rate_limit", 5)  # per minute
        self.retry_attempts = config.get("retry_attempts", 3)
        self.timeout = config.get("timeout", 10)

        # Regional Configuration
        region = config.get("region", "auto")
        if region == "auto":
            # Auto-detect from refresh token prefix
            if self.refresh_token and self.refresh_token.startswith("EU_"):
                self.region = "eu"
            elif self.refresh_token and self.refresh_token.startswith("AP_"):
                self.region = "cn"  # Closest available
            else:
                self.region = "na"
        else:
            self.region = region

        # Initialize API client (will be set up in start())
        self.api = None
        self.vehicle_data = TeslaVehicleData()
        self.command_timestamps = []  # Rate limiting

        # Private key for signed commands (TVCP)
        self.private_key = config.get("private_key")  # Path to private key file
        
        logger.info(
            f"Tesla API client initialized: vehicle_id={self.vehicle_id}, region={self.region}, "
            f"min_amps={self.min_amps}, max_amps={self.max_amps}, signed_commands={bool(self.private_key)}"
        )

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def start(self):
        """Initialize the Tesla API client."""
        if not self.enabled:
            return

        # Validate required configuration
        if not all([self.client_id, self.client_secret, self.refresh_token]):
            raise ValueError("Tesla authentication credentials required")

        try:
            # Create HTTP session
            import aiohttp
            session = aiohttp.ClientSession()
            
            # Get access token first
            access_token = await self._get_access_token()
            
            # Initialize API client
            self.api = TeslaFleetApi(
                session=session,
                access_token=access_token,
                region=self.region,
            )

            # Set up private key for signed commands if provided
            if self.private_key:
                try:
                    with open(self.private_key, 'r') as f:
                        private_key_content = f.read()
                    self.api.private_key = private_key_content
                    logger.info("Private key loaded for signed vehicle commands (TVCP)")
                except Exception as e:
                    logger.warning(f"Failed to load private key: {e}")
                    logger.warning("Vehicle commands may not work without signed commands")

            logger.info("Tesla API client started successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Tesla API client: {e}")
            raise

    async def close(self):
        """Close the Tesla API client."""
        if self.api and hasattr(self.api, 'session'):
            # Close the HTTP session
            await self.api.session.close()
        logger.info("Tesla API client closed")

    async def _get_access_token(self) -> str:
        """Get access token using refresh token."""
        try:
            import aiohttp
            
            # Tesla OAuth token endpoint
            token_url = "https://auth.tesla.com/oauth2/v3/token"
            
            data = {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(token_url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        access_token = result["access_token"]
                        logger.info("Tesla access token obtained successfully")
                        return access_token
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to get access token: {response.status} - {error_text}")
                        raise RuntimeError(f"Token refresh failed: {response.status}")
                        
        except Exception as e:
            logger.error(f"Failed to get Tesla access token: {e}")
            raise

    async def get_vehicle_data(self) -> TeslaVehicleData:
        """Get current vehicle data."""
        if not self.enabled or not self.api:
            return TeslaVehicleData()

        try:
            # Get vehicle data from Fleet API through vehicles endpoint
            vehicle_api = self.api.vehicles.specific(self.vehicle_id)
            vehicle_data = await vehicle_api.vehicle_data()
            
            if not vehicle_data or vehicle_data.get("response") is None:
                return TeslaVehicleData()

            response = vehicle_data["response"]
            charge_state = response.get("charge_state", {})

            # Extract relevant data
            new_data = TeslaVehicleData(
                battery_level=charge_state.get("battery_level"),
                charging_power=charge_state.get("charger_power"),
                charge_amps=charge_state.get("charge_current_request"),
                charging_state=charge_state.get("charging_state"),
                charge_port_status=charge_state.get("charge_port_door_open"),
                timestamp=datetime.now(),
            )

            # Cache the data
            self.vehicle_data = new_data
            
            logger.debug(
                f"Tesla data: SOC={new_data.battery_level}%, "
                f"Power={new_data.charging_power}kW, "
                f"State={new_data.charging_state}"
            )

            return new_data

        except VehicleOffline:
            logger.debug("Vehicle is offline/sleeping (expected)")
            return TeslaVehicleData()
        except Exception as e:
            logger.error(f"Failed to get Tesla vehicle data: {e}")
            return TeslaVehicleData()

    async def get_charging_schedule(self) -> dict[str, Any]:
        """Get current vehicle charging schedule configuration."""
        if not self.enabled or not self.api:
            return {}

        try:
            # Get vehicle data with charge schedule endpoints
            vehicle_api = self.api.vehicles.specific(self.vehicle_id)
            vehicle_data = await vehicle_api.vehicle_data(endpoints=["charge_schedule_data"])
            
            if not vehicle_data or vehicle_data.get("response") is None:
                return {}

            response = vehicle_data["response"]
            charge_schedule = response.get("charge_schedule_data", {})
            
            logger.debug(f"Tesla charging schedule: {charge_schedule}")
            return charge_schedule

        except VehicleOffline:
            logger.debug("Vehicle is offline/sleeping - cannot get charging schedule")
            return {"status": "vehicle_sleeping"}
        except Exception as e:
            logger.error(f"Failed to get Tesla charging schedule: {e}")
            return {}

    async def get_charging_config(self) -> dict[str, Any]:
        """Get current vehicle charging configuration including amp settings."""
        if not self.enabled or not self.api:
            return {}

        try:
            # Get vehicle data with charge state endpoint
            vehicle_api = self.api.vehicles.specific(self.vehicle_id)
            vehicle_data = await vehicle_api.vehicle_data(endpoints=["charge_state"])
            
            if not vehicle_data or vehicle_data.get("response") is None:
                return {}

            response = vehicle_data["response"]
            charge_state = response.get("charge_state", {})
            
            config = {
                "charge_current_request": charge_state.get("charge_current_request"),
                "charge_current_request_max": charge_state.get("charge_current_request_max"),
                "charge_limit_soc": charge_state.get("charge_limit_soc"),
                "charging_state": charge_state.get("charging_state"),
                "charger_voltage": charge_state.get("charger_voltage"),
                "charger_power": charge_state.get("charger_power"),
            }
            
            logger.debug(f"Tesla charging config: {config}")
            return config

        except VehicleOffline:
            logger.debug("Vehicle is offline/sleeping - cannot get charging config")
            return {"status": "vehicle_sleeping"}
        except Exception as e:
            logger.error(f"Failed to get Tesla charging config: {e}")
            return {}

    async def poll_vehicle_data_with_wake_option(self) -> tuple[TeslaVehicleData, bool]:
        """Poll vehicle data and return data plus whether vehicle was sleeping."""
        if not self.enabled or not self.api:
            return TeslaVehicleData(), False

        try:
            # Try to get vehicle data first - bypass the exception handling in get_vehicle_data
            vehicle_api = self.api.vehicles.specific(self.vehicle_id)
            vehicle_data_result = await vehicle_api.vehicle_data()
            
            # If we get here, vehicle responded - extract data
            response = vehicle_data_result.get("response", {})
            charge_state = response.get("charge_state", {})
            
            vehicle_data = TeslaVehicleData(
                battery_level=charge_state.get("battery_level"),
                charging_power=charge_state.get("charger_power"),
                charge_amps=charge_state.get("charge_current_request"),
                charging_state=charge_state.get("charging_state"),
                charge_port_status=charge_state.get("charge_port_door_open"),
                timestamp=datetime.now(),
            )
            
            return vehicle_data, False
                
        except VehicleOffline:
            # Vehicle is offline/sleeping
            logger.debug("Vehicle is offline/sleeping")
            return TeslaVehicleData(), True
        except Exception as e:
            logger.error(f"Failed to poll Tesla vehicle data: {e}")
            return TeslaVehicleData(), False

    async def wake_up(self) -> bool:
        """Wake up a sleeping Tesla vehicle."""
        if not self.enabled or not self.api:
            return False

        try:
            vehicle_api = self.api.vehicles.specific(self.vehicle_id)
            result = await vehicle_api.wake_up()
            
            if result and result.get("response"):
                logger.info("Tesla vehicle wake_up command sent successfully")
                return True
            else:
                logger.error(f"Tesla wake_up failed: {result}")
                return False

        except Exception as e:
            logger.error(f"Failed to wake Tesla vehicle: {e}")
            return False

    async def set_charging_amps(self, amps: int) -> bool:
        """Set Tesla charging amperage using tesla-fleet-api library."""
        if not self.enabled or not self.api:
            return False

        # Clamp to safety limits
        amps = max(self.min_amps, min(amps, self.max_amps))

        try:
            # Use signed commands if private key is configured
            if self.private_key and self.vin:
                # Use signed commands for TVCP-enabled vehicles
                vehicle_api = self.api.vehicles.specificSigned(self.vin)
                result = await vehicle_api.set_charging_amps(charging_amps=amps)
            else:
                # Fall back to regular Fleet API (may fail with 403 on modern vehicles)
                vehicle_api = self.api.vehicles.specific(self.vehicle_id)
                result = await vehicle_api.set_charging_amps(charging_amps=amps)
            
            if result and result.get("response", {}).get("result"):
                logger.info(f"Tesla charging amps set to {amps}A")
                return True
            else:
                logger.error(f"Failed to set Tesla charging amps: {result}")
                return False

        except Exception as e:
            error_msg = str(e)
            if "Tesla Vehicle Command Protocol required" in error_msg:
                logger.error(
                    f"TVCP required for set_charging_amps. Configure private_key in config to enable signed commands."
                )
            else:
                logger.error(f"Failed to set Tesla charging amps: {e}")
            return False

    async def charge_start(self) -> bool:
        """Start Tesla charging."""
        if not self.enabled or not self.api:
            return False

        try:
            if self.private_key and self.vin:
                vehicle_api = self.api.vehicles.specificSigned(self.vin)
                result = await vehicle_api.charge_start()
            else:
                vehicle_api = self.api.vehicles.specific(self.vehicle_id)
                result = await vehicle_api.charge_start()
            
            if result and result.get("response", {}).get("result"):
                logger.info("Tesla charging started")
                return True
            else:
                logger.error(f"Failed to start Tesla charging: {result}")
                return False

        except Exception as e:
            logger.error(f"Failed to start Tesla charging: {e}")
            return False

    async def charge_stop(self) -> bool:
        """Stop Tesla charging."""
        if not self.enabled or not self.api:
            return False

        try:
            if self.private_key and self.vin:
                vehicle_api = self.api.vehicles.specificSigned(self.vin)
                result = await vehicle_api.charge_stop()
            else:
                vehicle_api = self.api.vehicles.specific(self.vehicle_id)
                result = await vehicle_api.charge_stop()
            
            if result and result.get("response", {}).get("result"):
                logger.info("Tesla charging stopped")
                return True
            else:
                logger.error(f"Failed to stop Tesla charging: {result}")
                return False

        except Exception as e:
            logger.error(f"Failed to stop Tesla charging: {e}")
            return False

    def is_enabled(self) -> bool:
        """Check if Tesla API client is enabled."""
        return self.enabled

    def is_connected(self) -> bool:
        """Check if API client is connected."""
        return self.api is not None

    def get_status(self) -> dict[str, Any]:
        """Get Tesla API client status."""
        if not self.enabled:
            return {"enabled": False}

        return {
            "enabled": True,
            "authenticated": self.api is not None,
            "vehicle_id": self.vehicle_id,
            "region": self.region,
            "signed_commands": bool(self.private_key),
            "min_amps": self.min_amps,
            "max_amps": self.max_amps,
            "last_data": self.vehicle_data.timestamp.isoformat()
            if self.vehicle_data.timestamp
            else None,
        }