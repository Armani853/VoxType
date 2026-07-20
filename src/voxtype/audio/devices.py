"""Lazy sounddevice discovery that is safe to import without PortAudio."""

from __future__ import annotations

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


def _sounddevice() -> Any:
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        raise AudioBackendUnavailable(
            "Запись звука недоступна: установите sounddevice и рабочий PortAudio."
        ) from exc
    return sounddevice


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
        devices.append(
            InputDevice(
                index=index,
                name=str(raw.get("name", f"Input {index}")),
                host_api=host_name,
                max_input_channels=channels,
                default_sample_rate=default_rate,
                supported_sample_rates=tuple(sorted(set(supported))),
                is_default=index == default_input,
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
