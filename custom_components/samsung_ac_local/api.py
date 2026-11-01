"""
Samsung AC local DTLS/CoAP client.

Provides a blocking, thread-safe DTLS client for Samsung OCF air conditioners.
Uses pyOpenSSL for DTLS transport and CBOR for CoAP payload encoding.

The integration calls all public methods via ``hass.async_add_executor_job``
since the underlying UDP/DTLS I/O is synchronous.
"""

import contextlib
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import cbor2
from OpenSSL import SSL, crypto

from .const import (
    COAP_FORMAT_CBOR,
    COAP_GET,
    COAP_OPTION_EXT_THRESHOLD,
    COAP_OPTION_EXT_TWO_BYTE,
    COAP_PAYLOAD_MARKER,
    COAP_POST,
    DTLS_PORT,
    FAN_INDEX_TO_MODE,
    FAN_MODE_TO_INDEX,
    LOGGER,
    SAMSUNG_PREFIX,
    AirPurifyMode,
    AutoCleanAction,
    AutoCleanSetting,
    BeepVolume,
    ConvenientMode,
    FanMode,
    FreezeWashAction,
    HvacMode,
    LightMode,
    Power,
    Resource,
    SwingMode,
)


class SamsungACConnectionError(Exception):
    """Raised when the DTLS connection cannot be established."""


class SamsungACRequestError(Exception):
    """Raised when a CoAP request fails or receives no response."""


@dataclass
class ACStatus:
    """
    Complete AC state snapshot.

    Populated by ``SamsungACClient.get_status`` from multiple CoAP resource
    queries. Fields are None when the corresponding resource could not be read.
    """

    power: Power | None = None
    hvac_mode: HvacMode | None = None
    current_temperature: float | None = None
    target_temperature: float | None = None
    fan_mode: FanMode | None = None
    swing_mode: SwingMode | None = None
    humidity: int | None = None
    light: LightMode | None = None
    convenient_mode: ConvenientMode | None = None
    beep: BeepVolume | None = None
    air_purify: AirPurifyMode | None = None
    auto_clean_setting: AutoCleanSetting | None = None
    auto_clean_active: bool | None = None
    auto_clean_progress: int | None = None
    freeze_wash_active: bool | None = None
    freeze_wash_progress: int | None = None
    filter_usage_hours: int | None = None
    filter_capacity_hours: int | None = None
    filter_status: str | None = None
    energy_wh: int | None = None
    supported_modes: list[HvacMode] = field(default_factory=list)
    supported_swing: list[SwingMode] = field(default_factory=list)
    supported_convenient: list[ConvenientMode] = field(default_factory=list)

    # Extracted from mode options array
    outdoor_temperature: float | None = None
    outdoor_connected: bool | None = None
    duration_on_minutes: int | None = None
    sleep_timer_minutes: int | None = None
    ai_temperature: float | None = None
    operation_count: int | None = None
    temperature_min: float | None = None
    temperature_max: float | None = None
    temperature_step: float | None = None

    # Diagnostics
    rssi: int | None = None

    # Alarms
    alarms: list[dict] = field(default_factory=list)

    # AI sleep
    ai_sleep_active: bool | None = None
    ai_sleep_elapsed_minutes: int | None = None

    # Mute once (single-beep suppression)
    mute_once: bool | None = None

    # Cloud connection status (should be False if firewall is blocking)
    cloud_connected: bool | None = None


@dataclass
class DeviceInfo:
    """
    Static device information fetched once at integration setup.

    Does not change during runtime - firmware version, model, capabilities.
    """

    firmware_version: str | None = None
    firmware_update_available: bool | None = None
    model_id: str | None = None
    os_version: str | None = None
    one_ui_version: str | None = None
    mac_wifi: str | None = None
    mac_ble: str | None = None
    capabilities: list[str] = field(default_factory=list)


class SamsungACClient:
    """
    DTLS/CoAP client for Samsung OCF air conditioners.

    Maintains a persistent DTLS connection for fast command execution
    (approximately 50ms per request after the initial 4-5s handshake).
    Automatically reconnects on connection loss.

    All public methods are blocking and thread-safe.
    """

    # DTLS handshake timeout (AC takes ~4-5s)
    _timeout: float = 10.0
    # Per-response wait time for CoAP replies (~50-100ms typical)
    _recv_timeout: float = 3.0
    # Receive attempts per request before declaring failure
    _max_retries: int = 2

    def __init__(
        self,
        host: str,
        cert_pem: str,
        key_pem: str,
        port: int = DTLS_PORT,
    ) -> None:
        """
        Initialize the client.

        Args:
            host: AC IP address on the local network.
            cert_pem: Client certificate in PEM format.
            key_pem: Client EC private key in PEM format (no passphrase).
            port: AC DTLS port (default 49154).

        """
        self._host = host
        self._port = port
        self._cert_pem = cert_pem
        self._key_pem = key_pem

        self._conn: SSL.Connection | None = None
        self._sock: socket.socket | None = None
        self._mid: int = 0
        self._lock = threading.Lock()
        # Some Samsung firmwares reject the standard OCF desired-temperature
        # endpoint (4.04) but accept the vendor temperatures collection.
        self._temperature_write_resource = Resource.TEMPERATURE_DESIRED

    @property
    def host(self) -> str:
        """Return the AC host address."""
        return self._host

    @property
    def connected(self) -> bool:
        """Return True if the DTLS connection is active."""
        return self._conn is not None

    def connect(self) -> None:
        """
        Establish the DTLS connection.

        Raises:
            SamsungACConnectionError: If the handshake fails or times out.

        """
        with self._lock:
            self._connect_locked()

    def disconnect(self) -> None:
        """Close the DTLS connection gracefully."""
        with self._lock:
            self._disconnect_locked()

    def get_status(self) -> ACStatus:
        """
        Fetch the complete AC state.

        Queries all relevant CoAP resources and returns a populated ACStatus.
        Individual fields will be None if the corresponding request failed.

        Returns:
            ACStatus dataclass with current AC state.

        """
        status = ACStatus()
        self._poll_power(status)
        self._poll_mode(status)
        self._poll_temperatures(status)
        self._poll_fan(status)
        self._poll_swing(status)
        self._poll_humidity(status)
        self._poll_convenient(status)
        self._poll_light(status)
        self._poll_air_purify(status)
        self._poll_auto_clean(status)
        self._poll_filter(status)
        self._poll_energy(status)
        self._poll_rssi(status)
        self._poll_alarms(status)
        self._poll_ai_sleep(status)
        self._poll_mute_once(status)
        self._poll_cloud_connected(status)

        return status

    def get_device_info(self) -> DeviceInfo:
        """
        Fetch static device information.

        Should be called once during integration setup. Returns firmware
        version, model info, and device capabilities.

        Returns:
            DeviceInfo dataclass with device metadata.

        """
        info = DeviceInfo()

        data = self._get(Resource.OTN_INFO)
        if data:
            sw_info = data.get("swVersionInfo")
            if isinstance(sw_info, dict):
                info.os_version = sw_info.get("osVersion")
                info.one_ui_version = sw_info.get("oneUiVersion")

            otn_list = data.get("otnList")
            if isinstance(otn_list, list) and otn_list:
                first = otn_list[0]
                if isinstance(first, dict):
                    info.model_id = first.get("modelId")
                    versions = first.get("versions")
                    if isinstance(versions, list) and versions:
                        info.firmware_version = versions[0]

            update = data.get("x.com.samsung.da.newVersionAvailable")
            if update is not None:
                info.firmware_update_available = update is True or update == "true"

        data = self._get(Resource.CONFIGURATION)
        if data:
            caps = _samsung_val(data, "airconOptionList")
            if isinstance(caps, list):
                info.capabilities = caps

        data = self._get(Resource.WIRELESS_INFO)
        if data:
            info.mac_wifi = data.get("macaddressWiFi")
            info.mac_ble = data.get("macaddressBLE")

        return info

    # --- Setters ---
    def set_power(self, power: Power) -> bool:
        """
        Turn the AC on or off.

        Args:
            power: Desired power state.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(Resource.POWER, {f"{SAMSUNG_PREFIX}power": power.value})

    def set_hvac_mode(self, mode: HvacMode) -> bool:
        """
        Set the HVAC operating mode.

        Args:
            mode: One of Auto, Cool, Dry, Fan, Heat.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(Resource.MODE, {f"{SAMSUNG_PREFIX}modes": [mode.value]})

    def set_target_temperature(self, temp: float) -> bool:
        """
        Set the target temperature.

        Args:
            temp: Temperature in Celsius (16.0-30.0).

        Returns:
            True if the AC accepted the command.

        """
        vendor_payload = {
            f"{SAMSUNG_PREFIX}items": [
                {
                    f"{SAMSUNG_PREFIX}id": "0",
                    f"{SAMSUNG_PREFIX}desired": f"{temp:g}",
                }
            ]
        }

        if self._temperature_write_resource == Resource.TEMPERATURES:
            return self._post(Resource.TEMPERATURES, vendor_payload)

        success, code = self._post_with_code(
            Resource.TEMPERATURE_DESIRED, {"temperature": temp}
        )
        if success or code != "4.04":
            return success

        # A 4.04 means the OCF resource is not available on this firmware.
        # Do not retry other failures: an ambiguous POST may have succeeded.
        LOGGER.info(
            "Desired-temperature resource unavailable (4.04); "
            "trying Samsung vendor temperatures resource"
        )
        success = self._post(Resource.TEMPERATURES, vendor_payload)
        if success:
            self._temperature_write_resource = Resource.TEMPERATURES
            LOGGER.info("Using Samsung vendor temperature control for %s", self._host)
        return success

    def set_fan_mode(self, mode: FanMode) -> bool:
        """
        Set the fan speed.

        Args:
            mode: Desired fan speed.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(
            Resource.WIND_STRENGTH,
            {f"{SAMSUNG_PREFIX}modes": str(FAN_MODE_TO_INDEX[mode])},
        )

    def set_swing_mode(self, mode: SwingMode) -> bool:
        """
        Set the air swing direction.

        Args:
            mode: Desired swing mode.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(
            Resource.WIND_DIRECTION,
            {f"{SAMSUNG_PREFIX}modes": mode.value},
        )

    def set_convenient_mode(self, mode: ConvenientMode) -> bool:
        """
        Set the convenient (comfort/preset) mode.

        Includes WindFree, Sleep, Quiet, Smart, Speed, DryComfort.

        Args:
            mode: Desired convenient mode.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(Resource.CONVENIENT, {f"{SAMSUNG_PREFIX}modes": mode.value})

    def set_beep(self, volume: BeepVolume) -> bool:
        """
        Set the beep volume.

        Args:
            volume: Desired beep setting.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(Resource.MODE, {f"{SAMSUNG_PREFIX}options": [volume.value]})

    def set_light(self, mode: LightMode) -> bool:
        """
        Set the display panel light.

        Args:
            mode: On or Off.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(Resource.LIGHT, {"mode": mode.value})

    def set_auto_clean(self, setting: AutoCleanSetting) -> bool:
        """
        Enable or disable auto-clean.

        Args:
            setting: Desired auto-clean setting.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(
            Resource.AUTO_CLEAN, {f"{SAMSUNG_PREFIX}settingStatus": setting.value}
        )

    def set_auto_clean_action(self, action: AutoCleanAction) -> bool:
        """
        Start or stop an auto-clean cycle immediately.

        Args:
            action: Start or Stop.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(
            Resource.AUTO_CLEAN, {f"{SAMSUNG_PREFIX}status": action.value}
        )

    def set_freeze_wash_action(self, action: FreezeWashAction) -> bool:
        """
        Start or stop a freeze wash cycle immediately.

        Args:
            action: Start or Stop.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(
            Resource.MODE,
            {f"{SAMSUNG_PREFIX}options": [f"SmartCoolClean_{action.value}"]},
        )

    def set_air_purify(self, mode: AirPurifyMode) -> bool:
        """
        Set the air purify (ionizer) mode.

        Args:
            mode: On or Off.

        Returns:
            True if the AC accepted the command.

        """
        return self._post(Resource.AIR_PURIFY, {f"{SAMSUNG_PREFIX}modes": mode.value})

    # --- Raw CoAP access ---
    def coap_get(self, path: str) -> tuple[str | None, dict | None]:
        """
        Send a raw CoAP GET request.

        Args:
            path: Resource path without leading slash.

        Returns:
            Tuple of (response code like "2.05", decoded CBOR payload or None).

        """
        with self._lock:
            return self._request_locked(COAP_GET, path)

    def coap_post(self, path: str, payload: dict) -> tuple[str | None, dict | None]:
        """
        Send a raw CoAP POST request.

        Args:
            path: Resource path without leading slash.
            payload: Dictionary to CBOR-encode as the request body.

        Returns:
            Tuple of (response code like "2.04", decoded CBOR payload or None).

        """
        with self._lock:
            return self._request_locked(COAP_POST, path, payload)

    # --- Connection management (private, caller must hold lock) ---
    def _connect_locked(self) -> None:
        """Establish the DTLS connection."""
        self._disconnect_locked()

        ctx = SSL.Context(SSL.DTLS_CLIENT_METHOD)
        ctx.use_certificate(
            crypto.load_certificate(crypto.FILETYPE_PEM, self._cert_pem.encode())
        )
        ctx.use_privatekey(
            crypto.load_privatekey(crypto.FILETYPE_PEM, self._key_pem.encode())
        )
        ctx.set_verify(SSL.VERIFY_NONE, _noop_verify)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self._recv_timeout)
        sock.connect((self._host, self._port))

        conn = SSL.Connection(ctx, sock)
        conn.set_connect_state()

        t_start = time.monotonic()
        handshake_done = self._perform_handshake(conn, sock)
        if not handshake_done:
            sock.close()
            msg = f"DTLS handshake to {self._host}:{self._port} timed out"
            raise SamsungACConnectionError(msg)

        self._conn = conn
        self._sock = sock
        elapsed = time.monotonic() - t_start
        LOGGER.debug(
            "DTLS connected to %s:%d in %.1fs", self._host, self._port, elapsed
        )

    def _perform_handshake(self, conn: SSL.Connection, sock: socket.socket) -> bool:
        """
        Run the DTLS handshake with timeout and retry logic.

        Returns True on success, False on timeout.
        Raises SamsungACConnectionError on protocol errors.
        """
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                conn.do_handshake()
            except SSL.WantReadError:
                select.select([sock], [], [], self._recv_timeout)
                continue
            except TimeoutError:
                continue
            except SSL.Error as err:
                msg = f"DTLS handshake to {self._host}:{self._port} failed: {err}"
                raise SamsungACConnectionError(msg) from err
            else:
                return True

        return False

    def _disconnect_locked(self) -> None:
        """Close the DTLS connection and release the socket."""
        if self._conn is not None:
            with contextlib.suppress(SSL.Error, OSError):
                self._conn.shutdown()

            self._conn = None

        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()

            self._sock = None
            LOGGER.debug("DTLS disconnected from %s", self._host)

    def _ensure_connected(self) -> None:
        """Connect if no active connection exists."""
        if self._conn is None:
            self._connect_locked()

    @property
    def _active_conn(self) -> SSL.Connection:
        """Return the connection, raising if not connected."""
        conn = self._conn
        if conn is None:
            msg = "Not connected"
            raise SamsungACConnectionError(msg)

        return conn

    @property
    def _active_sock(self) -> socket.socket:
        """Return the socket, raising if not connected."""
        sock = self._sock
        if sock is None:
            msg = "Not connected"
            raise SamsungACConnectionError(msg)

        return sock

    # --- CoAP protocol (private) ---
    def _build_message(
        self,
        method: int,
        path: str,
        payload: dict | None = None,
    ) -> bytes:
        """Build a CoAP confirmable message with URI-Path options."""
        self._mid = (self._mid + 1) & 0xFFFF
        token = bytes([self._mid & 0xFF])
        # Ver=1, Type=CON, TKL=1
        msg = bytes([0x41, method]) + struct.pack("!H", self._mid) + token

        prev_option = 0
        for segment in path.split("/"):
            delta = 11 - prev_option
            prev_option = 11
            seg_len = len(segment)
            if seg_len < COAP_OPTION_EXT_THRESHOLD:
                msg += bytes([(delta << 4) | seg_len])
            else:
                msg += bytes([(delta << 4) | 13, seg_len - 13])

            msg += segment.encode()

        if payload is not None:
            delta = 12 - prev_option
            msg += bytes([(delta << 4) | 1, COAP_FORMAT_CBOR])
            msg += b"\xff" + cbor2.dumps(payload)

        return msg

    def _parse_response(self, data: bytes) -> tuple[str, dict | None]:
        """Extract the response code and optional CBOR payload."""
        code = f"{(data[1] >> 5) & 7}.{data[1] & 0x1F:02d}"
        payload = None
        payload_start = self._find_payload_offset(data)
        if payload_start is not None:
            raw = data[payload_start:]
            with contextlib.suppress(cbor2.CBORDecodeError, ValueError):
                payload = cbor2.loads(raw)

            if payload is not None and not isinstance(payload, dict):
                LOGGER.debug(
                    "Non-dict CBOR response (type=%s, raw=%s)",
                    type(payload).__name__,
                    raw[:64].hex(),
                )

        return code, payload

    @staticmethod
    def _find_payload_offset(data: bytes) -> int | None:
        """
        Walk CoAP options to find the payload start position.

        Returns the byte offset of the payload (after the 0xFF marker),
        or None if there is no payload.
        """
        # Header (4 bytes) + token (TKL from header byte 0)
        tkl = data[0] & 0x0F
        pos = 4 + tkl
        while pos < len(data):
            # 0xFF marks the payload boundary
            if data[pos] == COAP_PAYLOAD_MARKER:
                return pos + 1

            # Parse option delta and length from first byte
            delta = (data[pos] >> 4) & 0x0F
            length = data[pos] & 0x0F
            pos += 1

            # Extended delta
            if delta == COAP_OPTION_EXT_THRESHOLD:
                delta = data[pos] + COAP_OPTION_EXT_THRESHOLD
                pos += 1
            elif delta == COAP_OPTION_EXT_TWO_BYTE:
                delta = int.from_bytes(data[pos : pos + 2]) + 269
                pos += 2

            # Extended length
            if length == COAP_OPTION_EXT_THRESHOLD:
                length = data[pos] + COAP_OPTION_EXT_THRESHOLD
                pos += 1
            elif length == COAP_OPTION_EXT_TWO_BYTE:
                length = int.from_bytes(data[pos : pos + 2]) + 269
                pos += 2

            # Skip option value
            pos += length

        return None

    def _request_locked(
        self,
        method: int,
        path: str,
        payload: dict | None = None,
    ) -> tuple[str | None, dict | None]:
        """Send a CoAP request, recovering from an unresponsive DTLS session.

        A GET may be retried once on a fresh DTLS connection. Never
        automatically repeat a POST: a lost reply does not prove the command
        failed, and repeating a command can change the device state twice.
        """
        attempts = 2 if method == COAP_GET else 1
        for attempt in range(attempts):
            try:
                self._ensure_connected()
                msg = self._build_message(method, path, payload)
                self._active_conn.send(msg)
                return self._receive_response()
            except (SamsungACRequestError, SamsungACConnectionError) as err:
                LOGGER.debug("CoAP request to %s failed: %s", path, err)
                self._disconnect_locked()
                if attempt + 1 >= attempts:
                    raise
            except (SSL.Error, OSError, TimeoutError) as err:
                LOGGER.debug("CoAP transport error on %s: %s", path, err)
                self._disconnect_locked()
                if attempt + 1 >= attempts:
                    msg = f"CoAP request to {path} failed: {err}"
                    raise SamsungACRequestError(msg) from err

            LOGGER.debug("Retrying GET %s on a fresh DTLS session", path)

        msg = f"CoAP request to {path} exhausted retries"
        raise SamsungACRequestError(msg)

    def _receive_response(self) -> tuple[str, dict | None]:
        """Wait for the matching CoAP token, ignoring late unrelated replies.

        One monotonic deadline bounds the *total* wait time. Receiving stale
        packets no longer exhausts the response budget after two packets.
        """
        expected_token = self._mid & 0xFF
        deadline = time.monotonic() + self._recv_timeout
        discarded = 0

        while (remaining := deadline - time.monotonic()) > 0:
            ready, _, _ = select.select([self._active_sock], [], [], remaining)
            if not ready:
                break

            try:
                data = self._active_conn.recv(4096)
            except SSL.WantReadError:
                continue

            # CoAP v1 responses must contain the one-byte request token.
            if len(data) < 5 or (data[0] >> 6) != 1 or (data[0] & 0x0F) != 1:
                discarded += 1
                LOGGER.debug("Discarding short or invalid CoAP response")
                continue

            if data[4] != expected_token:
                discarded += 1
                LOGGER.debug(
                    "Discarding mismatched response (expected token %02x, got %02x)",
                    expected_token,
                    data[4],
                )
                continue

            return self._parse_response(data)

        msg = (
            f"No response after {self._recv_timeout:.1f}s "
            f"(discarded {discarded} stale/unrelated CoAP packets)"
        )
        raise SamsungACRequestError(msg)

    # --- Convenience wrappers (private) ---
    def _get(self, resource: Resource) -> dict | None:
        """GET a resource, returning the parsed payload or None."""
        with self._lock:
            try:
                code, data = self._request_locked(COAP_GET, resource.value)
            except (SamsungACConnectionError, SamsungACRequestError) as err:
                LOGGER.warning("GET %s failed: %s", resource.value, err)
                return None

        if code and code.startswith("2."):
            if isinstance(data, dict):
                return data

            LOGGER.debug(
                "GET %s returned non-dict payload: %s", resource.value, type(data)
            )
            return None

        LOGGER.debug("GET %s returned %s", resource.value, code)
        return None

    def _post(self, resource: Resource, payload: dict) -> bool:
        """POST to a resource, returning True if the AC accepted the command."""
        success, _code = self._post_with_code(resource, payload)
        return success

    def _post_with_code(
        self, resource: Resource, payload: dict
    ) -> tuple[bool, str | None]:
        """POST once and return success and response code for safe fallbacks.

        A missing response is never treated as permission to retry a POST.
        """
        with self._lock:
            try:
                code, data = self._request_locked(COAP_POST, resource.value, payload)
            except (SamsungACConnectionError, SamsungACRequestError) as err:
                LOGGER.error("POST %s failed: %s", resource.value, err)
                return False, None

        if code != "2.04":
            LOGGER.warning("POST %s returned code %s", resource.value, code)
            return False, code

        response = data.get("controlResponse") if isinstance(data, dict) else None
        if not response:
            LOGGER.warning("POST %s: no controlResponse in reply", resource.value)
            return False, code

        return (
            response.get("result") is True or response.get("errorCode") == "unchanged",
            code,
        )

    # --- Status polling helpers (private) ---
    def _poll_power(self, status: ACStatus) -> None:
        data = self._get(Resource.POWER)
        if data:
            status.power = _parse_enum(Power, _samsung_val(data, "power"))
            status.operation_count = _to_int(data.get("operationNumber"))

    def _poll_mode(self, status: ACStatus) -> None:
        data = self._get(Resource.MODE)
        if not data:
            return

        status.hvac_mode = _parse_enum(HvacMode, data.get("workingMode"))
        status.beep = _parse_beep(data)
        status.supported_modes = _parse_enum_list(
            HvacMode, _samsung_val(data, "supportedModes")
        )

        # Parse options array for embedded values
        options = _samsung_val(data, "options")
        if isinstance(options, list):
            _parse_mode_options(status, options)

    def _poll_temperatures(self, status: ACStatus) -> None:
        data = self._get(Resource.TEMPERATURE_CURRENT)
        if data:
            status.current_temperature = _to_float(data.get("temperature"))
            temp_range = data.get("range")
            if isinstance(temp_range, list):
                range_min, range_max, *_ = *temp_range, None, None
                status.temperature_min = _to_float(range_min)
                status.temperature_max = _to_float(range_max)

        data = self._get(Resource.TEMPERATURES)
        if data:
            items = _samsung_val(data, "items")
            if isinstance(items, list) and items:
                item = items[0]
                status.target_temperature = _to_float(_samsung_val(item, "desired"))

                if status.temperature_min is None:
                    status.temperature_min = _to_float(_samsung_val(item, "minimum"))

                if status.temperature_max is None:
                    status.temperature_max = _to_float(_samsung_val(item, "maximum"))

                status.temperature_step = _to_float(_samsung_val(item, "increment"))

    def _poll_fan(self, status: ACStatus) -> None:
        data = self._get(Resource.WIND_STRENGTH)
        if not data:
            return

        idx = _to_int(_samsung_val(data, "modes"))
        if idx is not None:
            status.fan_mode = FAN_INDEX_TO_MODE.get(idx)

    def _poll_swing(self, status: ACStatus) -> None:
        data = self._get(Resource.WIND_DIRECTION)
        if not data:
            return

        status.swing_mode = _parse_enum(SwingMode, _samsung_val(data, "modes"))
        status.supported_swing = _parse_enum_list(
            SwingMode, _samsung_val(data, "supportedModes")
        )

    def _poll_humidity(self, status: ACStatus) -> None:
        data = self._get(Resource.HUMIDITY)
        if data:
            val = _samsung_val(data, "fivepercentHumidity")
            if val is not None:
                status.humidity = int(val)

    def _poll_light(self, status: ACStatus) -> None:
        data = self._get(Resource.LIGHT)
        if data:
            status.light = _parse_enum(LightMode, data.get("mode"))

    def _poll_convenient(self, status: ACStatus) -> None:
        data = self._get(Resource.CONVENIENT)
        if data:
            status.convenient_mode = _parse_enum(
                ConvenientMode, _samsung_val(data, "modes")
            )
            status.supported_convenient = _parse_enum_list(
                ConvenientMode, _samsung_val(data, "supportedModes")
            )

    def _poll_auto_clean(self, status: ACStatus) -> None:
        data = self._get(Resource.AUTO_CLEAN)
        if not data:
            return

        status.auto_clean_setting = _parse_enum(
            AutoCleanSetting, _samsung_val(data, "settingStatus")
        )
        action = _samsung_val(data, "status")
        status.auto_clean_active = action == AutoCleanAction.START
        status.auto_clean_progress = _to_int(_samsung_val(data, "progress"))

    def _poll_filter(self, status: ACStatus) -> None:
        data = self._get(Resource.FILTER)
        if data:
            status.filter_usage_hours = _to_int(_samsung_val(data, "filterUsage"))
            status.filter_capacity_hours = _to_int(_samsung_val(data, "filterCapacity"))
            status.filter_status = _samsung_val(data, "filterStatus")

    def _poll_energy(self, status: ACStatus) -> None:
        data = self._get(Resource.ENERGY)
        if data:
            status.energy_wh = _to_int(_samsung_val(data, "cumulativePower"))

    def _poll_air_purify(self, status: ACStatus) -> None:
        data = self._get(Resource.AIR_PURIFY)
        if data:
            status.air_purify = _parse_enum(AirPurifyMode, _samsung_val(data, "modes"))

    def _poll_rssi(self, status: ACStatus) -> None:
        data = self._get(Resource.RM_WIFI)
        if not data:
            return

        rssi_list = data.get("x.com.samsung.rm.rssi")
        if isinstance(rssi_list, list) and rssi_list:
            status.rssi = _to_int(rssi_list[0])

    def _poll_alarms(self, status: ACStatus) -> None:
        data = self._get(Resource.ALARMS)
        if not data:
            return

        items = _samsung_val(data, "items")
        if isinstance(items, list):
            status.alarms = [
                {
                    "code": _samsung_val(item, "code"),
                    "state": _samsung_val(item, "state"),
                    "time": _samsung_val(item, "triggeredTime"),
                }
                for item in items
                if isinstance(item, dict)
            ]

    def _poll_ai_sleep(self, status: ACStatus) -> None:
        data = self._get(Resource.AI_SLEEP)
        if not data:
            return

        elapsed = _to_int(_samsung_val(data, "elapsedTime"))
        status.ai_sleep_elapsed_minutes = elapsed
        status.ai_sleep_active = elapsed is not None and elapsed > 0

    def _poll_mute_once(self, status: ACStatus) -> None:
        data = self._get(Resource.MUTE_ONCE)
        if not data:
            return

        status.mute_once = data.get("muteonce") == "On"

    def _poll_cloud_connected(self, status: ACStatus) -> None:
        data = self._get(Resource.REMOTE_DATA_CONTROL)
        if not data:
            return

        conn_status = _samsung_val(data, "connectionStatus")
        status.cloud_connected = conn_status == "Connected"


# --- Module-level helpers ---
def _noop_verify(
    _conn: SSL.Connection,
    _cert: crypto.X509,
    _errno: int,
    _depth: int,
    _ok: int,
) -> bool:
    """Accept any server certificate (AC uses self-signed Samsung chain)."""
    return True


def _samsung_val(data: dict, key: str) -> Any:
    """Look up a value by bare key or Samsung-prefixed key."""
    return data.get(key, data.get(f"{SAMSUNG_PREFIX}{key}"))


def _to_int(value: Any) -> int | None:
    """Convert a value to int, handling strings. Returns None on failure."""
    if value is None:
        return None

    with contextlib.suppress(ValueError, TypeError):
        return int(value)

    return None


def _to_float(value: Any) -> float | None:
    """Convert a value to float, handling strings. Returns None on failure."""
    if value is None:
        return None

    with contextlib.suppress(ValueError, TypeError):
        return float(value)

    return None


def _parse_enum[T: StrEnum](enum_class: type[T], value: Any) -> T | None:
    """Safely convert a value to an enum member, returning None on mismatch."""
    if value is None:
        return None

    with contextlib.suppress(ValueError):
        return enum_class(value)

    return None


def _parse_enum_list[T: StrEnum](enum_class: type[T], values: Any) -> list[T]:
    """Convert a list of values to enum members, skipping invalid entries."""
    if not isinstance(values, list):
        return []

    result: list[T] = []
    for v in values:
        with contextlib.suppress(ValueError):
            result.append(enum_class(v))

    return result


def _parse_mode_options(status: ACStatus, options: list) -> None:
    """Extract structured values from the mode options string array."""
    for opt in options:
        if not isinstance(opt, str):
            continue

        key, sep, value = opt.rpartition("_")
        if not sep:
            continue

        if key == "OutdoorTemp":
            # Samsung reports outdoor temp in Fahrenheit in the options array
            raw = _to_float(value)
            if raw is not None:
                status.outdoor_temperature = round((raw - 32) * 5 / 9, 1)
        elif key == "OutdoorConnection":
            status.outdoor_connected = value == "Connected"
        elif key == "DurationOn":
            status.duration_on_minutes = _to_int(value)
        elif key == "Sleep":
            status.sleep_timer_minutes = _to_int(value)
        elif key == "AiTemp":
            raw = _to_float(value)
            if raw is not None:
                status.ai_temperature = raw / 10.0
        elif key == "SmartCoolClean":
            status.freeze_wash_active = value == FreezeWashAction.START
        elif key == "ProgressSmartClean":
            status.freeze_wash_progress = _to_int(value)


def _parse_beep(data: dict) -> BeepVolume | None:
    """Extract beep volume from the mode resource options array."""
    options = _samsung_val(data, "options")
    if not isinstance(options, list):
        return None

    for opt in options:
        if isinstance(opt, str) and opt.startswith("Volume_"):
            with contextlib.suppress(ValueError):
                return BeepVolume(opt)

    return None
