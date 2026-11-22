"""Decode WM_POWERBROADCAST without taking a dependency on a UI toolkit."""

from __future__ import annotations

import ctypes
import logging
import os
import struct
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

WM_POWERBROADCAST = 0x0218
PBT_APMQUERYSUSPEND = 0x0000
PBT_APMQUERYSTANDBY = 0x0001
PBT_APMSUSPEND = 0x0004
PBT_APMSTANDBY = 0x0005
PBT_APMRESUMECRITICAL = 0x0006
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMESTANDBY = 0x0008
PBT_APMBATTERYLOW = 0x0009
PBT_APMPOWERSTATUSCHANGE = 0x000A
PBT_APMOEMEVENT = 0x000B
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013

MAX_POWER_SETTING_DATA = 1024 * 1024


def on_battery_power() -> bool | None:
    if os.name != "nt":
        return None

    class PowerStatus(ctypes.Structure):
        _fields_ = (
            ("ac", ctypes.c_ubyte),
            ("battery", ctypes.c_ubyte),
            ("percentage", ctypes.c_ubyte),
            ("system", ctypes.c_ubyte),
            ("life", ctypes.c_uint32),
            ("full_life", ctypes.c_uint32),
        )

    status = PowerStatus()
    try:
        function = ctypes.WinDLL("kernel32").GetSystemPowerStatus
        function.argtypes = (ctypes.POINTER(PowerStatus),)
        function.restype = ctypes.c_int
        if function(ctypes.byref(status)) and status.ac in (0, 1):
            return bool(status.ac == 0 and status.battery != 128)
    except (AttributeError, OSError):
        pass
    return None


class PowerEventKind(StrEnum):
    SUSPEND_QUERY = "suspend_query"
    SUSPEND = "suspend"
    RESUME_AUTOMATIC = "resume_automatic"
    RESUME_USER = "resume_user"
    RESUME_CRITICAL = "resume_critical"
    POWER_STATUS_CHANGE = "power_status_change"
    BATTERY_LOW = "battery_low"
    SETTING_CHANGE = "setting_change"
    OEM = "oem"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PowerSettingChange:
    setting: uuid.UUID
    data: bytes

    def as_uint32(self) -> int | None:
        return struct.unpack_from("<I", self.data)[0] if len(self.data) == 4 else None


@dataclass(frozen=True, slots=True)
class NativePowerEvent:
    kind: PowerEventKind
    raw_code: int
    setting_change: PowerSettingChange | None = None

    @property
    def is_resume(self) -> bool:
        return self.kind in {
            PowerEventKind.RESUME_AUTOMATIC,
            PowerEventKind.RESUME_USER,
            PowerEventKind.RESUME_CRITICAL,
        }


_EVENT_KIND = {
    PBT_APMQUERYSUSPEND: PowerEventKind.SUSPEND_QUERY,
    PBT_APMQUERYSTANDBY: PowerEventKind.SUSPEND_QUERY,
    PBT_APMSUSPEND: PowerEventKind.SUSPEND,
    PBT_APMSTANDBY: PowerEventKind.SUSPEND,
    PBT_APMRESUMECRITICAL: PowerEventKind.RESUME_CRITICAL,
    PBT_APMRESUMESUSPEND: PowerEventKind.RESUME_USER,
    PBT_APMRESUMESTANDBY: PowerEventKind.RESUME_USER,
    PBT_APMRESUMEAUTOMATIC: PowerEventKind.RESUME_AUTOMATIC,
    PBT_APMBATTERYLOW: PowerEventKind.BATTERY_LOW,
    PBT_APMPOWERSTATUSCHANGE: PowerEventKind.POWER_STATUS_CHANGE,
    PBT_APMOEMEVENT: PowerEventKind.OEM,
    PBT_POWERSETTINGCHANGE: PowerEventKind.SETTING_CHANGE,
}


def parse_power_setting_payload(payload: bytes) -> PowerSettingChange:
    """Parse POWERBROADCAST_SETTING bytes with strict length validation."""

    if len(payload) < 20:
        raise ValueError("POWERBROADCAST_SETTING payload is truncated")
    setting = uuid.UUID(bytes_le=payload[:16])
    data_length = struct.unpack_from("<I", payload, 16)[0]
    if data_length > MAX_POWER_SETTING_DATA:
        raise ValueError("POWERBROADCAST_SETTING data is unreasonably large")
    if len(payload) != 20 + data_length:
        raise ValueError("POWERBROADCAST_SETTING length does not match its header")
    return PowerSettingChange(setting, payload[20:])


def read_power_setting_pointer(pointer: int) -> PowerSettingChange:
    if not pointer:
        raise ValueError("POWERBROADCAST_SETTING pointer is null")
    header = ctypes.string_at(pointer, 20)
    data_length = struct.unpack_from("<I", header, 16)[0]
    if data_length > MAX_POWER_SETTING_DATA:
        raise ValueError("POWERBROADCAST_SETTING data is unreasonably large")
    return parse_power_setting_payload(ctypes.string_at(pointer, 20 + data_length))


def decode_power_event(wparam: int, lparam: int = 0) -> NativePowerEvent:
    code = int(wparam)
    kind = _EVENT_KIND.get(code, PowerEventKind.UNKNOWN)
    setting = (
        read_power_setting_pointer(int(lparam))
        if kind is PowerEventKind.SETTING_CHANGE and lparam
        else None
    )
    return NativePowerEvent(kind, code, setting)


PowerCallback = Callable[[NativePowerEvent], None]


class PowerEventDispatcher:
    """Thread-safe callback routing for a Qt/Win32 native event filter."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._callbacks: dict[PowerEventKind, list[PowerCallback]] = defaultdict(list)
        self._all_callbacks: list[PowerCallback] = []
        self._lock = threading.RLock()
        self._logger = logger or logging.getLogger(__name__)

    def subscribe(
        self, callback: PowerCallback, *, kind: PowerEventKind | None = None
    ) -> Callable[[], None]:
        with self._lock:
            target = self._all_callbacks if kind is None else self._callbacks[kind]
            target.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                selected = self._all_callbacks if kind is None else self._callbacks[kind]
                if callback in selected:
                    selected.remove(callback)

        return unsubscribe

    def dispatch(self, event: NativePowerEvent) -> tuple[Exception, ...]:
        with self._lock:
            callbacks = tuple(self._all_callbacks) + tuple(self._callbacks[event.kind])
        errors: list[Exception] = []
        for callback in callbacks:
            try:
                callback(event)
            except Exception as exc:
                errors.append(exc)
                self._logger.exception(
                    "Power-event callback failed",
                    extra={"power_event": event.kind.value},
                )
        return tuple(errors)

    def handle_message(self, message: int, wparam: int, lparam: int = 0) -> NativePowerEvent | None:
        if message != WM_POWERBROADCAST:
            return None
        event = decode_power_event(wparam, lparam)
        self.dispatch(event)
        return event


class PowerEventCoordinator:
    """Map suspend/resume broadcasts to injected lifecycle callbacks."""

    def __init__(
        self,
        *,
        on_suspend: Callable[[], None],
        on_resume: Callable[[], None],
        on_power_change: Callable[[], None] | None = None,
        dispatcher: PowerEventDispatcher | None = None,
    ) -> None:
        self.dispatcher = dispatcher or PowerEventDispatcher()
        self._on_suspend = on_suspend
        self._on_resume = on_resume
        self._on_power_change = on_power_change
        self.dispatcher.subscribe(self._handle)

    def _handle(self, event: NativePowerEvent) -> None:
        if event.kind is PowerEventKind.SUSPEND:
            self._on_suspend()
        elif event.is_resume:
            self._on_resume()
        elif event.kind is PowerEventKind.POWER_STATUS_CHANGE and self._on_power_change is not None:
            self._on_power_change()

    def handle_message(self, message: int, wparam: int, lparam: int = 0) -> NativePowerEvent | None:
        return self.dispatcher.handle_message(message, wparam, lparam)
