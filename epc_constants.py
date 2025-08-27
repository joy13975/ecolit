#!/usr/bin/env python3
"""ECHONET Lite Property Code (EPC) constants for cleaner code."""

# Common EPC codes used across all device types
class CommonEPC:
    # Core device properties
    OPERATION_STATUS = 0x80  # Operation status
    INSTALLATION_LOCATION = 0x81  # Installation location
    STANDARD_VERSION = 0x82  # Standard version information
    ID_NUMBER = 0x83  # Identification number
    INSTANTANEOUS_POWER = 0x84  # Instantaneous power consumption
    CUMULATIVE_POWER = 0x85  # Cumulative power consumption
    FAULT_STATUS = 0x88  # Fault status
    MANUFACTURER_CODE = 0x8A  # Manufacturer code
    BUSINESS_FACILITY_CODE = 0x8B  # Business facility code
    PRODUCT_CODE = 0x8C  # Product code
    PRODUCTION_NUMBER = 0x8D  # Production number
    PRODUCTION_DATE = 0x8E  # Production date
    POWER_SAVING_MODE = 0x8F  # Power saving operation setting
    REMOTE_CONTROL = 0x93  # Remote control setting
    CURRENT_TIME = 0x97  # Current time setting
    CURRENT_DATE = 0x98  # Current date setting
    POWER_LIMIT = 0x99  # Power limit setting
    CUMULATIVE_RUNTIME = 0x9A  # Cumulative operating time
    
    # Property maps
    STATUS_CHANGE_PROPERTY_MAP = 0x9D  # Status change announcement property map
    SET_PROPERTY_MAP = 0x9E  # Set property map
    GET_PROPERTY_MAP = 0x9F  # Get property map


# Solar Power Generation specific EPC codes
class SolarEPC:
    # Solar power specific properties
    POWER_FACTOR = 0xC0  # Power factor setting
    INSTANTANEOUS_POWER_GENERATION = 0xE0  # Instantaneous power generation
    CUMULATIVE_POWER_GENERATION = 0xE1  # Cumulative power generation amount
    INSTANTANEOUS_CURRENT = 0xE2  # Instantaneous current generation amount
    CUMULATIVE_CURRENT = 0xE3  # Cumulative current generation amount
    INSTANTANEOUS_VOLTAGE = 0xE4  # Instantaneous voltage generation amount
    GRID_POWER_FLOW = 0xE5  # Real-time grid power flow (+ import, - export)
    
    # System interconnection properties 
    SYSTEM_INTERCONNECTED_TYPE = 0xD0  # System-interconnected type
    OUTPUT_POWER_RESTRAINT_STATUS = 0xD1  # Output power restraint status
    
    # Output power control
    OUTPUT_POWER_CONTROL_1 = 0xA0  # Output power control setting 1
    OUTPUT_POWER_CONTROL_2 = 0xA1  # Output power control setting 2


# Storage Battery specific EPC codes
class BatteryEPC:
    # Battery capacity and SOC
    REMAINING_CAPACITY = 0xBA  # Battery remaining stored electricity
    WORKING_OPERATION_STATUS = 0xC5  # Working operation status
    CHARGING_DISCHARGING_AMOUNT = 0xD3  # Charging/discharging electric energy
    OPERATION_MODE = 0xDA  # Operation mode setting
    REMAINING_STORED_ELECTRICITY = 0xE2  # Remaining stored electricity (technical)
    CHARGING_POWER = 0xE3  # Instantaneous charging power
    DISCHARGING_POWER = 0xE4  # Instantaneous discharging power
    REMAINING_CAPACITY_PERCENTAGE = 0xE5  # Remaining stored electricity percentage
    
    # Display SOC (preferred for user interface)
    USER_DISPLAY_SOC = 0xBF  # User display SOC
    DISPLAY_SOC_ALT = 0xC9   # Alternative display SOC
    
    # Charging/discharging settings
    CHARGING_METHOD = 0xC1  # Charging method
    DISCHARGING_METHOD = 0xC2  # Discharging method
    CHARGING_CAPACITY = 0xA0  # AC charging capacity
    DISCHARGING_CAPACITY = 0xA1  # AC discharging capacity
    CHARGING_CURRENT_CAPACITY = 0xC7  # AC charging current capacity
    DISCHARGING_CURRENT_CAPACITY = 0xC8  # AC discharging current capacity
    
    # Battery specifications
    BATTERY_TYPE = 0xDE  # Battery type
    RATED_CAPACITY = 0xD0  # Rated electricity storage capacity


# Smart Electric Energy Meter specific EPC codes
class MeterEPC:
    MEASURED_INSTANTANEOUS_POWER = 0xE7  # Measured instantaneous electric energy
    MEASURED_CUMULATIVE_CONSUMPTION = 0xE8  # Measured cumulative electric energy consumption (normal direction)
    MEASURED_CUMULATIVE_GENERATION = 0xEA  # Measured cumulative electric energy generation (reverse direction)


# EPC code to human-readable name mapping
EPC_NAMES = {
    # Common properties
    0x80: "Operation status",
    0x81: "Installation location", 
    0x82: "Standard version info",
    0x83: "ID number",
    0x84: "Instantaneous power",
    0x85: "Cumulative power",
    0x88: "Fault status",
    0x8A: "Manufacturer code",
    0x8B: "Business facility code",
    0x8C: "Product code",
    0x8D: "Production number",
    0x8E: "Production date",
    0x8F: "Power saving operation",
    0x93: "Remote control",
    0x97: "Current time",
    0x98: "Current date",
    0x99: "Power limit",
    0x9A: "Cumulative runtime",
    0x9D: "Status notification property map",
    0x9E: "Set property map",
    0x9F: "Get property map",
    
    # Solar specific
    0xC0: "Power factor",
    0xE0: "Instantaneous power generation",
    0xE1: "Cumulative power generation",
    0xE2: "Instantaneous current",
    0xE3: "Cumulative current",
    0xE4: "Instantaneous voltage",
    0xE5: "Grid power flow",
    0xD0: "System interconnected type",
    0xD1: "Output power restraint status",
    0xA0: "Output power control 1",
    0xA1: "Output power control 2",
    
    # Battery specific  
    0xBA: "Battery remaining capacity",
    0xC5: "Working operation status",
    0xD3: "Charging/discharging amount",
    0xDA: "Operation mode",
    0xBF: "User display SOC",
    0xC9: "Display SOC (alt)",
    0xC1: "Charging method",
    0xC2: "Discharging method",
    0xC7: "Charging current capacity",
    0xC8: "Discharging current capacity",
    0xDE: "Battery type",
    
    # Smart meter specific
    0xE7: "Measured instantaneous power",
    0xE8: "Measured cumulative power consumption (normal)",
    0xEA: "Measured cumulative power generation (reverse)",
}