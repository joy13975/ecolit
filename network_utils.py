#!/usr/bin/env python3
"""Network utilities for auto-detecting private LAN ranges."""

import ipaddress
import socket
import subprocess


def get_local_networks() -> list[str]:
    """
    Auto-detect private LAN IP ranges from active network interfaces.
    Returns list of network prefixes (e.g., ['192.168.1', '10.0.0']).
    """
    networks = []

    try:
        # Get all network interfaces and their IP addresses
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)

        # Add the detected local network
        if local_ip != "127.0.0.1":
            ip_obj = ipaddress.IPv4Address(local_ip)
            if ip_obj.is_private:
                # Extract first 3 octets
                network_prefix = ".".join(local_ip.split(".")[:-1])
                networks.append(network_prefix)
    except Exception:
        pass

    try:
        # Alternative method using subprocess to get route information
        if hasattr(socket, "AF_ROUTE"):  # macOS/BSD
            result = subprocess.run(
                ["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "interface:" in line.lower():
                        interface = line.split(":")[1].strip()
                        # Get IP of this interface
                        result2 = subprocess.run(
                            ["ifconfig", interface], capture_output=True, text=True, timeout=5
                        )
                        if result2.returncode == 0:
                            for ifline in result2.stdout.split("\n"):
                                if "inet " in ifline and "inet6" not in ifline:
                                    parts = ifline.strip().split()
                                    for i, part in enumerate(parts):
                                        if part == "inet" and i + 1 < len(parts):
                                            ip = parts[i + 1]
                                            try:
                                                ip_obj = ipaddress.IPv4Address(ip)
                                                if ip_obj.is_private:
                                                    network_prefix = ".".join(ip.split(".")[:-1])
                                                    if network_prefix not in networks:
                                                        networks.append(network_prefix)
                                            except ValueError:
                                                pass
                                            break
    except Exception:
        pass

    try:
        # Linux method using ip command
        result = subprocess.run(["ip", "route", "show"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "src " in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == "src" and i + 1 < len(parts):
                            ip = parts[i + 1]
                            try:
                                ip_obj = ipaddress.IPv4Address(ip)
                                if ip_obj.is_private:
                                    network_prefix = ".".join(ip.split(".")[:-1])
                                    if network_prefix not in networks:
                                        networks.append(network_prefix)
                            except ValueError:
                                pass
                            break
    except Exception:
        pass

    # Remove duplicates and sort by common private ranges
    networks = list(dict.fromkeys(networks))  # Preserve order, remove dupes

    # If no networks detected, fall back to common ranges
    if not networks:
        networks = ["192.168.1", "192.168.0", "10.0.0", "192.168.11"]

    return networks


def expand_to_full_scan_range() -> range:
    """Return full IP scan range (1-254) instead of limited range."""
    return range(1, 255)  # 1-254 inclusive
