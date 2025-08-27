"""Centralized manager for ECHONET Lite device state access.

This module abstracts away direct api_client._state access patterns and provides
clean, safe methods for accessing device information and properties.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DeviceStateManager:
    """Manages access to ECHONET Lite device state information."""

    def __init__(self, api_client: Any):
        """Initialize with ECHONET API client.

        Args:
            api_client: ECHONET API client with _state attribute
        """
        self.api_client = api_client

    def is_device_discovered(self, ip: str) -> bool:
        """Check if a device at the given IP has been discovered.

        Args:
            ip: Device IP address

        Returns:
            True if device is discovered and has instances
        """
        if not hasattr(self.api_client, "_state") or not self.api_client._state:
            return False

        return ip in self.api_client._state and "instances" in self.api_client._state[ip]

    def get_device_instance(
        self, ip: str, eojgc: int, eojcc: int, instance: int
    ) -> dict[str, Any] | None:
        """Get device instance state data.

        Args:
            ip: Device IP address
            eojgc: ECHONET group code
            eojcc: ECHONET class code
            instance: Device instance number

        Returns:
            Device instance state dictionary or None if not found
        """
        if not self.is_device_discovered(ip):
            logger.debug(f"Device not discovered: {ip}")
            return None

        try:
            instances = self.api_client._state[ip]["instances"]
            if eojgc in instances and eojcc in instances[eojgc]:
                if instance in instances[eojgc][eojcc]:
                    return instances[eojgc][eojcc][instance]
        except (KeyError, TypeError) as e:
            logger.debug(
                f"Failed to access device instance {ip} 0x{eojgc:02X}{eojcc:02X}:{instance}: {e}"
            )

        return None

    def get_available_properties(self, ip: str, eojgc: int, eojcc: int, instance: int) -> list[int]:
        """Get list of available EPC property codes for a device.

        Args:
            ip: Device IP address
            eojgc: ECHONET group code
            eojcc: ECHONET class code
            instance: Device instance number

        Returns:
            List of integer EPC codes that are available
        """
        inst_state = self.get_device_instance(ip, eojgc, eojcc, instance)
        if not inst_state:
            return []

        # Filter for integer keys which represent EPC codes
        return [prop for prop in inst_state.keys() if isinstance(prop, int)]

    def has_property(self, ip: str, eojgc: int, eojcc: int, instance: int, epc: int) -> bool:
        """Check if a device instance has a specific EPC property.

        Args:
            ip: Device IP address
            eojgc: ECHONET group code
            eojcc: ECHONET class code
            instance: Device instance number
            epc: EPC property code to check

        Returns:
            True if the property exists
        """
        inst_state = self.get_device_instance(ip, eojgc, eojcc, instance)
        return inst_state is not None and epc in inst_state

    def device_exists(self, ip: str, eojgc: int, eojcc: int, instance: int) -> bool:
        """Check if a specific device instance exists.

        Args:
            ip: Device IP address
            eojgc: ECHONET group code
            eojcc: ECHONET class code
            instance: Device instance number

        Returns:
            True if the device instance exists
        """
        return self.get_device_instance(ip, eojgc, eojcc, instance) is not None

    def get_discovery_state(self, ip: str) -> dict[str, Any] | None:
        """Get complete discovery state for an IP address.

        Args:
            ip: Device IP address

        Returns:
            Complete state dictionary for the IP or None
        """
        if not hasattr(self.api_client, "_state") or not self.api_client._state:
            return None

        return self.api_client._state.get(ip)
