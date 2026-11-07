"""Short microphone recordings use callback capture on every Windows host API."""

from __future__ import annotations

from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voxtype.audio.devices import InputDevice
from voxtype.audio.recording import record_input


@pytest.mark.parametrize("host_api,channels", [("Windows WDM-KS", 2), ("Windows WASAPI", 1)])
def test_short_recording_uses_bounded_callback_and_mixes_stereo(
    monkeypatch: pytest.MonkeyPatch, host_api: str, channels: int
) -> None:
    opened: dict[str, Any] = {}

    class CallbackStop(Exception):
        pass

    class FakeStream:
        def __init__(self, **kwargs: Any) -> None:
            opened.update(kwargs)

        def __enter__(self) -> FakeStream:
            frame = np.tile(np.array([[0.2, 0.4]], dtype=np.float32)[:, :channels], (30, 1))
            with suppress(CallbackStop):
                opened["callback"](frame, 30, None, SimpleNamespace(input_overflow=False))
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "voxtype.audio.recording._sounddevice",
        lambda: SimpleNamespace(InputStream=FakeStream, CallbackStop=CallbackStop),
    )
    device = InputDevice(19, "Test input", host_api, 2, 1000, (1000,), False)
    result = record_input(device, 0.03, 1000)
    assert opened["channels"] == channels
    assert opened["blocksize"] == 30
    assert result.shape == (30,)
    np.testing.assert_allclose(result, 0.3 if channels == 2 else 0.2, atol=1e-6)
