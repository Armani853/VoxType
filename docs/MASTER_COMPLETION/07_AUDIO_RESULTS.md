# Audio results and route feasibility

The installed `b598039` candidate recorded ambient audio, but there was no controlled RU/EN source. One PortAudio overflow occurred, and a later 2.236 s fragment required 24.738 s ASR inference. The transcript is deliberately absent from technical logs. These observations are not a WER/CER study.

On 2026-11-06, a local Windows SAPI `Microsoft Irina Desktop` voice generated a 12.342 s 22,050 Hz Russian WAV in ignored `build/real_world_audio/route_probe.wav`. PortAudio enumerated Realtek Stereo Mixer at device 19, 48 kHz. Blocking input failed with `Blocking API not supported yet`; a callback stream opened but yielded **zero blocks** during output to Realtek WASAPI device 11. Realtek microphone device 12 yielded 12.920 s of blocks without queue overflow, but RMS was only 0.000512 and peak 0.013092 at 40% playback sample amplitude. Signal correlation and intelligible speech were not established. System defaults were not changed. This does not prove that VoxType could capture the generated speech.

| Required scenario | Controlled capture | Reference/actual transcript | WER/CER | Status |
|---|---|---|---|---|
| RU 5, 20, 40, 60, 90 s | None verified | None | Not measurable | BLOCKED |
| EN 5, 20, 40, 60, 90 s | None verified | None | Not measurable | BLOCKED |

Do not label the SAPI probe as live human speech or a VoxType end-to-end test. A working virtual cable or demonstrably captured physical route is needed for the autonomous ten-scenario matrix.
