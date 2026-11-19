"""Exercise Windows playback → PortAudio → VAD → ASR with synthetic speech.

This is a headless source test. It deliberately does not inject text or change
the clipboard and cannot verify Windows GUI or installed EXE acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from tools.run_accuracy_matrix import ResourceSampler, _score
from voxtype.core.controller import ApplicationController
from voxtype.runtime import build_runtime
from voxtype.settings.repository import SettingsRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("models", type=Path, help="Existing local model directory to copy")
    parser.add_argument("profile", type=Path, help="New isolated QA profile directory")
    parser.add_argument("--input-device", type=int, required=True)
    parser.add_argument("--output-device", type=int, required=True)
    parser.add_argument("--case", action="append", help="Case ID. Defaults to the full corpus.")
    args = parser.parse_args()
    args.profile.mkdir(parents=True, exist_ok=False)
    os.environ["VOXTYPE_DATA_DIRECTORY"] = str(args.profile.resolve())
    os.environ["VOXTYPE_CACHE_DIRECTORY"] = str((args.profile / "cache").resolve())
    references = json.loads(
        (Path(__file__).resolve().parents[1] / "tests/real_world_audio/references.json").read_text(
            encoding="utf-8"
        )
    )
    cases = args.case or list(references)
    if any(case not in references for case in cases):
        parser.error("Unknown case ID")
    for model in ("small", "silero-vad"):
        shutil.copytree(args.models / model, args.profile / "models" / model)
    SettingsRepository(args.profile / "settings.json").update(
        {
            "audio": {"device_id": args.input_device},
            "dictation": {
                "model_profile": "balanced",
                "auto_insert": False,
                "copy_full_text_on_complete": False,
            },
            "privacy": {"save_audio": False, "save_history": False},
        }
    )
    runtime = build_runtime()
    controller = runtime.controller
    results = []
    try:
        if runtime.device.index != args.input_device:
            raise RuntimeError("Requested capture device was not selected")
        for case in cases:
            if controller.snapshot().language.value != case[:2].lower():
                controller.switch_language()
            with wave.open(str(args.corpus / f"{case}.wav"), "rb") as stream:
                if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (
                    16_000,
                    1,
                    2,
                ):
                    raise ValueError(f"Expected 16 kHz mono PCM16 for {case}")
                source = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2")
            samples = np.repeat(source.astype(np.float32) / 32768.0, 3)
            stereo = np.repeat(samples[:, None], 2, axis=1)
            sampler = ResourceSampler()
            sampler.start()
            try:
                start = time.monotonic()
                if not controller.start_dictation():
                    raise RuntimeError(f"{case}: controller refused start")
                _wait_for_state(controller, "LISTENING", 30)
                listening = time.monotonic()
                backlog = 0.0
                with sd.OutputStream(
                    samplerate=48_000, channels=2, dtype="float32", device=args.output_device
                ) as output:
                    for offset in range(0, len(stereo), 24_000):
                        output.write(stereo[offset : offset + 24_000])
                        backlog = max(backlog, controller.snapshot().queued_audio_seconds)
                end = time.monotonic()
                controller.stop_dictation()
                _wait_for_state(controller, "COMPLETED", 60)
                transcript = controller.snapshot().full_transcript
                metrics = _score(references[case], transcript)
                metrics["wer"] = metrics.pop("pseudo_wer")
                results.append(
                    {
                        "id": case,
                        "wav_seconds": len(source) / 16_000,
                        "start_to_listening_seconds": listening - start,
                        "playback_end_to_completed_seconds": time.monotonic() - end,
                        "peak_sampled_backlog_seconds": backlog,
                        "peak_sampled_rss_bytes": max(sampler.rss_samples),
                        "peak_sampled_process_cpu_percent": max(sampler.cpu_samples, default=0.0),
                        "metrics": metrics,
                    }
                )
                (args.profile / "capture-results.json").write_text(
                    json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(f"{case} completed, WER={metrics['wer']:.1%}", flush=True)
            finally:
                sampler.stop()
    finally:
        if not runtime.close():
            raise RuntimeError("Capture test runtime did not shut down cleanly")
    return int(any(not result["metrics"]["hypothesis_words"] for result in results))


def _wait_for_state(controller: ApplicationController, expected: str, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    state = controller.state.name
    while time.monotonic() < deadline:
        state = controller.state.name
        if state == expected:
            return
        if state.startswith("ERROR"):
            raise RuntimeError(f"Controller reached {state} while waiting for {expected}")
        time.sleep(0.02)
    raise TimeoutError(f"Controller stayed in {state} while waiting for {expected}")


if __name__ == "__main__":
    raise SystemExit(main())
