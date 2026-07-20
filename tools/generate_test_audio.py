#!/usr/bin/env python3
"""Create copyright-free deterministic PCM fixtures for audio pipeline tests."""

from __future__ import annotations

import argparse
import math
import random
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt


def generate(
    duration_s: float,
    *,
    sample_rate: int = 16_000,
    amplitude: float = 0.2,
    noise: float = 0.0,
    seed: int = 42,
) -> npt.NDArray[np.float32]:
    if duration_s <= 0 or sample_rate <= 0:
        raise ValueError("duration and sample rate must be positive")
    generator = random.Random(seed)
    count = round(duration_s * sample_rate)
    samples = np.zeros(count, dtype=np.float32)
    for index in range(count):
        second = index / sample_rate
        active = int(second) % 3 != 2
        if active:
            envelope = min(1.0, (index % sample_rate) / (sample_rate * 0.05))
            samples[index] = amplitude * envelope * math.sin(2 * math.pi * 180 * second)
        if noise:
            samples[index] += generator.uniform(-noise, noise)
    return np.clip(samples, -1.0, 1.0)


def write_wav(path: Path, samples: npt.NDArray[np.float32], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.round(samples * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--sample-rate", type=int, default=16_000)
    parser.add_argument("--amplitude", type=float, default=0.2)
    parser.add_argument("--noise", type=float, default=0.0)
    arguments = parser.parse_args()
    write_wav(
        arguments.output,
        generate(
            arguments.duration,
            sample_rate=arguments.sample_rate,
            amplitude=arguments.amplitude,
            noise=arguments.noise,
        ),
        arguments.sample_rate,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
