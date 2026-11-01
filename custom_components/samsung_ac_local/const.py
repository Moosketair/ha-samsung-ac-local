"""
Constants for the Samsung AC Local integration.

Defines CoAP resource paths, enumerations for AC capabilities,
and protocol-level constants for the DTLS/CoAP communication.
"""

from enum import StrEnum
from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "samsung_ac_local"

# Network
DTLS_PORT = 49154

# Samsung global cloud server UUID (has full ACL access on any paired device)
SAMSUNG_CLOUD_UUID = "ab0b0ac4-aae9-4958-a04d-8ec36fe1b2f9"

# Samsung CoAP attribute prefix
SAMSUNG_PREFIX = "x.com.samsung.da."

# CoAP method codes (RFC 7252)
COAP_GET = 1
COAP_POST = 2

# CoAP content format identifier for CBOR
COAP_FORMAT_CBOR = 60

# CoAP option delta/length extension threshold (RFC 7252 section 3.1)
COAP_OPTION_EXT_THRESHOLD = 13
COAP_OPTION_EXT_TWO_BYTE = 14

# CoAP payload marker byte (separates options from payload)
COAP_PAYLOAD_MARKER = 0xFF


class Resource(StrEnum):
    """
    CoAP resource paths exposed by the Samsung AC.

    Each value is the URI path (without leading slash) used in
    CoAP GET/POST requests over the DTLS connection.
    """

    POWER = "power/vs/0"
    MODE = "mode/vs/0"
    TEMPERATURE_CURRENT = "temperature/current/0"
    TEMPERATURES = "temperatures/vs/0"
    TEMPERATURE_DESIRED = "temperature/desired/0"
    WIND_STRENGTH = "wind/strength/vs/0"
    WIND_DIRECTION = "wind/direction/vs/0"
    HUMIDITY = "humidity/vs/0"
    LIGHT = "light/vs/0"
    CONVENIENT = "mode/convenient/vs/0"
    AUTO_CLEAN = "option/autoclean/vs/0"
    AIR_PURIFY = "option/airpurify/vs/0"
    MUTE_ONCE = "option/muteonce/vs/0"
    FILTER = "filter/airdustfilter/vs/0"
    ENERGY = "energy/consumption/vs/0"
    ALARMS = "alarms/vs/0"
    DEVICE = "device/0"
    CONFIGURATION = "configuration/vs/0"
    AI_SLEEP = "aisleep/vs/0"
    SELFCHECK = "selfcheck/vs/0"
    OTN_INFO = "otninformation/vs/0"
    DRLC = "drlc/vs/0"
    WIRELESS_INFO = "wirelessinfo/vs/0"
    CONNECTION_CONFIG = "connectionconfig/vs/0"
    RM_WIFI = "rm/wifi/vs/0"
    REMOTE_DATA_CONTROL = "remotedatacontrol/vs/0"


class HvacMode(StrEnum):
    """
    HVAC operating modes supported by the AC.

    Values match the strings used in the Samsung CoAP protocol.
    """

    AUTO = "Auto"
    COOL = "Cool"
    DRY = "Dry"
    FAN = "Fan"
    HEAT = "Heat"


class FanMode(StrEnum):
    """
    Fan speed modes.

    The AC uses integer indices internally (0-4).
    These string labels come from the modesName array in responses.
    """

    AUTO = "Auto"
    LOW = "Low"
    MID = "Mid"
    HIGH = "High"
    TURBO = "Turbo"


FAN_MODE_TO_INDEX: dict[FanMode, int] = {
    FanMode.AUTO: 0,
    FanMode.LOW: 1,
    FanMode.MID: 2,
    FanMode.HIGH: 3,
    FanMode.TURBO: 4,
}

FAN_INDEX_TO_MODE: dict[int, FanMode] = {v: k for k, v in FAN_MODE_TO_INDEX.items()}


class SwingMode(StrEnum):
    """
    Swing (air direction) modes.

    Values match the Samsung CoAP protocol strings.
    """

    FIX = "Fix"
    VERTICAL = "Up_And_Low"
    HORIZONTAL = "Left_And_Right"
    ALL = "All"


class ConvenientMode(StrEnum):
    """
    Convenient (comfort/preset) modes.

    These are the modes available on mode/convenient/vs/0.
    Includes WindFree variants and other comfort presets.
    """

    OFF = "Off"
    SLEEP = "Sleep"
    QUIET = "Quiet"
    SMART = "Smart"
    SPEED = "Speed"
    WINDFREE = "Nano"
    WINDFREE_SLEEP = "NanoSleep"
    DRY_COMFORT = "DryComfort"


class Power(StrEnum):
    """Power state values."""

    ON = "On"
    OFF = "Off"


class LightMode(StrEnum):
    """Display panel light modes."""

    ON = "On"
    OFF = "Off"


class BeepVolume(StrEnum):
    """
    Beep volume options.

    These are sent as part of the mode options array.
    """

    ON = "Volume_100"
    OFF = "Volume_Mute"


class AirPurifyMode(StrEnum):
    """Air purify (ionizer) modes."""

    ON = "On"
    OFF = "Off"


class AutoCleanSetting(StrEnum):
    """Auto-clean on/off setting (whether it runs after AC turns off)."""

    ON = "On"
    OFF = "Off"


class AutoCleanAction(StrEnum):
    """Auto-clean immediate action (start/stop a cleaning cycle now)."""

    START = "Start"
    STOP = "Stop"


class FreezeWashAction(StrEnum):
    """Freeze wash immediate action (start/stop a cleaning cycle now)."""

    START = "On"
    STOP = "Off"


class OutdoorConnection(StrEnum):
    """Outdoor unit connection status."""

    CONNECTED = "Connected"
    DISCONNECTED = "Disconnected"
