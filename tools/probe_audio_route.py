"""Probe an explicit speaker-to-Stereo-Mix route without changing Windows defaults."""

from __future__ import annotations

import argparse
import json
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-device", type=int, default=19)
    parser.add_argument("--output-device", type=int, default=11)
    parser.add_argument("--input-channels", type=int, default=2)
    parser.add_argument("--blocksize", type=int, default=0)
    parser.add_argument("--playback-only", action="store_true")
    args = parser.parse_args()
    with wave.open(str(args.wav), "rb") as recording:
        if recording.getnchannels() != 1 or recording.getsampwidth() != 2:
            raise ValueError("Expected 16-bit mono PCM WAV")
        source_rate = recording.getframerate()
        mono = np.frombuffer(recording.readframes(recording.getnframes()), dtype=np.int16)
    if source_rate != 16_000:
        raise ValueError("Expected a 16 kHz source for exact 3x resampling")
    output = np.repeat(mono.astype(np.float32) / 32768.0, 3)
    output = np.repeat(output[:, None], 2, axis=1)
    silence = np.zeros((24_000, 2), dtype=np.float32)
    samples = np.concatenate((silence, output, silence), axis=0)
    if args.playback_only:
        with sd.OutputStream(
            samplerate=48_000, channels=2, dtype="float32", device=args.output_device
        ) as stream:
            stream.write(samples)
        print(json.dumps({"played_seconds": round(len(samples) / 48_000, 3)}))
        return 0
    captured = np.empty((len(samples), args.input_channels), dtype=np.float32)
    failure: list[str] = []
    finished = threading.Event()
    offset = 0

    def play() -> None:
        try:
            with sd.OutputStream(
                samplerate=48_000, channels=2, dtype="float32", device=args.output_device
            ) as stream:
                stream.write(samples)
        except Exception as exc:  # Report the exact device failure to the caller.
            failure.append(str(exc))

    def capture(
        indata: np.ndarray[Any, np.dtype[np.float32]],
        frames: int,
        _time: object,
        status: object,
    ) -> None:
        nonlocal offset
        if status:
            failure.append(str(status))
        count = min(frames, len(captured) - offset)
        captured[offset : offset + count] = indata[:count]
        offset += count
        if offset >= len(captured):
            finished.set()
            raise sd.CallbackStop

    with sd.InputStream(
        samplerate=48_000,
        channels=args.input_channels,
        dtype="float32",
        device=args.input_device,
        blocksize=args.blocksize,
        callback=capture,
    ):
        player = threading.Thread(target=play, name="audio-route-output")
        player.start()
        if not finished.wait(timeout=len(samples) / 48_000 + 3.0):
            failure.append("capture did not complete")
        player.join(timeout=3.0)
        if player.is_alive():
            failure.append("playback thread did not exit")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.output), "wb") as destination:
        destination.setnchannels(args.input_channels)
        destination.setsampwidth(2)
        destination.setframerate(48_000)
        destination.writeframes((np.clip(captured, -1, 1) * 32767).astype(np.int16).tobytes())
    peak = float(np.max(np.abs(captured)))
    rms = float(np.sqrt(np.mean(np.square(captured))))
    print(json.dumps({"peak": peak, "rms": rms, "failures": failure}, indent=2))
    return 0 if peak > 0.01 and not failure else 1


if __name__ == "__main__":
    raise SystemExit(main())
