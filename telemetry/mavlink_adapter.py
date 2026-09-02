from __future__ import annotations

import math
import socket
import time
from typing import Any

from core.contracts import FlightState


class MavlinkFlightAdapter:
    """Non-blocking MAVLink-to-FlightState adapter for ArduPlane SITL.

    The backend remains clocked at 5 Hz. Each poll drains pending MAVLink
    messages, keeps the freshest navigation state, and returns None until a
    usable stream has arrived. That None is the replay-fallback boundary.
    """

    def __init__(
        self,
        endpoint: str = "udpin:0.0.0.0:14550",
        stale_after_sec: float = 3.0,
    ) -> None:
        self.endpoint = endpoint
        self.stale_after_sec = stale_after_sec
        self._connection: Any | None = None
        self._mavutil: Any | None = None
        self._connect_error: str | None = None
        self._next_connect_attempt = 0.0
        self._connection_opened_monotonic: float | None = None
        self._started_monotonic: float | None = None
        self._last_message_monotonic: float | None = None
        self._latitude_deg = 28.6139
        self._longitude_deg = 77.2090
        self._altitude_m = 0.0
        self._airspeed_mps = 0.0
        self._vertical_speed_mps = 0.0
        self._throttle_pct = 0.0
        self._flight_mode = "UNKNOWN"
        self._seen_position = False
        self._seen_flight_data = False
        self._stream_requested = False

    def connect(self) -> None:
        if self._connection is not None or time.monotonic() < self._next_connect_attempt:
            return
        try:
            from pymavlink import mavutil

            self._mavutil = mavutil
            previous_timeout = socket.getdefaulttimeout()
            try:
                if self.endpoint.startswith("tcp:"):
                    socket.setdefaulttimeout(0.25)
                self._connection = mavutil.mavlink_connection(
                    self.endpoint,
                    source_system=255,
                    # pymavlink's TCP auto-reconnect can block recv_match after
                    # a peer disappears. The adapter owns bounded retry timing.
                    autoreconnect=False,
                    retries=1,
                )
            finally:
                socket.setdefaulttimeout(previous_timeout)
        except (ImportError, OSError, ValueError) as exc:
            self._connect_error = f"{type(exc).__name__}: {exc}"
            self._next_connect_attempt = time.monotonic() + 1.0
        else:
            self._connect_error = None
            self._connection_opened_monotonic = time.monotonic()

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except (AttributeError, OSError):
                pass
        self._connection = None
        self._connection_opened_monotonic = None
        self._stream_requested = False

    def mark_error(self, exc: BaseException) -> None:
        self._connect_error = f"{type(exc).__name__}: {exc}"
        self.close()
        self._next_connect_attempt = time.monotonic() + 1.0

    @property
    def last_message_age_sec(self) -> float | None:
        if self._last_message_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self._last_message_monotonic)

    @property
    def is_live(self) -> bool:
        age = self.last_message_age_sec
        return (
            age is not None
            and age <= self.stale_after_sec
            and self._seen_position
            and self._seen_flight_data
        )

    def status(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "socket_open": self._connection is not None,
            "live": self.is_live,
            "last_message_age_sec": (
                round(self.last_message_age_sec, 3)
                if self.last_message_age_sec is not None
                else None
            ),
            "error": self._connect_error,
        }

    def poll(self, ambient_offset_c: float = 25.0) -> FlightState | None:
        self.connect()
        if self._connection is None:
            return None

        try:
            for _ in range(200):
                message = self._connection.recv_match(blocking=False)
                if message is None:
                    break
                if message.get_type() == "BAD_DATA":
                    continue
                self._handle(message)
        except (ConnectionError, EOFError, OSError) as exc:
            self.mark_error(exc)
            return None

        age = self.last_message_age_sec
        open_age = (
            time.monotonic() - self._connection_opened_monotonic
            if self._connection_opened_monotonic is not None
            else 0.0
        )
        if (age is not None and age > self.stale_after_sec) or (
            age is None and open_age > self.stale_after_sec
        ):
            self.mark_error(TimeoutError("MAVLink stream became stale"))
            return None

        if not self.is_live:
            return None

        now = time.monotonic()
        if self._started_monotonic is None:
            self._started_monotonic = now
        ambient_temp_c = 15.0 - 0.0065 * max(0.0, self._altitude_m) + ambient_offset_c
        return FlightState(
            timestamp=time.time(),
            t_sec=now - self._started_monotonic,
            profile_id="sitl_arduplane_live",
            latitude_deg=self._latitude_deg,
            longitude_deg=self._longitude_deg,
            altitude_m=max(0.0, self._altitude_m),
            airspeed_mps=max(0.0, self._airspeed_mps),
            vertical_speed_mps=self._vertical_speed_mps,
            throttle_pct=max(0.0, min(100.0, self._throttle_pct)),
            ambient_temp_c=ambient_temp_c,
            ambient_offset_c=ambient_offset_c,
            flight_phase=self._flight_phase(),
            flight_mode=self._flight_mode,
            telemetry_source="ARDUPILOT_SITL",
        )

    def _handle(self, message: Any) -> None:
        message_type = message.get_type()
        now = time.monotonic()
        self._last_message_monotonic = now

        if message_type == "HEARTBEAT" and self._mavutil is not None:
            if message.autopilot != self._mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
                return
            mode_map = self._mavutil.mode_mapping_bynumber(message.type)
            mode = (
                mode_map.get(message.custom_mode)
                if mode_map is not None
                else self._mavutil.mode_string_v10(message)
            )
            if mode:
                self._flight_mode = str(mode)
            if not self._stream_requested and self._connection is not None:
                self._connection.mav.request_data_stream_send(
                    message.get_srcSystem(),
                    message.get_srcComponent(),
                    self._mavutil.mavlink.MAV_DATA_STREAM_ALL,
                    10,
                    1,
                )
                self._stream_requested = True
        elif message_type == "GLOBAL_POSITION_INT":
            latitude = float(getattr(message, "lat", 0.0)) / 1e7
            longitude = float(getattr(message, "lon", 0.0)) / 1e7
            if math.isfinite(latitude) and math.isfinite(longitude):
                self._latitude_deg = latitude
                self._longitude_deg = longitude
            relative_alt = float(getattr(message, "relative_alt", 0.0)) / 1000.0
            self._altitude_m = relative_alt
            self._vertical_speed_mps = -float(getattr(message, "vz", 0.0)) / 100.0
            self._seen_position = True
        elif message_type == "VFR_HUD":
            self._airspeed_mps = float(getattr(message, "airspeed", 0.0))
            self._throttle_pct = float(getattr(message, "throttle", 0.0))
            self._vertical_speed_mps = float(
                getattr(message, "climb", self._vertical_speed_mps)
            )
            if not self._seen_position:
                self._altitude_m = float(getattr(message, "alt", 0.0))
            self._seen_flight_data = True

    def _flight_phase(self) -> str:
        mode = self._flight_mode.upper()
        if "LOITER" in mode or "CIRCLE" in mode:
            return "LOITER"
        if "LAND" in mode or self._vertical_speed_mps < -1.0:
            return "DESCENT"
        if "TAKEOFF" in mode or (
            self._altitude_m < 120.0
            and self._vertical_speed_mps > 0.7
            and self._throttle_pct > 45.0
        ):
            return "TAKEOFF"
        if self._vertical_speed_mps > 1.0:
            return "CLIMB"
        if self._altitude_m < 3.0 and self._airspeed_mps < 5.0:
            return "GROUND"
        return "CRUISE"
