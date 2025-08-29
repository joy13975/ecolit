"""Tesla Fleet API client using tesla-fleet-api library with proper TVCP support."""

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
    battery_range: float | None = None  # EPA rated range in km (converted from miles)
    ideal_battery_range: float | None = None  # Ideal range in km (converted from miles)
    est_battery_range: float | None = None  # Estimated range based on driving efficiency in km
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

        # Token refresh state tracking
        self._refresh_attempted = False

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
                    from cryptography.hazmat.primitives import serialization

                    with open(self.private_key, "rb") as f:
                        private_key_bytes = f.read()

                    # Parse the PEM private key
                    private_key_obj = serialization.load_pem_private_key(
                        private_key_bytes,
                        password=None,  # Assuming no password
                    )

                    self.api.private_key = private_key_obj
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
        if self.api and hasattr(self.api, "session"):
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
                    elif response.status == 401:
                        error_text = await response.text()
                        logger.error(
                            f"Failed to get access token: {response.status} - {error_text}"
                        )

                        # Check if this is a complete token expiration requiring new OAuth
                        if "login_required" in error_text:
                            logger.warning(
                                "🔑 Tesla refresh token completely expired - starting OAuth flow..."
                            )

                            # Automatically invoke the mint process
                            if await self._mint_new_tokens():
                                logger.info(
                                    "✅ New Tesla tokens obtained - retrying authentication"
                                )
                                # Reload config with new tokens
                                await self._reload_config()
                                self._refresh_attempted = False  # Reset flag for new tokens
                                return await self._get_access_token()
                            else:
                                logger.error(
                                    "❌ Failed to obtain new Tesla tokens - Tesla data will be unavailable"
                                )
                                raise RuntimeError(f"Token mint failed: {response.status}")

                        # Only attempt refresh once for other 401 errors
                        if not self._refresh_attempted:
                            logger.info(
                                "🔄 Tesla access token expired - attempting automatic refresh..."
                            )
                            self._refresh_attempted = True

                            # Attempt automatic token refresh
                            if await self._refresh_tokens():
                                logger.info(
                                    "✅ Tesla tokens refreshed successfully - retrying authentication"
                                )
                                # Reload the refreshed token and retry
                                await self._reload_config()
                                return await self._get_access_token()
                            else:
                                logger.error(
                                    "❌ Automatic token refresh failed - Tesla data will be unavailable"
                                )
                                raise RuntimeError(f"Token refresh failed: {response.status}")
                        else:
                            logger.error(
                                "❌ Token refresh already attempted - Tesla data will be unavailable"
                            )
                            raise RuntimeError(f"Token refresh failed: {response.status}")
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Failed to get access token: {response.status} - {error_text}"
                        )
                        raise RuntimeError(f"Token refresh failed: {response.status}")

        except Exception as e:
            logger.error(f"Failed to get Tesla access token: {e}")
            raise

    async def _refresh_tokens(self) -> bool:
        """Automatically refresh Tesla tokens by calling the refresh module."""
        try:
            # Import and call the refresh function directly
            import sys
            from pathlib import Path

            # Add the project root to Python path to import tesla modules
            project_root = Path(__file__).parent.parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            import yaml

            from ecolit.tesla.refresh import refresh_user_token

            # Load current config
            config_path = project_root / "config.yaml"
            if not config_path.exists():
                logger.error("Config file not found for token refresh")
                return False

            with open(config_path) as f:
                config = yaml.safe_load(f)

            tesla_config = config.get("tesla", {})

            # Attempt token refresh
            success = await refresh_user_token(tesla_config, config, config_path)
            return success

        except Exception as e:
            logger.error(f"Failed to automatically refresh Tesla tokens: {e}")
            return False

    async def _mint_new_tokens(self) -> bool:
        """Automatically invoke the mint process to get new OAuth tokens."""
        try:
            from ecolit.tesla.mint import mint_tesla_tokens

            # Run the existing mint process directly
            success = await mint_tesla_tokens()

            if success:
                # Reload config after successful minting
                await self._reload_config()
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Failed to run Tesla mint process: {e}")
            return False

    async def _reload_config(self):
        """Reload Tesla configuration after token refresh."""
        try:
            from pathlib import Path

            import yaml

            # Reload config file
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "config.yaml"

            with open(config_path) as f:
                config = yaml.safe_load(f)

            tesla_config = config.get("tesla", {})

            # Update our stored tokens
            self.refresh_token = tesla_config.get("refresh_token")
            self.client_id = tesla_config.get("client_id")
            self.client_secret = tesla_config.get("client_secret")

            logger.debug("Tesla configuration reloaded after token refresh")

        except Exception as e:
            logger.error(f"Failed to reload Tesla configuration: {e}")

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

            # Convert miles to kilometers (1 mile = 1.60934 km)
            MILES_TO_KM = 1.60934

            # Extract relevant data
            new_data = TeslaVehicleData(
                battery_level=charge_state.get("battery_level"),
                charging_power=charge_state.get("charger_power"),
                charge_amps=charge_state.get("charger_actual_current"),
                charging_state=charge_state.get("charging_state"),
                charge_port_status=charge_state.get("charge_port_door_open"),
                battery_range=(charge_state.get("battery_range") * MILES_TO_KM)
                if charge_state.get("battery_range")
                else None,
                ideal_battery_range=(charge_state.get("ideal_battery_range") * MILES_TO_KM)
                if charge_state.get("ideal_battery_range")
                else None,
                est_battery_range=(charge_state.get("est_battery_range") * MILES_TO_KM)
                if charge_state.get("est_battery_range")
                else None,
                timestamp=datetime.now(),
            )

            # Cache the data
            self.vehicle_data = new_data

            logger.debug(
                f"Tesla data: SOC={new_data.battery_level}%, "
                f"Power={new_data.charging_power}kW, "
                f"State={new_data.charging_state}, "
                f"Range={new_data.battery_range:.0f}km"
                if new_data.battery_range
                else "Tesla data: SOC={new_data.battery_level}%, Power={new_data.charging_power}kW, State={new_data.charging_state}"
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

            # Convert miles to kilometers (1 mile = 1.60934 km)
            MILES_TO_KM = 1.60934

            vehicle_data = TeslaVehicleData(
                battery_level=charge_state.get("battery_level"),
                charging_power=charge_state.get("charger_power"),
                charge_amps=charge_state.get("charge_current_request"),
                charging_state=charge_state.get("charging_state"),
                charge_port_status=charge_state.get("charge_port_door_open"),
                battery_range=(charge_state.get("battery_range") * MILES_TO_KM)
                if charge_state.get("battery_range")
                else None,
                ideal_battery_range=(charge_state.get("ideal_battery_range") * MILES_TO_KM)
                if charge_state.get("ideal_battery_range")
                else None,
                est_battery_range=(charge_state.get("est_battery_range") * MILES_TO_KM)
                if charge_state.get("est_battery_range")
                else None,
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
        """Set Tesla charging amperage using TVCP signed commands (required for modern vehicles)."""
        if not self.enabled or not self.api:
            return False

        # Clamp to safety limits
        amps = max(self.min_amps, min(amps, self.max_amps))

        # TVCP is mandatory - no fallback to unsigned commands
        if not self.vin:
            logger.error("VIN is required for TVCP signed commands")
            return False

        try:
            # Always use TVCP proxy for signed commands (modern vehicles require this)
            vehicle_api = self.api.vehicles.specificSigned(self.vin)
            result = await vehicle_api.set_charging_amps(charging_amps=amps)

            if result and result.get("response", {}).get("result"):
                logger.info(f"Tesla charging amps set to {amps}A")
                return True
            else:
                logger.error(f"Failed to set Tesla charging amps: {result}")
                return False

        except Exception as e:
            logger.error(f"TVCP signed command failed: {e}")
            logger.error(
                "Ensure tesla-http-proxy is running on port 4443 and vehicle keys are configured"
            )
            return False

    async def charge_start(self) -> bool:
        """Start Tesla charging using TVCP signed commands (required for modern vehicles)."""
        if not self.enabled or not self.api:
            return False

        # TVCP is mandatory - no fallback to unsigned commands
        if not self.vin:
            logger.error("VIN is required for TVCP signed commands")
            return False

        try:
            # Always use TVCP proxy for signed commands
            vehicle_api = self.api.vehicles.specificSigned(self.vin)
            result = await vehicle_api.charge_start()

            if result and result.get("response", {}).get("result"):
                logger.info("Tesla charging started")
                return True
            else:
                logger.error(f"Failed to start Tesla charging: {result}")
                return False

        except Exception as e:
            logger.error(f"TVCP signed command failed: {e}")
            logger.error(
                "Ensure tesla-http-proxy is running on port 4443 and vehicle keys are configured"
            )
            return False

    async def charge_stop(self) -> bool:
        """Stop Tesla charging using TVCP signed commands (required for modern vehicles)."""
        if not self.enabled or not self.api:
            return False

        # TVCP is mandatory - no fallback to unsigned commands
        if not self.vin:
            logger.error("VIN is required for TVCP signed commands")
            return False

        try:
            # Always use TVCP proxy for signed commands
            vehicle_api = self.api.vehicles.specificSigned(self.vin)
            result = await vehicle_api.charge_stop()

            if result and result.get("response", {}).get("result"):
                logger.info("Tesla charging stopped")
                return True
            else:
                logger.error(f"Failed to stop Tesla charging: {result}")
                return False

        except Exception as e:
            logger.error(f"TVCP signed command failed: {e}")
            logger.error(
                "Ensure tesla-http-proxy is running on port 4443 and vehicle keys are configured"
            )
            return False

    def is_enabled(self) -> bool:
        """Check if Tesla API client is enabled."""
        return self.enabled

    async def get_charging_history(self, limit: int = 10) -> dict[str, Any]:
        """Get recent charging history from Tesla Fleet API.

        Args:
            limit: Number of charging sessions to retrieve (default 10)

        Returns:
            Dict containing charging history data or empty dict if error
        """
        if not self.enabled or not self.api:
            return {}

        try:
            # Use the tesla-fleet-api library's charging history endpoint
            # This should correspond to GET /api/1/dx/charging/history
            result = await self.api.charging.history(limit=limit)

            if result and "data" in result:
                logger.info(f"Retrieved {len(result['data'])} charging history entries")
                return result
            else:
                logger.warning("No charging history data returned")
                return {}

        except Exception as e:
            logger.error(f"Failed to get Tesla charging history: {e}")
            return {}

    async def get_energy_sites(self) -> dict[str, Any]:
        """Get list of Tesla energy sites (Powerwall, Solar, Wall Connector).

        Returns:
            Dict containing energy sites data or empty dict if error
        """
        if not self.enabled or not self.api:
            return {}

        try:
            # Use the products endpoint to get all Tesla products including energy sites
            result = await self.api.products()

            if result and "response" in result:
                products = result["response"]
                # Filter for energy products (sites)
                energy_sites = [p for p in products if "energy_site_id" in p]
                logger.info(
                    f"Retrieved {len(energy_sites)} energy sites from {len(products)} total products"
                )
                return {"response": energy_sites}
            else:
                logger.warning("No products data returned")
                return {}

        except Exception as e:
            logger.error(f"Failed to get Tesla energy sites: {e}")
            return {}

    async def get_wall_connector_live_status(self, energy_site_id: int = None) -> dict[str, Any]:
        """Get live Wall Connector status from Tesla Fleet API.

        Args:
            energy_site_id: Energy site ID. If None, will try to find first available site.

        Returns:
            Dict containing Wall Connector live status or empty dict if error
        """
        if not self.enabled or not self.api:
            return {}

        # If no site ID provided, try to get the first energy site
        if energy_site_id is None:
            sites_data = await self.get_energy_sites()
            if not sites_data or "response" not in sites_data:
                logger.error("No energy sites found")
                return {}

            sites = sites_data["response"]
            if not sites:
                logger.error("No energy sites available")
                return {}

            energy_site_id = sites[0]["energy_site_id"]
            logger.info(f"Using energy site ID: {energy_site_id}")

        try:
            # Create an EnergySite instance with correct parameters
            site = self.api.energySites.Site(self.api, energy_site_id)
            result = await site.live_status()

            if result and "response" in result:
                live_data = result["response"]
                wall_connectors = live_data.get("wall_connectors", [])
                logger.info(f"Retrieved live status for {len(wall_connectors)} wall connectors")
                return result
            else:
                logger.warning("No live status data returned")
                return {}

        except Exception as e:
            logger.error(f"Failed to get Wall Connector live status: {e}")
            return {}

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
