"""Windows session and Core Audio notifications for the desktop event loop."""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic


class DeviceEventType(StrEnum):
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    event_type: DeviceEventType
    endpoint_id: str | None
    is_default: bool
    role: int | None
    received_at: float


class WindowsSystemEventBridge:
    """One native message source, plus optional IMMNotificationClient support."""

    def __init__(self, notify: Callable[[str | DeviceEvent], None]) -> None:
        self._notify = notify
        self._hwnd = 0
        self._core_audio = None

    def start(self, hwnd: int) -> None:
        if sys.platform != "win32" or not hwnd or self._hwnd:
            return
        self._hwnd = hwnd
        wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        register = wts.WTSRegisterSessionNotification
        register.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        register.restype = ctypes.c_int
        self._unregister = wts.WTSUnRegisterSessionNotification
        self._unregister.argtypes = (ctypes.c_void_p,)
        self._unregister.restype = ctypes.c_int
        self._registered = bool(register(hwnd, 0))
        try:
            self._core_audio = self._register_core_audio()
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            logging.getLogger(__name__).debug("core_audio_notification_unavailable kind=%s", type(exc).__name__)

    def handle_message(self, msg: int, wparam: int) -> None:
        if msg == 0x02B1 and wparam in (7, 8):  # WM_WTSSESSION_CHANGE
            self._notify("lock" if wparam == 7 else "unlock")
        elif msg == 0x0219 and wparam in (7, 0x8000, 0x8004) and self._core_audio is None:
            self._notify(DeviceEvent(DeviceEventType.CHANGED, None, False, None, monotonic()))

    def close(self) -> None:
        if self._core_audio is not None:
            enumerator, client = self._core_audio
            try:
                enumerator.UnregisterEndpointNotificationCallback(client)
            except OSError:
                logging.getLogger(__name__).debug("core_audio_notification_unregister_failed")
            self._core_audio = None
        if self._hwnd and self._registered:
            self._unregister(self._hwnd)
        self._hwnd = 0

    def _register_core_audio(self):
        from ctypes import POINTER, c_int, c_ulong, c_void_p, c_wchar_p
        from comtypes import CLSCTX_INPROC_SERVER, COMMETHOD, COMObject, CoCreateInstance, GUID, HRESULT, IUnknown

        class PropertyKey(ctypes.Structure):
            _fields_ = (("fmtid", GUID), ("pid", c_ulong))

        class NotificationClient(IUnknown):
            _iid_ = GUID("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
            _methods_ = [
                COMMETHOD([], HRESULT, "OnDeviceStateChanged", (["in"], c_wchar_p, "device"), (["in"], c_ulong, "state")),
                COMMETHOD([], HRESULT, "OnDeviceAdded", (["in"], c_wchar_p, "device")),
                COMMETHOD([], HRESULT, "OnDeviceRemoved", (["in"], c_wchar_p, "device")),
                COMMETHOD([], HRESULT, "OnDefaultDeviceChanged", (["in"], c_int, "flow"), (["in"], c_int, "role"), (["in"], c_wchar_p, "device")),
                COMMETHOD([], HRESULT, "OnPropertyValueChanged", (["in"], c_wchar_p, "device"), (["in"], PropertyKey, "key")),
            ]

        class DeviceEnumerator(IUnknown):
            _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
            _methods_ = [
                COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_int, "flow"), (["in"], c_ulong, "mask"), (["out"], POINTER(c_void_p), "devices")),
                COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_int, "flow"), (["in"], c_int, "role"), (["out"], POINTER(c_void_p), "device")),
                COMMETHOD([], HRESULT, "GetDevice", (["in"], c_wchar_p, "id"), (["out"], POINTER(c_void_p), "device")),
                COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback", (["in"], POINTER(NotificationClient), "client")),
                COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback", (["in"], POINTER(NotificationClient), "client")),
            ]

        notify = self._notify

        class Client(COMObject):
            _com_interfaces_ = [NotificationClient]

            @staticmethod
            def _emit(kind, device, role=None):
                notify(DeviceEvent(kind, device, role is not None, role, monotonic()))
                return 0

            def OnDeviceStateChanged(self, device, state):
                return self._emit(DeviceEventType.CHANGED, device)

            def OnDeviceAdded(self, device):
                return self._emit(DeviceEventType.ADDED, device)

            def OnDeviceRemoved(self, device):
                return self._emit(DeviceEventType.REMOVED, device)

            def OnDefaultDeviceChanged(self, flow, role, device):
                return self._emit(DeviceEventType.DEFAULT, device, role) if flow == 1 else 0

            def OnPropertyValueChanged(self, device, key):
                return 0

        enumerator = CoCreateInstance(
            GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
            interface=DeviceEnumerator, clsctx=CLSCTX_INPROC_SERVER,
        )
        client = Client().QueryInterface(NotificationClient)
        enumerator.RegisterEndpointNotificationCallback(client)
        return enumerator, client
