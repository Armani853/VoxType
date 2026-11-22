"""Lazy sounddevice discovery that is safe to import without PortAudio."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any


class AudioBackendUnavailable(RuntimeError):
    """The optional sounddevice/PortAudio runtime is not installed or usable."""


@dataclass(frozen=True, slots=True)
class InputDevice:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_sample_rate: int
    supported_sample_rates: tuple[int, ...]
    is_default: bool
    endpoint_id: str | None = None

    @property
    def fingerprint(self) -> str:
        # PortAudio exposes a transient index. Match a saved choice only when
        # its public hardware characteristics identify one candidate.
        return "|".join(
            (
                self.host_api.casefold(),
                self.name.casefold(),
                str(self.max_input_channels),
                str(self.default_sample_rate),
            )
        )


def match_input_device(
    devices: tuple[InputDevice, ...],
    index: int | None,
    fingerprint: str = "",
    endpoint_id: str = "",
) -> InputDevice | None:
    if endpoint_id:
        matches = [
            device
            for device in devices
            if device.endpoint_id is not None
            and device.endpoint_id.casefold() == endpoint_id.casefold()
        ]
        return matches[0] if len(matches) == 1 else None
    if index is None:
        return next((device for device in devices if device.is_default), None)
    if fingerprint:
        matches = [device for device in devices if device.fingerprint == fingerprint]
        return matches[0] if len(matches) == 1 else None
    return next((device for device in devices if device.index == index), None)


def _sounddevice() -> Any:
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise AudioBackendUnavailable(
            "Запись звука недоступна: установите sounddevice и рабочий PortAudio."
        ) from exc
    return sounddevice


def _wasapi_device_accessor(sd: Any) -> Any:
    if os.name != "nt":
        return None
    try:
        function = ctypes.CDLL(sd._libname).PaWasapi_GetIMMDevice
        function.argtypes = (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        function.restype = ctypes.c_int
        return function
    except (AttributeError, OSError):
        return None


def _wasapi_endpoint(accessor: Any, index: int) -> tuple[str | None, bool]:
    """Read IMMDevice metadata from the current PortAudio inventory."""
    device = ctypes.c_void_p()
    if accessor(index, ctypes.byref(device)) != 0 or not device.value:
        return None, True
    # PortAudio owns the IMMDevice.  The endpoint ID is a caller-owned COM string.
    vtable = ctypes.cast(device, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    get_id = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
        vtable[5]
    )
    get_state = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))(
        vtable[6]
    )
    value = ctypes.c_void_p()
    state = ctypes.c_ulong()
    active = get_state(device, ctypes.byref(state)) != 0 or bool(state.value & 0x1)
    if get_id(device, ctypes.byref(value)) != 0 or not value.value:
        return None, active
    free = ctypes.OleDLL("ole32").CoTaskMemFree
    free.argtypes = (ctypes.c_void_p,)
    free.restype = None
    try:
        return ctypes.wstring_at(value), active
    finally:
        free(value)


def list_input_devices(
    *,
    candidate_sample_rates: tuple[int, ...] = (16_000, 22_050, 32_000, 44_100, 48_000, 96_000),
) -> tuple[InputDevice, ...]:
    """Return recording devices and rates accepted by PortAudio settings checks."""

    sd = _sounddevice()
    try:
        raw_devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        default_input = int(sd.default.device[0])
    except Exception as exc:
        raise AudioBackendUnavailable("PortAudio не смог перечислить устройства записи.") from exc

    devices: list[InputDevice] = []
    wasapi = _wasapi_device_accessor(sd)
    for index, raw in enumerate(raw_devices):
        channels = int(raw.get("max_input_channels", 0))
        if channels <= 0:
            continue
        supported: list[int] = []
        for rate in candidate_sample_rates:
            try:
                sd.check_input_settings(device=index, channels=1, dtype="float32", samplerate=rate)
            except Exception:
                # PortAudioError intentionally does not derive from OSError.
                # One unsupported rate must not hide an otherwise usable device.
                continue
            supported.append(rate)
        default_rate = round(float(raw.get("default_samplerate", 0.0)))
        if default_rate > 0 and default_rate not in supported:
            supported.append(default_rate)
        host_index = int(raw.get("hostapi", -1))
        host_name = "Unknown"
        if 0 <= host_index < len(host_apis):
            host_name = str(host_apis[host_index].get("name", "Unknown"))
        endpoint, active = (
            _wasapi_endpoint(wasapi, index)
            if wasapi is not None and "wasapi" in host_name.casefold()
            else (None, True)
        )
        if not active:
            continue
        devices.append(
            InputDevice(
                index=index,
                name=str(raw.get("name", f"Input {index}")),
                host_api=host_name,
                max_input_channels=channels,
                default_sample_rate=default_rate,
                supported_sample_rates=tuple(sorted(set(supported))),
                is_default=index == default_input,
                endpoint_id=endpoint,
            )
        )
    return tuple(devices)


def choose_capture_rate(device: InputDevice, target_rate: int = 16_000) -> int:
    """Prefer native 16 kHz, then 48 kHz, then the nearest supported rate."""

    rates = device.supported_sample_rates
    if not rates:
        if device.default_sample_rate <= 0:
            raise ValueError("the input device exposes no usable sample rate")
        return device.default_sample_rate
    if target_rate in rates:
        return target_rate
    if 48_000 in rates:
        return 48_000
    return min(rates, key=lambda rate: (abs(rate - target_rate), -rate))


def resolve_recording_device(
    selected_index: int | None,
    known_devices: tuple[InputDevice, ...],
    fingerprint: str = "",
    endpoint_id: str = "",
) -> InputDevice:
    """Resolve the stream device before choosing its native rate and channel count."""
    if selected_index is None:
        devices = list_input_devices(candidate_sample_rates=(16_000, 48_000))
        device = match_input_device(devices, None, endpoint_id=endpoint_id)
        if device is not None:
            return device
        raise AudioBackendUnavailable("Windows не назначила устройство записи по умолчанию.")
    selected = match_input_device(known_devices, selected_index, fingerprint, endpoint_id)
    if selected is not None:
        return selected
    raise AudioBackendUnavailable(f"Выбранное устройство записи недоступно ({selected_index}).")


def choose_capture_channels(device: InputDevice) -> int:
    """Use the native stereo pin on WDM-KS devices that expose two channels.

    Realtek Stereo Mix can accept a mono format check and open a mono stream,
    yet never call PortAudio's callback. Its stereo pin supplies PCM normally.
    Other host APIs retain the lighter mono path.
    """

    if "WDM-KS" in device.host_api.upper() and device.max_input_channels >= 2:
        return 2
    return 1
