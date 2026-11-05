"""The settings UI must use completed discovery rather than query PortAudio."""

from types import SimpleNamespace

from voxtype.app import DesktopApplication
from voxtype.audio.devices import InputDevice
from voxtype.settings.schema import AppSettings


def test_capture_reconfiguration_uses_cached_devices(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    device = InputDevice(
        index=0,
        name="Microphone",
        host_api="WASAPI",
        max_input_channels=1,
        default_sample_rate=48_000,
        supported_sample_rates=(16_000, 48_000),
        is_default=True,
    )
    runtime = SimpleNamespace(settings=AppSettings(), devices=(device,), device=device)
    controller = SimpleNamespace(capture_factory=None, microphone_name="")
    app = object.__new__(DesktopApplication)
    app.runtime = runtime
    app.controller = controller

    def forbidden_discovery() -> None:
        raise AssertionError("PortAudio discovery ran on the settings thread")

    monkeypatch.setattr("voxtype.app.list_input_devices", forbidden_discovery)
    app._configure_capture_factory()

    assert controller.capture_factory is not None
    assert controller.microphone_name == "Microphone (WASAPI)"
    assert runtime.device is device
