# Audio results and route feasibility

## 2026-11-19 source capture

Local SAPI Irina and Zira → Realtek output 11 → Stereo Mix input 19 → PortAudio → Silero VAD → faster-whisper small, balanced. References and normalization are in `tests/real_world_audio/`. The two short tests ran from `5848661` source. The longer series started before the delivery-only changes in `dbb2bd0`. One sample per case. These runs used an isolated source profile with insertion disabled. The longer series overlapped an automated test run, so its timing is diagnostic and cannot establish a performance SLO.

| Case | WAV, s | WER | CER | Playback end to completion, s | Peak sampled backlog, s | Result |
|---|---:|---:|---:|---:|---:|---|
| RU-05 | 4.95 | 9.1% | 2.1% | 6.38 | 5.07 | Text recognized |
| RU-20 | 19.75 | 5.3% | 5.1% | 12.89 | 19.56 | Text recognized |
| RU-40 | 42.39 | 8.6% | 7.7% | 27.28 | 28.26 | Text recognized |
| RU-60 | 62.28 | 3.3% | 0.7% | 16.58 | 21.66 | Text recognized |
| RU-90 | 91.23 | 8.8% | 5.7% | 48.30 | 64.17 | Text recognized |
| EN-05 | 5.08 | 0.0% | 0.0% | 5.77 | 5.19 | Text recognized |
| EN-20 | 18.37 | 2.2% | 1.4% | 7.23 | 9.36 | Text recognized |
| EN-40 | 41.07 | 1.0% | 0.4% | 55.03 | 11.91 | Text recognized |
| EN-60 | 55.67 | 100.0% | 100.0% | 0.53 | 0.27 | Empty result |
| EN-90 | 85.38 | 100.0% | 100.0% | 0.13 | 0.30 | Empty result |

EN-60 and EN-90 emitted no ASR chunks in the first series. A subsequent standalone PortAudio route probe recorded peak 0.330 and RMS 0.0328 with no reported errors. This confirms the route worked during the probe. The failure mechanism in the earlier sessions remains unknown. Original JSON metrics are under `build/qa-capture-20261119/` and `build/qa-capture-remaining-20261119/`. Real human speech, installed EXE behavior and actual text insertion remain separate requirements.

The second EN-60 attempt reached VAD and ASR. It stayed in `FINISHING` beyond the 60 s test deadline. Several queued chunks took roughly 10–14 seconds of inference each. Shutdown was deferred and the harness exited with failure. The repeat stopped at EN-60 before another EN-90 run, so both requirements remain open. Evidence: `build/qa-capture-en-recheck-20261119/logs/voxtype.log`. The 60 s timeout is a diagnostic limit, not a proposed latency target.

## Historical evidence

The installed `b598039` candidate recorded ambient audio, but there was no controlled RU/EN source. One PortAudio overflow occurred, and a later 2.236 s fragment required 24.738 s ASR inference. The transcript is deliberately absent from technical logs. These observations are not a WER/CER study.

On 2026-11-06, a local Windows SAPI `Microsoft Irina Desktop` voice generated a 12.342 s 22,050 Hz Russian WAV in ignored `build/real_world_audio/route_probe.wav`. PortAudio enumerated Realtek Stereo Mixer at device 19, 48 kHz. Blocking input failed with `Blocking API not supported yet`; a callback stream opened but yielded **zero blocks** during output to Realtek WASAPI device 11. Realtek microphone device 12 yielded 12.920 s of blocks without queue overflow, but RMS was only 0.000512 and peak 0.013092 at 40% playback sample amplitude. Signal correlation and intelligible speech were not established. System defaults were not changed. This does not prove that VoxType could capture the generated speech.

| Required scenario | Controlled capture | Reference/actual transcript | WER/CER | Status |
|---|---|---|---|---|
| RU 5, 20, 40, 60, 90 s | None verified | None | Not measurable | BLOCKED |
| EN 5, 20, 40, 60, 90 s | None verified | None | Not measurable | BLOCKED |

Do not label the SAPI probe as live human speech or a VoxType end-to-end test. A working virtual cable or demonstrably captured physical route is needed for the autonomous ten-scenario matrix.
